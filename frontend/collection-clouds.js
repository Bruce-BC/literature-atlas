import * as d3 from 'd3';
import {topologySignature, clampZoom} from './camera-policy.js';
import {stableHash} from './sampling.js';

/**
 * Pure, deterministic collection-cloud layout (no DOM).  One cloud per
 * non-empty top-level Zotero collection (sub-collections roll up via
 * `parentId`) plus one peripheral `cloud:unfiled` cloud.  Inner positions come
 * from a headless d3-force run seeded only from sorted IDs; clouds are packed
 * with `d3.packSiblings`.  Same input (in any order) -> same coordinates.
 */
export const CLOUD_POINT_SPACING = 10; // canvas px, S
export const CLOUD_GAP_FACTOR = 4; // N: cloud gap >= N * S
export const CLOUD_GAP = CLOUD_GAP_FACTOR * CLOUD_POINT_SPACING;
export const MIN_POINT_DISTANCE = 9;
export const UNFILED_CLOUD_ID = 'cloud:unfiled';
export const UNFILED_LABEL = '미분류 · 자동';
export const LABEL_MAX_WIDTH = 160; // screen px
export const LABEL_HEIGHT = 16;
export const LABEL_OFFSET = 4;
export const POINT_RADIUS = 4; // canvas px (G6 nodeSize 8)
export const FIT_PADDING = 24; // G6 fitView padding
export const FIT_MIN_ZOOM = 0.2;
export const LABEL_RESERVE_MIN_ZOOM = 0.22; // R-fit-2
export const FALLBACK_SPACING = 8; // R-fit-3
// 60 ticks, Barnes-Hut theta 1.5, no forceCollide: measured budget (forceCollide alone ~270 ms
// on 1,000 papers); the deterministic overlap pass below enforces MIN_POINT_DISTANCE instead.
export const LAYOUT_TICKS = 60;

const GOLDEN = Math.PI * (3 - Math.sqrt(5));
const cmp = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
const bySizeThenId = (a, b) => b.size - a.size || cmp(a.id, b.id);
const now = () => (globalThis.performance?.now ? globalThis.performance.now() : Date.now());
const sortedUnique = ids => [...new Set([...ids].map(String))].sort(cmp);

/** Clouds of a snapshot: [{id, label, paperIds, size, unfiled}] ordered size desc, id asc, unfiled last. */
export function cloudGroups(snapshot) {
  const active = new Set((snapshot?.papers || []).map(paper => String(paper.id)));
  const collections = [...(snapshot?.collections || [])].sort((a, b) => cmp(String(a.id), String(b.id)));
  const byId = new Map(collections.map(c => [String(c.id), c]));
  const rootOf = collection => { const seen = new Set([String(collection.id)]); let current = collection; while (current.parentId != null && byId.has(String(current.parentId)) && !seen.has(String(current.parentId))) { current = byId.get(String(current.parentId)); seen.add(String(current.id)); } return String(current.id); };
  const members = new Map(); const filed = new Set();
  for (const collection of collections) { const root = rootOf(collection); const set = members.get(root) || members.set(root, new Set()).get(root); for (const id of collection.paperIds || []) if (active.has(String(id))) { set.add(String(id)); filed.add(String(id)); } }
  const clouds = [...members].filter(([, set]) => set.size).map(([id, set]) => ({id, label: String(byId.get(id).label ?? id), paperIds: [...set].sort(cmp), size: set.size, unfiled: false})).sort(bySizeThenId);
  const unfiled = [...active].filter(id => !filed.has(id)).sort(cmp);
  if (unfiled.length) clouds.push({id: UNFILED_CLOUD_ID, label: UNFILED_LABEL, paperIds: unfiled, size: unfiled.length, unfiled: true});
  return clouds;
}

/**
 * Normalise a similarity input (`[{source,target,score}]` or `{pairs: [[a, b, w]]}`)
 * to undirected, de-duplicated (max score), ID-sorted pairs inside `ids`.
 */
