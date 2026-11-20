import test from 'node:test';
import assert from 'node:assert/strict';
import * as d3 from 'd3';
import {layoutCollectionClouds, innerLayout} from '../../frontend/collection-clouds.js';
import {classifyCloud, computeCloudShapes, clipToCircle, circleRing, pointInOutline, clearCloudShapeCache, resetCloudShapeStats, cloudShapeStats, MIN_VERTEX_CAP} from '../../frontend/cloud-shapes.js';

// Synthetic data only.  Every geometric check below is re-derived here (no module checker is trusted).
const TOL = 0.5;
const lcg = seed => { let s = seed >>> 0; return () => { s = (Math.imul(s, 1664525) + 1013904223) >>> 0; return s / 4294967296; }; };
const margin = (x, y, r) => 4 + Math.max(0, Math.min(6, r - Math.hypot(x, y) - 4));
const distSeg = (px, py, [ax, ay], [bx, by]) => { const vx = bx - ax; const vy = by - ay; const l = vx * vx + vy * vy; const t = l ? Math.max(0, Math.min(1, ((px - ax) * vx + (py - ay) * vy) / l)) : 0; return Math.hypot(px - ax - t * vx, py - ay - t * vy); };
const edges = path => path.map((p, i) => [p, path[(i + 1) % path.length]]);
const heavyTailSizes = (k, total) => { const raw = Array.from({length: k}, (_, i) => 1 / (i + 1) ** 1.2); const sum = raw.reduce((a, b) => a + b, 0); const sizes = raw.map(w => Math.max(1, Math.floor((w / sum) * total))); let rest = total - sizes.reduce((a, b) => a + b, 0); for (let i = 0; rest > 0; i = (i + 1) % k, rest -= 1) sizes[i] += 1; return sizes; };

function synth({k = 13, total = 1000, unfiled = 16, cluster = 15, degree = 4, seed = 11, multiFraction = 0.12} = {}) {
  const rand = lcg(seed); const papers = Array.from({length: total}, (_, i) => ({id: `zotero:test:0:P${String(i).padStart(4, '0')}`}));
  const collections = []; let offset = 0;
  heavyTailSizes(k, total - unfiled).forEach((size, c) => { collections.push({id: `collection:test:0:C${String(c + 1).padStart(2, '0')}`, label: `Synthetic cloud ${c + 1}`, paperIds: papers.slice(offset, offset + size).map(p => p.id)}); offset += size; });
  const rows = []; const seen = new Set();
  const add = (a, b) => { if (a === b) return; const key = a < b ? `${a}|${b}` : `${b}|${a}`; if (seen.has(key)) return; seen.add(key); rows.push({source: a, target: b, type: 'semantic', score: Number((0.8 + 0.15 * rand()).toFixed(4))}); };
  for (const c of collections) c.paperIds.forEach((id, i) => { const block = Math.floor(i / cluster) * cluster; const hi = Math.min(c.paperIds.length, block + cluster); for (let d = 0; d < degree / 2; d += 1) add(id, c.paperIds[block + Math.floor(rand() * (hi - block))]); });
  const filed = papers.slice(0, offset);
  for (let i = 0; i < Math.round(filed.length * multiFraction); i += 1) { const paper = filed[Math.floor(rand() * filed.length)]; const home = collections.findIndex(c => c.paperIds.includes(paper.id)); const other = collections[(home + 1 + Math.floor(rand() * (k - 1))) % k]; if (!other.paperIds.includes(paper.id)) other.paperIds.push(paper.id); }
  return {papers, collections, edges: rows};
}
function shuffled(snapshot, seed = 3) {
  const rand = lcg(seed); const mix = rows => { const out = [...rows]; for (let i = out.length - 1; i > 0; i -= 1) { const j = Math.floor(rand() * (i + 1)); [out[i], out[j]] = [out[j], out[i]]; } return out; };
  return {papers: mix(snapshot.papers), collections: mix(snapshot.collections.map(c => ({...c, paperIds: mix(c.paperIds)}))), edges: mix(snapshot.edges.map(e => (rand() < 0.5 ? {...e, source: e.target, target: e.source} : e)))};
}

