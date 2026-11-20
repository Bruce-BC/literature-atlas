import * as d3 from 'd3';
import {POINT_RADIUS} from './collection-clouds.js';
import {stableHash} from './sampling.js';

/**
 * Pure, deterministic cloud outline engine (no DOM).  Every outline lives in the
 * cloud's LOCAL frame (origin = cloud centre), contains each point's margin disc
 * (radius 4 + min(6, r - |p| - 4)) and never leaves the cloud circle.  Geometry is
 * memoised per cloud id + local-coordinate hash + spacing, so fit-area changes,
 * pan and zoom never recompute.  Fallback chain: contour (S1) -> soft hull (S2) -> circle.
 */
export const MAX_MARGIN = 6; // canvas px beyond the point disc
export const CAPSULE_MAX_SIZE = 7;
export const CONTOUR_MIN_EDGES = 8;
export const CONTOUR_ITERATIONS = 12;
export const CONTOUR_CELL_FACTOR = 1 / 16; // grid cell = r / 16, floored to a power of two (>= 2)
export const CONTOUR_BANDWIDTH_FACTOR = 0.06; // bandwidth = 0.06 r, never below CONTOUR_MIN_BANDWIDTH
export const CONTOUR_MIN_BANDWIDTH = 6;
export const MIN_VERTEX_CAP = 64;
export const ARC_SAG = 0.25; // max chord sag of arc samples (px)
export const SHAPE_TOLERANCE = 0.5;
const BUILD_TOLERANCE = 0.45; // headroom for the 2-decimal output rounding
const DISC_SAMPLES = 16; const HULL_INFLATE = 0.3; // 16-gon sag at r 10 is 0.19 < 0.3
const SPLINE_INFLATE = 1.5; const SPLINE_SPACING = 24; const MEMO_LIMIT = 512; const GRID = 32;
const TAU = Math.PI * 2;

const now = () => (globalThis.performance?.now ? globalThis.performance.now() : Date.now());
const q3 = v => Math.round(v * 1000) / 1000;
const r2 = v => { const x = Math.round(v * 100) / 100; return x === 0 ? 0 : x; };
const distSeg = (px, py, ax, ay, bx, by) => { const vx = bx - ax; const vy = by - ay; const l = vx * vx + vy * vy; let t = l ? ((px - ax) * vx + (py - ay) * vy) / l : 0; t = t < 0 ? 0 : t > 1 ? 1 : t; return Math.hypot(px - ax - t * vx, py - ay - t * vy); };
const orient = (a, b, c) => (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
const crosses = (a, b, c, d) => { const o1 = orient(a, b, c); const o2 = orient(a, b, d); const o3 = orient(c, d, a); const o4 = orient(c, d, b); return ((o1 > 0 && o2 < 0) || (o1 < 0 && o2 > 0)) && ((o3 > 0 && o4 < 0) || (o3 < 0 && o4 > 0)); };
const signedArea = ring => { let s = 0; for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) s += ring[j][0] * ring[i][1] - ring[i][0] * ring[j][1]; return s / 2; };
const ccw = ring => (signedArea(ring) < 0 ? [...ring].reverse() : ring);
/** Arc sample angle so that a chord never sags more than ARC_SAG below the circle. */
const arcStep = r => 2 * Math.asin(Math.min(1, Math.sqrt(8 * r * ARC_SAG) / (2 * r)));

/** Margin-disc radius of a local point (x, y) in a cloud of radius r. */
export const marginRadius = (x, y, r) => POINT_RADIUS + Math.max(0, Math.min(MAX_MARGIN, r - Math.hypot(x, y) - POINT_RADIUS));

/** Shape kind of a cloud ({size, mode, edgeCount, unfiled}): single | capsule | contour | soft-hull, plus the unfiled flag. */
export function classifyCloud(cloud) {
  const size = cloud.size ?? cloud.paperIds?.length ?? 0; const unfiled = Boolean(cloud.unfiled);
  const kind = size <= 1 ? 'single' : unfiled ? 'soft-hull' : size <= CAPSULE_MAX_SIZE ? 'capsule' : cloud.mode === 'force' && (cloud.edgeCount || 0) >= CONTOUR_MIN_EDGES ? 'contour' : 'soft-hull';
  return {kind, unfiled};
}

