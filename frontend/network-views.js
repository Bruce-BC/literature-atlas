import cytoscape from 'cytoscape';
import {densityColor, densityRamp} from './density-view.js';
import {renderG6Network} from './g6-render.js';
import {networkLegendForNodes, networkZoomOptions, clampNetworkZoom} from './network-style.js';
import {graphProjection} from './projection.js';
import {completePositions, planCamera, saveCamera, topologySignature} from './camera-policy.js';

const cssColor = (computed, name) => computed.getPropertyValue(name).trim() || '#64748b';

function cytoscapeStyle(c) {
  return [
    {selector: 'node', style: {'background-color': 'data(categoryColor)', label: '', color: c.text, 'font-size': 12, 'text-wrap': 'wrap', 'text-max-width': 140, 'border-width': 2, 'border-color': c.panel}},
    {selector: 'node[?dimmed]', style: {opacity: 0.25}},
    {selector: 'node[outline]', style: {'border-color': 'data(outline)', 'border-width': 2}}, // density mode: a text-colour outline is a non-colour cue that keeps pale ramp steps visible
    {selector: 'node:selected', style: {label: 'data(label)', 'text-background-color': c.panel, 'text-background-opacity': 0.9, 'text-background-padding': 3, 'text-border-color': c.focus, 'text-border-width': 1, 'text-border-opacity': 1, 'border-color': c.focus, 'border-width': 4}},
    {selector: 'edge', style: {'line-color': c.border, width: 1.5, 'curve-style': 'bezier', 'target-arrow-shape': 'none'}},
    {selector: 'edge.directed', style: {'target-arrow-shape': 'triangle', 'target-arrow-color': c.border}}
  ];
}

const positionsOf = cy => Object.fromEntries(cy.nodes().map(node => { const p = node.position(); return [node.id(), {x: p.x, y: p.y}]; }));

/**
 * Cytoscape view that follows the camera policy:
 *  same scope+topology -> reuse the instance (style/selection only);
 *  same scope, topology add/remove -> add/remove real elements in place, seed new nodes, keep camera;
 *  different scope -> save camera, rebuild from the stored plan (fit on first visit, restore on revisit).
 */
function cytoscapeView({view, container, nodes, edges, colors, selectedId, onTap, store, epochKey, scopeKey, existing}) {
  const ids = nodes.map(node => node.data.id); const signature = topologySignature(ids, edges.map(edge => [edge.data.source, edge.data.target, edge.data.id]));
  const elements = [...nodes, ...edges];
  const remember = wrapper => { if (!wrapper.cy || wrapper.cy.destroyed()) return; saveCamera(store, {view, scopeKey: wrapper.scopeKey, camera: {zoom: wrapper.cy.zoom(), pan: {...wrapper.cy.pan()}}, positions: positionsOf(wrapper.cy)}); };
  if (existing && !existing.cy.destroyed() && existing.epochKey === epochKey) {
    if (existing.scopeKey === scopeKey) {
      const plan = planCamera(store, {view, epochKey, scopeKey, signature});
      const cy = existing.cy; existing.stats.renders += 1; existing.onTap = onTap; // current projection/snapshot, never the first render's closure
      if (existing.signature === signature) {
        cy.batch(() => { for (const node of nodes) cy.getElementById(node.data.id).data({categoryColor: node.data.categoryColor, dimmed: node.data.dimmed, outline: node.data.outline}); });
        cy.style(cytoscapeStyle(colors)).update(); existing.stats.reused += 1;
      } else {
        // Topology change: real add/remove of elements; existing positions are untouched.
        const wanted = new Set(ids); const edgeIds = new Set(edges.map(edge => edge.data.id));
        cy.batch(() => {
          cy.nodes().filter(node => !wanted.has(node.id())).remove(); cy.edges().filter(edge => !edgeIds.has(edge.id())).remove();
          const current = positionsOf(cy); const seeded = completePositions(current, ids, edges.map(edge => [edge.data.source, edge.data.target]), {spacing: 40});
          for (const node of nodes) if (cy.getElementById(node.data.id).empty()) cy.add({group: 'nodes', data: node.data, position: seeded[node.data.id]});
          for (const edge of edges) if (cy.getElementById(edge.data.id).empty() && !cy.getElementById(edge.data.source).empty() && !cy.getElementById(edge.data.target).empty()) cy.add({group: 'edges', data: edge.data, classes: edge.classes});
          for (const node of nodes) cy.getElementById(node.data.id).data({categoryColor: node.data.categoryColor, dimmed: node.data.dimmed, outline: node.data.outline});
        });
        cy.style(cytoscapeStyle(colors)).update(); existing.stats.topologyChanges += 1; existing.signature = signature; existing.fitNewContentAvailable = plan.fitNewContentAvailable;
      }
      cy.elements(':selected').unselect(); if (selectedId) cy.getElementById(selectedId).select();
      remember(existing); return existing;
    }
    remember(existing); existing.cy.destroy();
  } else if (existing && !existing.cy.destroyed()) existing.cy.destroy();
  const plan = planCamera(store, {view, epochKey, scopeKey, signature});
  const saved = plan.positions && Object.keys(plan.positions).length ? completePositions(plan.positions, ids, edges.map(edge => [edge.data.source, edge.data.target]), {spacing: 40}) : null;
  const layout = saved ? {name: 'preset', positions: saved, fit: false} : (nodes.length > 250 ? {name: 'grid', fit: true, avoidOverlap: true} : {name: 'cose', animate: false, fit: true});
  const cy = cytoscape({container, ...networkZoomOptions(), elements, layout, style: cytoscapeStyle(colors), userPanningEnabled: true, userZoomingEnabled: true});
  if (saved) { if (plan.camera?.zoom) { cy.zoom(clampNetworkZoom(plan.camera.zoom)); if (plan.camera.pan) cy.pan(plan.camera.pan); } else cy.fit(undefined, 24); }
  cy.zoom(clampNetworkZoom(cy.zoom()));
  const wrapper = {kind: 'cytoscape', cy, view, scopeKey, epochKey, signature, plan, fitNewContentAvailable: false, onTap, stats: {renders: 1, reused: 0, topologyChanges: 0}};
  cy.on('tap', 'node', event => wrapper.onTap(event.target.id()));
  let pending = false; cy.on('zoom pan dragfree', () => { if (pending) return; pending = true; requestAnimationFrame(() => { pending = false; remember(wrapper); }); });
  if (selectedId) cy.getElementById(selectedId).select();
  remember(wrapper); return wrapper;
}

