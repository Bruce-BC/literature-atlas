<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-11-19 | Updated: 2026-11-19 -->

# tests/graph

## Purpose
Tests for `atlas.graph` GraphEngine and the taxonomy v2 classifier/concept-quality contracts, plus an opt-in real E5 embedding smoke.

## Key Files
| File | Description |
|------|-------------|
| `__init__.py` | Package marker. |
| `test_graph_engine.py` | GraphEngine behavior (unittest, 32 tests). |
| `test_taxonomy_v2.py` | Gold fixture, facet catalog, classifier, concept quality, field stats (56 tests; TAXV2-001..012, 016, 033, 040). |
| `test_real_e5_smoke.py` | Real E5 model smoke; skipped unless `ATLAS_REAL_E5=1` (2 tests). |

## For AI Agents

### Working In This Directory
- Gold gates (precision/F1/coverage/abstention, zero Analysis false positives) are synthetic conformance only; do not loosen them to pass.
- The fake embedding provider is for deterministic CI; never report it as real-model verification.
- Never copy raw span text into snapshots or traces; hashes and offsets only.

### Testing Requirements
```sh
python3 -m pytest -q tests/graph
python3 -m pytest -q tests/graph/test_taxonomy_v2.py
ATLAS_REAL_E5=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python3 -m pytest -q -rA tests/graph/test_real_e5_smoke.py   # explicit opt-in, needs local model cache
```

### Common Patterns
- `unittest.TestCase` classes run under pytest; temp dirs; fixtures from `tests/fixtures/taxonomy_gold*.json`.

## Dependencies

### Internal
- `atlas.graph`, `atlas.embeddings`, `tests/fixtures/`.

### External
- pytest; pinned E5 model in a local cache for the opt-in smoke only.

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
