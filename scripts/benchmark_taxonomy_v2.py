"""Cold/warm native-browser benchmark for taxonomy v2 (synthetic 1,000 papers / 1,410 concepts / 12 collections).

Every number is measured from a native mouse event to an observed state:
registered G6 points, real hit-test click, reducer revision, detail drawer DOM,
or stable canvas positions.  Gates are asserted against the MEASURED values and
a non-zero exit code is returned when any gate fails.  Nothing in the
application is patched; the owned bridge process is always cleaned up.

Usage: python3 scripts/benchmark_taxonomy_v2.py   (after ``npm run build``)
"""
from __future__ import annotations

import json
import math
import platform
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "e2e"))
from v2_support import OPT, OT, Harness, fixture_hash, large_fixture, legacy_screen, running_app  # noqa: E402

RECEIPT = ROOT / ".omx" / "context" / "taxonomy-v2-acceptance-performance.json"
SCREENSHOTS = ROOT / ".omx" / "context" / "taxonomy-v2-browser-screenshots"
# collectionPointCommitMs (motion plan ME-9): M0 baseline 83.3 ms (pre-motion median) + max(10 %, 20 ms) = 103.3 ms, measured WITH motion enabled.
COMMIT_BASELINE_MS = 83.3
GATES_MS = {"collectionPointCommitMs": round(COMMIT_BASELINE_MS + max(0.1 * COMMIT_BASELINE_MS, 20.0), 1), "coldFirstMeaningfulInteractiveMaxMs": 2500, "warmFieldP95Ms": 500, "warmSelectionP95Ms": 500, "expandFirstVisibleMs": 1500, "collapseMs": 500, "g6SettleMs": 10000, "interactionTaskMaxMs": 200, "cloudLayoutShapeColorMs": 150}  # AE-8: layout + outline shapes + colours, same limit, wider sum
LONGTASK_OBSERVER = """() => { window.__atlasLongTasks = []; try { new PerformanceObserver(list => { for (const e of list.getEntries()) window.__atlasLongTasks.push({start: e.startTime, duration: e.duration}); }).observe({type: 'longtask', buffered: true}); } catch (error) { window.__atlasLongTasks = null; } }"""


# STRICT readiness: the marker attributes must be PRESENT and have the settled values (a missing attribute never passes).
G6_QUIET = "sels => sels.every(sel => { const c = document.querySelector(sel); return Boolean(c) && c.dataset.rendererState === 'ready' && c.dataset.layoutState === 'settled' && c.dataset.updateState === 'idle'; })"


def wait_g6_quiet(app: Harness, selectors: list[str], timeout: float = 30.0) -> None:
    """Renderer ready AND real G6 layout settled (AFTER_LAYOUT event) AND no queued/pending update."""
    app.wait(G6_QUIET, timeout, selectors)


def p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def summary(values: list[float]) -> dict[str, float]:
    return {"n": len(values), "min": round(min(values), 1), "p50": round(statistics.median(values), 1), "p95": round(p95(values), 1), "max": round(max(values), 1), "range": round(max(values) - min(values), 1)}


def wait_until(app: Harness, expression: str, timeout: float, arg: Any = None) -> float:
    """Return elapsed seconds until the expression is truthy (10 ms polling; the resolution is reported)."""
    start = time.monotonic()
    app.wait(expression, timeout, arg)
    return time.monotonic() - start


def now_ms(app: Harness) -> float:
    return app.page.evaluate("() => performance.now()")


def long_tasks_since(app: Harness, mark: float) -> list[float]:
    rows = app.page.evaluate("() => window.__atlasLongTasks")
    if rows is None:
        raise AssertionError("PerformanceObserver longtask is unavailable; the 200 ms task gate cannot be measured")
    return [row["duration"] for row in rows if row["start"] >= mark]


