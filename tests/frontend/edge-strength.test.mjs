import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {midRankPercentiles, assignTiers, semanticStrength, keywordStrength, citationEdges, cloudBridges, edgesForState, tierLegend, EDGE_DRAW_CAP} from '../../frontend/edge-strength.js';
import {decodeSnapshot} from '../../frontend/decoder.js';
import {sampleDetail} from '../../frontend/sampling.js';
import {graphProjection} from '../../frontend/projection.js';
import {cloudGroups, layoutCollectionClouds, UNFILED_CLOUD_ID} from '../../frontend/collection-clouds.js';

// Synthetic data only.
const lcg = seed => { let s = seed >>> 0; return () => { s = (Math.imul(s, 1664525) + 1013904223) >>> 0; return s / 4294967296; }; };
const fixture = async name => JSON.parse(await readFile(new URL(`../fixtures/${name}`, import.meta.url), 'utf8'));
const near = (actual, expected, message) => assert.ok(Math.abs(actual - expected) < 1e-12, `${message}: ${actual} != ${expected}`);
const sem = (id, source, target, score) => ({id, source, target, type: 'semantic', directed: false, score});
const paper = (id, conceptIds = []) => ({id, conceptIds});
const view = result => Object.fromEntries(result.edges.map(row => [row.id, [row.strength, row.tier, row.mutualRank, row.jaccard, row.sharedConcepts]]));
const mix = (rows, rand) => { const out = [...rows]; for (let i = out.length - 1; i > 0; i -= 1) { const j = Math.floor(rand() * (i + 1)); [out[i], out[j]] = [out[j], out[i]]; } return out; };
const shuffledSnapshot = (snapshot, seed) => { const rand = lcg(seed); return {...snapshot, papers: mix(snapshot.papers, rand).map(p => ({...p, conceptIds: mix(p.conceptIds, rand)})), edges: mix(snapshot.edges, rand).map(e => (rand() < 0.5 ? {...e, source: e.target, target: e.source} : {...e}))}; };

// Hand graph: A{x,y} B{x,y} C{x,z} D{} E{}; degrees A2 B2 C3 D2 E1.
const hand = () => ({
  papers: [paper('A', ['x', 'y']), paper('B', ['x', 'y']), paper('C', ['x', 'z']), paper('D'), paper('E')],
  edges: [sem('e1', 'A', 'B', 0.9), sem('e2', 'A', 'C', 0.8), sem('e3', 'B', 'C', 0.8), sem('e4', 'C', 'D', 0.7), sem('e5', 'D', 'E', 0.6)],
  concepts: [], collections: []
});

test('ME-1 hand-computed mutual rank, Jaccard, percentiles and tiers', () => {
  const result = semanticStrength(hand()); const row = id => result.byId.get(id);
  // Per-paper normalised ranks (rank - 0.5) / degree, worse endpoint: e1 max(.25,.25), e2 max(.75,1/3), e3 max(.75,1/3), e4 max(2.5/3,.25), e5 max(.75,.5).
  near(row('e1').mutualRank, 0.25, 'e1 rank'); near(row('e2').mutualRank, 0.75, 'e2 rank'); near(row('e3').mutualRank, 0.75, 'e3 rank'); near(row('e4').mutualRank, 2.5 / 3, 'e4 rank'); near(row('e5').mutualRank, 0.75, 'e5 rank');
  assert.deepEqual(['e1', 'e2', 'e3', 'e4', 'e5'].map(id => [row(id).jaccard, row(id).sharedConcepts]), [[1, 2], [1 / 3, 1], [1 / 3, 1], [0, 0], [null, 0]]);
  // rank percentiles (mid-rank - 0.5)/5: e1 .1, e2/e3/e5 .5, e4 .9 -> inverted .9/.5/.1; Jaccard percentiles over 4 defined: e4 .125, e2/e3 .5, e1 .875.
  near(row('e1').strength, (0.9 + 0.875) / 2, 'e1'); near(row('e2').strength, 0.5, 'e2'); near(row('e3').strength, 0.5, 'e3'); near(row('e4').strength, (0.1 + 0.125) / 2, 'e4'); near(row('e5').strength, 0.5, 'e5 rank only');
  // Targets 1.0 and 3.5; tie block {e2,e3,e5} spans positions 1..3 -> cuts 1 and 4.
  assert.deepEqual(Object.fromEntries(result.edges.map(r => [r.id, r.tier])), {e1: 'strong', e2: 'medium', e3: 'medium', e5: 'medium', e4: 'weak'});
  assert.deepEqual(result.counts, {strong: 1, medium: 3, weak: 1});
  assert.deepEqual([result.boundaries.strongEnd, result.boundaries.mediumEnd], [1, 4]);
  assert.deepEqual(result.edges.map(r => r.id), ['e1', 'e2', 'e3', 'e5', 'e4']);
  assert.deepEqual(midRankPercentiles([0.25, 0.75, 0.75, 2.5 / 3, 0.75]), [0.1, 0.5, 0.5, 0.9, 0.5]);
});

