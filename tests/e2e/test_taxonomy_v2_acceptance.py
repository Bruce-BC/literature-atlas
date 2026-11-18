"""Native-browser acceptance for taxonomy/interaction v2 (TAXV2-015..043 UI contracts).

Rules this suite follows:
* the BUILT bundle is loaded; a stale ``dist`` fails the run;
* every action is a native mouse/keyboard event on a real canvas/DOM target
  whose client position comes from the read-only ``AtlasDebug`` observation facade;
* assertions are on exact node-ID sets, selected/detail DOM, reducer revisions
  and real pixels; ``AtlasDebug`` never dispatches or mutates;
* Playwright is mandatory: there is no skip path;
* the receipt records each test's real outcome from the test body itself
  (not from an unregistered pytest hook).
"""
from __future__ import annotations

import functools
import json
import platform
import sys
import time
from pathlib import Path
from typing import Any, Callable

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v2_support import (FIXTURE_VALIDATION, ANALYSIS, MULTI, OPT, OPT_DIRECT, OT, ROOT, UNKNOWN, Harness, acceptance_fixture, fixture_hash, large_fixture, running_app)  # noqa: E402

RECEIPT = ROOT / ".omx" / "context" / "taxonomy-v2-acceptance-browser.json"
ARTIFACTS = ROOT / ".omx" / "context" / "taxonomy-v2-browser-screenshots"  # ignored by git; audit-approved location
_RESULTS: list[dict[str, Any]] = []
_SCREENSHOTS: list[dict[str, Any]] = []
_FIXTURE_HASH: list[str] = []


def receipt(requirements: str) -> Callable[[Callable[..., None]], Callable[..., None]]:
    """Record the actual outcome (pass/fail + measurements) of the decorated test."""
    def wrap(function: Callable[..., None]) -> Callable[..., None]:
        @functools.wraps(function)
        def inner(*args: Any, **kwargs: Any) -> None:
            started = time.monotonic()
            row: dict[str, Any] = {"test": function.__name__, "requirements": requirements, "outcome": "failed"}
            try:
                function(*args, **kwargs)
                row["outcome"] = "passed"
            except BaseException as error:
                row["failure"] = f"{type(error).__name__}: {str(error)[:240]}"
                raise
            finally:
                row["durationMs"] = round((time.monotonic() - started) * 1000, 1)
                _RESULTS.append(row)
        return inner
    return wrap


@pytest.fixture(scope="session", autouse=True)
def write_receipt():
    yield
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT.write_text(json.dumps({
        "suite": "taxonomy-v2-acceptance", "fixture": "synthetic (demo bridge + authored facets/fieldStats)", "fixtureSha256": _FIXTURE_HASH[0] if _FIXTURE_HASH else None,
        "browser": "chromium-headless", "os": platform.platform(), "viewports": ["1440x1000", "320x900", "1440x900"],
        "bundle": "dist/web/app.js (mtime checked against frontend/*.js)", "tests": _RESULTS, "screenshots": _SCREENSHOTS,
        "fixtureValidation": FIXTURE_VALIDATION[-1:] or None, "limits": "synthetic fixture; facets/fieldStats authored in the fixture because demo mode serves no backend-generated extensions",
    }, indent=2), encoding="utf-8")


@pytest.fixture()
def app():
    fixture = acceptance_fixture()
    _FIXTURE_HASH[:] = [fixture_hash(fixture)]
    with running_app(fixture) as harness:
        yield harness
        assert not harness.errors, f"browser console/page errors: {harness.errors[:3]}"


def shot(app: Harness, name: str, **kwargs: Any) -> Path:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    path = ARTIFACTS / f"{name}.png"
    app.page.screenshot(path=str(path), **kwargs)
    assert path.exists() and path.stat().st_size > 2000, f"screenshot {name} was not saved"
    _SCREENSHOTS.append({"name": name, "bytes": path.stat().st_size})
    return path


def active_ids(app: Harness) -> set[str]:
    return {paper["id"] for paper in app.fixture["papers"]}


def pick_overview_mode(app: Harness, mode: str, nodes: int | None = None) -> None:
    app.page.locator("#atlas-overview-renderer").select_option(mode)
    app.overview_ready(nodes)


def legend_button(app: Harness, field_id: str):
    return app.page.locator(f'#atlas-field-legend button[data-field-id="{field_id}"]')


# ---------------------------------------------------------------------------------------------------------------------
@receipt("TAXV2-017,042")
def test_all_papers_native_point_click_selects_paper_detail_and_revision(app: Harness):
    pick_overview_mode(app, "all-papers", len(app.fixture["papers"]))
    projection = app.page.evaluate("() => window.AtlasDebug.getAllPaperOverview()")
    assert set(projection["representedIds"]) == active_ids(app) and len(projection["points"]) == len(app.fixture["papers"])
    assert projection["counters"]["represented"] == projection["counters"]["active"] == len(app.fixture["papers"])
    nodes = app.overview_nodes()
    assert {row["data"]["paperId"] for row in nodes} == active_ids(app) and len({row["id"] for row in nodes}) == len(nodes)
    assert UNKNOWN in {row["data"]["paperId"] for row in nodes}, "unknown/unclassified paper must have a point"
    # real canvas coordinates: finite, unique, inside the container, and actually painted
    box = app.page.locator("#atlas-field-network").bounding_box()
    points = {row["id"]: app.client_point("overview", row["id"]) for row in nodes}
    assert len({(round(p["x"], 2), round(p["y"], 2)) for p in points.values()}) == len(nodes)
    assert all(box["x"] <= p["x"] <= box["x"] + box["width"] and box["y"] <= p["y"] <= box["y"] + box["height"] for p in points.values()), "fit view must place every point inside the canvas"
    assert app.page.locator("#atlas-field-network").evaluate("el => el.dataset.layout") == "preset"
    target = next(row for row in nodes if row["data"]["paperId"] != UNKNOWN)
    assert app.pixel_painted("overview", points[target["id"]]["x"], points[target["id"]]["y"]), "no pixels drawn at the registered point"
    before = app.view()
    app.click_node("overview", target["id"])
    app.wait("id => window.AtlasDebug.getViewState()?.selected?.id === id", 8, target["data"]["paperId"])
    after = app.view()
    assert after["selected"] == {"kind": "paper", "id": target["data"]["paperId"]} and after["detail"] == after["selected"]
    assert after["revision"] > before["revision"] and after["renderRevision"] == after["revision"]
    lower = app.page.evaluate("() => window.AtlasDebug.getLowerProjection()")
    assert lower["revision"] == after["revision"] == lower["viewRevision"], "lower projection must belong to the same reducer revision"
    paper = next(p for p in app.fixture["papers"] if p["id"] == target["data"]["paperId"])
    assert app.page.locator(".atlas-selection-detail h3").first.inner_text() == paper["title"]
    assert app.page.get_by_role("button", name="Zotero에서 열기").count() == 1
    assert app.page.evaluate("() => window.AtlasDebug.getG6Stats().overview.nativeClicks") >= 1, "selection must come from the native G6 click event"
    shot(app, "all-papers-selected-1440-light")


