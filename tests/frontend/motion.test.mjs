import test from 'node:test';
import assert from 'node:assert/strict';
import {MOTION_LIMIT_MS, FORCED_FINISH_MS, OLD_FRAME_MS, KINDS, TIER_ORDER, buildTimeline, transitionSets, transitionKind, forcedFinishAt, cloudCamera, panCamera, cameraDecision, interpolateCamera, tweenFrame, sameCamera, createCameraMemory, rememberAutoMove, returnTarget, capturedCamera, interruption, coalesceInput, easeInOutCubic} from '../../frontend/motion.js';
import {renderG6Network} from '../../frontend/g6-render.js';
import {cytoscapeStyle, CY_TRANSITION_S} from '../../frontend/network-views.js';

const trackEnd = track => track.start + track.duration + (track.count > 1 ? (track.count - 1) * track.step : 0);
const VIEW = {width: 1000, height: 600};

test('ME-10: every generated timeline of every kind ends <= 450 ms (all option combinations), the end is its latest track', () => {
  let checked = 0;
  for (const kind of KINDS) for (const reduced of [false, true]) for (const camera of [false, true]) for (const oldFrame of [false, true]) for (const panel of [false, true]) for (const panelItems of [0, 1, 2, 6, 9, 40, 500]) {
    const timeline = buildTimeline(kind, {reduced, camera, oldFrame, panel, panelItems}); checked += 1;
    assert.ok(timeline.end <= MOTION_LIMIT_MS, `${kind} ends at ${timeline.end}`);
    for (const [name, track] of Object.entries(timeline.tracks)) { assert.ok(track.start >= 0 && track.duration >= 0 && trackEnd(track) <= MOTION_LIMIT_MS, `${kind}.${name} ${JSON.stringify(track)}`); }
    assert.equal(timeline.end, Math.max(0, ...Object.values(timeline.tracks).map(trackEnd)));
    if (!reduced && kind !== 'none') assert.ok(timeline.end > 0, `${kind} is a real transition`);
  }
  assert.ok(checked > 500);
});

test('ME-13: reduced motion yields zero-duration timelines but keeps the camera track (the move still happens, instantly)', () => {
  for (const kind of KINDS) {
    const timeline = buildTimeline(kind, {reduced: true, camera: true, oldFrame: true, panelItems: 6});
    assert.equal(timeline.end, 0); assert.ok(Object.values(timeline.tracks).every(track => track.start === 0 && track.duration === 0 && (track.step ?? 0) === 0));
    if (['cloud', 'paper-panel', 'deselect'].includes(kind)) assert.deepEqual(timeline.tracks.camera, {start: 0, duration: 0});
  }
  const from = {zoom: 1, x: 0, y: 0}; const to = {zoom: 2, x: -300, y: -100};
  assert.deepEqual(tweenFrame({from, to, elapsed: 0, duration: 0, viewport: VIEW}).camera, to, 'a zero-duration tween is its final camera at once');
});

test('T5 and choreography order: the old frame fades 0..250 ms, edges start drawing at <= 50 % image opacity, tiers strong -> medium -> weak', () => {
  const paper = buildTimeline('paper-map', {oldFrame: true});
  assert.deepEqual(paper.tracks.oldFrame, {start: 0, duration: OLD_FRAME_MS});
  const starts = TIER_ORDER.map(tier => paper.tracks[`edges.${tier}`].start);
  assert.ok(starts[0] >= OLD_FRAME_MS / 2 && starts[0] < starts[1] && starts[1] < starts[2]);
  const cloud = buildTimeline('cloud', {oldFrame: false, camera: true});
  assert.ok(cloud.tracks['edges.strong'] && !cloud.tracks['edges.medium'] && !cloud.tracks.oldFrame, 'a cloud draws its strong intra lines only');
  assert.ok(!buildTimeline('cloud', {camera: false}).tracks.camera && !buildTimeline('paper-map', {camera: true}).tracks.camera, 'no camera track without a move; a map point click has none at all');
  assert.ok(!buildTimeline('paper-map', {panel: false}).tracks.panel);
  assert.deepEqual(buildTimeline('none', {oldFrame: true, camera: true}), {kind: 'none', reduced: false, end: 0, tracks: {}});
});