def cold_run(fixture: dict[str, Any], run: int) -> dict[str, Any]:
    """Cold start on the DEFAULT screen (AC-11: the collection cloud map), timed in the browser clock from the native Connect click.

    Same milestones as before the default switch (mounted -> points registered -> hit-test ready -> first meaningful interaction),
    but the path is now Connect -> collection map -> native point click -> paper detail in the cloud panel (no mode select exists on it).
    """
    with running_app(fixture) as app:
        # vendor parse/GC phase is measured separately from interaction
        load_tasks = app.page.evaluate("() => window.__atlasLoadLongTasks ?? null")
        app.page.evaluate(LONGTASK_OBSERVER)
        mark = now_ms(app)  # buffered load-phase tasks start before this mark and are reported separately
        t0 = app.page.evaluate("() => window.__atlasConnectClickAt")
        assert isinstance(t0, (int, float)), "the Connect click was not observed"
        assert app.page.evaluate("() => window.AtlasDebug.getState().overviewRenderer") == "collections"
        app.wait("() => document.querySelector('#atlas-field-network')?.dataset.rendererState === 'ready'", 30)
        mounted = now_ms(app) - t0
        stats = app.wait("() => window.AtlasDebug.getCloudLayoutStats()", 30)
        app.wait("n => window.AtlasDebug.getOverviewGraph()?.getNodeData().length === n", 30, stats["points"])
        registered = now_ms(app) - t0
        registered_count = app.page.evaluate("() => window.AtlasDebug.getOverviewGraph().getNodeData().length")
        represented = app.page.evaluate("() => window.AtlasDebug.getAllPaperOverview().counters.active")
        assert represented == len(fixture["papers"]) == 1000 and registered_count == stats["points"] >= 1000
        node = app.overview_nodes()[500]
        point = app.client_point("overview", node["id"])
        box = app.page.locator("#atlas-field-network").bounding_box()
        assert box["x"] <= point["x"] <= box["x"] + box["width"] and box["y"] <= point["y"] <= box["y"] + box["height"], "hit-test target is outside the canvas"
        hit = now_ms(app) - t0
        # the point can move while the first fit settles: re-read right before the native click
        wait_g6_quiet(app, ["#atlas-field-network"], 20)
        app.stable("overview", 20, frames=3)
        point = app.client_point("overview", node["id"])
        assert app.pixel_painted("overview", point["x"], point["y"]), "no drawn pixels at the registered point"
        paper_id = node["data"]["paperId"]
        app.page.mouse.click(point["x"], point["y"])
        app.wait("id => window.AtlasDebug.getViewState()?.selected?.id === id", 10, paper_id)
        title = next(p["title"] for p in fixture["papers"] if p["id"] == paper_id)
        app.wait("t => [...document.querySelectorAll('.atlas-cloud-panel-detail h3')].some(h => !h.closest('[inert]') && h.textContent === t)", 10, title)  # the LIVE detail the user sees on the default screen (never the inert cross-fade ghost); end point = commit, the decoration after it is not timed
        total = now_ms(app) - t0
        view = app.view()
        lower = app.page.evaluate("() => window.AtlasDebug.getLowerProjection().revision")
        assert lower == view["revision"]
        return {"run": run, "path": "connect -> collections default -> point click -> cloud panel detail", "mountedMs": round(mounted, 1), "registeredMs": round(registered, 1), "hitTestReadyMs": round(hit, 1), "firstMeaningfulInteractiveMs": round(total, 1),
                "registeredPoints": registered_count, "representedPapers": represented, "nativeHitTest": True, "detailSelected": True, "interactionLongTasksMs": [round(v, 1) for v in long_tasks_since(app, mark)],
                "loadPhaseLongTasksMs": [round(row["duration"], 1) for row in (load_tasks or [])]}


