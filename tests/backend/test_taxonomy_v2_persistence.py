"""Persistence, fencing, migration and API contracts for taxonomy v2 (real SQLite + real GraphEngine)."""
from __future__ import annotations

import copy
import json
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import jsonschema
import pytest

from atlas import migration
from atlas.graph import GraphEngine
from atlas.server import AtlasHTTPServer
from atlas.storage import SQLiteStore
from atlas.sync import SyncManager
from atlas.zotero import ZoteroClient

ROOT = Path(__file__).parents[2]


class Replay:
    def __init__(self, title="QIIME 2 microbiome amplicon study", extra=None):
        self.server_id = "persist-db"
        data = {"itemType": "journalArticle", "title": title, "abstractNote": "16S marker gene data", "tags": [{"tag": "burden"}, {"tag": "disease burden"}]}
        if extra:
            data["extra"] = extra
        self.items = {"A1": (1, {"key": "A1", "version": 1, "data": data}),
                      "B1": (1, {"key": "B1", "version": 1, "data": {"itemType": "journalArticle", "title": "Bacteriophage therapy", "tags": [{"tag": "disease burden"}]}})}

    def __call__(self, method, url, headers):
        h = {"Zotero-Server-ID": self.server_id, "API-Schema": "44", "Zotero-Schema-Version": "44"}
        if "/collections" in url:
            return {"status": 200, "headers": h, "body": {} if "format=versions" in url else []}
        if "format=versions" in url:
            return {"status": 200, "headers": h, "body": {k: v[0] for k, v in self.items.items()}}
        return {"status": 200, "headers": h, "body": [v[1] for v in self.items.values()]}


class GatedEngine(GraphEngine):
    """Real engine that can pause inside ``build`` to open a race window."""
    gate: threading.Event | None = None
    entered: threading.Event | None = None
    builds = 0

    def build(self, papers, **kwargs):
        GatedEngine.builds += 1
        if GatedEngine.entered is not None and GatedEngine.gate is not None:
            GatedEngine.entered.set(); GatedEngine.gate.wait(5)
            GatedEngine.entered = GatedEngine.gate = None     # only the first build is held
        return super().build(papers, **kwargs)


def _manager(tmp_path, engine=None, replay=None):
    engine = engine or GraphEngine(tmp_path / "cache", provider="fake", allow_download=False)
    manager = SyncManager(SQLiteStore(tmp_path), ZoteroClient(transport=replay or Replay()), graph_engine=engine, poll_interval=5, embedding_provider="fake")
    return manager


def _settle(manager):
    manager.wait_for_idle()
    # re-queue loop may still be running the second pass
    for _ in range(100):
        manager.wait_for_idle()
        if not manager._reindex_pending:
            break


def _serve(manager):
    server = AtlasHTTPServer(("127.0.0.1", 0), manager)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    def call(path, method="GET", body=None, revision=None):
        headers = {"Authorization": "Bearer " + server.token, "Content-Type": "application/json"}
        if revision is not None:
            headers["If-Match"] = str(revision)
        request = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None, headers=headers)
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)
    def stop():
        server.shutdown(); server.server_close(); thread.join(); manager.stop_watcher()
    return call, stop


@pytest.fixture()
def api(tmp_path):
    manager = _manager(tmp_path)
    manager.sync(); _settle(manager)
    call, stop = _serve(manager)
    yield manager, call
    stop()


def _src(manager):
    s = manager.store.state()
    return {"sourceId": s["server_id"], "sourceEpoch": s["epoch"]}


def _facet(manager, ontology):
    snap = manager.snapshot()
    return next(e["id"] for e in snap["facetCatalog"]["entities"] if e["ontologyKey"] == ontology)


# ------------------------------------------------------------------------ API
def test_real_pipeline_snapshot_validates_schema_and_ledger_counts(api):
    manager, _ = api
    snap = manager.snapshot()
    jsonschema.validate(snap, json.loads((ROOT / "docs" / "snapshot.schema.json").read_text()))
    assert snap["recipe"] if "recipe" in snap else True
    assert snap["fences"]["taxonomyRevision"] == 0 and snap["categoryColorRegistry"]["entries"]
    ledger = snap["conceptQualityLedger"]
    assert ledger["counts"]["raw"] == len(ledger["raw"]) and set(ledger["active"]).isdisjoint(ledger["suppressed"])
    assert any(e["reason"] == "context-free-boilerplate" for e in ledger["entries"])        # "burden" tag suppressed
    assert any(c["label"]["en"] == "disease burden" for c in snap["concepts"])


def test_taxonomy_config_roundtrip_control_keys_not_persisted_and_extensions_retained(api):
    manager, call = api
    src = _src(manager); prefix = f"facet:{src['sourceId']}:{src['sourceEpoch']}:"
    life = _facet(manager, "subject:life-science")
    config = {"entities": [
        {"id": prefix + "subject:systems", "kind": "subject", "label": {"en": "Systems Biology"}, "broaderIds": ["subject:life-science"], "aliases": ["sysbio"]},
        {"id": prefix + "tool:nextflow", "kind": "tool", "label": "Nextflow", "broaderIds": []}]}
    status, body = call("/api/v1/taxonomy", "PUT", {**src, "facetCatalogConfig": config, "inputState": {"status": "valid-custom"}}, 0)
    assert status == 200, body
    stored = manager.store.read_json("taxonomy")[1]
    systems = next(e for e in stored["facetCatalogConfig"]["entities"] if e["id"].endswith("subject:systems"))
    assert systems["broaderIds"] == [life]                                   # canonical entity.id, alias normalised
    # A legacy fields-only edit keeps the facet config and never persists control keys.
    status, body = call("/api/v1/taxonomy", "PUT", {**src, "fields": [], "entityType": "field", "entityId": "x", "patch": {}, "label": "ignored"}, 1)
    assert status in (200, 400)
    status, body = call("/api/v1/taxonomy", "PUT", {**src, "fields": []}, manager.store.read_json("taxonomy")[0])
    assert status == 200
    stored = manager.store.read_json("taxonomy")[1]
    assert set(stored) <= {"fields", "concepts", "facetCatalogConfig", "inputState"}
    assert stored["facetCatalogConfig"]["entities"] and stored["inputState"]["status"] == "valid-custom"
    assert call("/api/v1/taxonomy", "PUT", {**src, "fields": []}, 0)[0] == 409           # stale CAS


