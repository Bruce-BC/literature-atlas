import test from 'node:test';
import assert from 'node:assert/strict';
import {createFrameGate, renderG6Network, IDLE_THROTTLE_MS, SLOW_TICK_MS} from '../../frontend/g6-render.js';

const container = () => ({dataset: {}, attributes: {}, addEventListener() {}, removeEventListener() {}, setAttribute(key, value) { this.attributes[key] = value; }});
const nodes = [{data: {id: 'one', label: 'One'}}, {data: {id: 'two', label: 'Two'}}];
class FakeGraph {
  constructor(options) { this.nodes = options.data.nodes; this.calls = []; this.position = [42, 23]; this.zoom = 1.6; }
  on() {}
  render() { return this.pending || Promise.resolve(); }
  getNodeData() { return this.nodes; }
  getElementPosition(id) { return id === 'one' ? [10, 20] : [30, 40]; }
  getViewportByCanvas() { return this.position; }
  getZoom() { return this.zoom; }
  stopLayout() { this.calls.push('stop'); }
  translateElementTo(positions) { this.calls.push(['positions', positions]); return Promise.resolve(); }
  zoomTo(value) { this.calls.push(['zoom', value]); return Promise.resolve(); }
  translateBy(position) { this.calls.push(['pan', position]); return Promise.resolve(); }
  setElementState() { this.calls.push('select'); return Promise.resolve(); }
  destroy() { this.calls.push('destroy'); }
}

test('pending render disposed before resolution has no stale callbacks or rejection', async () => {
  let resolve; class PendingGraph extends FakeGraph { render() { return new Promise(done => { resolve = done; }); } }
  const errors = []; const wrapper = renderG6Network(container(), nodes, [], {GraphClass: PendingGraph, selectedId: 'one', onError: error => errors.push(error)});
  await Promise.resolve(); wrapper.destroy(); resolve(); await wrapper.ready;
  assert.deepEqual(wrapper.graph.calls, ['destroy']); assert.deepEqual(errors, []);
});

test('destroy cancels delayed stop and render failures reach error handler', async t => {
  t.mock.timers.enable({apis: ['setTimeout']});
  const graph = renderG6Network(container(), nodes, [], {GraphClass: FakeGraph}); await graph.ready; graph.destroy(); t.mock.timers.tick(1300); assert.deepEqual(graph.graph.calls, ['destroy']);
  class FailedGraph extends FakeGraph { render() { return Promise.reject(new Error('render failed')); } }
  const errors = []; const failed = renderG6Network(container(), nodes, [], {GraphClass: FailedGraph, onError: error => errors.push(error.message)}); await failed.ready;
  assert.deepEqual(errors, ['render failed']); failed.destroy();
});

test('viewport and surviving node positions restore with bounded zoom', async () => {
  const first = renderG6Network(container(), nodes, [], {GraphClass: FakeGraph}); await first.ready; const saved = first.capture(); first.destroy();
  saved.positions.removed = [1, 2]; saved.zoom = 7;
  const next = renderG6Network(container(), nodes, [], {GraphClass: FakeGraph, viewState: saved}); await next.ready;
  assert.deepEqual(next.graph.calls, ['stop', ['positions', {one: [10, 20], two: [30, 40]}], ['zoom', 3], ['pan', [0, 0]]]); next.destroy();
});

test('readiness reports actual rendering, success, failure and disposal', async () => {
  let resolve; class DelayedGraph extends FakeGraph { render() { return new Promise(done => { resolve = done; }); } }
  const element = container(); const wrapper = renderG6Network(element, nodes, [], {GraphClass: DelayedGraph});
  assert.equal(element.dataset.rendererState, 'rendering'); assert.equal(element.attributes['aria-busy'], 'true');
  await Promise.resolve(); resolve(); await wrapper.ready;
  assert.equal(element.dataset.rendererState, 'ready'); assert.equal(element.attributes['aria-busy'], 'false');
  wrapper.destroy(); assert.equal(element.dataset.rendererState, 'disposed');
  class FailedGraph extends FakeGraph { render() { throw new Error('failure'); } }
  const failedElement = container(); const failed = renderG6Network(failedElement, nodes, [], {GraphClass: FailedGraph, onError: () => {}}); await failed.ready;
  assert.equal(failedElement.dataset.rendererState, 'error'); assert.equal(failedElement.attributes['aria-busy'], 'false'); failed.destroy();
});

