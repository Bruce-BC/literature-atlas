# Implementation interfaces (v1 + additive taxonomy v2)

This document freezes the cross-lane boundary. Payloads are UTF-8 JSON; unknown response fields are ignored by clients, while missing required identifiers fail closed. All IDs are source-scoped and are never joined by item key alone.

## Python graph contract

```python
class GraphEngine:
    def __init__(self, cache_dir: Path, provider: str = "e5", allow_download: bool = True): ...
    def build(self, papers, *, source, taxonomy=None, overlays=None,
              settings=None, attachments=None, previous=None) -> dict: ...
```

`build` is pure from the caller's perspective: it does not mutate input objects. It returns a schemaVersion 1 snapshot with `papers`, `fields`, `concepts`, `memberships`, `edges`, `overview`, `keywordAggregate`, `modelStatus`, and `allocationMode`. The backend publishes the complete graph only after source/epoch/generation fences pass. Embedding providers expose model revision, input hash, dimensions, token limit, truncation and failure state; fake vectors are test-only and visibly labelled.

`overview.keywordAggregate` is the complete compact cooccurrence truth, independent of the bounded display graph: `{conceptIds: string[], pairs: [[i, j, count], ...], encoding: "concept-index-pairs", totalPairs?: number, totalWeight?: number}`. Each pair uses ascending concept indices and records the full count across all eligible papers. `edges` may expose only a bounded, deterministic top-K keyword display projection with `type: "keyword"` and provenance pointing to the aggregate; clients must never infer full totals from that projection. Overview responses retain the aggregate; detail responses may bound display edges while leaving the aggregate unchanged. `overview.totalEdges` is the full typed edge count, while `overview.displayedEdges` and `overview.fullGraphEdgeCounts` make the bounded/full distinction explicit.

## Bridge process and HTTP API

The plugin launches `python -m atlas serve --data-dir DATA --web-root WEB --port 0 --poll-interval 30 --launch-id NONCE --parent-origin app://obsidian.md --embedding-provider e5`. The first stdout line is exactly one JSON object `{port, token, pid, launchId}`. Diagnostics go to stderr and never contain source values or tokens.

Loopback API endpoints:

| Method | Path | Contract |
|---|---|---|
| GET | `/health` | server ID, epoch, PID, launch ID, version; no records |
| GET | `/api/v1/status` | sync phase, stale flag, last success, watcher interval/lag |
| GET | `/api/v1/snapshot?projection=full\|overview\|detail&sample=...` | bounded schema snapshot; detail sampling never changes overview |
| POST | `/api/v1/sync` | authenticated single-flight local sync |
| GET/PUT | `/api/v1/settings` | allowlisted provider/threshold/topK/indexed-text/OpenAlex settings with revision CAS |
| GET/POST/PUT | `/api/v1/taxonomy` | source-scoped user fields/concepts plus `facetCatalogConfig`/`inputState`; parent-cycle (after parent-alias normalisation) and patch-key validation; only `fields`, `concepts`, `facetCatalogConfig`, `inputState` are persisted (request control keys and generated snapshot blocks are rejected/dropped) |
| GET | `/api/v1/taxonomy/resolve?entityType&entityId` | redirect-aware canonical ID; same-source suppressed concepts resolve through the quality ledger (`status`, `reason`, `inGraph:false`) without becoming graph nodes; foreign IDs are 404 |
| GET/PUT | `/api/v1/overlays` | source-scoped manual aliases/memberships/pins with `If-Match` CAS |
| GET | `/api/v1/papers/:id` | current paper/detail record |
| GET | `/api/v1/attachments/:id/url` | only a currently registered Zotero `http(s)`/`zotero` target |

