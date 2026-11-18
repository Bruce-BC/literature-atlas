"""Check pinned palette source metadata, optionally fetching every raw URL."""
from __future__ import annotations
import argparse, json, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
data = json.loads((ROOT / "assets/palette-sources.json").read_text())
parser = argparse.ArgumentParser(); parser.add_argument("--online", action="store_true"); args = parser.parse_args()
assert len(data.get("families", [])) == 3
for family in data["families"]:
    assert family["license"] == "MIT" and "MIT License" in family["licenseText"]
    assert len(family["commit"]) == 40 and family["sourceURLs"]
    if args.online:
        for url in family["sourceURLs"]:
            result = subprocess.run(["curl", "-fsSL", "--max-time", "20", url], capture_output=True, text=True)
            if result.returncode: raise SystemExit(f"source fetch failed: {url}: {result.stderr.strip()}")
            if url.endswith("LICENSE") and "MIT" not in result.stdout: raise SystemExit(f"MIT notice missing: {url}")
print(json.dumps({"families": len(data["families"]), "online": args.online, "license": "MIT", "status": "passed"}))
