"""Shared synthetic fixtures and a native-browser harness for taxonomy/interaction v2.

Everything here is synthetic.  The harness starts the real loopback bridge in
demo mode, loads the BUILT bundle (``dist/web``) and exposes only read-only
observation (``window.AtlasDebug``); application actions are always performed
with native mouse/keyboard events.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "demo-snapshot.json"
DIST_APP = ROOT / "dist" / "web" / "app.js"
SERVER, EPOCH = "demo-server", "demo-epoch"
OPT = f"field:{SERVER}:{EPOCH}:optimization"
OT = f"field:{SERVER}:{EPOCH}:optimization-transport"
ANALYSIS = f"field:{SERVER}:{EPOCH}:analysis"
OPT_DIRECT = 30  # > 24 so the child page boundary is real
MULTI = f"zotero:{SERVER}:0:MULTI00"
UNKNOWN = f"zotero:{SERVER}:0:UNKNOWN"


def fixture_hash(fixture: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(fixture, sort_keys=True).encode("utf-8")).hexdigest()


def _descendants(fields: list[dict[str, Any]], field_id: str) -> set[str]:
    result = {field_id}
    changed = True
    while changed:
        changed = False
        for field in fields:
            if field["id"] not in result and any(parent in result for parent in field["parentIds"]):
                result.add(field["id"])
                changed = True
    return result


def add_backend_style_extensions(fixture: dict[str, Any]) -> None:
    """Author synthetic facet/field-stat extensions with the documented formulas.

    The demo bridge serves the fixture as-is, so these stand in for backend
    output.  They are computed from the fixture itself (unique paper-ID sets).
    """
    papers, fields = fixture["papers"], fixture["fields"]
    direct = {field["id"]: {p["id"] for p in papers if field["id"] in p["fieldIds"]} for field in fields}
    primary: dict[str, int] = {}
    for paper in papers:
        if paper["displayFieldId"]:
            primary[paper["displayFieldId"]] = primary.get(paper["displayFieldId"], 0) + 1
    rows = []
    for field in fields:
        inclusive: set[str] = set()
        for fid in _descendants(fields, field["id"]):
            inclusive |= direct[fid]
        rows.append({"fieldId": field["id"], "direct": len(direct[field["id"]]), "inclusive": len(inclusive), "primary": primary.get(field["id"], 0)})
    known = sum(primary.values())
    fixture["fieldStats"] = {"version": "synthetic-v1", "paperCount": len(papers), "rows": rows, "unknown": len(papers) - known, "knownPrimary": known}
    entity = lambda fid, kind, label, legacy=None: {"id": fid, "kind": kind, "label": {"en": label}, "broaderIds": [], "aliases": [], "origin": "curated", **({"legacyFieldId": legacy} if legacy else {})}
    fixture["facetCatalog"] = {"version": 1, "namespace": f"atlas.taxonomy.{SERVER}.{EPOCH}", "sourceId": SERVER, "sourceEpoch": EPOCH, "inputState": "valid-custom", "entities": [
        entity("subject:optimal-transport", "subject", "Optimal transport", OT), entity("tool:qiime2", "tool", "QIIME 2"),
        entity("method:amplicon", "method", "Amplicon analysis"), entity("application:microbiome", "application", "Microbiome")]}
    memberships = [{"facetId": facet, "kind": kind, "origin": "curated", "score": 1, "evidenceRef": {"evidenceTier": tier, "field": "title" if tier == "metadata" else "abstract"}} for facet, kind, tier in (
        ("subject:optimal-transport", "subject", "metadata"), ("tool:qiime2", "tool", "metadata"), ("method:amplicon", "method", "indexed"), ("application:microbiome", "application", "fulltext"))]
    fixture["paperFacets"] = [{"paperId": MULTI, "directMemberships": memberships, "primaryByFacet": {"subject": "subject:optimal-transport", "tool": "tool:qiime2"}}]


def recompute_overview(fixture: dict[str, Any]) -> None:
    """Recompute overview counts from the fixture's own source tables (the demo overview predates added papers/fields)."""
    weights = {field["id"]: 0 for field in fixture["fields"]}
    for paper in fixture["papers"]:
        if paper.get("displayFieldId") in weights:
            weights[paper["displayFieldId"]] += 1
    overview = fixture["overview"]
    overview["fields"] = [{"fieldId": field_id, "paperWeight": weight} for field_id, weight in weights.items()]
    overview["totalPapers"] = len(fixture["papers"])
    overview["totalEdges"] = len(fixture["edges"])
    overview["displayedEdges"] = min(1000, len(fixture["edges"]))
    counts: dict[str, int] = {}
    for edge in fixture["edges"]:
        counts[edge["type"]] = counts.get(edge["type"], 0) + 1
    overview["fullGraphEdgeCounts"] = counts
    paper_ids = {paper["id"] for paper in fixture["papers"]}
    overview["ribbons"] = [row for row in overview["ribbons"] if row["sourceFieldId"] in weights and row["targetFieldId"] in weights]