test('Cytoscape programmatic and fit zoom respect the shared limits', async () => {
  const {default: cytoscape} = await import('cytoscape');
  const {networkZoomOptions} = await import('../../frontend/network-style.js');
  for (const kind of ['paper', 'concept']) {
    const graph = cytoscape({headless: true, ...networkZoomOptions(), elements: [{data: {id: kind}}]});
    assert.equal(graph.minZoom(), .2); assert.equal(graph.maxZoom(), 3);
    graph.zoom(100); assert.equal(graph.zoom(), 3);
    graph.zoom(.001); assert.equal(graph.zoom(), .2);
    graph.fit(); assert.ok(graph.zoom() >= .2 && graph.zoom() <= 3);
    graph.destroy();
  }
});

test('physical drag does not invoke click selection while a click still selects', async () => {
  const handlers = {}; const element = container();
  element.addEventListener = (name, handler) => { handlers[name] = handler; };
  class HitGraph extends FakeGraph { getClientByCanvas(point) { return point; } }
  const selected = []; const wrapper = renderG6Network(element, nodes, [], {GraphClass: HitGraph, onSelect: id => selected.push(id)}); await wrapper.ready;
  handlers.pointerdown({button: 0, clientX: 10, clientY: 20}); handlers.pointermove({clientX: 40, clientY: 40}); handlers.click({button: 0, clientX: 10, clientY: 20});
  assert.deepEqual(selected, []);
  handlers.pointerdown({button: 0, clientX: 10, clientY: 20}); handlers.click({button: 0, clientX: 10, clientY: 20});
  assert.deepEqual(selected, ['one']); wrapper.destroy();
});

test('palette updates redraw existing graph without rendering or force layout again', async () => {
  class StyleGraph extends FakeGraph {
    render() { this.calls.push('render'); return Promise.resolve(); }
    updateNodeData(data) { this.calls.push(['node-data', data]); }
    updateEdgeData(data) { this.calls.push(['edge-data', data]); }
    setNode(style) { this.calls.push(['node-style', style]); }
    setEdge(style) { this.calls.push(['edge-style', style]); }
    draw() { this.calls.push('draw'); return Promise.resolve(); }
  }
  const wrapper = renderG6Network(container(), nodes, [], {GraphClass: StyleGraph, textColor: '#111'}); await wrapper.ready;
  const colored = nodes.map(node => ({data: {...node.data, categoryColor: '#abcdef'}}));
  wrapper.restyle(colored, [], {textColor: '#eee', panelColor: '#222', borderColor: '#444', focusColor: '#f00', seriesColor: '#aaa'});
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(wrapper.graph.calls.filter(call => call === 'render').length, 1);
  assert.equal(wrapper.graph.calls.filter(call => call === 'draw').length, 1);
  assert.equal(wrapper.graph.calls.find(call => Array.isArray(call) && call[0] === 'node-data')[1][0].data.categoryColor, '#abcdef');
  wrapper.destroy();
});

test('node color data changes trigger redraw even when theme is unchanged', async () => {
  class StyleGraph extends FakeGraph { render(){return Promise.resolve()} updateNodeData(data){this.calls.push(['nodes',data])} updateEdgeData(){} setNode(){} setEdge(){} draw(){this.calls.push('draw');return Promise.resolve()} }
  const style={textColor:'#111',panelColor:'#222',borderColor:'#333',focusColor:'#444',seriesColor:'#555'};
  const wrapper=renderG6Network(container(),nodes,[],{GraphClass:StyleGraph,...style});await wrapper.ready;
  wrapper.restyle(nodes.map(node=>({data:{...node.data,categoryColor:'#abcdef'}})),[],style);await new Promise(resolve=>setTimeout(resolve,0));
  assert.equal(wrapper.graph.calls.find(call=>Array.isArray(call)&&call[0]==='nodes')[1][0].data.categoryColor,'#abcdef');assert.ok(wrapper.graph.calls.includes('draw'));wrapper.destroy();
});

