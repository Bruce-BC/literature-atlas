import {stableHash} from './sampling.js';
import {oklchToHex} from './network-style.js';

/*
 * Collection-cloud colour engine (pure, no DOM). Hues come from 24 fixed slots
 * assigned by id hash in collection-id order, so a cloud keeps its colour when
 * sizes, viewport, layout or input order change. Tones share one OKLCH
 * lightness per role; chroma alone is reduced at the sRGB gamut boundary.
 */
export const HUE_SLOTS = 24;
export const SLOT_DEGREES = 360 / HUE_SLOTS;
export const UNFILED_CLOUD_ID = 'cloud:unfiled';
export const PALETTE_HUE_OFFSETS = Object.freeze({atlas: 0, calm: 14, mocha: -18});
/** D-A6 seam: 'stability' (default, no correction) or 'separation' (opt-in local correction via `separateNeighbours`). */
export const COLOR_MODES = Object.freeze({stability: 'stability', separation: 'separation'});
export const DEFAULT_COLOR_MODE = COLOR_MODES.stability;
export const NEIGHBOUR_MIN_SLOTS = 4; // 60 degrees
export const DEFAULT_NEIGHBOUR_GAP = 40; // CLOUD_GAP of the layout module
// Tone table (plan section 2.3). Fill lightness is relative to the theme panel.
export const CLOUD_TONES = Object.freeze({
  light: Object.freeze({fill: {dL: -0.04, C: 0.02}, stroke: {L: 0.57, C: 0.13}, point: {L: 0.52, C: 0.15}, variantStep: -0.06}),
  dark: Object.freeze({fill: {dL: 0.05, C: 0.04}, stroke: {L: 0.72, C: 0.12}, point: {L: 0.78, C: 0.13}, variantStep: 0.06})
});
export const SELECTED_FILL_CHROMA_SCALE = 1.6;
export const UNFILED_CHROMA = 0;

// Machado, Oliveira & Fernandes (2009), severity 1.0, applied in linear sRGB.
export const CVD_MATRICES = Object.freeze({
  protanopia: Object.freeze([[0.152286, 1.052583, -0.204868], [0.114503, 0.786281, 0.099216], [-0.003882, -0.048116, 1.051998]]),
  deuteranopia: Object.freeze([[0.367322, 0.860646, -0.227968], [0.280085, 0.672501, 0.047413], [-0.011820, 0.042940, 0.968881]])
});

const clamp01 = value => Math.max(0, Math.min(1, value));
const decode = value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
const encode = value => value <= 0.0031308 ? 12.92 * value : 1.055 * Math.pow(value, 1 / 2.4) - 0.055;
const hexToLinear = hex => { const value = String(hex || '#000000').replace('#', ''); const full = value.length === 3 ? value.split('').map(x => x + x).join('') : value; const n = Number.parseInt(full, 16); return [n >> 16 & 255, n >> 8 & 255, n & 255].map(channel => decode(channel / 255)); };
const linearToHex = linear => `#${linear.map(value => Math.round(clamp01(encode(clamp01(value))) * 255).toString(16).padStart(2, '0')).join('')}`;
const wrapHue = hue => ((hue % 360) + 360) % 360;
const slotDistance = (a, b) => { const d = Math.abs(a - b) % HUE_SLOTS; return Math.min(d, HUE_SLOTS - d); };

/** Linear sRGB -> OKLab [L, a, b]. */
export function linearToOklab([r, g, b]) {
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b); const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b); const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  return [0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s, 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s, 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s];
}
/** OKLCH (hue in degrees) -> unclamped linear sRGB. */
export function oklchToLinear(L, C, hue) {
  const h = hue * Math.PI / 180; const a = C * Math.cos(h); const b = C * Math.sin(h);
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3; const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3; const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  return [4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s, -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s, -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s];
}
export const hexToOklab = hex => linearToOklab(hexToLinear(hex));
export const oklchToOklab = ([L, C, hue]) => [L, C * Math.cos(hue * Math.PI / 180), C * Math.sin(hue * Math.PI / 180)];
/** Euclidean distance in OKLab; accepts OKLab triples or hex strings. */
export function oklabDeltaE(first, second) { const a = typeof first === 'string' ? hexToOklab(first) : first; const b = typeof second === 'string' ? hexToOklab(second) : second; return Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]); }

