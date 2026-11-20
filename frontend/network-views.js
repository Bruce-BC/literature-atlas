import cytoscape from 'cytoscape';
import {densityColor, densityRamp} from './density-view.js';
import {renderG6Network} from './g6-render.js';
import {networkLegendForNodes, networkZoomOptions, clampNetworkZoom} from './network-style.js';
import {graphProjection} from './projection.js';
import {TIER_STYLE, withEdgeTiers} from './edge-strength.js';
import {clampPan, completePositions, planCamera, saveCamera, topologySignature} from './camera-policy.js';
import {HUB_MIN_DEGREE, TIER_LINKS, markHubs, namedWhenShown} from './node-emphasis.js';
import {iconUri} from './node-icons.js';

export const CY_EDGE_CURVE = 12; // px: every Cytoscape edge is a slightly bent curve
const CY_SIZE_SCALE = 1.5; // Cytoscape nodes are drawn larger than the shared base size
const CY_SHAPES = {circle: 'ellipse', diamond: 'diamond', hexagon: 'hexagon', star: 'star'};
/** Renderer-neutral emphasis -> the Cytoscape data fields the stylesheet reads. */
const cyNode = (node, named, iconColor) => { const iconUriValue = node.data.icon ? iconUri(node.data.icon, iconColor) : ''; return {...node, data: {...node.data, cyShape: CY_SHAPES[node.data.shape] || 'ellipse', cySize: node.data.size * CY_SIZE_SCALE, named: Boolean(named), ...(iconUriValue ? {iconUri: iconUriValue} : {}), ...(node.data.tierLineWidth ? {cyBorder: node.data.tierLineWidth + 1} : {})}}; }; // tier 2-3 nodes carry a Lucide icon (node-icons.js)
const CY_LIVE_FIELDS = ['categoryColor', 'dimmed', 'outline', 'cyShape', 'cySize', 'named', 'hub', 'links', 'iconUri', 'cyBorder'];
const liveData = data => Object.fromEntries(CY_LIVE_FIELDS.map(key => [key, data[key] ?? null]));

const cssColor = (computed, name) => computed.getPropertyValue(name).trim() || '#64748b';

/** Lower Cytoscape networks get only a 150 ms style transition on node clicks (plan motion-and-edges §3.4), 0 under reduced motion. */
export const CY_TRANSITION_S = 0.15;
const reducedMotion = () => typeof window !== 'undefined' && Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches);
export function cytoscapeStyle(c, {reduced = reducedMotion()} = {}) {
  return [
    {selector: 'node', style: {'background-color': 'data(categoryColor)', label: '', color: c.text, 'font-size': 12, 'text-wrap': 'wrap', 'text-max-width': 140, 'border-width': 2, 'border-color': c.panel, 'transition-property': 'opacity, border-width, border-color, background-color', 'transition-duration': reduced ? 0 : CY_TRANSITION_S}},
    {selector: 'node[cySize]', style: {shape: 'data(cyShape)', width: 'data(cySize)', height: 'data(cySize)'}}, // hubs (3+ displayed links) change shape and grow; concepts are diamonds
    {selector: 'node[iconUri]', style: {'background-image': 'data(iconUri)', 'background-fit': 'none', 'background-width': '52%', 'background-height': '52%', 'background-clip': 'none', 'background-image-containment': 'over'}},
    {selector: 'node[cyBorder]', style: {'border-width': 'data(cyBorder)'}},
    {selector: 'node[?named]', style: {label: 'data(label)', 'text-valign': 'bottom', 'text-margin-y': 4, 'font-size': 10, 'text-max-width': 110, 'text-background-color': c.panel, 'text-background-opacity': 0.8, 'text-background-padding': 2}},
    {selector: 'node[?dimmed]', style: {opacity: 0.25}},
    {selector: 'node[outline]', style: {'border-color': 'data(outline)', 'border-width': 2}}, // density mode: a text-colour outline is a non-colour cue that keeps pale ramp steps visible
    {selector: 'node:selected', style: {label: 'data(label)', 'text-background-color': c.panel, 'text-background-opacity': 0.9, 'text-background-padding': 3, 'text-border-color': c.focus, 'text-border-width': 1, 'text-border-opacity': 1, 'border-color': c.focus, 'border-width': 4}},
    {selector: 'edge', style: {'line-color': c.border, width: 1.5, 'curve-style': 'unbundled-bezier', 'control-point-distances': CY_EDGE_CURVE, 'control-point-weights': 0.5, 'target-arrow-shape': 'none'}},
    {selector: 'edge.directed', style: {'target-arrow-shape': 'triangle', 'target-arrow-color': c.border}},
    // plan §2.2 strength tiers (semantic + keyword co-occurrence only; citation/membership carry no tier and keep the rules above)
    ...Object.entries(TIER_STYLE).map(([tier, style]) => ({selector: `edge[tier = "${tier}"]`, style: {width: style.width, opacity: style.opacity, 'line-style': style.dash ? 'dashed' : 'solid', ...(style.dash ? {'line-dash-pattern': [...style.dash]} : {})}}))
  ];
}