/** The six AE-6 situations as one snapshot: [collection id or unfiled, papers, inner edges]. */
function situations() {
  const spec = [['c:single', 1, 0], ['c:capsule', 5, 4], ['c:contour', 30, 29], ['c:fewedges', 12, 5], ['c:sunflower', 20, 0], [null, 20, 19]];
  const papers = []; const collections = []; const rows = [];
  for (const [id, n, m] of spec) {
    const ids = Array.from({length: n}, (_, i) => `p:${id || 'unfiled'}:${String(i).padStart(2, '0')}`); papers.push(...ids.map(pid => ({id: pid})));
    if (id) collections.push({id, label: id, paperIds: ids});
    for (let i = 0; i < m; i += 1) rows.push({source: ids[i], target: ids[i + 1], type: 'semantic', score: 0.9});
  }
  return {papers, collections, edges: rows};
}

/** A hand-built single-cloud layout from local points (r = packEnclose of radius-4 discs, so the outer discs touch the circle). */
function handLayout(id, local, {mode = 'force', edgeCount = 40, unfiled = false, subgroups = []} = {}) {
  const enclosure = d3.packEnclose(local.map(([x, y]) => ({x, y, r: 4})));
  const points = local.map(([x, y], i) => ({id: `overview-point:${id}:q${i}`, paperId: `q${String(i).padStart(3, '0')}`, cloudId: id, x: x - enclosure.x, y: y - enclosure.y}));
  return {spacing: 10, clouds: [{id, cx: 0, cy: 0, r: enclosure.r, size: local.length, mode, edgeCount, unfiled, subgroups}], points};
}

/** AE-5 by direct geometry: every margin disc inside (centre in, edge distance >= m - 0.5), every vertex within r + 0.5, simple polygon. */
function assertContainment(path, centres, r, label) {
  assert.ok(path.length >= 3, `${label}: outline has ${path.length} vertices`);
  let worstOut = -Infinity; for (const [x, y] of path) worstOut = Math.max(worstOut, Math.hypot(x, y) - r);
  assert.ok(worstOut <= TOL, `${label}: outline leaves the cloud circle by ${worstOut.toFixed(3)} px`);
  let worst = Infinity;
  for (const [x, y] of centres) { assert.ok(d3.polygonContains(path, [x, y]), `${label}: point (${x.toFixed(2)}, ${y.toFixed(2)}) outside its outline`); const m = margin(x, y, r); for (const [a, b] of edges(path)) worst = Math.min(worst, distSeg(x, y, a, b) - m); }
  assert.ok(worst >= -TOL, `${label}: a margin disc is cut by ${(-worst).toFixed(3)} px`);
  assertSimple(path, label);
  return {worstOut, worst};
}
function assertSimple(path, label) {
  const o = (a, b, c) => Math.sign((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])); const es = edges(path); const n = es.length;
  for (let i = 0; i < n; i += 1) for (let j = i + 2; j < n; j += 1) { if (i === 0 && j === n - 1) continue; const [a, b] = es[i]; const [c, d] = es[j]; assert.ok(!(o(a, b, c) * o(a, b, d) < 0 && o(c, d, a) * o(c, d, b) < 0), `${label}: edges ${i} and ${j} intersect`); }
}
const localOf = (layout, cloud) => layout.points.filter(p => p.cloudId === cloud.id).map(p => [p.x - cloud.cx, p.y - cloud.cy]);
function assertLayoutShapes(layout, shapes, tag) {
  const rows = [];
  layout.clouds.forEach((cloud, i) => {
    const shape = shapes[i]; const centres = localOf(layout, cloud); assert.equal(shape.cloudId, cloud.id);
    if (shape.kind === 'single') { assert.equal(shape.method, 'none'); assert.equal(shape.d, ''); return; }
    const g = assertContainment(shape.path, centres, cloud.r, `${tag} ${cloud.id}`);
    assert.ok(shape.path.length <= Math.max(MIN_VERTEX_CAP, shape.arcVertices), `${tag} ${cloud.id}: ${shape.path.length} vertices > cap`);
    const members = new Map(layout.points.filter(p => p.cloudId === cloud.id).map(p => [p.paperId, [p.x - cloud.cx, p.y - cloud.cy]]));
    for (const sub of shape.subgroups) { const group = cloud.subgroups.find(g2 => g2.id === sub.id); assert.ok(group && !group.other, `${sub.id} is a real sub-group`); assertContainment(sub.path, group.paperIds.map(id => members.get(id)), cloud.r, `${tag} ${cloud.id} ${sub.id}`); }
    rows.push({id: cloud.id, method: shape.method, vertices: shape.path.length, worstOut: g.worstOut, clearance: g.worst});
  });
  return rows;
}