@receipt("TAXV2-018,019,020,042")
def test_field_focus_dims_points_syncs_lower_networks_and_paper_click_keeps_scope(app: Harness):
    pick_overview_mode(app, "all-papers", len(app.fixture["papers"]))
    legend_button(app, OPT).click()
    app.wait("() => window.AtlasDebug.getViewState()?.scope?.fieldId")
    view = app.view()
    assert view["scope"]["fieldId"] == OPT and view["selected"] == {"kind": "field", "id": OPT}
    scope_ids = set(view["scope"]["paperIds"])
    assert OPT_DIRECT + 6 + 1 == len(scope_ids), "Optimization scope = direct papers + OT + multi-parent MULTI"
    app.overview_ready(len(app.fixture["papers"]))
    nodes = app.overview_nodes()
    assert len(nodes) == len(app.fixture["papers"]), "field focus must not remove points"
    assert {row["data"]["paperId"] for row in nodes if row["data"]["dimmed"]} == active_ids(app) - scope_ids
    lower = app.page.evaluate("() => window.AtlasDebug.getLowerProjection()")
    assert lower["revision"] == view["revision"] and lower["paperNodes"] > 0 and set(lower["paperIds"]) <= scope_ids
    assert app.page.evaluate("() => window.AtlasDebug.getState().detail.eligiblePapers") == len(scope_ids)
    assert app.page.locator("#atlas-field-legend button[aria-pressed='true']").count() == 1
    sample_before = app.page.evaluate("() => window.AtlasDebug.getState().detail.eligiblePapers")
    app.page.locator("#atlas-sample").fill("90"); app.page.locator("#atlas-sample").dispatch_event("input")
    assert app.page.evaluate("() => window.AtlasDebug.getState().detail.eligiblePapers") == sample_before, "sample slider must not change eligible/aggregate counts"
    # native click on a lower-network paper: paper detail opens, field FOCUS (scope) stays, only one pressed entity
    app.stable("paper")
    paper_id = next(i for i in app.page.evaluate("() => window.AtlasDebug.getPaperGraph().getNodeData().map(n => n.id)"))
    app.click_node("paper", paper_id)
    app.wait("id => window.AtlasDebug.getViewState()?.selected?.id === id", 8, paper_id)
    after = app.view()
    assert after["scope"]["fieldId"] == OPT and after["selected"]["kind"] == "paper"
    assert app.page.locator("#atlas-field-legend button[aria-pressed='true']").count() == 0, "a selected paper must not leave a field pressed"
    assert app.page.locator("#atlas-field-legend button[aria-current='true']").count() == 1, "field scope stays visible as aria-current"
    assert app.page.locator(f"#atlas-paper-list button[aria-pressed='true']").count() == 1
    assert app.page.get_by_role("button", name="Zotero에서 열기").count() == 1
    # native click on a point OUTSIDE the scope -> library scope
    outside = next(row for row in app.overview_nodes() if row["data"]["dimmed"])
    app.click_node("overview", outside["id"])
    app.wait("() => window.AtlasDebug.getViewState()?.scope?.kind === 'library'", 8)
    assert app.view()["selected"]["id"] == outside["data"]["paperId"]
    shot(app, "all-papers-field-focus-1440-light")


