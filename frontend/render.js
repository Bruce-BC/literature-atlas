import * as d3 from 'd3';
import {g6View} from './network-views.js';
import {overviewGraphProjection, paperIdFromNode, paperNodeId} from './overview-expansion.js';
import {fieldPresentation} from './network-style.js';
import {displayLabel, safeText} from './decoder.js';
import {circularLayout, overviewProjection} from './projection.js';
import {allPaperOverviewProjection} from './all-paper-overview.js';

const NS = 'http://www.w3.org/2000/svg';
const svgEl = (tag, attrs = {}) => { const node = document.createElementNS(NS, tag); for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value)); return node; };
const clear = node => { while (node.firstChild) node.removeChild(node.firstChild); };
const emit = (target, type, detail) => target.dispatchEvent(new CustomEvent(type, {bubbles: true, detail}));

export function renderCircular(svg, snapshot, state) {
  clear(svg);
  const width = Math.max(320, svg.clientWidth || 640); const height = Math.max(320, svg.clientHeight || 520);
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`); svg.setAttribute('role', 'img');
  const group = svgEl('g', {transform: `translate(${width / 2 + state.panX},${height / 2 + state.panY}) scale(${state.zoom})`}); svg.append(group);
  const projection = overviewProjection(snapshot, state.relationType || 'semantic'); const layout = circularLayout(projection, width, height); const radius = layout.radius;
  const computed = getComputedStyle(document.getElementById('atlas-app') || document.documentElement); const fieldPalette = state.paletteContext || {series: Array.from({length:6}, (_, index) => computed.getPropertyValue(`--atlas-series-${index+1}-hex`).trim() || '#64748b')};
  const arc = d3.arc().innerRadius(radius).outerRadius(radius + 24);
  const ribbon = d3.ribbon().radius(radius - 12); const ribbonArrow = d3.ribbonArrow().radius(radius - 12).headRadius(7);
  const arcById = new Map(layout.arcs.map(item => [item.id, item])); const parents = new Map(); for (const field of layout.arcs) for (const parentId of field.parentIds || []) { const parentArc = arcById.get(parentId); if (!parentArc) continue; const group = parents.get(parentId) || {field: parentArc, children: []}; group.children.push(field); parents.set(parentId, group); }
  const parentArc = d3.arc().innerRadius(radius + 28).outerRadius(radius + 34); for (const groupInfo of parents.values()) { const startAngle = Math.min(...groupInfo.children.map(child => child.startAngle), groupInfo.field.startAngle); const endAngle = Math.max(...groupInfo.children.map(child => child.endAngle), groupInfo.field.endAngle); const headerArc = {...groupInfo.field, startAngle, endAngle}; const header = svgEl('path', {class: 'atlas-parent-block', d: parentArc(headerArc), 'data-field-parent-id': groupInfo.field.id, 'aria-label': `Field group ${displayLabel(groupInfo.field)}`}); group.append(header); const mid = (startAngle + endAngle) / 2; const label = svgEl('text', {class: 'atlas-parent-label', x: Math.sin(mid) * (radius + 42), y: -Math.cos(mid) * (radius + 42), 'text-anchor': Math.sin(mid) >= 0 ? 'start' : 'end'}); label.textContent = displayLabel(groupInfo.field); group.append(label); }
  for (const link of layout.links) {
    const source = {startAngle: link.sourceAngles[0], endAngle: link.sourceAngles[1]}; const target = {startAngle: link.targetAngles[0], endAngle: link.targetAngles[1]}; const directed = Boolean(link.directed); const path = svgEl('path', {class: `atlas-ribbon ${directed ? 'atlas-ribbon-directed' : 'atlas-ribbon-undirected'}`, d: (directed ? ribbonArrow : ribbon)({source, target}), 'data-edge-id': `${link.sourceFieldId}:${link.targetFieldId}:${link.relationType}`, 'data-relation': link.relationType, 'data-directed': String(directed), tabindex: 0, role: 'button', 'aria-label': `${safeText(displayLabel(layout.arcs.find(a => a.id === link.sourceFieldId)))} to ${safeText(displayLabel(layout.arcs.find(a => a.id === link.targetFieldId)))} relationship`});
    path.addEventListener('click', () => emit(svg, 'atlas:select', {kind: 'ribbon', value: link})); path.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); emit(svg, 'atlas:select', {kind: 'ribbon', value: link}); } }); group.append(path);
  }
  const labelSpecs = [];
  for (const field of layout.arcs) {
    const path = svgEl('path', {class: 'atlas-sector', d: arc(field), fill: fieldPresentation(field, snapshot, fieldPalette).color, 'data-field-id': field.id, tabindex: 0, role: 'button', 'aria-current': state.fieldId === field.id ? 'true' : 'false', 'aria-label': `${safeText(displayLabel(field))}, ${field.value} papers`});
    path.addEventListener('click', () => emit(svg, 'atlas:select', {kind: 'field', value: field})); path.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); emit(svg, 'atlas:select', {kind: 'field', value: field}); } }); group.append(path);
    const mid = (field.startAngle + field.endAngle) / 2; const labelText = displayLabel(field); const estimate = Math.min(120, Math.max(42, labelText.length * 7)); const side = Math.sin(mid) >= 0 ? 1 : -1; const maxX = width / 2 - estimate - 8; const x = side * Math.min(radius + 40, Math.max(20, maxX)); const desiredY = -Math.cos(mid) * (radius + 40); labelSpecs.push({field, labelText, estimate, side, x, desiredY});
  }
  labelSpecs.sort((a, b) => a.desiredY - b.desiredY); let previousY = -Infinity; for (const spec of labelSpecs) { let y = Math.max(-height / 2 + 16, Math.min(height / 2 - 16, spec.desiredY)); if (y - previousY < 16) y = previousY + 16; y = Math.min(height / 2 - 16, y); previousY = y; const label = svgEl('text', {class: 'atlas-sector-label', x: spec.x, y, 'text-anchor': spec.side > 0 ? 'start' : 'end'}); label.textContent = spec.labelText; const title = svgEl('title'); title.textContent = spec.labelText; label.append(title); group.append(label); }
  const center = svgEl('text', {class: 'atlas-center-label', 'text-anchor': 'middle', y: 4}); center.textContent = `${projection.totalPapers} papers`; group.append(center);
  return {projection, layout, sectorCount: layout.arcs.length, ribbonCount: layout.links.length, pathCount: svg.querySelectorAll('path').length};
}

export function renderList(node, rows, label, onSelect, selectedId = null) {
  clear(node); if (!rows.length) { const empty = document.createElement('p'); empty.className = 'atlas-empty'; empty.textContent = 'No matching records'; node.append(empty); return; }
  for (const row of rows) { const button = document.createElement('button'); button.type = 'button'; button.className = 'atlas-list-item'; button.dataset.entityId = row.id; button.setAttribute('aria-pressed', String(selectedId === row.id)); const title = document.createElement('span'); title.textContent = label(row); const meta = document.createElement('small'); meta.textContent = row.year ? String(row.year) : row.source || ''; button.append(title, meta); button.addEventListener('click', () => onSelect(row)); node.append(button); }
}

export function renderRelationList(node, edges, paperById, onSelect) {
  clear(node); if (!edges.length) { const empty = document.createElement('p'); empty.className = 'atlas-empty'; empty.textContent = 'No relationships'; node.append(empty); return; }
  for (const edge of edges.slice(0, 80)) { const button = document.createElement('button'); button.type = 'button'; button.className = 'atlas-list-item relation-item'; const source = paperById.get(edge.source); const target = paperById.get(edge.target); button.textContent = `${edge.type} · ${safeText(source?.title, edge.source)} → ${safeText(target?.title, edge.target)}`; button.dataset.relationType = edge.type; button.addEventListener('click', () => onSelect(edge)); node.append(button); }
}

export {renderNetworks} from './network-views.js';

const cssVar = (computed, name) => computed.getPropertyValue(name).trim() || '#64748b';
const styleFrom = computed => ({textColor: cssVar(computed, '--atlas-text-hex'), panelColor: cssVar(computed, '--atlas-panel-hex'), borderColor: cssVar(computed, '--atlas-border-hex'), focusColor: cssVar(computed, '--atlas-focus-hex'), seriesColor: undefined});

/** Representative (field) overview with bounded expansion.  `existing` is the previous view wrapper. */
export function renderG6Overview(container, snapshot, state, callbacks, existing = null) {
  const computed = getComputedStyle(document.getElementById('atlas-app') || document.documentElement); const palette = state.paletteContext || {};
  const projection = overviewGraphProjection(snapshot, {relation: state.paperRelation, expanded: state.viewState?.expanded || new Set(), pages: state.childPages || new Map(), palette});
  const select = id => {
    const paperId = paperIdFromNode(id);
    if (paperId) { const paper = snapshot.papers.find(row => row.id === paperId); const node = projection.nodes.find(row => row.data.id === id); const scopeField = state.viewState?.scope?.fieldId; const owners = node?.data.owners || []; callbacks.select({kind: 'paper', value: paper, ownerId: owners.includes(scopeField) ? scopeField : owners[0] || null}); return; }
    const field = snapshot.fields.find(row => row.id === id); if (field) callbacks.toggleRepresentative(field.id);
  };
  const selectedId = state.selectedPaperId && projection.nodes.some(row => row.data.id === paperNodeId(state.selectedPaperId)) ? paperNodeId(state.selectedPaperId) : (state.viewState?.selected?.kind === 'field' ? state.viewState.selected.id : null);
  const view = g6View({view: 'g6-overview', container, nodes: projection.nodes, edges: projection.edges, style: styleFrom(computed), selectedId, onSelect: select, onError: callbacks.error, store: state.cameraStore, epochKey: state.viewState?.source || '', scopeKey: 'overview', existing, revision: state.viewState?.revision ?? 0, ariaLabel: 'Field overview'}); // constant camera scope: expanding a representative is a topology change on the SAME graph, so the clicked node never jumps
  view.projection = projection; return view;
}

/** 전체 논문 지도: one deterministic point per active paper, preset coordinates, no force layout. */
export function renderG6AllPaperOverview(container, snapshot, state, callbacks, existing = null) {
  const computed = getComputedStyle(document.getElementById('atlas-app') || document.documentElement);
  const projection = allPaperOverviewProjection(snapshot, {focusScope: state.viewState?.scope?.kind === 'field' ? state.viewState.scope : null, relation: state.paperRelation, categoryPalette: state.paletteContext || {}});
  const select = id => { const paperId = paperIdFromNode(id); const paper = snapshot.papers.find(row => row.id === paperId); if (paper) callbacks.select({kind: 'overview-paper', value: paper}); };
  const selectedId = state.selectedPaperId ? paperNodeId(state.selectedPaperId) : null;
  // Scope key is constant: field focus dims points, it never rebuilds or refits the global map.
  const view = g6View({view: 'g6-all-papers', container, nodes: projection.nodes, edges: projection.edges, style: {...styleFrom(computed), nodeSize: 8, labels: false}, selectedId, onSelect: select, onError: callbacks.error, store: state.cameraStore, epochKey: state.viewState?.source || '', scopeKey: 'all-papers', existing, revision: state.viewState?.revision ?? 0, ariaLabel: '전체 논문 지도', extra: {layoutType: 'preset', hitRadius: 9}});
  view.projection = projection; return view;
}

/** Points currently inside the visible container (informational; never changes represented N). */
export function viewportPointCount(view, container) {
  const graph = view?.g6?.graph; if (!graph || !container || container.dataset.rendererState !== 'ready') return null; const rect = container.getBoundingClientRect(); let inside = 0;
  for (const node of graph.getNodeData()) { const [x, y] = graph.getClientByCanvas(graph.getElementPosition(node.id)); if (x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom) inside += 1; }
  return inside;
}

export const AtlasRender = {renderCircular, renderList, renderRelationList, renderG6Overview, renderG6AllPaperOverview, viewportPointCount};
