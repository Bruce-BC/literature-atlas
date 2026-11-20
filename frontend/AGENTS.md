<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-11-19 | Updated: 2026-11-19 -->

# frontend

## Purpose
Browser UI for Literature Atlas: ES-module source for the snapshot viewer (overview, paper, concept views, field tree, density panel, selection card). `scripts/build.mjs` bundles `main.js` with esbuild into `dist/web/app.js`, copies `index.html` and `styles.css` beside it, and embeds `dist/web/` in the Obsidian plugin. Files in this directory other than those three inputs (this `AGENTS.md` included) are not copied into `dist/`.

## Key Files
| File | Description |
|------|-------------|
| `index.html` | Static shell; copied verbatim to `dist/web/`. Default screen (AC-11): compact header, `#atlas-toolbar` (Search + counters/Fit/zoom added by `main.js`), the map card (`#atlas-circular`/`#atlas-field-network` slot + `#atlas-cloud-panel`). Everything else is in `<details id="atlas-advanced">` (closed): view selects, palette/theme, relation, detail, density, category table, lower networks, lists, Selection, Diagnostics, graph settings. |
| `styles.css` | UI styles; copied verbatim to `dist/web/`. |
| `main.js` | Bundle entry: DOM wiring, state (default `overviewRenderer: 'collections'`), API connect, palette/theme, view orchestration. Lower networks render only while Advanced is open (`#atlas-advanced[data-lower-networks]` = `deferred`/`rendered`); toolbar zoom/Fit and 'Fit new content' cover only on-screen views. |
| `api.js` | `AtlasApi` loopback client, `safeUrl`, snapshot compatibility check (`serverId`), entity patch builder. |
| `decoder.js` | `decodeSnapshot` schema validation, `displayLabel`, `safeText`, `indexSnapshot`. |
| `view-state.js` | Reducer for selection/scope/focus/camera reasons (`VIEW_EVENTS`, `reduceViewState`, `sourceKey`); scope kinds `library`, `field`, `cloud` (map-only dim scope, library camera). |
| `projection.js` | Overview, graph and circular-layout projections from a snapshot (`PAPER_EDGE_CAP`). |
| `overview-expansion.js` | Expandable field-to-paper overview graph with page and edge budgets. |
| `all-paper-overview.js` | All-papers map projection (`grouping: 'category'` default: one cloud per category via `categoryGroups` + `layoutCollectionClouds({groups, pointId})`, outlines/labels through the cloud overlay, `'collection'` for the cloud map) with preset coordinates and unique-paper viewport counting. In collections mode points take their cloud's hue (`categoryColor` = the cloud point tone or a sub-group variant; replica points get `ring` + `outline`), and each cloud carries its outline (`kind`, `method`, `d`, `bbox`) and colours; `cloudStats` reports `shapeMs`/`colorMs`. |
| `sampling.js` | `stableHash`, `stableOrder`, `sampleDetail` (deterministic detail subsampling). |
| `paper-density.js` | `computePaperDensity` over all eligible papers (semantic/citation relations, category matrix). |
| `density-view.js` | Density ramp colors and density panel rendering. |
| `category-explorer.js` | Paged per-category paper explorer. |
| `network-style.js` | Category/field colors (OKLCH to hex), contrast helpers, legends, field paths, zoom range. |
| `network-views.js` | Cytoscape network views and the G6 view wrapper (`g6View`, `renderNetworks`). Edges are slightly curved (`CY_EDGE_CURVE`); node shape/size/name flags come from `node-emphasis.js`; a shape key line is drawn under each network. |
| `node-emphasis.js` | Pure, renderer-neutral emphasis: `markHubs` (link tiers 0-2 / 3-5 / 6-11 / 12+: shape, size, icon from tier 2, heavier outline at tier 3), `markLevels` (field hierarchy depth -> shape, size, border, numeral badge, domain icon), `namedWhenShown` ("노드 이름 표시" toggle: all nodes up to 40, else hubs and field nodes). |
| `node-icons.js` | Vendored Lucide (ISC) outline paths with attribution, `FIELD_ICONS` (field slug -> icon), `iconUri` (cached, colour-sanitised `data:image/svg+xml` URI for G6 `iconSrc` / Cytoscape `background-image`; the bridge CSP allows `img-src data:`). Add icons here only, with their Lucide geometry; never fetch icons at runtime. |
| `g6-render.js` | AntV G6 renderer (`renderG6Network`, `graphData`) with keyboard focus support; gives every G layer canvas of its graph a per-instance idle frame gate (`createFrameGate`: ~4 ticks/s after 1 s idle, woken by input/updates; collection map canvases follow container size changes); owns the camera motion: self-owned rAF tween in one `animated` span with the wrapper's pan clamp on every frame, capture-phase gesture stop, return memory, `capture()` guard (never persists an auto-moved camera). |
| `cloud-overlay.js` | Collection cloud overlay (`createCloudOverlay`): outline `<path>`s (LOCAL geometry, one `translate` per cloud, model circle kept in `data-cx/cy/r`, plus `data-kind`/`data-method`) in an SVG under, and label buttons above, the G6 canvas as siblings of the container; colours are per-element CSS variables (`--cloud-fill`, `--cloud-stroke`, `--cloud-fill-selected`), the unfiled hatch `<defs>` id carries an instance-unique prefix; hover inside a cloud circle highlights its outline (hit-testing stays the circle test in `render.js`); from `SUBGROUP_MIN_ZOOM` it draws the sub-group outlines once (lazy `computeCloudShapes(layout)`, memoised) and only toggles their visibility afterwards; label weight tiers (`labelWeightTier`) are drawn as a glyph stroke so they never change label widths or placement; lifecycle driven by the G6 wrapper (`overlay` option); pure helpers `anchorLabels`, `labelBox`, `hiddenAfterZoom`. |
| `cloud-colors.js` | Pure cloud colour engine: `assignCloudHues` (24 fixed hue slots by id hash, id order, no neighbour correction by default = D-A6 stability), `cloudTones` (fill/stroke/point/variant/selected tones in OKLCH, fill relative to the panel), `neighbourStats` (measured only), CVD simulation. |
| `cloud-shapes.js` | Pure cloud outline engine: `classifyCloud`, `computeCloudShapes` (contour S1 -> soft hull S2 -> circle fallback, LOCAL coordinates, own memo by cloud id + local coordinates + spacing, `{subgroups: false}` for the initial render), `cloudShape`, `checkOutline`, `cloudShapeStats`. |
| `edge-strength.js` | Pure edge-strength engine: semantic tiers (mutual rank + shared-concept Jaccard), keyword tiers over `keywordCooccurrence` (projection.js, shared memo), untiered citations, `edgesForState` (the only source of what the collection map draws and of `displayedEdges`/`displayedBridges`), `tierLegend`, `TIER_STYLE` (tier width/opacity/dash shared by the overlay, Cytoscape and G6). |
| `cloud-panel.js` | Collection cloud panel (AC-7..10): pure `cloudPanelModel` from the FULL snapshot (sub-items, distinctive concepts `p_C·ln(p_C/p_L)`, noise filter, representative papers, years, related clouds, unfiled suggestion; `subItems.visibleIds`/`moreCount` = 8 largest + 'other' rows, the rest rendered behind one `<details>`), `cloudSelection`, `stripDisplayTags`, `isNoiseLabel`, and the `textContent`-only `renderCloudPanel` for `<aside id="atlas-cloud-panel">`. |
| `collection-clouds.js` | Pure deterministic collection-cloud layout (`layoutCollectionClouds`, `placeCloudLabels`), imports `d3`. |
| `render.js` | d3 circular chord view, list renderers, G6 overview entry points (incl. `renderG6CollectionOverview`). |
| `motion.js` | Pure transition coordinator (plan motion-and-edges §3): `buildTimeline` (every kind <= 450 ms, reduced = 0), `transitionKind`, `transitionSets`, `cameraDecision` (input-source rule: cloud 25 % / 45 %, panel 80 % pan, map point never), tween maths with per-frame clamp, return memory / `capturedCamera`, `interruption`, `coalesceInput`. |
| `motion-driver.js` | Thin DOM/RAF driver: `begin(source)` before the first dispatch (T5 old-frame capture, `data-motion-state="running"`), `play()` after the commit (camera decision, decorations, panel slide / cross-fade ghost / recede), `reset()`, idle tracking, `debug()` (= `AtlasDebug.getMotion()`). |
| `camera-policy.js` | Pure camera/position policy shared by Cytoscape and G6 (zoom clamp [0.2, 3], position seeding). |
| `selection-view.js` | Selection detail card rendering. |
| `palette.js` | Applies palettes from `assets/palettes.json` and resolves light/dark theme. |
| `trace.js` | Bounded local transition trace with whitelisted, typed fields (ring buffer, no console/network). |

