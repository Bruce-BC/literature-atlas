<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-11-19 | Updated: 2026-11-19 -->

# tests/frontend

## Purpose
Node built-in test runner suites for frontend modules: snapshot decoder, sampling, paper density, view-state reducer, all-paper overview, trace, network styling and G6 lifecycle.

## Key Files
| File | Description |
|------|-------------|
| `frontend.test.mjs` | Decoder, sampling, poll guard, overlay patches, network categories (26 tests). |
| `g6-lifecycle.test.mjs` | `renderG6Network` disposal, viewport restore, error handling (12 tests). |
| `paper-density.test.mjs` | `computePaperDensity` for semantic/citation edges (6 tests). |
| `taxonomy-decoder.test.mjs` | v3 decoder validation: legacy compat, cycles, ledger (7 tests). |
| `taxonomy-interaction.test.mjs` | View-state reducer, all-paper overview, child paging/budgets (23 tests). |
| `taxonomy-trace.test.mjs` | Whitelisted bounded trace (5 tests; TAXV2-036). |
| `taxonomy-visual.test.mjs` | Category color registry, OKLCH, field counts (12 tests). |

## For AI Agents

### Working In This Directory
- Files must be named `*.test.mjs`; `npm test` and `scripts/check.mjs` glob them.
- Import from `../../frontend/` only; no network, no real data.
- Reducer changes need matching updates to the TAXV2-017..032/041 mapping.

### Testing Requirements
```sh
npm test
node --test tests/frontend/*.test.mjs
node --test tests/frontend/taxonomy-interaction.test.mjs
node --test tests/frontend/taxonomy-*.test.mjs tests/frontend/g6-lifecycle.test.mjs
```

### Common Patterns
- `import test from 'node:test'` and `assert from 'node:assert/strict'`; fixtures read via `readFile` from `tests/fixtures/`.

## Dependencies

### Internal
- `frontend/decoder.js`, `sampling.js`, `paper-density.js`, `view-state.js`, `all-paper-overview.js`, `overview-expansion.js`, `trace.js`, `network-style.js`, `g6-render.js`; `tests/fixtures/`.

### External
- Node built-in test runner (`node:test`).

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
