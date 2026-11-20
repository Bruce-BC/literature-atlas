import test from 'node:test';
import assert from 'node:assert/strict';
import {cloudGroups, subgroupLPA, innerLayout, packClouds, fitZoomFor, placeCloudLabels, layoutCollectionClouds, clearCollectionCloudCache, UNFILED_CLOUD_ID} from '../../frontend/collection-clouds.js';

// Synthetic data only: parametric heavy-tail cloud sizes, planted clusters, kNN-like semantic edges.
const GAP = 40; const MIN_DISTANCE = 9; const LABEL_WIDTH = 160;
const lcg = seed => { let s = seed >>> 0; return () => { s = (Math.imul(s, 1664525) + 1013904223) >>> 0; return s / 4294967296; }; };

export function heavyTailSizes(k, total, alpha = 1.2) {
  if (k <= 0 || total <= 0) return [];
  const raw = Array.from({length: k}, (_, i) => 1 / (i + 1) ** alpha); const sum = raw.reduce((a, b) => a + b, 0);
  const sizes = raw.map(w => Math.max(1, Math.floor((w / sum) * total))); let rest = total - sizes.reduce((a, b) => a + b, 0);
  for (let i = 0; rest > 0; i = (i + 1) % k, rest -= 1) sizes[i] += 1;
  for (let i = k - 1; rest < 0; i = (i - 1 + k) % k) if (sizes[i] > 1) { sizes[i] -= 1; rest += 1; }
  return sizes;
}

function synthSnapshot({k = 12, total = 1000, alpha = 1.2, unfiled = 16, multiFraction = 0.12, cluster = 15, degree = 4, seed = 11} = {}) {
  const rand = lcg(seed); const papers = Array.from({length: total}, (_, i) => ({id: `zotero:test:0:P${String(i).padStart(4, '0')}`, title: `Synthetic paper ${i}`}));
  const sizes = heavyTailSizes(k, total - unfiled, alpha); const collections = []; let offset = 0;
  sizes.forEach((size, c) => { collections.push({id: `collection:test:0:C${String(c + 1).padStart(2, '0')}`, label: `Synthetic cloud ${c + 1}`, paperIds: papers.slice(offset, offset + size).map(p => p.id)}); offset += size; });
  const edges = []; const seen = new Set();
  const add = (a, b) => { if (a === b) return; const key = a < b ? `${a}|${b}` : `${b}|${a}`; if (seen.has(key)) return; seen.add(key); edges.push({id: `edge:test:${edges.length}`, source: a, target: b, type: 'semantic', directed: false, score: Number((0.8 + 0.15 * rand()).toFixed(4))}); };
  for (const collection of collections) {
    const ids = collection.paperIds;
    ids.forEach((id, i) => { const block = Math.floor(i / cluster) * cluster; const hi = Math.min(ids.length, block + cluster); for (let d = 0; d < degree / 2; d += 1) add(id, ids[block + Math.floor(rand() * (hi - block))]); });
  }
  if (k > 1) { const filed = papers.slice(0, offset); const extra = Math.round(filed.length * multiFraction); for (let i = 0; i < extra; i += 1) { const paper = filed[Math.floor(rand() * filed.length)]; const home = collections.findIndex(c => c.paperIds.includes(paper.id)); const other = collections[(home + 1 + Math.floor(rand() * (k - 1))) % k]; if (!other.paperIds.includes(paper.id)) other.paperIds.push(paper.id); } }
  return {papers, collections, edges};
}

function shuffled(snapshot, seed = 3) {
  const rand = lcg(seed); const mix = rows => { const out = [...rows]; for (let i = out.length - 1; i > 0; i -= 1) { const j = Math.floor(rand() * (i + 1)); [out[i], out[j]] = [out[j], out[i]]; } return out; };
  return {papers: mix(snapshot.papers), collections: mix(snapshot.collections.map(c => ({...c, paperIds: mix(c.paperIds)}))), edges: mix(snapshot.edges.map(e => (rand() < 0.5 ? {...e, source: e.target, target: e.source} : e)))};
}

const rounded = layout => ({
  points: [...layout.points].sort((a, b) => (a.id < b.id ? -1 : 1)).map(p => [p.id, p.x.toFixed(2), p.y.toFixed(2)]),
  clouds: [...layout.clouds].sort((a, b) => (a.id < b.id ? -1 : 1)).map(c => [c.id, c.cx.toFixed(2), c.cy.toFixed(2), c.r.toFixed(2)])
});

