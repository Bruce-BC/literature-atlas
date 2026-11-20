"""Cloud-aesthetics screenshot matrix (plan cloud-aesthetics AE-12) on the synthetic 1,000-paper fixture.

Usage: ``npm run build && python3 scripts/cloud_screenshot_matrix.py <output-dir>``

Writes light/dark x 320/720/1440 px default-screen shots plus one zoomed-in shot with sub-group outlines
into ``<output-dir>`` (keep it outside the repository) and prints a JSON receipt.  Automatic checks per shot:
AE-5 on screen (every outline bbox inside its model circle, every point centre inside its outline) and
label overlap 0 among visible labels (AC-3).  Aesthetic acceptance is a human judgement (AC-12 audit).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests" / "e2e"))
from v2_support import large_fixture, running_app  # noqa: E402

CHECK = """() => { const p = window.AtlasDebug.getAllPaperOverview(); const hulls = [...document.querySelectorAll('.atlas-cloud-hull')];
  const ae5 = hulls.filter(el => el.getAttribute('d')).map(el => { const cx = +el.dataset.cx, cy = +el.dataset.cy, r = +el.dataset.r, b = el.getBBox(); const pts = p.points.filter(q => q.cloudId === el.dataset.cloudId);
    return {id: el.dataset.cloudId, inCircle: b.x >= -r - 0.5 && b.y >= -r - 0.5 && b.x + b.width <= r + 0.5 && b.y + b.height <= r + 0.5, outside: pts.filter(q => !el.isPointInFill(new DOMPoint(q.deterministicX - cx, q.deterministicY - cy))).length}; });
  const boxes = [...document.querySelectorAll('#atlas-cloud-overlay .atlas-cloud-label')].filter(b => !b.hidden).map(b => b.getBoundingClientRect());
  let overlaps = 0; for (let i = 0; i < boxes.length; i += 1) for (let j = i + 1; j < boxes.length; j += 1) { const a = boxes[i], c = boxes[j]; if (a.x < c.x + c.width && c.x < a.x + a.width && a.y < c.y + c.height && c.y < a.y + a.height) overlaps += 1; }
  return {outlines: ae5.length, ae5Failures: ae5.filter(row => !row.inCircle || row.outside).length, visibleLabels: boxes.length, labelOverlaps: overlaps, subgroupOutlines: document.querySelectorAll('.atlas-cloud-subgroup').length, truncatedLabels: [...document.querySelectorAll('#atlas-cloud-overlay .atlas-cloud-label')].filter(b => !b.hidden && b.scrollWidth > b.clientWidth).map(b => [b.dataset.weightTier, b.textContent])}; }"""
READY = "() => { const c = document.querySelector('#atlas-field-network'); return c?.dataset.rendererState === 'ready' && c.dataset.cameraState === 'idle' && c.dataset.updateState === 'idle' && (window.AtlasDebug.getCloudLayoutStats()?.overlay?.placements || 0) > 0; }"


def main() -> int:
    out = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else None
    if out is None:
        print("usage: cloud_screenshot_matrix.py <output-dir>", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for scheme in ("light", "dark"):
        for width in (320, 720, 1440):
            with running_app(large_fixture(), viewport=(width, 1000), color_scheme=scheme) as app:
                app.wait(READY, 30)
                path = out / f"clouds-{scheme}-{width}.png"
                app.page.screenshot(path=str(path), full_page=True)
                rows.append({"shot": str(path), "scheme": scheme, "width": width, **app.page.evaluate(CHECK), "errors": app.errors[:3]})
                if scheme == "light" and width == 1440:
                    while app.page.evaluate("() => window.AtlasDebug.getOverviewGraph().getZoom()") < 1.6:  # SUBGROUP_MIN_ZOOM in frontend/cloud-overlay.js
                        app.page.locator("#atlas-network-zoom-in").click()
                        app.wait(READY, 10)
                    app.wait("() => window.AtlasDebug.getCloudLayoutStats().overlay.subgroupsVisible === true", 10)
                    app.page.wait_for_timeout(250)  # 150 ms opacity transition
                    path = out / "clouds-light-1440-zoomed-subgroups.png"
                    app.page.screenshot(path=str(path))
                    rows.append({"shot": str(path), "scheme": scheme, "width": width, "zoom": app.page.evaluate("() => window.AtlasDebug.getOverviewGraph().getZoom()"), **app.page.evaluate(CHECK), "errors": app.errors[:3]})
    ok = all(row["ae5Failures"] == 0 and row["labelOverlaps"] == 0 and not row["errors"] for row in rows)
    print(json.dumps({"passed": ok, "shots": rows}, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