def warm_runs(app: Harness, fixture: dict[str, Any]) -> dict[str, Any]:
    """Collect EXACTLY 20 real, successful field -> paper selections (fields without a listed paper are skipped and counted, never recorded as 0)."""
    fields = [row["id"] for row in fixture["fields"]]
    field_ms: list[float] = []; selection_ms: list[float] = []; revisions: list[int] = []; skipped = 0
    app.page.evaluate(LONGTASK_OBSERVER)
    mark = now_ms(app)
    attempt = 0
    while len(selection_ms) < 20:
        assert attempt < 200, f"could not collect 20 successful selections (got {len(selection_ms)}, skipped {skipped})"
        field_id = fields[attempt % len(fields)]
        attempt += 1
        before = app.view()["revision"]
        button = app.page.locator(f'#atlas-field-legend button[data-field-id="{field_id}"]')
        start = time.monotonic()
        button.click()
        app.wait("([f, r]) => { const v = window.AtlasDebug.getViewState(); return v.scope.fieldId === f && v.revision > r && window.AtlasDebug.getLowerProjection().revision === v.revision; }", 10, [field_id, before])
        elapsed_field = (time.monotonic() - start) * 1000
        view = app.view()
        assert view["selected"] == {"kind": "field", "id": field_id}
        paper_button = app.page.locator("#atlas-paper-list button").first
        if paper_button.count() == 0:
            skipped += 1
            continue
        paper_id = paper_button.get_attribute("data-entity-id")
        before = view["revision"]
        start = time.monotonic()
        paper_button.click()
        app.wait("([p, r]) => { const v = window.AtlasDebug.getViewState(); return v.selected?.id === p && v.revision > r && window.AtlasDebug.getLowerProjection().revision === v.revision && Boolean(document.querySelector('.atlas-selection-detail h3')); }", 10, [paper_id, before])
        elapsed_selection = (time.monotonic() - start) * 1000
        assert elapsed_selection > 0
        field_ms.append(elapsed_field); selection_ms.append(elapsed_selection); revisions.append(app.view()["revision"])
    assert len(field_ms) == len(selection_ms) == 20 and all(v > 0 for v in selection_ms)
    return {"fieldMs": [round(v, 1) for v in field_ms], "selectionMs": [round(v, 1) for v in selection_ms], "revisions": revisions, "skippedFieldsWithoutPapers": skipped, "attempts": attempt, "longTasksMs": [round(v, 1) for v in long_tasks_since(app, mark)]}


def expand_collapse(app: Harness) -> dict[str, Any]:
    app.page.locator("#atlas-field-legend button").first.click()  # leave any prior focus predictable
    app.page.locator("#atlas-overview-renderer").select_option("g6")
    app.overview_ready()
    before_ids = {row["id"] for row in app.overview_nodes()}
    assert OPT in before_ids
    app.reveal("overview")  # the category table now sits in Advanced below the map: bring the map back on screen before native clicks (outside the timed span)
    app.page.evaluate(LONGTASK_OBSERVER)
    mark = now_ms(app)
    point = app.client_point("overview", OPT)
    start = time.monotonic()
    app.page.mouse.click(point["x"], point["y"])
    app.wait("([ids]) => window.AtlasDebug.getViewState().expanded.length === 1 && window.AtlasDebug.getOverviewGraph().getNodeData().some(n => !ids.includes(n.id)) && document.querySelector('#atlas-field-network').dataset.rendererState === 'ready'", 12, [sorted(before_ids)])
    expand_ms = (time.monotonic() - start) * 1000
    after_ids = {row["id"] for row in app.overview_nodes()}
    assert after_ids > before_ids and OT in after_ids, "expansion must add real child nodes"
    app.overview_ready()
    point = app.client_point("overview", OPT)
    start = time.monotonic()
    app.page.mouse.click(point["x"], point["y"])
    app.wait("n => window.AtlasDebug.getViewState().expanded.length === 0 && window.AtlasDebug.getOverviewGraph().getNodeData().length === n", 12, len(before_ids))
    collapse_ms = (time.monotonic() - start) * 1000
    assert {row["id"] for row in app.overview_nodes()} == before_ids
    return {"expandFirstVisibleMs": round(expand_ms, 1), "collapseMs": round(collapse_ms, 1), "childNodesAdded": len(after_ids - before_ids), "longTasksMs": [round(v, 1) for v in long_tasks_since(app, mark)]}


