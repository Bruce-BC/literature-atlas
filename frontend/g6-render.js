import {Graph, GraphEvent, NodeEvent} from '@antv/g6';
import {forceCollide, forceLink, forceManyBody, forceSimulation} from 'd3';
import {NETWORK_ZOOM_RANGE, clampNetworkZoom} from './network-style.js';
import {clampPan, completePositions, topologySignature} from './camera-policy.js';
import {TIER_STYLE} from './edge-strength.js';
import {namedWhenShown} from './node-emphasis.js';
import {capturedCamera, createCameraMemory, easeInOutCubic, interpolateCamera, rememberAutoMove, returnTarget} from './motion.js';

const safeColor = value => typeof value === 'string' && value ? value : '#64748b';
const DRAG_THRESHOLD = 10;
const WAKE_EVENTS = ['pointerdown', 'pointermove', 'pointerup', 'wheel', 'keydown']; // any of these on the map container returns its G canvases to full rate at once
const DENSE_THRESHOLD = 400; const CHUNK_SIZE = 250;
/** Element enter/exit fade on small graphs: starts at once and decelerates (no linear pop). */
export const ELEMENT_ANIMATION_MS = 320; export const MOTION_EASING = 'cubic-bezier(0.22, 1, 0.36, 1)';
export const EDGE_CURVE_OFFSET = 10; // canvas px: every edge is a slightly bent quadratic curve
/**
 * Relayout after a topology change (expand / collapse): the target layout is computed at once, off screen, and the
 * wrapper moves every node there itself over RELAYOUT.duration on a critically damped spring curve, while new nodes fade in from
 * the node they hang off.  One continuous motion that starts with the click, instead of a pause followed by force ticks.
 */
export const RELAYOUT = Object.freeze({alpha: 0.5, alphaDecay: 0.1, duration: 560, seedSpacing: 6});
/**
 * Critically damped spring, normalised to reach 1 at t = 1: starts from rest (no jump on the first frame), reaches its
 * top speed after about a ninth of the duration and then decelerates into place without overshoot.
 */
const SPRING_OMEGA = 9; const SPRING_END = 1 - (1 + SPRING_OMEGA) * Math.exp(-SPRING_OMEGA);
export const easeSpring = t => { const x = Math.min(1, Math.max(0, t)); return x >= 1 ? 1 : (1 - (1 + SPRING_OMEGA * x) * Math.exp(-SPRING_OMEGA * x)) / SPRING_END; };
/**
 * Target positions of a seeded force relayout (pure, synchronous): same forces as the live layout; the centroid of the
 * nodes that already existed is kept, so the part of the graph the user was looking at does not drift.
 */
export function relayoutTargets(positions, edges, existingIds = null) {
  const nodes = Object.entries(positions).map(([id, p]) => ({id, x: p.x, y: p.y})); if (!nodes.length) return {};
  const known = new Set(nodes.map(node => node.id)); const links = edges.filter(([a, b]) => known.has(a) && known.has(b) && a !== b).map(([source, target]) => ({source, target}));
  const simulation = forceSimulation(nodes).force('charge', forceManyBody().strength(-80)).force('link', forceLink(links).id(node => node.id).distance(80)).force('collide', forceCollide(18)).alpha(RELAYOUT.alpha).alphaDecay(RELAYOUT.alphaDecay).stop();
  const ticks = Math.ceil(Math.log(simulation.alphaMin() / RELAYOUT.alpha) / Math.log(1 - RELAYOUT.alphaDecay)); simulation.tick(ticks);
  const anchors = nodes.filter(node => !existingIds || existingIds.has(node.id)); const pool = anchors.length ? anchors : nodes;
  const dx = pool.reduce((sum, node) => sum + (positions[node.id].x - node.x), 0) / pool.length; const dy = pool.reduce((sum, node) => sum + (positions[node.id].y - node.y), 0) / pool.length;
  return Object.fromEntries(nodes.map(node => [node.id, {x: node.x + dx, y: node.y + dy}]));
}
const FORCE_LAYOUT = Object.freeze({type: 'd3-force', animation: true, enableWorker: false, alpha: 1, alphaDecay: 0.14, manyBody: {strength: -80}, link: {distance: 80}, collide: {radius: 18}});
const reduceMotion = () => typeof window !== 'undefined' && Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches);
const nodeType = datum => datum.data?.shape || (datum.data?.kind === 'concept' ? 'diamond' : 'circle');
const yieldToBrowser = () => new Promise(resolve => setTimeout(resolve, 0));

function nodeOptionsFull(style, count = 0) {
  const size = Number(style.nodeSize) > 0 ? Number(style.nodeSize) : 18; const showLabel = style.labels !== false; const showAll = Boolean(style.showAllLabels);
  return {type: nodeType, style: {size: datum => datum.data?.size ?? (datum.data?.representative ? Math.max(size, 22) : size), fill: datum => safeColor(datum.data?.categoryColor || style.seriesColor || style.focusColor), fillOpacity: datum => datum.data?.dimmed ? 0.22 : 1, strokeOpacity: datum => datum.data?.dimmed ? 0.3 : 1, stroke: datum => datum.data?.outline || (datum.data?.representative ? safeColor(style.textColor) : safeColor(style.panelColor)), lineWidth: datum => datum.data?.levelLineWidth ?? (datum.data?.representative ? 3 : datum.data?.ring ? 2 : (size < 12 ? 1 : 2)), lineDash: datum => datum.data?.levelDash || 0, iconText: datum => datum.data?.glyph || '', iconFill: safeColor(style.panelColor), iconFontSize: 11, iconFontWeight: 700, label: showAll ? datum => namedWhenShown(datum.data || {}, count) : false, labelText: datum => { const label = String(datum.data?.label || datum.id); return label.length > 26 ? `${label.slice(0, 24)}…` : label; }, labelFill: safeColor(style.textColor), labelFontSize: 11, labelPlacement: 'bottom', labelOffsetY: 5, labelBackground: true, labelBackgroundFill: safeColor(style.panelColor), labelBackgroundRadius: 4, labelBackgroundPadding: [3, 5]}, state: {active: {label: showLabel, lineWidth: 4, stroke: safeColor(style.focusColor)}, selected: {label: showLabel, lineWidth: 5, stroke: safeColor(style.focusColor)}}};
}
/** Edge width / dash / opacity: the plan §2.2 strength tier when the edge has one (semantic, keyword), else the typed defaults (hierarchy dashed, citation arrow). */
const edgeTierStyle = data => { const tier = TIER_STYLE[data?.tier]; return {lineWidth: tier ? tier.width : data?.relation === 'hierarchy' ? 2 : 1.5, lineDash: tier?.dash ? [...tier.dash] : data?.relation === 'hierarchy' ? [6, 3] : [0, 0], strokeOpacity: data?.dimmed ? 0.15 : tier ? tier.opacity : 0.9}; };
const edgeOptionsFull = style => ({type: 'quadratic', style: {curveOffset: EDGE_CURVE_OFFSET, stroke: datum => datum.data?.relation === 'membership' || datum.data?.relation === 'hierarchy' ? safeColor(style.seriesColor || style.borderColor) : safeColor(style.borderColor), lineWidth: datum => edgeTierStyle(datum.data).lineWidth, lineDash: datum => edgeTierStyle(datum.data).lineDash, strokeOpacity: datum => edgeTierStyle(datum.data).strokeOpacity, endArrow: datum => Boolean(datum.data?.directed)}});

