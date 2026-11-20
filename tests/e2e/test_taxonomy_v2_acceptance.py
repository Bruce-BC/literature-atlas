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
from v2_support import (CAMERA_IDLE, FIXTURE_VALIDATION, ANALYSIS, MULTI, OPT, OPT_DIRECT, OT, EPOCH, ROOT, SERVER, UNKNOWN, Harness, acceptance_fixture, collections_fixture, edges_fixture, HUB, add_backend_style_extensions, fixture_hash, large_fixture, legacy_screen, open_advanced, recompute_overview, running_app)  # noqa: E402

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
        legacy_screen(harness.page)  # these tests drive legacy controls: Advanced open + Circular ribbons, the pre-AC-11 default
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
    app.page.locator("#atlas-overview-renderer").select_option("ribbon")  # the field legend belongs to the legacy modes (hidden on the collection map)

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
    # collections mode: points take the cloud hue, so the category legend is hidden there (and returns in a legacy mode)
    pick_overview_mode(app, "collections")
    assert app.page.locator("#atlas-field-legend").is_hidden() and app.page.locator("#atlas-field-legend").get_attribute("hidden") is not None
    app.page.locator("#atlas-overview-renderer").select_option("all-papers"); app.overview_ready()
    assert app.page.locator("#atlas-field-legend").is_visible()


@receipt("TAXV2-035")
@pytest.mark.parametrize("viewport,scheme,renderer,mode", [((320, 900), "light", "cytoscape", "all-papers"), ((1440, 900), "dark", "g6", "g6"), ((1440, 900), "light", "cytoscape", "category"), ((1440, 900), "light", "g6", "collections")])
def test_browser_matrix_screenshots_and_dom_receipt(app: Harness, viewport: tuple[int, int], scheme: str, renderer: str, mode: str):
    app.page.set_viewport_size({"width": viewport[0], "height": viewport[1]}); app.page.emulate_media(color_scheme=scheme)
    app.page.locator("#atlas-theme").select_option(scheme)
    app.page.locator("#atlas-renderer").select_option(renderer)
    app.page.locator("#atlas-overview-renderer").select_option(mode)
    if mode != "category":
        app.overview_ready()
    # the collection map hides the category legend (cloud colours): the same field focus comes from the Fields tree there
    (app.page.locator(f'#atlas-field-list button[data-entity-id="{OPT}"]') if mode == "collections" else legend_button(app, OPT)).click(); app.wait("() => window.AtlasDebug.getViewState()?.scope?.fieldId")
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
    page.locator("#atlas-overview-renderer").select_option("ribbon")  # the field legend belongs to the legacy modes (hidden on the collection map)
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
        legacy_screen(page)
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


# ---------------------------------------------------------------------------------------------------------------------
# Collection cloud map (opt-in `collections` overview): sibling overlay, unique counters, AC-3/AC-4/AC-7 click paths.
C05 = f"collection:{SERVER}:0:C05"
LABEL_BOXES = "() => [...document.querySelectorAll('#atlas-cloud-overlay .atlas-cloud-label')].map(b => { const r = b.getBoundingClientRect(); return {id: b.dataset.cloudId, hidden: b.hidden, x: r.x, y: r.y, w: r.width, h: r.height, text: b.textContent}; })"
# A client point inside the cloud circle whose nearest point is farther than `gap` px (null when none exists at this zoom).
EMPTY_IN_CLOUD = """([cloudId, gap]) => { const g = window.AtlasDebug.getOverviewGraph(); const cloud = window.AtlasDebug.getAllPaperOverview().clouds.find(c => c.id === cloudId); const [cx, cy] = g.getClientByCanvas([cloud.cx, cloud.cy]); const r = cloud.r * g.getZoom(); const pts = g.getNodeData().map(n => g.getClientByCanvas(g.getElementPosition(n.id))); const box = document.querySelector('#atlas-field-network').getBoundingClientRect();
  for (let d = 0; d < 0.92 * r; d += 3) for (let a = 0; a < 360; a += 10) { const x = cx + Math.cos(a * Math.PI / 180) * d, y = cy + Math.sin(a * Math.PI / 180) * d; if (x < box.left + 4 || x > box.right - 4 || y < box.top + 4 || y > box.bottom - 4) continue; if (pts.every(p => Math.hypot(p[0] - x, p[1] - y) > gap)) return {x, y}; } return null; }"""
# Same search, restricted to spots INSIDE (inside=true) or OUTSIDE (inside=false) the drawn outline path (model hull test via SVG isPointInFill in the path's local frame).
EMPTY_BY_PATH = """([cloudId, gap, inside]) => { const g = window.AtlasDebug.getOverviewGraph(); const cloud = window.AtlasDebug.getAllPaperOverview().clouds.find(c => c.id === cloudId); const path = document.querySelector(`.atlas-cloud-hull[data-cloud-id="${CSS.escape(cloudId)}"]`); if (!path.getAttribute('d')) return null; const [cx, cy] = g.getClientByCanvas([cloud.cx, cloud.cy]); const r = cloud.r * g.getZoom(); const pts = g.getNodeData().map(n => g.getClientByCanvas(g.getElementPosition(n.id))); const box = document.querySelector('#atlas-field-network').getBoundingClientRect();
  for (let d = 0; d < 0.97 * r; d += 2) for (let a = 0; a < 360; a += 5) { const x = cx + Math.cos(a * Math.PI / 180) * d, y = cy + Math.sin(a * Math.PI / 180) * d; if (x < box.left + 4 || x > box.right - 4 || y < box.top + 4 || y > box.bottom - 4) continue; if (!pts.every(p => Math.hypot(p[0] - x, p[1] - y) > gap)) continue; const [wx, wy] = g.getCanvasByClient([x, y]); if (Math.hypot(wx - cloud.cx, wy - cloud.cy) > cloud.r) continue; if (path.isPointInFill(new DOMPoint(wx - cloud.cx, wy - cloud.cy)) === inside) return {x, y}; } return null; }"""
# Model circle (data-cx/cy/r) and drawn outline of every hull: local path bbox and centre-in-fill test of each of its points (isPointInFill, centres only).
HULL_GEOMETRY = """() => { const p = window.AtlasDebug.getAllPaperOverview(); return [...document.querySelectorAll('.atlas-cloud-hull')].map(el => { const cx = +el.dataset.cx, cy = +el.dataset.cy, r = +el.dataset.r; const b = el.getAttribute('d') ? el.getBBox() : null; const pts = p.points.filter(q => q.cloudId === el.dataset.cloudId);
  return {id: el.dataset.cloudId, tag: el.tagName, x: cx, y: cy, r, kind: el.dataset.kind, method: el.dataset.method, d: el.getAttribute('d'), bbox: b && {x: b.x, y: b.y, w: b.width, h: b.height}, points: pts.length, outsideFill: b ? pts.filter(q => !el.isPointInFill(new DOMPoint(q.deterministicX - cx, q.deterministicY - cy))).length : null}; }); }"""
HULL_STROKES = "() => [...document.querySelectorAll('.atlas-cloud-hull')].map(el => getComputedStyle(el).stroke)"
# Per node: data colour, rendered fill and canvas coordinates.
POINT_STYLE = "() => { const g = window.AtlasDebug.getOverviewGraph(); return Object.fromEntries(g.getNodeData().map(n => [n.id, {color: n.data.categoryColor, fill: g.getElementRenderStyle(n.id).fill, xy: g.getElementPosition(n.id).slice(0, 2)}])); }"
HULL_D = "() => Object.fromEntries([...document.querySelectorAll('.atlas-cloud-hull')].map(el => [el.dataset.cloudId, el.getAttribute('d')]))"


@pytest.fixture()
def cloud_app():
    fixture = collections_fixture()
    with running_app(fixture) as harness:
        yield harness
        assert not harness.errors, f"browser console/page errors: {harness.errors[:3]}"


def open_collections(app: Harness) -> dict[str, Any]:
    # AC-11: the collection cloud map IS the default screen; reaching it takes no <select> interaction
    assert app.page.evaluate("() => window.AtlasDebug.getState().overviewRenderer") == "collections"
    projection = app.wait("() => { const p = window.AtlasDebug.getAllPaperOverview(); return p?.grouping === 'collection' ? p : null; }")
    app.overview_ready(len(projection["points"]))
    app.wait(CAMERA_IDLE, 10)
    app.wait("() => (window.AtlasDebug.getCloudLayoutStats()?.overlay?.placements || 0) > 0", 10)
    return app.page.evaluate("() => window.AtlasDebug.getAllPaperOverview()")


def overlap(a: dict[str, float], b: dict[str, float]) -> bool:
    return a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"] and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"]


def selected_paper_points(app: Harness) -> list[str]:
    return app.page.evaluate("() => window.AtlasDebug.getG6Stats().overview.selectedIds")


