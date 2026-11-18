"""Cold/warm native-browser benchmark for taxonomy v2 (synthetic 1,000 papers / 1,410 concepts).

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
from v2_support import OPT, OT, Harness, fixture_hash, large_fixture, running_app  # noqa: E402

RECEIPT = ROOT / ".omx" / "context" / "taxonomy-v2-acceptance-performance.json"
SCREENSHOTS = ROOT / ".omx" / "context" / "taxonomy-v2-browser-screenshots"
GATES_MS = {"coldFirstMeaningfulInteractiveMaxMs": 2500, "warmFieldP95Ms": 500, "warmSelectionP95Ms": 500, "expandFirstVisibleMs": 1500, "collapseMs": 500, "g6SettleMs": 10000, "interactionTaskMaxMs": 200}
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
    with running_app(fixture) as app:
        # vendor parse/GC phase is measured separately from interaction
        load_tasks = app.page.evaluate("() => window.__atlasLoadLongTasks ?? null")
        app.page.evaluate(LONGTASK_OBSERVER)
        mark = now_ms(app)  # buffered load-phase tasks start before this mark and are reported separately
        t0 = time.monotonic()
        app.page.locator("#atlas-overview-renderer").select_option("all-papers")
        app.wait("() => document.querySelector('#atlas-field-network')?.dataset.rendererState === 'ready'", 30)
        mounted = time.monotonic() - t0
        app.wait("n => window.AtlasDebug.getOverviewGraph()?.getNodeData().length === n", 30, len(fixture["papers"]))
        registered = time.monotonic() - t0
        registered_count = app.page.evaluate("() => window.AtlasDebug.getOverviewGraph().getNodeData().length")
        assert registered_count == len(fixture["papers"]) == 1000
        node = app.overview_nodes()[500]
        point = app.client_point("overview", node["id"])
        box = app.page.locator("#atlas-field-network").bounding_box()
        assert box["x"] <= point["x"] <= box["x"] + box["width"] and box["y"] <= point["y"] <= box["y"] + box["height"], "hit-test target is outside the canvas"
        hit = time.monotonic() - t0
        # the point can move while the first fit settles: re-read right before the native click
        wait_g6_quiet(app, ["#atlas-field-network"], 20)
        app.stable("overview", 20, frames=3)
        point = app.client_point("overview", node["id"])
        assert app.pixel_painted("overview", point["x"], point["y"]), "no drawn pixels at the registered point"
        paper_id = node["data"]["paperId"]
        app.page.mouse.click(point["x"], point["y"])
        app.wait("id => window.AtlasDebug.getViewState()?.selected?.id === id", 10, paper_id)
        title = next(p["title"] for p in fixture["papers"] if p["id"] == paper_id)
        app.wait("t => document.querySelector('.atlas-selection-detail h3')?.textContent === t", 10, title)
        total = time.monotonic() - t0
        view = app.view()
        lower = app.page.evaluate("() => window.AtlasDebug.getLowerProjection().revision")
        assert lower == view["revision"]
        return {"run": run, "mountedMs": round(mounted * 1000, 1), "registeredMs": round(registered * 1000, 1), "hitTestReadyMs": round(hit * 1000, 1), "firstMeaningfulInteractiveMs": round(total * 1000, 1),
                "registeredPoints": registered_count, "nativeHitTest": True, "detailSelected": True, "interactionLongTasksMs": [round(v, 1) for v in long_tasks_since(app, mark)],
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


def main() -> int:
    fixture = large_fixture()
    assert len(fixture["papers"]) == 1000 and len(fixture["concepts"]) == 1410
    digest = fixture_hash(fixture)
    cold: list[dict[str, Any]] = []
    receipt: dict[str, Any] = {"fixture": "synthetic-1000-papers-1410-concepts", "fixtureSha256": digest, "browser": "chromium-headless", "os": platform.platform(), "viewport": "1440x1000", "pollResolutionMs": "10-30 (page.evaluate round trip)",
                               "papers": 1000, "concepts": 1410}
    try:
        for run in range(5):
            cold.append(cold_run(fixture, run + 1))
        with running_app(fixture) as app:
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
    measured = {"coldFirstMeaningfulInteractiveMaxMs": max(cold_ms), "warmFieldP95Ms": p95(warm["fieldMs"]), "warmSelectionP95Ms": p95(warm["selectionMs"]), "expandFirstVisibleMs": expand["expandFirstVisibleMs"], "collapseMs": expand["collapseMs"], "g6SettleMs": settle["g6SettleMs"], "interactionTaskMaxMs": max(interaction_tasks or [0.0])}
    gates = {name: {"limit": GATES_MS[name], "measured": round(value, 1), "passed": value <= GATES_MS[name]} for name, value in measured.items()}
    receipt.update({"cold": {"runs": cold, "firstMeaningfulInteractive": summary(cold_ms), "note": "cold reports max/range, not p95"}, "warm": {**warm, "field": summary(warm["fieldMs"]), "selection": summary(warm["selectionMs"])},
                    "expandCollapse": expand, "g6Settle": settle, "interactionLongTasksMs": sorted(interaction_tasks), "taskGateScope": "longtasks observed only during interaction phases (cold click path, warm field/selection, expand/collapse); vendor parse/GC at load is reported separately as loadPhaseLongTasksMs",
                    "finalState": counts, "browserConsoleErrors": console_errors, "gates": gates, "allGatesPassed": all(row["passed"] for row in gates.values()) and not console_errors})
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps({"gates": gates, "allGatesPassed": receipt["allGatesPassed"], "receipt": str(RECEIPT.relative_to(ROOT))}, indent=2))
    return 0 if receipt["allGatesPassed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