test('leave / stay / enter sets', () => {
  assert.deepEqual(transitionSets(['a', 'b', 'c'], ['c', 'd', 'b']), {leave: ['a'], stay: ['b', 'c'], enter: ['d']});
  assert.deepEqual(transitionSets([], ['x']), {leave: [], stay: [], enter: ['x']});
  assert.deepEqual(transitionSets(['x'], []), {leave: ['x'], stay: [], enter: []});
});

test('transition kind from committed states and the input source', () => {
  const none = {}; const cloudA = {cloudId: 'A'}; const paper = {cloudId: 'A', paperId: 'p', pointId: 'pt'};
  assert.equal(transitionKind(none, cloudA, 'label'), 'cloud');
  assert.equal(transitionKind(none, cloudA, 'miss'), 'cloud');
  assert.equal(transitionKind(cloudA, {cloudId: 'B'}, 'related'), 'cloud');
  assert.equal(transitionKind(cloudA, paper, 'point'), 'paper-map');
  assert.equal(transitionKind(paper, {cloudId: 'A', paperId: 'q'}, 'panel'), 'paper-panel');
  assert.equal(transitionKind(paper, {...paper, subItemId: 's'}, 'subitem'), 'subitem');
  assert.equal(transitionKind(paper, {}, 'close'), 'deselect');
  assert.equal(transitionKind(cloudA, {}, 'miss'), 'deselect');
  assert.equal(transitionKind(cloudA, cloudA, 'label'), 'none');
  assert.equal(transitionKind(none, none, 'miss'), 'none');
});

test('ME-15 cloud rule: no move when fully inside and diameter >= 25 % of the shorter side, else 45 % (zoom-capped) and centred', () => {
  const camera = {zoom: 1, x: 0, y: 0}; const short = 600;
  assert.equal(cloudCamera({camera, viewport: VIEW, cloud: {cx: 500, cy: 300, r: 0.25 * short / 2}}).move, false, 'exactly 25 %');
  const small = cloudCamera({camera, viewport: VIEW, cloud: {cx: 500, cy: 300, r: 0.249 * short / 2}});
  assert.equal(small.move, true);
  assert.ok(Math.abs(2 * 0.249 * short / 2 * small.target.zoom - 0.45 * short) < 1e-9, 'diameter becomes 45 % of the shorter side');
  assert.ok(Math.abs(500 * small.target.zoom + small.target.x - VIEW.width / 2) < 1e-9 && Math.abs(300 * small.target.zoom + small.target.y - VIEW.height / 2) < 1e-9, 'centred');
  const clipped = cloudCamera({camera, viewport: VIEW, cloud: {cx: 980, cy: 300, r: 200}});
  assert.equal(clipped.move, true, 'a big cloud partly outside the viewport moves'); assert.equal(clipped.reason, 'zoom-centre');
  const tiny = cloudCamera({camera, viewport: VIEW, cloud: {cx: 100, cy: 100, r: 2}, zoomRange: [0.2, 3]});
  assert.equal(tiny.target.zoom, 3); assert.equal(tiny.reason, 'zoom-capped');
  const huge = cloudCamera({camera: {zoom: 1, x: 2000, y: 0}, viewport: VIEW, cloud: {cx: 0, cy: 0, r: 5000}, zoomRange: [0.2, 3]});
  assert.equal(huge.target.zoom, 0.2, 'the lower zoom limit caps too');
});

test('ME-15 panel-list rule: pan only, only outside the central 80 %, by the minimum translation', () => {
  const camera = {zoom: 2, x: 10, y: 20};
  assert.equal(panCamera({camera, viewport: VIEW, point: {x: 200, y: 100}}).move, false, 'screen (410, 220) is inside');
  const out = panCamera({camera, viewport: VIEW, point: {x: 480, y: 290}}); // screen (970, 600)
  assert.equal(out.move, true); assert.equal(out.target.zoom, 2, 'no zoom change');
  const sx = 480 * 2 + out.target.x; const sy = 290 * 2 + out.target.y;
  assert.ok(Math.abs(sx - 900) < 1e-9 && Math.abs(sy - 540) < 1e-9, 'lands exactly on the 80 % boundary (minimum translation)');
  const left = panCamera({camera, viewport: VIEW, point: {x: -10, y: 100}});
  assert.ok(Math.abs(-10 * 2 + left.target.x - 100) < 1e-9 && left.target.y === camera.y, 'one axis only when only one is outside');
});

