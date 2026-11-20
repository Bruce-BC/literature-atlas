import {categoryForPaper, fieldPresentation} from './network-style.js';
import {displayLabel} from './decoder.js';
import {overviewProjection} from './projection.js';

export const CHILD_PAGE_SIZE = 24;
export const CHILD_TOTAL_LIMIT = 150;
export const VIEW_EDGE_BUDGET = 1000;
export const paperNodeId = paperId => `overview-paper:${paperId}`;
export const paperIdFromNode = nodeId => String(nodeId).startsWith('overview-paper:') ? String(nodeId).slice('overview-paper:'.length) : null;

/**
 * Bounded visible projection of the representative (field) overview.
 *
 * `expanded` (reducer-owned) is the source of truth.  Expanding field f adds
 *  - hierarchy edges f -> each typed child field (children are already nodes),
 *  - up to `pages*24` direct paper children of f, never more than 150 loaded
 *    papers across all owners; a paper exposed by several owners is ONE node
 *    whose `owners` lists every expanded owner,
 *  - membership edges from each loaded paper to every field it belongs to
 *    (multi-membership is preserved, the primary field is flagged),
 *  - truthful relation edges between loaded papers.
 * The whole view never exceeds `edgeBudget` edges.
 */
export function overviewGraphProjection(snapshot, {relation = 'semantic', expanded = new Set(), pages = new Map(), palette = {}, edgeBudget = VIEW_EDGE_BUDGET} = {}) {
  const overview = overviewProjection(snapshot, relation); const fields = new Map(snapshot.fields.map(field => [field.id, field]));
  // Visible forest: roots, plus children of every EXPANDED visible field (any parent suffices for multi-parent fields).
  const visible = new Set(snapshot.fields.filter(field => !(field.parentIds || []).some(parent => fields.has(parent))).map(field => field.id));
  for (let changed = true; changed;) { changed = false; for (const field of snapshot.fields) if (!visible.has(field.id) && (field.parentIds || []).some(parent => visible.has(parent) && expanded.has(parent))) { visible.add(field.id); changed = true; } }
  const ancestorMemo = new Map();
  const visibleAncestor = (id, trail = new Set()) => { if (visible.has(id)) return id; if (ancestorMemo.has(id)) return ancestorMemo.get(id); if (trail.has(id)) return null; trail.add(id); let found = null; for (const parent of [...(fields.get(id)?.parentIds || [])].sort()) { found = visibleAncestor(parent, trail); if (found) break; } ancestorMemo.set(id, found); return found; };
  const nodes = overview.fields.filter(field => visible.has(field.id)).map(field => { const presentation = fieldPresentation(fields.get(field.id), snapshot, palette); return {data: {id: field.id, label: displayLabel(fields.get(field.id)) || field.id, kind: 'field', representative: true, level: presentation.depth, expanded: expanded.has(field.id), hasChildren: snapshot.fields.some(child => (child.parentIds || []).includes(field.id)), categoryId: field.id, categoryColor: presentation.color, categoryLabel: presentation.label, categoryPath: presentation.path.join(' / ')}}; });
  const nodeIds = new Set(nodes.map(node => node.data.id));
  const rolled = new Map(); for (const row of overview.ribbons) { const source = visibleAncestor(row.sourceFieldId); const target = visibleAncestor(row.targetFieldId); if (!source || !target || source === target) continue; const key = row.directed ? `${source}>${target}` : [source, target].sort().join('|'); const existing = rolled.get(key) || {sourceFieldId: row.directed ? source : key.split('|')[0], targetFieldId: row.directed ? target : key.split('|')[1], value: 0, directed: Boolean(row.directed)}; existing.value += Number(row.value || 0); rolled.set(key, existing); }
  const ribbonRows = [...rolled.values()].sort((a, b) => b.value - a.value || `${a.sourceFieldId}${a.targetFieldId}`.localeCompare(`${b.sourceFieldId}${b.targetFieldId}`));
  const hierarchy = []; const membership = []; const paperNodes = new Map(); const owners = new Map(); let loaded = 0; const remaining = new Map(); const ownerLoaded = new Map();
  const expandedIds = [...expanded].filter(id => visible.has(id)).sort();
  for (const fieldId of expandedIds) {
    for (const child of snapshot.fields.filter(field => (field.parentIds || []).includes(fieldId) && visible.has(field.id)).sort((a, b) => a.id.localeCompare(b.id))) hierarchy.push({data: {id: `overview-hierarchy:${fieldId}:${child.id}`, source: fieldId, target: child.id, relation: 'hierarchy', directed: false}});
    const direct = snapshot.papers.filter(paper => (paper.fieldIds || []).includes(fieldId)).sort((a, b) => a.id.localeCompare(b.id));
    const wanted = direct.slice(0, Math.max(1, pages.get(fieldId) || 1) * CHILD_PAGE_SIZE); let count = 0;
    for (const paper of wanted) {
      const already = paperNodes.has(paper.id);
      if (!already) { if (loaded >= CHILD_TOTAL_LIMIT) continue; loaded += 1; const category = categoryForPaper(paper, snapshot, palette); paperNodes.set(paper.id, {data: {id: paperNodeId(paper.id), paperId: paper.id, label: paper.title, kind: 'paper', categoryId: category.id, categoryColor: category.color, categoryLabel: category.label, categoryPath: category.path.join(' / '), fieldIds: [...(paper.fieldIds || [])], additionalFields: (paper.fieldIds || []).filter(id => id !== category.id), owners: []}}); }
      const row = paperNodes.get(paper.id); if (!row.data.owners.includes(fieldId)) row.data.owners.push(fieldId); count += 1;
    }
    ownerLoaded.set(fieldId, count); remaining.set(fieldId, Math.max(0, direct.length - count));
  }
  for (const [paperId, row] of paperNodes) {
    const primary = row.data.categoryId; const paper = snapshot.papers.find(item => item.id === paperId);
    for (const fieldId of paper.fieldIds || []) if (nodeIds.has(fieldId)) membership.push({data: {id: `overview-membership:${fieldId}:${paperId}`, source: fieldId, target: paperNodeId(paperId), relation: 'membership', primary: fieldId === primary, owner: row.data.owners.includes(fieldId), directed: false}});
  }
  const loadedIds = new Set(paperNodes.keys());
  const semantic = snapshot.edges.filter(edge => edge.type === relation && loadedIds.has(edge.source) && loadedIds.has(edge.target)).sort((a, b) => a.id.localeCompare(b.id)).map(edge => ({data: {id: `overview-edge:${edge.id}`, source: paperNodeId(edge.source), target: paperNodeId(edge.target), relation: edge.type, directed: Boolean(edge.directed)}}));
  // Budget: hierarchy and membership are structural, then semantic, then aggregate ribbons fill the rest.
  const structural = [...hierarchy, ...membership].slice(0, edgeBudget); let room = edgeBudget - structural.length;
  const shownSemantic = semantic.slice(0, Math.max(0, room)); room -= shownSemantic.length;
  const shownRibbons = ribbonRows.slice(0, Math.max(0, room)).map((ribbon, index) => ({data: {id: `overview:${ribbon.sourceFieldId}>${ribbon.targetFieldId}:${index}`, source: ribbon.sourceFieldId, target: ribbon.targetFieldId, directed: Boolean(ribbon.directed), weight: ribbon.value, relation: 'aggregate'}}));
  const edges = [...shownRibbons, ...structural, ...shownSemantic];
  return {nodes: [...nodes, ...paperNodes.values()], edges, loadedPapers: loaded, ownerLoaded, remaining, counts: {fields: nodes.length, loadedPapers: loaded, hierarchyEdges: hierarchy.length, membershipEdges: membership.length, semanticEdges: semantic.length, eligibleRibbons: ribbonRows.length, displayedRibbons: shownRibbons.length, displayedEdges: edges.length, edgeBudget}};
}

export const AtlasOverviewExpansion = {CHILD_PAGE_SIZE, CHILD_TOTAL_LIMIT, VIEW_EDGE_BUDGET, overviewGraphProjection, paperNodeId, paperIdFromNode};
if (typeof window !== 'undefined') window.AtlasOverviewExpansion = AtlasOverviewExpansion;