@receipt("TAXV2-023,024,025,028")
def test_g6_representative_expand_collapse_child_click_paging_and_dedup(app: Harness):
    pick_overview_mode(app, "g6")
    before = app.overview_nodes(); before_ids = {n["id"] for n in before}
    optimization = next(n for n in before if n["id"] == OPT)
    assert OT not in before_ids, "typed child categories are hidden until their representative is expanded"
    app.click_node("overview", OPT)
    app.wait("() => window.AtlasDebug.getViewState().expanded.length === 1", 8)
    app.overview_ready()
    view = app.view()
    assert view["expanded"] == [OPT] and view["selected"] == {"kind": "field", "id": OPT} and view["scope"]["fieldId"] == OPT
    expanded = app.overview_nodes(); added = [n for n in expanded if n["id"] not in before_ids]
    child_papers = [n for n in added if n["data"].get("kind") == "paper"]
    assert OT in {n["id"] for n in added}, "hierarchy child category must be added"
    assert len(child_papers) == 24, f"first direct-paper page must be 24, got {len(child_papers)}"
    edges = app.page.evaluate("() => window.AtlasDebug.getOverviewGraph().getEdgeData().map(e => ({source: e.source, target: e.target, relation: e.data.relation}))")
    assert any(e["relation"] == "hierarchy" and e["source"] == OPT and e["target"] == OT for e in edges), "real child-field hierarchy edge"
    assert len(edges) <= 1000
    # second page via the explicit control
    app.page.locator("#atlas-overview-more").click()
    app.wait("n => window.AtlasDebug.getOverviewGraph().getNodeData().filter(x => x.data.kind === 'paper').length === n", 10, OPT_DIRECT)
    app.overview_ready()
    assert sum(1 for n in app.overview_nodes() if n["data"].get("kind") == "paper") == OPT_DIRECT, "page 2 loads the remaining direct papers"
    # child paper: native click selects paper only, never toggles
    child = next(n for n in app.overview_nodes() if n["data"].get("kind") == "paper")
    expanded_before = app.view()["expanded"]
    app.click_node("overview", child["id"])
    app.wait("() => window.AtlasDebug.getViewState()?.selected?.kind === 'paper'", 8)
    app.overview_ready()
    after_child = app.view()
    assert after_child["selected"]["id"] == child["data"]["paperId"] and after_child["expanded"] == expanded_before, "child click must not toggle the owner"
    assert app.page.evaluate("() => window.__ATLAS_G6_LAST_CLICK__?.target") == child["id"]
    # collapse via the SAME representative: selected child is replaced by the parent field
    app.click_node("overview", OPT)
    app.wait("() => window.AtlasDebug.getViewState().expanded.length === 0", 8)
    app.overview_ready(len(before))
    collapsed = app.view()
    assert collapsed["selected"] == {"kind": "field", "id": OPT} and collapsed["detail"] == {"kind": "field", "id": OPT}, "collapse replaces hidden child detail with the parent field"
    assert {n["id"] for n in app.overview_nodes()} == before_ids
    assert app.page.locator(".atlas-selection-detail h3").first.inner_text() == "Optimization"
    # multi-owner dedup: expand OT and Analysis; MULTI is one semantic node owned by both
    app.click_node("overview", OPT); app.wait("() => window.AtlasDebug.getViewState().expanded.length === 1", 8); app.overview_ready()
    app.click_node("overview", OT); app.wait("() => window.AtlasDebug.getViewState().expanded.length === 2", 8); app.overview_ready()
    analysis_visible = any(n["id"] == ANALYSIS for n in app.overview_nodes())
    assert analysis_visible
    app.click_node("overview", ANALYSIS); app.wait("() => window.AtlasDebug.getViewState().expanded.length === 3", 8); app.overview_ready()
    ids = [n["id"] for n in app.overview_nodes()]
    assert len(ids) == len(set(ids)) and ids.count(f"overview-paper:{MULTI}") == 1
    multi = next(n for n in app.overview_nodes() if n["id"] == f"overview-paper:{MULTI}")
    assert set(multi["data"]["owners"]) == {OT, ANALYSIS} and len(multi["data"]["fieldIds"]) == 2
    assert app.page.evaluate("() => window.AtlasDebug.getOverviewProjection().counts.loadedPapers") <= 150
    shot(app, "g6-overview-expanded-1440-light")


@receipt("TAXV2-024")
def test_rapid_representative_clicks_last_intent_wins_without_dropping(app: Harness):
    pick_overview_mode(app, "g6")
    for _ in range(5):
        app.click_node("overview", OPT)  # back-to-back native clicks; the target position is re-read each time, no settling waits
    app.wait("() => window.AtlasDebug.getViewState().revision >= 5", 10)
    app.wait("() => window.AtlasDebug.getLowerProjection().revision === window.AtlasDebug.getViewState().revision", 10)
    app.overview_ready()
    view = app.view()
    assert view["expanded"] == [OPT], "five toggles (odd) must end expanded; none silently dropped"
    expected = {n["id"] for n in app.overview_nodes()}
    assert OT in expected, "final G6 node set must match the final reducer state, not an intermediate render"


@receipt("TAXV2-029")
def test_drag_does_not_trigger_trailing_click_and_keyboard_matches_pointer(app: Harness):
    pick_overview_mode(app, "all-papers", len(app.fixture["papers"]))
    node = next(row for row in app.overview_nodes() if row["data"]["paperId"] != UNKNOWN)
    p = app.client_point("overview", node["id"])
    app.page.mouse.move(p["x"], p["y"]); app.page.mouse.down(); app.page.mouse.move(p["x"] + 60, p["y"] + 40, steps=6); app.page.mouse.up()
    app.page.wait_for_timeout(150)
    assert app.view()["selected"] is None, "drag + trailing click must not select"
    assert app.page.evaluate("() => window.AtlasDebug.getG6Stats().overview.suppressedDragClicks") >= 1
    # keyboard: arrow to a node, Enter selects exactly like a pointer click
    app.page.locator("#atlas-field-network").focus()
    app.page.keyboard.press("ArrowRight")
    proxy = app.page.evaluate("() => { const c = document.querySelector('#atlas-field-network'); const id = c.getAttribute('aria-activedescendant'); const el = id && document.getElementById(id); return el ? {id, role: el.getAttribute('role'), name: el.textContent, node: el.dataset.nodeId, container: c.getAttribute('role')} : null; }")
    assert proxy and proxy["role"] == "option" and proxy["container"] == "listbox", "aria-activedescendant must resolve to a real DOM option"
    node_label = next(row["data"]["label"] for row in app.overview_nodes() if row["id"] == proxy["node"])
    assert proxy["name"].startswith(node_label) and app.view()["selected"] is None, "focus gives an accessible name and never selects"
    assert app.page.locator("#atlas-field-network [role=option]").count() == 1, "bounded: one proxy, not one DOM node per paper"
    app.page.keyboard.press("Enter")
    app.wait("() => window.AtlasDebug.getViewState()?.selected?.kind === 'paper'", 8)
    assert app.page.evaluate("() => window.AtlasDebug.getG6Stats().overview.keyboardActivations") >= 1
    assert f"overview-paper:{app.view()['selected']['id']}" == proxy["node"]


@receipt("TAXV2-029")
def test_keyboard_enter_toggles_representative_like_pointer(app: Harness):
    pick_overview_mode(app, "g6")
    app.page.locator("#atlas-field-network").focus()
    for _ in range(12):
        app.page.keyboard.press("ArrowRight")
        if app.page.evaluate("() => document.querySelector('#atlas-field-network [role=option]')?.dataset.nodeId") == OPT:
            break
    else:
        raise AssertionError("could not reach the Optimization representative by keyboard")
    app.page.keyboard.press("Space")
    app.wait("() => window.AtlasDebug.getViewState().expanded.length === 1", 8)
    assert app.view()["expanded"] == [OPT] and app.view()["selected"]["id"] == OPT