@pytest.mark.parametrize("mutate,label", [
    (lambda c, p: c["entities"].append({"id": "facet:other:epoch:tool:x", "kind": "tool", "label": "X"}), "foreign source fence"),
    (lambda c, p: c["entities"].append({"id": p + "tool:x", "kind": "tool", "label": "X", "surprise": 1}), "unknown entity key"),
    (lambda c, p: c.update({"junk": 1}), "unknown config key"),
    (lambda c, p: c["entities"].append({"id": p + "tool:x", "kind": "tool", "label": "X", "broaderIds": ["subject:life-science"]}), "cross-kind parent"),
    (lambda c, p: c["entities"].append({"id": p + "tool:x", "kind": "tool", "label": "X", "broaderIds": ["tool:missing"]}), "unknown parent"),
    (lambda c, p: c["entities"].extend([{"id": p + "tool:a", "kind": "tool", "label": "A", "broaderIds": ["tool:b"], "ontologyKey": "tool:a"},
                                         {"id": p + "tool:b", "kind": "tool", "label": "B", "broaderIds": [p + "tool:a"], "ontologyKey": "tool:b"}]), "cycle through alias"),
    (lambda c, p: c["entities"].append({"id": p + "tool:x", "kind": "bogus", "label": "X"}), "bad kind"),
])
def test_taxonomy_config_rejections_do_not_advance_cas(api, mutate, label):
    manager, call = api
    src = _src(manager); prefix = f"facet:{src['sourceId']}:{src['sourceEpoch']}:"
    config = {"entities": []}; mutate(config, prefix)
    status, _ = call("/api/v1/taxonomy", "PUT", {**src, "facetCatalogConfig": config}, 0)
    assert status == 400, label
    assert manager.store.read_json("taxonomy")[0] == 0


def test_generated_snapshot_blocks_are_not_writable_taxonomy_payloads(api):
    manager, call = api
    src = _src(manager)
    for key in ("paperFacets", "fieldStats", "categoryColorRegistry", "conceptQualityLedger"):
        assert call("/api/v1/taxonomy", "PUT", {**src, key: []}, 0)[0] == 400, key
    assert manager.store.read_json("taxonomy")[0] == 0


def test_overlay_facet_overrides_are_canonicalised_and_validated(api):
    manager, call = api
    src = _src(manager); pid = manager.snapshot()["papers"][0]["id"]
    topology = _facet(manager, "subject:topology")
    status, body = call("/api/v1/overlays", "PUT", {**src, "entityType": "paper", "entityId": pid,
                        "patch": {"facetAssignments": ["subject:topology"], "primaryFacetOverrides": {"subject": "subject:topology"}}}, 0)
    assert status == 200, body
    entity = manager.store.read_json("overlays")[1]["entities"][pid]
    assert entity["facetAssignments"] == [{"facetId": topology}] and entity["primaryFacetOverrides"] == {"subject": topology}
    revision = manager.store.read_json("overlays")[0]
    for patch in ({"facetAssignments": ["facet:missing"]}, {"primaryFacetOverrides": {"tool": topology}}, {"primaryFacetOverrides": {"bogus": topology}},
                  {"facetAssignments": [{"facetId": topology, "evil": 1}]}):
        assert call("/api/v1/overlays", "PUT", {**src, "entityType": "paper", "entityId": pid, "patch": patch}, revision)[0] == 400
    assert call("/api/v1/overlays", "PUT", {**src, "entityType": "paper", "entityId": pid, "patch": {"pinned": True}}, 0)[0] == 412
    manager.request_reindex(); _settle(manager)
    row = next(r for r in manager.snapshot()["paperFacets"] if r["paperId"] == pid)
    assert row["primaryByFacet"]["subject"] == topology and topology in {m["facetId"] for m in row["directMemberships"]}


def test_resolve_endpoint_resolves_suppressed_ledger_concepts_without_fake_nodes(api):
    manager, call = api
    snap = manager.snapshot(); src = _src(manager)
    suppressed = next(e["id"] for e in snap["conceptQualityLedger"]["entries"] if e["status"] == "suppressed")
    assert suppressed not in {c["id"] for c in snap["concepts"]}
    q = lambda eid, kind="concept": f"/api/v1/taxonomy/resolve?entityType={kind}&entityId={urllib.parse.quote(eid)}"
    status, body = call(q(suppressed))
    assert status == 200 and body["status"] == "suppressed" and body["inGraph"] is False and body["canonicalId"] == suppressed
    assert suppressed not in {c["id"] for c in manager.snapshot()["concepts"]}            # still not an active node
    active = snap["concepts"][0]["id"]
    assert call(q(active))[1]["status"] in {"active", "protected"}
    foreign = suppressed.replace(src["sourceEpoch"], "other-epoch")
    assert call(q(foreign))[0] == 404 and call(q("concept:nope:nope:x"))[0] == 404


# ---------------------------------------------------------- fencing / concurrency
def test_publish_fenced_rejects_stale_inputs_and_keeps_original_receipt(tmp_path):
    store = SQLiteStore(tmp_path)
    state, _ = store.ensure_source("source-a", 44, epoch_override="epoch-a")
    snap = {"source": {"id": "source-a", "serverId": "source-a", "epoch": "epoch-a"}, "papers": [], "fields": [], "concepts": []}
    inputs = store.snapshot_inputs()
    assert store.publish_snapshot_fenced(snap, state["generation"], expected_source=state, expected_taxonomy_revision=0, expected_overlay_revision=0, expected_config_revision=0) == 1
    first, created = store.write_migration_backup("epoch-a", state["generation"], {"marker": "original"})
    second, created2 = store.write_migration_backup("epoch-a", state["generation"], {"marker": "overwrite-attempt"})
    assert created and not created2 and first == second and store.migration_backup("epoch-a", state["generation"])["marker"] == "original"
    for table, kw in (("taxonomy", "expected_taxonomy_revision"), ("overlays", "expected_overlay_revision"), ("settings", "expected_config_revision")):
        store.cas_json(table, {"x": 1}, 0, source=state if table != "settings" else None)
        assert store.publish_snapshot_fenced(snap, state["generation"], **{kw: 0}) is None, table
    assert inputs["taxonomyRevision"] == 0


