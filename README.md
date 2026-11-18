# Literature Atlas

Literature Atlas is a local, read-only Zotero projection with linked circular block/ribbon, paper and concept views, OKLCH light/dark/system themes, deterministic detail sampling, and an Obsidian companion plugin.

The source of truth is Zotero. The bridge never writes Zotero metadata, SQLite, PDF files or filenames. Local source IDs and epochs fence all derived work; incomplete source responses preserve the last valid snapshot. Handwritten Obsidian notes are never deleted or overwritten.

## Development

Requirements: Python 3.11+, Node 22+, Chromium and Playwright. A clean setup is:

```sh
npm ci
python3 -m pip install -e '.[test,pdf]'
python3 -m playwright install chromium
npm run verify
```

The browser suite starts the synthetic `atlas serve --demo` bridge and fails if the bridge, app or Playwright is absent. A real e5 model smoke is separate from deterministic fake-vector CI:

```sh
python3 -m pip install -e '.[ai]'
ATLAS_DATA_DIR="$(mktemp -d)"
python3 -m atlas serve --data-dir "$ATLAS_DATA_DIR" --web-root dist/web --port 0 --poll-interval 30 --launch-id local-e5-smoke --parent-origin app://obsidian.md --embedding-provider e5 --zotero-url http://127.0.0.1:23119/api --library-id 0
```

The bridge is read-only and this release is scoped to the personal Zotero library (`--library-id 0`) on the local API; group libraries, cloud uploads, and remote Zotero URLs are outside the product scope. Record only redacted model revision/status and latency from a real smoke. The fake provider never satisfies the real-inference claim.

The versioned contracts are [snapshot.schema.json](docs/snapshot.schema.json) and [implementation-interfaces.md](docs/implementation-interfaces.md). Palette provenance and notices are in `assets/palette-sources.json`; derived OKLCH tokens and deterministic fallbacks are in `assets/palettes.json`.
Run `python3 scripts/palette_sources_check.py --online` before publication to re-check all pinned raw URLs and MIT license notices; ordinary CI uses the offline pinned metadata and conversion checks.

The repository intentionally contains only synthetic fixtures. Do not copy a Zotero database, PDFs, vault paths, model caches, tokens, or `.omx/` state into this repository or a release artifact.

## Obsidian install and runtime

After `npm run build`, install only into a selected vault with `python3 scripts/install_obsidian.py --vault /path/to/vault`. The script touches that vault's `.obsidian/plugins/literature-atlas` directory and backs up an existing plugin directory before replacement. In Obsidian, enable **Literature Atlas**, leave the bridge interpreter as `python3` or select an explicit Python 3.11+ executable in plugin settings, then run **Open Literature Atlas**. The bridge starts a read-only watcher immediately, polls metadata every 30 seconds by default (5–300 seconds allowed), exposes stale/lag status while Zotero is offline, and catches up after reconnect. Managed exports use stable hashed IDs in the plugin-owned directory; handwritten notes and unrelated vault files are byte-preserved.

`npm run package` creates `.artifacts/literature-atlas-plugin.zip` and a checksum manifest; `npm run package:check` verifies required members, normalized timestamps, forbidden private/corpus paths, checksum equality and repeat-build determinism. The package contains only built web/plugin/bridge files and public palette assets. Real Zotero smoke evidence is redacted and kept outside Git; it is never a substitute for synthetic lifecycle tests.