The bridge accepts only loopback requests, validates the bearer in constant time, bounds bodies/responses, and never serves arbitrary filesystem paths. Metadata and attachment/full-text clocks are separate. A sync cursor or tombstone is committed only after complete unfiltered `includeTrashed=1` maps, exact payload coverage, collection fence and a second source fence succeed. Standalone attachments are represented with `parentId: null` and `standalone: true`; every unparented record in a user's personal library is retained and never joined to a paper by filename. Live smoke counts are redacted and kept outside Git; repository fixtures use only synthetic counts.

## Obsidian plugin boundary

The plugin owns only its child process. It stores the child handle, PID/start time and launch nonce, starts one watcher on load/reconnect, and stops/restarts only that handle. The panel receives the token and launch nonce through one in-memory `postMessage` to the exact loopback origin; tokens are absent from URLs, notes, logs and persisted files. A managed export uses a stable hashed ID filename and writes only the explicitly owned directory/marker.

## Frontend boundary

`window.AtlasSnapshotDecoder` validates required IDs, known references, duplicate IDs, same-kind hierarchy and cycles, source fences, redirect cycles, facet kinds, and registry color strings. Unknown top-level v1 fields remain forward-compatible; optional taxonomy v2 payloads are deep-copied before being exposed to renderers. Decoding never creates HTML. A legacy schemaVersion 1 snapshot without the optional blocks remains valid.

`collections[]` rows carry an optional `parentId` (collection id string or `null`; absent decodes as `null`, so older cached snapshots remain valid). The backend sets it from the Zotero `parentCollection` key only when that collection currently exists (not trashed) in the same library, otherwise `null`. A non-null `parentId` must reference an existing collection id in the same snapshot, must not be the collection itself, and the hierarchy must be acyclic (the decoder rejects violations). The `detail` projection keeps every ancestor of a kept collection, with `paperIds: []` when it has no visible papers, so the hierarchy stays closed.

### Collection-map counters and edge layers (frontend only, no schema change)

`allPaperOverviewProjection(snapshot, {grouping: 'collection', ...})` returns `counters = {active, represented, points, clouds, viewport, eligibleEdges, displayedEdges, displayedBridges}`:

- `eligibleEdges`: every eligible edge of the selected relation between active papers (unchanged meaning).
- `displayedEdges`: the number of paper–paper **semantic** lines the overlay actually draws for the current view state.
- `displayedBridges`: the number of aggregate cloud–cloud bridges drawn (counted separately; never added to `displayedEdges`).

Both drawn counts come from the pure `edgesForState(snapshot, layout, state)` in `frontend/edge-strength.js` (`state = collectionEdgeState({selectedCloudId, selectedPaperId, selectedPointId, subItemPaperIds, nodeIdsByPaper})`), never from the renderer. Idle draws bridges only (`displayedEdges = 0`); a selected cloud draws its strong intra-cloud lines and emphasises the bridges touching it (others dimmed); a selected paper draws every semantic line of the paper from the clicked replica (or the replica in the selected cloud) and dims every bridge; lines + bridges <= 1000. The map always draws semantic strength whatever the relation select shows. Tier encoding (`TIER_STYLE`): strong 2.4 px / 0.90, medium 1.4 px / 0.55, weak 0.8 px / 0.28 dashed, widths in screen px (`vector-effect: non-scaling-stroke`). `AtlasDebug.getCloudEdges()` exposes the engine result, the mapped state, `edgeMs` and the overlay's drawn counts per tier and bridges. The toolbar shows `N lines · M bridges / E eligible edges` on the collection map.

Lower networks (Advanced) get the same tiers through `withEdgeTiers(snapshot, edges)`: every edge row keeps its id/type and gains `data.tier` (`strong`/`medium`/`weak` for `semantic` and `concept-cooccurrence`, `null` otherwise). Cytoscape styles `edge[tier = ...]`; the G6 wrapper puts the tier into its static edge style and into the edge part of its update key.

### Collection-map transitions (frontend only, no schema change)

Selections on the collection map commit immediately (view-state, the G6 update queue, panel DOM, focus); a transition (plan `motion-and-edges` §3, <= 450 ms) only decorates the committed state.