class TopologyGraph extends FakeGraph {
  render() { return Promise.resolve(); }
  setData(data) { this.nodes = data.nodes; this.calls.push(['setData', data.nodes.map(node => node.id), data.nodes.map(node => node.style)]); }
  getClientByCanvas(point) { return point; }
  getEdgeData() { return []; } setNode() {} setEdge() {} layout() { this.calls.push('layout'); return Promise.resolve(); } draw() { this.calls.push('draw'); return Promise.resolve(); }
}
const palette = {textColor: '#111', panelColor: '#222', borderColor: '#333', focusColor: '#444', seriesColor: '#555'};

test('rapid updates with the same desired topology never skip the node add (decided against the APPLIED graph)', async () => {
  const wrapper = renderG6Network(container(), nodes, [], {GraphClass: TopologyGraph, layoutType: 'preset', ...palette}); await wrapper.ready;
  const withX = [...nodes, {data: {id: 'x', label: 'X', x: 5, y: 6}}];
  wrapper.update(withX, [], palette, {revision: 1});
  wrapper.update(withX, [], {...palette, textColor: '#999'}, {revision: 2}); // same topology, newer revision; rev1 is skipped as stale
  await wrapper.whenIdle();
  assert.deepEqual(wrapper.graph.getNodeData().map(node => node.id).sort(), ['one', 'two', 'x']);
  assert.ok(wrapper.graph.calls.some(call => Array.isArray(call) && call[0] === 'setData' && call[1].includes('x')));
  assert.equal(wrapper.stats.droppedStale, 1); assert.equal(wrapper.stats.topologyChanges, 1);
  assert.equal(wrapper.graph.nodes.find(node => node.id === 'x').style.x, 5); // preset coordinates from data, not (0,0)
  assert.equal(wrapper.signature, wrapper.desiredSignature);
  wrapper.destroy();
});

test('a reused wrapper resolves clicks with the CURRENT handler (new nodes after a poll are clickable with current metadata)', async () => {
  const handlers = {}; const element = container(); element.addEventListener = (name, handler) => { handlers[name] = handler; };
  const first = []; const second = [];
  const wrapper = renderG6Network(element, nodes, [], {GraphClass: TopologyGraph, layoutType: 'preset', hitRadius: 12, onSelect: id => first.push(id), ...palette}); await wrapper.ready;
  wrapper.setOnSelect(id => second.push(id));
  handlers.pointerdown({button: 0, clientX: 10, clientY: 20}); handlers.click({button: 0, clientX: 10, clientY: 20});
  assert.deepEqual(first, []); assert.deepEqual(second, ['one']); wrapper.destroy();
});

test('native node click after drag is suppressed and a plain native click selects exactly once', async () => {
  const listeners = {}; const element = container(); element.addEventListener = (name, handler) => { (listeners[name] ||= []).push(handler); };
  let nodeClick; class EventGraph extends TopologyGraph { on(name, handler) { if (String(name).includes('click')) nodeClick = handler; } }
  const selected = []; const wrapper = renderG6Network(element, nodes, [], {GraphClass: EventGraph, onSelect: id => selected.push(id), ...palette}); await wrapper.ready;
  const fire = (name, event) => listeners[name].forEach(handler => handler(event));
  fire('pointerdown', {button: 0, clientX: 10, clientY: 20}); fire('pointermove', {clientX: 60, clientY: 60}); nodeClick({target: {id: 'one'}, targetType: 'node'});
  assert.deepEqual(selected, []); assert.equal(wrapper.stats.suppressedDragClicks, 1);
  fire('pointerdown', {button: 0, clientX: 10, clientY: 20}); nodeClick({target: {id: 'one'}, targetType: 'node'}); fire('click', {button: 0, clientX: 10, clientY: 20});
  assert.deepEqual(selected, ['one']); assert.equal(wrapper.stats.nativeClicks, 1); assert.equal(wrapper.stats.physicalClicks, 0); wrapper.destroy();
});

