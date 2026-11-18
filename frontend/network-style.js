import {stableHash} from './sampling.js';
import {displayLabel} from './decoder.js';

const FAMILIES = ['blue', 'teal', 'gold', 'violet', 'rose', 'green'];
const FAMILY_HUES = Object.freeze([258.75, 183.31, 71.37, 293.28, 15.51, 150.46]);
const DEFAULT_OKLCH = Object.freeze({lightness: 0.57, chroma: 0.145});
export const NETWORK_ZOOM_RANGE = Object.freeze([0.2, 3]);
export function clampNetworkZoom(value) { const number = Number(value); return Number.isFinite(number) ? Math.max(NETWORK_ZOOM_RANGE[0], Math.min(NETWORK_ZOOM_RANGE[1], number)) : 1; }
export const networkZoomOptions = () => ({minZoom: NETWORK_ZOOM_RANGE[0], maxZoom: NETWORK_ZOOM_RANGE[1]});
const rgb = hex => { const value = String(hex || '#64748b').replace('#', ''); const n = Number.parseInt(value.length === 3 ? value.split('').map(x => x + x).join('') : value, 16); return [n >> 16 & 255, n >> 8 & 255, n & 255]; };
const hex = values => `#${values.map(value => Math.round(Math.max(0, Math.min(255, value))).toString(16).padStart(2, '0')).join('')}`;
const clamp01 = value => Math.max(0, Math.min(1, Number(value) || 0));

// OKLCH -> sRGB conversion with chroma reduction at the gamut boundary. Keeping
// this local avoids a runtime dependency and makes the fallback registry stable
// in Node tests as well as in the browser.
export function oklchToHex(lightness, chroma, hue) {
  const L = clamp01(lightness); const h = Number(hue) * Math.PI / 180;
  let C = Math.max(0, Number(chroma) || 0);
  const toRgb = chromaValue => {
    const a = chromaValue * Math.cos(h); const b = chromaValue * Math.sin(h);
    const l = L + 0.3963377774 * a + 0.2158037573 * b;
    const m = L - 0.1055613458 * a - 0.0638541728 * b;
    const s = L - 0.0894841775 * a - 1.291485548 * b;
    const l3 = l ** 3; const m3 = m ** 3; const s3 = s ** 3;
    const linear = [4.0767416621 * l3 - 3.3077115913 * m3 + 0.2309699292 * s3, -1.2684380046 * l3 + 2.6097574011 * m3 - 0.3413193965 * s3, -0.0041960863 * l3 - 0.7034186147 * m3 + 1.707614701 * s3];
    const srgb = linear.map(value => value <= 0.0031308 ? 12.92 * value : 1.055 * Math.pow(Math.max(0, value), 1 / 2.4) - 0.055);
    return {linear, srgb};
  };
  let result = toRgb(C);
  for (let attempt = 0; attempt < 14 && result.srgb.some(value => value < 0 || value > 1); attempt += 1) { C *= 0.9; result = toRgb(C); }
  return hex(result.srgb.map(value => clamp01(value) * 255));
}
export function relativeLuminance(color) { return rgb(color).map(value => value / 255).map(value => value <= .03928 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4).reduce((sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index], 0); }
export function contrastRatio(first, second) { const a = relativeLuminance(first); const b = relativeLuminance(second); return (Math.max(a, b) + .05) / (Math.min(a, b) + .05); }
/** Nudge `foreground` toward black/white until it reaches `minimum` contrast on `background` (graph non-text contrast >= 3). */
export function ensureContrast(foreground, background, minimum = 3) {
  if (!background || contrastRatio(foreground, background) >= minimum) return foreground;
  const target = relativeLuminance(background) > 0.5 ? [0, 0, 0] : [255, 255, 255]; const base = rgb(foreground);
  for (let step = 1; step <= 20; step += 1) { const mix = hex(base.map((value, index) => value + (target[index] - value) * step * 0.05)); if (contrastRatio(mix, background) >= minimum) return mix; }
  return hex(target);
}
export function variantColor(base, depth = 0) { const [r, g, b] = rgb(base); const amount = Math.min(0.28, Math.max(-0.18, depth * 0.08)); return hex([r + (255 - r) * amount, g + (255 - g) * amount, b + (255 - b) * amount]); }

