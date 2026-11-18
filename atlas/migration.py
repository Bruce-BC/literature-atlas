"""Receipts, validation and rollback for graph recipe migrations (app-owned state only).

Zotero source data and vault notes are never touched: receipts live under
``<dataDir>/migrations/atlas-graph-v3/<sourceEpoch>/<generation>.json`` and only
contain derived data plus the user's app-owned taxonomy/overlay payloads.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping

from .storage import SQLiteStore
from .taxonomy import check_facet_invariants

RECIPE_VERSION = "atlas-graph-v3"


def canonical_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def identity_ledger(snapshot: Mapping[str, Any] | None) -> dict[str, Any]:
    snapshot = snapshot or {}
    ids: dict[str, list[str]] = {}
    for key in ("papers", "fields", "concepts", "memberships", "edges"):
        rows = snapshot.get(key, [])
        ids[key] = sorted(str(row.get("id")) if isinstance(row, Mapping) and row.get("id") is not None else canonical_hash(row) for row in rows if isinstance(row, Mapping))
    ledger = snapshot.get("conceptQualityLedger") if isinstance(snapshot.get("conceptQualityLedger"), Mapping) else {}
    quality = {name: sorted(str(x if not isinstance(x, Mapping) else x.get("id")) for x in ledger.get(name, [])) for name in ("raw", "active", "suppressed", "protected", "user")}
    return {"counts": {key: len(value) for key, value in ids.items()}, "ids": ids, "quality": quality,
            "qualityCounts": {key: len(value) for key, value in quality.items()}, "hash": canonical_hash([ids, quality])}


def is_recipe_migration(previous: Mapping[str, Any] | None) -> bool:
    """True when the previous valid snapshot predates the v3 derived payloads.

    A previous graph that already carries ``facetCatalog`` is a plain re-derivation
    (no full backup needed).  A previous graph with derived content (fields or
    concepts) but without the v3 blocks, or stamped with another recipe, is an
    actual recipe migration and needs the full original backup.
    """
    if not isinstance(previous, Mapping):
        return False
    status = previous.get("modelStatus") if isinstance(previous.get("modelStatus"), Mapping) else {}
    stamped = status.get("recipeVersion") or previous.get("recipe")
    if stamped and stamped != RECIPE_VERSION:
        return True
    return "facetCatalog" not in previous and bool(previous.get("fields") or previous.get("concepts"))


def create_migration_receipt(store: SQLiteStore, *, before: Mapping[str, Any] | None, inputs: Mapping[str, Any],
                             source_id: str, source_epoch: str, generation: int, recipe: str = RECIPE_VERSION,
                             id_map: Mapping[str, str] | None = None) -> tuple[str, bool]:
    """Create the immutable original migration backup (once per epoch/generation).

    The receipt records only what existed *before* the attempt: the previous full
    snapshot, the user's app-owned taxonomy/overlay payloads, the input revisions
    and hashes.  What was actually published (and every discarded attempt) is
    appended to the separate audit log via :func:`record_publication`, so a stale
    first attempt can never misdescribe the eventual successful publication.
    """
    status = before.get("modelStatus") if isinstance(before, Mapping) and isinstance(before.get("modelStatus"), Mapping) else {}
    payload = {
        "recipeVersion": recipe, "previousRecipeVersion": status.get("recipeVersion") or (before or {}).get("recipe"),
        "sourceId": source_id, "sourceEpoch": source_epoch, "generation": int(generation),
        "expectedRevisions": {"taxonomy": inputs.get("taxonomyRevision", 0), "overlays": inputs.get("overlayRevision", 0), "config": inputs.get("configRevision", 0)},
        "snapshotRevisionBefore": int(inputs.get("snapshotRevision", 0)),
        "before": identity_ledger(before),
        "idMap": dict(sorted((str(k), str(v)) for k, v in (id_map or {}).items())),
        "userState": {"taxonomy": copy.deepcopy(inputs.get("taxonomy", {})), "overlays": copy.deepcopy(inputs.get("overlays", {}))},
        "rollbackPayload": copy.deepcopy(dict(before)) if before else None,
        "preserved": {key: copy.deepcopy((before or {}).get(key)) for key in ("taxonomyRedirects", "taxonomyState") if (before or {}).get(key) is not None},
    }
    payload["hashLedger"] = {"before": payload["before"]["hash"], "taxonomy": canonical_hash(payload["userState"]["taxonomy"]),
                             "overlays": canonical_hash(payload["userState"]["overlays"]), "previousSnapshot": canonical_hash(before) if before else None}
    payload["hashLedger"]["receipt"] = canonical_hash(payload)
    path, created = store.write_migration_backup(source_epoch, generation, payload)
    return str(path), created


def record_publication(store: SQLiteStore, *, source_epoch: str, generation: int, inputs: Mapping[str, Any], outcome: str,
                       snapshot: Mapping[str, Any] | None = None, snapshot_revision: int | None = None, detail: str | None = None) -> None:
    """Append what actually happened for this attempt (published / discarded-stale / validation-failed)."""
    record: dict[str, Any] = {
        "outcome": outcome,
        "inputFences": {"taxonomy": inputs.get("taxonomyRevision", 0), "overlays": inputs.get("overlayRevision", 0), "config": inputs.get("configRevision", 0),
                        "snapshotRevisionBefore": int(inputs.get("snapshotRevision", 0))},
    }
    if snapshot is not None:
        record["afterHash"] = identity_ledger(snapshot)["hash"]
        record["afterCounts"] = identity_ledger(snapshot)["counts"]
    if snapshot_revision is not None:
        record["snapshotRevision"] = int(snapshot_revision)
    if detail:
        record["detail"] = detail[:200]
    store.append_migration_audit(source_epoch, generation, record)


def rollback(store: SQLiteStore, *, source_epoch: str, generation: int, trace: Any = None) -> tuple[bool, str, int | None]:
    """Roll back to the original backup, bound to the recorded published snapshot revision."""
    ok, reason, revision = store.rollback_migration(source_epoch, generation)
    if trace is not None:
        state = store.state() or {}
        mapped = {"snapshot-revision-changed": "rollback.snapshot_revision_changed", "source-changed": "rollback.source_changed",
                  "taxonomy-changed": "rollback.user_state_changed", "overlays-changed": "rollback.user_state_changed", "config-changed": "rollback.user_state_changed"}
        trace.emit("rollback.ok" if ok else "rollback.rejected", source_id=state.get("server_id"), source_epoch=state.get("epoch"),
                   revisions={"generation": generation, **({"snapshot": revision} if revision else {})}, reasons={} if ok else {mapped.get(reason, "rollback.other"): 1})
    return ok, reason, revision


def validate_migration(before: Mapping[str, Any] | None, after: Mapping[str, Any], inputs: Mapping[str, Any]) -> list[str]:
    """Phase-1 validation of a freshly built snapshot; non-empty means do not publish."""
    errors: list[str] = []
    if not isinstance(after.get("papers"), list):
        return ["snapshot has no papers array"]
    paper_ids = {p.get("id") for p in after["papers"] if isinstance(p, Mapping)}
    catalog = after.get("facetCatalog")
    if isinstance(catalog, Mapping) and isinstance(after.get("paperFacets"), list):
        errors += check_facet_invariants(after["paperFacets"], catalog)
        if {row.get("paperId") for row in after["paperFacets"]} - paper_ids:
            errors.append("paperFacets reference unknown paper")
    ledger = after.get("conceptQualityLedger")
    if isinstance(ledger, Mapping):
        if set(map(str, ledger.get("active", []))) & set(map(str, ledger.get("suppressed", []))):
            errors.append("concept ledger active/suppressed overlap")
        concept_ids = {c.get("id") for c in after.get("concepts", []) if isinstance(c, Mapping)}
        if concept_ids & set(map(str, ledger.get("suppressed", []))):
            errors.append("suppressed concept present as active graph node")
    stats = after.get("fieldStats")
    if isinstance(stats, Mapping) and isinstance(stats.get("rows"), list):
        if stats.get("knownPrimary", 0) + stats.get("unknown", 0) != stats.get("paperCount"):
            errors.append("fieldStats known primary + unknown != paper count")
        if any(not (r["primary"] <= r["direct"] <= r["inclusive"]) for r in stats["rows"]):
            errors.append("fieldStats primary<=direct<=inclusive violated")
    # User-owned state must survive the migration.
    overlays = inputs.get("overlays") if isinstance(inputs.get("overlays"), Mapping) else {}
    pins = overlays.get("pins") if isinstance(overlays.get("pins"), Mapping) else {}
    known = {str(row.get("id")) for key in ("concepts", "fields", "papers") for row in after.get(key, []) if isinstance(row, Mapping)}
    resolvable = (known | set(str(x) for x in (after.get("taxonomyRedirects") or {})) | {str(x.get("id") if isinstance(x, Mapping) else x) for x in (ledger or {}).get("raw", [])}) if isinstance(ledger, Mapping) else known
    missing = [k for k, v in pins.items() if v and str(k) not in resolvable and str(k).startswith(("concept:", "field:")) and str(k).split(":")[1] == (after.get("source") or {}).get("id")]
    if missing:
        errors.append("pinned entity lost in migration")
    if isinstance(before, Mapping):
        for old, target in (before.get("taxonomyRedirects") or {}).items():
            if old not in (after.get("taxonomyRedirects") or {}) and old not in known:
                # A redirect may legitimately be dropped only once its target is gone.
                if target in known:
                    errors.append("taxonomy redirect lost in migration")
                    break
    return errors
