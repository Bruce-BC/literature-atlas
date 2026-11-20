"""Collections-mode map height (F2) on the built app, synthetic fixture only.

The collection map uses the viewport height (at least 420 px, otherwise ~100vh minus the header/toolbar/Advanced-summary
chrome); wide layouts keep the side panel's max-height equal to the map height; the stacked (<= 900 px) layout caps the map
at ~45vh while a cloud panel is open below it, and the G6 canvases follow the container without a fit, a 'Fit new content'
offer, or a busy camera.  Legacy overview modes keep clamp(320px, 42vw, 560px).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v2_support import CAMERA_IDLE, collections_fixture, open_advanced, running_app  # noqa: E402

GEO = """() => { const box = s => { const e = document.querySelector(s); if (!e || e.hidden) return null; const r = e.getBoundingClientRect(); return {top: r.top + scrollY, bottom: r.bottom + scrollY, h: r.height, w: r.width}; };
  const c = document.querySelector('#atlas-field-network'); const canvas = c.querySelector('canvas'); const panel = document.getElementById('atlas-cloud-panel');
  return {vh: innerHeight, vw: innerWidth, wrap: box('.atlas-circular-wrap'), net: box('#atlas-field-network'), panel: box('#atlas-cloud-panel'), adv: box('#atlas-advanced'), panelMax: parseFloat(getComputedStyle(panel).maxHeight),
    canvas: canvas ? [canvas.clientWidth, canvas.clientHeight] : null, camera: c.dataset.cameraState, fitNew: !document.querySelector('#atlas-fit-new-content')?.hidden, outlines: window.AtlasDebug.getCloudLayoutStats().outlines.computed}; }"""


def open_cloud(app) -> None:
    app.page.evaluate("() => document.querySelector('#atlas-cloud-overlay .atlas-cloud-label').click()")
    app.wait("() => !document.getElementById('atlas-cloud-panel').hidden", 10)
    app.wait(CAMERA_IDLE, 10)
    app.wait("() => { const c = document.querySelector('#atlas-field-network'); const v = c.querySelector('canvas'); return Math.abs(v.clientHeight - c.clientHeight) < 1; }", 10)
    app.wait(CAMERA_IDLE, 10)


@pytest.mark.parametrize("viewport", [(737, 964), (1440, 1000), (1280, 720)])
def test_collections_map_uses_the_viewport_height_and_stays_usable_with_the_panel(viewport: tuple[int, int]):
    with running_app(collections_fixture(), viewport=viewport) as app:
        page = app.page
        app.wait("() => window.AtlasDebug.getAllPaperOverview()?.grouping === 'collection'", 30)
        app.overview_ready()
        app.wait(CAMERA_IDLE, 10)
        idle = page.evaluate(GEO)
        assert idle["net"]["h"] >= 420 and idle["wrap"]["h"] == idle["net"]["h"], idle
        assert idle["adv"]["bottom"] <= idle["vh"] + 1 and idle["vh"] - idle["adv"]["bottom"] <= 40, f"the map fills the pane down to the Advanced summary: {idle}"
        assert idle["canvas"] == [round(idle["net"]["w"]), round(idle["net"]["h"])], idle
        outlines = idle["outlines"]
        open_cloud(app)
        sel = page.evaluate(GEO)
        assert sel["camera"] == "idle" and not sel["fitNew"] and sel["outlines"] == outlines, sel
        assert sel["canvas"] == [round(sel["net"]["w"]), round(sel["net"]["h"])], f"the G6 canvases follow the container: {sel}"
        if viewport[0] > 900:
            assert sel["net"]["h"] == idle["net"]["h"] and abs(sel["panelMax"] - sel["net"]["h"]) <= 1, f"wide: panel beside the map, max-height = map height: {sel}"
        else:
            assert sel["net"]["h"] <= 0.46 * sel["vh"] and sel["net"]["h"] >= 260, f"stacked: the map is capped while the panel is open: {sel}"
            assert sel["panel"]["top"] >= sel["net"]["bottom"] - 1 and sel["panel"]["top"] + 120 <= sel["vh"], f"stacked: map and panel both on screen: {sel}"
            page.locator(".atlas-cloud-panel-close").click()
            app.wait("() => document.getElementById('atlas-cloud-panel').hidden", 10)
            app.wait("h => document.querySelector('#atlas-field-network').clientHeight === h", 10, round(idle["net"]["h"]))
            app.wait(CAMERA_IDLE, 10)
            back = page.evaluate(GEO)
            assert back["canvas"] == [round(back["net"]["w"]), round(back["net"]["h"])] and not back["fitNew"], back
        assert not app.errors, app.errors[:3]


def test_legacy_overview_modes_keep_their_height():
    with running_app(collections_fixture(), viewport=(1440, 1000)) as app:
        page = app.page
        open_advanced(page)
        page.locator("#atlas-overview-renderer").select_option("ribbon")
        page.locator("#atlas-circular").wait_for(state="visible", timeout=20000)
        height = page.evaluate("() => document.querySelector('.atlas-circular-wrap').getBoundingClientRect().height")
        assert height == min(560, max(320, 0.42 * 1440)), height