- `#atlas-field-network[data-motion-state]`: `running` from the input handler (set before its first dispatch) until idle; `idle` = no camera tween in the G6 wrapper, every animation in the `.atlas-map-row` subtree finished, and `data-update-state="idle"`. Reduced motion never decorates: `running` lasts only until the update queue is idle. Browser tests wait with `CAMERA_IDLE` / `MOTION_IDLE` (`tests/e2e/v2_support.py`).
- `window.AtlasMotion` (pure `frontend/motion.js`): `buildTimeline`, `transitionKind`, `cameraDecision` / `cloudCamera` / `panCamera`, `interpolateCamera`, `tweenFrame`, the return-memory helpers and `interruption`. Read-only use by tests.
- `AtlasDebug.getMotion()` (read-only): `{state, reduced, pending, tweening, tweenFrames, tweens, tweensStopped, tweensForced, instantMoves, lastTween, lastTransition: {kind, source, reduced, end, tracks, camera: {move, reason, target, returning, home}, oldFrame, panel}, memory, camera, captured, decorations, clones, oldFrames, animations, stats}`. `camera` / `captured` are `{zoom, x, y}` with screen = model × zoom + (x, y); `captured` is what the wrapper's `capture()` would persist.
- G6 wrapper additions (`renderG6Network`): `camera()`, `viewport()`, `clampCamera(camera)` (the pan clamp the wrapper applies itself), `moveCamera(target, {delay, duration, onFrame, onStop})`, `returnCamera(timing)`, `finishTween(how)`, `tweening`, `lastTween`, `cameraMemory`, `whenTween()`. `capture()` returns the pre-auto-move camera while a tween runs or while the auto-moved camera is untouched, so an auto-moved camera is never stored in the camera store.
- Idle frame gate (`createFrameGate` in `frontend/g6-render.js`): AntV G re-renders every layer canvas on every animation frame even when nothing changed. Each wrapper hands its own gate to every G layer canvas of its graph as that canvas's `requestAnimationFrame` / `cancelAnimationFrame` (G `CanvasConfig`, passed through the G6 `canvas` option; per instance, no global is patched). After `IDLE_THROTTLE_MS` (1000 ms) without activity the layers' frame requests share one `SLOW_TICK_MS` (250 ms) timer (~4 ticks/s). Any `pointerdown` / `pointermove` / `pointerup` / `wheel` / `keydown` on the map container (capture phase), every wrapper operation (update queue, selection, camera, resize) and every G6 viewport transform wake it: the pending slow tick is cancelled and the waiting callbacks go onto a real animation frame at once. It never throttles before the graph is ready, in a hidden tab (native rAF semantics kept), or while `data-camera-state` / `data-motion-state` / `data-layout-state` / `data-update-state` is not idle, a camera tween or a G timeline animation runs. Destroy closes it. `AtlasDebug.getG6Stats().<view>` adds `idleThrottled` (the last frame request went to the slow timer), `slowTicks`, `wakes` (throttled -> full rate transitions caused by activity), `lastWakeMs` (wake -> first real frame; `null` until that frame ran) and `throttleLayers` (G layer canvases using the gate; 4 in G6 5.1.1).
- Collection map height (CSS only): in collections mode the map is `max(420px, 100vh - --atlas-map-chrome)` (250 px wide, 280 px at <= 900 px); from 901 px the cloud panel's max-height equals it; in the stacked layout an open cloud panel caps the map at `max(260px, 45vh)`. The collection-map wrapper (the one with an overlay) keeps its G canvases at the container size with a `ResizeObserver` (`getG6Stats().overview.containerResizes`): G keeps the camera centre on a resize, so the wrapper re-anchors the top-left (the content does not move), clamps the pan at once (a running tween clamps itself) and re-syncs the overlay from the actual camera in the same callback; no fit. The motion driver decides the cloud camera move against the new container size. In the stacked layout an open panel has `min-height: 55vh`, and opening it, a sub-group click or a panel paper click scrolls the page so the map row's top is at the viewport top (focus uses `preventScroll` there; `renderCloudPanel(..., {focusScroll})`); the wide layout is unchanged. The toolbar's 'N in viewport' (`#atlas-field-network[data-viewport-points]`) is recounted 120 ms after `data-camera-state` returns to idle. Legacy overview modes keep `clamp(320px, 42vw, 560px)`. Because a taller map raises the fit zoom, the collection map passes `fitMaxZoom = SUBGROUP_MIN_ZOOM - 0.1` (1.5) to the wrapper: the initial fit and the Fit button never zoom past it, so the initial view never shows sub-group outlines (a restored camera is applied as is).
- Cloud panel sub-items: `cloudPanelModel(...).subItems` keeps every row in `items` and adds `visibleIds` (the `PANEL_LIMITS.subItems` = 8 largest non-'other' rows in list order, then the 'other' rows 기타 / 직접 소속 last, uncounted) and `moreCount`. The panel renders the rest inside one `<details class="atlas-cloud-subitems-more">` (`하위 그룹 N개 더 보기` / `하위 컬렉션 N개 더 보기`) in the same section, open when it holds the selected row or was open before a re-render of the same cloud.
- Decoration elements carry their own classes (`atlas-motion-*`) and never the committed ones: line ghosts are `.atlas-motion-ghost` (no `data-edge-id`), the panel ghost is `.atlas-motion-clone[inert][aria-hidden="true"]` with every `id`, `tabindex`, `aria-live` and focus key removed. All are gone at idle.

