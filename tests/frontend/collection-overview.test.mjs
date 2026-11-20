import test from 'node:test';
import assert from 'node:assert/strict';
import {allPaperOverviewProjection, countInViewport, COLLECTIONS_ID} from '../../frontend/all-paper-overview.js';
import {layoutCollectionClouds, placeCloudLabels, clearCollectionCloudCache, UNFILED_CLOUD_ID} from '../../frontend/collection-clouds.js';
import {anchorLabels, labelBox, hiddenAfterZoom} from '../../frontend/cloud-overlay.js';
import {renderG6Network} from '../../frontend/g6-render.js';
import {edgesForState} from '../../frontend/edge-strength.js';

// Synthetic: 6 papers, 2 collections (p:b in both), p:e and p:f uncollected.
const snapshot = () => ({
  schemaVersion: 1, attachments: [], status: {}, source: {serverId: 'server', epoch: 'epoch'}, modelStatus: {}, concepts: [], memberships: [], overview: {fields: [], ribbons: []},
  fields: [{id: 'f:x', parentIds: []}],
  papers: ['a', 'b', 'c', 'd', 'e', 'f'].map(key => ({id: `p:${key}`, title: `Paper ${key}`, fieldIds: key === 'f' ? [] : ['f:x'], displayFieldId: key === 'f' ? null : 'f:x'})),
  collections: [{id: 'col:1', label: 'Synthetic one', paperIds: ['p:a', 'p:b', 'p:c']}, {id: 'col:2', label: 'Synthetic two', paperIds: ['p:b', 'p:d']}],
  edges: [{id: 'e:1', source: 'p:a', target: 'p:b', type: 'semantic'}, {id: 'e:2', source: 'p:c', target: 'p:e', type: 'semantic'}, {id: 'e:3', source: 'p:a', target: 'p:d', type: 'citation'}]
});

test('category grouping stays the default: one point per active paper', () => {
  const projection = allPaperOverviewProjection(snapshot(), {relation: 'semantic'});
  assert.equal(projection.grouping, undefined); assert.equal(projection.nodes.length, 6);
  assert.ok(projection.nodes.every(node => node.data.id.startsWith('overview-paper:')));
  assert.deepEqual(projection.counters, {active: 6, represented: 6, viewport: 6, eligibleEdges: 2, displayedEdges: 2});
});

test('collection grouping: replica points per collection, one unfiled cloud, Map resolution and truthful unique counters', () => {
  clearCollectionCloudCache();
  const projection = allPaperOverviewProjection(snapshot(), {grouping: 'collection', relation: 'semantic'});
  assert.equal(projection.id, COLLECTIONS_ID); assert.equal(projection.grouping, 'collection');
  const ids = projection.nodes.map(node => node.data.id).sort();
  assert.deepEqual(ids, ['overview-point:col:1:p:a', 'overview-point:col:1:p:b', 'overview-point:col:1:p:c', 'overview-point:col:2:p:b', 'overview-point:col:2:p:d', 'overview-point:unfiled:p:e', 'overview-point:unfiled:p:f']);
  assert.deepEqual(projection.clouds.map(cloud => cloud.id).sort(), [UNFILED_CLOUD_ID, 'col:1', 'col:2']);
  assert.equal(projection.clouds.filter(cloud => cloud.unfiled).length, 1);
  assert.deepEqual(projection.nodeIdsByPaper.get('p:b').sort(), ['overview-point:col:1:p:b', 'overview-point:col:2:p:b']);
  for (const node of projection.nodes) assert.equal(projection.paperIdByNode.get(node.data.id), node.data.paperId);
  assert.deepEqual(projection.representedIds, ['p:a', 'p:b', 'p:c', 'p:d', 'p:e', 'p:f']);
  // Contract (motion-and-edges M2a): idle draws no paper lines and only the engine's bridges (none here: no cloud pair reaches weight 3).
  assert.deepEqual(projection.counters, {active: 6, represented: 6, points: 7, clouds: 3, viewport: 6, eligibleEdges: 2, displayedEdges: 0, displayedBridges: 0});
  assert.deepEqual(projection.counters.displayedBridges, edgesForState(snapshot(), projection.cloudLayout, {}).counters.displayedBridges); assert.deepEqual(projection.edgeView.edges, []);
  assert.deepEqual(projection.edges, []); // graph data = paper points only
  assert.ok(projection.nodes.every(node => Number.isFinite(node.data.x) && Number.isFinite(node.data.y) && node.data.kind === 'paper'));
});

