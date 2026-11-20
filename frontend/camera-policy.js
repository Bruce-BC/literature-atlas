/**
 * Pure camera/position policy shared by Cytoscape and G6 views.
 *
 *  - same (view, epoch, recipe, scope, topology)      -> preserve positions/zoom/pan
 *  - first visit of a scope                           -> fit once
 *  - same scope, topology add/remove                  -> seed new nodes, keep camera,
 *                                                        offer explicit "Fit new content"
 *  - revisit of a valid scope                         -> restore stored camera
 *  - epoch/recipe change                              -> drop everything, fit
 * Zoom is always clamped to [0.2, 3]; pan is bounded by `clampPan` (nodes never leave the viewport entirely).
 */
export const ZOOM_RANGE = Object.freeze([0.2, 3]);
export const clampZoom = value => { const n = Number(value); return Number.isFinite(n) ? Math.max(ZOOM_RANGE[0], Math.min(ZOOM_RANGE[1], n)) : 1; };
export const PAN_MIN_VISIBLE = 48;
/**
 * Bound pan so the node bbox (model coords; screen = model * zoom + pan) overlaps the viewport on each axis by at least
 * min(48, bbox screen width, bbox screen height, viewport width, viewport height) px (AC-14 floor) AND by
 * min(half the bbox screen extent, viewport extent): the bbox centre stays on screen, and a bbox larger than the viewport always covers it
 * (its edges stay reachable).  A 48 px bbox corner alone is often empty of nodes.
 * Invalid viewport/bbox/zoom (no nodes, hidden container) leaves a finite pan unchanged; a non-finite pan becomes the bbox-centred (or zero) pan.  Idempotent.
 */
export function clampPan({viewport, bbox, zoom, pan} = {}) {
  const width = Number(viewport?.width); const height = Number(viewport?.height); const z = Number(zoom); const [x1, y1, x2, y2] = [bbox?.x1, bbox?.y1, bbox?.x2, bbox?.y2].map(Number);
  const valid = [width, height, z, x1, y1, x2, y2].every(Number.isFinite) && width > 0 && height > 0 && z > 0 && x2 >= x1 && y2 >= y1;
  const px = Number(pan?.x); const py = Number(pan?.y);
  const current = {x: Number.isFinite(px) ? px : (valid ? width / 2 - (x1 + x2) / 2 * z : 0), y: Number.isFinite(py) ? py : (valid ? height / 2 - (y1 + y2) / 2 * z : 0)};
  if (!valid) return current;
  const floor = Math.min(PAN_MIN_VISIBLE, (x2 - x1) * z, (y2 - y1) * z, width, height);
  const axis = (value, lo, hi, size) => { const need = Math.max(floor, Math.min((hi - lo) * z / 2, size)); return Math.max(need - hi * z, Math.min(size - need - lo * z, value)); };
  return {x: axis(current.x, x1, x2, width), y: axis(current.y, y1, y2, height)};
}

export const topologySignature = (nodeIds, edgeRows = []) => {
  const ids = [...nodeIds].map(String).sort(); const edges = edgeRows.map(row => Array.isArray(row) ? row.join('>') : String(row)).sort();
  let hash = 2166136261; for (const part of [...ids, '|', ...edges]) { for (let i = 0; i < part.length; i += 1) { hash ^= part.charCodeAt(i); hash = Math.imul(hash, 16777619); } hash ^= 124; hash = Math.imul(hash, 16777619); }
  return `${ids.length}:${edges.length}:${(hash >>> 0).toString(16)}`;
};

export function createCameraStore() { return {views: new Map()}; }

const viewEntry = (store, view) => { let entry = store.views.get(view); if (!entry) { entry = {epochKey: null, scopes: new Map(), current: null}; store.views.set(view, entry); } return entry; };

/**
 * Decide what to do for the next render of `view`.
 * @returns {{action:'fit'|'preserve'|'restore'|'seed-keep', reason:string, camera:object|null, positions:object|null, fitNewContentAvailable:boolean}}
 */
export function planCamera(store, {view, epochKey, scopeKey, signature}) {
  const entry = viewEntry(store, view);
  if (entry.epochKey !== epochKey) { const reset = entry.epochKey !== null; entry.scopes.clear(); entry.epochKey = epochKey; entry.scopes.set(scopeKey, {signature, camera: null, positions: null}); entry.current = scopeKey; return {action: 'fit', reason: reset ? 'epoch-or-recipe-reset' : 'first-visit', camera: null, positions: null, fitNewContentAvailable: false}; }
  const saved = entry.scopes.get(scopeKey);
  if (!saved) { entry.scopes.set(scopeKey, {signature, camera: null, positions: null}); entry.current = scopeKey; return {action: 'fit', reason: 'first-visit', camera: null, positions: null, fitNewContentAvailable: false}; }
  const revisit = entry.current !== scopeKey; entry.current = scopeKey;
  if (saved.signature === signature) return {action: revisit && saved.camera ? 'restore' : 'preserve', reason: revisit ? 'revisit' : 'same-signature', camera: saved.camera, positions: saved.positions, fitNewContentAvailable: false};
  saved.signature = signature;
  if (!saved.camera && !saved.positions) return {action: 'fit', reason: 'first-visit', camera: null, positions: null, fitNewContentAvailable: false};
  return {action: 'seed-keep', reason: 'topology-changed', camera: saved.camera, positions: saved.positions, fitNewContentAvailable: true};
}

