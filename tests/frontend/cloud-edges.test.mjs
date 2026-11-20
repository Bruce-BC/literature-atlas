import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {TIER_STYLE, PAINT_ORDER, TIERS, withEdgeTiers, semanticStrength, keywordStrength} from '../../frontend/edge-strength.js';
import {graphProjection} from '../../frontend/projection.js';
import {decodeSnapshot} from '../../frontend/decoder.js';
import {renderG6Network, graphData} from '../../frontend/g6-render.js';
import {cytoscapeStyle} from '../../frontend/network-views.js';
import cytoscape from 'cytoscape';
import {edgePathD, bridgePathD, BRIDGE_WIDTHS} from '../../frontend/cloud-overlay.js';
import {collectionEdgeState} from '../../frontend/all-paper-overview.js';
import {cloudTones, slotHue, HUE_SLOTS} from '../../frontend/cloud-colors.js';
import {contrastRatio} from '../../frontend/network-style.js';

const palettes = JSON.parse(await readFile(new URL('../../assets/palettes.json', import.meta.url), 'utf8')).palettes;
const rgb = hex => [1, 3, 5].map(i => Number.parseInt(hex.slice(i, i + 2), 16));
/** `fg` at `alpha` over `bg` (what a stroke-opacity line looks like on the panel). */
const over = (fg, bg, alpha) => `#${rgb(fg).map((v, i) => Math.round(v * alpha + rgb(bg)[i] * (1 - alpha)).toString(16).padStart(2, '0')).join('')}`;

test('ME-3 tier encoding: width and opacity strong > medium > weak, only weak dashed, paint order weak -> strong', () => {
  assert.deepEqual(TIERS, ['strong', 'medium', 'weak']); assert.deepEqual(PAINT_ORDER, ['weak', 'medium', 'strong']);
  assert.deepEqual(TIERS.map(t => TIER_STYLE[t].width), [2.4, 1.4, 0.8]); assert.deepEqual(TIERS.map(t => TIER_STYLE[t].opacity), [0.9, 0.55, 0.28]);
  assert.deepEqual(TIERS.map(t => Boolean(TIER_STYLE[t].dash)), [false, false, true]);
  assert.ok(BRIDGE_WIDTHS[0] < BRIDGE_WIDTHS[1] && BRIDGE_WIDTHS[1] < BRIDGE_WIDTHS[2], 'three bridge width levels');
});

test('ME-7 strong line vs panel contrast >= 3:1 on 3 palettes x 2 themes (neutral tone and every cloud stroke tone, at the strong opacity)', () => {
  const alpha = TIER_STYLE.strong.opacity; let checked = 0;
  assert.equal(palettes.length, 3);
  for (const palette of palettes) for (const theme of ['light', 'dark']) {
    const {panel, muted} = palette.themes[theme];
    const neutral = contrastRatio(over(muted, panel, alpha), panel); assert.ok(neutral >= 3, `${palette.id}/${theme} neutral ${neutral.toFixed(2)}`); checked += 1;
    for (let slot = 0; slot < HUE_SLOTS; slot += 1) { const stroke = cloudTones({hue: slotHue(slot, palette.id), theme, panelHex: panel}).stroke.hex; const ratio = contrastRatio(over(stroke, panel, alpha), panel); assert.ok(ratio >= 3, `${palette.id}/${theme} slot ${slot} ${ratio.toFixed(2)}`); checked += 1; }
    const unfiled = cloudTones({hue: null, theme, panelHex: panel, unfiled: true}).stroke.hex; assert.ok(contrastRatio(over(unfiled, panel, alpha), panel) >= 3, `${palette.id}/${theme} unfiled`); checked += 1;
  }
  assert.equal(checked, 3 * 2 * (HUE_SLOTS + 2));
});

