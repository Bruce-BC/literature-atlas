"""Authenticated loopback HTTP bridge for Literature Atlas."""
from __future__ import annotations

import hmac
import hashlib
import copy
import json
import mimetypes
import os
import secrets
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .graph import MAX_TAXONOMY_REDIRECTS_PER_KIND, canonical_context_matches, canonical_entity_key, canonical_identity_matches, resolve_taxonomy_id
from .storage import SQLiteStore
from .sync import SyncManager
from .facet_validation import (
    facet_entities,
    known_facet_index,
    merge_payload,
    validate_facet_assignments,
    validate_input_state,
    validate_primary_overrides,
    validate_quality_override,
)

class RedirectLimitError(ValueError):
    """Reject oversized or excessively deep redirect state without mutation."""


MAX_BODY = 1024 * 1024
MAX_RESPONSE = 16 * 1024 * 1024
DEFAULT_SETTINGS = {"provider": "tfidf", "threshold": 0.8, "topK": 5, "includeIndexedText": False, "openAlex": False}


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class AtlasHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], manager: SyncManager, *, web_root: Path | str | None = None, token: str | None = None, launch_id: str = "", parent_origin: str | None = None):
        self.manager = manager
        self.store = manager.store
        self.web_root = Path(web_root).resolve() if web_root else None
        self.token = token or secrets.token_urlsafe(32)
        self.launch_id = launch_id
        self.parent_origin = parent_origin
        self.taxonomy_lock = threading.RLock()
        self.pid = os.getpid()
        super().__init__(address, AtlasRequestHandler)

    def health(self) -> dict[str, Any]:
        return {"ok": True, "version": "0.1.0", "pid": self.pid, "launchId": self.launch_id, "parentOrigin": self.parent_origin, "port": self.server_port}


