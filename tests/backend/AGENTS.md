<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-11-19 | Updated: 2026-11-19 -->

# tests/backend

## Purpose
pytest suites for the loopback bridge (`atlas.server`), SQLite storage/sync, snapshot schema contracts, category colors, taxonomy v2 persistence and trace, and watcher behavior. Zotero is replayed through in-memory/fake transports; the source stays read-only.

## Key Files
| File | Description |
|------|-------------|
| `__init__.py` | Package marker. |
| `test_bridge.py` | Bridge process/API behavior, auth and bootstrap (18 tests). |
| `test_category_colors.py` | `atlas.category_colors` append-only registry, OKLCH to hex (TAXV2-013). |
| `test_duplicate_contracts.py` | Duplicate-item handling through the HTTP API. |
| `test_projection_contracts.py` | Projection output validated against the JSON Schema. |
| `test_settings_persistence.py` | Settings persistence across server restarts. |
| `test_status_consistency.py` | Status/phase consistency with `SyncManager` and graph recipe version. |
| `test_taxonomy_resolver.py` | Taxonomy resolver over the API. |
| `test_taxonomy_v2_persistence.py` | v2 persistence, CAS fencing, migration, rollback, audit (30 tests; TAXV2-008/038/039). |
| `test_taxonomy_v2_schema.py` | Schema + Python/JS decoder roundtrip for real v3 snapshots (TAXV2-006/040). |
| `test_taxonomy_v2_trace.py` | Privacy-safe trace sanitizer (TAXV2-036). |
| `test_watcher_acceptance.py` | Automatic watcher with in-memory Zotero API replay. |

## For AI Agents

### Working In This Directory
- Never write to Zotero; use fake transports. Do not replace real SQLite/GraphEngine with mocks.
- Source ID / epoch / generation fencing must stay covered when touching storage or sync.
- Schema/interface changes also require `docs/snapshot.schema.json`, `docs/implementation-interfaces.md`, the decoder and these tests.

### Testing Requirements
```sh
python3 -m pytest -q tests/backend
python3 -m pytest -q tests/backend/test_taxonomy_v2_persistence.py
python3 -m pytest -q tests/backend/test_bridge.py::<test_name>
```

### Common Patterns
- Temp dirs for SQLite, servers started on loopback in threads, `urllib` clients with auth headers.
- Fixtures loaded from `tests/fixtures/` or generated synthetically in-test.

## Dependencies

### Internal
- `atlas.server`, `atlas.storage`, `atlas.sync`, `atlas.zotero`, `atlas.category_colors`, `docs/snapshot.schema.json`, `tests/fixtures/`, `frontend/decoder.js` (via node subprocess).

### External
- pytest, jsonschema; Node for decoder roundtrip.

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
