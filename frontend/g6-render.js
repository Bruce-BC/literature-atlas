import {Graph, NodeEvent} from '@antv/g6';
import {NETWORK_ZOOM_RANGE, clampNetworkZoom} from './network-style.js';

const safeColor = value => typeof value === 'string' && value ? value : '#64748b';


function nodeOptions(style) { return {type: datum => datum.data?.kind === 'concept' ? 'diamond' : 'circle', style: {size: 18, fill: datum => safeColor(datum.data?.categoryColor || style.seriesColor || style.focusColor), stroke: safeColor(style.panelColor), lineWidth: 2, label: false, labelText: datum => { const label = String(datum.data?.label || datum.id); return label.length > 34 ? `${label.slice(0, 31)}…` : label; }, labelFill: safeColor(style.textColor), labelFontSize: 12, labelPlacement: 'bottom', labelOffsetY: 5, labelBackground: true, labelBackgroundFill: safeColor(style.panelColor), labelBackgroundRadius: 4, labelBackgroundPadding: [3, 5]}, state: {active: {label: true, lineWidth: 4, stroke: safeColor(style.focusColor)}, selected: {label: true, lineWidth: 5, stroke: safeColor(style.focusColor)}}}; }
function edgeOptions(style) { return {type: 'line', style: {stroke: safeColor(style.borderColor), lineWidth: 1.5, endArrow: datum => datum.data?.directed ? true : false}}; }

function graphData(nodes, edges) {
  return {nodes: nodes.map(node => ({id: node.data.id, data: {...node.data, label: String(node.data.label || node.data.id)}})), edges: edges.map(edge => ({id: edge.data.id, source: edge.data.source, target: edge.data.target, data: {...edge.data}}))};
}

