# Literature Atlas

Literature Atlas is a desktop-only, local, read-only Zotero projection with linked circular block/ribbon, paper and concept views, OKLCH light/dark/system themes, deterministic detail sampling, and an Obsidian companion plugin. The Obsidian community submission has not been submitted or listed.

The source of truth is Zotero. The bridge never writes Zotero metadata, SQLite, PDF files or filenames. Local source IDs and epochs fence all derived work; incomplete source responses preserve the last valid snapshot. Handwritten Obsidian notes are never deleted or overwritten.

## Development

Requirements: Python 3.11+, Node 22+, Chromium and Playwright. A clean setup is:

```sh
npm ci
python3 -m pip install -e '.[test,pdf]'
python3 -m playwright install chromium
npm run verify
```

The browser suite starts the synthetic `atlas serve --demo` bridge and fails if the bridge, app or Playwright is absent. A real e5 model smoke is separate from deterministic fake-vector CI. The plugin never downloads model code or weights and never runs pip; install the optional AI dependencies and cache the model manually before this smoke:

```sh
python3 -m pip install -e '.[ai]'  # manual setup; the plugin never runs this
ATLAS_DATA_DIR="$(mktemp -d)"
python3 -m atlas serve --data-dir "$ATLAS_DATA_DIR" --web-root dist/web --port 0 --poll-interval 30 --launch-id local-e5-smoke --parent-origin app://obsidian.md --embedding-provider e5 --zotero-url http://127.0.0.1:23119/api --library-id 0
```

The bridge is read-only and this release is scoped to the personal Zotero library (`--library-id 0`) on the local API; group libraries, cloud uploads, and remote Zotero URLs are outside the product scope. OpenAlex DOI metadata enrichment is opt-in. Record only redacted model revision/status and latency from a real smoke. The fake provider never satisfies the real-inference claim.

The versioned contracts are [snapshot.schema.json](docs/snapshot.schema.json) and [implementation-interfaces.md](docs/implementation-interfaces.md). Palette provenance and notices are in `assets/palette-sources.json`; derived OKLCH tokens and deterministic fallbacks are in `assets/palettes.json`.
Run `python3 scripts/palette_sources_check.py --online` before publication to re-check all pinned raw URLs and MIT license notices; ordinary CI uses the offline pinned metadata and conversion checks.

The repository intentionally contains only synthetic fixtures. Do not copy a Zotero database, PDFs, vault paths, model caches, tokens, or `.omx/` state into this repository or a release artifact. The community release downloads only `main.js`, `manifest.json`, and `styles.css`; the bridge and web assets are embedded in `main.js`, so no runtime Python install or network code fetch occurs.

## Obsidian install and runtime

After `npm run build`, install only into a selected vault with `python3 scripts/install_obsidian.py --vault /path/to/vault --source dist/community-plugin`. The script touches that vault's `.obsidian/plugins/literature-atlas` directory and backs up an existing plugin directory before replacement. In Obsidian 1.15.1 or newer, enable **Literature Atlas**, leave the bridge interpreter as `python3` or select an explicit Python 3.11+ executable in plugin settings, then run **Open Literature Atlas**. The bridge starts a read-only watcher immediately, polls metadata every 30 seconds by default (5–300 seconds allowed), exposes stale/lag status while Zotero is offline, and catches up after reconnect. Managed exports use stable hashed IDs in the plugin-owned directory; handwritten notes and unrelated vault files are byte-preserved. The circular view supports pan/zoom, category filters, theme palettes, and bounded detail sampling; full aggregates remain independent of the visible sample.

## Using the atlas

Start in **Overview**, expand a category to its 24 paper groups, open a paper page, then use previous/next navigation. Multiple categories can stay expanded or be collapsed independently. Paper color encodes connection degree; the eligible-paper panel reports connection density for the currently selected scope. Directed citations and semantic relationships remain separate, and full eligible-paper/edge counts remain unchanged when the detail sample is reduced. The UI reports observed coverage and status rather than implying complete literature coverage.

The G6 view reuses the same filters, selection, and aggregate counts as the other views. Its current synthetic readiness reference is about 3.7 seconds on the test fixture; that is an observed fixture result, not a guarantee for a user's library. Large graphs remain bounded by the configured visible-node and relationship limits.

`npm run package` creates `.artifacts/literature-atlas-plugin.zip` and a checksum manifest; `npm run package:check` verifies required members, normalized timestamps, forbidden private/corpus paths, checksum equality and repeat-build determinism. The package contains only built web/plugin/bridge files and public palette assets. Real Zotero smoke evidence is redacted and kept outside Git; it is never a substitute for synthetic lifecycle tests.

The community release is emitted at `.artifacts/community-plugin/` as exactly `main.js`, `manifest.json`, and `styles.css`, plus `SHA256SUMS`. Its `main.js` carries an immutable versioned resource contract for the Python bridge and web assets; the host extracts those verified UTF-8 resources into a digest-scoped plugin runtime directory. `manifest.json` and `versions.json` advertise version `0.1.0` with minimum Obsidian `1.15.1`.