def test_slow_build_with_concurrent_taxonomy_write_never_publishes_stale_graph(tmp_path):
    GatedEngine.gate, GatedEngine.entered, GatedEngine.builds = threading.Event(), threading.Event(), 0
    manager = _manager(tmp_path, GatedEngine(tmp_path / "cache", provider="fake", allow_download=False))
    manager.sync()
    assert GatedEngine.entered.wait(5)                       # first build is in flight with taxonomy revision 0
    src = _src(manager); prefix = f"facet:{src['sourceId']}:{src['sourceEpoch']}:"
    ok, revision, _ = manager.store.cas_json("taxonomy", {"fields": [], "concepts": [], "facetCatalogConfig": {"version": 1, "entities": [
        {"id": prefix + "tool:nextflow", "kind": "tool", "label": "Nextflow", "aliases": ["qiime 2"], "broaderIds": []}]}, "inputState": {"status": "valid-custom"}}, 0,
        source=manager.store.state())
    assert ok and revision == 1
    GatedEngine.gate.set()
    _settle(manager)
    snap = manager.snapshot()
    assert GatedEngine.builds >= 2                            # stale result discarded, rebuilt from latest inputs
    assert snap["fences"]["taxonomyRevision"] == 1
    state = manager.store.state()
    # Audit truth: the stale first attempt is recorded as discarded; the retry is the one actually published, with its own fences/hash.
    audit = manager.store.migration_audit(state["epoch"], state["generation"])["attempts"]
    outcomes = [a["outcome"] for a in audit]
    assert outcomes[0] == "discarded-stale" and outcomes[-1] == "published"
    assert audit[0]["inputFences"]["taxonomy"] == 0 and audit[-1]["inputFences"]["taxonomy"] == 1
    assert audit[0]["afterHash"] != audit[-1]["afterHash"]
    assert audit[-1]["snapshotRevision"] == manager.store.get_snapshot()[0]
    assert any(e["id"] == prefix + "tool:nextflow" for e in snap["facetCatalog"]["entities"])
    assert snap["taxonomyState"]["status"] == "valid-custom"
    manager.stop_watcher()


def test_slow_build_with_concurrent_settings_write_is_requeued(tmp_path):
    GatedEngine.gate, GatedEngine.entered, GatedEngine.builds = threading.Event(), threading.Event(), 0
    manager = _manager(tmp_path, GatedEngine(tmp_path / "cache", provider="fake", allow_download=False))
    manager.sync()
    assert GatedEngine.entered.wait(5)
    manager.store.cas_json("settings", {"semanticTopK": 3}, 0)
    GatedEngine.gate.set()
    _settle(manager)
    assert GatedEngine.builds >= 2 and manager.snapshot()["fences"]["configRevision"] == 1
    manager.stop_watcher()


def test_invalid_or_disabled_taxonomy_serves_previous_valid_snapshot_with_reason_and_recovery(tmp_path):
    manager = _manager(tmp_path)
    manager.sync(); _settle(manager)
    before = manager.snapshot()
    assert before["taxonomyState"]["status"] == "absent" and before["fields"]
    for state in ("disabled", "invalid"):
        revision, _, _ = manager.store.read_json("taxonomy")
        ok, _, _ = manager.store.cas_json("taxonomy", {"fields": [], "inputState": {"status": state, "reason": "operator test"}}, revision, source=manager.store.state())
        assert ok
        manager.request_reindex(); _settle(manager)
        after = manager.snapshot()
        assert after["taxonomyState"]["status"] == state and after["taxonomyState"]["reason"] == "operator test"
        assert after["taxonomyState"]["recovery"]
        jsonschema.validate(after, json.loads((ROOT / "docs" / "snapshot.schema.json").read_text()))
        assert [f["id"] for f in after["fields"]] == [f["id"] for f in before["fields"]]          # builtin hierarchy not re-enabled/dropped
        assert after["papers"] == before["papers"]
    manager.stop_watcher()


# ----------------------------------------------------------------- migration
def _legacy_v2_store(tmp_path):
    """A manager whose store holds a v2-shaped snapshot (fields/concepts, no v3 blocks, recipe atlas-graph-v2)."""
    manager = _manager(tmp_path)
    manager.sync(); _settle(manager)
    snap = manager.snapshot()
    for key in ("facetCatalog", "paperFacets", "conceptQualityLedger", "categoryColorRegistry", "fieldStats", "taxonomyState", "fences"):
        snap.pop(key, None)
    snap["modelStatus"] = {**snap["modelStatus"], "recipeVersion": "atlas-graph-v2"}
    state = manager.store.state()
    assert manager.store.publish_snapshot(snap, state["generation"]) is not None
    return manager


def test_only_actual_recipe_migrations_write_a_full_backup_and_every_publication_is_audited(tmp_path):
    manager = _manager(tmp_path)
    manager.sync(); _settle(manager)
    store, state = manager.store, manager.store.state()
    assert store.migration_backup(state["epoch"], state["generation"]) is None            # plain first derivation: no full backup
    audit = store.migration_audit(state["epoch"], state["generation"])["attempts"]
    assert [a["outcome"] for a in audit] == ["published"] and audit[0]["snapshotRevision"] == store.get_snapshot()[0]
    assert audit[0]["afterHash"].startswith("sha256:") and audit[0]["inputFences"] == {"taxonomy": 0, "overlays": 0, "config": 0, "snapshotRevisionBefore": audit[0]["inputFences"]["snapshotRevisionBefore"]}
    manager.stop_watcher()
    # an actual recipe migration writes the immutable original once
    legacy = _legacy_v2_store(tmp_path / "legacy")
    legacy.request_reindex(); _settle(legacy)
    st = legacy.store.state(); receipt = legacy.store.migration_backup(st["epoch"], st["generation"])
    assert receipt and receipt["previousRecipeVersion"] == "atlas-graph-v2" and receipt["rollbackPayload"]["fields"]
    assert receipt["userState"] == {"taxonomy": {}, "overlays": {}} and "after" not in receipt and "afterHash" not in json.dumps(receipt["hashLedger"])
    original = receipt["hashLedger"]["receipt"]
    legacy.request_reindex(); _settle(legacy)
    assert legacy.store.migration_backup(st["epoch"], st["generation"])["hashLedger"]["receipt"] == original      # original untouched by later enrichments
    legacy.stop_watcher()