test('collection grouping is deterministic and input-order independent; viewport counts unique papers', () => {
  clearCollectionCloudCache();
  const a = allPaperOverviewProjection(snapshot(), {grouping: 'collection', relation: 'semantic'});
  const shuffled = snapshot(); shuffled.papers.reverse(); shuffled.collections.reverse(); shuffled.edges.reverse(); clearCollectionCloudCache();
  const b = allPaperOverviewProjection(shuffled, {grouping: 'collection', relation: 'semantic'});
  const coords = projection => Object.fromEntries(projection.points.map(point => [point.id, [point.deterministicX.toFixed(2), point.deterministicY.toFixed(2)]]));
  assert.deepEqual(coords(a), coords(b));
  const everywhere = {minX: -1e6, maxX: 1e6, minY: -1e6, maxY: 1e6};
  assert.equal(countInViewport(a.points, everywhere), 6); // 7 points, 6 unique papers
  assert.equal(allPaperOverviewProjection(snapshot(), {grouping: 'collection', relation: 'semantic', viewport: everywhere}).counters.viewport, 6);
});

test('field focus and a selected cloud only dim; no point is removed', () => {
  const s = snapshot(); s.papers.find(paper => paper.id === 'p:a').fieldIds = []; s.papers.find(paper => paper.id === 'p:a').displayFieldId = null;
  const focused = allPaperOverviewProjection(s, {grouping: 'collection', relation: 'semantic', focusScope: {paperIds: ['p:b']}});
  assert.equal(focused.nodes.length, 7); assert.deepEqual(focused.points.filter(point => !point.dimmed).map(point => point.id).sort(), ['overview-point:col:1:p:b', 'overview-point:col:2:p:b']);
  const cloud = allPaperOverviewProjection(snapshot(), {grouping: 'collection', relation: 'semantic', selectedCloudId: 'col:2'});
  assert.equal(cloud.selectedCloudId, 'col:2'); assert.equal(cloud.nodes.length, 7);
  assert.deepEqual(cloud.points.filter(point => !point.dimmed).map(point => point.cloudId), ['col:2', 'col:2']);
  assert.equal(allPaperOverviewProjection(snapshot(), {grouping: 'collection', selectedCloudId: 'col:missing'}).selectedCloudId, null);
});

test('placeCloudLabels with an explicit camera equals the centred placement shifted by the same origin', () => {
  const layout = layoutCollectionClouds(snapshot(), {useMemo: false}); const zoom = 1.5; const fitArea = {width: 600, height: 400, padding: 0};
  const centred = placeCloudLabels(layout, zoom, fitArea);
  const xs = layout.clouds.flatMap(cloud => [cloud.cx - cloud.r, cloud.cx + cloud.r]); const ys = layout.clouds.flatMap(cloud => [cloud.cy - cloud.r, cloud.cy + cloud.r]);
  const camera = {x: 300 - ((Math.min(...xs) + Math.max(...xs)) / 2) * zoom, y: 200 - ((Math.min(...ys) + Math.max(...ys)) / 2) * zoom};
  const explicit = placeCloudLabels(layout, zoom, fitArea, undefined, camera);
  assert.deepEqual(explicit.map(label => [label.cloudId, label.anchor, label.x.toFixed(6), label.y.toFixed(6)]), centred.map(label => [label.cloudId, label.anchor, label.x.toFixed(6), label.y.toFixed(6)]));
});