export function renderG6Network(container, nodes, edges, {textColor, panelColor, borderColor, focusColor, seriesColor, onSelect, selectedId = null, viewState = null, onError = console.error, GraphClass = Graph} = {}) {
  let currentStyle = {textColor, panelColor, borderColor, focusColor, seriesColor};
  const dataFingerprint = (rows, links) => JSON.stringify([rows.map(row => row.data), links.map(link => link.data)]);
  let currentDataKey = dataFingerprint(nodes, edges);
  const graph = new GraphClass({
    container,
    zoomRange: [...NETWORK_ZOOM_RANGE],
    autoFit: 'view',
    padding: 24,
    data: graphData(nodes, edges),
    layout: {type: 'd3-force', animation: true, enableWorker: false, alphaDecay: 0.14, manyBody: {strength: -80}, link: {distance: 80}, collide: {radius: 18}},
    node: nodeOptions(currentStyle),
    edge: edgeOptions(currentStyle),
    behaviors: ['drag-canvas', 'zoom-canvas', {type: 'drag-element-force', key: 'force-drag', fixed: false, trigger: []}, 'click-select', {type: 'hover-activate', degree: 0}]
  });
  let disposed = false;
  let rendered = false;
  let lastSelected = selectedId;
  container.dataset.rendererState = 'rendering';
  container.setAttribute('aria-busy', 'true');
  let stopTimer = null;
  const report = error => { if (!disposed) { container.dataset.rendererState = 'error'; container.setAttribute('aria-busy', 'false'); onError(error); } };
  const run = operation => { try { Promise.resolve(operation()).catch(report); } catch (error) { report(error); } };
  graph.on(NodeEvent.POINTER_ENTER, event => { if (disposed) return; const id = event.target?.id || event.target; const datum = graph.getNodeData().find(node => node.id === id); if (datum) container.title = [datum.data?.label || id, datum.data?.categoryPath || datum.data?.categoryLabel, Number.isFinite(datum.data?.degree) ? `Observed neighbors ${datum.data.degree}/${datum.data.degreePossible} possible` : null].filter(Boolean).join(' · '); });
  graph.on(NodeEvent.POINTER_LEAVE, () => { container.title = ''; });
  let nativeClicked = false;
  graph.on(NodeEvent.CLICK, event => { if (disposed) return; nativeClicked = true; window.__ATLAS_G6_LAST_CLICK__ = {target: event.target?.id || event.target, targetType: event.targetType}; onSelect?.(event.target?.id || event.target); });
  let pointerStart = null; let pointerDragged = false;
  const pointerDown = event => { if (event.button === 0) { pointerStart = [event.clientX, event.clientY]; pointerDragged = false; } };
  const pointerMove = event => { if (pointerStart && Math.hypot(event.clientX - pointerStart[0], event.clientY - pointerStart[1]) >= 10) pointerDragged = true; };
  const physicalHit = event => { if (nativeClicked) { nativeClicked = false; pointerStart = null; pointerDragged = false; return; } if (pointerDragged) { pointerDragged = false; pointerStart = null; return; } pointerStart = null; if (disposed || !rendered || event.button !== 0) return; const x = event.clientX; const y = event.clientY; let nearest = null; let distance = Infinity; for (const node of graph.getNodeData()) { const position = graph.getElementPosition(node.id); const client = graph.getClientByCanvas(position); const next = Math.hypot(client[0] - x, client[1] - y); if (next < distance) { distance = next; nearest = node.id; } } if (nearest && distance <= 30) onSelect?.(nearest); };
  container.addEventListener('pointerdown', pointerDown); container.addEventListener('pointermove', pointerMove);
  container.addEventListener('click', physicalHit);
  const ready = Promise.resolve().then(() => disposed ? null : graph.render()).then(async () => {
    if (disposed) return null;
    if (viewState) {
      graph.stopLayout();
      const ids = new Set(graph.getNodeData().map(node => node.id));
      const positions = Object.fromEntries(Object.entries(viewState.positions || {}).filter(([id, point]) => ids.has(id) && Array.isArray(point) && point.length >= 2 && point.every(Number.isFinite)));
      if (Object.keys(positions).length) await graph.translateElementTo(positions, false);
      if (disposed) return null;
      if (Number.isFinite(viewState.zoom)) await graph.zoomTo(clampNetworkZoom(viewState.zoom), false);
      if (disposed) return null;
      if (Array.isArray(viewState.position) && viewState.position.every(Number.isFinite)) { const currentOrigin = graph.getViewportByCanvas([0, 0]); await graph.translateBy(viewState.position.map((value, index) => value - (currentOrigin[index] || 0)), false); }
      if (disposed) return null;
    } else {
      stopTimer = setTimeout(() => { stopTimer = null; if (!disposed) run(() => graph.stopLayout()); }, 1200);
    }
    if (lastSelected && graph.getNodeData().some(node => node.id === lastSelected)) await graph.setElementState(lastSelected, 'selected');
    if (disposed) return null;
    rendered = true; container.dataset.rendererState = 'ready'; container.setAttribute('aria-busy', 'false');
    return graph;
  }).catch(error => { report(error); return null; });
  return {
    graph, ready,
    restyle: (nextNodes, nextEdges, nextStyle) => {
      const key = JSON.stringify(nextStyle); const nextDataKey = dataFingerprint(nextNodes, nextEdges); if (key === JSON.stringify(currentStyle) && nextDataKey === currentDataKey) return;
      currentStyle = {...nextStyle}; currentDataKey = nextDataKey;
      run(async () => { await ready; if (disposed || !rendered) return; graph.updateNodeData(graphData(nextNodes, []).nodes); graph.updateEdgeData(graphData([], nextEdges).edges); graph.setNode(nodeOptions(currentStyle)); graph.setEdge(edgeOptions(currentStyle)); await graph.draw(); });
    },
    capture: () => {
      if (disposed || !rendered) return viewState;
      try {
        return {zoom: graph.getZoom(), position: graph.getViewportByCanvas([0, 0]), positions: Object.fromEntries(graph.getNodeData().map(node => [node.id, graph.getElementPosition(node.id)]))};
      } catch (error) { report(error); return viewState; }
    },
    destroy: () => { if (disposed) return; disposed = true; if (stopTimer !== null) clearTimeout(stopTimer); container.removeEventListener('pointerdown', pointerDown); container.removeEventListener('pointermove', pointerMove); container.removeEventListener('click', physicalHit); container.dataset.rendererState = 'disposed'; container.setAttribute('aria-busy', 'false'); graph.destroy(); },
    select: id => { if (disposed || id === lastSelected) return; if (!rendered) { lastSelected = id; return; } const ids = new Set(graph.getNodeData().map(node => node.id)); const changes = {}; if (lastSelected && ids.has(lastSelected)) changes[lastSelected] = []; if (id && ids.has(id)) changes[id] = ['selected']; lastSelected = id; if (Object.keys(changes).length) run(() => graph.setElementState(changes, false)); },
    resize: (width, height) => { if (!disposed) run(() => graph.setSize(width, height)); },
    zoomBy: ratio => { if (!disposed && rendered) run(() => graph.zoomTo(clampNetworkZoom(graph.getZoom() * ratio))); },
    fit: () => { if (!disposed && rendered) run(() => graph.fitView({when: 'always'})); }
  };
}

export const AtlasG6 = {renderG6Network};
