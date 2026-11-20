"""TAXV2-036: bounded, local, privacy-safe structured trace (enums and counts only)."""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import pytest

from atlas import migration, sync as sync_module, trace as trace_module
from atlas.storage import SQLiteStore
from atlas.trace import COUNT_KEYS, EVENTS, MAX_INT, REASON_KEYS, RECIPE_KEYS, REVISION_KEYS, TraceLog, sanitize, summarize_snapshot
from tests.backend.test_taxonomy_v2_persistence import GatedEngine, Replay, _manager, _serve, _settle, _src

ROOT = Path(__file__).parents[2]
# Private-looking path is CONSTRUCTED at runtime so no literal home-directory string exists in the repository.
HOME_LIKE_PATH = "/".join(["", "".join(["Us", "ers"]), "someone", "data"])
SENTINELS = ("SECRET-TITLE-QQ77", "SECRET-ABSTRACT-QQ77", "secret-tag-qq77", "SECRET-EXTRA-QQ77")


class SentinelReplay(Replay):
    def __init__(self):
        super().__init__()
        data = {"itemType": "journalArticle", "title": "SECRET-TITLE-QQ77 QIIME 2 microbiome amplicon study", "abstractNote": "SECRET-ABSTRACT-QQ77 16S marker gene",
                "tags": [{"tag": "secret-tag-qq77"}, {"tag": "burden"}], "extra": "MSC2020: 11M20\nnote: SECRET-EXTRA-QQ77"}
        self.items = {"A1": (1, {"key": "A1", "version": 1, "data": data}),
                      "B1": (1, {"key": "B1", "version": 1, "data": {"itemType": "journalArticle", "title": "An unrelated note", "tags": []}})}


def _records(manager):
    return manager.trace.read()


# --------------------------------------------------------------------------- sanitizer
def test_sanitizer_is_a_fail_closed_whitelist_of_enums_and_bounded_integers():
    record = sanitize("build.complete", source_id="raw-server-id", source_epoch="raw-epoch",
                      recipe={"recipe": "atlas-graph-v3", "title": "Secret Title", "ruleVersion": "has spaces and /paths/"},
                      revisions={"taxonomy": 3, "token": 7, "generation": True},
                      counts={"papers": 5, "concepts": 10 ** 12, "abstract": 4, "evidence_cache_parts": "7"},
                      reasons={"abstain.ambiguous": 2, "free text": 1, "suppress.low-termhood": -5})
    assert record["event"] == "build.complete" and record["v"] == 1
    assert re.fullmatch(r"[0-9a-f]{16}", record["source"]) and re.fullmatch(r"[0-9a-f]{16}", record["epoch"])
    assert "raw-server-id" not in json.dumps(record) and "raw-epoch" not in json.dumps(record)
    assert record["recipe"] == {"recipe": "atlas-graph-v3"}
    assert record["revisions"] == {"taxonomy": 3}                                 # unknown key and bool dropped
    assert record["counts"] == {"papers": 5, "concepts": MAX_INT}                 # whitelisted key, value clamped to the bound; "abstract" and "7" (string) dropped
    assert record["reasons"] == {"abstain.ambiguous": 2, "suppress.low-termhood": 0}    # unknown reason dropped, negative clamped
    assert record["dropped"] == 7
    assert sanitize("not.an.event") is None
    assert all(isinstance(v, (int, float)) for v in (record["t"], record["v"]))
    # arbitrary objects can never be logged: nested structures are not integers/enums and are dropped
    nested = sanitize("build.start", counts={"papers": {"title": "x"}, "fields": [1, 2]}, reasons={"abstain.ambiguous": "secret"})
    assert "counts" not in nested and "reasons" not in nested and nested["dropped"] == 3