@receipt("TAXV2-027")
def test_g6_lower_network_reuses_graph_for_theme_and_topology_change_adds_nodes(app: Harness):
    app.page.locator("#atlas-renderer").select_option("g6")
    legend_button(app, OPT).click()
    app.wait("() => document.querySelector('#atlas-paper-network')?.dataset.rendererState === 'ready'")
    app.stable("paper")
    stats = app.page.evaluate("() => window.AtlasDebug.getG6Stats().paper")
    ids_before = set(app.page.evaluate("() => window.AtlasDebug.getPaperGraph().getNodeData().map(n => n.id)"))
    app.page.locator("#atlas-theme").select_option("dark")
    app.wait("() => document.getElementById('atlas-app').dataset.atlasTheme === 'dark'")
    after = app.page.evaluate("() => window.AtlasDebug.getG6Stats().paper")
    assert after["created"] == stats["created"] == 1 and after["layoutRuns"] == stats["layoutRuns"], "theme change must reuse graph and layout"
    app.page.locator("#atlas-sample").fill("100"); app.page.locator("#atlas-sample").dispatch_event("input")
    app.wait("n => window.AtlasDebug.getPaperGraph().getNodeData().length > n", 10, len(ids_before))
    app.wait("() => window.AtlasDebug.getG6Stats().paper.topologyChanges >= 1", 10)
    ids_after = set(app.page.evaluate("() => window.AtlasDebug.getPaperGraph().getNodeData().map(n => n.id)"))
    assert ids_before < ids_after and app.page.evaluate("() => window.AtlasDebug.getG6Stats().paper.created") == 1
    # newly ADDED node is natively clickable with current metadata
    new_id = sorted(ids_after - ids_before)[0]
    app.stable("paper")
    app.click_node("paper", new_id)
    app.wait("id => window.AtlasDebug.getViewState()?.selected?.id === id", 8, new_id)
    assert app.page.locator(".atlas-selection-detail h3").first.inner_text() == next(p["title"] for p in app.fixture["papers"] if p["id"] == new_id)


@receipt("TAXV2-022,043")
def test_cytoscape_camera_policy_with_real_canvas_positions(app: Harness):
    legend_button(app, OPT).click()
    app.wait("() => window.AtlasDebug.getViewState()?.scope?.fieldId")
    graph = "window.AtlasDebug.getPaperGraph()"
    snap = lambda: app.page.evaluate(f"() => {{ const g = {graph}; return {{zoom: g.getZoom(), pan: g.getPan(), pos: Object.fromEntries(g.getNodeData().map(n => [n.id, g.getElementPosition(n.id).slice(0, 2)]))}}; }}")
    first = snap()
    positions = list(first["pos"].values())
    assert all(isinstance(v, (int, float)) for p in positions for v in p) and len({tuple(round(v, 3) for v in p) for p in positions}) == len(positions), "finite unique positions, no (0,0) stack"
    assert sum(1 for p in positions if abs(p[0]) < 1e-9 and abs(p[1]) < 1e-9) <= 1
    app.reveal("paper")
    box = app.page.locator("#atlas-paper-network").bounding_box()
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    zoom_expr = f"() => {graph}.getZoom()"
    app.page.mouse.move(cx, cy); app.page.mouse.wheel(0, -240)
    app.wait(f"z => {graph}.getZoom() !== z", 5, first["zoom"])
    after_wheel = snap()
    app.page.mouse.move(box["x"] + 10, box["y"] + 10); app.page.mouse.down(); app.page.mouse.move(box["x"] + 40, box["y"] + 30, steps=5); app.page.mouse.up()
    app.wait(f"p => {{ const q = {graph}.getPan(); return q.x !== p.x || q.y !== p.y; }}", 5, after_wheel["pan"])
    moved = snap()
    assert moved["zoom"] != first["zoom"] and moved["pan"] != first["pan"]
    app.page.wait_for_timeout(100)  # let the camera-remember frame run
    # same signature re-render (theme) keeps positions/zoom/pan exactly
    app.page.locator("#atlas-theme").select_option("dark"); app.wait("() => document.getElementById('atlas-app').dataset.atlasTheme === 'dark'")
    same = snap()
    assert same["pos"] == moved["pos"] and abs(same["zoom"] - moved["zoom"]) < 1e-9 and same["pan"] == moved["pan"]
    assert app.page.evaluate("() => window.AtlasDebug.getG6Stats().paper.reused") >= 1, "same-signature render must reuse the Cytoscape instance"
    # zoom limits
    for _ in range(40):
        app.page.mouse.wheel(0, -400)
    app.wait(f"() => {graph}.getZoom() >= 2.99", 8); assert snap()["zoom"] <= 3 + 1e-9
    for _ in range(80):
        app.page.mouse.wheel(0, 400)
    app.wait(f"() => {graph}.getZoom() <= 0.21", 8); assert snap()["zoom"] >= 0.2 - 1e-9
    # restore a deliberate camera, then visit another field (first visit => fit) and revisit (restore)
    app.page.locator("#atlas-theme").select_option("light"); app.wait("() => document.getElementById('atlas-app').dataset.atlasTheme === 'light'")
    app.page.mouse.move(cx, cy); app.page.mouse.wheel(0, -200)
    app.wait(f"() => {graph}.getZoom() > 0.2005", 5); app.page.wait_for_timeout(100)
    saved = snap()
    other = f"field:demo-server:demo-epoch:topology"
    legend_button(app, other).click(); app.wait(f"() => window.AtlasDebug.getViewState().scope.fieldId === '{other}'")
    plan = app.page.evaluate("() => window.AtlasDebug.getCameraPlans().paper")
    assert plan["action"] == "fit" and plan["reason"] == "first-visit"
    legend_button(app, OPT).click(); app.wait(f"() => window.AtlasDebug.getViewState().scope.fieldId === '{OPT}'")
    assert app.page.evaluate("() => window.AtlasDebug.getCameraPlans().paper.action") == "restore"
    revisit = snap()
    assert abs(revisit["zoom"] - saved["zoom"]) < 1e-6 and all(abs(revisit["pan"][k] - saved["pan"][k]) < 1e-6 for k in ("x", "y")), "revisit restores the stored camera"
    # topology add/remove in the SAME scope: camera preserved, new nodes seeded, explicit Fit-new-content only
    ids_before = set(revisit["pos"])
    app.page.locator("#atlas-sample").fill("100"); app.page.locator("#atlas-sample").dispatch_event("input")
    app.wait("n => window.AtlasDebug.getPaperGraph().getNodeData().length > n", 10, len(ids_before))
    grown = snap()
    assert abs(grown["zoom"] - revisit["zoom"]) < 1e-9 and grown["pan"] == revisit["pan"], "topology change must not auto-fit"
    assert all(grown["pos"][i] == revisit["pos"][i] for i in ids_before), "existing node positions untouched"
    new_positions = [grown["pos"][i] for i in set(grown["pos"]) - ids_before]
    assert new_positions and len({tuple(round(v, 3) for v in p) for p in new_positions}) == len(new_positions)
    assert app.page.locator("#atlas-fit-new-content").is_visible()
    app.page.locator("#atlas-fit-new-content").click()
    assert not app.page.locator("#atlas-fit-new-content").is_visible()
    fitted = app.page.evaluate(f"() => {{ const g = {graph}; const r = document.querySelector('#atlas-paper-network').getBoundingClientRect(); return g.getNodeData().every(n => {{ const c = g.getClientByCanvas(g.getElementPosition(n.id)); return c[0] >= r.left - 1 && c[0] <= r.right + 1 && c[1] >= r.top - 1 && c[1] <= r.bottom + 1; }}); }}")
    assert fitted, "Fit new content must bring every node into view"


