# Architecture

Literature Atlas is a local read-only projection of Zotero, served by a Python 3.11+ loopback bridge and embedded in an Obsidian desktop ItemView. SQLite stores source partitions, overlays, tombstones and derived graph state. D3 renders circular field relations; Cytoscape renders papers and concepts. Real e5 local embeddings and explicitly labelled offline providers are separate.

The versioned public contract is `snapshot.schema.json` and `implementation-interfaces.md`. Source IDs are Zotero database IDs, not temporary process IDs. Same-DB restarts retain identity/epoch. Different DBs never inherit overlays by coincidentally matching item keys. A complete stable double-fenced source snapshot is committed atomically; derived work is guarded by source/epoch/generation. Metadata/fulltext clocks are separate.

The default circular allocation is one display field per paper; additional memberships remain available without duplicating the global total. Detail sampling is deterministic, stratified and selection-preserving. It never changes full overview aggregates. Evidence type and source remain visible; semantic edges are never citations.

The plugin owns only its spawned child, uses a memory-only exact-origin postMessage bootstrap and validates PID/launch nonce. Tokens never enter URLs or notes. It writes only explicitly managed exports; handwritten content is preserved. No private corpus, PDF, local path or token is part of this repository.
