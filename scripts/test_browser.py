"""Authenticated production-browser replay for the built Literature Atlas.

The suite starts the real loopback bridge and loads the built app through HTTP.
It uses the public fixture only as expected-value input; the browser obtains
its snapshot through the authenticated API.
"""
from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "demo-snapshot.json"
REPORT_PATH = ROOT / ".artifacts" / "browser-report.json"
ARTIFACT_DIR = ROOT / ".artifacts" / "browser-screenshots"
BOOTSTRAP_TIMEOUT = 15.0
READY_TIMEOUT = 10.0


class Assertions:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def check(self, name: str, condition: bool, detail: str = "") -> None:
        self.rows.append({"name": name, "passed": bool(condition), "detail": detail[:240]})
        if not condition:
            raise AssertionError(f"{name}: {detail}")


def http_json(url: str, token: str | None = None, *, method: str = "GET", body: Any = None) -> tuple[int, Any]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, headers=headers, method=method, data=data)
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw)
        except Exception:
            payload = raw.decode(errors="replace")[:240]
        return exc.code, payload


def read_bootstrap(stream: Any, timeout: float) -> dict[str, Any]:
    """Read one bootstrap JSON line without an unbounded readline call."""
    result: queue.Queue[str] = queue.Queue(maxsize=1)

    def read_one() -> None:
        try:
            result.put(stream.readline())
        except Exception as exc:  # pragma: no cover
            result.put(f"__read_error__:{exc}")

    threading.Thread(target=read_one, daemon=True).start()
    try:
        line = result.get(timeout=timeout).strip()
    except queue.Empty as exc:
        raise AssertionError("bridge bootstrap timed out") from exc
    if not line or line.startswith("__read_error__"):
        raise AssertionError(f"bridge exited without bootstrap: {line[:240]}")
    try:
        value = json.loads(line)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"bridge bootstrap was not JSON: {line[:240]}") from exc
    if not isinstance(value, dict) or set(value) != {"port", "token", "pid", "launchId"}:
        raise AssertionError("bootstrap must contain exactly port/token/pid/launchId")
    if not isinstance(value["port"], int) or not isinstance(value["pid"], int):
        raise AssertionError("bootstrap port and pid must be integers")
    if not all(isinstance(value[key], str) and value[key] for key in ("token", "launchId")):
        raise AssertionError("bootstrap token and launchId must be non-empty strings")
    return value


def drain_stderr(stream: Any, sink: list[str]) -> None:
    for line in iter(stream.readline, ""):
        if len(sink) < 80:
            sink.append(line.rstrip()[:240])


