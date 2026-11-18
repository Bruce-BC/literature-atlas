import subprocess
import sys
import struct
import zlib
from pathlib import Path

from scripts.audit_repository import ROOT, scan_file, text_matches, validate_png_bytes


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def tiny_png(*metadata: bytes) -> bytes:
    header = png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    pixels = png_chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
    return b"\x89PNG\r\n\x1a\n" + header + b"".join(metadata) + pixels + png_chunk(b"IEND", b"")


def test_privacy_allowances_are_exact_and_path_scoped() -> None:
    errors: list[str] = []
    text_matches(
        "tests/plugin/plugin.test.mjs",
        "file://" + "/" + "Users/secret/paper.pdf\nAuthorization: " + "Bearer " + "secret-token",
        errors,
    )
    assert errors == []

    text_matches(
        "tests/plugin/plugin.test.mjs",
        "file://" + "/" + "Users/secret/paper.pdf\n" + "/" + "Users/private-vault/notes/paper.pdf",
        errors,
    )
    assert len(errors) == 1 and "Users" in errors[0]

    errors = []
    text_matches("tests/fixtures/snapshot.json", "Bearer " + "private-token-value", errors)
    assert len(errors) == 1 and "Bearer" in errors[0]


def test_installer_preserves_unowned_vault_state(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    target = vault / ".obsidian" / "plugins" / "literature-atlas"
    source = tmp_path / "dist" / "obsidian-plugin"
    target.mkdir(parents=True)
    (target / ".local").mkdir()
    (target / "data.json").write_text('{"userSetting":true}\n', encoding="utf-8")
    (target / ".local" / "cache.json").write_text("keep-local\n", encoding="utf-8")
    (target / "handwritten.md").write_text("# Keep this note\n", encoding="utf-8")
    (target / "main.js").write_text("old\n", encoding="utf-8")

    (source / "bridge").mkdir(parents=True)
    (source / "web").mkdir(parents=True)
    (source / "manifest.json").write_text('{"id":"literature-atlas"}\n', encoding="utf-8")
    (source / "main.js").write_text("new\n", encoding="utf-8")
    (source / "styles.css").write_text("body {}\n", encoding="utf-8")
    (source / "bridge" / "atlas.py").write_text("# bridge\n", encoding="utf-8")
    (source / "web" / "index.html").write_text("<!-- web -->\n", encoding="utf-8")

    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "install_obsidian.py"), "--vault", str(vault), "--source", str(source)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert (target / "data.json").read_text(encoding="utf-8") == '{"userSetting":true}\n'
    assert (target / ".local" / "cache.json").read_text(encoding="utf-8") == "keep-local\n"
    assert (target / "handwritten.md").read_text(encoding="utf-8") == "# Keep this note\n"
    assert (target / "main.js").read_text(encoding="utf-8") == "new\n"
    assert list(target.parent.glob(".literature-atlas.backup-*"))


def test_known_png_structure_and_metadata_boundary() -> None:
    valid = tiny_png(png_chunk(b"tEXt", b"Comment\x00synthetic browser screenshot"))
    errors: list[str] = []
    validate_png_bytes(".artifacts/browser-screenshots/viewport-320.png", valid, errors)
    assert errors == []

    private_path = b"/" + b"Users/private-vault/notes/paper.pdf"
    bad_metadata = tiny_png(png_chunk(b"tEXt", b"Comment\x00" + private_path))
    errors = []
    validate_png_bytes(".artifacts/browser-screenshots/viewport-320.png", bad_metadata, errors)
    assert len(errors) == 1 and "Users" in errors[0]

    compressed = zlib.compress(b"Bearer " + b"private-token-value")
    bad_compressed = tiny_png(png_chunk(b"zTXt", b"Comment\x00\x00" + compressed))
    errors = []
    validate_png_bytes(".artifacts/browser-screenshots/viewport-320.png", bad_compressed, errors)
    assert len(errors) == 1 and "Bearer" in errors[0]

    bad_international = tiny_png(png_chunk(b"iTXt", b"Comment\x00\x00\x00\x00\x00" + b"Bearer " + b"private-token-value"))
    errors = []
    validate_png_bytes(".artifacts/browser-screenshots/viewport-320.png", bad_international, errors)
    assert len(errors) == 1 and "Bearer" in errors[0]

    invalid = tiny_png()[:-12]
    errors = []
    validate_png_bytes(".artifacts/browser-screenshots/viewport-320.png", invalid, errors)
    assert errors


def test_unknown_binary_and_unrelated_text_remain_fail_closed() -> None:
    unknown_binary = ROOT / ".artifacts" / "r2-test-unknown.bin"
    unknown_binary.write_bytes(b"\x00\xff\x01")
    try:
        errors: list[str] = []
        scan_file(unknown_binary, errors)
        assert errors
    finally:
        unknown_binary.unlink(missing_ok=True)

    unknown_png = ROOT / ".artifacts" / "r2-test-unknown.png"
    unknown_png.write_bytes(tiny_png())
    try:
        errors = []
        scan_file(unknown_png, errors)
        assert errors == [".artifacts/r2-test-unknown.png is an unapproved PNG artifact"]
    finally:
        unknown_png.unlink(missing_ok=True)

    unrelated_text = ROOT / ".artifacts" / "r2-test-unrelated.txt"
    unrelated_text.write_text("zotero" + ".sqlite\n", encoding="utf-8")
    try:
        errors = []
        scan_file(unrelated_text, errors)
        assert len(errors) == 1 and "sqlite" in errors[0]
    finally:
        unrelated_text.unlink(missing_ok=True)
