"""JSON-schema + cross-language decoder roundtrip for real GraphEngine v3 snapshots."""
import json
import subprocess
import tempfile
from pathlib import Path

import jsonschema
import pytest

from atlas.graph import GraphEngine

ROOT = Path(__file__).parents[2]
SOURCE = {"id": "schema-test", "serverId": "schema-test", "epoch": "epoch-1", "alias": "fixture", "zoteroApi": "3", "schema": 44}


def _schema():
    return json.loads((ROOT / "docs" / "snapshot.schema.json").read_text())


def _build(papers, **kwargs):
    with tempfile.TemporaryDirectory() as directory:
        return GraphEngine(Path(directory), provider="fake").build(papers, source=SOURCE, settings={"fakeVectors": {}}, **kwargs)


def _decode_with_node(snapshot):
    script = "import {decodeSnapshot} from './frontend/decoder.js'; let t=''; process.stdin.on('data',c=>t+=c).on('end',()=>{const d=decodeSnapshot(JSON.parse(t)); console.log(JSON.stringify({papers:d.papers.length,facets:d.paperFacets?.length,ledger:Object.keys(d.conceptQualityLedger||{}),registry:d.categoryColorRegistry?.entries.length,stats:d.fieldStats?.paperCount}));});"
    result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT, input=json.dumps(snapshot), text=True, capture_output=True)
    assert result.returncode == 0, result.stderr[-800:]
    return json.loads(result.stdout)


def _full_snapshot():
    """A realistic GraphEngine snapshot: user concepts, pins, exclusions, suppressed noise, custom facets, registry."""
    prefix = f"facet:{SOURCE['id']}:{SOURCE['epoch']}:"
    papers = [
        {"key": "A", "title": "QIIME 2 microbiome amplicon analysis", "abstract": "16S marker gene", "keywords": ["burden", "disease burden", "11M20"]},
        {"key": "B", "title": "Bacteriophage therapy", "keywords": ["vector bundles"]},
        {"key": "C", "title": "An unrelated note", "conceptIds": ["my user concept"]},
    ]
    taxonomy = {"fields": [], "concepts": [], "inputState": {"status": "valid-custom"},
                "facetCatalogConfig": {"version": 1, "entities": [{"id": prefix + "tool:nextflow", "kind": "tool", "label": "Nextflow"}]}}
    overlays = {"pins": {f"concept:{SOURCE['id']}:{SOURCE['epoch']}:disease-burden": True},
                "entities": {f"concept:{SOURCE['id']}:{SOURCE['epoch']}:burden": {"entityType": "concept", "excluded": True}}}
    snapshot = _build(papers, taxonomy=taxonomy, overlays=overlays)
    from atlas.sync import SyncManager
    snapshot["status"] = {"phase": "idle", "stale": False}
    SyncManager._ensure_v2_metadata(snapshot, {})
    return snapshot


def test_generated_snapshot_validates_json_schema_and_decodes_across_languages():
    snapshot = _full_snapshot()
    jsonschema.validate(snapshot, _schema())
    ledger = snapshot["conceptQualityLedger"]
    # Real ownership shapes: user-owned concepts are in `user` AND `protected`/`active`; excluded ones are suppressed AND `user`.
    assert set(ledger["user"]) & set(ledger["protected"]) and set(ledger["user"]) & set(ledger["suppressed"])
    assert set(ledger["active"]).isdisjoint(ledger["suppressed"]) and not ({c["id"] for c in snapshot["concepts"]} & set(ledger["suppressed"]))
    assert any(e["status"] == "suppressed" and e["reason"] == "user-excluded" for e in ledger["entries"])
    out = _decode_with_node(snapshot)
    assert out["papers"] == 3 and out["facets"] == 3 and out["registry"] == len(snapshot["categoryColorRegistry"]["entries"]) and out["stats"] == 3
    # Palette keys use the real palette ids; semantic builtin keys stay semantic, custom keys stay source-scoped.
    keys = {e["categoryKey"] for e in snapshot["categoryColorRegistry"]["entries"]}
    assert "subject:bioinformatics" in keys and f"facet:{SOURCE['id']}:{SOURCE['epoch']}:tool:nextflow" in keys


def test_field_stats_invariants_in_real_snapshot():
    snapshot = _full_snapshot()
    stats = snapshot["fieldStats"]
    assert stats["knownPrimary"] + stats["unknown"] == stats["paperCount"] == 3
    assert all(r["primary"] <= r["direct"] <= r["inclusive"] for r in stats["rows"])
    primaries = [p["displayFieldId"] for p in snapshot["papers"]]
    by_field = {r["fieldId"]: r for r in stats["rows"]}
    for field_id, row in by_field.items():
        assert row["primary"] <= primaries.count(field_id)


def test_primary_is_always_an_accepted_direct_membership_in_real_snapshot():
    snapshot = _full_snapshot()
    for row in snapshot["paperFacets"]:
        direct = {m["facetId"] for m in row["directMemberships"]}
        for kind, facet_id in row["primaryByFacet"].items():
            assert facet_id is None or facet_id in direct


@pytest.mark.parametrize("state", ["absent", "intentional-empty-valid", "valid-custom", "invalid", "disabled"])
def test_every_taxonomy_input_state_produces_a_schema_valid_decodable_snapshot(state):
    taxonomy = None if state == "absent" else {"fields": [], "concepts": [], "inputState": {"status": state}}
    snapshot = _build([{"key": "A", "title": "QIIME 2 microbiome amplicon analysis"}], taxonomy=taxonomy)
    snapshot["status"] = {"phase": "idle", "stale": False}
    jsonschema.validate(snapshot, _schema())
    _decode_with_node(snapshot)
    assert snapshot["taxonomyState"]["status"] == state


def test_schema_keeps_legacy_fixture_backward_compatible():
    for name in ("snapshot.json", "demo-snapshot.json"):
        jsonschema.validate(json.loads((ROOT / "tests" / "fixtures" / name).read_text()), _schema())