export function similarityPairs(ids, similarityInput = []) {
  const inside = ids instanceof Set ? ids : new Set(ids);
  const rows = Array.isArray(similarityInput) ? similarityInput : (similarityInput?.pairs || []).map(([source, target, score]) => ({source, target, score}));
  const best = new Map();
  for (const row of rows) {
    const a = String(row.source); const b = String(row.target); if (a === b || !inside.has(a) || !inside.has(b)) continue;
    const [source, target] = a < b ? [a, b] : [b, a]; const score = Number.isFinite(Number(row.score)) && row.score !== null ? Number(row.score) : 1; const key = `${source}\u0000${target}`;
    if (!best.has(key) || best.get(key).score < score) best.set(key, {source, target, score});
  }
  return [...best.values()].sort((x, y) => cmp(x.source, y.source) || cmp(x.target, y.target));
}

/**
 * Deterministic score-weighted label propagation.  Nodes are visited in ID order,
 * ties go to the lexicographically smallest label, at most `maxIterations` sweeps.
 * Groups smaller than `minSize` merge into one `other` group; sizes sum to the unique count.
 */
export function subgroupLPA(paperIds, edges = [], {minSize = 3, maxIterations = 20} = {}) {
  const ids = sortedUnique(paperIds); const pairs = similarityPairs(ids, edges);
  const adjacency = new Map(ids.map(id => [id, []]));
  for (const {source, target, score} of pairs) { adjacency.get(source).push([target, score]); adjacency.get(target).push([source, score]); }
  const label = new Map(ids.map(id => [id, id]));
  for (let iteration = 0; iteration < maxIterations; iteration += 1) {
    let changed = false;
    for (const id of ids) {
      const neighbours = adjacency.get(id); if (!neighbours.length) continue;
      const weight = new Map(); for (const [other, score] of neighbours) { const l = label.get(other); weight.set(l, (weight.get(l) || 0) + score); }
      let best = null; let bestWeight = -Infinity;
      for (const [l, w] of weight) if (w > bestWeight + 1e-9 || (Math.abs(w - bestWeight) <= 1e-9 && cmp(l, best) < 0)) { best = l; bestWeight = w; }
      if (best !== label.get(id)) { label.set(id, best); changed = true; }
    }
    if (!changed) break;
  }
  const byLabel = new Map(); for (const id of ids) { const l = label.get(id); (byLabel.get(l) || byLabel.set(l, []).get(l)).push(id); }
  const groups = []; const other = [];
  for (const [l, members] of byLabel) if (members.length >= minSize) groups.push({id: `subgroup:${l}`, paperIds: members, size: members.length, other: false}); else other.push(...members);
  groups.sort(bySizeThenId);
  if (other.length) groups.push({id: 'subgroup:other', paperIds: other.sort(cmp), size: other.length, other: true});
  return groups;
}

const sunflowerAt = (ids, spacing, cx = 0, cy = 0, out = new Map()) => { ids.forEach((id, i) => { const radius = spacing * Math.sqrt(i + 0.5); out.set(id, {x: cx + Math.cos(i * GOLDEN) * radius, y: cy + Math.sin(i * GOLDEN) * radius}); }); return out; };

