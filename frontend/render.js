import * as d3 from 'd3';
import cytoscape from 'cytoscape';
import {densityColor, densityRamp} from './density-view.js';
import {renderG6Network} from './g6-render.js';
import {networkLegendForNodes, clampNetworkZoom, networkZoomOptions, fieldPresentation} from './network-style.js';
import {displayLabel, safeText} from './decoder.js';
import {circularLayout, graphProjection, overviewProjection} from './projection.js';

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
  const computed = getComputedStyle(document.getElementById('atlas-app') || document.documentElement); const fieldPalette = {series: Array.from({length:6}, (_, index) => computed.getPropertyValue(`--atlas-series-${index+1}-hex`).trim() || '#64748b')};
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

export function renderNetworks(containers, snapshot, detail, state, callbacks) {
  const selectedConceptIds = state.selectedConceptId ? [state.selectedConceptId] : []; const mandatoryConceptIds = state.selectedPaperId ? (snapshot.papers.find(paper => paper.id === state.selectedPaperId)?.conceptIds || []) : []; const computed = getComputedStyle(document.getElementById('atlas-app') || document.documentElement); const color = name => computed.getPropertyValue(name).trim() || '#64748b'; const series = Array.from({length: 6}, (_, index) => color(`--atlas-series-${index + 1}-hex`)); const graph = graphProjection(snapshot, detail, {maxConceptNodes: Math.max(30, Math.ceil(300 * state.fraction)), selectedConceptIds, mandatoryConceptIds, selectedPaperId: state.selectedPaperId, fieldId: state.fieldId, categoryPalette: {series}}); const elements = [...graph.paperNodes, ...graph.paperEdges];
  const textColor = color('--atlas-text-hex'); const panelColor = color('--atlas-panel-hex'); const borderColor = color('--atlas-border-hex'); const focusColor = color('--atlas-focus-hex'); const series4 = color('--atlas-series-4-hex'); const series1 = color('--atlas-series-1-hex');
  const drawLegend = (container, nodes, label) => { let legend = container.parentElement.querySelector('.atlas-network-legend'); if (!legend) { legend = document.createElement('div'); legend.className = 'atlas-network-legend'; container.parentElement.append(legend); } legend.replaceChildren(); const title = document.createElement('span'); title.className = 'atlas-network-legend-title'; title.textContent = `${label} categories`; legend.append(title); for (const category of networkLegendForNodes(nodes.map(node => node.data))) { const item = document.createElement('span'); item.className = 'atlas-network-legend-item'; const swatch = document.createElement('i'); swatch.className = 'atlas-network-legend-swatch'; swatch.style.background = category.color || '#64748b'; const text = document.createElement('span'); text.textContent = `${category.label}${category.inferred ? ' · inferred' : ''} (${category.count})`; item.append(swatch, text); legend.append(item); } };
  if (state.colorMode === 'density' && state.paperDensity) { const rows = new Map(state.paperDensity.rows.map(row => [row.id, row])); const max = Math.max(0, ...state.paperDensity.rows.map(row => row.degree)); for (const node of graph.paperNodes) { const degree=rows.get(node.data.id)?.degree || 0; node.data.categoryColor=densityColor(degree,max); node.data.degree=degree; node.data.degreePossible=Math.max(0,state.paperDensity.counts.papers-1); } }
  drawLegend(containers.paper, graph.paperNodes, 'Paper'); drawLegend(containers.concept, [...graph.conceptNodes, ...graph.conceptPaperNodes], 'Concept');
  if (state.colorMode === 'density' && state.paperDensity) { const legend=containers.paper.parentElement.querySelector('.atlas-network-legend');legend.replaceChildren();const title=document.createElement('span');title.textContent=`Observed degree · 0 to ${Math.max(0,...state.paperDensity.rows.map(row=>row.degree))} / ${Math.max(0,state.paperDensity.counts.papers-1)} possible`;legend.append(title);for(const color of densityRamp()){const swatch=document.createElement('i');swatch.className='atlas-network-legend-swatch';swatch.style.background=color.hex;legend.append(swatch);} }
  if (state.renderer === 'g6') {
    const signature = (nodes, edges) => JSON.stringify([nodes.map(node => node.data.id), edges.map(edge => [edge.data.id, edge.data.source, edge.data.target])]);
    const paperSignature = signature(graph.paperNodes, graph.paperEdges);
    const conceptNodes = [...graph.conceptNodes, ...graph.conceptPaperNodes]; const conceptSignature = signature(conceptNodes, graph.conceptEdges);
    let g6Paper = state.g6Paper; let g6Concept = state.g6Concept;
    if (g6Paper && g6Paper.signature !== paperSignature) { g6Paper.destroy(); g6Paper = null; }
    if (g6Concept && g6Concept.signature !== conceptSignature) { g6Concept.destroy(); g6Concept = null; }
    if (g6Paper) { g6Paper.restyle(graph.paperNodes, graph.paperEdges, {textColor, panelColor, borderColor, focusColor, seriesColor: focusColor}); g6Paper.select(state.selectedPaperId); }
    else { g6Paper = renderG6Network(containers.paper, graph.paperNodes, graph.paperEdges, {textColor, panelColor, borderColor, focusColor, seriesColor: focusColor, viewState: state.g6Views?.[state.g6PaperKind], onError: callbacks.error, selectedId: state.selectedPaperId, onSelect: id => callbacks.select({kind: 'paper', value: graph.paperById.get(id)})}); g6Paper.signature = paperSignature; }
    if (g6Concept) { g6Concept.restyle(conceptNodes, graph.conceptEdges, {textColor, panelColor, borderColor, focusColor, seriesColor: series4}); g6Concept.select(state.selectedConceptId); }
    else { g6Concept = renderG6Network(containers.concept, conceptNodes, graph.conceptEdges, {textColor, panelColor, borderColor, focusColor, seriesColor: series4, viewState: state.g6Views?.concept, onError: callbacks.error, selectedId: state.selectedConceptId, onSelect: id => { const concept = graph.conceptById.get(id); const paper = graph.paperById.get(id); if (concept) callbacks.select({kind: 'concept', value: concept}); else if (paper) callbacks.select({kind: 'paper', value: paper}); }}); g6Concept.signature = conceptSignature; }
    return {cyPaper: null, cyConcept: null, g6Paper, g6Concept, graph};
  }
  const paperLayout = state.cyPositions && Object.keys(state.cyPositions).length ? {name: 'preset', positions: state.cyPositions, fit: true} : (graph.paperNodes.length > 250 ? {name: 'grid', fit: true, avoidOverlap: true} : {name: 'cose', animate: false});
  const cyPaper = cytoscape({container: containers.paper, ...networkZoomOptions(), elements, layout: paperLayout, style: [
    {selector: 'node', style: {'background-color': 'data(categoryColor)', label: '', color: textColor, 'font-size': 12, 'text-wrap': 'wrap', 'text-max-width': 140, 'border-width': 2, 'border-color': panelColor}},
    {selector: 'node:selected', style: {label: 'data(label)', 'text-background-color': panelColor, 'text-background-opacity': 0.9, 'text-background-padding': 3, 'text-border-color': focusColor, 'text-border-width': 1, 'text-border-opacity': 1}},
    {selector: 'edge', style: {'line-color': borderColor, width: 1.5, 'curve-style': 'bezier', 'target-arrow-shape': 'none'}},
    {selector: 'edge.directed', style: {'target-arrow-shape': 'triangle'}},
    {selector: '.is-selected', style: {'border-color': focusColor, 'border-width': 4}}
  ], userPanningEnabled: true, userZoomingEnabled: true});
  cyPaper.on('tap', 'node', event => callbacks.select({kind: 'paper', value: graph.paperById.get(event.target.id())}));
  const conceptElements = [...graph.conceptNodes, ...graph.conceptPaperNodes, ...graph.conceptEdges];
  const conceptLayout = state.conceptPositions && Object.keys(state.conceptPositions).length ? {name: 'preset', positions: state.conceptPositions, fit: true} : (graph.conceptNodes.length + graph.conceptPaperNodes.length > 200 ? {name: 'grid', fit: true, avoidOverlap: true} : {name: 'cose', animate: false});
  const cyConcept = cytoscape({container: containers.concept, ...networkZoomOptions(), elements: conceptElements, layout: conceptLayout, style: [
    {selector: 'node[kind = "concept"]', style: {'background-color': 'data(categoryColor)', label: '', color: textColor, 'font-size': 12}},
    {selector: 'node[kind = "paper"]', style: {'background-color': 'data(categoryColor)', label: '', color: textColor, 'font-size': 12}},
    {selector: 'node:selected', style: {label: 'data(label)', 'text-background-color': panelColor, 'text-background-opacity': 0.9, 'text-background-padding': 3, 'text-border-color': focusColor, 'text-border-width': 1, 'text-border-opacity': 1}},
    {selector: 'edge', style: {'line-color': borderColor, width: 1}}
  ], userPanningEnabled: true, userZoomingEnabled: true});
  cyConcept.on('tap', 'node', event => { const concept = graph.conceptById.get(event.target.id()); if (concept) callbacks.select({kind: 'concept', value: concept}); });
  return {cyPaper, cyConcept, graph};
}