### Additive taxonomy v2 snapshot blocks

The following blocks are optional and remain in the schemaVersion 1 envelope so older clients can continue to render `fields`, `memberships`, and `concepts`:

```json
{
  "facetCatalog": {
    "version": "facet-catalog-v4",
    "namespace": "atlas:<serverId>:<epoch>",
    "inputState": "valid-custom",
    "entities": [{
      "id": "facet:<serverId>:<epoch>:subject:bioinformatics",
      "facetId": "subject:bioinformatics",
      "ontologyKey": "subject:bioinformatics",
      "kind": "subject",
      "label": {"en": "Bioinformatics"},
      "broaderIds": [], "aliases": [], "origin": "builtin"
    }]
  },
  "paperFacets": [{
    "paperId": "zotero:<serverId>:<libraryId>:<key>",
    "directMemberships": [{"facetId": "facet:<serverId>:<epoch>:subject:bioinformatics", "ontologyKey": "subject:bioinformatics", "kind": "subject", "scoreKind": "evidence", "score": 0.9, "origin": "rule", "evidenceRef": {"sourceId": "<serverId>", "sourceEpoch": "<epoch>"}}],
    "primaryByFacet": {"subject": "facet:<serverId>:<epoch>:subject:bioinformatics"}
  }],
  "conceptQualityLedger": {"version": "concept-quality-v4", "entries": [{"id": "concept:<serverId>:<epoch>:x", "status": "suppressed", "reason": "context-free-boilerplate"}], "raw": [], "active": [], "suppressed": [], "protected": [], "user": [], "counts": {"raw": 0, "active": 0, "suppressed": 0, "protected": 0, "user": 0}},
  "fieldStats": {"version": "field-stats-v3", "paperCount": 0, "rows": [{"fieldId": "field:<serverId>:<epoch>:analysis", "direct": 0, "inclusive": 0, "primary": 0}], "unknown": 0, "unknownPaperIds": [], "knownPrimary": 0},
  "taxonomyState": {"status": "valid-custom", "sourceId": "<serverId>", "sourceEpoch": "<epoch>"},
  "taxonomyRedirects": {"field:<serverId>:<epoch>:old": "field:<serverId>:<epoch>:analysis"}
}
```