const fieldMapCache = new WeakMap();
const fieldMap = fields => { let map = fieldMapCache.get(fields); if (!map) { map = new Map(fields.map(field => [field.id, field])); fieldMapCache.set(fields, map); } return map; };
const depthMemo = new WeakMap();
function depthOf(field, fields, memo = null, trail = new Set()) {
  if (!field || trail.has(field.id)) return 0; const cache = memo || (depthMemo.get(fields) || depthMemo.set(fields, new Map()).get(fields)); if (cache.has(field.id)) return cache.get(field.id);
  const parent = fieldMap(fields).get((field.parentIds || [])[0]); const depth = parent ? depthOf(parent, fields, cache, new Set([...trail, field.id])) + 1 : 0; cache.set(field.id, depth); return depth;
}
const siblingCache = new WeakMap();
function siblingIndex(field, fields) {
  let groups = siblingCache.get(fields); if (!groups) { groups = new Map(); for (const candidate of fields) { const key = JSON.stringify(candidate.parentIds || []); (groups.get(key) || groups.set(key, []).get(key)).push(candidate.id); } for (const ids of groups.values()) ids.sort((a, b) => String(a).localeCompare(String(b))); siblingCache.set(fields, groups); }
  return Math.max(0, (groups.get(JSON.stringify(field.parentIds || [])) || []).indexOf(field.id));
}
function registryEntries(snapshot) { const registry = snapshot?.categoryColorRegistry; return Array.isArray(registry?.entries) ? registry.entries : []; }
function semanticCategoryKey(field, snapshot) {
  const entity = snapshot?.facetCatalog?.entities?.find(item => item.legacyFieldId === field?.id) || snapshot?.facetCatalogConfig?.entities?.find(item => item.legacyFieldId === field?.id);
  if (!entity && field?.facetId) return field.origin === 'custom' ? field.id : field.facetId;
  if (entity?.origin === 'custom') return entity.id || field?.id;
  return entity?.facetId || entity?.ontologyKey || field?.id;
}
function registryEntry(field, snapshot) { const key = field?.id; const semantic = semanticCategoryKey(field, snapshot); return registryEntries(snapshot).find(entry => entry.categoryKey === key || entry.categoryKey === semantic || entry.ontologyKey === key || entry.ontologyKey === semantic || entry.id === key) || null; }

/**
 * Return an append-only semantic color registry. A supplied snapshot registry
 * always wins; fallback entries are deterministic by semantic ID and sibling
 * index, so reordering or relabeling fields cannot recolor existing nodes.
 */