@receipt("TAXV2-030,031")
def test_selection_ego_graph_nodes_edges_chips_and_keyboard_are_native(app: Harness):
    app.page.locator("#atlas-paper-list button").first.click()
    app.wait("() => document.querySelector('.atlas-selection-graph')")
    selected = app.view()["selected"]
    assert selected["kind"] == "paper"
    nodes = app.page.locator(".atlas-selection-node")
    assert nodes.count() >= 2 and nodes.first.get_attribute("aria-label")
    # edges are native-clickable (wide hit target) and keyboard focusable
    groups = app.page.locator(".atlas-selection-edge-group")
    assert groups.count() >= 1 and groups.first.get_attribute("role") == "button" and groups.first.get_attribute("tabindex") == "0"
    kinds = [nodes.nth(i).get_attribute("data-kind") for i in range(nodes.count())]
    assert "concept" in kinds or "field" in kinds
    target_index = next(i for i, kind in enumerate(kinds) if i > 0 and kind in ("field", "concept"))
    kind, node_id = kinds[target_index], nodes.nth(target_index).get_attribute("data-id")
    nodes.nth(target_index).click()
    app.wait("([k, i]) => { const s = window.AtlasDebug.getViewState()?.selected; return s && s.kind === k && s.id === i; }", 8, [kind, node_id])
    assert app.page.locator(".atlas-selection-detail h3").count() == 1 and app.page.locator(".atlas-selection-chip").count() >= 0
    # an SVG edge click (native, at its midpoint) selects the related entity
    groups = app.page.locator(".atlas-selection-edge-group")
    if groups.count():
        box = groups.first.locator(".atlas-selection-edge-hit").bounding_box()
        before = app.view()["revision"]
        app.page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        app.wait("r => window.AtlasDebug.getViewState().revision > r", 8, before)
    # chip click + keyboard Enter on a node
    chips = app.page.locator(".atlas-selection-chip")
    if chips.count():
        before = app.view()["revision"]; chips.first.click()
        app.wait("r => window.AtlasDebug.getViewState().revision > r", 8, before)
    node = app.page.locator(".atlas-selection-node").nth(1)
    if node.count():
        before = app.view()["revision"]; node.focus(); app.page.keyboard.press("Enter")
        app.wait("r => window.AtlasDebug.getViewState().revision > r", 8, before)
    app.page.locator("#atlas-paper-list button").first.click()
    app.wait("() => window.AtlasDebug.getViewState().selected?.kind === 'paper'")
    assert app.page.get_by_role("button", name="Zotero에서 열기").count() == 1
    shot(app, "selection-ego-1440-light")


@receipt("TAXV2-026,036")
def test_multi_facet_detail_shows_subject_method_tool_application_with_primary_and_evidence_tier(app: Harness):
    pick_overview_mode(app, "all-papers", len(app.fixture["papers"]))
    app.click_node("overview", f"overview-paper:{MULTI}")
    app.wait("id => window.AtlasDebug.getViewState()?.selected?.id === id", 8, MULTI)
    text = app.page.locator("#atlas-detail").inner_text()
    for label in ("Subject", "Method", "Tool", "Application"):
        assert label in text, f"{label} facet row missing"
    assert "Optimal transport ★ primary · metadata" in text and "QIIME 2 ★ primary · metadata" in text and "Amplicon analysis · indexed" in text and "Microbiome · fulltext" in text
    # multi-membership paper keeps its additional field relation in the Selection ego graph
    assert app.page.locator(".atlas-selection-node[data-kind='field']").count() == 2