const big = synth();
const bigLayout = layoutCollectionClouds(big, {useMemo: false});

test('AE-6: the six situations map to the expected kind and method', () => {
  clearCloudShapeCache();
  const layout = layoutCollectionClouds(situations(), {useMemo: false}); const {shapes} = computeCloudShapes(layout);
  const byId = new Map(shapes.map(s => [s.cloudId, s])); const cloudById = new Map(layout.clouds.map(c => [c.id, c]));
  assert.equal(cloudById.get('c:contour').mode, 'force'); assert.ok(cloudById.get('c:contour').edgeCount >= 8);
  assert.equal(cloudById.get('c:fewedges').mode, 'force'); assert.ok(cloudById.get('c:fewedges').edgeCount >= 3 && cloudById.get('c:fewedges').edgeCount <= 7);
  assert.equal(cloudById.get('c:sunflower').mode, 'sunflower'); assert.ok(cloudById.get('cloud:unfiled').edgeCount >= 8, 'unfiled cloud would qualify for a contour');
  const expected = {'c:single': ['single', 'none', false], 'c:capsule': ['capsule', 'capsule', false], 'c:contour': ['contour', 'contour', false], 'c:fewedges': ['soft-hull', 'soft-hull', false], 'c:sunflower': ['soft-hull', 'soft-hull', false], 'cloud:unfiled': ['soft-hull', 'soft-hull', true]};
  for (const [id, [kind, method, unfiled]] of Object.entries(expected)) {
    const shape = byId.get(id); assert.deepEqual([shape.kind, shape.method, shape.unfiled], [kind, method, unfiled], id);
    assert.deepEqual(classifyCloud(cloudById.get(id)), {kind, unfiled});
  }
  assertLayoutShapes(layout, shapes, 'situations');
});

test('AE-5 on the synthetic 1,000-paper map: containment, inside the circle, simple, vertex cap, sub-groups', t => {
  clearCloudShapeCache(); resetCloudShapeStats();
  const {shapes, stats} = computeCloudShapes(bigLayout);
  const rows = assertLayoutShapes(bigLayout, shapes, 'big');
  assert.equal(stats.computed, bigLayout.clouds.length);
  t.diagnostic(`methods=${JSON.stringify(stats.methods)} fallbacks=${JSON.stringify(stats.fallbacks)} subgroupsOmitted=${stats.subgroupsOmitted} shapeMs=${stats.shapeMs.toFixed(1)} subgroupOutlines=${shapes.reduce((s, x) => s + x.subgroups.length, 0)}`);
  t.diagnostic(`vertices=${rows.map(r => r.vertices).join(',')} maxOutside=${Math.max(...rows.map(r => r.worstOut)).toFixed(3)} minClearance=${Math.min(...rows.map(r => r.clearance)).toFixed(3)}`);
});

test('AE-5 outlier fixture: one point far from the rest is still enclosed', t => {
  const rand = lcg(7); const local = Array.from({length: 24}, (_, i) => [Math.cos(i * 2.4) * 3 * Math.sqrt(i + 0.5) * 3, Math.sin(i * 2.4) * 3 * Math.sqrt(i + 0.5) * 3]).map(([x, y]) => [x + rand(), y + rand()]);
  local.push([150, 20]);
  const layout = handLayout('c:outlier', local); clearCloudShapeCache();
  const {shapes, stats} = computeCloudShapes(layout); assert.equal(shapes[0].kind, 'contour');
  assertLayoutShapes(layout, shapes, 'outlier');
  t.diagnostic(`outlier: method=${shapes[0].method} vertices=${shapes[0].path.length} arc=${shapes[0].arcVertices} fallbacks=${JSON.stringify(stats.fallbacks)}`);
});

test('AE-5 every point touching the circle: contour, soft-hull and capsule variants', t => {
  for (const [n, mode, edgeCount] of [[16, 'force', 20], [16, 'sunflower', 0], [6, 'sunflower', 0]]) {
    const R = 6 * n; const local = Array.from({length: n}, (_, i) => [Math.cos((i / n) * Math.PI * 2) * R, Math.sin((i / n) * Math.PI * 2) * R]);
    const layout = handLayout(`c:ring${n}${mode}`, local, {mode, edgeCount}); clearCloudShapeCache();
    const cloud = layout.clouds[0]; for (const [x, y] of localOf(layout, cloud)) assert.ok(Math.abs(Math.hypot(x, y) + 4 - cloud.r) < 1e-6, 'fixture: every disc touches the circle');
    const {shapes} = computeCloudShapes(layout); assertLayoutShapes(layout, shapes, `ring ${n} ${mode}`);
    t.diagnostic(`ring n=${n} ${mode}: kind=${shapes[0].kind} method=${shapes[0].method} vertices=${shapes[0].path.length} arc=${shapes[0].arcVertices}`);
  }
});