const inGamut = linear => linear.every(value => value >= 0 && value <= 1); // strict, so oklchToHex never reduces again
/** Largest chroma <= `chroma` that keeps (L, hue) inside sRGB; hue and L never move. */
export function fitChroma(L, chroma, hue) {
  if (inGamut(oklchToLinear(L, chroma, hue))) return chroma;
  let lo = 0; let hi = chroma; for (let step = 0; step < 32; step += 1) { const mid = (lo + hi) / 2; if (inGamut(oklchToLinear(L, mid, hue))) lo = mid; else hi = mid; }
  return lo;
}
const tone = (L, chroma, hue) => { const C = fitChroma(L, chroma, hue); return {oklch: [L, C, hue], hex: oklchToHex(L, C, hue)}; };

/** Simulate protanopia/deuteranopia (Machado 2009, severity 1.0) on a hex colour. */
export function simulateCvd(hex, type = 'deuteranopia') {
  const matrix = CVD_MATRICES[type]; if (!matrix) throw new Error(`unknown CVD type: ${type}`);
  const linear = hexToLinear(hex); return linearToHex(matrix.map(row => row[0] * linear[0] + row[1] * linear[1] + row[2] * linear[2]));
}
/** Smallest OKLab difference of two colours across protanopia and deuteranopia simulation. */
export const cvdDeltaE = (first, second) => Math.min(...Object.keys(CVD_MATRICES).map(type => oklabDeltaE(simulateCvd(first, type), simulateCvd(second, type))));

export const slotHue = (slot, paletteId = 'atlas') => wrapHue(slot * SLOT_DEGREES + (PALETTE_HUE_OFFSETS[paletteId] || 0));
const isUnfiled = cloud => Boolean(cloud?.unfiled) || cloud?.id === UNFILED_CLOUD_ID;
const cloudId = cloud => String(cloud?.collectionId ?? cloud?.id);
let referenceHexes = null; // palette-independent slot references for CVD tie-breaks
const slotReference = slot => (referenceHexes ||= Array.from({length: HUE_SLOTS}, (_, index) => tone(CLOUD_TONES.light.point.L, CLOUD_TONES.light.point.C, index * SLOT_DEGREES).hex))[slot];
const cvdScore = (slot, taken) => taken.length ? Math.min(...taken.map(other => cvdDeltaE(slotReference(slot), slotReference(other)))) : 0;

/**
 * Assign a hue slot per cloud: primary = stableHash(id) mod 24, processed in
 * ascending id order, probing +1, -1, +2, -2, ... to the nearest free slot (an
 * equidistant pair is broken by simulated CVD difference to already-assigned
 * slots, then +k). Beyond 24 clouds slots are reused, least-used nearest first.
 * Returns Map(id -> {slot, hue, unfiled}); unfiled has slot/hue null.
 */
export function assignCloudHues(clouds, {paletteId = 'atlas'} = {}) {
  const result = new Map(); const usage = new Array(HUE_SLOTS).fill(0); const order = [];
  for (const id of [...new Set((clouds || []).filter(cloud => !isUnfiled(cloud)).map(cloudId))].sort((a, b) => a < b ? -1 : a > b ? 1 : 0)) {
    const primary = stableHash(id) % HUE_SLOTS; const level = Math.min(...usage); let slot = null;
    for (let k = 0; k <= HUE_SLOTS / 2 && slot === null; k += 1) {
      const candidates = [...new Set([(primary + k) % HUE_SLOTS, (primary - k + HUE_SLOTS) % HUE_SLOTS])].filter(candidate => usage[candidate] === level);
      if (candidates.length === 1) slot = candidates[0];
      else if (candidates.length === 2) { const taken = order; const [up, down] = candidates; slot = cvdScore(down, taken) > cvdScore(up, taken) ? down : up; }
    }
    usage[slot] += 1; if (usage[slot] === 1) order.push(slot);
    result.set(id, {slot, hue: slotHue(slot, paletteId), unfiled: false});
  }
  for (const cloud of clouds || []) if (isUnfiled(cloud)) result.set(cloudId(cloud), {slot: null, hue: null, unfiled: true});
  return result;
}

