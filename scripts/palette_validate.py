"""Offline OKLCH/sRGB and contrast validation for checked-in palette assets."""
from __future__ import annotations
import json, math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
def lin(x): return x / 12.92 if x <= .04045 else ((x + .055) / 1.055) ** 2.4
def rgb(h): return tuple(int(h[i:i+2], 16) / 255 for i in (1, 3, 5))
def luminance(h):
    r, g, b = map(lin, rgb(h)); return .2126*r + .7152*g + .0722*b
def contrast(a, b):
    x, y = sorted((luminance(a), luminance(b))); return (y + .05) / (x + .05)
def lch_to_rgb(L, C, h):
    a, b = C * math.cos(math.radians(h)), C * math.sin(math.radians(h))
    l, m, s = (L + .3963377774*a + .2158037573*b)**3, (L - .1055613458*a - .0638541728*b)**3, (L - .0894841775*a - 1.2914855480*b)**3
    return (4.0767416621*l - 3.3077115913*m + .2309699292*s, -1.2684380046*l + 2.6097574011*m - .3413193965*s, -.0041960863*l - .7034186147*m + 1.7076147010*s)
def srgb(v):
    out = []
    for x in v:
        x = max(0, min(1, x)); out.append(12.92*x if x <= .0031308 else 1.055*x**(1/2.4)-.055)
    return '#' + ''.join(f'{round(x*255):02x}' for x in out)
def parse_oklch(value):
    parts = value.removeprefix('oklch(').removesuffix(')').split()
    return float(parts[0]), float(parts[1]), float(parts[2])

palettes = json.loads((ROOT / 'assets/palettes.json').read_text())
sources = json.loads((ROOT / 'assets/palette-sources.json').read_text())
assert len(palettes['palettes']) == 3 and {p['id'] for p in palettes['palettes']} == {'atlas', 'calm', 'mocha'}
assert len(sources['families']) == 3 and all(f['license'] == 'MIT' and f['licenseText'] for f in sources['families'])
checks = 0; min_text = 99.0; min_series = 99.0
for palette in palettes['palettes']:
    assert set(palette['themes']) == {'light', 'dark'}
    for theme in palette['themes'].values():
        assert all(theme[k].startswith('#') and len(theme[k]) == 7 for k in ('bg', 'panel', 'text', 'muted', 'border', 'focus'))
        for surface in ('bg', 'panel'):
            for role in ('text', 'muted'):
                ratio = contrast(theme[role], theme[surface]); min_text = min(min_text, ratio); checks += 1; assert ratio >= 4.5
            for series in theme['series']:
                ratio = contrast(series['hex'], theme[surface]); min_series = min(min_series, ratio); checks += 1; assert ratio >= 3
                L, C, h = parse_oklch(series['oklch']); assert all(-1e-6 <= x <= 1 + 1e-6 for x in lch_to_rgb(L, C, h))
                assert srgb(lch_to_rgb(L, C, h)) == series['hex']
assert abs(contrast('#000000', '#ffffff') - 21) < 1e-9
assert srgb(lch_to_rgb(.62795536, .25768331, 29.233885)) == '#ff0000'
print(json.dumps({'palettes': 3, 'themes': 6, 'opaqueContrastChecks': checks, 'minimumTextContrast': min_text, 'minimumSeriesContrast': min_series, 'translucentRibbons': 'solid outline/text alternative required'}, indent=2))