def test_summarize_snapshot_yields_only_enum_counts_from_public_fields():
    snapshot = {"papers": [{"id": "x"}] * 3, "fields": [], "concepts": [{"id": "c"}], "memberships": [], "edges": [],
                "facetCatalog": {"entities": [{"id": "e"}] * 4},
                "paperFacets": [{"paperId": "a", "status": "classified", "directMemberships": []}, {"paperId": "b", "status": "ambiguous", "directMemberships": []},
                                {"paperId": "c", "status": "abstain", "directMemberships": []},
                                {"paperId": "d", "status": "abstain", "directMemberships": [{"kind": "subject"}]}],
                "conceptQualityLedger": {"counts": {"raw": 5, "active": 3, "suppressed": 2, "protected": 1, "user": 0},
                                         "entries": [{"id": "s1", "status": "suppressed", "reason": "low-termhood"}, {"id": "s2", "status": "suppressed", "reason": "weird free text with Title"}]}}
    counts, reasons = summarize_snapshot(snapshot)
    assert counts["papers"] == 3 and counts["facet_entities"] == 4 and counts["concepts_suppressed"] == 2 and counts["paper_facet_rows"] == 4
    assert reasons == {"status.classified": 1, "abstain.ambiguous": 1, "abstain.no_subject_evidence": 1, "abstain.below_primary_threshold": 1,
                       "suppress.low-termhood": 1, "suppress.other": 1}
    assert set(counts) <= COUNT_KEYS and set(reasons) <= REASON_KEYS


# --------------------------------------------------------------------------- retention
def test_trace_retention_is_bounded_by_records_and_bytes_and_never_raises(tmp_path):
    log = TraceLog(tmp_path, max_records=50, max_bytes=8 * 1024)
    for i in range(1000):
        assert log.emit("build.complete", counts={"papers": i}, reasons={"abstain.ambiguous": i})
    records = log.read()
    assert 0 < len(records) <= 50 and log.path.stat().st_size <= 8 * 1024
    assert records[-1]["counts"]["papers"] == 999                                   # newest records are kept
    assert not list(log.path.parent.glob(".trace.*"))                               # no temp files left behind
    broken = TraceLog(tmp_path / "does" / "not" / "exist", max_records=5)
    (tmp_path / "does").write_text("a file blocks the directory")                  # unwritable location: emission fails soft
    assert broken.emit("build.start") is False


# --------------------------------------------------------------------------- positive emission through the REAL pipeline
def _walk_strings(value, path=()):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk_strings(key, path + ("key",)); yield from _walk_strings(item, path + (key,))
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item, path)
    elif isinstance(value, str):
        yield path, value


def test_real_sync_emits_build_and_publish_trace_with_counts_and_reason_enums_only(tmp_path):
    manager = _manager(tmp_path, replay=SentinelReplay())
    manager.sync(); _settle(manager)
    records = _records(manager)
    events = [r["event"] for r in records]
    assert events[:2] == ["build.start", "build.complete"] and events[-1] == "publish.ok" and set(events) <= EVENTS
    complete = next(r for r in records if r["event"] == "build.complete")
    snap = manager.snapshot()
    assert complete["counts"]["papers"] == len(snap["papers"]) == 2 and complete["counts"]["facet_entities"] == len(snap["facetCatalog"]["entities"])
    assert complete["counts"]["concepts_suppressed"] == snap["conceptQualityLedger"]["counts"]["suppressed"] >= 1
    assert complete["reasons"]["suppress.context-free-boilerplate"] >= 1                 # suppression reason enum counted ("burden")
    assert complete["reasons"]["status.classified"] == 1 and complete["reasons"]["abstain.no_subject_evidence"] == 1     # abstention reason enum counted
    ok = records[-1]
    assert ok["revisions"]["snapshot"] == manager.store.get_snapshot()[0] and ok["recipe"] == {"recipe": "atlas-graph-v3", "ruleVersion": "taxonomy-v4", "ontologyPolicy": "ontology-label-policy-v2"}
    manager.stop_watcher()
    # PRIVACY: nothing identifying survives anywhere in the file
    state = manager.store.state(); text = manager.trace.path.read_text(encoding="utf-8")
    forbidden = list(SENTINELS) + ["A1", "B1", "zotero:", "persist-db", state["epoch"], str(tmp_path), "Bearer", "http", "/", "\\\\", "11M20", "marker gene", "QIIME", "concept:", "paper"]
    for needle in forbidden:
        assert needle not in text.replace('"papers"', "").replace('"paper_facet_rows"', ""), needle
    allowed_recipe = {"atlas-graph-v3", "taxonomy-v4", "ontology-label-policy-v2"}
    for record in manager.trace.read():
        assert set(record) <= {"v", "t", "event", "source", "epoch", "recipe", "revisions", "counts", "reasons", "dropped"}
        assert set(record.get("revisions", {})) <= REVISION_KEYS and set(record.get("counts", {})) <= COUNT_KEYS and set(record.get("reasons", {})) <= REASON_KEYS
        assert set(record.get("recipe", {})) <= RECIPE_KEYS and "dropped" not in record                  # the pipeline itself never hands over non-whitelisted data
        for path, value in _walk_strings(record):
            if path[-1:] == ("key",):
                continue
            assert value in EVENTS or value in allowed_recipe or re.fullmatch(r"[0-9a-f]{16}", value), (path, value)


