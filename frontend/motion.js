/**
 * Pure transition coordinator for the collection map (plan motion-and-edges §3).  DOM-free: the RAF/WAAPI drivers
 * (`motion-driver.js`, the G6 wrapper's camera tween, the overlay and panel decorations) only execute what this
 * module decides.
 *
 *  - IMMEDIATE COMMIT: the real state is committed by the input handler exactly as without motion; a transition is
 *    decoration on top and never delays or alters the final state.
 *  - One transition per user input (`coalesceInput`): a point click dispatches twice but plays once.
 *  - Every timeline ends <= MOTION_LIMIT_MS (`buildTimeline`, generated, never a constant); reduced motion = 0 ms.
 *  - Camera rule by INPUT SOURCE (`cameraDecision`): cloud selection (label / chip / empty spot / related cloud /
 *    keyboard) zooms + centres only when the cloud is not fully visible or too small; a panel-list paper pans the
 *    minimum into the central 80 %; a map point click never moves.
 *  - Camera return memory (`rememberAutoMove`, `returnTarget`, `capturedCamera`): an auto-moved camera is never
 *    persisted while the user has not touched it.
 * Camera = {zoom, x, y}: screen = model * zoom + (x, y) (the G6 viewport position of the canvas origin; `clampPan`'s pan).
 */
export const MOTION_LIMIT_MS = 450;
export const FORCED_FINISH_MS = 100; // safety: decorations are removed and tweens forced to their final frame at timeline end + 100 ms
export const OLD_FRAME_MS = 250; // T5: the captured previous frame fades out over this span
export const CLOUD_VISIBLE_MIN = 0.25; // a fully visible cloud whose screen diameter is >= 25 % of the viewport's shorter side does not move
export const CLOUD_TARGET = 0.45; // otherwise its diameter becomes 45 % of the shorter side (zoom range permitting), centred
export const PANEL_SAFE = 0.8; // a panel-list paper outside the central 80 % is panned the minimum distance back inside
export const TIER_ORDER = Object.freeze(['strong', 'medium', 'weak']);
export const CLOUD_SOURCES = Object.freeze(['label', 'chip', 'miss', 'related', 'keyboard']);
export const KINDS = Object.freeze(['cloud', 'paper-map', 'paper-panel', 'subitem', 'deselect', 'none']);

/** Choreography (ms from the click; plan §3.4, tentative): [start, duration] per track; `edges` is per tier (strong -> medium -> weak, `TIER_STEP` apart). */
export const TIER_STEP = 60;
export const PANEL_STEP = 30;
const TRACKS = Object.freeze({
  cloud: {outline: [0, 300], edges: [80, 220], bridges: [80, 220], camera: [150, 300], panel: [150, 220]},
  'paper-map': {ripple: [0, 300], edges: [80, 180], panel: [150, 250]},
  'paper-panel': {leave: [0, 150], travel: [80, 220], camera: [80, 220], edges: [150, 180], panel: [150, 250]},
  subitem: {item: [0, 150], dim: [80, 220]},
  deselect: {leave: [0, 150], camera: [80, 220], panel: [150, 250]},
  none: {}
});

export const easeInOutCubic = t => { const x = Math.max(0, Math.min(1, t)); return x < 0.5 ? 4 * x * x * x : 1 - ((-2 * x + 2) ** 3) / 2; };
const sortedIds = values => [...new Set(values)].sort();

/** Leave / stay / enter id sets between two drawn states (ids only, sorted). */
export function transitionSets(previousIds = [], nextIds = []) {
  const before = new Set(previousIds); const after = new Set(nextIds);
  return {leave: sortedIds(previousIds.filter(id => !after.has(id))), stay: sortedIds(nextIds.filter(id => before.has(id))), enter: sortedIds(nextIds.filter(id => !before.has(id)))};
}

/** Transition kind from the committed states ({cloudId, paperId, subItemId}) and the input source. */
export function transitionKind(previous = {}, next = {}, source = 'other') {
  const same = (previous.cloudId ?? null) === (next.cloudId ?? null) && (previous.paperId ?? null) === (next.paperId ?? null) && (previous.subItemId ?? null) === (next.subItemId ?? null) && (previous.pointId ?? null) === (next.pointId ?? null);
  if (same) return 'none';
  if (!next.cloudId && !next.paperId) return previous.cloudId || previous.paperId ? 'deselect' : 'none';
  if (source === 'subitem') return 'subitem';
  if (source === 'point') return next.paperId ? 'paper-map' : 'cloud';
  if (CLOUD_SOURCES.includes(source)) return 'cloud';
  if (next.paperId && next.paperId !== previous.paperId) return 'paper-panel';
  return next.cloudId !== previous.cloudId ? 'cloud' : 'subitem';
}