test('ME-1 degree-1 paper keeps 0.5 and its only edge is not forced weakest', () => {
  // E has degree 1: its own normalised rank is 0.5, so e5 is bounded by D's 0.75 and lands medium, above e4.
  const result = semanticStrength(hand());
  assert.equal(result.byId.get('e5').tier, 'medium'); assert.ok(result.byId.get('e5').strength > result.byId.get('e4').strength);
  // A leaf with the hub's strongest edge: max(hub 0.5/4, leaf 0.5) = 0.5, never the 1 - 0.5/deg worst slot.
  const star = {papers: ['H', 'L1', 'L2', 'L3', 'L4'].map(id => paper(id)), edges: [sem('s1', 'H', 'L1', 0.9), sem('s2', 'H', 'L2', 0.8), sem('s3', 'H', 'L3', 0.7), sem('s4', 'H', 'L4', 0.6)]};
  const s = semanticStrength(star); near(s.byId.get('s1').mutualRank, 0.5, 'leaf bound'); near(s.byId.get('s4').mutualRank, 3.5 / 4, 'hub worst');
  assert.notEqual(s.byId.get('s1').tier, 'weak');
});

test('ME-1 deterministic under shuffled papers, edges, concept order and endpoint swaps', () => {
  const base = view(semanticStrength(hand()));
  for (const seed of [1, 2, 3, 4, 5]) assert.deepEqual(view(semanticStrength(shuffledSnapshot(hand(), seed))), base);
  const big = plantedCommunities(); const bigView = view(semanticStrength(big));
  for (const seed of [7, 8]) assert.deepEqual(view(semanticStrength(shuffledSnapshot(big, seed))), bigView);
});

test('ME-1 tie-block boundaries: nearest block edge, equal deviation goes to the lower tier, emptied tiers', () => {
  const rows = values => values.map((strength, i) => ({id: `r${String(i).padStart(2, '0')}`, strength}));
  const tiers = values => { const {order, tierOf} = assignTiers(rows(values)); return order.map(r => tierOf.get(r.id)[0]).join(''); };
  // n=10, target 2 inside block at positions 1..2 -> cuts 1 (dev 1) or 3 (dev 1): equal -> block goes medium.
  assert.equal(tiers([5, 4, 4, 3, 3, 3, 3, 2, 1, 0]), 'smmmmmmwww');
  // block 0..2 -> cut 0 (dev 2) vs 3 (dev 1): end wins, strong grows to 3.
  assert.equal(tiers([9, 9, 9, 8, 7, 6, 5, 4, 3, 2]), 'sssmmmmwww');
  // block 1..3 -> cut 1 (dev 1) vs 4 (dev 2): start wins.
  assert.equal(tiers([9, 8, 8, 8, 7, 6, 5, 4, 3, 2]), 'smmmmmmwww');
  // target 7 inside block 5..8 -> cut 5 (dev 2) vs 9 (dev 2): equal -> block goes weak.
  assert.equal(tiers([9, 8, 7, 6, 5, 4, 4, 4, 4, 1]), 'ssmmmwwwww');
  // One block -> strong and weak empty; legend lists medium only.
  const flat = assignTiers(rows(new Array(10).fill(0.5))); assert.deepEqual(flat.counts, {strong: 0, medium: 10, weak: 0}); assert.deepEqual(tierLegend(flat), [{tier: 'medium', count: 10}]);
  assert.equal(tiers([1, 1, 1, 1, 1, 1, 1, 1, 0, 0]), 'mmmmmmmmww');
  // Symmetric ring with equal scores and identical concepts: every edge ties -> all medium.
  const ring = {papers: Array.from({length: 8}, (_, i) => paper(`R${i}`, ['k'])), edges: Array.from({length: 8}, (_, i) => sem(`ring${i}`, `R${i}`, `R${(i + 1) % 8}`, 0.9))};
  assert.deepEqual(semanticStrength(ring).counts, {strong: 0, medium: 8, weak: 0});
});