def test_real_pipeline_rollback_true_positive_and_all_rejections(tmp_path):
    manager = _legacy_v2_store(tmp_path)
    manager.request_reindex(); _settle(manager)
    store = manager.store; state = store.state(); epoch, generation = state["epoch"], state["generation"]
    receipt = store.migration_backup(epoch, generation)
    assert receipt and receipt["rollbackPayload"]["modelStatus"]["recipeVersion"] in {"atlas-graph-v2", "atlas-graph-v3"} and receipt["rollbackPayload"]["fields"]
    published = [a for a in store.migration_audit(epoch, generation)["attempts"] if a["outcome"] == "published"][-1]["snapshotRevision"]
    assert published == store.get_snapshot()[0]
    # bound to the RECORDED published revision: a caller cannot supply a different one
    assert store.rollback_migration(epoch, generation, expected_snapshot_revision=published + 7)[:2] == (False, "snapshot-revision-changed")
    # a newer publish (e.g. a later enrichment) rejects the rollback and leaves it untouched
    newer = copy.deepcopy(store.get_snapshot()[2]); assert store.publish_snapshot(newer, generation) == published + 1
    assert migration.rollback(store, source_epoch=epoch, generation=generation)[:2] == (False, "snapshot-revision-changed")
    # restore a clean state to test the true positive: re-enrich in a fresh store
    clean = _legacy_v2_store(tmp_path / "clean")
    clean.request_reindex(); _settle(clean)
    cs = clean.store.state()
    ok, reason, revision = migration.rollback(clean.store, source_epoch=cs["epoch"], generation=cs["generation"])
    assert ok and reason == "rolled-back"
    restored = clean.store.get_snapshot()
    assert restored[0] == revision and "facetCatalog" not in restored[2] and restored[2]["fences"]["rolledBackFrom"]
    assert clean.store.migration_audit(cs["epoch"], cs["generation"])["attempts"][-1]["outcome"] == "rolled-back"
    # after a rollback, a second rollback is rejected (no longer the published revision)
    assert migration.rollback(clean.store, source_epoch=cs["epoch"], generation=cs["generation"])[:2] == (False, "snapshot-revision-changed")
    clean.stop_watcher(); manager.stop_watcher()


@pytest.mark.parametrize("table,expected", [("overlays", "overlays-changed"), ("taxonomy", "taxonomy-changed"), ("settings", "config-changed")])
def test_rollback_refuses_when_user_state_changed_since_the_original_backup(tmp_path, table, expected):
    manager = _legacy_v2_store(tmp_path)
    manager.request_reindex(); _settle(manager)
    store = manager.store; state = store.state()
    store.cas_json(table, {"pins": {}} if table == "overlays" else {"x": 1}, 0, source=state if table != "settings" else None)
    before = store.get_snapshot()
    assert migration.rollback(store, source_epoch=state["epoch"], generation=state["generation"])[:2] == (False, expected)
    assert store.get_snapshot()[0] == before[0]                      # nothing restored, nothing lost
    manager.stop_watcher()


def test_rollback_refuses_after_source_or_generation_change_and_without_a_published_record(tmp_path):
    manager = _legacy_v2_store(tmp_path)
    manager.request_reindex(); _settle(manager)
    store = manager.store; state = store.state(); epoch, generation = state["epoch"], state["generation"]
    manager.request_reindex(); _settle(manager)                      # generation advances
    assert store.rollback_migration(epoch, generation)[:2] == (False, "source-changed")
    other = SQLiteStore(tmp_path / "other"); st, _ = other.ensure_source("source-a", 44, epoch_override="epoch-a")
    assert other.rollback_migration("epoch-a", st["generation"])[:2] == (False, "no-receipt")
    other.write_migration_backup("epoch-a", st["generation"], {"sourceId": "source-a", "sourceEpoch": "epoch-a", "generation": st["generation"], "rollbackPayload": {"papers": []}, "expectedRevisions": {}})
    assert other.rollback_migration("epoch-a", st["generation"])[:2] == (False, "not-published")
    manager.stop_watcher()


def test_migration_records_are_bounded_per_epoch_and_overall(tmp_path):
    store = SQLiteStore(tmp_path)
    for epoch in ("e1", "e2", "e3", "e4"):
        for generation in range(1, 9):
            store.write_migration_backup(epoch, generation, {"marker": generation}); store.append_migration_audit(epoch, generation, {"outcome": "published"})
    store.prune_migrations()
    root = tmp_path / "migrations" / "atlas-graph-v3"
    assert len([d for d in root.iterdir() if d.is_dir()]) == SQLiteStore.KEEP_MIGRATION_EPOCHS
    for directory in root.iterdir():
        kept = sorted(int(f.name.split(".", 1)[0]) for f in directory.glob("*.json") if not f.name.endswith(".audit.json"))
        assert kept == list(range(9 - SQLiteStore.KEEP_MIGRATION_GENERATIONS, 9))
    assert store.migration_audit("e4", 8) is not None and store.migration_backup("e4", 1) is None


def test_color_registry_persists_append_only_across_rebuilds(tmp_path):
    manager = _manager(tmp_path)
    manager.sync(); _settle(manager)
    first = copy.deepcopy(manager.snapshot()["categoryColorRegistry"])
    src = _src(manager); prefix = f"facet:{src['sourceId']}:{src['sourceEpoch']}:"
    manager.store.cas_json("taxonomy", {"fields": [], "inputState": {"status": "intentional-empty-valid"}, "facetCatalogConfig": {"version": 1, "entities": [
        {"id": prefix + "tool:aaa-first", "kind": "tool", "label": "AAA"}]}}, 0, source=manager.store.state())
    manager.request_reindex(); _settle(manager)
    second = manager.snapshot()["categoryColorRegistry"]
    by_key = {e["categoryKey"]: e for e in second["entries"]}
    for entry in first["entries"]:
        assert by_key[entry["categoryKey"]] == entry                 # byte-identical, never recoloured
    assert prefix + "tool:aaa-first" in by_key and second["namespace"] == first["namespace"]
    assert {"atlas:light", "atlas:dark", "calm:light", "calm:dark", "mocha:light", "mocha:dark", "system"} <= set(by_key[prefix + "tool:aaa-first"]["hexByPaletteTheme"])
    manager.stop_watcher()