export function buildCategoryColorRegistry(snapshot, palette = {}) {
  const fields = snapshot?.fields || []; const existing = registryEntries(snapshot); const byKey = new Map(existing.map(entry => [entry.categoryKey || entry.ontologyKey, entry]));
  const byIdMap = fieldMap(fields); const rootOf = field => { let current = field; const seen = new Set(); while (current?.parentIds?.[0] && !seen.has(current.id)) { seen.add(current.id); current = byIdMap.get(current.parentIds[0]); } return current?.id || field.id; }; const roots = [...new Set(fields.map(rootOf))].sort((a, b) => String(a).localeCompare(String(b)));
  for (const field of fields) {
    if (byKey.has(field.id)) continue;
    const depth = depthOf(field, fields); const index = siblingIndex(field, fields); const root = rootOf(field); const rootIndex = Math.max(0, roots.indexOf(root)) % FAMILY_HUES.length; const hue = (FAMILY_HUES[rootIndex] + index * 31.7 + depth * 8.5) % 360;
    const themes = {}; const hexThemes = {}; for (const paletteId of ['atlas', 'calm', 'mocha']) for (const theme of ['light', 'dark']) { const L = theme === 'dark' ? .72 : .57; const C = theme === 'dark' ? .13 : DEFAULT_OKLCH.chroma; const paletteHue = (hue + ({atlas: 0, calm: 14, mocha: -18}[paletteId] || 0)) % 360; const color = oklchToHex(L, C, paletteHue); const key = `${paletteId}:${theme}`; themes[key] = {oklch: `oklch(${L.toFixed(4)} ${C.toFixed(4)} ${paletteHue.toFixed(2)})`, hex: color}; hexThemes[key] = color; } themes.system = themes['atlas:light']; hexThemes.system = hexThemes['atlas:light'];
    const categoryKey = semanticCategoryKey(field, snapshot); byKey.set(categoryKey, {categoryKey, ontologyKey: categoryKey, kind: 'field', family: FAMILIES[rootIndex], siblingIndex: index, depthVariant: depth, oklchByPaletteTheme: themes, hexByPaletteTheme: hexThemes, origin: 'frontend-fallback'});
  }
  return {version: snapshot?.categoryColorRegistry?.version || 1, namespace: snapshot?.categoryColorRegistry?.namespace || 'atlas.category', entries: [...byKey.values()]};
}

function registryColor(entry, palette, depth, theme = 'light') {
  const paletteId = palette.id || palette.paletteId || 'atlas'; const keys = [`${paletteId}:${theme}`, theme, `${paletteId}:light`, 'atlas:light', 'light', 'system']; const configuredKey = keys.find(key => entry?.hexByPaletteTheme?.[key] || entry?.oklchByPaletteTheme?.[key]); const configured = entry?.hexByPaletteTheme?.[configuredKey]?.hex || entry?.hexByPaletteTheme?.[configuredKey] || entry?.oklchByPaletteTheme?.[configuredKey]?.hex;
  if (configured) return configured;
  const family = FAMILIES.indexOf(entry?.family); const base = palette.series?.[family >= 0 ? family : 0] || '#64748b';
  return variantColor(base, depth);
}
const fallbackCache = new WeakMap();
function fallbackRegistry(snapshot, palette) {
  const key = snapshot.fields || snapshot; const cached = fallbackCache.get(key);
  if (cached && cached.registry === snapshot.categoryColorRegistry && cached.facets === snapshot.facetCatalog) return cached.value;
  const value = buildCategoryColorRegistry(snapshot, palette); fallbackCache.set(key, {registry: snapshot.categoryColorRegistry, facets: snapshot.facetCatalog, value}); return value;
}
export function fieldPresentation(field, snapshot, palette = {}) {
  const fields = snapshot.fields || [];
  if (!field) return {id: 'unknown', label: 'Unknown field', path: ['Unknown field'], depth: 0, familyIndex: null, family: null, color: '#64748b', inferred: false};
  const ancestors = []; let current = field; const seen = new Set();
  while (current && !seen.has(current.id)) { seen.add(current.id); ancestors.unshift(current); current = fields.find(candidate => candidate.id === (current.parentIds || [])[0]); }
  const rootId = ancestors[0]?.id || field.id; const entry = registryEntry(field, snapshot) || fallbackRegistry(snapshot, palette).entries.find(item => item.categoryKey === semanticCategoryKey(field, snapshot)); const mapped = FAMILIES.indexOf(palette.familyIndex?.[rootId]); const familyIndex = mapped >= 0 ? mapped : Math.max(0, FAMILIES.indexOf(entry?.family));
  const depth = depthOf(field, fields); const base = palette.series?.[familyIndex] || '#64748b'; const color = ensureContrast(registryColor(entry, palette, depth, palette.theme || 'light') || variantColor(base, depth), palette.panel, 3);
  return {id: field.id, label: displayLabel(field), path: ancestors.map(displayLabel), depth, familyIndex, family: FAMILIES[familyIndex], color, registryEntry: entry, inferred: false};
}
const facetIndexCache = new WeakMap();
function facetIndex(snapshot) {
  const key = snapshot.paperFacets || snapshot; const cached = facetIndexCache.get(key); if (cached && cached.catalog === snapshot.facetCatalog) return cached;
  const legacy = new Map(); for (const entity of snapshot.facetCatalog?.entities || []) if (entity.legacyFieldId) { legacy.set(entity.id, entity.legacyFieldId); if (entity.facetId) legacy.set(entity.facetId, entity.legacyFieldId); }
  const rows = new Map((Array.isArray(snapshot.paperFacets) ? snapshot.paperFacets : []).map(row => [row.paperId, row]));
  const value = {catalog: snapshot.facetCatalog, legacy, rows}; facetIndexCache.set(key, value); return value;
}
/** Typed subject primary (backend `paperFacets.primaryByFacet.subject`) -> legacy field, if it is a real field. */
export function subjectPrimaryFieldId(paper, snapshot) {
  const {legacy, rows} = facetIndex(snapshot); const facetId = rows.get(paper.id)?.primaryByFacet?.subject; if (!facetId) return null;
  const fieldId = legacy.get(facetId) || (snapshot.fields || []).find(field => field.id === facetId)?.id || null; return fieldId && (snapshot.fields || []).some(field => field.id === fieldId) ? fieldId : null;
}
const acceptedMembership = row => row && row.accepted !== false && row.suppressed !== true && row.status !== 'suppressed' && (row.method !== 'model' || row.accepted === true || row.origin === 'curated');
/**
 * Category of a paper: typed subject primary first, then an accepted
 * displayFieldId, then the deepest accepted direct field.  Broad ancestors never
 * override a specific accepted subject.
 */