test('ME-1 single edge is medium, zero edges are empty, repo fixture', async () => {
  const one = semanticStrength({papers: [paper('A'), paper('B')], edges: [sem('only', 'A', 'B', 0.3)]});
  assert.equal(one.byId.get('only').tier, 'medium'); assert.deepEqual(one.counts, {strong: 0, medium: 1, weak: 0});
  const none = semanticStrength({papers: [paper('A')], edges: []}); assert.deepEqual([none.edges, none.counts], [[], {strong: 0, medium: 0, weak: 0}]); assert.deepEqual(tierLegend(none), []);
  const snapshot = decodeSnapshot(await fixture('snapshot.json')); const result = semanticStrength(snapshot);
  assert.equal(result.edges.length, 1); assert.equal(result.edges[0].tier, 'medium'); assert.deepEqual(tierLegend(result), [{tier: 'medium', count: 1}]);
  assert.deepEqual(keywordStrength(snapshot).edges, [], 'the fixture keyword edge joins papers, not concepts');
});

test('ME-1 undefined Jaccard falls back to the rank indicator only', () => {
  // All concept-less: strength = inverted rank percentile; with D/E concept-less in the hand graph e5 already uses rank only.
  const bare = {papers: ['A', 'B', 'C'].map(id => paper(id)), edges: [sem('ab', 'A', 'B', 0.9), sem('bc', 'B', 'C', 0.5), sem('ac', 'A', 'C', 0.7)]};
  const r = semanticStrength(bare);
  for (const row of r.edges) assert.equal(row.jaccard, null);
  // ranks: ab max(.25,.25)=.25, ac max(.75,.25)=.75, bc max(.75,.75)=.75 -> percentiles 1/6, 4/6, 4/6 -> strengths 5/6, 1/3, 1/3.
  near(r.byId.get('ab').strength, 5 / 6, 'ab'); near(r.byId.get('ac').strength, 1 / 3, 'ac'); near(r.byId.get('bc').strength, 1 / 3, 'bc');
});

test('ME-1 independent of detail fraction and of non-eligible edges', async () => {
  const raw = await fixture('demo-snapshot.json'); const snapshot = decodeSnapshot(raw); const reference = view(semanticStrength(snapshot));
  for (const fraction of [0.2, 0.5, 1]) {
    sampleDetail(snapshot, {fraction});
    assert.deepEqual(view(semanticStrength(decodeSnapshot(raw))), reference, `fraction ${fraction}`);
  }
  const detail = sampleDetail(snapshot, {fraction: 0.2}); const sampled = semanticStrength({...snapshot, papers: detail.papers, edges: detail.edges});
  assert.ok(sampled.edges.length < Object.keys(reference).length, 'a sampled snapshot would differ, so the full-snapshot rule matters');
  const base = hand(); const noisy = {...base, edges: [...base.edges, {id: 'c1', source: 'A', target: 'B', type: 'citation', directed: true, verified: true, score: 1}, {id: 'k1', source: 'x', target: 'y', type: 'keyword', score: 1}, sem('ghost', 'A', 'Z-inactive', 0.99), sem('loop', 'A', 'A', 0.99), {id: 'r1', source: 'A', target: 'B', type: 'related', score: 1}]};
  assert.deepEqual(view(semanticStrength(noisy)), view(semanticStrength(base)));
});