All facet references (`broaderIds`, `directMemberships[].facetId`, `primaryByFacet`, user `facetAssignments`/`primaryFacetOverrides`) are the canonical source/epoch scoped `entity.id`; `ontologyKey` (`<kind>:<slug>`) is the semantic key, and the write API also accepts a semantic key and persists the canonical `entity.id`. `facetId` on an entity is kept as a compatibility alias of `ontologyKey`. Custom facet IDs must start with `facet:<serverId>:<epoch>:`; entities fenced to another source are ignored at build time and rejected at write time. Legacy fields merge into a builtin entity only when the label **and** parent context match; same-label fields in a different context stay separate entities. Builtin entities may be parents of custom ones; `broaderIds` are same-kind, normalised to entity IDs before the cycle check, and acyclic.

`primaryByFacet` has at most one value per kind, is `null` on abstention (never a fake entity), and every non-null primary is also an accepted direct membership of that kind (user primary overrides are themselves accepted direct memberships, even when the classifier abstains). Evidence refs carry `field`, `evidenceTier`, `ruleId`, `spanRange` (offsets in the NFKC/casefold text) and scores; they never contain raw title/abstract/full-text spans. QIIME 2 evidence requires an explicit `QIIME 2`/`QIIME2`/`q2-*` token (QIIME 1 or unversioned QIIME never yields `tool:qiime2`).

Taxonomy input states: `absent`/`uninitialized` inherit the builtin hierarchy; `intentional-empty-valid` keeps the internal classifier catalog but publishes `fields=[]`, `memberships=[]`, `displayFieldId=null`; `valid-custom` merges custom hierarchy and facet config with the curated classifier; `invalid`/`disabled` never re-enable builtin rules: the last valid snapshot is served with `taxonomyState{status, reason, recovery}`.

`conceptQualityLedger.entries[]` resolve every raw concept ID (`status`, `reason`, `ruleVersion`, `protectedBy`, `termhood`, `mergedInto`). `raw`, `protected` and `user` are ownership buckets that intentionally overlap `active` (and `user` overlaps `suppressed` for user-excluded concepts); only active-vs-suppressed is exclusive, and suppressed IDs are never present in `concepts`. `fieldStats` is one object `{version, paperCount, rows[{fieldId, direct, inclusive, primary}], unknown, unknownPaperIds, knownPrimary, invariants}` computed from unique paper sets: `direct` = accepted direct memberships, `inclusive` = union over descendant fields, `primary` = known display primaries, with `knownPrimary + unknown = paperCount` and `primary <= direct <= inclusive`. Structured MSC codes (`subjectCodes`, or `MSC:`-keyed `Extra` lines) become protected `extra-msc` concepts; other `Extra` text is never mined or published. Redirects are source-scoped and acyclic.

Suppression `reason` values: `user-excluded`, `generated-metadata-tag`, `context-free-boilerplate`, `low-termhood`, `function-word`, `no-current-membership`, `merged-into-canonical`. `function-word` and part of `low-termhood` come from the library-derived lexical model (`taxonomy.lexical_model` / `lexical_verdict`, `concept-quality-v4`): for concept labels of one or two tokens that exist only because they were read from title/abstract/indexed text, the library's own statistics decide — residual IDF (a function word is spread evenly, a term is bursty), title affinity (one-sided binomial test against the library's title rate), ubiquity (>= 40% of texts) and adjacent-pair cohesion (normalised PMI). There is no curated word list in this step. Explicit Zotero tags/keywords, user-owned or pinned concepts, protected phrases and technical codes are never judged. Duplicate titles count once, and the model is not built below 50 distinct texts (the filter is then inactive).

`categoryColorRegistry{version,namespace,entries[]}` is append-only and persisted in the snapshot: later rebuilds of the same namespace reuse earlier entries byte-for-byte (across epochs only non-custom entries carry over). Theme keys are `<paletteId>:<light|dark>` for every palette id in `assets/palettes.json` (`atlas`, `calm`, `mocha`) plus `system`; builtin entries use the semantic `categoryKey`, custom entries the source-scoped one.

### Fencing, migration and rollback

