"""Minimal, bounded, local, privacy-safe structured trace (TAXV2-036).

The trace answers "what did the build/publish pipeline do" with enums and counts only:

* event names, publication outcomes and reason keys come from fixed enums;
* values are bounded integers; the only strings are enum members and truncated SHA-256 hashes of the
  source id / epoch (never the raw values);
* it never receives, and therefore can never log, titles, abstracts, evidence spans, file paths,
  tokens, item/paper/concept IDs or arbitrary objects: the sanitizer is a whitelist and fails closed
  (unknown keys and non-conforming values are dropped and counted in ``dropped``);
* records go to one local JSONL file under the app-owned data directory with a hard cap on size and
  record count; there is no network sink and no endpoint.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Mapping

TRACE_VERSION = 1
MAX_RECORDS = 400
MAX_BYTES = 256 * 1024
MAX_INT = 10_000_000

EVENTS = frozenset({"build.start", "build.complete", "publish.ok", "publish.stale", "publish.validation_failed", "publish.kept_previous", "rollback.ok", "rollback.rejected"})
# Fixed enums; anything else is dropped.
COUNT_KEYS = frozenset({"papers", "fields", "concepts", "concepts_raw", "concepts_active", "concepts_suppressed", "concepts_protected", "concepts_user",
                        "facet_entities", "paper_facet_rows", "memberships", "edges", "evidence_cache_parts"})
REASON_KEYS = frozenset({
    # abstention (per paper, derived from paperFacets status/directs)
    "abstain.no_subject_evidence", "abstain.below_primary_threshold", "abstain.ambiguous", "status.classified", "status.user",
    # suppression (concept ledger reason enum)
    "suppress.context-free-boilerplate", "suppress.generated-metadata-tag", "suppress.low-termhood", "suppress.no-current-membership",
    "suppress.merged-into-canonical", "suppress.user-excluded", "suppress.other",
    # publication / fence reasons
    "fence.taxonomy_changed", "fence.overlays_changed", "fence.config_changed", "fence.source_or_generation_changed", "fence.unspecified",
    "validation.failed", "rollback.snapshot_revision_changed", "rollback.user_state_changed", "rollback.source_changed", "rollback.other"})
REVISION_KEYS = frozenset({"taxonomy", "overlays", "config", "snapshot", "generation"})
# Strict per-key version grammar: an identifier, token or secret passed under an allowed key is dropped.
RECIPE_GRAMMAR = {"recipe": re.compile(r"atlas-graph-v[0-9]{1,3}"), "ruleVersion": re.compile(r"taxonomy-v[0-9]{1,3}"),
                  "ontologyPolicy": re.compile(r"ontology-label-policy-v[0-9]{1,3}")}
RECIPE_KEYS = frozenset(RECIPE_GRAMMAR)
_SUPPRESS_KNOWN = {"context-free-boilerplate", "generated-metadata-tag", "low-termhood", "no-current-membership", "merged-into-canonical", "user-excluded"}


def _hash(value: Any) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]


def _clamp(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return max(0, min(MAX_INT, value))


def sanitize(event: str, *, source_id: Any = None, source_epoch: Any = None, recipe: Mapping[str, Any] | None = None,
             revisions: Mapping[str, Any] | None = None, counts: Mapping[str, Any] | None = None,
             reasons: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    """Build a record from whitelisted pieces only.  Returns ``None`` for an unknown event."""
    if event not in EVENTS:
        return None
    dropped = 0
    record: dict[str, Any] = {"v": TRACE_VERSION, "t": round(time.time(), 3), "event": event}
    if source_id is not None:
        record["source"] = _hash(source_id)
    if source_epoch is not None:
        record["epoch"] = _hash(source_epoch)
    for name, allowed, target in (("recipe", RECIPE_KEYS, None), ("revisions", REVISION_KEYS, "int"), ("counts", COUNT_KEYS, "int"), ("reasons", REASON_KEYS, "int")):
        source = {"recipe": recipe, "revisions": revisions, "counts": counts, "reasons": reasons}[name]
        if not isinstance(source, Mapping):
            continue
        clean: dict[str, Any] = {}
        for key, value in source.items():
            if key not in allowed:
                dropped += 1; continue
            if target == "int":
                number = _clamp(value)
                if number is None:
                    dropped += 1; continue
                clean[key] = number
            else:            # recipe/version labels: short identifier-like tokens only (e.g. "atlas-graph-v3")
                if isinstance(value, str) and RECIPE_GRAMMAR[key].fullmatch(value):
                    clean[key] = value
                else:
                    dropped += 1
        if clean:
            record[name] = clean
    if dropped:
        record["dropped"] = min(dropped, 999)
    return record


def summarize_snapshot(snapshot: Mapping[str, Any]) -> tuple[dict[str, int], dict[str, int]]:
    """Counts and reason-enum counts derived from a built snapshot (no identifiers, no text)."""
    counts: dict[str, int] = {}
    reasons: dict[str, int] = {}
    for key, name in (("papers", "papers"), ("fields", "fields"), ("concepts", "concepts"), ("memberships", "memberships"), ("edges", "edges")):
        if isinstance(snapshot.get(key), list):
            counts[name] = len(snapshot[key])
    catalog = snapshot.get("facetCatalog")
    if isinstance(catalog, Mapping) and isinstance(catalog.get("entities"), list):
        counts["facet_entities"] = len(catalog["entities"])
    rows = snapshot.get("paperFacets")
    if isinstance(rows, list):
        counts["paper_facet_rows"] = len(rows)
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            status = row.get("status")
            if status in ("classified", "user"):
                reasons[f"status.{status}"] = reasons.get(f"status.{status}", 0) + 1
            elif status == "ambiguous":
                reasons["abstain.ambiguous"] = reasons.get("abstain.ambiguous", 0) + 1
            elif status == "abstain":
                has_subject = any(isinstance(m, Mapping) and m.get("kind") == "subject" for m in row.get("directMemberships", []) or [])
                key = "abstain.below_primary_threshold" if has_subject else "abstain.no_subject_evidence"
                reasons[key] = reasons.get(key, 0) + 1
    ledger = snapshot.get("conceptQualityLedger")
    if isinstance(ledger, Mapping):
        ledger_counts = ledger.get("counts") if isinstance(ledger.get("counts"), Mapping) else {}
        for name, key in (("raw", "concepts_raw"), ("active", "concepts_active"), ("suppressed", "concepts_suppressed"), ("protected", "concepts_protected"), ("user", "concepts_user")):
            if isinstance(ledger_counts.get(name), int):
                counts[key] = ledger_counts[name]
        for entry in ledger.get("entries", []) or []:
            if isinstance(entry, Mapping) and entry.get("status") == "suppressed":
                reason = entry.get("reason")
                key = f"suppress.{reason}" if reason in _SUPPRESS_KNOWN else "suppress.other"
                reasons[key] = reasons.get(key, 0) + 1
    return counts, reasons


class TraceLog:
    """Append-only local JSONL trace with hard size/record bounds and fail-soft emission."""

    def __init__(self, data_dir: Path | str, *, max_records: int = MAX_RECORDS, max_bytes: int = MAX_BYTES):
        self.path = Path(data_dir) / "trace" / "atlas-trace.jsonl"
        self.max_records, self.max_bytes = int(max_records), int(max_bytes)
        self._lock = threading.Lock()

    def emit(self, event: str, **parts: Any) -> bool:
        """Record one event; never raises (observability must not break a build)."""
        try:
            record = sanitize(event, **parts)
            if record is None:
                return False
            line = json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n"
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.path, "ab") as handle:
                    handle.write(line.encode("utf-8"))
                if self.path.stat().st_size > self.max_bytes or self._line_count() > self.max_records:
                    self._trim()
            return True
        except Exception:
            return False

    def _line_count(self) -> int:
        with open(self.path, "rb") as handle:
            return sum(1 for _ in handle)

    def _trim(self) -> None:
        """Keep the newest half of the allowed records (atomic rewrite)."""
        with open(self.path, "rb") as handle:
            lines = handle.readlines()
        keep = lines[-max(1, self.max_records // 2):]
        while keep and sum(len(x) for x in keep) > self.max_bytes // 2:
            keep = keep[1:]
        fd, temporary = tempfile.mkstemp(prefix=".trace.", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "wb") as out:
                out.writelines(keep); out.flush(); os.fsync(out.fileno())
            os.replace(temporary, self.path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def read(self) -> list[dict[str, Any]]:
        try:
            return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except (OSError, ValueError):
            return []