// Planted communities: 6 x 50 papers; concepts and scores both carry a noisy community signal.
function plantedCommunities({k = 6, size = 50, seed = 42} = {}) {
  const rand = lcg(seed); const papers = []; const community = new Map();
  for (let c = 0; c < k; c += 1) for (let i = 0; i < size; i += 1) {
    const id = `P${c}-${String(i).padStart(3, '0')}`; community.set(id, c); const concepts = new Set();
    for (let d = 0; d < 5; d += 1) concepts.add(rand() < 0.55 ? `c${c}-${Math.floor(rand() * 10)}` : `g-${Math.floor(rand() * 40)}`);
    papers.push(paper(id, [...concepts]));
  }
  const edges = []; const seen = new Set();
  for (const p of papers) for (let d = 0; d < 5; d += 1) {
    const sameWanted = rand() < 0.6; const pool = sameWanted ? papers.filter(q => community.get(q.id) === community.get(p.id)) : papers; const q = pool[Math.floor(rand() * pool.length)];
    if (q.id === p.id) continue; const key = p.id < q.id ? `${p.id}|${q.id}` : `${q.id}|${p.id}`; if (seen.has(key)) continue; seen.add(key);
    const same = community.get(p.id) === community.get(q.id); edges.push(sem(`edge:${key}`, p.id, q.id, Number((0.82 + (same ? 0.03 : 0) + 0.07 * rand()).toFixed(4))));
  }
  return {papers, edges, community};
}

test('ME-2 planted communities: same-community rate strictly strong > medium > weak', () => {
  const graph = plantedCommunities(); const result = semanticStrength(graph); const stats = {strong: [0, 0], medium: [0, 0], weak: [0, 0]};
  for (const row of result.edges) { stats[row.tier][1] += 1; if (graph.community.get(row.source) === graph.community.get(row.target)) stats[row.tier][0] += 1; }
  const rate = tier => stats[tier][0] / stats[tier][1];
  assert.ok(result.edges.length > 1000, `edges ${result.edges.length}`); for (const tier of ['strong', 'medium', 'weak']) assert.ok(stats[tier][1] > 50, `${tier} size`);
  assert.ok(rate('strong') > rate('medium') && rate('medium') > rate('weak'), `rates ${rate('strong')} / ${rate('medium')} / ${rate('weak')}`);
});

// ---- ME-4 display rules on hand-built layouts ----
const pt = (cloudId, paperId, x, y) => ({id: `overview-point:${cloudId === UNFILED_CLOUD_ID ? 'unfiled' : cloudId}:${paperId}`, paperId, cloudId, x, y});
const cl = (id, extra = {}) => ({id, cx: 0, cy: 0, r: 10, unfiled: id === UNFILED_CLOUD_ID, ...extra});
// Hand graph placed in clouds: A in C1+C2, B in C1, C in C2, D in C2+C3, E unfiled.
const handLayout = () => ({clouds: [cl('C1'), cl('C2'), cl('C3'), cl(UNFILED_CLOUD_ID)], points: [pt('C1', 'A', 0, 0), pt('C2', 'A', 100, 0), pt('C1', 'B', 5, 0), pt('C2', 'C', 105, 0), pt('C2', 'D', 110, 0), pt('C3', 'D', 0, 100), pt(UNFILED_CLOUD_ID, 'E', 200, 200)]});
const idle = {cloudId: null, paperId: null, pointId: null, subItemPaperIds: null};

