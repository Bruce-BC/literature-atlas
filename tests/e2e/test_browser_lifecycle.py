"""Browser E02 lifecycle replay through a real HTTP Zotero emulator.

The emulator is test-owned and in-memory. The production ZoteroClient uses its
normal urllib transport, while the browser uses the authenticated Atlas API.
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from atlas.server import AtlasHTTPServer
from atlas.storage import SQLiteStore
from atlas.sync import SyncManager
from atlas.zotero import ZoteroClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v2_support import open_advanced  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


class Source:
    def __init__(self):
        self.lock = threading.RLock()
        self.online = True
        self.items = {
            "A1": {"key": "A1", "version": 1, "data": {"itemType": "journalArticle", "title": "Lifecycle A", "creators": []}},
            "B2": {"key": "B2", "version": 1, "data": {"itemType": "journalArticle", "title": "Lifecycle B", "creators": []}},
            "C3": {"key": "C3", "version": 1, "data": {"itemType": "journalArticle", "title": "Lifecycle C", "creators": []}},
            "ATT1": {"key": "ATT1", "version": 1, "data": {"itemType": "attachment", "parentItem": "A1", "filename": "one.pdf", "path": "zotero://open-pdf/library/items/ATT1"}},
        }


class ZoteroHandler(BaseHTTPRequestHandler):
    source: Source

    def log_message(self, *_args):
        return

    def do_GET(self):
        if not self.source.online:
            self.send_error(503)
            return
        path = urllib.parse.urlsplit(self.path).path
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        with self.source.lock:
            items = list(self.source.items.values())
            if path.endswith("/fulltext") and "/items/" not in path:
                body = {}
            elif path.endswith("/file/view/url"):
                key = path.split("/")[-3]
                body = {"url": f"zotero://open-pdf/library/items/{key}"}
            elif "/items/" in path and path.endswith("/fulltext"):
                body = {"indexedPages": 1, "totalPages": 1}
            elif path.endswith("/collections"):
                body = {} if query.get("format") == ["versions"] else []
            elif query.get("format") == ["versions"]:
                body = {key: value["version"] for key, value in self.source.items.items()}
            else:
                body = items
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Zotero-Server-ID", "browser-lifecycle-source")
        self.send_header("Zotero-Schema-Version", "44")
        self.send_header("Last-Modified-Version", str(max((item["version"] for item in items), default=0)))
        self.end_headers()
        self.wfile.write(raw)


class FastStop:
    def __init__(self):
        self.event = threading.Event()

    def clear(self):
        self.event.clear()

    def set(self):
        self.event.set()

    def is_set(self):
        return self.event.is_set()

    def wait(self, _timeout):
        time.sleep(0.12)
        return self.event.is_set()


def wait_state(page, predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = page.evaluate("window.AtlasDebug?.getState()")
        if state and predicate(state):
            return state
        time.sleep(0.12)
    raise AssertionError("browser did not observe lifecycle state before timeout")


def wait_text(page, selector: str, predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate(page.locator(selector).inner_text()):
            return
        time.sleep(0.12)
    raise AssertionError(f"browser text state did not converge: {selector}")


def test_browser_real_source_lifecycle(tmp_path: Path):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        pytest.fail(f"Playwright is required for E02: {exc}")
    source = Source()
    ZoteroHandler.source = source
    zotero = ThreadingHTTPServer(("127.0.0.1", 0), ZoteroHandler)
    zotero_thread = threading.Thread(target=zotero.serve_forever, daemon=True); zotero_thread.start()
    manager = SyncManager(SQLiteStore(tmp_path / "atlas"), ZoteroClient(f"http://127.0.0.1:{zotero.server_port}/api"), embedding_provider="tfidf", poll_interval=5)
    manager._watch_stop = FastStop()
    manager.start_watcher()
    atlas = AtlasHTTPServer(("127.0.0.1", 0), manager, web_root=ROOT / "dist/web", parent_origin="app://obsidian.md", launch_id="browser-lifecycle")
    atlas_thread = threading.Thread(target=atlas.serve_forever, daemon=True); atlas_thread.start()
    try:
        import urllib.request
        bootstrap = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{atlas.server_port}/health").read().decode())
        # Bootstrap is normally emitted by the CLI; the in-process Atlas server
        # exposes the same values for this browser boundary test.
        bootstrap.update({"port": atlas.server_port, "token": atlas.token, "pid": atlas.pid, "launchId": "browser-lifecycle"})
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1024, "height": 900})
            page.goto(f"http://127.0.0.1:{atlas.server_port}/", wait_until="domcontentloaded")
            page.locator("#atlas-token-input").fill(bootstrap["token"])
            page.locator("#atlas-connect").click()
            wait_state(page, lambda s: {p["key"] for p in s["snapshot"]["papers"]} == {"A1", "B2", "C3"})
            open_advanced(page)  # the Detail slider lives in Advanced (AC-11)
            initial = page.evaluate("window.AtlasDebug.getState()")
            initial_overview = initial["snapshot"]["overview"]
            page.locator("#atlas-sample").fill("10"); page.locator("#atlas-sample").dispatch_event("input")
            assert page.evaluate("window.AtlasDebug.getState().snapshot.overview") == initial_overview

            with source.lock:
                source.items["D4"] = {"key": "D4", "version": 1, "data": {"itemType": "journalArticle", "title": "Lifecycle D added", "creators": []}}
            added = wait_state(page, lambda s: {p["key"] for p in s["snapshot"]["papers"]} == {"A1", "B2", "C3", "D4"})
            assert len(added["snapshot"]["papers"]) == 4
            assert added["snapshot"]["overview"]["totalPapers"] == 4

            with source.lock:
                source.items["A1"]["version"] = 2; source.items["A1"]["data"]["title"] = "Lifecycle A edited"
            edited = wait_state(page, lambda s: next((p for p in s["snapshot"]["papers"] if p["key"] == "A1"), {}).get("title") == "Lifecycle A edited")
            assert {p["key"] for p in edited["snapshot"]["papers"]} == {"A1", "B2", "C3", "D4"}
            assert edited["snapshot"]["overview"]["totalPapers"] == 4

            with source.lock:
                source.items.pop("ATT1")
                source.items["ATT2"] = {"key": "ATT2", "version": 3, "data": {"itemType": "attachment", "parentItem": "A1", "filename": "two.pdf", "path": "zotero://open-pdf/library/items/ATT2"}}
            replaced = wait_state(page, lambda s: {a["key"] for a in s["snapshot"]["attachments"]} == {"ATT2"})
            assert {p["key"] for p in replaced["snapshot"]["papers"]} == {"A1", "B2", "C3", "D4"}
            assert replaced["snapshot"]["overview"]["totalPapers"] == 4

            with source.lock:
                source.items["ATT2"]["version"] = 4; source.items["ATT2"]["data"]["deleted"] = True
            attachment_trashed = wait_state(page, lambda s: s["snapshot"]["attachments"] == [])
            assert {p["key"] for p in attachment_trashed["snapshot"]["papers"]} == {"A1", "B2", "C3", "D4"}
            with source.lock:
                source.items["ATT2"]["version"] = 5; source.items["ATT2"]["data"].pop("deleted")
            attachment_restored = wait_state(page, lambda s: {a["key"] for a in s["snapshot"]["attachments"]} == {"ATT2"})
            assert {p["key"] for p in attachment_restored["snapshot"]["papers"]} == {"A1", "B2", "C3", "D4"}

            with source.lock:
                source.items["A1"]["version"] = 6; source.items["A1"]["data"]["deleted"] = True
            parent_trashed = wait_state(page, lambda s: "A1" not in {p["key"] for p in s["snapshot"]["papers"]})
            assert {p["key"] for p in parent_trashed["snapshot"]["papers"]} == {"B2", "C3", "D4"}
            with source.lock:
                source.items["A1"]["version"] = 7; source.items["A1"]["data"].pop("deleted")
            parent_restored = wait_state(page, lambda s: "A1" in {p["key"] for p in s["snapshot"]["papers"]})
            assert {p["key"] for p in parent_restored["snapshot"]["papers"]} == {"A1", "B2", "C3", "D4"}
            with source.lock:
                source.items.pop("A1"); source.items.pop("ATT2")
            hard_deleted = wait_state(page, lambda s: "A1" not in {p["key"] for p in s["snapshot"]["papers"]})
            assert {p["key"] for p in hard_deleted["snapshot"]["papers"]} == {"B2", "C3", "D4"}

            source.online = False
            wait_text(page, "#atlas-status", lambda value: "offline" in value.lower() or "stale" in value.lower())
            with source.lock:
                source.online = True
                source.items["A1"] = {"key": "A1", "version": 8, "data": {"itemType": "journalArticle", "title": "Lifecycle A reconnected", "creators": []}}
            final = wait_state(page, lambda s: next((p for p in s["snapshot"]["papers"] if p["key"] == "A1"), {}).get("title") == "Lifecycle A reconnected")
            assert {p["key"] for p in final["snapshot"]["papers"]} == {"A1", "B2", "C3", "D4"}
            assert final["snapshot"]["overview"]["totalPapers"] == 4
            browser.close()
    finally:
        atlas.shutdown(); atlas.server_close(); manager.stop_watcher(); zotero.shutdown(); zotero.server_close()
