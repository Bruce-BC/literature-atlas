<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-11-19 | Updated: 2026-11-19 -->

# tests

## Purpose
All automated tests for the Python bridge/graph engine, the frontend modules, the Obsidian plugin, release scripts, and browser E2E. Data is synthetic only. Criteria-to-test mapping lives in `docs/taxonomy-v2-acceptance.md`.

## Key Files
| File | Description |
|------|-------------|
| `__init__.py` | Marks `tests` as a package so Python tests import as `tests.*`. |

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| [`backend/`](backend/AGENTS.md) | pytest: HTTP bridge, storage/persistence, schema, trace, watcher |
| [`e2e/`](e2e/AGENTS.md) | pytest + Playwright: browser acceptance against the built app |
| [`fixtures/`](fixtures/AGENTS.md) | Synthetic JSON snapshots and gold/contract fixtures |
| [`frontend/`](frontend/AGENTS.md) | `node --test`: decoder, reducer, trace, styling, G6 lifecycle |
| [`graph/`](graph/AGENTS.md) | unittest/pytest: GraphEngine, taxonomy v2, optional real E5 |
| [`plugin/`](plugin/AGENTS.md) | Node: Obsidian plugin runtime tests and community smoke |
| [`root/`](root/AGENTS.md) | pytest: release/audit scripts and community package |

## For AI Agents

### Working In This Directory
- Public fixtures are synthetic; never add real library data, PDFs, vault paths, tokens or model caches.
- Fix production code for failing tests; do not weaken tests or replace real inference/host checks with mocks.
- Keep the 43-criteria mapping in `docs/taxonomy-v2-acceptance.md` in sync when tests move.
- There is no pytest config in `pyproject.toml`; run `python3 -m pytest` from the repo root (tests import `atlas` and `scripts`).

### Testing Requirements
```sh
npm test                                  # frontend + plugin (node --test)
python3 -m pytest -q tests                # all Python (backend, graph, root, e2e)
python3 -m pytest -q tests/backend tests/graph tests/root
npm run build && npm run test:e2e         # browser E2E driver (scripts/test_browser.py)
npm run verify                            # full gate, once at the end
```

### Common Patterns
- Python: pytest functions plus `unittest.TestCase` in `graph/`; real SQLite and real GraphEngine, fake Zotero transport.
- JS: `node:test` + `node:assert/strict`, importing from `../../frontend/`.
- Opt-in real model smoke: `ATLAS_REAL_E5=1` (skipped otherwise).

## Dependencies

### Internal
- `atlas/`, `frontend/`, `obsidian-plugin/`, `scripts/`, `docs/snapshot.schema.json`, `assets/`.

### External
- pytest 8.4.2, jsonschema, Playwright 1.62.0 (chromium); Node built-in test runner.

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
