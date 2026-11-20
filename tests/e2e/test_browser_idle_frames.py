"""Idle frame budget of the G6 map (F1) on the built app, native input, synthetic fixtures only.

AntV G re-renders every layer canvas on every animation frame.  The G6 wrapper hands each layer canvas of its own graph a
per-instance frame scheduler: after a short idle period the layers tick at ~4/s, and any input or wrapper update returns
them to real animation frames at once.  This checks the idle script budget (CDP ``Performance.getMetrics``), that the
throttle really engaged on every layer, that hover input is drawn on the next real frame, and that a click after a 2 s idle
still commits in its own task.  The threshold is deliberately generous (measured ~125 ms per 5 s; unthrottled ~1,400 ms).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v2_support import CAMERA_IDLE, large_fixture, running_app  # noqa: E402

IDLE_BUDGET_MS = 400.0  # script time per 5 s of idle map
STATS = "() => { const s = window.AtlasDebug.getG6Stats().overview; return {idleThrottled: s.idleThrottled, slowTicks: s.slowTicks, wakes: s.wakes, lastWakeMs: s.lastWakeMs, throttleLayers: s.throttleLayers}; }"
G6_LAYERS = 4  # G6 5.1.1 multi-layer canvas: background, main, label, transient
POINTS = """n => { const g = window.AtlasDebug.getOverviewGraph(); const c = document.querySelector('#atlas-field-network'); const r = c.getBoundingClientRect(); const out = [];
  for (const node of g.getNodeData()) { const [x, y] = g.getClientByCanvas(g.getElementPosition(node.id)); if (x < r.left + 14 || x > r.right - 14 || y < r.top + 14 || y > Math.min(r.bottom, innerHeight) - 14) continue;
    const hit = document.elementFromPoint(x, y); if (!hit || !c.contains(hit)) continue;
    if (g.getNodeData().some(o => o.id !== node.id && Math.hypot(...g.getClientByCanvas(g.getElementPosition(o.id)).slice(0, 2).map((v, i) => v - [x, y][i])) < 10)) continue; out.push({id: node.id, paperId: node.data.paperId, x, y}); if (out.length >= n) break; }
  return out; }"""


def script_ms(cdp) -> float:
    metrics = {row["name"]: row["value"] for row in cdp.send("Performance.getMetrics")["metrics"]}
    return metrics["ScriptDuration"] * 1000.0


def test_idle_map_throttles_g_layer_ticks_and_wakes_on_the_first_frame_after_input():
    with running_app(large_fixture()) as app:
        page = app.page
        app.wait("() => window.AtlasDebug.getAllPaperOverview()?.grouping === 'collection'", 30)
        app.overview_ready()
        app.wait(CAMERA_IDLE, 10)
        app.reveal("overview")
        app.wait(CAMERA_IDLE, 10)
        assert page.evaluate(STATS)["throttleLayers"] == G6_LAYERS, "every G layer canvas of the map uses the wrapper's frame gate"
        cdp = page.context.new_cdp_session(page)
        cdp.send("Performance.enable")
        app.wait("() => window.AtlasDebug.getG6Stats().overview.idleThrottled === true", 10)
        before, ticks = script_ms(cdp), page.evaluate(STATS)["slowTicks"]
        page.wait_for_timeout(5000)
        spent, after = script_ms(cdp) - before, page.evaluate(STATS)
        assert spent <= IDLE_BUDGET_MS, f"idle map spent {spent:.0f} ms of script per 5 s (budget {IDLE_BUDGET_MS:.0f})"
        assert after["idleThrottled"] and 5 <= after["slowTicks"] - ticks <= 22, f"~4 slow ticks per second while idle: {after} from {ticks}"
        # hover after idle: the woken layers render on the next real frame, not on the slow timer
        point = page.evaluate(POINTS, 1)[0]
        wakes = page.evaluate(STATS)["wakes"]
        page.mouse.move(point["x"], point["y"])
        woke = app.wait("w => { const s = window.AtlasDebug.getG6Stats().overview; return s.wakes > w && s.lastWakeMs !== null && s; }", 5, wakes)
        assert woke["lastWakeMs"] < 100, f"first woken frame {woke['lastWakeMs']:.0f} ms after input (slow tick is 250 ms)"
        # a click after a 2 s idle still commits in its own task
        app.wait(CAMERA_IDLE, 10)
        page.wait_for_timeout(2000)
        assert page.evaluate(STATS)["idleThrottled"], "throttled again after the idle delay"
        page.mouse.click(point["x"], point["y"])
        selected = page.evaluate("() => window.AtlasDebug.getViewState().selected")
        assert selected == {"kind": "paper", "id": point["paperId"]}, selected
        app.wait(CAMERA_IDLE, 10)
        assert not app.errors, app.errors
