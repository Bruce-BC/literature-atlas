import {Graph, GraphEvent, NodeEvent} from '@antv/g6';
import {NETWORK_ZOOM_RANGE, clampNetworkZoom} from './network-style.js';
import {completePositions, topologySignature} from './camera-policy.js';

const safeColor = value => typeof value === 'string' && value ? value : '#64748b';
const DRAG_THRESHOLD = 10;
const DENSE_THRESHOLD = 400; const CHUNK_SIZE = 250;
const yieldToBrowser = () => new Promise(resolve => setTimeout(resolve, 0));

function nodeOptionsFull(style) {
  const size = Number(style.nodeSize) > 0 ? Number(style.nodeSize) : 18; const showLabel = style.labels !== false;
  return {type: datum => datum.data?.kind === 'concept' ? 'diamond' : 'circle', style: {size: datum => datum.data?.representative ? Math.max(size, 22) : size, fill: datum => safeColor(datum.data?.categoryColor || style.seriesColor || style.focusColor), fillOpacity: datum => datum.data?.dimmed ? 0.22 : 1, strokeOpacity: datum => datum.data?.dimmed ? 0.3 : 1, stroke: datum => datum.data?.outline || (datum.data?.representative ? safeColor(style.textColor) : safeColor(style.panelColor)), lineWidth: datum => datum.data?.representative ? 3 : (size < 12 ? 1 : 2), label: false, labelText: datum => { const label = String(datum.data?.label || datum.id); return label.length > 34 ? `${label.slice(0, 31)}…` : label; }, labelFill: safeColor(style.textColor), labelFontSize: 12, labelPlacement: 'bottom', labelOffsetY: 5, labelBackground: true, labelBackgroundFill: safeColor(style.panelColor), labelBackgroundRadius: 4, labelBackgroundPadding: [3, 5]}, state: {active: {label: showLabel, lineWidth: 4, stroke: safeColor(style.focusColor)}, selected: {label: showLabel, lineWidth: 5, stroke: safeColor(style.focusColor)}}};
}
const edgeOptionsFull = style => ({type: 'line', style: {stroke: datum => datum.data?.relation === 'membership' || datum.data?.relation === 'hierarchy' ? safeColor(style.seriesColor || style.borderColor) : safeColor(style.borderColor), lineWidth: datum => datum.data?.relation === 'hierarchy' ? 2 : 1.5, lineDash: datum => datum.data?.relation === 'hierarchy' ? [6, 3] : [0, 0], strokeOpacity: datum => datum.data?.dimmed ? 0.15 : 0.9, endArrow: datum => Boolean(datum.data?.directed)}});

