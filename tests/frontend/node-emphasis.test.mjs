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

test('relayout motion: spring ease starts from rest, never overshoots, lands exactly; targets keep the non-expanded nodes in place', async () => {
  const {easeSpring, relayoutTargets, RELAYOUT} = await import('../../frontend/g6-render.js');
  assert.equal(easeSpring(0), 0); assert.equal(easeSpring(1), 1); assert.ok(easeSpring(0.02) < 0.02, 'soft start: slower than linear on the first frames');
  let previous = 0; for (let i = 1; i <= 100; i += 1) { const value = easeSpring(i / 100); assert.ok(value >= previous && value <= 1, `monotonic without overshoot at ${i}`); previous = value; }
  assert.ok(easeSpring(0.5) > 0.9, 'most of the travel is done by half time, the rest is the soft landing'); assert.ok(RELAYOUT.duration >= 400 && RELAYOUT.duration <= 700);
  const from = {hub: {x: 0, y: 0}, a: {x: 200, y: 0}, b: {x: -150, y: 120}}; const edges = [['hub', 'a'], ['hub', 'b']];
  for (let i = 0; i < 10; i += 1) { from[`k${i}`] = {x: 6 * Math.cos(i * 2.4), y: 6 * Math.sin(i * 2.4)}; edges.push(['hub', `k${i}`]); }
  const to = relayoutTargets(from, edges, new Set(['hub', 'a', 'b'])); assert.deepEqual(relayoutTargets(from, edges, new Set(['hub', 'a', 'b'])), to); // deterministic
  for (let i = 0; i < 10; i += 1) assert.ok(Math.hypot(to[`k${i}`].x - to.hub.x, to[`k${i}`].y - to.hub.y) > 30, `child ${i} leaves the node it grew from`);
  for (const id of ['a', 'b']) assert.deepEqual(to[id], from[id], `${id} (not expanded) stays exactly in place`); // expansion pins every existing node except the expanded one, which moves out of the cluster
});

test('relayout clearance: children spawned on a large field node end clear of its drawn size (halo + gap included)', async () => {
  const {relayoutTargets, collideRadius} = await import('../../frontend/g6-render.js');
  const from = {field: {x: 0, y: 0, size: 54}, other: {x: 160, y: 40, size: 34}}; const edges = [['field', 'other']];
  for (let i = 0; i < 24; i += 1) { from[`p${i}`] = {x: 6 * Math.cos(i * 2.4), y: 6 * Math.sin(i * 2.4), size: 14}; edges.push(['field', `p${i}`]); }
  const to = relayoutTargets(from, edges, new Set(['field', 'other']));
  const clear = collideRadius(54) + collideRadius(14) - 2; // d3 collide is soft: allow 2 px
  for (let i = 0; i < 24; i += 1) { const d = Math.hypot(to[`p${i}`].x - to.field.x, to[`p${i}`].y - to.field.y); assert.ok(d >= clear, `child ${i} at ${d.toFixed(1)} px must be >= ${clear.toFixed(1)} px from the field centre`); }
  assert.ok(Math.hypot(to.other.x - to.field.x, to.other.y - to.field.y) >= collideRadius(54) + collideRadius(34) - 2, 'two field nodes never overlap');
});