def g6_settle(app: Harness) -> dict[str, Any]:
    app.page.locator("#atlas-renderer").select_option("g6")
    field = [row["id"] for row in app.fixture["fields"]][0]
    start = time.monotonic()
    app.page.locator(f'#atlas-field-legend button[data-field-id="{field}"]').click()
    containers = ["#atlas-paper-network", "#atlas-concept-network"]
    wait_g6_quiet(app, containers, 30)  # ready + layoutState=settled (AFTER_LAYOUT) + updateState=idle on BOTH lower G6 graphs
    app.stable("paper", 30, frames=5)
    app.stable("concept", 30, frames=5)
    settle_ms = (time.monotonic() - start) * 1000
    stats = app.page.evaluate("() => { const s = window.AtlasDebug.getG6Stats(); return {paper: s.paper, concept: s.concept}; }")
    assert stats["paper"]["layoutsFinished"] >= 1 and stats["concept"]["layoutsFinished"] >= 1, "no real G6 layout-finished event was observed"
    assert stats["paper"]["failedUpdates"] == stats["concept"]["failedUpdates"] == 0
    markers = app.page.evaluate("sels => sels.map(sel => { const c = document.querySelector(sel).dataset; return [c.rendererState, c.layoutState, c.updateState]; })", containers)
    assert markers == [["ready", "settled", "idle"]] * 2
    return {"g6SettleMs": round(settle_ms, 1), "paperNodes": app.page.evaluate("() => window.AtlasDebug.getActualCounts().paperNodes"), "markers": markers, "layoutsFinished": {"paper": stats["paper"]["layoutsFinished"], "concept": stats["concept"]["layoutsFinished"]}, "layoutRuns": {"paper": stats["paper"]["layoutRuns"], "concept": stats["concept"]["layoutRuns"]},
            "marker": "rendererState=ready + layoutState=settled (G6 AFTER_LAYOUT) + updateState=idle on both lower G6 graphs, then 5 identical consecutive position frames on each; the library force layout is INCLUDED in the measured time, nothing is exempted"}


def cloud_layout_run(fixture: dict[str, Any], run: int) -> dict[str, Any]:
    """Fresh page (nothing memoised): the collections map is the default screen (AC-11); layoutMs is measured inside collection-clouds.js (performance.now)."""
    with running_app(fixture) as app:
        stats = app.wait("() => window.AtlasDebug.getCloudLayoutStats()", 30)
        app.wait("n => window.AtlasDebug.getOverviewGraph()?.getNodeData().length === n", 30, stats["points"])
        wait_g6_quiet(app, ["#atlas-field-network"], 30)
        app.wait("() => (window.AtlasDebug.getCloudLayoutStats()?.overlay?.placements || 0) > 0", 10)
        stats = app.page.evaluate("() => window.AtlasDebug.getCloudLayoutStats()")
        assert stats["clouds"] == len(fixture["collections"]) + 1, "12 synthetic collections + the unfiled cloud"
        assert not app.errors, app.errors[:3]
        shape_ms = stats["cloudShapeMsPeak"]  # the call that really computed the outlines (a later memo hit would under-report)
        return {"run": run, "cloudLayoutMs": round(stats["layoutMs"], 1), "cloudShapeMs": round(shape_ms, 1), "cloudColorMs": round(stats["cloudColorMs"], 1), "cloudLayoutShapeColorMs": round(stats["layoutMs"] + shape_ms + stats["cloudColorMs"], 1), "cloudEdgeMs": round(stats["cloudEdgeMs"], 1),
                "shapeMethods": stats["methods"], "shapeFallbacks": stats["fallbacks"], "outlinesComputed": stats["outlines"]["computed"], "neighbours": stats["neighbours"], "clouds": stats["clouds"], "points": stats["points"], "spacing": stats["spacing"], "fitZoom": stats["fit"]["zoom"] if stats["fit"] else None,
                "visibleLabels": stats["overlay"]["visibleLabels"], "hiddenLabels": stats["overlay"]["hiddenLabels"], "connectLongTasksMs": [round(row["duration"], 1) for row in (app.page.evaluate("() => window.__atlasLoadLongTasks ?? []"))]}