/** AC-2 geometry: pairwise cloud gaps, points inside their cloud circle, min point distance (independent O(n^2) check). */
function geometry(layout) {
  let minGap = Infinity; let overlaps = 0; let outside = 0; let violations = 0; let minPoint = Infinity;
  for (let i = 0; i < layout.clouds.length; i += 1) for (let j = i + 1; j < layout.clouds.length; j += 1) {
    const a = layout.clouds[i]; const b = layout.clouds[j]; const gap = Math.hypot(a.cx - b.cx, a.cy - b.cy) - a.r - b.r; minGap = Math.min(minGap, gap); if (gap < 0) overlaps += 1;
  }
  const byCloud = new Map(layout.clouds.map(c => [c.id, []])); for (const p of layout.points) byCloud.get(p.cloudId).push(p);
  for (const cloud of layout.clouds) {
    const pts = byCloud.get(cloud.id);
    for (const p of pts) if (Math.hypot(p.x - cloud.cx, p.y - cloud.cy) > cloud.r + 1e-6) outside += 1;
    for (let i = 0; i < pts.length; i += 1) for (let j = i + 1; j < pts.length; j += 1) { const d = Math.hypot(pts[i].x - pts[j].x, pts[i].y - pts[j].y); minPoint = Math.min(minPoint, d); if (d < MIN_DISTANCE) violations += 1; }
  }
  return {minGap, overlaps, outside, violations, minPoint};
}

function assertGeometry(layout) {
  const g = geometry(layout);
  assert.equal(g.overlaps, 0); assert.equal(g.outside, 0); assert.equal(g.violations, 0, `min point distance ${g.minPoint}`);
  if (layout.clouds.length > 1) assert.ok(g.minGap >= GAP, `cloud gap ${g.minGap} < ${GAP}`);
  return g;
}

const fresh = (snapshot, options = {}) => layoutCollectionClouds(snapshot, {useMemo: false, ...options});
const big = synthSnapshot({k: 13, total: 1000, unfiled: 16}); // 13 core clouds + 1 unfiled = 14 clouds

test('AC-1: one cloud per non-empty top-level collection, sub-collections roll up, one unfiled cloud, k memberships -> k points', () => {
  const snapshot = {
    papers: ['p1', 'p2', 'p3', 'p4', 'p5', 'p6'].map(id => ({id})),
    collections: [
      {id: 'col:b', label: 'B', paperIds: ['p2', 'p4']},
      {id: 'col:a1', label: 'A child', parentId: 'col:a', paperIds: ['p3', 'p2']},
      {id: 'col:a', label: 'A', parentId: null, paperIds: ['p1', 'p2']},
      {id: 'col:a1x', label: 'A grandchild', parentId: 'col:a1', paperIds: ['p1']},
      {id: 'col:empty', label: 'Empty', paperIds: []},
      {id: 'col:stale', label: 'Inactive only', paperIds: ['gone']}
    ],
    edges: []
  };
  const clouds = cloudGroups(snapshot);
  assert.deepEqual(clouds.map(c => [c.id, c.paperIds]), [['col:a', ['p1', 'p2', 'p3']], ['col:b', ['p2', 'p4']], [UNFILED_CLOUD_ID, ['p5', 'p6']]]);
  assert.equal(clouds.filter(c => c.unfiled).length, 1);
  const layout = fresh(snapshot);
  assert.equal(layout.points.length, 3 + 2 + 2);
  assert.deepEqual(layout.points.filter(p => p.paperId === 'p2').map(p => p.id).sort(), ['overview-point:col:a:p2', 'overview-point:col:b:p2']);
  assert.ok(layout.points.some(p => p.id === 'overview-point:unfiled:p5'));
  assert.deepEqual(layout.representedIds, ['p1', 'p2', 'p3', 'p4', 'p5', 'p6']);
  assert.equal(cloudGroups({...snapshot, papers: snapshot.papers.slice(0, 4)}).some(c => c.unfiled), false, 'no unfiled cloud when every paper is filed');
});

test('AC-1/AC-4 on synthetic 1,000 papers: points = memberships, unique represented = active papers', () => {
  const layout = fresh(big);
  const memberships = big.collections.reduce((sum, c) => sum + c.paperIds.length, 0);
  const filed = new Set(big.collections.flatMap(c => c.paperIds));
  assert.equal(layout.clouds.length, 14);
  assert.equal(layout.points.length, memberships + (big.papers.length - filed.size));
  assert.deepEqual(layout.representedIds, big.papers.map(p => p.id).sort());
  assert.ok(layout.points.length > big.papers.length, 'multi-membership produces duplicate points');
});