test('link tiers: four tiers by link count, icons from tier 2, field nodes carry a domain icon; icons are self-contained data URIs', async () => {
  const {markHubs, markLevels, tierOf, TIER_LINKS} = await import('../../frontend/node-emphasis.js');
  const {iconUri, fieldIcon, ICON_PATHS, FIELD_ICONS} = await import('../../frontend/node-icons.js');
  assert.deepEqual([0, 2, 3, 5, 6, 11, 12, 40].map(tierOf), [0, 0, 1, 1, 2, 2, 3, 3]); assert.deepEqual([...TIER_LINKS], [3, 6, 12]);
  const nodes = ['a', 'b', 'c', 'd'].map(id => ({data: {id, kind: 'paper'}})); const edges = [];
  const link = (hub, n) => { for (let i = 0; i < n; i += 1) { const id = `${hub}-${i}`; nodes.push({data: {id, kind: 'paper'}}); edges.push({data: {source: hub, target: id}}); } };
  link('b', 3); link('c', 7); link('d', 13);
  const by = Object.fromEntries(markHubs(nodes, edges).map(node => [node.data.id, node.data]));
  assert.deepEqual(['a', 'b', 'c', 'd'].map(id => [by[id].tier, by[id].shape, by[id].icon ?? null]), [[0, 'circle', null], [1, 'hexagon', null], [2, 'hexagon', 'file-text'], [3, 'hexagon', 'award']]);
  assert.ok(by.a.size < by.b.size && by.b.size < by.c.size && by.c.size < by.d.size, 'size grows with the tier');
  assert.ok(by.d.tierLineWidth > by.c.tierLineWidth, 'tier 3 has the heavier outline');
  assert.equal(markHubs(nodes, edges, {baseSize: 8}).find(node => node.data.id === 'd').data.icon, undefined, 'tiny map points never get an icon');
  const concept = markHubs([{data: {id: 'k', kind: 'concept'}}, ...Array.from({length: 6}, (_, i) => ({data: {id: `k${i}`, kind: 'concept'}}))], Array.from({length: 6}, (_, i) => ({data: {source: 'k', target: `k${i}`}})))[0].data;
  assert.deepEqual([concept.shape, concept.icon], ['star', 'lightbulb']);
  const [field] = markLevels([{data: {id: 'field:s:e:genome-biology', representative: true, level: 0}}]); assert.equal(field.data.icon, 'dna'); assert.equal(field.data.glyph, 'Ⅰ');
  assert.equal(fieldIcon('field:s:e:my-custom-field'), 'folder'); assert.equal(fieldIcon('field:s:e:life-science-86fc21f6e4a0'), 'leaf'); assert.equal(fieldIcon('field:s:e:statistics'), 'chart-column'); for (const name of Object.values(FIELD_ICONS)) assert.ok(ICON_PATHS[name], `${name} has geometry`);
  const uri = iconUri('dna', '#112233'); assert.ok(uri.startsWith('data:image/svg+xml;charset=utf-8,')); assert.ok(decodeURIComponent(uri).includes('stroke="#112233"')); assert.ok(!/https?:/.test(decodeURIComponent(uri).replace('http://www.w3.org/2000/svg', '')), 'no network reference');
  assert.equal(iconUri('dna', 'red;<script>'), iconUri('dna', '#0f172a'), 'colour is sanitised'); assert.equal(iconUri('no-such-icon'), '');
});