test('ME-4 idle, cloud, sub-item and paper states', () => {
  const snapshot = hand(); const layout = handLayout();
  const rest = edgesForState(snapshot, layout, idle); assert.deepEqual(rest.edges, []); assert.deepEqual(rest.counters, {displayedEdges: 0, displayedBridges: rest.bridges.length});
  const c1 = edgesForState(snapshot, layout, {...idle, cloudId: 'C1'});
  assert.deepEqual(c1.edges.map(e => [e.id, e.tier, e.fromPointId, e.toPointId, e.intraCloud]), [['e1', 'strong', 'overview-point:C1:A', 'overview-point:C1:B', true]]);
  assert.deepEqual(edgesForState(snapshot, layout, {...idle, cloudId: 'C2'}).edges, [], 'only strong edges with both ends in the cloud');
  const sub = edgesForState(snapshot, layout, {...idle, cloudId: 'C1', subItemPaperIds: ['A']}); assert.equal(sub.edges[0].dimmed, true);
  assert.equal(edgesForState(snapshot, layout, {...idle, cloudId: 'C1', subItemPaperIds: ['A', 'B']}).edges[0].dimmed, false);
  // Paper A, no point and no cloud: first point by id (C1); B has a C1 replica, C only C2.
  const a = edgesForState(snapshot, layout, {...idle, paperId: 'A'});
  assert.deepEqual(a.edges.map(e => [e.id, e.tier, e.fromPointId, e.toPointId, e.intraCloud]), [['e1', 'strong', 'overview-point:C1:A', 'overview-point:C1:B', true], ['e2', 'medium', 'overview-point:C1:A', 'overview-point:C2:C', false]]);
  assert.deepEqual(a.counters, {displayedEdges: 2, displayedBridges: a.bridges.length}); assert.ok(a.bridges.every(b => !b.emphasis && b.dimmed), 'paper state: every bridge dimmed');
  // Paper rule wins over a selected cloud; start = A's point in that cloud.
  const a2 = edgesForState(snapshot, layout, {...idle, cloudId: 'C2', paperId: 'A'});
  assert.deepEqual(a2.edges.map(e => [e.id, e.fromPointId, e.toPointId, e.intraCloud]), [['e1', 'overview-point:C2:A', 'overview-point:C1:B', false], ['e2', 'overview-point:C2:A', 'overview-point:C2:C', true]]);
  assert.deepEqual(edgesForState(snapshot, layout, {...idle, cloudId: 'C1', paperId: 'A', pointId: 'overview-point:C2:A'}).edges.map(e => e.fromPointId), ['overview-point:C2:A', 'overview-point:C2:A'], 'explicit pointId wins');
  // All three tiers for paper C (e3 medium, e2 medium, e4 weak); D attaches to its C2 replica (same cloud as start).
  const c = edgesForState(snapshot, layout, {...idle, paperId: 'C'});
  assert.deepEqual(c.edges.map(e => [e.id, e.tier, e.toPointId]), [['e2', 'medium', 'overview-point:C2:A'], ['e3', 'medium', 'overview-point:C1:B'], ['e4', 'weak', 'overview-point:C2:D']]);
  assert.deepEqual(tierLegend(c), [{tier: 'medium', count: 2}, {tier: 'weak', count: 1}]);
});

test('ME-4 replica rule: same cloud first, else nearest, distance tie -> cloud id; one line per paper pair', () => {
  const snapshot = {papers: ['X', 'Y', 'Z'].map(id => paper(id)), edges: [sem('xy', 'X', 'Y', 0.9), sem('xz', 'X', 'Z', 0.8), sem('zy', 'Z', 'Y', 0.7), sem('xy-dup', 'Y', 'X', 0.5)]};
  const tie = {clouds: [cl('C1'), cl('C2'), cl('C3')], points: [pt('C1', 'X', 0, 0), pt('C3', 'Y', 80, 60), pt('C2', 'Y', 60, 80), pt('C1', 'Z', 3, 4), pt('C2', 'Z', 200, 0)]};
  const x = edgesForState(snapshot, tie, {...idle, paperId: 'X'});
  assert.deepEqual(x.edges.map(e => [e.id, e.toPointId]), [['xy', 'overview-point:C2:Y'], ['xz', 'overview-point:C1:Z']], 'equal distance 100 -> C2 < C3; duplicate pair drawn once (strongest)');
  const closer = {...tie, points: tie.points.map(p => (p.id === 'overview-point:C3:Y' ? {...p, x: 70, y: 60} : p))};
  assert.equal(edgesForState(snapshot, closer, {...idle, paperId: 'X'}).edges[0].toPointId, 'overview-point:C3:Y');
  // Start Z@C2: Y has a C2 replica -> used even though Y@C3 is nearer; X only in C1.
  const z = edgesForState(snapshot, {...tie, points: [...tie.points.filter(p => p.id !== 'overview-point:C3:Y'), pt('C3', 'Y', 199, 0)]}, {...idle, paperId: 'Z', pointId: 'overview-point:C2:Z'});
  assert.deepEqual(z.edges.map(e => [e.id, e.fromPointId, e.toPointId]).sort(), [['xz', 'overview-point:C2:Z', 'overview-point:C1:X'], ['zy', 'overview-point:C2:Z', 'overview-point:C2:Y']]);
});

