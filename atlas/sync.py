"""Source reconciliation, watcher and generation-fenced graph publication."""
from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .storage import SQLiteStore
from .migration import RECIPE_VERSION, create_migration_receipt, is_recipe_migration, record_publication, validate_migration
from .taxonomy import field_stats, msc_codes_from_extra, taxonomy_input_state, ONTOLOGY_POLICY_VERSION, RULE_VERSION
from .trace import TraceLog, summarize_snapshot
from .category_colors import build_category_color_registry
from .zotero import (
    IncompleteResponse,
    SourceChanged,
    ZoteroClient,
    ZoteroError,
    normalize_attachment,
    normalize_collection,
    normalize_item,
    verify_exact_payloads,
)

# GraphEngine is a required production boundary. Tests inject a compatible
# fake through SyncManager(graph_engine=...), while model/provider failures are
# reported by the engine itself rather than masked as a missing backend module.
from .graph import GraphEngine  # type: ignore

GRAPH_RECIPE_VERSION = RECIPE_VERSION


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class SyncManager:
    def __init__(self, store: SQLiteStore, client: ZoteroClient, *, cache_dir: Path | str | None = None, graph_engine: Any = None, embedding_provider: str | None = None, poll_interval: float = 30.0, library_id: int = 0, demo_snapshot: dict[str, Any] | None = None):
        if not 5 <= float(poll_interval) <= 300:
            raise ValueError("poll_interval must be between 5 and 300 seconds")
        self.store, self.client = store, client
        self.trace = TraceLog(store.data_dir)      # local, bounded, privacy-safe structured trace (enums/counts only)
        self.cache_dir = Path(cache_dir or store.data_dir / "models")
        self.graph_engine = graph_engine
        self.poll_interval = float(poll_interval)
        self.library_id = int(library_id)
        persisted_settings = store.read_json("settings")[1]
        saved_provider = persisted_settings.get("provider")
        self.embedding_provider = embedding_provider if embedding_provider is not None else (saved_provider if saved_provider in {"e5", "tfidf"} else "tfidf")
        self.include_indexed_text = bool(persisted_settings.get("includeIndexedText", False))
        self.demo_snapshot = copy.deepcopy(demo_snapshot)
        self._generation_lock = threading.RLock()
        self._sync_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="atlas-enrichment")
        self._future: Future[Any] | None = None
        self._engine_instance: Any = graph_engine
        self._engine_provider: str | None = self.embedding_provider if graph_engine is None else None
        self._reindex_pending = False
        self._watch_stop = threading.Event()
        self._watch_thread: threading.Thread | None = None
        self._status_lock = threading.RLock()
        self._status: dict[str, Any] = {"phase": "idle", "stale": True, "lastSuccess": None, "message": None, "observedLag": None, "observedLatency": None, "triggerMode": None, "watcherInterval": self.poll_interval, "lastError": None, "indexing": False, "modelStatus": {"provider": self.embedding_provider, "state": "pending"}}

    @property
    def status(self) -> dict[str, Any]:
        with self._status_lock:
            result = copy.deepcopy(self._status)
        stored = self.store.get_snapshot()
        result["revision"] = (stored or (0, 0, {}))[0]
        running = bool(self._future and not self._future.done())
        if running:
            result["indexing"] = True
        # A restarted bridge starts with a pending in-memory status even when
        # a completed graph is already cached. Restore only the matching
        # source/generation/recipe/provider, and never mask an active job or
        # a reported error. Connectivity remains independently stale/offline.
        model = result.get("modelStatus", {})
        if stored and not result.get("indexing") and isinstance(model, dict) and model.get("state") == "pending":
            state = self.store.state() or {}
            snapshot = stored[2]
            source = snapshot.get("source", {})
            cached = snapshot.get("modelStatus", {})
            if (source.get("serverId") == state.get("server_id") and source.get("epoch") == state.get("epoch")
                    and stored[1] == state.get("generation") and isinstance(cached, dict)
                    and cached.get("provider") == self.embedding_provider
                    and cached.get("recipeVersion") == GRAPH_RECIPE_VERSION
                    and (cached.get("state") == "ready" or cached.get("status") == "ready")
                    and not cached.get("failed")):
                result["modelStatus"] = copy.deepcopy(cached)
        if stored and not running and result.get("phase") != "offline":
            state = self.store.state() or {}
            snapshot = stored[2]
            source, snapshot_status = snapshot.get("source", {}), snapshot.get("status", {})
            cached_model = snapshot.get("modelStatus", {})
            if (source.get("serverId") == state.get("server_id") and source.get("epoch") == state.get("epoch")
                    and stored[1] == state.get("generation") and snapshot_status.get("phase") == "error"
                    and isinstance(cached_model, dict) and cached_model.get("state") == "error"):
                result.update(phase="error", stale=True, message="enrichment failed", lastError=cached_model.get("error"), modelStatus=copy.deepcopy(cached_model))
        return result

    def _set_status(self, **updates: Any) -> None:
        with self._status_lock:
            self._status.update(updates)

    def start_watcher(self) -> None:
        if self._watch_thread and self._watch_thread.is_alive():
            return
        self._watch_stop.clear()
        self._watch_thread = threading.Thread(target=self._watch_loop, name="atlas-watcher", daemon=True)
        self._watch_thread.start()

    def stop_watcher(self) -> None:
        self._watch_stop.set()
        if self._watch_thread and self._watch_thread.is_alive():
            self._watch_thread.join(timeout=2)
        future_done = not self._future or self._future.done()
        if self._future and not self._future.done():
            try:
                self._future.result(timeout=2)
            except Exception:
                pass
        self._executor.shutdown(wait=future_done, cancel_futures=True)
        if future_done:
            self.store.close_all()

    def _watch_loop(self) -> None:
        # Immediate startup sync is part of the bridge contract.
        backoff = self.poll_interval
        while not self._watch_stop.is_set():
            try:
                self.sync(trigger="automatic")
                status = self.status
                if status.get("phase") == "offline" or (status.get("phase") == "error" and status.get("message") != "enrichment failed"):
                    raise ZoteroError("automatic sync failed")
                try:
                    self.sync_fulltext()
                except ZoteroError:
                    # Metadata remains a valid source snapshot even when the
                    # independent full-text clock is temporarily unavailable.
                    self._set_status(lastError="fulltext_unavailable")
                backoff = self.poll_interval
            except Exception as exc:  # sync records details without leaking source values
                self._set_status(phase="offline", stale=True, lastError=type(exc).__name__, message="source unavailable")
                backoff = min(300.0, max(self.poll_interval, backoff * 2))
                with self._status_lock:
                    self._status["watcherInterval"] = backoff
            self._watch_stop.wait(backoff)

    def sync(self, *, trigger: str = "manual") -> dict[str, Any]:
        if not self._sync_lock.acquire(blocking=False):
            current = self.store.get_snapshot()
            return current[2] if current else self._empty_snapshot("sync already running")
        started = time.monotonic()
        self._set_status(phase="syncing", message=None, lastError=None, indexing=False)
        try:
            snapshot = self._sync_once()
            self._set_status(phase="idle", stale=False, lastSuccess=now_iso(), message=None, lastError=None, observedLag=None, observedLatency=round(time.monotonic() - started, 3), triggerMode=trigger, indexing=bool(self._future and not self._future.done()), watcherInterval=self.poll_interval)
            return snapshot
        except (ZoteroError, IncompleteResponse, SourceChanged, OSError, ValueError) as exc:
            self._set_status(phase="offline" if isinstance(exc, ZoteroError) else "error", stale=True, message="sync failed", lastError=type(exc).__name__)
            current = self.store.get_snapshot()
            if current:
                return current[2]
            raise
        finally:
            self._sync_lock.release()

    def _sync_once(self) -> dict[str, Any]:
        if self.demo_snapshot is not None:
            return self._sync_demo()
        self.client.reset_identity()
        server_id, schema = self.client.fence()
        first_modified_version = self.client.last_modified_version
        state, changed = self.store.ensure_source(server_id, schema, "Zotero")
        if changed:
            generation = state["generation"]
        else:
            generation = int(state["generation"])
            state = self.store.state() or state
        versions = self.client.version_map(self.library_id)
        if self.client.server_id and self.client.server_id != server_id:
            raise SourceChanged("source changed during version map")
        collection_versions = self.client.collection_versions(self.library_id)
        old_items = self.store.current_items(include_trashed=True)
        old_item_versions = {(int(row["library_id"]), str(row["item_key"])): int(row["version"]) for row in old_items if not row["trashed"] or str(row["item_key"]) in versions}
        old_collections = self.store.current_collections(include_trashed=True)
        old_collection_versions = {(int(row["library_id"]), str(row["collection_key"])): int(row["version"]) for row in old_collections if not row["trashed"] or str(row["collection_key"]) in collection_versions}
        item_fingerprint = {(self.library_id, str(k)): int(v) for k, v in versions.items()}
        collection_fingerprint = {(self.library_id, str(k)): int(v) for k, v in collection_versions.items()}
        if not changed and old_item_versions == item_fingerprint and old_collection_versions == collection_fingerprint:
            second_versions = self.client.version_map(self.library_id)
            second_collection_versions = self.client.collection_versions(self.library_id)
            second_id, second_schema = self.client.fence()
            if second_id != server_id or second_schema != schema:
                raise SourceChanged("source fence changed during unchanged reconciliation")
            if second_versions != versions or second_collection_versions != collection_versions:
                raise SourceChanged("source version fence changed during unchanged reconciliation")
            if self._refresh_attachment_targets(state):
                current = self.store.get_snapshot()
                if current:
                    papers, snapshot_attachments, snapshot_collections = self._normalized_records(state)
                    refreshed = self._base_graph(papers, snapshot_attachments, snapshot_collections, state, current[2].get("modelStatus", {}), previous=current[2])
                    refreshed["status"] = copy.deepcopy(current[2].get("status", refreshed["status"]))
                    self.store.publish_snapshot(refreshed, int(state["generation"]))
                    return refreshed
            current = self.store.get_snapshot()
            if current:
                # A cache created by an older backend may still contain
                # trashed attachment rows or stale standalone nodes even when
                # Zotero metadata versions are unchanged. Reconcile the
                # active projection from source rows before serving it.
                active_papers, active_attachments, active_collections = self._normalized_records(state)
                if {a.get("id") for a in current[2].get("attachments", [])} != {a.get("id") for a in active_attachments} or {p.get("id") for p in current[2].get("papers", [])} != {p.get("id") for p in active_papers}:
                    refreshed = self._base_graph(active_papers, active_attachments, active_collections, state, current[2].get("modelStatus", {}), previous=current[2])
                    self.store.publish_snapshot(refreshed, int(state["generation"]))
                    return refreshed
                model_status = current[2].get("modelStatus", {})
                derivation_ready = model_status.get("state") == "ready" or model_status.get("status") == "ready"
                if model_status.get("recipeVersion") != GRAPH_RECIPE_VERSION or not derivation_ready or model_status.get("failed"):
                    self.store.bump_generation()
                    current_state = self.store.state() or state
                    self._schedule_enrichment(int(current_state["generation"]), current_state)
                return current[2]
        payloads = self.client.payloads(self.library_id, list(versions))
        verify_exact_payloads(versions, payloads)
        item_by_key = {str(p.get("key") or p.get("data", {}).get("key")): p for p in payloads}
        items = [normalize_item(item_by_key[k], versions[k], self.library_id, server_id) for k in versions]
        attachment_keys = [k for k in versions if str((item_by_key[k].get("data") or {}).get("itemType") or item_by_key[k].get("itemType")) == "attachment" and not bool((item_by_key[k].get("data") or {}).get("deleted") or item_by_key[k].get("deleted"))]
        with ThreadPoolExecutor(max_workers=16, thread_name_prefix="atlas-attachment") as attachment_pool:
            attachments = list(attachment_pool.map(lambda key: normalize_attachment(item_by_key[key], versions[key], self.library_id, self.client, server_id), attachment_keys))
        collection_payloads = self.client.collection_payloads(self.library_id, list(collection_versions))
        verify_exact_payloads(collection_versions, collection_payloads)
        collection_by_key = {str(p.get("key") or (p.get("data") or {}).get("key")): p for p in collection_payloads}
        collections = [normalize_collection(collection_by_key[k], collection_versions[k], self.library_id, server_id) for k in collection_versions]
        second_versions = self.client.version_map(self.library_id)
        second_collection_versions = self.client.collection_versions(self.library_id)
        second_id, second_schema = self.client.fence()
        second_modified_version = self.client.last_modified_version
        if second_id != server_id or second_schema != schema:
            raise SourceChanged("source fence changed during reconciliation")
        if second_versions != versions or second_collection_versions != collection_versions:
            raise SourceChanged("source version fence changed during reconciliation")
        if first_modified_version is not None and second_modified_version is not None and first_modified_version != second_modified_version:
            raise SourceChanged("source last-modified fence changed during reconciliation")
        old_keys = {(int(row["library_id"]), str(row["item_key"])) for row in old_items}
        missing = old_keys - {(self.library_id, str(k)) for k in versions}
        self.store.bump_generation()
        self.store.replace_source_rows(state, items, collections, attachments, missing, metadata_cursor=max(versions.values(), default=0))
        generation = int((self.store.state() or state)["generation"])
        current_state = self.store.state() or state
        papers, snapshot_attachments, snapshot_collections = self._normalized_records(current_state)
        previous_snapshot = self.store.get_snapshot()[2] if self.store.get_snapshot() else None
        baseline = self._base_graph(papers, snapshot_attachments, snapshot_collections, current_state, {"provider": self.embedding_provider, "state": "pending"}, previous=previous_snapshot)
        published_revision = self.store.publish_snapshot(baseline, generation)
        if published_revision is None:
            raise SourceChanged("source generation changed before metadata publish")
        baseline["revision"] = published_revision
        self._schedule_enrichment(generation, state)
        return baseline

    def _refresh_attachment_targets(self, state: dict[str, Any]) -> bool:
        rows = self.store.current_attachments()
        if not rows:
            return False
        def resolve(row: dict[str, Any]) -> dict[str, Any]:
            payload = json.loads(row["payload"])
            return normalize_attachment(payload, int(row["version"]), int(row["library_id"]), self.client, state["server_id"])
        with ThreadPoolExecutor(max_workers=16, thread_name_prefix="atlas-attachment-refresh") as pool:
            refreshed = list(pool.map(resolve, rows))
        return self.store.refresh_attachment_targets(state, refreshed)

    def _sync_demo(self) -> dict[str, Any]:
        source = copy.deepcopy(self.demo_snapshot.get("source", {}))
        server_id = str(source.get("serverId") or source.get("id") or "demo")
        state, _ = self.store.ensure_source(server_id, int(source.get("schema", 44)), str(source.get("alias", "synthetic-demo")), epoch_override=str(source.get("epoch") or "demo-epoch"))
        existing = self.store.get_snapshot()
        if existing and existing[2].get("source", {}).get("serverId") == server_id and existing[2].get("source", {}).get("epoch") == state["epoch"]:
            return existing[2]
        generation = self.store.bump_generation()
        state = self.store.state() or state
        snapshot = copy.deepcopy(self.demo_snapshot)
        snapshot["source"] = self._source(state)
        self._ensure_snapshot_shape(snapshot)
        snapshot["status"] = {"phase": "idle", "stale": False, "lastSuccess": now_iso(), "message": None, "observedLag": None, "watcherInterval": self.poll_interval}
        snapshot["revision"] = self.store.publish_snapshot(snapshot, int(state["generation"]))
        self._set_status(modelStatus=snapshot.get("modelStatus", {}), indexing=False)
        return snapshot

    def _apply_demo_edits(self) -> None:
        current = self.store.get_snapshot(); state = self.store.state()
        if not current or not state:
            return
        snapshot = copy.deepcopy(current[2]); taxonomy = self.store.read_json("taxonomy")[1]; overlays = self.store.read_json("overlays")[1]
        by_field = {str(x.get("id")): x for x in snapshot.get("fields", [])}; by_concept = {str(x.get("id")): x for x in snapshot.get("concepts", [])}
        if isinstance(taxonomy, dict):
            for x in taxonomy.get("fields", []):
                if isinstance(x, dict) and x.get("id"): by_field[str(x["id"])] = copy.deepcopy(x)
            for x in taxonomy.get("concepts", []):
                if isinstance(x, dict) and x.get("id"): by_concept[str(x["id"])] = copy.deepcopy(x)
        snapshot["fields"] = list(by_field.values()); snapshot["concepts"] = list(by_concept.values())
        papers = {str(x.get("id")): x for x in snapshot.get("papers", [])}; memberships = list(snapshot.get("memberships", []))
        entities = overlays.get("entities", {}) if isinstance(overlays, dict) else {}
        if isinstance(entities, dict):
            for entity_id, patch in entities.items():
                if not isinstance(patch, dict): continue
                paper = papers.get(str(entity_id))
                if paper is not None:
                    if isinstance(patch.get("fieldIds"), list): paper["fieldIds"] = list(patch["fieldIds"]); paper["displayFieldId"] = paper["fieldIds"][0] if paper["fieldIds"] else None
                    if "pinned" in patch: paper["pinned"] = bool(patch["pinned"])
                    if isinstance(patch.get("memberships"), list): memberships.extend(copy.deepcopy(patch["memberships"]))
                concept = by_concept.get(str(entity_id))
                if concept is not None and isinstance(patch.get("aliases"), list): concept["aliases"] = list(patch["aliases"])
        snapshot["papers"] = list(papers.values()); snapshot["memberships"] = memberships
        snapshot["modelStatus"] = {**snapshot.get("modelStatus", {}), "recipeVersion": GRAPH_RECIPE_VERSION}
        snapshot["status"] = {**snapshot.get("status", {}), "phase": "idle", "stale": False, "message": None}
        self.store.publish_snapshot(snapshot, int(state["generation"]))

    def _schedule_enrichment(self, generation: int, state: dict[str, Any]) -> None:
        if self._future and not self._future.done():
            with self._generation_lock:
                self._reindex_pending = True
            return
        with self._generation_lock:
            self._reindex_pending = False
        self._future = self._executor.submit(self._enrich_wrapper, generation, copy.deepcopy(state))

    def _enrich_wrapper(self, generation: int, state: dict[str, Any]) -> None:
        while True:
            self._enrich(generation, state)
            with self._generation_lock:
                pending = self._reindex_pending
                self._reindex_pending = False
            if not pending:
                return
            current = self.store.state()
            if not current:
                return
            generation, state = int(current["generation"]), current

    def _engine(self) -> Any:
        if self.graph_engine is not None:
            return self.graph_engine
        if self._engine_instance is not None and self._engine_provider == self.embedding_provider:
            return self._engine_instance
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._engine_instance = GraphEngine(cache_dir=self.cache_dir, provider=self.embedding_provider, allow_download=False)
        self._engine_provider = self.embedding_provider
        return self._engine_instance

    def _trace_recipe(self) -> dict[str, str]:
        return {"recipe": GRAPH_RECIPE_VERSION, "ruleVersion": RULE_VERSION, "ontologyPolicy": ONTOLOGY_POLICY_VERSION}

    def _stale_fence_reasons(self, inputs: dict[str, Any], state: dict[str, Any], generation: int) -> dict[str, int]:
        """Which captured fence moved (enum keys only)."""
        now = self.store.snapshot_inputs()
        reasons: dict[str, int] = {}
        if now["taxonomyRevision"] != inputs["taxonomyRevision"]: reasons["fence.taxonomy_changed"] = 1
        if now["overlayRevision"] != inputs["overlayRevision"]: reasons["fence.overlays_changed"] = 1
        if now["configRevision"] != inputs["configRevision"]: reasons["fence.config_changed"] = 1
        if now["sourceId"] != state["server_id"] or now["sourceEpoch"] != state["epoch"] or now["generation"] != generation: reasons["fence.source_or_generation_changed"] = 1
        return reasons or {"fence.unspecified": 1}

    def _requeue(self) -> None:
        """Ask the enrichment loop to rebuild from the latest inputs."""
        with self._generation_lock:
            self._reindex_pending = True

    def _enrich(self, generation: int, state: dict[str, Any]) -> None:
        # Capture every fence BEFORE reading inputs or building, atomically.  The
        # graph is published only against these captured revisions; any newer
        # taxonomy/overlay/settings/source write makes publication a no-op and
        # re-queues a rebuild from the latest inputs (never a stale graph).
        inputs = self.store.snapshot_inputs()
        if inputs["sourceId"] != state["server_id"] or inputs["sourceEpoch"] != state["epoch"] or inputs["generation"] != generation:
            return
        trace_ids = {"source_id": state["server_id"], "source_epoch": state["epoch"], "recipe": self._trace_recipe()}
        self.trace.emit("build.start", revisions={"taxonomy": inputs["taxonomyRevision"], "overlays": inputs["overlayRevision"], "config": inputs["configRevision"],
                                                   "snapshot": inputs["snapshotRevision"], "generation": generation}, **trace_ids)
        try:
            papers, attachments, collections = self._normalized_records(state)
            engine = self._engine()
            stored = self.store.get_snapshot()
            previous = stored[2] if stored else None
            taxonomy, overlays, settings = copy.deepcopy(inputs["taxonomy"]), copy.deepcopy(inputs["overlays"]), copy.deepcopy(inputs["settings"])
            for source, name in ((inputs["taxonomySource"], "taxonomy"), (inputs["overlaySource"], "overlays")):
                if source and (source.get("server_id") != state["server_id"] or source.get("epoch") != state["epoch"]):
                    if name == "taxonomy": taxonomy = {}
                    else: overlays = {}
            fence_kwargs = dict(expected_source=state, expected_taxonomy_revision=inputs["taxonomyRevision"],
                                expected_overlay_revision=inputs["overlayRevision"], expected_config_revision=inputs["configRevision"])
            fences = {"sourceId": state["server_id"], "sourceEpoch": state["epoch"], "generation": generation,
                      "taxonomyRevision": inputs["taxonomyRevision"], "overlayRevision": inputs["overlayRevision"], "configRevision": inputs["configRevision"]}
            input_state, input_reason = taxonomy_input_state(taxonomy)
            if input_state in {"invalid", "disabled"} and previous:
                # Never silently re-enable builtin rules: keep the last valid
                # snapshot and record why plus how to recover.
                kept = copy.deepcopy(previous)
                kept["taxonomyState"] = {"status": input_state, "sourceId": state["server_id"], "sourceEpoch": state["epoch"],
                                         "reason": input_reason or f"taxonomy {input_state}", "recovery": "restore-or-replace-taxonomy-then-resync",
                                         "revision": inputs["taxonomyRevision"]}
                kept["fences"] = fences
                kept_revision = self.store.publish_snapshot_fenced(kept, generation, **fence_kwargs)
                if kept_revision is None:
                    self.trace.emit("publish.stale", reasons=self._stale_fence_reasons(inputs, state, generation), **trace_ids)
                    self._requeue()
                else:
                    self.trace.emit("publish.kept_previous", revisions={"snapshot": kept_revision, "generation": generation}, **trace_ids)
                return
            overlays = self._canonical_overlays(overlays, papers)
            field_aliases = overlays.pop("fieldAliases", {}) if isinstance(overlays, dict) else {}
            if isinstance(field_aliases, dict) and isinstance(taxonomy, dict):
                taxonomy = copy.deepcopy(taxonomy)
                for field in taxonomy.get("fields", []):
                    if isinstance(field, dict) and field.get("id") in field_aliases:
                        field["aliases"] = list(field_aliases[field["id"]])
            result = engine.build(papers, source=self._source(state), taxonomy=taxonomy, overlays=overlays, settings=settings, attachments=attachments, previous=previous)
            result = self._complete_snapshot(result, state, papers, attachments, collections, {"provider": self.embedding_provider, "state": "ready"}, previous=previous)
            result["fences"] = fences
            # Phase 1: validate the new derived data; invalid output is never published.
            problems = validate_migration(previous, result, inputs)
            summary_counts, summary_reasons = summarize_snapshot(result)
            self.trace.emit("build.complete", counts=summary_counts, reasons=summary_reasons, **trace_ids)
            if problems:
                self.trace.emit("publish.validation_failed", reasons={"validation.failed": 1}, **trace_ids)
                record_publication(self.store, source_epoch=state["epoch"], generation=generation, inputs=inputs, outcome="validation-failed", detail=problems[0])
                raise ValueError("migration validation failed: " + problems[0])
            current = self.store.state()
            if not current or current["server_id"] != state["server_id"] or current["epoch"] != state["epoch"] or int(current["generation"]) != generation:
                return
            # Only an actual recipe migration needs the full original backup; it is created
            # once and never overwritten.  Publication facts go to the append-only audit log.
            if is_recipe_migration(previous):
                create_migration_receipt(self.store, before=previous, inputs=inputs, source_id=state["server_id"],
                                         source_epoch=state["epoch"], generation=generation)
            # Phase 2: publish against the captured fences.
            revision = self.store.publish_snapshot_fenced(result, generation, **fence_kwargs)
            if revision is None:
                record_publication(self.store, source_epoch=state["epoch"], generation=generation, inputs=inputs, outcome="discarded-stale", snapshot=result)
                self.trace.emit("publish.stale", reasons=self._stale_fence_reasons(inputs, state, generation), **trace_ids)
                latest = self.store.state()
                if latest and latest["server_id"] == state["server_id"] and latest["epoch"] == state["epoch"] and int(latest["generation"]) == generation:
                    self._requeue()
                return
            record_publication(self.store, source_epoch=state["epoch"], generation=generation, inputs=inputs, outcome="published", snapshot=result, snapshot_revision=revision)
            self.trace.emit("publish.ok", revisions={"snapshot": revision, "generation": generation}, counts=summary_counts, reasons=summary_reasons, **trace_ids)
            self.store.prune_migrations()
            self._set_status(modelStatus=result.get("modelStatus", {}), indexing=False)
        except Exception as exc:
            # Publish errors only for the still-current source and generation;
            # an older worker must not overwrite a newer job's state.
            current = self.store.get_snapshot()
            source_state = self.store.state()
            if (current and source_state and source_state["server_id"] == state["server_id"]
                    and source_state["epoch"] == state["epoch"] and int(source_state["generation"]) == generation):
                failed = copy.deepcopy(current[2])
                failed["status"] = {**failed.get("status", {}), "phase": "error", "stale": True, "message": "enrichment failed"}
                failed["modelStatus"] = {"provider": self.embedding_provider, "state": "error", "error": type(exc).__name__}
                if self.store.publish_snapshot(failed, generation) is not None:
                    latest = self.store.state()
                    if latest and latest["server_id"] == state["server_id"] and latest["epoch"] == state["epoch"] and int(latest["generation"]) == generation:
                        self._set_status(phase="error", stale=True, message="enrichment failed", lastError=type(exc).__name__, modelStatus=failed["modelStatus"], indexing=False)

    def wait_for_idle(self, timeout: float = 10) -> None:
        if self._future:
            self._future.result(timeout=timeout)

    def request_reindex(self, *, provider: str | None = None) -> None:
        if self.demo_snapshot is not None:
            self._apply_demo_edits()
            return
        if provider in {"e5", "tfidf"}:
            self.embedding_provider = provider
        state = self.store.state()
        if not state:
            return
        generation = self.store.bump_generation()
        self._schedule_enrichment(generation, state)

    def sync_fulltext(self) -> dict[str, Any]:
        """Advance the independent attachment/full-text clock.

        Zotero's full-text version namespace is unrelated to item metadata
        versions. A malformed attachment response leaves the full-text cursor
        unchanged and never deletes metadata rows.
        """
        if self.demo_snapshot is not None:
            return {"updated": 0, "cursor": None}
        state = self.store.state()
        if not state:
            return {"updated": 0, "cursor": None}
        cursor = state.get("fulltext_cursor") or 0
        missing_text = {str(row["attachment_key"]) for row in self.store.current_attachments() if not row.get("indexed_text")}
        query_since = 0 if self.include_indexed_text and missing_text else int(cursor)
        versions = self.client.fulltext_versions(self.library_id, query_since)
        attachments = {str(row["attachment_key"]): row for row in self.store.current_attachments()}
        if not self.include_indexed_text:
            version_rows = [(key, int(version)) for key, version in versions.items() if key in attachments]
            if version_rows:
                if not self.store.update_fulltext_versions(state, version_rows, max(versions.values())):
                    return {"updated": 0, "cursor": cursor, "indexedVersions": 0}
            return {"updated": 0, "cursor": max(versions.values(), default=cursor), "indexedVersions": len(version_rows)}
        updates: list[tuple[str, int, int | None, int | None, str | None, str | None]] = []
        for key, version in versions.items():
            if key not in attachments:
                continue
            if key not in missing_text and int(version) <= int(attachments[key].get("fulltext_version") or 0):
                continue
            content = self.client.fulltext(self.library_id, key)
            if not isinstance(content, dict):
                raise IncompleteResponse("fulltext payload is not an object")
            indexed = content.get("indexedPages")
            total = content.get("totalPages")
            raw_text = content.get("content") or content.get("text") or ""
            if not isinstance(raw_text, str):
                raw_text = str(raw_text)
            indexed_text = raw_text[:200_000]
            text_hash = "sha256:" + hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
            try:
                indexed = int(indexed) if indexed is not None else None
                total = int(total) if total is not None else None
            except (TypeError, ValueError) as exc:
                raise IncompleteResponse("invalid fulltext page counts") from exc
            updates.append((key, int(version), indexed, total, indexed_text, text_hash))
        if versions:
            if not self.store.update_fulltext_batch(state, updates, max(versions.values())):
                return {"updated": 0, "cursor": cursor}
            stored = self.store.get_snapshot()
            if stored:
                papers, snapshot_attachments, snapshot_collections = self._normalized_records(state)
                current_snapshot = stored[2]
                refreshed = self._base_graph(papers, snapshot_attachments, snapshot_collections, state, current_snapshot.get("modelStatus", {}), previous=current_snapshot)
                refreshed["status"] = copy.deepcopy(current_snapshot.get("status", refreshed["status"]))
                self.store.publish_snapshot(refreshed, int(state["generation"]))
            self.request_reindex()
        return {"updated": len(updates), "cursor": max(versions.values(), default=cursor)}

    def _source(self, state: dict[str, Any]) -> dict[str, Any]:
        return {"id": state["server_id"], "serverId": state["server_id"], "epoch": state["epoch"], "alias": state.get("alias") or "Zotero", "zoteroApi": "3", "schema": int(state.get("schema") or 44)}

    @staticmethod
    def _canonical_overlays(overlays: Any, papers: list[dict[str, Any]]) -> dict[str, Any]:
        if not isinstance(overlays, dict):
            return {}
        result = copy.deepcopy(overlays)
        entities = result.pop("entities", {})
        if not isinstance(entities, dict):
            return result
        paper_ids = {str(p.get("id")) for p in papers}
        paper_fields = dict(result.get("paperFields", {}))
        paper_facets = dict(result.get("paperFacets", {})) if isinstance(result.get("paperFacets"), dict) else {}
        primary_facets = dict(result.get("primaryFacetOverrides", {})) if isinstance(result.get("primaryFacetOverrides"), dict) else {}
        quality_overrides = dict(result.get("conceptQualityOverrides", {})) if isinstance(result.get("conceptQualityOverrides"), dict) else {}
        memberships = list(result.get("memberships", []))
        concept_aliases = dict(result.get("conceptAliases", {}))
        for entity_id, raw in entities.items():
            if not isinstance(raw, dict):
                continue
            patch = raw
            if entity_id in paper_ids:
                if isinstance(patch.get("fieldIds"), list):
                    paper_fields[entity_id] = list(patch["fieldIds"])
                for paper in papers:
                    if paper.get("id") == entity_id and "pinned" in patch:
                        paper["pinned"] = bool(patch["pinned"])
                if isinstance(patch.get("facetAssignments"), list):
                    paper_facets[entity_id] = copy.deepcopy(patch["facetAssignments"])
                if isinstance(patch.get("primaryFacetOverrides"), dict):
                    primary_facets[entity_id] = copy.deepcopy(patch["primaryFacetOverrides"])
            if str(entity_id).startswith("concept:") and isinstance(patch.get("quality"), dict):
                quality_overrides[entity_id] = copy.deepcopy(patch["quality"])
            if isinstance(patch.get("aliases"), list) and str(entity_id).startswith("concept:"):
                concept_aliases[entity_id] = list(patch["aliases"])
            if isinstance(patch.get("aliases"), list) and str(entity_id).startswith("field:"):
                field_aliases = dict(result.get("fieldAliases", {})); field_aliases[entity_id] = list(patch["aliases"]); result["fieldAliases"] = field_aliases
            if isinstance(patch.get("memberships"), list):
                memberships.extend(x for x in patch["memberships"] if isinstance(x, dict))
        # Facet/quality overrides are consumed by the graph as entity patches.
        # Only those keys are passed through (everything else was flattened
        # above), so no override is applied twice.
        passthrough: dict[str, Any] = {}
        for entity_id, raw in entities.items():
            if isinstance(raw, dict):
                keep = {k: copy.deepcopy(raw[k]) for k in ("facetAssignments", "primaryFacetOverrides", "excluded", "protected", "quality") if k in raw}
                if keep:
                    keep["entityType"] = raw.get("entityType") or ("paper" if entity_id in paper_ids else "concept" if str(entity_id).startswith("concept:") else "field")
                    passthrough[str(entity_id)] = keep
        if passthrough: result["entities"] = passthrough
        if paper_fields: result["paperFields"] = paper_fields
        if memberships: result["memberships"] = memberships
        if concept_aliases: result["conceptAliases"] = concept_aliases
        if paper_facets: result["paperFacets"] = paper_facets
        if primary_facets: result["primaryFacetOverrides"] = primary_facets
        if quality_overrides: result["conceptQualityOverrides"] = quality_overrides
        return result

    def _normalized_records(self, state: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        state = state or self.store.state()
        if not state:
            return [], [], []
        all_item_rows = self.store.current_items(include_trashed=True)
        active_item_keys = {str(row["item_key"]) for row in all_item_rows if not row["trashed"]}
        raw_items = [row for row in all_item_rows if not row["trashed"]]
        raw_atts = self.store.current_attachments()
        active_attachment_keys = {str(row["attachment_key"]) for row in raw_atts if str(row["attachment_key"]) in active_item_keys}
        parents = {str(row["item_key"]) for row in raw_items if row["item_type"] not in {"attachment", "note", "annotation"}}
        attachments: list[dict[str, Any]] = []
        attachment_ids_by_parent: dict[str, list[str]] = {}
        for row in raw_atts:
            if str(row["attachment_key"]) not in active_item_keys:
                continue
            if row.get("parent_key") and row["parent_key"] not in parents:
                continue  # parent trash suppresses children
            attachment_key = row["attachment_key"]
            aid = f"attachment:{state['server_id']}:{row['library_id']}:{attachment_key}"
            attachments.append({"id": aid, "parentId": f"zotero:{state['server_id']}:{row['library_id']}:{row['parent_key']}" if row.get("parent_key") else "", "name": row["name"], "status": row["status"] if row["status"] in {"readable", "indexed", "missing", "scan", "error"} else "missing", "contentHash": row.get("content_hash"), "url": row.get("url"), "key": attachment_key})
            if row.get("text_hash"):
                attachments[-1]["textHash"] = row["text_hash"]
            if row.get("parent_key"):
                attachment_ids_by_parent.setdefault(str(row["parent_key"]), []).append(aid)
        papers: list[dict[str, Any]] = []
        for row in raw_items:
            if row["item_type"] in {"attachment", "note", "annotation"}:
                continue
            payload = json.loads(row["payload"])
            data = payload.get("data", payload) if isinstance(payload, dict) else {}
            creators = []
            for creator in data.get("creators", []) if isinstance(data, dict) else []:
                if not isinstance(creator, dict):
                    continue
                name = creator.get("name") or " ".join(filter(None, [creator.get("firstName"), creator.get("lastName")]))
                if name:
                    creators.append(str(name))
            tags = data.get("tags", []) if isinstance(data, dict) else []
            keywords = [str(t.get("tag", "")) if isinstance(t, dict) else str(t) for t in tags]
            doi = data.get("DOI") or data.get("doi")
            related_items: list[str] = []
            relations = data.get("relations", {}) if isinstance(data, dict) else {}
            relation_values = relations.values() if isinstance(relations, dict) else relations if isinstance(relations, list) else []
            for relation in relation_values:
                values = relation if isinstance(relation, list) else [relation]
                for value in values:
                    text = str(value)
                    if "/items/" in text:
                        related_items.append(text.rsplit("/items/", 1)[-1].split("/", 1)[0])
            year = None
            raw_date = str(data.get("date", ""))
            import re
            match = re.search(r"(19|20)\d{2}", raw_date)
            if match:
                year = int(match.group(0))
            pid = f"zotero:{state['server_id']}:{row['library_id']}:{row['item_key']}"
            paper_row = {"id": pid, "key": row["item_key"], "libraryID": row["library_id"], "title": str(data.get("title") or ""), "year": year, "authors": creators, "doi": str(doi) if doi else None, "abstract": str(data.get("abstractNote")) if data.get("abstractNote") is not None else None, "keywords": [k for k in keywords if k], "relatedItems": related_items, "subjectCodes": msc_codes_from_extra(data.get("extra")), "fieldIds": [], "conceptIds": [], "displayFieldId": None, "attachmentIds": attachment_ids_by_parent.get(str(row["item_key"]), []), "textStatus": "indexed" if any(a["parentId"].endswith(":" + str(row["item_key"])) and a["status"] == "indexed" for a in attachments) else ("readable" if attachment_ids_by_parent.get(str(row["item_key"])) else "missing"), "sourcePayload": payload}
            if self.include_indexed_text:
                text_parts = [str(raw.get("indexed_text")) for raw in raw_atts if str(raw.get("attachment_key")) in active_attachment_keys and raw.get("parent_key") == row["item_key"] and raw.get("indexed_text")]
                if text_parts:
                    paper_row["indexedText"] = "\n".join(text_parts)[:200_000]
            papers.append(paper_row)
        # Standalone Zotero file attachments are real source records. Give
        # each one an explicit attachment-backed paper node so the v1 schema's
        # required parentId remains referentially closed without inventing a
        # parent item or exposing an unregistered PDF action.
        for attachment in attachments:
            if attachment.get("parentId"):
                continue
            standalone_id = f"zotero:{state['server_id']}:{attachment['key'].split(':')[-2] if ':' in attachment['key'] else self.library_id}:{attachment['key']}"
            attachment["parentId"] = standalone_id; attachment["standalone"] = True
            papers.append({"id": standalone_id, "key": attachment["key"], "libraryID": self.library_id, "title": attachment.get("name", attachment["key"]), "year": None, "authors": [], "doi": None, "abstract": None, "keywords": [], "relatedItems": [], "fieldIds": [], "conceptIds": [], "displayFieldId": None, "attachmentIds": [attachment["id"]], "textStatus": attachment.get("status", "missing"), "attachmentBacked": True})
        collections: list[dict[str, Any]] = []
        item_collection_keys: dict[str, set[str]] = {}
        for paper in papers:
            raw = paper.get("sourcePayload", {})
            data = raw.get("data", raw) if isinstance(raw, dict) else {}
            values = data.get("collections", []) if isinstance(data, dict) else []
            item_collection_keys[paper["key"]] = {str(value) for value in values if value}
        paper_id_by_key = {p["key"]: p["id"] for p in papers}
        collection_rows = self.store.current_collections()
        live_collection_keys = {(str(r["library_id"]), str(r["collection_key"])) for r in collection_rows}
        for row in collection_rows:
            payload = json.loads(row["payload"])
            data = payload.get("data", payload) if isinstance(payload, dict) else {}
            keys = data.get("relations", []) if isinstance(data, dict) else []
            item_keys = data.get("items", data.get("itemKeys", [])) if isinstance(data, dict) else []
            if isinstance(item_keys, dict):
                item_keys = list(item_keys)
            derived_items = [paper_id_by_key[key] for key, collection_keys in item_collection_keys.items() if str(row["collection_key"]) in collection_keys]
            payload_items = [f"zotero:{state['server_id']}:{row['library_id']}:{k}" for k in item_keys if str(k) in paper_id_by_key]
            parent_key = data.get("parentCollection") if isinstance(data, dict) else None
            parent_id = f"collection:{state['server_id']}:{row['library_id']}:{parent_key}" if isinstance(parent_key, str) and parent_key != str(row["collection_key"]) and (str(row["library_id"]), parent_key) in live_collection_keys else None
            collections.append({"id": f"collection:{state['server_id']}:{row['library_id']}:{row['collection_key']}", "parentId": parent_id, "label": str(data.get("name") or row["collection_key"]), "paperIds": sorted(set(derived_items or payload_items)), "key": row["collection_key"], "relations": keys})
        return papers, attachments, collections

    def _empty_snapshot(self, message: str | None = None) -> dict[str, Any]:
        state = self.store.state() or {"server_id": "unavailable", "epoch": "unavailable", "schema": 44, "alias": "Zotero"}
        return self._base_graph([], [], [], state, {"provider": self.embedding_provider, "state": "pending"}, message=message)

    def _base_graph(self, papers: list[dict[str, Any]], attachments: list[dict[str, Any]], collections: list[dict[str, Any]], state: dict[str, Any], model_status: dict[str, Any], message: str | None = None, previous: dict[str, Any] | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {"papers": papers, "fields": [], "concepts": [], "memberships": [], "edges": [], "overview": {"fields": [], "ribbons": [], "totalPapers": len(papers), "totalEdges": 0}, "modelStatus": model_status, "allocationMode": "primary-display-field"}
        if previous and previous.get("source", {}).get("serverId") == state.get("server_id") and previous.get("source", {}).get("epoch") == state.get("epoch"):
            # Keep the last valid derived graph visible while metadata and the
            # next enrichment generation are in flight.
            old_papers = {str(p.get("id")): p for p in previous.get("papers", []) if isinstance(p, dict)}
            for paper in result["papers"]:
                old = old_papers.get(str(paper.get("id")))
                if old:
                    for key in ("fieldIds", "conceptIds", "displayFieldId"):
                        if key in old:
                            paper[key] = copy.deepcopy(old[key])
            for key in ("fields", "concepts", "memberships", "edges", "overview", "allocationMode", "facetCatalog", "facetCatalogConfig", "inputState", "paperFacets", "conceptQualityLedger", "categoryColorRegistry", "fieldStats", "fences", "taxonomyRedirects", "taxonomyState"):
                # Same-source/epoch only (guarded above): redirect history and taxonomy state are
                # identity metadata of the derived graph; dropping them at the base publish would
                # strip old merged IDs before enrichment and hide the loss from migration validation.
                if key in previous:
                    result[key] = copy.deepcopy(previous[key])
            live_ids = {str(p.get("id")) for p in result["papers"]}
            field_ids = {str(f.get("id")) for f in result.get("fields", [])}
            concept_ids = {str(c.get("id")) for c in result.get("concepts", [])}
            allowed = live_ids | field_ids | concept_ids
            result["memberships"] = [m for m in result.get("memberships", []) if str(m.get("paperId")) in live_ids and str(m.get("fieldId")) in field_ids]
            result["edges"] = [e for e in result.get("edges", []) if str(e.get("source")) in allowed and str(e.get("target")) in allowed]
            # Per-paper v3 rows follow the CURRENT active papers: a source deletion must not leave
            # paperFacets pointing at a removed paper (the strict decoder rejects that), and the
            # aggregates are recomputed from what this pending snapshot actually contains.  New
            # papers have no facet row until enrichment publishes (modelStatus.state == "pending").
            if isinstance(result.get("paperFacets"), list):
                result["paperFacets"] = [row for row in result["paperFacets"] if isinstance(row, dict) and str(row.get("paperId")) in live_ids]
            if isinstance(result.get("fieldStats"), dict):
                result["fieldStats"] = field_stats(result["papers"], {str(f["id"]): f for f in result.get("fields", []) if isinstance(f, dict) and f.get("id")}, result["memberships"])
            result["overview"] = dict(result.get("overview", {}))
            result["overview"]["totalPapers"] = len(result["papers"])
            result["overview"]["totalEdges"] = len(result["edges"])
        return self._complete_snapshot(result, state, papers, attachments, collections, model_status, message=message)

    def _complete_snapshot(self, result: dict[str, Any], state: dict[str, Any], papers: list[dict[str, Any]], attachments: list[dict[str, Any]], collections: list[dict[str, Any]], model_status: dict[str, Any], message: str | None = None, previous: dict[str, Any] | None = None) -> dict[str, Any]:
        snapshot = copy.deepcopy(result or {})
        snapshot["schemaVersion"] = 1
        snapshot["source"] = self._source(state)
        snapshot["status"] = {"phase": "idle", "stale": False, "lastSuccess": now_iso(), "message": message, "observedLag": None, "watcherInterval": self.poll_interval}
        snapshot.setdefault("papers", papers)
        snapshot.setdefault("fields", [])
        snapshot.setdefault("concepts", [])
        snapshot.setdefault("memberships", [])
        snapshot.setdefault("edges", [])
        snapshot["attachments"] = attachments if not snapshot.get("attachments") else snapshot["attachments"]
        snapshot["collections"] = collections if not snapshot.get("collections") else snapshot["collections"]
        snapshot.setdefault("overview", {"fields": [], "ribbons": [], "totalPapers": len(snapshot["papers"]), "totalEdges": len(snapshot["edges"])})
        overview = snapshot["overview"]
        overview.setdefault("keywordAggregate", {"conceptIds": [], "pairs": [], "encoding": "concept-index-pairs", "totalPairs": 0, "totalWeight": 0})
        overview["keywordAggregate"].setdefault("conceptIds", [])
        overview.setdefault("fullEdgeCount", overview.get("totalEdges", len(snapshot["edges"]))); overview.setdefault("displayMaterializedEdgeCount", len(snapshot["edges"]))
        snapshot["modelStatus"] = dict(snapshot.get("modelStatus") or model_status)
        snapshot["modelStatus"].setdefault("recipeVersion", GRAPH_RECIPE_VERSION)
        snapshot.setdefault("allocationMode", "primary-display-field")
        self._ensure_v2_metadata(snapshot, state, previous)
        # The graph lane may intentionally omit source payloads from its output.
        for paper in snapshot["papers"]:
            paper.pop("sourcePayload", None)
            paper.pop("indexedText", None)
            paper.pop("fullText", None)
        return snapshot

    @staticmethod
    def _ensure_v2_metadata(snapshot: dict[str, Any], state: dict[str, Any], previous: dict[str, Any] | None = None) -> None:
        """Derive the persisted colour registry and (when missing) fieldStats.

        The registry is append-only: entries from the previous snapshot of the
        same source/epoch namespace are reused byte-for-byte; across epochs only
        non-custom (semantic) entries carry over.  Facet entities / catalog
        payloads are produced by the graph engine and are never taken from
        user-writable taxonomy state.
        """
        source = snapshot.get("source") if isinstance(snapshot.get("source"), dict) else {}
        namespace = f"atlas:{source.get('serverId', source.get('id', 'local'))}:{source.get('epoch', 'unknown')}"
        catalog = snapshot.get("facetCatalog") if isinstance(snapshot.get("facetCatalog"), dict) else {}
        entities = [x for x in catalog.get("entities", []) if isinstance(x, dict)]
        mapped = {x.get("legacyFieldId") for x in entities if x.get("legacyFieldId")}
        categories = list(entities) + [f for f in snapshot.get("fields", []) if isinstance(f, dict) and f.get("id") not in mapped]
        prior = snapshot.get("categoryColorRegistry") if isinstance(snapshot.get("categoryColorRegistry"), dict) else (previous or {}).get("categoryColorRegistry")
        if isinstance(prior, dict) and prior.get("namespace") != namespace:
            prior = {**prior, "entries": [e for e in prior.get("entries", []) if isinstance(e, dict) and e.get("origin") != "custom"]}
        snapshot["categoryColorRegistry"] = build_category_color_registry(categories, existing=prior, namespace=namespace)
        snapshot["categoryColorRegistry"]["namespace"] = namespace
        if not isinstance(snapshot.get("fieldStats"), dict):
            snapshot["fieldStats"] = field_stats(snapshot.get("papers", []), {str(f["id"]): f for f in snapshot.get("fields", []) if isinstance(f, dict) and f.get("id")}, snapshot.get("memberships", []))

    def snapshot(self, projection: str = "full", sample: int | None = None) -> dict[str, Any]:
        stored = self.store.get_snapshot()
        snapshot = copy.deepcopy(stored[2] if stored else self._empty_snapshot())
        self._ensure_snapshot_shape(snapshot)
        if projection == "overview":
            return {k: snapshot[k] for k in ("schemaVersion", "source", "status", "overview", "modelStatus", "allocationMode", "revision") if k in snapshot}
        if projection == "detail":
            papers = snapshot.get("papers", [])
            if sample is not None and sample >= 0:
                snapshot["papers"] = papers[: min(int(sample), len(papers))]
                ids = {p.get("id") for p in snapshot["papers"]}
                snapshot["memberships"] = [m for m in snapshot.get("memberships", []) if m.get("paperId") in ids]
                field_ids = {m.get("fieldId") for m in snapshot["memberships"]}
                field_map = {f.get("id"): f for f in self.store.get_snapshot()[2].get("fields", [])}
                pending_fields = list(field_ids)
                while pending_fields:
                    parent_ids = field_map.get(pending_fields.pop(), {}).get("parentIds", [])
                    for parent in parent_ids:
                        if parent not in field_ids: field_ids.add(parent); pending_fields.append(parent)
                concept_ids = {c for p in snapshot["papers"] for c in p.get("conceptIds", [])}
                allowed_edge_ids = ids | concept_ids
                snapshot["edges"] = [e for e in snapshot.get("edges", []) if e.get("source") in allowed_edge_ids and e.get("target") in allowed_edge_ids]
                snapshot["fields"] = [f for f in snapshot.get("fields", []) if f.get("id") in field_ids]
                snapshot["concepts"] = [c for c in snapshot.get("concepts", []) if c.get("id") in concept_ids]
                snapshot["attachments"] = [a for a in snapshot.get("attachments", []) if a.get("parentId") in ids]
                collections = []
                all_collections = snapshot.get("collections", [])
                collection_by_id = {c.get("id"): c for c in all_collections}
                kept_collection_ids = {c.get("id") for c in all_collections if any(paper_id in ids for paper_id in c.get("paperIds", []))}
                # Ancestors of kept collections stay (possibly with no visible papers) so parentId references remain closed.
                pending_collections = list(kept_collection_ids)
                while pending_collections:
                    parent_id = collection_by_id.get(pending_collections.pop(), {}).get("parentId")
                    if parent_id in collection_by_id and parent_id not in kept_collection_ids:
                        kept_collection_ids.add(parent_id); pending_collections.append(parent_id)
                for collection in all_collections:
                    if collection.get("id") not in kept_collection_ids:
                        continue
                    all_ids = collection.get("paperIds", [])
                    visible_ids = [paper_id for paper_id in all_ids if paper_id in ids]
                    collection["paperIds"] = visible_ids
                    collection["visiblePaperCount"] = len(visible_ids)
                    collection["hiddenPaperCount"] = len(all_ids) - len(visible_ids)
                    collections.append(collection)
                snapshot["collections"] = collections
                self._project_v3_extensions(snapshot, ids, {c.get("id") for c in snapshot["concepts"]}, field_ids)
                full_edges = snapshot["overview"].get("totalEdges", len(stored[2].get("edges", [])) if stored else 0)
                visible_edges = len(snapshot["edges"])
                snapshot["visibility"] = {"visiblePapers": len(snapshot["papers"]), "hiddenPapers": len(papers) - len(snapshot["papers"]), "visibleEdges": visible_edges, "hiddenEdges": max(0, full_edges - visible_edges), "fullEdgeCount": full_edges, "displayMaterializedEdgeCount": visible_edges}
            return snapshot
        return snapshot

    @staticmethod
    def _project_v3_extensions(snapshot: dict[str, Any], paper_ids: set[Any], concept_ids: set[Any], field_ids: set[Any]) -> None:
        """Project the optional v3 per-entity extensions onto a sampled detail view.

        Per-entity rows (paperFacets, ledger buckets, fieldStats rows) follow the
        projected papers/concepts/fields so the strict decoder's references stay
        closed; catalog-level data (facetCatalog, color registry, redirects) is
        kept whole.  Aggregate numbers (ledger ``counts``, fieldStats
        ``paperCount``/``unknown``/``knownPrimary``) always describe the FULL graph
        and are marked ``projected: true`` so clients never mistake them for the
        sample.  Suppressed concepts stay resolvable (they are not graph nodes).
        """
        if isinstance(snapshot.get("paperFacets"), list):
            snapshot["paperFacets"] = [row for row in snapshot["paperFacets"] if row.get("paperId") in paper_ids]
        ledger = snapshot.get("conceptQualityLedger")
        if isinstance(ledger, dict):
            suppressed = {str(x) for x in ledger.get("suppressed", [])}
            keep = {str(x) for x in concept_ids} | suppressed
            idof = lambda item: str(item.get("id") or item.get("conceptId")) if isinstance(item, dict) else str(item)
            projected = dict(ledger)
            for bucket in ("active", "protected", "user", "suppressed"):
                if bucket in projected:
                    projected[bucket] = [item for item in projected[bucket] if idof(item) in keep]
            if "entries" in projected:
                projected["entries"] = [entry for entry in projected["entries"] if idof(entry) in keep]
            if "raw" in projected:
                owned = {idof(x) for bucket in ("active", "protected", "user", "suppressed") for x in projected.get(bucket, [])}
                projected["raw"] = [item for item in projected["raw"] if idof(item) in owned]
            projected["projected"] = True
            snapshot["conceptQualityLedger"] = projected
        stats = snapshot.get("fieldStats")
        if isinstance(stats, dict) and isinstance(stats.get("rows"), list):
            snapshot["fieldStats"] = {**stats, "rows": [row for row in stats["rows"] if row.get("fieldId") in field_ids], "projected": True}

    @staticmethod
    def _ensure_snapshot_shape(snapshot: dict[str, Any]) -> None:
        overview = snapshot.setdefault("overview", {})
        overview.setdefault("keywordAggregate", {"conceptIds": [], "pairs": [], "encoding": "concept-index-pairs", "totalPairs": 0, "totalWeight": 0})
        overview["keywordAggregate"].setdefault("conceptIds", [])
        overview.setdefault("fullEdgeCount", overview.get("totalEdges", len(snapshot.get("edges", []))))
        overview.setdefault("displayMaterializedEdgeCount", len(snapshot.get("edges", [])))