test('overlay anchors: labels follow their cloud under pan/zoom, pan never changes visibility, zoom-out hides overlaps', () => {
  const clouds = [{id: 'big', cx: 0, cy: 0, r: 50, size: 10}, {id: 'small', cx: 120, cy: 0, r: 20, size: 2}];
  const labels = [{cloudId: 'big', x: 40, y: 20, width: 80, height: 16, anchor: 'top', leader: false, hidden: false}, {cloudId: 'small', x: 180, y: 52, width: 60, height: 16, anchor: 'top', leader: false, hidden: false}];
  const camera = {zoom: 1, x: 80, y: 90}; const entries = anchorLabels(labels, clouds, camera);
  assert.deepEqual(labelBox(entries[0], camera), {x: 41, y: 20, width: 78, height: 16}); // drawn box keeps 1 px free on each side
  const panned = {zoom: 1, x: 130, y: 60}; assert.deepEqual(labelBox(entries[0], panned), {x: 91, y: -10, width: 78, height: 16});
  const zoomed = {zoom: 2, x: 80, y: 90}; const box = labelBox(entries[0], zoomed); // anchor = top of the circle: scales with the circle, offset stays
  assert.equal(box.y + box.height + 4, 90 - 50 * 2);
  assert.equal(hiddenAfterZoom(entries, camera).size, 0); assert.equal(hiddenAfterZoom(entries, panned).size, 0);
  assert.deepEqual([...hiddenAfterZoom(entries, {zoom: 0.2, x: 80, y: 90})], ['small']); // overlap at zoom-out hides the later (smaller) label
  const withHidden = anchorLabels([labels[0], {...labels[1], hidden: true, anchor: 'none'}], clouds, camera);
  assert.deepEqual([...hiddenAfterZoom(withHidden, camera)], ['small']);
  const leader = anchorLabels([{cloudId: 'small', x: 300, y: 10, width: 60, height: 16, anchor: 'leader', leader: true, hidden: false, leaderFrom: {x: 220, y: 90}}], clouds, camera)[0];
  assert.deepEqual([leader.fromX, leader.fromY], [140, 0]); assert.deepEqual(labelBox(leader, camera), {x: 301, y: 10, width: 58, height: 16});
});

// ---- G6 wrapper: id-array selection, preset coordinate updates, overlay lifecycle and miss clicks -------------------
const container = () => { const listeners = {}; return {listeners, dataset: {}, attributes: {}, addEventListener(name, handler) { (listeners[name] ||= []).push(handler); }, removeEventListener() {}, setAttribute(key, value) { this.attributes[key] = value; }}; };
class FakeGraph {
  constructor(options) { this.nodes = options.data.nodes; this.calls = []; this.handlers = {}; }
  on(name, handler) { (this.handlers[name] ||= []).push(handler); }
  emit(name) { for (const handler of this.handlers[name] || []) handler({}); }
  render() { return Promise.resolve(); } draw() { return Promise.resolve(); } setNode() {} setEdge() {} stopLayout() {} destroy() { this.calls.push('destroy'); }
  getNodeData() { return this.nodes; } getEdgeData() { return []; } getZoom() { return 1; } getSize() { return [400, 300]; }
  getElementPosition(id) { const node = this.nodes.find(row => row.id === id); return [node.style?.x ?? 0, node.style?.y ?? 0]; }
  getViewportByCanvas(point) { return point; } getClientByCanvas(point) { return point; } getCanvasByClient(point) { return point; }
  setData(data) { this.nodes = data.nodes; } updateNodeData(rows) { this.calls.push(['updateNodeData', rows]); for (const row of rows) { const node = this.nodes.find(item => item.id === row.id); if (row.style) node.style = {...node.style, ...row.style}; } } updateEdgeData() {}
  setElementState(states) { this.calls.push(['state', states]); return Promise.resolve(); } translateBy() { return Promise.resolve(); } setSize() { return Promise.resolve(); } fitView() { return Promise.resolve(); }
}
const points = [{data: {id: 'pt:1:a', paperId: 'a', x: 0, y: 0}}, {data: {id: 'pt:2:a', paperId: 'a', x: 50, y: 0}}, {data: {id: 'pt:1:b', paperId: 'b', x: 0, y: 50}}];