/** Pan that keeps the node bbox (model coords, node size included) visible; see `clampPan`. */
const cyClampedPan = cy => clampPan({viewport: {width: cy.width(), height: cy.height()}, bbox: cy.nodes().length ? cy.nodes().boundingBox({includeLabels: false, includeOverlays: false}) : null, zoom: cy.zoom(), pan: cy.pan()});
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
  const remember = wrapper => { if (!wrapper.cy || wrapper.cy.destroyed()) return; saveCamera(store, {view, scopeKey: wrapper.scopeKey, camera: {zoom: wrapper.cy.zoom(), pan: cyClampedPan(wrapper.cy)}, positions: positionsOf(wrapper.cy)}); };
  if (existing && !existing.cy.destroyed() && existing.epochKey === epochKey) {
    if (existing.scopeKey === scopeKey) {
      const plan = planCamera(store, {view, epochKey, scopeKey, signature});
      const cy = existing.cy; existing.stats.renders += 1; existing.onTap = onTap; // current projection/snapshot, never the first render's closure
      if (existing.signature === signature) {
        cy.batch(() => { for (const node of nodes) cy.getElementById(node.data.id).data(liveData(node.data)); for (const edge of edges) cy.getElementById(edge.data.id).data({tier: edge.data.tier ?? null}); }); // a new snapshot can move an edge to another tier
        cy.style(cytoscapeStyle(colors)).update(); existing.stats.reused += 1;
      } else {
        // Topology change: real add/remove of elements; existing positions are untouched.
        const wanted = new Set(ids); const edgeIds = new Set(edges.map(edge => edge.data.id));
        cy.batch(() => {
          cy.nodes().filter(node => !wanted.has(node.id())).remove(); cy.edges().filter(edge => !edgeIds.has(edge.id())).remove();
          const current = positionsOf(cy); const seeded = completePositions(current, ids, edges.map(edge => [edge.data.source, edge.data.target]), {spacing: 40});
          for (const node of nodes) if (cy.getElementById(node.data.id).empty()) cy.add({group: 'nodes', data: node.data, position: seeded[node.data.id]});
          for (const edge of edges) if (cy.getElementById(edge.data.id).empty() && !cy.getElementById(edge.data.source).empty() && !cy.getElementById(edge.data.target).empty()) cy.add({group: 'edges', data: edge.data, classes: edge.classes});
          for (const node of nodes) cy.getElementById(node.data.id).data(liveData(node.data));
          for (const edge of edges) cy.getElementById(edge.data.id).data({tier: edge.data.tier ?? null});
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
  cy.zoom(clampNetworkZoom(cy.zoom())); cy.pan(cyClampedPan(cy)); // a restored out-of-range pan is clamped before any handler sees it
  const wrapper = {kind: 'cytoscape', cy, view, scopeKey, epochKey, signature, plan, fitNewContentAvailable: false, onTap, stats: {renders: 1, reused: 0, topologyChanges: 0}};
  cy.on('tap', 'node', event => wrapper.onTap(event.target.id()));
  // cy.pan() inside a 'viewport' handler re-fires 'viewport': the guard makes the correction run once; it runs before the rAF remember, so only clamped cameras are saved.
  let clamping = false; cy.on('viewport', () => { if (clamping || cy.destroyed()) return; const pan = cy.pan(); const next = cyClampedPan(cy); if (Math.abs(next.x - pan.x) < 0.5 && Math.abs(next.y - pan.y) < 0.5) return; clamping = true; try { cy.pan(next); } finally { clamping = false; } });
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
      const topologyChanged = existing.signature !== signature; existing.g6.setOnSelect(onSelect); if (extra.onMiss) existing.g6.setOnMiss(extra.onMiss); existing.g6.update(nodes, edges, style, {revision, overlay: extra.overlayModel}); existing.g6.select(selectedId); existing.signature = signature; existing.fitNewContentAvailable = existing.fitNewContentAvailable || topologyChanged || plan.fitNewContentAvailable; return existing; // camera is kept on topology change; the explicit control offers the fit
    }
    remember(existing); existing.g6.destroy();
  } else if (existing) existing.g6.destroy();
  const plan = planCamera(store, {view, epochKey, scopeKey, signature});
  const viewState = plan.action === 'fit' ? null : (plan.camera ? {...plan.camera, positions: plan.positions || plan.camera.positions} : null);
  const g6 = renderG6Network(container, nodes, edges, {...style, ...extra, viewState, selectedId, onSelect, onError, ariaLabel});
  return {kind: 'g6', g6, graph: g6.graph, container, view, scopeKey, epochKey, signature, plan, fitNewContentAvailable: false};
}

/** One line under a network: what the node shapes mean (only the shapes that are present). */
function shapeKey(container, nodes) {
  let key = container.parentElement.querySelector('.atlas-shape-key'); if (!key) { key = document.createElement('div'); key.className = 'atlas-shape-key atlas-note'; container.parentElement.append(key); }
  const has = shape => nodes.some(node => node.data.shape === shape); const hubs = nodes.filter(node => node.data.hub).length;
  key.dataset.hubs = String(hubs); key.textContent = [has('circle') ? '● 논문' : null, has('diamond') ? '◆ 개념' : null, has('hexagon') ? `육각형 = 연결 ${HUB_MIN_DEGREE}개 이상 논문` : null, has('star') ? `★ 연결 ${HUB_MIN_DEGREE}개 이상 개념` : null, nodes.some(node => node.data.tier >= 2) ? `아이콘 = 연결 ${TIER_LINKS[1]}개 이상(문서·전구), ${TIER_LINKS[2]}개 이상(메달·반짝임)` : null, hubs ? '크기 = 연결 수' : null].filter(Boolean).join(' · ');
}

/** Lower paper/concept networks.  Colours come from the ACTUAL palette/theme context in `state.paletteContext`. */
/** `deferred` (lower networks off screen): compute the projection (counts) only; no legend, no Cytoscape/G6 instance is created or touched. */
export function renderNetworks(containers, snapshot, detail, state, callbacks, {deferred = false} = {}) {
  const computed = getComputedStyle(document.getElementById('atlas-app') || document.documentElement); const palette = state.paletteContext || {series: Array.from({length: 6}, (_, index) => cssColor(computed, `--atlas-series-${index + 1}-hex`))};
  const scope = state.viewState?.scope; const forced = state.viewState?.detail?.forceVisible || {paperIds: [], conceptIds: []};
  const selectedConceptIds = [...new Set([...(state.selectedConceptId ? [state.selectedConceptId] : []), ...(forced.conceptIds || [])])]; const selectedPaper = state.selectedPaperId ? snapshot.papers.find(paper => paper.id === state.selectedPaperId) : null;
  const mandatoryConceptIds = selectedPaper?.conceptIds || [];
  const graph = graphProjection(snapshot, detail, {maxConceptNodes: Math.max(30, Math.ceil(300 * state.fraction)), selectedConceptIds, mandatoryConceptIds, selectedPaperId: state.selectedPaperId, selectedEdgeId: state.selectedEdgeId, forcedPaperIds: forced.paperIds || [], fieldId: state.fieldId, categoryPalette: palette});
  if (deferred) return {graph, deferred: true};
  const colors = {text: cssColor(computed, '--atlas-text-hex'), panel: cssColor(computed, '--atlas-panel-hex'), border: cssColor(computed, '--atlas-border-hex'), focus: cssColor(computed, '--atlas-focus-hex'), series4: cssColor(computed, '--atlas-series-4-hex')};
  const drawLegend = (container, nodes, label) => { let legend = container.parentElement.querySelector('.atlas-network-legend'); if (!legend) { legend = document.createElement('div'); legend.className = 'atlas-network-legend'; container.parentElement.append(legend); } legend.replaceChildren(); const title = document.createElement('span'); title.className = 'atlas-network-legend-title'; title.textContent = `${label} categories`; legend.append(title); for (const category of networkLegendForNodes(nodes.map(node => node.data))) { const item = document.createElement('span'); item.className = 'atlas-network-legend-item'; const swatch = document.createElement('i'); swatch.className = 'atlas-network-legend-swatch'; swatch.style.background = category.color || '#64748b'; swatch.dataset.categoryColor = category.color || ''; const text = document.createElement('span'); text.textContent = `${category.label}${category.inferred ? ' · inferred' : ''} (${category.count})`; item.append(swatch, text); legend.append(item); } };
  const allConceptNodes = [...graph.conceptNodes, ...graph.conceptPaperNodes];
  // The concept graph reuses paper IDs for connected papers; the paper graph owns the plain ID, so distinct DOM/G6 IDs are not needed (separate graphs).
  if (state.colorMode === 'density' && state.paperDensity) { const rows = new Map(state.paperDensity.rows.map(row => [row.id, row])); const max = Math.max(0, ...state.paperDensity.rows.map(row => row.degree)); for (const node of graph.paperNodes) { const degree = rows.get(node.data.id)?.degree || 0; node.data.categoryColor = densityColor(degree, max); node.data.outline = colors.text; node.data.degree = degree; node.data.degreePossible = Math.max(0, state.paperDensity.counts.papers - 1); } }
  drawLegend(containers.paper, graph.paperNodes, 'Paper'); drawLegend(containers.concept, allConceptNodes, 'Concept');
  if (state.colorMode === 'density' && state.paperDensity) { const legend = containers.paper.parentElement.querySelector('.atlas-network-legend'); legend.replaceChildren(); const title = document.createElement('span'); title.textContent = `Observed degree (slate ramp, not a category) · 0 to ${Math.max(0, ...state.paperDensity.rows.map(row => row.degree))} / ${Math.max(0, state.paperDensity.counts.papers - 1)} possible`; legend.append(title); for (const color of densityRamp()) { const swatch = document.createElement('i'); swatch.className = 'atlas-network-legend-swatch'; swatch.style.background = color.hex; legend.append(swatch); } }
  const epochKey = state.viewState?.source || ''; const scopeKey = `${state.viewState?.camera?.scopeKey || 'library'}`; const store = state.cameraStore; const revision = state.viewState?.revision ?? 0;
  const paperEdges = withEdgeTiers(snapshot, graph.paperEdges); const conceptEdges = withEdgeTiers(snapshot, graph.conceptEdges); // tier = full-snapshot strength; the projection's budgets/sampling are untouched
  // Hubs are decided from the DISPLAYED links of each graph (after the density recolour, so `degree` stays the density statistic).
  const paperNodes = markHubs(graph.paperNodes, paperEdges); const conceptNodes = markHubs(allConceptNodes, conceptEdges); const showNames = Boolean(state.showLabels);
  for (const [container, nodes] of [[containers.paper, paperNodes], [containers.concept, conceptNodes]]) shapeKey(container, nodes);
  const paperSelect = id => callbacks.select({kind: 'paper', value: graph.paperById.get(id)});
  const conceptSelect = id => { const concept = graph.conceptById.get(id); const paper = graph.paperById.get(id); if (concept) callbacks.select({kind: 'concept', value: concept}); else if (paper) callbacks.select({kind: 'paper', value: paper}); };
  const result = {graph, paper: null, concept: null, cyPaper: null, cyConcept: null, g6Paper: null, g6Concept: null};
  if (state.renderer === 'g6') {
    for (const wrapper of [state.cyPaperView, state.cyConceptView]) wrapper?.cy.destroy();
    const style = {textColor: colors.text, panelColor: colors.panel, borderColor: colors.border, focusColor: colors.focus, seriesColor: colors.focus, showAllLabels: showNames};
    result.paper = g6View({view: 'g6-paper', container: containers.paper, nodes: paperNodes, edges: paperEdges, style, selectedId: state.selectedPaperId, onSelect: paperSelect, onError: callbacks.error, store, epochKey, scopeKey, existing: state.g6PaperView, revision, ariaLabel: 'Paper network'});
    result.concept = g6View({view: 'g6-concept', container: containers.concept, nodes: conceptNodes, edges: conceptEdges, style: {...style, seriesColor: colors.series4}, selectedId: state.selectedConceptId, onSelect: conceptSelect, onError: callbacks.error, store, epochKey, scopeKey, existing: state.g6ConceptView, revision, ariaLabel: 'Concept network'});
    result.g6Paper = result.paper.g6; result.g6Concept = result.concept.g6; return result;
  }
  for (const wrapper of [state.g6PaperView, state.g6ConceptView]) wrapper?.g6.destroy();
  result.paper = cytoscapeView({view: 'cy-paper', container: containers.paper, nodes: paperNodes.map(node => cyNode(node, showNames && namedWhenShown(node.data, paperNodes.length), colors.panel)), edges: paperEdges, colors, selectedId: state.selectedPaperId, onTap: paperSelect, store, epochKey, scopeKey, existing: state.cyPaperView});
  result.concept = cytoscapeView({view: 'cy-concept', container: containers.concept, nodes: conceptNodes.map(node => cyNode(node, showNames && namedWhenShown(node.data, conceptNodes.length), colors.panel)), edges: conceptEdges, colors, selectedId: state.selectedConceptId, onTap: conceptSelect, store, epochKey, scopeKey, existing: state.cyConceptView});
  result.cyPaper = result.paper.cy; result.cyConcept = result.concept.cy; return result;
}

export const AtlasNetworkViews = {renderNetworks, cytoscapeStyle, CY_TRANSITION_S};