/** Push apart every pair closer than `minDistance` (ID order, grid hash); scales out if it cannot converge. */
function resolveOverlaps(ids, positions, minDistance) {
  const target = minDistance + 0.5; const trigger = minDistance + 0.25; const n = ids.length; if (n < 2) return;
  const xs = ids.map(id => positions.get(id).x); const ys = ids.map(id => positions.get(id).y);
  const cellKey = (gx, gy) => (gx + 32768) * 65536 + (gy + 32768);
  const buildGrid = () => { const grid = new Map(); for (let i = 0; i < n; i += 1) { const key = cellKey(Math.floor(xs[i] / target), Math.floor(ys[i] / target)); const bucket = grid.get(key); if (bucket) bucket.push(i); else grid.set(key, [i]); } return grid; };
  // One sweep in index (= sorted ID) order; returns the number of pairs closer than `limit`, pushing them apart when `push` is set.
  const sweep = (limit, push) => {
    const grid = buildGrid(); let count = 0;
    for (let i = 0; i < n; i += 1) {
      const gx = Math.floor(xs[i] / target); const gy = Math.floor(ys[i] / target);
      for (let dx = -1; dx <= 1; dx += 1) for (let dy = -1; dy <= 1; dy += 1) for (const j of grid.get(cellKey(gx + dx, gy + dy)) || []) {
        if (j <= i) continue; let vx = xs[j] - xs[i]; let vy = ys[j] - ys[i]; let d = Math.hypot(vx, vy); if (d >= limit) continue;
        count += 1; if (!push) continue;
        if (d < 1e-9) { const angle = (stableHash(`${ids[i]}|${ids[j]}`) / 4294967296) * Math.PI * 2; vx = Math.cos(angle); vy = Math.sin(angle); d = 0; } else { vx /= d; vy /= d; }
        const shift = (target - d) / 2; xs[i] -= vx * shift; ys[i] -= vy * shift; xs[j] += vx * shift; ys[j] += vy * shift;
      }
    }
    return count;
  };
  for (let attempt = 0; attempt < 8; attempt += 1) {
    for (let round = 0; round < 60; round += 1) if (!sweep(trigger, true)) break;
    if (!sweep(minDistance, false)) break;
    for (let i = 0; i < n; i += 1) { xs[i] *= 1.15; ys[i] *= 1.15; }
  }
  ids.forEach((id, i) => positions.set(id, {x: xs[i], y: ys[i]}));
}

/**
 * Deterministic inner layout of one cloud.  `similarityInput` is the replaceable
 * interface (`[{source,target,score}]` or `{pairs}`), e.g. for a backend layout later.
 * Initial positions come from sorted IDs (LPA-group packing + sunflower), never input order.
 * @returns {{positions: Map<string,{x,y}>, radius:number, mode:'empty'|'sunflower'|'force', groups:Array, edgeCount:number}}
 */
export function innerLayout(paperIds, similarityInput = [], seed = 0, {spacing = CLOUD_POINT_SPACING, ticks = LAYOUT_TICKS, minDistance = MIN_POINT_DISTANCE} = {}) {
  const ids = sortedUnique(paperIds); const n = ids.length;
  if (!n) return {positions: new Map(), radius: 0, mode: 'empty', groups: [], edgeCount: 0};
  const pairs = similarityPairs(ids, similarityInput); const groups = subgroupLPA(ids, pairs);
  let positions; let mode;
  if (n <= 3 || pairs.length < 3) { mode = 'sunflower'; positions = sunflowerAt(groups.flatMap(group => group.paperIds), spacing); }
  else {
    mode = 'force';
    const circles = groups.map(group => ({id: group.id, r: spacing * (Math.sqrt(group.size) + 1)})); d3.packSiblings(circles);
    const seedPositions = new Map(); groups.forEach((group, i) => sunflowerAt(group.paperIds, spacing, circles[i].x, circles[i].y, seedPositions));
    const index = new Map(ids.map((id, i) => [id, i])); const nodes = ids.map(id => ({id, ...seedPositions.get(id)}));
    const degree = new Array(n).fill(0); const links = pairs.map(pair => { const source = index.get(pair.source); const target = index.get(pair.target); degree[source] += 1; degree[target] += 1; return {source, target, score: pair.score}; });
    const random = d3.randomLcg(Number.isFinite(seed) ? seed : stableHash(seed) / 4294967296);
    d3.forceSimulation(nodes).randomSource(random).alphaDecay(1 - Math.pow(0.001, 1 / Math.max(1, ticks)))
      .force('link', d3.forceLink(links).distance(spacing * 1.2).strength(link => link.score / Math.min(degree[link.source.index], degree[link.target.index])))
      .force('charge', d3.forceManyBody().strength(-2 * spacing).theta(1.5))
      .force('x', d3.forceX(0).strength(0.06)).force('y', d3.forceY(0).strength(0.06))
      .stop().tick(ticks);
    let cx = 0; let cy = 0; for (const node of nodes) { cx += node.x; cy += node.y; } cx /= n; cy /= n;
    let extent = 0; for (const node of nodes) extent = Math.max(extent, Math.hypot(node.x - cx, node.y - cy));
    const scale = extent > 0 ? (spacing * Math.sqrt(n)) / extent : 1;
    positions = new Map(nodes.map(node => [node.id, {x: (node.x - cx) * scale, y: (node.y - cy) * scale}]));
  }
  resolveOverlaps(ids, positions, minDistance);
  const enclosure = d3.packEnclose(ids.map(id => ({x: positions.get(id).x, y: positions.get(id).y, r: POINT_RADIUS})));
  for (const id of ids) { const p = positions.get(id); positions.set(id, {x: p.x - enclosure.x, y: p.y - enclosure.y}); }
  return {positions, radius: enclosure.r, mode, groups, edgeCount: pairs.length};
}