test('edge geometry: intra-cloud straight, inter-cloud quadratic through the midpoint of the two cloud centres, bridges bent deterministically', () => {
  const a = {x: 0, y: 0}; const b = {x: 10, y: 0};
  assert.equal(edgePathD(a, b), 'M0 0L10 0'); assert.equal(edgePathD(a, b, [{cx: 5, cy: 5}, {cx: 5, cy: 5}]), 'M0 0L10 0', 'same cloud -> straight');
  assert.equal(edgePathD(a, b, [{cx: 0, cy: 20}, {cx: 10, cy: 40}]), 'M0 0Q5 30 10 0', 'control = midpoint of the centres');
  assert.equal(edgePathD(a, b, [{cx: 0, cy: 20}, {cx: 10, cy: 40}]), edgePathD({...a}, {...b}, [{cx: 0, cy: 20}, {cx: 10, cy: 40}]), 'deterministic');
  const d = bridgePathD({cx: 0, cy: 0}, {cx: 100, cy: 0}); assert.equal(d, 'M0 0Q50 12 100 0');
  assert.equal(bridgePathD({cx: 100, cy: 0}, {cx: 0, cy: 0}), 'M100 0Q50 -12 0 0', 'the side follows source -> target (bridge ids are ordered, so it is stable)');
});

test('view state -> edgesForState state mapping', () => {
  const nodeIdsByPaper = new Map([['p:1', ['pt:A:p:1', 'pt:B:p:1']], ['p:2', ['pt:A:p:2']]]);
  assert.deepEqual(collectionEdgeState({nodeIdsByPaper}), {cloudId: null, paperId: null, pointId: null, subItemPaperIds: null}, 'idle');
  assert.deepEqual(collectionEdgeState({selectedCloudId: 'A', nodeIdsByPaper}), {cloudId: 'A', paperId: null, pointId: null, subItemPaperIds: null});
  assert.deepEqual(collectionEdgeState({selectedCloudId: 'A', selectedPaperId: 'p:1', selectedPointId: 'pt:B:p:1', nodeIdsByPaper}), {cloudId: 'A', paperId: 'p:1', pointId: 'pt:B:p:1', subItemPaperIds: null}, 'clicked replica kept');
  assert.equal(collectionEdgeState({selectedPaperId: 'p:1', selectedPointId: 'pt:A:p:2', nodeIdsByPaper}).pointId, null, 'a stale point of another paper is dropped (engine replica rule)');
  assert.equal(collectionEdgeState({selectedPaperId: 'p:9', selectedPointId: 'x', nodeIdsByPaper}).paperId, null, 'a paper without a point draws nothing');
  assert.deepEqual(collectionEdgeState({selectedCloudId: 'A', subItemPaperIds: new Set(['p:2', 'p:1']), nodeIdsByPaper}).subItemPaperIds, ['p:1', 'p:2'], 'scope Set -> sorted array');
});

test('ME-8 lower networks: tiers reach paper and concept edges from the full snapshot; citation/membership untiered; budgets untouched', async () => {
  const snapshot = decodeSnapshot(JSON.parse(await readFile(new URL('../fixtures/demo-snapshot.json', import.meta.url), 'utf8')));
  const graph = graphProjection(snapshot, null, {maxConceptNodes: 10000}); const semantic = semanticStrength(snapshot).byId; const keyword = keywordStrength(snapshot).byId;
  const paper = withEdgeTiers(snapshot, graph.paperEdges); const concept = withEdgeTiers(snapshot, graph.conceptEdges);
  assert.deepEqual(paper.map(e => e.data.id), graph.paperEdges.map(e => e.data.id)); assert.deepEqual(concept.map(e => e.data.id), graph.conceptEdges.map(e => e.data.id));
  assert.ok(graph.paperEdges.every(e => !('tier' in e.data)), 'the projection rows are not mutated');
  for (const e of paper) assert.equal(e.data.tier, e.data.relation === 'semantic' ? semantic.get(e.data.id).tier : null, e.data.id);
  for (const e of concept) assert.equal(e.data.tier, e.data.relation === 'concept-cooccurrence' ? keyword.get(e.data.id).tier : null, e.data.id);
  assert.ok(paper.some(e => e.data.relation === 'citation' && e.data.tier === null) && concept.some(e => e.data.tier) && concept.some(e => e.data.relation === 'concept-membership' && e.data.tier === null));
});