def test_palette_ids_match_assets():
    from atlas.category_colors import PALETTE_HUE_OFFSETS
    assets = json.loads((ROOT / "assets" / "palettes.json").read_text())
    assert set(PALETTE_HUE_OFFSETS) == {p["id"] for p in assets["palettes"]}


def test_msc_codes_flow_from_structured_extra_through_sync_to_protected_concepts(tmp_path):
    manager = _manager(tmp_path, replay=Replay(extra="MSC2020: 11M20, 14M25, 52B20\nPMID: 99Z99"))     # synthetic input
    manager.sync(); _settle(manager)
    snap = manager.snapshot()
    labels = {c["label"]["en"] for c in snap["concepts"]}
    assert {"11M20", "14M25", "52B20"} <= labels and "99Z99" not in labels
    assert not any("extra" in p or "sourcePayload" in p for p in snap["papers"])
    manager.stop_watcher()


def _decode_in_node(snapshot):
    import subprocess
    script = "import {decodeSnapshot} from './frontend/decoder.js'; let t=''; process.stdin.on('data',c=>t+=c).on('end',()=>{const d=decodeSnapshot(JSON.parse(t)); console.log(JSON.stringify({papers:d.papers.length,facets:d.paperFacets?.length,stats:d.fieldStats?.rows.length}));});"
    result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT, input=json.dumps(snapshot), text=True, capture_output=True)
    assert result.returncode == 0, result.stderr[-600:]
    return json.loads(result.stdout)


@pytest.mark.parametrize("sample", [1, 0])
def test_real_v3_snapshot_detail_projection_validates_schema_and_strict_decoder(tmp_path, sample):
    replay = Replay(extra="MSC2020: 11M20")
    manager = _manager(tmp_path, replay=replay)
    manager.sync(); _settle(manager)
    src = _src(manager); full = manager.snapshot()
    # user-excluded concept + a pin so the ledger has overlapping ownership buckets in a REAL snapshot
    burden = next(e["id"] for e in full["conceptQualityLedger"]["entries"] if e["status"] == "suppressed")
    manager.store.cas_json("overlays", {"entities": {burden: {"entityType": "concept", "excluded": True}}}, 0, source=manager.store.state())
    manager.request_reindex(); _settle(manager)
    full = manager.snapshot(); detail = manager.snapshot("detail", sample)
    schema = json.loads((ROOT / "docs" / "snapshot.schema.json").read_text())
    jsonschema.validate(full, schema); jsonschema.validate(detail, schema)
    out = _decode_in_node(detail); _decode_in_node(full)
    assert out["papers"] == sample and out["facets"] == sample
    assert {row["paperId"] for row in detail["paperFacets"]} == {p["id"] for p in detail["papers"]}
    ledger, full_ledger = detail["conceptQualityLedger"], full["conceptQualityLedger"]
    assert ledger["projected"] is True and ledger["counts"] == full_ledger["counts"]                       # aggregates stay FULL
    assert set(ledger["suppressed"]) == set(full_ledger["suppressed"])                                      # still resolvable
    assert set(ledger["active"]) <= {c["id"] for c in detail["concepts"]} and not ({c["id"] for c in detail["concepts"]} & set(ledger["suppressed"]))
    stats, full_stats = detail["fieldStats"], full["fieldStats"]
    assert stats["projected"] is True and stats["paperCount"] == full_stats["paperCount"] == 2 and stats["unknown"] == full_stats["unknown"]
    assert {r["fieldId"] for r in stats["rows"]} <= {f["id"] for f in detail["fields"]}
    # overview and the stored full aggregates are independent of the detail sample
    assert manager.snapshot("overview")["overview"] == full["overview"]
    assert manager.snapshot()["conceptQualityLedger"].get("projected") is None
    manager.stop_watcher()


# ------------------------------------------------------------- H2/M3: bounded cycle checks
def _entity(prefix, name, parents=()):
    return {"id": prefix + f"tool:{name}", "kind": "tool", "label": name, "broaderIds": [prefix + f"tool:{p}" for p in parents]}


def _prefix(manager):
    src = _src(manager)
    return src, f"facet:{src['sourceId']}:{src['sourceEpoch']}:"


class _CountingParents(dict):
    gets = 0
    def get(self, key, default=None):
        _CountingParents.gets += 1
        return super().get(key, default)


def test_cycle_detection_is_linear_on_stacked_diamonds_and_iterative_on_long_chains():
    from atlas.taxonomy import find_back_edges
    parents = _CountingParents({"top": []})
    previous = "top"
    for i in range(22):                              # 22 stacked diamonds: ~2^22 distinct paths for a path-copying DFS
        parents[f"a{i}"], parents[f"b{i}"], parents[f"j{i}"] = [previous], [previous], [f"a{i}", f"b{i}"]
        previous = f"j{i}"
    _CountingParents.gets = 0
    assert find_back_edges(parents) == []
    edges = sum(len(v) for v in parents.values())
    assert _CountingParents.gets <= 3 * (len(parents) + edges)                       # O(V+E) work, deterministic (no timers)
    chain = {f"c{i}": [f"c{i + 1}"] if i < 5000 else [] for i in range(5001)}        # far beyond the default recursion limit
    assert find_back_edges(chain) == []
    chain["c5000"] = ["c0"]
    assert find_back_edges(chain) == [("c5000", "c0")]


def test_http_write_accepts_long_chain_and_diamonds_and_rejects_cycles_and_oversize(api):
    manager, call = api
    src, prefix = _prefix(manager)
    chain = [_entity(prefix, f"c{i}", [f"c{i + 1}"] if i < 1499 else []) for i in range(1500)]
    status, body = call("/api/v1/taxonomy", "PUT", {**src, "facetCatalogConfig": {"entities": chain}}, 0)
    assert status == 200, body                                                       # no RecursionError / 500
    rev = manager.store.read_json("taxonomy")[0]
    diamonds, previous = [_entity(prefix, "top")], "top"
    for i in range(22):
        diamonds += [_entity(prefix, f"a{i}", [previous]), _entity(prefix, f"b{i}", [previous]), _entity(prefix, f"j{i}", [f"a{i}", f"b{i}"])]; previous = f"j{i}"
    assert call("/api/v1/taxonomy", "PUT", {**src, "facetCatalogConfig": {"entities": diamonds}}, rev)[0] == 200   # <100 nodes: lock released promptly
    rev = manager.store.read_json("taxonomy")[0]
    cyc = [_entity(prefix, "x", ["y"]), _entity(prefix, "y", ["z"]), _entity(prefix, "z", ["x"])]
    status, body = call("/api/v1/taxonomy", "PUT", {**src, "facetCatalogConfig": {"entities": cyc}}, rev)
    assert status == 400 and manager.store.read_json("taxonomy")[0] == rev           # explicit validation error, CAS not advanced
    big = [_entity(prefix, f"n{i}") for i in range(2001)]
    status, _ = call("/api/v1/taxonomy", "PUT", {**src, "facetCatalogConfig": {"entities": big}}, rev)
    assert status == 400 and manager.store.read_json("taxonomy")[0] == rev           # documented bound, never silently truncated