// All edges equal score, no concepts -> one tie block -> every edge medium, so bridge weights are exact edge counts.
function bridgeFixture() {
  const papers = []; const edges = []; const groups = new Map(); let n = 0;
  const join = (cloudsU, cloudsV) => { const u = `u${n}`; const v = `v${n}`; n += 1; papers.push(paper(u), paper(v)); for (const c of cloudsU) (groups.get(c) || groups.set(c, []).get(c)).push(u); for (const c of cloudsV) (groups.get(c) || groups.set(c, []).get(c)).push(v); edges.push(sem(`b${String(n).padStart(3, '0')}`, u, v, 0.9)); };
  const weights = [['K03', 'K04', 9], ['K05', 'K06', 8], ['K07', 'K08', 7], ['K09', 'K10', 6], ['K11', 'K12', 5], ['K03', 'K05', 4], ['K03', 'K06', 4], ['K03', 'K07', 3], ['K04', 'K05', 3], ['K04', 'K06', 3], ['K04', 'K07', 3], ['K05', 'K07', 3], ['K06', 'K07', 2], [UNFILED_CLOUD_ID, 'K03', 5]];
  for (const [a, b, w] of weights) for (let i = 0; i < w; i += 1) join([a], [b]);
  for (let i = 0; i < 3; i += 1) join(['K01', 'K02'], ['K01', 'K02']); // both ends in both clouds: {K01,K02} once per edge
  const list = [...groups].map(([id, paperIds]) => ({id, paperIds, unfiled: id === UNFILED_CLOUD_ID})).sort((a, b) => (a.id < b.id ? -1 : 1));
  return {snapshot: {papers, edges}, groups: list};
}

test('ME-4 bridge aggregation: once per pair, unfiled excluded, threshold, top-12 ties, tercile ties', () => {
  const {snapshot, groups} = bridgeFixture(); assert.equal(semanticStrength(snapshot).counts.medium, snapshot.edges.length);
  const bridges = cloudBridges(snapshot, groups);
  assert.deepEqual(bridges.map(b => [b.id, b.weight, b.level]), [
    ['bridge:K03|K04', 9, 3], ['bridge:K05|K06', 8, 3], ['bridge:K07|K08', 7, 3], ['bridge:K09|K10', 6, 3], ['bridge:K11|K12', 5, 2], ['bridge:K03|K05', 4, 2], ['bridge:K03|K06', 4, 2],
    ['bridge:K01|K02', 3, 1], ['bridge:K03|K07', 3, 1], ['bridge:K04|K05', 3, 1], ['bridge:K04|K06', 3, 1], ['bridge:K04|K07', 3, 1]
  ]); // K05|K07 (3) drops on pair id; K06|K07 (2) below threshold; unfiled pair absent; all five 3s stay level 1 across the tercile index.
  // Same answer through a real layout's points (edgesForState) and through cloudGroups.
  const collections = groups.filter(g => !g.unfiled).map(g => ({id: g.id, label: g.id, paperIds: g.paperIds}));
  const layout = layoutCollectionClouds({...snapshot, collections}, {useMemo: false});
  const state = edgesForState(snapshot, layout, idle); assert.deepEqual(state.bridges.map(b => [b.id, b.weight, b.level]), bridges.map(b => [b.id, b.weight, b.level]));
  assert.deepEqual(cloudBridges(snapshot, cloudGroups({...snapshot, collections})).map(b => b.id), bridges.map(b => b.id));
  assert.deepEqual(state.counters, {displayedEdges: 0, displayedBridges: 12});
  const k03 = edgesForState(snapshot, layout, {...idle, cloudId: 'K03'});
  assert.deepEqual(k03.bridges.filter(b => b.emphasis).map(b => b.id), ['bridge:K03|K04', 'bridge:K03|K05', 'bridge:K03|K06', 'bridge:K03|K07']);
  assert.ok(k03.bridges.every(b => b.emphasis !== b.dimmed)); assert.equal(k03.bridges.length, 12);
  // Weak edges never contribute: hand graph e4 (C-D) is weak.
  const weakOnly = cloudBridges(hand(), [{id: 'G1', paperIds: ['C']}, {id: 'G2', paperIds: ['D']}, {id: 'G3', paperIds: ['A']}, {id: 'G4', paperIds: ['B']}], {minWeight: 1});
  assert.deepEqual(weakOnly.map(b => b.id), ['bridge:G1|G3', 'bridge:G1|G4', 'bridge:G3|G4']);
});

