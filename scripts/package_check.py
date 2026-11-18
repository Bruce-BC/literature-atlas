"""Verify the portable package contents, checksum manifest and determinism."""
from __future__ import annotations
import hashlib, json, subprocess, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / ".artifacts" / "literature-atlas-plugin.zip"
MANIFEST = ROOT / ".artifacts" / "manifest.json"
if not ARTIFACT.is_file() or not MANIFEST.is_file(): raise SystemExit("package artifact or manifest is missing")
def digest(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()
before = digest(ARTIFACT)
subprocess.run(["node", "scripts/package.mjs"], cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
after = digest(ARTIFACT)
if before != after: raise SystemExit("package archive is not deterministic")
manifest = json.loads(MANIFEST.read_text())
if manifest.get("archive", {}).get("sha256") != after: raise SystemExit("manifest archive checksum does not match ZIP")
required = {"main.js", "manifest.json", "styles.css", "bridge/atlas/__main__.py", "web/index.html", "web/app.js", "web/styles.css", "web/assets/palettes.json", "LICENSE", "README.md", "THIRD_PARTY_NOTICES/palette-sources.json", "THIRD_PARTY_NOTICES/palette-source-verification.json", "THIRD_PARTY_NOTICES/THIRD_PARTY_NOTICES.md"}
forbidden = (".zotero", "zotero" + ".sqlite", ".omx/", ".pdf", ".safetensors", ".local/")
with zipfile.ZipFile(ARTIFACT) as archive:
    names = set(archive.namelist())
    if not required <= names: raise SystemExit(f"package missing required members: {sorted(required - names)}")
    if any(any(token in name.lower() for token in forbidden) for name in names): raise SystemExit("package contains forbidden private/corpus member")
    for info in archive.infolist():
        if info.date_time != (1980, 1, 1, 0, 0, 0): raise SystemExit("package member timestamp is not normalized")
print(json.dumps({"archive": str(ARTIFACT.relative_to(ROOT)), "sha256": after, "members": len(names), "deterministic": True}))