/**
 * Pack cloud circles ({id, r, size, unfiled, reserve?}) with `d3.packSiblings`
 * (size desc, id asc).  Each circle is padded by gap/2 (+ label reserve), so
 * `centerDistance - r_i - r_j >= gap`.  The unfiled cloud sits outside the
 * enclosing circle of the core clouds.  @returns Map id -> {cx, cy}
 */
export function packClouds(clouds, {gap = CLOUD_GAP} = {}) {
  const pad = gap / 2 + 0.5; const result = new Map();
  const core = clouds.filter(cloud => !cloud.unfiled).sort(bySizeThenId).map(cloud => ({id: cloud.id, r: cloud.r + pad + (cloud.reserve || 0)}));
  d3.packSiblings(core);
  const enclosure = core.length ? d3.packEnclose(core) : {x: 0, y: 0, r: 0};
  for (const circle of core) result.set(circle.id, {cx: circle.x - enclosure.x, cy: circle.y - enclosure.y});
  const unfiled = clouds.find(cloud => cloud.unfiled);
  if (unfiled) {
    const radius = unfiled.r + pad + (unfiled.reserve || 0);
    if (!core.length) result.set(unfiled.id, {cx: 0, cy: 0});
    else {
      const distance = enclosure.r + radius + 1e-6; let best = null;
      for (let k = 0; k < 16; k += 1) {
        const angle = (k / 16) * Math.PI * 2; const ux = Math.cos(angle) * distance; const uy = Math.sin(angle) * distance;
        let minX = ux - radius; let maxX = ux + radius; let minY = uy - radius; let maxY = uy + radius;
        for (const circle of core) { const p = result.get(circle.id); minX = Math.min(minX, p.cx - circle.r); maxX = Math.max(maxX, p.cx + circle.r); minY = Math.min(minY, p.cy - circle.r); maxY = Math.max(maxY, p.cy + circle.r); }
        const score = [Math.max(maxX - minX, maxY - minY), (maxX - minX) * (maxY - minY)];
        if (!best || score[0] < best.score[0] - 1e-6 || (Math.abs(score[0] - best.score[0]) <= 1e-6 && score[1] < best.score[1] - 1e-6)) best = {score, cx: ux, cy: uy};
      }
      result.set(unfiled.id, {cx: best.cx, cy: best.cy});
    }
  }
  return result;
}

const layoutBounds = layout => {
  let minX = Infinity; let maxX = -Infinity; let minY = Infinity; let maxY = -Infinity;
  for (const cloud of layout.clouds) { const r = cloud.r + (cloud.reserve || 0); minX = Math.min(minX, cloud.cx - r); maxX = Math.max(maxX, cloud.cx + r); minY = Math.min(minY, cloud.cy - r); maxY = Math.max(maxY, cloud.cy + r); }
  return layout.clouds.length ? {minX, maxX, minY, maxY} : {minX: 0, maxX: 0, minY: 0, maxY: 0};
};

/** Raw (unclamped) zoom that fits every cloud circle (+ label reserve) into width x height. */
export function fitZoomFor(layout, width, height) {
  const {minX, maxX, minY, maxY} = layoutBounds(layout); const w = maxX - minX; const h = maxY - minY;
  if (!(w > 0) || !(h > 0)) return 1;
  return Math.min(width / w, height / h);
}