# M0 (motion plan ME-9 baseline): real pointer click on a collections-map point -> the G6 wrapper's update queue back to idle.
# The click time is the pointerdown of the native click, taken in a window capture listener (observed: G6 5.1.1 dispatches its node click and the app commits before the DOM pointerup/click are delivered); the end is the first mutation record that
# leaves data-update-state 'pending' while the state is 'idle' (attributeOldValue, so a pending->idle pair inside one checkpoint is still seen); a click whose
# update is a no-op for the queue (same cloud: only the selected state changes) ends at the first task after the click (setTimeout 0) and is counted in `syncCommits`.
COMMIT_PROBE = """() => { const c = document.querySelector('#atlas-field-network'); const s = window.__atlasCommit = {start: null, ms: null};
  window.addEventListener('pointerdown', event => { if (!(event.target instanceof Node) || !c.contains(event.target)) return; s.start = performance.now(); s.ms = null; s.queued = false; setTimeout(() => { if (s.ms === null && !s.queued && c.dataset.updateState === 'idle') { s.ms = performance.now() - s.start; s.sync = (s.sync || 0) + 1; } }, 0); }, true);
  new MutationObserver(records => { if (s.start === null || s.ms !== null) return; if (c.dataset.updateState === 'pending') s.queued = true; if (records.some(r => r.oldValue === 'pending') && c.dataset.updateState === 'idle') s.ms = performance.now() - s.start; }).observe(c, {attributes: true, attributeFilter: ['data-update-state'], attributeOldValue: true}); }"""


def collection_point_commit(app: Harness, clicks: int = 11) -> dict[str, Any]:
    """Warm-up 1 + median of the rest: native click on distinct visible points of the default collections map -> updateState idle (REPORTED, no gate)."""
    wait_g6_quiet(app, ["#atlas-field-network"], 30)
    app.stable("overview", 20, frames=3)
    app.page.evaluate(COMMIT_PROBE)
    nodes = app.overview_nodes()
    samples: list[float] = []; papers: list[str] = []; attempt = 0
    while len(samples) < clicks:
        assert attempt < 20 * clicks, f"could not collect {clicks} commit samples (got {len(samples)})"
        node = nodes[(attempt * 89 + 37) % len(nodes)]; attempt += 1
        point = app.client_point("overview", node["id"])
        hit = app.page.evaluate("([x, y]) => { const c = document.querySelector('#atlas-field-network'); const t = document.elementFromPoint(x, y); const r = c.getBoundingClientRect(); return Boolean(t && c.contains(t)) && x > r.left + 12 && x < r.right - 12 && y > r.top + 12 && y < r.bottom - 12; }", [point["x"], point["y"]])
        if not hit or node["data"]["paperId"] == (app.view().get("selected") or {}).get("id"):
            continue  # under a label button / outside the map / already selected: not a commit
        before = app.view()["revision"]
        app.page.mouse.click(point["x"], point["y"])
        app.wait("r => window.__atlasCommit.ms !== null && window.AtlasDebug.getViewState().revision > r", 10, before)
        samples.append(app.page.evaluate("() => window.__atlasCommit.ms")); papers.append(app.view()["selected"]["id"])
        wait_g6_quiet(app, ["#atlas-field-network"], 20)
        app.stable("overview", 20, frames=3)
    measured = samples[1:]
    return {"collectionPointCommitMs": round(statistics.median(measured), 1), "warmupMs": round(samples[0], 1), "samplesMs": [round(v, 1) for v in measured], "distinctPapers": len(set(papers)), "syncCommits": app.page.evaluate("() => window.__atlasCommit.sync || 0"),
            "marker": "window capture 'pointerdown' of the native click on #atlas-field-network -> first data-update-state mutation pending->idle (selection commit; motion enabled, the <= 450 ms decoration after the commit is not timed); warm-up 1 + median of 10"}


# ME-17 (reported only): rAF frame intervals from a native cloud-label click (camera auto-move + decorations) until data-motion-state is idle.
FRAME_PROBE = """() => { const s = window.__atlasFrames = {stamps: [], done: false}; const tick = t => { s.stamps.push(t); if (document.querySelector('#atlas-field-network').dataset.motionState === 'idle' && s.stamps.length > 2) { s.done = true; return; } requestAnimationFrame(tick); }; requestAnimationFrame(tick); }"""
MOVING_LABEL = """() => { const m = window.AtlasDebug.getMotion(); const c = document.querySelector('#atlas-field-network'); const vp = {width: c.clientWidth, height: c.clientHeight};
  const labels = new Map([...document.querySelectorAll('#atlas-cloud-overlay .atlas-cloud-label')].filter(b => !b.hidden).map(b => [b.dataset.cloudId, b.getBoundingClientRect()]));
  const row = window.AtlasDebug.getAllPaperOverview().clouds.filter(cl => labels.has(cl.id) && window.AtlasMotion.cloudCamera({camera: m.camera, viewport: vp, cloud: cl, zoomRange: [0.2, 3]}).move).sort((a, b) => b.size - a.size)[0];
  if (!row) return null; const r = labels.get(row.id); return {id: row.id, x: r.x + r.width / 2, y: r.y + r.height / 2}; }"""


