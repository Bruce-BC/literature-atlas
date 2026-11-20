"""Real-pipeline harness for taxonomy/interaction v2 integration tests.

Starts the REAL ``AtlasHTTPServer`` + ``SyncManager`` + ``GraphEngine`` (deterministic
``fake`` embeddings, no model download) over a synthetic Zotero transport and a
temporary derived SQLite store, then drives a real browser against the built,
authenticated API.  There is no test-only HTTP endpoint, no Zotero mutation (the
synthetic transport is an in-memory object), and the browser only ever sees what the
normal poll/connect path delivers.  ``AtlasDebug`` stays a read-only observer.
"""
from __future__ import annotations

import contextlib
import json
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path
from typing import Any, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v2_support import DIST_APP, ROOT, Harness, legacy_screen  # noqa: E402

sys.path.insert(0, str(ROOT))
from atlas.graph import GraphEngine  # noqa: E402
from atlas.server import AtlasHTTPServer  # noqa: E402
from atlas.storage import SQLiteStore  # noqa: E402
from atlas.sync import SyncManager  # noqa: E402
from atlas.zotero import ZoteroClient  # noqa: E402


class SyntheticZotero:
    """In-memory stand-in for the Zotero local API transport (read-only protocol, synthetic items)."""

    def __init__(self, server_id: str = "synthetic-pipeline-db") -> None:
        self.server_id = server_id
        self.items: dict[str, dict[str, Any]] = {}
        self.version = 1

    def add(self, key: str, title: str, abstract: str, tags: tuple[str, ...] = (), year: str = "2024") -> str:
        self.version += 1
        self.items[key] = {"key": key, "version": self.version, "data": {
            "itemType": "journalArticle", "title": title, "abstractNote": abstract, "tags": [{"tag": tag} for tag in tags],
            "DOI": "", "date": year, "creators": [{"creatorType": "author", "firstName": "A", "lastName": "Author"}]}}
        return self.paper_id(key)

    def remove(self, key: str) -> None:
        self.version += 1
        del self.items[key]

    def paper_id(self, key: str) -> str:
        return f"zotero:{self.server_id}:0:{key}"

    def __call__(self, method: str, url: str, headers: dict[str, str]) -> dict[str, Any]:
        response_headers = {"Zotero-Server-ID": self.server_id, "Zotero-Schema-Version": "44"}
        if "/collections" in url:
            return {"status": 200, "headers": response_headers, "body": {} if "format=versions" in url else []}
        if "format=versions" in url:
            return {"status": 200, "headers": response_headers, "body": {key: row["version"] for key, row in self.items.items()}}
        return {"status": 200, "headers": response_headers, "body": list(self.items.values())}


class ControlledEngine:
    """Wraps the real GraphEngine: can hold a build (slow engine) and stamp another recipeVersion on its real output."""

    def __init__(self, inner: GraphEngine) -> None:
        self.inner = inner
        self.recipe: str | None = None
        self._hold = threading.Event()
        self._hold.set()
        self.entered = threading.Event()

    def block(self) -> None:
        self.entered.clear()
        self._hold.clear()

    def release(self) -> None:
        self._hold.set()

    def build(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.entered.set()
        if not self._hold.wait(timeout=120):
            raise TimeoutError("controlled engine was never released")
        result = self.inner.build(*args, **kwargs)
        if self.recipe:
            result["modelStatus"] = {**result.get("modelStatus", {}), "recipeVersion": self.recipe}
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


class Pipeline:
    def __init__(self, harness: Harness, manager: SyncManager, source: SyntheticZotero, engine: ControlledEngine, server: AtlasHTTPServer) -> None:
        self.h, self.manager, self.source, self.engine, self.server = harness, manager, source, engine, server

    def api(self, path: str) -> Any:
        request = urllib.request.Request(f"http://127.0.0.1:{self.server.server_port}{path}", headers={"Authorization": f"Bearer {self.server.token}", "Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))

    def snapshot(self) -> dict[str, Any]:
        return self.api("/api/v1/snapshot?projection=full&sample=1")

    def sync(self) -> None:
        self.manager.sync(trigger="test")

    def wait_idle(self, timeout: float = 60) -> None:
        self.manager.wait_for_idle(timeout)


def qiime_items(source: SyntheticZotero) -> dict[str, str]:
    """The two reviewed contract cases: QIIME2 + amplicon (bioinformatics) and phage therapy using QIIME2."""
    return {
        "qiime": source.add("QIIME01", "Microbiome amplicon sequencing analysis with QIIME 2", "We analyse 16S rRNA amplicon sequencing data of the gut microbiome with QIIME2 and DADA2 to profile community composition.", ("microbiome",)),
        "phage": source.add("PHAGE01", "Phage therapy against multidrug-resistant infections", "Bacteriophage therapy cocktails were evaluated; community profiles were summarised using QIIME2.", ("phage",)),
    }


@contextlib.contextmanager
def running_pipeline(populate: Any, viewport: tuple[int, int] = (1440, 1000)) -> Iterator[Pipeline]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - mandatory dependency, never skipped
        raise AssertionError(f"Playwright is mandatory: {exc}") from exc
    if not DIST_APP.exists():
        raise AssertionError("dist/web/app.js is missing; run `npm run build` first")
    source_mtime = max(path.stat().st_mtime for path in (ROOT / "frontend").glob("*.js"))
    if DIST_APP.stat().st_mtime + 1 < source_mtime:
        raise AssertionError("dist/web/app.js is older than frontend sources; run `npm run build`")
    source = SyntheticZotero()
    populate(source)
    server: AtlasHTTPServer | None = None
    manager: SyncManager | None = None
    with tempfile.TemporaryDirectory(prefix="atlas-v2-pipeline-") as data_dir:
        root = Path(data_dir)
        try:
            engine = ControlledEngine(GraphEngine(root / "models", provider="fake", allow_download=False))
            manager = SyncManager(SQLiteStore(root), ZoteroClient(transport=source), graph_engine=engine, poll_interval=300)  # the watcher is never started; syncs are test-driven through manager.sync()
            manager.sync()
            manager.wait_for_idle(60)
            server = AtlasHTTPServer(("127.0.0.1", 0), manager, web_root=ROOT / "dist" / "web", launch_id="taxonomy-v2-pipeline", parent_origin="app://obsidian.md")
            threading.Thread(target=server.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{server.server_port}"
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    page = browser.new_page(viewport={"width": viewport[0], "height": viewport[1]}, color_scheme="light")
                    errors: list[str] = []
                    page.on("pageerror", lambda error: errors.append(str(error)[:200]))
                    page.on("console", lambda message: errors.append(message.text[:200]) if message.type == "error" else None)
                    page.goto(base + "/", wait_until="domcontentloaded")
                    page.locator("#atlas-token-input").fill(server.token)
                    page.locator("#atlas-connect").click()
                    harness = Harness(page, browser, {}, base, errors)
                    harness.wait("() => Boolean(window.AtlasDebug?.getState()?.snapshot?.papers?.length)", 30)
                    legacy_screen(page)  # pipeline tests drive legacy controls: Advanced open + Circular ribbons, the pre-AC-11 default
                    yield Pipeline(harness, manager, source, engine, server)
                finally:
                    engine.release()
                    browser.close()
        finally:
            if server is not None:
                server.shutdown()
                server.server_close()
            if manager is not None:
                manager.stop_watcher()