test('ME-4 lines + bridges never exceed the cap; capped by tier, strength, id', () => {
  const leaves = Array.from({length: 1100}, (_, i) => `L${String(i).padStart(4, '0')}`);
  const snapshot = {papers: [paper('H'), ...leaves.map(id => paper(id))], edges: leaves.map((id, i) => sem(`h-${id}`, 'H', id, 0.5 + (i % 97) / 200))};
  const all = semanticStrength(snapshot).edges; const expected = [...all].sort((a, b) => ['strong', 'medium', 'weak'].indexOf(a.tier) - ['strong', 'medium', 'weak'].indexOf(b.tier) || b.strength - a.strength || (a.id < b.id ? -1 : 1));
  const one = {clouds: [cl('C1')], points: [pt('C1', 'H', 0, 0), ...leaves.map((id, i) => pt('C1', id, i, 1))]};
  const r1 = edgesForState(snapshot, one, {...idle, paperId: 'H'});
  assert.equal(r1.capped, true); assert.deepEqual(r1.counters, {displayedEdges: EDGE_DRAW_CAP, displayedBridges: 0}); assert.deepEqual(r1.edges.map(e => e.id), expected.slice(0, EDGE_DRAW_CAP).map(e => e.id));
  const two = {clouds: [cl('C1'), cl('C2')], points: [pt('C1', 'H', 0, 0), ...leaves.map((id, i) => pt(i % 2 ? 'C2' : 'C1', id, i, 1))]};
  const r2 = edgesForState(snapshot, two, {...idle, paperId: 'H'});
  assert.equal(r2.bridges.length, 1); assert.equal(r2.counters.displayedEdges + r2.counters.displayedBridges, EDGE_DRAW_CAP); assert.equal(r2.capped, true);
  // Cloud state: complete graph of 110 papers -> ~1,200 strong edges inside one cloud.
  const ids = Array.from({length: 110}, (_, i) => `Q${String(i).padStart(3, '0')}`); const rand = lcg(9); const edges = [];
  for (let i = 0; i < ids.length; i += 1) for (let j = i + 1; j < ids.length; j += 1) edges.push(sem(`q${i}-${j}`, ids[i], ids[j], Number(rand().toFixed(5))));
  const dense = {papers: ids.map(id => paper(id)), edges}; assert.ok(semanticStrength(dense).counts.strong > EDGE_DRAW_CAP);
  const r3 = edgesForState(dense, {clouds: [cl('C1')], points: ids.map((id, i) => pt('C1', id, i, 0))}, {...idle, cloudId: 'C1'});
  assert.equal(r3.capped, true); assert.equal(r3.counters.displayedEdges, EDGE_DRAW_CAP); assert.ok(r3.edges.every(e => e.tier === 'strong'));
  assert.equal(edgesForState(snapshot, one, {...idle, paperId: 'L0001'}).capped, false);
});