test('select accepts an id list: every replica point is selected and the previous selection is cleared', async () => {
  const wrapper = renderG6Network(container(), points, [], {GraphClass: FakeGraph, layoutType: 'preset', selectedId: ['pt:1:b']}); await wrapper.ready;
  assert.deepEqual(wrapper.selectedIds, ['pt:1:b']);
  wrapper.select(['pt:1:a', 'pt:2:a']); await wrapper.whenIdle();
  assert.deepEqual(wrapper.graph.calls.at(-1), ['state', {'pt:1:b': [], 'pt:1:a': ['selected'], 'pt:2:a': ['selected']}]);
  const before = wrapper.graph.calls.length; wrapper.select(['pt:1:a', 'pt:2:a']); assert.equal(wrapper.graph.calls.length, before); // identical list is a no-op
  wrapper.select(null); assert.deepEqual(wrapper.graph.calls.at(-1), ['state', {'pt:1:a': [], 'pt:2:a': []}]); assert.deepEqual(wrapper.selectedIds, []);
  wrapper.destroy();
});

test('preset re-layout with the same IDs moves the points (style update, no topology change, no layout run)', async () => {
  const wrapper = renderG6Network(container(), points, [], {GraphClass: FakeGraph, layoutType: 'preset'}); await wrapper.ready;
  const moved = points.map(node => ({data: {...node.data, x: node.data.x + 100}}));
  await wrapper.update(moved, [], {}, {revision: 1});
  assert.deepEqual(wrapper.graph.getElementPosition('pt:2:a'), [150, 0]);
  assert.equal(wrapper.stats.topologyChanges, 0); assert.equal(wrapper.stats.styleUpdates, 1); assert.equal(wrapper.stats.layoutRuns, 0);
  wrapper.destroy();
});

test('wrapper drives the overlay lifecycle and reports physical misses', async () => {
  const log = []; let created = null;
  const factory = ({graph, container: element}) => { created = {graph, element}; return {update: model => log.push(['update', model?.v]), ready: () => log.push('ready'), follow: active => log.push(['follow', active]), refit: () => log.push('refit'), hover: id => log.push(['hover', id]), resize: () => log.push('resize'), destroy: () => log.push('destroy')}; };
  const element = container(); const misses = []; const selected = [];
  const wrapper = renderG6Network(element, points, [], {GraphClass: FakeGraph, layoutType: 'preset', hitRadius: 9, overlay: factory, overlayModel: {v: 1}, onSelect: id => selected.push(id), onMiss: miss => misses.push(miss)}); await wrapper.ready;
  assert.equal(created.element, element); assert.equal(wrapper.overlay, wrapper.overlay); assert.deepEqual(log, [['update', 1], 'ready']);
  wrapper.graph.emit('beforetransform'); wrapper.graph.emit('aftertransform'); assert.deepEqual(log.slice(2), [['follow', true], ['follow', false]]);
  await wrapper.update(points, [], {}, {revision: 1, overlay: {v: 2}}); assert.deepEqual(log.at(-1), ['update', 2]);
  await wrapper.fit(); assert.equal(log.at(-1), 'refit');
  wrapper.resize(300, 200); assert.equal(log.at(-1), 'resize');
  const fire = (name, event) => element.listeners[name].forEach(handler => handler(event));
  fire('pointerdown', {button: 0, clientX: 52, clientY: 1}); fire('click', {button: 0, clientX: 52, clientY: 1}); assert.deepEqual(selected, ['pt:2:a']); assert.equal(misses.length, 0);
  fire('pointerdown', {button: 0, clientX: 25, clientY: 25}); fire('click', {button: 0, clientX: 25, clientY: 25});
  assert.deepEqual(misses, [{clientX: 25, clientY: 25, canvas: [25, 25]}]); assert.equal(wrapper.stats.missedClicks, 1); assert.deepEqual(selected, ['pt:2:a']);
  wrapper.destroy(); assert.equal(log.at(-1), 'destroy');
});

