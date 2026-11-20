<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-11-19 | Updated: 2026-11-19 -->

# atlas

## Purpose
Python 3.11+ local bridge package (`literature-atlas` console script, `python3 -m atlas`). It reads a Zotero library through the Zotero local API (read-only), builds a source-scoped graph/taxonomy snapshot, keeps derived state in a local SQLite store, and serves it to the Obsidian plugin over an authenticated loopback HTTP API. `scripts/build.mjs` copies only `atlas/*.py` into the plugin bridge; this .md file does not ship.

## Key Files
| File | Description |
|------|-------------|
| `__init__.py` | Package marker ("Literature Atlas local bridge"). |
| `__main__.py` | Dispatches `python3 -m atlas` to `cli.main`. |
| `cli.py` | argparse entry point; `serve` wires `ZoteroClient`, `SQLiteStore`, `SyncManager` and `AtlasHTTPServer`; loads the demo snapshot. |
| `server.py` | `AtlasHTTPServer` / `AtlasRequestHandler`: loopback-only authenticated HTTP API, Host/Origin/Bearer checks, CSP, static file serving, taxonomy redirect and facet endpoints. |
| `storage.py` | `SQLiteStore`: transactional app-owned state (snapshots, overlays, settings, receipts) plus `file_sha256`. |
| `sync.py` | `SyncManager`: source reconciliation, watcher, background jobs, generation-fenced snapshot publication. |
| `zotero.py` | `ZoteroClient` (Zotero local API, read-only), `normalize_*` item/collection/attachment helpers, `verify_exact_payloads`, `ZoteroError` family. |
| `graph.py` | Builds the source-scoped graph snapshot: entity canonicalization, taxonomy redirects, aggregates, citation/semantic/keyword edges, optional cached OpenAlex reference fetch. |
| `taxonomy.py` | Deterministic multifacet taxonomy: facet catalog, evidence classification, concept quality, cycle checks, source/epoch-scoped entity IDs. |
| `facet_validation.py` | Validation and merge helpers for the taxonomy v2 persistence contract (inputs, assignments, primary/quality overrides). |
| `embeddings.py` | `EmbeddingProvider` for local multilingual-e5-small embeddings; lazy `sentence_transformers` import; hash helpers. |
| `migration.py` | Receipts, validation and rollback for graph recipe migrations (app-owned state only). |
| `trace.py` | `TraceLog`/`sanitize`: bounded, whitelist-only JSONL pipeline trace; `summarize_snapshot`. |
| `category_colors.py` | Deterministic append-only category colour registry (OKLCH to hex). |

## For AI Agents

### Working In This Directory
- `zotero.py`: Zotero is read-only. Do not add any write/POST/PUT/DELETE call, attachment mutation, or SQLite access to Zotero. Incomplete or changed-source responses raise `IncompleteResponse`/`SourceChanged`; never publish from a partial snapshot.
- `sync.py`, `storage.py`, `migration.py`: source ID, epoch and item generation fence every job and publication. Never delete from a failed/partial snapshot and never remap overlays across different source DBs. Migration validation and rollback touch app-owned state only.
- `graph.py`, `taxonomy.py`: full aggregates stay independent of detail subsampling; citation, semantic and keyword co-occurrence stay distinct edge types. Facet references use canonical source/epoch-scoped `entity.id`.
- `taxonomy.py`, `graph.py`, `trace.py`: evidence carries field/tier/offset metadata and hashes, never raw span text. Do not copy span text into snapshots, trace records or logs.
- `trace.py`: the sanitizer is a fail-closed whitelist (enums, bounded ints, truncated hashes). Do not add free-form string or ID fields.
- `server.py`: keep loopback-only binding, Host/Origin checks (exact plugin origin, memory-only token), Bearer auth, and no arbitrary file/URL read. Metadata is returned as JSON text, never HTML.
- `graph.py` OpenAlex fetch is the only outbound network path in the package; it is off by default (`"openAlex": False` in `server.py` `DEFAULT_SETTINGS`); keep it opt-in, cached, and free of library content beyond identifiers.
- `embeddings.py`: keep the `sentence_transformers` import lazy; report unavailability instead of mocking inference.
- Schema/interface changes also require `docs/snapshot.schema.json`, `docs/implementation-interfaces.md`, the frontend decoder and tests to be updated together. No new dependencies.
- Only `atlas/*.py` ships in the plugin; keep non-code files out of runtime paths.