/** Disc set with a uniform-grid index: discs [[x, y, m]], near(minX, minY, maxX, maxY) -> discs. */
function discSet(points, r) {
  const discs = points.map(([x, y]) => [x, y, marginRadius(x, y, r)]); const grid = new Map();
  for (const disc of discs) { const key = `${Math.floor(disc[0] / GRID)},${Math.floor(disc[1] / GRID)}`; (grid.get(key) || grid.set(key, []).get(key)).push(disc); }
  const near = (minX, minY, maxX, maxY) => { const out = []; const pad = POINT_RADIUS + MAX_MARGIN + 1; for (let gx = Math.floor((minX - pad) / GRID); gx <= Math.floor((maxX + pad) / GRID); gx += 1) for (let gy = Math.floor((minY - pad) / GRID); gy <= Math.floor((maxY + pad) / GRID); gy += 1) for (const d of grid.get(`${gx},${gy}`) || []) out.push(d); return out; };
  return {discs, near};
}

/** Does `ring` contain every margin disc within `tol` (centre inside, edge distance >= m - tol)? */
/** Edge buckets of a ring on the GRID; nearest(x, y, reach) = min distance to edges within `reach` (Infinity if none). */
function edgeIndex(ring) {
  const grid = new Map();
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const a = ring[j]; const b = ring[i];
    for (let gx = Math.floor(Math.min(a[0], b[0]) / GRID); gx <= Math.floor(Math.max(a[0], b[0]) / GRID); gx += 1) for (let gy = Math.floor(Math.min(a[1], b[1]) / GRID); gy <= Math.floor(Math.max(a[1], b[1]) / GRID); gy += 1) { const key = `${gx},${gy}`; (grid.get(key) || grid.set(key, []).get(key)).push(j); }
  }
  return (x, y, reach) => {
    let best = Infinity;
    for (let gx = Math.floor((x - reach) / GRID); gx <= Math.floor((x + reach) / GRID); gx += 1) for (let gy = Math.floor((y - reach) / GRID); gy <= Math.floor((y + reach) / GRID); gy += 1) for (const j of grid.get(`${gx},${gy}`) || []) { const a = ring[j]; const b = ring[(j + 1) % ring.length]; best = Math.min(best, distSeg(x, y, a[0], a[1], b[0], b[1])); }
    return best;
  };
}

function containsDiscs(ring, discs, tol) {
  const nearest = edgeIndex(ring);
  for (const [x, y, m] of discs) if (!d3.polygonContains(ring, [x, y]) || nearest(x, y, m) < m - tol) return false;
  return true;
}

const simple = ring => {
  const n = ring.length;
  for (let i = 0; i < n; i += 1) { const a = ring[i]; const b = ring[(i + 1) % n]; for (let j = i + 2; j < n; j += 1) { if (i === 0 && j === n - 1) continue; const c = ring[j]; const d = ring[(j + 1) % n]; if (Math.max(a[0], b[0]) < Math.min(c[0], d[0]) || Math.max(c[0], d[0]) < Math.min(a[0], b[0]) || Math.max(a[1], b[1]) < Math.min(c[1], d[1]) || Math.max(c[1], d[1]) < Math.min(a[1], b[1])) continue; if (crosses(a, b, c, d)) return false; } }
  return true;
};

/**
 * Validate an outline: >= 3 vertices, simple, every vertex within r + tol, every margin disc inside (tol).
 * @returns {ok:boolean, reason?:string, outside:number, minClearance:number}
 */
export function checkOutline(ring, points, r, tol = SHAPE_TOLERANCE) {
  let outside = 0; for (const [x, y] of ring) outside = Math.max(outside, Math.hypot(x, y) - r);
  let minClearance = Infinity; let centresOut = 0;
  for (const [x, y] of points) {
    const m = marginRadius(x, y, r); if (!d3.polygonContains(ring, [x, y])) centresOut += 1;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) minClearance = Math.min(minClearance, distSeg(x, y, ring[j][0], ring[j][1], ring[i][0], ring[i][1]) - m);
  }
  const reason = ring.length < 3 ? 'degenerate' : !simple(ring) ? 'self-intersection' : outside > tol ? 'outside-circle' : centresOut ? 'centre-outside' : minClearance < -tol ? 'disc-cut' : null;
  return {ok: !reason, reason, outside, minClearance};
}