def test_stored_cyclic_config_is_surfaced_not_silently_dropped_at_build(tmp_path):
    manager = _manager(tmp_path)
    manager.sync(); _settle(manager)
    src, prefix = _prefix(manager)
    bad = {"fields": [], "inputState": {"status": "valid-custom"},
           "facetCatalogConfig": {"version": 1, "entities": [_entity(prefix, "x", ["y"]), _entity(prefix, "y", ["x"])]}}      # bypasses write validation
    manager.store.cas_json("taxonomy", bad, 0, source=manager.store.state())
    manager.request_reindex(); _settle(manager)
    snap = manager.snapshot()
    errors = snap["facetCatalog"]["validationErrors"]
    assert errors and all(e.startswith("cyclic parent edge dropped:") for e in errors) and len(errors) == 1
    assert snap["taxonomyState"]["status"] == "valid-custom" and "facet catalog issue" in snap["taxonomyState"]["reason"]
    jsonschema.validate(snap, json.loads((ROOT / "docs" / "snapshot.schema.json").read_text()))
    ents = {e["id"]: e for e in snap["facetCatalog"]["entities"]}
    assert not (prefix + "tool:y" in ents[prefix + "tool:x"]["broaderIds"] and prefix + "tool:x" in ents[prefix + "tool:y"]["broaderIds"])
    manager.stop_watcher()


# ------------------------------------------------------------- M1: entity patch on ledger-only concepts
def test_entity_patch_can_protect_a_suppressed_ledger_only_concept_and_existing_pins_survive(tmp_path):
    manager = _manager(tmp_path)
    manager.sync(); _settle(manager)
    call, stop = _serve(manager)
    try:
        src = _src(manager); snap = manager.snapshot()
        suppressed = next(e["id"] for e in snap["conceptQualityLedger"]["entries"] if e["status"] == "suppressed" and e["reason"] == "context-free-boilerplate")
        pinned = next(c["id"] for c in snap["concepts"] if c["label"]["en"] == "disease burden")
        assert suppressed not in {c["id"] for c in snap["concepts"]}
        assert call("/api/v1/overlays", "PUT", {**src, "entityType": "concept", "entityId": pinned, "patch": {"pinned": True}}, 0)[0] == 200
        revision = manager.store.read_json("overlays")[0]
        status, body = call("/api/v1/overlays", "PUT", {**src, "entityType": "concept", "entityId": suppressed, "patch": {"protected": True}}, revision)
        assert status == 200, body                                                       # previously 400 "unknown overlay entity"
        assert call("/api/v1/overlays", "PUT", {**src, "entityType": "concept", "entityId": f"concept:{src['sourceId']}:other:x", "patch": {"protected": True}}, revision + 1)[0] == 400
        assert call("/api/v1/overlays", "PUT", {**src, "entityType": "paper", "entityId": snap["papers"][0]["id"], "patch": {"protected": True}}, revision + 1)[0] == 400
        manager.request_reindex(); _settle(manager)
        after = manager.snapshot()
        entry = next(e for e in after["conceptQualityLedger"]["entries"] if e["id"] == suppressed)
        assert entry["status"] == "protected" and entry["protectedBy"] == "user" and suppressed in {c["id"] for c in after["concepts"]}
        pin_entry = next(e for e in after["conceptQualityLedger"]["entries"] if e["id"] == pinned)
        assert pin_entry["status"] == "protected"                                       # existing pin retained
        assert manager.store.read_json("overlays")[1]["entities"][pinned]["pinned"] is True
    finally:
        stop()


# ------------------------------------------------------------- epoch laundering guard
def test_foreign_epoch_tagged_taxonomy_and_overlays_are_not_carried_into_a_merge(api):
    manager, call = api
    src, prefix = _prefix(manager); store = manager.store
    assert call("/api/v1/taxonomy", "PUT", {**src, "facetCatalogConfig": {"entities": [_entity(prefix, "kept-only-if-current")]}, "inputState": {"status": "disabled", "reason": "old epoch"}}, 0)[0] == 200
    assert call("/api/v1/overlays", "PUT", {**src, "pins": {manager.snapshot()["papers"][0]["id"]: True}}, 0)[0] == 200
    conn = store._connection()
    for table in ("taxonomy", "overlays"):
        conn.execute(f"UPDATE {table} SET server_id='old-source', epoch='old-epoch' WHERE singleton=1")
    taxonomy_revision, overlay_revision = store.read_json("taxonomy")[0], store.read_json("overlays")[0]
    assert store.read_json("taxonomy")[2] == {"server_id": "old-source", "epoch": "old-epoch"}
    assert call("/api/v1/taxonomy", "PUT", {**src, "fields": []}, taxonomy_revision)[0] == 200       # unrelated legacy edit
    merged = store.read_json("taxonomy")
    assert set(merged[1]) == {"fields", "concepts"} and "inputState" not in merged[1] and "facetCatalogConfig" not in merged[1]
    assert merged[2] == {"server_id": src["sourceId"], "epoch": src["sourceEpoch"]}               # restamped, but nothing foreign carried
    assert call("/api/v1/overlays", "PUT", {**src, "aliases": {}}, overlay_revision)[0] == 200
    assert "pins" not in store.read_json("overlays")[1]
    manager.request_reindex(); _settle(manager)
    assert manager.snapshot()["taxonomyState"]["status"] == "absent"                                  # the old `disabled` never became authoritative