test('arc replacement: a radially clamped chord cuts a touching disc, the arc samples do not', () => {
  const r = 50; const p = [46, 0]; const m = margin(...p, r); assert.equal(m, 4);
  const ring = [[0, -40], [80, 0], [0, 40], [-30, 0]]; // ccw, one spike leaves the circle around the touching point
  const clamped = ring.map(([x, y]) => { const d = Math.hypot(x, y); return d > r ? [(x / d) * r, (y / d) * r] : [x, y]; });
  const clampedCut = Math.min(...edges(clamped).map(([a, b]) => distSeg(...p, a, b))) - m;
  assert.ok(clampedCut < -1, `vertex clamp alone cuts the disc by ${(-clampedCut).toFixed(2)} px (the failure the arc rule prevents)`);
  const clipped = clipToCircle(ring, r).map(([x, y]) => [x, y]);
  const arcCut = Math.min(...edges(clipped).map(([a, b]) => distSeg(...p, a, b))) - m;
  assert.ok(arcCut >= -0.25 - 1e-9, `arc chords sag at most 0.25 px into the disc (got ${arcCut})`);
  const onCircle = clipped.filter(([x, y]) => Math.abs(Math.hypot(x, y) - r) < 1e-9); assert.ok(onCircle.length >= 3);
  for (let i = 0; i < clipped.length; i += 1) { const a = clipped[i]; const b = clipped[(i + 1) % clipped.length]; if (onCircle.includes(a) && onCircle.includes(b)) assert.ok(Math.hypot(a[0] - b[0], a[1] - b[1]) <= Math.sqrt(8 * r * 0.25) + 1e-9, 'arc sample spacing'); }
  assert.ok(Math.max(...clipped.map(([x, y]) => Math.hypot(x, y))) <= r + 1e-9);
  const circle = circleRing(r); for (let i = 0; i < circle.length; i += 1) { const a = circle[i]; const b = circle[(i + 1) % circle.length]; const mid = Math.hypot((a[0] + b[0]) / 2, (a[1] + b[1]) / 2); assert.ok(r - mid <= 0.25 + 1e-9, 'circle chord sag <= 0.25'); }
});

test('vertex cap: a smaller cap is honoured; an unreachable cap walks the chain to the circle', t => {
  clearCloudShapeCache(); resetCloudShapeStats();
  const capped = computeCloudShapes(bigLayout, {minVertexCap: 24, subgroups: false});
  for (const shape of capped.shapes) if (shape.kind !== 'single') assert.ok(shape.path.length <= Math.max(24, shape.arcVertices), `${shape.cloudId}: ${shape.path.length}`);
  assertLayoutShapes(bigLayout, capped.shapes, 'cap24');
  const impossible = computeCloudShapes(bigLayout, {minVertexCap: 3, subgroups: false});
  assertLayoutShapes(bigLayout, impossible.shapes, 'cap3');
  assert.ok(impossible.shapes.some(s => s.method === 'circle'), 'cap 3 leaves only the circle for some cloud');
  assert.ok(impossible.stats.fallbacks.s1ToS2 > 0 && impossible.stats.fallbacks.s2ToCircle > 0, JSON.stringify(impossible.stats.fallbacks));
  t.diagnostic(`cap24 methods=${JSON.stringify(capped.stats.methods)} fallbacks=${JSON.stringify(capped.stats.fallbacks)}; cap3 methods=${JSON.stringify(impossible.stats.methods)} fallbacks=${JSON.stringify(impossible.stats.fallbacks)}`);
});

test('fallback S1 -> S2: no contour iterations means every contour cloud uses the soft hull', () => {
  clearCloudShapeCache();
  const {shapes, stats} = computeCloudShapes(bigLayout, {contourIterations: 0, subgroups: false});
  const contourKinds = shapes.filter(s => s.kind === 'contour'); assert.ok(contourKinds.length >= 8);
  assert.ok(contourKinds.every(s => s.method === 'soft-hull')); assert.equal(stats.fallbacks.s1ToS2, contourKinds.length);
  assertLayoutShapes(bigLayout, shapes, 'noS1');
});