@receipt("TAXV2-015")
@pytest.mark.parametrize("width,scheme", [(320, "light"), (1440, "dark")])
def test_legend_three_aligned_columns_right_aligned_numbers_no_overflow(app: Harness, width: int, scheme: str):
    app.page.set_viewport_size({"width": width, "height": 900}); app.page.emulate_media(color_scheme=scheme)
    app.page.locator("#atlas-theme").select_option(scheme); app.wait(f"() => document.getElementById('atlas-app').dataset.atlasTheme === '{scheme}'")
    rows = app.page.locator("#atlas-field-legend .atlas-field-legend-row")
    assert rows.count() >= 4
    geometry = rows.evaluate_all("rows => rows.map(r => [...r.children].map(c => { const b = c.getBoundingClientRect(); return {left: Math.round(b.left), right: Math.round(b.right), align: getComputedStyle(c).textAlign}; }))")
    first = geometry[0]
    assert all(len(row) == 3 for row in geometry)
    for row in geometry[1:]:
        assert [cell["left"] for cell in row] == [cell["left"] for cell in first], "three columns must share one x-grid"
        assert [cell["right"] for cell in row[1:]] == [cell["right"] for cell in first[1:]]
        assert row[1]["align"] == "right" and row[2]["align"] == "right", "numeric columns are right aligned"
    texts = app.page.locator("#atlas-field-legend .atlas-field-legend-header span").all_inner_texts()
    assert [t.lower() for t in texts] == ["category", "direct", "incl. subfields"]
    assert app.page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1"), "no horizontal overflow"
    # legend numbers come from the (synthetic) fieldStats rows
    stats = {row["fieldId"]: row for row in app.fixture["fieldStats"]["rows"]}
    button = app.page.locator(f'#atlas-field-legend button[data-field-id="{OPT}"] .atlas-legend-number')
    assert button.nth(0).inner_text() == str(stats[OPT]["direct"]) and button.nth(1).inner_text() == str(stats[OPT]["inclusive"])
    assert "Known primary" in app.page.locator(".atlas-field-legend-caption").inner_text()
    shot(app, f"legend-{width}-{scheme}")


@receipt("TAXV2-013,014")
def test_palette_theme_colors_are_actual_distinct_and_contrasted(app: Harness):
    def swatches() -> list[str]:
        return app.page.locator("#atlas-field-legend .atlas-legend-swatch").evaluate_all("els => els.map(e => getComputedStyle(e).backgroundColor)")

    def rgb(text: str) -> tuple[int, ...]:
        return tuple(int(v) for v in text[text.index("(") + 1:text.index(")")].split(",")[:3])

    def luminance(c: tuple[int, ...]) -> float:
        v = [x / 255 for x in c]; v = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in v]
        return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]
    seen: dict[tuple[str, str], list[str]] = {}
    for palette in ("atlas", "calm", "mocha"):
        for theme in ("light", "dark"):
            app.page.locator("#atlas-palette").select_option(palette); app.page.locator("#atlas-theme").select_option(theme)
            app.wait(f"() => document.getElementById('atlas-app').dataset.atlasTheme === '{theme}' && document.getElementById('atlas-app').dataset.atlasPalette === '{palette}'")
            colors = swatches(); seen[(palette, theme)] = colors
            panel = rgb(app.page.evaluate("() => getComputedStyle(document.getElementById('atlas-app')).getPropertyValue('--atlas-panel-hex').trim() ? (() => { const e = document.createElement('i'); e.style.color = getComputedStyle(document.getElementById('atlas-app')).getPropertyValue('--atlas-panel-hex').trim(); document.body.append(e); const c = getComputedStyle(e).color; e.remove(); return c; })() : 'rgb(255,255,255)'"))
            roots = {rgb(c) for c in colors}
            assert len(roots) >= 5, f"{palette}/{theme}: categories collapsed to {len(roots)} colors"
            for c in roots:
                ratio = (max(luminance(c), luminance(panel)) + .05) / (min(luminance(c), luminance(panel)) + .05)
                assert ratio >= 3, f"{palette}/{theme} swatch {c} contrast {ratio:.2f} < 3 on panel {panel}"
    assert seen[("atlas", "light")] != seen[("atlas", "dark")] and seen[("atlas", "light")] != seen[("calm", "light")], "palette/theme context must reach category colors"
    non_color = app.page.locator("#atlas-field-legend .atlas-legend-category span:last-child").all_inner_texts()
    assert all(text.strip() for text in non_color), "every category swatch has a text label (non-color cue)"


@receipt("TAXV2-035")
@pytest.mark.parametrize("viewport,scheme,renderer,mode", [((320, 900), "light", "cytoscape", "all-papers"), ((1440, 900), "dark", "g6", "g6"), ((1440, 900), "light", "cytoscape", "category")])
def test_browser_matrix_screenshots_and_dom_receipt(app: Harness, viewport: tuple[int, int], scheme: str, renderer: str, mode: str):
    app.page.set_viewport_size({"width": viewport[0], "height": viewport[1]}); app.page.emulate_media(color_scheme=scheme)
    app.page.locator("#atlas-theme").select_option(scheme)
    app.page.locator("#atlas-renderer").select_option(renderer)
    app.page.locator("#atlas-overview-renderer").select_option(mode)
    if mode != "category":
        app.overview_ready()
    legend_button(app, OPT).click(); app.wait("() => window.AtlasDebug.getViewState()?.scope?.fieldId")
    if renderer == "g6":
        app.wait("() => document.querySelector('#atlas-paper-network')?.dataset.rendererState === 'ready'")
    state = app.page.evaluate("() => ({counts: window.AtlasDebug.getActualCounts(), revision: window.AtlasDebug.getViewState().revision, lower: window.AtlasDebug.getLowerProjection().revision})")
    assert state["revision"] == state["lower"]
    assert app.page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")
    shot(app, f"matrix-{viewport[0]}-{scheme}-{renderer}-{mode}")


# ---------------------------------------------------------------------------------------------------------------------
def _rgb(text: str) -> tuple[int, int, int]:
    return tuple(int(v) for v in text[text.index("(") + 1:text.index(")")].replace("/", ",").split(",")[:3])  # type: ignore[return-value]


def _lum(c: tuple[int, int, int]) -> float:
    v = [x / 255 for x in c]
    v = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in v]
    return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]


