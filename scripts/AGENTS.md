<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-11-19 | Updated: 2026-11-19 -->

# scripts

## Purpose
Build, packaging, verification and audit tooling invoked by the `package.json` scripts and CI. Nothing here is shipped inside the plugin; it produces and checks `dist/` and `.artifacts/`.

## Key Files
| File | Description |
|------|-------------|
| `check.mjs` | `npm run lint`: required-file check, `node --check` on all JS, `py_compile` on Python (forces `arch -arm64 python3` on macOS), JSON-schema validation of fixtures, sensitive-pattern scan of the interface doc |
| `build.mjs` | `npm run build`: esbuild frontend to `dist/web/app.js` and plugin to `dist/obsidian-plugin/main.js`; copies `atlas/*.py`, `assets/`, manifests; builds the embedded `ATLAS_BUNDLED_RESOURCES` contract and license notice banner; writes `dist/community-plugin/` |
| `package.mjs` | `npm run package`: deterministic ZIP and `.artifacts/manifest.json`, `.artifacts/community-plugin/` with `SHA256SUMS` |
| `package_check.py` | `npm run package:check`: community asset set, embedded-resource digest, license notices, checksum match, ZIP determinism (re-runs `package.mjs`), required/forbidden members |
| `palette_validate.py` | `npm run test:palette`: offline OKLCH to sRGB and WCAG contrast checks on `assets/palettes.json` |
| `palette_sources_check.py` | Palette source metadata check; `--online` additionally fetches each raw URL (not part of `verify`) |
| `test_browser.py` | `npm run test:e2e`: authenticated Playwright replay against the real built bridge and demo fixture |
| `benchmark_taxonomy_v2.py` | Cold/warm taxonomy v2 benchmark on synthetic 1,000 papers; run after build; writes receipts under `.omx/` (untracked); the cloud gate is layout + shape + colour <= 150 ms |
| `cloud_screenshot_matrix.py` | Cloud-aesthetics screenshot matrix (AE-12): light/dark x 320/720/1440 default screen + one zoomed-in sub-group shot on the synthetic 1,000-paper fixture, with AE-5 and label-overlap checks; `python3 scripts/cloud_screenshot_matrix.py <out-dir>` after build, out-dir outside the repo |
| `audit_repository.py` | `npm run audit:privacy`: fail-closed scan of tracked files, generated bundles, archives, PNG text chunks and git history for private paths, tokens and Zotero DB names |
| `install_obsidian.py` | Manual helper: copies `dist/community-plugin` three files into one explicit vault's `.obsidian/plugins/literature-atlas` after a timestamped backup |

## For AI Agents

### Working In This Directory
- Pipeline order (`npm run verify`): lint, test, test:palette, build, package, package:check, test:community, test:python, test:e2e, audit:privacy. Later steps depend on `dist/` and `.artifacts/` from earlier ones.
- Community release is exactly `main.js`, `manifest.json`, `styles.css` (+ `SHA256SUMS`). Bridge `.py` and `web/` are embedded in `main.js`, so `assets/` is bundled into it: never add docs, notes, or large/private files to `assets/`. Keep docs in `docs/`.
- Root `manifest.json` and `obsidian-plugin/manifest.json` must match; `build.mjs` throws on drift. Bundled npm packages must have an allowed license plus a notice (package-local LICENSE or `assets/licenses/provenance.json` with matching hash).
- Packaging is deterministic: sorted entries, fixed timestamps (1980-01-01), fixed mode `0o100644`, `generatedAt: 'normalized'`, `.map`/`.log` skipped. Do not add clocks, random values, absolute paths or unsorted walks. `package_check.py` fails if two runs differ.
- `package.mjs` walks `dist`, `docs`, `assets` and `obsidian-plugin` only to list files in `.artifacts/manifest.json`; the ZIP itself holds `dist/obsidian-plugin/**`, `LICENSE`, `README.md` and third-party notices.
- Forbidden in packages and history: Zotero SQLite names, the Zotero data directory, `.omx/`, `.local/`, PDFs, model weights (`.safetensors`), tokens, home/temp absolute paths. Update `ALLOWED_LITERAL_MATCHES` in `audit_repository.py` only for synthetic test fixtures, with a reason.
- `install_obsidian.py` touches only the chosen vault's plugin directory and never `data.json`; do not run it against a real vault without the user asking.
- No personal paths in scripts; derive from `ROOT`. Scripts call `python3` from `PATH`.

### Testing Requirements
```sh
npm run lint
npm run test:palette
npm run build && npm run package && npm run package:check
python3 -m pytest -q tests/root/test_release_scripts.py tests/root/test_community_package.py
npm run test:e2e          # needs build first and Playwright chromium
npm run audit:privacy
npm run verify            # full gate, once at the end
```
Benchmark: `npm run build && python3 scripts/benchmark_taxonomy_v2.py`.

### Common Patterns
- `.mjs` scripts: top-level await, `root = resolve(new URL('..', import.meta.url).pathname)`, throw `Error` with the missing item named.
- Python scripts: `ROOT = Path(__file__).resolve().parents[1]`, `raise SystemExit(msg)` on failure, one JSON summary line on success.
- Checks fail closed: an empty test lane or missing artifact is an error, not a skip.

## Dependencies

### Internal
- `frontend/`, `obsidian-plugin/`, `atlas/`, `assets/`, `docs/`, `manifest.json`, `versions.json`, `tests/fixtures/`, `tests/e2e/v2_support.py` (benchmark).

### External
- Node 22+, `esbuild`, `d3`, `cytoscape`, `@antv/g6`; Python 3.11+ with `jsonschema`, Playwright chromium; `curl` only for `palette_sources_check.py --online`.

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
