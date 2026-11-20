import {PAPER_EDGE_CAP, keywordCooccurrence} from './projection.js';

/**
 * Pure edge-strength engine (no DOM).  Semantic paper–paper edges get a relative
 * strong / medium / weak tier from two indicators (mutual rank + shared-concept
 * Jaccard, mid-rank percentiles); concept–concept keyword edges get tiers from the
 * snapshot's co-occurrence counts; citation edges are never tiered.  Every result is
 * computed from the FULL snapshot (never from a detail sample) and memoised per
 * snapshot object.  `edgesForState` is the single source of what the collection map draws.
 */
export const TIERS = Object.freeze(['strong', 'medium', 'weak']);
export const STRONG_SHARE = 0.2; // cumulative share ending the strong tier
export const MEDIUM_SHARE = 0.7; // cumulative share ending the medium tier
export const BRIDGE_MIN_WEIGHT = 3;
export const BRIDGE_LIMIT = 12;
export const BRIDGE_LEVELS = 3;
export const EDGE_DRAW_CAP = PAPER_EDGE_CAP; // paper lines + bridges per screen
/** Plan §2.2 tier encoding shared by every renderer (overlay SVG, Cytoscape, G6): screen-px width, opacity, dash (weak only); draw order weak -> medium -> strong. */
export const TIER_STYLE = Object.freeze({strong: Object.freeze({width: 2.4, opacity: 0.9, dash: null}), medium: Object.freeze({width: 1.4, opacity: 0.55, dash: null}), weak: Object.freeze({width: 0.8, opacity: 0.28, dash: Object.freeze([2, 3])})});
export const PAINT_ORDER = Object.freeze(['weak', 'medium', 'strong']);

const cmp = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
const tierIndex = tier => TIERS.indexOf(tier);
const scoreOf = edge => { const n = Number(edge.score); return edge.score !== null && Number.isFinite(n) ? n : 0; };
const memo = new WeakMap();
const layoutMemo = new WeakMap();

// Doubled mid-ranks minus one (odd integers 1..2n-1), ascending; equal values share the average rank.
function oddRanks(values) {
  const n = values.length; const order = values.map((value, i) => i).sort((a, b) => values[a] - values[b]); const out = new Array(n);
  for (let i = 0; i < n;) { let j = i; while (j + 1 < n && values[order[j + 1]] === values[order[i]]) j += 1; for (let k = i; k <= j; k += 1) out[order[k]] = i + j + 1; i = j + 1; }
  return out;
}

/** Mid-rank percentiles `(midRank - 0.5) / n` of `values` (ascending; equal values share the average rank). */
export const midRankPercentiles = values => oddRanks(values).map(a => a / (2 * values.length));

/**
 * Tier rows `{id, strength}`: strength desc (id asc only orders a tie block), cuts at the
 * block boundaries nearest to 20 % / 70 % of n; equal deviation -> the block goes to the lower tier.
 * @returns {{order:Array, tierOf:Map<string,string>, boundaries:{strongEnd,mediumEnd,strongTarget,mediumTarget}, counts:{strong,medium,weak}}}
 */
export function assignTiers(rows) {
  const order = [...rows].sort((a, b) => b.strength - a.strength || cmp(String(a.id), String(b.id))); const n = order.length;
  const cuts = [0]; for (let k = 1; k < n; k += 1) if (order[k].strength !== order[k - 1].strength) cuts.push(k); if (n) cuts.push(n);
  const nearest = target => cuts.reduce((best, k) => (Math.abs(k - target) < Math.abs(best - target) - 1e-9 ? k : best), cuts[0]);
  const strongTarget = n * STRONG_SHARE; const mediumTarget = n * MEDIUM_SHARE; const strongEnd = nearest(strongTarget); const mediumEnd = Math.max(strongEnd, nearest(mediumTarget));
  const tierOf = new Map(); order.forEach((row, k) => tierOf.set(row.id, k < strongEnd ? 'strong' : k < mediumEnd ? 'medium' : 'weak'));
  return {order, tierOf, boundaries: {strongEnd, mediumEnd, strongTarget, mediumTarget}, counts: {strong: strongEnd, medium: mediumEnd - strongEnd, weak: n - mediumEnd}};
}

