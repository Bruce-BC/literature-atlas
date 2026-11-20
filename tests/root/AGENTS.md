<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-11-19 | Updated: 2026-11-19 -->

# tests/root

## Purpose
Repository-level pytest checks for release tooling: privacy audit scanner, PNG validation, vault installer state preservation, and the built three-file community plugin artifact.

## Key Files
| File | Description |
|------|-------------|
| `test_community_package.py` | Built community plugin is three-file compatible; artifact installs exactly three assets and preserves vault state (2 tests). |
| `test_release_scripts.py` | Privacy allowances, installer vault-state preservation, PNG structure/metadata boundary, fail-closed unknown binaries (4 tests; uses `scripts.audit_repository`). |

## For AI Agents

### Working In This Directory
- Do not weaken privacy-audit patterns; add cases for new forbidden patterns.
- Tests import `scripts.*`, so run pytest from the repo root.

### Testing Requirements
```sh
python3 -m pytest -q tests/root
npm run audit:privacy
```

### Common Patterns
- `tmp_path` vaults, `subprocess` calls with `sys.executable`; synthetic byte blobs for PNG checks. Community tests need built/packaged output (`npm run build && npm run package`).

## Dependencies

### Internal
- `scripts/audit_repository.py`, `scripts/package.mjs`, `scripts/package_check.py`, `obsidian-plugin/manifest.json`.

### External
- pytest, Node (for packaging scripts).

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
