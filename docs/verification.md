# Verification contract

The release gate is `npm run verify`. It fails closed when any source, frontend/plugin test, schema fixture, Python test, built asset, browser dependency, or privacy check is missing. The gate runs:

1. JavaScript syntax and Python compilation checks, with snapshot schema validation.
2. Node frontend/plugin unit tests and Python graph/backend tests.
3. Offline OKLCH conversion, deterministic sRGB fallback, 96 opaque contrast checks across three palettes and two themes. Low-alpha ribbons require a solid outline or text relation alternative.
4. A strict build that bundles the web app, plugin and Python bridge; no placeholder files are accepted.
5. A deterministic ZIP package with normalized timestamps and SHA-256 manifest.
6. Fixture-backed Playwright replay against `atlas serve --demo` at 320, 736 and 1024 pixel widths, including API authentication, rendered SVG sectors, selection clicks and DOM-safe hostile metadata.
7. Source, generated bundle, package archive and Git-history privacy scans.

The browser replay uses synthetic data only. The separate real e5 smoke must report model revision and status; the fake provider is for deterministic CI and never represents real inference. A live Zotero smoke is read-only and produces only redacted counts/status/latency evidence outside Git.

The large demo fixture is `tests/fixtures/demo-snapshot.json`: 48 synthetic papers, four root fields and eight DAG subfields, multi-memberships, concept-cooccurrence edges, separated citation/semantic/related edges, an isolate, an unknown-text record, hostile literal metadata, uneven strata, and primary-display-field overview weights. `tests/fixtures/snapshot.json` remains the small unit fixture.

## Latest verified scope: taxonomy / interaction v2 (version 0.1.0)

The gate additionally covers the v2 suites listed in [taxonomy-v2-acceptance.md](taxonomy-v2-acceptance.md) (43 criteria). Last recorded results on **public synthetic fixtures only**:

| Check | Result |
|---|---|
| `npm run lint` | 55 JS and 56 Python files |
| `npm test` | 220 JS tests passed |
| Python | 291 passed, 2 skipped (the optional real-model tests); 79 of them are the browser suites under `tests/e2e` |
| Browser E2E (`npm run test:e2e`) | 247 of 247 assertions passed |
| Package / community | deterministic package check and 9 community smoke checks passed |
| Privacy audit | passed |
| Real E5, explicit opt-in (`ATLAS_REAL_E5=1`, offline model cache) | 2 passed: 384 dimensions, pinned model revision, semantic graph edge, on synthetic text |

Performance (`scripts/benchmark_taxonomy_v2.py`, synthetic 1,000 papers / 1,410 concepts / 12 synthetic collections, headless Chromium on a development machine, 9 of 9 gates passed): cold first interactive max 983.1 ms (gate 2500); warm field p95 147.0 ms (500); selection p95 81.3 ms (500); representative expand 89.9 ms (1500); collapse 73.6 ms (500); G6 settle 2108.9 ms (10000); interaction task max 148 ms (200); collection-map layout + shape + colour 105.0 ms (150); collection-map point click to committed selection 88.7 ms (103.3 = measured pre-motion baseline 83.3 + 20). Reported without a gate: transition frame interval p95 17.5 ms in this run (17 ms before the audit fixes, 25 to 33 ms across four runs after them, 17.5 ms now; the cause of the spread was not isolated), cloud edge computation max 7 ms, idle collection-map script time 100.8 ms per 5 s (about 1,360 to 1,550 ms per 5 s before the G6 frame gate). Outside the benchmark, on the same fixture (per-frame recording of one child node in headless Chromium): after a click on a field of the G6 field overview the first frame with the child nodes came 9 ms after pointer release, and the node then moved on one spring curve for 560 ms with no zoom change. Before this change the same recording showed the camera zooming from 1.4 to 1.83 within about 50 ms roughly half a second after the click and snapping back to 1.4 about 300 ms later. These are fixture results, not guarantees for any user's library.

Native Obsidian host QA was reviewed and recorded as a redacted PASS: the installed bundle matched the build, the local E5 model reported ready (384 dimensions), and native pointer and visual checks of all-paper overview, field focus, Selection, G6 expand/collapse and light/dark themes passed. Private host counts are intentionally not published.

Follow-up host check for the automatic concept filter, node emphasis and the category-cloud all-paper map (same redaction rules, screenshots kept local): the installed bundle hash matched the build; after a reindex the real library's concept ledger reported `concept-quality-v4` and fewer active concepts, the newly removed entries carrying `function-word` or `low-termhood`; the all-paper map showed one named, separated cloud per category; the G6 field overview showed level shapes with numerals and curved edges; the lower networks showed hub shapes and the shape key. Not checked in the host this round: the "노드 이름 표시" toggle, the lower networks with the G6 engine, light colour scheme, keyboard use, and expand timing (measured on the synthetic fixture only).

Collection cloud map host audit (same redaction rules, screenshots kept local): the installed bundle hash matched the build. With a real Zotero library the map opened as the default screen with one named, visually separated cloud per collection plus one unfiled cloud; the cloud → sub-group → paper detail workflow completed in three native pointer clicks; dragging to the pan limits kept clouds on screen; after the frame gate the idle renderer process measured about 2% CPU (about 35% before). The audit found and fixed, each with a regression test: idle CPU from the G render loop, a map limited to 420 px in tall panes, an unbounded sub-group list, overlay/canvas misalignment after a stacked-layout resize, the page scrolling the map away in the stacked layout, a stale `in viewport` counter, and leader lines drawn across the map for off-screen clouds. Not verified in the host: the light theme (the UI follows the OS colour scheme, not the Obsidian theme, so it was checked only in headless light-scheme runs), keyboard-only operation, `prefers-reduced-motion`, and the Advanced views after these changes. Known and left as is: half of the real collection labels are truncated at the 160 px label cap (the full name is in the panel and tooltip); automatic sub-group names reuse raw tags, including workflow tags; on the real library a cloud camera move rendered 7 frames in 300 ms.

Limits: the source digest recorded for the last full run (`b0c37e14fa9ab6531b4a963b5e3793518b8c1700951594b05d1b05f6b8caddd2`, 124 files) is a historical pre-handoff value and does not identify the current tree. Gold v2 results are synthetic conformance to a new ontology, not blind or private accuracy. The native baseline had zero user overrides, so preservation of non-empty user state is proven by synthetic tests only. Native keyboard-only activation was not separately proven (public browser tests cover keyboard semantics). Add/delete handling uses the real backend with a fake Zotero transport. Rollback has no user trigger. Community submission remains deferred.

Reproduce: `npm run verify`; explicit real model run and benchmark commands are in [claude-handoff.md](claude-handoff.md). npm scripts call `python3` from `PATH`; activate the intended virtual environment first. Node and Python should share one CPU architecture (on macOS `npm run lint` forces `arch -arm64 python3`).