/**
 * Replace every stretch of a ccw ring outside the circle (radius r, centre 0) by arc
 * samples on the circle.  Vertices are [x, y, onArc].  @returns ring | null (no vertex inside, no crossing)
 */
export function clipToCircle(ring, r) {
  const r2sq = r * r; const step = arcStep(r); const inside = p => p[0] * p[0] + p[1] * p[1] < r2sq; const out = [];
  const at = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
  const arc = (from, to) => { let delta = to - from; while (delta <= 0) delta += TAU; const k = Math.ceil(delta / step); for (let i = 1; i < k; i += 1) { const angle = from + (delta * i) / k; out.push([r * Math.cos(angle), r * Math.sin(angle), true]); } };
  let state = inside(ring[0]); let exitAngle = null; let firstEntry = null;
  const cross = p => { const angle = Math.atan2(p[1], p[0]); if (state) { out.push([p[0], p[1], true]); exitAngle = angle; } else { if (exitAngle === null) firstEntry = angle; else arc(exitAngle, angle); out.push([p[0], p[1], true]); exitAngle = null; } state = !state; };
  for (let i = 0; i < ring.length; i += 1) {
    const a = ring[i]; const b = ring[(i + 1) % ring.length]; const bIn = inside(b);
    if (state) out.push([a[0], a[1], Boolean(a[2])]);
    const dx = b[0] - a[0]; const dy = b[1] - a[1]; const A = dx * dx + dy * dy; const B = 2 * (a[0] * dx + a[1] * dy); const C = a[0] * a[0] + a[1] * a[1] - r2sq; const disc = B * B - 4 * A * C;
    const root = sign => (A > 0 && disc >= 0 ? Math.min(1, Math.max(0, (-B + sign * Math.sqrt(disc)) / (2 * A))) : 0);
    if (state && !bIn) cross(at(a, b, root(1)));
    else if (!state && bIn) cross(at(a, b, root(-1)));
    else if (!state && !bIn && disc > 0 && A > 0) { const t1 = (-B - Math.sqrt(disc)) / (2 * A); const t2 = (-B + Math.sqrt(disc)) / (2 * A); if (t1 > 0 && t2 < 1 && t2 - t1 > 1e-9) { cross(at(a, b, t1)); cross(at(a, b, t2)); } }
  }
  if (!state) { if (exitAngle === null) return null; if (firstEntry !== null) arc(exitAngle, firstEntry); }
  const deduped = []; for (const p of out) { const last = deduped[deduped.length - 1]; if (last && Math.abs(last[0] - p[0]) < 1e-6 && Math.abs(last[1] - p[1]) < 1e-6) last[2] = last[2] || p[2]; else deduped.push(p); }
  if (deduped.length > 1) { const first = deduped[0]; const last = deduped[deduped.length - 1]; if (Math.abs(last[0] - first[0]) < 1e-6 && Math.abs(last[1] - first[1]) < 1e-6) { first[2] = first[2] || last[2]; deduped.pop(); } }
  return deduped.length >= 3 ? deduped : null;
}

/** The cloud circle itself as arc samples (always a valid outline). */
export function circleRing(r) { const k = Math.max(8, Math.ceil(TAU / arcStep(r))); return Array.from({length: k}, (_, i) => [r * Math.cos((TAU * i) / k), r * Math.sin((TAU * i) / k), true]); }

/**
 * Greedy smallest-area vertex removal down to `cap` vertices.  Arc vertices are never
 * removed; a removal is taken only if the new edge cuts no margin disc (BUILD_TOLERANCE)
 * and crosses no other edge.  @returns ring | null when the cap cannot be met safely.
 */