export function g6View({view, container, nodes, edges, style, selectedId, onSelect, onError, store, epochKey, scopeKey, existing, revision, ariaLabel, extra = {}}) {
  const signature = topologySignature(nodes.map(node => node.data.id), edges.map(edge => [edge.data.source, edge.data.target, edge.data.id]));
  const remember = wrapper => { const camera = wrapper.g6.capture(); if (camera) saveCamera(store, {view, scopeKey: wrapper.scopeKey, camera, positions: camera.positions}); };
  if (existing && existing.epochKey === epochKey && existing.container === container) {
    if (existing.scopeKey === scopeKey) {
      const plan = planCamera(store, {view, epochKey, scopeKey, signature}); existing.plan = plan;
      const topologyChanged = existing.signature !== signature; existing.g6.setOnSelect(onSelect); existing.g6.update(nodes, edges, style, {revision}); existing.g6.select(selectedId); existing.signature = signature; existing.fitNewContentAvailable = existing.fitNewContentAvailable || topologyChanged || plan.fitNewContentAvailable; return existing; // camera is kept on topology change; the explicit control offers the fit
    }
    remember(existing); existing.g6.destroy();
  } else if (existing) existing.g6.destroy();
  const plan = planCamera(store, {view, epochKey, scopeKey, signature});
  const viewState = plan.action === 'fit' ? null : (plan.camera ? {...plan.camera, positions: plan.positions || plan.camera.positions} : null);
  const g6 = renderG6Network(container, nodes, edges, {...style, ...extra, viewState, selectedId, onSelect, onError, ariaLabel});
  return {kind: 'g6', g6, graph: g6.graph, container, view, scopeKey, epochKey, signature, plan, fitNewContentAvailable: false};
}