def transition_frames(app: Harness, runs: int = 3) -> dict[str, Any]:
    """Frame interval p95 during real cloud-selection transitions on the 1,000-paper map (REPORTED, no gate): label click -> idle, panel closed between runs."""
    intervals: list[float] = []; kinds: list[str] = []
    for _ in range(runs):
        if app.page.evaluate("() => !document.getElementById('atlas-cloud-panel').hidden"):
            app.page.locator(".atlas-cloud-panel-close").click()
        app.stable("overview", 20, frames=3)
        label = app.page.evaluate(MOVING_LABEL)
        assert label, "no cloud label whose selection moves the camera"
        app.page.evaluate(FRAME_PROBE)
        app.page.mouse.click(label["x"], label["y"])
        app.wait("() => window.__atlasFrames.done === true", 10)
        stamps = app.page.evaluate("() => window.__atlasFrames.stamps")
        intervals.extend(b - a for a, b in zip(stamps, stamps[1:]))
        motion = app.page.evaluate("() => window.AtlasDebug.getMotion()")
        kinds.append(f"{motion['lastTransition']['kind']}:{motion['lastTween']['frames']}f")
    return {"transitionFrameIntervalP95Ms": round(p95(intervals), 1), "frameIntervals": summary(intervals), "transitions": kinds, "marker": "rAF timestamps from a native cloud-label click (camera tween + decorations + T5) until data-motion-state=idle; 3 transitions"}


def idle_script(app: Harness, seconds: float = 5.0) -> dict[str, Any]:
    """F1 (REPORTED, no gate): CDP ScriptDuration of the idle collection map over 5 s, once the G6 wrapper's frame gate throttled the G layer canvases."""
    if app.page.evaluate("() => !document.getElementById('atlas-cloud-panel').hidden"):
        app.page.locator(".atlas-cloud-panel-close").click()
    app.stable("overview", 20, frames=3)
    app.wait("() => window.AtlasDebug.getG6Stats().overview.idleThrottled === true", 10)
    cdp = app.page.context.new_cdp_session(app.page)
    cdp.send("Performance.enable")
    script = lambda: next(row["value"] for row in cdp.send("Performance.getMetrics")["metrics"] if row["name"] == "ScriptDuration") * 1000.0  # noqa: E731
    stats = "() => { const s = window.AtlasDebug.getG6Stats().overview; return {idleThrottled: s.idleThrottled, slowTicks: s.slowTicks, wakes: s.wakes, throttleLayers: s.throttleLayers}; }"
    before, ticks = script(), app.page.evaluate(stats)["slowTicks"]
    app.page.wait_for_timeout(seconds * 1000)
    spent, after = script() - before, app.page.evaluate(stats)
    return {"mapIdleScriptMsPer5s": round(spent * 5.0 / seconds, 1), "slowTicks": after["slowTicks"] - ticks, "throttleLayers": after["throttleLayers"], "idleThrottled": after["idleThrottled"],
            "marker": "CDP Performance.getMetrics ScriptDuration delta over 5 s of the idle default collection map (no input, panel closed, G layers throttled to ~4 ticks/s); unthrottled before F1: ~1,360-1,550 ms"}