test('ME-8 Cytoscape: tier selectors carry the shared width/opacity/dash; citation keeps its arrow and no tier', () => {
  const c = {text: '#111111', panel: '#ffffff', border: '#cbd5e1', focus: '#3c74ca'};
  const cy = cytoscape({headless: true, styleEnabled: true, style: cytoscapeStyle(c), elements: [...['a', 'b'].map(id => ({data: {id}})), ...TIERS.map(tier => ({data: {id: tier, source: 'a', target: 'b', relation: 'semantic', tier}})), {data: {id: 'cite', source: 'a', target: 'b', relation: 'citation', tier: null}, classes: 'directed'}]});
  for (const tier of TIERS) { const edge = cy.getElementById(tier); assert.equal(edge.numericStyle('width'), TIER_STYLE[tier].width, tier); assert.equal(edge.numericStyle('opacity'), TIER_STYLE[tier].opacity, tier); assert.equal(edge.style('line-style'), TIER_STYLE[tier].dash ? 'dashed' : 'solid', tier); }
  const cite = cy.getElementById('cite'); assert.equal(cite.numericStyle('width'), 1.5); assert.equal(cite.style('line-style'), 'solid'); assert.equal(cite.style('target-arrow-shape'), 'triangle'); cy.destroy();
});

test('ME-8 G6: tier in the static (dense) edge style and a tier-only change redraws', async () => {
  const style = {borderColor: '#cbd5e1'}; const nodes = [{data: {id: 'one', label: 'One'}}, {data: {id: 'two', label: 'Two'}}];
  const edge = (tier, extra = {}) => ({data: {id: 'e', source: 'one', target: 'two', relation: 'semantic', tier, ...extra}});
  for (const tier of TIERS) { const row = graphData(nodes, [edge(tier)], {}, style).edges[0].style; assert.equal(row.lineWidth, TIER_STYLE[tier].width); assert.equal(row.strokeOpacity, TIER_STYLE[tier].opacity); assert.deepEqual(row.lineDash, TIER_STYLE[tier].dash ? [...TIER_STYLE[tier].dash] : [0, 0]); }
  const cite = graphData(nodes, [{data: {id: 'c', source: 'one', target: 'two', relation: 'citation', directed: true, tier: null}}], {}, style).edges[0].style; assert.deepEqual([cite.lineWidth, cite.strokeOpacity, cite.endArrow], [1.5, 0.9, true]);
  class TierGraph {
    constructor(options) { this.nodes = options.data.nodes; this.calls = []; }
    on() {} render() { return Promise.resolve(); } getNodeData() { return this.nodes; } getEdgeData() { return []; } getElementPosition() { return [0, 0]; } getViewportByCanvas() { return [0, 0]; } getZoom() { return 1; } getSize() { return [100, 100]; }
    stopLayout() {} setElementState() { return Promise.resolve(); } layout() { return Promise.resolve(); } destroy() {} translateBy() { return Promise.resolve(); }
    updateNodeData(data) { this.calls.push(['node-data', data]); } updateEdgeData(data) { this.calls.push(['edge-data', data]); } setData(data) { this.calls.push(['set-data', data]); }
    setNode() {} setEdge() {} draw() { this.calls.push('draw'); return Promise.resolve(); }
  }
  const box = {dataset: {}, attributes: {}, addEventListener() {}, removeEventListener() {}, setAttribute(key, value) { this.attributes[key] = value; }};
  const wrapper = renderG6Network(box, nodes, [edge('weak')], {GraphClass: TierGraph, layoutType: 'preset', ...style}); await wrapper.ready;
  wrapper.update(nodes, [edge('weak')], {...style}); await wrapper.whenIdle();
  assert.equal(wrapper.graph.calls.filter(call => call === 'draw').length, 0, 'identical data: no redraw');
  wrapper.update(nodes, [edge('strong')], {...style}); await wrapper.whenIdle();
  assert.equal(wrapper.graph.calls.filter(call => call === 'draw').length, 1, 'a tier-only change redraws'); assert.equal(wrapper.stats.styleUpdates, 1); assert.equal(wrapper.stats.topologyChanges, 0);
  assert.equal(wrapper.graph.calls.find(call => Array.isArray(call) && call[0] === 'edge-data')[1][0].data.tier, 'strong');
  wrapper.destroy();
});