/**
 * Tone set for one cloud. Fill lightness follows the theme panel (Lp from
 * `panelL` or `panelHex`); every other tone uses a fixed L shared by all hues.
 * Each tone is {oklch: [L, C, h] (pre-quantisation, chroma fitted), hex}.
 */
export function cloudTones({hue = 0, theme = 'light', panelHex = '#ffffff', panelL = null, textHex = null, unfiled = false} = {}) {
  const spec = CLOUD_TONES[theme === 'dark' ? 'dark' : 'light']; const h = unfiled || hue === null ? 0 : wrapHue(Number(hue) || 0);
  const cap = chroma => unfiled || hue === null ? UNFILED_CHROMA : chroma; const Lp = Number.isFinite(panelL) ? panelL : hexToOklab(panelHex)[0];
  const fill = tone(clamp01(Lp + spec.fill.dL), cap(spec.fill.C), h);
  const point = tone(spec.point.L, cap(spec.point.C), h);
  return {
    theme: theme === 'dark' ? 'dark' : 'light', hue: unfiled ? null : h, unfiled: Boolean(unfiled), fill,
    stroke: tone(spec.stroke.L, cap(spec.stroke.C), h), point,
    pointVariants: [0, 1, 2].map(step => step ? tone(clamp01(spec.point.L + step * spec.variantStep), cap(spec.point.C), h) : point),
    selectedFill: tone(fill.oklch[0], fill.oklch[1] * SELECTED_FILL_CHROMA_SCALE, h),
    label: {background: fill.hex, text: textHex}
  };
}

const centerOf = (cloud, baseCenters) => { const entry = baseCenters instanceof Map ? baseCenters.get(cloud.id) : baseCenters?.[cloud.id]; return entry ? {cx: entry.cx, cy: entry.cy, r: entry.r ?? cloud.r} : null; };
/** Pairs of hued clouds whose circle gap (centre distance - r1 - r2) is <= 2 * gap. */
export function adjacentPairs(clouds, baseCenters, {gap = DEFAULT_NEIGHBOUR_GAP} = {}) {
  const rows = (clouds || []).filter(cloud => !isUnfiled(cloud)).map(cloud => ({id: cloud.id, ...centerOf(cloud, baseCenters)})).filter(row => Number.isFinite(row.cx) && Number.isFinite(row.r)).sort((a, b) => a.id < b.id ? -1 : a.id > b.id ? 1 : 0); const pairs = [];
  for (let i = 0; i < rows.length; i += 1) for (let j = i + 1; j < rows.length; j += 1) { const a = rows[i]; const b = rows[j]; const distance = Math.hypot(a.cx - b.cx, a.cy - b.cy) - a.r - b.r; if (distance <= 2 * gap) pairs.push({a: a.id, b: b.id, gap: distance}); }
  return pairs;
}

/**
 * Measured-only neighbour statistics (never fed back into assignment in the
 * default mode): adjacent pair count, pairs under 4 slots apart, and minimum
 * stroke/point OKLab and CVD-simulated point differences among adjacent pairs.
 */
