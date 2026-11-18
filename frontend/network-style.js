import {stableHash} from './sampling.js';
import {displayLabel} from './decoder.js';

const FAMILIES = ['blue', 'teal', 'gold', 'violet', 'rose', 'green'];
export const NETWORK_ZOOM_RANGE = Object.freeze([0.2, 3]);
export function clampNetworkZoom(value) { const number = Number(value); return Number.isFinite(number) ? Math.max(NETWORK_ZOOM_RANGE[0], Math.min(NETWORK_ZOOM_RANGE[1], number)) : 1; }
export const networkZoomOptions = () => ({minZoom: NETWORK_ZOOM_RANGE[0], maxZoom: NETWORK_ZOOM_RANGE[1]});
const rgb = hex => { const value = String(hex || '#64748b').replace('#', ''); const n = Number.parseInt(value.length === 3 ? value.split('').map(x => x + x).join('') : value, 16); return [n >> 16 & 255, n >> 8 & 255, n & 255]; };
const hex = values => `#${values.map(value => Math.round(Math.max(0, Math.min(255, value))).toString(16).padStart(2, '0')).join('')}`;
export function variantColor(base, depth = 0) { const [r, g, b] = rgb(base); const amount = Math.min(0.28, Math.max(-0.18, depth * 0.08)); return hex([r + (255 - r) * amount, g + (255 - g) * amount, b + (255 - b) * amount]); }

function depthOf(field, fields, memo = new Map(), trail = new Set()) { if (!field || trail.has(field.id)) return 0; if (memo.has(field.id)) return memo.get(field.id); const parent = fields.find(candidate => candidate.id === (field.parentIds || [])[0]); const depth = parent ? depthOf(parent, fields, memo, new Set([...trail, field.id])) + 1 : 0; memo.set(field.id, depth); return depth; }
export function fieldPresentation(field, snapshot, palette = {}) {
  const fields = snapshot.fields || [];
  if (!field) return {id: 'unknown', label: 'Unknown field', path: ['Unknown field'], depth: 0, familyIndex: null, family: null, color: '#64748b', inferred: false};
  const ancestors = []; let current = field; const seen = new Set();
  while (current && !seen.has(current.id)) { seen.add(current.id); ancestors.unshift(current); current = fields.find(candidate => candidate.id === (current.parentIds || [])[0]); }
  const rootId = ancestors[0]?.id || field.id; const mapped = FAMILIES.indexOf(palette.familyIndex?.[rootId]); const familyIndex = mapped >= 0 ? mapped : stableHash(rootId) % FAMILIES.length;
  const depth = depthOf(field, fields); const base = palette.series?.[familyIndex] || '#64748b';
  return {id: field.id, label: displayLabel(field), path: ancestors.map(displayLabel), depth, familyIndex, family: FAMILIES[familyIndex], color: variantColor(base, depth), inferred: false};
}
export function categoryForPaper(paper, snapshot, palette = {}) {
  const fields = snapshot.fields || []; const memberships = (snapshot.memberships || []).filter(row => row.paperId === paper.id); const candidates = fields.filter(field => (paper.fieldIds || []).includes(field.id)).map(field => ({field, membership: memberships.find(row => row.fieldId === field.id)}));
  candidates.sort((a, b) => depthOf(b.field, fields) - depthOf(a.field, fields) || Number(b.membership?.weight || 0) - Number(a.membership?.weight || 0) || Number(paper.displayFieldId === b.field.id) - Number(paper.displayFieldId === a.field.id) || a.field.id.localeCompare(b.field.id));
  return fieldPresentation(candidates[0]?.field, snapshot, palette);
}

export function categoryForConcept(concept, papers, snapshot, palette = {}) {
  const counts = new Map(); for (const paper of papers.filter(row => (row.conceptIds || []).includes(concept.id))) { const category = categoryForPaper(paper, snapshot, palette); const existing = counts.get(category.id) || {...category, count: 0}; existing.count += 1; counts.set(category.id, existing); }
  const best = [...counts.values()].sort((a, b) => b.count - a.count || a.id.localeCompare(b.id))[0]; return best ? {...best, inferred: true} : {id: 'unknown', label: 'Unknown field', path: ['Unknown field'], color: '#64748b', inferred: true, count: 0};
}

export function networkLegend(categories) { const grouped = new Map(); for (const category of categories) { const item = grouped.get(category.id) || {...category, count: 0}; item.count += 1; grouped.set(category.id, item); } return [...grouped.values()].sort((a, b) => a.label.localeCompare(b.label) || a.id.localeCompare(b.id)); }
export function networkLegendForNodes(nodes) { return networkLegend(nodes.map(node => ({id: node.categoryId || 'unknown', label: node.categoryLabel || 'Unknown field', color: node.categoryColor || '#64748b', inferred: Boolean(node.categoryInferred)}))); }
export function fieldPaperCounts(snapshot) { const fields = snapshot.fields || []; const descendants = id => { const result = new Set([id]); let changed = true; while (changed) { changed = false; for (const field of fields) if ((field.parentIds || []).some(parent => result.has(parent)) && !result.has(field.id)) { result.add(field.id); changed = true; } } return result; }; return new Map(fields.map(field => { const ids = descendants(field.id); const direct = new Set(snapshot.papers.filter(paper => paper.displayFieldId === field.id).map(paper => paper.id)); const inclusive = new Set(snapshot.papers.filter(paper => (paper.fieldIds || []).some(fieldId => ids.has(fieldId))).map(paper => paper.id)); return [field.id, {direct: direct.size, inclusive: inclusive.size}]; })); }

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