def test_resolve_endpoint_never_treats_a_per_paper_context_merge_as_a_global_redirect(tmp_path):
    class MixedReplay(Replay):
        def __init__(self):
            super().__init__()
            self.items = {"T1": (1, {"key": "T1", "version": 1, "data": {"itemType": "journalArticle", "title": "Occupational therapist-led OT sessions", "tags": [{"tag": "OT"}]}}),
                          "M1": (1, {"key": "M1", "version": 1, "data": {"itemType": "journalArticle", "title": "OT plans with Wasserstein costs", "tags": [{"tag": "OT"}]}})}
    manager = _manager(tmp_path, replay=MixedReplay())
    manager.sync(); _settle(manager)
    call, stop = _serve(manager)
    try:
        snap = manager.snapshot()
        ot = next(e["id"] for e in snap["conceptQualityLedger"]["entries"] if e["label"]["en"] == "OT")
        status, body = call(f"/api/v1/taxonomy/resolve?entityType=concept&entityId={urllib.parse.quote(ot)}")
        assert status == 200 and body["canonicalId"] == ot and body["redirected"] is False and body["status"] == "active"
        assert "mergedInto" not in body and body["mergeScope"] == "per-paper" and body["contextMergedInto"]
    finally:
        stop()


def test_input_state_and_quality_override_reject_unbounded_extra_keys(api):
    manager, call = api
    src = _src(manager)
    assert call("/api/v1/taxonomy", "PUT", {**src, "inputState": {"status": "valid-custom", "blob": "x" * 10}}, 0)[0] == 400
    assert call("/api/v1/taxonomy", "PUT", {**src, "inputState": {"status": "disabled", "reason": "r" * 300}}, 0)[0] == 400
    assert call("/api/v1/taxonomy", "PUT", {**src, "inputState": {"status": "disabled", "reason": "maintenance"}}, 0)[0] == 200
    suppressed = next(e["id"] for e in manager.snapshot()["conceptQualityLedger"]["entries"] if e["status"] == "suppressed")
    assert call("/api/v1/overlays", "PUT", {**src, "entityType": "concept", "entityId": suppressed, "patch": {"quality": {"status": "active", "payload": "x"}}}, 0)[0] == 400
    assert call("/api/v1/overlays", "PUT", {**src, "entityType": "concept", "entityId": suppressed, "patch": {"quality": {"protected": True}}}, 0)[0] == 200


# ---------------------------------------------------------------- N1: redirects through the REAL base publish
def _seed_v2_with_history(manager):
    """Make the store look like a pre-upgrade v2 library with saved redirects, aliases and pins."""
    snap = manager.snapshot()
    src = _src(manager)
    field = next(f["id"] for f in snap["fields"] if f["label"]["en"] == "Bioinformatics")
    concept = next(c["id"] for c in snap["concepts"] if c["label"]["en"] == "disease burden")
    old_field, old_concept = f"field:{src['sourceId']}:{src['sourceEpoch']}:old-bio", f"concept:{src['sourceId']}:{src['sourceEpoch']}:old-burden"
    for key in ("facetCatalog", "paperFacets", "conceptQualityLedger", "categoryColorRegistry", "fieldStats", "taxonomyState", "fences"):
        snap.pop(key, None)
    redirects = {old_field: field, old_concept: concept}
    snap["taxonomyRedirects"] = redirects
    snap["modelStatus"] = {**snap["modelStatus"], "recipeVersion": "atlas-graph-v2"}
    state = manager.store.state()
    assert manager.store.publish_snapshot(snap, state["generation"]) is not None
    overlays = {"pins": {concept: True}, "conceptAliases": {concept: ["burden of disease"]}}
    ok, _, _ = manager.store.cas_json("overlays", overlays, 0, source=state)
    assert ok
    return redirects, overlays, field, concept


def _resolve(call, kind, entity_id):
    return call(f"/api/v1/taxonomy/resolve?entityType={kind}&entityId={urllib.parse.quote(entity_id)}")


def test_base_publish_keeps_redirects_and_state_then_enrich_backs_up_and_rolls_back_true_safe(tmp_path):
    replay = Replay()
    manager = _manager(tmp_path, replay=replay)
    manager.sync(); _settle(manager)
    redirects, overlays, field, concept = _seed_v2_with_history(manager)
    store = manager.store
    # Source changes: one paper added, one DELETED from Zotero (must not be resurrected by backup/rollback).
    replay.items["C1"] = (2, {"key": "C1", "version": 2, "data": {"itemType": "journalArticle", "title": "A new amplicon study", "tags": [{"tag": "disease burden"}]}})
    del replay.items["B1"]
    captured = []
    real_schedule = manager._schedule_enrichment
    manager._schedule_enrichment = lambda generation, state: captured.append((generation, state, copy.deepcopy(store.get_snapshot()[2])))
    manager.sync()                                                          # changed-source path: real BASE publish, enrichment held
    generation, state, base = captured[0]
    assert base["taxonomyRedirects"] == redirects                           # N1: base publish no longer strips the history
    assert {p["key"] for p in base["papers"]} == {"A1", "C1"}               # current active papers only (B1 deleted)
    assert field in {f["id"] for f in base["fields"]} and concept in {c["id"] for c in base["concepts"]}
    manager._schedule_enrichment = real_schedule
    call, stop = _serve(manager)
    try:
        for kind, old, new in (("field", *list(redirects.items())[0]), ("concept", *list(redirects.items())[1])):
            status, body = _resolve(call, kind, old)
            assert status == 200 and body["canonicalId"] == new and body["redirected"] is True        # resolves on the BASE snapshot
        manager._enrich(generation, state)                                   # actual enrichment for that generation
        final = manager.snapshot()
        assert final["modelStatus"]["recipeVersion"] == "atlas-graph-v3" and "facetCatalog" in final
        assert final["taxonomyRedirects"] == redirects                       # old IDs still resolve after recipe v3
        for kind, old, new in (("field", *list(redirects.items())[0]), ("concept", *list(redirects.items())[1])):
            assert _resolve(call, kind, old)[1]["canonicalId"] == new
        assert {p["key"] for p in final["papers"]} == {"A1", "C1"}
        # backup contents: immutable original = last valid pre-enrichment CURRENT-source snapshot WITH its identity history
        receipt = store.migration_backup(state["epoch"], generation)
        assert receipt and receipt["previousRecipeVersion"] in {"atlas-graph-v2", "atlas-graph-v3"}
        assert receipt["rollbackPayload"]["taxonomyRedirects"] == redirects and receipt["preserved"]["taxonomyRedirects"] == redirects
        assert {p["key"] for p in receipt["rollbackPayload"]["papers"]} == {"A1", "C1"}               # no deleted-item resurrection
        assert receipt["userState"]["overlays"] == overlays and receipt["hashLedger"]["overlays"] == migration.canonical_hash(overlays)
        strip = lambda snap: {k: v for k, v in snap.items() if k not in {"fences", "revision"}}
        before_hash = migration.canonical_hash(strip(receipt["rollbackPayload"]))
        # true-safe rollback: nothing changed since the backup, newest publish is the recorded one
        ok, reason, _ = migration.rollback(store, source_epoch=state["epoch"], generation=generation)
        assert ok and reason == "rolled-back"
        restored = manager.snapshot()
        assert migration.canonical_hash(strip({k: v for k, v in restored.items()})) == before_hash        # byte-level preservation
        assert restored["taxonomyRedirects"] == redirects and {p["key"] for p in restored["papers"]} == {"A1", "C1"}
        for kind, old, new in (("field", *list(redirects.items())[0]), ("concept", *list(redirects.items())[1])):
            assert _resolve(call, kind, old)[1]["canonicalId"] == new                                     # still resolve after rollback
    finally:
        stop()