const membershipIndexCache = new WeakMap();
const membershipsByPaper = snapshot => { const key = snapshot.memberships || snapshot; let index = membershipIndexCache.get(key); if (!index) { index = new Map(); for (const row of snapshot.memberships || []) (index.get(row.paperId) || index.set(row.paperId, []).get(row.paperId)).push(row); membershipIndexCache.set(key, index); } return index; };
export function categoryForPaper(paper, snapshot, palette = {}) {
  const fields = snapshot.fields || []; const subjectId = subjectPrimaryFieldId(paper, snapshot);
  if (subjectId) return fieldPresentation(fields.find(field => field.id === subjectId), snapshot, palette);
  const memberships = membershipsByPaper(snapshot).get(paper.id) || []; const fieldById = fieldMap(fields); const candidates = (paper.fieldIds || []).map(id => fieldById.get(id)).filter(Boolean).map(field => ({field, membership: memberships.find(row => row.fieldId === field.id)}));
  const display = candidates.find(candidate => candidate.field.id === paper.displayFieldId && (!candidate.membership || acceptedMembership(candidate.membership)));
  if (display) return fieldPresentation(display.field, snapshot, palette);
  const accepted = candidates.filter(candidate => !candidate.membership || acceptedMembership(candidate.membership));
  accepted.sort((a, b) => depthOf(b.field, fields) - depthOf(a.field, fields) || Number(b.membership?.weight || 0) - Number(a.membership?.weight || 0) || a.field.id.localeCompare(b.field.id));
  const pool = accepted.length ? accepted : candidates.sort((a, b) => depthOf(b.field, fields) - depthOf(a.field, fields) || a.field.id.localeCompare(b.field.id));
  return fieldPresentation(pool[0]?.field, snapshot, palette);
}

