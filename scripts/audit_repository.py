"""Fail-closed privacy audit for source, generated bundles, archives and history."""
from pathlib import Path
import re, struct, subprocess, sys, zipfile, zlib

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = [
    re.compile("zotero" + r"\.sqlite", re.I),
    re.compile(r"\." + r"zotero(?:/|\\)", re.I),
    re.compile("Bearer" + r"\s+[A-Za-z0-9._-]{12,}"),
    re.compile("/" + r"Users/[^/\s]+/"),
    re.compile("/" + "private/var/folders/", re.I),
]
EXCLUDE_PARTS = {".git", "node_modules", ".cache", ".venv", "__pycache__"}
FORBIDDEN_PATH_PARTS = {".omx", ".local", ".zotero"}
ALLOWED_LITERAL_MATCHES = {
    "tests/fixtures/privacy_gate_first_push.json": {"zotero" + ".sqlite"},
    "tests/plugin/plugin.test.mjs": {"/" + "Users/secret/", "Bearer " + "secret-token"},
    "tests/fixtures/plugin_takeover_and_token_leak.json": {"Bearer " + "synthetic-token-never-persist"},
}
KNOWN_PNG_PATHS = {
    ".artifacts/browser-screenshots/viewport-320.png",
    ".artifacts/browser-screenshots/viewport-736.png",
    ".artifacts/browser-screenshots/viewport-1024.png",
    *{
        f".artifacts/visual-audit/{prefix}{palette}-{theme}.png"
        for prefix in ("", "r2-", "r3-")
        for palette in ("320-atlas", "320-calm", "320-mocha", "1440-atlas", "1440-calm", "1440-mocha")
        for theme in ("light", "dark")
    },
    ".artifacts/visual-audit/320-selected.png",
    ".artifacts/visual-audit/1440-selected.png",
    ".artifacts/visual-audit/r2-320-selected.png",
    ".artifacts/visual-audit/r2-1440-selected.png",
    ".artifacts/visual-audit/r3-320-selected.png",
    ".artifacts/visual-audit/r3-1440-selected.png",
    ".artifacts/g6-dark.png",
    ".artifacts/g6-light.png",
}
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_TEXT_CHUNKS = {b"tEXt", b"zTXt", b"iTXt"}

def text_matches(label: str, text: str, errors: list[str]) -> None:
    path_label = label
    if label.startswith("history:"):
        _, _, path_label = label.partition(":")
        _, _, path_label = path_label.partition(":")
    allowed = ALLOWED_LITERAL_MATCHES.get(path_label, set())
    for pattern in FORBIDDEN:
        if any(match.group(0) not in allowed for match in pattern.finditer(text)):
            errors.append(f"{label} matches {pattern.pattern}")


def is_known_png_path(label: str) -> bool:
    return label in KNOWN_PNG_PATHS


def _png_text(label: str, chunk_type: bytes, data: bytes, errors: list[str]) -> None:
    try:
        if chunk_type == b"tEXt":
            _, value = data.split(b"\x00", 1)
            text_matches(label, value.decode("latin-1"), errors)
        elif chunk_type == b"zTXt":
            _, payload = data.split(b"\x00", 1)
            if len(payload) < 2 or payload[0] != 0:
                raise ValueError("invalid zTXt compression header")
            text_matches(label, zlib.decompress(payload[1:]).decode("latin-1"), errors)
        else:
            _, rest = data.split(b"\x00", 1)
            if len(rest) < 2:
                raise ValueError("invalid iTXt fields")
            compressed, method = rest[:2]
            if compressed not in {0, 1}:
                raise ValueError("invalid iTXt compression flag")
            rest = rest[2:]
            _, rest = rest.split(b"\x00", 1)
            _, value = rest.split(b"\x00", 1)
            if compressed == 1:
                if method != 0:
                    raise ValueError("invalid iTXt compressed text")
                value = zlib.decompress(value)
            text_matches(label, value.decode("utf-8"), errors)
    except (UnicodeDecodeError, ValueError, zlib.error) as exc:
        errors.append(f"{label} has invalid {chunk_type.decode('ascii', errors='replace')} metadata: {exc}")


def validate_png_bytes(label: str, data: bytes, errors: list[str]) -> None:
    if not data.startswith(PNG_SIGNATURE):
        errors.append(f"{label} is not a PNG")
        return
    offset = len(PNG_SIGNATURE)
    seen_header = seen_data = seen_end = False
    idat_payloads: list[bytes] = []
    while offset < len(data):
        if len(data) - offset < 12:
            errors.append(f"{label} has a truncated PNG chunk")
            return
        length = struct.unpack_from(">I", data, offset)[0]
        chunk_type = data[offset + 4:offset + 8]
        end = offset + 12 + length
        if end > len(data) or not all(65 <= byte <= 122 and not 91 <= byte <= 96 for byte in chunk_type):
            errors.append(f"{label} has an invalid PNG chunk")
            return
        payload = data[offset + 8:offset + 8 + length]
        crc = struct.unpack_from(">I", data, offset + 8 + length)[0]
        if zlib.crc32(chunk_type + payload) & 0xFFFFFFFF != crc:
            errors.append(f"{label} has an invalid PNG CRC")
            return
        if not seen_header:
            if chunk_type != b"IHDR" or length != 13:
                errors.append(f"{label} is missing a leading IHDR")
                return
            width, height, depth, color_type, compression, filter_method, interlace = struct.unpack(">IIBBBBB", payload)
            valid_depths = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}
            if not width or not height or width > 10000 or height > 10000 or depth not in valid_depths.get(color_type, set()) or compression != 0 or filter_method != 0 or interlace not in {0, 1}:
                errors.append(f"{label} has invalid PNG dimensions or IHDR fields")
                return
            seen_header = True
        elif chunk_type == b"IHDR":
            errors.append(f"{label} has duplicate IHDR")
            return
        if chunk_type in PNG_TEXT_CHUNKS:
            _png_text(label, chunk_type, payload, errors)
        if chunk_type == b"IDAT":
            seen_data = True
            idat_payloads.append(payload)
        if chunk_type == b"IEND":
            if length != 0 or seen_end or not seen_data or end != len(data):
                errors.append(f"{label} has an invalid IEND")
                return
            seen_end = True
        offset = end
    if not seen_data or not seen_end:
        errors.append(f"{label} is missing PNG image data or IEND")
    else:
        try:
            zlib.decompress(b"".join(idat_payloads))
        except zlib.error as exc:
            errors.append(f"{label} has invalid PNG image data: {exc}")