/**
 * Timeline for one transition.  `reduced` -> every track and the end are 0.  `oldFrame` (T5) adds the 0..OLD_FRAME_MS fade and
 * starts every edge draw-in at or after the image's 50 % opacity point.  `panelItems` sizes the panel stagger so it ends in time.
 * @returns {{kind, reduced, end, tracks: Object<string, {start, duration, step?, count?}>}}
 */
export function buildTimeline(kind, {reduced = false, camera = false, oldFrame = false, panelItems = 0, panel = true} = {}) {
  const spec = TRACKS[kind] || TRACKS.none; const tracks = {};
  const put = (name, start, duration, extra = {}) => { const s = Math.min(start, MOTION_LIMIT_MS); tracks[name] = {start: s, duration: Math.max(0, Math.min(duration, MOTION_LIMIT_MS - s)), ...extra}; };
  if (kind !== 'none' && oldFrame) put('oldFrame', 0, OLD_FRAME_MS);
  const drawFrom = oldFrame ? OLD_FRAME_MS / 2 : 0;
  for (const [name, [start, duration]] of Object.entries(spec)) {
    if (name === 'camera' && !camera) continue;
    if (name === 'panel' && !panel) continue;
    if (name === 'edges') { const first = Math.max(start, drawFrom); const tiers = kind === 'cloud' ? ['strong'] : TIER_ORDER; tiers.forEach((tier, index) => { const s = first + index * TIER_STEP; put(`edges.${tier}`, s, Math.min(duration, MOTION_LIMIT_MS - s)); }); continue; }
    if (name === 'panel') { const count = Math.max(1, Math.floor(panelItems) || 1); const room = MOTION_LIMIT_MS - start - duration; const step = count > 1 ? Math.max(0, Math.min(PANEL_STEP, Math.floor(room / (count - 1)))) : 0; put(name, start, duration, {step, count}); continue; }
    put(name, start, duration);
  }
  if (reduced) for (const track of Object.values(tracks)) { track.start = 0; track.duration = 0; if ('step' in track) track.step = 0; }
  const end = Object.values(tracks).reduce((max, track) => Math.max(max, track.start + track.duration + (track.count > 1 ? (track.count - 1) * track.step : 0)), 0);
  return {kind, reduced: Boolean(reduced), end, tracks};
}

/** Safety deadline of a timeline: its end + FORCED_FINISH_MS. */
export const forcedFinishAt = timeline => (timeline?.end || 0) + FORCED_FINISH_MS;

const clampValue = (value, [lo, hi]) => Math.max(lo, Math.min(hi, value));
const screenCircle = (camera, cloud) => ({x: cloud.cx * camera.zoom + camera.x, y: cloud.cy * camera.zoom + camera.y, r: cloud.r * camera.zoom});

/** Cloud rule: no move when the circle is fully inside the viewport and its screen diameter >= CLOUD_VISIBLE_MIN of the shorter side; else zoom to CLOUD_TARGET (capped) and centre. */
export function cloudCamera({camera, viewport, cloud, zoomRange = [0.2, 3]}) {
  const short = Math.min(viewport.width, viewport.height); const circle = screenCircle(camera, cloud);
  const inside = circle.x - circle.r >= 0 && circle.y - circle.r >= 0 && circle.x + circle.r <= viewport.width && circle.y + circle.r <= viewport.height;
  if (inside && 2 * circle.r >= CLOUD_VISIBLE_MIN * short) return {move: false, reason: 'visible', target: null};
  const wanted = cloud.r > 0 ? (CLOUD_TARGET * short) / (2 * cloud.r) : camera.zoom; const zoom = clampValue(wanted, zoomRange);
  return {move: true, reason: zoom !== wanted ? 'zoom-capped' : 'zoom-centre', target: {zoom, x: viewport.width / 2 - cloud.cx * zoom, y: viewport.height / 2 - cloud.cy * zoom}};
}

/** Panel-list paper: pan only, and only when the point is outside the central PANEL_SAFE box, by the minimum translation that brings it inside. */
export function panCamera({camera, viewport, point}) {
  const margin = (1 - PANEL_SAFE) / 2; const sx = point.x * camera.zoom + camera.x; const sy = point.y * camera.zoom + camera.y;
  const axis = (value, size) => value < size * margin ? size * margin - value : value > size * (1 - margin) ? size * (1 - margin) - value : 0;
  const dx = axis(sx, viewport.width); const dy = axis(sy, viewport.height);
  if (dx === 0 && dy === 0) return {move: false, reason: 'inside', target: null};
  return {move: true, reason: 'pan', target: {zoom: camera.zoom, x: camera.x + dx, y: camera.y + dy}};
}