test('ME-15 movement rule is decided by the INPUT SOURCE: a map point click never moves, even when it selects a small cloud', () => {
  const camera = {zoom: 1, x: 0, y: 0}; const cloud = {cx: 50, cy: 50, r: 5}; const point = {x: 990, y: 590};
  assert.equal(cameraDecision({source: 'point', kind: 'cloud', camera, viewport: VIEW, cloud, point}).move, false);
  assert.equal(cameraDecision({source: 'point', kind: 'paper-map', camera, viewport: VIEW, cloud, point}).move, false);
  for (const source of ['label', 'chip', 'miss', 'related', 'keyboard']) assert.equal(cameraDecision({source, kind: 'cloud', camera, viewport: VIEW, cloud}).move, true, source);
  assert.equal(cameraDecision({source: 'panel', kind: 'paper-panel', camera, viewport: VIEW, cloud, point}).move, true);
  assert.equal(cameraDecision({source: 'subitem', kind: 'subitem', camera, viewport: VIEW, cloud, point}).move, false);
  assert.equal(cameraDecision({source: 'label', kind: 'cloud', camera, viewport: {width: 0, height: 0}, cloud}).move, false, 'no viewport, no move');
});

test('tween maths: eased, exact endpoints, geometric zoom, and EVERY frame passes the clamp', () => {
  assert.equal(easeInOutCubic(0), 0); assert.equal(easeInOutCubic(1), 1); assert.ok(Math.abs(easeInOutCubic(0.5) - 0.5) < 1e-12);
  const from = {zoom: 1, x: 0, y: 0}; const to = {zoom: 4, x: -900, y: -300};
  assert.deepEqual(interpolateCamera(from, to, 0, VIEW), from); assert.deepEqual(interpolateCamera(from, to, 1, VIEW), to);
  assert.ok(Math.abs(interpolateCamera(from, to, 0.5, VIEW).zoom - 2) < 1e-12, 'zoom interpolates geometrically');
  const clamp = camera => ({...camera, x: Math.max(-500, camera.x)}); const frames = [];
  for (let elapsed = 0; elapsed <= 500; elapsed += 16) frames.push(tweenFrame({from, to, elapsed, delay: 100, duration: 300, viewport: VIEW, clamp}));
  assert.ok(frames.filter(frame => !frame.started).every(frame => frame.camera === null), 'nothing is applied before the delay');
  const applied = frames.filter(frame => frame.started); assert.ok(applied.length >= 2);
  assert.ok(applied.every(frame => frame.camera.x >= -500 && sameCamera(frame.camera, clamp(frame.camera), 1e-9)), 'per-frame clamp');
  assert.ok(applied.at(-1).done && sameCamera(applied.at(-1).camera, clamp(to), 1e-9), 'the last frame is the clamped target');
});

test('return memory: first pre-move camera kept across cloud -> cloud, dropped after a user move; capture() never persists an auto-moved camera', () => {
  const fit = {zoom: 1, x: 0, y: 0}; const a = {zoom: 2, x: -100, y: -50}; const b = {zoom: 2.5, x: -400, y: -80}; const user = {zoom: 2.5, x: -300, y: -80};
  let memory = rememberAutoMove(createCameraMemory(), fit, a);
  assert.deepEqual(memory, {home: fit, auto: a});
  memory = rememberAutoMove(memory, a, b); assert.deepEqual(memory.home, fit, 'cloud -> cloud keeps the FIRST pre-move camera');
  assert.deepEqual(returnTarget(memory, b), fit, 'untouched -> return');
  assert.equal(returnTarget(memory, user), null, 'the user moved -> no return');
  assert.deepEqual(rememberAutoMove(memory, user, a).home, user, 'after a user move the next auto-move starts a new memory');
  assert.deepEqual(capturedCamera(memory, b, null), fit, 'untouched auto-moved camera -> the pre-move camera is captured');
  assert.deepEqual(capturedCamera(memory, user, null), user, 'after a user gesture -> the live camera');
  assert.deepEqual(capturedCamera(memory, {zoom: 1.7, x: -60, y: -20}, {target: b}), fit, 'during a tween -> the pre-move camera');
  assert.deepEqual(capturedCamera(createCameraMemory(), {zoom: 1.7, x: -60, y: -20}, {target: fit}), fit, 'during a return tween -> its target (the pre-move camera)');
});