// Dense graphs: static per-datum styles and constant element types (no per-element JS callbacks).
function nodeOptions(style, dense = false) {
  const full = nodeOptionsFull(style); if (!dense) return full;
  const {size, fill, fillOpacity, strokeOpacity, stroke, lineWidth, ...rest} = full.style; return {type: 'circle', style: rest, state: full.state};
}
function edgeOptions(style, dense = false) {
  const full = edgeOptionsFull(style); if (!dense) return full;
  return {type: 'line', style: {}};
}
const finite = value => Number.isFinite(value);
/** Static per-datum node style (same visual result as the style functions in nodeOptions). */
const staticNodeStyle = (data, style) => { const size = Number(style.nodeSize) > 0 ? Number(style.nodeSize) : 18; return {size: data.representative ? Math.max(size, 22) : size, fill: safeColor(data.categoryColor || style.seriesColor || style.focusColor), fillOpacity: data.dimmed ? 0.22 : 1, strokeOpacity: data.dimmed ? 0.3 : 1, stroke: data.outline || (data.representative ? safeColor(style.textColor) : safeColor(style.panelColor)), lineWidth: data.representative ? 3 : (size < 12 ? 1 : 2)}; };
/** Nodes carry coordinates in `style` (G6 5.x); a top-level x/y is not honoured. */
export function graphData(nodes, edges, positions = {}, staticStyle = null) {
  return {
    nodes: nodes.map(node => { const supplied = positions[node.data.id]; const x = finite(supplied?.x) ? supplied.x : node.data.x; const y = finite(supplied?.y) ? supplied.y : node.data.y; const base = finite(x) && finite(y) ? {x, y} : {}; return {id: node.data.id, ...(staticStyle || Object.keys(base).length ? {style: {...base, ...(staticStyle ? staticNodeStyle(node.data, staticStyle) : {})}} : {}), data: {...node.data, label: String(node.data.label || node.data.id)}}; }),
    edges: edges.map(edge => ({id: edge.data.id, source: edge.data.source, target: edge.data.target, ...(staticStyle ? {style: {stroke: safeColor(staticStyle.borderColor), lineWidth: 1.5, strokeOpacity: edge.data.dimmed ? 0.15 : 0.9, endArrow: Boolean(edge.data.directed)}} : {}), data: {...edge.data}}))
  };
}
const signatureOf = (nodes, edges) => topologySignature(nodes.map(node => node.data.id), edges.map(edge => [edge.data.source, edge.data.target, edge.data.id]));
const styleKey = style => JSON.stringify([style.textColor, style.panelColor, style.borderColor, style.focusColor, style.seriesColor, style.nodeSize, style.labels]);
const dataKey = (nodes, edges) => JSON.stringify([nodes.map(row => [row.data.id, row.data.categoryColor, row.data.dimmed, row.data.label, row.data.outline]), edges.map(row => [row.data.id, row.data.dimmed])]);

