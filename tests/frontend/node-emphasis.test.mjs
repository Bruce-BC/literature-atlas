import test from 'node:test';
import assert from 'node:assert/strict';
import cytoscape from 'cytoscape';
import {HUB_MIN_DEGREE, HUB_MAX_SCALE, LABEL_ALL_LIMIT, hubScale, linkCounts, markHubs, markLevels, levelStyle, namedWhenShown} from '../../frontend/node-emphasis.js';
import {cytoscapeStyle, CY_EDGE_CURVE} from '../../frontend/network-views.js';
import {allPaperOverviewProjection, categoryGroups} from '../../frontend/all-paper-overview.js';

const node = (id, extra = {}) => ({data: {id, kind: 'paper', ...extra}});
const edge = (source, target) => ({data: {id: `${source}-${target}`, source, target}});

test('hubs: 3 or more displayed links change the shape and grow the node; fewer links change nothing', () => {
  const nodes = [node('a'), node('b'), node('c'), node('d'), node('k', {kind: 'concept'}), node('f', {representative: true, level: 0})];
  const edges = [edge('a', 'b'), edge('a', 'c'), edge('a', 'd'), edge('k', 'a'), edge('k', 'b'), edge('k', 'c'), edge('b', 'b')];
  assert.equal(HUB_MIN_DEGREE, 3); assert.deepEqual([...linkCounts(edges)].sort(), [['a', 4], ['b', 2], ['c', 2], ['d', 1], ['k', 3]]); // the self-loop is ignored
  const by = new Map(markHubs(nodes, edges).map(row => [row.data.id, row.data]));
  assert.deepEqual([by.get('a').hub, by.get('a').shape, by.get('a').links], [true, 'hexagon', 4]);
  assert.deepEqual([by.get('k').hub, by.get('k').shape], [true, 'star']);
  assert.deepEqual([by.get('b').hub, by.get('b').shape, by.get('b').size], [false, 'circle', 18]);
  assert.ok(by.get('a').size > by.get('b').size); assert.equal(by.get('f').shape, undefined); // field nodes belong to markLevels
  assert.equal(nodes[0].data.hub, undefined); // input is not mutated
  assert.equal(hubScale(2), 1); assert.ok(hubScale(3) > 1); assert.ok(hubScale(6) > hubScale(3)); assert.equal(hubScale(10000), HUB_MAX_SCALE);
  assert.equal(markHubs([node('p')], [], {baseSize: 8})[0].data.size, 8);
});

test('levels: each hierarchy depth has its own shape, size, border and numeral; deeper levels share the last style', () => {
  const marked = markLevels([node('f0', {representative: true, level: 0}), node('f1', {representative: true, level: 1}), node('f2', {representative: true, level: 2}), node('f5', {representative: true, level: 5}), node('p')]);
  const [f0, f1, f2, f5, p] = marked.map(row => row.data);
  assert.equal(new Set([f0.shape, f1.shape, f2.shape]).size, 3); assert.equal(new Set([f0.glyph, f1.glyph, f2.glyph]).size, 3);
  assert.ok(f0.size > f1.size && f1.size > f2.size); assert.equal(f0.levelDash, null); assert.deepEqual(f2.levelDash, [4, 2]);
  assert.deepEqual([f5.shape, f5.glyph], [f2.shape, f2.glyph]); assert.equal(p.shape, undefined); assert.equal(levelStyle(-1), levelStyle(0));
});

test('show names: every node of a small graph, only hubs and field nodes of a large one', () => {
  assert.equal(namedWhenShown({}, LABEL_ALL_LIMIT), true); assert.equal(namedWhenShown({}, LABEL_ALL_LIMIT + 1), false);
  assert.equal(namedWhenShown({hub: true}, 1000), true); assert.equal(namedWhenShown({representative: true}, 1000), true);
});

test('Cytoscape: edges are slightly curved, hub shape/size and the name flag come from node data', () => {
  const colors = {text: '#111111', panel: '#ffffff', border: '#888888', focus: '#2563eb'};
  const cy = cytoscape({headless: true, styleEnabled: true, style: cytoscapeStyle(colors, {reduced: true}), elements: [
    {data: {id: 'a', label: 'Alpha', cyShape: 'hexagon', cySize: 40, named: true}}, {data: {id: 'b', label: 'Beta', cyShape: 'ellipse', cySize: 27, named: false}}, {data: {id: 'e', source: 'a', target: 'b'}}]});
  try {
    const e = cy.getElementById('e'); assert.equal(e.style('curve-style'), 'unbundled-bezier'); assert.deepEqual(e.numericStyle('control-point-distances'), [CY_EDGE_CURVE]);
    const a = cy.getElementById('a'); const b = cy.getElementById('b');
    assert.equal(a.style('shape'), 'hexagon'); assert.equal(a.numericStyle('width'), 40); assert.equal(b.style('shape'), 'ellipse'); assert.equal(b.numericStyle('width'), 27);
    assert.equal(a.style('label'), 'Alpha'); assert.equal(b.style('label'), '');
  } finally { cy.destroy(); }
});

