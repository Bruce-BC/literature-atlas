<!-- Generated: 2026-11-19 | Updated: 2026-11-19 -->

# Literature Atlas contributor contract

- Preserve the accepted real Zotero/Obsidian scope in docs/architecture.md and docs/verification.md. Synthetic demo alone is not delivery.
- Zotero is read-only. Never change source metadata, files, filenames or attachments. Never write Zotero SQLite.
- Source DB identity, epoch and item generation fence all jobs. Never delete from a failed/partial source snapshot or remap overlays across different source DBs.
- Full graph aggregates are independent of detail subsampling; citations, semantic similarity and keyword cooccurrence stay distinct.
- All user metadata is text, never untrusted HTML. Loopback-only authenticated API, exact-origin memory-only plugin handshake, no arbitrary file/URL read.
- Keep real library data, PDF content, vault paths, tokens, runtime/model caches and .omx/ out of Git and release artifacts.
- Respect lane ownership. Do not revert another contributor's work. Coordinate schema/interface changes through the main integration lane.
- Run targeted tests, then the relevant build/lint/browser checks. Report failures and limitations plainly; do not replace real inference/host checks with mocks.

## Purpose
Desktop-only, local, read-only Zotero projection. A Python 3.11+ loopback bridge (`atlas/`) polls the local Zotero API, builds fenced snapshots, taxonomy and graphs; a web UI (`frontend/`, D3 / Cytoscape / AntV G6) renders overview, paper and concept views; an Obsidian plugin (`obsidian-plugin/`) launches the bridge and hosts the UI. Version `0.1.0`, minimum Obsidian `1.15.1`. Community submission is deferred. `CLAUDE.md` and `docs/claude-handoff.md` (Korean) carry the full handoff.

## Key Files
| File | Description |
|------|-------------|
| `CLAUDE.md` | Claude Code entry point: invariants, setup, test command table |
| `README.md` | Product description, dev setup, Obsidian install, usage; shipped in the plugin ZIP |
| `package.json` | npm scripts (`build`, `lint`, `test`, `test:e2e`, `package`, `verify`, `audit:privacy`) and pinned JS deps |
| `pyproject.toml` | Python package `literature-atlas-local` (`atlas*`), extras `pdf` / `ai` / `test`, CLI `literature-atlas` |
| `manifest.json` / `versions.json` | Obsidian plugin manifest and version → min-app map (copied into `dist/` by build) |
| `SECURITY.md` | Security policy |
| `LICENSE` | MIT; shipped in the plugin ZIP |
| `.gitignore` | Excludes `.omx/`, `.omc/`, `.local/`, `.artifacts/`, `dist/`, SQLite, PDFs, model weights, `.env` |
| `.github/workflows/ci.yml` | CI: same step sequence as `npm run verify` on Ubuntu, Node 22, Python 3.11 |

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `atlas/` | Python bridge: Zotero reader, storage, taxonomy, graph, embeddings, HTTP server (see `atlas/AGENTS.md`) |
| `frontend/` | Web UI sources bundled into `dist/web` (see `frontend/AGENTS.md`) |
| `obsidian-plugin/` | Obsidian host: bridge lifecycle, handshake, runtime resources, managed export (see `obsidian-plugin/AGENTS.md`) |
| `scripts/` | Build, lint, package, privacy audit, browser runner, benchmark, installer (see `scripts/AGENTS.md`) |
| `docs/` | Public contracts, architecture, acceptance mapping, verification log (see `docs/AGENTS.md`) |
| `tests/` | Python, JS, browser E2E tests and synthetic fixtures (see `tests/AGENTS.md`) |
| `assets/` | `palettes.json`, `palette-sources.json`, third-party `licenses/`. No AGENTS.md here on purpose: `scripts/build.mjs` copies this directory recursively into `dist/web/assets` and embeds it in community `main.js`, so any added file ships |

## For AI Agents

### Working In This Directory
- Read order: `CLAUDE.md` → `docs/claude-handoff.md` → contracts in `docs/` → nearest directory `AGENTS.md`.
- Schema/interface changes update `docs/snapshot.schema.json`, `docs/implementation-interfaces.md`, the decoder and tests together.
- Do not add dependencies; install only existing extras (`pip install -e '.[test,pdf]'`).
- npm scripts call `python3` from `PATH`; activate the intended venv first. On macOS `npm run lint` forces `arch -arm64 python3`.
- Never write personal absolute paths in docs or code; `scripts/audit_repository.py` scans tracked files and Git history.
- Never add files under `assets/` unless they are meant to ship.

### Testing Requirements
- Targeted test first, then related build/lint/browser checks.
- Docs-only changes: `npm run lint && npm run audit:privacy`.
- Full gate: `npm run verify` (lint → JS unit → palette → build → package → package:check → community smoke → pytest → E2E → privacy audit).

### Common Patterns
- Synthetic fixtures only; real-library evidence stays redacted and outside Git.
- Deterministic outputs: normalized ZIP timestamps, sorted resource contracts, fixed detail sampling.

## Dependencies

### External
- JS: `d3` 7.9.2, `cytoscape` 3.35.0, `@antv/g6` 5.1.1; dev `esbuild` 0.28.2. Node ≥ 22.
- Python: stdlib core; optional `pypdf` (pdf), `sentence-transformers`/`torch` stack (ai, real E5 only), `pytest`/`playwright`/`jsonschema` (test).

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