def scan_file(path: Path, errors: list[str]) -> None:
    rel = path.relative_to(ROOT).as_posix()
    parts = set(path.relative_to(ROOT).parts)
    if any(part in EXCLUDE_PARTS for part in parts): return
    if parts & FORBIDDEN_PATH_PARTS: errors.append(f"{rel} is a forbidden private-state path"); return
    if path.suffix.lower() == ".pdf": errors.append(f"{rel} is a PDF; release source must not contain corpus PDFs")
    if path.suffix.lower() == ".png":
        if not is_known_png_path(rel):
            errors.append(f"{rel} is an unapproved PNG artifact")
        else:
            try: validate_png_bytes(rel, path.read_bytes(), errors)
            except OSError as exc: errors.append(f"{rel} cannot be read: {exc}")
        return
    if path.suffix.lower() in {".pyc", ".zip"}: return
    try: text_matches(rel, path.read_text(encoding="utf-8", errors="strict"), errors)
    except UnicodeDecodeError:
        errors.append(f"{rel} is not UTF-8 text; privacy scan cannot complete")
    except OSError as exc:
        errors.append(f"{rel} cannot be read: {exc}")

def scan_repository() -> list[str]:
    errors: list[str] = []
    for top in ("atlas", "frontend", "obsidian-plugin", "docs", "tests", "scripts", "assets", "dist", ".artifacts", ".github"):
        base = ROOT / top
        if base.exists():
            for path in base.rglob("*"):
                if path.is_file(): scan_file(path, errors)
    for name in ("README.md", "AGENTS.md", "LICENSE", "package.json", "package-lock.json", "pyproject.toml", ".gitignore"):
        path = ROOT / name
        if path.is_file(): scan_file(path, errors)
    for archive in (ROOT / ".artifacts").glob("*.zip") if (ROOT / ".artifacts").exists() else []:
        try:
            with zipfile.ZipFile(archive) as z:
                for info in z.infolist():
                    if info.filename.lower().endswith(".pdf"): errors.append(f"{archive.name}:{info.filename} is a PDF")
                    try: text_matches(f"{archive.name}:{info.filename}", z.read(info).decode("utf-8"), errors)
                    except UnicodeDecodeError: errors.append(f"{archive.name}:{info.filename} is not UTF-8 text; privacy scan cannot complete")
        except (OSError, zipfile.BadZipFile) as exc: errors.append(f"cannot inspect {archive.name}: {exc}")

    # Scan committed history too; an empty repository has no objects and is safe.
    try:
        commits = subprocess.check_output(["git", "rev-list", "--all"], cwd=ROOT, text=True).split()
    except (subprocess.CalledProcessError, OSError) as exc:
        errors.append(f"cannot enumerate Git history: {exc}")
        commits = []
    for commit in commits:
        try:
            names = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", commit], cwd=ROOT, text=True).splitlines()
        except (subprocess.CalledProcessError, OSError) as exc:
            errors.append(f"cannot inspect Git tree {commit[:12]}: {exc}")
            continue
        for name in names:
            if any(part in EXCLUDE_PARTS for part in Path(name).parts): continue
            if set(Path(name).parts) & FORBIDDEN_PATH_PARTS:
                errors.append(f"history:{commit[:12]}:{name} is a forbidden private-state path"); continue
            try:
                blob = subprocess.check_output(["git", "show", f"{commit}:{name}"], cwd=ROOT, stderr=subprocess.DEVNULL)
                text_matches(f"history:{commit[:12]}:{name}", blob.decode("utf-8"), errors)
            except (subprocess.CalledProcessError, OSError) as exc:
                errors.append(f"cannot read Git blob history:{commit[:12]}:{name}: {exc}")
            except UnicodeDecodeError:
                errors.append(f"history:{commit[:12]}:{name} is not UTF-8 text; privacy scan cannot complete")
            if name.lower().endswith(".pdf"): errors.append(f"history:{commit[:12]}:{name} is a PDF")
    return errors

def main() -> int:
    errors = scan_repository()
    if errors:
        print("privacy audit failed")
        print("\n".join(sorted(set(errors))))
        return 1
    print("privacy audit passed (source, generated assets, archives and history scanned)")
    return 0


if __name__ == "__main__": sys.exit(main())
