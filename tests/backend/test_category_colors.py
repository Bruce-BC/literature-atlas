import json
import subprocess
from pathlib import Path

from atlas.category_colors import build_category_color_registry, oklch_to_hex


def test_registry_uses_semantic_builtin_and_source_scoped_custom_keys():
    rows = [
        {"id": "field:server:epoch:analysis", "facetId": "subject:analysis", "ontologyKey": "subject:analysis", "origin": "builtin", "kind": "subject"},
        {"id": "facet:server:epoch:subject:custom", "facetId": "subject:custom", "origin": "custom", "kind": "subject"},
    ]
    registry = build_category_color_registry(rows, namespace="atlas:server:epoch")
    assert {entry["categoryKey"] for entry in registry["entries"]} == {"subject:analysis", "facet:server:epoch:subject:custom"}
    assert all(entry["hexByPaletteTheme"].get(f"{palette}:{theme}") for entry in registry["entries"] for palette in ("atlas", "calm", "mocha") for theme in ("light", "dark"))


def test_registry_is_append_only_across_rename_insert_and_root_addition():
    initial = [
        {"id": "field:a", "facetId": "subject:a", "origin": "builtin", "kind": "subject"},
        {"id": "field:b", "facetId": "subject:b", "origin": "builtin", "kind": "subject"},
    ]
    first = build_category_color_registry(initial)
    old = {entry["categoryKey"]: (entry["family"], entry["siblingIndex"], dict(entry["hexByPaletteTheme"])) for entry in first["entries"]}
    changed = [
        {**initial[1], "label": {"en": "Renamed"}},
        {**initial[0], "label": {"en": "A"}},
        {"id": "field:c", "facetId": "subject:c", "origin": "builtin", "kind": "subject"},
        {"id": "field:new-root", "facetId": "subject:new-root", "origin": "builtin", "kind": "subject"},
    ]
    second = build_category_color_registry(changed, existing=first)
    for key, (family, sibling, colors) in old.items():
        entry = next(row for row in second["entries"] if row["categoryKey"] == key)
        assert (entry["family"], entry["siblingIndex"], entry["hexByPaletteTheme"]) == (family, sibling, colors)


def test_oklch_gamut_mapping_returns_hex():
    assert oklch_to_hex(0.57, 0.145, 258.75).startswith("#")
    assert len(oklch_to_hex(0.72, 0.13, 15.5)) == 7


def test_python_and_frontend_fallback_registry_contract_matches():
    rows = [
        {"id": "field:server:epoch:analysis", "facetId": "subject:analysis", "origin": "builtin", "kind": "subject"},
        {"id": "field:server:epoch:topology", "facetId": "subject:topology", "parentIds": ["field:server:epoch:analysis"], "origin": "builtin", "kind": "subject"},
        {"id": "field:server:epoch:custom", "facetId": "subject:custom", "origin": "custom", "kind": "subject"},
    ]
    py_registry = build_category_color_registry(rows)
    root = Path(__file__).parents[2]
    script = """
const rows = JSON.parse(process.argv[1]);
const {buildCategoryColorRegistry} = await import('./frontend/network-style.js');
console.log(JSON.stringify(buildCategoryColorRegistry({fields: rows})));
"""
    result = subprocess.run(["node", "--input-type=module", "-e", script, json.dumps(rows)], cwd=root, text=True, capture_output=True, check=True)
    js_registry = json.loads(result.stdout)
    py_by_key = {row["categoryKey"]: row for row in py_registry["entries"]}
    js_by_key = {row["categoryKey"]: row for row in js_registry["entries"]}
    assert set(py_by_key) == set(js_by_key)
    for key in py_by_key:
        assert py_by_key[key]["family"] == js_by_key[key]["family"]
        assert py_by_key[key]["siblingIndex"] == js_by_key[key]["siblingIndex"]
        assert py_by_key[key]["depthVariant"] == js_by_key[key]["depthVariant"]
        assert py_by_key[key]["hexByPaletteTheme"] == js_by_key[key]["hexByPaletteTheme"]


def test_deep_ancestry_and_all_three_palettes_match_between_python_and_javascript():
    rows = [
        {"id": "field:s:e:a", "facetId": "subject:a", "origin": "builtin", "kind": "subject"},
        {"id": "field:s:e:b", "facetId": "subject:b", "parentIds": ["field:s:e:a"], "origin": "builtin", "kind": "subject"},
        {"id": "field:s:e:c", "facetId": "subject:c", "parentIds": ["field:s:e:b"], "origin": "builtin", "kind": "subject"},
        {"id": "field:s:e:d", "facetId": "subject:d", "parentIds": ["field:s:e:b"], "origin": "builtin", "kind": "subject"},
        {"id": "field:s:e:z", "facetId": "subject:z", "origin": "builtin", "kind": "subject"},
    ]
    py = {row["categoryKey"]: row for row in build_category_color_registry(rows)["entries"]}
    root = Path(__file__).parents[2]
    script = "const rows = JSON.parse(process.argv[1]); const {buildCategoryColorRegistry} = await import('./frontend/network-style.js'); console.log(JSON.stringify(buildCategoryColorRegistry({fields: rows})));"
    js = {row["categoryKey"]: row for row in json.loads(subprocess.run(["node", "--input-type=module", "-e", script, json.dumps(rows)], cwd=root, text=True, capture_output=True, check=True).stdout)["entries"]}
    assert set(py) == set(js) and py["subject:c"]["depthVariant"] == 2 and py["subject:d"]["siblingIndex"] == 1
    assert py["subject:a"]["family"] == py["subject:c"]["family"] != py["subject:z"]["family"]
    for key in py:
        assert (py[key]["family"], py[key]["siblingIndex"], py[key]["depthVariant"]) == (js[key]["family"], js[key]["siblingIndex"], js[key]["depthVariant"])
        assert set(py[key]["hexByPaletteTheme"]) == set(js[key]["hexByPaletteTheme"]) >= {"atlas:light", "calm:dark", "mocha:light", "system"}
        assert py[key]["hexByPaletteTheme"] == js[key]["hexByPaletteTheme"]


def test_registry_ancestry_is_stable_when_a_deeper_category_is_appended_later():
    base = [{"id": "field:a", "facetId": "subject:a", "origin": "builtin", "kind": "subject"}, {"id": "field:b", "facetId": "subject:b", "parentIds": ["field:a"], "origin": "builtin", "kind": "subject"}]
    first = build_category_color_registry(base)
    later = build_category_color_registry(base + [{"id": "field:c", "facetId": "subject:c", "parentIds": ["field:b"], "origin": "builtin", "kind": "subject"},
                                                   {"id": "field:0root", "facetId": "subject:0root", "origin": "builtin", "kind": "subject"}], existing=first)
    assert later["entries"][:2] == first["entries"]
    c = next(e for e in later["entries"] if e["categoryKey"] == "subject:c")
    assert c["family"] == first["entries"][0]["family"] and c["depthVariant"] == 2
    root = next(e for e in later["entries"] if e["categoryKey"] == "subject:0root")
    assert root["family"] != first["entries"][0]["family"]