def main() -> int:
    fixture = large_fixture()
    assert len(fixture["papers"]) == 1000 and len(fixture["concepts"]) == 1410
    digest = fixture_hash(fixture)
    cold: list[dict[str, Any]] = []
    receipt: dict[str, Any] = {"fixture": "synthetic-1000-papers-1410-concepts-12-collections", "fixtureSha256": digest, "browser": "chromium-headless", "os": platform.platform(), "viewport": "1440x1000", "pollResolutionMs": "10-30 (page.evaluate round trip)",
                               "papers": 1000, "concepts": 1410}
    try:
        for run in range(5):
            cold.append(cold_run(fixture, run + 1))
        clouds = [cloud_layout_run(fixture, run + 1) for run in range(3)]
        with running_app(fixture) as app:
            commit = collection_point_commit(app)
            frames = transition_frames(app)
            idle = idle_script(app)
        with running_app(fixture) as app:
            legacy_screen(app.page)  # warm/expand/settle drive legacy views: Advanced open + Circular ribbons (the pre-AC-11 default)
            warm = warm_runs(app, fixture)
            expand = expand_collapse(app)
            settle = g6_settle(app)
            SCREENSHOTS.mkdir(parents=True, exist_ok=True)
            shot = SCREENSHOTS / "benchmark-g6-1440-light.png"
            app.page.screenshot(path=str(shot))
            assert shot.exists() and shot.stat().st_size > 2000
            counts = app.page.evaluate("() => ({papers: window.AtlasDebug.getState().snapshot.papers.length, concepts: window.AtlasDebug.getState().snapshot.concepts.length, actual: window.AtlasDebug.getActualCounts()})")
            console_errors = list(app.errors)
    finally:
        pass
    cold_ms = [row["firstMeaningfulInteractiveMs"] for row in cold]
    interaction_tasks = [v for row in cold for v in row["interactionLongTasksMs"]] + warm["longTasksMs"] + expand["longTasksMs"]
    measured = {"coldFirstMeaningfulInteractiveMaxMs": max(cold_ms), "warmFieldP95Ms": p95(warm["fieldMs"]), "warmSelectionP95Ms": p95(warm["selectionMs"]), "expandFirstVisibleMs": expand["expandFirstVisibleMs"], "collapseMs": expand["collapseMs"], "g6SettleMs": settle["g6SettleMs"], "interactionTaskMaxMs": max(interaction_tasks or [0.0]), "cloudLayoutShapeColorMs": max(row["cloudLayoutShapeColorMs"] for row in clouds)}
    measured["collectionPointCommitMs"] = commit["collectionPointCommitMs"]
    gates = {name: {"limit": GATES_MS[name], "measured": round(value, 1), "passed": value <= GATES_MS[name]} for name, value in measured.items()}
    receipt.update({"cold": {"runs": cold, "firstMeaningfulInteractive": summary(cold_ms), "note": "cold reports max/range, not p95"}, "warm": {**warm, "field": summary(warm["fieldMs"]), "selection": summary(warm["selectionMs"])},
                    "expandCollapse": expand, "g6Settle": settle, "collectionClouds": {"runs": clouds, "gate": "max of 3 fresh pages of layoutMs + cloudShapeMs + cloudColorMs (each measured in the page with performance.now)", "note": "the map is the default screen, so its layout runs during connect: connectLongTasksMs (page lifetime incl. vendor parse) is reported, not part of interactionTaskMaxMs"}, "collectionPointCommit": commit, "transitionFrames": frames, "idleScript": idle, "reported": {"collectionPointCommitMs": commit["collectionPointCommitMs"], "collectionPointCommitBaselineMs": COMMIT_BASELINE_MS, "transitionFrameIntervalP95Ms": frames["transitionFrameIntervalP95Ms"], "cloudEdgeMsMax": max(row["cloudEdgeMs"] for row in clouds), "mapIdleScriptMsPer5s": idle["mapIdleScriptMsPer5s"]}, "interactionLongTasksMs": sorted(interaction_tasks), "taskGateScope": "longtasks observed only during interaction phases (cold click path, warm field/selection, expand/collapse); vendor parse/GC at load is reported separately as loadPhaseLongTasksMs",
                    "finalState": counts, "browserConsoleErrors": console_errors, "gates": gates, "allGatesPassed": all(row["passed"] for row in gates.values()) and not console_errors})
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps({"gates": gates, "reported": receipt["reported"], "allGatesPassed": receipt["allGatesPassed"], "receipt": str(RECEIPT.relative_to(ROOT))}, indent=2))
    return 0 if receipt["allGatesPassed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