test('전체 논문 지도: one cloud per category, points of a category stay together and clouds do not overlap', () => {
  const fields = ['f:a', 'f:b', 'f:c'].map(id => ({id, label: {en: id.toUpperCase()}, parentIds: []}));
  const papers = Array.from({length: 90}, (_, i) => ({id: `p:${String(i).padStart(3, '0')}`, title: `T${i}`, fieldIds: i < 84 ? [fields[i % 3].id] : [], displayFieldId: i < 84 ? fields[i % 3].id : null, conceptIds: []}));
  const snapshot = {source: {serverId: 's', epoch: 'e'}, fields, papers, concepts: [], edges: [], memberships: [], collections: []};
  const projection = allPaperOverviewProjection(snapshot, {relation: 'semantic'});
  assert.deepEqual(projection.clouds.map(cloud => [cloud.id, cloud.size, cloud.unfiled]), [['category:f:a', 28, false], ['category:f:b', 28, false], ['category:f:c', 28, false], ['category:unknown', 6, true]]);
  assert.equal(projection.clouds.at(-1).label, '미분류'); assert.equal(projection.points.length, 90); assert.equal(projection.nodes[0].data.id, 'overview-paper:p:000');
  const cloudById = new Map(projection.clouds.map(cloud => [cloud.id, cloud]));
  for (const point of projection.points) { const cloud = cloudById.get(point.cloudId); assert.equal(point.cloudId, `category:${point.categoryKey}`); assert.ok(Math.hypot(point.deterministicX - cloud.cx, point.deterministicY - cloud.cy) <= cloud.r + 1e-6, `${point.id} inside its category cloud`); }
  for (const [i, a] of projection.clouds.entries()) for (const b of projection.clouds.slice(i + 1)) assert.ok(Math.hypot(a.cx - b.cx, a.cy - b.cy) >= a.r + b.r, `${a.id} and ${b.id} are separate`);
  assert.deepEqual(allPaperOverviewProjection(snapshot, {relation: 'semantic'}).points, projection.points); // deterministic
  assert.equal(typeof categoryGroups, 'function');
});

test('relayout motion: spring ease starts from rest, never overshoots, lands exactly; targets keep the existing nodes in place on average', async () => {
  const {easeSpring, relayoutTargets, RELAYOUT} = await import('../../frontend/g6-render.js');
  assert.equal(easeSpring(0), 0); assert.equal(easeSpring(1), 1); assert.ok(easeSpring(0.02) < 0.02, 'soft start: slower than linear on the first frames');
  let previous = 0; for (let i = 1; i <= 100; i += 1) { const value = easeSpring(i / 100); assert.ok(value >= previous && value <= 1, `monotonic without overshoot at ${i}`); previous = value; }
  assert.ok(easeSpring(0.5) > 0.9, 'most of the travel is done by half time, the rest is the soft landing'); assert.ok(RELAYOUT.duration >= 400 && RELAYOUT.duration <= 700);
  const from = {hub: {x: 0, y: 0}, a: {x: 200, y: 0}, b: {x: -150, y: 120}}; const edges = [['hub', 'a'], ['hub', 'b']];
  for (let i = 0; i < 10; i += 1) { from[`k${i}`] = {x: 6 * Math.cos(i * 2.4), y: 6 * Math.sin(i * 2.4)}; edges.push(['hub', `k${i}`]); }
  const to = relayoutTargets(from, edges, new Set(['hub', 'a', 'b'])); assert.deepEqual(relayoutTargets(from, edges, new Set(['hub', 'a', 'b'])), to); // deterministic
  for (let i = 0; i < 10; i += 1) assert.ok(Math.hypot(to[`k${i}`].x - to.hub.x, to[`k${i}`].y - to.hub.y) > 30, `child ${i} leaves the node it grew from`);
  const drift = ['hub', 'a', 'b'].reduce((sum, id) => [sum[0] + to[id].x - from[id].x, sum[1] + to[id].y - from[id].y], [0, 0]); assert.ok(Math.hypot(...drift) < 1e-6, 'centroid of the existing nodes is kept');
});
