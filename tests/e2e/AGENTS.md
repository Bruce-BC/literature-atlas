<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-11-19 | Updated: 2026-11-19 -->

# tests/e2e

## Purpose
Playwright (headless chromium) acceptance tests against the built, authenticated app: taxonomy/interaction v2 UI contracts, G6 and Cytoscape behavior, density, lifecycle replay. Synthetic data only. No `__init__.py` here.

## Key Files
| File | Description |
|------|-------------|
| `v2_support.py` | Shared synthetic fixtures and native-browser harness for v2. |
| `v2_pipeline_support.py` | Real-pipeline harness: browser, real API, `SyncManager`, `GraphEngine`. |
| `test_taxonomy_v2_acceptance.py` | Native-browser acceptance for TAXV2-015..043 UI contracts (16 tests). |
| `test_taxonomy_v2_pipeline.py` | Integrated pipeline tests (5 tests). |
| `test_browser_g6.py` | AntV G6 renderer acceptance on the authenticated built app. |
| `test_browser_g6_performance.py` | Large synthetic G6 responsiveness and graph-reuse smoke. |
| `test_browser_category_density.py` | Category expansion and full-graph density on the rich fixture. |
| `test_browser_motion.py` | Collection-map transitions ME-9..ME-16: immediate commit, motion vs reduced-motion final-state equality, rapid clicks, drag during a tween, dense + non-dense camera tween / return / `capture()` guard / AC-14 / T5 registration, keyboard + inert panel ghost, nothing animating at idle. |
| `test_browser_idle_frames.py` | F1: idle collection map script budget (CDP `ScriptDuration` <= 400 ms / 5 s on `large_fixture()`), the G6 wrapper's frame gate on every G layer, wake on the first frame after hover input, click after a 2 s idle commits. |
| `test_browser_map_height.py` | F2: collections-mode map height follows the viewport (737x964, 1440x1000, 1280x720), wide panel max-height = map height, stacked layout caps the map at ~45vh with the panel open, G6 canvases follow the container; legacy modes keep their height. |
| `test_browser_stacked_layout.py` | B1-B3: points and overlay stay aligned after the stacked shrink/regrow (dpr 2) and a wide viewport resize, the stacked layout keeps the map row at the top after cloud, sub-group and paper clicks, 'N in viewport' follows a pan. |
| `test_browser_cloud_panel_subgroups.py` | F3: cloud panel shows the 8 largest sub-groups + 기타, the rest behind one `<details>`; a selected remainder row keeps the details open. |
| `test_browser_lifecycle.py` | Lifecycle (E02) replay through a real HTTP Zotero emulator. |

## For AI Agents

### Working In This Directory
- Requires built assets: run `npm run build` first, and `python3 -m playwright install chromium`.
- Do not mock the browser, API or sync pipeline; failures are reported as is.
- Collection-map selections animate (<= 450 ms) on top of an already committed state. Read screen coordinates or use panel/edge locators only after `app.wait(CAMERA_IDLE)` / `app.stable(...)` (`v2_support.CAMERA_IDLE` includes `data-motion-state` idle): during a cross-fade an inert ghost of the old panel carries the same classes. Never add a test-only switch that disables motion; emulate `prefers-reduced-motion` instead.
- Keep screenshots/receipts free of titles, paths and tokens from real data.
- `scripts/test_browser.py` is the main E2E driver (`npm run test:e2e`); it uses its own temp data dir.

### Testing Requirements
```sh
npm run build && npm run test:e2e
python3 -m pytest -q tests/e2e/test_taxonomy_v2_acceptance.py tests/e2e/test_taxonomy_v2_pipeline.py
python3 -m pytest -q tests/e2e/test_browser_g6.py
```

### Common Patterns
- Support modules build synthetic snapshots and start the app; tests assert on DOM/canvas state and revision counters.

## Dependencies

### Internal
- `web/` build output, `atlas/`, `frontend/`, `scripts/test_browser.py`, `tests/fixtures/`.

### External
- Playwright 1.62.0, chromium, pytest.

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
