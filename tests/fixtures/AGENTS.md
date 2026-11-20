<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-11-19 | Updated: 2026-11-19 -->

# tests/fixtures

## Purpose
Synthetic JSON fixtures for snapshots, taxonomy gold labels and contract scenarios. Public fixtures must never contain real library data, PDFs, paths or tokens.

## Key Files
| File | Description |
|------|-------------|
| `snapshot.json` | Small synthetic snapshot; required and parsed by `scripts/check.mjs`, schema-validated. |
| `demo-snapshot.json` | Large synthetic demo (48 papers, 4 roots, 8 subfields); required by `scripts/check.mjs`, used by `atlas serve --demo`. |
| `taxonomy_gold.json` | Archived v1 gold cases; left byte-identical (hash-pinned by v2). |
| `taxonomy_gold_v2.json` | v2 gold cases (ontology-label-policy-v2) based on the v1 file. |
| `taxonomy_gold_v2_policy.json` | Static record of the four reviewed label corrections. |
| `taxonomy_gold_regression.json` | Regression cases (e.g. OT/KL ambiguity). |
| `embedding_provider_reproducibility.json` | Fake-provider embedding reproducibility fixture. |
| `overlay_two_writer_conflict.json` | Two-writer overlay revision conflict scenario. |
| `overview_allocation_multimembership.json` | Overview allocation under multi-membership. |
| `plugin_takeover_and_token_leak.json` | Plugin bootstrap/takeover cases with a synthetic token. |
| `privacy_gate_first_push.json` | Privacy gate forbidden-pattern scenario. |
| `reconcile_trace_source_swap.json` | Reconcile trace across a source swap. |

## For AI Agents

### Working In This Directory
- Synthetic only; tokens are obvious placeholders. Do not edit `taxonomy_gold.json` (hash-pinned, tests fail on change).
- Changing gold fixtures requires updating gates in `tests/graph/test_taxonomy_v2.py` and the acceptance doc.
- Snapshot fixtures must validate against `docs/snapshot.schema.json`.
- Do not add fixtures derived from a real Zotero library.

### Testing Requirements
```sh
npm run lint            # parses and schema-validates snapshot.json / demo-snapshot.json
npm test && python3 -m pytest -q tests/backend tests/graph
npm run audit:privacy
```

### Common Patterns
- Contract fixtures carry a `fixture` name key; gold files carry `expected` labels per case.

## Dependencies

### Internal
- Consumed by `tests/backend`, `tests/graph`, `tests/frontend`, `tests/root`, `tests/e2e`, `scripts/check.mjs`, `scripts/audit_repository.py`.

### External
- None.

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
