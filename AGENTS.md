# Literature Atlas contributor contract

- Preserve the accepted real Zotero/Obsidian scope in docs/architecture.md and docs/verification.md. Synthetic demo alone is not delivery.
- Zotero is read-only. Never change source metadata, files, filenames or attachments. Never write Zotero SQLite.
- Source DB identity, epoch and item generation fence all jobs. Never delete from a failed/partial source snapshot or remap overlays across different source DBs.
- Full graph aggregates are independent of detail subsampling; citations, semantic similarity and keyword cooccurrence stay distinct.
- All user metadata is text, never untrusted HTML. Loopback-only authenticated API, exact-origin memory-only plugin handshake, no arbitrary file/URL read.
- Keep real library data, PDF content, vault paths, tokens, runtime/model caches and .omx/ out of Git and release artifacts.
- Respect lane ownership. Do not revert another contributor's work. Coordinate schema/interface changes through the main integration lane.
- Run targeted tests, then the relevant build/lint/browser checks. Report failures and limitations plainly; do not replace real inference/host checks with mocks.