/** Cheap label width estimate for 12 px UI text (wide CJK glyphs 12 px, others 7 px) + 8 px button padding. */
export const estimateLabelWidth = text => [...String(text)].reduce((sum, ch) => sum + (ch.codePointAt(0) >= 0x2e80 ? 12 : 7), 8);

/**
 * Screen-space cloud labels at `zoom` for a fit area `{width, height, padding=24}`.
 * Coordinates are relative to the container (fit area + padding on each side),
 * map centred.  Greedy by size desc, id asc; candidates top, bottom, right, left;
 * then a leader-line label outside the map disc; otherwise `hidden`.  Labels never
 * intersect each other, any point, or the container edge.  An explicit `camera`
 * `{x, y}` (container position of canvas origin, e.g. a live G6 camera) replaces the centring.
 * @returns Array<{cloudId,x,y,width,height,anchor,leader,hidden,truncated,leaderFrom?}>
 */
export function placeCloudLabels(layout, zoom, fitArea, measure = estimateLabelWidth, camera = null) {
  const padding = fitArea.padding ?? FIT_PADDING; const W = fitArea.width + 2 * padding; const H = fitArea.height + 2 * padding;
  const {minX, maxX, minY, maxY} = layoutBounds(layout); const bx = (minX + maxX) / 2; const by = (minY + maxY) / 2;
  const toScreen = camera ? (x, y) => ({x: camera.x + x * zoom, y: camera.y + y * zoom}) : (x, y) => ({x: W / 2 + (x - bx) * zoom, y: H / 2 + (y - by) * zoom});
  const pointRadius = POINT_RADIUS * zoom; const cell = 32; const grid = new Map();
  for (const point of layout.points) { const s = toScreen(point.x, point.y); const key = `${Math.floor(s.x / cell)},${Math.floor(s.y / cell)}`; (grid.get(key) || grid.set(key, []).get(key)).push(s); }
  const hitsPoint = box => { for (let gx = Math.floor((box.x - pointRadius) / cell); gx <= Math.floor((box.x + box.width + pointRadius) / cell); gx += 1) for (let gy = Math.floor((box.y - pointRadius) / cell); gy <= Math.floor((box.y + box.height + pointRadius) / cell); gy += 1) for (const s of grid.get(`${gx},${gy}`) || []) if (s.x + pointRadius > box.x && s.x - pointRadius < box.x + box.width && s.y + pointRadius > box.y && s.y - pointRadius < box.y + box.height) return true; return false; };
  const placed = []; const intersects = (a, b) => a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;
  const fits = box => box.x >= 0 && box.y >= 0 && box.x + box.width <= W && box.y + box.height <= H && !placed.some(other => intersects(box, other)) && !hitsPoint(box);
  const disc = layout.clouds.length ? d3.packEnclose(layout.clouds.map(cloud => ({x: cloud.cx, y: cloud.cy, r: cloud.r}))) : {x: 0, y: 0, r: 0};
  const discCenter = toScreen(disc.x, disc.y); const discRadius = disc.r * zoom;
  const results = new Map();
  for (const cloud of [...layout.clouds].sort(bySizeThenId)) {
    const natural = Math.ceil(measure(cloud.label)); const width = Math.min(LABEL_MAX_WIDTH, natural); const height = LABEL_HEIGHT;
    const c = toScreen(cloud.cx, cloud.cy); const sr = cloud.r * zoom; const base = {cloudId: cloud.id, width, height, truncated: natural > LABEL_MAX_WIDTH};
    const candidates = [['top', c.x - width / 2, c.y - sr - LABEL_OFFSET - height], ['bottom', c.x - width / 2, c.y + sr + LABEL_OFFSET], ['right', c.x + sr + LABEL_OFFSET, c.y - height / 2], ['left', c.x - sr - LABEL_OFFSET - width, c.y - height / 2]];
    let chosen = null;
    for (const [anchor, x, y] of candidates) { const box = {x, y, width, height}; if (fits(box)) { chosen = {...base, x, y, anchor, leader: false, hidden: false}; break; } }
    if (!chosen && c.x >= 0 && c.y >= 0 && c.x <= W && c.y <= H) { // an off-screen cloud never gets a leader: it falls through to hidden (cloud-list chip)
      const dx = c.x - discCenter.x; const dy = c.y - discCenter.y; const theta = Math.hypot(dx, dy) > 1e-6 ? Math.atan2(dy, dx) : -Math.PI / 2;
      search: for (let k = 0; k < 24; k += 1) {
        const phi = theta + (k % 2 ? 1 : -1) * Math.ceil(k / 2) * (Math.PI / 12);
        for (const extra of [8, 8 + height + LABEL_OFFSET]) {
          const px = discCenter.x + Math.cos(phi) * (discRadius + extra); const py = discCenter.y + Math.sin(phi) * (discRadius + extra);
          const box = {x: px + Math.cos(phi) * width / 2 - width / 2, y: py + Math.sin(phi) * height / 2 - height / 2, width, height};
          if (fits(box)) { const tx = box.x + width / 2 - c.x; const ty = box.y + height / 2 - c.y; const t = Math.hypot(tx, ty) || 1; chosen = {...base, x: box.x, y: box.y, anchor: 'leader', leader: true, hidden: false, leaderFrom: {x: c.x + tx / t * sr, y: c.y + ty / t * sr}}; break search; }
        }
      }
    }
    if (chosen) placed.push(chosen); else chosen = {...base, x: c.x - width / 2, y: c.y - height / 2, anchor: 'none', leader: false, hidden: true};
    results.set(cloud.id, chosen);
  }
  return layout.clouds.map(cloud => results.get(cloud.id));
}

