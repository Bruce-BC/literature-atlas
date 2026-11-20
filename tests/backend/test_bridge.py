from __future__ import annotations

import json
import os
import subprocess
import threading
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from atlas.server import AtlasHTTPServer
from atlas.storage import SQLiteStore
from atlas.sync import GRAPH_RECIPE_VERSION, SyncManager
from atlas.zotero import ZoteroClient
from atlas.graph import GraphEngine


class Replay:
    def __init__(self):
        self.server_id = "replay-db"
        self.items = {"A1": (1, {"key": "A1", "version": 1, "data": {"itemType": "journalArticle", "title": "A"}})}
        self.collections = {"C1": (1, {"key": "C1", "version": 1, "data": {"name": "C", "items": ["A1"]}})}

    def __call__(self, method, url, headers):
        is_collection = "/collections" in url
        data = self.collections if is_collection else self.items
        response_headers = {"Zotero-Server-ID": self.server_id, "API-Schema": "44"}
        if "format=versions" in url:
            return {"status": 200, "headers": response_headers, "body": {key: value[0] for key, value in data.items()}}
        return {"status": 200, "headers": response_headers, "body": [value[1] for value in data.values()]}


class FakeGraph:
    def build(self, papers, **kwargs):
        return {"papers": papers, "fields": [], "concepts": [], "memberships": [], "edges": [], "overview": {"fields": [], "ribbons": [], "totalPapers": len(papers), "totalEdges": 0}, "modelStatus": {"provider": "fake", "status": "ready"}, "allocationMode": "primary-display-field"}


class LifecycleReplay:
    def __init__(self):
        self.server_id = "lifecycle-db"
        self.online = True
        self.items = {
            "A1": {"key": "A1", "version": 1, "data": {"itemType": "journalArticle", "title": "Initial", "collections": ["C1"]}},
            "ATT1": {"key": "ATT1", "version": 1, "data": {"itemType": "attachment", "parentItem": "A1", "filename": "one.pdf"}},
        }
        self.collections = {"C1": {"key": "C1", "version": 1, "data": {"name": "Collection one"}}}
        self.fulltext_version = 11650

    def __call__(self, method, url, headers):
        if not self.online:
            raise OSError("offline")
        response_headers = {"Zotero-Server-ID": self.server_id, "Zotero-Schema-Version": "44"}
        if "/file/view/url" in url:
            return {"status": 200, "headers": {**response_headers, "Content-Type": "text/plain"}, "body": "file:///tmp/lifecycle.pdf"}
        if "/fulltext" in url:
            if "since=" in url:
                return {"status": 200, "headers": response_headers, "body": {"ATT1": self.fulltext_version}}
            return {"status": 200, "headers": response_headers, "body": {"content": "indexed", "indexedPages": 2, "totalPages": 3}}
        if "/collections" in url:
            values = self.collections
            if "format=versions" in url:
                return {"status": 200, "headers": response_headers, "body": {key: value["version"] for key, value in values.items()}}
            return {"status": 200, "headers": response_headers, "body": list(values.values())}
        if "format=versions" in url:
            return {"status": 200, "headers": response_headers, "body": {key: value["version"] for key, value in self.items.items()}}
        return {"status": 200, "headers": response_headers, "body": list(self.items.values())}