test('AC-2: 14 clouds / ~1,000 papers keep gap >= 40, zero overlap, zero point-distance violations', t => {
  const layout = fresh(big); const g = assertGeometry(layout);
  t.diagnostic(`clouds=${layout.clouds.length} points=${layout.points.length} minGap=${g.minGap.toFixed(2)} minPointDistance=${g.minPoint.toFixed(2)} modes=${[...new Set(layout.clouds.map(c => c.mode))].join(',')}`);
});

test('AC-2 edge cases: empty snapshot, single cloud, 1-paper, 2-paper and zero-edge clouds', () => {
  const empty = fresh({papers: [], collections: [], edges: []});
  assert.equal(empty.clouds.length, 0); assert.equal(empty.points.length, 0); assert.equal(fitZoomFor(empty, 652, 372), 1);
  assertGeometry(fresh(synthSnapshot({k: 1, total: 300, unfiled: 0, multiFraction: 0})));
  const tiny = {papers: ['a', 'b', 'c', 'u'].map(id => ({id})), collections: [{id: 'c:one', label: 'One', paperIds: ['a']}, {id: 'c:two', label: 'Two', paperIds: ['b', 'c']}], edges: [{source: 'b', target: 'c', type: 'semantic', score: 0.9}]};
  const tinyLayout = fresh(tiny); assertGeometry(tinyLayout); assert.equal(tinyLayout.clouds.length, 3);
  const noEdges = synthSnapshot({k: 3, total: 400, unfiled: 0, multiFraction: 0}); noEdges.edges = [];
  const noEdgeLayout = fresh(noEdges); assertGeometry(noEdgeLayout);
  assert.ok(noEdgeLayout.clouds.every(c => c.mode === 'sunflower'));
  assert.ok(fresh({papers: [{id: 'x'}], collections: [], edges: []}).clouds[0].unfiled);
});

test('packClouds guarantees the gap for arbitrary circles and puts unfiled outside the core enclosure', () => {
  const circles = [{id: 'a', r: 170, size: 300}, {id: 'b', r: 4, size: 1}, {id: 'c', r: 60, size: 40}, {id: 'd', r: 4, size: 1}, {id: 'u', r: 30, size: 16, unfiled: true}];
  const centers = packClouds(circles);
  for (let i = 0; i < circles.length; i += 1) for (let j = i + 1; j < circles.length; j += 1) {
    const a = circles[i]; const b = circles[j]; const pa = centers.get(a.id); const pb = centers.get(b.id);
    assert.ok(Math.hypot(pa.cx - pb.cx, pa.cy - pb.cy) - a.r - b.r >= GAP);
  }
  const u = centers.get('u'); const coreReach = Math.max(...circles.filter(c => !c.unfiled).map(c => Math.hypot(centers.get(c.id).cx, centers.get(c.id).cy) + c.r));
  assert.ok(Math.hypot(u.cx, u.cy) - 30 > coreReach, 'unfiled cloud lies beyond every core cloud');
});

const FIT_AREAS = [[252, 292], [652, 272], [652, 372], [982, 512]];
const wideLabel = () => 400; // every label is clipped to the 160 px maximum