// Dense graphs: static per-datum styles and constant element types (no per-element JS callbacks).
function nodeOptions(style, dense = false, count = 0) {
  const full = nodeOptionsFull(style, count); if (!dense) return full;
  const {size, fill, fillOpacity, strokeOpacity, stroke, lineWidth, lineDash, iconText, label, ...rest} = full.style; return {type: nodeType, style: rest, state: full.state}; // dense: `label` is a static per-datum value (hubs only)
}
function edgeOptions(style, dense = false) {
  const full = edgeOptionsFull(style); if (!dense) return full;
  return {type: 'quadratic', style: {curveOffset: EDGE_CURVE_OFFSET}};
}
const finite = value => Number.isFinite(value);
/** Bounding box of finite [x, y] points (null when there are none). */
const boxOf = points => { let x1 = Infinity; let y1 = Infinity; let x2 = -Infinity; let y2 = -Infinity; for (const [x, y] of points) { if (!finite(x) || !finite(y)) continue; x1 = Math.min(x1, x); y1 = Math.min(y1, y); x2 = Math.max(x2, x); y2 = Math.max(y2, y); } return x1 <= x2 ? {x1, y1, x2, y2} : null; };
const dataBox = rows => boxOf(rows.map(row => [row.data.x, row.data.y]));
const nextFrame = callback => typeof requestAnimationFrame === 'function' ? requestAnimationFrame(callback) : setTimeout(callback, 16);
const cancelFrame = handle => { if (typeof cancelAnimationFrame === 'function') cancelAnimationFrame(handle); else clearTimeout(handle); };
const nowMs = () => (typeof performance !== 'undefined' ? performance.now() : Date.now());
export const IDLE_THROTTLE_MS = 1000; export const SLOW_TICK_MS = 250;
/**
 * Per-graph frame scheduler handed to every G layer canvas as its OWN `requestAnimationFrame`/`cancelAnimationFrame`
 * (G `CanvasConfig`, an instance property: no global is patched).  G's render loop and its animation timeline request
 * one frame per tick; at full rate that is a real animation frame.  When nothing is busy and there was no activity for
 * `idleMs`, requests are batched onto ONE `slowMs` timer instead (~4 ticks/s).  `wake()` (any input or wrapper update)
 * cancels the pending slow tick and moves every waiting callback onto a real animation frame at once.  Counters are
 * written into `stats` (`idleThrottled`, `slowTicks`, `wakes`, `lastWakeMs`).
 */
export function createFrameGate({isBusy = () => false, stats = {}, now = nowMs, raf = nextFrame, caf = cancelFrame, setTimer = setTimeout, clearTimer = clearTimeout, idleMs = IDLE_THROTTLE_MS, slowMs = SLOW_TICK_MS} = {}) {
  let seq = 0; let last = now(); let slowTimer = null; let closed = false; let wokeAt = null; const native = new Map(); const slow = new Map();
  stats.idleThrottled = false; stats.slowTicks = stats.slowTicks || 0; stats.wakes = stats.wakes || 0; stats.lastWakeMs = stats.lastWakeMs ?? null;
  // lastWakeMs: wake -> first real frame that runs a woken callback (one frame, never the slow interval)
  const toNative = (id, callback) => native.set(id, raf(time => { native.delete(id); if (wokeAt !== null) { stats.lastWakeMs = now() - wokeAt; wokeAt = null; } callback(time); }));
  const flushSlow = () => { slowTimer = null; const due = [...slow.values()]; slow.clear(); stats.slowTicks += 1; const time = now(); for (const callback of due) callback(time); };
  const request = callback => {
    const id = ++seq; const busy = closed || isBusy(); if (busy) last = now();
    if (!busy && now() - last >= idleMs) { stats.idleThrottled = true; slow.set(id, callback); if (slowTimer === null) slowTimer = setTimer(flushSlow, slowMs); }
    else { stats.idleThrottled = false; toNative(id, callback); }
    return id;
  };
  const cancel = id => { if (native.has(id)) { caf(native.get(id)); native.delete(id); } else if (slow.delete(id) && !slow.size && slowTimer !== null) { clearTimer(slowTimer); slowTimer = null; } };
  const wake = (count = true) => {
    last = now(); stats.idleThrottled = false; if (!slow.size) return;
    if (slowTimer !== null) { clearTimer(slowTimer); slowTimer = null; } const due = [...slow]; slow.clear(); if (count) { stats.wakes += 1; stats.lastWakeMs = null; wokeAt = now(); } for (const [id, callback] of due) toNative(id, callback);
  };
  const close = () => { closed = true; wake(false); for (const handle of native.values()) caf(handle); native.clear(); stats.idleThrottled = false; };
  return {request, cancel, wake, close, get pending() { return {native: native.size, slow: slow.size}; }};
}
/** Static per-datum node style (same visual result as the style functions in nodeOptions). */
const staticNodeStyle = (data, style) => { const size = Number(style.nodeSize) > 0 ? Number(style.nodeSize) : 18; return {label: Boolean(style.showAllLabels && (data.hub || data.representative)), size: data.size ?? (data.representative ? Math.max(size, 22) : size), fill: safeColor(data.categoryColor || style.seriesColor || style.focusColor), fillOpacity: data.dimmed ? 0.22 : 1, strokeOpacity: data.dimmed ? 0.3 : 1, stroke: data.outline || (data.representative ? safeColor(style.textColor) : safeColor(style.panelColor)), lineWidth: data.representative ? 3 : data.ring ? 2 : (size < 12 ? 1 : 2)}; };
/** Nodes carry coordinates in `style` (G6 5.x); a top-level x/y is not honoured. */
export function graphData(nodes, edges, positions = {}, staticStyle = null) {
  return {
    nodes: nodes.map(node => { const supplied = positions[node.data.id]; const x = finite(supplied?.x) ? supplied.x : node.data.x; const y = finite(supplied?.y) ? supplied.y : node.data.y; const base = finite(x) && finite(y) ? {x, y} : {}; return {id: node.data.id, ...(staticStyle || Object.keys(base).length ? {style: {...base, ...(staticStyle ? staticNodeStyle(node.data, staticStyle) : {})}} : {}), data: {...node.data, label: String(node.data.label || node.data.id)}}; }),
    edges: edges.map(edge => ({id: edge.data.id, source: edge.data.source, target: edge.data.target, ...(staticStyle ? {style: {stroke: safeColor(staticStyle.borderColor), ...edgeTierStyle(edge.data), endArrow: Boolean(edge.data.directed)}} : {}), data: {...edge.data}}))
  };
}
const signatureOf = (nodes, edges) => topologySignature(nodes.map(node => node.data.id), edges.map(edge => [edge.data.source, edge.data.target, edge.data.id]));
const styleKey = style => JSON.stringify([style.textColor, style.panelColor, style.borderColor, style.focusColor, style.seriesColor, style.nodeSize, style.labels, Boolean(style.showAllLabels)]);
// Preset views also key on data coordinates so a re-laid-out preset (same IDs) really moves its points.
const dataKey = (nodes, edges, withXY = false) => JSON.stringify([nodes.map(row => [row.data.id, row.data.categoryColor, row.data.dimmed, row.data.label, row.data.outline, row.data.shape ?? null, row.data.size ?? null, ...(withXY ? [row.data.x, row.data.y] : [])]), edges.map(row => [row.data.id, row.data.dimmed, row.data.tier ?? null])]); // tier: a tier-only change must redraw
/** Selection is an ID list: one paper may own several replica points (collection clouds). */
const selectionOf = value => Array.isArray(value) ? [...new Set(value.filter(id => id !== null && id !== undefined))] : value === null || value === undefined ? [] : [value];
const selectionKey = ids => ids.join('\u0000');

