"""Cloud panel sub-group list length (F3) on the built app, synthetic fixture only.

A cloud with 11 automatic sub-groups (citation cliques: the map relation is citation when the fixture has citation edges of 5,4,4,4,3,...) and 3 isolated papers (기타): the panel shows the
8 largest groups and 기타 as the last visible row, the remaining 3 behind one native ``<details>`` in the same section,
keeps every group in the model, and opens the details when the selected sub-group is in the remainder.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v2_support import CAMERA_IDLE, EPOCH, SERVER, acceptance_fixture, recompute_overview, running_app  # noqa: E402

MANY = f"collection:{SERVER}:0:C20"
SIZES = [5, 4, 4, 4, 3, 3, 3, 3, 3, 3, 3]
PANEL_OPEN = "id => { const p = document.getElementById('atlas-cloud-panel'); return !p.hidden && p.dataset.cloudId === id; }"
ROWS = """() => { const sec = document.querySelector('#atlas-cloud-panel [data-section="subitems"]'); const more = sec.querySelector('details.atlas-cloud-subitems-more');
  const row = b => ({id: b.dataset.subitemId, count: Number(b.dataset.count), pressed: b.getAttribute('aria-pressed'), visible: b.checkVisibility()});
  return {visible: [...sec.querySelectorAll('.atlas-cloud-subitem')].filter(b => !b.closest('details')).map(row), more: more ? [...more.querySelectorAll('.atlas-cloud-subitem')].map(row) : [],
    open: more ? more.open : null, summary: more ? more.querySelector('summary').textContent : null, inSection: Boolean(more && more.parentElement === sec), all: sec.querySelectorAll('.atlas-cloud-subitem').length}; }"""


def many_groups_fixture() -> dict[str, Any]:
    lone = 3
    fixture = acceptance_fixture(extra_papers=sum(SIZES) + lone)
    pid = lambda index: f"zotero:{SERVER}:0:X{index:04d}"  # noqa: E731
    seq, start = 0, 0
    for size in SIZES:
        members = list(range(start, start + size))
        for a in members:
            for b in members:
                if a < b:
                    fixture["edges"].append({"id": f"edge:{SERVER}:{EPOCH}:many:{seq:03d}", "source": pid(a), "target": pid(b), "type": "citation", "directed": True, "score": 0.9,
                                             "provenance": {"provider": "synthetic", "method": "synthetic", "modelRevision": None}, "evidenceTier": "metadata", "verified": False})
                    seq += 1
        start += size
    fixture["collections"].append({"id": MANY, "label": "Synthetic many sub-groups", "paperIds": [pid(i) for i in range(start + lone)]})
    recompute_overview(fixture)
    return fixture


def test_cloud_panel_shows_eight_largest_sub_groups_and_other_with_the_rest_behind_one_details():
    with running_app(many_groups_fixture()) as app:
        page = app.page
        app.wait("() => window.AtlasDebug.getAllPaperOverview()?.grouping === 'collection'", 30)
        app.overview_ready()
        app.wait(CAMERA_IDLE, 10)
        page.evaluate("id => [...document.querySelectorAll('#atlas-cloud-overlay .atlas-cloud-label')].find(b => b.dataset.cloudId === id).click()", MANY)
        app.wait(PANEL_OPEN, 10, MANY)
        app.wait(CAMERA_IDLE, 10)
        model = page.evaluate("() => window.AtlasDebug.getCloudPanel()")
        items = model["subItems"]["items"]
        assert model["subItems"]["kind"] == "auto" and len(items) == len(SIZES) + 1 and items[-1]["other"], "the model keeps the full list (11 groups + 기타)"
        assert model["subItems"]["visibleIds"][-1] == items[-1]["id"] and len(model["subItems"]["visibleIds"]) == 9 and model["subItems"]["moreCount"] == 3
        rows = page.evaluate(ROWS)
        assert [row["id"] for row in rows["visible"]] == model["subItems"]["visibleIds"], "8 largest groups, then 기타 as the last visible row"
        assert sorted((row["count"] for row in rows["visible"][:-1]), reverse=True) == sorted(SIZES, reverse=True)[:8]
        assert rows["inSection"] and rows["open"] is False and rows["summary"] == "하위 그룹 3개 더 보기" and len(rows["more"]) == 3 and not any(row["visible"] for row in rows["more"])
        assert rows["all"] == len(items) and sum(row["count"] for row in rows["visible"] + rows["more"]) == model["size"], "every group is still in the panel DOM"
        # a sub-group from the remainder: open the details natively, select it; after the re-render it stays visible (details open)
        page.locator("#atlas-cloud-panel details.atlas-cloud-subitems-more > summary").click()
        target = rows["more"][0]["id"]
        page.locator(f'#atlas-cloud-panel .atlas-cloud-subitem[data-subitem-id="{target}"]').click()
        app.wait("id => window.AtlasDebug.getViewState()?.scope?.subItemId === id", 10, target)
        app.wait(CAMERA_IDLE, 10)
        rows = page.evaluate(ROWS)
        selected = next(row for row in rows["more"] if row["id"] == target)
        assert rows["open"] is True and selected["pressed"] == "true" and selected["visible"], rows
        assert not app.errors, app.errors[:3]