`_enrich` atomically captures source/generation and the taxonomy, overlay and settings revisions (`SQLiteStore.snapshot_inputs`) *before* reading inputs or building, validates the result (`migration.validate_migration`) and publishes only if every captured revision still matches; otherwise the result is discarded and a rebuild from the latest inputs is queued.

App-owned records live under `<dataDir>/migrations/atlas-graph-v3/<epoch>/`:

* `<generation>.json` — the **immutable original backup**, written only for an actual recipe migration (`migration.is_recipe_migration`: the previous valid snapshot carries derived content without the v3 blocks, or another recipe stamp). Exact semantics: the backup is the **last valid pre-enrichment snapshot of the current source/epoch** — for a changed source that is the base snapshot published just before enrichment (the *current* active papers with the previously derived graph), not the raw pre-upgrade payload, so papers deleted from Zotero since are not resurrected by a rollback. The base publish carries over, for the same source/epoch only, the derived identity history (`taxonomyRedirects`, `taxonomyState`, fields/concepts/memberships/edges/ledger/registry), so old merged IDs keep resolving before, during and after enrichment and after a rollback, and `validate_migration` can detect a lost redirect. User-owned aliases and pins live in the overlay payload recorded in the receipt, not in the snapshot. It holds the previous full snapshot, the user's app-owned taxonomy/overlay payloads, the input revisions they were taken under, ID ledgers and hashes. It never contains what was published and is never overwritten.
* `<generation>.audit.json` — the append-only publication audit (≤50 attempts): one record per attempt with `outcome` (`published`, `discarded-stale`, `validation-failed`, `rolled-back`), the input fences, the attempted `afterHash`/counts and, for `published`, the real `snapshotRevision`. A stale first attempt is therefore recorded as discarded and the successful retry as published with its own fences and hash.
* Retention: `prune_migrations` keeps the newest 5 generations per epoch and the newest 3 epochs.

`SQLiteStore.rollback_migration(epoch, generation)` restores the original backup in one write transaction and is bound to the **recorded published snapshot revision** from the audit (a caller-supplied revision may only confirm it): a newer publish rejects it (`snapshot-revision-changed`); a changed source/generation (`source-changed`) rejects it; and because the backup would silently discard newer user state, any taxonomy/overlay/settings revision different from the revisions the backup was taken under refuses it (`taxonomy-changed`, `overlays-changed`, `config-changed`) — the fences are never relaxed to make a rollback succeed. There is no HTTP/CLI trigger for rollback.

### Classification and ledger rules added by review

* OT as an acronym needs a strong mathematical term (`transport*`, Wasserstein, Sinkhorn, Monge, Kantorovich, coupling, earth mover — never bare "optimal") and is blocked by `occupational therap*`/`ergotherap*`; in biomedical text two distinct strong terms are required. Explicit manual overrides always win. A bare `q2-<word>` (e.g. "Q2-2023") is not `tool:qiime2`; only named QIIME 2 plugins, or `q2-<letters>` together with the word "plugin", are.
* Ledger `mergedInto` is present only for a **global-safe** merge (every paper holding the concept was remapped, single target, `mergeScope: "global"`). A shared acronym merged only where its context matched (OT, KL) is reported as `contextMergedInto` with `mergeScope: "per-paper"` and `contextMergedPaperCount`; clients and `/taxonomy/resolve` must never follow it as a redirect.
* Parent cycles are detected iteratively (O(V+E), no recursion): writes reject a cycle (`400`) and more than 2000 entities (`400`, never truncated); a stored config that already contains a cycle is built with the cycle-closing edges dropped and surfaced in `facetCatalog.validationErrors` and `taxonomyState.reason`.
* Evidence collection prefilters rules with a literal-substring test (a necessary condition of each pattern) and caches per (catalog fingerprint, text part, text hash) within one build, so the graph's two passes scan an unchanged large text part once. Results are identical with the prefilter/cache disabled (tested).

