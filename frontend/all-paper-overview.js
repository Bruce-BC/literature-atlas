import {categoryForPaper} from './network-style.js';
import {fieldDescendants} from './view-state.js';
import {layoutCollectionClouds} from './collection-clouds.js';
import {computeCloudShapes} from './cloud-shapes.js';
import {assignCloudHues, cloudTones, hexToOklab} from './cloud-colors.js';
import {edgesForState} from './edge-strength.js';

export const ALL_PAPERS_ID = 'all-papers';
export const ALL_PAPERS_LABEL = '전체 논문 지도';
export const ALL_PAPER_EDGE_BUDGET = 1000;
export const ALL_PAPER_SPACING = 15; // canvas px between neighbouring points
export const COLLECTIONS_ID = 'collections';
export const COLLECTIONS_LABEL = '컬렉션 구름 지도';

const GOLDEN = Math.PI * (3 - Math.sqrt(5));
const now = () => (globalThis.performance?.now ? globalThis.performance.now() : Date.now());
const shapeCache = new WeakMap(); // layout object (memoised by signature) -> base outlines; sub-group outlines are computed lazily by the overlay
const shapesFor = layout => shapeCache.get(layout) || shapeCache.set(layout, computeCloudShapes(layout, {subgroups: false})).get(layout);
/**
 * Cloud colours for one palette/theme (plan D-A1): Map cloudId -> tones, plus the point colour of every (cloud, paper)
 * pair — sub-group members use the one-directional point variants (rank mod 3 by sub-group size, `subgroup:other` = base).
 */
function cloudColors(layout, palette) {
  const theme = palette.theme === 'dark' ? 'dark' : 'light'; const hues = assignCloudHues(layout.clouds, {paletteId: palette.id || 'atlas'}); const tones = new Map(); const pointColor = new Map();
  for (const cloud of layout.clouds) {
    const hue = hues.get(cloud.id); const tone = cloudTones({hue: hue?.hue ?? null, theme, panelHex: palette.panel || (theme === 'dark' ? '#1e293b' : '#ffffff'), unfiled: Boolean(cloud.unfiled || hue?.unfiled)}); tones.set(cloud.id, {...tone, slot: hue?.slot ?? null});
    let rank = 0; for (const group of cloud.subgroups || []) { const hex = group.other ? tone.point.hex : tone.pointVariants[rank % 3].hex; if (!group.other) rank += 1; for (const paperId of group.paperIds || []) pointColor.set(`${cloud.id}|${paperId}`, hex); }
  }
  return {theme, tones, pointColor};
}
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

export const CATEGORY_CLOUD_PREFIX = 'category:';
export const UNKNOWN_CATEGORY_CLOUD = 'category:unknown';
const cmp = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
/**
 * One cloud per paper category (the typed subject primary, see `categoryForPaper`): [{id, label, paperIds, size, unfiled, fieldId, color}]
 * ordered size desc, id asc, the unclassified cloud last.  Every active paper is in exactly one cloud, so the map has no replica points.
 */
export function categoryGroups(papers, categories, keyOf) {
  const byKey = new Map();
  for (const paper of papers) { const key = keyOf(paper.id); const row = byKey.get(key) || byKey.set(key, {key, category: categories.get(paper.id), paperIds: []}).get(key); row.paperIds.push(String(paper.id)); }
  const groups = [...byKey.values()].map(row => ({id: `${CATEGORY_CLOUD_PREFIX}${row.key}`, label: row.key === 'unknown' ? '미분류' : String(row.category?.label || row.key), paperIds: row.paperIds.sort(cmp), size: row.paperIds.length, unfiled: row.key === 'unknown', fieldId: row.key === 'unknown' ? null : row.key, color: row.category?.color || UNKNOWN.color}));
  return [...groups.filter(group => !group.unfiled).sort((a, b) => b.size - a.size || cmp(a.id, b.id)), ...groups.filter(group => group.unfiled)];
}
const hueOfHex = hex => { const [, a, b] = hexToOklab(hex); return (Math.atan2(b, a) * 180 / Math.PI + 360) % 360; };

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
 * `grouping: 'category'` (default): one point per ACTIVE paper (including
 * unknown/unclassified), laid out as one cloud per category (`categoryGroups`);
 * `clouds`/`cloudLayout` feed the outline + label overlay.  `grouping: 'collection'`: one point per (active paper,
 * core collection cloud) pair, uncollected papers in the single `cloud:unfiled`
 * cloud; coordinates come from `layoutCollectionClouds` (deterministic preset)
 * and node IDs resolve to papers through `paperIdByNode`.  Field focus (and a
 * selected cloud) only dims; it never removes a point.  Counters stay truthful:
 * `active`/`represented` count unique papers, `points` the rendered points,
 * `viewport` unique papers (supplied by the renderer via `countInViewport`).
 */