/** Remember the live camera/positions for the current scope of `view`. */
export function saveCamera(store, {view, scopeKey, camera, positions}) {
  const entry = viewEntry(store, view); const saved = entry.scopes.get(scopeKey); if (!saved) return;
  if (camera) saved.camera = {...camera, zoom: clampZoom(camera.zoom), ...(camera.pan ? {pan: {...camera.pan}} : {}), ...(camera.position ? {position: [...camera.position]} : {})};
  if (positions) saved.positions = positions;
}
export const resetCameraView = (store, view) => store.views.delete(view);

const finitePoint = point => point && Number.isFinite(point.x) && Number.isFinite(point.y);
const hashUnit = (text, salt) => { let hash = 2166136261 ^ salt; for (let i = 0; i < text.length; i += 1) { hash ^= text.charCodeAt(i); hash = Math.imul(hash, 16777619); } return (hash >>> 0) / 4294967296; };

/**
 * Return finite, unique positions for every node.  Existing finite positions
 * are kept byte-for-byte; missing ones are seeded deterministically beside an
 * already-positioned neighbour (or on a spiral around the centroid), so a
 * partial preset can never stack nodes at (0,0).
 */
export function completePositions(existing, nodeIds, edges = [], {spacing = 36} = {}) {
  const positions = {}; const missing = [];
  for (const id of nodeIds) { if (finitePoint(existing?.[id])) positions[id] = {x: existing[id].x, y: existing[id].y}; else missing.push(id); }
  const placed = Object.values(positions); const centroid = placed.length ? {x: placed.reduce((s, p) => s + p.x, 0) / placed.length, y: placed.reduce((s, p) => s + p.y, 0) / placed.length} : {x: 0, y: 0};
  const neighbors = new Map(); for (const [a, b] of edges) { (neighbors.get(a) || neighbors.set(a, []).get(a)).push(b); (neighbors.get(b) || neighbors.set(b, []).get(b)).push(a); }
  const taken = new Set(Object.values(positions).map(p => `${Math.round(p.x / 4)},${Math.round(p.y / 4)}`));
  let spiral = 0;
  for (const id of [...missing].sort()) {
    const anchorId = (neighbors.get(id) || []).find(other => positions[other]); const anchor = anchorId ? positions[anchorId] : centroid;
    let angle = hashUnit(id, 7) * Math.PI * 2; let radius = anchorId ? spacing : spacing * (1 + Math.sqrt(placed.length + spiral));
    let point; let guard = 0;
    do { point = {x: anchor.x + Math.cos(angle) * radius, y: anchor.y + Math.sin(angle) * radius}; angle += 2.399963; radius += guard % 6 === 5 ? spacing * 0.35 : 0; guard += 1; } while (taken.has(`${Math.round(point.x / 4)},${Math.round(point.y / 4)}`) && guard < 200);
    positions[id] = point; taken.add(`${Math.round(point.x / 4)},${Math.round(point.y / 4)}`); spiral += 1;
  }
  return positions;
}

/** True when every node has a finite position (Cytoscape preset precondition). */
export const hasFullCoverage = (positions, nodeIds) => Boolean(positions) && nodeIds.length > 0 && nodeIds.every(id => finitePoint(positions[id]));
export const positionStats = (positions, nodeIds) => {
  const keys = new Set(); let minX = Infinity; let maxX = -Infinity; let minY = Infinity; let maxY = -Infinity; let origin = 0;
  for (const id of nodeIds) { const p = positions[id]; if (!finitePoint(p)) return {finite: false, unique: 0, origin: 0, bounds: null}; keys.add(`${p.x.toFixed(2)},${p.y.toFixed(2)}`); if (Math.abs(p.x) < 1e-9 && Math.abs(p.y) < 1e-9) origin += 1; minX = Math.min(minX, p.x); maxX = Math.max(maxX, p.x); minY = Math.min(minY, p.y); maxY = Math.max(maxY, p.y); }
  return {finite: true, unique: keys.size, origin, bounds: {minX, maxX, minY, maxY}};
};

export const AtlasCameraPolicy = {ZOOM_RANGE, clampZoom, PAN_MIN_VISIBLE, clampPan, topologySignature, createCameraStore, planCamera, saveCamera, resetCameraView, completePositions, hasFullCoverage, positionStats};
if (typeof window !== 'undefined') window.AtlasCameraPolicy = AtlasCameraPolicy;