### Gap-fill display subjects (taxonomy-v4)

`FALLBACK_SUBJECTS` (`subject:mathematics` general evidence, `genome-biology`, `protein-science`, `drug-discovery`, `statistics`, `computer-science`, `machine-learning`, `quantum-computing`, `medicine`) never enter `resolve_facets`: `directMemberships`, `primaryByFacet` and the gold contracts are those of the core rules. `gap_fill_subjects(papers, catalog, evidence, resolved)` runs only for papers whose subject primary is `null` and returns `{paperId: (entityId, score, ruleId)}` from gap evidence with score ≥ `PRIMARY_THRESHOLD`; a near tie resolves to the deepest common ancestor, else the top-ranked subject. The graph turns it into the display field and a membership with `method: "model"`, `scoreKind: "heuristic"`, `provenance: {provider: "term-rules", method: "gap-fill", label: "rule-based", ruleId}`. The gap subjects never block the broad Life Science fallback. A cached snapshot whose `facetCatalog.classifier.ruleVersion` differs from `RULE_VERSION` is re-derived. Field id of the genome subject is `genome-biology` because `genomics` is an alias of Bioinformatics.

### Broad-domain Life Science (ontology-label-policy-v2)

The existing `subject:life-science` is assigned by rule only for (a) a high-specificity biological compound (gene expression, protein folding, CRISPR, transcriptomics, mitochondria, microbiome/microbiota, microbial communities, …) in the title/tags — in the abstract alone it is a lower-confidence direct membership — or (b) at least two **independent** biological evidence families (molecular, cellular, organism, ecology, physiology) in the title/tags/abstract/indexed tiers. Full text alone is never enough; generic clinical/statistical words are not biology; look-alikes (genetic algorithm/programming, gene expression programming, evolutionary algorithms, cellular automata, neural networks, viral marketing, computer virus, cell phone, software ecosystem) are blanked before matching; no bare `virus`/`analysis` rule and no Mathematics fallback. It is withheld whenever a confident narrower subject exists (any Life Science descendant at the direct threshold, or any other subject at the primary threshold), so QIIME 2 development stays Bioinformatics, phage therapy using QIIME 2 stays Phage Biology, narrow mathematics wins, and a QIIME 2-only paper gets only the tool facet. Manual choices always win. Provenance: `evidenceRef.ruleId` is `broad-domain-compound` or `broad-domain-families` (with `families`), i.e. broad-domain evidence, never an invented Bioinformatics assignment; the membership is an ordinary accepted direct subject, so primary-in-direct and the direct/inclusive/primary/unknown count invariants hold (inclusive counts add the Bioinformatics/Phage descendants by unique paper). The catalog `classifier.ontologyPolicy` records the policy version.

Reference fixtures: `tests/fixtures/taxonomy_gold.json` (v1, hash `594c41cc…`) is archived byte-identical. `tests/fixtures/taxonomy_gold_v2.json` (hash `28daba23…`) is the versioned reference for this policy: the same 92 cases and splits with exactly four predeclared label corrections (g039, g040, g041, g070 → Life Science primary) plus 32 added broad-biology positives, near-neighbour negatives and narrow-wins cases, declared in `.omx/context/claude-v2-ontology-label-policy-v2.json` **before** the rule change. Metrics on v2 are synthetic conformance to a new ontology — not unchanged v1 accuracy, not blind held-out accuracy (added cases are not blind) and not private-corpus accuracy.

### Evidence spans (TAXV2-004) and keyword provenance