/** Lower paper/concept networks.  Colours come from the ACTUAL palette/theme context in `state.paletteContext`. */
export function renderNetworks(containers, snapshot, detail, state, callbacks) {
  const computed = getComputedStyle(document.getElementById('atlas-app') || document.documentElement); const palette = state.paletteContext || {series: Array.from({length: 6}, (_, index) => cssColor(computed, `--atlas-series-${index + 1}-hex`))};
  const scope = state.viewState?.scope; const forced = state.viewState?.detail?.forceVisible || {paperIds: [], conceptIds: []};
  const selectedConceptIds = [...new Set([...(state.selectedConceptId ? [state.selectedConceptId] : []), ...(forced.conceptIds || [])])]; const selectedPaper = state.selectedPaperId ? snapshot.papers.find(paper => paper.id === state.selectedPaperId) : null;
  const mandatoryConceptIds = selectedPaper?.conceptIds || [];
  const graph = graphProjection(snapshot, detail, {maxConceptNodes: Math.max(30, Math.ceil(300 * state.fraction)), selectedConceptIds, mandatoryConceptIds, selectedPaperId: state.selectedPaperId, selectedEdgeId: state.selectedEdgeId, forcedPaperIds: forced.paperIds || [], fieldId: state.fieldId, categoryPalette: palette});
  const colors = {text: cssColor(computed, '--atlas-text-hex'), panel: cssColor(computed, '--atlas-panel-hex'), border: cssColor(computed, '--atlas-border-hex'), focus: cssColor(computed, '--atlas-focus-hex'), series4: cssColor(computed, '--atlas-series-4-hex')};
  const drawLegend = (container, nodes, label) => { let legend = container.parentElement.querySelector('.atlas-network-legend'); if (!legend) { legend = document.createElement('div'); legend.className = 'atlas-network-legend'; container.parentElement.append(legend); } legend.replaceChildren(); const title = document.createElement('span'); title.className = 'atlas-network-legend-title'; title.textContent = `${label} categories`; legend.append(title); for (const category of networkLegendForNodes(nodes.map(node => node.data))) { const item = document.createElement('span'); item.className = 'atlas-network-legend-item'; const swatch = document.createElement('i'); swatch.className = 'atlas-network-legend-swatch'; swatch.style.background = category.color || '#64748b'; swatch.dataset.categoryColor = category.color || ''; const text = document.createElement('span'); text.textContent = `${category.label}${category.inferred ? ' · inferred' : ''} (${category.count})`; item.append(swatch, text); legend.append(item); } };
  const conceptNodes = [...graph.conceptNodes, ...graph.conceptPaperNodes];
  // The concept graph reuses paper IDs for connected papers; the paper graph owns the plain ID, so distinct DOM/G6 IDs are not needed (separate graphs).
  if (state.colorMode === 'density' && state.paperDensity) { const rows = new Map(state.paperDensity.rows.map(row => [row.id, row])); const max = Math.max(0, ...state.paperDensity.rows.map(row => row.degree)); for (const node of graph.paperNodes) { const degree = rows.get(node.data.id)?.degree || 0; node.data.categoryColor = densityColor(degree, max); node.data.outline = colors.text; node.data.degree = degree; node.data.degreePossible = Math.max(0, state.paperDensity.counts.papers - 1); } }
  drawLegend(containers.paper, graph.paperNodes, 'Paper'); drawLegend(containers.concept, conceptNodes, 'Concept');
  if (state.colorMode === 'density' && state.paperDensity) { const legend = containers.paper.parentElement.querySelector('.atlas-network-legend'); legend.replaceChildren(); const title = document.createElement('span'); title.textContent = `Observed degree (slate ramp, not a category) · 0 to ${Math.max(0, ...state.paperDensity.rows.map(row => row.degree))} / ${Math.max(0, state.paperDensity.counts.papers - 1)} possible`; legend.append(title); for (const color of densityRamp()) { const swatch = document.createElement('i'); swatch.className = 'atlas-network-legend-swatch'; swatch.style.background = color.hex; legend.append(swatch); } }
  const epochKey = state.viewState?.source || ''; const scopeKey = `${state.viewState?.camera?.scopeKey || 'library'}`; const store = state.cameraStore; const revision = state.viewState?.revision ?? 0;
  const paperEdges = graph.paperEdges; const conceptEdges = graph.conceptEdges;
  const paperSelect = id => callbacks.select({kind: 'paper', value: graph.paperById.get(id)});
  const conceptSelect = id => { const concept = graph.conceptById.get(id); const paper = graph.paperById.get(id); if (concept) callbacks.select({kind: 'concept', value: concept}); else if (paper) callbacks.select({kind: 'paper', value: paper}); };
  const result = {graph, paper: null, concept: null, cyPaper: null, cyConcept: null, g6Paper: null, g6Concept: null};
  if (state.renderer === 'g6') {
    for (const wrapper of [state.cyPaperView, state.cyConceptView]) wrapper?.cy.destroy();
    const style = {textColor: colors.text, panelColor: colors.panel, borderColor: colors.border, focusColor: colors.focus, seriesColor: colors.focus};
    result.paper = g6View({view: 'g6-paper', container: containers.paper, nodes: graph.paperNodes, edges: paperEdges, style, selectedId: state.selectedPaperId, onSelect: paperSelect, onError: callbacks.error, store, epochKey, scopeKey, existing: state.g6PaperView, revision, ariaLabel: 'Paper network'});
    result.concept = g6View({view: 'g6-concept', container: containers.concept, nodes: conceptNodes, edges: conceptEdges, style: {...style, seriesColor: colors.series4}, selectedId: state.selectedConceptId, onSelect: conceptSelect, onError: callbacks.error, store, epochKey, scopeKey, existing: state.g6ConceptView, revision, ariaLabel: 'Concept network'});
    result.g6Paper = result.paper.g6; result.g6Concept = result.concept.g6; return result;
  }
  for (const wrapper of [state.g6PaperView, state.g6ConceptView]) wrapper?.g6.destroy();
  result.paper = cytoscapeView({view: 'cy-paper', container: containers.paper, nodes: graph.paperNodes, edges: paperEdges, colors, selectedId: state.selectedPaperId, onTap: paperSelect, store, epochKey, scopeKey, existing: state.cyPaperView});
  result.concept = cytoscapeView({view: 'cy-concept', container: containers.concept, nodes: conceptNodes, edges: conceptEdges, colors, selectedId: state.selectedConceptId, onTap: conceptSelect, store, epochKey, scopeKey, existing: state.cyConceptView});
  result.cyPaper = result.paper.cy; result.cyConcept = result.concept.cy; return result;
}

export const AtlasNetworkViews = {renderNetworks};