test('determinism: two runs, shuffled input and a second layout give identical d strings', () => {
  const ds = layout => { clearCloudShapeCache(); return computeCloudShapes(layout).shapes.map(s => [s.cloudId, s.method, s.d, s.subgroups.map(g => g.d)]); };
  const first = ds(bigLayout);
  assert.deepEqual(ds(bigLayout), first);
  assert.deepEqual(ds(layoutCollectionClouds(shuffled(big, 5), {useMemo: false})), first);
  assert.ok(first.every(([, , d]) => d === '' || /^M-?\d+\.\d\d,-?\d+\.\d\d(L-?\d+\.\d\d,-?\d+\.\d\d)+Z$/.test(d)), 'fixed 2-decimal formatting');
});

test('memo: fit areas, re-packing and repeated calls perform 0 recomputations', t => {
  clearCloudShapeCache(); resetCloudShapeStats();
  computeCloudShapes(bigLayout); const afterFirst = cloudShapeStats(); assert.equal(afterFirst.computed, bigLayout.clouds.length);
  const fitted = [[652, 372], [982, 512], [1440, 900]].map(([width, height]) => layoutCollectionClouds(big, {useMemo: false, fitArea: {width, height}})).filter(layout => layout.spacing === bigLayout.spacing);
  assert.ok(fitted.length >= 1, 'at least one fit area keeps the default spacing');
  assert.ok(fitted.some(layout => layout.fit.reserved && layout.clouds.some((c, i) => c.cx !== bigLayout.clouds[i].cx)), 'a fit area re-packs the clouds (label reserve)');
  const before = cloudShapeStats();
  for (const layout of fitted) { const {shapes, stats} = computeCloudShapes(layout); assert.equal(stats.computed + stats.subgroupsComputed, 0); assertLayoutShapes(layout, shapes, `fit ${layout.fit.rawZoom.toFixed(3)}`); }
  computeCloudShapes(bigLayout); const after = cloudShapeStats();
  assert.equal(after.computed - before.computed, 0); assert.equal(after.subgroupsComputed - before.subgroupsComputed, 0); assert.ok(after.memoHits > before.memoHits);
  t.diagnostic(`fit layouts reused=${fitted.length} memoHits=${after.memoHits} computed=${after.computed}`);
});

test('pointInOutline agrees with containment', () => {
  clearCloudShapeCache();
  const {shapes} = computeCloudShapes(bigLayout); const rand = lcg(17);
  bigLayout.clouds.forEach((cloud, i) => {
    const shape = shapes[i];
    for (const [x, y] of localOf(bigLayout, cloud)) assert.equal(pointInOutline(shape, x, y), shape.kind !== 'single');
    for (let k = 0; k < 50; k += 1) { const a = rand() * Math.PI * 2; const d = cloud.r + TOL + 0.01 + rand() * 20; assert.equal(pointInOutline(shape, Math.cos(a) * d, Math.sin(a) * d), false); }
    for (let k = 0; k < 50; k += 1) { const x = (rand() * 2 - 1) * cloud.r; const y = (rand() * 2 - 1) * cloud.r; if (pointInOutline(shape, x, y)) assert.ok(Math.hypot(x, y) <= cloud.r + TOL); }
  });
});

test('layout additions: edgeCount, sub-group members and fit-independent baseCenters', () => {
  const nofit = bigLayout; const fitted = layoutCollectionClouds(big, {useMemo: false, fitArea: {width: 652, height: 372}});
  assert.deepEqual(fitted.baseCenters, nofit.clouds.map(c => ({id: c.id, cx: c.cx, cy: c.cy, r: c.r})), 'baseCenters = reserve-0 default-spacing build');
  assert.deepEqual(nofit.baseCenters, nofit.clouds.map(c => ({id: c.id, cx: c.cx, cy: c.cy, r: c.r})));
  for (const cloud of nofit.clouds) {
    const ids = nofit.points.filter(p => p.cloudId === cloud.id).map(p => p.paperId).sort();
    assert.deepEqual(cloud.subgroups.flatMap(g => g.paperIds).sort(), ids, `${cloud.id}: sub-groups partition the cloud`);
    for (const g of cloud.subgroups) assert.equal(g.paperIds.length, g.size);
    const memberEdges = big.edges.filter(e => ids.includes(e.source) && ids.includes(e.target));
    assert.equal(cloud.edgeCount, innerLayout(ids, memberEdges).edgeCount);
  }
});