export function renderList(node, rows, label, onSelect, selectedId = null) {
  clear(node); if (!rows.length) { const empty = document.createElement('p'); empty.className = 'atlas-empty'; empty.textContent = 'No matching records'; node.append(empty); return; }
  for (const row of rows) { const button = document.createElement('button'); button.type = 'button'; button.className = 'atlas-list-item'; button.dataset.entityId = row.id; button.setAttribute('aria-pressed', String(selectedId === row.id)); const title = document.createElement('span'); title.textContent = label(row); const meta = document.createElement('small'); meta.textContent = row.year ? String(row.year) : row.source || ''; button.append(title, meta); button.addEventListener('click', () => onSelect(row)); node.append(button); }
}

export function renderRelationList(node, edges, paperById, onSelect) {
  clear(node); if (!edges.length) { const empty = document.createElement('p'); empty.className = 'atlas-empty'; empty.textContent = 'No relationships'; node.append(empty); return; }
  for (const edge of edges.slice(0, 80)) { const button = document.createElement('button'); button.type = 'button'; button.className = 'atlas-list-item relation-item'; const source = paperById.get(edge.source); const target = paperById.get(edge.target); button.textContent = `${edge.type} · ${safeText(source?.title, edge.source)} → ${safeText(target?.title, edge.target)}`; button.dataset.relationType = edge.type; button.addEventListener('click', () => onSelect(edge)); node.append(button); }
}

