# Implementation interfaces (v1)

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
| GET/POST/PUT | `/api/v1/taxonomy` | source-scoped user fields/concepts; parent-cycle and patch-key validation |
| GET/PUT | `/api/v1/overlays` | source-scoped manual aliases/memberships/pins with `If-Match` CAS |
| GET | `/api/v1/papers/:id` | current paper/detail record |
| GET | `/api/v1/attachments/:id/url` | only a currently registered Zotero `http(s)`/`zotero` target |

The bridge accepts only loopback requests, validates the bearer in constant time, bounds bodies/responses, and never serves arbitrary filesystem paths. Metadata and attachment/full-text clocks are separate. A sync cursor or tombstone is committed only after complete unfiltered `includeTrashed=1` maps, exact payload coverage, collection fence and a second source fence succeed. Standalone attachments are represented with `parentId: null` and `standalone: true`; every unparented record in a user's personal library is retained and never joined to a paper by filename. Live smoke counts are redacted and kept outside Git; repository fixtures use only synthetic counts.

## Obsidian plugin boundary

The plugin owns only its child process. It stores the child handle, PID/start time and launch nonce, starts one watcher on load/reconnect, and stops/restarts only that handle. The panel receives the token and launch nonce through one in-memory `postMessage` to the exact loopback origin; tokens are absent from URLs, notes, logs and persisted files. A managed export uses a stable hashed ID filename and writes only the explicitly owned directory/marker.

## Frontend boundary

`window.AtlasSnapshotDecoder` must validate required IDs and treat unknown fields as forward-compatible. D3 consumes `overview` for circular sectors/ribbons; Cytoscape consumes typed detail nodes/edges. Detail sampling is deterministic by `{source.epoch, filter, seed, fraction}` and retains selected nodes plus their mandatory one-hop neighborhood. Every view reports visible and hidden node/edge counts and preserves citation/semantic/keyword/field/related types.

## Palette assets

`assets/palettes.json` contains source OKLCH palettes and derived tokens. `assets/palette-sources.json` contains URLs, SPDX identifiers and notices. Rendering must provide deterministic sRGB fallbacks and light/dark/system variants; selected paths use halo/text cues in addition to color.
