import {placeCloudLabels, estimateLabelWidth} from './collection-clouds.js';
import {computeCloudShapes} from './cloud-shapes.js';
import {TIER_STYLE} from './edge-strength.js';

/**
 * Wrapper-owned collection-cloud overlay.  It is a SIBLING of the G6 container
 * inside their common `position: relative` parent (never a child: the container
 * owns click hit-testing, keydown and `role="listbox"`).  Cloud outlines (`<path>`s
 * from cloud-shapes.js, LOCAL geometry placed by a per-cloud translate) sit in an
 * SVG UNDER the transparent canvas (`pointer-events: none`); cloud labels are
 * `<button>`s ABOVE it (Tab + Enter/Space).  Each hull keeps its model circle in
 * `data-cx/cy/r` (hit-testing and label anchors stay on the circle).  Colours are
 * per-element CSS variables, so a theme/palette change swaps variables only and
 * never touches the outline geometry.  Labels are placed once at the Fit camera
 * (`placeCloudLabels`), then anchored to their cloud and only translated/scaled
 * with the camera; overlap visibility is re-evaluated only when the zoom changes.
 * Hidden labels are listed in a 'cloud list' chip row (hidden count == chip count).
 * All user text is set with `textContent`/attributes.
 * Semantic edge layers (plan motion-and-edges §2.2), bottom -> top inside the same world
 * transform: aggregate bridges -> hull outlines -> paper lines (intra-cloud + the selected
 * paper's) -> (canvas points) -> labels.  What is drawn comes ONLY from the model's
 * `edgeView` (= `edgesForState`); widths are screen px (`vector-effect: non-scaling-stroke`).
 */
const NS = 'http://www.w3.org/2000/svg';
const LABEL_GAP = 1; // px kept free on each side of a label box, so sub-pixel camera drift never makes neighbours touch
const DIRECTIONS = {top: [0, -1], bottom: [0, 1], right: [1, 0], left: [-1, 0]};
const nextFrame = callback => typeof requestAnimationFrame === 'function' ? requestAnimationFrame(callback) : setTimeout(callback, 16);
const cancelFrame = handle => { if (typeof cancelAnimationFrame === 'function') cancelAnimationFrame(handle); else clearTimeout(handle); };
/** Zoom (canvas -> screen scale) from which LPA sub-group outlines are shown; computed lazily the first time it is crossed (memoised). */
export const SUBGROUP_MIN_ZOOM = 1.6;
/** Label weight tier (1 = heaviest) by size rank: thirds of the clouds ordered size desc, id asc.  Drawn as a glyph stroke (styles.css), which keeps advance widths, so it never feeds label placement. */
export const labelWeightTier = (rank, count) => (rank < Math.ceil(count / 3) ? 1 : rank < Math.ceil((2 * count) / 3) ? 2 : 3);
let overlaySeq = 0; // instance-unique <defs> id prefix (several Obsidian windows may host an atlas)

/**
 * Anchor placed labels to their clouds (pure).  A side label keeps a fixed screen
 * offset from the circle point on its side; a leader label is pinned to the canvas
 * point under its centre.  `camera` = {zoom, x, y} used for the placement.
 * @returns Array<{cloudId, ax, ay, dx, dy, width, height, baseHidden, leader, fromX?, fromY?}> in placement order
 */
export function anchorLabels(labels, clouds, {zoom, x, y}) {
  const byId = new Map(clouds.map(cloud => [cloud.id, cloud]));
  return labels.map(label => {
    const cloud = byId.get(label.cloudId); const base = {cloudId: label.cloudId, width: label.width, height: label.height, baseHidden: Boolean(label.hidden), leader: Boolean(label.leader)};
    if (label.leader) { const ax = (label.x + label.width / 2 - x) / zoom; const ay = (label.y + label.height / 2 - y) / zoom; return {...base, ax, ay, dx: -label.width / 2, dy: -label.height / 2, fromX: (label.leaderFrom.x - x) / zoom, fromY: (label.leaderFrom.y - y) / zoom}; }
    const [ux, uy] = DIRECTIONS[label.anchor] || [0, 0]; const ax = cloud.cx + ux * cloud.r; const ay = cloud.cy + uy * cloud.r;
    return {...base, ax, ay, dx: label.x - (x + ax * zoom), dy: label.y - (y + ay * zoom)};
  });
}

/** Screen box of an anchored label under `camera` (the drawn button, inset by LABEL_GAP). */
export const labelBox = (entry, {zoom, x, y}) => ({x: x + entry.ax * zoom + entry.dx + LABEL_GAP, y: y + entry.ay * zoom + entry.dy, width: Math.max(0, entry.width - 2 * LABEL_GAP), height: entry.height});