test('ME-12/ME-14 interruption, forced finish and one-input-one-transition coalescing', () => {
  assert.deepEqual(interruption('input'), {tween: 'jump', decorations: 'remove', dropMemory: false});
  assert.deepEqual(interruption('gesture'), {tween: 'stop', decorations: 'remove', dropMemory: true});
  assert.deepEqual(interruption('reset'), {tween: 'jump', decorations: 'remove', dropMemory: false}, 'snapshot replacement: no animation, final frame');
  assert.deepEqual(interruption('forced'), {tween: 'jump', decorations: 'remove', dropMemory: false});
  assert.throws(() => interruption('later'));
  for (const kind of KINDS) { const timeline = buildTimeline(kind, {camera: true, oldFrame: true}); assert.equal(forcedFinishAt(timeline), timeline.end + FORCED_FINISH_MS); assert.ok(forcedFinishAt(timeline) <= MOTION_LIMIT_MS + FORCED_FINISH_MS); }
  assert.deepEqual(coalesceInput(null, 'point'), {join: false, source: 'point'});
  assert.deepEqual(coalesceInput({source: 'point'}, 'other'), {join: true, source: 'point'}, 'the paper dispatch of a point click joins its cloud dispatch');
});

// ---- the G6 wrapper: one self-owned tween, per-frame clamp, gesture stop, capture() guard (FakeGraph with real camera maths) ----
const nodes = [{data: {id: 'a', x: 0, y: 0}}, {data: {id: 'b', x: 400, y: 300}}];
class CameraGraph {
  constructor(options) { this.nodes = options.data.nodes; this.zoom = 1; this.origin = [0, 0]; this.transforms = 0; }
  on() {} render() { return Promise.resolve(); } destroy() {}
  getNodeData() { return this.nodes; } getElementPosition(id) { const n = this.nodes.find(row => row.id === id); return [n.style.x, n.style.y]; }
  getSize() { return [800, 600]; } getZoom() { return this.zoom; } getViewportByCanvas() { return [...this.origin]; }
  zoomTo(zoom, animation, origin = [0, 0]) { const c = [(origin[0] - this.origin[0]) / this.zoom, (origin[1] - this.origin[1]) / this.zoom]; this.zoom = zoom; this.origin = [origin[0] - c[0] * zoom, origin[1] - c[1] * zoom]; this.transforms += 1; return Promise.resolve(); }
  translateBy(delta) { this.origin = [this.origin[0] + delta[0], this.origin[1] + delta[1]]; this.transforms += 1; return Promise.resolve(); }
  setElementState() { return Promise.resolve(); } stopLayout() {}
}
const fakeContainer = () => { const listeners = []; return {listeners, dataset: {}, clientWidth: 800, clientHeight: 600, addEventListener(type, handler, options) { listeners.push({type, handler, capture: options === true || Boolean(options?.capture)}); }, removeEventListener() {}, setAttribute() {}}; };
const waitFor = async (condition, ms = 3000) => { const end = Date.now() + ms; while (!condition()) { if (Date.now() > end) throw new Error('timeout'); await new Promise(resolve => setTimeout(resolve, 5)); } };

test('ME-15 capture() guard: during a tween and while the auto-moved camera is untouched capture() returns the pre-move camera; after a user move, the live one', async () => {
  const container = fakeContainer(); const g6 = renderG6Network(container, nodes, [], {GraphClass: CameraGraph, layoutType: 'preset'}); await g6.ready;
  const before = g6.camera(); const target = g6.clampCamera({zoom: 2, x: -150, y: -100});
  const done = g6.moveCamera(target, {duration: 120});
  await waitFor(() => g6.stats.tweenFrames >= 1);
  assert.equal(g6.tweening, 1); assert.equal(container.dataset.cameraState, 'animating', 'one animated span for the whole tween');
  let captured = g6.capture(); assert.ok(sameCamera({zoom: captured.zoom, x: captured.position[0], y: captured.position[1]}, before), 'mid-tween: pre-move camera');
  assert.equal(await done, 'done'); assert.ok(g6.stats.tweenFrames >= 2, 'more than one frame'); assert.ok(sameCamera(g6.camera(), target, 1e-6), 'reaches the clamped target');
  captured = g6.capture(); assert.ok(sameCamera({zoom: captured.zoom, x: captured.position[0], y: captured.position[1]}, before), 'untouched auto-moved camera is not persisted');
  g6.graph.translateBy([30, 0]); // a user pan
  captured = g6.capture(); assert.ok(sameCamera({zoom: captured.zoom, x: captured.position[0], y: captured.position[1]}, g6.camera()), 'after a user move: the live camera');
  assert.equal(g6.returnCamera({duration: 0}), null, 'moved by the user -> no return on deselect');
  g6.destroy();
});

