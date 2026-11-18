"""Automatic watcher acceptance tests with an in-memory Zotero API replay."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from atlas.sync import SyncManager
from atlas.storage import SQLiteStore
from atlas.zotero import ZoteroClient, ZoteroError


class Replay:
    def __init__(self) -> None:
        self.online = True
        self.items: dict[str, dict] = {
            "A1": {"key": "A1", "version": 1, "data": {"itemType": "journalArticle", "title": "Initial A", "creators": []}},
            "B2": {"key": "B2", "version": 1, "data": {"itemType": "journalArticle", "title": "Initial B", "creators": []}},
            "C3": {"key": "C3", "version": 1, "data": {"itemType": "journalArticle", "title": "Initial C", "creators": []}},
            "ATT1": {"key": "ATT1", "version": 1, "data": {"itemType": "attachment", "parentItem": "A1", "filename": "one.pdf", "path": "zotero://open-pdf/library/items/ATT1"}},
        }

    def __call__(self, method: str, url: str, headers: dict):
        if not self.online:
            raise ZoteroError("replay offline")
        if "/collections" in url:
            body = {} if "format=versions" in url else []
        elif "format=versions" in url:
            body = {key: value["version"] for key, value in self.items.items()}
        else:
            body = list(self.items.values())
        return {"status": 200, "headers": {"Zotero-Server-ID": "watcher-fixture", "Zotero-Schema-Version": "44"}, "body": body}


class FakeClockStop:
    def __init__(self, on_wait):
        self._event = threading.Event()
        self.ticks = 0
        self.on_wait = on_wait

    def is_set(self):
        return self._event.is_set()

    def set(self):
        self._event.set()

    def clear(self):
        self._event.clear()

    def wait(self, timeout):
        self.ticks += 1
        self.on_wait(self.ticks)
        time.sleep(0.01)
        return self._event.is_set()


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("watcher did not converge before timeout")


def make_manager(tmp_path: Path, replay: Replay) -> SyncManager:
    # TF-IDF is explicit here: this acceptance test is about source lifecycle,
    # polling, and publication fences, never model availability.
    return SyncManager(SQLiteStore(tmp_path), ZoteroClient(transport=replay), embedding_provider="tfidf", poll_interval=5)


def test_poll_interval_bounds_are_enforced(tmp_path: Path):
    replay = Replay()
    with pytest.raises(ValueError):
        SyncManager(SQLiteStore(tmp_path / "low"), ZoteroClient(transport=replay), poll_interval=4)
    with pytest.raises(ValueError):
        SyncManager(SQLiteStore(tmp_path / "high"), ZoteroClient(transport=replay), poll_interval=301)


def test_watcher_automatic_add_edit_attachment_trash_restore_delete_offline_reconnect(tmp_path: Path):
    replay = Replay()
    manager = make_manager(tmp_path, replay)
    milestones: list[str] = []
    gate = threading.Event()

    def on_wait(tick: int):
        if tick == 1:
            milestones.append("startup")
            gate.wait(timeout=5); gate.clear()
        elif tick == 2:
            replay.items["D4"] = {"key": "D4", "version": 1, "data": {"itemType": "journalArticle", "title": "Added D", "creators": []}}
            milestones.append("add")
        elif tick == 3:
            gate.wait(timeout=5); gate.clear()
            replay.items["A1"]["version"] = 2
            replay.items["A1"]["data"]["title"] = "Edited A"
            milestones.append("edit")
        elif tick == 4:
            gate.wait(timeout=5); gate.clear()
            replay.items.pop("ATT1")
            replay.items["ATT2"] = {"key": "ATT2", "version": 3, "data": {"itemType": "attachment", "parentItem": "A1", "filename": "two.pdf", "path": "zotero://open-pdf/library/items/ATT2"}}
            milestones.append("attachment-replace")
        elif tick == 5:
            gate.wait(timeout=5); gate.clear()
            replay.items["ATT2"]["version"] = 4
            replay.items["ATT2"]["data"]["deleted"] = True
            milestones.append("attachment-trash")
        elif tick == 6:
            gate.wait(timeout=5); gate.clear()
            replay.items["ATT2"]["version"] = 5
            replay.items["ATT2"]["data"].pop("deleted")
            milestones.append("attachment-restore")
        elif tick == 7:
            gate.wait(timeout=5); gate.clear()
            replay.items["A1"]["version"] = 6
            replay.items["A1"]["data"]["deleted"] = True
            milestones.append("parent-trash")
        elif tick == 8:
            gate.wait(timeout=5); gate.clear()
            replay.items["A1"]["version"] = 7
            replay.items["A1"]["data"].pop("deleted")
            milestones.append("parent-restore")
        elif tick == 9:
            gate.wait(timeout=5); gate.clear()
            replay.items.pop("A1")
            replay.items.pop("ATT2")
            milestones.append("hard-delete")
        elif tick == 10:
            gate.wait(timeout=5); gate.clear()
            replay.online = False
            milestones.append("offline")
        elif tick == 11:
            gate.wait(timeout=5); gate.clear()
            replay.online = True
            replay.items["A1"] = {"key": "A1", "version": 8, "data": {"itemType": "journalArticle", "title": "Reconnected A", "creators": []}}
            milestones.append("reconnect")

    manager._watch_stop = FakeClockStop(on_wait)
    manager.start_watcher()
    try:
        wait_for(lambda: {p["key"] for p in manager.snapshot()["papers"]} == {"A1", "B2", "C3"})
        gate.set()
        wait_for(lambda: "D4" in {p["key"] for p in manager.snapshot()["papers"]}); assert {p["key"] for p in manager.snapshot()["papers"]} == {"A1", "B2", "C3", "D4"}; gate.set()
        wait_for(lambda: next(p for p in manager.snapshot()["papers"] if p["key"] == "A1")["title"] == "Edited A"); gate.set()
        wait_for(lambda: {a["key"] for a in manager.snapshot()["attachments"]} == {"ATT2"}); gate.set()
        wait_for(lambda: manager.snapshot()["attachments"] == []); gate.set()
        wait_for(lambda: {a["key"] for a in manager.snapshot()["attachments"]} == {"ATT2"}); gate.set()
        wait_for(lambda: "A1" not in {p["key"] for p in manager.snapshot()["papers"]}); gate.set()
        wait_for(lambda: "A1" in {p["key"] for p in manager.snapshot()["papers"]}); gate.set()
        wait_for(lambda: "A1" not in {p["key"] for p in manager.snapshot()["papers"]}); gate.set()
        wait_for(lambda: manager.status["stale"] is True); gate.set()
        wait_for(lambda: next((p for p in manager.snapshot()["papers"] if p["key"] == "A1"), {}).get("title") == "Reconnected A")
        assert milestones[:11] == ["startup", "add", "edit", "attachment-replace", "attachment-trash", "attachment-restore", "parent-trash", "parent-restore", "hard-delete", "offline", "reconnect"]
        assert 5 <= manager.status["watcherInterval"] <= 300
        assert manager.status["stale"] is False
        assert manager.snapshot()["papers"][0]["id"].endswith(":A1")
        assert manager.snapshot()["attachments"] == []
    finally:
        manager.stop_watcher()


def test_watcher_single_flight_and_offline_backoff(tmp_path: Path):
    replay = Replay()
    manager = make_manager(tmp_path, replay)
    entered = threading.Event()
    release = threading.Event()
    original = manager._sync_once

    def guarded_sync():
        entered.set()
        release.wait(timeout=2)
        return original()

    manager._sync_once = guarded_sync  # type: ignore[method-assign]
    results: list[dict] = []

    def run_sync():
        results.append(manager.sync())

    first = threading.Thread(target=run_sync)
    second = threading.Thread(target=run_sync)
    first.start(); assert entered.wait(timeout=2); second.start(); second.join(timeout=2); release.set(); first.join(timeout=3)
    assert len(results) == 2
    assert manager.store.get_snapshot() is not None

    replay.online = False
    manager._watch_stop = FakeClockStop(lambda _tick: None)
    manager.start_watcher()
    try:
        wait_for(lambda: manager.status["stale"] is True)
        wait_for(lambda: manager.status["watcherInterval"] == 300)
        assert 5 <= manager.status["watcherInterval"] <= 300
    finally:
        manager.stop_watcher()