class AtlasRequestHandler(BaseHTTPRequestHandler):
    server: AtlasHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        # Never include query strings, bearer headers, or source values in logs.
        return

    def finish(self) -> None:
        try:
            super().finish()
        finally:
            # Each ThreadingHTTPServer request has its own thread-local SQLite
            # connection. Close it before that short-lived thread exits.
            self.server.store.close()

    def _host_ok(self) -> bool:
        host = self.headers.get("Host", "")
        try:
            parsed = urllib.parse.urlsplit("//" + host)
            hostname = (parsed.hostname or "").lower()
            port = parsed.port or 80
        except ValueError:
            return False
        return hostname in {"127.0.0.1", "localhost"} and port == self.server.server_port and self.client_address[0] in {"127.0.0.1", "::1"}

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        allowed = {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}
        if self.server.parent_origin:
            allowed.add(self.server.parent_origin)
        return origin in allowed

    def _auth_ok(self) -> bool:
        value = self.headers.get("Authorization", "")
        if not value.startswith("Bearer "):
            return False
        candidate = value[7:].strip()
        try:
            return hmac.compare_digest(candidate, self.server.token)
        except TypeError:
            return False

    def _send(self, status: int, payload: Any, *, content_type: str = "application/json", headers: dict[str, str] | None = None) -> None:
        body = payload if isinstance(payload, bytes) else _json_bytes(payload) if content_type == "application/json" else str(payload).encode("utf-8")
        if len(body) > MAX_RESPONSE:
            status, body = HTTPStatus.INTERNAL_SERVER_ERROR, _json_bytes({"error": "response too large"})
            content_type = "application/json"
        self.send_response(status)
        self.send_header("Content-Type", content_type + ("; charset=utf-8" if content_type.startswith("text/") or content_type == "application/json" else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if self.server.parent_origin:
            self.send_header("Access-Control-Allow-Origin", self.server.parent_origin)
            self.send_header("Vary", "Origin")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _reject(self, status: int, message: str) -> None:
        self._send(status, {"error": message})

    def _preflight(self) -> None:
        if not self._host_ok() or not self._origin_ok():
            self._reject(403, "forbidden")
            return
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.send_header("Access-Control-Allow-Origin", self.server.parent_origin or f"http://127.0.0.1:{self.server.server_port}")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, If-Match")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.end_headers()

    def _guard(self, *, auth: bool = True) -> bool:
        if not self._host_ok() or not self._origin_ok():
            self._reject(403, "forbidden")
            return False
        if auth and not self._auth_ok():
            self._send(401, {"error": "unauthorized"}, headers={"WWW-Authenticate": "Bearer"})
            return False
        return True

    def _body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("invalid content length")
        if length < 0 or length > MAX_BODY:
            raise ValueError("request body too large")
        raw = self.rfile.read(length)
        value = json.loads(raw.decode("utf-8")) if raw else {}
        if not isinstance(value, dict):
            raise ValueError("JSON object required")
        return value

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._preflight()

    def do_GET(self) -> None:  # noqa: N802
        request_path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        if not self._guard(auth=request_path.startswith("/api/")):
            return
        parsed = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(parsed.path)
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if path == "/health":
                self._send(200, self.server.health()); return
            if path == "/api/v1/status":
                self._send(200, self.server.manager.status); return
            if path == "/api/v1/snapshot":
                projection = query.get("projection", ["full"])[0]
                if projection not in {"full", "overview", "detail"}:
                    self._reject(400, "invalid projection"); return
                sample = None
                if query.get("sample"):
                    sample = int(query["sample"][0])
                    if sample < 0 or sample > 10000:
                        raise ValueError("invalid sample")
                self._send(200, self.server.manager.snapshot(projection, sample)); return
            if path == "/api/v1/settings":
                revision, payload, _ = self.server.store.read_json("settings")
                settings = dict(DEFAULT_SETTINGS); settings.update(payload)
                if self.server.manager.embedding_provider in {"e5", "tfidf"}: settings["provider"] = self.server.manager.embedding_provider
                self._send(200, {"revision": revision, "settings": settings}); return
            if path == "/api/v1/taxonomy":
                revision, payload, source = self.server.store.read_json("taxonomy")
                state, snapshot, redirects = self._taxonomy_view()
                self._send(200, {"revision": revision, "taxonomy": payload, "source": source, "sourceId": state.get("server_id"), "sourceEpoch": state.get("epoch"), "redirects": redirects}); return
            if path == "/api/v1/taxonomy/resolve":
                kind = query.get("entityType", [""])[0]
                entity_id = query.get("entityId", [""])[0]
                if kind not in {"field", "concept"} or not entity_id: raise ValueError("entityType and entityId required")
                state, snapshot, redirects = self._taxonomy_view()
                prefix = f"{kind}:{state.get('server_id')}:{state.get('epoch')}:"
                canonical = redirects.get(entity_id, entity_id)
                known = {str(row.get("id")) for row in snapshot.get(kind + "s", []) if isinstance(row, dict)}
                # Suppressed concepts leave the active graph but stay resolvable
                # through the same-source quality ledger (no fake graph nodes).
                ledger = {}
                if kind == "concept" and isinstance(snapshot.get("conceptQualityLedger"), dict):
                    ledger = {str(e.get("id") or e.get("conceptId")): e for e in snapshot["conceptQualityLedger"].get("entries", []) if isinstance(e, dict)}
                if not entity_id.startswith(prefix) or not canonical.startswith(prefix) or (canonical not in known and canonical not in ledger):
                    self._reject(404, "taxonomy entity not found"); return
                revision = self.server.store.read_json("taxonomy")[0]
                body = {"requestedId": entity_id, "canonicalId": canonical, "redirected": canonical != entity_id, "sourceId": state.get("server_id"), "sourceEpoch": state.get("epoch"), "revision": revision}
                if kind == "concept":
                    entry = ledger.get(canonical)
                    body["status"] = "active" if canonical in known else str(entry.get("status", "suppressed"))
                    if entry is not None:
                        body["reason"] = entry.get("reason"); body["inGraph"] = canonical in known
                        merged = entry.get("mergedInto")
                        if canonical not in known and merged and merged[0] in known and entry.get("mergeScope") == "global":
                            body["mergedInto"] = list(merged)
                        if entry.get("contextMergedInto"):
                            # Per-paper context merge (shared acronym): informational only, never a redirect.
                            body["contextMergedInto"] = list(entry["contextMergedInto"]); body["mergeScope"] = "per-paper"
                self._send(200, body); return
            if path == "/api/v1/overlays":
                revision, payload, source = self.server.store.read_json("overlays")
                self._send(200, {"revision": revision, "overlays": payload, "source": source}); return
            if path.startswith("/api/v1/papers/"):
                paper_id = path.rsplit("/", 1)[-1]
                snapshot = self.server.manager.snapshot()
                state = self.server.store.state() or {}
                if snapshot.get("source", {}).get("serverId") != state.get("server_id") or snapshot.get("source", {}).get("epoch") != state.get("epoch"):
                    self._reject(404, "paper not found"); return
                row = next((p for p in snapshot.get("papers", []) if p.get("id") == paper_id), None)
                if row is None:
                    self._reject(404, "paper not found")
                else:
                    self._send(200, row)
                return
            if path.startswith("/api/v1/attachments/") and path.endswith("/url"):
                attachment_id = path[len("/api/v1/attachments/"):-len("/url")]
                snapshot = self.server.manager.snapshot()
                state = self.server.store.state() or {}
                if snapshot.get("source", {}).get("serverId") != state.get("server_id") or snapshot.get("source", {}).get("epoch") != state.get("epoch"):
                    self._reject(404, "attachment target unavailable"); return
                row = next((a for a in snapshot.get("attachments", []) if a.get("id") == attachment_id), None)
                url = row.get("url") if row else None
                if not row or not isinstance(url, str) or not url.startswith(("http://", "https://", "zotero://")):
                    self._reject(404, "attachment target unavailable")
                else:
                    self._send(200, {"url": url})
                return
            if self._serve_static(path):
                return
            self._reject(404, "not found")
        except RedirectLimitError as exc:
            self._reject(503, str(exc))
        except (ValueError, TypeError):
            self._reject(400, "invalid request")
        except Exception:
            self._reject(500, "request failed")

    def _serve_static(self, path: str) -> bool:
        root = self.server.web_root
        if not root or path.startswith("/api/"):
            return False
        relative = path.lstrip("/") or "index.html"
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            self._reject(404, "not found"); return True
        if not candidate.is_file():
            candidate = root / "index.html"
        if not candidate.is_file():
            return False
        body = candidate.read_bytes()
        ancestors = "'self'" if not self.server.parent_origin else f"'self' {self.server.parent_origin}"
        self._send(200, body, content_type=mimetypes.guess_type(str(candidate))[0] or "application/octet-stream", headers={"Content-Security-Policy": f"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' http://127.0.0.1:* http://localhost:*; object-src 'none'; base-uri 'none'; frame-ancestors {ancestors}"})
        return True

    def do_POST(self) -> None:  # noqa: N802
        if not self._guard(): return
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        try:
            if path == "/api/v1/sync":
                snapshot = self.server.manager.sync(trigger="manual")
                self._send(200, {"accepted": True, "revision": snapshot.get("revision", 0), "status": self.server.manager.status}); return
            if path == "/api/v1/taxonomy":
                body = self._body(); self._taxonomy_write(body, create=True); return
            self._reject(404, "not found")
        except RedirectLimitError as exc:
            self._reject(503, str(exc))
        except ValueError as exc:
            self._reject(400, str(exc))
        except Exception:
            self._reject(500, "request failed")

    def do_PUT(self) -> None:  # noqa: N802
        if not self._guard(): return
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        try:
            body = self._body()
            if path == "/api/v1/settings": self._settings_write(body); return
            if path == "/api/v1/taxonomy": self._taxonomy_write(body, create=False); return
            if path == "/api/v1/overlays": self._overlay_write(body); return
            self._reject(404, "not found")
        except RedirectLimitError as exc:
            self._reject(503, str(exc))
        except ValueError as exc:
            self._reject(400, str(exc))
        except Exception:
            self._reject(500, "request failed")

    def _expected_revision(self, body: dict[str, Any]) -> int | None:
        raw = self.headers.get("If-Match")
        if raw is None:
            nested = body.get("taxonomy", body.get("overlays", {}))
            if not isinstance(nested, dict):
                nested = {}
            raw = body.get("expectedRevision", body.get("revision", nested.get("expectedRevision", nested.get("revision"))))
        if raw is None: raise ValueError("revision required")
        try:
            return int(str(raw).strip('"'))
        except ValueError:
            raise ValueError("invalid revision")

    def _settings_write(self, body: dict[str, Any]) -> None:
        allowed = {"provider", "threshold", "topK", "includeIndexedText", "openAlex"}
        payload = body.get("settings", body)
        if not isinstance(payload, dict) or set(payload) - allowed - {"revision"}:
            raise ValueError("unknown settings key")
        current = dict(DEFAULT_SETTINGS); current.update(self.server.store.read_json("settings")[1])
        merged = dict(current)
        for key, value in payload.items():
            if key == "revision": continue
            if key == "provider" and value not in {"e5", "tfidf"}: raise ValueError("invalid provider")
            if key == "threshold" and (not isinstance(value, (int, float)) or not 0 <= value <= 1): raise ValueError("invalid threshold")
            if key == "topK" and (not isinstance(value, int) or not 1 <= value <= 1000): raise ValueError("invalid topK")
            if key in {"includeIndexedText", "openAlex"} and not isinstance(value, bool): raise ValueError("invalid setting")
            merged[key] = value
        ok, revision, result = self.server.store.cas_json("settings", merged, self._expected_revision(body))
        if not ok: self._send(409, {"error": "revision conflict", "revision": revision}); return
        previous_indexed = self.server.manager.include_indexed_text
        self.server.manager.include_indexed_text = bool(merged.get("includeIndexedText", False))
        if previous_indexed and not self.server.manager.include_indexed_text:
            state = self.server.store.state()
            if state:
                self.server.store.clear_indexed_text(state)
        self.server.manager.request_reindex(provider=merged.get("provider"))
        self._send(200, {"revision": revision, "settings": result})

    def _taxonomy_view(self) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
        state = self.server.store.state() or {}
        snapshot = self.server.manager.snapshot()
        source = snapshot.get("source", {})
        if not state or source.get("serverId") != state.get("server_id") or source.get("epoch") != state.get("epoch"):
            return state, {}, {}
        raw = snapshot.get("taxonomyRedirects", {})
        if not isinstance(raw, dict):
            return state, snapshot, {}
        redirects: dict[str, str] = {}
        for kind in ("field", "concept"):
            count = sum(isinstance(key, str) and key.startswith(kind + ":") for key in raw)
            if count > MAX_TAXONOMY_REDIRECTS_PER_KIND:
                raise RedirectLimitError(f"{kind} taxonomy redirect limit exceeded")
            prefix = f"{kind}:{state['server_id']}:{state['epoch']}:"
            known = {str(row.get("id")) for row in snapshot.get(kind + "s", []) if isinstance(row, dict) and str(row.get("id", "")).startswith(prefix)}
            scoped = {old: target for old, target in raw.items() if isinstance(old, str) and isinstance(target, str) and old.startswith(prefix) and target.startswith(prefix)}
            for old in scoped:
                try:
                    target = resolve_taxonomy_id(old, scoped)
                except ValueError as exc:
                    raise RedirectLimitError(str(exc)) from exc
                if target in known and target != old:
                    redirects[old] = target
        return state, snapshot, redirects

    def _reject_redirected_write(self, payload: Any, redirects: dict[str, str], revision: int) -> bool:
        def values(node: Any):
            if isinstance(node, dict):
                for key, value in node.items():
                    if key in {"entityId", "id", "fieldId", "conceptId", "paperId"} and isinstance(value, str):
                        yield value
                    elif key in {"parentIds", "fieldIds", "conceptIds"} and isinstance(value, list):
                        yield from (item for item in value if isinstance(item, str))
                    elif key in {"aliases", "pins", "entities", "paperFields", "paperConcepts", "conceptAliases", "fieldAliases"} and isinstance(value, dict):
                        yield from value.keys()
                        yield from values(value)
                    elif isinstance(value, (dict, list)):
                        yield from values(value)
            elif isinstance(node, list):
                for value in node:
                    yield from values(value)
        for value in values(payload):
            if value in redirects:
                self._send(409, {"error": "taxonomy id has been merged", "message": "Use the canonical entity ID and retry.", "requestedId": value, "canonicalId": redirects[value], "revision": revision})
                return True
        return False

    def _active_epoch_payload(self, payload: dict[str, Any], tagged: dict[str, Any] | None) -> dict[str, Any]:
        """Never carry a payload tagged with another source/epoch into a merge.

        ``cas_json`` re-stamps a merged payload with the active epoch, so a
        foreign-tagged ``facetCatalogConfig``/``inputState``/overlay would
        otherwise be laundered into the current epoch.
        """
        state = self.server.store.state() or {}
        if tagged and (tagged.get("server_id") != state.get("server_id") or tagged.get("epoch") != state.get("epoch")):
            return {}
        return payload

    @staticmethod
    def _concept_ids_with_ledger(snapshot: dict[str, Any]) -> set[str]:
        """Active concepts plus same-snapshot ledger IDs (suppressed ones stay overridable)."""
        ids = {str(row.get("id")) for row in snapshot.get("concepts", []) if isinstance(row, dict)}
        ledger = snapshot.get("conceptQualityLedger")
        for entry in (ledger.get("entries", []) if isinstance(ledger, dict) else []):
            if isinstance(entry, dict) and (entry.get("id") or entry.get("conceptId")):
                ids.add(str(entry.get("id") or entry.get("conceptId")))
        return ids

    def _taxonomy_write(self, body: dict[str, Any], *, create: bool) -> None:
        with self.server.taxonomy_lock:
            self._taxonomy_write_locked(body, create=create)

    def _taxonomy_write_locked(self, body: dict[str, Any], *, create: bool) -> None:
        payload = body.get("taxonomy", body)
        if not isinstance(payload, dict): raise ValueError("taxonomy object required")
        allowed = {"fields", "concepts", "facetCatalogConfig", "inputState", "revision", "expectedRevision", "sourceEpoch", "sourceId", "entityType", "entityId", "label", "aliases", "parentIds", "patch", "source"}
        if set(payload) - allowed: raise ValueError("unknown taxonomy key")
        current_revision, current, current_source = self.server.store.read_json("taxonomy")
        current = self._active_epoch_payload(current, current_source)
        expected = self._expected_revision(body)
        if expected != current_revision:
            self._send(409, {"error": "revision conflict", "revision": current_revision}); return
        merged = merge_payload(current, payload)
        merged.setdefault("fields", copy.deepcopy(current.get("fields", [])))
        merged.setdefault("concepts", copy.deepcopy(current.get("concepts", [])))
        state = self.server.store.state() or {}
        source_id = body.get("sourceId", payload.get("sourceId")); source_epoch = body.get("sourceEpoch", payload.get("sourceEpoch"))
        if source_id != state.get("server_id") or source_epoch != state.get("epoch"): raise ValueError("source identity required")
        for key in ("sourceId", "sourceEpoch", "revision", "expectedRevision"):
            merged.pop(key, None)
        if "facetCatalogConfig" in payload:
            entities = facet_entities(payload.get("facetCatalogConfig"), source_id=state.get("server_id"), source_epoch=state.get("epoch"), require_source_scope=True)
            # Persist the canonical (parent-alias-normalised) form only.
            merged["facetCatalogConfig"] = {"version": 1, "entities": list(entities.values())}
        if "inputState" in payload:
            merged["inputState"] = validate_input_state(payload.get("inputState"))
        _, snapshot, redirects = self._taxonomy_view()
        if self._reject_redirected_write(payload, redirects, current_revision): return
        scoped_prefix = {kind: f"{kind}:{state.get('server_id')}:{state.get('epoch')}:" for kind in ("field", "concept")}
        active = {kind: {str(row.get("id")): row for row in snapshot.get(kind + "s", [])
                         if str(row.get("id", "")).startswith(scoped_prefix[kind])} for kind in ("field", "concept")}
        for kind in ("field", "concept"):
            for row in merged[kind + "s"]:
                if isinstance(row, dict) and str(row.get("id", "")).startswith(scoped_prefix[kind]):
                    active[kind].setdefault(str(row["id"]), row)
        # Include alias edits that are committed but whose graph rebuild is
        # still pending, fenced to the active source and epoch.
        _, saved_overlays, overlay_source = self.server.store.read_json("overlays")
        if overlay_source and overlay_source.get("server_id") == state.get("server_id") and overlay_source.get("epoch") == state.get("epoch"):
            saved_entities = saved_overlays.get("entities", {})
            for kind in ("field", "concept"):
                saved_aliases = saved_overlays.get(kind + "Aliases", {})
                if not isinstance(saved_aliases, dict): saved_aliases = {}
                for entity_id, row in list(active[kind].items()):
                    patch = saved_entities.get(entity_id, {}) if isinstance(saved_entities, dict) else {}
                    aliases = patch.get("aliases") if isinstance(patch, dict) and "aliases" in patch else saved_aliases.get(entity_id)
                    if isinstance(aliases, list):
                        active[kind][entity_id] = {**row, "aliases": aliases}
        if payload.get("entityType"):
            kind = str(payload["entityType"])
            if kind not in {"field", "concept"}: raise ValueError("invalid taxonomy entity type")
            label = payload.get("label")
            if not isinstance(label, (str, dict)): raise ValueError("invalid taxonomy label")
            import re
            raw_label = label.get("en", "") if isinstance(label, dict) else label
            slug = re.sub(r"[^A-Za-z0-9_-]+", "-", str(raw_label)).strip("-").lower() or "item"
            aliases, parent_ids = payload.get("aliases", []), payload.get("parentIds", [])
            if not isinstance(aliases, list) or any(not isinstance(x, str) for x in aliases): raise ValueError("aliases must be string array")
            if not isinstance(parent_ids, list) or any(not isinstance(x, str) for x in parent_ids): raise ValueError("parentIds must be string array")
            candidate_key = canonical_entity_key(kind, label, aliases)
            candidate_parents = set(parent_ids) if kind == "field" else set()
            for entity_id, existing in active[kind].items():
                candidate = {"label": label, "aliases": aliases, "parentIds": parent_ids}
                same_context = canonical_context_matches(kind, candidate, existing, snapshot.get("taxonomyRedirects"))
                if same_context and candidate_key and canonical_identity_matches(kind, candidate, existing):
                    self._send(409, {"error": "duplicate taxonomy entity", "message": "An equivalent entity already exists in this context.", "existingEntityId": entity_id, "revision": current_revision}); return
            if f"{kind}:{state.get('server_id')}:{state.get('epoch')}:{slug}" in active[kind]:
                digest = hashlib.sha256(_json_bytes({"label": label, "parents": sorted(candidate_parents)})).hexdigest()[:12]
                slug = f"{slug}-{digest}"
            payload = dict(payload)
            payload["fields" if kind == "field" else "concepts"] = [{"id": f"{kind}:{state.get('server_id')}:{state.get('epoch')}:{slug}", "label": label if isinstance(label, dict) else {"en": str(label), "ko": str(label)}, "parentIds": list(payload.get("parentIds", [])) if kind == "field" else [], "aliases": list(payload.get("aliases", [])), "source": str(payload.get("source") or "user")}]
        if payload.get("entityId") and isinstance(payload.get("patch"), dict):
            patch = payload["patch"]
            if set(patch) - {"label", "aliases", "parentIds", "source"}: raise ValueError("unknown taxonomy patch key")
            if not any(entry.get("id") == payload["entityId"] for kind in ("fields", "concepts") for entry in merged[kind]): raise ValueError("unknown taxonomy entity")
            for kind in ("fields", "concepts"):
                for entry in merged[kind]:
                    if entry.get("id") == payload["entityId"]:
                        entry.update(patch)
            payload = dict(payload)
            payload["fields"], payload["concepts"] = [], []
        for kind in ("fields", "concepts"):
            for entry in payload.get(kind, []):
                if not isinstance(entry, dict) or set(entry) - {"id", "label", "parentIds", "aliases", "source", "revision"}: raise ValueError("unknown taxonomy field")
                if not entry.get("id", "").startswith(f"{kind[:-1]}:{state.get('server_id')}:{state.get('epoch')}:"): raise ValueError("taxonomy id is not source scoped")
                if not create and not any(old.get("id") == entry.get("id") for old in merged[kind]): raise ValueError("unknown taxonomy entity")
            if create: merged[kind].extend(payload.get(kind, []))
            elif payload.get(kind): merged[kind] = payload[kind]
        # Validate every create/edit surface, including bulk payloads, against
        # the active graph and persisted definitions before committing the CAS.
        for kind in ("field", "concept"):
            original = {str(row.get("id")): row for row in current.get(kind + "s", []) if isinstance(row, dict)}
            candidates = dict(active[kind])
            for entry in merged[kind + "s"]:
                entity_id = str(entry.get("id"))
                if original.get(entity_id) == entry:
                    continue
                aliases, parent_ids = entry.get("aliases", []), entry.get("parentIds", [])
                if not isinstance(aliases, list) or any(not isinstance(x, str) for x in aliases): raise ValueError("aliases must be string array")
                if not isinstance(parent_ids, list) or any(not isinstance(x, str) for x in parent_ids): raise ValueError("parentIds must be string array")
                key = canonical_entity_key(kind, entry.get("label", {}), aliases)
                if not key: raise ValueError("taxonomy label required")
                for other_id, other in candidates.items():
                    if other_id == entity_id: continue
                    same_context = canonical_context_matches(kind, entry, other, snapshot.get("taxonomyRedirects"))
                    if same_context and canonical_identity_matches(kind, entry, other):
                        self._send(409, {"error": "duplicate taxonomy entity", "message": "An equivalent entity already exists in this context.", "existingEntityId": other_id, "revision": current_revision}); return
                candidates[entity_id] = entry
        all_fields = dict(active["field"])
        all_fields.update({str(f.get("id")): f for f in merged["fields"]})
        ids = set(all_fields)
        parents = {key: [str(x) for x in field.get("parentIds", [])] for key, field in all_fields.items()}
        for child, parent_ids in parents.items():
            for parent in parent_ids:
                if parent not in ids: raise ValueError("unknown taxonomy parent")
        def visit(node: str, path: set[str]) -> None:
            if node in path: raise ValueError("taxonomy cycle")
            for parent in parents.get(node, []):
                visit(parent, path | {node})
        for child in parents:
            visit(child, set())
        ok, revision, result = self.server.store.cas_json("taxonomy", merged, expected, source=state)
        if not ok: self._send(409, {"error": "revision conflict", "revision": revision}); return
        self.server.manager.request_reindex()
        self._send(200, {"revision": revision, "taxonomy": result})

    def _overlay_write(self, body: dict[str, Any]) -> None:
        with self.server.taxonomy_lock:
            self._overlay_write_locked(body)

    def _overlay_write_locked(self, body: dict[str, Any]) -> None:
        payload = body.get("overlays", body)
        if not isinstance(payload, dict): raise ValueError("overlay object required")
        if payload.get("entityType"):
            if payload.get("entityType") not in {"paper", "concept", "field"} or not isinstance(payload.get("entityId"), str) or not isinstance(payload.get("patch"), dict): raise ValueError("invalid overlay entity")
            if set(payload["patch"]) - {"aliases", "fieldIds", "memberships", "pinned", "facetAssignments", "primaryFacetOverrides", "quality", "excluded", "protected"}: raise ValueError("unknown overlay patch key")
            if payload.get("entityType") == "paper" and "aliases" in payload["patch"]:
                raise ValueError("paper aliases are unsupported")
            patch = payload["patch"]
            if "aliases" in patch and (not isinstance(patch["aliases"], list) or any(not isinstance(value, str) for value in patch["aliases"])): raise ValueError("aliases must be string array")
            if "fieldIds" in patch and (not isinstance(patch["fieldIds"], list) or any(not isinstance(value, str) for value in patch["fieldIds"])): raise ValueError("fieldIds must be string array")
            if "pinned" in patch and not isinstance(patch["pinned"], bool): raise ValueError("pinned must be boolean")
            if payload.get("entityType") != "concept" and {"excluded", "protected", "quality"} & set(patch): raise ValueError("concept quality overrides apply to concepts")
            if "excluded" in patch and not isinstance(patch["excluded"], bool): raise ValueError("excluded must be boolean")
            if "protected" in patch and not isinstance(patch["protected"], bool): raise ValueError("protected must be boolean")
            if "memberships" in patch and (not isinstance(patch["memberships"], list) or any(not isinstance(value, dict) or set(value) - {"paperId", "fieldId", "weight", "method", "score"} or not isinstance(value.get("paperId"), str) or not isinstance(value.get("fieldId"), str) or ("weight" in value and (not isinstance(value["weight"], (int, float)) or value["weight"] < 0)) for value in patch["memberships"])): raise ValueError("invalid memberships")
        elif set(payload) - {"aliases", "memberships", "pins", "archived", "facetAssignments", "primaryFacetOverrides", "conceptQuality", "revision", "expectedRevision", "sourceEpoch", "sourceId"}:
            raise ValueError("unknown overlay key")
        state = self.server.store.state() or {}
        source_id = body.get("sourceId", payload.get("sourceId")); source_epoch = body.get("sourceEpoch", payload.get("sourceEpoch"))
        if source_id != state.get("server_id") or source_epoch != state.get("epoch"): raise ValueError("source identity required")
        current_revision = self.server.store.read_json("overlays")[0]
        if self._expected_revision(body) != current_revision:
            self._send(412, {"error": "revision conflict", "revision": current_revision}); return
        _, _, redirects = self._taxonomy_view()
        if self._reject_redirected_write(payload, redirects, current_revision): return
        if payload.get("entityType"):
            entity_id = str(payload.get("entityId"))
            if not (entity_id.startswith(f"zotero:{state.get('server_id')}:") or entity_id.startswith(f"field:{state.get('server_id')}:{state.get('epoch')}:") or entity_id.startswith(f"concept:{state.get('server_id')}:{state.get('epoch')}:") ):
                raise ValueError("overlay id is not source scoped")
            snapshot = self.server.manager.snapshot()
            known = {str(row.get("id")) for key in ("papers", "fields", "concepts") for row in snapshot.get(key, [])}
            if payload.get("entityType") == "concept":
                known |= self._concept_ids_with_ledger(snapshot)      # suppressed (ledger-only) concepts stay overridable
            if entity_id not in known:
                raise ValueError("unknown overlay entity")
            field_ids = payload["patch"].get("fieldIds", [])
            if not isinstance(field_ids, list) or any(str(field_id) not in {str(row.get("id")) for row in snapshot.get("fields", [])} for field_id in field_ids):
                raise ValueError("unknown overlay field")
            for membership in payload["patch"].get("memberships", []):
                if not isinstance(membership, dict) or str(membership.get("paperId")) not in {str(row.get("id")) for row in snapshot.get("papers", [])} or str(membership.get("fieldId")) not in {str(row.get("id")) for row in snapshot.get("fields", [])}:
                    raise ValueError("unknown overlay membership")
            taxonomy = self.server.store.read_json("taxonomy")[1]
            facet_ids, facet_kinds, facet_semantic = known_facet_index(taxonomy, snapshot, source_id=state.get("server_id"), source_epoch=state.get("epoch"))
            paper_ids = {str(row.get("id")) for row in snapshot.get("papers", []) if isinstance(row, dict)}
            if payload.get("entityType") != "paper" and ("facetAssignments" in payload["patch"] or "primaryFacetOverrides" in payload["patch"]):
                raise ValueError("facet overrides apply to papers")
            if "facetAssignments" in payload["patch"]:
                payload["patch"]["facetAssignments"] = validate_facet_assignments(payload["patch"]["facetAssignments"], known_ids=facet_ids, semantic=facet_semantic, paper_ids=paper_ids)
            if "primaryFacetOverrides" in payload["patch"]:
                payload["patch"]["primaryFacetOverrides"] = validate_primary_overrides(payload["patch"]["primaryFacetOverrides"], known_ids=facet_ids, kinds=facet_kinds, semantic=facet_semantic)
            if payload.get("entityType") == "concept" and "quality" in payload["patch"]:
                validate_quality_override(payload["patch"]["quality"], known_concepts=self._concept_ids_with_ledger(snapshot))
        else:
            snapshot = self.server.manager.snapshot(); known = {str(row.get("id")) for key in ("papers", "fields", "concepts") for row in snapshot.get(key, [])}
            aliases = payload.get("aliases", {}); pins = payload.get("pins", {})
            if not isinstance(aliases, dict) or any(str(k) not in known or not isinstance(v, list) or any(not isinstance(x, str) for x in v) for k, v in aliases.items()): raise ValueError("invalid overlay aliases")
            if not isinstance(pins, dict) or any(str(k) not in known or not isinstance(v, bool) for k, v in pins.items()): raise ValueError("invalid overlay pins")
            for membership in payload.get("memberships", []):
                if not isinstance(membership, dict) or str(membership.get("paperId")) not in {str(row.get("id")) for row in snapshot.get("papers", [])} or str(membership.get("fieldId")) not in {str(row.get("id")) for row in snapshot.get("fields", [])}: raise ValueError("unknown overlay membership")
            taxonomy = self.server.store.read_json("taxonomy")[1]
            facet_ids, facet_kinds, facet_semantic = known_facet_index(taxonomy, snapshot, source_id=state.get("server_id"), source_epoch=state.get("epoch"))
            paper_ids = {str(row.get("id")) for row in snapshot.get("papers", []) if isinstance(row, dict)}
            if "facetAssignments" in payload or "primaryFacetOverrides" in payload:
                raise ValueError("bulk facet overrides must use a paper entity patch")
            if "conceptQuality" in payload:
                if not isinstance(payload["conceptQuality"], dict): raise ValueError("conceptQuality must be an object")
                concept_ids = self._concept_ids_with_ledger(snapshot)
                for concept_id, quality in payload["conceptQuality"].items():
                    if concept_id not in concept_ids: raise ValueError("unknown concept quality concept")
                    validate_quality_override(quality, known_concepts=concept_ids)
        _, current, current_source = self.server.store.read_json("overlays")
        current = self._active_epoch_payload(current, current_source)
        merged = dict(current)
        if payload.get("entityType"):
            entities = dict(merged.get("entities", {})); entity_id = str(payload["entityId"])
            entity = dict(entities.get(entity_id, {})); entity.update(payload["patch"]); entity["entityType"] = payload["entityType"]; entities[entity_id] = entity; merged["entities"] = entities
        else:
            for key, value in payload.items():
                if key in {"revision", "expectedRevision", "sourceEpoch", "sourceId"}: continue
                if key in {"aliases", "pins"} and isinstance(value, dict):
                    existing = dict(merged.get(key, {})); existing.update(value); merged[key] = existing
                elif key == "memberships" and isinstance(value, list):
                    merged[key] = list(merged.get(key, [])) + copy.deepcopy(value)
                else:
                    merged[key] = value
        ok, revision, result = self.server.store.cas_json("overlays", merged, self._expected_revision(body), source=state)
        if not ok: self._send(412, {"error": "revision conflict", "revision": revision}); return
        self.server.manager.request_reindex()
        self._send(200, {"revision": revision, "overlays": result})