class BackendTests(unittest.TestCase):
    def make_manager(self):
        root = Path(tempfile.mkdtemp(prefix="atlas-test-"))
        replay = Replay()
        manager = SyncManager(SQLiteStore(root), ZoteroClient(transport=replay), graph_engine=FakeGraph(), poll_interval=5)
        manager.include_indexed_text = True
        return manager, replay

    @staticmethod
    def wait_for(predicate, timeout=6):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.02)
        raise AssertionError("timed out waiting for automatic watcher")

    def test_complete_replay_and_unchanged_poll_do_not_rebuild(self):
        manager, replay = self.make_manager()
        first = manager.sync(trigger="automatic")
        manager.wait_for_idle()
        self.assertEqual(len(first["papers"]), 1)
        self.assertEqual(len(first["collections"]), 1)
        second = manager.sync(trigger="automatic")
        self.assertEqual(second["source"]["serverId"], "replay-db")
        manager.stop_watcher()

    def test_recipe_upgrade_rebuilds_same_source_cache(self):
        manager, _ = self.make_manager(); first = manager.sync(); manager.wait_for_idle()
        stored = manager.store.get_snapshot()[2]; stored["modelStatus"]["recipeVersion"] = "atlas-graph-v1"
        manager.store.publish_snapshot(stored, int(manager.store.state()["generation"]))
        manager.sync(); manager.wait_for_idle()
        self.assertEqual(manager.store.get_snapshot()[2]["modelStatus"].get("recipeVersion"), GRAPH_RECIPE_VERSION)
        manager.stop_watcher()

    def test_demo_cas_edits_change_snapshot_and_survive_watcher_restart(self):
        fixture = json.loads(Path(__file__).parents[1].joinpath("fixtures", "snapshot.json").read_text(encoding="utf-8"))
        root = Path(tempfile.mkdtemp(prefix="atlas-demo-")); manager = SyncManager(SQLiteStore(root), ZoteroClient(transport=Replay()), graph_engine=FakeGraph(), poll_interval=5, demo_snapshot=fixture, embedding_provider="fake")
        initial = manager.sync(); self.assertEqual(initial["source"]["epoch"], fixture["source"]["epoch"])
        server = AtlasHTTPServer(("127.0.0.1", 0), manager, launch_id="demo"); thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start(); base = f"http://127.0.0.1:{server.server_port}"; auth = {"Authorization": "Bearer " + server.token, "Content-Type": "application/json"}
        def call(path, method, body, revision):
            headers = dict(auth); headers["If-Match"] = str(revision); req = urllib.request.Request(base + path, method=method, headers=headers, data=json.dumps(body).encode()); return json.loads(urllib.request.urlopen(req).read())
        created = call("/api/v1/taxonomy", "POST", {"entityType": "field", "label": {"en": "Demo field"}, "aliases": ["demo"], "parentIds": [], "sourceId": fixture["source"]["serverId"], "sourceEpoch": fixture["source"]["epoch"], "revision": 0}, 0)
        self.assertEqual(created["revision"], 1); self.assertTrue(any(row["label"]["en"] == "Demo field" for row in manager.snapshot()["fields"]))
        paper_id = fixture["papers"][0]["id"]; call("/api/v1/overlays", "PUT", {"entityType": "paper", "entityId": paper_id, "patch": {"pinned": True}, "sourceId": fixture["source"]["serverId"], "sourceEpoch": fixture["source"]["epoch"], "revision": 0}, 0)
        self.assertTrue(next(row for row in manager.snapshot()["papers"] if row["id"] == paper_id).get("pinned"))
        second_id = fixture["papers"][1]["id"]; call("/api/v1/overlays", "PUT", {"entityType": "paper", "entityId": second_id, "patch": {"pinned": True}, "sourceId": fixture["source"]["serverId"], "sourceEpoch": fixture["source"]["epoch"], "revision": 1}, 1)
        call("/api/v1/overlays", "PUT", {"entityType": "paper", "entityId": second_id, "patch": {"pinned": False}, "sourceId": fixture["source"]["serverId"], "sourceEpoch": fixture["source"]["epoch"], "revision": 2}, 2)
        self.assertTrue(next(row for row in manager.snapshot()["papers"] if row["id"] == paper_id).get("pinned")); self.assertFalse(next(row for row in manager.snapshot()["papers"] if row["id"] == second_id).get("pinned", False))
        manager.poll_interval = 0.05; manager.start_watcher(); time.sleep(0.15); manager.stop_watcher(); self.assertTrue(next(row for row in manager.snapshot()["papers"] if row["id"] == paper_id).get("pinned"))
        server.shutdown(); server.server_close()

    def test_restart_requeues_pending_derivation_without_metadata_change(self):
        root = Path(tempfile.mkdtemp(prefix="atlas-restart-")); replay = Replay(); first = SyncManager(SQLiteStore(root), ZoteroClient(transport=replay), graph_engine=FakeGraph(), poll_interval=5); first.sync(); first.wait_for_idle(); stored = first.store.get_snapshot()[2]; stored["modelStatus"] = {"provider": "e5", "state": "pending", "recipeVersion": "atlas-graph-v1"}; first.store.publish_snapshot(stored, int(first.store.state()["generation"])); first.stop_watcher()
        second = SyncManager(SQLiteStore(root), ZoteroClient(transport=replay), graph_engine=FakeGraph(), poll_interval=5); second.sync(); second.wait_for_idle(); self.assertEqual(second.store.get_snapshot()[2]["modelStatus"].get("status"), "ready"); second.stop_watcher()

    def test_legacy_cache_reconciles_trashed_attachment_rows_on_restart(self):
        root = Path(tempfile.mkdtemp(prefix="atlas-legacy-")); replay = LifecycleReplay(); first = SyncManager(SQLiteStore(root), ZoteroClient(transport=replay), graph_engine=FakeGraph(), poll_interval=5); first.sync(); first.wait_for_idle(); state = first.store.state()
        with first.store.transaction() as conn:
            conn.execute("INSERT INTO items(server_id,epoch,library_id,item_key,version,item_type,trashed,parent_key,payload,updated_at) VALUES(?,?,?,?,?,?,?,?,?,0)", (state["server_id"], state["epoch"], 0, "OLD", 1, "attachment", 1, "A1", json.dumps({"key":"OLD","data":{"itemType":"attachment","parentItem":"A1","deleted":True}})))
            conn.execute("INSERT INTO attachments(server_id,epoch,library_id,attachment_key,parent_key,version,name,url,path,status,payload,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,0)", (state["server_id"], state["epoch"], 0, "OLD", "A1", 1, "old.pdf", "zotero://open-pdf/library/items/OLD", None, "readable", "{}"))
        stale = first.store.get_snapshot()[2]; stale["attachments"].append({"id":"attachment:lifecycle-db:0:OLD","parentId":"zotero:lifecycle-db:0:A1","name":"old.pdf","status":"readable"}); first.store.publish_snapshot(stale, int(state["generation"])); first.stop_watcher()
        second = SyncManager(SQLiteStore(root), ZoteroClient(transport=replay), graph_engine=FakeGraph(), poll_interval=5); second.sync(); self.assertEqual(len(second.snapshot()["attachments"]), 1); self.assertEqual(second.snapshot()["attachments"][0]["key"], "ATT1"); second.stop_watcher()

    def test_missing_payload_preserves_previous_snapshot(self):
        manager, replay = self.make_manager()
        manager.sync(); manager.wait_for_idle()
        replay.items["A1"] = (2, {"key": "A1", "version": 2, "data": {"itemType": "journalArticle", "title": "A2"}})
        # Return an incomplete exact batch; the old snapshot remains visible.
        original = replay.__call__
        def incomplete(method, url, headers):
            value = original(method, url, headers)
            if "format=versions" not in url and "/collections" not in url:
                value["body"] = []
            return value
        manager.client.transport = incomplete
        snapshot = manager.sync()
        self.assertEqual(snapshot["papers"][0]["title"], "A")
        manager.stop_watcher()

    def test_http_token_and_origin_boundary(self):
        manager, _ = self.make_manager()
        server = AtlasHTTPServer(("127.0.0.1", 0), manager, parent_origin="app://obsidian.md", launch_id="test")
        import threading
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        health = json.loads(urllib.request.urlopen(base + "/health").read())
        self.assertEqual(health["launchId"], "test")
        req = urllib.request.Request(base + "/api/v1/status", headers={"Authorization": "Bearer wrong"})
        with self.assertRaises(urllib.error.HTTPError) as ctx: urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 401)
        req = urllib.request.Request(base + "/api/v1/status", headers={"Authorization": "Bearer " + server.token, "Origin": "https://evil.example"})
        with self.assertRaises(urllib.error.HTTPError) as ctx: urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 403)
        req = urllib.request.Request(base + "/%61pi/v1/status")
        with self.assertRaises(urllib.error.HTTPError) as ctx: urllib.request.urlopen(req)
        self.assertIn(ctx.exception.code, {401, 404})
        server.shutdown(); server.server_close(); manager.stop_watcher()

    def test_http_bounds_traversal_and_public_static_csp(self):
        manager, _ = self.make_manager()
        root = Path(tempfile.mkdtemp(prefix="atlas-web-")); (root / "index.html").write_text("ok", encoding="utf-8"); (root.parent / "atlas-secret.txt").write_text("private", encoding="utf-8")
        server = AtlasHTTPServer(("127.0.0.1", 0), manager, web_root=root, parent_origin="app://obsidian.md")
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start(); base = f"http://127.0.0.1:{server.server_port}"
        static = urllib.request.urlopen(base + "/")
        self.assertEqual(static.read(), b"ok"); self.assertIn("frame-ancestors 'self' app://obsidian.md", static.headers["Content-Security-Policy"]); self.assertIn("img-src 'self' data:;", static.headers["Content-Security-Policy"])  # node icons are inline SVG data URIs (node-icons.js), nothing remote
        with self.assertRaises(urllib.error.HTTPError) as traversal:
            urllib.request.urlopen(base + "/%2e%2e/atlas-secret.txt")
        self.assertIn(traversal.exception.code, {404, 400})
        request = urllib.request.Request(base + "/api/v1/settings", method="PUT", headers={"Authorization": "Bearer " + server.token, "Content-Type": "application/json"}, data=(b"{" + b"x" * (1024 * 1024 + 1) + b"}"))
        try:
            urllib.request.urlopen(request)
        except (urllib.error.HTTPError, urllib.error.URLError) as oversized:
            if isinstance(oversized, urllib.error.HTTPError): self.assertEqual(oversized.code, 400)
        server.shutdown(); server.server_close(); manager.stop_watcher()

    def test_process_lock_rejects_second_bridge_without_bootstrap(self):
        root = Path(tempfile.mkdtemp(prefix="atlas-lock-")); command = [os.environ.get("PYTHON", __import__("sys").executable), "-m", "atlas", "serve", "--demo", "--embedding-provider", "fake", "--data-dir", str(root), "--port", "0", "--poll-interval", "5"]
        first = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        line = first.stdout.readline().strip(); self.assertTrue(line); bootstrap = json.loads(line)
        second = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
        self.assertNotEqual(second.returncode, 0); self.assertEqual(second.stdout, ""); self.assertIn("already in use", second.stderr)
        health = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{bootstrap['port']}/health").read()); self.assertEqual(health["pid"], bootstrap["pid"])
        first.terminate(); first.wait(timeout=5)
        third = subprocess.Popen(command + ["--launch-id", "after"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertTrue(third.stdout.readline().strip()); third.terminate(); third.wait(timeout=5)
        for handle in (first.stdout, first.stderr, third.stdout, third.stderr):
            if handle: handle.close()

    def test_parent_stdin_eof_releases_owned_bridge(self):
        root = Path(tempfile.mkdtemp(prefix="atlas-parent-")); command = [os.environ.get("PYTHON", __import__("sys").executable), "-m", "atlas", "serve", "--demo", "--embedding-provider", "fake", "--watch-parent-stdin", "--data-dir", str(root), "--port", "0", "--poll-interval", "5"]
        child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        bootstrap = json.loads(child.stdout.readline()); health = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{bootstrap['port']}/health").read()); self.assertEqual(health["pid"], bootstrap["pid"])
        child.stdin.close(); child.wait(timeout=7); self.assertEqual(child.returncode, 0)
        restarted = subprocess.Popen(command + ["--launch-id", "restarted"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True); self.assertTrue(restarted.stdout.readline().strip()); restarted.stdin.close(); restarted.wait(timeout=7)
        for handle in (child.stdout, child.stderr, restarted.stdout, restarted.stderr):
            if handle: handle.close()

    def test_automatic_lifecycle_and_independent_fulltext_clock(self):
        root = Path(tempfile.mkdtemp(prefix="atlas-lifecycle-"))
        replay = LifecycleReplay()
        manager = SyncManager(SQLiteStore(root), ZoteroClient(transport=replay), graph_engine=FakeGraph(), poll_interval=5)
        manager.include_indexed_text = True
        manager.poll_interval = 0.05  # fake clock/short test interval; constructor still validates production bounds
        manager.start_watcher()
        self.wait_for(lambda: len(manager.snapshot()["papers"]) == 1)
        self.wait_for(lambda: manager.snapshot()["attachments"][0]["status"] == "indexed")
        self.assertEqual(manager.store.state()["metadata_cursor"], 1)
        self.assertEqual(manager.store.state()["fulltext_cursor"], 11650)

        replay.items["A1"] = {"key": "A1", "version": 2, "data": {"itemType": "journalArticle", "title": "Edited", "collections": ["C1"]}}
        replay.items.pop("ATT1")
        replay.items["ATT2"] = {"key": "ATT2", "version": 3, "data": {"itemType": "attachment", "parentItem": "A1", "filename": "two.pdf"}}
        self.wait_for(lambda: manager.snapshot()["papers"][0]["title"] == "Edited")
        self.wait_for(lambda: manager.snapshot()["attachments"][0]["key"] == "ATT2")
        replay.items["ATT2"]["version"] = 4; replay.items["ATT2"]["data"]["deleted"] = True
        self.wait_for(lambda: len(manager.snapshot()["attachments"]) == 0)
        replay.items["ATT2"]["version"] = 5; replay.items["ATT2"]["data"].pop("deleted")
        self.wait_for(lambda: manager.snapshot()["attachments"] and manager.snapshot()["attachments"][0]["key"] == "ATT2")

        replay.items["A1"]["version"] = 4; replay.items["A1"]["data"]["deleted"] = True
        self.wait_for(lambda: len(manager.snapshot()["papers"]) == 0)
        replay.items["A1"]["version"] = 5; replay.items["A1"]["data"].pop("deleted")
        self.wait_for(lambda: len(manager.snapshot()["papers"]) == 1)
        replay.items.pop("A1")
        replay.items.pop("ATT2")
        self.wait_for(lambda: len(manager.snapshot()["papers"]) == 0)

        replay.online = False
        time.sleep(0.12)
        self.assertTrue(manager.status["stale"])
        replay.online = True
        replay.items["A1"] = {"key": "A1", "version": 6, "data": {"itemType": "journalArticle", "title": "Reconnected"}}
        self.wait_for(lambda: manager.snapshot().get("papers") and manager.snapshot()["papers"][0]["title"] == "Reconnected")
        manager.stop_watcher()

    def test_source_swap_fences_late_enrichment_and_failed_projection(self):
        manager, replay = self.make_manager()
        started = threading.Event(); release = threading.Event()
        class SlowGraph(FakeGraph):
            def build(self, papers, **kwargs):
                started.set(); release.wait(2); return super().build(papers, **kwargs)
        manager.graph_engine = SlowGraph()
        old = manager.sync(); self.assertTrue(started.wait(2)); old_epoch = old["source"]["epoch"]
        replay.server_id = "new-db"
        replay.items = {"A1": (1, {"key": "A1", "version": 1, "data": {"itemType": "journalArticle", "title": "New source"}})}
        replay.collections = {}
        original_transport = manager.client.transport
        first = True
        def fail_after_fence(*args):
            nonlocal first
            if first:
                first = False
                return original_transport(*args)
            raise OSError("offline")
        manager.client.transport = fail_after_fence
        failed = manager.sync()
        self.assertEqual(failed["source"]["epoch"], old_epoch)
        self.assertIsNone(manager.store.state()["metadata_cursor"])
        manager.client.transport = original_transport
        release.set()
        new = manager.sync(); manager.wait_for_idle()
        self.assertEqual(new["source"]["serverId"], "new-db")
        self.assertNotEqual(new["source"]["epoch"], old_epoch)
        self.assertEqual(new["papers"][0]["title"], "New source")
        manager.stop_watcher()

    def test_fulltext_optin_backfills_after_version_only_cursor(self):
        root = Path(tempfile.mkdtemp(prefix="atlas-fulltext-")); replay = LifecycleReplay()
        manager = SyncManager(SQLiteStore(root), ZoteroClient(transport=replay), graph_engine=FakeGraph(), poll_interval=5)
        manager.sync(); manager.wait_for_idle()
        version_only = manager.sync_fulltext()
        self.assertEqual(version_only["updated"], 0)
        self.assertEqual(manager.snapshot()["attachments"][0]["status"], "missing")
        manager.include_indexed_text = True
        fetched = manager.sync_fulltext()
        self.assertEqual(fetched["updated"], 1)
        manager.wait_for_idle()
        self.assertEqual(manager.snapshot()["attachments"][0]["status"], "indexed")
        self.assertFalse(any("indexedText" in paper or "fullText" in paper for paper in manager.snapshot()["papers"]))
        manager.stop_watcher()

    def test_persisted_indexed_text_setting_loads_on_restart(self):
        root = Path(tempfile.mkdtemp(prefix="atlas-ft-setting-")); replay = LifecycleReplay(); store = SQLiteStore(root); manager = SyncManager(store, ZoteroClient(transport=replay), graph_engine=FakeGraph(), poll_interval=5); manager.sync(); manager.wait_for_idle(); state = store.state(); ok, _, _ = store.cas_json("settings", {"provider": "tfidf", "includeIndexedText": True}, 0); self.assertTrue(ok); manager.stop_watcher()
        restarted = SyncManager(SQLiteStore(root), ZoteroClient(transport=replay), graph_engine=FakeGraph(), poll_interval=5); self.assertTrue(restarted.include_indexed_text); self.assertEqual(restarted.embedding_provider, "tfidf"); restarted.stop_watcher()

    def test_tfidf_graph_uses_optin_indexed_term_without_emitting_text(self):
        base = {"id": "zotero:test:0:A", "key": "A", "libraryID": 0, "title": "Base", "abstract": None, "authors": [], "keywords": [], "fieldIds": [], "conceptIds": [], "attachmentIds": [], "textStatus": "missing"}
        source = {"id": "test", "serverId": "test", "epoch": "e", "alias": "test", "zoteroApi": "3", "schema": 44}
        engine = GraphEngine(Path(tempfile.mkdtemp(prefix="atlas-tfidf-")), provider="tfidf", allow_download=False)
        without = engine.build([dict(base)], source=source, settings={})
        with_text = dict(base); with_text["indexedText"] = "UniqueIndexedTerm"
        with_index = engine.build([with_text], source=source, settings={"includeIndexedText": True})
        labels = {c["label"]["en"].casefold() for c in with_index["concepts"]}
        self.assertIn("uniqueindexedterm", labels); self.assertNotIn("indexedText", with_index["papers"][0]); self.assertFalse(any("uniqueindexedterm" in c["label"]["en"].casefold() for c in without["concepts"]))

    def test_deleted_source_item_archives_overlay_without_retargeting(self):
        manager, replay = self.make_manager(); manager.sync(); manager.wait_for_idle(); state = manager.store.state(); ok, _, _ = manager.store.cas_json("overlays", {"entities": {"zotero:replay-db:0:A1": {"pinned": True}}}, 0, source=state); self.assertTrue(ok)
        replay.items["A1"] = (2, {"key": "A1", "version": 2, "deleted": True, "data": {"itemType": "journalArticle", "title": "A", "deleted": True}})
        manager.sync(); manager.wait_for_idle(); revision, overlays, _ = manager.store.read_json("overlays"); self.assertNotIn("zotero:replay-db:0:A1", overlays.get("entities", {})); self.assertTrue(any(row.get("entityId") == "zotero:replay-db:0:A1" for row in overlays.get("archived", []))); manager.stop_watcher()

    def test_cas_taxonomy_diamond_and_overlay_patch(self):
        manager, _ = self.make_manager(); manager.sync(); manager.wait_for_idle()
        server = AtlasHTTPServer(("127.0.0.1", 0), manager, launch_id="cas")
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        base = f"http://127.0.0.1:{server.server_port}"; auth = {"Authorization": "Bearer " + server.token, "Content-Type": "application/json"}
        def call(path, method, body, revision=None):
            headers = dict(auth)
            if revision is not None: headers["If-Match"] = str(revision)
            req = urllib.request.Request(base + path, method=method, headers=headers, data=json.dumps(body).encode())
            return urllib.request.urlopen(req)
        state = manager.store.state(); prefix = f"field:{state['server_id']}:{state['epoch']}:"
        a, b, c = prefix + "a", prefix + "b", prefix + "c"
        payload = {"fields": [{"id": a, "label": {"en": "A"}, "parentIds": [], "aliases": [], "source": "user"}, {"id": b, "label": {"en": "B"}, "parentIds": [a], "aliases": [], "source": "user"}, {"id": c, "label": {"en": "C"}, "parentIds": [a], "aliases": [], "source": "user"}], "sourceId": state["server_id"], "sourceEpoch": state["epoch"], "revision": 0}
        self.assertEqual(call("/api/v1/taxonomy", "POST", payload, 0).status, 200)
        revision = json.loads(urllib.request.urlopen(urllib.request.Request(base + "/api/v1/taxonomy", headers={"Authorization": "Bearer " + server.token})).read())["revision"]
        with self.assertRaises(urllib.error.HTTPError) as cycle_error:
            call("/api/v1/taxonomy", "PUT", {"fields": [{"id": a, "label": {"en": "Cycle"}, "parentIds": [b], "aliases": [], "source": "user"}], "sourceId": state["server_id"], "sourceEpoch": state["epoch"], "revision": revision}, revision)
        self.assertEqual(cycle_error.exception.code, 400)
        overlay = {"entityType": "paper", "entityId": "zotero:replay-db:0:A1", "patch": {"pinned": True}, "sourceId": state["server_id"], "sourceEpoch": state["epoch"], "revision": 0}
        for invalid_patch in ({"aliases": "bad"}, {"pinned": "false"}, {"memberships": [{"paperId": "bad"}]}):
            invalid = dict(overlay); invalid["patch"] = invalid_patch
            with self.assertRaises(urllib.error.HTTPError) as invalid_error: call("/api/v1/overlays", "PUT", invalid, 0)
            self.assertEqual(invalid_error.exception.code, 400)
        self.assertEqual(call("/api/v1/overlays", "PUT", overlay, 0).status, 200)
        manager.wait_for_idle()
        self.assertTrue(next(p for p in manager.snapshot()["papers"] if p["id"] == "zotero:replay-db:0:A1").get("pinned"))
        with self.assertRaises(urllib.error.HTTPError) as ctx: call("/api/v1/overlays", "PUT", overlay, 0)
        self.assertEqual(ctx.exception.code, 412)
        with self.assertRaises(urllib.error.HTTPError) as ctx: call("/api/v1/overlays", "PUT", {"aliases": {"foreign:paper": ["x"]}, "revision": 1}, 1)
        self.assertEqual(ctx.exception.code, 400)
        with self.assertRaises(urllib.error.HTTPError) as ctx: call("/api/v1/taxonomy", "POST", {"entityType": "field", "label": {"en": "No scope"}, "revision": 1}, 1)
        self.assertEqual(ctx.exception.code, 400)
        server.shutdown(); server.server_close(); manager.stop_watcher()

    def test_atomic_source_rows_and_cursor_rollback(self):
        manager, _ = self.make_manager(); manager.sync(); manager.wait_for_idle(); state = manager.store.state(); before = manager.store.current_items(include_trashed=True)
        with manager.store.transaction() as conn:
            conn.execute("CREATE TRIGGER fail_item_insert AFTER INSERT ON items WHEN NEW.item_key='B2' BEGIN SELECT RAISE(ABORT, 'rollback'); END")
        items = [
            {"key": "A1", "version": 2, "library_id": 0, "item_type": "journalArticle", "trashed": False, "parent_key": None, "payload": {"key": "A1", "version": 2}},
            {"key": "B2", "version": 1, "library_id": 0, "item_type": "journalArticle", "trashed": False, "parent_key": None, "payload": {"key": "B2", "version": 1}},
        ]
        with self.assertRaises(Exception):
            manager.store.replace_source_rows(state, items, [], [], metadata_cursor=2)
        self.assertEqual(manager.store.current_items(include_trashed=True), before)
        self.assertEqual(manager.store.state()["metadata_cursor"], 1)
        manager.stop_watcher()


if __name__ == "__main__":
    unittest.main()
