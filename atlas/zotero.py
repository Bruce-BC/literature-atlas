"""Read-only Zotero local API client and complete snapshot collector."""
from __future__ import annotations

import json
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .storage import file_sha256


class ZoteroError(RuntimeError):
    pass


class IncompleteResponse(ZoteroError):
    pass


class SourceChanged(ZoteroError):
    pass


@dataclass(frozen=True)
class APIResponse:
    status: int
    headers: Mapping[str, str]
    body: Any


def _header(headers: Mapping[str, str], *names: str) -> str | None:
    lowered = {str(k).lower(): str(v) for k, v in headers.items()}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    return None


class ZoteroClient:
    """Minimal API3 client.

    ``transport`` is intentionally injectable.  It receives ``(method, url,
    headers)`` and may return an :class:`APIResponse`, a mapping, or a pair of
    ``(headers, body)`` for replay tests.
    """

    def __init__(self, base_url: str = "http://127.0.0.1:23119/api", *, timeout: float = 10, transport: Callable[..., Any] | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport
        self.server_id: str | None = None
        self.schema: int | None = None
        self.last_modified_version: str | None = None
        self.last_payload_complete = False

    def reset_identity(self) -> None:
        """Clear the per-reconciliation fence; permits a new Zotero DB epoch."""
        self.server_id = None
        self.schema = None
        self.last_modified_version = None

    def _request(self, path: str, params: Mapping[str, Any] | None = None) -> APIResponse:
        url = self.base_url + (path if path.startswith("/") else "/" + path)
        if params:
            query = urllib.parse.urlencode([(k, v) for k, v in params.items() if v is not None], doseq=True)
            url += ("&" if "?" in url else "?") + query
        headers = {"Accept": "application/json", "User-Agent": "literature-atlas/0.1"}
        if self.server_id:
            headers["Zotero-Server-ID"] = self.server_id
        try:
            if self.transport:
                raw = self.transport("GET", url, headers)
                if isinstance(raw, APIResponse):
                    response = raw
                elif isinstance(raw, tuple) and len(raw) == 2:
                    response = APIResponse(200, raw[0], raw[1])
                elif isinstance(raw, Mapping) and "body" in raw:
                    response = APIResponse(int(raw.get("status", 200)), raw.get("headers", {}), raw["body"])
                else:
                    response = APIResponse(200, {}, raw)
            else:
                req = urllib.request.Request(url, headers=headers, method="GET")
                with urllib.request.urlopen(req, timeout=self.timeout) as res:
                    data = res.read(32 * 1024 * 1024)
                    response_headers = dict(res.headers.items())
                    content_type = _header(response_headers, "Content-Type") or ""
                    text = data.decode("utf-8", errors="replace") if data else ""
                    if "/file/view/url" in path or "text/plain" in content_type:
                        body: Any = text.strip()
                    else:
                        try:
                            body = json.loads(text) if text else None
                        except json.JSONDecodeError as exc:
                            raise ZoteroError("malformed Zotero JSON") from exc
                    response = APIResponse(res.status, response_headers, body)
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
            raise ZoteroError("Zotero unavailable") from exc
        if response.status >= 400:
            raise ZoteroError(f"Zotero HTTP {response.status}")
        sid = _header(response.headers, "Zotero-Server-ID", "Zotero-Server-Id")
        schema = _header(response.headers, "Zotero-Schema-Version", "API-Schema", "Zotero-API-Schema")
        if sid:
            if self.server_id and self.server_id != sid:
                raise SourceChanged("Zotero server identity changed during request")
            self.server_id = sid
        if schema:
            try:
                self.schema = int(schema)
            except ValueError:
                pass
        modified = _header(response.headers, "Last-Modified-Version")
        if modified is not None:
            self.last_modified_version = modified
        return response

    def fence(self) -> tuple[str, int]:
        # API 3 identity is carried by normal API responses.  Avoid /instance
        # and other unsupported endpoints on Zotero 10.
        response = self._request("/users/0/items", {"format": "versions", "includeTrashed": 1, "limit": 1})
        sid = self.server_id or _header(response.headers, "Zotero-Server-ID", "Zotero-Server-Id")
        schema = self.schema or int(_header(response.headers, "Zotero-Schema-Version", "API-Schema", "Zotero-API-Schema") or 44)
        if not sid:
            raise ZoteroError("Zotero response omitted server identity")
        return sid, schema

    @staticmethod
    def _versions(body: Any) -> dict[str, int]:
        if not isinstance(body, Mapping):
            raise IncompleteResponse("version map is not an object")
        result: dict[str, int] = {}
        for key, value in body.items():
            if isinstance(value, Mapping):
                value = value.get("version")
            try:
                result[str(key)] = int(value)
            except (TypeError, ValueError):
                raise IncompleteResponse("invalid version map")
        return result

    @staticmethod
    def _payloads(body: Any) -> list[dict[str, Any]]:
        if isinstance(body, Mapping):
            if "data" in body and isinstance(body["data"], Mapping):
                return [dict(body)]
            # Some replay fakes return a key -> payload map.
            if all(isinstance(v, Mapping) for v in body.values()):
                return [dict(v) for v in body.values()]
        if isinstance(body, list):
            return [dict(v) for v in body if isinstance(v, Mapping)]
        raise IncompleteResponse("payload response is not an array")

    def version_map(self, library_id: int = 0) -> dict[str, int]:
        response = self._request(f"/users/{library_id}/items", {"format": "versions", "includeTrashed": 1})
        return self._versions(response.body)

    def payloads(self, library_id: int, keys: list[str], *, collection: bool = False) -> list[dict[str, Any]]:
        if not keys:
            return []
        path = f"/users/{library_id}/collections" if collection else f"/users/{library_id}/items"
        # Zotero's local API expands itemKey requests with children and applies
        # the limit after expansion. A complete unfiltered JSON response is the
        # exact source fence for both live and replay transports; callers then
        # validate the returned key/version set without hiding extras.
        response = self._request(path, {"includeTrashed": 1, "format": "json"})
        return self._payloads(response.body)

    def collection_versions(self, library_id: int = 0) -> dict[str, int]:
        response = self._request(f"/users/{library_id}/collections", {"format": "versions", "includeTrashed": 1})
        return self._versions(response.body)

    def collection_payloads(self, library_id: int, keys: list[str]) -> list[dict[str, Any]]:
        return self.payloads(library_id, keys, collection=True)

    def fulltext_versions(self, library_id: int = 0, since: int = 0) -> dict[str, int]:
        response = self._request(f"/users/{library_id}/fulltext", {"since": since})
        return self._versions(response.body)

    def fulltext(self, library_id: int, attachment_key: str) -> dict[str, Any]:
        response = self._request(f"/users/{library_id}/items/{urllib.parse.quote(attachment_key)}/fulltext")
        return dict(response.body) if isinstance(response.body, Mapping) else {"content": response.body}

    def attachment_url(self, library_id: int, key: str) -> str | None:
        response = self._request(f"/users/{library_id}/items/{urllib.parse.quote(key)}/file/view/url")
        if isinstance(response.body, str):
            return response.body
        if isinstance(response.body, Mapping):
            return response.body.get("url") or response.body.get("href")
        return None


def _item_data(payload: Mapping[str, Any]) -> dict[str, Any]:
    data = payload.get("data", payload)
    return dict(data) if isinstance(data, Mapping) else {}


def normalize_item(payload: Mapping[str, Any], version: int, library_id: int, server_id: str) -> dict[str, Any]:
    data = _item_data(payload)
    key = str(payload.get("key") or data.get("key") or "")
    item_type = str(data.get("itemType") or payload.get("itemType") or "unknown")
    parent = data.get("parentItem") or payload.get("parentItem")
    return {"key": key, "version": int(version), "library_id": int(library_id), "item_type": item_type, "parent_key": str(parent) if parent else None, "trashed": bool(data.get("deleted") or payload.get("deleted")), "payload": dict(payload), "server_id": server_id}


def normalize_collection(payload: Mapping[str, Any], version: int, library_id: int, server_id: str) -> dict[str, Any]:
    data = _item_data(payload)
    key = str(payload.get("key") or data.get("key") or "")
    return {"key": key, "version": int(version), "library_id": int(library_id), "trashed": bool(data.get("deleted") or payload.get("deleted")), "payload": dict(payload), "server_id": server_id}


def normalize_attachment(payload: Mapping[str, Any], version: int, library_id: int, client: ZoteroClient, server_id: str) -> dict[str, Any]:
    data = _item_data(payload)
    key = str(payload.get("key") or data.get("key") or "")
    parent = data.get("parentItem") or payload.get("parentItem")
    url = data.get("url") or payload.get("url")
    # For attachment records the API's current file/view URL is authoritative;
    # ``data.url`` may be a stale publisher URL or a Zotmoov-era path.
    try:
        current_url = client.attachment_url(library_id, key)
        if current_url:
            url = current_url
    except ZoteroError:
        pass
    if not url:
        try:
            # Zotmoov and Zotero can change attachment paths/keys. Resolve the
            # current view URL instead of reconstructing a filesystem path.
            url = client.attachment_url(library_id, key)
        except ZoteroError:
            url = None
    path = None
    public_url = str(url) if url else None
    status = "missing"
    content_hash = None
    if isinstance(url, str) and url.startswith("file://"):
        path = urllib.parse.unquote(urllib.parse.urlparse(url).path)
        # A local file URL is never exposed through the bridge. The registered
        # Zotero deep link remains a safe, current target for the desktop app.
        public_url = f"zotero://open-pdf/library/items/{urllib.parse.quote(key)}"
    if path:
        candidate = Path(path)
        if candidate.exists() and candidate.is_file():
            status = "readable"
            try:
                # Large live PDFs are hashed by the enrichment worker; keeping
                # initial metadata reconciliation bounded avoids blocking the
                # watcher on multi-hundred-megabyte files.
                if candidate.stat().st_size <= 1024 * 1024:
                    content_hash = file_sha256(candidate)
            except OSError:
                pass
    elif isinstance(url, str) and url.startswith(("http://", "https://", "zotero://")):
        status = "readable"
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            public_url = f"zotero://open-pdf/library/items/{urllib.parse.quote(key)}"
    return {"key": key, "version": int(version), "library_id": int(library_id), "parent_key": str(parent) if parent else None, "name": str(data.get("filename") or data.get("title") or key), "url": public_url, "path": path, "status": status, "content_hash": content_hash, "payload": dict(payload), "server_id": server_id}


def verify_exact_payloads(version_map: Mapping[str, int], payloads: list[Mapping[str, Any]], *, key_field: str = "key") -> None:
    seen: dict[str, int] = {}
    for payload in payloads:
        data = _item_data(payload)
        key = str(payload.get(key_field) or data.get(key_field) or "")
        if not key or key in seen:
            raise IncompleteResponse("duplicate or missing payload key")
        value = payload.get("version", data.get("version"))
        if value is None:
            raise IncompleteResponse("payload missing version")
        seen[key] = int(value)
    if set(seen) != set(version_map) or any(seen[k] != int(v) for k, v in version_map.items()):
        raise IncompleteResponse("payload coverage does not exactly match version map")

