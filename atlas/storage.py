"""Transactional local state for the read-only Literature Atlas projection.

The database is derived state.  Zotero is never opened or written by this
module; all source data arrives through :mod:`atlas.zotero`.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class SQLiteStore:
    """Small SQLite repository with explicit transaction boundaries."""

    def __init__(self, data_dir: Path | str):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._lock_path = self.data_dir / "bridge.lock"
        self._lock_handle = self._acquire_process_lock()
        self.path = self.data_dir / "atlas.sqlite3"
        self._local = threading.local()
        self._lock = threading.RLock()
        self._connections: set[sqlite3.Connection] = set()
        self._init()

    def _acquire_process_lock(self):
        handle = self._lock_path.open("a+", encoding="utf-8")
        if fcntl is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                handle.close()
                raise RuntimeError("bridge data directory already in use") from exc
        return handle

    def _connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "connection", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.connection = conn
            self._connections.add(conn)
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "connection", None)
        if conn is not None:
            conn.close()
            self._local.connection = None

    def close_all(self) -> None:
        with self._lock:
            for conn in list(self._connections):
                try:
                    conn.close()
                except Exception:
                    pass
            self._connections.clear()
            self._local.connection = None
            if getattr(self, "_lock_handle", None) is not None:
                if fcntl is not None:
                    try:
                        fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass
                self._lock_handle.close()
                self._lock_handle = None

    def __del__(self) -> None:  # pragma: no cover - interpreter shutdown path
        try:
            self.close_all()
        except Exception:
            pass

    @contextlib.contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = self._connection()
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except Exception:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")

    def _init(self) -> None:
        # sqlite3.executescript manages its own implicit transaction and would
        # commit an outer BEGIN on Python 3.12+; schema creation is idempotent,
        # so run it directly under the repository lock.
        with self._lock:
            c = self._connection()
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS source_state (
                  singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                  server_id TEXT, epoch TEXT, schema INTEGER, alias TEXT,
                  generation INTEGER NOT NULL DEFAULT 0,
                  metadata_cursor INTEGER, fulltext_cursor INTEGER,
                  updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS items (
                  server_id TEXT NOT NULL, epoch TEXT NOT NULL, library_id INTEGER NOT NULL,
                  item_key TEXT NOT NULL, version INTEGER NOT NULL, item_type TEXT NOT NULL,
                  trashed INTEGER NOT NULL DEFAULT 0, parent_key TEXT,
                  payload TEXT NOT NULL, updated_at REAL NOT NULL,
                  PRIMARY KEY(server_id, epoch, library_id, item_key)
                );
                CREATE TABLE IF NOT EXISTS collections (
                  server_id TEXT NOT NULL, epoch TEXT NOT NULL, library_id INTEGER NOT NULL,
                  collection_key TEXT NOT NULL, version INTEGER NOT NULL, trashed INTEGER NOT NULL DEFAULT 0,
                  payload TEXT NOT NULL, updated_at REAL NOT NULL,
                  PRIMARY KEY(server_id, epoch, library_id, collection_key)
                );
                CREATE TABLE IF NOT EXISTS attachments (
                  server_id TEXT NOT NULL, epoch TEXT NOT NULL, library_id INTEGER NOT NULL,
                  attachment_key TEXT NOT NULL, parent_key TEXT, version INTEGER NOT NULL,
                  name TEXT NOT NULL, url TEXT, path TEXT, status TEXT NOT NULL DEFAULT 'missing',
                  content_hash TEXT, fulltext_version INTEGER, indexed_pages INTEGER,
                  total_pages INTEGER, payload TEXT NOT NULL, updated_at REAL NOT NULL,
                  PRIMARY KEY(server_id, epoch, library_id, attachment_key)
                );
                CREATE TABLE IF NOT EXISTS tombstones (
                  server_id TEXT NOT NULL, epoch TEXT NOT NULL, library_id INTEGER NOT NULL,
                  item_key TEXT NOT NULL, version INTEGER, deleted_at REAL NOT NULL,
                  PRIMARY KEY(server_id, epoch, library_id, item_key)
                );
                CREATE TABLE IF NOT EXISTS snapshots (
                  singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL,
                  generation INTEGER NOT NULL, payload TEXT NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS overlays (
                  singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL,
                  server_id TEXT, epoch TEXT, payload TEXT NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS taxonomy (
                  singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL,
                  server_id TEXT, epoch TEXT, payload TEXT NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (
                  singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL,
                  payload TEXT NOT NULL, updated_at REAL NOT NULL
                );
                """
            )
            existing_columns = {row[1] for row in c.execute("PRAGMA table_info(attachments)").fetchall()}
            if "indexed_text" not in existing_columns:
                c.execute("ALTER TABLE attachments ADD COLUMN indexed_text TEXT")
            if "text_hash" not in existing_columns:
                c.execute("ALTER TABLE attachments ADD COLUMN text_hash TEXT")

    def state(self) -> dict[str, Any] | None:
        row = self._connection().execute("SELECT * FROM source_state WHERE singleton=1").fetchone()
        return dict(row) if row else None

    def ensure_source(self, server_id: str, schema: int, alias: str = "Zotero", epoch_override: str | None = None) -> tuple[dict[str, Any], bool]:
        """Return current source state and whether an epoch was created."""
        now = time.time()
        with self.transaction() as c:
            row = c.execute("SELECT * FROM source_state WHERE singleton=1").fetchone()
            changed = not row or row["server_id"] != server_id
            if changed:
                epoch = epoch_override or secrets.token_hex(16)
                generation = int(row["generation"]) + 1 if row else 1
                c.execute(
                    """INSERT INTO source_state(singleton,server_id,epoch,schema,alias,generation,metadata_cursor,fulltext_cursor,updated_at)
                       VALUES(1,?,?,?,?,?,?,?,?)
                       ON CONFLICT(singleton) DO UPDATE SET server_id=excluded.server_id,epoch=excluded.epoch,schema=excluded.schema,
                       alias=excluded.alias,generation=excluded.generation,metadata_cursor=NULL,fulltext_cursor=NULL,updated_at=excluded.updated_at""",
                    (server_id, epoch, schema, alias, generation, None, None, now),
                )
                # Source-scoped user state cannot follow coincident item keys
                # into a different Zotero database. Keep the old value as an
                # archive record while exposing an empty state for the new epoch.
                for table in ("overlays", "taxonomy"):
                    old_json = c.execute(f"SELECT revision,payload FROM {table} WHERE singleton=1").fetchone()
                    if old_json:
                        old_payload = json.loads(old_json["payload"])
                        archived = old_payload.get("archived", []) if isinstance(old_payload, dict) else []
                        if not isinstance(archived, list):
                            archived = []
                        archived.append({"serverId": row["server_id"] if row else None, "epoch": row["epoch"] if row else None, "payload": old_payload})
                        empty = {"archived": archived}
                        if table == "taxonomy":
                            empty.update({"fields": [], "concepts": []})
                        c.execute(f"UPDATE {table} SET revision=revision+1,server_id=?,epoch=?,payload=?,updated_at=? WHERE singleton=1", (server_id, epoch, _json(empty), now))
                # Old source rows remain available for forensic/cache purposes but
                # are unreachable from the current source/epoch snapshot.
            else:
                c.execute("UPDATE source_state SET schema=?,alias=?,updated_at=? WHERE singleton=1", (schema, alias, now))
                epoch = row["epoch"]
                generation = row["generation"]
            current = c.execute("SELECT * FROM source_state WHERE singleton=1").fetchone()
        return dict(current), changed

    def bump_generation(self) -> int:
        with self.transaction() as c:
            c.execute("UPDATE source_state SET generation=generation+1,updated_at=? WHERE singleton=1", (time.time(),))
            row = c.execute("SELECT generation FROM source_state WHERE singleton=1").fetchone()
            return int(row[0])

    def set_cursor(self, metadata: int | None = None, fulltext: int | None = None) -> None:
        fields, values = [], []
        if metadata is not None:
            fields.append("metadata_cursor=?"); values.append(metadata)
        if fulltext is not None:
            fields.append("fulltext_cursor=?"); values.append(fulltext)
        if fields:
            values.append(time.time())
            with self.transaction() as c:
                c.execute(f"UPDATE source_state SET {','.join(fields)},updated_at=? WHERE singleton=1", values)

    def current_items(self, include_trashed: bool = False) -> list[dict[str, Any]]:
        state = self.state()
        if not state:
            return []
        q = "SELECT * FROM items WHERE server_id=? AND epoch=?"
        args: list[Any] = [state["server_id"], state["epoch"]]
        if not include_trashed:
            q += " AND trashed=0"
        q += " ORDER BY library_id,item_key"
        return [dict(r) for r in self._connection().execute(q, args).fetchall()]

    def current_collections(self, include_trashed: bool = False) -> list[dict[str, Any]]:
        state = self.state()
        if not state:
            return []
        q = "SELECT * FROM collections WHERE server_id=? AND epoch=?"
        args: list[Any] = [state["server_id"], state["epoch"]]
        if not include_trashed:
            q += " AND trashed=0"
        return [dict(r) for r in self._connection().execute(q, args).fetchall()]

    def current_attachments(self) -> list[dict[str, Any]]:
        state = self.state()
        if not state:
            return []
        return [dict(r) for r in self._connection().execute(
            "SELECT * FROM attachments WHERE server_id=? AND epoch=? ORDER BY library_id,attachment_key",
            (state["server_id"], state["epoch"]),
        ).fetchall()]

    def get_snapshot(self) -> tuple[int, int, dict[str, Any]] | None:
        row = self._connection().execute("SELECT revision,generation,payload FROM snapshots WHERE singleton=1").fetchone()
        if not row:
            return None
        return int(row["revision"]), int(row["generation"]), json.loads(row["payload"])

    def publish_snapshot(self, snapshot: dict[str, Any], generation: int) -> int | None:
        with self.transaction() as c:
            source_row = c.execute("SELECT generation FROM source_state WHERE singleton=1").fetchone()
            if source_row is not None and int(source_row[0]) != int(generation):
                return None
            row = c.execute("SELECT revision FROM snapshots WHERE singleton=1").fetchone()
            revision = int(row[0]) + 1 if row else 1
            snapshot = dict(snapshot)
            snapshot["revision"] = revision
            c.execute(
                "INSERT INTO snapshots(singleton,revision,generation,payload,updated_at) VALUES(1,?,?,?,?) ON CONFLICT(singleton) DO UPDATE SET revision=excluded.revision,generation=excluded.generation,payload=excluded.payload,updated_at=excluded.updated_at",
                (revision, generation, _json(snapshot), time.time()),
            )
        return revision

    def snapshot_generation(self) -> int | None:
        row = self._connection().execute("SELECT generation FROM snapshots WHERE singleton=1").fetchone()
        return int(row[0]) if row else None

    def cas_json(self, table: str, payload: dict[str, Any], expected: int | None, *, source: dict[str, Any] | None = None) -> tuple[bool, int, dict[str, Any]]:
        if table not in {"overlays", "taxonomy", "settings"}:
            raise ValueError("invalid state table")
        with self.transaction() as c:
            if source is not None:
                active = c.execute("SELECT server_id,epoch FROM source_state WHERE singleton=1").fetchone()
                if not active or active[0] != source.get("server_id") or active[1] != source.get("epoch"):
                    row = c.execute(f"SELECT revision,payload FROM {table} WHERE singleton=1").fetchone()
                    return False, int(row["revision"]) if row else 0, json.loads(row["payload"]) if row else {}
            row = c.execute(f"SELECT revision,payload FROM {table} WHERE singleton=1").fetchone()
            current_revision = int(row["revision"]) if row else 0
            current_payload = json.loads(row["payload"]) if row else {}
            if expected is not None and expected != current_revision:
                return False, current_revision, current_payload
            revision = current_revision + 1
            now = time.time()
            if table == "settings":
                c.execute("INSERT INTO settings(singleton,revision,payload,updated_at) VALUES(1,?,?,?) ON CONFLICT(singleton) DO UPDATE SET revision=excluded.revision,payload=excluded.payload,updated_at=excluded.updated_at", (revision, _json(payload), now))
            else:
                server_id = source.get("server_id") if source else None
                epoch = source.get("epoch") if source else None
                c.execute(f"INSERT INTO {table}(singleton,revision,server_id,epoch,payload,updated_at) VALUES(1,?,?,?,?,?) ON CONFLICT(singleton) DO UPDATE SET revision=excluded.revision,server_id=excluded.server_id,epoch=excluded.epoch,payload=excluded.payload,updated_at=excluded.updated_at", (revision, server_id, epoch, _json(payload), now))
            return True, revision, payload

    def read_json(self, table: str) -> tuple[int, dict[str, Any], dict[str, Any] | None]:
        row = self._connection().execute(f"SELECT * FROM {table} WHERE singleton=1").fetchone()
        if not row:
            return 0, {}, None
        source = {"server_id": row["server_id"], "epoch": row["epoch"]} if table != "settings" else None
        return int(row["revision"]), json.loads(row["payload"]), source

    def replace_source_rows(self, state: dict[str, Any], items: list[dict[str, Any]], collections: list[dict[str, Any]], attachments: list[dict[str, Any]], missing_item_keys: set[tuple[int, str]] | None = None, *, metadata_cursor: int | None = None) -> None:
        """Commit a complete, already validated source projection in one transaction."""
        missing_item_keys = missing_item_keys or set()
        now = time.time()
        server_id, epoch = state["server_id"], state["epoch"]
        with self.transaction() as c:
            deleted_source_ids: set[str] = set()
            for item in items:
                c.execute(
                    """INSERT INTO items(server_id,epoch,library_id,item_key,version,item_type,trashed,parent_key,payload,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(server_id,epoch,library_id,item_key) DO UPDATE SET version=excluded.version,item_type=excluded.item_type,trashed=excluded.trashed,parent_key=excluded.parent_key,payload=excluded.payload,updated_at=excluded.updated_at""",
                    (server_id, epoch, item["library_id"], item["key"], item["version"], item["item_type"], int(item.get("trashed", False)), item.get("parent_key"), _json(item["payload"]), now),
                )
                if item.get("trashed"):
                    deleted_source_ids.add(f"zotero:{server_id}:{item['library_id']}:{item['key']}")
                    c.execute("INSERT INTO tombstones(server_id,epoch,library_id,item_key,version,deleted_at) VALUES(?,?,?,?,?,?) ON CONFLICT(server_id,epoch,library_id,item_key) DO UPDATE SET version=excluded.version,deleted_at=excluded.deleted_at", (server_id, epoch, item["library_id"], item["key"], item["version"], now))
                else:
                    c.execute("DELETE FROM tombstones WHERE server_id=? AND epoch=? AND library_id=? AND item_key=?", (server_id, epoch, item["library_id"], item["key"]))
            for library_id, key in missing_item_keys:
                deleted_source_ids.add(f"zotero:{server_id}:{library_id}:{key}")
                c.execute("UPDATE items SET trashed=1,updated_at=? WHERE server_id=? AND epoch=? AND library_id=? AND item_key=?", (now, server_id, epoch, library_id, key))
                c.execute("INSERT INTO tombstones(server_id,epoch,library_id,item_key,version,deleted_at) VALUES(?,?,?,?,NULL,?) ON CONFLICT(server_id,epoch,library_id,item_key) DO UPDATE SET deleted_at=excluded.deleted_at", (server_id, epoch, library_id, key, now))
            for coll in collections:
                c.execute(
                    """INSERT INTO collections(server_id,epoch,library_id,collection_key,version,trashed,payload,updated_at) VALUES(?,?,?,?,?,?,?,?)
                       ON CONFLICT(server_id,epoch,library_id,collection_key) DO UPDATE SET version=excluded.version,trashed=excluded.trashed,payload=excluded.payload,updated_at=excluded.updated_at""",
                    (server_id, epoch, coll["library_id"], coll["key"], coll["version"], int(coll.get("trashed", False)), _json(coll["payload"]), now),
                )
            for attachment in attachments:
                c.execute(
                    """INSERT INTO attachments(server_id,epoch,library_id,attachment_key,parent_key,version,name,url,path,status,content_hash,fulltext_version,indexed_pages,total_pages,payload,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(server_id,epoch,library_id,attachment_key) DO UPDATE SET parent_key=excluded.parent_key,version=excluded.version,name=excluded.name,url=excluded.url,path=excluded.path,status=excluded.status,content_hash=excluded.content_hash,fulltext_version=COALESCE(excluded.fulltext_version,attachments.fulltext_version),indexed_pages=excluded.indexed_pages,total_pages=excluded.total_pages,payload=excluded.payload,updated_at=excluded.updated_at""",
                    (server_id, epoch, attachment["library_id"], attachment["key"], attachment.get("parent_key"), attachment["version"], attachment.get("name", ""), attachment.get("url"), attachment.get("path"), attachment.get("status", "missing"), attachment.get("content_hash"), attachment.get("fulltext_version"), attachment.get("indexed_pages"), attachment.get("total_pages"), _json(attachment.get("payload", {})), now),
                )
            attachment_keys = {(int(a["library_id"]), str(a["key"])) for a in attachments}
            for row in c.execute("SELECT library_id,attachment_key FROM attachments WHERE server_id=? AND epoch=?", (server_id, epoch)).fetchall():
                if (int(row[0]), str(row[1])) not in attachment_keys:
                    c.execute("DELETE FROM attachments WHERE server_id=? AND epoch=? AND library_id=? AND attachment_key=?", (server_id, epoch, row[0], row[1]))
            collection_keys = {(int(coll["library_id"]), str(coll["key"])) for coll in collections}
            for row in c.execute("SELECT library_id,collection_key FROM collections WHERE server_id=? AND epoch=?", (server_id, epoch)).fetchall():
                if (int(row[0]), str(row[1])) not in collection_keys:
                    c.execute("UPDATE collections SET trashed=1,updated_at=? WHERE server_id=? AND epoch=? AND library_id=? AND collection_key=?", (now, server_id, epoch, row[0], row[1]))
            if metadata_cursor is None:
                c.execute("UPDATE source_state SET updated_at=? WHERE singleton=1", (now,))
            else:
                c.execute("UPDATE source_state SET metadata_cursor=?,updated_at=? WHERE singleton=1", (metadata_cursor, now))
            if deleted_source_ids:
                overlay_row = c.execute("SELECT server_id,epoch,payload FROM overlays WHERE singleton=1").fetchone()
                if overlay_row and overlay_row["server_id"] == server_id and overlay_row["epoch"] == epoch:
                    overlay = json.loads(overlay_row["payload"])
                    if isinstance(overlay, dict):
                        archived = overlay.get("archived")
                        if not isinstance(archived, list):
                            archived = []
                        removed: dict[str, dict[str, Any]] = {}
                        for name in ("entities", "paperFields", "paperConcepts", "conceptMemberships", "pins", "aliases"):
                            values = overlay.get(name)
                            if isinstance(values, dict):
                                for entity_id in deleted_source_ids & values.keys():
                                    removed.setdefault(entity_id, {})[name] = values.pop(entity_id)
                        memberships = overlay.get("memberships")
                        if isinstance(memberships, list):
                            kept = []
                            for membership in memberships:
                                entity_id = membership.get("paperId") if isinstance(membership, dict) else None
                                if entity_id in deleted_source_ids:
                                    removed.setdefault(entity_id, {}).setdefault("memberships", []).append(membership)
                                else:
                                    kept.append(membership)
                            overlay["memberships"] = kept
                        # Memberships may also be attached to another entity's patch.
                        entities = overlay.get("entities")
                        if isinstance(entities, dict):
                            for owner_id, patch in entities.items():
                                if isinstance(patch, dict) and isinstance(patch.get("memberships"), list):
                                    kept = []
                                    for membership in patch["memberships"]:
                                        entity_id = membership.get("paperId") if isinstance(membership, dict) else None
                                        if entity_id in deleted_source_ids:
                                            removed.setdefault(entity_id, {}).setdefault("entityMemberships", []).append({"ownerId": owner_id, "membership": membership})
                                        else:
                                            kept.append(membership)
                                    patch["memberships"] = kept
                        for entity_id, payload in sorted(removed.items()):
                            archived.append({"serverId": server_id, "epoch": epoch, "entityId": entity_id, "payload": payload})
                        if removed:
                            overlay["archived"] = archived
                            c.execute("UPDATE overlays SET revision=revision+1,payload=?,updated_at=? WHERE singleton=1", (_json(overlay), now))

    def update_fulltext(self, state: dict[str, Any], attachment_key: str, version: int, *, indexed_pages: int | None = None, total_pages: int | None = None, status: str = "indexed") -> None:
        with self.transaction() as c:
            c.execute("UPDATE attachments SET fulltext_version=?,indexed_pages=?,total_pages=?,status=?,updated_at=? WHERE server_id=? AND epoch=? AND attachment_key=?", (version, indexed_pages, total_pages, status, time.time(), state["server_id"], state["epoch"], attachment_key))

    def update_fulltext_batch(self, state: dict[str, Any], updates: list[tuple[str, int, int | None, int | None, str | None, str | None]], cursor: int) -> bool:
        with self.transaction() as c:
            current = c.execute("SELECT server_id,epoch,generation FROM source_state WHERE singleton=1").fetchone()
            if not current or current[0] != state["server_id"] or current[1] != state["epoch"] or int(current[2]) != int(state.get("generation", current[2])):
                return False
            now = time.time()
            for key, version, indexed_pages, total_pages, indexed_text, text_hash in updates:
                c.execute("UPDATE attachments SET fulltext_version=?,indexed_pages=?,total_pages=?,indexed_text=?,text_hash=?,status='indexed',updated_at=? WHERE server_id=? AND epoch=? AND attachment_key=?", (version, indexed_pages, total_pages, indexed_text, text_hash, now, state["server_id"], state["epoch"], key))
            c.execute("UPDATE source_state SET fulltext_cursor=?,updated_at=? WHERE singleton=1", (cursor, now))
            return True

    def update_fulltext_versions(self, state: dict[str, Any], versions: list[tuple[str, int]], cursor: int) -> bool:
        with self.transaction() as c:
            current = c.execute("SELECT server_id,epoch,generation FROM source_state WHERE singleton=1").fetchone()
            if not current or current[0] != state["server_id"] or current[1] != state["epoch"] or int(current[2]) != int(state.get("generation", current[2])):
                return False
            now = time.time()
            for key, version in versions:
                c.execute("UPDATE attachments SET fulltext_version=?,updated_at=? WHERE server_id=? AND epoch=? AND attachment_key=?", (version, now, state["server_id"], state["epoch"], key))
            c.execute("UPDATE source_state SET fulltext_cursor=?,updated_at=? WHERE singleton=1", (cursor, now))
            return True

    def clear_indexed_text(self, state: dict[str, Any]) -> None:
        with self.transaction() as c:
            c.execute("UPDATE attachments SET indexed_text=NULL,text_hash=NULL,indexed_pages=NULL,total_pages=NULL,updated_at=? WHERE server_id=? AND epoch=?", (time.time(), state["server_id"], state["epoch"]))

    def refresh_attachment_targets(self, state: dict[str, Any], rows: list[dict[str, Any]]) -> bool:
        changed = False
        with self.transaction() as c:
            now = time.time()
            for row in rows:
                current = c.execute("SELECT url,path,status,content_hash FROM attachments WHERE server_id=? AND epoch=? AND attachment_key=?", (state["server_id"], state["epoch"], row["key"])).fetchone()
                if not current:
                    continue
                values = (row.get("url"), row.get("path"), row.get("status", "missing"), row.get("content_hash"))
                if tuple(current) != values:
                    changed = True
                    c.execute("UPDATE attachments SET url=?,path=?,status=?,content_hash=?,updated_at=? WHERE server_id=? AND epoch=? AND attachment_key=?", (*values, now, state["server_id"], state["epoch"], row["key"]))
        return changed


def file_sha256(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for block in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(block)
        return "sha256:" + h.hexdigest()
    except (OSError, ValueError):
        return None