function cacheFor(snapshot) {
  let cached = memo.get(snapshot);
  if (!cached || cached.edges !== snapshot.edges || cached.papers !== snapshot.papers || cached.concepts !== snapshot.concepts || cached.overview !== snapshot.overview) { cached = {edges: snapshot.edges, papers: snapshot.papers, concepts: snapshot.concepts, overview: snapshot.overview}; memo.set(snapshot, cached); }
  return cached;
}

/**
 * Semantic tiers over every `type === 'semantic'` edge whose endpoints are both active papers.
 * Edge rows: {id, type:'semantic', source, target, score, mutualRank, jaccard|null, sharedConcepts, strength, tier}.
 * @returns {{edges:Array (strength desc, id asc), byId:Map, boundaries, counts}}
 */
export function semanticStrength(snapshot) {
  const cached = cacheFor(snapshot); if (cached.semantic) return cached.semantic;
  const concepts = new Map((snapshot.papers || []).map(paper => [String(paper.id), new Set((paper.conceptIds || []).map(String))]));
  const eligible = (snapshot.edges || []).filter(edge => edge.type === 'semantic' && String(edge.source) !== String(edge.target) && concepts.has(String(edge.source)) && concepts.has(String(edge.target)));
  const incident = new Map();
  for (const edge of eligible) for (const end of [String(edge.source), String(edge.target)]) (incident.get(end) || incident.set(end, []).get(end)).push(edge);
  const worst = new Map();
  for (const edges of incident.values()) {
    const sorted = [...edges].sort((a, b) => scoreOf(b) - scoreOf(a)); const degree = sorted.length;
    for (let i = 0; i < degree;) { let j = i; while (j + 1 < degree && scoreOf(sorted[j + 1]) === scoreOf(sorted[i])) j += 1; const normalised = ((i + j) / 2 + 1 - 0.5) / degree; for (let k = i; k <= j; k += 1) worst.set(sorted[k], Math.max(worst.get(sorted[k]) ?? 0, normalised)); i = j + 1; }
  }
  const rows = eligible.map(edge => {
    const a = concepts.get(String(edge.source)); const b = concepts.get(String(edge.target)); let shared = 0; for (const id of a) if (b.has(id)) shared += 1; const union = a.size + b.size - shared;
    return {id: String(edge.id), type: 'semantic', source: String(edge.source), target: String(edge.target), score: scoreOf(edge), mutualRank: worst.get(edge), jaccard: union ? shared / union : null, sharedConcepts: shared, strength: 0, tier: null};
  });
  // Exact arithmetic: percentile = odd/(2n), so strength = integer / (4nm) and equal reals stay equal floats (no tie split by rounding).
  const n = rows.length; const withJaccard = rows.filter(row => row.jaccard !== null); const m = withJaccard.length || 1;
  const rankOdd = oddRanks(rows.map(row => row.mutualRank)); const jaccardOdd = new Map(oddRanks(withJaccard.map(row => row.jaccard)).map((b, i) => [withJaccard[i], b]));
  rows.forEach((row, i) => { const inverted = 2 * n - rankOdd[i]; row.strength = (row.jaccard === null ? inverted * 2 * m : inverted * m + jaccardOdd.get(row) * n) / (4 * n * m); });
  const {order, tierOf, boundaries, counts} = assignTiers(rows); for (const row of order) row.tier = tierOf.get(row.id);
  const byPaper = new Map(); for (const row of order) for (const end of [row.source, row.target]) (byPaper.get(end) || byPaper.set(end, []).get(end)).push(row);
  cached.semantic = {edges: order, byId: new Map(order.map(row => [row.id, row])), byPaper, boundaries, counts};
  return cached.semantic;
}