test('a failed queued update clears pending desired state: identical retry applies, final nodes are right and ready phase recovers', async () => {
  let failures = 1; class FlakyGraph extends TopologyGraph { draw() { if (failures-- > 0) return Promise.reject(new Error('draw failed')); return super.draw(); } }
  const errors = []; const element = container(); const wrapper = renderG6Network(element, nodes, [], {GraphClass: FlakyGraph, layoutType: 'preset', onError: error => errors.push(error.message), ...palette}); await wrapper.ready;
  const withX = [...nodes, {data: {id: 'x', label: 'X', x: 1, y: 2}}];
  await wrapper.update(withX, [], palette, {revision: 1});
  assert.deepEqual(errors, ['draw failed']); assert.equal(element.dataset.rendererState, 'error'); assert.equal(wrapper.stats.failedUpdates, 1);
  assert.equal(wrapper.desiredSignature, wrapper.signature); // after failure the pending desired state is reset to the applied graph (still 2 nodes)
  await wrapper.update(withX, [], palette, {revision: 2}); // IDENTICAL request must not be short-circuited
  assert.deepEqual(wrapper.graph.getNodeData().map(node => node.id).sort(), ['one', 'two', 'x']);
  assert.equal(wrapper.signature, wrapper.desiredSignature); assert.equal(element.dataset.rendererState, 'ready'); assert.equal(errors.length, 1);
  wrapper.destroy();
});

// ---- idle frame gate (F1): G layer canvases tick at ~4/s when idle, any input/update wakes them onto a real frame at once ----
const fakeFrames = () => {
  const clock = {t: 0}; const frames = new Map(); const timers = new Map(); let next = 0;
  return {clock, frames, timers, now: () => clock.t, raf: cb => { next += 1; frames.set(next, cb); return next; }, caf: id => frames.delete(id), setTimer: (cb, ms) => { next += 1; timers.set(next, {cb, ms}); return next; }, clearTimer: id => timers.delete(id),
    runFrame() { const due = [...frames.values()]; frames.clear(); for (const cb of due) cb(clock.t); }, runTimers() { const due = [...timers.values()]; timers.clear(); for (const {cb} of due) cb(); }};
};

test('F1 frame gate: full rate until idle, then all layer requests share ONE slow timer; wake cancels it and schedules a real frame at once', () => {
  const env = fakeFrames(); let busy = false; const stats = {}; const gate = createFrameGate({...env, isBusy: () => busy, stats});
  assert.equal(SLOW_TICK_MS, 250, 'about 4 ticks per second'); assert.ok(IDLE_THROTTLE_MS > 0 && IDLE_THROTTLE_MS <= 2000, 'a short idle delay');
  const ticks = []; const layer = name => { const tick = () => { ticks.push(name); gate.request(tick); }; return tick; };
  for (const name of ['background', 'main', 'label', 'transient']) gate.request(layer(name));
  assert.equal(env.frames.size, 4, 'not idle yet: native frames'); assert.equal(env.timers.size, 0); assert.equal(stats.idleThrottled, false);
  env.clock.t = IDLE_THROTTLE_MS + 1; env.runFrame();
  assert.equal(env.frames.size, 0); assert.equal(env.timers.size, 1, 'four layers -> one slow timer'); assert.equal([...env.timers.values()][0].ms, SLOW_TICK_MS); assert.equal(stats.idleThrottled, true); assert.deepEqual(gate.pending, {native: 0, slow: 4});
  env.runTimers(); assert.equal(stats.slowTicks, 1); assert.equal(ticks.length, 8); assert.equal(env.timers.size, 1, 'still idle: next slow tick');
  gate.wake(); assert.equal(env.timers.size, 0, 'pending slow tick cancelled'); assert.equal(env.frames.size, 4, 'every waiting layer is on a real frame'); assert.equal(stats.wakes, 1); assert.equal(stats.idleThrottled, false); assert.equal(stats.lastWakeMs, null);
  env.clock.t += 16; env.runFrame(); assert.equal(stats.lastWakeMs, 16, 'wake -> first real frame'); assert.equal(env.frames.size, 4, 'just woken: full rate again'); assert.equal(env.timers.size, 0);
  gate.wake(); assert.equal(stats.wakes, 1, 'a wake at full rate is not counted');
  busy = true; env.clock.t += 10 * IDLE_THROTTLE_MS; env.runFrame(); assert.equal(env.timers.size, 0, 'busy (camera/motion/layout/update/animation): never throttled');
  busy = false; env.clock.t += IDLE_THROTTLE_MS - 1; env.runFrame(); assert.equal(env.timers.size, 0, 'idle delay counts from the end of the busy period');
  env.clock.t += 2; env.runFrame(); assert.equal(env.timers.size, 1);
  const id = gate.request(() => {}); gate.cancel(id); assert.equal(gate.pending.slow, 4, 'cancel removes only that request');
  gate.close(); assert.deepEqual(gate.pending, {native: 0, slow: 0}); assert.equal(env.timers.size, 0); assert.equal(env.frames.size, 0, 'closed: nothing left scheduled'); assert.equal(stats.wakes, 1, 'close is not a user wake');
});