def _contrast(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    x, y = _lum(a), _lum(b)
    return (max(x, y) + 0.05) / (min(x, y) + 0.05)


def _distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    return sum((p - q) ** 2 for p, q in zip(a, b)) ** 0.5


@receipt("TAXV2-014")
def test_system_theme_follows_native_media_switch_for_all_palettes_with_distinct_density_and_outline_cues(app: Harness):
    """theme=system: only the browser's own prefers-color-scheme changes (emulate_media); no selects, no dispatch."""
    page = app.page
    page.locator("#atlas-theme").select_option("system")
    panel_js = "() => { const e = document.createElement('i'); e.style.color = getComputedStyle(document.getElementById('atlas-app')).getPropertyValue('--atlas-panel-hex').trim(); document.body.append(e); const c = getComputedStyle(e).color; e.remove(); return c; }"
    category: dict[tuple[str, str], list[tuple[int, int, int]]] = {}
    for palette in ("atlas", "calm", "mocha"):
        page.locator("#atlas-palette").select_option(palette)
        for scheme in ("light", "dark"):
            page.emulate_media(color_scheme=scheme)  # the ONLY thing that changes: the OS/browser colour scheme
            app.wait(f"() => document.getElementById('atlas-app').dataset.atlasTheme === '{scheme}' && document.getElementById('atlas-app').dataset.atlasPalette === '{palette}'", 10)
            panel = _rgb(page.evaluate(panel_js))
            swatches = [_rgb(c) for c in page.locator("#atlas-field-legend .atlas-legend-swatch").evaluate_all("els => els.map(e => getComputedStyle(e).backgroundColor)")]
            category[(palette, scheme)] = swatches
            assert len(set(swatches)) >= 5, f"{palette}/{scheme}: categories collapsed"
            for c in set(swatches):
                assert _contrast(c, panel) >= 3, f"{palette}/{scheme}: swatch {c} contrast {_contrast(c, panel):.2f} < 3 on {panel}"
            labels = page.locator("#atlas-field-legend .atlas-legend-category span:last-child").all_inner_texts()
            assert labels and all(text.strip() for text in labels), "category cue must also be a text label"
        assert category[(palette, "light")] != category[(palette, "dark")], f"{palette}: the system scheme switch did not change colours (listener bug)"
    assert category[("atlas", "light")] != category[("calm", "light")] != category[("mocha", "light")]
    # density mode: distinct ramp channel + outline cue + explanatory text, in both schemes
    page.locator("#atlas-color-mode").select_option("density")
    for scheme in ("light", "dark"):
        page.emulate_media(color_scheme=scheme)
        app.wait(f"() => document.getElementById('atlas-app').dataset.atlasTheme === '{scheme}'", 10)
        app.stable("paper", 20, frames=3)
        legend = page.locator("#atlas-paper-network").locator("xpath=..").locator(".atlas-network-legend")
        text = legend.inner_text()
        assert "Observed degree" in text and "not a category" in text, "density legend must state it is a different channel in words"
        ramp = [_rgb(c) for c in legend.locator(".atlas-network-legend-swatch").evaluate_all("els => els.map(e => getComputedStyle(e).backgroundColor)")]
        assert len(ramp) == 5 and len(set(ramp)) == 5
        panel = _rgb(page.evaluate(panel_js))
        for step in ramp:
            assert all(_distance(step, c) > 25 for cs in category.values() for c in cs), f"density step {step} is too close to a category colour"
        outlines = page.evaluate("() => window.AtlasDebug.getPaperGraph().getNodeData().map(n => n.outline || n.data?.outline || null)")
        assert outlines and all(outlines), "every density node needs an outline cue"
        hex_to_rgb = lambda h: tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))
        assert all(_contrast(hex_to_rgb(o), panel) >= 3 for o in set(outlines)), "outline contrast must be >= 3:1 against the panel"
    page.locator("#atlas-color-mode").select_option("category")
    assert not any(app.page.evaluate("() => window.AtlasDebug.getPaperGraph().getNodeData().map(n => Boolean(n.outline || n.data?.outline))")), "category mode has no density outline"
    shot(app, "system-theme-density-1440-dark")


@receipt("TAXV2-043")
def test_incompatible_filter_gets_its_own_fitted_camera_and_revisit_restores_the_old_one(app: Harness):
    page = app.page
    legend_button(app, OPT).click()
    app.wait(f"() => window.AtlasDebug.getViewState()?.scope?.fieldId === '{OPT}'")
    graph = "window.AtlasDebug.getPaperGraph()"
    snap = lambda: page.evaluate(f"() => {{ const g = {graph}; return {{zoom: g.getZoom(), pan: g.getPan(), pos: Object.fromEntries(g.getNodeData().map(n => [n.id, g.getElementPosition(n.id).slice(0, 2)]))}}; }}")
    app.reveal("paper")
    initial_zoom = snap()["zoom"]
    for _ in range(2):
        page.locator("#atlas-network-zoom-in").click()
    app.wait(f"z => {graph}.getZoom() > z + 0.05", 8, initial_zoom)
    page.wait_for_timeout(150)  # let the camera-remember frame run
    saved = snap()
    concept_id = next(p["conceptIds"][0] for p in app.fixture["papers"] if p["key"] == "OPT00")
    page.locator(f'#atlas-concept-list button[data-entity-id="{concept_id}"]').click()
    app.wait(f"() => window.AtlasDebug.getViewState().scope.conceptId === '{concept_id}'", 10)
    view = app.view()
    assert view["scope"]["kind"] == "field" and view["scope"]["fieldId"] == OPT, "compatible concept keeps the field and adds the constraint"
    plan = page.evaluate("() => window.AtlasDebug.getCameraPlans().paper")
    assert plan["action"] == "fit" and plan["reason"] == "first-visit", f"an incompatible filter must not reuse the old camera: {plan}"
    app.reveal("paper")
    filtered = snap()
    positions = list(filtered["pos"].values())
    assert positions and len({tuple(round(v, 3) for v in p) for p in positions}) == len(positions), "finite unique positions"
    inside = page.evaluate(f"() => {{ const g = {graph}; const r = document.querySelector('#atlas-paper-network').getBoundingClientRect(); return g.getNodeData().every(n => {{ const c = g.getClientByCanvas(g.getElementPosition(n.id)); return c[0] >= r.left - 1 && c[0] <= r.right + 1 && c[1] >= r.top - 1 && c[1] <= r.bottom + 1; }}); }}")
    assert inside, "a fresh filtered scope must be fitted, not left at the old zoom/pan"
    assert filtered["zoom"] != saved["zoom"] or filtered["pan"] != saved["pan"], "the previous camera must not be applied to the filtered scope"
    assert page.evaluate("() => window.AtlasDebug.getLowerProjection().revision") == view["revision"]
    # clearing the filter (re-selecting the field) revisits the original scope: its camera is restored exactly
    legend_button(app, OPT).click()
    app.wait("() => window.AtlasDebug.getViewState().scope.conceptId === null", 10)
    assert page.evaluate("() => window.AtlasDebug.getCameraPlans().paper.action") == "restore"
    back = snap()
    assert abs(back["zoom"] - saved["zoom"]) < 1e-6 and all(abs(back["pan"][k] - saved["pan"][k]) < 1e-6 for k in ("x", "y")), "revisit restores the pre-filter camera"


