<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-11-19 | Updated: 2026-11-19 -->

# docs

## Purpose
Public contracts and records for Literature Atlas: architecture, bridge/plugin/frontend interfaces, the snapshot JSON Schema, taxonomy v2 acceptance criteria, verification results, and third-party notices. Everything here must be self-contained and free of private data, local paths and tokens.

## Key Files
| File | Description |
|------|-------------|
| `architecture.md` | Short architecture overview: loopback bridge, SQLite partitions, D3/Cytoscape/G6 views, fencing, privacy. |
| `implementation-interfaces.md` | Frozen cross-lane contract: Python graph contract, HTTP API, plugin boundary, frontend boundary, additive taxonomy v2 blocks, palette assets. |
| `snapshot.schema.json` | JSON Schema (Draft 2020-12) for snapshots; validated by `scripts/check.mjs` and backend schema tests. |
| `taxonomy-v2-acceptance.md` | The 43 TAXV2 acceptance criteria mapped to test files/functions. |
| `verification.md` | Verification contract (`npm run verify` stages), last recorded results, limits. |
| `claude-handoff.md` | Korean handoff for the next agent: goals, state, modules, invariants, commands, known limits. |
| `THIRD_PARTY_NOTICES.md` | Notices for bundled packages; copied into the plugin release ZIP by `scripts/package.mjs`. |
| `palette-source-verification.json` | Palette source verification record; copied into the plugin release ZIP by `scripts/package.mjs`. |

## For AI Agents

### Working In This Directory
- Schema or interface changes must update `snapshot.schema.json`, `implementation-interfaces.md`, the decoder (`frontend/decoder.js`) and tests together.
- If a test is added, renamed or removed, update the mapping in `taxonomy-v2-acceptance.md`; update numbers in `verification.md` only from fresh `npm run verify` output.
- Report failures and limits as they are; never claim mock results as real-model or host verification.
- `THIRD_PARTY_NOTICES.md` and `palette-source-verification.json` are release-packaged (`scripts/package_check.py` requires them in the ZIP); keep their file names stable.
- No personal absolute paths, tokens, library data or `.omx/` references. `claude-handoff.md` is Korean; keep its language.

### Testing Requirements
```sh
npm run lint            # parses snapshot.schema.json and validates fixtures against it
npm run audit:privacy   # sufficient for doc-only changes
python3 -m pytest -q tests/backend/test_taxonomy_v2_schema.py   # after schema changes
npm run package && npm run package:check                         # after touching notices/verification JSON
```

### Common Patterns
- Criteria IDs are `TAXV2-001`..`TAXV2-043`; evidence is cited as `file::test`.
- Relative Markdown links between docs; commands in fenced `sh` blocks.

## Dependencies

### Internal
- `frontend/decoder.js`, `atlas/` (graph engine, storage, server), `scripts/check.mjs`, `scripts/package.mjs`, `scripts/package_check.py`, `tests/`.

### External
- None at runtime (documentation only).

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