function simplify(ring, cap, near) {
  const n = ring.length; const prev = ring.map((_, i) => (i - 1 + n) % n); const next = ring.map((_, i) => (i + 1) % n); const alive = ring.map(() => true); let count = n;
  const safe = i => {
    const ia = prev[i]; const ib = next[i]; const a = ring[ia]; const v = ring[i]; const b = ring[ib];
    if (orient(a, v, b) >= 0) { // convex (ccw): the triangle (a, v, b) is cut away
      const tri = [a, v, b];
      for (const [x, y, m] of near(Math.min(a[0], v[0], b[0]), Math.min(a[1], v[1], b[1]), Math.max(a[0], v[0], b[0]), Math.max(a[1], v[1], b[1]))) if (distSeg(x, y, a[0], a[1], b[0], b[1]) < m - BUILD_TOLERANCE || d3.polygonContains(tri, [x, y])) return false;
    }
    for (let j = next[ib]; j !== ia; j = next[j]) { const k = next[j]; if (k === ia) break; if (crosses(a, b, ring[j], ring[k])) return false; }
    return true;
  };
  // Batched Visvalingam sweeps: smallest triangle first; a vertex whose neighbour changed this sweep waits for the next one.
  while (count > cap) {
    const order = []; for (let i = 0; i < n; i += 1) if (alive[i] && !ring[i][2]) order.push([Math.abs(orient(ring[prev[i]], ring[i], ring[next[i]])), i]);
    order.sort((x, y) => x[0] - y[0] || x[1] - y[1]);
    const touched = new Set(); let removed = 0;
    for (const [, i] of order) {
      if (count <= cap) break; if (touched.has(i) || !safe(i)) continue;
      alive[i] = false; next[prev[i]] = next[i]; prev[next[i]] = prev[i]; touched.add(prev[i]); touched.add(next[i]); count -= 1; removed += 1;
    }
    if (!removed) return null;
  }
  const out = []; let start = 0; while (!alive[start]) start += 1; let i = start; do { out.push(ring[i]); i = next[i]; } while (i !== start);
  return out;
}

/** Clip to the circle, simplify to max(minCap, arc vertices), round to 2 decimals, validate.  @returns {path, arcVertices} | null */
function finish(ring, points, r, near, minCap) {
  const clipped = clipToCircle(ccw(ring), r); if (!clipped) return null;
  const arcVertices = clipped.reduce((sum, p) => sum + (p[2] ? 1 : 0), 0); const cap = Math.max(minCap, arcVertices);
  const reduced = clipped.length > cap ? simplify(clipped, cap, near) : clipped; if (!reduced) return null;
  const path = []; for (const [x, y] of reduced) { const p = [r2(x), r2(y)]; const last = path[path.length - 1]; if (!last || last[0] !== p[0] || last[1] !== p[1]) path.push(p); }
  if (path.length > 1 && path[0][0] === path[path.length - 1][0] && path[0][1] === path[path.length - 1][1]) path.pop();
  return checkOutline(path, points, r).ok ? {path, arcVertices} : null;
}

/** S1: density contour, threshold max/2 halved until one exterior ring holds every margin disc (strictly). */
function contourRing(points, discs, scale, iterations) {
  let minX = Infinity; let minY = Infinity; let maxX = -Infinity; let maxY = -Infinity;
  for (const [x, y] of points) { minX = Math.min(minX, x); minY = Math.min(minY, y); maxX = Math.max(maxX, x); maxY = Math.max(maxY, y); }
  const pad = POINT_RADIUS + MAX_MARGIN + 2; const ox = minX - pad; const oy = minY - pad;
  const cell = 2 ** Math.max(1, Math.floor(Math.log2(scale * CONTOUR_CELL_FACTOR))); const bandwidth = Math.max(CONTOUR_MIN_BANDWIDTH, scale * CONTOUR_BANDWIDTH_FACTOR);
  const contours = d3.contourDensity().x(p => p[0] - ox).y(p => p[1] - oy).size([maxX - minX + 2 * pad, maxY - minY + 2 * pad]).cellSize(cell).bandwidth(bandwidth).contours(points);
  let threshold = contours.max / 2;
  for (let iteration = 0; iteration < iterations; iteration += 1, threshold /= 2) {
    for (const polygon of contours(threshold).coordinates) {
      const ring = polygon[0].slice(0, -1).map(([x, y]) => [x + ox, y + oy]); // exterior only: holes dropped
      if (ring.length >= 3 && containsDiscs(ring, discs, 0)) return {ring, iterations: iteration + 1};
    }
  }
  return {ring: null, iterations};
}

