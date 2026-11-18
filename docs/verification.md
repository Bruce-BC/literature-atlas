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