export const AtlasRender = {renderCircular, renderNetworks, renderList, renderRelationList};

export function renderG6Overview(container, snapshot, state, callbacks, existing = null) {
  const computed = getComputedStyle(document.getElementById('atlas-app') || document.documentElement);
  const color = name => computed.getPropertyValue(name).trim() || '#64748b';
  const palette = {series: Array.from({length:6}, (_, index) => color(`--atlas-series-${index+1}-hex`))};
  const overview = overviewProjection(snapshot, state.paperRelation);
  const fields = new Map(snapshot.fields.map(field => [field.id, field]));
  const nodes = overview.fields.map(field => ({data: {id: field.id, label: field.label?.en || field.label?.ko || field.id, categoryColor: fieldPresentation(fields.get(field.id), snapshot, palette).color, categoryLabel: field.label?.en || field.label?.ko || field.id}}));
  const edges = overview.ribbons.map((ribbon, index) => ({data: {id: `overview:${index}`, source: ribbon.sourceFieldId, target: ribbon.targetFieldId, directed: ribbon.directed, weight: ribbon.value}}));
  if (existing) { existing.restyle(nodes, edges, {textColor: color('--atlas-text-hex'), panelColor: color('--atlas-panel-hex'), borderColor: color('--atlas-border-hex'), focusColor: color('--atlas-focus-hex'), seriesColor: undefined}); existing.select(state.selectedFieldId); return existing; }
  return renderG6Network(container, nodes, edges, {textColor: color('--atlas-text-hex'), panelColor: color('--atlas-panel-hex'), borderColor: color('--atlas-border-hex'), focusColor: color('--atlas-focus-hex'), viewState: state.g6Views?.overview, selectedId: state.selectedFieldId, onError: callbacks.error, onSelect: id => { const field = fields.get(id); if (field) callbacks.select({kind: 'field', value: field}); }});
}