test('expansion moves the expanded node out of the cluster and fans its children on the far side, with no overlaps', async () => {
  const {relayoutTargets, collideRadius, FAN} = await import('../../frontend/g6-render.js');
  // parent at the bottom of a cluster of field nodes (the screenshot case): children must go downwards, never into the cluster
  const from = {parent: {x: 0, y: 200, size: 40}}; const edges = []; const existing = new Set(['parent']);
  for (let i = 0; i < 9; i += 1) { const id = `f${i}`; from[id] = {x: -150 + (i % 3) * 150, y: -150 + Math.floor(i / 3) * 110, size: 32}; existing.add(id); edges.push(['parent', id]); }
  for (let i = 0; i < 24; i += 1) { const id = `p${i}`; from[id] = {x: 6 * Math.cos(i * 2.4), y: 200 + 6 * Math.sin(i * 2.4), size: i < 6 ? 32 : 14}; edges.push(['parent', id]); if (i % 4 === 0) edges.push([id, 'f4']); if (i < 6) edges.push([id, 'f0'], [id, 'f8']); } // p0-p5: sub-fields that also link to sibling fields (f0/f8 are as large as the parent)
  from.f0.size = 40; from.f8.size = 40;
  const to = relayoutTargets(from, edges, existing); assert.deepEqual(relayoutTargets(from, edges, existing), to, 'deterministic');
  const others = [...existing].filter(id => id !== 'parent'); for (const id of others) assert.deepEqual(to[id], {x: from[id].x, y: from[id].y}, `${id} stays where it was`);
  const cx = others.reduce((s, id) => s + from[id].x, 0) / others.length; const cy = others.reduce((s, id) => s + from[id].y, 0) / others.length;
  const extent = Math.max(...others.map(id => Math.hypot(from[id].x - cx, from[id].y - cy) + collideRadius(from[id].size)));
  const moved = Math.hypot(to.parent.x - from.parent.x, to.parent.y - from.parent.y); assert.ok(moved < 260, `the expanded node moves only as far as its fan needs (${moved.toFixed(0)} px)`); void extent;
  const away = [to.parent.x - cx, to.parent.y - cy]; assert.ok(away[0] * (from.parent.x - cx) + away[1] * (from.parent.y - cy) > 0, 'it moves outwards along its own side');
  const fanAngles = Array.from({length: 24}, (_, i) => Math.atan2(to[`p${i}`].y - to.parent.y, to[`p${i}`].x - to.parent.x)); const mid = Math.atan2(away[1], away[0]); const off = fanAngles.map(a => Math.abs(Math.atan2(Math.sin(a - mid), Math.cos(a - mid))));
  assert.ok(Math.max(...off) <= Math.PI / 2, `children stay within a half turn around the outward direction (widest ${(Math.max(...off) * 180 / Math.PI).toFixed(0)}°)`);
  for (let i = 0; i < 24; i += 1) { const c = to[`p${i}`]; assert.ok(Math.hypot(c.x - cx, c.y - cy) > Math.hypot(to.parent.x - cx, to.parent.y - cy) - collideRadius(40), `child ${i} does not fall back towards the cluster`); void FAN; }
  const ids = Object.keys(to); const size = id => from[id].size;
  for (let i = 0; i < ids.length; i += 1) for (let j = i + 1; j < ids.length; j += 1) { const a = ids[i]; const b = ids[j]; if (existing.has(a) && existing.has(b)) continue; const d = Math.hypot(to[a].x - to[b].x, to[a].y - to[b].y); assert.ok(d >= collideRadius(size(a)) + collideRadius(size(b)) - 3, `${a} and ${b} overlap (${d.toFixed(1)} px)`); }
});

test('camera walk on expansion: pans only towards the content that left the view, zooms out only when it must', async () => {
  const {cameraToInclude} = await import('../../frontend/g6-render.js');
  const viewport = {width: 800, height: 600}; const camera = {zoom: 1, x: 0, y: 0}; // world (0..800, 0..600) is on screen
  const inside = cameraToInclude({x1: 100, y1: 100, x2: 500, y2: 400}, camera, viewport); assert.deepEqual(inside, camera, 'everything visible: the camera does not move');
  const right = cameraToInclude({x1: 100, y1: 100, x2: 760, y2: 400}, camera, viewport); assert.ok(right.x < 0 && right.y === 0 && right.zoom === 1, 'content grew to the right: the camera pans right (world moves left), not up/down');
  assert.ok(760 * right.zoom + right.x <= 800 - 60 + 1e-6, 'the far side is brought inside the margin');
  const left = cameraToInclude({x1: -200, y1: 100, x2: 300, y2: 400}, camera, viewport); assert.ok(left.x > 0, 'content grew to the left: the camera pans left');
  const big = cameraToInclude({x1: -1000, y1: 0, x2: 1500, y2: 400}, camera, viewport); assert.ok(big.zoom < 1, 'too wide: zooms out'); assert.ok(-1000 * big.zoom + big.x >= 59 && 1500 * big.zoom + big.x <= 741, 'and the whole box fits');
  assert.ok(cameraToInclude({x1: 300, y1: 250, x2: 320, y2: 260}, {zoom: 0.5, x: 200, y: 150}, viewport, {maxZoom: 1.4}).zoom === 0.5, 'never zooms in on its own');
});
