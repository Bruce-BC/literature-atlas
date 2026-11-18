# Security boundary

Literature Atlas is a desktop-only, local-first Obsidian plugin. The community release contains exactly `main.js`, `manifest.json`, and `styles.css`; `main.js` embeds the public bridge and web assets and extracts only verified, versioned resources into a plugin-owned runtime directory.

The bridge uses a subprocess running a preinstalled Python 3.11+ interpreter, listens on loopback, and reads Zotero through the local read-only API. It never writes Zotero metadata, SQLite, PDFs, filenames, or handwritten notes. Tokens remain in memory and are not placed in URLs or logs. The plugin does not install Python, download executable code, self-update, or fetch runtime code from the network.

OpenAlex DOI metadata requests are opt-in enrichment. E5 inference requires the user to preinstall the optional AI dependencies and pre-cache model assets; the plugin never downloads model code or weights and never runs pip. It remains separate from the local fake-vector test provider. Report suspected issues with a minimal reproduction that excludes library records, PDFs, vault paths, tokens, and model caches.