// Two dense collections joined by many cross-collection semantic edges -> at least one aggregate bridge.
const bridged = () => {
  const ids = Array.from({length: 12}, (_, i) => `p:${String(i).padStart(2, '0')}`); const left = ids.slice(0, 6); const right = ids.slice(6); const edges = []; let n = 0;
  for (const a of left) for (const b of right) edges.push({id: `x:${n += 1}`, source: a, target: b, type: 'semantic', score: 0.8 + (n % 7) / 100});
  for (const group of [left, right]) for (let i = 0; i < group.length; i += 1) for (let j = i + 1; j < group.length; j += 1) edges.push({id: `i:${n += 1}`, source: group[i], target: group[j], type: 'semantic', score: 0.9 + (n % 5) / 100});
  return {schemaVersion: 1, attachments: [], status: {}, source: {serverId: 'server', epoch: 'epoch'}, modelStatus: {}, concepts: [], memberships: [], overview: {fields: [], ribbons: []}, fields: [{id: 'f:x', parentIds: []}],
    papers: ids.map(id => ({id, title: `Paper ${id}`, fieldIds: ['f:x'], displayFieldId: 'f:x', conceptIds: []})), collections: [{id: 'col:L', label: 'Left', paperIds: left}, {id: 'col:R', label: 'Right', paperIds: right}], edges};
};

test('collection counters come from edgesForState: idle -> 0 lines + N bridges, cloud -> strong intra lines, paper -> its lines', () => {
  clearCollectionCloudCache(); const s = bridged();
  const idle = allPaperOverviewProjection(s, {grouping: 'collection', relation: 'semantic'}); const engineIdle = edgesForState(s, idle.cloudLayout, {});
  assert.ok(engineIdle.bridges.length >= 1, 'fixture must produce a bridge');
  assert.equal(idle.counters.displayedEdges, 0); assert.equal(idle.counters.displayedBridges, engineIdle.bridges.length); assert.deepEqual(idle.edgeView.bridges.map(b => b.id), engineIdle.bridges.map(b => b.id));
  const cloud = allPaperOverviewProjection(s, {grouping: 'collection', relation: 'semantic', selectedCloudId: 'col:L'}); const engineCloud = edgesForState(s, cloud.cloudLayout, {cloudId: 'col:L'});
  assert.deepEqual(cloud.counters.displayedEdges, engineCloud.counters.displayedEdges); assert.ok(cloud.edgeView.edges.every(e => e.tier === 'strong' && e.intraCloud));
  assert.ok(cloud.edgeView.bridges.every(b => b.emphasis), 'the only bridge touches the selected cloud');
  const pointId = cloud.nodeIdsByPaper.get('p:00')[0]; const paper = allPaperOverviewProjection(s, {grouping: 'collection', relation: 'semantic', selectedCloudId: 'col:L', selectedPaperId: 'p:00', selectedPointId: pointId});
  const enginePaper = edgesForState(s, paper.cloudLayout, {cloudId: 'col:L', paperId: 'p:00', pointId});
  assert.deepEqual(paper.counters, {...paper.counters, displayedEdges: enginePaper.counters.displayedEdges, displayedBridges: enginePaper.counters.displayedBridges}); assert.equal(paper.counters.displayedEdges, 11, 'every semantic edge of p:00');
  assert.ok(paper.edgeView.bridges.every(b => b.dimmed)); assert.ok(paper.counters.displayedEdges + paper.counters.displayedBridges <= 1000);
  assert.equal(paper.counters.eligibleEdges, s.edges.length, 'eligibleEdges keeps its meaning');
});
