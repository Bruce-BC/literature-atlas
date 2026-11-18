"""Verify the portable package contents, checksum manifest and determinism."""
from __future__ import annotations
import hashlib, json, subprocess, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMUNITY = ROOT / ".artifacts" / "community-plugin"
ARTIFACT = ROOT / ".artifacts" / "literature-atlas-plugin.zip"
MANIFEST = ROOT / ".artifacts" / "manifest.json"
if not ARTIFACT.is_file() or not MANIFEST.is_file(): raise SystemExit("package artifact or manifest is missing")
if {p.name for p in COMMUNITY.iterdir()} != {"main.js", "manifest.json", "styles.css", "SHA256SUMS"}: raise SystemExit("community release must contain exactly three assets plus SHA256SUMS")
community_manifest = json.loads((COMMUNITY / "manifest.json").read_text())
if community_manifest.get("id") != "literature-atlas" or community_manifest.get("version") != "0.1.0": raise SystemExit("community manifest identity/version mismatch")
community_main = (COMMUNITY / "main.js").read_text()
if 'const ATLAS_BUNDLED_RESOURCES = Object.freeze(' not in community_main: raise SystemExit("community main.js lacks lexical embedded resources")
if 'globalThis.ATLAS_BUNDLED_RESOURCES' in community_main: raise SystemExit("embedded resources must not be global mutable state")
meta = json.loads((ROOT / "dist" / "web-meta.json").read_text())
provenance = json.loads((ROOT / "assets" / "licenses" / "provenance.json").read_text())
for input_name in meta.get("inputs", {}):
    if "node_modules/" not in input_name: continue
    marker = input_name.split("node_modules/", 1)[1].split("/")
    package_name = "/".join(marker[:2]) if marker[0].startswith("@") else marker[0]
    package_root = ROOT / "node_modules" / package_name
    metadata = json.loads((package_root / "package.json").read_text())
    notice_marker = f"{package_name}@{metadata['version']} ({metadata['license']})"
    if notice_marker not in community_main: raise SystemExit(f"community main.js missing bundled package notice: {notice_marker}")
    license_path = next((package_root / name for name in ("LICENSE", "LICENSE.md", "LICENSE.txt") if (package_root / name).exists()), None)
    if license_path is None:
        reviewed = next((row for row in provenance if row["package"] == package_name and row["version"] == metadata["version"] and row["license"] == metadata["license"]), None)
        if reviewed is None: raise SystemExit(f"bundled package license provenance missing: {package_name}")
        license_path = ROOT / reviewed["vendoredPath"]
        if not license_path.exists() or hashlib.sha256(license_path.read_bytes()).hexdigest() != reviewed["licenseFileSHA256"]: raise SystemExit(f"bundled package vendored license hash mismatch: {package_name}")
    license_text = license_path.read_text()
    copyright_lines = [line.strip() for line in license_text.splitlines() if line.strip().lower().startswith("copyright")]
    if copyright_lines and not all(line in community_main for line in copyright_lines[:2]): raise SystemExit(f"community main.js missing copyright notice: {package_name}")
contract = json.loads((ROOT / "dist" / "community-plugin" / "resource-contract.json").read_text())
if contract.get("version") != 1 or not contract.get("files"): raise SystemExit("embedded resource contract is incomplete")
for row in contract["files"]:
    path = row.get("path", "")
    if not path or path.startswith("/") or ".." in Path(path).parts: raise SystemExit(f"unsafe embedded resource path: {path}")
    if len(row.get("sha256", "")) != 64: raise SystemExit(f"invalid embedded resource hash: {path}")
if contract["digest"] not in community_main: raise SystemExit("main.js resource digest mismatch")
for line in (COMMUNITY / "SHA256SUMS").read_text().splitlines():
    checksum, name = line.split("  ", 1)
    if hashlib.sha256((COMMUNITY / name).read_bytes()).hexdigest() != checksum: raise SystemExit(f"community checksum mismatch: {name}")
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