/**
 * Keyword tiers for concept–concept co-occurrence edges: exactly the edge set and ids of
 * `keywordCooccurrence` in projection.js (shared memo, one source of truth with the concept
 * network); count = explicit `cooccurrenceCount`, else the aggregate count of the pair, else `count`, else 1.
 * Rows: {id, type:'keyword', source, target, cooccurrenceCount, strength, tier}.
 */
export function keywordStrength(snapshot) {
  const cached = cacheFor(snapshot); if (cached.keyword) return cached.keyword;
  const {conceptEdgesAll, aggregateCount} = keywordCooccurrence(snapshot);
  const rows = conceptEdgesAll.map(edge => ({id: String(edge.id), type: 'keyword', source: String(edge.source), target: String(edge.target), cooccurrenceCount: Number(edge.cooccurrenceCount ?? aggregateCount(edge.source, edge.target) ?? edge.count ?? 1)}));
  midRankPercentiles(rows.map(row => row.cooccurrenceCount)).forEach((p, i) => { rows[i].strength = p; });
  const {order, tierOf, boundaries, counts: tierCounts} = assignTiers(rows); for (const row of order) row.tier = tierOf.get(row.id);
  cached.keyword = {edges: order, byId: new Map(order.map(row => [row.id, row])), boundaries, counts: tierCounts};
  return cached.keyword;
}

/** Citation edges between active papers, never tiered: [{id, type:'citation', source, target, directed, verified, tier:null}]. */
export function citationEdges(snapshot) {
  const cached = cacheFor(snapshot); if (cached.citation) return cached.citation;
  const papers = new Set((snapshot.papers || []).map(paper => String(paper.id)));
  cached.citation = (snapshot.edges || []).filter(edge => edge.type === 'citation' && papers.has(String(edge.source)) && papers.has(String(edge.target))).map(edge => ({id: String(edge.id), type: 'citation', source: String(edge.source), target: String(edge.target), directed: edge.directed !== false, verified: edge.verified === true, tier: null})).sort((a, b) => cmp(a.id, b.id));
  return cached.citation;
}

/** Aggregate strong+medium semantic edges into cloud pairs; `cloudsOf`: paperId -> cloudIds (unfiled already removed). */
function bridgesFrom(strength, cloudsOf, {minWeight = BRIDGE_MIN_WEIGHT, limit = BRIDGE_LIMIT} = {}) {
  const weight = new Map();
  for (const row of strength.edges) {
    if (row.tier === 'weak') continue; const from = cloudsOf.get(row.source); const to = cloudsOf.get(row.target); if (!from || !to) continue; const keys = new Set();
    for (const a of from) for (const b of to) if (a !== b) keys.add(a < b ? `${a}\u0000${b}` : `${b}\u0000${a}`);
    for (const key of keys) weight.set(key, (weight.get(key) || 0) + 1);
  }
  const kept = [...weight].filter(([, w]) => w >= minWeight).map(([key, w]) => { const [source, target] = key.split('\u0000'); return {id: `bridge:${source}|${target}`, source, target, weight: w, level: 1}; })
    .sort((a, b) => b.weight - a.weight || cmp(a.id, b.id)).slice(0, limit);
  const sorted = kept.map(bridge => bridge.weight).sort((a, b) => a - b); const at = share => sorted[Math.max(0, Math.ceil(sorted.length * share) - 1)];
  const thresholds = Array.from({length: BRIDGE_LEVELS - 1}, (_, k) => at((k + 1) / BRIDGE_LEVELS)); for (const bridge of kept) bridge.level = 1 + thresholds.filter(t => bridge.weight > t).length;
  return kept;
}