test('AC-3: Fit zoom and label placement on the plan fit areas with 14 labels of 160 px', t => {
  clearCollectionCloudCache();
  const rows = [];
  for (const [width, height] of FIT_AREAS) {
    const layout = fresh(big, {fitArea: {width, height}, measure: wideLabel});
    const {fit, labels} = layout;
    assert.equal(labels.length, 14); assert.ok(labels.every(label => label.width === LABEL_WIDTH));
    assert.ok(fit.passes <= 2);
    const shown = labels.filter(label => !label.hidden);
    for (let i = 0; i < shown.length; i += 1) for (let j = i + 1; j < shown.length; j += 1) {
      const a = shown[i]; const b = shown[j];
      assert.ok(!(a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height), `labels ${a.cloudId} and ${b.cloudId} overlap at ${width}x${height}`);
    }
    // Independent point check in container coordinates (map centred, padding 24).
    const W = width + 48; const H = height + 48; let minX = Infinity; let maxX = -Infinity; let minY = Infinity; let maxY = -Infinity;
    for (const c of layout.clouds) { const r = c.r + c.reserve; minX = Math.min(minX, c.cx - r); maxX = Math.max(maxX, c.cx + r); minY = Math.min(minY, c.cy - r); maxY = Math.max(maxY, c.cy + r); }
    const screen = layout.points.map(p => ({x: W / 2 + (p.x - (minX + maxX) / 2) * fit.zoom, y: H / 2 + (p.y - (minY + maxY) / 2) * fit.zoom}));
    for (const label of shown) {
      assert.ok(label.x >= 0 && label.y >= 0 && label.x + label.width <= W && label.y + label.height <= H, `label ${label.cloudId} leaves the container`);
      assert.ok(!screen.some(s => s.x > label.x && s.x < label.x + label.width && s.y > label.y && s.y < label.y + label.height), `label ${label.cloudId} covers a point`);
    }
    const hidden = labels.length - shown.length; const leaders = shown.filter(label => label.leader).length;
    rows.push({area: `${width}x${height}`, rawZoom: fit.rawZoom, spacing: fit.spacing, reserved: fit.reserved, passes: fit.passes, hidden, leaders});
    t.diagnostic(`${width}x${height}: rawZoom=${fit.rawZoom.toFixed(3)} S=${fit.spacing} reserved=${fit.reserved} passes=${fit.passes} hidden=${hidden} leaders=${leaders}`);
  }
  const at = area => rows.find(row => row.area === area);
  // Plan §0.7 / R-fit-1..3: Fit >= 0.2 must hold on every measured fit area (S may drop to 8 by R-fit-3).
  for (const row of rows) assert.ok(row.rawZoom >= 0.2, `Fit zoom ${row.rawZoom} < 0.2 at ${row.area}`);
  // AC-3: every label shown at the 720 (R-fit-1) and 1440 widths.
  assert.equal(at('652x372').hidden, 0); assert.equal(at('982x512').hidden, 0);
});

test('AC-3: placeCloudLabels hides a label only when nothing fits and never overlaps', () => {
  const layout = {clouds: [{id: 'a', label: 'A', cx: 0, cy: 0, r: 100, size: 10}, {id: 'b', label: 'B', cx: 0, cy: 0, r: 100, size: 5}], points: []};
  const labels = placeCloudLabels(layout, 1, {width: 200, height: 200, padding: 0}, () => 160);
  assert.equal(labels.filter(label => label.hidden).length, 2, 'a disc filling the container leaves no room');
  const roomy = placeCloudLabels(layout, 0.5, {width: 400, height: 400}, () => 160);
  assert.equal(roomy.filter(label => label.hidden).length, 0);
  assert.notDeepEqual([roomy[0].x, roomy[0].y], [roomy[1].x, roomy[1].y]);
});

test('placeCloudLabels: a cloud whose centre is off-screen gets no leader label (it goes to the cloud list)', () => {
  const layout = {clouds: [{id: 'on', label: 'On', cx: 150, cy: 150, r: 60, size: 10}, {id: 'off', label: 'Off', cx: 150, cy: 700, r: 60, size: 5}], points: []};
  const panned = placeCloudLabels(layout, 1, {width: 400, height: 300, padding: 0}, () => 160, {x: 0, y: 0});
  const off = panned.find(label => label.cloudId === 'off'); const on = panned.find(label => label.cloudId === 'on');
  assert.equal(off.hidden, true); assert.equal(off.leader, false); assert.equal(on.hidden, false);
});

/** AC-5 ratio: mean distance of connected pairs / unconnected pairs inside one cloud. */
function connectedRatio(layout, snapshot, cloudId) {
  const pts = layout.points.filter(p => p.cloudId === cloudId); const ids = new Set(pts.map(p => p.paperId));
  const connected = new Set(snapshot.edges.filter(e => ids.has(e.source) && ids.has(e.target)).map(e => [e.source, e.target].sort().join('|')));
  let cs = 0; let cn = 0; let us = 0; let un = 0;
  for (let i = 0; i < pts.length; i += 1) for (let j = i + 1; j < pts.length; j += 1) { const d = Math.hypot(pts[i].x - pts[j].x, pts[i].y - pts[j].y); if (connected.has([pts[i].paperId, pts[j].paperId].sort().join('|'))) { cs += d; cn += 1; } else { us += d; un += 1; } }
  return {ratio: (cs / cn) / (us / un), n: pts.length, edges: cn};
}

