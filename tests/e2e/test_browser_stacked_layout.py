"""Collection map after container resizes and in the stacked layout (B1-B3), built app, native input, synthetic fixtures only.

B1: after the map shrinks (stacked layout, panel open), grows back (panel closed) or the viewport changes (wide layout), the G6
points and the SVG overlay agree (point centroid vs hull bbox centre per cloud with >= 8 points, unchanged by the resize) and the
content keeps its top-left anchor; checked at dpr 2.  B2: in the stacked layout, opening a cloud, a sub-group click and a panel
paper click leave the map row at the top of the viewport and the whole map on screen.  B3: 'N in viewport' follows a pan.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v2_support import CAMERA_IDLE, collections_fixture, running_app  # noqa: E402

# per cloud with >= 8 points: centroid of the G6 point centres (client px) minus the centre of its hull path's client bbox
ALIGN = """() => { const g = window.AtlasDebug.getOverviewGraph(); const pts = window.AtlasDebug.getAllPaperOverview().points; const by = new Map();
  for (const p of pts) (by.get(p.cloudId) || by.set(p.cloudId, []).get(p.cloudId)).push(p.id); const out = {}; const box = document.querySelector('#atlas-field-network').getBoundingClientRect();
  for (const [id, ids] of by) { if (ids.length < 8) continue; const hull = [...document.querySelectorAll('g.atlas-cloud')].find(n => n.dataset.cloudId === id)?.querySelector('.atlas-cloud-hull'); if (!hull) continue;
    const r = hull.getBoundingClientRect(); let x = 0, y = 0; for (const pid of ids) { const c = g.getClientByCanvas(g.getElementPosition(pid)); x += c[0]; y += c[1]; }
    out[id] = {dx: x / ids.length - (r.left + r.width / 2), dy: y / ids.length - (r.top + r.height / 2), cx: x / ids.length - box.left, cy: y / ids.length - box.top, size: Math.max(r.width, r.height)}; }
  return out; }"""
STILL = """([w, h]) => { const m = window.AtlasDebug.getMotion(); const labels = new Set([...document.querySelectorAll('#atlas-cloud-overlay .atlas-cloud-label')].filter(b => !b.hidden).map(b => b.dataset.cloudId));
  const big = new Set(Object.keys((%s)()));
  const row = window.AtlasDebug.getAllPaperOverview().clouds.filter(c => labels.has(c.id) && big.has(c.id) && !window.AtlasMotion.cloudCamera({camera: m.camera, viewport: {width: w, height: h}, cloud: c, zoomRange: [0.2, 3]}).move)[0];
  return row ? row.id : null; }""" % ALIGN
MAP_ON_SCREEN = "() => { const r = document.querySelector('#atlas-field-network').getBoundingClientRect(); return {top: r.top, bottom: r.bottom, vh: innerHeight}; }"
RESIZES = "() => window.AtlasDebug.getG6Stats().overview.containerResizes"


def assert_aligned(before: dict, after: dict, label: str, tolerance: float = 2.5) -> None:
    assert before and set(before) == set(after), (label, before, after)
    for cloud, row in after.items():
        start = before[cloud]
        assert abs(row["dx"] - start["dx"]) <= tolerance and abs(row["dy"] - start["dy"]) <= tolerance, f"{label}: overlay and points disagree for {cloud}: {start} -> {row}"
        assert abs(row["dx"]) <= max(8.0, 0.25 * row["size"]) and abs(row["dy"]) <= max(8.0, 0.25 * row["size"]), f"{label}: centroid far from its hull for {cloud}: {row}"


def open_map(app) -> None:
    app.wait("() => window.AtlasDebug.getAllPaperOverview()?.grouping === 'collection'", 30)
    app.overview_ready()
    app.wait(CAMERA_IDLE, 10)


def test_stacked_shrink_and_regrow_keep_points_and_overlay_aligned_without_a_jump_at_dpr2():
    with running_app(collections_fixture(), viewport=(737, 964), device_scale_factor=2) as app:
        page = app.page
        open_map(app)
        start = page.evaluate(ALIGN)
        assert len(start) >= 2, start
        cloud = page.evaluate(STILL, [737, round(0.45 * 964)])
        assert cloud, "a cloud that is already visible in the shrunk map (no camera move)"
        resizes = page.evaluate(RESIZES)
        page.evaluate("id => [...document.querySelectorAll('#atlas-cloud-overlay .atlas-cloud-label')].find(b => b.dataset.cloudId === id).click()", cloud)
        app.wait("n => window.AtlasDebug.getG6Stats().overview.containerResizes > n", 10, resizes)
        app.wait(CAMERA_IDLE, 10)
        motion = page.evaluate("() => window.AtlasDebug.getMotion()")
        assert motion["lastTransition"]["camera"]["move"] is False, f"precondition: no camera tween resyncs the overlay {motion['lastTransition']}"
        shrunk = page.evaluate(ALIGN)
        assert_aligned(start, shrunk, "after the stacked shrink")
        assert abs(shrunk[cloud]["cx"] - start[cloud]["cx"]) <= 3 and abs(shrunk[cloud]["cy"] - start[cloud]["cy"]) <= 3, f"the clicked cloud stays where it was: {start[cloud]} -> {shrunk[cloud]}"
        page.evaluate("() => document.querySelector('.atlas-cloud-panel-close').click()")
        app.wait("n => window.AtlasDebug.getG6Stats().overview.containerResizes > n", 10, resizes + 1)
        app.wait(CAMERA_IDLE, 10)
        assert_aligned(start, page.evaluate(ALIGN), "after the panel closed (map grows back)")
        assert not app.errors, app.errors[:3]


def test_wide_viewport_resize_keeps_points_and_overlay_aligned():
    with running_app(collections_fixture(), viewport=(1440, 1000)) as app:
        page = app.page
        open_map(app)
        start = page.evaluate(ALIGN)
        resizes = page.evaluate(RESIZES)
        page.set_viewport_size({"width": 1300, "height": 820})
        app.wait("n => window.AtlasDebug.getG6Stats().overview.containerResizes > n", 10, resizes)
        app.wait(CAMERA_IDLE, 10)
        assert_aligned(start, page.evaluate(ALIGN), "after a wide viewport resize")
        assert not app.errors, app.errors[:3]


def test_stacked_layout_keeps_the_map_row_at_the_top_after_cloud_subgroup_and_paper_clicks():
    with running_app(collections_fixture(), viewport=(737, 964)) as app:
        page = app.page
        open_map(app)

        def check(step: str) -> None:
            app.wait(CAMERA_IDLE, 10)
            box = page.evaluate(MAP_ON_SCREEN)
            assert 0 <= box["top"] <= 8 and box["bottom"] <= box["vh"], f"{step}: the map must stay at the top and fully visible {box}"
            panel = page.evaluate("() => { const chips = document.getElementById('atlas-cloud-chips'); return {top: document.getElementById('atlas-cloud-panel').getBoundingClientRect().top, chips: chips && !chips.hidden ? chips.getBoundingClientRect().bottom : null}; }")
            above = max(box["bottom"], panel["chips"] or 0)
            assert abs(panel["top"] - above) <= 2, f"{step}: the panel starts right under the map (and its cloud-list chips): {panel} vs {box}"

        label = page.locator("#atlas-cloud-overlay .atlas-cloud-label:not([hidden])").first
        label.click()
        app.wait("() => !document.getElementById('atlas-cloud-panel').hidden", 10)
        check("cloud click")
        assert page.evaluate("() => Boolean(document.activeElement?.closest('#atlas-cloud-panel')) || document.activeElement?.classList.contains('atlas-cloud-label')")
        page.locator("#atlas-cloud-panel .atlas-cloud-subitem").first.click()
        app.wait("() => Boolean(window.AtlasDebug.getViewState()?.scope?.subItemId)", 10)
        check("sub-group click")
        paper = page.locator("#atlas-cloud-panel .atlas-cloud-paper").first
        paper_id = paper.get_attribute("data-paper-id")
        paper.click()
        app.wait("id => window.AtlasDebug.getViewState()?.selected?.id === id", 10, paper_id)
        check("paper click")
        assert page.evaluate("() => document.activeElement?.closest('.atlas-cloud-panel-detail') !== null"), "focus moved to the detail without moving the map"
        assert not app.errors, app.errors[:3]


def test_viewport_counter_follows_a_pan():
    with running_app(collections_fixture(), viewport=(1440, 1000)) as app:
        page = app.page
        open_map(app)
        app.reveal("overview")
        before = int(page.evaluate("() => document.querySelector('#atlas-field-network').dataset.viewportPoints"))
        box = page.locator("#atlas-field-network").bounding_box()
        x, y = box["x"] + 20, box["y"] + box["height"] / 2
        page.mouse.move(x, y); page.mouse.down(); page.mouse.move(x + 8, y, steps=2); page.mouse.move(x + box["width"] * 0.8, y, steps=20); page.mouse.up()
        app.wait(CAMERA_IDLE, 10)
        expected = page.evaluate("""() => { const g = window.AtlasDebug.getOverviewGraph(); const r = document.querySelector('#atlas-field-network').getBoundingClientRect(); const ids = new Set();
          for (const n of g.getNodeData()) { const [x, y] = g.getClientByCanvas(g.getElementPosition(n.id)); if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) ids.add(n.data?.paperId ?? n.id); } return ids.size; }""")
        assert expected < before, f"the drag must move points out of the map ({before} -> {expected})"
        app.wait("n => document.querySelector('#atlas-field-network').dataset.viewportPoints === String(n) && document.getElementById('atlas-overview-counters')?.textContent.includes(`${n} in viewport`)", 5, expected)
        assert not app.errors, app.errors[:3]