export function neighbourStats(clouds, baseCenters, assignment, {gap = DEFAULT_NEIGHBOUR_GAP, theme = 'light'} = {}) {
  const pairs = adjacentPairs(clouds, baseCenters, {gap}).filter(pair => assignment.get(pair.a)?.slot != null && assignment.get(pair.b)?.slot != null);
  const tonesFor = id => cloudTones({hue: assignment.get(id).hue, theme}); let minStrokeDeltaE = null; let minPointDeltaE = null; let minCvdPointDeltaE = null; let closePairs = 0;
  const lower = (current, value) => current === null ? value : Math.min(current, value);
  for (const pair of pairs) {
    if (slotDistance(assignment.get(pair.a).slot, assignment.get(pair.b).slot) < NEIGHBOUR_MIN_SLOTS) closePairs += 1;
    const a = tonesFor(pair.a); const b = tonesFor(pair.b);
    minStrokeDeltaE = lower(minStrokeDeltaE, oklabDeltaE(oklchToOklab(a.stroke.oklch), oklchToOklab(b.stroke.oklch)));
    minPointDeltaE = lower(minPointDeltaE, oklabDeltaE(oklchToOklab(a.point.oklch), oklchToOklab(b.point.oklch)));
    minCvdPointDeltaE = lower(minCvdPointDeltaE, cvdDeltaE(a.point.hex, b.point.hex));
  }
  return {adjacentPairs: pairs.length, closePairs, minStrokeDeltaE, minPointDeltaE, minCvdPointDeltaE};
}

/**
 * Opt-in 'separation' mode only (D-A6 switch). For each adjacent pair under 4
 * slots apart, move the smaller cloud (size, then id) to the nearest free slot
 * at least 4 slots from all its neighbours. Not used by the default path.
 */
export function separateNeighbours(clouds, baseCenters, assignment, {gap = DEFAULT_NEIGHBOUR_GAP, paletteId = 'atlas'} = {}) {
  const next = new Map([...assignment].map(([id, value]) => [id, {...value}])); const sizes = new Map((clouds || []).map(cloud => [cloud.id, Number(cloud.size) || 0])); const pairs = adjacentPairs(clouds, baseCenters, {gap}).filter(pair => next.get(pair.a)?.slot != null && next.get(pair.b)?.slot != null);
  const neighbours = id => pairs.flatMap(pair => pair.a === id ? [pair.b] : pair.b === id ? [pair.a] : []);
  for (const pair of pairs) {
    if (slotDistance(next.get(pair.a).slot, next.get(pair.b).slot) >= NEIGHBOUR_MIN_SLOTS) continue;
    const mover = (sizes.get(pair.a) - sizes.get(pair.b) || (pair.a < pair.b ? 1 : -1)) < 0 ? pair.a : pair.b; const used = new Set([...next.values()].map(value => value.slot)); const start = next.get(mover).slot;
    for (let k = 1; k <= HUE_SLOTS / 2; k += 1) { const found = [(start + k) % HUE_SLOTS, (start - k + HUE_SLOTS) % HUE_SLOTS].find(slot => !used.has(slot) && neighbours(mover).every(other => slotDistance(slot, next.get(other).slot) >= NEIGHBOUR_MIN_SLOTS)); if (found !== undefined) { next.set(mover, {slot: found, hue: slotHue(found, paletteId), unfiled: false}); break; } }
  }
  return next;
}

export const AtlasCloudColors = {HUE_SLOTS, SLOT_DEGREES, UNFILED_CLOUD_ID, PALETTE_HUE_OFFSETS, COLOR_MODES, DEFAULT_COLOR_MODE, NEIGHBOUR_MIN_SLOTS, DEFAULT_NEIGHBOUR_GAP, CLOUD_TONES, SELECTED_FILL_CHROMA_SCALE, UNFILED_CHROMA, CVD_MATRICES, linearToOklab, oklchToLinear, hexToOklab, oklchToOklab, oklabDeltaE, fitChroma, simulateCvd, cvdDeltaE, slotHue, assignCloudHues, cloudTones, adjacentPairs, neighbourStats, separateNeighbours};
if (typeof window !== 'undefined') window.AtlasCloudColors = AtlasCloudColors;
