import json
from pathlib import Path

import jsonschema

from atlas.storage import SQLiteStore
from atlas.sync import SyncManager
from atlas.zotero import ZoteroClient


ROOT = Path(__file__).resolve().parents[2]


def manager(tmp_path):
    return SyncManager(SQLiteStore(tmp_path), ZoteroClient(), embedding_provider="fake")


def test_sample_closes_collections_and_preserves_full_edge_counts(tmp_path):
    m = manager(tmp_path)
    source = json.loads((ROOT / "tests/fixtures/snapshot.json").read_text())
    source["overview"]["totalEdges"] = 71
    m.store.publish_snapshot(source, 0)
    detail = m.snapshot("detail", 1)
    visible = {p["id"] for p in detail["papers"]}
    assert all(set(c["paperIds"]) <= visible for c in detail["collections"])
    assert detail["collections"][0]["hiddenPaperCount"] == 1
    assert detail["visibility"]["fullEdgeCount"] == 71
    assert detail["visibility"]["visibleEdges"] + detail["visibility"]["hiddenEdges"] == 71
    assert detail["overview"]["totalEdges"] == 71


def test_empty_pending_and_error_snapshots_validate_schema(tmp_path):
    m = manager(tmp_path)
    schema = json.loads((ROOT / "docs/snapshot.schema.json").read_text())
    empty = m.snapshot()
    state, _ = m.store.ensure_source("test", 44)
    pending = m._base_graph([], [], [], state, {"provider": "fake", "state": "pending"})
    failed = m._base_graph([], [], [], state, {"provider": "fake", "state": "error"}, message="failure", previous=pending)
    for value in (empty, pending, failed):
        jsonschema.validate(value, schema)


def test_deleted_paper_archives_compatibility_and_entity_overlays(tmp_path):
    m = manager(tmp_path)
    state, _ = m.store.ensure_source("test", 44)
    a, b = "zotero:test:0:A1", "zotero:test:0:B2"
    payload = {"entities": {a: {"pinned": True}, b: {"pinned": True}},
               "paperFields": {a: ["field:x"], b: ["field:y"]},
               "paperConcepts": {a: ["concept:x"], b: ["concept:y"]},
               "conceptMemberships": {a: ["concept:x"], b: ["concept:y"]},
               "pins": {a: True, b: True, "field:x": True},
               "aliases": {a: ["alias"], b: ["other"]},
               "memberships": [{"paperId": a, "fieldId": "field:x"}, {"paperId": b, "fieldId": "field:y"}]}
    m.store.cas_json("overlays", payload, 0, source=state)
    m.store.replace_source_rows(state, [], [], [], {(0, "A1")})
    revision, actual, _ = m.store.read_json("overlays")
    for key in ("entities", "paperFields", "paperConcepts", "conceptMemberships", "pins", "aliases"):
        assert a not in actual[key]
        assert b in actual[key]
    assert actual["pins"]["field:x"] is True
    assert actual["memberships"] == [{"paperId": b, "fieldId": "field:y"}]
    assert revision == 2
    assert any(entry["entityId"] == a and entry["epoch"] == state["epoch"] for entry in actual["archived"])
    m.store.replace_source_rows(state, [], [], [], {(0, "A1")})
    assert m.store.read_json("overlays")[0] == revision
    # A subsequent source restoration does not silently retarget archived edits.
    restored = {"library_id": 0, "key": "A1", "version": 2, "item_type": "journalArticle", "payload": {}}
    m.store.replace_source_rows(state, [restored], [], [])
    assert a not in m.store.read_json("overlays")[1]["paperFields"]
    assert any(entry["entityId"] == a for entry in m.store.read_json("overlays")[1]["archived"])
