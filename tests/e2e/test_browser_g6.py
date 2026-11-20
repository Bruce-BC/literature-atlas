"""AntV G6 renderer acceptance checks against the authenticated built app."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v2_support import legacy_screen  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "demo-snapshot.json"


def wait_for(page, predicate, timeout=10, arg=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = page.evaluate(predicate, arg) if arg is not None else page.evaluate(predicate)
        if value:
            return value
        time.sleep(.1)
    raise AssertionError("G6 browser state did not converge")


def graph_metrics(page, which="paper"):
    return page.evaluate("""which => {
      const graph = which === 'paper' ? window.AtlasDebug.getPaperGraph() : window.AtlasDebug.getConceptGraph();
      const nodes = graph?.getNodeData?.() || [];
      const edges = graph?.getEdgeData?.() || [];
      const first = which === 'concept' ? (nodes.find(node => node.data?.kind === 'concept')?.id || nodes[0]?.id) : nodes[0]?.id;
      const pos = first && graph.getElementPosition ? graph.getElementPosition(first) : null;
      const client = pos && graph.getClientByCanvas ? graph.getClientByCanvas(pos) : null;
      return {nodes: nodes.length, edges: edges.length, zoom: graph?.getZoom?.() ?? null, first, pos, client};
    }""", which)


def wait_graph_settled(page, which="paper", timeout=8):
    deadline = time.monotonic() + timeout
    previous = None
    while time.monotonic() < deadline:
        current = graph_metrics(page, which)["pos"]
        if current and previous:
            a = (previous[0], previous[1]) if isinstance(previous, list) else (previous["x"], previous["y"])
            b = (current[0], current[1]) if isinstance(current, list) else (current["x"], current["y"])
            if abs(a[0] - b[0]) < 1 and abs(a[1] - b[1]) < 1:
                return graph_metrics(page, which)
        previous = current
        time.sleep(.2)
    return graph_metrics(page, which)


def wait_renderer_ready(page, selector, console_errors, timeout=15):
    try:
        page.locator(selector).wait_for(timeout=timeout * 1000)
    except Exception as exc:
        attrs = page.locator(selector.replace("[data-renderer-state='ready']", "")).evaluate("el => ({state: el.dataset.rendererState || null, busy: el.getAttribute('aria-busy'), text: el.textContent?.slice(0,160)})")
        raise AssertionError(f"G6 readiness timeout selector={selector} attrs={attrs} errors={console_errors[-8:]}") from exc


def test_g6_renderer_switches_interacts_and_preserves_projection():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        pytest.fail(f"Playwright is required for G6 acceptance: {exc}")
    stderr: list[str] = []
    with tempfile.TemporaryDirectory(prefix="atlas-g6-") as data_dir:
        process = subprocess.Popen([sys.executable, "-m", "atlas", "serve", "--demo", "--demo-fixture", str(FIXTURE), "--embedding-provider", "fake", "--port", "0", "--data-dir", data_dir, "--web-root", str(ROOT / "dist/web"), "--poll-interval", "5", "--launch-id", "g6-test", "--parent-origin", "app://obsidian.md"], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert process.stdout and process.stderr
        bootstrap = json.loads(process.stdout.readline())
        base = f"http://127.0.0.1:{bootstrap['port']}"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1200, "height": 1000})
            console_errors: list[str] = []
            page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
            page.goto(base + "/", wait_until="domcontentloaded")
            page.locator("#atlas-token-input").fill(bootstrap["token"]); page.locator("#atlas-connect").click()
            wait_for(page, "() => Boolean(window.AtlasDebug?.getState()?.snapshot?.papers?.length)")
            legacy_screen(page)  # legacy controls live in Advanced (AC-11); Circular ribbons was the default this test was written against
            overview = page.evaluate("window.AtlasDebug.getState().snapshot.overview")
            page.locator("#atlas-sample").fill("100"); page.locator("#atlas-sample").dispatch_event("input")
            page.locator("#atlas-overview-renderer").select_option("g6")
            wait_for(page, "() => !document.querySelector('#atlas-field-network')?.hidden")
            assert page.locator("#atlas-field-network canvas").count() >= 1
            assert page.evaluate("window.AtlasDebug.getState().snapshot.overview") == overview
            page.locator("#atlas-overview-renderer").select_option("ribbon")
            wait_for(page, "() => document.querySelector('#atlas-field-network')?.hidden === true")
            page.locator("#atlas-renderer").select_option("g6")
            wait_for(page, "() => window.AtlasDebug.getState().renderer === 'g6'")
            wait_renderer_ready(page, "#atlas-paper-network[data-renderer-state='ready']", console_errors)
            wait_renderer_ready(page, "#atlas-concept-network[data-renderer-state='ready']", console_errors)
            paper = wait_graph_settled(page, "paper"); concept = wait_graph_settled(page, "concept")
            assert paper["nodes"] == page.evaluate("window.AtlasDebug.getActualCounts().paperNodes")
            assert paper["nodes"] > 0 and paper["edges"] > 0 and concept["nodes"] > 0
            assert page.evaluate("window.AtlasDebug.getState().snapshot.overview") == overview
            network_legends = page.locator(".atlas-network-legend")
            assert network_legends.count() >= 2
            assert all("categories" in network_legends.nth(i).inner_text() for i in range(network_legends.count()))
            category_swatches = page.locator(".atlas-network-legend-swatch")
            assert category_swatches.count() > 0
            assert all(category_swatches.nth(i).evaluate("el => getComputedStyle(el).backgroundColor != 'rgba(0, 0, 0, 0)'") for i in range(category_swatches.count()))
            paper_categories = page.evaluate("""() => [...(window.AtlasDebug.getPaperGraph().getNodeData?.() || [])].map(node => ({label: node.data?.categoryLabel || 'Unknown field', color: node.data?.categoryColor || '#64748b', title: node.data?.label || ''}))""")
            paper_labels = {row["label"] for row in paper_categories}
            paper_legend_text = network_legends.nth(0).inner_text()
            assert paper_labels and all(label in paper_legend_text for label in paper_labels)
            assert not any(row["title"] and row["title"] in paper_legend_text for row in paper_categories)
            assert any(row["color"].lower() != "#64748b" for row in paper_categories if row["label"] != "Unknown field")
            concept_categories = page.evaluate("""() => [...(window.AtlasDebug.getConceptGraph().getNodeData?.() || [])].map(node => node.data?.categoryLabel || 'Unknown field')""")
            assert set(concept_categories) & {row["label"] for row in paper_categories}

            container = page.locator("#atlas-paper-network")
            container.scroll_into_view_if_needed()
            paper = graph_metrics(page, "paper")
            box = container.bounding_box(); assert box and paper["pos"] and paper["client"]
            before = paper["pos"]
            bx, by = (before[0], before[1]) if isinstance(before, list) else (before["x"], before["y"])
            client_x, client_y = paper["client"][0], paper["client"][1]
            hit = page.evaluate("""point => { const container=document.querySelector('#atlas-paper-network'); const target=document.elementFromPoint(point[0], point[1]); return {viewport: point[0] >= 0 && point[1] >= 0 && point[0] <= innerWidth && point[1] <= innerHeight, inside: Boolean(target && container?.contains(target))}; }""", [client_x, client_y])
            assert hit["viewport"] and hit["inside"]
            page.mouse.click(paper["client"][0], paper["client"][1])
            expected_title = page.evaluate("id => (window.AtlasDebug.getState().snapshot.papers.find(paper => paper.id === id) || {}).title", paper["first"])
            wait_for(page, "title => document.querySelector('#atlas-detail')?.textContent.includes(title)", timeout=10, arg=expected_title)
            wait_renderer_ready(page, "#atlas-paper-network[data-renderer-state='ready']", console_errors)
            wait_renderer_ready(page, "#atlas-concept-network[data-renderer-state='ready']", console_errors)
            paper_after = wait_graph_settled(page, "paper")
            drag_plan = page.evaluate("""() => { const g=window.AtlasDebug.getPaperGraph(); const nodes=g.getNodeData(); const edges=g.getEdgeData(); const node=nodes.find(n=>edges.some(e=>e.source===n.id||e.target===n.id)) || nodes[0]; const neighbors=edges.filter(e=>e.source===node.id||e.target===node.id).map(e=>e.source===node.id?e.target:e.source); const point=g.getElementPosition(node.id); return {id:node.id, client:g.getClientByCanvas(point), pos:point, neighbors, neighborPositions:Object.fromEntries(neighbors.map(id=>[id,g.getElementPosition(id)]))}; }""")
            page.evaluate("""() => { const g=window.AtlasDebug.getPaperGraph(); window.__ATLAS_G6_DRAG_EVENTS__=[]; for (const type of ['node:dragstart','node:drag','node:dragend']) g.on(type, event => window.__ATLAS_G6_DRAG_EVENTS__.push({type, id:event.target?.id || event.target})); }""")
            page.mouse.move(drag_plan["client"][0], drag_plan["client"][1]); page.mouse.down(); page.wait_for_timeout(180); page.mouse.move(drag_plan["client"][0] + 12, drag_plan["client"][1] + 8); page.mouse.move(drag_plan["client"][0] + 42, drag_plan["client"][1] + 24, steps=8); page.mouse.up()
            drag_result = page.evaluate("""plan => { const g=window.AtlasDebug.getPaperGraph(); const end=g.getElementPosition(plan.id); const neighbors=Object.fromEntries(plan.neighbors.map(id=>[id,g.getElementPosition(id)])); return {end, neighbors, events:window.__ATLAS_G6_DRAG_EVENTS__ || []}; }""", drag_plan)
            delta = ((drag_result["end"][0]-drag_plan["pos"][0])**2 + (drag_result["end"][1]-drag_plan["pos"][1])**2) ** .5
            assert delta > 5
            assert {event["type"] for event in drag_result["events"]} >= {"node:dragstart", "node:dragend"}
            assert any(((drag_result["neighbors"][id][0]-drag_plan["neighborPositions"][id][0])**2 + (drag_result["neighbors"][id][1]-drag_plan["neighborPositions"][id][1])**2) ** .5 > .05 for id in drag_plan["neighbors"])

            concept_container = page.locator("#atlas-concept-network")
            concept_container.scroll_into_view_if_needed()
            concept = wait_graph_settled(page, "concept")
            cclient = concept["client"]
            assert cclient and page.evaluate("point => { const c=document.querySelector('#atlas-concept-network'); const e=document.elementFromPoint(point[0],point[1]); return Boolean(e && c?.contains(e)); }", cclient)
            concept_label = page.evaluate("id => (window.AtlasDebug.getState().snapshot.concepts.find(concept => concept.id === id) || {}).label?.en || id", concept["first"])
            page.mouse.click(cclient[0], cclient[1])
            wait_for(page, "label => document.querySelector('#atlas-detail')?.textContent.includes(label)", timeout=10, arg=concept_label)
            wait_graph_settled(page, "concept")  # the selection rebuilt the scope graph; drag only after the renderer is ready
            concept_container.scroll_into_view_if_needed()
            after_select = graph_metrics(page, "concept"); cbefore = after_select["pos"]; cclient = after_select["client"]  # the scope graph was rebuilt and refit: re-read the live target
            page.mouse.move(cclient[0], cclient[1]); page.mouse.down(); page.wait_for_timeout(180); page.mouse.move(cclient[0] + 12, cclient[1] + 8); page.mouse.move(cclient[0] + 30, cclient[1] + 18, steps=8); page.mouse.up()
            assert graph_metrics(page, "concept")["pos"] != cbefore

            page.locator("#atlas-network-zoom-out").click(); low = graph_metrics(page, "paper")["zoom"]; low_concept = graph_metrics(page, "concept")["zoom"]; assert .2 <= low <= 3 and .2 <= low_concept <= 3
            for _ in range(30): page.locator("#atlas-network-zoom-in").click()
            high = graph_metrics(page, "paper")["zoom"]; high_concept = graph_metrics(page, "concept")["zoom"]; assert .2 <= high <= 3 and .2 <= high_concept <= 3
            page.locator("#atlas-network-zoom-reset").click(); fit = graph_metrics(page, "paper")["zoom"]; fit_concept = graph_metrics(page, "concept")["zoom"]; assert .2 <= fit <= 3 and .2 <= fit_concept <= 3
            page.mouse.move(box["x"] + 100, box["y"] + 100); page.mouse.wheel(0, -600); assert .2 <= graph_metrics(page, "paper")["zoom"] <= 3

            page.locator("#atlas-theme").select_option("dark"); page.locator("#atlas-palette").select_option("mocha")
            assert page.locator("#atlas-app").get_attribute("data-atlas-theme") == "dark"
            assert page.evaluate("getComputedStyle(document.querySelector('#atlas-app')).getPropertyValue('--atlas-series-1-hex').trim()")
            Path(".artifacts").mkdir(exist_ok=True)
            page.screenshot(path=str(ROOT / ".artifacts" / "g6-dark.png"))
            legend = page.locator("#atlas-field-legend button.atlas-legend-field")
            assert legend.count() == len(json.loads(FIXTURE.read_text())["fields"])
            assert all(page.locator(".atlas-legend-swatch").nth(i).evaluate("el => getComputedStyle(el).backgroundColor != 'rgba(0, 0, 0, 0)'") for i in range(legend.count()))

            page.locator("#atlas-renderer").select_option("cytoscape")
            wait_for(page, "() => window.AtlasDebug.getState().renderer === 'cytoscape'")
            cy = graph_metrics(page, "paper"); assert cy["nodes"] > 0
            assert page.evaluate("window.AtlasDebug.getState().snapshot.overview") == overview
            page.locator("#atlas-renderer").select_option("g6"); wait_for(page, "() => window.AtlasDebug.getState().renderer === 'g6'")
            assert graph_metrics(page, "paper")["nodes"] == cy["nodes"]
            page.locator("#atlas-theme").select_option("light")
            page.screenshot(path=str(ROOT / ".artifacts" / "g6-light.png"))
            assert not any("Content Security Policy" in error or "ERR_BLOCKED_BY_CLIENT" in error for error in console_errors)
            browser.close()
        process.terminate(); process.wait(timeout=5)