export function allPaperOverviewProjection(snapshot, {grouping = 'category', focusScope = null, fieldId = null, relation = null, relationType = null, edgeBudget = ALL_PAPER_EDGE_BUDGET, categoryPalette = {}, viewport = null, fitArea = null, selectedCloudId = null, selectedPaperId = null, selectedPointId = null, subItemPaperIds = null} = {}) {
  const relationName = relation || relationType || 'semantic';
  const papers = [...(snapshot?.papers || [])].sort((a, b) => String(a.id).localeCompare(String(b.id)));
  const focus = focusIds(snapshot, focusScope || (fieldId ? {fieldId} : null));
  const categories = new Map(papers.map(paper => { const category = categoryForPaper(paper, snapshot, categoryPalette); return [paper.id, (paper.fieldIds || []).length || paper.displayFieldId ? category : {...UNKNOWN, ...(category.id === 'unknown' ? {} : category)}]; }));
  const keyOf = id => { const c = categories.get(id); return c?.id && c.id !== 'unknown' ? c.id : 'unknown'; };
  if (grouping === 'collection') return collectionProjection(snapshot, papers, {focus, categories, keyOf, relationName, fitArea, selectedCloudId, selectedPaperId, selectedPointId, subItemPaperIds, viewport, palette: categoryPalette || {}});
  // Category clouds: the same deterministic cloud layout as the collection map, grouped by category (one point per paper).
  const groups = categoryGroups(papers, categories, keyOf); const layout = layoutCollectionClouds(snapshot, {relation: relationName, fitArea, groups, pointId: (cloud, paperId) => `overview-paper:${paperId}`});
  const placed = new Map(layout.points.map(point => [point.paperId, point]));
  const points = papers.map(paper => { const c = placed.get(String(paper.id)); return {id: paper.id, cloudId: c.cloudId, deterministicX: c.x, deterministicY: c.y, categoryKey: keyOf(paper.id), dimmed: Boolean(focus && !focus.has(paper.id))}; });
  const activeIds = new Set(papers.map(paper => paper.id));
  const eligible = (snapshot?.edges || []).filter(edge => edge.type === relationName && activeIds.has(edge.source) && activeIds.has(edge.target));
  const paperById = new Map(papers.map(paper => [paper.id, paper]));
  const nodes = points.map(point => { const paper = paperById.get(point.id) || {}; const category = categories.get(point.id) || UNKNOWN; return {data: {id: `overview-paper:${point.id}`, paperId: point.id, label: paper.title || point.id, kind: 'paper', x: point.deterministicX, y: point.deterministicY, categoryId: point.categoryKey, categoryLabel: category.label, categoryPath: (category.path || []).join(' / '), categoryColor: category.color, dimmed: point.dimmed}}; });
  // Paper-to-paper lines are NOT drawn on the field map: hundreds of crossing lines hid the clouds.  Each pair of fields is
  // joined by one aggregated bridge whose width carries the number/strength of the semantic links (edge-strength.js).
  const edges = [];
  const focusCloudId = focusScope?.fieldId ? groups.find(group => group.fieldId === focusScope.fieldId)?.id || null : null;
  const baseView = edgesForState(snapshot, layout, {});
  const edgeView = {...baseView, bridges: baseView.bridges.map(bridge => (!focusCloudId ? bridge : bridge.source === focusCloudId || bridge.target === focusCloudId ? {...bridge, emphasis: true} : {...bridge, dimmed: true})), edges: []};
  const theme = categoryPalette.theme === 'dark' ? 'dark' : 'light'; const shaped = shapesFor(layout); const shapeById = new Map(shaped.shapes.map(shape => [shape.cloudId, shape])); const groupById = new Map(groups.map(group => [group.id, group]));
  const clouds = layout.clouds.map(cloud => { const shape = shapeById.get(cloud.id); const group = groupById.get(cloud.id); const tone = cloudTones({hue: cloud.unfiled ? null : hueOfHex(group.color), theme, panelHex: categoryPalette.panel || (theme === 'dark' ? '#1e293b' : '#ffffff'), unfiled: cloud.unfiled}); return {id: cloud.id, label: cloud.label, fieldId: group.fieldId, cx: cloud.cx, cy: cloud.cy, r: cloud.r, size: cloud.size, unfiled: cloud.unfiled, kind: shape.kind, method: shape.method, d: shape.d, bbox: shape.bbox, colors: {fill: tone.fill.hex, stroke: tone.stroke.hex, selectedFill: tone.selectedFill.hex}}; });
  const nodeIdsByPaper = new Map(papers.map(paper => [String(paper.id), [`overview-paper:${paper.id}`]]));
  const projection = {id: ALL_PAPERS_ID, label: ALL_PAPERS_LABEL, points, edges, edgeView, nodes, clouds, cloudLayout: layout, nodeIdsByPaper, representedIds: points.map(point => point.id), activePaperIds: papers.map(paper => paper.id), focusedIds: focus ? [...focus] : [], counters: {active: papers.length, represented: points.length, viewport: points.length, eligibleEdges: eligible.length, displayedEdges: edges.length, displayedBridges: edgeView.bridges.length}};
  if (viewport) projection.counters.viewport = countInViewport(points, viewport);
  // Legacy aliases used by older callers/tests.
  projection.totalPapers = papers.length; projection.visiblePapers = points.length; projection.eligibleEdges = eligible.length; projection.visibleEdges = edges.length;
  return projection;
}