def test_stale_fence_and_validation_failure_and_rollback_are_traced_with_reason_enums(tmp_path, monkeypatch):
    GatedEngine.gate, GatedEngine.entered, GatedEngine.builds = threading.Event(), threading.Event(), 0
    manager = _manager(tmp_path, GatedEngine(tmp_path / "cache", provider="fake", allow_download=False), replay=SentinelReplay())
    manager.sync()
    assert GatedEngine.entered.wait(5)
    manager.store.cas_json("settings", {"semanticTopK": 3}, 0)                      # settings revision moves during the build
    GatedEngine.gate.set(); _settle(manager)
    records = _records(manager)
    stale = [r for r in records if r["event"] == "publish.stale"]
    assert stale and stale[0]["reasons"] == {"fence.config_changed": 1}
    assert [r["event"] for r in records if r["event"].startswith("publish.")][-1] == "publish.ok"               # retry published
    # validation failure
    monkeypatch.setattr(sync_module, "validate_migration", lambda *a, **k: ["synthetic problem with SECRET-TITLE-QQ77"])
    manager.request_reindex(); _settle(manager)
    failed = [r for r in manager.trace.read() if r["event"] == "publish.validation_failed"]
    assert failed and failed[-1]["reasons"] == {"validation.failed": 1}
    assert "SECRET" not in manager.trace.path.read_text(encoding="utf-8")                                   # problem text is never traced
    # rollback rejection is traced by enum
    state = manager.store.state()
    ok, reason, _ = migration.rollback(manager.store, source_epoch=state["epoch"], generation=state["generation"], trace=manager.trace)
    assert not ok and manager.trace.read()[-1]["event"] == "rollback.rejected"
    assert set(manager.trace.read()[-1]["reasons"]) <= REASON_KEYS
    manager.stop_watcher()


def test_trace_has_no_outbound_sink_and_no_endpoint(tmp_path):
    source = (ROOT / "atlas" / "trace.py").read_text(encoding="utf-8")
    for module in ("urllib", "socket", "requests", "http.client", "smtplib", "subprocess", "logging"):
        assert f"import {module}" not in source and f"from {module}" not in source, module
    manager = _manager(tmp_path, replay=SentinelReplay())
    manager.sync(); _settle(manager)
    call, stop = _serve(manager)
    try:
        for path in ("/api/v1/trace", "/api/v1/status/trace", "/trace"):
            status, body = call(path)
            assert status == 404 and "build.start" not in json.dumps(body)
        assert "trace" not in json.dumps(call("/api/v1/status")[1])
    finally:
        stop()


@pytest.mark.parametrize("key", ["recipe", "ruleVersion", "ontologyPolicy"])
def test_version_labels_follow_a_strict_per_key_grammar_so_private_strings_are_dropped(key):
    good = {"recipe": "atlas-graph-v3", "ruleVersion": "taxonomy-v4", "ontologyPolicy": "ontology-label-policy-v2"}
    assert sanitize("build.start", recipe={key: good[key]})["recipe"] == {key: good[key]}
    adversarial = ["zotero:persist-db:0:A1", "A1", "secret-token-abc123", "SECRET-TITLE-QQ77", "Bearer abc", HOME_LIKE_PATH, "atlas-graph-v3-extra", "taxonomy-v", "v3",
                   "atlas-graph-v3\nINJECT", "ontology-label-policy-v2 ", "atlas-graph-vX", "taxonomy-v1234", "", None, 3, ["atlas-graph-v3"]]
    for value in adversarial:
        record = sanitize("build.start", recipe={key: value})
        assert "recipe" not in record and record["dropped"] == 1, (key, value)
        assert "secret" not in json.dumps(record).casefold() and "A1" not in json.dumps(record)
    # a value valid for ANOTHER key's grammar is not valid here
    other = next(v for k, v in good.items() if k != key)
    assert "recipe" not in sanitize("build.start", recipe={key: other})
    # and a mixed payload keeps only the conforming entries
    assert sanitize("build.start", recipe={"recipe": "atlas-graph-v3", "ruleVersion": "item-ID-99"})["recipe"] == {"recipe": "atlas-graph-v3"}