const discHull = (discs, inflate) => d3.polygonHull(discs.flatMap(([x, y, m]) => Array.from({length: DISC_SAMPLES}, (_, k) => [x + Math.cos((TAU * k) / DISC_SAMPLES) * (m + inflate), y + Math.sin((TAU * k) / DISC_SAMPLES) * (m + inflate)])));

/** S2 smoothing: resample the inflated hull by arc length and sample the closed centripetal Catmull-Rom curve drawn through it. */
function splineRing(hull) {
  const ring = ccw(hull); const n = ring.length; let perimeter = 0; const lengths = [];
  for (let i = 0; i < n; i += 1) { const l = Math.hypot(ring[(i + 1) % n][0] - ring[i][0], ring[(i + 1) % n][1] - ring[i][1]); lengths.push(l); perimeter += l; }
  const count = Math.max(8, Math.min(32, Math.round(perimeter / SPLINE_SPACING))); const controls = []; let edge = 0; let walked = 0;
  for (let k = 0; k < count; k += 1) { const target = (perimeter * k) / count; while (walked + lengths[edge] < target && edge < n - 1) { walked += lengths[edge]; edge += 1; } const t = lengths[edge] ? (target - walked) / lengths[edge] : 0; const a = ring[edge]; const b = ring[(edge + 1) % n]; controls.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]); }
  const out = []; let cur = null;
  const context = {moveTo(x, y) { cur = [x, y]; }, lineTo(x, y) { out.push([x, y]); cur = [x, y]; }, closePath() {}, bezierCurveTo(ax, ay, bx, by, x, y) { for (const t of [0.5, 1]) { const u = 1 - t; out.push([u * u * u * cur[0] + 3 * u * u * t * ax + 3 * u * t * t * bx + t * t * t * x, u * u * u * cur[1] + 3 * u * u * t * ay + 3 * u * t * t * by + t * t * t * y]); } cur = [x, y]; }};
  d3.line().curve(d3.curveCatmullRomClosed.alpha(0.5)).context(context)(controls);
  return out;
}

/** One outline for a point set inside a cloud of radius r: tries `methods` in order, e.g. ['contour', 'soft-hull']. */
function outline(points, r, methods, options, stats) {
  const {discs, near} = discSet(points, r); const minCap = options.minVertexCap ?? MIN_VERTEX_CAP;
  for (const method of methods) {
    let result = null;
    if (method === 'contour') { const {ring} = contourRing(points, discs, options.contourScale ?? r, options.contourIterations ?? CONTOUR_ITERATIONS); result = ring && finish(ring, points, r, near, minCap); }
    else if (method === 'soft-hull') { result = finish(splineRing(discHull(discs, SPLINE_INFLATE)), points, r, near, minCap); if (!result) { stats.splineToHull += 1; result = finish(discHull(discs, HULL_INFLATE), points, r, near, minCap); } }
    else if (method === 'capsule') result = finish(discHull(discs, HULL_INFLATE), points, r, near, minCap);
    if (result) return {method, ...result};
    if (method === 'contour') stats.s1ToS2 += 1; else stats.s2ToCircle += 1;
  }
  return null;
}

const pathD = path => (path.length ? `M${path.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join('L')}Z` : '');
const bboxOf = (path, r) => { if (!path.length) return {minX: -r, minY: -r, maxX: r, maxY: r}; let minX = Infinity; let minY = Infinity; let maxX = -Infinity; let maxY = -Infinity; for (const [x, y] of path) { minX = Math.min(minX, x); minY = Math.min(minY, y); maxX = Math.max(maxX, x); maxY = Math.max(maxY, y); } return {minX, minY, maxX, maxY}; };