/** Greedy visibility in placement order (size desc, id asc): a label hides when it overlaps an earlier visible one.  Returns the set of hidden cloud IDs. */
export function hiddenAfterZoom(entries, camera) {
  const visible = []; const hidden = new Set(); const overlaps = (a, b) => a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;
  for (const entry of entries) { if (entry.baseHidden) { hidden.add(entry.cloudId); continue; } const box = labelBox(entry, camera); if (visible.some(other => overlaps(box, other))) hidden.add(entry.cloudId); else visible.push(box); }
  return hidden;
}

/** Bridge widths (screen px) by the engine's `level` 1..3. */
export const BRIDGE_WIDTHS = Object.freeze([1.5, 3, 4.5]);
export const BRIDGE_BEND = 0.12; // perpendicular control offset of a bridge, as a share of its length (bridge id order fixes the side)
const fmt = value => String(Math.round(value * 100) / 100);
/**
 * Pure line geometry in canvas coordinates.  Intra-cloud (`centres` null or the same cloud) -> straight `M L`;
 * inter-cloud -> quadratic whose control point is the midpoint of the two cloud centres (deterministic, no gradients).
 */
export function edgePathD(from, to, centres = null) {
  if (!centres || (centres[0].cx === centres[1].cx && centres[0].cy === centres[1].cy)) return `M${fmt(from.x)} ${fmt(from.y)}L${fmt(to.x)} ${fmt(to.y)}`;
  const cx = (centres[0].cx + centres[1].cx) / 2; const cy = (centres[0].cy + centres[1].cy) / 2;
  return `M${fmt(from.x)} ${fmt(from.y)}Q${fmt(cx)} ${fmt(cy)} ${fmt(to.x)} ${fmt(to.y)}`;
}
/** Bridge between two cloud centres: quadratic bent by BRIDGE_BEND of its length to the left of source -> target. */
export function bridgePathD(source, target) {
  const dx = target.cx - source.cx; const dy = target.cy - source.cy; const mx = (source.cx + target.cx) / 2 - dy * BRIDGE_BEND; const my = (source.cy + target.cy) / 2 + dx * BRIDGE_BEND;
  return `M${fmt(source.cx)} ${fmt(source.cy)}Q${fmt(mx)} ${fmt(my)} ${fmt(target.cx)} ${fmt(target.cy)}`;
}

