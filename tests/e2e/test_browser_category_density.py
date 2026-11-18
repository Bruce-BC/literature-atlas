"""Category expansion and full-graph density acceptance for the rich fixture."""
from __future__ import annotations

import json
import copy
import atexit
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "demo-snapshot.json"


def wait_for(page, predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if page.evaluate(predicate):
            return
        time.sleep(.1)
    raise AssertionError("category-density browser state did not converge")


def test_category_density_and_expansion_preserve_full_graph_truth():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        pytest.fail(f"Playwright is required: {exc}")
    with __import__("tempfile").TemporaryDirectory(prefix="atlas-category-density-") as data_dir:
        fixture = json.loads(FIXTURE.read_text())
        template = fixture["papers"][0]
        primary_field = fixture["fields"][0]["id"]
        for index in range(60):
            paper = copy.deepcopy(template)
            paper.update({"id": f"zotero:demo-server:0:DC{index:02d}", "key": f"DC{index:02d}", "title": f"Dense category paper {index}", "fieldIds": [primary_field], "displayFieldId": primary_field, "conceptIds": [], "attachmentIds": [], "textStatus": "missing"})
            fixture["papers"].append(paper)
            fixture["memberships"].append({"id": f"membership:demo-server:demo-epoch:DC{index:02d}:analysis", "paperId": paper["id"], "fieldId": primary_field, "method": "user", "score": 1, "weight": 1})
        fixture_path = Path(data_dir) / "category-density-fixture.json"
        fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
        process = subprocess.Popen([sys.executable, "-m", "atlas", "serve", "--demo", "--demo-fixture", str(fixture_path), "--embedding-provider", "fake", "--port", "0", "--data-dir", data_dir, "--web-root", str(ROOT / "dist/web"), "--poll-interval", "5", "--launch-id", "category-density", "--parent-origin", "app://obsidian.md"], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        def cleanup_process():
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=3)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=3)
        atexit.register(cleanup_process)
        assert process.stdout
        bootstrap = json.loads(process.stdout.readline())
        base = f"http://127.0.0.1:{bootstrap['port']}"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1200, "height": 1000})
            page.goto(base + "/", wait_until="domcontentloaded")
            page.locator("#atlas-token-input").fill(bootstrap["token"]); page.locator("#atlas-connect").click()
            wait_for(page, "() => Boolean(window.AtlasDebug?.getState()?.snapshot?.papers?.length)")
            full = page.evaluate("window.AtlasDebug.getState()")
            source_epoch = full["snapshot"]["source"]["epoch"]
            full_overview = full["snapshot"]["overview"]
            full_edge_counts = full_overview["fullGraphEdgeCounts"]

            page.locator("#atlas-sample").fill("35"); page.locator("#atlas-sample").dispatch_event("input")
            sampled = page.evaluate("window.AtlasDebug.getState()")
            assert sampled["snapshot"]["source"]["epoch"] == source_epoch
            assert sampled["snapshot"]["overview"] == full_overview
            assert sampled["detail"]["eligiblePapers"] == len(full["snapshot"]["papers"])
            assert sampled["detail"]["eligibleEdges"] <= sum(full_edge_counts.values())

            page.locator("#atlas-sample").fill("100"); page.locator("#atlas-sample").dispatch_event("input")
            expanded = page.evaluate("window.AtlasDebug.getState()")
            assert expanded["snapshot"]["overview"] == full_overview
            assert expanded["detail"]["hiddenPapers"] == 0

            page.locator("#atlas-relation").select_option("citation")
            citation = page.evaluate("window.AtlasDebug.getState()")
            assert all(edge["directed"] for edge in citation["detail"]["edges"] if edge["type"] == "citation")
            page.locator("#atlas-relation").select_option("semantic")
            semantic = page.evaluate("window.AtlasDebug.getState()")
            assert all(not edge["directed"] for edge in semantic["detail"]["edges"] if edge["type"] == "semantic")
            assert {edge["type"] for edge in citation["detail"]["edges"]} != {edge["type"] for edge in semantic["detail"]["edges"]}

            page.locator("#atlas-sample").fill("35"); page.locator("#atlas-sample").dispatch_event("input")
            page.locator("#atlas-density-panel").evaluate("details => details.open = true")
            page.locator("#atlas-color-mode").select_option("density")
            density35 = page.locator("#atlas-density-content").inner_text()
            page.locator("#atlas-sample").fill("100"); page.locator("#atlas-sample").dispatch_event("input")
            assert page.locator("#atlas-density-content").inner_text() == density35
            page.locator("#atlas-search").fill("phage"); page.locator("#atlas-search").dispatch_event("input")
            assert page.locator("#atlas-density-content").inner_text() != density35
            page.locator("#atlas-search").fill(""); page.locator("#atlas-search").dispatch_event("input")

            page.locator("#atlas-overview-renderer").select_option("category")
            wait_for(page, "() => !document.querySelector('#atlas-category-explorer')?.hidden")
            anchors = page.locator("#atlas-category-explorer [data-category-anchor]")
            assert anchors.count() > 1
            assert page.locator("#atlas-category-explorer .atlas-category-card h3").count() == anchors.count()
            assert any(anchors.nth(i).evaluate("el => getComputedStyle(el).fill != 'rgb(100, 100, 100)' && getComputedStyle(el).fill != '#64748b'") for i in range(anchors.count()))
            first_children = page.locator("#atlas-category-explorer [data-category-child]:visible")
            anchor_ids = anchors.evaluate_all("nodes => nodes.map(node => node.dataset.categoryAnchor)")
            anchor_index = anchor_ids.index(primary_field)
            count_match = re.search(r"· (\d+) papers", page.locator("#atlas-category-explorer .atlas-category-card").nth(anchor_index).locator("h3").inner_text())
            assert count_match and int(count_match.group(1)) > 24
            anchors.nth(anchor_index).click()
            assert first_children.count() > 0
            first_ids = set(first_children.evaluate_all("nodes => nodes.map(node => node.dataset.paperId || node.dataset.categoryChild)"))
            assert first_ids <= {paper["id"] for paper in full["snapshot"]["papers"]}
            next_button = page.get_by_role("button", name="Next").first
            assert next_button.count() == 1
            next_button.click()
            next_ids = set(page.locator("#atlas-category-explorer [data-category-child]:visible").evaluate_all("nodes => nodes.map(node => node.dataset.paperId || node.dataset.categoryChild)"))
            assert next_ids and next_ids.isdisjoint(first_ids)
            previous_button = page.get_by_role("button", name="Previous").first
            assert previous_button.count() == 1
            previous_button.click()
            restored_ids = set(page.locator("#atlas-category-explorer [data-category-child]:visible").evaluate_all("nodes => nodes.map(node => node.dataset.paperId || node.dataset.categoryChild)"))
            assert restored_ids == first_ids
            second_index = next(i for i, anchor_id in enumerate(anchor_ids) if anchor_id != primary_field and int(re.search(r"· (\d+) papers", page.locator("#atlas-category-explorer .atlas-category-card").nth(i).locator("h3").inner_text()).group(1)) > 0)
            anchors.nth(second_index).click()
            second_ids = set(page.locator("#atlas-category-explorer [data-category-child]:visible").evaluate_all("nodes => nodes.map(node => node.dataset.paperId || node.dataset.categoryChild)"))
            assert second_ids and len(second_ids) >= len(first_ids)
            anchors.nth(anchor_index).click()
            assert page.locator("#atlas-category-explorer [data-category-child]:visible").count() > 0
            page.locator("#atlas-category-explorer [data-category-child]:visible").first.click()
            wait_for(page, "() => document.querySelector('#atlas-detail')?.textContent.includes('Paper')")
            browser.close()
        cleanup_process()