const byXY = (a, b) => a.x - b.x || a.y - b.y || (a.paperId < b.paperId ? -1 : a.paperId > b.paperId ? 1 : 0);

/**
 * Zoomed-in detail: one outline per LPA sub-group (`subgroup:other` skipped), same margin-disc
 * and inside-the-cloud-circle rules.  Members <= 7 -> capsule; contour clouds try S1 -> S2,
 * others S2.  A sub-group whose chain fails is omitted (no circle fallback) and counted.
 */
function subgroupOutlines(cloud, localPoints, kind, options, stats) {
  const r = cloud.r; const byPaper = new Map(localPoints.map(p => [String(p.paperId), p])); const out = [];
  for (const group of cloud.subgroups || []) {
    if (group.other || !group.paperIds?.length) continue;
    const members = group.paperIds.map(id => byPaper.get(String(id))).filter(Boolean).sort(byXY).map(p => [p.x, p.y]); if (members.length < 2) continue;
    let sx = 0; let sy = 0; for (const [x, y] of members) { sx += x; sy += y; } let extent = 0; for (const [x, y] of members) extent = Math.max(extent, Math.hypot(x - sx / members.length, y - sy / members.length));
    const chain = members.length <= CAPSULE_MAX_SIZE ? ['capsule'] : kind === 'contour' ? ['contour', 'soft-hull'] : ['soft-hull'];
    const groupStats = {s1ToS2: 0, s2ToCircle: 0, splineToHull: 0}; const result = outline(members, r, chain, {...options, contourScale: extent + POINT_RADIUS}, groupStats);
    if (result) out.push({id: group.id, method: result.method, path: result.path, d: pathD(result.path)}); else stats.subgroupsOmitted = (stats.subgroupsOmitted || 0) + 1;
  }
  return out;
}
const hasSubgroupOutlines = (kind, unfiled) => kind === 'contour' || (kind === 'soft-hull' && !unfiled);

/**
 * Outline of one cloud from LOCAL points [{paperId, x, y}] (origin = cloud centre).
 * `cloud` = {id, r, size, mode, edgeCount, unfiled, subgroups?: [{id, other, paperIds}]}.
 * Options: subgroups (default true), contourIterations, minVertexCap (tests/tuning).
 * @returns {cloudId, kind, unfiled, method, path, d, bbox, arcVertices, subgroups: [{id, method, path, d}]}
 */
export function cloudShape(cloud, localPoints, options = {}, stats = {s1ToS2: 0, s2ToCircle: 0, splineToHull: 0}) {
  const {kind, unfiled} = classifyCloud({...cloud, size: cloud.size ?? localPoints.length}); const r = cloud.r;
  const sorted = [...localPoints].sort(byXY); const points = sorted.map(p => [p.x, p.y]); let method = 'none'; let path = []; let arcVertices = 0;
  if (kind !== 'single') {
    const result = outline(points, r, kind === 'contour' ? ['contour', 'soft-hull'] : [kind], options, stats);
    if (result) ({method, path, arcVertices} = result); else { method = 'circle'; path = circleRing(r).map(([x, y]) => [r2(x), r2(y)]); arcVertices = path.length; }
  }
  const subgroups = options.subgroups !== false && hasSubgroupOutlines(kind, unfiled) ? subgroupOutlines(cloud, sorted, kind, options, stats) : [];
  return {cloudId: cloud.id, kind, unfiled, method, path, d: pathD(path), bbox: bboxOf(path, r), arcVertices, subgroups};
}

/** Pure even-odd test of a local point against a shape's outline (false when the shape has no outline). */
export const pointInOutline = (shape, x, y) => shape.path.length >= 3 && d3.polygonContains(shape.path, [x, y]);

const memo = new Map(); // key -> cloud shape without sub-groups; `${key}|sub|...` -> sub-group outlines
const freshStats = () => ({computed: 0, subgroupsComputed: 0, memoHits: 0, fallbacks: {s1ToS2: 0, s2ToCircle: 0}, subgroupsOmitted: 0, shapeMs: 0});
let counters = freshStats();
export const clearCloudShapeCache = () => memo.clear();
export const resetCloudShapeStats = () => { counters = freshStats(); };
/** Cumulative counters since the last reset: {computed, subgroupsComputed, memoHits, fallbacks: {s1ToS2, s2ToCircle}, subgroupsOmitted, shapeMs (last call)}. */
export const cloudShapeStats = () => ({...counters, fallbacks: {...counters.fallbacks}});
const remember = (key, value) => { memo.set(key, value); if (memo.size > MEMO_LIMIT) memo.delete(memo.keys().next().value); return value; };