test('ME-12 a capture-phase pointerdown stops the tween where it is and closes the animated span synchronously; every frame is clamped', async () => {
  const container = fakeContainer(); const g6 = renderG6Network(container, nodes, [], {GraphClass: CameraGraph, layoutType: 'preset'}); await g6.ready;
  const seen = []; const target = {zoom: 3, x: -5000, y: -5000}; // far outside: every frame must be clamped
  const done = g6.moveCamera(target, {duration: 400, onFrame: camera => seen.push(camera)});
  await waitFor(() => seen.length >= 3);
  assert.ok(seen.every(camera => sameCamera(camera, g6.clampCamera(camera), 1e-6)), 'each applied frame satisfies the pan clamp');
  const gesture = container.listeners.find(row => row.type === 'pointerdown' && row.capture); assert.ok(gesture, 'capture-phase listener on the map container');
  const frames = g6.stats.tweenFrames; gesture.handler({button: 0});
  assert.equal(g6.tweening, 0, 'stopped synchronously'); assert.notEqual(container.dataset.cameraState, 'animating', 'span closed at once');
  assert.equal(await done, 'stop'); assert.equal(g6.stats.tweensStopped, 1);
  await new Promise(resolve => setTimeout(resolve, 60)); assert.equal(g6.stats.tweenFrames, frames, 'no frame after the stop');
  assert.deepEqual(g6.cameraMemory, {home: null, auto: null}, 'the user owns the camera: memory dropped');
  g6.destroy();
});

test('ME-12 forced finish: when frames stop (hidden tab) the final frame is applied at timeline end + 100 ms', async t => {
  const realRaf = globalThis.requestAnimationFrame; globalThis.requestAnimationFrame = () => 0; globalThis.cancelAnimationFrame = () => {}; // frames never come
  t.after(() => { if (realRaf) globalThis.requestAnimationFrame = realRaf; else delete globalThis.requestAnimationFrame; delete globalThis.cancelAnimationFrame; });
  const container = fakeContainer(); const g6 = renderG6Network(container, nodes, [], {GraphClass: CameraGraph, layoutType: 'preset'}); await g6.ready;
  const target = g6.clampCamera({zoom: 1.5, x: -50, y: -20}); const started = Date.now();
  assert.equal(await g6.moveCamera(target, {delay: 10, duration: 40}), 'forced');
  assert.ok(Date.now() - started >= 140, 'after end + 100 ms'); assert.ok(sameCamera(g6.camera(), target, 1e-6)); assert.equal(g6.stats.tweensForced, 1);
  g6.destroy();
});

test('ME-13 reduced motion still moves the camera, instantly and to the same final camera; Cytoscape transition 150 ms -> 0', async () => {
  const run = async duration => { const g6 = renderG6Network(fakeContainer(), nodes, [], {GraphClass: CameraGraph, layoutType: 'preset'}); await g6.ready; await g6.moveCamera({zoom: 2.2, x: -333, y: -111}, {duration}); const camera = g6.camera(); const frames = g6.stats.tweenFrames; g6.destroy(); return {camera, frames}; };
  const motion = await run(60); const reduced = await run(0);
  assert.equal(reduced.frames, 0, 'no tween frames'); assert.ok(motion.frames >= 2);
  assert.ok(sameCamera(motion.camera, reduced.camera, 1e-6), 'same final camera');
  const node = style => style.find(row => row.selector === 'node').style['transition-duration'];
  assert.equal(node(cytoscapeStyle({text: '#000', panel: '#fff', border: '#ccc', focus: '#00f'}, {reduced: false})), CY_TRANSITION_S); assert.equal(CY_TRANSITION_S, 0.15);
  assert.equal(node(cytoscapeStyle({text: '#000', panel: '#fff', border: '#ccc', focus: '#00f'}, {reduced: true})), 0);
});
