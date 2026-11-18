import test from 'node:test';
import assert from 'node:assert/strict';
import {renderG6Network} from '../../frontend/g6-render.js';

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

test('view states keep filtered positions, prune deleted IDs, and reset on source epoch change', async () => {
  const {updateG6Views} = await import('../../frontend/g6-view-state.js');
  const snapshot = {source: {serverId: 'library', epoch: 'a'}, papers: [{id: 'one'}, {id: 'two'}], concepts: [{id: 'c'}], fields: [{id: 'field'}]};
  const initial = updateG6Views(null, {}, snapshot);
  const view = {zoom: 1.5, position: [5, 7], positions: {one: [1, 1], two: [2, 2], deleted: [9, 9]}};
  const saved = updateG6Views(initial.source, {}, snapshot, {paper: view});
  const filtered = updateG6Views(saved.source, saved.views, snapshot, {paper: {zoom: 2, position: [6, 8], positions: {one: [3, 3]}}});
  assert.deepEqual(filtered.views.paper.positions, {one: [3, 3], two: [2, 2]});
  assert.equal(filtered.views.paper.zoom, 2);
  const changed = updateG6Views(filtered.source, filtered.views, {...snapshot, source: {...snapshot.source, epoch: 'b'}}, {paper: view});
  assert.deepEqual(changed.views, {});
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