/** Overlay factory for `renderG6Network({overlay})`. */
export function createCloudOverlay({graph, container}) {
  const doc = container.ownerDocument || globalThis.document; const host = container.parentElement;
  const svg = doc.createElementNS(NS, 'svg'); svg.setAttribute('class', 'atlas-cloud-hulls'); svg.setAttribute('aria-hidden', 'true');
  const world = doc.createElementNS(NS, 'g'); const hullGroup = doc.createElementNS(NS, 'g'); const replicaGroup = doc.createElementNS(NS, 'g'); const leaderGroup = doc.createElementNS(NS, 'g'); world.setAttribute('class', 'atlas-cloud-world');
  const prefix = `atlas-cloud-${overlaySeq += 1}`; svg.dataset.idPrefix = prefix; const defs = doc.createElementNS(NS, 'defs'); const hatch = doc.createElementNS(NS, 'pattern');
  for (const [key, value] of [['id', `${prefix}-hatch`], ['patternUnits', 'userSpaceOnUse'], ['width', '6'], ['height', '6'], ['patternTransform', 'rotate(45)']]) hatch.setAttribute(key, value);
  const hatchBg = doc.createElementNS(NS, 'rect'); hatchBg.setAttribute('class', 'atlas-cloud-hatch-bg'); hatchBg.setAttribute('width', '6'); hatchBg.setAttribute('height', '6'); const hatchLine = doc.createElementNS(NS, 'line'); hatchLine.setAttribute('class', 'atlas-cloud-hatch-line'); for (const [key, value] of [['x1', 0], ['y1', 0], ['x2', 0], ['y2', 6]]) hatchLine.setAttribute(key, String(value));
  hatch.append(hatchBg, hatchLine); defs.append(hatch);
  const bridgeGroup = doc.createElementNS(NS, 'g'); bridgeGroup.setAttribute('class', 'atlas-cloud-bridges'); const edgeGroup = doc.createElementNS(NS, 'g'); edgeGroup.setAttribute('class', 'atlas-cloud-edges'); hullGroup.setAttribute('class', 'atlas-cloud-hull-layer');
  const motionLayer = doc.createElementNS(NS, 'g'); motionLayer.setAttribute('class', 'atlas-motion-layer'); // transition decorations (plan motion-and-edges §3.4), under the canvas like the lines
  world.append(bridgeGroup, hullGroup, edgeGroup, replicaGroup, motionLayer); svg.append(defs, world, leaderGroup); host.insertBefore(svg, container);
  const labelLayer = doc.createElement('div'); labelLayer.className = 'atlas-cloud-labels'; labelLayer.id = 'atlas-cloud-overlay'; labelLayer.setAttribute('role', 'group'); labelLayer.setAttribute('aria-label', 'Collection clouds'); labelLayer.dataset.selectedCloud = ''; container.after(labelLayer);
  const chipRow = doc.createElement('div'); chipRow.className = 'atlas-cloud-chips'; chipRow.id = 'atlas-cloud-chips'; chipRow.setAttribute('role', 'group'); chipRow.setAttribute('aria-label', '구름 목록'); chipRow.hidden = true; host.after(chipRow);
  let model = null; let signature = null; let clouds = []; let hulls = new Map(); let groups = new Map(); let buttons = new Map(); let entries = []; let hidden = new Set(); let hoverId = null;
  let subgroupsDrawn = false; let detail = false; let edgeKey = null; let pointById = new Map(); let cloudById = new Map();
  let isReady = false; let destroyed = false; let needsPlacement = true; let lastZoom = null; let following = false; let frame = null; let measureContext = null;
  let decor = {anims: [], cleanups: [], generation: 0}; let maskSeq = 0;
  const stats = {placements: 0, syncs: 0, reevaluations: 0, replicaLines: 0, hoverPaperId: null, hoverCloudId: null, hoverOutsideOutline: false, idPrefix: prefix, subgroupsVisible: false, subgroupOutlines: 0, subgroupDraws: 0, edges: {strong: 0, medium: 0, weak: 0, total: 0, dimmed: 0, intra: 0, bridges: 0, bridgeEmphasis: 0, bridgeDimmed: 0, draws: 0}};
  const select = (cloudId, source) => { if (!destroyed) model?.onSelectCloud?.(cloudId, source); };
  const measure = text => {
    const probe = buttons.values().next().value; if (!probe || typeof getComputedStyle !== 'function') return estimateLabelWidth(text) + 2 * LABEL_GAP;
    measureContext ||= doc.createElement('canvas').getContext?.('2d') || null; if (!measureContext) return estimateLabelWidth(text) + 2 * LABEL_GAP;
    const style = getComputedStyle(probe); measureContext.font = style.font || `${style.fontSize} ${style.fontFamily}`;
    const chrome = ['paddingLeft', 'paddingRight', 'borderLeftWidth', 'borderRightWidth'].reduce((sum, key) => sum + (parseFloat(style[key]) || 0), 0);
    return Math.ceil(measureContext.measureText(String(text)).width + chrome + 1) + 2 * LABEL_GAP;
  };

  const rebuild = () => {
    clearDecorations(); hullGroup.replaceChildren(); labelLayer.replaceChildren(); hulls = new Map(); groups = new Map(); buttons = new Map(); hoverId = null;
    for (const cloud of clouds) {
      const group = doc.createElementNS(NS, 'g'); group.setAttribute('class', 'atlas-cloud'); group.setAttribute('transform', `translate(${cloud.cx} ${cloud.cy})`); group.dataset.cloudId = cloud.id;
      const path = doc.createElementNS(NS, 'path'); path.setAttribute('d', cloud.d || ''); // the 1-paper kind has no outline (empty d)
      for (const [key, value] of [['cloudId', cloud.id], ['cx', cloud.cx], ['cy', cloud.cy], ['r', cloud.r], ['kind', cloud.kind || 'circle'], ['method', cloud.method || 'none']]) path.dataset[key] = String(value);
      const subgroupLayer = doc.createElementNS(NS, 'g'); subgroupLayer.setAttribute('class', 'atlas-cloud-subgroups');
      group.append(path, subgroupLayer); hullGroup.append(group); hulls.set(cloud.id, path); groups.set(cloud.id, group);
    }
    subgroupsDrawn = false; stats.subgroupOutlines = 0;
    const ranked = [...clouds].sort((a, b) => b.size - a.size || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
    for (const [rank, cloud] of ranked.entries()) {
      const button = doc.createElement('button'); button.dataset.weightTier = String(labelWeightTier(rank, ranked.length)); button.type = 'button'; button.className = `atlas-cloud-label${cloud.unfiled ? ' is-unfiled' : ''}`; button.dataset.cloudId = cloud.id; button.textContent = cloud.label; button.title = cloud.label; button.setAttribute('aria-label', `${cloud.label} · ${cloud.size} papers`); button.setAttribute('aria-pressed', 'false');
      button.addEventListener('click', () => select(cloud.id, 'label')); labelLayer.append(button); buttons.set(cloud.id, button);
    }
    entries = []; hidden = new Set(); needsPlacement = true; edgeKey = null; pointById = new Map((model?.points || []).map(point => [String(point.id), point])); cloudById = new Map(clouds.map(cloud => [cloud.id, cloud]));
  };
  /** Per-element CSS variables from the cloud tones (no redraw, no geometry change). */
  const applyColors = () => {
    for (const cloud of clouds) {
      const colors = cloud.colors || {}; const group = groups.get(cloud.id); const button = buttons.get(cloud.id);
      for (const [name, value] of [['--cloud-fill', colors.fill], ['--cloud-stroke', colors.stroke], ['--cloud-fill-selected', colors.selectedFill]]) if (value) { group.style.setProperty(name, value); button.style.setProperty(name, value); }
      if (cloud.unfiled) { group.style.setProperty('--cloud-hatch', `url(#${prefix}-hatch)`); if (colors.fill) svg.style.setProperty('--cloud-unfiled-fill', colors.fill); if (colors.stroke) svg.style.setProperty('--cloud-unfiled-stroke', colors.stroke); }
    }
  };
  /** Edge layers from `model.edgeView` only; rebuilt when the drawn set changes (never per camera frame — the world transform moves them). */
  const drawEdges = () => {
    const view = model?.edgeView || {edges: [], bridges: []}; const key = `${signature}\u0001${view.edges.map(e => `${e.id}|${e.tier}|${e.fromPointId}|${e.toPointId}|${e.dimmed ? 1 : 0}`).join(',')}\u0001${view.bridges.map(b => `${b.id}|${b.level}|${b.emphasis ? 1 : 0}|${b.dimmed ? 1 : 0}`).join(',')}`;
    if (key === edgeKey) return; edgeKey = key; bridgeGroup.replaceChildren(); edgeGroup.replaceChildren();
    const counts = {strong: 0, medium: 0, weak: 0, total: 0, dimmed: 0, intra: 0, bridges: 0, bridgeEmphasis: 0, bridgeDimmed: 0, draws: stats.edges.draws + 1};
    for (const bridge of view.bridges) {
      const a = cloudById.get(bridge.source); const b = cloudById.get(bridge.target); if (!a || !b) continue; const path = doc.createElementNS(NS, 'path');
      path.setAttribute('class', `atlas-cloud-bridge${bridge.emphasis ? ' is-emphasis' : ''}${bridge.dimmed ? ' is-dimmed' : ''}`); path.setAttribute('d', bridgePathD(a, b)); path.setAttribute('stroke-width', String(BRIDGE_WIDTHS[Math.min(BRIDGE_WIDTHS.length, Math.max(1, bridge.level)) - 1]));
      path.dataset.bridgeId = bridge.id; path.dataset.level = String(bridge.level); path.dataset.weight = String(bridge.weight); bridgeGroup.append(path); counts.bridges += 1; if (bridge.emphasis) counts.bridgeEmphasis += 1; if (bridge.dimmed) counts.bridgeDimmed += 1;
    }
    for (const edge of [...view.edges].reverse()) { // engine order is strong -> weak (strength desc): reversed, weak paints first and the strongest line ends on top
      const from = pointById.get(edge.fromPointId); const to = pointById.get(edge.toPointId); const style = TIER_STYLE[edge.tier]; if (!from || !to || !style) continue; const path = doc.createElementNS(NS, 'path');
      const intra = String(from.cloudId) === String(to.cloudId); const centres = intra ? null : [cloudById.get(String(from.cloudId)), cloudById.get(String(to.cloudId))]; path.setAttribute('d', edgePathD(from, to, centres && centres.every(Boolean) ? centres : null));
      path.setAttribute('class', `atlas-cloud-edge${edge.dimmed ? ' is-dimmed' : ''}`); path.setAttribute('stroke-width', String(style.width)); path.setAttribute('stroke-opacity', String(style.opacity)); if (style.dash) path.setAttribute('stroke-dasharray', style.dash.join(' '));
      Object.assign(path.dataset, {edgeId: edge.id, tier: edge.tier, from: edge.fromPointId, to: edge.toPointId, intra: String(intra), cloudId: intra ? String(from.cloudId) : ''}); edgeGroup.append(path);
      counts[edge.tier] += 1; counts.total += 1; if (edge.dimmed) counts.dimmed += 1; if (intra) counts.intra += 1;
    }
    stats.edges = counts; applyEdgeColors();
  };
  /** Intra-cloud lines take their cloud's stroke tone; inter-cloud lines and bridges keep the neutral theme tone (CSS `--atlas-muted`). */
  const applyEdgeColors = () => { for (const path of edgeGroup.children) { const stroke = path.dataset.cloudId ? cloudById.get(path.dataset.cloudId)?.colors?.stroke : null; if (stroke) path.style.setProperty('--edge-stroke', stroke); else path.style.removeProperty('--edge-stroke'); } };
  const applySelection = () => {
    const selectedId = model?.selectedCloudId || null; labelLayer.dataset.selectedCloud = selectedId || '';
    for (const [id, path] of hulls) path.setAttribute('class', `atlas-cloud-hull${clouds.find(cloud => cloud.id === id)?.unfiled ? ' is-unfiled' : ''}${selectedId === id ? ' is-selected' : selectedId ? ' is-dimmed' : ''}${hoverId === id ? ' is-hover' : ''}`);
    for (const [id, button] of buttons) { button.setAttribute('aria-pressed', String(selectedId === id)); button.classList.toggle('is-dimmed', Boolean(selectedId && selectedId !== id)); }
    for (const chip of chipRow.querySelectorAll('button')) chip.setAttribute('aria-pressed', String(selectedId === chip.dataset.cloudId));
  };
  /** Zoomed-in detail: sub-group outlines (LOCAL, `subgroup:other` has none) drawn once per layout, then only shown/hidden by a class (150 ms opacity). */
  const drawSubgroups = () => {
    if (!model?.layout) return; const {shapes} = computeCloudShapes(model.layout); let count = 0; stats.subgroupDraws += 1;
    for (const shape of shapes) { const layer = groups.get(shape.cloudId)?.querySelector('.atlas-cloud-subgroups'); if (!layer) continue; layer.replaceChildren(); for (const sub of shape.subgroups) { const path = doc.createElementNS(NS, 'path'); path.setAttribute('class', 'atlas-cloud-subgroup'); path.setAttribute('d', sub.d); path.dataset.subgroupId = sub.id; path.dataset.method = sub.method; layer.append(path); count += 1; } }
    subgroupsDrawn = true; stats.subgroupOutlines = count;
  };
  const applyDetail = zoom => { detail = zoom >= SUBGROUP_MIN_ZOOM; if (detail && !subgroupsDrawn) drawSubgroups(); svg.classList.toggle('is-detail', detail); stats.subgroupsVisible = detail; };
  const camera = () => { const zoom = graph.getZoom(); const origin = graph.getViewportByCanvas([0, 0]); return {zoom, x: origin[0], y: origin[1]}; };
  const place = cam => {
    const width = container.clientWidth; const height = container.clientHeight;
    const labels = placeCloudLabels({clouds, points: model.points}, cam.zoom, {width, height, padding: 0}, measure, cam);
    entries = anchorLabels(labels, clouds, cam); hidden = new Set(entries.filter(entry => entry.baseHidden).map(entry => entry.cloudId)); lastZoom = cam.zoom; needsPlacement = false; stats.placements += 1;
    for (const entry of entries) { const button = buttons.get(entry.cloudId); button.style.width = `${Math.max(0, entry.width - 2 * LABEL_GAP)}px`; button.dataset.anchor = entry.leader ? 'leader' : labels.find(label => label.cloudId === entry.cloudId).anchor; }
  };
  const renderChips = () => {
    const ids = entries.filter(entry => hidden.has(entry.cloudId)).map(entry => entry.cloudId); const current = [...chipRow.querySelectorAll('button')].map(chip => chip.dataset.cloudId);
    if (ids.join('\u0000') !== current.join('\u0000')) {
      chipRow.replaceChildren(); const title = doc.createElement('span'); title.className = 'atlas-cloud-chips-title'; title.textContent = '구름 목록'; chipRow.append(title);
      for (const id of ids) { const cloud = clouds.find(row => row.id === id); const chip = doc.createElement('button'); chip.type = 'button'; chip.className = 'atlas-cloud-chip'; chip.dataset.cloudId = id; chip.textContent = `${cloud.label} (${cloud.size})`; chip.title = cloud.label; chip.addEventListener('click', () => select(id, 'chip')); chipRow.append(chip); }
    }
    chipRow.hidden = ids.length === 0; chipRow.dataset.hiddenClouds = String(ids.length); applySelection();
  };
  const sync = () => {
    if (!isReady || destroyed || !model) return; stats.syncs += 1;
    const box = {left: container.offsetLeft, top: container.offsetTop, width: container.clientWidth, height: container.clientHeight};
    for (const layer of [svg, labelLayer]) { layer.style.left = `${box.left}px`; layer.style.top = `${box.top}px`; layer.style.width = `${box.width}px`; layer.style.height = `${box.height}px`; }
    svg.setAttribute('width', String(box.width)); svg.setAttribute('height', String(box.height));
    const cam = camera(); world.setAttribute('transform', `matrix(${cam.zoom} 0 0 ${cam.zoom} ${cam.x} ${cam.y})`); applyDetail(cam.zoom);
    if (needsPlacement) place(cam); else if (Math.abs(cam.zoom - lastZoom) > 1e-9) { hidden = hiddenAfterZoom(entries, cam); lastZoom = cam.zoom; stats.reevaluations += 1; }
    leaderGroup.replaceChildren();
    for (const entry of entries) {
      const button = buttons.get(entry.cloudId); const isHidden = hidden.has(entry.cloudId); button.hidden = isHidden; button.dataset.hidden = String(isHidden); if (isHidden) continue;
      const labelAt = labelBox(entry, cam); button.style.transform = `translate(${labelAt.x}px, ${labelAt.y}px)`;
      if (entry.leader) { const fromX = cam.x + entry.fromX * cam.zoom; const fromY = cam.y + entry.fromY * cam.zoom; const toX = Math.min(Math.max(fromX, labelAt.x), labelAt.x + labelAt.width); const toY = Math.min(Math.max(fromY, labelAt.y), labelAt.y + labelAt.height); const line = doc.createElementNS(NS, 'line'); line.setAttribute('class', 'atlas-cloud-leader'); for (const [key, value] of [['x1', fromX], ['y1', fromY], ['x2', toX], ['y2', toY]]) line.setAttribute(key, String(value)); line.dataset.cloudId = entry.cloudId; leaderGroup.append(line); }
    }
    renderChips();
  };
  /** AE-10: the hovered cloud = the circle under the pointer (the click hit-test); its outline is highlighted, also between outline and circle. */
  const setHover = (id, outside) => { stats.hoverCloudId = id; stats.hoverOutsideOutline = outside; if (id === hoverId) return; const previous = hoverId; hoverId = id; for (const key of [previous, id]) hulls.get(key)?.classList.toggle('is-hover', key === id && id !== null); };
  const pointerMove = event => {
    if (!isReady || destroyed || !model) return; const [x, y] = graph.getCanvasByClient([event.clientX, event.clientY]); const cloud = clouds.find(row => Math.hypot(x - row.cx, y - row.cy) <= row.r) || null;
    const path = cloud ? hulls.get(cloud.id) : null; const point = path && typeof DOMPoint === 'function' ? new DOMPoint(x - cloud.cx, y - cloud.cy) : null;
    setHover(cloud?.id || null, Boolean(path && cloud.d && point && typeof path.isPointInFill === 'function' && !path.isPointInFill(point)));
  };
  const pointerLeave = () => setHover(null, false);
  container.addEventListener('pointermove', pointerMove); container.addEventListener('pointerleave', pointerLeave);
  const tick = () => { frame = null; if (!following || destroyed) return; sync(); frame = nextFrame(tick); };
  const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(() => { if (!isReady || destroyed) return; needsPlacement = true; sync(); }) : null; observer?.observe(container);

  // ---- transition decorations (driven by motion-driver.js with a motion.js timeline; never part of the committed state) ----
  // Every decoration is removable at once (clearDecorations: new input, forced finish, reset); masks reveal/retract lines from a focus
  // point (radial reveal: the dashed weak tier ends in its exact dash pattern because the mask never touches stroke-dasharray).
  function clearDecorations() { const current = decor; decor = {anims: [], cleanups: [], generation: current.generation + 1}; for (const anim of current.anims) { try { anim.cancel(); } catch { /* already gone */ } } for (const cleanup of current.cleanups) cleanup(); }
  const svgEl = (tag, attrs = {}) => { const node = doc.createElementNS(NS, tag); for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value)); return node; };
  const timing = (track, extra = {}) => ({delay: track.start, duration: Math.max(1, track.duration), easing: 'ease-out', fill: 'both', ...extra});
  const pointXY = id => { const point = pointById.get(String(id)); return point ? {x: point.x, y: point.y} : null; };
  /** Canvas radius from `centre` that covers both ends of every line (curves bend towards the cloud centres: +15 %). */
  const reach = (centre, nodes) => { let far = 0; for (const node of nodes) for (const id of [node.dataset.from, node.dataset.to]) { const p = pointXY(id); if (p) far = Math.max(far, Math.hypot(p.x - centre.x, p.y - centre.y)); } return far * 1.15 + 6 / Math.max(1e-6, graph.getZoom()); };
  /** A userSpaceOnUse mask whose white disc scales from `from` to `to` around `centre` (canvas units of the masked element). */
  const radialMask = (centre, radius, from, to, track) => {
    maskSeq += 1; const id = `${prefix}-motion-${maskSeq}`; const mask = svgEl('mask', {id, maskUnits: 'userSpaceOnUse', x: -1e6, y: -1e6, width: 2e6, height: 2e6}); mask.setAttribute('class', 'atlas-motion-mask');
    const anchor = svgEl('g', {transform: `translate(${centre.x} ${centre.y})`}); const disc = svgEl('circle', {r: Math.max(radius, 1e-3), fill: '#fff'}); anchor.append(disc); mask.append(anchor); defs.append(mask);
    const anim = disc.animate([{transform: `scale(${from})`}, {transform: `scale(${to})`}], timing(track)); decor.anims.push(anim); decor.cleanups.push(() => mask.remove()); return {id, anim};
  };
  const animate = (node, keyframes, options) => { const anim = node.animate(keyframes, options); decor.anims.push(anim); return anim; };
  const cloudCentre = id => { const cloud = id ? cloudById.get(String(id)) : null; return cloud ? {x: cloud.cx, y: cloud.cy} : null; };
  const labelOrigin = id => { const entry = entries.find(row => row.cloudId === id); return entry && !entry.leader ? {x: entry.ax, y: entry.ay} : null; };

  /**
   * Draw one transition's decorations.  `plan` = {kind, tracks (motion.js timeline tracks), retired (snapshotEdges() taken before the
   * commit), source, origin (canvas click point or null), prevPoint, nextPoint, cloudId, prevCloudId}.  Returns the WAAPI animations.
   */
  function decorate({kind, tracks = {}, retired = null, source = null, origin = null, prevPoint = null, nextPoint = null, cloudId = null, prevCloudId = null} = {}) {
    if (destroyed || !isReady || !model || kind === 'none') return [];
    const zoom = Math.max(1e-6, graph.getZoom()); const live = [...edgeGroup.children]; const before = retired?.ids || new Map();
    // leaving lines: the retired (detached) paths become ghosts that retract towards the previous focus
    if (tracks.leave && retired?.nodes?.length) {
      const liveIds = new Set(live.map(node => node.dataset.edgeId)); const ghosts = retired.nodes.filter(node => !node.isConnected && !liveIds.has(node.dataset.edgeId));
      const centre = prevPoint || cloudCentre(prevCloudId);
      if (ghosts.length) {
        const group = svgEl('g'); group.setAttribute('class', 'atlas-motion-ghosts'); group.setAttribute('aria-hidden', 'true'); const radius = centre ? reach(centre, ghosts) : 0;
        for (const node of ghosts) { node.setAttribute('class', 'atlas-motion-ghost'); node.removeAttribute('mask'); for (const key of Object.keys(node.dataset)) delete node.dataset[key]; group.append(node); }
        motionLayer.append(group); decor.cleanups.push(() => group.remove());
        if (centre) group.setAttribute('mask', `url(#${radialMask(centre, radius, 1, 0, tracks.leave).id})`); else animate(group, [{opacity: 1}, {opacity: 0}], timing(tracks.leave));
      }
    }
    // entering lines draw outward from the focus, strong -> medium -> weak (each tier its own disc)
    const focus = kind === 'cloud' ? (origin || (source === 'label' ? labelOrigin(cloudId) : null) || cloudCentre(cloudId)) : nextPoint;
    if (focus) for (const tier of ['strong', 'medium', 'weak']) {
      const track = tracks[`edges.${tier}`]; if (!track) continue; const nodes = live.filter(node => node.dataset.tier === tier && !before.has(node.dataset.edgeId)); if (!nodes.length) continue;
      const mask = radialMask(focus, reach(focus, nodes), 0, 1, track); for (const node of nodes) node.setAttribute('mask', `url(#${mask.id})`);
      decor.cleanups.push(() => { for (const node of nodes) node.removeAttribute('mask'); });
    }
    // cloud: its outline thickens from the click point, related bridges rise
    if (kind === 'cloud' && tracks.outline) {
      const path = hulls.get(cloudId); const cloud = cloudById.get(String(cloudId)); const group = groups.get(cloudId);
      if (path && cloud && group && path.getAttribute('d')) {
        const at = focus || {x: cloud.cx, y: cloud.cy}; const local = {x: at.x - cloud.cx, y: at.y - cloud.cy}; const outline = svgEl('path', {d: path.getAttribute('d')}); outline.setAttribute('class', 'atlas-motion-outline'); outline.setAttribute('aria-hidden', 'true');
        group.append(outline); decor.cleanups.push(() => outline.remove());
        outline.setAttribute('mask', `url(#${radialMask(local, Math.hypot(local.x, local.y) + cloud.r, 0, 1, {start: tracks.outline.start, duration: tracks.outline.duration / 2}).id})`);
        animate(outline, [{opacity: 1}, {opacity: 1, offset: 0.5}, {opacity: 0}], timing(tracks.outline));
      }
    }
    if (kind === 'cloud' && tracks.bridges) for (const node of bridgeGroup.querySelectorAll('.atlas-cloud-bridge.is-emphasis')) animate(node, [{opacity: 0.25}, {opacity: 1}], timing(tracks.bridges, {fill: 'backwards'}));
    // paper on the map: one ripple ring from the clicked point
    if (tracks.ripple && nextPoint) {
      const anchor = svgEl('g', {transform: `translate(${nextPoint.x} ${nextPoint.y})`}); anchor.setAttribute('class', 'atlas-motion-ripple'); anchor.setAttribute('aria-hidden', 'true'); const ring = svgEl('circle', {r: 26 / zoom}); anchor.append(ring); motionLayer.append(anchor); decor.cleanups.push(() => anchor.remove());
      animate(ring, [{transform: 'scale(0.25)', opacity: 1}, {transform: 'scale(1)', opacity: 0}], timing(tracks.ripple));
    }
    // paper from the panel list: a highlight ring travels from the previous point to the new one
    if (tracks.travel && nextPoint) {
      const from = prevPoint || nextPoint; const traveller = svgEl('g'); traveller.setAttribute('class', 'atlas-motion-travel'); traveller.setAttribute('aria-hidden', 'true'); traveller.append(svgEl('circle', {r: 11 / zoom})); motionLayer.append(traveller); decor.cleanups.push(() => traveller.remove());
      const at = p => `translate(${p.x}px, ${p.y}px)`; animate(traveller, [{transform: at(from), opacity: 0.95}, {transform: at(nextPoint), opacity: 0.95, offset: 0.75}, {transform: at(nextPoint), opacity: 0}], timing(tracks.travel));
    }
    // sub-item: lines whose dimming changed fade between the two states
    if (tracks.dim) for (const node of live) { const was = before.get(node.dataset.edgeId); const now = node.classList.contains('is-dimmed'); if (was === undefined || was === now) continue; animate(node, now ? [{opacity: 1}, {opacity: 0.22}] : [{opacity: 0.22}, {opacity: 1}], timing(tracks.dim, {fill: 'backwards'})); }
    const current = decor; if (current.anims.length) Promise.allSettled(current.anims.map(anim => anim.finished)).then(() => { if (decor === current) clearDecorations(); });
    else if (current.cleanups.length) clearDecorations();
    return [...current.anims];
  }

  return {
    /** Snapshot of the drawn paper lines taken BEFORE a commit: the paths (detached by the next rebuild -> ghosts) and their dimming. */
    snapshotEdges: () => { const nodes = [...edgeGroup.children]; return {nodes, ids: new Map(nodes.map(node => [node.dataset.edgeId, node.classList.contains('is-dimmed')]))}; },
    decorate,
    clearDecorations: () => { if (!destroyed) clearDecorations(); },
    /** Decoration elements currently in the overlay (0 at motion idle). */
    decorationCount: () => motionLayer.childElementCount + defs.querySelectorAll('.atlas-motion-mask').length + hullGroup.querySelectorAll('.atlas-motion-outline').length,
    /** New model: rebuild DOM only when the cloud layout changed; selection/callbacks always refresh. */
    update(next) {
      if (destroyed || !next) return; model = next;
      clouds = next.clouds; if (next.signature !== signature) { signature = next.signature; rebuild(); } else cloudById = new Map(clouds.map(cloud => [cloud.id, cloud]));
      applyColors(); drawEdges(); applyEdgeColors(); applySelection(); sync();
    },
    ready() { if (destroyed) return; isReady = true; needsPlacement = true; sync(); },
    follow(active) { if (destroyed || !isReady) return; following = active; if (active) { if (frame === null) frame = nextFrame(tick); } else { if (frame !== null) { cancelFrame(frame); frame = null; } sync(); } },
    sync,
    /** Fit just ran: labels are placed again at the Fit camera. */
    refit() { if (destroyed || !isReady) return; needsPlacement = true; sync(); },
    resize() { if (destroyed || !isReady) return; needsPlacement = true; sync(); },
    /** Hover a point: dashed lines join every replica of the same paper (multi-collection papers). */
    hover(nodeId) {
      if (destroyed || !model) return; replicaGroup.replaceChildren(); stats.replicaLines = 0; stats.hoverPaperId = null;
      const point = nodeId ? model.points.find(row => row.id === nodeId) : null; if (!point) return;
      const replicas = (model.nodeIdsByPaper?.get(point.paperId) || []).filter(id => id !== nodeId).map(id => model.points.find(row => row.id === id)).filter(Boolean);
      for (const other of replicas) { const line = doc.createElementNS(NS, 'line'); line.setAttribute('class', 'atlas-cloud-replica'); for (const [key, value] of [['x1', point.x], ['y1', point.y], ['x2', other.x], ['y2', other.y]]) line.setAttribute(key, String(value)); replicaGroup.append(line); }
      stats.replicaLines = replicas.length; stats.hoverPaperId = point.paperId;
    },
    stats: () => ({...stats, clouds: clouds.length, labels: entries.length, visibleLabels: entries.length - hidden.size, hiddenLabels: hidden.size, chips: chipRow.querySelectorAll('button').length, selectedCloudId: model?.selectedCloudId || null, zoom: lastZoom}),
    destroy() { if (destroyed) return; clearDecorations(); destroyed = true; following = false; if (frame !== null) cancelFrame(frame); observer?.disconnect(); container.removeEventListener('pointermove', pointerMove); container.removeEventListener('pointerleave', pointerLeave); svg.remove(); labelLayer.remove(); chipRow.remove(); }
  };
}

export const AtlasCloudOverlay = {SUBGROUP_MIN_ZOOM, BRIDGE_WIDTHS, BRIDGE_BEND, labelWeightTier, edgePathD, bridgePathD, createCloudOverlay, anchorLabels, labelBox, hiddenAfterZoom};
if (typeof window !== 'undefined') window.AtlasCloudOverlay = AtlasCloudOverlay;