A stored span is **auditable without publishing text**: `evidenceRef` records the evidence `field`, the `evidenceTier`, codepoint `spanRange` offsets into the normalized field text, the `normalization` version (`nfkc-casefold-hyphen-v1`: title/abstract are whitespace-normalized, then NFKC + casefold + hyphen unification) and `inputHash` = `sha256:` of exactly that normalized field text. `evidenceSpan` stays `null`: raw text is never duplicated in the public snapshot, trace or logs. The local resolver `taxonomy.resolve_evidence_span(sourcePaper, ref)` re-normalizes the named field of the ORIGINAL source record and returns `text[start:end]` only if the hash matches; an edited field, another normalization version, another field or malformed offsets fail closed with `None`. The keyword field is bound to the original source tags only: `keywords` then `tags`, normalized by `source_keyword_items` (a string is one keyword, mappings use `tag`/`label`, reserved `auto-classifier:`/`auto-primary:` control tags are dropped), identically in the build and in the resolver. The classifier never reads the concept-extraction `keywords` list that the build later publishes, so abstract- or indexed-text-derived tokens cannot be promoted to keyword-tier confidence; the source rows are never modified.

### Local trace (TAXV2-036)

`atlas/trace.py` appends one JSON line per pipeline event to `<dataDir>/trace/atlas-trace.jsonl` (events: `build.start`, `build.complete`, `publish.ok`, `publish.stale`, `publish.validation_failed`, `publish.kept_previous`, `rollback.ok`, `rollback.rejected`). Records contain only fixed-enum event/reason keys, bounded non-negative integers (revisions, counts), recipe/rule/ontology-policy labels that must match a strict per-key grammar (`atlas-graph-vN`, `taxonomy-vN`, `ontology-label-policy-vN`; anything else is dropped), and truncated SHA-256 hashes of the source id and epoch. Reason enums cover abstention (`abstain.no_subject_evidence`, `abstain.below_primary_threshold`, `abstain.ambiguous`), classification status, concept suppression (`suppress.<ledger reason>`), stale-fence causes (`fence.taxonomy_changed`, `fence.overlays_changed`, `fence.config_changed`, `fence.source_or_generation_changed`) and rollback rejection. The sanitizer is a fail-closed whitelist: titles, abstracts, spans, paths, tokens, item/paper/concept IDs and arbitrary objects can never be recorded (unknown keys/values are dropped and counted in `dropped`). Retention is bounded (≤400 records and ≤256 KiB; the newest half is kept on trim), emission never raises, and there is no network sink or endpoint (`/api/v1/trace` is a 404).

### Pending (base-published) snapshots

Between a source change and the end of enrichment the served snapshot is the base snapshot (`modelStatus.state == "pending"`): it holds the CURRENT active papers with the previously derived graph. Per-paper rows follow the current papers (`paperFacets` rows of deleted papers are removed; new papers have no facet row until enrichment publishes) and `fieldStats` is recomputed from what the snapshot contains, so every pending full/detail view still passes strict schema and decoder validation. Redirect history and `taxonomyState` carry over for the same source/epoch only.

### Detail projection of v3 extensions

`GET /api/v1/snapshot?projection=detail&sample=N` projects the per-entity v3 extensions with the sampled papers/concepts/fields so strict decoding still passes: `paperFacets` rows follow the projected papers; ledger `active`/`protected`/`user`/`entries`/`raw` follow the projected concepts while all `suppressed` IDs stay resolvable; `fieldStats.rows` follow the projected fields. Both carry `projected: true`; their aggregate numbers (`counts`, `paperCount`, `unknown`, `knownPrimary`) always describe the FULL graph. Catalog-level data (`facetCatalog`, `categoryColorRegistry`, `taxonomyRedirects`) is returned whole and `overview` is independent of the sample.

D3 consumes `overview` for circular sectors/ribbons; Cytoscape consumes typed detail nodes/edges. Detail sampling is deterministic by `{source.epoch, filter, seed, fraction}` and retains selected nodes plus their mandatory one-hop neighborhood. Every view reports visible and hidden node/edge counts and preserves citation/semantic/keyword/field/related types.

## Palette assets

`assets/palettes.json` contains source OKLCH palettes and derived tokens. `assets/palette-sources.json` contains URLs, SPDX identifiers and notices. Rendering must provide deterministic sRGB fallbacks and light/dark/system variants; selected paths use halo/text cues in addition to color.