test('ME-5 citation edges never tiered, types never merged, bridges count semantic only', () => {
  const base = hand(); const cite = (id, s, t, verified) => ({id, source: s, target: t, type: 'citation', directed: true, verified, score: 1});
  const snapshot = {...base, edges: [...base.edges, cite('c-ab', 'A', 'B', true), cite('c-cd', 'C', 'D', false), {id: 'kw', source: 'A', target: 'B', type: 'keyword', score: 1}]};
  assert.deepEqual(citationEdges(snapshot), [{id: 'c-ab', type: 'citation', source: 'A', target: 'B', directed: true, verified: true, tier: null}, {id: 'c-cd', type: 'citation', source: 'C', target: 'D', directed: true, verified: false, tier: null}]);
  const ids = semanticStrength(snapshot).edges.map(e => e.id).sort(); assert.deepEqual(ids, ['e1', 'e2', 'e3', 'e4', 'e5']);
  assert.ok(semanticStrength(snapshot).edges.every(e => e.type === 'semantic'));
  const drawn = edgesForState(snapshot, handLayout(), {...idle, paperId: 'A'}).edges; assert.ok(drawn.every(e => e.type === 'semantic' && ids.includes(e.id)));
  // Citation-only cross-cloud traffic never forms a bridge.
  const papers = Array.from({length: 10}, (_, i) => paper(`p${i}`)); const citeOnly = {papers, edges: Array.from({length: 5}, (_, i) => cite(`c${i}`, `p${i}`, `p${i + 5}`, true))};
  assert.deepEqual(cloudBridges(citeOnly, [{id: 'K1', paperIds: ['p0', 'p1', 'p2', 'p3', 'p4']}, {id: 'K2', paperIds: ['p5', 'p6', 'p7', 'p8', 'p9']}], {minWeight: 1}), []);
});

test('keyword tiers reuse the snapshot co-occurrence aggregate and graphProjection edge set', async () => {
  const snapshot = decodeSnapshot(await fixture('demo-snapshot.json')); const result = keywordStrength(snapshot);
  const projected = graphProjection(snapshot, null, {maxConceptNodes: 10000}).conceptEdges.filter(e => e.data.relation === 'concept-cooccurrence');
  assert.deepEqual(result.edges.map(e => e.id).sort(), projected.map(e => e.data.id).sort(), 'same edge ids as the projection cache');
  const aggregate = snapshot.overview.keywordAggregate; const countOf = new Map(aggregate.pairs.map(([a, b, c]) => [[aggregate.conceptIds[a], aggregate.conceptIds[b]].sort().join('|'), c]));
  for (const row of result.edges) assert.equal(row.cooccurrenceCount, countOf.get([row.source, row.target].sort().join('|')) ?? 1, row.id);
  const byProjection = new Map(projected.map(e => [e.data.id, e.data.count])); for (const row of result.edges.filter(r => r.id.startsWith('keyword-aggregate:'))) assert.equal(row.cooccurrenceCount, byProjection.get(row.id));
  const order = ['strong', 'medium', 'weak']; for (const a of result.edges) for (const b of result.edges) if (a.cooccurrenceCount > b.cooccurrenceCount) assert.ok(order.indexOf(a.tier) <= order.indexOf(b.tier), `${a.id} vs ${b.id}`);
  assert.ok(result.counts.strong > 0 && result.counts.weak > 0);
});

test('memo returns the same object per snapshot and recomputes for a new snapshot', () => {
  const s = hand(); assert.equal(semanticStrength(s), semanticStrength(s)); assert.equal(keywordStrength(s), keywordStrength(s)); assert.equal(citationEdges(s), citationEdges(s));
  const other = hand(); assert.notEqual(semanticStrength(other), semanticStrength(s)); assert.deepEqual(view(semanticStrength(other)), view(semanticStrength(s)));
  s.edges = s.edges.slice(0, 1); assert.equal(semanticStrength(s).edges.length, 1, 'replacing the edges array invalidates the entry');
});