/**
 * Cloud bridges from `cloudGroups` output ([{id, paperIds, unfiled}]): top 12 pairs with weight >= 3
 * (weight desc, pair id asc); `level` 1..3 by terciles of the kept weights (ties -> lower level).
 * @returns Array<{id:'bridge:<A>|<B>', source:A, target:B, weight, level}>
 */
export function cloudBridges(snapshot, groups, options = {}) {
  const cloudsOf = new Map(); for (const group of groups || []) if (!group.unfiled) for (const id of group.paperIds || []) (cloudsOf.get(String(id)) || cloudsOf.set(String(id), []).get(String(id))).push(String(group.id));
  return bridgesFrom(semanticStrength(snapshot), cloudsOf, options);
}

function layoutIndex(snapshot, layout) {
  let perSnapshot = layoutMemo.get(layout); if (!perSnapshot) { perSnapshot = new WeakMap(); layoutMemo.set(layout, perSnapshot); }
  let index = perSnapshot.get(snapshot); if (index && index.points === layout.points && index.clouds === layout.clouds) return index;
  const unfiled = new Set((layout.clouds || []).filter(cloud => cloud.unfiled).map(cloud => String(cloud.id)));
  const pointById = new Map(); const pointsOf = new Map(); const cloudsOf = new Map();
  for (const point of [...(layout.points || [])].sort((a, b) => cmp(String(a.id), String(b.id)))) {
    const paperId = String(point.paperId); const cloudId = String(point.cloudId); pointById.set(String(point.id), point); (pointsOf.get(paperId) || pointsOf.set(paperId, []).get(paperId)).push(point);
    if (!unfiled.has(cloudId)) (cloudsOf.get(paperId) || cloudsOf.set(paperId, []).get(paperId)).push(cloudId);
  }
  index = {points: layout.points, clouds: layout.clouds, pointById, pointsOf, bridges: bridgesFrom(semanticStrength(snapshot), cloudsOf)}; perSnapshot.set(snapshot, index);
  return index;
}

const drawOrder = (a, b) => tierIndex(a.tier) - tierIndex(b.tier) || b.strength - a.strength || cmp(a.id, b.id);
const line = (row, from, to, extra = {}) => ({id: row.id, tier: row.tier, type: 'semantic', strength: row.strength, fromPointId: String(from.id), toPointId: String(to.id), intraCloud: String(from.cloudId) === String(to.cloudId), ...extra});

/**
 * What the collection map draws for `state` {cloudId, paperId, pointId, subItemPaperIds} (null = unset).
 * idle -> bridges only; cloud -> strong edges inside the cloud + bridges (`emphasis` when touching
 * the cloud, else `dimmed`); paper (wins over cloud) -> every semantic edge of the paper, one per
 * pair, replica rule, every bridge `dimmed`; sub-item -> cloud rule with edges not inside it `dimmed`.
 * Lines + bridges <= EDGE_DRAW_CAP (lines dropped by tier, strength desc, id asc).
 * @returns {{bridges:Array, edges:Array<{id,tier,type,strength,fromPointId,toPointId,intraCloud,dimmed?}>, counters:{displayedEdges, displayedBridges}, capped:boolean}}
 */
