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
| `npm run lint` | 36 JS and 48 Python files |
| `npm test` | 103 JS tests passed |
| Python | 235 passed, 2 skipped (the optional real-model tests) |
| Browser E2E | 238 passed |
| Package / community | deterministic package check and 9 community smoke checks passed |
| Privacy audit | passed |
| Real E5, explicit opt-in (`ATLAS_REAL_E5=1`, offline model cache) | 2 passed: 384 dimensions, pinned model revision, semantic graph edge, on synthetic text |

Performance (`scripts/benchmark_taxonomy_v2.py`, synthetic 1,000 papers / 1,410 concepts, headless Chromium on a development machine, 7 of 7 gates passed): cold all-paper first interactive max 843.8 ms (gate 2500); warm field p95 137.5 ms (500); selection p95 64.1 ms (500); representative expand 80.4 ms (1500); collapse 48.5 ms (500); G6 settle 2583.3 ms (10000); interaction task max 154 ms (200). These are fixture results, not guarantees for any user's library.

Native Obsidian host QA was reviewed and recorded as a redacted PASS: the installed bundle matched the build, the local E5 model reported ready (384 dimensions), and native pointer and visual checks of all-paper overview, field focus, Selection, G6 expand/collapse and light/dark themes passed. Private host counts are intentionally not published.

Limits: the source digest recorded for the last full run (`b0c37e14fa9ab6531b4a963b5e3793518b8c1700951594b05d1b05f6b8caddd2`, 124 files) is a historical pre-handoff value and does not identify the current tree. Gold v2 results are synthetic conformance to a new ontology, not blind or private accuracy. The native baseline had zero user overrides, so preservation of non-empty user state is proven by synthetic tests only. Native keyboard-only activation was not separately proven (public browser tests cover keyboard semantics). Add/delete handling uses the real backend with a fake Zotero transport. Rollback has no user trigger. Community submission remains deferred.

Reproduce: `npm run verify`; explicit real model run and benchmark commands are in [claude-handoff.md](claude-handoff.md). npm scripts call `python3` from `PATH`; activate the intended virtual environment first. Node and Python should share one CPU architecture (on macOS `npm run lint` forces `arch -arm64 python3`).