export function categoryForConcept(concept, papers, snapshot, palette = {}) {
  const counts = new Map(); for (const paper of papers.filter(row => (row.conceptIds || []).includes(concept.id))) { const category = categoryForPaper(paper, snapshot, palette); const existing = counts.get(category.id) || {...category, count: 0}; existing.count += 1; counts.set(category.id, existing); }
  const best = [...counts.values()].sort((a, b) => b.count - a.count || a.id.localeCompare(b.id))[0]; return best ? {...best, inferred: true} : {id: 'unknown', label: 'Unknown field', path: ['Unknown field'], color: '#64748b', inferred: true, count: 0};
}

export function networkLegend(categories) { const grouped = new Map(); for (const category of categories) { const item = grouped.get(category.id) || {...category, count: 0}; item.count += 1; grouped.set(category.id, item); } return [...grouped.values()].sort((a, b) => a.label.localeCompare(b.label) || a.id.localeCompare(b.id)); }
export function networkLegendForNodes(nodes) { return networkLegend(nodes.map(node => ({id: node.categoryId || 'unknown', label: node.categoryLabel || 'Unknown field', color: node.categoryColor || '#64748b', inferred: Boolean(node.categoryInferred)}))); }
/**
 * Direct / inclusive / primary counts per field as unique paper-ID sets.  Backend
 * `fieldStats` is preferred; the fallback implements the same formula on ACCEPTED
 * direct memberships (never displayFieldId).
 */
export function fieldPaperCounts(snapshot) {
  const fields = snapshot.fields || []; const papers = snapshot.papers || [];
  const statsRows = Array.isArray(snapshot.fieldStats?.rows) ? snapshot.fieldStats.rows : Array.isArray(snapshot.fieldStats?.fields) ? snapshot.fieldStats.fields : Array.isArray(snapshot.fieldStats) ? snapshot.fieldStats : null;
  const byField = new Map(fields.map(field => [field.id, new Set()])); const rawByField = new Map(fields.map(field => [field.id, new Set()])); const memberRows = new Map(); for (const row of snapshot.memberships || []) { const list = memberRows.get(row.paperId) || []; list.push(row); memberRows.set(row.paperId, list); }
  for (const paper of papers) { const rows = memberRows.get(paper.id); const rejected = new Set((rows || []).filter(row => !acceptedMembership(row)).map(row => row.fieldId)); const direct = (paper.fieldIds || []).filter(id => !rejected.has(id)); for (const id of direct) byField.get(id)?.add(paper.id); for (const id of paper.fieldIds || []) rawByField.get(id)?.add(paper.id); }
  const children = new Map(); for (const field of fields) for (const parent of field.parentIds || []) (children.get(parent) || children.set(parent, []).get(parent)).push(field.id);
  const closure = id => { const out = new Set([id]); const queue = [id]; while (queue.length) for (const child of children.get(queue.shift()) || []) if (!out.has(child)) { out.add(child); queue.push(child); } return out; };
  const primary = new Map(); for (const paper of papers) { const id = subjectPrimaryFieldId(paper, snapshot) || null; if (id) primary.set(id, (primary.get(id) || 0) + 1); }
  const knownPrimary = [...primary.values()].reduce((sum, value) => sum + value, 0);
  const primaryByField = new Map();
  const result = new Map(fields.map(field => {
    const stats = statsRows?.find(row => row.fieldId === field.id || row.id === field.id) || (statsRows ? null : snapshot.fieldStats?.[field.id]); const inclusive = new Set(); for (const id of closure(field.id)) for (const paperId of [...(byField.get(id) || []), ...(rawByField.get(id) || [])]) inclusive.add(paperId); // legacy fallback: unreviewed model fields still count toward coverage
    const direct = Number.isFinite(Number(stats?.direct)) ? Number(stats.direct) : byField.get(field.id).size; const incl = Number.isFinite(Number(stats?.inclusive)) ? Number(stats.inclusive) : inclusive.size;
    primaryByField.set(field.id, Number.isFinite(Number(stats?.primary)) ? Number(stats.primary) : (primary.get(field.id) || 0));
    return [field.id, {direct, inclusive: Math.max(incl, direct)}];
  }));
  result.primaryByField = primaryByField;
  result.unknown = Number.isFinite(Number(snapshot.fieldStats?.unknown)) ? Number(snapshot.fieldStats.unknown) : papers.length - knownPrimary; result.total = papers.length;
  return result;
}

