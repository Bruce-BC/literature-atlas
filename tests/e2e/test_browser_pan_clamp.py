"""AC-14 pan clamp: no graph view can be dragged, wheeled or restored into an empty canvas.

Native pointer/wheel events only (drag starts on an empty canvas spot); ``AtlasDebug`` is read-only
observation.  Combos: Cytoscape x {paper, concept}, G6 x {paper, concept, overview}; the G6 overview
combo covers both G6 overview modes (force field overview and preset all-papers map).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v2_support import OPT, Harness, acceptance_fixture, legacy_screen, running_app  # noqa: E402

OTHER = "field:demo-server:demo-epoch:topology"
GRAPH = {"overview": "window.AtlasDebug.getOverviewGraph()", "paper": "window.AtlasDebug.getPaperGraph()", "concept": "window.AtlasDebug.getConceptGraph()"}

# Node centres inside the container's client rect (what the user can actually see of the graph).
VISIBLE = """(sel) => { const g = GRAPH_EXPR; const r = document.querySelector(sel).getBoundingClientRect(); if (!g) return -1; let n = 0; for (const node of g.getNodeData()) { const c = g.getClientByCanvas(g.getElementPosition(node.id)); if (c[0] >= r.left && c[0] <= r.right && c[1] >= r.top && c[1] <= r.bottom) n += 1; } return n; }"""
# An empty canvas spot (>= 24 px from every node) lying as far "behind" the drag direction as possible.
EMPTY_SPOT = """([sel, dx, dy]) => { const g = GRAPH_EXPR; const c = document.querySelector(sel); const r = c.getBoundingClientRect(); const pts = g.getNodeData().map(n => g.getClientByCanvas(g.getElementPosition(n.id))); let best = null;
  const left = Math.max(r.left, 0) + 16, top = Math.max(r.top, 0) + 16, right = Math.min(r.right, innerWidth) - 16, bottom = Math.min(r.bottom, innerHeight) - 16;
  for (let x = left; x <= right; x += 12) for (let y = top; y <= bottom; y += 12) { let d = Infinity; for (const p of pts) d = Math.min(d, Math.hypot(p[0] - x, p[1] - y)); const hit = document.elementFromPoint(x, y); if (d < 24 || !hit || !c.contains(hit)) continue; const score = dx * x + dy * y; if (!best || score < best.score) best = {x, y, score}; }
  return best && {x: best.x, y: best.y, left, top, right, bottom}; }"""


def visible(app: Harness, which: str) -> int:
    return app.page.evaluate(VISIBLE.replace("GRAPH_EXPR", GRAPH[which]), app.SELECTORS[which])


def settle(app: Harness, which: str) -> None:
    # G6 corrects once after its own camera animation ends (its onfinish trails the visible motion): wait for the published camera state
    app.wait(f"() => !['animating', 'clamping'].includes(document.querySelector('{app.SELECTORS[which]}')?.dataset.cameraState)", 10)
    app.page.wait_for_timeout(150)  # rAF pan correction + camera-remember frame
    app.stable(which, 20, frames=3)


def extreme_drag(app: Harness, which: str, direction: tuple[int, int], repeats: int = 4) -> None:
    """Drag the empty canvas as far as the container allows, several times, in one direction."""
    for _ in range(repeats):
        spot = app.page.evaluate(EMPTY_SPOT.replace("GRAPH_EXPR", GRAPH[which]), [app.SELECTORS[which], direction[0], direction[1]])
        assert spot, f"{which}: no empty canvas spot to start a pan drag"
        end_x = spot["right"] if direction[0] > 0 else spot["left"]
        end_y = spot["bottom"] if direction[1] > 0 else spot["top"]
        app.page.mouse.move(spot["x"], spot["y"]); app.page.mouse.down()
        app.page.mouse.move(spot["x"] + direction[0] * 6, spot["y"] + direction[1] * 6, steps=2)
        app.page.mouse.move(end_x, end_y, steps=14); app.page.mouse.up()
        app.page.wait_for_timeout(80)


def extreme_wheel(app: Harness, which: str) -> None:
    """Zoom far in around an empty corner (pushes the nodes away), then all the way out."""
    spot = app.page.evaluate(EMPTY_SPOT.replace("GRAPH_EXPR", GRAPH[which]), [app.SELECTORS[which], 1, 1])
    assert spot, f"{which}: no empty canvas spot for the wheel"
    app.page.mouse.move(spot["x"], spot["y"])
    for _ in range(25):
        app.page.mouse.wheel(0, -400)
    app.page.wait_for_timeout(120)
    for _ in range(40):
        app.page.mouse.wheel(0, 400)
    app.page.wait_for_timeout(120)


def abuse_camera(app: Harness, which: str, buttons: bool) -> dict[str, int]:
    if buttons:  # the network buttons drive BOTH networks: start each network combo from a fitted camera, not the previous combo's zoom 3
        app.page.locator("#atlas-network-zoom-reset").click()
    app.reveal(which); settle(app, which)
    seen: dict[str, int] = {}
    extreme_drag(app, which, (1, 1)); settle(app, which); seen["drag+"] = visible(app, which)
    extreme_drag(app, which, (-1, -1)); settle(app, which); seen["drag-"] = visible(app, which)
    extreme_wheel(app, which); settle(app, which); seen["wheel"] = visible(app, which)
    extreme_drag(app, which, (1, -1)); settle(app, which); seen["drag+-"] = visible(app, which)
    if buttons:  # network zoom buttons: Cytoscape zooms about the model origin, G6 animates (clamped once after it)
        for _ in range(12):
            app.page.locator("#atlas-network-zoom-in").click()
        settle(app, which); seen["buttons"] = visible(app, which)
    return seen


def revisit_network(app: Harness, which: str, g6: bool) -> int:
    """Leave the field scope and come back: the stored camera is restored (and must be clamped)."""
    app.page.wait_for_timeout(150)  # let the camera-remember frame run before leaving the scope
    app.page.locator(f'#atlas-field-legend button[data-field-id="{OTHER}"]').click()
    app.wait(f"() => window.AtlasDebug.getViewState().scope.fieldId === '{OTHER}'")
    app.page.locator(f'#atlas-field-legend button[data-field-id="{OPT}"]').click()
    app.wait(f"() => window.AtlasDebug.getViewState().scope.fieldId === '{OPT}'")
    app.wait(f"() => window.AtlasDebug.getCameraPlans().{which}?.action === 'restore'", 10)
    if g6:
        app.wait(f"() => document.querySelector('{app.SELECTORS[which]}')?.dataset.rendererState === 'ready'", 20)
    app.reveal(which); settle(app, which)
    return visible(app, which)


def test_pan_clamp_matrix():
    fixture = acceptance_fixture()
    results: dict[str, dict[str, int]] = {}
    with running_app(fixture) as app:
        legacy_screen(app.page)  # legacy controls live in Advanced (AC-11); Circular ribbons was the default this test was written against
        app.page.locator(f'#atlas-field-legend button[data-field-id="{OPT}"]').click()
        app.wait(f"() => window.AtlasDebug.getViewState()?.scope?.fieldId === '{OPT}'")
        for renderer in ("cytoscape", "g6"):
            app.page.locator("#atlas-renderer").select_option(renderer)
            app.wait(f"() => window.AtlasDebug.getState().renderer === '{renderer}'")
            for which in ("paper", "concept"):
                if renderer == "g6":
                    app.wait(f"() => document.querySelector('{app.SELECTORS[which]}')?.dataset.rendererState === 'ready'", 20)
                app.reveal(which); settle(app, which)
                assert visible(app, which) > 0, f"{renderer}/{which}: nothing visible before interaction"
                seen = abuse_camera(app, which, buttons=True)
                seen["revisit"] = revisit_network(app, which, renderer == "g6")
                results[f"{renderer}/{which}"] = seen
        for mode in ("g6", "all-papers"):
            app.page.locator("#atlas-overview-renderer").select_option(mode); app.overview_ready()
            seen = abuse_camera(app, "overview", buttons=False)
            app.page.locator("#atlas-overview-renderer").select_option("ribbon")
            app.wait("() => document.querySelector('#atlas-field-network')?.hidden === true")
            app.page.locator("#atlas-overview-renderer").select_option(mode); app.overview_ready()
            app.reveal("overview"); settle(app, "overview"); seen["revisit"] = visible(app, "overview")
            results[f"g6/overview:{mode}"] = seen
        assert not app.errors, f"browser console/page errors: {app.errors[:3]}"
    empty = {combo: {step: count for step, count in seen.items() if count <= 0} for combo, seen in results.items()}
    empty = {combo: steps for combo, steps in empty.items() if steps}
    assert not empty, f"graph panned/restored into an empty canvas: {empty} (all: {results})"
    assert len(results) == 6