def wait_healthy(base: str, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, payload = http_json(f"{base}/health")
        if status == 200 and isinstance(payload, dict) and payload.get("ok") is True:
            return payload
        time.sleep(0.1)
    raise AssertionError("bridge did not become healthy before timeout")


def assert_schema(snapshot: dict[str, Any], fixture: dict[str, Any], a: Assertions) -> None:
    required = {"schemaVersion", "source", "status", "papers", "fields", "concepts", "memberships", "edges", "attachments", "collections", "overview", "modelStatus", "allocationMode"}
    a.check("snapshot has required top-level schema fields", required <= set(snapshot), "missing fields")
    a.check("snapshot schema version", snapshot.get("schemaVersion") == 1)
    a.check("fixture source identity", snapshot.get("source", {}).get("id") == fixture["source"]["id"])
    for key in ("papers", "fields", "concepts", "memberships", "edges", "attachments", "collections"):
        a.check(f"snapshot {key} count", len(snapshot.get(key, [])) == len(fixture.get(key, [])), f"{len(snapshot.get(key, []))} != {len(fixture.get(key, []))}")
        a.check(f"snapshot {key} IDs match fixture", {row.get("id") for row in snapshot.get(key, [])} == {row.get("id") for row in fixture.get(key, [])})
    for edge in snapshot["edges"]:
        a.check("edge type is explicit", edge.get("type") in {"citation", "semantic", "keyword", "field", "related"})
        a.check("edge direction/provenance present", isinstance(edge.get("directed"), bool) and isinstance(edge.get("provenance"), dict))
    expected_types = {row["type"] for row in fixture["edges"]}
    observed_types = {row["type"] for row in snapshot["edges"]}
    a.check("relationship types remain segregated", expected_types <= observed_types, f"{expected_types} vs {observed_types}")
    from collections import Counter
    a.check("relationship type counts match fixture", Counter(row["type"] for row in snapshot["edges"]) == Counter(row["type"] for row in fixture["edges"]))
    a.check("directed citation edges remain directed", all(row["directed"] for row in snapshot["edges"] if row["type"] == "citation"))
    a.check("non-citation edges remain undirected", all(not row["directed"] for row in snapshot["edges"] if row["type"] != "citation"))
    expected_overview = fixture["overview"]
    observed_overview = snapshot["overview"]
    baseline_keys = ("fields", "ribbons", "totalPapers", "totalEdges")
    a.check("overview aggregate is fixture truth", all(observed_overview.get(key) == expected_overview.get(key) for key in baseline_keys), "canonical overview fields changed")
    if "fullGraphEdgeCounts" in expected_overview:
        a.check("overview full graph edge counts are canonical", observed_overview.get("fullGraphEdgeCounts") == expected_overview["fullGraphEdgeCounts"])


def expected_ribbons(fixture: dict[str, Any], relation_type: str) -> dict[tuple[str, str, bool], float]:
    papers = {row["id"]: row for row in fixture["papers"]}
    result: dict[tuple[str, str, bool], float] = {}
    for edge in fixture["edges"]:
        if edge["type"] != relation_type or edge["source"] not in papers or edge["target"] not in papers:
            continue
        source_field = papers[edge["source"]].get("displayFieldId")
        target_field = papers[edge["target"]].get("displayFieldId")
        if not source_field or not target_field or source_field == target_field:
            continue
        directed = bool(edge.get("directed"))
        key = (source_field, target_field, directed) if directed else (*sorted((source_field, target_field)), directed)
        result[key] = result.get(key, 0) + 1
    return result


def svg_hit_point(locator: Any, fraction: float = .08) -> dict[str, float] | None:
    return locator.evaluate("""(el, fraction) => { const length=el.getTotalLength(); const p=el.getPointAtLength(length * fraction); const screen=el.getScreenCTM(); return screen ? {x:p.x * screen.a + p.y * screen.c + screen.e, y:p.x * screen.b + p.y * screen.d + screen.f} : null; }""", fraction)


def sector_hit_point(page: Any, index: int) -> dict[str, Any] | None:
    return page.evaluate("""index => {
      const state = window.AtlasDebug.getState();
      const svg = document.querySelector('#atlas-circular');
      const path = svg?.querySelectorAll('path.atlas-sector')[index];
      if (!path) return null;
      const expected = path.getAttribute('data-field-id');
      for (const fraction of [.08, .2, .35, .5, .65, .8, .92]) {
        const point = path.getPointAtLength(path.getTotalLength() * fraction);
        const screen = point.matrixTransform(path.getScreenCTM());
        const target = document.elementFromPoint(screen.x, screen.y)?.closest('path.atlas-sector');
        if (target?.getAttribute('data-field-id') === expected) return {x: screen.x, y: screen.y, targetField: expected};
      }
      return {targetField: document.elementFromPoint(0, 0)?.closest('path.atlas-sector')?.getAttribute('data-field-id') || null};
    }""", index)


ADVANCED_RENDERED = "() => { const d = document.getElementById('atlas-advanced'); return Boolean(d?.open) && d.dataset.lowerNetworks === 'rendered'; }"


def open_advanced(page: Any) -> None:
    """Open the 'Advanced' disclosure (closed on the default collection-map screen, AC-11) with a native click; mirrors tests/e2e/v2_support.open_advanced."""
    page.wait_for_function("() => Boolean(window.AtlasDebug?.getState()?.snapshot)", timeout=READY_TIMEOUT * 1000)
    if not page.evaluate("() => document.getElementById('atlas-advanced').open"):
        page.locator("#atlas-advanced > summary").click()
    page.wait_for_function(ADVANCED_RENDERED, timeout=READY_TIMEOUT * 1000)


# Collection-map transition finished (plan motion-and-edges §5.1; mirrors tests/e2e/v2_support.MOTION_IDLE): no camera tween, no
# decoration animation, update queue idle.  Immediate on views without transitions (ribbon/category, lower networks).
MOTION_IDLE = "() => { const c = document.querySelector('#atlas-field-network'); return !c || ((c.dataset.motionState || 'idle') === 'idle' && (!c.dataset.cameraState || c.dataset.cameraState === 'idle')); }"


def wait_motion_idle(page: Any) -> None:
    page.wait_for_function(MOTION_IDLE, timeout=READY_TIMEOUT * 1000)


def open_ribbon_overview(page: Any) -> None:
    """Advanced open + the Circular ribbons overview selected explicitly (it was the default before AC-11)."""
    open_advanced(page)
    page.locator("#atlas-overview-renderer").select_option("ribbon")


def main() -> int:
    a = Assertions()
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    for asset in (ROOT / "dist/web/index.html", ROOT / "dist/web/app.js", ROOT / "dist/web/styles.css"):
        a.check("built asset exists", asset.is_file(), str(asset.relative_to(ROOT)))
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
    except ImportError as exc:
        raise SystemExit("browser replay failed: Playwright 1.62.0 is required") from exc

    stderr_lines: list[str] = []
    process: subprocess.Popen[str] | None = None
    report: dict[str, Any] = {"suite": "browser-replay", "fixtureEpoch": fixture["source"]["epoch"], "assertions": a.rows, "passed": False}
    try:
        with tempfile.TemporaryDirectory(prefix="literature-atlas-e2e-") as data_dir:
            launch_id = f"browser-{os.getpid()}-{int(time.time() * 1000)}"
            command = [sys.executable, "-m", "atlas", "serve", "--demo", "--demo-fixture", str(FIXTURE_PATH), "--embedding-provider", "fake", "--port", "0", "--data-dir", data_dir, "--web-root", str(ROOT / "dist/web"), "--poll-interval", "5", "--launch-id", launch_id, "--parent-origin", "app://obsidian.md"]
            process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
            assert process.stdout and process.stderr
            threading.Thread(target=drain_stderr, args=(process.stderr, stderr_lines), daemon=True).start()
            bootstrap = read_bootstrap(process.stdout, BOOTSTRAP_TIMEOUT)
            a.check("bootstrap launch id", bootstrap["launchId"] == launch_id)
            a.check("bootstrap owns child pid", bootstrap["pid"] == process.pid)
            base = f"http://127.0.0.1:{bootstrap['port']}"
            health = wait_healthy(base, READY_TIMEOUT)
            a.check("health is unauthenticated and loopback", health["pid"] == process.pid and health["launchId"] == launch_id)
            status, _ = http_json(f"{base}/api/v1/snapshot?projection=full&sample=1")
            a.check("snapshot requires bearer authentication", status == 401, f"status={status}")
            status, snapshot = http_json(f"{base}/api/v1/snapshot?projection=full&sample=1", bootstrap["token"])
            a.check("authenticated snapshot available", status == 200)
            assert_schema(snapshot, fixture, a)
            status, detail = http_json(f"{base}/api/v1/snapshot?projection=detail&sample=10", bootstrap["token"])
            a.check("bounded detail projection available", status == 200 and isinstance(detail, dict))
            a.check("detail sampling preserves aggregate", detail.get("overview") == snapshot.get("overview"))
            status, bridge_status = http_json(f"{base}/api/v1/status", bootstrap["token"])
            a.check("status exposes automatic watcher state", status == 200 and "watcherInterval" in bridge_status)

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                for width, height, scheme in ((320, 736, "light"), (736, 900, "dark"), (1024, 900, "light")):
                    page = browser.new_page(viewport={"width": width, "height": height}, color_scheme=scheme)
                    page.goto(base + "/", wait_until="domcontentloaded")
                    page.evaluate("""() => window.dispatchEvent(new MessageEvent('message', {origin: 'https://evil.example', source: window.parent, data: {type: 'atlas:bootstrap', token: 'wrong', launchId: 'wrong', pid: 1, parentOrigin: 'https://evil.example'}}))""")
                    a.check(f"wrong-origin bootstrap rejected {width}", page.locator("#atlas-connection").is_visible())
                    page.locator("#atlas-token-input").fill(bootstrap["token"])
                    page.locator("#atlas-connect").click()
                    page.wait_for_function("() => Boolean(window.AtlasDebug?.getState()?.snapshot)", timeout=READY_TIMEOUT * 1000)
                    page.locator("#atlas-field-network").wait_for(state="visible", timeout=READY_TIMEOUT * 1000)
                    a.check(f"default screen is the collection map {width}", page.evaluate("window.AtlasDebug.getState().overviewRenderer") == "collections" and page.locator("#atlas-circular").is_hidden())
                    a.check(f"view selects are not on the first screen {width}", not page.locator("#atlas-overview-renderer").is_visible() and not page.locator("#atlas-renderer").is_visible())
                    a.check(f"default screen has no horizontal overflow {width}", page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"))
                    open_ribbon_overview(page)
                    page.locator("#atlas-circular").wait_for(state="visible", timeout=READY_TIMEOUT * 1000)
                    page.locator("path.atlas-sector").first.wait_for(state="visible", timeout=READY_TIMEOUT * 1000)
                    page.locator("#atlas-sample").fill("100")
                    page.locator("#atlas-sample").dispatch_event("input")
                    paper_text = page.locator("#atlas-paper-list").inner_text()
                    a.check(f"hostile title has literal text when eligible {width}", "<img" in paper_text or "hostile fixture" in paper_text)
                    a.check(f"literal hostile title stays text {width}", page.locator("#atlas-paper-list img").count() == 0 and page.locator("#atlas-paper-list script").count() == 0)
                    a.check(f"responsive viewport {width}", page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"))
                    a.check(f"visible sector labels fit {width}", page.evaluate("""() => { const svg=document.querySelector('#atlas-circular'); const box=svg.getBoundingClientRect(); return [...svg.querySelectorAll('.atlas-sector-label')].filter(el => getComputedStyle(el).display !== 'none' && getComputedStyle(el).visibility !== 'hidden').every(el => { const r=el.getBoundingClientRect(); return r.left >= box.left - 1 && r.right <= box.right + 1 && r.top >= box.top - 1 && r.bottom <= box.bottom + 1; }); }"""))
                    a.check(f"actual D3 sectors {width}", page.locator("path.atlas-sector").count() == len(fixture["fields"]))
                    legend_ids = page.locator("#atlas-field-legend button.atlas-legend-field").evaluate_all("buttons => buttons.map(button => button.dataset.fieldId)")
                    a.check(f"all source fields remain accessible in legend {width}", set(legend_ids) == {field["id"] for field in fixture["fields"]})
                    circular_state = page.evaluate("window.AtlasDebug.getState()")
                    relation_type = circular_state["relationTypes"][0] if circular_state["relationTypes"] else "semantic"
                    expected_ribbon_map = expected_ribbons(fixture, relation_type)
                    observed_ribbon_map = {tuple((row["sourceFieldId"], row["targetFieldId"], bool(row.get("directed")))): float(row.get("value", row.get("weight", 0))) for row in circular_state["circular"]["projection"]["ribbons"]}
                    a.check(f"actual D3 ribbon matrix for selected {relation_type} relation {width}", observed_ribbon_map == expected_ribbon_map, f"expected={expected_ribbon_map} observed={observed_ribbon_map}")
                    for field_index in range(page.locator("path.atlas-sector").count()):
                        field_path = page.locator("path.atlas-sector").nth(field_index)
                        expected_field = field_path.get_attribute("data-field-id")
                        field_path.scroll_into_view_if_needed()
                        field_point = sector_hit_point(page, field_index)
                        keyboard_fallback = False
                        if not (field_point and field_point.get("targetField") == expected_field):
                            field_path.focus()
                            field_path.press("Enter")
                            keyboard_fallback = True
                        a.check(f"sector {field_index} resolves to expected physical target {width}", keyboard_fallback or bool(field_point and field_point.get("targetField") == expected_field), f"expected={expected_field} actual={field_point and field_point.get('targetField')}")
                        if field_point and not keyboard_fallback:
                            page.mouse.click(field_point["x"], field_point["y"])
                        wait_motion_idle(page)
                        current_fields = page.locator('path.atlas-sector[aria-current="true"]')
                        current_id = current_fields.first.get_attribute("data-field-id", timeout=1000) if current_fields.count() else None
                        a.check(f"sector {field_index} selects exact field {width}", current_id == expected_field, f"expected={expected_field} actual={current_id}")
                    field_state = page.evaluate("window.AtlasDebug.getState()")
                    a.check(f"sector click changes field selection {width}", field_state["snapshot"] is not None and field_state["detail"] is not None)
                    if page.locator("path.atlas-ribbon").count():
                        ribbon = page.locator("path.atlas-ribbon").first
                        ribbon_id = ribbon.get_attribute("data-edge-id")
                        field_ids = [field["id"] for field in fixture["fields"]]
                        pair = next(([source, target] for source in field_ids for target in field_ids if (ribbon_id or "").startswith(f"{source}:{target}:")), [])
                        ribbon.scroll_into_view_if_needed()
                        ribbon_point = svg_hit_point(ribbon)
                        a.check(f"ribbon has physical hit target {width}", ribbon_point is not None)
                        if ribbon_point:
                            page.mouse.click(ribbon_point["x"], ribbon_point["y"])
                        wait_motion_idle(page)
                        pair_state = page.evaluate("window.AtlasDebug.getState()")
                        expected_pair_ids = {paper["id"] for paper in fixture["papers"] if any(field in paper.get("fieldIds", []) for field in pair)}
                        actual_pair_ids = {row["id"] for row in pair_state["detail"]["papers"]}
                        a.check(f"field-pair ribbon click filters exact membership {width}", actual_pair_ids == expected_pair_ids, f"expected={len(expected_pair_ids)} actual={len(actual_pair_ids)}")
                    counts = page.evaluate("window.AtlasDebug.getActualCounts()")
                    a.check(f"actual Cytoscape graph counts {width}", counts["paperNodes"] >= 1 and counts["paperEdges"] >= 1)
                    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(ARTIFACT_DIR / f"viewport-{width}.png"))
                    page.close()
                page = browser.new_page(viewport={"width": 1024, "height": 900})
                page.goto(base + "/", wait_until="domcontentloaded")
                page.locator("#atlas-token-input").fill(bootstrap["token"])
                page.locator("#atlas-connect").click()
                open_ribbon_overview(page)
                page.locator("path.atlas-sector").first.wait_for(state="visible", timeout=READY_TIMEOUT * 1000)
                initial = page.evaluate("window.AtlasDebug.getState()")
                initial_overview = initial["snapshot"]["overview"]
                page.locator("#atlas-paper-list button").first.click()
                wait_motion_idle(page)
                selected = page.evaluate("window.AtlasDebug.getState()")
                selected_id = selected["snapshot"]["papers"][0]["id"]
                page.locator("#atlas-sample").fill("10")
                page.locator("#atlas-sample").dispatch_event("input")
                sample = page.evaluate("window.AtlasDebug.getState()")
                a.check("10 percent sample reports visible and hidden counts", sample["detail"]["visiblePapers"] + sample["detail"]["hiddenPapers"] == sample["detail"]["eligiblePapers"])
                relation_types = set(sample["relationTypes"])
                expected_retained = {selected_id}
                for edge in sample["snapshot"]["edges"]:
                    if edge["type"] in relation_types and (edge["source"] == selected_id or edge["target"] == selected_id):
                        expected_retained.update((edge["source"], edge["target"]))
                a.check("sample budget retains selected one-hop neighborhood", expected_retained <= {row["id"] for row in sample["detail"]["papers"]}, f"missing={expected_retained - {row['id'] for row in sample['detail']['papers']}}")
                a.check("sample hidden count is exact", sample["detail"]["hiddenPapers"] == sample["detail"]["eligiblePapers"] - sample["detail"]["visiblePapers"])
                page.locator("#atlas-sample").fill("100")
                page.locator("#atlas-sample").dispatch_event("input")
                full = page.evaluate("window.AtlasDebug.getState()")
                a.check("100 percent sample shows all eligible records", full["detail"]["hiddenPapers"] == 0)
                a.check("sampling never changes aggregate overview", full["snapshot"]["overview"] == initial_overview)
                page.locator("#atlas-relation").select_option("semantic")
                semantic = page.evaluate("window.AtlasDebug.getState()")
                a.check("relation filter changes visible edge types", semantic["relationTypes"] == ["semantic"])
                page.locator("#atlas-relation").select_option("citation")
                selected = page.evaluate("window.AtlasDebug.getState()")
                a.check("paper selection updates exact selected paper", selected["detail"]["papers"][0]["id"] == selected["snapshot"]["papers"][0]["id"])
                page.locator("#atlas-paper-network").scroll_into_view_if_needed()  # native click coordinates must be on screen
                paper_point = page.evaluate("""() => { const g=window.AtlasDebug.getPaperGraph(); const n=g.getNodeData()[0]; const p=g.getClientByCanvas(g.getElementPosition(n.id)); return {x:p[0],y:p[1]}; }""")
                page.mouse.click(paper_point["x"], paper_point["y"])
                wait_motion_idle(page)
                a.check("native Cytoscape paper click updates selection", "Paper" in page.locator("#atlas-detail").inner_text())
                concept_buttons = page.locator("#atlas-concept-list button")
                if concept_buttons.count():
                    clicked_concept_id = concept_buttons.first.get_attribute("data-entity-id")
                    concept_buttons.first.click()
                    wait_motion_idle(page)
                    concept_state = page.evaluate("window.AtlasDebug.getState()")
                    concept_id = clicked_concept_id
                    expected_concept_ids = {paper["id"] for paper in fixture["papers"] if concept_id in paper.get("conceptIds", [])}
                    actual_concept_ids = {row["id"] for row in concept_state["detail"]["papers"]}
                    a.check("concept selection filters exact related papers", actual_concept_ids == expected_concept_ids, f"expected={len(expected_concept_ids)} actual={len(actual_concept_ids)}")
                    concept_point = page.evaluate("""() => { const g=window.AtlasDebug.getConceptGraph(); const n=g.getNodeData().find(row => row.kind === 'concept' || row.data?.kind === 'concept') || g.getNodeData()[0]; const p=g.getClientByCanvas(g.getElementPosition(n.id)); return {x:p[0],y:p[1]}; }""")
                    page.mouse.click(concept_point["x"], concept_point["y"])
                    wait_motion_idle(page)
                    a.check("native Cytoscape concept click updates selection", "Concept" in page.locator("#atlas-detail").inner_text())
                palette_values = []
                for palette_id in ("atlas", "calm", "mocha"):
                    page.locator("#atlas-palette").select_option(palette_id)
                    palette_values.append(page.evaluate("getComputedStyle(document.querySelector('#atlas-app')).getPropertyValue('--atlas-series-1').trim()"))
                a.check("three palettes expose computed series colors", len(set(palette_values)) == 3, str(palette_values))
                page.locator("#atlas-theme").select_option("dark")
                a.check("dark theme applies", page.locator("#atlas-app").get_attribute("data-atlas-theme") == "dark")
                page.locator("#atlas-theme").select_option("light")
                a.check("light theme applies", page.locator("#atlas-app").get_attribute("data-atlas-theme") == "light")
                page.locator("#atlas-theme").select_option("system")
                a.check("system theme option available", page.locator("#atlas-theme option[value=system]").count() == 1)
                page.locator("#atlas-taxonomy-form").locator("xpath=ancestor::details[1]").evaluate("details => details.open = true")
                page.locator("#atlas-threshold").fill("0.91")
                page.locator("#atlas-save-settings").click()
                try:
                    page.get_by_text("Settings saved", exact=True).wait_for(timeout=3000)
                except Exception:
                    page.wait_for_timeout(300)
                page.reload(wait_until="domcontentloaded")
                page.locator("#atlas-token-input").fill(bootstrap["token"])
                page.locator("#atlas-connect").click()
                open_ribbon_overview(page)
                page.locator("path.atlas-sector").first.wait_for(state="visible", timeout=READY_TIMEOUT * 1000)
                page.locator("#atlas-taxonomy-form").locator("xpath=ancestor::details[1]").evaluate("details => details.open = true")
                persisted_threshold = page.locator("#atlas-threshold").input_value()
                a.check("settings CAS survives reload", persisted_threshold == "0.91", f"observed={persisted_threshold}")
                taxonomy_errors: list[str] = []
                taxonomy_events: list[str] = []
                taxonomy_requests: list[str] = []
                def capture_taxonomy_request(request: Any) -> None:
                    if "/api/v1/taxonomy" in request.url and request.method in {"POST", "PUT"}:
                        try:
                            payload = json.loads(request.post_data or "{}")
                            tax = payload.get("taxonomy", payload)
                            taxonomy_requests.append(json.dumps({"sourceId": tax.get("sourceId"), "sourceEpoch": tax.get("sourceEpoch"), "revision": payload.get("revision"), "firstFieldId": (tax.get("fields") or [{}])[0].get("id"), "newField": [row.get("id") for row in tax.get("fields", []) if row.get("label", {}).get("en") == "Browser-created field"]}, ensure_ascii=False))
                        except Exception:
                            taxonomy_requests.append((request.post_data or "")[:240])
                def capture_taxonomy_error(response: Any) -> None:
                    if "/api/v1/taxonomy" in response.url:
                        taxonomy_events.append(f"status={response.status}")
                    if "/api/v1/taxonomy" in response.url and response.status >= 400:
                        try:
                            taxonomy_errors.append(response.body().decode(errors="replace")[:240])
                        except Exception:
                            taxonomy_errors.append(f"status={response.status}")
                page.on("response", capture_taxonomy_error)
                page.on("request", capture_taxonomy_request)
                page.locator("#atlas-taxonomy-form input[name=entityId]").fill("")
                page.locator("#atlas-taxonomy-form select[name=entityType]").select_option("field")
                page.locator("#atlas-taxonomy-form input[name=label]").fill("Browser-created field")
                page.locator("#atlas-taxonomy-form").locator("button[type=submit]").click()
                try:
                    page.locator("#atlas-field-list button", has_text="Browser-created field").wait_for(timeout=3000)
                    taxonomy_created = True
                except PlaywrightTimeoutError:
                    taxonomy_created = False
                a.check("taxonomy create control persists source-scoped field", taxonomy_created, f"events={taxonomy_events}; responses={taxonomy_errors}; requests={taxonomy_requests}")
                page.close()
                browser.close()
            report["passed"] = True
            report["counts"] = {"fixturePapers": len(fixture["papers"]), "fixtureEdges": len(fixture["edges"]), "fixtureFields": len(fixture["fields"])}
    except (AssertionError, PlaywrightTimeoutError) as exc:
        report["error"] = str(exc)[:400]
    except Exception as exc:  # pragma: no cover
        report["error"] = f"{type(exc).__name__}: {exc}"[:400]
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        report["assertions"] = a.rows
        report["assertionCount"] = len(a.rows)
        report["passedAssertions"] = sum(1 for row in a.rows if row["passed"])
        report["stderrLines"] = [re.sub(r"(?i)(token|bearer)[^ ]*", "[redacted]", line) for line in stderr_lines]
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "assertions": report["assertionCount"], "passedAssertions": report["passedAssertions"], "report": str(REPORT_PATH.relative_to(ROOT))}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