/**
 * `overlay` (optional) is a factory `({graph, container}) => handle` for a wrapper-owned
 * overlay that lives OUTSIDE the container (see cloud-overlay.js).  The wrapper drives its
 * lifecycle: `update(model)` (creation and `update(..., {overlay})`), `ready()`, `follow(active)`
 * around camera transforms, `refit()` after Fit, `hover(id)`, `resize()` and `destroy()`.
 * `onMiss` receives physical clicks that hit no node ({clientX, clientY, canvas}).
 */
export function renderG6Network(container, nodes, edges, {textColor, panelColor, borderColor, focusColor, seriesColor, nodeSize, labels, showAllLabels = false, onSelect, onMiss = null, selectedId = null, viewState = null, onError = console.error, GraphClass = Graph, layoutType = 'd3-force', hitRadius = null, ariaLabel = 'Network graph', overlay: overlayFactory = null, overlayModel = null, fitMaxZoom = null} = {}) {
  let currentStyle = {textColor, panelColor, borderColor, focusColor, seriesColor, nodeSize, labels, showAllLabels};
  const presetLayout = layoutType === 'preset';
  // `desired*` is the latest requested state; `applied*` is what the live graph really contains (committed after success).
  let desiredDataKey = dataKey(nodes, edges, presetLayout); let desiredSignature = signatureOf(nodes, edges); let desiredStyle = currentStyle;
  let appliedDataKey = desiredDataKey; let appliedSignature = desiredSignature; let appliedStyleKey = styleKey(currentStyle); let appliedStyle = currentStyle; let latestRevision = -Infinity; let selectHandler = onSelect; let missHandler = onMiss;
  const stats = {created: 1, topologyChanges: 0, styleUpdates: 0, layoutRuns: 0, droppedStale: 0, failedUpdates: 0, chunks: 0, layoutsFinished: 0, nativeClicks: 0, physicalClicks: 0, missedClicks: 0, suppressedDragClicks: 0, keyboardActivations: 0, fitNewContentRuns: 0, tweens: 0, tweenFrames: 0, tweensStopped: 0, tweensForced: 0, instantMoves: 0, idleThrottled: false, slowTicks: 0, wakes: 0, lastWakeMs: null, throttleLayers: 0, containerResizes: 0, lastRelayoutMs: null};
  // Preset views own deterministic data coordinates: stored (possibly dragged) positions are never restored, so the cached data bbox stays exact.
  const initialPositions = presetLayout ? {} : {...(viewState?.positions || {})}; const restoredPositions = Object.fromEntries(Object.entries(initialPositions).filter(([, point]) => Array.isArray(point) && point.every(finite)).map(([id, point]) => [id, {x: point[0], y: point[1]}]));
  const dense = nodes.length > DENSE_THRESHOLD; // dense graphs use precomputed per-datum styles: per-node style FUNCTIONS dominated vendor element creation
  const initialData = graphData(dense ? nodes.slice(0, CHUNK_SIZE) : nodes, dense ? [] : edges, restoredPositions, dense ? currentStyle : null); // dense graphs are created in bounded chunks between tasks
  // dense graphs disable element animations: they would make every awaited draw() take ~1 s
  // Idle frame gate (see createFrameGate): every G layer canvas of THIS graph gets the gate as its own frame scheduler.
  // Never throttled before ready, in a hidden tab (native rAF semantics kept), or while anything camera/motion/layout/update/animation is in flight.
  const doc = container.ownerDocument || (typeof document !== 'undefined' ? document : null);
  const layersOf = () => { try { return Object.values(graph.getCanvas?.()?.getLayers?.() || {}); } catch { return []; } };
  const frameBusy = () => { try { return !rendered || disposed || doc?.visibilityState === 'hidden' || animating > 0 || tween !== null || relayout !== null || panFrame !== null || correctingPan || transforming || pendingUpdates > 0 || layoutRequested || container.dataset.cameraState !== 'idle' || container.dataset.layoutState === 'running' || (container.dataset.motionState || 'idle') !== 'idle' || layersOf().some(layer => layer.document?.timeline?.animations?.length > 0); } catch { return true; } };
  const gate = createFrameGate({isBusy: frameBusy, stats}); let transforming = false;
  const inputWake = () => gate.wake(); const visibilityWake = () => gate.wake(false);
  const options = {container, canvas: {requestAnimationFrame: gate.request, cancelAnimationFrame: gate.cancel}, zoomRange: [...NETWORK_ZOOM_RANGE], autoFit: viewState || dense ? undefined : 'view', animation: dense ? false : {duration: reduceMotion() ? 0 : ELEMENT_ANIMATION_MS, easing: MOTION_EASING}, padding: 24, data: initialData, node: nodeOptions(currentStyle, dense, nodes.length), edge: edgeOptions(currentStyle, dense), behaviors: ['drag-canvas', 'zoom-canvas', {type: 'drag-element-force', key: 'force-drag', fixed: false, trigger: []}, {type: 'hover-activate', degree: 0}]};
  // G6 5.1.1 registers no `preset` layout: positions come from node style.x/y and no layout is configured.
  if (!presetLayout) options.layout = {...FORCE_LAYOUT};
  const graph = new GraphClass(options);
  const overlay = typeof overlayFactory === 'function' ? overlayFactory({graph, container}) : null; overlay?.update(overlayModel);
  let disposed = false; let rendered = false; let selected = selectionOf(selectedId); let stopTimer = null; let focusIndex = -1;
  // ---- pan clamp: preset bbox is cached from data coordinates; force bbox is recomputed per (frame-merged) event ----
  let presetBox = presetLayout ? dataBox(nodes) : null; let panFrame = null; let correctingPan = false; let animating = 0;
  const nodeBox = () => presetLayout ? presetBox : boxOf(graph.getNodeData().map(node => graph.getElementPosition(node.id)));
  const clampedOrigin = (origin, zoom = graph.getZoom()) => { const size = typeof graph.getSize === 'function' ? graph.getSize() : null; const next = clampPan({viewport: size ? {width: size[0], height: size[1]} : null, bbox: nodeBox(), zoom, pan: {x: origin[0], y: origin[1]}}); return [next.x, next.y]; };
  container.dataset.rendererState = 'rendering'; container.dataset.layout = presetLayout ? 'preset' : 'd3-force'; container.dataset.layoutState = presetLayout ? 'settled' : 'running'; container.setAttribute('aria-busy', 'true');
  container.tabIndex = 0; container.setAttribute('role', 'listbox'); container.setAttribute('aria-roledescription', 'graph'); container.setAttribute('aria-label', `${ariaLabel} · Arrow keys move between nodes, Enter or Space activates`);
  // ONE bounded DOM proxy gives the canvas node in focus a real accessible name (no per-node DOM).
  const proxy = typeof document !== 'undefined' && typeof container.append === 'function' ? document.createElement('div') : {setAttribute() {}, dataset: {}, remove() {}}; proxy.setAttribute('role', 'option'); proxy.setAttribute('aria-selected', 'false'); proxy.className = 'atlas-sr-only'; proxy.id = `atlas-g6-focus-${container.id || 'graph'}`; if (typeof container.append === 'function') container.append(proxy);
  const report = error => { if (!disposed) { container.dataset.rendererState = 'error'; container.setAttribute('aria-busy', 'false'); onError(error); } };
  const run = operation => { gate.wake(); try { return Promise.resolve(operation()).catch(report); } catch (error) { report(error); return Promise.resolve(); } };
  // One correction per frame; our own translateBy re-fires AFTER_TRANSFORM (re-entrancy guard); never during our fitView/zoomTo animations (one correction after they finish).
  // data-camera-state: 'animating' (our fitView/zoomTo), 'clamping' (correction frame pending), 'idle' (camera final and clamped).
  const cameraIdle = () => { if (!disposed && animating === 0 && panFrame === null && !correctingPan) container.dataset.cameraState = 'idle'; };
  const correctPan = () => { panFrame = null; if (disposed || !rendered || correctingPan || animating > 0) return; try { const origin = graph.getViewportByCanvas([0, 0]); const target = clampedOrigin(origin); const delta = [target[0] - origin[0], target[1] - origin[1]]; if (Math.abs(delta[0]) < 0.5 && Math.abs(delta[1]) < 0.5) { cameraIdle(); return; } correctingPan = true; run(() => graph.translateBy(delta, false)).finally(() => { correctingPan = false; cameraIdle(); }); } catch (error) { report(error); } };
  const schedulePanClamp = () => { if (panFrame === null && !disposed) { panFrame = nextFrame(correctPan); if (animating === 0) container.dataset.cameraState = 'clamping'; } };
  const animated = operation => { animating += 1; container.dataset.cameraState = 'animating'; return run(operation).finally(() => { animating -= 1; schedulePanClamp(); }); };
  // ---- camera motion (plan motion-and-edges §3.3): self-owned rAF tween, independent of G6 animation (off on the dense path) ----
  // Camera = {zoom, x, y} with screen = model * zoom + (x, y).  The whole tween is ONE animated span (counter opened at start, closed
  // synchronously on finish/stop), and every frame's camera goes through clampCamera = the wrapper's own pan clamp (AC-14 on every frame).
  let tween = null; let memory = createCameraMemory(); let lastTween = null;
  const cameraNow = () => { const origin = graph.getViewportByCanvas([0, 0]); return {zoom: graph.getZoom(), x: origin[0], y: origin[1]}; };
  const viewportNow = () => { const size = typeof graph.getSize === 'function' ? graph.getSize() : null; return size ? {width: size[0], height: size[1]} : {width: container.clientWidth, height: container.clientHeight}; };
  const clampCamera = camera => { const zoom = clampNetworkZoom(camera.zoom); const [x, y] = clampedOrigin([camera.x, camera.y], zoom); return {zoom, x, y}; };
  const applyCamera = camera => { if (Math.abs(graph.getZoom() - camera.zoom) > 1e-9) run(() => graph.zoomTo(camera.zoom, false, [0, 0])); const origin = graph.getViewportByCanvas([0, 0]); const delta = [camera.x - origin[0], camera.y - origin[1]]; if (Math.abs(delta[0]) > 1e-6 || Math.abs(delta[1]) > 1e-6) run(() => graph.translateBy(delta, false)); };
  /** finish: 'done' | 'forced' (final frame applied) | 'jump' (final frame applied, new input/reset) | 'stop' (user gesture: stays where it is). */
  const endTween = how => { const current = tween; if (!current) return; tween = null; cancelFrame(current.frame); clearTimeout(current.guard); if (how !== 'stop' && !disposed) { applyCamera(current.target); current.onFrame?.(current.target); } current.record.state = how; current.record.camera = disposed ? null : cameraNow(); if (how === 'stop') stats.tweensStopped += 1; if (how === 'forced') stats.tweensForced += 1; animating -= 1; if (!disposed) schedulePanClamp(); current.resolve(how); if (how === 'stop') current.onStop?.(); };
  const startTween = (target, {delay = 0, duration = 0, onFrame = null, onStop = null, kind = 'move', easing = easeInOutCubic} = {}) => {
    endTween('jump'); const to = clampCamera(target);
    if (!(duration > 0)) { stats.instantMoves += 1; lastTween = {kind, frames: 0, state: 'instant', target: to, camera: null}; animated(() => applyCamera(to)); onFrame?.(to); if (lastTween) lastTween.camera = cameraNow(); return Promise.resolve('instant'); }
    const from = cameraNow(); const viewport = viewportNow(); const start = nowMs(); const record = lastTween = {kind, frames: 0, state: 'running', from, target: to, camera: null};
    animating += 1; container.dataset.cameraState = 'animating'; stats.tweens += 1;
    return new Promise(resolve => {
      const current = tween = {target: to, record, onFrame, onStop, resolve, frame: null, guard: null};
      const step = () => {
        current.frame = null; if (tween !== current || disposed) return; const t = (nowMs() - start - delay) / duration;
        if (t >= 0) { if (t >= 1) { record.frames += 1; stats.tweenFrames += 1; endTween('done'); return; } const camera = clampCamera(interpolateCamera(from, to, easing(t), viewport)); applyCamera(camera); record.frames += 1; stats.tweenFrames += 1; onFrame?.(camera); }
        current.frame = nextFrame(step);
      };
      current.frame = nextFrame(step); current.guard = setTimeout(() => { if (tween === current) endTween('forced'); }, delay + duration + 100); // hidden tab: rAF stops, the final frame is still applied
    });
  };
  // A user pointerdown / wheel / key on the map (capture phase, before G6's drag handling) stops the tween where it is and closes the span synchronously, so pan correction applies to the user's gesture.
  const userGesture = () => { if (tween) { memory = createCameraMemory(); endTween('stop'); } };
  // The overlay follows every camera frame: animated transforms only emit AFTER_TRANSFORM when they finish.
  graph.on(GraphEvent.BEFORE_TRANSFORM, () => { if (disposed) return; transforming = true; gate.wake(); overlay?.follow(true); });
  graph.on(GraphEvent.AFTER_TRANSFORM, () => { if (disposed) return; transforming = false; overlay?.follow(false); if (!correctingPan && animating === 0) schedulePanClamp(); });

  // ---- single click dispatcher -------------------------------------------------
  let pointerStart = null; let dragged = false; let nativeClicked = false;
  const activate = id => { if (!disposed && id !== undefined && id !== null) selectHandler?.(id); };
  const idOf = event => event.target?.id ?? event.target;
  graph.on(NodeEvent.POINTER_ENTER, event => { if (disposed) return; overlay?.hover(idOf(event)); const datum = graph.getNodeData().find(node => node.id === idOf(event)); if (datum) container.title = [datum.data?.label || datum.id, datum.data?.categoryPath || datum.data?.categoryLabel, Number.isFinite(datum.data?.degree) ? `Observed neighbors ${datum.data.degree}/${datum.data.degreePossible} possible` : null].filter(Boolean).join(' · '); });
  graph.on(NodeEvent.POINTER_LEAVE, () => { container.title = ''; if (!disposed) overlay?.hover(null); });
  // Real layout lifecycle: 'settled' means the layout finished (positions stop moving), not that a timer elapsed.
  // A layout is REQUESTED the moment a topology change is accepted (before draw), STARTED on BEFORE_LAYOUT and finished on the first AFTER_LAYOUT that follows its start.
  let layoutRequested = !presetLayout; let layoutStarted = false; let relayoutStarted = null; let relayout = null;
  const markSettled = () => { layoutRequested = false; layoutStarted = false; if (!disposed) container.dataset.layoutState = 'settled'; };
  const requestLayout = () => { if (presetLayout) return; layoutRequested = true; layoutStarted = false; container.dataset.layoutState = 'running'; };
  if (!presetLayout) { graph.on(GraphEvent.BEFORE_LAYOUT, () => { if (!disposed && layoutRequested) layoutStarted = true; }); graph.on(GraphEvent.AFTER_LAYOUT, () => { if (disposed) return; stats.layoutsFinished += 1; if (relayoutStarted !== null) { stats.lastRelayoutMs = Math.round(nowMs() - relayoutStarted); relayoutStarted = null; } if (!layoutRequested || layoutStarted) markSettled(); }); }
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
    else if (missHandler) { stats.missedClicks += 1; missHandler({clientX: event.clientX, clientY: event.clientY, canvas: graph.getCanvasByClient([event.clientX, event.clientY])}); }
  };
  container.addEventListener('pointerdown', userGesture, true); container.addEventListener('wheel', userGesture, {capture: true, passive: true}); container.addEventListener('keydown', userGesture, true);
  for (const type of WAKE_EVENTS) container.addEventListener(type, inputWake, {capture: true, passive: true}); doc?.addEventListener?.('visibilitychange', visibilityWake);
  container.addEventListener('pointerdown', pointerDown); container.addEventListener('pointermove', pointerMove); container.addEventListener('click', trailingClick, true); container.addEventListener('click', physicalHit);
  // ---- keyboard: focus moves an 'active' highlight only; Enter/Space activates ---
  const keyDown = event => {
    const ids = graph.getNodeData().map(node => node.id); if (!ids.length || disposed || !rendered) return;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') { event.preventDefault(); focusIndex = (focusIndex + 1) % ids.length; }
    else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') { event.preventDefault(); focusIndex = (focusIndex - 1 + ids.length) % ids.length; }
    else if ((event.key === 'Enter' || event.key === ' ') && focusIndex >= 0) { event.preventDefault(); stats.keyboardActivations += 1; activate(ids[focusIndex]); return; }
    else return;
    const datum = graph.getNodeData().find(node => node.id === ids[focusIndex]); const name = `${datum?.data?.label || ids[focusIndex]}${datum?.data?.categoryLabel ? ` · ${datum.data.categoryLabel}` : ''}${datum?.data?.representative ? (datum.data.expanded ? ' · expanded' : ' · collapsed') : ''}`; proxy.textContent = name; proxy.dataset.nodeId = ids[focusIndex]; proxy.setAttribute('aria-selected', String(selected.includes(ids[focusIndex]))); container.setAttribute('aria-activedescendant', proxy.id); container.title = name;
    run(() => graph.setElementState(Object.fromEntries(ids.map(id => [id, selected.includes(id) ? ['selected'] : id === ids[focusIndex] ? ['active'] : []])), false));
  };
  container.addEventListener('keydown', keyDown);
  // A wrapper with an overlay (the collection map) keeps its G canvases at the container size when CSS changes the map height
  // (viewport height, stacked layout with the panel open): no fit, the camera stays and is clamped, the overlay re-places its labels.
  // G keeps the camera CENTRE on a canvas resize, which would shift the content by half the size change: the top-left is re-anchored
  // (the content stays where the user saw it), clamped at once (unless a tween owns the camera: it clamps every frame), and the overlay
  // is re-synced from the actual camera in the same callback, so points and outlines never disagree for a frame.
  const syncSize = () => {
    if (disposed || !rendered || typeof graph.getSize !== 'function') return; const width = container.clientWidth; const height = container.clientHeight; const size = graph.getSize();
    if (!width || !height || (Math.abs(size[0] - width) < 1 && Math.abs(size[1] - height) < 1)) return;
    stats.containerResizes += 1; gate.wake();
    try {
      const before = graph.getViewportByCanvas([0, 0]); graph.setSize(width, height); const after = graph.getViewportByCanvas([0, 0]);
      const target = tween ? before : clampedOrigin(before); const delta = [target[0] - after[0], target[1] - after[1]];
      if (Math.abs(delta[0]) > 1e-6 || Math.abs(delta[1]) > 1e-6) { correctingPan = true; try { run(() => graph.translateBy(delta, false)); } finally { correctingPan = false; } }
    } catch (error) { report(error); }
    overlay.resize(); schedulePanClamp();
  };
  const sizeObserver = overlay && typeof ResizeObserver === 'function' ? new ResizeObserver(syncSize) : null; sizeObserver?.observe(container);

  const positionsNow = () => Object.fromEntries(graph.getNodeData().map(node => { const p = graph.getElementPosition(node.id); return [node.id, {x: p[0], y: p[1]}]; }));
  const scheduleStop = () => { if (presetLayout) return; if (stopTimer !== null) clearTimeout(stopTimer); stopTimer = setTimeout(() => { stopTimer = null; if (!disposed) run(() => graph.stopLayout()).then(markSettled); }, 8000); }; // safety bound only; normal settle is the AFTER_LAYOUT event
  const applySelected = async () => { const present = new Set(graph.getNodeData().map(node => node.id)); const ids = selected.filter(id => present.has(id)); if (ids.length === 1) await graph.setElementState(ids[0], 'selected'); else if (ids.length) await graph.setElementState(Object.fromEntries(ids.map(id => [id, ['selected']]))); };

  // ---- eased relayout: target computed once, every node moved by the wrapper (see RELAYOUT) ----
  // Fit of a force graph: an eased camera tween to the box of `positions` (the relayout TARGETS while nodes are still moving), never past fitMaxZoom.
  let fitOnRelayout = false;
  const easedFit = positions => {
    const box = boxOf(Object.values(positions).map(point => [point.x, point.y])); if (!box || disposed) return Promise.resolve();
    const {width, height} = viewportNow(); const margin = 36; const pad = 24; const bw = Math.max(1, box.x2 - box.x1 + 2 * margin); const bh = Math.max(1, box.y2 - box.y1 + 2 * margin);
    const zoom = clampNetworkZoom(Math.min((width - 2 * pad) / bw, (height - 2 * pad) / bh, fitMaxZoom > 0 ? fitMaxZoom : Infinity));
    return startTween({zoom, x: width / 2 - (box.x1 + box.x2) / 2 * zoom, y: height / 2 - (box.y1 + box.y2) / 2 * zoom}, {duration: reduceMotion() ? 0 : RELAYOUT.duration, easing: easeSpring, kind: 'fit'});
  };
  const stopRelayout = () => { const current = relayout; if (!current) return; relayout = null; cancelFrame(current.frame); clearTimeout(current.guard); current.resolve(); };
  const startRelayout = (from, edgePairs, existingIds) => {
    stopRelayout(); const started = nowMs(); const to = relayoutTargets(from, edgePairs, existingIds); const ids = Object.keys(to); if (fitOnRelayout) { fitOnRelayout = false; easedFit(to); } const duration = reduceMotion() ? 0 : RELAYOUT.duration;
    const place = k => { const live = new Set(graph.getNodeData().map(node => node.id)); const frame = {}; for (const id of ids) if (live.has(id)) frame[id] = [from[id].x + (to[id].x - from[id].x) * k, from[id].y + (to[id].y - from[id].y) * k]; if (Object.keys(frame).length) run(() => graph.translateElementTo(frame, false)); };
    return new Promise(resolve => {
      const finish = () => { if (relayout !== current) return; relayout = null; cancelFrame(current.frame); clearTimeout(current.guard); if (!disposed) { place(1); stats.layoutsFinished += 1; stats.lastRelayoutMs = Math.round(nowMs() - started); markSettled(); schedulePanClamp(); } resolve(); };
      const current = relayout = {frame: null, guard: null, resolve, to};
      if (!(duration > 0)) { finish(); return; }
      const step = () => { current.frame = null; if (relayout !== current || disposed) return; const t = (nowMs() - started) / duration; if (t >= 1) { finish(); return; } place(easeSpring(t)); current.frame = nextFrame(step); };
      current.frame = nextFrame(step); current.guard = setTimeout(finish, duration + 150); // hidden tab: rAF stops, the final positions are still applied
    });
  };

  const ready = Promise.resolve().then(() => disposed ? null : graph.render()).then(async () => {
    if (disposed) return null;
    if (dense) {
      const rest = graphData(nodes.slice(CHUNK_SIZE), [], restoredPositions, currentStyle).nodes; const allEdges = graphData([], edges, {}, currentStyle).edges;
      for (let index = 0; index < rest.length && !disposed; index += CHUNK_SIZE) { await yieldToBrowser(); if (disposed) return null; graph.addNodeData(rest.slice(index, index + CHUNK_SIZE)); await graph.draw(); stats.chunks += 1; }
      for (let index = 0; index < allEdges.length && !disposed; index += CHUNK_SIZE) { await yieldToBrowser(); if (disposed) return null; graph.addEdgeData(allEdges.slice(index, index + CHUNK_SIZE)); await graph.draw(); stats.chunks += 1; }
      if (disposed) return null;
      if (!viewState) await graph.fitView({when: 'always'});
    }
    if (!viewState && fitMaxZoom > 0 && graph.getZoom() > fitMaxZoom) await graph.zoomTo(fitMaxZoom, false); // a fit never zooms past the view's cap (collection map: below the sub-group detail zoom)
    if (viewState) {
      graph.stopLayout(); markSettled();
      const known = new Set(presetLayout ? [] : graph.getNodeData().map(node => node.id)); const restored = Object.fromEntries(Object.entries(viewState.positions || {}).filter(([id, point]) => known.has(id) && Array.isArray(point) && point.length >= 2 && point.every(finite)));
      if (Object.keys(restored).length) await graph.translateElementTo(restored, false);
      if (disposed) return null;
      if (Number.isFinite(viewState.zoom)) await graph.zoomTo(clampNetworkZoom(viewState.zoom), false);
      if (disposed) return null;
      if (Array.isArray(viewState.position) && viewState.position.every(finite)) { const origin = graph.getViewportByCanvas([0, 0]); const target = clampedOrigin(viewState.position); await graph.translateBy(target.map((value, index) => value - (origin[index] || 0)), false); } // a stored out-of-range pan is clamped before it is applied
      if (disposed) return null;
    } else scheduleStop();
    await applySelected(); if (disposed) return null;
    rendered = true; stats.throttleLayers = layersOf().filter(layer => layer.requestAnimationFrame === gate.request).length; container.dataset.rendererState = 'ready'; container.setAttribute('aria-busy', 'false'); schedulePanClamp(); overlay?.ready(); if (sizeObserver) syncSize(); // a resize that arrived before the first render finished was ignored above
    return graph;
  }).catch(error => { report(error); return null; });

  // Serial queue: every update runs after the previous one; stale revisions are skipped (latest wins).
  let queue = ready;
  // A failed operation clears the pending desired state (back to what is actually applied) so an identical retry is NOT short-circuited.
  let pendingUpdates = 0; const markUpdateState = () => { if (!disposed) container.dataset.updateState = pendingUpdates > 0 ? 'pending' : 'idle'; };
  container.dataset.updateState = 'idle';
  const enqueue = (revision, operation) => { gate.wake(); pendingUpdates += 1; markUpdateState(); queue = queue.then(() => { if (disposed || !rendered) return null; if (revision < latestRevision) { stats.droppedStale += 1; return null; } return operation(); }).catch(error => { desiredSignature = appliedSignature; desiredDataKey = appliedDataKey; desiredStyle = appliedStyle; stats.failedUpdates += 1; markSettled(); report(error); }).then(() => { pendingUpdates -= 1; markUpdateState(); }); return queue; };

  return {
    graph, ready, stats, overlay, get selectedIds() { return [...selected]; }, get signature() { return appliedSignature; }, get desiredSignature() { return desiredSignature; }, get layoutType() { return layoutType; },
    /** Replace the click handler so a reused wrapper never resolves IDs against a stale snapshot/projection. */
    setOnSelect(handler) { selectHandler = handler; },
    setOnMiss(handler) { missHandler = handler; },
    /** Apply new data/style.  Topology changes really add/remove nodes (seeded, bounded relayout); style-only changes reuse graph and layout. */
    update(nextNodes, nextEdges, nextStyle, {revision = latestRevision + 1, overlay: nextOverlayModel} = {}) {
      latestRevision = Math.max(latestRevision, revision); if (nextOverlayModel && !disposed) overlay?.update(nextOverlayModel);
      const signature = signatureOf(nextNodes, nextEdges); const nextDataKey = dataKey(nextNodes, nextEdges, presetLayout); const nextKey = styleKey(nextStyle);
      if (signature === desiredSignature && nextDataKey === desiredDataKey && nextKey === styleKey(desiredStyle)) return queue; // identical to the latest request
      desiredSignature = signature; desiredDataKey = nextDataKey; desiredStyle = {...nextStyle};
      if (signature !== appliedSignature) requestLayout();
      return enqueue(revision, async () => {
        // Decide against what the graph ACTUALLY contains; a skipped stale revision never marks its keys applied.
        const topologyChanged = signature !== appliedSignature; const styleChanged = nextKey !== appliedStyleKey; if (!topologyChanged && !styleChanged && nextDataKey === appliedDataKey) return;
        currentStyle = {...nextStyle}; stopRelayout(); let before = null;
        const ids = nextNodes.map(node => node.data.id);
        const data = graphData(nextNodes, nextEdges, topologyChanged ? completePositions(before = positionsNow(), ids, nextEdges.map(edge => [edge.data.source, edge.data.target]), presetLayout ? {} : {spacing: RELAYOUT.seedSpacing}) : positionsNow(), dense ? currentStyle : null);
        // Preset views keep their deterministic data coordinates for known IDs.
        if (presetLayout) { const byId = new Map(nextNodes.map(node => [node.data.id, node.data])); for (const row of data.nodes) { const supplied = byId.get(row.id); if (finite(supplied?.x) && finite(supplied?.y)) row.style = {x: supplied.x, y: supplied.y}; } presetBox = boxOf(data.nodes.map(row => [row.style?.x, row.style?.y])); }
        if (topologyChanged && !presetLayout) graph.stopLayout(); // never let an old simulation keep running over replaced data
        if (topologyChanged || typeof graph.updateNodeData !== 'function') graph.setData(data); else { const plain = graphData(nextNodes, nextEdges, {}, dense ? currentStyle : null); const presetXY = presetLayout ? new Map(data.nodes.map(row => [row.id, row.style])) : null; graph.updateNodeData(plain.nodes.map(({style, ...row}) => { const kept = dense ? Object.fromEntries(Object.entries(style || {}).filter(([key]) => key !== 'x' && key !== 'y')) : null; const xy = presetXY?.get(row.id); return {...row, ...(kept || xy ? {style: {...(kept || {}), ...(xy ? {x: xy.x, y: xy.y} : {})}} : {})}; })); graph.updateEdgeData(plain.edges); }
        graph.setNode(nodeOptions(currentStyle, dense, nextNodes.length)); graph.setEdge(edgeOptions(currentStyle, dense));
        const moving = topologyChanged && !presetLayout && typeof graph.translateElementTo === 'function';
        if (moving) { const drawn = graph.draw(); const moved = startRelayout(Object.fromEntries(data.nodes.map(row => [row.id, {x: row.style.x, y: row.style.y}])), nextEdges.map(edge => [edge.data.source, edge.data.target]), new Set(Object.keys(before || {}))); await drawn; await moved; } // the fade-in and the move run TOGETHER
        else await graph.draw();
        await applySelected(); if (fitOnRelayout) { fitOnRelayout = false; easedFit(positionsNow()); } // a requested fit whose update moved nothing
        appliedSignature = signature; appliedDataKey = nextDataKey; appliedStyleKey = nextKey; appliedStyle = {...nextStyle}; // commit only after success
        if (container.dataset.rendererState === 'error') { container.dataset.rendererState = 'ready'; container.setAttribute('aria-busy', 'false'); }
        if (topologyChanged) { stats.topologyChanges += 1; if (!presetLayout) { stats.layoutRuns += 1; if (!moving) { relayoutStarted = nowMs(); run(() => graph.layout({...FORCE_LAYOUT})); scheduleStop(); } } } else stats.styleUpdates += 1; // bounded relayout runs in the background: it must never block later updates
      });
    },
    restyle(nextNodes, nextEdges, nextStyle, meta) { return this.update(nextNodes, nextEdges, nextStyle, meta); },
    capture: () => {
      if (disposed || !rendered) return viewState;
      // an auto-moved camera is never persisted: during a tween or while untouched by the user, the pre-auto-move camera is captured
      try { const live = cameraNow(); const [x, y] = clampedOrigin([live.x, live.y]); const camera = capturedCamera(memory, {zoom: live.zoom, x, y}, tween); return {zoom: camera.zoom, position: [camera.x, camera.y], positions: Object.fromEntries(graph.getNodeData().map(node => [node.id, graph.getElementPosition(node.id)]))}; } catch (error) { report(error); return viewState; }
    },
    destroy: () => { if (disposed) return; endTween('jump'); stopRelayout(); disposed = true; if (stopTimer !== null) clearTimeout(stopTimer); if (panFrame !== null) cancelFrame(panFrame); delete container.dataset.cameraState; container.removeEventListener('pointerdown', userGesture, true); container.removeEventListener('wheel', userGesture, true); container.removeEventListener('keydown', userGesture, true); container.removeEventListener('pointerdown', pointerDown); container.removeEventListener('pointermove', pointerMove); container.removeEventListener('click', trailingClick, true); container.removeEventListener('click', physicalHit); container.removeEventListener('keydown', keyDown); for (const type of WAKE_EVENTS) container.removeEventListener(type, inputWake, true); doc?.removeEventListener?.('visibilitychange', visibilityWake); proxy.remove(); overlay?.destroy(); container.dataset.rendererState = 'disposed'; container.setAttribute('aria-busy', 'false'); sizeObserver?.disconnect(); graph.destroy(); gate.close(); },
    /** Select one ID or an ID list (every replica point of a paper); the previous selection is cleared. */
    select: id => { const next = selectionOf(id); if (disposed || selectionKey(next) === selectionKey(selected)) return; if (!rendered) { selected = next; return; } const ids = new Set(graph.getNodeData().map(node => node.id)); const changes = {}; for (const old of selected) if (ids.has(old)) changes[old] = []; for (const nid of next) if (ids.has(nid)) changes[nid] = ['selected']; selected = next; if (Object.keys(changes).length) run(() => graph.setElementState(changes, false)); },
    resize: (width, height) => { if (!disposed) { run(() => graph.setSize(width, height)); overlay?.resize(); } },
    zoomBy: ratio => { if (!disposed && rendered) { userGesture(); animated(() => graph.zoomTo(clampNetworkZoom(graph.getZoom() * ratio))); } },
    fit: () => { if (!disposed && rendered && !presetLayout && typeof graph.translateElementTo === 'function') { userGesture(); return easedFit(relayout ? relayout.to : positionsNow()); } if (!disposed && rendered) { userGesture(); return animated(async () => { await graph.fitView({when: 'always'}); if (fitMaxZoom > 0 && graph.getZoom() > fitMaxZoom) await graph.zoomTo(fitMaxZoom, false); }).then(() => { if (!disposed) overlay?.refit(); }); } return Promise.resolve(); },
    /** Camera motion (motion-driver.js): live camera, viewport, the wrapper's clamp, auto-move / return tweens, the running tween. */
    camera: () => (disposed || !rendered ? null : cameraNow()),
    viewport: () => viewportNow(),
    clampCamera: camera => clampCamera(camera),
    /** Auto-move: remembers the FIRST pre-move camera (return memory), then tweens (duration 0 = instant, still clamped). */
    moveCamera(target, timing = {}) { if (disposed || !rendered) return Promise.resolve('disposed'); endTween('jump'); const to = clampCamera(target); memory = rememberAutoMove(memory, cameraNow(), to); return startTween(to, {...timing, kind: 'move'}); },
    /** Deselect: tween back to the remembered camera when the user has not moved since the auto-move; the memory is cleared either way. */
    returnCamera(timing = {}) { if (disposed || !rendered) return null; endTween('jump'); const home = returnTarget(memory, cameraNow()); memory = createCameraMemory(); return home ? startTween(home, {...timing, kind: 'return'}) : null; },
    /** New input / reset / forced finish: the running tween jumps to its final frame. */
    finishTween: (how = 'jump') => endTween(how),
    get tweening() { return tween ? 1 : 0; },
    get lastTween() { return lastTween ? {...lastTween} : null; },
    get cameraMemory() { return {home: memory.home ? {...memory.home} : null, auto: memory.auto ? {...memory.auto} : null}; },
    whenTween: () => (tween ? new Promise(resolve => { const current = tween; const done = current.resolve; current.resolve = how => { done(how); resolve(how); }; }) : Promise.resolve(null)),
    /** Explicit "Fit new content": the only way topology changes move the camera. */
    fitNewContent() { stats.fitNewContentRuns += 1; if (!presetLayout && rendered && !disposed) { userGesture(); if (relayout) return easedFit(relayout.to); if (pendingUpdates > 0) { fitOnRelayout = true; return queue; } } return this.fit(); },
    whenIdle: () => queue
  };
}

export const AtlasG6 = {renderG6Network, graphData, createFrameGate, IDLE_THROTTLE_MS, SLOW_TICK_MS};