const memo = new Map(); const MEMO_LIMIT = 4;
export const clearCollectionCloudCache = () => memo.clear();

/** Default point id of a (collection cloud, paper) pair. */
export const collectionPointId = (cloud, paperId) => `overview-point:${cloud.unfiled ? 'unfiled' : cloud.id}:${paperId}`;

/**
 * Compose cloudGroups -> innerLayout -> packClouds (-> fit + labels when `fitArea` is given).
 * Fit rules: R-fit-3 drops S to 8 when Fit < 0.2; R-fit-2 reserves label room
 * (second pass) only when the Fit zoom stays >= 0.22.  Memoised by `topologySignature`.
 * Points: {id:'overview-point:<collectionId|unfiled>:<paperId>', paperId, cloudId, x, y}.
 * `groups` (same shape as `cloudGroups` output) replaces the collection grouping and `pointId(cloud, paperId)` the point id:
 * the category map (전체 논문 지도) lays out one cloud per category with them.
 * Clouds also carry `edgeCount` (inner similarity pairs) and sub-group `paperIds`; `baseCenters`
 * [{id, cx, cy, r}] is the reserve-0 build at the requested spacing from the same call.
 */
export function layoutCollectionClouds(snapshot, {relation = 'semantic', spacing = CLOUD_POINT_SPACING, gap = CLOUD_GAP, fitArea = null, measure = estimateLabelWidth, useMemo = true, groups: suppliedGroups = null, pointId = collectionPointId} = {}) {
  const started = now(); const groups = suppliedGroups || cloudGroups(snapshot);
  const edges = (snapshot?.edges || []).filter(edge => edge.type === relation);
  const pointIds = groups.flatMap(cloud => cloud.paperIds.map(paperId => pointId(cloud, paperId)));
  const signature = `${topologySignature([...pointIds, ...groups.map(cloud => `label:${cloud.id}=${cloud.label}`)], edges.map(edge => [String(edge.source), String(edge.target), Number(edge.score ?? 1).toFixed(4)]))}|${relation}|${spacing}|${gap}|${fitArea ? `${fitArea.width}x${fitArea.height}+${fitArea.padding ?? FIT_PADDING}|${measure === estimateLabelWidth ? 'default' : 'custom'}` : 'nofit'}`;
  if (useMemo && measure === estimateLabelWidth && memo.has(signature)) return memo.get(signature);
  const cloudsOf = new Map(); for (const cloud of groups) for (const id of cloud.paperIds) (cloudsOf.get(id) || cloudsOf.set(id, []).get(id)).push(cloud.id);
  const cloudEdges = new Map(groups.map(cloud => [cloud.id, []]));
  for (const edge of edges) { const targetClouds = cloudsOf.get(String(edge.target)); if (!targetClouds) continue; for (const cloudId of cloudsOf.get(String(edge.source)) || []) if (targetClouds.includes(cloudId)) cloudEdges.get(cloudId).push(edge); }
  const innerCache = new Map();
  const inner = s => innerCache.get(s) || innerCache.set(s, new Map(groups.map(cloud => [cloud.id, innerLayout(cloud.paperIds, cloudEdges.get(cloud.id), stableHash(cloud.id) / 4294967296, {spacing: s})]))).get(s);
  const build = (s, reserve) => {
    const layouts = inner(s); const circles = groups.map(cloud => ({id: cloud.id, size: cloud.size, unfiled: cloud.unfiled, r: layouts.get(cloud.id).radius, reserve}));
    const centers = packClouds(circles, {gap});
    const clouds = groups.map(cloud => { const {cx, cy} = centers.get(cloud.id); const local = layouts.get(cloud.id); return {id: cloud.id, label: cloud.label, cx, cy, r: local.radius, size: cloud.size, unfiled: cloud.unfiled, mode: local.mode, edgeCount: local.edgeCount, reserve, subgroups: local.groups.map(group => ({id: group.id, size: group.size, other: group.other, paperIds: group.paperIds}))}; });
    const points = groups.flatMap(cloud => { const {cx, cy} = centers.get(cloud.id); const positions = layouts.get(cloud.id).positions; return cloud.paperIds.map(paperId => { const p = positions.get(paperId); return {id: pointId(cloud, paperId), paperId, cloudId: cloud.id, x: cx + p.x, y: cy + p.y}; }); });
    return {clouds, points, spacing: s};
  };
  let layout = build(spacing, 0); let fit = null; let labels = null;
  const baseCenters = layout.clouds.map(cloud => ({id: cloud.id, cx: cloud.cx, cy: cloud.cy, r: cloud.r})); // reserve 0, requested spacing: fit-independent
  if (fitArea) {
    let rawZoom = fitZoomFor(layout, fitArea.width, fitArea.height); let passes = 1; let reserved = false;
    if (rawZoom < FIT_MIN_ZOOM && spacing > FALLBACK_SPACING) { layout = build(FALLBACK_SPACING, 0); rawZoom = fitZoomFor(layout, fitArea.width, fitArea.height); }
    if (rawZoom >= LABEL_RESERVE_MIN_ZOOM) {
      const candidate = build(layout.spacing, (LABEL_HEIGHT + LABEL_OFFSET) / rawZoom); const candidateZoom = fitZoomFor(candidate, fitArea.width, fitArea.height); passes = 2;
      if (candidateZoom >= LABEL_RESERVE_MIN_ZOOM) { layout = candidate; rawZoom = candidateZoom; reserved = true; }
    }
    const zoom = clampZoom(rawZoom);
    fit = {rawZoom, zoom, passes, reserved, spacing: layout.spacing};
    labels = placeCloudLabels(layout, zoom, fitArea, measure);
  }
  const result = {signature, clouds: layout.clouds, points: layout.points, spacing: layout.spacing, gap, representedIds: groups.length ? sortedUnique(groups.flatMap(cloud => cloud.paperIds)) : [], baseCenters, fit, labels, timing: {layoutMs: now() - started}};
  if (useMemo && measure === estimateLabelWidth) { memo.set(signature, result); if (memo.size > MEMO_LIMIT) memo.delete(memo.keys().next().value); }
  return result;
}

export const AtlasCollectionClouds = {CLOUD_POINT_SPACING, CLOUD_GAP_FACTOR, CLOUD_GAP, MIN_POINT_DISTANCE, UNFILED_CLOUD_ID, UNFILED_LABEL, LABEL_MAX_WIDTH, cloudGroups, similarityPairs, subgroupLPA, innerLayout, packClouds, fitZoomFor, placeCloudLabels, estimateLabelWidth, layoutCollectionClouds, clearCollectionCloudCache};
if (typeof window !== 'undefined') window.AtlasCollectionClouds = AtlasCollectionClouds;