test('F1 the wrapper hands its gate to every G layer canvas of its own graph, wakes on input and updates, and closes it on destroy', async () => {
  const listeners = []; const element = {dataset: {}, attributes: {}, addEventListener(type, handler, options) { listeners.push({type, handler, capture: options === true || Boolean(options?.capture)}); }, removeEventListener(type, handler) { const i = listeners.findIndex(row => row.type === type && row.handler === handler); if (i >= 0) listeners.splice(i, 1); }, setAttribute() {}};
  class LayerGraph extends FakeGraph {
    constructor(options) { super(options); this.options = options; this.layers = Object.fromEntries(['background', 'main', 'label', 'transient'].map(name => [name, {requestAnimationFrame: options.canvas.requestAnimationFrame, cancelAnimationFrame: options.canvas.cancelAnimationFrame, document: {timeline: {animations: []}}}])); }
    getCanvas() { return {getLayers: () => this.layers}; }
  }
  const wrapper = renderG6Network(element, nodes, [], {GraphClass: LayerGraph, layoutType: 'preset'}); await wrapper.ready;
  assert.equal(typeof wrapper.graph.options.canvas.requestAnimationFrame, 'function', 'per-instance scheduler passed through the G6 canvas config');
  assert.equal(wrapper.stats.throttleLayers, 4, 'every layer canvas of this graph uses the gate'); assert.equal(wrapper.stats.idleThrottled, false); assert.equal(wrapper.stats.wakes, 0);
  for (const type of ['pointerdown', 'pointermove', 'pointerup', 'wheel', 'keydown']) assert.ok(listeners.some(row => row.type === type && row.capture), `${type} wakes the canvases`);
  const other = renderG6Network({...element, dataset: {}, addEventListener() {}}, nodes, [], {GraphClass: LayerGraph, layoutType: 'preset'}); await other.ready;
  assert.notEqual(other.graph.options.canvas.requestAnimationFrame, wrapper.graph.options.canvas.requestAnimationFrame, 'one gate per graph (overview, paper, concept)');
  other.destroy(); wrapper.destroy();
  assert.equal(listeners.filter(row => ['pointermove', 'pointerup'].includes(row.type)).length, 0, 'wake listeners removed on destroy');
});

test('F2 fitMaxZoom: an initial fit never zooms past the cap; a stored camera is restored as is', async () => {
  const capped = renderG6Network(container(), nodes, [], {GraphClass: FakeGraph, fitMaxZoom: 1.5}); await capped.ready;
  assert.ok(capped.graph.calls.some(call => Array.isArray(call) && call[0] === 'zoom' && call[1] === 1.5), 'fit zoom 1.6 capped to 1.5'); capped.destroy();
  const free = renderG6Network(container(), nodes, [], {GraphClass: FakeGraph}); await free.ready;
  assert.ok(!free.graph.calls.some(call => Array.isArray(call) && call[0] === 'zoom'), 'no cap: G6 fit untouched'); free.destroy();
  const restored = renderG6Network(container(), nodes, [], {GraphClass: FakeGraph, fitMaxZoom: 1.5, viewState: {zoom: 2, position: [0, 0], positions: {}}}); await restored.ready;
  assert.ok(!restored.graph.calls.some(call => Array.isArray(call) && call[0] === 'zoom' && call[1] === 1.5), 'a restored camera is not a fit'); restored.destroy();
});