/**
 * View state -> `edgesForState` state (pure): the selected cloud, the selected paper (null when it has no point) and the
 * clicked replica point only when it belongs to that paper (a panel/list selection passes none -> the engine's replica rule).
 * The map always draws SEMANTIC strength (plan §2), whatever the relation select shows; `eligibleEdges` keeps counting that relation.
 */
export function collectionEdgeState({selectedCloudId = null, selectedPaperId = null, selectedPointId = null, subItemPaperIds = null, nodeIdsByPaper = null} = {}) {
  const points = selectedPaperId == null ? null : nodeIdsByPaper?.get(selectedPaperId) || null;
  return {cloudId: selectedCloudId ?? null, paperId: points ? selectedPaperId : null, pointId: points && points.includes(selectedPointId) ? selectedPointId : null, subItemPaperIds: subItemPaperIds instanceof Set || Array.isArray(subItemPaperIds) ? [...subItemPaperIds].map(String).sort() : null};
}

function collectionProjection(snapshot, papers, {focus, categories, keyOf, relationName, fitArea, selectedCloudId, selectedPaperId, selectedPointId, subItemPaperIds, viewport, palette}) {
  const layout = layoutCollectionClouds(snapshot, {relation: relationName, fitArea}); const shaped = shapesFor(layout); const shapeById = new Map(shaped.shapes.map(shape => [shape.cloudId, shape]));
  const colorStarted = now(); const colors = cloudColors(layout, palette); const ringHex = palette.text || (colors.theme === 'dark' ? '#e2e8f0' : '#1e293b');
  const paperById = new Map(papers.map(paper => [paper.id, paper])); const cloudById = new Map(layout.clouds.map(cloud => [cloud.id, cloud]));
  const selectedCloud = selectedCloudId && cloudById.has(selectedCloudId) ? selectedCloudId : null;
  const points = layout.points.map(point => ({id: point.id, paperId: point.paperId, cloudId: point.cloudId, deterministicX: point.x, deterministicY: point.y, categoryKey: keyOf(point.paperId), dimmed: Boolean((focus && !focus.has(point.paperId)) || (selectedCloud && point.cloudId !== selectedCloud))}));
  const paperIdByNode = new Map(points.map(point => [point.id, point.paperId])); const nodeIdsByPaper = new Map();
  for (const point of points) (nodeIdsByPaper.get(point.paperId) || nodeIdsByPaper.set(point.paperId, []).get(point.paperId)).push(point.id);
  const nodes = points.map(point => { const paper = paperById.get(point.paperId) || {}; const category = categories.get(point.paperId) || UNKNOWN; const cloud = cloudById.get(point.cloudId); const ring = nodeIdsByPaper.get(point.paperId).length > 1; return {data: {id: point.id, paperId: point.paperId, cloudId: point.cloudId, cloudLabel: cloud.label, label: paper.title || point.paperId, kind: 'paper', x: point.deterministicX, y: point.deterministicY, categoryId: point.categoryKey, categoryLabel: category.label, categoryPath: [cloud.label, ...(category.path || [])].join(' / '), categoryColor: colors.pointColor.get(`${point.cloudId}|${point.paperId}`) || colors.tones.get(point.cloudId).point.hex, ...(ring ? {ring: true, outline: ringHex} : {}), dimmed: point.dimmed}}; });
  const cloudColorMs = now() - colorStarted;
  const activeIds = new Set(papers.map(paper => paper.id));
  const eligible = (snapshot?.edges || []).filter(edge => edge.type === relationName && activeIds.has(edge.source) && activeIds.has(edge.target));
  const representedIds = [...nodeIdsByPaper.keys()].sort((a, b) => a.localeCompare(b));
  const hex = tone => tone.hex; // overlay colours: per-element CSS variables (no gradients); geometry is LOCAL (origin = cloud centre)
  const clouds = layout.clouds.map(cloud => { const shape = shapeById.get(cloud.id); const tone = colors.tones.get(cloud.id); return {id: cloud.id, label: cloud.label, cx: cloud.cx, cy: cloud.cy, r: cloud.r, size: cloud.size, unfiled: cloud.unfiled, kind: shape.kind, method: shape.method, d: shape.d, bbox: shape.bbox, colors: {slot: tone.slot, hue: tone.hue, fill: hex(tone.fill), stroke: hex(tone.stroke), selectedFill: hex(tone.selectedFill), point: hex(tone.point), pointVariants: tone.pointVariants.map(hex)}}; });
  // The graph holds paper points only: hulls, labels, replica lines and the semantic edge layers (bridges + paper lines, ONLY from `edgesForState`) are drawn by the wrapper-owned overlay.
  const edgeStarted = now(); const edgeState = collectionEdgeState({selectedCloudId: selectedCloud, selectedPaperId, selectedPointId, subItemPaperIds, nodeIdsByPaper}); const edgeView = edgesForState(snapshot, layout, edgeState); const edgeMs = now() - edgeStarted;
  const projection = {id: COLLECTIONS_ID, label: COLLECTIONS_LABEL, grouping: 'collection', points, edges: [], nodes, clouds, cloudLayout: layout, cloudTheme: colors.theme, edgeState, edgeView, cloudStats: {shapeMs: shaped.stats.shapeMs, colorMs: cloudColorMs, edgeMs, computed: shaped.stats.computed, memoHits: shaped.stats.memoHits, methods: {...shaped.stats.methods}, fallbacks: {...shaped.stats.fallbacks}}, paperIdByNode, nodeIdsByPaper, selectedCloudId: selectedCloud, representedIds, activePaperIds: papers.map(paper => paper.id), focusedIds: focus ? [...focus] : [],
    counters: {active: papers.length, represented: representedIds.length, points: points.length, clouds: clouds.length, viewport: representedIds.length, eligibleEdges: eligible.length, displayedEdges: edgeView.counters.displayedEdges, displayedBridges: edgeView.counters.displayedBridges}};
  if (viewport) projection.counters.viewport = countInViewport(points, viewport);
  projection.totalPapers = papers.length; projection.visiblePapers = representedIds.length; projection.eligibleEdges = eligible.length; projection.visibleEdges = edgeView.counters.displayedEdges;
  return projection;
}

/** Number of unique papers with a point inside a canvas-space viewport; purely informational. */
export const countInViewport = (points, {minX, maxX, minY, maxY}) => new Set(points.filter(point => point.deterministicX >= minX && point.deterministicX <= maxX && point.deterministicY >= minY && point.deterministicY <= maxY).map(point => point.paperId ?? point.id)).size;

export const AtlasAllPaperOverview = {categoryGroups, CATEGORY_CLOUD_PREFIX, UNKNOWN_CATEGORY_CLOUD, ALL_PAPERS_ID, ALL_PAPERS_LABEL, COLLECTIONS_ID, COLLECTIONS_LABEL, allPaperOverviewProjection, collectionEdgeState, presetCoordinates, countInViewport};
if (typeof window !== 'undefined') window.AtlasAllPaperOverview = AtlasAllPaperOverview;