FIXTURE_VALIDATION: list[dict[str, Any]] = []


def validate_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    """Validate against docs/snapshot.schema.json AND the real frontend decoder; raise before any browser starts."""
    import jsonschema
    schema = json.loads((ROOT / "docs" / "snapshot.schema.json").read_text(encoding="utf-8"))
    errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(fixture), key=lambda error: list(error.path))
    if errors:
        raise AssertionError("fixture violates docs/snapshot.schema.json: " + "; ".join(f"{'/'.join(map(str, e.path))}: {e.message[:120]}" for e in errors[:5]))
    script = "import {readFileSync} from 'node:fs'; import {decodeSnapshot} from process.argv[1]; const s = decodeSnapshot(JSON.parse(readFileSync(0, 'utf8'))); console.log(JSON.stringify({papers: s.papers.length, concepts: s.concepts.length, fields: s.fields.length}));"
    decoder = (ROOT / "frontend" / "decoder.js").as_uri()
    result = subprocess.run(["node", "--input-type=module", "-e", script.replace("process.argv[1]", json.dumps(decoder))], input=json.dumps(fixture), capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise AssertionError("fixture rejected by frontend decodeSnapshot: " + next((line for line in result.stderr.splitlines() if "Error:" in line), result.stderr[-200:])[:300])
    row = {"jsonSchema": "valid", "decodeSnapshot": "valid", "sha256": fixture_hash(fixture), "decoded": json.loads(result.stdout)}
    FIXTURE_VALIDATION.append(row)
    return row


def acceptance_fixture(extra_papers: int = 0) -> dict[str, Any]:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    fixture["fields"].extend([
        {"id": OPT, "label": {"en": "Optimization", "ko": "최적화"}, "parentIds": [], "aliases": [], "source": "user"},
        {"id": OT, "label": {"en": "Optimal transport", "ko": "최적수송"}, "parentIds": [OPT, ANALYSIS], "aliases": ["OT"], "source": "user"},
    ])
    template = copy.deepcopy(fixture["papers"][0])
    concept_ids = [c["id"] for c in fixture["concepts"]]

    def add(key: str, title: str, field_ids: list[str], display: str | None, concepts: list[str] | None = None) -> None:
        paper = copy.deepcopy(template)
        paper.update({"id": f"zotero:{SERVER}:0:{key}", "key": key, "title": title, "fieldIds": field_ids, "displayFieldId": display, "conceptIds": concepts or [], "attachmentIds": [], "textStatus": "missing"})
        fixture["papers"].append(paper)
        for field_id in field_ids:
            fixture["memberships"].append({"id": f"membership:{SERVER}:{EPOCH}:{key}:{field_id[-12:]}", "paperId": paper["id"], "fieldId": field_id, "method": "user", "score": 1, "weight": 1})

    for index in range(OPT_DIRECT):
        add(f"OPT{index:02d}", f"Optimization direct paper {index}", [OPT], OPT, concept_ids[index % len(concept_ids): index % len(concept_ids) + 1])
    for index in range(6):
        add(f"OT{index:02d}", f"Optimal transport paper {index}", [OT], OT, concept_ids[:2])
    add("MULTI00", "Multi parent transport paper", [OT, ANALYSIS], OT, concept_ids[:3])
    add("UNKNOWN", "Unclassified acceptance paper", [], None)
    for index in range(extra_papers):
        add(f"X{index:04d}", f"Extra synthetic paper {index}", [OPT], OPT)
    fixture["attachments"] = []
    recompute_overview(fixture)
    add_backend_style_extensions(fixture)
    return fixture


def collections_fixture() -> dict[str, Any]:
    """Acceptance fixture plus synthetic Zotero collections: multi-membership replicas, tiny clouds, a long (truncated) label and uncollected papers."""
    fixture = acceptance_fixture()
    ids = {paper["key"]: paper["id"] for paper in fixture["papers"]}

    def collection(key: str, label: str, keys: list[str]) -> None:
        fixture["collections"].append({"id": f"collection:{SERVER}:0:{key}", "label": label, "paperIds": [ids[k] for k in keys]})

    collection("C03", "Synthetic optimization methods", [f"OPT{i:02d}" for i in range(18)])
    collection("C04", "Synthetic optimization applications and benchmark studies for a deliberately long label", [f"OPT{i:02d}" for i in range(12, 30)])  # OPT12..17 are in both
    collection("C05", "Synthetic transport", [f"OT{i:02d}" for i in range(6)] + ["MULTI00"])
    collection("C06", "Synthetic reading list", ["MULTI00", "D01", "D02", "D03", "D30", "D31"])
    collection("C07", "합성 컬렉션 일곱", ["D10", "D11", "D12", "D40"])
    collection("C08", "Synthetic pair", ["D05", "D06"])
    collection("C09", "Synthetic single", ["D41"])
    return fixture  # UNKNOWN and D48 stay uncollected -> one cloud:unfiled


HUB = f"zotero:{SERVER}:0:OPT05"  # edges_fixture: one paper whose semantic lines span every strength tier


def edges_fixture() -> dict[str, Any]:
    """collections_fixture plus synthetic semantic edges with graded scores (motion-and-edges M2a/M2b): a hub paper with
    intra- and inter-cloud lines in all three tiers, a dense C03 <-> C04 corridor (aggregate bridge), and lines of the
    two-collection paper MULTI00 into C05, C06 and other clouds (replica rule).  Synthetic ids and scores only."""
    fixture = collections_fixture()
    pid = lambda key: f"zotero:{SERVER}:0:{key}"  # noqa: E731
    rows: list[tuple[str, str, float]] = []
    partners = [f"OPT{i:02d}" for i in (0, 1, 2, 3, 4)] + [f"OPT{i:02d}" for i in (20, 21, 22, 23, 24)] + ["OT00", "OT01", "OT02", "D20", "D21"]
    for index, key in enumerate(partners):
        rows.append(("OPT05", key, round(0.97 - index * 0.012, 3)))
    for index, key in enumerate(partners[:-1]):
        rows.append((key, partners[index + 1], round(0.72 - index * 0.004, 3)))  # every partner has a weaker second line, so its own rank of the hub line is high
    for a in range(6, 12):
        for b in range(24, 30):
            if (a + b) % 2 == 0:
                rows.append((f"OPT{a:02d}", f"OPT{b:02d}", round(0.9 - ((a * 7 + b) % 11) * 0.01, 3)))  # C03 <-> C04 corridor
    for a in range(6, 11):
        rows.append((f"OPT{a:02d}", f"OPT{a + 1:02d}", round(0.95 - a * 0.002, 3)))  # strong lines inside C03
    for key, score in (("OT03", 0.93), ("D01", 0.91), ("OPT10", 0.85), ("D10", 0.8), ("D40", 0.76)):
        rows.append(("MULTI00", key, score))
    concepts = [concept["id"] for concept in fixture["concepts"]]; papers = {paper["key"]: paper for paper in fixture["papers"] if "key" in paper}
    papers["OPT05"]["conceptIds"] = concepts[5:7]  # shared-concept Jaccard grades the hub's lines: 1.0 for the first partners, 0.5, then 0
    for index, key in enumerate(partners):
        papers[key]["conceptIds"] = concepts[5:7] if index < 3 else concepts[5:6] if index < 7 else papers[key]["conceptIds"]
    for a in range(6, 12):
        papers[f"OPT{a:02d}"]["conceptIds"] = concepts[8:10]  # the C03 chain shares its concepts -> strong intra-cloud lines
    for index, (a, b, score) in enumerate(rows):
        fixture["edges"].append({"id": f"edge:{SERVER}:{EPOCH}:tier:{index:03d}", "source": pid(a), "target": pid(b), "type": "semantic", "directed": False, "score": score, "provenance": {"provider": "synthetic", "method": "synthetic", "modelRevision": None}, "evidenceTier": "metadata", "verified": False})
    recompute_overview(fixture)
    return fixture


def add_synthetic_collections(fixture: dict[str, Any], count: int = 12) -> None:
    """``count`` synthetic collections with heavy-tail sizes (largest ~30 %), 5 % multi-membership, uncollected unknowns."""
    members: list[list[str]] = [[] for _ in range(count)]
    for index, paper in enumerate(fixture["papers"]):
        if index % 50 == 49:
            continue  # uncollected -> cloud:unfiled
        rank = min(count - 1, int(count * (((index * 7919) % 1000) / 1000) ** 2))
        members[rank].append(paper["id"])
        if index % 20 == 0:
            members[(rank + 1) % count].append(paper["id"])
    fixture["collections"] = [{"id": f"collection:{SERVER}:0:SC{k:02d}", "label": f"Synthetic collection {k + 1:02d}" + (" · extended synthetic topic group" if k % 4 == 3 else ""), "paperIds": sorted(set(ids))} for k, ids in enumerate(members) if ids]


def large_fixture(papers: int = 1000, concepts: int = 1410) -> dict[str, Any]:
    """1,000 papers / 1,410 concepts over a valid Optimization→OT multi-parent forest with direct papers and unknowns, in 12 synthetic collections."""
    fixture = acceptance_fixture()
    template = copy.deepcopy(fixture["papers"][0])
    fixture["papers"], fixture["memberships"], fixture["edges"], fixture["collections"] = [], [], [], []
    field_ids = [field["id"] for field in fixture["fields"]]
    concept_template = copy.deepcopy(fixture["concepts"][0])
    fixture["concepts"] = []
    for index in range(concepts):
        concept = copy.deepcopy(concept_template)
        concept.update({"id": f"concept:{SERVER}:{EPOCH}:v2-{index:04d}", "label": {"en": f"V2 concept {index}"}})
        fixture["concepts"].append(concept)
    for index in range(papers):
        if index % 50 == 49:
            field_list: list[str] = []
        elif index % 25 == 0:
            field_list = [OT, ANALYSIS]
        else:
            field_list = [field_ids[index % len(field_ids)]]
        paper = copy.deepcopy(template)
        paper.update({"id": f"zotero:{SERVER}:0:V2{index:04d}", "key": f"V2{index:04d}", "title": f"Taxonomy v2 synthetic paper {index}", "fieldIds": field_list, "displayFieldId": field_list[0] if field_list else None,
                      "conceptIds": [fixture["concepts"][(index * 7) % concepts]["id"], fixture["concepts"][(index * 7 + 1) % concepts]["id"]], "attachmentIds": [], "textStatus": "missing"})
        fixture["papers"].append(paper)
        for field_id in field_list:
            fixture["memberships"].append({"id": f"membership:{SERVER}:{EPOCH}:V2{index:04d}:{field_id[-10:]}", "paperId": paper["id"], "fieldId": field_id, "method": "user", "score": 1, "weight": 1})
        for other in (index - 1, index - 13):
            if other >= 0:
                fixture["edges"].append({"id": f"edge:v2:{index:04d}:{other:04d}", "source": fixture["papers"][other]["id"], "target": paper["id"], "type": "semantic", "directed": False,
                                         "provenance": {"provider": "synthetic", "method": "synthetic", "modelRevision": None}, "evidenceTier": "metadata", "verified": False})
    # keep the Optimization hierarchy exercised in the large fixture
    fixture["overview"]["keywordAggregate"] = {"conceptIds": [], "pairs": [], "encoding": "concept-index-pairs", "totalPairs": 0, "totalWeight": 0}
    recompute_overview(fixture)
    add_backend_style_extensions(fixture)
    fixture["paperFacets"] = []  # MULTI00 does not exist in the large fixture
    fixture.pop("facetCatalog", None)
    add_synthetic_collections(fixture)
    return fixture


# Map camera + queue + transition idle (plan motion-and-edges §5.1): data-camera-state idle (no G6 animation, no clamp frame pending), the
# update queue idle, and data-motion-state idle (no camera tween, no decoration animation, the queue idle).  Shared by every browser test.
CAMERA_IDLE = "() => { const c = document.querySelector('#atlas-field-network'); return c?.dataset.cameraState === 'idle' && c.dataset.updateState === 'idle' && (c.dataset.motionState || 'idle') === 'idle'; }"
# Tolerant variant for paths where the map may not be a G6 view (ribbon/category modes): only a RUNNING transition or camera move blocks.
MOTION_IDLE = "() => { const c = document.querySelector('#atlas-field-network'); return !c || ((c.dataset.motionState || 'idle') === 'idle' && (!c.dataset.cameraState || c.dataset.cameraState === 'idle')); }"


ADVANCED_RENDERED = "() => { const d = document.getElementById('atlas-advanced'); return Boolean(d?.open) && d.dataset.lowerNetworks === 'rendered'; }"


def open_advanced(page: Any, timeout: float = 20.0) -> None:
    """Open the 'Advanced' disclosure (closed on the default screen, AC-11) with a native click on its summary.

    Every legacy control (view selects, palette/theme, detail slider, category table, lower networks, lists,
    Selection card, settings) lives inside it.  Waits until the deferred lower networks are really rendered.
    Call it right after connect (snapshot loaded) in every path that drives those controls.
    """
    page.wait_for_function("() => Boolean(window.AtlasDebug?.getState()?.snapshot)", timeout=timeout * 1000)
    if not page.evaluate("() => document.getElementById('atlas-advanced').open"):
        page.locator("#atlas-advanced > summary").click()
    page.wait_for_function(ADVANCED_RENDERED, timeout=timeout * 1000)


def legacy_screen(page: Any, timeout: float = 20.0) -> None:
    """Pre-Phase-4 starting point for tests written against it: Advanced open + the Circular ribbons overview selected explicitly."""
    open_advanced(page, timeout)
    page.locator("#atlas-overview-renderer").select_option("ribbon")
    page.locator("#atlas-circular").wait_for(state="visible", timeout=timeout * 1000)


class Harness:
    """A running bridge + browser page.  Close it with ``with`` or ``close()``."""

    def __init__(self, page: Any, browser: Any, fixture: dict[str, Any], base: str, errors: list[str]) -> None:
        self.page, self.browser, self.fixture, self.base, self.errors = page, browser, fixture, base, errors

    # ---- waiting helpers (state based, never fixed sleeps) ------------------------------------
    def wait(self, expression: str, timeout: float = 20.0, arg: Any = None) -> Any:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = self.page.evaluate(expression, arg) if arg is not None else self.page.evaluate(expression)
            if value:
                return value
            time.sleep(0.02)
        try:
            context = self.page.evaluate("() => { const v = window.AtlasDebug?.getViewState?.(); return {expanded: v?.expanded?.length, selected: v?.selected?.kind, revision: v?.revision, overview: document.querySelector('#atlas-field-network')?.dataset.layoutState, status: document.getElementById('atlas-status')?.textContent}; }")
        except Exception:  # noqa: BLE001 - diagnostics only
            context = None
        raise AssertionError(f"browser state did not converge: {expression} | state={context} | errors={self.errors[:2]}")

    def overview_ready(self, expected_nodes: int | None = None, timeout: float = 25.0) -> None:
        """Renderer ready AND positions stable for consecutive checks (stable-frame marker)."""
        self.wait("() => document.querySelector('#atlas-field-network')?.dataset.rendererState === 'ready' && Boolean(window.AtlasDebug.getOverviewGraph())", timeout)
        if expected_nodes is not None:
            self.wait("n => window.AtlasDebug.getOverviewGraph().getNodeData().length === n", timeout, expected_nodes)
        self.stable("overview", timeout)

    def stable(self, which: str, timeout: float = 25.0, frames: int = 4) -> None:
        selector = self.SELECTORS["overview" if which == "overview" else "concept" if which == "concept" else "paper"]
        # G6 force views publish data-layout-state: wait for 'no pending layout' before sampling frames
        self.wait("sel => { const c = document.querySelector(sel); return !c || ((!c.dataset.layoutState || c.dataset.layoutState === 'settled') && (!c.dataset.updateState || c.dataset.updateState === 'idle')); }", timeout, selector)
        self.wait(MOTION_IDLE, timeout)  # collection-map transition (camera tween, decorations) finished: positions are final
        script = "w => { const g = w === 'overview' ? window.AtlasDebug.getOverviewGraph() : w === 'concept' ? window.AtlasDebug.getConceptGraph() : window.AtlasDebug.getPaperGraph(); if (!g) return null; return g.getNodeData().map(n => g.getClientByCanvas(g.getElementPosition(n.id)).slice(0, 2).map(v => Math.round(v * 10) / 10).join(',')).join('|'); }"
        deadline, last, same = time.monotonic() + timeout, None, 0
        while time.monotonic() < deadline:
            current = self.page.evaluate(script, which)
            same = same + 1 if current is not None and current == last else 0
            last = current
            if same >= frames:
                return
            self.page.wait_for_timeout(40)
        raise AssertionError(f"{which} positions never stabilised")

    def view(self) -> dict[str, Any]:
        return self.page.evaluate("() => window.AtlasDebug.getViewState()")

    def client_point(self, which: str, node_id: str) -> dict[str, float]:
        point = self.page.evaluate("""([which, id]) => { const g = which === 'overview' ? window.AtlasDebug.getOverviewGraph() : which === 'concept' ? window.AtlasDebug.getConceptGraph() : window.AtlasDebug.getPaperGraph(); const c = g.getClientByCanvas(g.getElementPosition(id)); return {x: c[0], y: c[1]}; }""", [which, node_id])
        assert point and all(isinstance(point[k], (int, float)) for k in ("x", "y"))
        return point

    SELECTORS = {"overview": "#atlas-field-network", "paper": "#atlas-paper-network", "concept": "#atlas-concept-network"}

    def reveal(self, which: str) -> None:
        """Scroll the target canvas into the viewport: native mouse events only reach what is visible."""
        locator = self.page.locator(self.SELECTORS[which])
        locator.scroll_into_view_if_needed()
        last, same = None, 0
        for _ in range(100):  # wait until the scroll settled (box unchanged for 3 reads)
            box = locator.bounding_box()
            key = (round(box["x"], 1), round(box["y"], 1)) if box else None
            same = same + 1 if key == last else 0
            last = key
            if same >= 3:
                self.page.wait_for_timeout(450)  # Chromium wheel-scroll latching: wheel events right after a scroll still target the page
                return
            self.page.wait_for_timeout(30)
        raise AssertionError("canvas never settled after scrolling into view")

    def click_node(self, which: str, node_id: str) -> dict[str, float]:
        self.wait(MOTION_IDLE)  # a running camera tween would move the node under the click: read coordinates only once it is done
        self.reveal(which)
        point = self.client_point(which, node_id)
        box = self.page.locator(self.SELECTORS[which]).bounding_box()
        inside = lambda p: box["x"] <= p["x"] <= box["x"] + box["width"] and box["y"] <= p["y"] <= box["y"] + box["height"]
        if not inside(point):
            # Topology changes keep the camera by design; the user-visible recovery is the explicit "Fit new content" button.
            fit = self.page.locator("#atlas-fit-new-content")
            assert fit.is_visible(), f"{node_id} is outside the canvas and no Fit-new-content control is offered"
            fit.click()
            self.stable("overview" if which == "overview" else "paper", 20, frames=3)
            point = self.client_point(which, node_id)
            assert inside(point), f"{node_id} is still outside the canvas after Fit new content"
        self.page.mouse.click(point["x"], point["y"])
        return point

    def overview_nodes(self) -> list[dict[str, Any]]:
        return self.page.evaluate("() => window.AtlasDebug.getOverviewGraph().getNodeData().map(n => ({id: n.id, data: n.data}))")

    def pixel_painted(self, which: str, x: float, y: float) -> bool:
        """True when a 5x5 area under the client point has non-transparent canvas pixels (real drawing, not just node data)."""
        return bool(self.page.evaluate("""([sel, x, y]) => { for (const canvas of document.querySelectorAll(sel + ' canvas')) { const r = canvas.getBoundingClientRect(); const sx = canvas.width / r.width, sy = canvas.height / r.height; const px = Math.round((x - r.left) * sx), py = Math.round((y - r.top) * sy); if (px < 2 || py < 2 || px > canvas.width - 3 || py > canvas.height - 3) continue; let ctx; try { ctx = canvas.getContext('2d'); } catch (e) { ctx = null; } if (!ctx) continue; const data = ctx.getImageData(px - 2, py - 2, 5, 5).data; for (let i = 3; i < data.length; i += 4) if (data[i] > 0) return true; } return false; }""", ["#atlas-field-network" if which == "overview" else "#atlas-paper-network", x, y]))


@contextlib.contextmanager
def running_app(fixture: dict[str, Any], viewport: tuple[int, int] = (1440, 1000), color_scheme: str = "light", device_scale_factor: float | None = None) -> Iterator[Harness]:
    """Start the bridge and a browser; ALWAYS terminate the owned process in ``finally``."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - mandatory dependency, never skipped
        raise AssertionError(f"Playwright is mandatory for taxonomy v2 acceptance: {exc}") from exc
    if not DIST_APP.exists():
        raise AssertionError("dist/web/app.js is missing; run `npm run build` first")
    source_mtime = max(path.stat().st_mtime for path in (ROOT / "frontend").glob("*.js"))
    if DIST_APP.stat().st_mtime + 1 < source_mtime:
        raise AssertionError("dist/web/app.js is older than frontend sources; run `npm run build` (stale bundle would invalidate native proof)")
    validate_fixture(fixture)
    process: subprocess.Popen[str] | None = None
    with tempfile.TemporaryDirectory(prefix="atlas-taxonomy-v2-") as data_dir:
        fixture_path = Path(data_dir) / "fixture.json"
        fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
        try:
            process = subprocess.Popen([
                sys.executable, "-m", "atlas", "serve", "--demo", "--demo-fixture", str(fixture_path), "--embedding-provider", "fake", "--port", "0", "--data-dir", data_dir,
                "--web-root", str(ROOT / "dist/web"), "--poll-interval", "5", "--launch-id", "taxonomy-v2-acceptance", "--parent-origin", "app://obsidian.md",
            ], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            assert process.stdout is not None
            bootstrap = json.loads(process.stdout.readline())
            base = f"http://127.0.0.1:{bootstrap['port']}"
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    page = browser.new_page(viewport={"width": viewport[0], "height": viewport[1]}, color_scheme=color_scheme, **({"device_scale_factor": device_scale_factor} if device_scale_factor else {}))
                    errors: list[str] = []
                    page.on("pageerror", lambda error: errors.append(str(error)[:200]))
                    page.on("console", lambda message: errors.append(message.text[:200]) if message.type == "error" else None)
                    # load-phase (vendor parse/GC) long tasks are kept apart from interaction-phase tasks
                    page.add_init_script("window.__atlasLoadLongTasks = []; try { new PerformanceObserver(list => { for (const e of list.getEntries()) window.__atlasLoadLongTasks.push({start: e.startTime, duration: e.duration}); }).observe({type: 'longtask', buffered: true}); } catch (error) { window.__atlasLoadLongTasks = null; } document.addEventListener('click', event => { if (event.target instanceof Element && event.target.id === 'atlas-connect') window.__atlasConnectClickAt = performance.now(); }, true);")
                    page.goto(base + "/", wait_until="domcontentloaded")
                    page.locator("#atlas-token-input").fill(bootstrap["token"])
                    page.locator("#atlas-connect").click()
                    harness = Harness(page, browser, fixture, base, errors)
                    harness.wait("() => Boolean(window.AtlasDebug?.getState()?.snapshot?.papers?.length)")
                    yield harness
                finally:
                    browser.close()
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