/**
 * Outlines for every cloud of a `layoutCollectionClouds` result (order = layout.clouds).
 * Memo key: cloud id + spacing + hash(local coords to 1e-3, r, classification inputs, options);
 * sub-group outlines (option `subgroups`, default true) are memoised separately with their members.
 * @returns {shapes, stats: {computed, subgroupsComputed, memoHits, fallbacks: {s1ToS2, s2ToCircle}, subgroupsOmitted, shapeMs, methods}}
 */
export function computeCloudShapes(layout, options = {}) {
  const started = now(); const byCloud = new Map(layout.clouds.map(cloud => [cloud.id, []]));
  for (const p of layout.points) byCloud.get(p.cloudId)?.push(p);
  const call = {...freshStats(), methods: {}}; const optionKey = `${options.contourIterations ?? ''}|${options.minVertexCap ?? ''}`;
  const shapes = layout.clouds.map(cloud => {
    const local = byCloud.get(cloud.id).map(p => ({paperId: String(p.paperId), x: q3(p.x - cloud.cx), y: q3(p.y - cloud.cy)}));
    const coords = local.map(p => `${p.paperId}:${p.x},${p.y}`).sort().join(';'); const r = q3(cloud.r);
    const key = `${cloud.id}|${layout.spacing}|${stableHash(`${r}|${cloud.size}|${cloud.mode}|${cloud.edgeCount}|${Boolean(cloud.unfiled)}|${coords}|${optionKey}`)}`;
    const stats = {s1ToS2: 0, s2ToCircle: 0, splineToHull: 0}; const input = {...cloud, r};
    let base = memo.get(key);
    if (base) call.memoHits += 1; else { base = remember(key, cloudShape(input, local, {...options, subgroups: false}, stats)); call.computed += 1; }
    let subgroups = [];
    if (options.subgroups !== false && hasSubgroupOutlines(base.kind, base.unfiled)) {
      const subKey = `${key}|sub|${stableHash((cloud.subgroups || []).map(g => `${g.id}=${(g.paperIds || []).join(',')}`).join(';'))}`;
      subgroups = memo.get(subKey);
      if (subgroups) call.memoHits += 1; else { subgroups = remember(subKey, subgroupOutlines(input, [...local].sort(byXY), base.kind, options, stats)); call.subgroupsComputed += 1; }
    }
    call.fallbacks.s1ToS2 += stats.s1ToS2; call.fallbacks.s2ToCircle += stats.s2ToCircle; call.subgroupsOmitted += stats.subgroupsOmitted || 0;
    call.methods[base.method] = (call.methods[base.method] || 0) + 1;
    return {...base, subgroups};
  });
  call.shapeMs = now() - started;
  for (const k of ['computed', 'subgroupsComputed', 'memoHits', 'subgroupsOmitted']) counters[k] += call[k];
  counters.fallbacks.s1ToS2 += call.fallbacks.s1ToS2; counters.fallbacks.s2ToCircle += call.fallbacks.s2ToCircle; counters.shapeMs = call.shapeMs;
  return {shapes, stats: call};
}

export const AtlasCloudShapes = {MAX_MARGIN, CAPSULE_MAX_SIZE, CONTOUR_MIN_EDGES, CONTOUR_ITERATIONS, MIN_VERTEX_CAP, ARC_SAG, SHAPE_TOLERANCE, marginRadius, classifyCloud, checkOutline, clipToCircle, circleRing, cloudShape, computeCloudShapes, pointInOutline, clearCloudShapeCache, resetCloudShapeStats, cloudShapeStats};
if (typeof window !== 'undefined') window.AtlasCloudShapes = AtlasCloudShapes;