## For AI Agents

### Working In This Directory
- User metadata (titles, abstracts, aliases, labels) is text: use `textContent`/`createElement`, never `innerHTML` (none is used today). Run values through `safeText`/`displayLabel`.
- Full aggregates (`overview` totals, `paper-density.js`) are computed from all eligible papers and stay independent of `sampling.js` detail subsampling.
- Keep citation, semantic and keyword co-occurrence edges as distinct relation types; do not merge them in `projection.js` or `overview-expansion.js`.
- Colors: categorical families and OKLCH to hex live in `network-style.js`; collection-cloud colours live in `cloud-colors.js` (the collection map hides the category field legend, because its points use cloud colours, not category colours); the quantitative density ramp lives in `density-view.js` as a low-chroma slate ramp that no categorical family shares. Do not reuse one for the other.
- `trace.js` drops unknown keys, free text, paths, tokens, item IDs, titles and spans. Add a field only with a typed validator; never log span text (hash + offset only).
- Do not store the token outside memory; connection and origin rules are in `api.js` and the plugin.
- A schema change needs `docs/snapshot.schema.json`, `docs/implementation-interfaces.md`, `decoder.js` and tests updated together.
- Each module exports a plain `Atlas*` object alongside named exports; keep both in sync when adding exports.
- No new dependencies. No real library data, vault paths or tokens in fixtures.