### Testing Requirements
- Run targeted tests first, then `npm run lint` (Python compile + schema) and `python3 -m pytest -q tests`; full gate is `npm run verify`.
- `cli.py`, `server.py`, `zotero.py`, `sync.py`, `storage.py`: `python3 -m pytest -q tests/backend/test_bridge.py tests/backend/test_watcher_acceptance.py tests/backend/test_settings_persistence.py tests/backend/test_status_consistency.py`.
- `graph.py`, `embeddings.py`, `taxonomy.py`: `python3 -m pytest -q tests/graph/test_graph_engine.py tests/graph/test_taxonomy_v2.py tests/backend/test_taxonomy_resolver.py tests/backend/test_projection_contracts.py tests/backend/test_duplicate_contracts.py`.
- `facet_validation.py`, `storage.py`, `migration.py` (v2 persistence/schema): `tests/backend/test_taxonomy_v2_persistence.py`, `tests/backend/test_taxonomy_v2_schema.py`.
- `trace.py`: `python3 -m pytest -q tests/backend/test_taxonomy_v2_trace.py`.
- `category_colors.py`: `python3 -m pytest -q tests/backend/test_category_colors.py`.
- Real embeddings (explicit opt-in): `ATLAS_REAL_E5=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python3 -m pytest -q -rA tests/graph/test_real_e5_smoke.py`.
- End-to-end: `npm run build && npm run test:e2e` (`tests/e2e/test_taxonomy_v2_acceptance.py`, `test_taxonomy_v2_pipeline.py`, browser tests). Fixtures are synthetic under `tests/fixtures/`.

### Common Patterns
- Every module starts with `from __future__ import annotations` and uses `Any`/`Mapping` type hints; `cli.py` returns int exit codes.
- Private helpers use a leading underscore (`_norm`, `_slug`, `_hash`); public contract helpers are module-level functions.
- Input validation raises `ValueError`-style errors with named fields (`facet_validation._string`/`_list`); `server.py` has `RedirectLimitError`.
- Persistence goes through `SQLiteStore` (threading lock, transactions); other modules do not open SQLite directly.
- Background work uses `ThreadPoolExecutor`/`Future` in `sync.py`; the HTTP server is `ThreadingHTTPServer`.
- Deterministic output: stable hashes (`hashlib.sha256`), sorted/canonical JSON, versioned rules (`RULE_VERSION`, `ONTOLOGY_POLICY_VERSION`, `RECIPE_VERSION`).
- Derived state is rebuildable; user overlays and receipts are the durable, app-owned data.

## Dependencies

### Internal
- `cli.py` -> `server`, `storage`, `sync`, `zotero`.
- `server.py` -> `graph`, `storage`, `sync`, `facet_validation`.
- `sync.py` -> `storage`, `migration`, `taxonomy`, `trace`, `category_colors`, `zotero`.
- `graph.py` -> `embeddings`, `taxonomy`; `taxonomy.py` -> `embeddings`.
- `migration.py` -> `storage`, `taxonomy`; `zotero.py` -> `storage` (`file_sha256`).
- Frontend contract: `docs/snapshot.schema.json`, `docs/implementation-interfaces.md`.

### External
- Python standard library only for the core bridge (`pyproject.toml` `dependencies = []`; uses `sqlite3`, `http.server`, `urllib`, `hashlib`, `threading`).
- Optional `ai` extra: `sentence-transformers` (lazy import in `embeddings.py`).
- Optional `pdf` extra (`pypdf`) is declared in `pyproject.toml` but not imported in `atlas/`.
- Test extra: `pytest`, `jsonschema`, `playwright`.

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
