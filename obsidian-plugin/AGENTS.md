<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-11-19 | Updated: 2026-11-19 -->

# obsidian-plugin

## Purpose
Desktop-only Obsidian companion (`isDesktopOnly: true`). It spawns the Python loopback bridge (`atlas/`) as an owned child process, embeds the web frontend in a sandboxed iframe, and mediates the few actions that leave the frame (open in Zotero, open PDF, export a managed note). This directory is source; the release plugin is built into `dist/` by `scripts/build.mjs`.

## Key Files
| File | Description |
|------|-------------|
| `main.cjs` | Plugin entry: `LiteratureAtlasPlugin`, `AtlasView` (iframe + postMessage bootstrap), settings tab (`pythonPath`, `pollInterval`), `atlas:ready`/`atlas:open` message handling, `zotero://` open, managed export dispatch |
| `bridge-manager.cjs` | `BridgeManager`: Python 3.11+ probe, spawn of `python -m atlas serve`, bootstrap line parsing, `/health` watcher, restart, SIGTERM then forced SIGKILL, registered paper/attachment lookups |
| `security.cjs` | Pure guards: `parseBootstrap` (exact keys), `makeLaunchId`, `makeLoopbackUrl` (rejects credentials in URL), `isLoopbackOrigin`, `sanitizeDiagnostic`, `isAllowedZoteroUrl`, `safeManagedPath`, `OBSIDIAN_ORIGIN` |
| `runtime-resources.cjs` | Validates and extracts the embedded `ATLAS_BUNDLED_RESOURCES` into `<plugin>/.runtime/<digest>`; symlink/path/hash/size checks; `resolvePluginDir`, `resolveRuntime` |
| `managed-export.cjs` | Writes one stable note per paper under `Literature Atlas/Managed/`, touching only the region between the managed start/end markers |
| `manifest.json` | Plugin manifest; must stay byte-for-byte equal (as parsed JSON) to root `manifest.json` or `scripts/build.mjs` fails |
| `styles.css` | Two rules: status text and full-size iframe |

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `bridge/` | Holds only `bridge/atlas/__init__.py`, a stub package marker. `scripts/build.mjs` copies the repo `atlas/*.py` into `dist/obsidian-plugin/bridge/atlas/`; the child runs with `bridge/` as its cwd. Do not add real code or a separate AGENTS.md here |

## For AI Agents

### Working In This Directory
- Handshake: the bridge prints one JSON bootstrap line on stdout with exactly `launchId`, `pid`, `port`, `token` (`parseBootstrap`, line max 4096 bytes). Extra or missing keys are rejected.
- Credentials are memory-only. The iframe URL (`http://127.0.0.1:<port>/index.html`) carries no query or hash. The token reaches the frame only via `postMessage({type:'atlas:bootstrap', ...})` with `targetOrigin` set to the exact loopback origin. Never persist the token (`saveData` stores only `pythonPath` and `pollInterval`) and never log it without `sanitizeDiagnostic`.
- Incoming messages are accepted only when `event.source` is a registered view's `contentWindow`, `event.origin` matches the exact `http://127.0.0.1:<port>` (`isLoopbackOrigin`), and `launchId` matches the current bootstrap. `atlas:open` kinds are limited to `item`, `pdf`, `export`; ids are length-capped.
- Bridge requests use `Authorization: Bearer <token>` plus `Origin: app://obsidian.md`; `BridgeManager` rejects any other `parentOrigin`. Child is spawned with `shell: false`, `--port 0`, `--watch-parent-stdin`, `--parent-origin`, and a per-launch id. Data dir is `<plugin>/.local` (gitignored, excluded from packages).
- Process management: `stop()` sends SIGTERM and schedules SIGKILL; `onunload` and settings changes stop the owned child. A stale launch id invalidates the frame. Keep fetch/spawn/timer injectable (constructor options) so tests stay hermetic.
- External opens go through `isAllowedZoteroUrl`: only `zotero://select|open-pdf/...` with strict path regexes, or loopback http(s). PDFs must be a registered attachment of the registered paper.
- Runtime resources: community `main.js` embeds bridge `.py` and `web/` files; `runtime-resources.cjs` re-verifies every SHA-256, the digest, allowed path regex (`bridge/atlas/*.py`, `web/...`), and limits (512 files, 16 MiB per file, 48 MiB total) before extraction with mode 0o600/0o700 and no symlinks. Loosening any of these needs a test change.
- Managed export: paper metadata is escaped as text; a pre-existing user file without owner markers is never overwritten (`reason: 'unowned'`). Paths are confined by `safeManagedPath`.
- `.cjs` files must stay `require`-based (esbuild bundles `main.cjs` to cjs; `obsidian`, `electron`, node builtins are external). No new runtime dependencies.
- Changing a message type, bootstrap key, or origin rule means updating `frontend/` and `atlas/server.py` counterparts and `docs/implementation-interfaces.md` together.

### Testing Requirements
```sh
node --test tests/plugin/plugin.test.mjs      # unit: security, bridge manager, export, resources, view
npm test                                       # frontend + plugin node tests
npm run build && npm run package && npm run package:check
npm run test:community                         # tests/plugin/community-smoke.cjs: installs the 3 release assets in a temp vault, real Python bridge
python3 -m pytest -q tests/root/test_community_package.py
npm run audit:privacy
```
Run `npm run lint` after edits (it `node --check`s every `.cjs`). The community smoke defaults to `.artifacts/community-plugin`, so run `npm run package` first.

### Common Patterns
- Validate-then-freeze pure helpers in `security.cjs`; side effects live in `main.cjs` / `bridge-manager.cjs`.
- Errors are thrown as short generic `Error` messages; UI shows a generic status string, never raw child output.
- Status flows `BridgeManager.onStatus` -> `AtlasView.setStatus`; `getStatus()` is the stable shape tests read.

## Dependencies

### Internal
- `atlas/` Python package (CLI `serve` flags, `/health`, `/api/v1/...`) copied into `bridge/atlas/` at build time.
- `frontend/` (built to `dist/.../web/`), `assets/` (embedded by build), root `manifest.json`, `scripts/build.mjs`, `scripts/package.mjs`.
- `tests/plugin/plugin.test.mjs`, `tests/plugin/community-smoke.cjs`, `tests/root/test_community_package.py`.

### External
- Obsidian API (`obsidian`) and `electron` (`shell.openExternal`), provided by the host and left external in the bundle.
- Node builtins only (`node:fs`, `node:path`, `node:crypto`, `node:child_process`); Python 3.11+ at runtime.

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