export function renderG6Network(container, nodes, edges, {textColor, panelColor, borderColor, focusColor, seriesColor, nodeSize, labels, onSelect, selectedId = null, viewState = null, onError = console.error, GraphClass = Graph, layoutType = 'd3-force', hitRadius = null, ariaLabel = 'Network graph'} = {}) {
  let currentStyle = {textColor, panelColor, borderColor, focusColor, seriesColor, nodeSize, labels};
  // `desired*` is the latest requested state; `applied*` is what the live graph really contains (committed after success).
  let desiredDataKey = dataKey(nodes, edges); let desiredSignature = signatureOf(nodes, edges); let desiredStyle = currentStyle;
  let appliedDataKey = desiredDataKey; let appliedSignature = desiredSignature; let appliedStyleKey = styleKey(currentStyle); let appliedStyle = currentStyle; let latestRevision = -Infinity; let selectHandler = onSelect;
  const stats = {created: 1, topologyChanges: 0, styleUpdates: 0, layoutRuns: 0, droppedStale: 0, failedUpdates: 0, chunks: 0, layoutsFinished: 0, nativeClicks: 0, physicalClicks: 0, suppressedDragClicks: 0, keyboardActivations: 0, fitNewContentRuns: 0};
  const initialPositions = {...(viewState?.positions || {})}; const restoredPositions = Object.fromEntries(Object.entries(initialPositions).filter(([, point]) => Array.isArray(point) && point.every(finite)).map(([id, point]) => [id, {x: point[0], y: point[1]}]));
  const dense = nodes.length > DENSE_THRESHOLD; // dense graphs use precomputed per-datum styles: per-node style FUNCTIONS dominated vendor element creation
  const initialData = graphData(dense ? nodes.slice(0, CHUNK_SIZE) : nodes, dense ? [] : edges, restoredPositions, dense ? currentStyle : null); // dense graphs are created in bounded chunks between tasks
  const presetLayout = layoutType === 'preset';
  // dense graphs disable element animations: they would make every awaited draw() take ~1 s
  const options = {container, zoomRange: [...NETWORK_ZOOM_RANGE], autoFit: viewState || dense ? undefined : 'view', ...(dense ? {animation: false} : {}), padding: 24, data: initialData, node: nodeOptions(currentStyle, dense), edge: edgeOptions(currentStyle, dense), behaviors: ['drag-canvas', 'zoom-canvas', {type: 'drag-element-force', key: 'force-drag', fixed: false, trigger: []}, {type: 'hover-activate', degree: 0}]};
  // G6 5.1.1 registers no `preset` layout: positions come from node style.x/y and no layout is configured.
  if (!presetLayout) options.layout = {type: 'd3-force', animation: true, enableWorker: false, alphaDecay: 0.14, manyBody: {strength: -80}, link: {distance: 80}, collide: {radius: 18}};
  const graph = new GraphClass(options);
  let disposed = false; let rendered = false; let lastSelected = selectedId; let stopTimer = null; let focusIndex = -1;
  container.dataset.rendererState = 'rendering'; container.dataset.layout = presetLayout ? 'preset' : 'd3-force'; container.dataset.layoutState = presetLayout ? 'settled' : 'running'; container.setAttribute('aria-busy', 'true');
  container.tabIndex = 0; container.setAttribute('role', 'listbox'); container.setAttribute('aria-roledescription', 'graph'); container.setAttribute('aria-label', `${ariaLabel} · Arrow keys move between nodes, Enter or Space activates`);
  // ONE bounded DOM proxy gives the canvas node in focus a real accessible name (no per-node DOM).
  const proxy = typeof document !== 'undefined' && typeof container.append === 'function' ? document.createElement('div') : {setAttribute() {}, dataset: {}, remove() {}}; proxy.setAttribute('role', 'option'); proxy.setAttribute('aria-selected', 'false'); proxy.className = 'atlas-sr-only'; proxy.id = `atlas-g6-focus-${container.id || 'graph'}`; if (typeof container.append === 'function') container.append(proxy);
  const report = error => { if (!disposed) { container.dataset.rendererState = 'error'; container.setAttribute('aria-busy', 'false'); onError(error); } };
  const run = operation => { try { return Promise.resolve(operation()).catch(report); } catch (error) { report(error); return Promise.resolve(); } };

  // ---- single click dispatcher -------------------------------------------------
  let pointerStart = null; let dragged = false; let nativeClicked = false;
  const activate = id => { if (!disposed && id !== undefined && id !== null) selectHandler?.(id); };
  const idOf = event => event.target?.id ?? event.target;
  graph.on(NodeEvent.POINTER_ENTER, event => { if (disposed) return; const datum = graph.getNodeData().find(node => node.id === idOf(event)); if (datum) container.title = [datum.data?.label || datum.id, datum.data?.categoryPath || datum.data?.categoryLabel, Number.isFinite(datum.data?.degree) ? `Observed neighbors ${datum.data.degree}/${datum.data.degreePossible} possible` : null].filter(Boolean).join(' · '); });
  graph.on(NodeEvent.POINTER_LEAVE, () => { container.title = ''; });
  // Real layout lifecycle: 'settled' means the layout finished (positions stop moving), not that a timer elapsed.
  // A layout is REQUESTED the moment a topology change is accepted (before draw), STARTED on BEFORE_LAYOUT and finished on the first AFTER_LAYOUT that follows its start.
  let layoutRequested = !presetLayout; let layoutStarted = false;
  const markSettled = () => { layoutRequested = false; layoutStarted = false; if (!disposed) container.dataset.layoutState = 'settled'; };
  const requestLayout = () => { if (presetLayout) return; layoutRequested = true; layoutStarted = false; container.dataset.layoutState = 'running'; };
  if (!presetLayout) { graph.on(GraphEvent.BEFORE_LAYOUT, () => { if (!disposed && layoutRequested) layoutStarted = true; }); graph.on(GraphEvent.AFTER_LAYOUT, () => { if (disposed) return; stats.layoutsFinished += 1; if (!layoutRequested || layoutStarted) markSettled(); }); }
  graph.on(NodeEvent.CLICK, event => { if (disposed) return; if (dragged) { stats.suppressedDragClicks += 1; return; } nativeClicked = true; stats.nativeClicks += 1; globalThis.__ATLAS_G6_LAST_CLICK__ = {target: idOf(event), targetType: event.targetType}; activate(idOf(event)); });
  const pointerDown = event => { if (event.button === 0) { pointerStart = [event.clientX, event.clientY]; dragged = false; nativeClicked = false; } };
  const pointerMove = event => { if (pointerStart && Math.hypot(event.clientX - pointerStart[0], event.clientY - pointerStart[1]) >= DRAG_THRESHOLD) dragged = true; };
  // Capture phase: a click that trails a drag never reaches G6 or the fallback.
  const trailingClick = event => { if (dragged) { stats.suppressedDragClicks += 1; event.stopPropagation(); event.preventDefault(); dragged = false; pointerStart = null; } };
  const physicalHit = event => {
    if (dragged) { stats.suppressedDragClicks += 1; dragged = false; pointerStart = null; return; }
    if (nativeClicked) { nativeClicked = false; pointerStart = null; return; }
    pointerStart = null; if (disposed || !rendered || event.button !== 0) return;
    const radius = hitRadius ?? Math.max(10, (Number(currentStyle.nodeSize) || 18) / 2 + 4); let nearest = null; let distance = Infinity;
    for (const node of graph.getNodeData()) { const client = graph.getClientByCanvas(graph.getElementPosition(node.id)); const next = Math.hypot(client[0] - event.clientX, client[1] - event.clientY); if (next < distance) { distance = next; nearest = node.id; } }
    if (nearest !== null && distance <= radius) { stats.physicalClicks += 1; activate(nearest); }
  };
  container.addEventListener('pointerdown', pointerDown); container.addEventListener('pointermove', pointerMove); container.addEventListener('click', trailingClick, true); container.addEventListener('click', physicalHit);
  // ---- keyboard: focus moves an 'active' highlight only; Enter/Space activates ---
  const keyDown = event => {
    const ids = graph.getNodeData().map(node => node.id); if (!ids.length || disposed || !rendered) return;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') { event.preventDefault(); focusIndex = (focusIndex + 1) % ids.length; }
    else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') { event.preventDefault(); focusIndex = (focusIndex - 1 + ids.length) % ids.length; }
    else if ((event.key === 'Enter' || event.key === ' ') && focusIndex >= 0) { event.preventDefault(); stats.keyboardActivations += 1; activate(ids[focusIndex]); return; }
    else return;
    const datum = graph.getNodeData().find(node => node.id === ids[focusIndex]); const name = `${datum?.data?.label || ids[focusIndex]}${datum?.data?.categoryLabel ? ` · ${datum.data.categoryLabel}` : ''}${datum?.data?.representative ? (datum.data.expanded ? ' · expanded' : ' · collapsed') : ''}`; proxy.textContent = name; proxy.dataset.nodeId = ids[focusIndex]; proxy.setAttribute('aria-selected', String(ids[focusIndex] === lastSelected)); container.setAttribute('aria-activedescendant', proxy.id); container.title = name;
    run(() => graph.setElementState(Object.fromEntries(ids.map(id => [id, id === lastSelected ? ['selected'] : id === ids[focusIndex] ? ['active'] : []])), false));
  };
  container.addEventListener('keydown', keyDown);

  const positionsNow = () => Object.fromEntries(graph.getNodeData().map(node => { const p = graph.getElementPosition(node.id); return [node.id, {x: p[0], y: p[1]}]; }));
  const scheduleStop = () => { if (presetLayout) return; if (stopTimer !== null) clearTimeout(stopTimer); stopTimer = setTimeout(() => { stopTimer = null; if (!disposed) run(() => graph.stopLayout()).then(markSettled); }, 8000); }; // safety bound only; normal settle is the AFTER_LAYOUT event
  const applySelected = async () => { if (lastSelected && graph.getNodeData().some(node => node.id === lastSelected)) await graph.setElementState(lastSelected, 'selected'); };

  const ready = Promise.resolve().then(() => disposed ? null : graph.render()).then(async () => {
    if (disposed) return null;
    if (dense) {
      const rest = graphData(nodes.slice(CHUNK_SIZE), [], restoredPositions, currentStyle).nodes; const allEdges = graphData([], edges, {}, currentStyle).edges;
      for (let index = 0; index < rest.length && !disposed; index += CHUNK_SIZE) { await yieldToBrowser(); if (disposed) return null; graph.addNodeData(rest.slice(index, index + CHUNK_SIZE)); await graph.draw(); stats.chunks += 1; }
      for (let index = 0; index < allEdges.length && !disposed; index += CHUNK_SIZE) { await yieldToBrowser(); if (disposed) return null; graph.addEdgeData(allEdges.slice(index, index + CHUNK_SIZE)); await graph.draw(); stats.chunks += 1; }
      if (disposed) return null;
      if (!viewState) await graph.fitView({when: 'always'});
    }
    if (viewState) {
      graph.stopLayout(); markSettled();
      const known = new Set(graph.getNodeData().map(node => node.id)); const restored = Object.fromEntries(Object.entries(viewState.positions || {}).filter(([id, point]) => known.has(id) && Array.isArray(point) && point.length >= 2 && point.every(finite)));
      if (Object.keys(restored).length) await graph.translateElementTo(restored, false);
      if (disposed) return null;
      if (Number.isFinite(viewState.zoom)) await graph.zoomTo(clampNetworkZoom(viewState.zoom), false);
      if (disposed) return null;
      if (Array.isArray(viewState.position) && viewState.position.every(finite)) { const origin = graph.getViewportByCanvas([0, 0]); await graph.translateBy(viewState.position.map((value, index) => value - (origin[index] || 0)), false); }
      if (disposed) return null;
    } else scheduleStop();
    await applySelected(); if (disposed) return null;
    rendered = true; container.dataset.rendererState = 'ready'; container.setAttribute('aria-busy', 'false');
    return graph;
  }).catch(error => { report(error); return null; });

  // Serial queue: every update runs after the previous one; stale revisions are skipped (latest wins).
  let queue = ready;
  // A failed operation clears the pending desired state (back to what is actually applied) so an identical retry is NOT short-circuited.
  let pendingUpdates = 0; const markUpdateState = () => { if (!disposed) container.dataset.updateState = pendingUpdates > 0 ? 'pending' : 'idle'; };
  container.dataset.updateState = 'idle';
  const enqueue = (revision, operation) => { pendingUpdates += 1; markUpdateState(); queue = queue.then(() => { if (disposed || !rendered) return null; if (revision < latestRevision) { stats.droppedStale += 1; return null; } return operation(); }).catch(error => { desiredSignature = appliedSignature; desiredDataKey = appliedDataKey; desiredStyle = appliedStyle; stats.failedUpdates += 1; markSettled(); report(error); }).then(() => { pendingUpdates -= 1; markUpdateState(); }); return queue; };

  return {
    graph, ready, stats, get signature() { return appliedSignature; }, get desiredSignature() { return desiredSignature; }, get layoutType() { return layoutType; },
    /** Replace the click handler so a reused wrapper never resolves IDs against a stale snapshot/projection. */
    setOnSelect(handler) { selectHandler = handler; },
    /** Apply new data/style.  Topology changes really add/remove nodes (seeded, bounded relayout); style-only changes reuse graph and layout. */
    update(nextNodes, nextEdges, nextStyle, {revision = latestRevision + 1} = {}) {
      latestRevision = Math.max(latestRevision, revision);
      const signature = signatureOf(nextNodes, nextEdges); const nextDataKey = dataKey(nextNodes, nextEdges); const nextKey = styleKey(nextStyle);
      if (signature === desiredSignature && nextDataKey === desiredDataKey && nextKey === styleKey(desiredStyle)) return queue; // identical to the latest request
      desiredSignature = signature; desiredDataKey = nextDataKey; desiredStyle = {...nextStyle};
      if (signature !== appliedSignature) requestLayout();
      return enqueue(revision, async () => {
        // Decide against what the graph ACTUALLY contains; a skipped stale revision never marks its keys applied.
        const topologyChanged = signature !== appliedSignature; const styleChanged = nextKey !== appliedStyleKey; if (!topologyChanged && !styleChanged && nextDataKey === appliedDataKey) return;
        currentStyle = {...nextStyle};
        const ids = nextNodes.map(node => node.data.id);
        const data = graphData(nextNodes, nextEdges, topologyChanged ? completePositions(positionsNow(), ids, nextEdges.map(edge => [edge.data.source, edge.data.target])) : positionsNow(), dense ? currentStyle : null);
        // Preset views keep their deterministic data coordinates for known IDs.
        if (presetLayout) { const byId = new Map(nextNodes.map(node => [node.data.id, node.data])); for (const row of data.nodes) { const supplied = byId.get(row.id); if (finite(supplied?.x) && finite(supplied?.y)) row.style = {x: supplied.x, y: supplied.y}; } }
        if (topologyChanged && !presetLayout) graph.stopLayout(); // never let an old simulation keep running over replaced data
        if (topologyChanged || typeof graph.updateNodeData !== 'function') graph.setData(data); else { const plain = graphData(nextNodes, nextEdges, {}, dense ? currentStyle : null); graph.updateNodeData(plain.nodes.map(({style, ...row}) => ({...row, ...(dense ? {style: Object.fromEntries(Object.entries(style || {}).filter(([key]) => key !== 'x' && key !== 'y'))} : {})}))); graph.updateEdgeData(plain.edges); }
        graph.setNode(nodeOptions(currentStyle, dense)); graph.setEdge(edgeOptions(currentStyle, dense));
        await graph.draw(); await applySelected();
        appliedSignature = signature; appliedDataKey = nextDataKey; appliedStyleKey = nextKey; appliedStyle = {...nextStyle}; // commit only after success
        if (container.dataset.rendererState === 'error') { container.dataset.rendererState = 'ready'; container.setAttribute('aria-busy', 'false'); }
        if (topologyChanged) { stats.topologyChanges += 1; if (!presetLayout) { stats.layoutRuns += 1; run(() => graph.layout()); scheduleStop(); } } else stats.styleUpdates += 1; // bounded relayout runs in the background: it must never block later updates
      });
    },
    restyle(nextNodes, nextEdges, nextStyle, meta) { return this.update(nextNodes, nextEdges, nextStyle, meta); },
    capture: () => {
      if (disposed || !rendered) return viewState;
      try { return {zoom: graph.getZoom(), position: graph.getViewportByCanvas([0, 0]), positions: Object.fromEntries(graph.getNodeData().map(node => [node.id, graph.getElementPosition(node.id)]))}; } catch (error) { report(error); return viewState; }
    },
    destroy: () => { if (disposed) return; disposed = true; if (stopTimer !== null) clearTimeout(stopTimer); container.removeEventListener('pointerdown', pointerDown); container.removeEventListener('pointermove', pointerMove); container.removeEventListener('click', trailingClick, true); container.removeEventListener('click', physicalHit); container.removeEventListener('keydown', keyDown); proxy.remove(); container.dataset.rendererState = 'disposed'; container.setAttribute('aria-busy', 'false'); graph.destroy(); },
    select: id => { if (disposed || id === lastSelected) return; if (!rendered) { lastSelected = id; return; } const ids = new Set(graph.getNodeData().map(node => node.id)); const changes = {}; if (lastSelected && ids.has(lastSelected)) changes[lastSelected] = []; if (id && ids.has(id)) changes[id] = ['selected']; lastSelected = id; if (Object.keys(changes).length) run(() => graph.setElementState(changes, false)); },
    resize: (width, height) => { if (!disposed) run(() => graph.setSize(width, height)); },
    zoomBy: ratio => { if (!disposed && rendered) run(() => graph.zoomTo(clampNetworkZoom(graph.getZoom() * ratio))); },
    fit: () => { if (!disposed && rendered) return run(() => graph.fitView({when: 'always'})); return Promise.resolve(); },
    /** Explicit "Fit new content": the only way topology changes move the camera. */
    fitNewContent() { stats.fitNewContentRuns += 1; return this.fit(); },
    whenIdle: () => queue
  };
}

export const AtlasG6 = {renderG6Network, graphData};
