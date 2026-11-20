/**
 * Automatic node emphasis shared by the Cytoscape and G6 renderers (pure; no DOM).
 *
 *  - Hubs: a node with `HUB_MIN_DEGREE` or more DISPLAYED links gets another shape and grows with its link count
 *    (paper circle -> hexagon, concept diamond -> star).  The rule is data-driven: nothing is hand-picked.
 *  - Tiers: link count splits papers and concepts into four tiers (0: 0-2 links, 1: 3-5, 2: 6-11, 3: 12+).  Tier 1 changes
 *    shape, tier 2 adds an icon (paper: file, concept: light bulb), tier 3 a distinct icon (award / sparkles) and a heavier
 *    outline, so importance reads from shape, icon and size together, never from colour alone.
 *  - Levels: representative field nodes of the field overview carry a shape, size, border and numeral per hierarchy
 *    depth, so a parent and its children stay distinguishable when their colours are close.
 */
import {fieldIcon} from './node-icons.js';
export const HUB_MIN_DEGREE = 3;
export const HUB_MAX_SCALE = 1.8;
export const LABEL_ALL_LIMIT = 40; // "show names" labels every node up to this many; above it only hubs and field nodes (more names than this overlap into noise)

/** Size multiplier of a node with `links` displayed links: 1 below the hub threshold, then logarithmic up to HUB_MAX_SCALE. */
export const hubScale = links => (links >= HUB_MIN_DEGREE ? Math.min(HUB_MAX_SCALE, 1 + 0.25 * Math.log2(links / 2)) : 1);

export const TIER_LINKS = Object.freeze([HUB_MIN_DEGREE, 6, 12]); // lower bounds of tiers 1, 2 and 3
export const tierOf = links => TIER_LINKS.filter(bound => links >= bound).length;
export const TIER_ICONS = Object.freeze({paper: Object.freeze([null, null, 'file-text', 'award']), concept: Object.freeze([null, null, 'lightbulb', 'sparkles'])});
export const TIER_NAMES = Object.freeze(['연결 0~2개', '연결 3~5개', '연결 6~11개', '연결 12개 이상']);
const TIER_LINE = [null, null, 2.5, 3.5]; // tier 3 outline is heavier (non-colour cue)
const BASE_SHAPE = {paper: 'circle', concept: 'diamond'};
const HUB_SHAPE = {paper: 'hexagon', concept: 'star'};
const BASE_SIZE = {paper: 18, concept: 22};

/** Displayed link count per node id (self-loops ignored, parallel edges counted). */
export function linkCounts(edges) {
  const counts = new Map();
  for (const edge of edges || []) { const {source, target} = edge.data || edge; if (source === target) continue; counts.set(source, (counts.get(source) || 0) + 1); counts.set(target, (counts.get(target) || 0) + 1); }
  return counts;
}

/**
 * Copy of `nodes` with `links`, `hub`, `shape` and `size` in `data`.  Representative field nodes are left to `markLevels`.
 * `baseSize` overrides the per-kind base size (point maps use small points).
 */
export function markHubs(nodes, edges, {baseSize = null} = {}) {
  const counts = linkCounts(edges);
  return nodes.map(node => {
    const data = node.data; if (data.representative) return node;
    const kind = data.kind === 'concept' ? 'concept' : 'paper'; const links = counts.get(data.id) || 0; const hub = links >= HUB_MIN_DEGREE; const base = baseSize ?? BASE_SIZE[kind];
    const tier = tierOf(links); const icon = baseSize != null && baseSize < 12 ? null : TIER_ICONS[kind][tier]; // point maps (tiny points) keep shapes only: an icon would not be legible
    const iconScale = icon ? Math.max(1, tier === 3 ? 1.45 : 1.25) : 1; // an icon needs room: tiers 2-3 grow a little more than the hub curve
    return {...node, data: {...data, links, hub, tier, shape: hub ? HUB_SHAPE[kind] : BASE_SHAPE[kind], size: Math.round(base * Math.max(hubScale(links), iconScale) * 10) / 10, ...(icon ? {icon} : {}), ...(TIER_LINE[tier] && icon ? {tierLineWidth: TIER_LINE[tier]} : {})}};
  });
}

/** Level presentation of a field node at hierarchy depth `level` (0 = top). */
export const LEVEL_STYLES = Object.freeze([
  Object.freeze({shape: 'hexagon', size: 40, lineWidth: 3, lineDash: null, glyph: 'Ⅰ', name: '1단계 분야'}),
  Object.freeze({shape: 'rect', size: 32, lineWidth: 2.5, lineDash: null, glyph: 'Ⅱ', name: '2단계 분야'}),
  Object.freeze({shape: 'triangle', size: 30, lineWidth: 2, lineDash: Object.freeze([4, 2]), glyph: 'Ⅲ', name: '3단계 이하 분야'})
]);
export const levelStyle = level => LEVEL_STYLES[Math.max(0, Math.min(LEVEL_STYLES.length - 1, Number(level) || 0))];

/** Copy of `nodes` where every representative field node carries its level's `shape`, `size`, `lineWidth`, `lineDash`, `glyph` (level numeral, drawn as a badge) and its domain `icon`. */
export function markLevels(nodes) {
  return nodes.map(node => { const data = node.data; if (!data.representative) return node; const style = levelStyle(data.level); return {...node, data: {...data, shape: style.shape, size: style.size, levelLineWidth: style.lineWidth, levelDash: style.lineDash ? [...style.lineDash] : null, glyph: style.glyph, icon: fieldIcon(data.id)}}; });
}

/** Whether a node is named when "show names" is on: every node of a small graph, otherwise hubs and field nodes only. */
export const namedWhenShown = (data, nodeCount) => nodeCount <= LABEL_ALL_LIMIT || Boolean(data.hub || data.representative);

export const AtlasNodeEmphasis = {TIER_LINKS, TIER_ICONS, TIER_NAMES, tierOf, HUB_MIN_DEGREE, HUB_MAX_SCALE, LABEL_ALL_LIMIT, LEVEL_STYLES, hubScale, linkCounts, markHubs, markLevels, levelStyle, namedWhenShown};
if (typeof window !== 'undefined') window.AtlasNodeEmphasis = AtlasNodeEmphasis;