test('AC-5: planted structure is reflected inside clouds (connected/unconnected distance ratio <= 0.75)', t => {
  const planted = synthSnapshot({k: 1, total: 120, unfiled: 0, multiFraction: 0, cluster: 12});
  const layout = fresh(planted); const result = connectedRatio(layout, planted, layout.clouds[0].id);
  assert.ok(result.n >= 10 && result.edges >= 10); assert.ok(result.ratio <= 0.75, `ratio ${result.ratio}`);
  const bigLayout = fresh(big);
  const ratios = bigLayout.clouds.filter(c => !c.unfiled).map(c => connectedRatio(bigLayout, big, c.id)).filter(r => r.n >= 10 && r.edges >= 10);
  for (const r of ratios) assert.ok(r.ratio <= 0.75, `ratio ${r.ratio}`);
  t.diagnostic(`planted 120: ratio=${result.ratio.toFixed(3)} edges=${result.edges}; 1,000-paper clouds (n>=10, edges>=10): max=${Math.max(...ratios.map(r => r.ratio)).toFixed(3)} over ${ratios.length} clouds`);
});

test('determinism: two runs deep-equal; shuffled papers/edges/collections give identical coordinates', () => {
  const strip = layout => ({clouds: layout.clouds, points: layout.points, labels: layout.labels, fit: layout.fit});
  assert.deepEqual(strip(fresh(big, {fitArea: {width: 652, height: 372}})), strip(fresh(big, {fitArea: {width: 652, height: 372}})));
  assert.deepEqual(rounded(fresh(shuffled(big, 5))), rounded(fresh(big)));
  assert.deepEqual(rounded(fresh(shuffled(big, 9), {fitArea: {width: 252, height: 292}})), rounded(fresh(big, {fitArea: {width: 252, height: 292}})));
});

test('memoisation: same topology returns the cached layout; a changed edge invalidates it', () => {
  clearCollectionCloudCache();
  const first = layoutCollectionClouds(big); assert.equal(layoutCollectionClouds(big), first);
  const changed = {...big, edges: big.edges.slice(1)};
  const second = layoutCollectionClouds(changed); assert.notEqual(second, first); assert.notEqual(second.signature, first.signature);
  assert.ok(Number.isFinite(first.timing.layoutMs));
});

test('subgroupLPA: deterministic, sizes sum to the unique count, small groups merge into other', () => {
  const clique = (prefix, n) => { const rows = []; for (let i = 0; i < n; i += 1) for (let j = i + 1; j < n; j += 1) rows.push({source: `${prefix}${i}`, target: `${prefix}${j}`, score: 0.9}); return rows; };
  const ids = [...Array.from({length: 5}, (_, i) => `a${i}`), ...Array.from({length: 4}, (_, i) => `b${i}`), 'c0', 'c1', 'lone'];
  const edges = [...clique('a', 5), ...clique('b', 4), {source: 'a0', target: 'b0', score: 0.81}, {source: 'c0', target: 'c1', score: 0.95}, {source: 'a1', target: 'outside', score: 1}];
  const groups = subgroupLPA([...ids, 'a0'], edges);
  assert.equal(groups.reduce((sum, g) => sum + g.size, 0), ids.length);
  assert.deepEqual(groups.map(g => [g.paperIds, g.other]), [[['a0', 'a1', 'a2', 'a3', 'a4'], false], [['b0', 'b1', 'b2', 'b3'], false], [['c0', 'c1', 'lone'], true]]);
  assert.ok(groups.filter(g => !g.other).every(g => g.size >= 3));
  const mix = lcg(4); const reversed = [...edges].sort(() => mix() - 0.5).map(e => ({source: e.target, target: e.source, score: e.score}));
  assert.deepEqual(subgroupLPA([...ids].reverse(), reversed), groups);
  const cloud = big.collections[0].paperIds; const total = subgroupLPA(cloud, big.edges).reduce((sum, g) => sum + g.size, 0);
  assert.equal(total, new Set(cloud).size);
});

test('innerLayout accepts the {pairs} similarity interface and ignores input order', () => {
  const ids = Array.from({length: 30}, (_, i) => `n${String(i).padStart(2, '0')}`);
  const pairs = ids.slice(1).map((id, i) => [ids[i], id, 0.85]);
  const a = innerLayout(ids, {pairs}, 0.5); const b = innerLayout([...ids].reverse(), pairs.map(([s, t, w]) => ({source: t, target: s, score: w})).reverse(), 0.5);
  assert.equal(a.mode, 'force');
  assert.deepEqual([...a.positions].map(([id, p]) => [id, p.x.toFixed(2), p.y.toFixed(2)]).sort(), [...b.positions].map(([id, p]) => [id, p.x.toFixed(2), p.y.toFixed(2)]).sort());
});
