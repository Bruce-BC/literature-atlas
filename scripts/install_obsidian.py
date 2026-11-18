"""Install the built companion into one explicitly selected Obsidian vault.

Only ``<vault>/.obsidian/plugins/<plugin-id>`` is touched. Existing plugin
files are copied to a timestamped sibling backup before replacement.
"""
from __future__ import annotations
import argparse, shutil, time
from pathlib import Path

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault", required=True, type=Path)
    parser.add_argument("--source", type=Path, default=Path("dist/obsidian-plugin"))
    parser.add_argument("--plugin-id", default="literature-atlas")
    args = parser.parse_args()
    vault = args.vault.expanduser().resolve()
    source = args.source.resolve()
    if not vault.is_dir(): raise SystemExit(f"vault does not exist: {vault}")
    if not source.is_dir() or not (source / "manifest.json").is_file(): raise SystemExit(f"built plugin is incomplete: {source}")
    if "/" in args.plugin_id or "\\" in args.plugin_id or args.plugin_id in {"", ".", ".."}: raise SystemExit("invalid plugin id")
    plugins = vault / ".obsidian" / "plugins"
    target = (plugins / args.plugin_id).resolve()
    if target.parent != plugins.resolve(): raise SystemExit("plugin target escaped vault")
    plugins.mkdir(parents=True, exist_ok=True)
    if target.exists():
        backup = plugins / f".{args.plugin_id}.backup-{int(time.time())}"
        shutil.copytree(target, backup)
        print(f"backup={backup}")
    # Replace only files owned by this plugin. Obsidian's data.json and any
    # user-created files in the plugin directory remain untouched.
    target.mkdir(parents=True, exist_ok=True)
    for name in ("main.js", "manifest.json", "styles.css", "bridge", "web"):
        owned = target / name
        if owned.is_dir(): shutil.rmtree(owned)
        elif owned.exists(): owned.unlink()
        source_item = source / name
        if source_item.is_dir(): shutil.copytree(source_item, owned)
        elif source_item.is_file(): shutil.copy2(source_item, owned)
    print(f"installed={target}")
    return 0

if __name__ == "__main__": raise SystemExit(main())
