"""Deterministic append-only category colour registry.

The registry is generated at the persistence boundary and consumed by the
frontend.  Semantic IDs are used for builtin facets; custom facets keep their
source-scoped IDs. Existing entries are copied byte-for-byte so adding,
renaming, or reordering categories never recolours a saved library.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from typing import Any

FAMILIES = ("blue", "teal", "gold", "violet", "rose", "green")
FAMILY_HUES = (258.75, 183.31, 71.37, 293.28, 15.51, 150.46)
PALETTE_HUE_OFFSETS = {"atlas": 0.0, "calm": 14.0, "mocha": -18.0}


def _hex(values: Iterable[float]) -> str:
    # JS Math.round semantics, unlike Python's bankers rounding, keep Python
    # and browser generated values byte-identical at half-byte boundaries.
    return "#" + "".join(f"{max(0, min(255, math.floor(float(value) + 0.5))):02x}" for value in values)


def oklch_to_hex(lightness: float, chroma: float, hue: float) -> str:
    """Convert OKLCH to sRGB, reducing chroma until it fits the gamut."""
    L = max(0.0, min(1.0, float(lightness))); angle = math.radians(float(hue)); C = max(0.0, float(chroma))

    def convert(chroma_value: float) -> tuple[list[float], list[float]]:
        a, b = chroma_value * math.cos(angle), chroma_value * math.sin(angle)
        l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
        m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
        s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3
        linear = [4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
                  -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
                  -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s]
        srgb = [12.92 * value if value <= 0.0031308 else 1.055 * max(0.0, value) ** (1 / 2.4) - 0.055 for value in linear]
        return linear, srgb

    _, srgb = convert(C)
    for _ in range(14):
        if all(0.0 <= value <= 1.0 for value in srgb):
            break
        C *= 0.9; _, srgb = convert(C)
    return _hex(value * 255 for value in srgb)


def _semantic_key(row: Mapping[str, Any]) -> str:
    """Stable registry key: semantic ID for builtin entities, scoped ID for custom ones."""
    if str(row.get("origin") or "").casefold() == "custom":
        return str(row.get("id") or row.get("legacyFieldId") or row.get("facetId") or row.get("ontologyKey"))
    return str(row.get("facetId") or row.get("ontologyKey") or row.get("categoryKey") or row.get("id"))


def _parents(row: Mapping[str, Any]) -> list[str]:
    return [str(x) for x in (row.get("parentIds") or row.get("broaderIds") or [])]


def build_category_color_registry(categories: Iterable[Mapping[str, Any]], *, existing: Mapping[str, Any] | None = None,
                                  namespace: str = "atlas", version: int = 1) -> dict[str, Any]:
    """Build or extend the ``categoryColorRegistry`` payload (append-only).

    Existing entries are returned untouched.  A fresh library follows the same
    formula as the frontend fallback (roots sorted by ID -> family; siblings
    sorted by ID within one parent set), so both implementations agree before
    any registry is persisted.  Later categories only append: new roots take
    the next free family slot and new siblings the next sibling index.
    """
    rows = [dict(row) for row in categories if isinstance(row, Mapping) and (row.get("id") or row.get("categoryKey") or row.get("facetId"))]
    by_id = {str(row["id"]): row for row in rows if row.get("id")}
    source = existing if isinstance(existing, Mapping) else {}
    entries = [dict(entry) for entry in source.get("entries", []) if isinstance(entry, Mapping)]
    by_key = {str(entry.get("categoryKey") or entry.get("ontologyKey")): entry for entry in entries}

    def root_of(row: Mapping[str, Any]) -> str:
        current, seen = row, set()
        while current and _parents(current) and str(current.get("id")) not in seen:
            seen.add(str(current.get("id")))
            nxt = by_id.get(_parents(current)[0])
            if nxt is None:
                break
            current = nxt
        return str(current.get("id") or _semantic_key(current))

    def depth_of(row: Mapping[str, Any]) -> int:
        depth, current, seen = 0, row, set()
        while _parents(current) and str(current.get("id")) not in seen:
            seen.add(str(current.get("id")))
            parent = by_id.get(_parents(current)[0])
            if parent is None:
                break
            depth += 1; current = parent
        return depth

    prior_entries = list(entries)          # sibling slots only append after pre-existing entries
    fresh = [row for row in rows if _semantic_key(row) not in by_key]
    fresh.sort(key=lambda row: str(row.get("id") or _semantic_key(row)))
    root_ids = sorted({root_of(row) for row in fresh})
    # Families already claimed by registered roots are never reassigned.
    known_roots = {str(e.get("categoryKey")): e for e in entries if int(e.get("depthVariant", 0)) == 0}
    used_slots = len({e.get("family") for e in entries if e.get("family") in FAMILIES}) if entries else 0
    next_slot = used_slots
    family_for_root: dict[str, str] = {}
    for root in root_ids:
        root_row = by_id.get(root, {})
        registered = by_key.get(_semantic_key(root_row)) if root_row else None
        if registered and registered.get("family") in FAMILIES:
            family_for_root[root] = str(registered["family"])
        else:
            family_for_root[root] = FAMILIES[next_slot % len(FAMILIES)]; next_slot += 1
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for row in fresh:
        groups.setdefault(json.dumps(_parents(row)), []).append(row)
    for row in fresh:
        key = _semantic_key(row)
        if key in by_key:
            continue
        family = family_for_root[root_of(row)]; family_index = FAMILIES.index(family)
        depth = depth_of(row)
        base = max((int(e.get("siblingIndex", -1)) for e in prior_entries if e.get("family") == family and int(e.get("depthVariant", 0)) == depth), default=-1) + 1
        sibling = base + [str(r.get("id")) for r in groups[json.dumps(_parents(row))]].index(str(row.get("id")))
        hue = (FAMILY_HUES[family_index] + sibling * 31.7 + depth * 8.5) % 360
        oklch_by_theme: dict[str, Any] = {}; hex_by_theme: dict[str, str] = {}
        for palette_id, offset in PALETTE_HUE_OFFSETS.items():
            for theme in ("light", "dark"):
                lightness, chroma = (0.72, 0.13) if theme == "dark" else (0.57, 0.145)
                palette_hue = (hue + offset) % 360
                color = oklch_to_hex(lightness, chroma, palette_hue)
                theme_key = f"{palette_id}:{theme}"
                oklch_by_theme[theme_key] = {"oklch": f"oklch({lightness:.4f} {chroma:.4f} {palette_hue:.2f})", "hex": color}; hex_by_theme[theme_key] = color
        oklch_by_theme["system"] = oklch_by_theme["atlas:light"]; hex_by_theme["system"] = hex_by_theme["atlas:light"]
        entry = {"categoryKey": key, "ontologyKey": str(row.get("ontologyKey") or key), "kind": str(row.get("kind") or "field"),
                 "family": family, "siblingIndex": sibling, "depthVariant": depth, "oklchByPaletteTheme": oklch_by_theme,
                 "hexByPaletteTheme": hex_by_theme, "origin": "custom" if str(row.get("origin")) == "custom" else "generated"}
        entries.append(entry); by_key[key] = entry
    return {"version": int(source.get("version", version)), "namespace": str(source.get("namespace") or namespace), "entries": entries}


__all__ = ["build_category_color_registry", "oklch_to_hex", "PALETTE_HUE_OFFSETS"]