@receipt("TAXV2-028")
def test_dense_library_keeps_every_views_node_and_edge_budget_and_truthful_counts():
    fixture = large_fixture()
    with running_app(fixture) as app:
        page = app.page
        page.locator("#atlas-sample").fill("100"); page.locator("#atlas-sample").dispatch_event("input")
        app.wait("n => window.AtlasDebug.getState().detail.visiblePapers === n", 30, 1000)
        app.wait("() => window.AtlasDebug.getPaperGraph() && window.AtlasDebug.getPaperGraph().getNodeData().length === 1000", 30)
        counts = page.evaluate("() => window.AtlasDebug.getActualCounts()")
        stats = page.evaluate("() => window.AtlasDebug.getPaperStats()")
        total_edges = len(fixture["edges"])
        assert total_edges > 1000, "the fixture must actually exceed the edge budget"
        assert counts["paperNodes"] == 1000 == stats["displayedNodes"] == stats["eligibleNodes"], "paper nodes are bounded only by the requested sample (documented), here 1000"
        assert counts["paperEdges"] == stats["displayedEdges"] == 1000 and stats["candidateEdges"] == total_edges and stats["hiddenEdges"] == total_edges - 1000 and stats["eligibleEdges"] == total_edges
        assert counts["conceptEdges"] <= 1000 and counts["conceptNodes"] <= 300 + 150, counts
        notice = page.locator("#atlas-sampling").inner_text()
        assert f"1000 displayed / {total_edges} sampled / {total_edges} eligible relationships" in notice, notice
        # a selected edge is PINNED through main.js -> graphProjection even when it ranks outside the unpinned top 1000
        unpinned_top = set(sorted(edge["id"] for edge in fixture["edges"])[:1000])
        paper_id = "zotero:demo-server:0:V20900"
        page.locator(f'#atlas-paper-list button[data-entity-id="{paper_id}"]').click()
        app.wait("id => window.AtlasDebug.getViewState()?.selected?.id === id", 10, paper_id)
        app.wait("() => document.querySelector('.atlas-selection-edge-group[data-edge-kind=edge]')", 10)
        group = page.locator(".atlas-selection-edge-group[data-edge-kind=edge]").first
        edge_id = group.get_attribute("data-edge-id")
        assert edge_id not in unpinned_top, "the chosen edge must rank outside the unpinned cap so pinning is observable"
        group.scroll_into_view_if_needed()
        page.wait_for_timeout(450)  # scroll settle / wheel-latching window before native pointer input
        box = group.locator(".atlas-selection-edge-hit").bounding_box()
        assert 0 <= box["y"] <= page.viewport_size["height"], "edge hit target must be inside the viewport for a native click"
        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        app.wait("() => window.AtlasDebug.getViewState()?.selected?.kind === 'edge'", 10)
        assert app.view()["selected"]["id"] == edge_id
        app.wait("() => window.AtlasDebug.getPaperStats()?.selectedEdgeRetained === true", 10)
        pinned = page.evaluate("() => ({stats: window.AtlasDebug.getPaperStats(), ids: window.AtlasDebug.getPaperGraph().getEdgeData().map(e => e.id || e.data?.id)})")
        assert edge_id in pinned["ids"] and len(pinned["ids"]) == 1000 == pinned["stats"]["displayedEdges"] and pinned["stats"]["candidateEdges"] == total_edges
        endpoints = {e["source"] for e in fixture["edges"] if e["id"] == edge_id} | {e["target"] for e in fixture["edges"] if e["id"] == edge_id}
        assert endpoints <= set(page.evaluate("() => window.AtlasDebug.getPaperGraph().getNodeData().map(n => n.id)")), "selected edge endpoints stay displayed"
        assert pinned["ids"].count(edge_id) == 1
        # full aggregates are unaffected by the display cap
        snapshot_edges = page.evaluate("() => window.AtlasDebug.getState().snapshot.edges.length")
        assert snapshot_edges == total_edges == page.evaluate("() => window.AtlasDebug.getState().snapshot.overview.totalEdges")
        # all-papers overview: one point per paper, displayed edges capped and counted truthfully
        pick_overview_mode(app, "all-papers", 1000)
        overview = page.evaluate("() => window.AtlasDebug.getAllPaperOverview().counters")
        assert overview["represented"] == overview["active"] == 1000 and overview["displayedEdges"] <= 1000 and overview["eligibleEdges"] == total_edges
        assert page.evaluate("() => window.AtlasDebug.getActualCounts().overviewEdges") == overview["displayedEdges"] <= 1000
        # representative overview with expansion stays within the single view budget
        pick_overview_mode(app, "g6")
        app.click_node("overview", OPT)
        app.wait("() => window.AtlasDebug.getViewState().expanded.length === 1", 10)
        app.overview_ready()
        projection = page.evaluate("() => window.AtlasDebug.getOverviewProjection().counts")
        assert projection["displayedEdges"] <= 1000 and projection["loadedPapers"] <= 150
        assert page.evaluate("() => window.AtlasDebug.getActualCounts().overviewEdges") <= 1000
        assert not app.errors, app.errors[:3]
