import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def test_built_community_plugin_is_three_file_compatible() -> None:
    release = ROOT / "dist" / "community-plugin"
    assert {path.name for path in release.iterdir() if path.is_file()} == {"main.js", "manifest.json", "styles.css", "resource-contract.json"}
    manifest = json.loads((release / "manifest.json").read_text())
    assert manifest["id"] == "literature-atlas"
    assert manifest["version"] == "0.1.0"
    assert manifest["minAppVersion"] == "1.15.1"
    assert manifest["isDesktopOnly"] is True
    main = (release / "main.js").read_text()
    assert "const ATLAS_BUNDLED_RESOURCES = Object.freeze(" in main
    for marker in ("@antv/g6@5.1.1 (MIT)", "cytoscape@3.35.0 (MIT)", "d3@7.9.2 (ISC)"):
        assert marker in main
    contract = json.loads((release / "resource-contract.json").read_text())
    assert contract["version"] == 1 and contract["files"]
    assert hashlib.sha256(json.dumps([{k: row[k] for k in ("path", "sha256")} for row in contract["files"]], separators=(",", ":")).encode()).hexdigest() == contract["digest"]
    assert all("/" not in row["path"] or not row["path"].startswith("/") for row in contract["files"])


def test_artifact_installs_exact_three_assets_and_preserves_vault_state(tmp_path: Path) -> None:
    artifact = ROOT / ".artifacts" / "community-plugin"
    assert {path.name for path in artifact.iterdir() if path.is_file()} == {"main.js", "manifest.json", "styles.css", "SHA256SUMS"}
    vault = tmp_path / "vault"
    target = vault / ".obsidian" / "plugins" / "literature-atlas"
    target.mkdir(parents=True)
    (target / "data.json").write_text('{"palette":"mocha"}\n', encoding="utf-8")
    (target / ".local-cache").write_text("keep\n", encoding="utf-8")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "install_obsidian.py"), "--vault", str(vault), "--source", str(artifact)], cwd=ROOT, check=True, capture_output=True, text=True)
    assert {path.name for path in target.iterdir()} == {"main.js", "manifest.json", "styles.css", "data.json", ".local-cache"}
    assert (target / "data.json").read_text(encoding="utf-8") == '{"palette":"mocha"}\n'
    assert (target / ".local-cache").read_text(encoding="utf-8") == "keep\n"
    assert (target / "main.js").stat().st_size > 100_000