export function renderFieldLegend(container, snapshot, {selectedId = null, scopeId = null, onSelect = () => {}, palette = {}, fields: subset = null} = {}) {
  if (!container) return null; container.replaceChildren(); container.classList.add('atlas-field-legend-grid'); const counts = fieldPaperCounts(snapshot); const fields = subset || snapshot.fields || [];
  const header = document.createElement('div'); header.className = 'atlas-field-legend-row atlas-field-legend-header'; for (const text of ['Category', 'Direct', 'Incl. subfields']) { const cell = document.createElement('span'); cell.textContent = text; header.append(cell); } container.append(header);
  for (const field of fields) { const button = document.createElement('button'); button.type = 'button'; button.className = 'atlas-field-legend-row atlas-legend-field'; button.dataset.fieldId = field.id; button.setAttribute('aria-pressed', String(selectedId === field.id)); button.dataset.scopeActive = String(scopeId === field.id); if (scopeId === field.id) button.setAttribute('aria-current', 'true'); const stat = {...(counts.get(field.id) || {direct: 0, inclusive: 0}), primary: counts.primaryByField?.get(field.id) || 0}; button.title = `${fieldPath(snapshot, field.id)} · primary subject: ${stat.primary} · direct ${stat.direct} ≤ incl. subfields ${stat.inclusive} (unique papers)`; button.setAttribute('aria-label', `${displayLabel(field)}: ${stat.direct} direct, ${stat.inclusive} including subfields, ${stat.primary} primary`); const label = document.createElement('span'); label.className = 'atlas-legend-category'; const swatch = document.createElement('i'); swatch.className = 'atlas-legend-swatch'; const presentation = fieldPresentation(field, snapshot, palette); swatch.style.background = presentation.color; swatch.dataset.categoryColor = presentation.color; swatch.dataset.depth = String(presentation.depth); const name = document.createElement('span'); name.textContent = displayLabel(field); label.append(swatch, name); const values = counts.get(field.id) || {direct: 0, inclusive: 0}; const direct = document.createElement('span'); direct.className = 'atlas-legend-number'; direct.textContent = String(values.direct); const inclusive = document.createElement('span'); inclusive.className = 'atlas-legend-number'; inclusive.textContent = String(values.inclusive); button.append(label, direct, inclusive); button.addEventListener('click', () => onSelect(field)); container.append(button); }
  if (subset) return {rows: fields.length, counts}; // a related-category subset is not the whole library: no library-wide caption
  const caption = document.createElement('p'); caption.className = 'atlas-field-legend-caption'; const known = [...(counts.primaryByField?.values() || [])].reduce((sum, value) => sum + value, 0); caption.textContent = `Known primary ${known} + unknown ${counts.unknown ?? 0} = ${counts.total ?? (snapshot.papers || []).length} papers · Direct and Incl. subfields are unique-paper counts`; container.append(caption);
  return {rows: fields.length, counts};
}

export function fieldPath(snapshot, id) {
  const fields = new Map(snapshot.fields.map(field => [field.id, field]));
  let field = fields.get(id); const path = []; const seen = new Set();
  while (field && !seen.has(field.id)) { seen.add(field.id); path.unshift(displayLabel(field)); field = fields.get((field.parentIds || [])[0]); }
  return path.join(' / ') || 'Unknown field';
}

export function endpointLabel(snapshot, id) {
  const entity = [...snapshot.papers, ...snapshot.concepts, ...snapshot.fields].find(item => item.id === id);
  return entity ? displayLabel(entity) : 'Unavailable item';
}
