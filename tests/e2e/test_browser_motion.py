"""Collection-map transitions (plan motion-and-edges ME-9 .. ME-16) on the built app, native input, synthetic fixtures only.

IMMEDIATE COMMIT (the real state is final in the input's own task), final-state equality between a motion run and a
reduced-motion run, interruption (rapid clicks, user drag during a camera tween), the self-owned camera tween on the
dense (> 400 points) and non-dense paths, return memory, the ``capture()`` guard, T5 old-frame registration, the inert
panel ghost, and "nothing animates at idle".  ``AtlasDebug.getMotion()`` is read-only observation; no test-only switch
changes the app's motion behaviour (reduced motion is the real ``prefers-reduced-motion`` media feature, emulated).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v2_support import CAMERA_IDLE, Harness, collections_fixture, large_fixture, open_advanced, running_app  # noqa: E402

MOTION = "() => window.AtlasDebug.getMotion()"
RUNNING = "() => document.querySelector('#atlas-field-network').dataset.motionState === 'running'"
LABEL_BOXES = "() => [...document.querySelectorAll('#atlas-cloud-overlay .atlas-cloud-label')].map(b => { const r = b.getBoundingClientRect(); return {id: b.dataset.cloudId, hidden: b.hidden, x: r.x, y: r.y, w: r.width, h: r.height}; })"
# The biggest cloud (>= 3 papers, label shown) that the movement rule WILL move from the current camera (decided by the app's own pure rule).
MOVING_CLOUD = """() => { const m = window.AtlasDebug.getMotion(); const c = document.querySelector('#atlas-field-network'); const vp = {width: c.clientWidth, height: c.clientHeight};
  const shown = new Set([...document.querySelectorAll('#atlas-cloud-overlay .atlas-cloud-label')].filter(b => !b.hidden).map(b => b.dataset.cloudId));
  const rows = window.AtlasDebug.getAllPaperOverview().clouds.filter(cl => shown.has(cl.id) && cl.size >= 3).map(cl => ({id: cl.id, size: cl.size, decision: window.AtlasMotion.cloudCamera({camera: m.camera, viewport: vp, cloud: cl, zoomRange: [0.2, 3]})}));
  return rows.filter(r => r.decision.move).sort((a, b) => b.size - a.size || (a.id < b.id ? -1 : 1))[0] || null; }"""
# Everything the user-visible final state consists of (ME-11); the panel text excludes the inert cross-fade ghost.
FINAL_STATE = """() => { const v = window.AtlasDebug.getViewState(); const g = window.AtlasDebug.getOverviewGraph(); const m = window.AtlasDebug.getMotion(); const panel = document.getElementById('atlas-cloud-panel');
  return {selected: v.selected, scope: {kind: v.scope.kind, cloudId: v.scope.cloudId ?? null, subItemId: v.scope.subItemId ?? null}, selectedIds: [...window.AtlasDebug.getG6Stats().overview.selectedIds].sort(),
    dimmed: g.getNodeData().filter(n => n.data.dimmed).map(n => n.id).sort(), camera: m.camera, edges: [...document.querySelectorAll('.atlas-cloud-edges .atlas-cloud-edge')].map(e => [e.dataset.edgeId, e.dataset.tier]).sort(),
    panel: panel.hidden ? null : [...panel.children].filter(c => !c.classList.contains('atlas-motion-clone')).map(c => c.innerText).join('\\n')}; }"""
LEFTOVERS = "() => { const m = window.AtlasDebug.getMotion(); return {state: m.state, tweening: m.tweening, decorations: m.decorations, clones: m.clones, oldFrames: m.oldFrames, animations: m.animations, ghosts: document.querySelectorAll('.atlas-motion-ghost, .atlas-motion-outline, .atlas-motion-ripple, .atlas-motion-travel, .atlas-motion-mask').length, masked: document.querySelectorAll('.atlas-cloud-edge[mask], .atlas-motion-outline').length}; }"
IDLE_LEFTOVERS = {"state": "idle", "tweening": 0, "decorations": 0, "clones": 0, "oldFrames": 0, "animations": 0, "ghosts": 0, "masked": 0}
# Visible point centres (distinct papers) whose hit-test target is the map container: not under a label button, inside the map.
CLICKABLE_POINTS = """n => { const g = window.AtlasDebug.getOverviewGraph(); const c = document.querySelector('#atlas-field-network'); const r = c.getBoundingClientRect(); const seen = new Set(); const out = [];
  for (const node of g.getNodeData()) { const [x, y] = g.getClientByCanvas(g.getElementPosition(node.id)); if (x < r.left + 14 || x > r.right - 14 || y < r.top + 14 || y > Math.min(r.bottom, innerHeight) - 14) continue;
    const hit = document.elementFromPoint(x, y); const pid = node.data.paperId; if (!hit || !c.contains(hit) || seen.has(pid)) continue;
    if (g.getNodeData().some(o => o.id !== node.id && Math.hypot(...g.getClientByCanvas(g.getElementPosition(o.id)).slice(0, 2).map((v, i) => v - [x, y][i])) < 10)) continue; seen.add(pid); out.push({id: node.id, paperId: pid, x, y}); if (out.length >= n) break; }
  return out; }"""
EMPTY_SPOT = """() => { const g = window.AtlasDebug.getOverviewGraph(); const c = document.querySelector('#atlas-field-network'); const r = c.getBoundingClientRect(); const pts = g.getNodeData().map(n => g.getClientByCanvas(g.getElementPosition(n.id)));
  for (let x = r.left + 40; x < r.right - 120; x += 12) for (let y = r.top + 40; y < Math.min(r.bottom, innerHeight) - 80; y += 12) { const hit = document.elementFromPoint(x, y); if (!hit || !c.contains(hit)) continue; if (pts.every(p => Math.hypot(p[0] - x, p[1] - y) > 24)) return {x, y}; }
  return null; }"""
# Per-frame sampler during a transition: every applied camera satisfies the wrapper's pan clamp (AC-14), and the old-frame image
# (T5) shows a sample point where the live camera draws it.  Runs in rAF AFTER the tween's own callback of the same frame.
SAMPLER = """([nodeId, pre]) => { const g = window.AtlasDebug.getOverviewGraph(); const c = document.querySelector('#atlas-field-network'); const out = window.__motionSamples = {frames: 0, clampErr: 0, registered: 0, regErr: 0};
  const xs = g.getNodeData().map(n => g.getElementPosition(n.id)); const bbox = {x1: Math.min(...xs.map(p => p[0])), y1: Math.min(...xs.map(p => p[1])), x2: Math.max(...xs.map(p => p[0])), y2: Math.max(...xs.map(p => p[1]))};
  const m0 = g.getElementPosition(nodeId); const s0 = [m0[0] * pre.zoom + pre.x, m0[1] * pre.zoom + pre.y];
  const tick = () => { const m = window.AtlasDebug.getMotion(); if (m.tweening) { const cam = m.camera; const clamped = window.AtlasCameraPolicy.clampPan({viewport: {width: c.clientWidth, height: c.clientHeight}, bbox, zoom: cam.zoom, pan: {x: cam.x, y: cam.y}}); out.frames += 1; out.clampErr = Math.max(out.clampErr, Math.abs(clamped.x - cam.x), Math.abs(clamped.y - cam.y));
      const img = document.querySelector('.atlas-motion-frame'); if (img) { const rect = img.getBoundingClientRect(); const k = rect.width / img.offsetWidth; const shown = [rect.left + s0[0] * k, rect.top + s0[1] * k]; const live = g.getClientByCanvas(m0); out.registered += 1; out.regErr = Math.max(out.regErr, Math.hypot(shown[0] - live[0], shown[1] - live[1])); } }
    if (document.querySelector('#atlas-field-network').dataset.motionState !== 'idle') requestAnimationFrame(tick); else out.done = true; };
  requestAnimationFrame(tick); }"""


def open_map(app: Harness) -> None:
    app.wait("() => window.AtlasDebug.getAllPaperOverview()?.grouping === 'collection'", 30)
    app.overview_ready()
    app.wait(CAMERA_IDLE, 10)
    app.wait("() => (window.AtlasDebug.getCloudLayoutStats()?.overlay?.placements || 0) > 0", 10)
    app.reveal("overview")


def click_label(app: Harness, cloud_id: str) -> None:
    label = next(row for row in app.page.evaluate(LABEL_BOXES) if row["id"] == cloud_id and not row["hidden"])
    app.page.mouse.click(label["x"] + label["w"] / 2, label["y"] + label["h"] / 2)


def close(a: dict[str, float], b: dict[str, float], tolerance: float = 0.5) -> bool:
    return abs(a["zoom"] - b["zoom"]) <= 1e-6 * max(1.0, abs(b["zoom"])) and abs(a["x"] - b["x"]) <= tolerance and abs(a["y"] - b["y"]) <= tolerance


def idle(app: Harness) -> dict[str, Any]:
    app.wait(CAMERA_IDLE, 10)
    return app.page.evaluate(LEFTOVERS)


# ---- ME-9: immediate commit ----------------------------------------------------------------------------------------
@pytest.mark.parametrize("reduced", [False, True])
def test_immediate_commit_view_state_panel_and_focus_are_final_in_the_input_task(reduced: bool):
    with running_app(collections_fixture()) as app:
        page = app.page
        if reduced:
            page.emulate_media(reduced_motion="reduce")
        open_map(app)
        cloud = page.evaluate(MOVING_CLOUD)["id"]
        # label click (keyboard-equivalent button activation) and the reads happen in ONE task: nothing may be deferred to a later frame
        now = page.evaluate("""id => { const b = [...document.querySelectorAll('#atlas-cloud-overlay .atlas-cloud-label')].find(x => x.dataset.cloudId === id); b.click();
          const v = window.AtlasDebug.getViewState(); const p = document.getElementById('atlas-cloud-panel'); return {scope: v.scope.kind, cloudId: v.scope.cloudId, selected: v.selected, panelHidden: p.hidden, panelCloud: p.dataset.cloudId,
            title: document.getElementById('atlas-cloud-panel-title')?.textContent ?? null, overlay: document.querySelector('#atlas-cloud-overlay').dataset.selectedCloud, motion: document.querySelector('#atlas-field-network').dataset.motionState,
            dimmedOutside: window.AtlasDebug.getAllPaperOverview().points.filter(q => q.cloudId !== id).every(q => q.dimmed)}; }""", cloud)
        label = next(c["label"] for c in page.evaluate("() => window.AtlasDebug.getAllPaperOverview().clouds") if c["id"] == cloud)
        assert now["scope"] == "cloud" and now["cloudId"] == cloud and now["selected"] is None and now["overlay"] == cloud and now["dimmedOutside"], now
        assert not now["panelHidden"] and now["panelCloud"] == cloud and now["title"] == label, f"the panel is final in the click's own task: {now}"
        assert now["motion"] == "running", "running is set before the first dispatch (reduced motion too: until the update queue is idle)"
        app.wait(CAMERA_IDLE, 10)
        # panel-list paper: selection, detail text and focus are final in the same task
        target = page.evaluate("() => document.querySelector('#atlas-cloud-panel .atlas-cloud-paper').dataset.paperId")
        title = next(p["title"] for p in app.fixture["papers"] if p["id"] == target)
        now = page.evaluate("""id => { document.querySelector(`#atlas-cloud-panel .atlas-cloud-paper[data-paper-id="${CSS.escape(id)}"]`).click(); const v = window.AtlasDebug.getViewState();
          const live = [...document.querySelectorAll('.atlas-cloud-panel-detail h3')].filter(h => !h.closest('[inert]')); const a = document.activeElement;
          return {selected: v.selected, detail: live.map(h => h.textContent), focusInDetail: Boolean(a?.closest('.atlas-cloud-panel-detail')) && !a.closest('[inert]'), motion: document.querySelector('#atlas-field-network').dataset.motionState}; }""", target)
        assert now["selected"] == {"kind": "paper", "id": target} and now["detail"] == [title] and now["focusInDetail"], now
        assert now["motion"] == "running" or reduced, "a decorated transition is running; under reduced motion idle only waits for the update queue (none needed here)"
        app.wait(CAMERA_IDLE, 10)
        # native point click: no wait at all between the click and the read
        point = page.evaluate(CLICKABLE_POINTS, 40)
        point = next(p for p in point if p["paperId"] != target)
        page.mouse.click(point["x"], point["y"])
        now = page.evaluate("() => { const v = window.AtlasDebug.getViewState(); const live = [...document.querySelectorAll('.atlas-cloud-panel-detail h3')].filter(h => !h.closest('[inert]')); return {selected: v.selected, detail: live.map(h => h.textContent), pointId: window.AtlasDebug.getAllPaperOverview().edgeState.pointId}; }")
        title = next(p["title"] for p in app.fixture["papers"] if p["id"] == point["paperId"])
        assert now["selected"] == {"kind": "paper", "id": point["paperId"]} and now["detail"] == [title] and now["pointId"] == point["id"], now
        assert idle(app) == IDLE_LEFTOVERS
        assert not app.errors, app.errors[:3]


# ---- ME-11 / ME-13: final-state equality between motion and reduced motion ----------------------------------------------
def scripted_session(reduced: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    with running_app(collections_fixture()) as app:
        page = app.page
        if reduced:
            page.emulate_media(reduced_motion="reduce")
        open_map(app)
        assert page.evaluate("() => matchMedia('(prefers-reduced-motion: reduce)').matches") == reduced
        states: list[dict[str, Any]] = []
        counters: dict[str, Any] = {"animationsAfterInput": [], "frames": []}

        def after_input() -> None:
            counters["animationsAfterInput"].append(page.evaluate("() => document.querySelector('.atlas-map-row').getAnimations({subtree: true}).length"))
            app.wait(CAMERA_IDLE, 10)
            states.append(page.evaluate(FINAL_STATE))
            counters["frames"].append(page.evaluate(MOTION)["tweenFrames"])

        cloud = page.evaluate(MOVING_CLOUD)["id"]
        click_label(app, cloud); after_input()                                  # cloud (camera auto-move)
        nodes = [n for n in app.overview_nodes() if n["data"]["cloudId"] == cloud]
        app.click_node("overview", sorted(n["id"] for n in nodes)[0]); after_input()  # paper on the map (no camera move)
        rows = page.locator("#atlas-cloud-panel .atlas-cloud-paper:not([aria-pressed='true'])")
        rows.first.click(); after_input()                                        # paper from the panel list
        page.locator(".atlas-cloud-subitem").first.click(); after_input()       # sub-item
        page.locator(".atlas-cloud-panel-close").click(); after_input()          # close: camera returns
        counters["errors"] = list(app.errors)
        return states, counters


def test_final_state_is_identical_with_motion_and_with_reduced_motion():
    motion_states, motion = scripted_session(False)
    reduced_states, reduced = scripted_session(True)
    assert not motion["errors"] and not reduced["errors"], (motion["errors"][:2], reduced["errors"][:2])
    assert len(motion_states) == len(reduced_states) == 5
    for step, (a, b) in enumerate(zip(motion_states, reduced_states)):
        assert close(a["camera"], b["camera"]), f"step {step}: camera {a['camera']} vs reduced {b['camera']}"
        for key in ("selected", "scope", "selectedIds", "dimmed", "edges", "panel"):
            assert a[key] == b[key], f"step {step}: {key} differs between motion and reduced motion"
    assert motion_states[0]["camera"] != motion_states[-1]["camera"] and close(motion_states[-1]["camera"], reduced_states[-1]["camera"])
    # ME-13: reduced motion runs no script animation and no tween frame; the motion run really animated
    assert reduced["animationsAfterInput"] == [0] * 5 and reduced["frames"] == [0] * 5, reduced
    assert motion["frames"][0] >= 2 and any(n > 0 for n in motion["animationsAfterInput"]), motion


# ---- ME-12: interruption -----------------------------------------------------------------------------------------------
def test_five_rapid_point_clicks_last_wins_without_leftovers():
    with running_app(collections_fixture()) as app:
        page = app.page
        open_map(app)
        points = page.evaluate(CLICKABLE_POINTS, 5)
        assert len(points) == 5 and len({p["paperId"] for p in points}) == 5
        before = page.evaluate(MOTION)["stats"]["interrupted"]
        for point in points:
            page.mouse.click(point["x"], point["y"])
            page.wait_for_timeout(100)
        assert idle(app) == IDLE_LEFTOVERS, "zero decorations, ghosts, panel ghosts and old-frame images; idle reached"
        assert app.view()["selected"] == {"kind": "paper", "id": points[-1]["paperId"]}, "the last click wins"
        assert page.evaluate("() => window.AtlasDebug.getAllPaperOverview().edgeState.pointId") == points[-1]["id"]
        assert page.evaluate(MOTION)["stats"]["interrupted"] - before >= 3, "each new input removed the in-flight transition at once"
        assert not app.errors, app.errors[:3]


def test_user_drag_during_a_camera_tween_cancels_it_and_the_drag_takes_effect():
    with running_app(collections_fixture()) as app:
        page = app.page
        open_map(app)
        spot = page.evaluate(EMPTY_SPOT)
        assert spot, "an empty map spot to start the drag"
        cloud = page.evaluate(MOVING_CLOUD)["id"]
        click_label(app, cloud)
        app.wait("() => window.AtlasDebug.getMotion().tweenFrames >= 2", 5)  # the tween is really moving the camera
        page.mouse.move(spot["x"], spot["y"]); page.mouse.down()
        stopped = page.evaluate(MOTION)
        assert stopped["tweening"] == 0 and stopped["lastTween"]["state"] == "stop" and stopped["oldFrames"] == 0, "pointerdown stops the tween synchronously; the old frame (no longer registered) is removed"
        at_stop = stopped["lastTween"]["camera"]
        page.mouse.move(spot["x"] + 6, spot["y"] + 4, steps=2); page.mouse.move(spot["x"] + 60, spot["y"] + 35, steps=14); page.mouse.up()
        after = idle(app)
        assert after == IDLE_LEFTOVERS
        motion = page.evaluate(MOTION)
        dx, dy = motion["camera"]["x"] - at_stop["x"], motion["camera"]["y"] - at_stop["y"]
        assert abs(dx) + abs(dy) > 8 and motion["camera"]["zoom"] == at_stop["zoom"], f"the drag pans from where the tween stopped ({dx}, {dy})"
        assert motion["lastTween"]["frames"] == stopped["lastTween"]["frames"], "no tween frame after the stop"
        assert motion["memory"] == {"home": None, "auto": None} and close(motion["captured"], motion["camera"]), "the user owns the camera: capture() returns the live camera"
        assert page.locator("#atlas-cloud-panel").get_attribute("data-cloud-id") == cloud, "the committed selection is untouched"
        assert not app.errors, app.errors[:3]


# ---- ME-15: camera tween on the dense and the non-dense path, return memory, capture() guard, AC-14, T5 ------------------
@pytest.mark.parametrize("which", ["non-dense", "dense"])
def test_camera_tween_point_click_return_capture_guard_mode_switch_and_pan_clamp(which: str):
    fixture = collections_fixture() if which == "non-dense" else large_fixture()
    with running_app(fixture) as app:
        page = app.page
        open_map(app)
        points = page.evaluate("() => window.AtlasDebug.getOverviewGraph().getNodeData().length")
        assert (points > 400) == (which == "dense"), f"{which} fixture has {points} points"
        pre = page.evaluate(MOTION)["camera"]
        row = page.evaluate(MOVING_CLOUD)
        assert row, "a cloud the rule moves"
        sample = sorted(n["id"] for n in app.overview_nodes() if n["data"]["cloudId"] == row["id"])[0]
        click_label(app, row["id"])
        page.evaluate(SAMPLER, [sample, pre])
        app.wait("() => window.__motionSamples?.done === true", 10)
        samples = page.evaluate("() => window.__motionSamples")
        assert idle(app) == IDLE_LEFTOVERS
        motion = page.evaluate(MOTION)
        target = motion["lastTransition"]["camera"]["target"]
        assert motion["lastTransition"]["kind"] == "cloud" and motion["lastTransition"]["camera"]["move"] and motion["lastTween"]["state"] == "done"
        assert motion["lastTween"]["frames"] >= 2, f"a real multi-frame tween: {motion['lastTween']}"
        assert close(motion["camera"], target), f"the tween reaches the (clamped) target {motion['camera']} vs {target}"
        assert samples["frames"] >= 2 and samples["clampErr"] <= 0.5, f"AC-14 holds on every tween frame {samples}"
        assert samples["registered"] >= 2 and samples["regErr"] <= 1.0, f"T5: the old frame stays registered within 1 px during the tween {samples}"
        assert close(motion["captured"], pre), "capture() never persists the untouched auto-moved camera"
        # a map point click never moves the camera (it also selects its cloud)
        app.click_node("overview", sample)
        app.wait("id => window.AtlasDebug.getViewState()?.selected?.id === id", 10, next(n["data"]["paperId"] for n in app.overview_nodes() if n["id"] == sample))
        assert idle(app) == IDLE_LEFTOVERS
        moved = page.evaluate(MOTION)
        assert moved["lastTransition"]["kind"] == "paper-map" and moved["lastTransition"]["camera"]["reason"] == "point-click" and close(moved["camera"], motion["camera"], 0.01)
        # deselect returns to the pre-move camera (the user did not move since)
        page.locator(".atlas-cloud-panel-close").click()
        assert idle(app) == IDLE_LEFTOVERS
        back = page.evaluate(MOTION)
        assert back["lastTransition"]["kind"] == "deselect" and back["lastTransition"]["camera"]["returning"] and close(back["camera"], pre), f"return {back['camera']} vs {pre}"
        clamped = page.evaluate("cam => { const g = window.AtlasDebug.getOverviewGraph(); const c = document.querySelector('#atlas-field-network'); const xs = g.getNodeData().map(n => g.getElementPosition(n.id)); return window.AtlasCameraPolicy.clampPan({viewport: {width: c.clientWidth, height: c.clientHeight}, bbox: {x1: Math.min(...xs.map(p => p[0])), y1: Math.min(...xs.map(p => p[1])), x2: Math.max(...xs.map(p => p[0])), y2: Math.max(...xs.map(p => p[1]))}, zoom: cam.zoom, pan: {x: cam.x, y: cam.y}}); }", back["camera"])
        assert abs(clamped["x"] - back["camera"]["x"]) <= 0.5 and abs(clamped["y"] - back["camera"]["y"]) <= 0.5, "AC-14 after the return"
        # an auto-moved camera is not restored after switching the overview mode and back
        click_label(app, row["id"]); idle(app)
        auto = page.evaluate(MOTION)["camera"]
        assert not close(auto, pre)
        open_advanced(page)
        page.locator("#atlas-overview-renderer").select_option("all-papers")
        app.wait("() => window.AtlasDebug.getState().overviewRenderer === 'all-papers' && document.querySelector('#atlas-field-network').dataset.rendererState === 'ready'", 30)
        page.locator("#atlas-overview-renderer").select_option("collections")
        open_map(app)
        restored = page.evaluate(MOTION)["camera"]
        assert close(restored, pre) and not close(restored, auto), f"restored {restored}, pre-move {pre}, auto-moved {auto}"
        assert not app.errors, app.errors[:3]


# ---- ME-16 / ME-14: keyboard, inert id-less panel ghost, nothing animating at idle --------------------------------------
def test_keyboard_selection_same_transition_immediate_focus_and_inert_idless_panel_ghost():
    with running_app(collections_fixture()) as app:
        page = app.page
        open_map(app)
        cloud = page.evaluate(MOVING_CLOUD)["id"]
        page.locator(f'#atlas-cloud-overlay .atlas-cloud-label[data-cloud-id="{cloud}"]').focus()
        page.keyboard.press("Space")
        first = page.evaluate(MOTION)["lastTransition"]
        assert first["kind"] == "cloud" and first["source"] == "label" and first["camera"]["move"], "keyboard activation of a label = the label transition"
        assert idle(app) == IDLE_LEFTOVERS
        row = page.locator("#atlas-cloud-panel .atlas-cloud-paper").first
        paper = row.get_attribute("data-paper-id")
        row.focus(); page.keyboard.press("Enter")
        ghost = page.evaluate("""() => { const g = document.querySelector('#atlas-cloud-panel .atlas-motion-clone'); const a = document.activeElement; if (!g) return null;
          return {inert: g.hasAttribute('inert'), ariaHidden: g.getAttribute('aria-hidden'), pointerEvents: getComputedStyle(g).pointerEvents, ids: g.querySelectorAll('[id]').length + (g.id ? 1 : 0), focusKeys: g.querySelectorAll('[data-focus-key], [tabindex], [aria-live]').length,
            titleIds: document.querySelectorAll('#atlas-cloud-panel-title').length, focusInLive: Boolean(a?.closest('.atlas-cloud-panel-detail')) && !a.closest('.atlas-motion-clone'), kind: window.AtlasDebug.getMotion().lastTransition.kind}; }""")
        assert ghost, "the previous panel content cross-fades out"
        assert ghost == {"inert": True, "ariaHidden": "true", "pointerEvents": "none", "ids": 0, "focusKeys": 0, "titleIds": 1, "focusInLive": True, "kind": "paper-panel"}, ghost
        assert app.view()["selected"] == {"kind": "paper", "id": paper}
        assert idle(app) == IDLE_LEFTOVERS, "at idle: no running animation in the overlay/panel subtree, tween counter 0, ghost gone"
        assert page.evaluate("() => document.querySelector('.atlas-map-row').getAnimations({subtree: true}).length") == 0
        page.wait_for_timeout(300)
        assert page.evaluate(LEFTOVERS) == IDLE_LEFTOVERS, "nothing starts animating while idle"
        assert not app.errors, app.errors[:3]