- Default screen contract (AC-11): legacy overview modes still render in the map slot when picked from the Advanced Overview select (no renderer moved). Do not add controls to `#atlas-toolbar` without updating the exact-control-set E2E assertion. Browser tests that drive legacy controls call `open_advanced` / `legacy_screen` (`tests/e2e/v2_support.py`, mirrored in `scripts/test_browser.py`).

### Testing Requirements
- Unit (Node): `npm test` runs `tests/frontend/*.test.mjs`.
  - `frontend.test.mjs`: api, category-explorer, decoder, density-view, network-style, projection, sampling.
  - `g6-lifecycle.test.mjs`: `g6-render.js`, `network-style.js`.
  - `paper-density.test.mjs`: `decoder.js`, `paper-density.js`.
  - `taxonomy-decoder.test.mjs`: `decoder.js`.
  - `taxonomy-interaction.test.mjs`: `all-paper-overview.js`, `camera-policy.js`, `overview-expansion.js`, `view-state.js`.
  - `collection-overview.test.mjs`: `all-paper-overview.js` (collection grouping), `cloud-overlay.js` pure helpers, `g6-render.js` (id-array select, overlay lifecycle, miss clicks).
  - `edge-strength.test.mjs`: `edge-strength.js` (ME-1/2/4/5). `cloud-edges.test.mjs`: tier encoding, strong-line contrast (ME-7), edge geometry, view-state mapping, lower-network tiers (ME-8).
  - `cloud-colors.test.mjs`: `cloud-colors.js` (AE-1/2/3/7). `cloud-shapes.test.mjs`: `cloud-shapes.js` (AE-5/6, memo, determinism).
  - `cloud-panel.test.mjs`: `cloud-panel.js` model, `view-state.js` cloud events (`CLOUD_SELECT`, `CLOUD_SUBITEM_SELECT`), `trace.js` cloud enums.
  - `taxonomy-trace.test.mjs`: `trace.js`, `view-state.js`.
  - `node-emphasis.test.mjs`: `node-emphasis.js`, Cytoscape curve/hub style, category-cloud layout of the all-paper map.
  - `motion.test.mjs`: `motion.js` (ME-10..ME-15), the G6 wrapper's camera tween / gesture stop / forced finish / `capture()` guard (fake graph), `cytoscapeStyle` transition (ME-13).
  - `taxonomy-visual.test.mjs`: `decoder.js`, `density-view.js`, `network-style.js`, `projection.js`.
- Browser: `npm run build && npm run test:e2e` runs `tests/e2e/` (`test_browser_g6.py`, `test_browser_g6_performance.py`, `test_browser_lifecycle.py`, `test_browser_category_density.py`, `test_taxonomy_v2_acceptance.py`, `test_taxonomy_v2_pipeline.py`) against `dist/web/`; rebuild after every frontend edit.
- Before finishing: `npm run lint`, `npm run audit:privacy`; full gate is `npm run verify`.

### Common Patterns
- Pure, DOM-free modules (`projection.js`, `sampling.js`, `view-state.js`, `camera-policy.js`, `paper-density.js`, `collection-clouds.js`) so Node tests import them directly. `collection-clouds.js` imports `d3`, so its Node test needs `node_modules`.
- State flows through `reduceViewState` events; renderers consume state and dispatch events (hover and keyboard focus do not dispatch).
- Collection-map transitions decorate a state that is already committed: call `motion.begin(source)` in an input handler BEFORE its first dispatch and never move real state changes into an animation callback. Decoration elements use `atlas-motion-*` classes only, so selectors for committed elements never match them.
- Rendering is idempotent: renderers take an `existing` handle and a revision, and reuse or replace it.
- Graph size is bounded by edge budgets and page sizes (`PAPER_EDGE_CAP`, `VIEW_EDGE_BUDGET`, `CHILD_PAGE_SIZE`, `CATEGORY_PAGE_SIZE`).
- Derived values from a snapshot are cached in a `WeakMap` keyed by the snapshot object.

## Dependencies

### Internal
- `../assets/palettes.json` (imported by `main.js`), `../assets/` copied into `dist/web/assets`.
- Backend snapshot contract: `../docs/snapshot.schema.json`, `../atlas/` served over the loopback bridge.
- Consumed by `../scripts/build.mjs` and embedded by `../obsidian-plugin/`.

### External
- `d3` (`import * as d3`, in `render.js`): arc/ribbon chord view; in `collection-clouds.js`: headless `d3-force` with a fixed `randomLcg` seed and `packSiblings` for the collection cloud layout.
- `cytoscape` (default import, in `network-views.js`): network views.
- `@antv/g6` (`Graph`, `GraphEvent`, `NodeEvent`, in `g6-render.js`): G6 renderer.

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
