const defaultVars = {bg: '#f8fafc', panel: '#ffffff', text: '#1e293b', muted: '#475569', border: '#cbd5e1', focus: '#3c74ca'};

export function findPalette(asset, id) {
  return (asset?.palettes || []).find(palette => palette.id === id) || asset?.palettes?.[0] || null;
}

export function resolveTheme(theme, matchMedia = globalThis.matchMedia) {
  if (theme === 'light' || theme === 'dark') return theme;
  return matchMedia?.('(prefers-color-scheme: dark)')?.matches ? 'dark' : 'light';
}

export function applyPalette(root, asset, paletteId, theme) {
  const palette = findPalette(asset, paletteId);
  const resolved = resolveTheme(theme);
  const tokens = palette?.themes?.[resolved] || { ...defaultVars, series: [] };
  root.dataset.atlasPalette = palette?.id || paletteId || 'atlas';
  root.dataset.atlasTheme = resolved;
  root.style.colorScheme = resolved;
  const supportsOklch = Boolean(globalThis.CSS?.supports?.('color: oklch(50% .1 180)'));
  for (const key of Object.keys(defaultVars)) { const hex = tokens[key] || defaultVars[key]; const oklch = tokens.neutralOKLCH?.[key]; root.style.setProperty(`--atlas-${key}`, supportsOklch && oklch ? oklch : hex); root.style.setProperty(`--atlas-${key}-hex`, hex); if (oklch) root.style.setProperty(`--atlas-${key}-oklch`, oklch); }
  root.style.setProperty('--atlas-context-alpha', String(tokens.ribbonAlpha ?? 0.16));
  root.style.setProperty('--atlas-selected-alpha', String(tokens.selectedAlpha ?? 0.7));
  root.style.setProperty('--atlas-panel-2', tokens.panel2 || tokens.bg || defaultVars.bg);
  (tokens.series || []).forEach((series, index) => {
    const hex = series.hex || '#64748b'; root.style.setProperty(`--atlas-series-${index + 1}`, supportsOklch && series.oklch ? series.oklch : hex);
    root.style.setProperty(`--atlas-series-${index + 1}-hex`, hex);
    if (series.oklch) root.style.setProperty(`--atlas-series-${index + 1}-oklch`, series.oklch);
  });
  root.style.setProperty('--atlas-ribbon', `color-mix(in srgb, var(--atlas-text) 18%, transparent)`);
  root.style.setProperty('--atlas-ribbon-selected', `color-mix(in srgb, var(--atlas-focus) 72%, transparent)`);
  return {palette, theme: resolved, tokens};
}

/** Actual palette/theme context for category colours (never the default-light fallback). */
export function readPaletteContext(root) {
  const computed = getComputedStyle(root); const hexOf = name => computed.getPropertyValue(name).trim();
  return {id: root.dataset.atlasPalette || 'atlas', theme: root.dataset.atlasTheme === 'dark' ? 'dark' : 'light', panel: hexOf('--atlas-panel-hex') || '#ffffff', series: Array.from({length: 6}, (_, index) => hexOf(`--atlas-series-${index + 1}-hex`) || '#64748b')};
}

export const AtlasPalette = {findPalette, resolveTheme, applyPalette, readPaletteContext};
if (typeof window !== 'undefined') window.AtlasPalette = AtlasPalette;
