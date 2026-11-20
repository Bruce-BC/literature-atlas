<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-11-19 | Updated: 2026-11-19 -->

# tests/plugin

## Purpose
Tests for the Obsidian plugin (`obsidian-plugin/`): vault/symlink safety, bundled runtime extraction, child process and bootstrap handshake, and the community-package smoke check.

## Key Files
| File | Description |
|------|-------------|
| `plugin.test.mjs` | node:test plugin runtime tests (12 tests); run by `npm test`. |
| `community-smoke.cjs` | Standalone script (not node:test); run by `npm run test:community` against the package. |

## For AI Agents

### Working In This Directory
- Exact-origin, memory-only token handshake and PID/launch-nonce checks must stay covered; tokens never go in URLs or notes.
- `community-smoke.cjs` is NOT part of `npm test`; run it after `npm run package`.
- Synthetic tokens only.

### Testing Requirements
```sh
node --test tests/plugin/*.test.mjs
npm run build && npm run package && npm run test:community
```

### Common Patterns
- Stubbed Obsidian host objects and `EventEmitter` child processes; temp dirs for vault trees.

## Dependencies

### Internal
- `obsidian-plugin/`, `scripts/package.mjs` output, `tests/fixtures/plugin_takeover_and_token_leak.json`.

### External
- Node built-ins (`node:test`, `node:child_process`, `node:crypto`).

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