@receipt("TAXV2-017,042,027;AC-1,AC-3,AC-4")
@pytest.mark.parametrize("width,which", [(1440, "collections"), (720, "collections"), (720, "large")])
def test_collections_mode_separated_labeled_clouds_unique_counters_and_label_boxes(width: int, which: str):
    fixture = collections_fixture() if which == "collections" else large_fixture()
    with running_app(fixture, viewport=(width, 1000)) as app:
        projection = open_collections(app)
        page = app.page
        active = {paper["id"] for paper in fixture["papers"]}
        filed: dict[str, set[str]] = {c["id"]: set(c["paperIds"]) & active for c in fixture["collections"] if set(c["paperIds"]) & active}
        unfiled = active - set().union(*filed.values())
        # AC-1: one cloud per non-empty collection + exactly one unfiled cloud
        assert {c["id"] for c in projection["clouds"]} == set(filed) | ({"cloud:unfiled"} if unfiled else set())
        assert [c["unfiled"] for c in projection["clouds"]].count(True) == (1 if unfiled else 0)
        # AC-4: replica points, unique counters, node IDs, graph = points only
        expected_points = sum(len(ids) for ids in filed.values()) + len(unfiled)
        nodes = app.overview_nodes()
        assert len(nodes) == expected_points == projection["counters"]["points"] and not page.evaluate("() => window.AtlasDebug.getOverviewGraph().getEdgeData().length")
        assert {n["id"] for n in nodes} == {f"overview-point:{cid}:{pid}" for cid, ids in filed.items() for pid in ids} | {f"overview-point:unfiled:{pid}" for pid in unfiled}
        assert set(projection["representedIds"]) == active and projection["counters"]["active"] == projection["counters"]["represented"] == len(active)
        debug = page.evaluate("() => window.AtlasDebug.getAllPaperOverview().counters")
        assert debug["viewport"] == len(active), "viewport counts unique papers, not replica points"
        assert f"{len(active)} active · {expected_points} points" in page.locator("#atlas-overview-counters").inner_text()
        assert page.locator("#atlas-field-network").evaluate("el => el.dataset.layout") == "preset"
        assert page.evaluate("() => window.AtlasDebug.getG6Stats().overview.layoutRuns") == 0
        # AC-2: model cloud circles (the hull's data-cx/cy/r) exist for every cloud and never overlap; AE-5 on screen: each drawn outline stays in its circle and holds its points
        circles = page.evaluate(HULL_GEOMETRY)
        assert {c["id"] for c in circles} == {c["id"] for c in projection["clouds"]} and all(c["tag"] == "path" for c in circles)
        model = {c["id"]: c for c in projection["clouds"]}
        assert all(abs(c["x"] - model[c["id"]]["cx"]) < 1e-6 and abs(c["y"] - model[c["id"]]["cy"]) < 1e-6 and abs(c["r"] - model[c["id"]]["r"]) < 1e-6 for c in circles), "data-cx/cy/r carry the model circle"
        for i, a in enumerate(circles):
            for b in circles[i + 1:]:
                assert ((a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2) ** 0.5 > a["r"] + b["r"], f"clouds {a['id']} and {b['id']} overlap on screen"
        for c in circles:
            if c["kind"] == "single":
                assert c["points"] == 1 and not c["d"], f"a 1-paper cloud draws no outline: {c}"
                continue
            box = c["bbox"]
            assert c["d"] and box["x"] >= -c["r"] - 0.5 and box["y"] >= -c["r"] - 0.5 and box["x"] + box["w"] <= c["r"] + 0.5 and box["y"] + box["h"] <= c["r"] + 0.5, f"outline bbox leaves the cloud circle: {c}"
            assert c["outsideFill"] == 0, f"{c['outsideFill']} point centre(s) of {c['id']} lie outside its outline: {c}"
        # AC-3: every label shown at 720/1440 Fit, real DOM boxes never overlap each other or leave the map
        labels = page.evaluate(LABEL_BOXES)
        assert len(labels) == len(projection["clouds"]) and not any(row["hidden"] for row in labels), labels
        assert page.locator("#atlas-cloud-chips").is_hidden() and page.locator("#atlas-cloud-chips button").count() == 0
        for i, a in enumerate(labels):
            assert a["w"] <= 160.5 and a["h"] > 0 and a["text"]
            for b in labels[i + 1:]:
                assert not overlap(a, b), f"labels overlap: {a} / {b}"
        box = page.locator("#atlas-field-network").bounding_box()
        assert box["height"] >= 420 - 1, "collections mode reserves min-height 420 px for the map"
        assert all(box["x"] - 0.5 <= l["x"] and l["x"] + l["w"] <= box["x"] + box["width"] + 0.5 and box["y"] - 0.5 <= l["y"] and l["y"] + l["h"] <= box["y"] + box["height"] + 0.5 for l in labels)
        # the G6 canvas is transparent: the hull layer UNDER it stays visible (no pixels away from points) — probed inside the drawn outline
        spot = page.evaluate(EMPTY_BY_PATH, [max(projection["clouds"], key=lambda c: c["r"])["id"], 12, True]) or page.evaluate("() => { const r = document.querySelector('#atlas-field-network').getBoundingClientRect(); return {x: r.left + 6, y: r.top + 6}; }")
        assert not app.pixel_painted("overview", spot["x"], spot["y"]), "G6 canvas paints a background over the hull layer"
        # theme change: same graph, no layout, overlay keeps its placement (overlay colours are per-element CSS variables, points a G6 style update)
        strokes_light = page.evaluate(HULL_STROKES)
        open_advanced(page)  # Theme lives in Advanced (AC-11); opening it must not touch the map either
        stats = page.evaluate("() => ({g6: window.AtlasDebug.getG6Stats().overview, cloud: window.AtlasDebug.getCloudLayoutStats()})")
        assert stats["g6"]["created"] == 1 and stats["g6"]["layoutRuns"] == 0
        placements = page.evaluate("() => window.AtlasDebug.getCloudLayoutStats().overlay.placements")
        before = page.evaluate(POINT_STYLE); outlines = page.evaluate(HULL_D); computed = page.evaluate("() => window.AtlasDebug.getCloudLayoutStats().outlines.computed")
        page.locator("#atlas-theme").select_option("dark"); app.wait("() => document.getElementById('atlas-app').dataset.atlasTheme === 'dark'")
        app.wait("() => window.AtlasDebug.getCloudLayoutStats()?.theme === 'dark'", 10); app.wait(CAMERA_IDLE, 10)
        stats = page.evaluate("() => ({g6: window.AtlasDebug.getG6Stats().overview, cloud: window.AtlasDebug.getCloudLayoutStats()})")
        assert stats["g6"]["created"] == 1 and stats["g6"]["layoutRuns"] == 0 and stats["cloud"]["overlay"]["placements"] == placements
        # AE-9: colours only — outline d strings and point coordinates identical, point colours (data + rendered fill) and hull strokes changed, no outline recomputed
        after = page.evaluate(POINT_STYLE)
        assert page.evaluate(HULL_D) == outlines, "a theme change must not touch outline geometry"
        assert stats["cloud"]["outlines"]["computed"] == computed and stats["g6"]["topologyChanges"] == 0
        assert {k: v["xy"] for k, v in after.items()} == {k: v["xy"] for k, v in before.items()}, "point coordinates are unchanged"
        assert all(after[k]["color"] != before[k]["color"] and after[k]["fill"] == after[k]["color"] for k in after), "every point takes its cloud's dark-theme point tone"
        assert all(a != b for a, b in zip(page.evaluate(HULL_STROKES), strokes_light)), "hull strokes swap to the dark tones"
        assert not app.errors, app.errors[:3]


@receipt("AC-3")
def test_collections_narrow_width_hidden_labels_equal_cloud_list_chips():
    with running_app(large_fixture(), viewport=(320, 900)) as app:
        projection = open_collections(app)
        labels = app.page.evaluate(LABEL_BOXES)
        hidden = [row["id"] for row in labels if row["hidden"]]
        chips = app.page.locator("#atlas-cloud-chips button").evaluate_all("els => els.map(e => e.dataset.cloudId)")
        assert hidden, "the 320 px fixture must actually hide labels so the chip contract is exercised"
        assert sorted(hidden) == sorted(chips) and len(labels) == len(projection["clouds"])
        visible = [row for row in labels if not row["hidden"]]
        for i, a in enumerate(visible):
            for b in visible[i + 1:]:
                assert not overlap(a, b), f"labels overlap: {a} / {b}"
        assert app.page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")
        app.page.locator(f'#atlas-cloud-chips button[data-cloud-id="{chips[0]}"]').click()
        app.wait("id => document.querySelector('#atlas-cloud-overlay').dataset.selectedCloud === id", 10, chips[0])
        assert app.view()["selected"] is None and selected_paper_points(app) == []
        assert not app.errors, app.errors[:3]


@receipt("AC-7")
def test_cloud_label_click_and_keyboard_select_cloud_without_paper_and_miss_click_inside_circle_selects_cloud(cloud_app: Harness):
    app = cloud_app; page = app.page
    projection = open_collections(app)
    app.reveal("overview")
    target = next(row for row in page.evaluate(LABEL_BOXES) if row["id"] == C05)
    before = app.view()
    page.mouse.click(target["x"] + target["w"] / 2, target["y"] + target["h"] / 2)  # native click on the label button
    app.wait("id => document.querySelector('#atlas-cloud-overlay').dataset.selectedCloud === id", 10, C05)
    assert app.view()["selected"] is None and before["selected"] is None, "a label click selects the cloud, never a paper"
    assert selected_paper_points(app) == [] and page.evaluate("() => window.AtlasDebug.getCloudLayoutStats().selectedCloudId") == C05
    points = page.evaluate("() => window.AtlasDebug.getAllPaperOverview().points")
    assert {p["cloudId"] for p in points if not p["dimmed"]} == {C05} and len([p for p in points if not p["dimmed"]]) == 7, "points outside the selected cloud are dimmed (none removed)"
    assert page.locator(f'.atlas-cloud-hull[data-cloud-id="{C05}"]').get_attribute("class").split().count("is-selected") == 1
    # keyboard: Tab reaches the next label button, Enter activates it
    page.keyboard.press("Tab")
    focused = page.evaluate("() => document.activeElement?.classList.contains('atlas-cloud-label') ? document.activeElement.dataset.cloudId : null")
    assert focused and focused != C05
    page.keyboard.press("Enter")
    app.wait("id => document.querySelector('#atlas-cloud-overlay').dataset.selectedCloud === id", 10, focused)
    assert app.view()["selected"] is None and selected_paper_points(app) == []
    # a click that misses every point but lands inside a cloud circle selects that cloud (zoom in so gaps exceed the 9 px hit radius)
    for _ in range(4):
        page.locator("#atlas-network-zoom-in").click(); app.wait(CAMERA_IDLE, 10)
    big = next(c["id"] for c in sorted(projection["clouds"], key=lambda c: -c["size"]) if page.evaluate(EMPTY_IN_CLOUD, [c["id"], 14]))
    spot = page.evaluate(EMPTY_IN_CLOUD, [big, 14])
    misses = page.evaluate("() => window.AtlasDebug.getG6Stats().overview.missedClicks")
    page.mouse.click(spot["x"], spot["y"])
    app.wait("id => document.querySelector('#atlas-cloud-overlay').dataset.selectedCloud === id", 10, big)
    assert page.evaluate("() => window.AtlasDebug.getG6Stats().overview.missedClicks") == misses + 1
    assert app.view()["selected"] is None and selected_paper_points(app) == []
    # hit-testing stays on the circle: a spot inside the circle but OUTSIDE the drawn outline selects that cloud too, and hovering it highlights the outline (AE-10)
    other = next((c["id"], spot) for c in sorted(projection["clouds"], key=lambda c: -c["size"]) if c["id"] != big for spot in [page.evaluate(EMPTY_BY_PATH, [c["id"], 14, False])] if spot)
    cloud_id, spot = other
    page.mouse.move(spot["x"] + 1, spot["y"] + 1); page.mouse.move(spot["x"], spot["y"])
    app.wait("id => { const o = window.AtlasDebug.getCloudLayoutStats().overlay; return o.hoverCloudId === id && o.hoverOutsideOutline === true; }", 10, cloud_id)
    assert page.locator(f'.atlas-cloud-hull[data-cloud-id="{cloud_id}"]').get_attribute("class").split().count("is-hover") == 1 and page.locator(".atlas-cloud-hull.is-hover").count() == 1
    misses = page.evaluate("() => window.AtlasDebug.getG6Stats().overview.missedClicks")
    page.mouse.click(spot["x"], spot["y"])
    app.wait("id => document.querySelector('#atlas-cloud-overlay').dataset.selectedCloud === id", 10, cloud_id)
    assert page.evaluate("() => window.AtlasDebug.getG6Stats().overview.missedClicks") == misses + 1
    assert app.view()["selected"] is None and selected_paper_points(app) == []


@receipt("AC-4,AC-7;TAXV2-042")
def test_cloud_point_click_selects_paper_all_replicas_and_reports_its_cloud_and_hover_shows_replica_lines(cloud_app: Harness):
    app = cloud_app; page = app.page
    open_collections(app)
    open_advanced(page)  # the Selection card read below lives in Advanced (AC-11)
    replicas = sorted(n["id"] for n in app.overview_nodes() if n["data"]["paperId"] == MULTI)
    assert len(replicas) == 2, "MULTI00 is filed in two collections"
    clicked = f"overview-point:{C05}:{MULTI}"
    point = app.click_node("overview", clicked)
    app.wait("id => window.AtlasDebug.getViewState()?.selected?.id === id", 10, MULTI)
    assert app.view()["selected"] == {"kind": "paper", "id": MULTI}
    app.wait("ids => JSON.stringify([...window.AtlasDebug.getG6Stats().overview.selectedIds].sort()) === JSON.stringify(ids)", 10, replicas)
    assert page.locator("#atlas-cloud-overlay").get_attribute("data-selected-cloud") == C05, "the point click reports its cloud"
    paper = next(p for p in app.fixture["papers"] if p["id"] == MULTI)
    assert page.locator(".atlas-selection-detail h3").first.inner_text() == paper["title"]
    stats = page.evaluate("() => window.AtlasDebug.getG6Stats().overview")
    assert stats["nativeClicks"] + stats["physicalClicks"] >= 1
    page.mouse.move(point["x"] + 30, point["y"] + 30); page.mouse.move(point["x"], point["y"], steps=4)
    app.wait("() => window.AtlasDebug.getCloudLayoutStats().overlay.replicaLines === 1", 10)
    assert page.locator(".atlas-cloud-replica").count() == 1


@receipt("AC-14;overlay-sync")
def test_cloud_overlay_stays_aligned_with_points_after_native_pan_and_zoom(cloud_app: Harness):
    app = cloud_app; page = app.page
    projection = open_collections(app)
    app.reveal("overview")
    # REAL rendered coordinates: the model circle (data-cx/cy/r) projected through the overlay world group's screen CTM vs the G6 projection,
    # and the drawn path's getBBox() through its own screen CTM vs the model outline bbox projected by G6.
    ALIGN = """() => { const g = window.AtlasDebug.getOverviewGraph(); const p = window.AtlasDebug.getAllPaperOverview(); const z = g.getZoom(); const world = document.querySelector('.atlas-cloud-hulls .atlas-cloud-world').getScreenCTM();
      const screenBox = (m, b) => { const xs = [], ys = []; for (const [x, y] of [[b.x, b.y], [b.x + b.width, b.y], [b.x, b.y + b.height], [b.x + b.width, b.y + b.height]]) { const q = new DOMPoint(x, y).matrixTransform(m); xs.push(q.x); ys.push(q.y); } return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)]; };
      return p.clouds.map(c => { const el = document.querySelector(`.atlas-cloud-hull[data-cloud-id="${CSS.escape(c.id)}"]`); const centre = new DOMPoint(+el.dataset.cx, +el.dataset.cy).matrixTransform(world); const [x, y] = g.getClientByCanvas([c.cx, c.cy]);
        const pts = p.points.filter(q => q.cloudId === c.id).map(q => g.getClientByCanvas(g.getElementPosition(q.id))); const mx = pts.reduce((s, q) => s + q[0], 0) / pts.length, my = pts.reduce((s, q) => s + q[1], 0) / pts.length;
        const hx = centre.x, hy = centre.y, hr = +el.dataset.r * Math.hypot(world.a, world.b);
        let dBox = 0; if (el.getAttribute('d')) { const drawn = screenBox(el.getScreenCTM(), el.getBBox()); const lo = g.getClientByCanvas([c.cx + c.bbox.minX, c.cy + c.bbox.minY]), hi = g.getClientByCanvas([c.cx + c.bbox.maxX, c.cy + c.bbox.maxY]); dBox = Math.max(...[lo[0], lo[1], hi[0], hi[1]].map((v, i) => Math.abs(v - drawn[i]))); }
        return {id: c.id, dCenter: Math.hypot(hx - x, hy - y), dMean: Math.hypot(hx - mx, hy - my), rScreen: hr, rExpected: c.r * z, outside: Math.max(0, ...pts.map(q => Math.hypot(q[0] - hx, q[1] - hy) - hr)), dBox, hx, hy}; }); }"""

    def check(tag: str) -> list[dict[str, Any]]:
        rows = page.evaluate(ALIGN)
        for row in rows:
            assert row["dCenter"] <= 1.5 and abs(row["rScreen"] - row["rExpected"]) <= 2, f"{tag}: hull {row['id']} misaligned {row}"
            assert row["dBox"] <= 1.5, f"{tag}: drawn outline of {row['id']} is off its projected model bbox {row}"
            assert row["dMean"] <= row["rScreen"] + 0.5, f"{tag}: points' mean lies outside the hull {row}"
            assert row["outside"] <= 1.5, f"{tag}: a point of {row['id']} is drawn outside its hull {row}"
        return rows

    computed = page.evaluate("() => window.AtlasDebug.getCloudLayoutStats().outlines.computed")
    check("fit")
    box = page.locator("#atlas-field-network").bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    zoom_before = page.evaluate("() => window.AtlasDebug.getOverviewGraph().getZoom()")
    page.mouse.wheel(0, -240)  # one notch: the map stays larger than the viewport but far from the max zoom / pan clamp edges
    app.wait("z => window.AtlasDebug.getOverviewGraph().getZoom() > z * 1.05", 10, zoom_before)
    app.wait(CAMERA_IDLE, 10)
    start = check("wheel zoom")
    # native canvas drag (the Phase 0 clamp may shorten it): hull and label move exactly like the camera origin
    ORIGIN = "() => window.AtlasDebug.getOverviewGraph().getClientByCanvas([0, 0]).slice(0, 2)"
    origin_before = page.evaluate(ORIGIN); label_before = next(row for row in page.evaluate(LABEL_BOXES) if row["id"] == C05 and not row["hidden"])
    grab = {"x": box["x"] + box["width"] / 2, "y": box["y"] + box["height"] / 2}
    page.mouse.move(grab["x"], grab["y"]); page.mouse.down(); page.mouse.move(grab["x"] + 6, grab["y"] + 4, steps=2); page.mouse.move(grab["x"] + 60, grab["y"] + 35, steps=14); page.mouse.up()
    app.wait(CAMERA_IDLE, 10)
    panned = check("pan")
    origin_after = page.evaluate(ORIGIN); dx, dy = origin_after[0] - origin_before[0], origin_after[1] - origin_before[1]
    assert abs(dx) + abs(dy) > 8, f"the drag must really pan the camera ({dx}, {dy})"
    moved = next(r for r in panned if r["id"] == C05); origin = next(r for r in start if r["id"] == C05)
    assert abs(moved["hx"] - origin["hx"] - dx) <= 1.5 and abs(moved["hy"] - origin["hy"] - dy) <= 1.5, "the hull follows the dragged canvas"
    label_after = next(row for row in page.evaluate(LABEL_BOXES) if row["id"] == C05)
    assert abs(label_after["x"] - label_before["x"] - dx) <= 1.5 and abs(label_after["y"] - label_before["y"] - dy) <= 1.5, "labels are only translated with the camera"
    assert app.view()["selected"] is None, "a canvas drag selects nothing"
    page.locator("#atlas-network-zoom-out").click(); app.wait(CAMERA_IDLE, 10)
    check("animated button zoom")
    page.locator("#atlas-network-zoom-reset").click(); app.wait(CAMERA_IDLE, 10)
    check("fit")
    assert page.evaluate("() => window.AtlasDebug.getCloudLayoutStats().overlay.reevaluations") >= 1
    assert len(projection["clouds"]) >= 8
    # AE-8: container resize re-places labels only; no outline is recomputed across pan, zoom and resize
    placements = page.evaluate("() => window.AtlasDebug.getCloudLayoutStats().overlay.placements")
    page.set_viewport_size({"width": 1180, "height": 1000})
    app.wait("n => window.AtlasDebug.getCloudLayoutStats().overlay.placements > n", 10, placements); app.wait(CAMERA_IDLE, 10)
    assert page.evaluate("() => window.AtlasDebug.getCloudLayoutStats().outlines.computed") == computed, "pan/zoom/resize must not recompute outlines"


# ---------------------------------------------------------------------------------------------------------------------
# Cloud panel + 3-click workflow (AC-7..AC-10): map -> cloud (1) -> representative paper (2) -> Zotero/PDF action (3).
C06 = f"collection:{SERVER}:0:C06"
# Real pointer clicks (trusted, capture phase) and every <select> interaction, counted from the first screen on.
COUNT_INPUT = """() => { const c = window.__atlasInput = {clicks: 0, selects: 0}; const isSelect = e => e.target instanceof Element && Boolean(e.target.closest('select'));
  document.addEventListener('click', e => { if (e.isTrusted) c.clicks += 1; }, true); for (const type of ['pointerdown', 'change', 'input', 'keydown']) document.addEventListener(type, e => { if (isSelect(e)) c.selects += 1; }, true); }"""
BOX = "sel => { const el = document.querySelector(sel); if (!el || el.hidden) return null; const r = el.getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height, top: r.top, bottom: r.bottom, vh: innerHeight, vw: innerWidth}; }"
PANEL_OPEN = "id => { const p = document.getElementById('atlas-cloud-panel'); return !p.hidden && p.dataset.cloudId === id && window.AtlasDebug.getCloudPanel()?.cloudId === id; }"


def fully_visible(box: dict[str, float] | None) -> bool:
    return bool(box) and box["w"] > 0 and box["h"] > 0 and box["top"] >= -0.5 and box["bottom"] <= box["vh"] + 0.5 and box["x"] >= -0.5 and box["x"] + box["w"] <= box["vw"] + 0.5


def expected_cloud(fixture: dict[str, Any], cloud_id: str) -> dict[str, Any]:
    """Independent Python recomputation of the panel numbers from the synthetic fixture (no parentId rows in it)."""
    papers = {p["id"]: p for p in fixture["papers"]}
    clouds = {c["id"]: set(c["paperIds"]) & set(papers) for c in fixture["collections"] if set(c["paperIds"]) & set(papers)}
    unfiled = set(papers) - set().union(*clouds.values())
    if unfiled:
        clouds["cloud:unfiled"] = unfiled
    mine = clouds[cloud_id]
    clouds_of = lambda pid: {cid for cid, ids in clouds.items() if pid in ids}
    related: dict[str, dict[str, int]] = {}
    neighbours: dict[str, set[str]] = {pid: set() for pid in mine}
    for e in fixture["edges"]:
        s, t = e["source"], e["target"]
        if s not in papers or t not in papers or s == t:
            continue
        if e["type"] == "semantic" and s in mine and t in mine:
            neighbours[s].add(t); neighbours[t].add(s)
        if e["type"] not in ("semantic", "citation"):
            continue
        others = (clouds_of(t) if s in mine else set()) | (clouds_of(s) if t in mine else set())
        for other in others - {cloud_id}:
            related.setdefault(other, {"semantic": 0, "citation": 0})[e["type"]] += 1
    years: dict[str, int] = {}
    for pid in mine:
        year = papers[pid].get("year")
        key = str(year) if isinstance(year, int) and year > 0 else "none"
        years[key] = years.get(key, 0) + 1
    ranked = sorted(mine, key=lambda pid: (-len(neighbours[pid]), -(papers[pid]["year"] or -10**9), pid))
    return {"size": len(mine), "related": related, "years": years, "top": ranked[:5]}


@receipt("AC-7,AC-8,AC-9,AC-10;A-10,A-13")
def test_collection_map_three_click_workflow(cloud_app: Harness):
    app = cloud_app; page = app.page
    page.evaluate(COUNT_INPUT)  # counted from the first screen on: the map is the default (AC-11), no mode activation
    projection = open_collections(app)
    assert not page.evaluate("() => document.getElementById('atlas-advanced').open"), "the workflow runs on the default screen with Advanced closed"
    expected = expected_cloud(app.fixture, C06)
    # click 1: the cloud label, where it already is on screen (no scrolling by the test)
    label = next(row for row in page.evaluate(LABEL_BOXES) if row["id"] == C06 and not row["hidden"])
    assert 0 <= label["y"] and label["y"] + label["h"] <= page.viewport_size["height"], f"the cloud label is on screen after mode activation {label}"
    page.mouse.click(label["x"] + label["w"] / 2, label["y"] + label["h"] / 2)
    app.wait(PANEL_OPEN, 10, C06)
    assert app.view()["selected"] is None and selected_paper_points(app) == [], "a label click opens the panel and selects no paper"
    app.wait(CAMERA_IDLE, 10)  # the cloud transition (camera auto-move, panel slide-in) has finished: on-screen boxes are final
    panel = page.evaluate(BOX, "#atlas-cloud-panel"); heading = page.evaluate(BOX, "#atlas-cloud-panel-title")
    assert fully_visible(heading) and panel["top"] < panel["vh"], f"panel visible without scrolling (A-13): {panel}"
    map_box = page.evaluate(BOX, "#atlas-field-network")
    assert panel["x"] >= map_box["x"] + map_box["w"] - 1 and abs(panel["top"] - map_box["top"]) < map_box["h"], "at 1440 px the panel sits beside the map"
    model = page.evaluate("() => window.AtlasDebug.getCloudPanel()")
    # numbers match the fixture (full snapshot; the detail sample is never used)
    assert model["size"] == expected["size"] == 6 and page.locator("#atlas-cloud-panel").get_attribute("data-paper-count") == "6"
    assert page.locator("#atlas-cloud-panel-title").inner_text() == "Synthetic reading list"
    sub_counts = page.locator(".atlas-cloud-subitem").evaluate_all("els => els.map(e => Number(e.dataset.count))")
    assert sum(sub_counts) == expected["size"] and len(sub_counts) == len(model["subItems"]["items"]) >= 1
    assert all(item["auto"] for item in model["subItems"]["items"]) and any(item["label"].endswith("· 자동") for item in model["subItems"]["items"])
    years = dict(page.locator(".atlas-cloud-year").evaluate_all("els => els.map(e => [e.dataset.year, Number(e.dataset.count)])"))
    assert years == expected["years"]
    related = page.locator(".atlas-cloud-related").evaluate_all("els => els.map(e => [e.dataset.cloudId, Number(e.dataset.semantic), Number(e.dataset.citation), e.textContent])")
    assert len(related) == min(5, len(expected["related"])) and related
    for cloud_id, semantic, citation, text in related:
        assert {"semantic": semantic, "citation": citation} == expected["related"][cloud_id], cloud_id
        assert f"semantic {semantic} · citation {citation}" in text, "semantic and citation are shown separately, 0 explicitly"
    assert any(row[2] == 0 for row in related) and any(row[2] > 0 for row in related), "the fixture exercises both a citation-0 row and a citation row"
    concepts = page.locator(".atlas-cloud-concept").evaluate_all("els => els.map(e => e.textContent)")
    assert 1 <= len(concepts) <= 8 and not any(text[:1].isdigit() or len(text) <= 2 for text in concepts)
    papers = page.locator(".atlas-cloud-paper").evaluate_all("els => els.map(e => e.dataset.paperId)")
    assert papers == expected["top"], "representative papers: semantic degree desc -> year desc -> id"
    hostile = next(p for p in app.fixture["papers"] if p["id"] == papers[0])
    assert page.locator(f'.atlas-cloud-paper[data-paper-id="{papers[0]}"] span').first.inner_text() == hostile["title"], "titles are text: a hostile '<img ...>' title is shown verbatim, never parsed"
    # click 2: the first representative paper, as rendered on screen
    row = page.evaluate(BOX, f'.atlas-cloud-paper[data-paper-id="{papers[0]}"]')
    assert fully_visible(row), f"representative paper reachable without scrolling {row}"
    page.mouse.click(row["x"] + row["w"] / 2, row["y"] + row["h"] / 2)
    app.wait("id => window.AtlasDebug.getViewState()?.selected?.id === id", 10, papers[0])
    app.wait(PANEL_OPEN, 10, C06)
    replicas = sorted(p["id"] for p in projection["points"] if p["paperId"] == papers[0])
    assert len(replicas) == 2, "D01 is filed in C01 and C06"
    app.wait("ids => JSON.stringify([...window.AtlasDebug.getG6Stats().overview.selectedIds].sort()) === JSON.stringify(ids)", 10, replicas)
    assert app.view()["scope"]["kind"] == "cloud" and app.view()["scope"]["cloudId"] == C06, "a paper click inside the cloud keeps the panel"
    detail = page.locator(".atlas-cloud-panel-detail")
    assert detail.get_attribute("data-paper-id") == papers[0] and detail.locator("h3").inner_text() == hostile["title"]
    assert page.evaluate("() => document.activeElement?.closest('.atlas-cloud-panel-detail') !== null"), "focus moves to the paper detail"
    # click 3 = the existing Zotero/PDF action: present, enabled and on screen (not launched: it would open an external app)
    action = detail.locator(".atlas-actions button", has_text="Zotero에서 열기")
    assert action.count() == 1 and action.is_enabled() and detail.locator(".atlas-actions button", has_text="PDF 열기").count() == len(hostile["attachmentIds"])
    assert fully_visible(page.evaluate(BOX, ".atlas-cloud-panel-detail .atlas-actions button")), "the action is on screen without scrolling"
    counts = page.evaluate("() => window.__atlasInput")
    assert counts["clicks"] == 2 and counts["clicks"] + 1 <= 3, f"<= 3 clicks from the map to the action: {counts}"
    assert counts["selects"] == 0, "zero <select> interactions in total: the map is the default screen"
    assert page.locator("#atlas-fit-new-content").is_hidden(), "A-13: selecting a paper in the panel offers no 'Fit new content'"
    assert not page.evaluate("() => document.getElementById('atlas-advanced').open")
    # sub-item click dims every point outside it (filter by dimming; nothing removed)
    app.wait(CAMERA_IDLE, 10)  # the paper transition's panel cross-fade ghost (inert, id-less copy of the old panel) is gone
    item = model["subItems"]["items"][0]
    page.locator(f'.atlas-cloud-subitem[data-subitem-id="{item["id"]}"]').click()
    app.wait("id => window.AtlasDebug.getViewState()?.scope?.subItemId === id", 10, item["id"])
    points = page.evaluate("() => window.AtlasDebug.getAllPaperOverview().points")
    assert {p["id"] for p in points if not p["dimmed"]} == {f"overview-point:{C06}:{pid}" for pid in item["paperIds"]} and len(points) == projection["counters"]["points"]
    assert page.locator(f'.atlas-cloud-subitem[data-subitem-id="{item["id"]}"]').get_attribute("aria-pressed") == "true"
    assert app.view()["selected"]["id"] == papers[0], "dimming keeps the selection"
    # close returns to the unselected map; focus goes back to the cloud label
    page.locator(".atlas-cloud-panel-close").click()
    app.wait("() => document.getElementById('atlas-cloud-panel').hidden && document.querySelector('#atlas-cloud-overlay').dataset.selectedCloud === ''", 10)
    assert app.view()["selected"] is None and app.view()["scope"]["kind"] == "library" and selected_paper_points(app) == []
    assert not any(p["dimmed"] for p in page.evaluate("() => window.AtlasDebug.getAllPaperOverview().points"))
    assert page.evaluate("id => document.activeElement?.dataset?.cloudId === id", C06)
    assert not app.errors, app.errors[:3]


@receipt("AC-7,A-13")
@pytest.mark.parametrize("viewport", [(720, 1000), (320, 900)])
def test_collection_panel_narrow_width_sits_under_the_map_and_scrolls_into_view(viewport: tuple[int, int]):
    with running_app(collections_fixture(), viewport=viewport) as app:
        page = app.page
        open_collections(app)
        app.reveal("overview")  # the user looks at the map
        label = next((row for row in page.evaluate(LABEL_BOXES) if row["id"] == C06 and not row["hidden"]), None)
        if label and 0 <= label["y"] and label["y"] + label["h"] <= viewport[1]:
            page.mouse.click(label["x"] + label["w"] / 2, label["y"] + label["h"] / 2)
        else:  # hidden at 320 px: the 'cloud list' chip is the same cloud click
            page.locator(f'#atlas-cloud-chips button[data-cloud-id="{C06}"]').click()
        app.wait(PANEL_OPEN, 10, C06)
        app.wait("() => { const r = document.getElementById('atlas-cloud-panel-title').getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight; }", 10)
        panel = page.evaluate(BOX, "#atlas-cloud-panel"); wrap = page.evaluate(BOX, ".atlas-circular-wrap")
        assert panel["top"] >= wrap["bottom"] - 1, f"below 900 px the panel sits under the map {panel} {wrap}"
        assert fully_visible(page.evaluate(BOX, "#atlas-cloud-panel-title")) and panel["h"] <= viewport[1] * 0.7 + 1
        assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1"), "no horizontal overflow"
        first = page.locator(".atlas-cloud-paper").first
        first.click()
        app.wait("() => document.querySelector('.atlas-cloud-panel-detail .atlas-actions button') !== null", 10)
        assert page.locator(".atlas-cloud-panel-detail .atlas-actions button", has_text="Zotero에서 열기").is_enabled()
        assert not app.errors, app.errors[:3]


# ---------------------------------------------------------------------------------------------------------------------
# AC-11 default screen: the collection map and its 3-step flow, nothing else competing; every legacy view one click away under Advanced.
# Every visible, focusable control on the page.  Map content (cloud labels/chips) is reported by kind because its count follows the data.
VISIBLE_CONTROLS = """() => [...document.querySelectorAll('button, input, select, textarea, summary, a[href], [tabindex]')].filter(el => {
    if (el.tabIndex < 0) return false; const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && el.checkVisibility({contentVisibilityAuto: true, opacityProperty: false, visibilityProperty: true}); })
  .map(el => el.classList.contains('atlas-cloud-label') ? 'cloud-label' : el.classList.contains('atlas-cloud-chip') ? 'cloud-chip' : el.tagName === 'CANVAS' && el.closest('#atlas-field-network') ? 'map-canvas' : el.tagName === 'SUMMARY' ? `summary:${el.parentElement.id}` : el.id ? `#${el.id}` : `${el.tagName.toLowerCase()}.${el.className}`)"""
FIRST_SCREEN = ["#atlas-search", "#atlas-network-zoom-out", "#atlas-network-zoom-reset", "#atlas-network-zoom-in", "#atlas-field-network", "summary:atlas-advanced"]
MAP_CONTENT = ("cloud-label", "cloud-chip", "map-canvas")  # G6's own layered canvases + overlay labels/chips: part of the map, counts follow the data


@receipt("AC-11;A-7,A-8")
@pytest.mark.parametrize("width", [1440, 720, 320])
def test_default_screen_is_the_collection_map_with_view_selects_hidden_and_a_bounded_control_set(width: int):
    with running_app(collections_fixture(), viewport=(width, 900)) as app:
        page = app.page
        projection = open_collections(app)
        assert not page.locator("#atlas-overview-renderer").is_visible(), "Overview select is not on the first screen"
        assert not page.locator("#atlas-renderer").is_visible(), "Network renderer select is not on the first screen"
        assert page.locator("#atlas-field-network").is_visible() and page.locator("#atlas-cloud-overlay .atlas-cloud-label").count() == len(projection["clouds"])
        assert page.evaluate("() => document.getElementById('atlas-advanced').open") is False
        assert page.locator("#atlas-advanced > summary").inner_text().startswith("Advanced")
        # exact set of visible interactive controls: Search, Fit/zoom, the map (+ its cloud labels/chips) and the Advanced summary
        controls = page.evaluate(VISIBLE_CONTROLS)
        assert sorted(c for c in controls if c not in MAP_CONTENT) == sorted(FIRST_SCREEN), controls
        assert "map-canvas" in controls
        labels = page.evaluate(LABEL_BOXES)
        assert controls.count("cloud-label") == sum(1 for row in labels if not row["hidden"]) and controls.count("cloud-chip") == sum(1 for row in labels if row["hidden"])
        assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1"), f"no horizontal overflow at {width}"
        # A-8: a short human status after connect; developer diagnostics exist but only inside Advanced
        status = page.locator("#atlas-app-status").inner_text()
        assert "Connect to the local" not in status and status.startswith(f"{len(app.fixture['papers'])} papers · {len(app.fixture['collections'])} collections · "), status
        for selector, text in (("#atlas-model-policy", "revision"), ("#atlas-model-policy", "keyword policy"), ("#atlas-overview-count", "allocation"), ("#atlas-overview-count", "typed relationships")):
            assert not page.locator(selector).is_visible() and text in page.locator(selector).text_content(), selector
        # hidden lower networks cost nothing: no Cytoscape/G6 instance exists while Advanced is closed
        assert page.locator("#atlas-advanced").get_attribute("data-lower-networks") == "deferred"
        assert page.evaluate("() => { const c = window.AtlasDebug.getActualCounts(); return c.paperNodes + c.conceptNodes; }") == 0
        assert page.evaluate("() => window.AtlasDebug.getG6Stats().paper") is None
        shot(app, f"default-screen-{width}")
        assert not app.errors, app.errors[:3]


@receipt("AC-11")
def test_advanced_opens_lower_networks_at_real_size_and_every_legacy_overview_mode_renders(cloud_app: Harness):
    app = cloud_app; page = app.page
    open_collections(app)
    open_advanced(page)
    assert page.locator("#atlas-overview-renderer").is_visible() and page.locator("#atlas-renderer").is_visible()
    lower = page.evaluate("() => ['atlas-paper-network', 'atlas-concept-network'].map(id => { const c = document.getElementById(id); const g = id === 'atlas-paper-network' ? window.AtlasDebug.getPaperGraph() : window.AtlasDebug.getConceptGraph(); const canvas = c.querySelector('canvas'); return {w: c.clientWidth, h: c.clientHeight, nodes: g?.getNodeData().length || 0, canvasW: canvas ? Math.round(canvas.getBoundingClientRect().width) : 0}; })")
    for row in lower:
        assert row["w"] > 0 and row["h"] > 0 and row["nodes"] > 0 and abs(row["canvasW"] - row["w"]) <= 2, f"lower network created at its real size when Advanced opens: {row}"
    assert page.locator("#atlas-field-legend").is_hidden() and page.locator("#atlas-paper-list button").first.is_visible(), "collections mode hides the category legend (points use cloud colours)"
    # the four legacy overview modes render exactly as before once selected
    page.locator("#atlas-overview-renderer").select_option("ribbon")
    page.locator("path.atlas-sector").first.wait_for(state="visible", timeout=10000)
    assert page.locator("#atlas-field-legend button.atlas-legend-field").first.is_visible(), "the legend returns in a legacy mode"
    assert page.locator("path.atlas-sector").count() == len(app.fixture["fields"]) and page.locator("#atlas-field-network").is_hidden()
    assert page.locator("#atlas-map-title").inner_text() == "Field overview" and page.locator("#atlas-cloud-overlay").count() == 0
    pick_overview_mode(app, "g6")
    assert {n["id"] for n in app.overview_nodes()} >= {f["id"] for f in app.fixture["fields"] if not f["parentIds"]}
    pick_overview_mode(app, "all-papers", len(app.fixture["papers"]))
    assert page.evaluate("() => window.AtlasDebug.getAllPaperOverview().grouping") != "collection", "one point per paper, not the cloud map"
    page.locator("#atlas-overview-renderer").select_option("category")
    app.wait("() => !document.querySelector('#atlas-category-explorer')?.hidden && document.querySelectorAll('#atlas-category-explorer .atlas-category-card').length > 0", 10)
    assert page.locator(".atlas-circular-wrap").is_hidden()
    page.locator("#atlas-overview-renderer").select_option("collections")
    app.wait("() => (window.AtlasDebug.getCloudLayoutStats()?.overlay?.placements || 0) > 0", 15)
    assert page.locator("#atlas-map-title").inner_text() == "Collection map"
    assert not app.errors, app.errors[:3]


@receipt("AC-11;AC-14")
def test_closed_advanced_freezes_hidden_g6_lower_networks_and_reopening_resizes_and_catches_up(cloud_app: Harness):
    app = cloud_app; page = app.page
    open_collections(app)
    open_advanced(page)
    page.locator("#atlas-renderer").select_option("g6")
    app.wait("() => ['#atlas-paper-network', '#atlas-concept-network'].every(s => document.querySelector(s)?.dataset.rendererState === 'ready')", 20)
    app.stable("paper"); app.stable("concept")
    page.locator("#atlas-advanced > summary").click()
    app.wait("() => document.getElementById('atlas-advanced').dataset.lowerNetworks === 'deferred'", 10)
    before = page.evaluate("() => { const s = window.AtlasDebug.getG6Stats(); return [s.paper, s.concept].map(v => [v.created, v.topologyChanges, v.styleUpdates, v.failedUpdates]); }")
    # the 3-step flow on the map while the lower graphs are closed (zero-size containers)
    label = next(row for row in page.evaluate(LABEL_BOXES) if row["id"] == C06 and not row["hidden"])
    page.mouse.click(label["x"] + label["w"] / 2, label["y"] + label["h"] / 2)
    app.wait(PANEL_OPEN, 10, C06)
    paper_id = page.locator(".atlas-cloud-paper").first.get_attribute("data-paper-id")
    page.locator(".atlas-cloud-paper").first.click()
    app.wait("id => window.AtlasDebug.getViewState()?.selected?.id === id", 10, paper_id)
    after = page.evaluate("() => { const s = window.AtlasDebug.getG6Stats(); return [s.paper, s.concept].map(v => [v.created, v.topologyChanges, v.styleUpdates, v.failedUpdates]); }")
    assert after == before, "hidden lower graphs are neither updated nor relaid out while Advanced is closed"
    assert page.locator("#atlas-fit-new-content").is_hidden()
    # reopening: the lower graphs catch up to the current revision at the container's real size; the camera stays clamped
    open_advanced(page)
    app.wait("() => window.AtlasDebug.getLowerProjection().revision === window.AtlasDebug.getViewState().revision", 10)
    app.wait("id => window.AtlasDebug.getG6Stats().paper.selectedIds.includes(id)", 10, paper_id)
    sizes = page.evaluate("() => ['#atlas-paper-network', '#atlas-concept-network'].map(s => { const c = document.querySelector(s); const canvas = c.querySelector('canvas'); return [c.clientWidth, Math.round(canvas.getBoundingClientRect().width), c.dataset.rendererState]; })")
    assert all(w > 0 and abs(w - cw) <= 2 and state == "ready" for w, cw, state in sizes), sizes
    app.wait("() => ['#atlas-paper-network', '#atlas-concept-network'].every(s => document.querySelector(s).dataset.cameraState === 'idle')", 10)
    for which in ("paper", "concept"):
        app.stable(which)
        box = page.locator(app.SELECTORS[which]).bounding_box()
        points = page.evaluate("w => { const g = w === 'paper' ? window.AtlasDebug.getPaperGraph() : window.AtlasDebug.getConceptGraph(); return g.getNodeData().map(n => g.getClientByCanvas(g.getElementPosition(n.id)).slice(0, 2)); }", which)
        assert any(box["x"] <= x <= box["x"] + box["width"] and box["y"] <= y <= box["y"] + box["height"] for x, y in points), f"{which} nodes stay on screen after reopening"
    assert not app.errors, app.errors[:3]


@receipt("A-13")
def test_cloud_panel_paper_selection_never_offers_fit_new_content_on_the_default_screen():
    with running_app(large_fixture()) as app:
        page = app.page
        open_collections(app)
        label = next(row for row in page.evaluate(LABEL_BOXES) if not row["hidden"])
        page.mouse.click(label["x"] + label["w"] / 2, label["y"] + label["h"] / 2)
        app.wait("() => !document.getElementById('atlas-cloud-panel').hidden", 10)
        for index in range(3):  # 35 % detail sample: these papers are mostly outside it, which used to change the hidden lower networks' topology
            row = page.locator(".atlas-cloud-paper").nth(index)
            paper_id = row.get_attribute("data-paper-id")
            row.click()
            app.wait("id => window.AtlasDebug.getViewState()?.selected?.id === id", 10, paper_id)
            app.wait(CAMERA_IDLE, 10)
            assert page.locator("#atlas-fit-new-content").is_hidden(), f"'Fit new content' appeared after paper {index}"
            assert page.evaluate("() => window.AtlasDebug.getCameraPlans().fitNewContent") is False
        assert page.evaluate("() => window.AtlasDebug.getG6Stats().overview.topologyChanges") == 0, "the map itself never changed topology"
        assert not app.errors, app.errors[:3]


@receipt("A-11")
def test_category_table_hides_empty_categories_without_changing_totals():
    fixture = acceptance_fixture()
    empty = f"field:{SERVER}:{EPOCH}:synthetic-empty"
    fixture["fields"].append({"id": empty, "label": {"en": "Synthetic empty category"}, "parentIds": [], "aliases": [], "source": "user"})
    recompute_overview(fixture); add_backend_style_extensions(fixture)
    row = next(r for r in fixture["fieldStats"]["rows"] if r["fieldId"] == empty)
    assert row["direct"] == row["inclusive"] == 0
    stats = fixture["fieldStats"]
    with running_app(fixture) as app:
        page = app.page
        open_advanced(page)
        hidden = page.locator(f'#atlas-field-legend button[data-field-id="{empty}"]')
        assert hidden.count() == 1 and hidden.is_hidden(), "a 0 direct / 0 incl. row is hidden"
        visible = page.locator("#atlas-field-legend button.atlas-legend-field").evaluate_all("els => els.filter(e => !e.hidden).map(e => e.dataset.fieldId)")
        assert sorted(visible) == sorted(f["id"] for f in fixture["fields"] if f["id"] != empty)
        caption = page.locator("#atlas-field-legend .atlas-field-legend-caption").inner_text()
        assert caption.startswith(f"Known primary {stats['knownPrimary']} + unknown {stats['unknown']} = {stats['paperCount']} papers"), caption
        assert page.locator(f'#atlas-field-list button[data-entity-id="{empty}"]').count() == 1, "the empty field stays reachable in the Fields tree"
        assert not app.errors, app.errors[:3]


# ---------------------------------------------------------------------------------------------------------------------
# Cloud aesthetics (plan cloud-aesthetics AE-1..AE-11): colours, outlines, replica rings, motion, <defs> id isolation.
CLOUD_COLORS = """() => [...document.querySelectorAll('.atlas-cloud-hull')].map(el => ({id: el.dataset.cloudId, x: +el.dataset.cx, y: +el.dataset.cy, r: +el.dataset.r, unfiled: el.classList.contains('is-unfiled'), stroke: getComputedStyle(el).stroke, fill: getComputedStyle(el).fill}))"""


def _oklab_l(rgb: tuple[int, int, int]) -> float:
    lin = [(c / 255) / 12.92 if c / 255 <= 0.04045 else (((c / 255) + 0.055) / 1.055) ** 2.4 for c in rgb]
    l, m, s = (0.4122214708 * lin[0] + 0.5363325363 * lin[1] + 0.0514459929 * lin[2]), (0.2119034982 * lin[0] + 0.6806995451 * lin[1] + 0.1073969566 * lin[2]), (0.0883024619 * lin[0] + 0.2817188376 * lin[1] + 0.6299787005 * lin[2])
    return 0.2104542553 * l ** (1 / 3) + 0.793617785 * m ** (1 / 3) - 0.0040720468 * s ** (1 / 3)


@receipt("AE-1,AE-2,AE-3")
def test_cloud_strokes_differ_between_neighbours_and_share_one_lightness(cloud_app: Harness):
    app = cloud_app; page = app.page
    open_collections(app)
    rows = page.evaluate(CLOUD_COLORS); core = [r for r in rows if not r["unfiled"]]
    assert len(core) >= 8
    gap = 2 * 40  # adjacency: circle gap <= 2 x CLOUD_GAP (plan 2.2), on the rendered model circles
    pairs = [(a, b) for i, a in enumerate(core) for b in core[i + 1:] if ((a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2) ** 0.5 - a["r"] - b["r"] <= gap]
    assert pairs, "the fixture must have neighbouring clouds"
    for a, b in pairs:
        assert a["stroke"] != b["stroke"], f"neighbours {a['id']} / {b['id']} share a stroke colour {a['stroke']}"
    assert len({r["stroke"] for r in core}) == len(core), "every core cloud has its own hue (<= 24 clouds)"
    lightness = [_oklab_l(_rgb(r["stroke"])) for r in core]
    assert max(lightness) - min(lightness) <= 0.01, f"core stroke tones must share one OKLCH lightness (8-bit quantisation only): {lightness}"
    fills = [_oklab_l(_rgb(r["fill"])) for r in core]
    assert max(fills) - min(fills) <= 0.01, f"core fills share one lightness: {fills}"
    unfiled = [r for r in rows if r["unfiled"]]
    assert unfiled and "url(" in unfiled[0]["fill"] and len(set(_rgb(unfiled[0]["stroke"]))) == 1, "unfiled: achromatic dashed stroke + hatch pattern fill"
    stats = page.evaluate("() => window.AtlasDebug.getCloudLayoutStats()")
    assert stats["neighbours"]["adjacentPairs"] >= 1 and stats["neighbours"]["minStrokeDeltaE"] > 0, "neighbour separation is measured and reported (D-A6 stability default)"


@receipt("AE-7")
def test_cloud_colours_are_identical_at_320_720_1440():
    seen = []
    for width in (320, 720, 1440):
        with running_app(collections_fixture(), viewport=(width, 900)) as app:
            open_collections(app)
            colors = app.page.evaluate("() => Object.fromEntries(window.AtlasDebug.getCloudLayoutStats().colors.map(c => [c.id, [c.slot, c.stroke, c.fill, c.point]]))")
            strokes = {r["id"]: r["stroke"] for r in app.page.evaluate(CLOUD_COLORS)}
            points = app.page.evaluate("() => Object.fromEntries(window.AtlasDebug.getOverviewGraph().getNodeData().map(n => [n.id, n.data.categoryColor]))")
            seen.append((colors, strokes, points))
            assert not app.errors, app.errors[:3]
    assert seen[0] == seen[1] == seen[2], "a collection keeps its colours at every container size"


@receipt("AE-2;D-A1")
def test_replica_points_carry_a_ring_and_selection_stroke_wins(cloud_app: Harness):
    app = cloud_app; page = app.page
    open_collections(app)
    STYLE = "ids => { const g = window.AtlasDebug.getOverviewGraph(); return ids.map(id => { const n = g.getNodeData().find(row => row.id === id); const s = g.getElementRenderStyle(id); return {ring: Boolean(n.data.ring), outline: n.data.outline || null, color: n.data.categoryColor, stroke: s.stroke, lineWidth: s.lineWidth, fill: s.fill}; }); }"
    nodes = app.overview_nodes()
    replicas = sorted(n["id"] for n in nodes if n["data"]["paperId"] == MULTI)
    single = next(n["id"] for n in nodes if sum(1 for m in nodes if m["data"]["paperId"] == n["data"]["paperId"]) == 1)
    rows = page.evaluate(STYLE, replicas + [single])
    assert len(replicas) == 2 and all(r["ring"] and r["outline"] and r["stroke"] == r["outline"] and r["lineWidth"] == 2 for r in rows[:2]), rows
    assert not rows[2]["ring"] and rows[2]["stroke"] != rows[0]["outline"] and rows[2]["lineWidth"] == 1, rows
    assert rows[0]["fill"] == rows[0]["color"] and rows[0]["color"] != rows[1]["color"], "each replica takes its own cloud's point tone"
    tones = {c["id"]: set(c["pointVariants"]) for c in page.evaluate("() => window.AtlasDebug.getCloudLayoutStats().colors")}
    assert all(n["data"]["categoryColor"] in tones[n["data"]["cloudId"]] for n in nodes), "D-A1: every point takes its cloud's point tone (or a sub-group variant of it)"
    app.click_node("overview", replicas[0])
    app.wait("ids => JSON.stringify([...window.AtlasDebug.getG6Stats().overview.selectedIds].sort()) === JSON.stringify(ids)", 10, replicas)
    focus = page.evaluate("() => getComputedStyle(document.getElementById('atlas-app')).getPropertyValue('--atlas-focus-hex').trim()")
    selected = page.evaluate(STYLE, replicas)
    assert all(r["stroke"].lower() == focus.lower() and r["lineWidth"] == 5 for r in selected), f"selection stroke wins over the ring: {selected}"


@receipt("AE-11")
def test_cloud_outline_transitions_are_150ms_and_zero_under_reduced_motion(cloud_app: Harness):
    app = cloud_app; page = app.page
    open_collections(app)
    DURATION = "() => getComputedStyle(document.querySelector('.atlas-cloud-hull')).transitionDuration.split(',').map(v => v.trim())"
    page.emulate_media(reduced_motion="no-preference")
    assert set(page.evaluate(DURATION)) == {"0.15s"}
    page.emulate_media(reduced_motion="reduce")
    assert set(page.evaluate(DURATION)) == {"0s"}


@receipt("AE-9;R-svg-ids")
def test_two_overlay_instances_on_one_page_have_disjoint_defs_ids(cloud_app: Harness):
    app = cloud_app; page = app.page
    open_collections(app)
    result = page.evaluate("""() => { const host = document.createElement('div'); host.style.position = 'relative'; const box = document.createElement('div'); host.append(box); document.body.append(host);
      const graph = {getZoom: () => 1, getViewportByCanvas: () => [0, 0], getCanvasByClient: p => p};
      const second = window.AtlasCloudOverlay.createCloudOverlay({graph, container: box}); second.update({signature: 's', clouds: [{id: 'cloud:unfiled', label: 'u', cx: 0, cy: 0, r: 20, size: 2, unfiled: true, kind: 'soft-hull', method: 'soft-hull', d: 'M-5,0L5,0L0,5Z', colors: {fill: '#eeeeee', stroke: '#777777', selectedFill: '#eeeeee'}}], points: [], nodeIdsByPaper: new Map()});
      const ids = svg => [...svg.querySelectorAll('[id]')].map(e => e.id); const svgs = [...document.querySelectorAll('svg.atlas-cloud-hulls')];
      const hatch = svg => getComputedStyle(svg.querySelector('.atlas-cloud-hull.is-unfiled')).fill;
      const out = {count: svgs.length, ids: svgs.map(ids), prefixes: svgs.map(s => s.dataset.idPrefix), fills: svgs.map(hatch)}; second.destroy(); host.remove(); return out; }""")
    assert result["count"] == 2 and all(result["ids"]) and not set(result["ids"][0]) & set(result["ids"][1]), result
    assert result["prefixes"][0] != result["prefixes"][1] and all(f"#{prefix}-hatch" in fill for prefix, fill in zip(result["prefixes"], result["fills"])), result
    assert page.locator("svg.atlas-cloud-hulls").count() == 1, "destroy removes the second overlay"


SUBGROUPS = """() => [...document.querySelectorAll('.atlas-cloud-subgroup')].map(el => { const hull = el.closest('.atlas-cloud').querySelector('.atlas-cloud-hull'); const r = +hull.dataset.r; const b = el.getBBox(); const s = getComputedStyle(el);
  return {cloud: hull.dataset.cloudId, id: el.dataset.subgroupId, inCircle: b.x >= -r - 0.5 && b.y >= -r - 0.5 && b.x + b.width <= r + 0.5 && b.y + b.height <= r + 0.5, opacity: s.opacity, duration: s.transitionDuration}; })"""
DETAIL = "() => { const s = window.AtlasDebug.getCloudLayoutStats(); return {visible: s.overlay.subgroupsVisible, draws: s.overlay.subgroupDraws, outlines: s.overlay.subgroupOutlines, computed: s.outlines.computed, subComputed: s.outlines.subgroupsComputed, zoom: window.AtlasDebug.getOverviewGraph().getZoom()}; }"


@receipt("AE-8,AE-11;plan-3.2-zoom-detail")
def test_zoom_in_shows_lazily_computed_subgroup_outlines_and_zoom_out_hides_them(cloud_app: Harness):
    app = cloud_app; page = app.page
    open_collections(app); app.reveal("overview")
    start = page.evaluate(DETAIL)
    assert start["visible"] is False and start["draws"] == 0 and start["subComputed"] == 0 and start["zoom"] < 1.6, f"sub-group outlines are never computed for the initial layout: {start}"
    assert page.locator(".atlas-cloud-subgroup").count() == 0
    while page.evaluate(DETAIL)["zoom"] < 1.6:
        page.locator("#atlas-network-zoom-in").click(); app.wait(CAMERA_IDLE, 10)
    app.wait("() => window.AtlasDebug.getCloudLayoutStats().overlay.subgroupsVisible === true", 10)
    shown = page.evaluate(DETAIL)
    assert shown["draws"] == 1 and shown["outlines"] > 0 and shown["subComputed"] >= 1 and shown["computed"] == start["computed"], shown
    app.wait("() => [...document.querySelectorAll('.atlas-cloud-subgroup')].every(el => getComputedStyle(el).opacity === '1')", 5)
    rows = page.evaluate(SUBGROUPS)
    assert len(rows) == shown["outlines"] and all(r["inCircle"] for r in rows) and not any(r["id"] == "subgroup:other" for r in rows), rows
    assert all(r["duration"] == "0.15s" for r in rows)
    # zoom out below the threshold: hidden again (opacity 0), nothing recomputed; zoom back in: the memoised outlines are reused
    page.locator("#atlas-network-zoom-reset").click(); app.wait(CAMERA_IDLE, 10)
    app.wait("() => window.AtlasDebug.getCloudLayoutStats().overlay.subgroupsVisible === false", 10)
    app.wait("() => [...document.querySelectorAll('.atlas-cloud-subgroup')].every(el => getComputedStyle(el).opacity === '0')", 5)
    while page.evaluate(DETAIL)["zoom"] < 1.6:
        page.locator("#atlas-network-zoom-in").click(); app.wait(CAMERA_IDLE, 10)
    app.wait("() => window.AtlasDebug.getCloudLayoutStats().overlay.subgroupsVisible === true", 10)
    again = page.evaluate(DETAIL)
    assert again["draws"] == 1 and again["subComputed"] == shown["subComputed"] and again["computed"] == start["computed"], again
    page.emulate_media(reduced_motion="reduce")
    assert {r["duration"] for r in page.evaluate(SUBGROUPS)} == {"0s"}


@receipt("AC-3;plan-3.4-label-weight")
@pytest.mark.parametrize("width,which", [(1440, "collections"), (320, "large")])
def test_label_weight_tiers_never_change_label_placement(width: int, which: str):
    with running_app(collections_fixture() if which == "collections" else large_fixture(), viewport=(width, 900)) as app:
        page = app.page
        projection = open_collections(app)
        tiers = page.evaluate("() => [...document.querySelectorAll('#atlas-cloud-overlay .atlas-cloud-label')].map(b => [b.dataset.cloudId, b.dataset.weightTier, getComputedStyle(b).webkitTextStrokeWidth, getComputedStyle(b).fontSize, b.getBoundingClientRect().height, getComputedStyle(b).fontWeight])")
        sizes = {c["id"]: c["size"] for c in projection["clouds"]}
        assert {t[1] for t in tiers} == {"1", "2", "3"} and {t[1]: t[2] for t in tiers} == {"1": "0.45px", "2": "0.2px", "3": "0px"} and len({t[5] for t in tiers}) == 1, "three glyph-weight tiers; the font itself is unchanged" 
        assert {t[3] for t in tiers} == {"12px"} and all(abs(t[4] - 16) < 0.01 for t in tiers if t[4]), "font size and the 16 px label height are unchanged"
        ordered = [t[1] for t in sorted(tiers, key=lambda t: (-sizes[t[0]], t[0]))]
        assert ordered == sorted(ordered), "tiers follow the size rank"
        TRUNCATED = "() => [...document.querySelectorAll('#atlas-cloud-overlay .atlas-cloud-label')].filter(b => !b.hidden && b.scrollWidth > b.clientWidth).map(b => b.dataset.cloudId).sort()"
        truncated = page.evaluate(TRUNCATED)
        with_tiers = sorted(page.evaluate(LABEL_BOXES), key=lambda r: r["id"]); chips = page.locator("#atlas-cloud-chips button").count()
        placements = page.evaluate("() => window.AtlasDebug.getCloudLayoutStats().overlay.placements")
        page.evaluate("() => document.querySelectorAll('#atlas-cloud-overlay .atlas-cloud-label').forEach(b => { b.style.webkitTextStroke = '0'; })")
        page.locator("#atlas-network-zoom-reset").click(); app.wait(CAMERA_IDLE, 10)
        app.wait("n => window.AtlasDebug.getCloudLayoutStats().overlay.placements > n", 10, placements)
        without = sorted(page.evaluate(LABEL_BOXES), key=lambda r: r["id"])
        assert [(r["id"], r["hidden"]) for r in without] == [(r["id"], r["hidden"]) for r in with_tiers] and page.locator("#atlas-cloud-chips button").count() == chips
        for a, b in zip(with_tiers, without):
            assert all(abs(a[k] - b[k]) <= 0.01 for k in ("x", "y", "w", "h")), f"label placement changed with the weight tier: {a} / {b}"
        assert page.evaluate(TRUNCATED) == truncated, "the weight tier truncates no additional label"
        assert not app.errors, app.errors[:3]



# ---------------------------------------------------------------------------------------------------------------------
# Collection-map edge layers (plan motion-and-edges M2a, ME-3..ME-7): every drawn line/bridge comes from edgesForState.
C03 = f"collection:{SERVER}:0:C03"
DRAWN = """() => ({edges: [...document.querySelectorAll('.atlas-cloud-edges .atlas-cloud-edge')].map(el => { const cs = getComputedStyle(el); return {id: el.dataset.edgeId, tier: el.dataset.tier, from: el.dataset.from, to: el.dataset.to, intra: el.dataset.intra === 'true', dimmed: el.classList.contains('is-dimmed'),
    width: parseFloat(cs.strokeWidth), opacity: parseFloat(cs.strokeOpacity), dash: cs.strokeDasharray, stroke: cs.stroke, vectorEffect: cs.vectorEffect, d: el.getAttribute('d')}; }),
  bridges: [...document.querySelectorAll('.atlas-cloud-bridges .atlas-cloud-bridge')].map(el => { const cs = getComputedStyle(el); return {id: el.dataset.bridgeId, level: +el.dataset.level, emphasis: el.classList.contains('is-emphasis'), dimmed: el.classList.contains('is-dimmed'), width: parseFloat(cs.strokeWidth), stroke: cs.stroke, vectorEffect: cs.vectorEffect}; })})"""
# Independent recomputation in the page: the engine itself (window.AtlasEdgeStrength) on the live snapshot/layout and the projection's mapped state.
ENGINE = """() => { const p = window.AtlasDebug.getAllPaperOverview(); const e = window.AtlasEdgeStrength.edgesForState(window.AtlasDebug.getState().snapshot, p.cloudLayout, p.edgeState);
  return {state: p.edgeState, counters: e.counters, projected: {displayedEdges: p.counters.displayedEdges, displayedBridges: p.counters.displayedBridges, eligibleEdges: p.counters.eligibleEdges}, edges: e.edges.map(x => [x.id, x.tier, x.fromPointId, x.toPointId, Boolean(x.dimmed)]).sort(), bridges: e.bridges.map(b => [b.id, b.level, b.emphasis, b.dimmed]).sort()}; }"""
LEGEND = "() => [...document.querySelectorAll('#atlas-cloud-panel [data-section=\"edges\"] .atlas-cloud-edge-legend-row')].map(r => [r.dataset.tier, +r.dataset.count, r.textContent])"


@pytest.fixture()
def edges_app():
    fixture = edges_fixture()
    with running_app(fixture) as harness:
        yield harness
        assert not harness.errors, f"browser console/page errors: {harness.errors[:3]}"


def drawn_matches_engine(app: Harness) -> dict[str, Any]:
    """Drawn DOM == engine result for the mapped state; counters (projection + toolbar) == engine counts; total <= 1000."""
    app.wait(CAMERA_IDLE, 10)  # incl. motion idle: draw-in masks and the old-frame image are gone before pixels/styles are read
    drawn = app.page.evaluate(DRAWN); engine = app.page.evaluate(ENGINE)
    assert sorted([e["id"], e["tier"], e["from"], e["to"], e["dimmed"]] for e in drawn["edges"]) == engine["edges"], "drawn paper lines == edgesForState"
    assert sorted([b["id"], b["level"], b["emphasis"], b["dimmed"]] for b in drawn["bridges"]) == engine["bridges"], "drawn bridges == edgesForState"
    assert engine["projected"]["displayedEdges"] == engine["counters"]["displayedEdges"] == len(drawn["edges"])
    assert engine["projected"]["displayedBridges"] == engine["counters"]["displayedBridges"] == len(drawn["bridges"])
    assert len(drawn["edges"]) + len(drawn["bridges"]) <= 1000
    text = app.page.locator("#atlas-overview-counters").inner_text()
    assert f"{len(drawn['edges'])} lines · {len(drawn['bridges'])} bridges / {engine['projected']['eligibleEdges']} eligible edges" in text, text
    stats = app.page.evaluate("() => window.AtlasDebug.getCloudEdges().drawn")
    assert stats["total"] == len(drawn["edges"]) and stats["bridges"] == len(drawn["bridges"]) and all(stats[t] == sum(1 for e in drawn["edges"] if e["tier"] == t) for t in ("strong", "medium", "weak"))
    return {"drawn": drawn, "engine": engine}


def _png_pixel(png: bytes) -> tuple[int, int, int]:
    """RGB of a 1x1 PNG (Playwright screenshot clip): for a single pixel every PNG filter reduces to the raw bytes."""
    import struct, zlib
    pos, idat = 8, b""
    while pos < len(png):
        length = struct.unpack(">I", png[pos:pos + 4])[0]; kind = png[pos + 4:pos + 8]
        if kind == b"IHDR":
            assert struct.unpack(">II", png[pos + 8:pos + 16]) == (1, 1) and png[pos + 16] == 8
        if kind == b"IDAT":
            idat += png[pos + 8:pos + 8 + length]
        pos += 12 + length
    raw = zlib.decompress(idat)
    return raw[1], raw[2], raw[3]


def screen_pixel(app: Harness, x: float, y: float) -> tuple[int, int, int]:
    return _png_pixel(app.page.screenshot(clip={"x": round(x), "y": round(y), "width": 1, "height": 1}))


@receipt("ME-4,ME-6;D-M1;counter-contract")
def test_collection_map_draws_only_engine_edges_bridges_idle_cloud_and_paper_states_with_truthful_counters(edges_app: Harness):
    app = edges_app; page = app.page
    open_collections(app)
    app.reveal("overview")
    # idle: bridges only (D-M1), no legend
    idle = drawn_matches_engine(app)
    assert idle["drawn"]["edges"] == [] and len(idle["drawn"]["bridges"]) >= 1 and idle["engine"]["state"] == {"cloudId": None, "paperId": None, "pointId": None, "subItemPaperIds": None}
    assert all(not b["emphasis"] and not b["dimmed"] and b["vectorEffect"] == "non-scaling-stroke" for b in idle["drawn"]["bridges"])
    assert all(b["width"] == [1.5, 3, 4.5][b["level"] - 1] for b in idle["drawn"]["bridges"]), "bridge width follows the engine level"
    # cloud: its strong intra-cloud lines + bridges touching it emphasised, the rest dimmed
    target = next(row for row in page.evaluate(LABEL_BOXES) if row["id"] == C03 and not row["hidden"])
    page.mouse.click(target["x"] + target["w"] / 2, target["y"] + target["h"] / 2)
    app.wait("id => document.querySelector('#atlas-cloud-overlay').dataset.selectedCloud === id", 10, C03)
    cloud = drawn_matches_engine(app)
    assert cloud["drawn"]["edges"] and all(e["tier"] == "strong" and e["intra"] and C03 in e["from"] and C03 in e["to"] for e in cloud["drawn"]["edges"])
    assert all(b["emphasis"] == (C03 in b["id"]) and b["dimmed"] == (C03 not in b["id"]) for b in cloud["drawn"]["bridges"]) and any(b["emphasis"] for b in cloud["drawn"]["bridges"])
    app.wait("() => document.querySelector('#atlas-cloud-panel [data-section=\"edges\"]') !== null", 10)
    assert [row[0] for row in page.evaluate(LEGEND)] == ["strong"] and page.evaluate(LEGEND)[0][1] == len(cloud["drawn"]["edges"])
    # paper (native point click on the hub's point): all of its lines in three tiers, bridges dimmed
    hub_point = f"overview-point:{C03}:{HUB}"
    app.click_node("overview", hub_point)
    app.wait("id => window.AtlasDebug.getViewState()?.selected?.id === id", 10, HUB)
    paper = drawn_matches_engine(app)
    assert paper["engine"]["state"]["paperId"] == HUB and paper["engine"]["state"]["pointId"] == hub_point
    edges = paper["drawn"]["edges"]
    assert {e["tier"] for e in edges} == {"strong", "medium", "weak"} and all(e["from"] == hub_point for e in edges)
    assert len({e["id"] for e in edges}) == len(edges) == sum(1 for e in app.fixture["edges"] if e["type"] == "semantic" and HUB in (e["source"], e["target"])), "one line per pair, every semantic edge of the paper"
    assert all(b["dimmed"] and not b["emphasis"] for b in paper["drawn"]["bridges"]), "paper state: bridges recede"
    legend = page.evaluate(LEGEND)
    assert [row[0] for row in legend] == ["strong", "medium", "weak"] and [row[1] for row in legend] == [sum(1 for e in edges if e["tier"] == t) for t in ("strong", "medium", "weak")]
    assert page.locator('#atlas-cloud-panel [data-section="edges"] .atlas-note').inner_text() == "선 굵기는 이 라이브러리 안에서의 상대 강도입니다."
    # inter-cloud lines are curves (Q), intra-cloud lines straight (L); intra lines take the cloud stroke tone, inter lines the neutral tone
    assert all(("Q" in e["d"]) != e["intra"] for e in edges) and any(not e["intra"] for e in edges)
    hull_stroke = page.evaluate("id => getComputedStyle(document.querySelector(`.atlas-cloud-hull[data-cloud-id=\"${CSS.escape(id)}\"]`)).stroke", C03)
    assert {e["stroke"] for e in edges if e["intra"]} == {hull_stroke}
    neutral = page.evaluate("() => { const probe = document.createElementNS('http://www.w3.org/2000/svg', 'path'); probe.style.stroke = 'var(--atlas-muted)'; document.querySelector('.atlas-cloud-edges').append(probe); const v = getComputedStyle(probe).stroke; probe.remove(); return v; }")
    assert {e["stroke"] for e in edges if not e["intra"]} == {neutral} != {hull_stroke}
    # panel representative-paper route: no clicked point -> the engine's replica rule starts in the selected cloud
    row = page.locator(f'#atlas-cloud-panel .atlas-cloud-paper:not([data-paper-id="{HUB}"])').first
    other = row.get_attribute("data-paper-id")
    row.click()
    app.wait("id => window.AtlasDebug.getViewState()?.selected?.id === id", 10, other)
    listed = drawn_matches_engine(app)
    assert listed["engine"]["state"]["pointId"] is None and listed["engine"]["state"]["paperId"] == other
    assert all(e["from"] == f"overview-point:{C03}:{other}" for e in listed["drawn"]["edges"]), "panel selection starts at the paper's point in the selected cloud"
    assert bool(page.evaluate(LEGEND)) == bool(listed["drawn"]["edges"])
    # closing the panel returns to idle: no lines, undimmed bridges
    page.locator(".atlas-cloud-panel-close").click()
    app.wait("() => document.getElementById('atlas-cloud-panel').hidden", 10)
    back = drawn_matches_engine(app)
    assert back["drawn"]["edges"] == [] and all(not b["dimmed"] for b in back["drawn"]["bridges"])


@receipt("ME-3;ME-7")
def test_tiered_lines_have_ordered_screen_widths_opacities_and_only_weak_dashes_constant_under_zoom_above_hulls_under_points(edges_app: Harness):
    app = edges_app; page = app.page
    open_collections(app)
    hub_point = f"overview-point:{C03}:{HUB}"
    app.click_node("overview", hub_point)
    app.wait("id => window.AtlasDebug.getViewState()?.selected?.id === id", 10, HUB)
    edges = drawn_matches_engine(app)["drawn"]["edges"]
    by = {t: [e for e in edges if e["tier"] == t] for t in ("strong", "medium", "weak")}
    assert all(by[t] for t in by)
    assert min(e["width"] for e in by["strong"]) > max(e["width"] for e in by["medium"]) and min(e["width"] for e in by["medium"]) > max(e["width"] for e in by["weak"]), "computed stroke widths strong > medium > weak"
    assert min(e["opacity"] for e in by["strong"]) > max(e["opacity"] for e in by["medium"]) and min(e["opacity"] for e in by["medium"]) > max(e["opacity"] for e in by["weak"]), "opacities strong > medium > weak"
    assert all(e["dash"] == "none" for e in by["strong"] + by["medium"]) and all(e["dash"] not in ("none", "") for e in by["weak"]), "only weak lines are dashed"
    assert all(e["vectorEffect"] == "non-scaling-stroke" for e in edges)
    order = page.evaluate("() => [...document.querySelectorAll('.atlas-cloud-edges .atlas-cloud-edge')].map(el => el.dataset.tier)")
    assert order == sorted(order, key=["weak", "medium", "strong"].index), "paint order weak -> medium -> strong (strong on top)"
    # layer order: bridges < hull outlines < paper lines inside the under-canvas SVG; the SVG precedes the G6 container, labels follow it
    layers = page.evaluate("() => { const w = document.querySelector('.atlas-cloud-hulls .atlas-cloud-world'); const kids = [...w.children].map(c => c.getAttribute('class')); const svg = document.querySelector('.atlas-cloud-hulls'); const c = document.getElementById('atlas-field-network'); const labels = document.getElementById('atlas-cloud-overlay');"
                           " return {kids, svgBeforeCanvas: Boolean(svg.compareDocumentPosition(c) & Node.DOCUMENT_POSITION_FOLLOWING), labelsAfterCanvas: Boolean(c.compareDocumentPosition(labels) & Node.DOCUMENT_POSITION_FOLLOWING)}; }")
    assert layers["kids"].index("atlas-cloud-bridges") < layers["kids"].index("atlas-cloud-hull-layer") < layers["kids"].index("atlas-cloud-edges") and layers["svgBeforeCanvas"] and layers["labelsAfterCanvas"]
    # real pixels: the middle of a strong intra-cloud line (away from every point) shows the line, not the hull fill; the hub point's centre shows the point, not a line
    MID = """id => { const el = [...document.querySelectorAll('.atlas-cloud-edge')].find(e => e.dataset.edgeId === id); const len = el.getTotalLength(); const g = window.AtlasDebug.getOverviewGraph();
      for (const f of [0.5, 0.4, 0.6, 0.3, 0.7]) { const q = el.getPointAtLength(len * f).matrixTransform(el.getScreenCTM()); const far = g.getNodeData().every(n => { const [x, y] = g.getClientByCanvas(g.getElementPosition(n.id)); return Math.hypot(x - q.x, y - q.y) > 9; }); if (far) return {x: q.x, y: q.y}; } return null; }"""
    spot = next(m for e in sorted(by["strong"], key=lambda e: e["id"]) if e["intra"] for m in [page.evaluate(MID, e["id"])] if m)
    fill = _rgb(page.evaluate("id => getComputedStyle(document.querySelector(`.atlas-cloud-hull[data-cloud-id=\"${CSS.escape(id)}\"]`)).fill", C03))
    line_px = screen_pixel(app, spot["x"], spot["y"])
    assert _distance(line_px, fill) > 40, f"the strong line is painted above the hull fill: pixel {line_px} vs fill {fill}"
    centre = app.client_point("overview", hub_point)
    fill_text = page.evaluate("id => window.AtlasDebug.getOverviewGraph().getElementRenderStyle(id).fill", hub_point)
    point_fill = tuple(int(fill_text[i:i + 2], 16) for i in (1, 3, 5)) if fill_text.startswith("#") else _rgb(fill_text)
    assert _distance(screen_pixel(app, centre["x"], centre["y"]), point_fill) < 40, "the point is painted above its own lines"
    # zoom: the world transform scales, the drawn widths stay the same screen px (non-scaling stroke)
    scale_before = page.evaluate("() => document.querySelector('.atlas-cloud-world').getScreenCTM().a"); draws = page.evaluate("() => window.AtlasDebug.getCloudEdges().drawn.draws")
    page.locator("#atlas-network-zoom-in").click(); app.wait(CAMERA_IDLE, 10)
    page.locator("#atlas-network-zoom-in").click(); app.wait(CAMERA_IDLE, 10)
    after = page.evaluate(DRAWN)["edges"]
    assert page.evaluate("() => document.querySelector('.atlas-cloud-world').getScreenCTM().a") > scale_before * 1.2
    assert {(e["id"], e["width"], e["opacity"], e["dash"], e["vectorEffect"]) for e in after} == {(e["id"], e["width"], e["opacity"], e["dash"], e["vectorEffect"]) for e in edges}
    assert page.evaluate("() => window.AtlasDebug.getCloudEdges().drawn.draws") == draws, "camera moves never rebuild the edge layers (the world transform moves them)"


@receipt("ME-4;replica-rule")
def test_multi_collection_paper_lines_follow_the_replica_rule_from_the_clicked_point(edges_app: Harness):
    app = edges_app; page = app.page
    open_collections(app)
    ends = {}
    for cloud in (C05, C06):
        clicked = f"overview-point:{cloud}:{MULTI}"
        app.click_node("overview", clicked)
        app.wait("([id, c]) => window.AtlasDebug.getViewState()?.selected?.id === id && window.AtlasDebug.getAllPaperOverview().edgeState.pointId === c", 10, [MULTI, clicked])
        edges = drawn_matches_engine(app)["drawn"]["edges"]
        assert edges and all(e["from"] == clicked for e in edges), "lines start at the clicked replica"
        ends[cloud] = {e["id"]: e["to"] for e in edges}
        for e in edges:
            other = e["to"].split(":zotero:")[0].removeprefix("overview-point:"); partner = "zotero:" + e["to"].split(":zotero:")[1]
            replicas = [n["data"]["cloudId"] for n in app.overview_nodes() if n["data"]["paperId"] == partner]
            if cloud in replicas:
                assert other == cloud, f"a partner with a replica in the clicked cloud ends there ({e['id']})"
            else:
                assert other in replicas
    assert ends[C05].keys() == ends[C06].keys(), "one line per pair, whichever replica was clicked"
    d01 = next(k for k, v in ends[C06].items() if v.endswith(":D01")); ot03 = next(k for k, v in ends[C05].items() if v.endswith(":OT03"))
    assert ends[C06][d01] == f"overview-point:{C06}:zotero:{SERVER}:0:D01" and ends[C05][ot03] == ends[C06][ot03] == f"overview-point:{C05}:zotero:{SERVER}:0:OT03"
    # from C05 (no D01 replica there) the line goes to the canvas-nearest D01 replica (ties -> cloud id)
    pts = {pt["id"]: pt for pt in page.evaluate("() => window.AtlasDebug.getAllPaperOverview().cloudLayout.points")}; start = pts[f"overview-point:{C05}:{MULTI}"]
    nearest = min((pt for pt in pts.values() if pt["paperId"].endswith(":D01")), key=lambda pt: (((pt["x"] - start["x"]) ** 2 + (pt["y"] - start["y"]) ** 2) ** 0.5, pt["cloudId"]))
    assert ends[C05][d01] == nearest["id"]


@receipt("ME-7;AE-9;theme")
def test_theme_change_recolours_edges_and_bridges_without_relayout(edges_app: Harness):
    app = edges_app; page = app.page
    open_collections(app)
    app.click_node("overview", f"overview-point:{C03}:{HUB}")
    app.wait("id => window.AtlasDebug.getViewState()?.selected?.id === id", 10, HUB)
    light = drawn_matches_engine(app)["drawn"]
    outlines = page.evaluate(HULL_D); placements = page.evaluate("() => window.AtlasDebug.getCloudLayoutStats().overlay.placements")
    open_advanced(page)
    page.locator("#atlas-theme").select_option("dark"); app.wait("() => window.AtlasDebug.getCloudLayoutStats()?.theme === 'dark'", 10); app.wait(CAMERA_IDLE, 10)
    dark = drawn_matches_engine(app)["drawn"]
    stats = page.evaluate("() => ({g6: window.AtlasDebug.getG6Stats().overview, cloud: window.AtlasDebug.getCloudLayoutStats()})")
    assert stats["g6"]["layoutRuns"] == 0 and stats["g6"]["topologyChanges"] == 0 and stats["cloud"]["overlay"]["placements"] == placements and page.evaluate(HULL_D) == outlines, "no relayout"
    key = lambda rows: sorted((e["id"], e.get("tier"), e.get("d")) for e in rows)  # noqa: E731
    assert key(dark["edges"]) == key(light["edges"]) and key(dark["bridges"]) == key(light["bridges"]), "same lines, same geometry"
    l = {e["id"]: e["stroke"] for e in light["edges"]}; d = {e["id"]: e["stroke"] for e in dark["edges"]}
    assert all(l[i] != d[i] for i in l), "every line colour swaps to the dark tones (cloud stroke or neutral)"
    assert all(a["stroke"] != b["stroke"] for a, b in zip(sorted(light["bridges"], key=lambda b: b["id"]), sorted(dark["bridges"], key=lambda b: b["id"])))


@receipt("ME-4;EDGE_DRAW_CAP;1000-papers")
def test_large_fixture_edges_stay_within_the_draw_cap_and_match_the_engine():
    with running_app(large_fixture()) as app:
        page = app.page
        projection = open_collections(app)
        idle = drawn_matches_engine(app)
        assert idle["drawn"]["edges"] == [] and len(idle["drawn"]["bridges"]) <= 12
        app.reveal("overview")
        label = next(row for row in page.evaluate(LABEL_BOXES) if not row["hidden"])
        page.mouse.click(label["x"] + label["w"] / 2, label["y"] + label["h"] / 2)
        app.wait("id => document.querySelector('#atlas-cloud-overlay').dataset.selectedCloud === id", 10, label["id"])
        drawn_matches_engine(app)
        node = next(n for n in app.overview_nodes() if n["data"]["cloudId"] == label["id"])
        app.click_node("overview", node["id"])
        app.wait("id => window.AtlasDebug.getViewState()?.selected?.id === id", 10, node["data"]["paperId"])
        paper = drawn_matches_engine(app)
        assert paper["drawn"]["edges"] and len(paper["drawn"]["edges"]) + len(paper["drawn"]["bridges"]) <= 1000
        assert page.evaluate("() => window.AtlasDebug.getCloudEdges().edgeMs") >= 0 and projection["counters"]["eligibleEdges"] == paper["engine"]["projected"]["eligibleEdges"]
        assert not app.errors, app.errors[:3]



LOWER_TIERS = """which => { const s = window.AtlasDebug.getState().snapshot; const E = window.AtlasEdgeStrength; const sem = E.semanticStrength(s).byId; const kw = E.keywordStrength(s).byId;
  const g = which === 'paper' ? window.AtlasDebug.getPaperGraph() : window.AtlasDebug.getConceptGraph(); if (!g) return null;
  return g.getEdgeData().map(e => { const d = e.data || e; const id = e.id ?? d.id; const expected = d.relation === 'semantic' ? sem.get(id)?.tier ?? null : d.relation === 'concept-cooccurrence' ? kw.get(id)?.tier ?? null : null;
    const r = g.getElementRenderStyle ? g.getElementRenderStyle(id) : null; return {id, relation: d.relation, tier: d.tier ?? null, expected, width: r ? r.lineWidth : null, opacity: r ? r.strokeOpacity : null, dash: r ? r.lineDash : null}; }); }"""


@receipt("ME-8;ME-5")
def test_lower_networks_carry_edge_tiers_in_cytoscape_and_g6(edges_app: Harness):
    app = edges_app; page = app.page
    open_collections(app)
    open_advanced(page)
    app.wait("() => document.getElementById('atlas-advanced').dataset.lowerNetworks === 'rendered' && (window.AtlasDebug.getPaperGraph()?.getEdgeData().length || 0) > 0", 20)
    page.locator("#atlas-sample").fill("100"); page.locator("#atlas-sample").dispatch_event("input")  # full detail: the hub's lines are all in the paper network
    quiet = "() => ['#atlas-paper-network', '#atlas-concept-network'].every(sel => { const c = document.querySelector(sel); return !c.dataset.updateState || c.dataset.updateState === 'idle'; })"
    widths = {"strong": 2.4, "medium": 1.4, "weak": 0.8}
    for renderer in ("cytoscape", "g6"):
        if renderer == "g6":
            page.locator("#atlas-renderer").select_option("g6")
            app.wait("() => ['#atlas-paper-network', '#atlas-concept-network'].every(sel => document.querySelector(sel)?.dataset.rendererState === 'ready')", 30)
        for relation in ("citation", "semantic"):
            page.locator("#atlas-relation").select_option(relation)
            app.wait("r => (window.AtlasDebug.getPaperGraph()?.getEdgeData() || []).some(e => (e.data || e).relation === r)", 20, relation); app.wait(quiet, 20)
            paper = page.evaluate(LOWER_TIERS, "paper"); concept = page.evaluate(LOWER_TIERS, "concept")
            assert len(paper) <= 1000 and len(concept) <= 1000, "edge budgets kept"
            assert all(row["tier"] == row["expected"] for row in paper + concept), f"{renderer}/{relation}: every edge carries its full-snapshot tier"
            assert any(row["tier"] for row in concept if row["relation"] == "concept-cooccurrence"), f"{renderer}: keyword tiers reach the concept network"
            assert all(row["tier"] is None for row in paper + concept if row["relation"] not in ("semantic", "concept-cooccurrence")), "citation/membership carry no tier"
            if relation == "semantic":
                assert {row["tier"] for row in paper if row["relation"] == "semantic"} == {"strong", "medium", "weak"}, f"{renderer}: all three semantic tiers reach the paper network"
            else:
                assert any(row["relation"] == "citation" for row in paper)
            if renderer == "g6":
                app.wait(quiet, 20)
                paper = page.evaluate(LOWER_TIERS, "paper"); concept = page.evaluate(LOWER_TIERS, "concept")
                assert all(abs(row["width"] - widths[row["tier"]]) < 1e-6 for row in paper + concept if row["tier"]), "G6 renders the tier width"
                assert all(row["width"] == 1.5 for row in paper if row["relation"] == "citation"), "citation keeps its typed width"