def test_validate_migration_detects_redirect_loss_that_the_old_base_publish_would_have_hidden():
    before = {"source": {"id": "s"}, "taxonomyRedirects": {"concept:s:e:old": "concept:s:e:new"}}
    after = {"source": {"id": "s"}, "papers": [], "fields": [], "concepts": [{"id": "concept:s:e:new", "label": {"en": "n"}}], "taxonomyRedirects": {}}
    assert any("redirect lost" in e for e in migration.validate_migration(before, after, {}))
    after["taxonomyRedirects"] = dict(before["taxonomyRedirects"])
    assert migration.validate_migration(before, after, {}) == []


def test_unchanged_source_recipe_upgrade_also_keeps_redirects_and_pins_through_enrich(tmp_path):
    manager = _manager(tmp_path)
    manager.sync(); _settle(manager)
    redirects, overlays, field, concept = _seed_v2_with_history(manager)
    manager.sync(); _settle(manager)                                          # unchanged source: recipe-stamp mismatch triggers enrichment
    final = manager.snapshot(); state = manager.store.state()
    assert final["taxonomyRedirects"] == redirects and "facetCatalog" in final
    receipt = manager.store.migration_backup(state["epoch"], state["generation"])
    assert receipt["rollbackPayload"]["taxonomyRedirects"] == redirects
    entry = next(e for e in final["conceptQualityLedger"]["entries"] if e["id"] == concept)
    assert entry["status"] == "protected"                                      # pin retained
    assert manager.store.read_json("overlays")[1] == overlays
    manager.stop_watcher()


# ------------------------------------------------- pending (base-published) v3 snapshot must stay strictly valid
def _all_public_views(manager, call):
    """Every public full/detail view of the current store snapshot."""
    views = {"full": manager.snapshot(), "detail1": manager.snapshot("detail", 1), "detail0": manager.snapshot("detail", 0)}
    status, via_http = call("/api/v1/snapshot?projection=full")
    assert status == 200
    views["http-full"] = via_http
    return views


def test_pending_v3_snapshot_after_source_add_delete_validates_schema_and_strict_decoder(tmp_path):
    replay = Replay()
    GatedEngine.gate = GatedEngine.entered = None
    manager = _manager(tmp_path, GatedEngine(tmp_path / "cache", provider="fake", allow_download=False), replay=replay)
    manager.sync(); _settle(manager)                                         # complete v3 snapshot
    redirects, overlays, field, concept = _seed_v2_with_history(manager)
    manager.sync(); _settle(manager)                                         # recipe-upgrade enrichment so v3 + redirects are the stored state
    full_before = manager.snapshot()
    assert {r["paperId"] for r in full_before["paperFacets"]} == {p["id"] for p in full_before["papers"]}
    deleted_id = next(p["id"] for p in full_before["papers"] if p["key"] == "B1")
    # hold the NEXT enrichment, then add + delete source items: only the BASE publish is visible meanwhile
    GatedEngine.gate, GatedEngine.entered = threading.Event(), threading.Event()
    replay.items["C1"] = (2, {"key": "C1", "version": 2, "data": {"itemType": "journalArticle", "title": "A new amplicon study", "tags": [{"tag": "disease burden"}]}})
    del replay.items["B1"]
    manager.sync()
    assert GatedEngine.entered.wait(5)
    call, stop = _serve(manager)
    try:
        schema = json.loads((ROOT / "docs" / "snapshot.schema.json").read_text())
        for name, view in _all_public_views(manager, call).items():
            jsonschema.validate(view, schema)
            _decode_in_node(view)                                            # strict decoder, unchanged
        pending = manager.snapshot()
        assert {p["key"] for p in pending["papers"]} == {"A1", "C1"}
        facet_papers = {r["paperId"] for r in pending["paperFacets"]}
        assert deleted_id not in facet_papers and facet_papers <= {p["id"] for p in pending["papers"]}      # no deleted-source reference
        assert deleted_id not in {m["paperId"] for m in pending["memberships"]} and deleted_id not in json.dumps(pending["fieldStats"])
        stats = pending["fieldStats"]
        assert stats["paperCount"] == len(pending["papers"]) == 2 and stats["knownPrimary"] + stats["unknown"] == 2    # aggregate clock honest
        assert pending["modelStatus"]["state"] == "pending"                                                    # derivation clock: not yet enriched
        assert pending["taxonomyRedirects"] == redirects and pending["fences"]["generation"] <= manager.store.state()["generation"]
        assert pending["source"]["epoch"] == full_before["source"]["epoch"]
        GatedEngine.gate.set()
        _settle(manager)
        for name, view in _all_public_views(manager, call).items():
            jsonschema.validate(view, schema); _decode_in_node(view)
        final = manager.snapshot()
        assert {r["paperId"] for r in final["paperFacets"]} == {p["id"] for p in final["papers"]} and deleted_id not in json.dumps(final["paperFacets"])
        assert final["taxonomyRedirects"] == redirects and final["modelStatus"].get("state") != "pending"
    finally:
        GatedEngine.gate = GatedEngine.entered = None
        stop()