/** Movement rule by INPUT SOURCE (never by the dispatched events): a map point click never moves, even though it also selects its cloud. */
export function cameraDecision({source, kind, camera, viewport, cloud = null, point = null, zoomRange}) {
  if (!camera || !viewport || !(viewport.width > 0) || !(viewport.height > 0)) return {move: false, reason: 'no-viewport', target: null};
  if (source === 'point') return {move: false, reason: 'point-click', target: null};
  if (kind === 'cloud' && CLOUD_SOURCES.includes(source) && cloud) return cloudCamera({camera, viewport, cloud, zoomRange});
  if (kind === 'paper-panel' && source === 'panel' && point) return panCamera({camera, viewport, point});
  return {move: false, reason: 'source', target: null};
}

/** Camera at eased progress `e`: zoom interpolates geometrically, the model point under the viewport centre linearly (straight path on screen). */
export function interpolateCamera(from, to, e, viewport) {
  if (e >= 1) return {...to}; if (e <= 0) return {...from};
  const cx = viewport.width / 2; const cy = viewport.height / 2;
  const m0 = {x: (cx - from.x) / from.zoom, y: (cy - from.y) / from.zoom}; const m1 = {x: (cx - to.x) / to.zoom, y: (cy - to.y) / to.zoom};
  const zoom = from.zoom * (to.zoom / from.zoom) ** e; const mx = m0.x + (m1.x - m0.x) * e; const my = m0.y + (m1.y - m0.y) * e;
  return {zoom, x: cx - mx * zoom, y: cy - my * zoom};
}

/** One tween frame: progress from the elapsed time, eased + interpolated camera, then the SAME clamp the wrapper uses (every frame satisfies AC-14). */
export function tweenFrame({from, to, elapsed, delay = 0, duration, viewport, clamp = camera => camera}) {
  const t = duration > 0 ? (elapsed - delay) / duration : 1;
  if (t < 0) return {started: false, done: false, t, camera: null};
  const done = t >= 1; return {started: true, done, t: Math.min(1, t), camera: clamp(done ? {...to} : interpolateCamera(from, to, easeInOutCubic(t), viewport))};
}

/** Same camera within `tolerance` screen px (zoom relative 1e-4). */
export const sameCamera = (a, b, tolerance = 0.5) => Boolean(a && b) && Math.abs(a.zoom - b.zoom) <= 1e-4 * Math.max(1, Math.abs(b.zoom)) && Math.abs(a.x - b.x) <= tolerance && Math.abs(a.y - b.y) <= tolerance;
export const createCameraMemory = () => ({home: null, auto: null});
/** Before an auto-move: keep the FIRST pre-move camera while the user has not moved since the last auto-move; otherwise start over from the live camera. */
export function rememberAutoMove(memory, live, target) {
  const untouched = Boolean(memory?.home) && sameCamera(live, memory.auto);
  return {home: untouched ? {...memory.home} : {...live}, auto: {...target}};
}
/** Deselect: the camera to return to (null = stay), and the memory is always cleared by the caller. */
export const returnTarget = (memory, live) => memory?.home && sameCamera(live, memory.auto) ? {...memory.home} : null;
/** What `capture()` may persist: while a tween runs or while the auto-moved camera is untouched, the pre-auto-move camera; after a user gesture, the live one. */
export function capturedCamera(memory, live, tween = null) {
  if (tween) return {...(memory?.home || tween.target || live)};
  return memory?.home && sameCamera(live, memory.auto) ? {...memory.home} : {...live};
}

/**
 * Interruption: a new input jumps the running tween to its final frame and removes decorations at once (no queue);
 * a user gesture on the map stops the tween where it is (the user owns the camera from there, memory dropped);
 * the forced finish and a reset (snapshot replacement, overlay rebuild) jump to the final frame without animation.
 */
export function interruption(cause) {
  if (cause === 'gesture') return {tween: 'stop', decorations: 'remove', dropMemory: true};
  if (cause === 'input' || cause === 'forced' || cause === 'reset') return {tween: 'jump', decorations: 'remove', dropMemory: false};
  throw new Error(`unknown interruption cause: ${cause}`);
}

/** One transition per user input: a second begin while one is pending (same input, e.g. a point click's two dispatches) joins it. */
export const coalesceInput = (pending, source) => pending ? {join: true, source: pending.source} : {join: false, source};

export const AtlasMotion = {MOTION_LIMIT_MS, FORCED_FINISH_MS, OLD_FRAME_MS, CLOUD_VISIBLE_MIN, CLOUD_TARGET, PANEL_SAFE, TIER_ORDER, TIER_STEP, PANEL_STEP, CLOUD_SOURCES, KINDS, easeInOutCubic, transitionSets, transitionKind, buildTimeline, forcedFinishAt, cloudCamera, panCamera, cameraDecision, interpolateCamera, tweenFrame, sameCamera, createCameraMemory, rememberAutoMove, returnTarget, capturedCamera, interruption, coalesceInput};
if (typeof window !== 'undefined') window.AtlasMotion = AtlasMotion;