export function edgesForState(snapshot, layout, state = {}) {
  const strength = semanticStrength(snapshot); const index = layoutIndex(snapshot, layout);
  const cloudId = state.cloudId == null ? null : String(state.cloudId); const paperId = state.paperId == null ? null : String(state.paperId);
  let bridges = index.bridges.map(bridge => ({...bridge, emphasis: false, dimmed: false})); let edges = [];
  if (paperId !== null) {
    const own = index.pointsOf.get(paperId) || []; const chosen = state.pointId == null ? null : index.pointById.get(String(state.pointId));
    const start = (chosen && String(chosen.paperId) === paperId ? chosen : null) || own.find(point => String(point.cloudId) === cloudId) || own[0];
    const pairs = new Map(); for (const row of start ? strength.byPaper.get(paperId) || [] : []) { const other = row.source === paperId ? row.target : row.source; if (!pairs.has(other)) pairs.set(other, row); }
    for (const [other, row] of pairs) {
      const replicas = index.pointsOf.get(other); if (!replicas) continue;
      const same = replicas.find(point => String(point.cloudId) === String(start.cloudId));
      const end = same || replicas.reduce((best, point) => { const d = Math.hypot(point.x - start.x, point.y - start.y); const bd = Math.hypot(best.x - start.x, best.y - start.y); return d < bd || (d === bd && cmp(String(point.cloudId), String(best.cloudId)) < 0) ? point : best; });
      edges.push(line(row, start, end));
    }
    bridges = bridges.map(bridge => ({...bridge, dimmed: true})); // the paper's own lines carry the view; the aggregate bridges recede
  } else if (cloudId !== null) {
    const inCloud = paperIdOf => (index.pointsOf.get(paperIdOf) || []).find(point => String(point.cloudId) === cloudId);
    const sub = Array.isArray(state.subItemPaperIds) ? new Set(state.subItemPaperIds.map(String)) : null;
    for (const row of strength.edges) {
      if (row.tier !== 'strong') continue; const from = inCloud(row.source); const to = from && inCloud(row.target); if (!to) continue;
      edges.push(line(row, from, to, sub ? {dimmed: !(sub.has(row.source) && sub.has(row.target))} : {}));
    }
    bridges = bridges.map(bridge => (bridge.source === cloudId || bridge.target === cloudId ? {...bridge, emphasis: true} : {...bridge, dimmed: true}));
  }
  edges.sort(drawOrder); const room = Math.max(0, EDGE_DRAW_CAP - bridges.length); const capped = edges.length > room; if (capped) edges = edges.slice(0, room);
  return {bridges, edges, counters: {displayedEdges: edges.length, displayedBridges: bridges.length}, capped};
}

/**
 * Lower networks (Cytoscape + G6): copy of `edges` ({data: {id, relation}}) with `data.tier` from the full-snapshot strength —
 * semantic edges by `semanticStrength`, concept co-occurrence by `keywordStrength`; citation, membership and every other
 * type keep `tier: null` (types never merged, citation never tiered).  The ≤ 1000 budgets and sampling stay with the projection.
 */
export function withEdgeTiers(snapshot, edges) {
  const semantic = semanticStrength(snapshot).byId; let keyword = null;
  return (edges || []).map(edge => {
    const relation = edge.data?.relation; const id = String(edge.data?.id);
    const tier = relation === 'semantic' ? semantic.get(id)?.tier ?? null : relation === 'concept-cooccurrence' ? (keyword ||= keywordStrength(snapshot).byId).get(id)?.tier ?? null : null;
    return {...edge, data: {...edge.data, tier}};
  });
}

/** Legend rows for the non-empty tiers of a strength result (`counts`) or an `edgesForState` result (`edges`). */
export function tierLegend(result) {
  const counts = result?.counts || (result?.edges || []).reduce((acc, edge) => { if (edge.tier) acc[edge.tier] = (acc[edge.tier] || 0) + 1; return acc; }, {});
  return TIERS.filter(tier => (counts[tier] || 0) > 0).map(tier => ({tier, count: counts[tier]}));
}

export const AtlasEdgeStrength = {TIERS, TIER_STYLE, PAINT_ORDER, STRONG_SHARE, MEDIUM_SHARE, BRIDGE_MIN_WEIGHT, BRIDGE_LIMIT, BRIDGE_LEVELS, EDGE_DRAW_CAP, midRankPercentiles, assignTiers, semanticStrength, keywordStrength, citationEdges, cloudBridges, edgesForState, withEdgeTiers, tierLegend};
if (typeof window !== 'undefined') window.AtlasEdgeStrength = AtlasEdgeStrength;
