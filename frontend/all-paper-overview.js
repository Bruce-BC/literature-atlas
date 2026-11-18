import {categoryForPaper} from './network-style.js';
import {fieldDescendants} from './view-state.js';

export const ALL_PAPERS_ID = 'all-papers';
export const ALL_PAPERS_LABEL = '전체 논문 지도';
export const ALL_PAPER_EDGE_BUDGET = 1000;
export const ALL_PAPER_SPACING = 15; // canvas px between neighbouring points

const GOLDEN = Math.PI * (3 - Math.sqrt(5));
const UNKNOWN = Object.freeze({id: 'unknown', label: 'Unclassified', path: ['Unclassified'], color: '#64748b'});

/**
 * Deterministic preset coordinates in G6 canvas space: a sunflower ordered by
 * (category, paper id).  Same input -> same coordinates; no force simulation.
 */
export function presetCoordinates(rows) {
  const ordered = [...rows].sort((a, b) => String(a.categoryKey).localeCompare(String(b.categoryKey)) || String(a.id).localeCompare(String(b.id)));
  const result = new Map();
  ordered.forEach((row, index) => { const radius = ALL_PAPER_SPACING * Math.sqrt(index + 0.5); const angle = index * GOLDEN; result.set(row.id, {x: Math.cos(angle) * radius, y: Math.sin(angle) * radius}); });
  return result;
}

const focusIds = (snapshot, focusScope) => {
  if (!focusScope) return null;
  if (focusScope instanceof Set) return focusScope;
  if (Array.isArray(focusScope)) return new Set(focusScope);
  if (focusScope.paperIds) return new Set(focusScope.paperIds);
  const fieldId = typeof focusScope === 'string' ? focusScope : focusScope.fieldId; if (!fieldId) return null;
  const ids = fieldDescendants(snapshot, fieldId);
  return new Set((snapshot.papers || []).filter(paper => (paper.fieldIds || []).some(id => ids.has(id))).map(paper => paper.id));
};

/**
 * One point per ACTIVE paper (including unknown/unclassified).  Field focus only
 * dims; it never removes a point.  `counters.viewport` is supplied by the
 * renderer via `countInViewport` and never changes `represented`.
 */
export function allPaperOverviewProjection(snapshot, {focusScope = null, fieldId = null, relation = null, relationType = null, edgeBudget = ALL_PAPER_EDGE_BUDGET, categoryPalette = {}, viewport = null} = {}) {
  const relationName = relation || relationType || 'semantic';
  const papers = [...(snapshot?.papers || [])].sort((a, b) => String(a.id).localeCompare(String(b.id)));
  const focus = focusIds(snapshot, focusScope || (fieldId ? {fieldId} : null));
  const categories = new Map(papers.map(paper => { const category = categoryForPaper(paper, snapshot, categoryPalette); return [paper.id, (paper.fieldIds || []).length || paper.displayFieldId ? category : {...UNKNOWN, ...(category.id === 'unknown' ? {} : category)}]; }));
  const keyOf = id => { const c = categories.get(id); return c?.id && c.id !== 'unknown' ? c.id : 'unknown'; };
  const coords = presetCoordinates(papers.map(paper => ({id: paper.id, categoryKey: keyOf(paper.id)})));
  const points = papers.map(paper => { const c = coords.get(paper.id); return {id: paper.id, deterministicX: c.x, deterministicY: c.y, categoryKey: keyOf(paper.id), dimmed: Boolean(focus && !focus.has(paper.id))}; });
  const activeIds = new Set(papers.map(paper => paper.id));
  const eligible = (snapshot?.edges || []).filter(edge => edge.type === relationName && activeIds.has(edge.source) && activeIds.has(edge.target));
  const rank = edge => (focus && (focus.has(edge.source) || focus.has(edge.target)) ? 0 : 1);
  const displayed = [...eligible].sort((a, b) => rank(a) - rank(b) || String(a.id).localeCompare(String(b.id))).slice(0, Math.max(0, edgeBudget));
  const paperById = new Map(papers.map(paper => [paper.id, paper]));
  const nodes = points.map(point => { const paper = paperById.get(point.id) || {}; const category = categories.get(point.id) || UNKNOWN; return {data: {id: `overview-paper:${point.id}`, paperId: point.id, label: paper.title || point.id, kind: 'paper', x: point.deterministicX, y: point.deterministicY, categoryId: point.categoryKey, categoryLabel: category.label, categoryPath: (category.path || []).join(' / '), categoryColor: category.color, dimmed: point.dimmed}}; });
  const edges = displayed.map(edge => ({data: {id: `overview-edge:${edge.id}`, source: `overview-paper:${edge.source}`, target: `overview-paper:${edge.target}`, relation: edge.type, directed: Boolean(edge.directed)}}));
  const projection = {id: ALL_PAPERS_ID, label: ALL_PAPERS_LABEL, points, edges, nodes, representedIds: points.map(point => point.id), activePaperIds: papers.map(paper => paper.id), focusedIds: focus ? [...focus] : [], counters: {active: papers.length, represented: points.length, viewport: points.length, eligibleEdges: eligible.length, displayedEdges: edges.length}};
  if (viewport) projection.counters.viewport = countInViewport(points, viewport);
  // Legacy aliases used by older callers/tests.
  projection.totalPapers = papers.length; projection.visiblePapers = points.length; projection.eligibleEdges = eligible.length; projection.visibleEdges = edges.length;
  return projection;
}

/** Number of points inside a canvas-space viewport; purely informational. */
export const countInViewport = (points, {minX, maxX, minY, maxY}) => points.reduce((sum, point) => sum + (point.deterministicX >= minX && point.deterministicX <= maxX && point.deterministicY >= minY && point.deterministicY <= maxY ? 1 : 0), 0);

export const AtlasAllPaperOverview = {ALL_PAPERS_ID, ALL_PAPERS_LABEL, allPaperOverviewProjection, presetCoordinates, countInViewport};
if (typeof window !== 'undefined') window.AtlasAllPaperOverview = AtlasAllPaperOverview;
