import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {decodeSnapshot} from '../../frontend/decoder.js';
import {buildCategoryColorRegistry, contrastRatio, fieldPaperCounts, fieldPresentation, oklchToHex} from '../../frontend/network-style.js';

const fixture = decodeSnapshot(JSON.parse(await readFile(new URL('../fixtures/snapshot.json', import.meta.url))));

test('semantic category registry gives siblings distinct stable colors', () => {
  const data = structuredClone(fixture); data.fields[1].parentIds = [data.fields[0].id];
  const first = buildCategoryColorRegistry(data); const firstColors = new Map(first.entries.map(entry => [entry.categoryKey, entry.hexByPaletteTheme['atlas:light']]));
  const reordered = {...data, fields: [...data.fields].reverse().map(field => ({...field, label: {en: `Renamed ${field.id}`}}))};
  const second = buildCategoryColorRegistry(reordered); for (const [id, color] of firstColors) assert.equal(second.entries.find(entry => entry.categoryKey === id)?.hexByPaletteTheme['atlas:light'], color);
  assert.notEqual(first.entries.find(entry => entry.categoryKey === data.fields[0].id)?.hexByPaletteTheme['atlas:light'], first.entries.find(entry => entry.categoryKey === data.fields[1].id)?.hexByPaletteTheme['atlas:light']);
});

test('OKLCH fallback is valid hex and has readable text contrast', () => {
  const color = oklchToHex(.57, .145, 258.75); assert.match(color, /^#[0-9a-f]{6}$/i); assert.ok(contrastRatio(color, '#ffffff') >= 2.2);
});

test('field counts use accepted direct memberships rather than displayFieldId', () => {
  const data = structuredClone(fixture); data.papers[0].displayFieldId = data.fields[0].id; data.papers[0].fieldIds = data.papers[0].fieldIds.filter(id => id !== data.fields[0].id); data.memberships = data.memberships.filter(row => row.fieldId !== data.fields[0].id);
  const counts = fieldPaperCounts(data); assert.equal(counts.get(data.fields[0].id).direct, 0); assert.equal(counts.get(data.fields[1].id).direct, 1);
  data.memberships.push({paperId: data.papers[0].id, fieldId: data.fields[0].id, accepted: false}); assert.equal(fieldPaperCounts(data).get(data.fields[0].id).direct, 0);
});

test('field presentation consumes persisted registry colors', () => {
  const data = structuredClone(fixture); data.categoryColorRegistry = {version: 1, namespace: 'test', entries: [{categoryKey: data.fields[0].id, family: 'rose', hexByPaletteTheme: {light: '#123456'}, oklchByPaletteTheme: {light: 'oklch(.4 .1 20)'}}]};
  assert.equal(fieldPresentation(data.fields[0], data).color, '#123456');
});

import {execFileSync} from 'node:child_process';
import {categoryForPaper, contrastRatio as contrast, subjectPrimaryFieldId} from '../../frontend/network-style.js';
import {densityRamp} from '../../frontend/density-view.js';

const palettes = JSON.parse(await readFile(new URL('../../assets/palettes.json', import.meta.url))).palettes;
const forest = () => {
  const data = structuredClone(fixture); const ids = ['alpha', 'beta', 'gamma', 'delta', 'epsilon', 'zeta', 'eta'];
  data.fields = ids.map(id => ({id: `field:fixture-server:epoch-001:${id}`, label: {en: id}, parentIds: [], aliases: [], source: 'curated'}));
  data.fields.push({id: 'field:fixture-server:epoch-001:alpha-child-1', label: {en: 'c1'}, parentIds: [data.fields[0].id], aliases: [], source: 'curated'}, {id: 'field:fixture-server:epoch-001:alpha-child-2', label: {en: 'c2'}, parentIds: [data.fields[0].id], aliases: [], source: 'curated'});
  data.papers = data.papers.map(paper => ({...paper, fieldIds: [data.fields[0].id], displayFieldId: data.fields[0].id})); data.memberships = [];
  return data;
};
const hue = hexColor => { const [r, g, b] = [1, 3, 5].map(i => parseInt(hexColor.slice(i, i + 2), 16) / 255); const max = Math.max(r, g, b); const min = Math.min(r, g, b); if (max === min) return null; const d = max - min; const h = max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4; return (h * 60 + 360) % 360; };
const dist = (a, b) => { const x = [1, 3, 5].map(i => parseInt(a.slice(i, i + 2), 16)); const y = [1, 3, 5].map(i => parseInt(b.slice(i, i + 2), 16)); return Math.hypot(...x.map((v, i) => v - y[i])); };

test('3 palettes x light/dark: actual theme context drives color; contrast >= 3, no all-green, no sibling collisions', () => {
  const data = forest(); const seen = new Map();
  for (const palette of palettes) for (const theme of ['light', 'dark']) {
    const ctx = {id: palette.id, theme, panel: palette.themes[theme].panel, series: palette.themes[theme].series.map(row => row.hex)};
    const colors = data.fields.map(field => fieldPresentation(field, data, ctx).color); seen.set(`${palette.id}:${theme}`, colors);
    for (const color of colors) assert.ok(contrast(color, ctx.panel) >= 3, `${palette.id}/${theme} ${color} contrast ${contrast(color, ctx.panel)}`);
    const roots = colors.slice(0, 7); const families = new Set(roots.map(color => Math.round((hue(color) ?? 0) / 40))); assert.ok(families.size >= 4, `${palette.id}/${theme} needs >=4 hue families, got ${families.size}`);
    const green = roots.filter(color => { const h = hue(color); return h !== null && h >= 90 && h <= 170; }).length; assert.ok(green <= 2, `${palette.id}/${theme} has ${green} green roots`);
    for (let i = 0; i < colors.length; i += 1) for (let j = i + 1; j < colors.length; j += 1) assert.ok(dist(colors[i], colors[j]) >= 12, `${palette.id}/${theme} collision ${i}/${j}`);
  }
  assert.notDeepEqual(seen.get('atlas:light'), seen.get('atlas:dark')); assert.notDeepEqual(seen.get('atlas:light'), seen.get('calm:light')); assert.notDeepEqual(seen.get('calm:light'), seen.get('mocha:light'));
});

test('registry is append-only: insert, root add, reorder and rename never recolor existing categories', () => {
  const data = forest(); const first = buildCategoryColorRegistry(data); const snap = new Map(first.entries.map(entry => [entry.categoryKey, JSON.stringify(entry)]));
  const changed = structuredClone(data); changed.categoryColorRegistry = first;
  changed.fields = [{id: 'field:fixture-server:epoch-001:aaa-new-root', label: {en: 'New root'}, parentIds: [], aliases: [], source: 'curated'}, ...changed.fields.reverse().map(field => ({...field, label: {en: `Renamed ${field.id}`}})), {id: 'field:fixture-server:epoch-001:alpha-child-0', label: {en: 'inserted sibling'}, parentIds: [data.fields[0].id], aliases: [], source: 'curated'}];
  const second = buildCategoryColorRegistry(changed);
  for (const [key, json] of snap) assert.equal(JSON.stringify(second.entries.find(entry => entry.categoryKey === key)), json, `${key} was recolored`);
  assert.ok(second.entries.length > first.entries.length); assert.equal(new Set(second.entries.map(entry => entry.categoryKey)).size, second.entries.length);
  for (const key of ['atlas:light', 'atlas:dark', 'calm:light', 'calm:dark', 'mocha:light', 'mocha:dark']) assert.ok(second.entries.every(entry => /^#[0-9a-f]{6}$/.test(entry.hexByPaletteTheme[key])), key);
});

test('JS and Python registry generation agree on every palette/theme hex (shared function contract)', () => {
  const data = forest(); const js = buildCategoryColorRegistry(data);
  const rows = JSON.stringify(data.fields.map(field => ({id: field.id, parentIds: field.parentIds, kind: 'field'})));
  const out = execFileSync('python3', ['-I', '-c', 'import json,sys; sys.path.insert(0, sys.argv[2]); from atlas.category_colors import build_category_color_registry as b; print(json.dumps(b(json.loads(sys.argv[1]))))', rows, new URL('../..', import.meta.url).pathname], {encoding: 'utf8'});
  const py = JSON.parse(out); assert.equal(py.entries.length, js.entries.length);
  for (const entry of py.entries) { const mine = js.entries.find(row => row.categoryKey === entry.categoryKey); assert.ok(mine, entry.categoryKey); assert.deepEqual(mine.hexByPaletteTheme, entry.hexByPaletteTheme, entry.categoryKey); assert.equal(mine.family, entry.family); }
});

test('typed subject primary drives category color before accepted displayField and deepest field', () => {
  const data = forest(); const [alpha, beta] = data.fields; const child = data.fields[7];
  const paper = {...data.papers[0], fieldIds: [alpha.id, beta.id, child.id], displayFieldId: alpha.id}; data.papers = [paper];
  data.facetCatalog = {version: 1, entities: [{id: 'subject:beta', kind: 'subject', label: {en: 'Beta'}, broaderIds: [], aliases: [], legacyFieldId: beta.id}]};
  data.paperFacets = [{paperId: paper.id, directMemberships: [{facetId: 'subject:beta'}], primaryByFacet: {subject: 'subject:beta'}}];
  assert.equal(subjectPrimaryFieldId(paper, data), beta.id); assert.equal(categoryForPaper(paper, data).id, beta.id);
  data.paperFacets = []; assert.equal(categoryForPaper(paper, data).id, alpha.id); // accepted displayField beats deepest (child)
  data.memberships = [{paperId: paper.id, fieldId: alpha.id, accepted: false, method: 'model'}]; assert.equal(categoryForPaper(paper, data).id, child.id); // rejected display -> deepest accepted
});

test('backend fieldStats are preferred; fallback never uses displayFieldId; known primary + unknown = N', () => {
  const data = forest(); const [alpha, beta] = data.fields;
  data.fieldStats = {rows: [{fieldId: alpha.id, direct: 7, inclusive: 9, primary: 5}, {fieldId: beta.id, direct: 1, inclusive: 1, primary: 1}], unknown: 3, paperCount: 9};
  const counts = fieldPaperCounts(data); assert.deepEqual(counts.get(alpha.id), {direct: 7, inclusive: 9}); assert.equal(counts.primaryByField.get(alpha.id), 5); assert.equal(counts.unknown, 3);
  const children = {...data.fieldStats, rows: [{fieldId: alpha.id, direct: 7, inclusive: 3}]}; data.fieldStats = children; assert.ok(fieldPaperCounts(data).get(alpha.id).inclusive >= 7); // inclusive can never be below direct
});

test('density ramp is a separate low-chroma channel that never matches any category color', () => {
  const ramp = densityRamp().map(row => row.hex); const data = forest(); const colors = new Set();
  for (const palette of palettes) for (const theme of ['light', 'dark']) for (const field of data.fields) colors.add(fieldPresentation(field, data, {id: palette.id, theme, panel: palette.themes[theme].panel}).color);
  for (const color of ramp) for (const category of colors) assert.ok(dist(color, category) > 25, `${color} too close to category ${category}`);
  for (const color of ramp) { const [r, g, b] = [1, 3, 5].map(i => parseInt(color.slice(i, i + 2), 16)); assert.ok(Math.max(r, g, b) - Math.min(r, g, b) < 40, `${color} must be low chroma`); }
});

import {graphProjection} from '../../frontend/projection.js';
test('concept category is computed from the FULL eligible papers, not from the display sample', () => {
  const data = forest(); const [alpha, beta] = data.fields; const base = data.papers[0];
  data.concepts = [{id: 'concept:fixture-server:epoch-001:c1', label: {en: 'c1'}, aliases: [], source: 'user'}]; const cid = data.concepts[0].id;
  data.papers = [['a1', alpha], ['a2', alpha], ['b1', beta]].map(([key, field]) => ({...base, id: `paper:${key}`, fieldIds: [field.id], displayFieldId: field.id, conceptIds: [cid]})); data.memberships = []; data.edges = [];
  const sampleOnlyB = {papers: [data.papers[2]], edges: [], eligiblePaperIds: data.papers.map(paper => paper.id), eligiblePapers: 3, filterActive: false, eligibleConceptIds: [cid]};
  const graph = graphProjection(data, sampleOnlyB, {maxConceptNodes: 5, categoryPalette: {id: 'atlas', theme: 'light', panel: '#ffffff'}});
  const concept = graph.conceptNodes.find(node => node.data.id === cid); assert.ok(concept, 'concept node present');
  assert.equal(concept.data.categoryId, alpha.id, 'majority of ALL eligible papers (2 alpha vs 1 beta), even though only the beta paper is sampled');
  const smaller = graphProjection(data, {...sampleOnlyB, papers: [data.papers[0]]}, {maxConceptNodes: 5}); assert.equal(smaller.conceptNodes.find(node => node.data.id === cid).data.categoryId, alpha.id); // sample fraction never changes the semantic category
});

import {PAPER_EDGE_CAP} from '../../frontend/projection.js';
test('paper network edges are capped at 1000 deterministically with selected-edge/endpoint priority, types and direction preserved, counts truthful', () => {
  const data = forest(); const base = data.papers[0]; const n = 300;
  data.papers = Array.from({length: n}, (_, i) => ({...base, id: `paper:p${String(i).padStart(3, '0')}`, fieldIds: [data.fields[0].id], displayFieldId: data.fields[0].id, conceptIds: []})); data.memberships = [];
  data.edges = []; let k = 0; for (let i = 0; i < n; i += 1) for (let j = i + 1; j < n && data.edges.length < 3000; j += 1) { if ((i * 7 + j) % 11 === 0 || j === i + 1) data.edges.push({id: `edge:e${String(k++).padStart(5, '0')}`, source: data.papers[i].id, target: data.papers[j].id, type: k % 2 ? 'semantic' : 'citation', directed: k % 2 === 0, score: ((i * 31 + j * 17) % 100) / 100, provenance: {provider: 't', method: 't'}, evidenceTier: 'metadata'}); }
  assert.ok(data.edges.length > PAPER_EDGE_CAP * 2);
  const detail = {papers: data.papers, edges: data.edges, eligiblePaperIds: data.papers.map(p => p.id), eligiblePapers: n, eligibleEdges: data.edges.length, filterActive: false};
  const weakest = [...data.edges].sort((a, b) => a.score - b.score || a.id.localeCompare(b.id))[0];
  const graph = graphProjection(data, detail, {maxConceptNodes: 5, selectedEdgeId: weakest.id});
  assert.equal(graph.paperEdges.length, PAPER_EDGE_CAP); assert.equal(PAPER_EDGE_CAP, 1000);
  assert.deepEqual({...graph.paperStats}, {cap: 1000, candidateEdges: data.edges.length, displayedEdges: 1000, hiddenEdges: data.edges.length - 1000, eligibleEdges: data.edges.length, displayedNodes: n, eligibleNodes: n, selectedEdgeRetained: true});
  assert.equal(graph.paperEdges[0].data.id, weakest.id, 'the selected edge outranks every stronger edge');
  const types = new Set(graph.paperEdges.map(e => `${e.data.relation}:${e.data.directed}`)); assert.deepEqual([...types].sort(), ['citation:true', 'semantic:false']);
  const again = graphProjection(data, detail, {maxConceptNodes: 5, selectedEdgeId: weakest.id}); assert.deepEqual(again.paperEdges.map(e => e.data.id), graph.paperEdges.map(e => e.data.id)); // deterministic
  const scores = graph.paperEdges.slice(1).map(e => e.data.score); assert.ok(scores.slice(0, -1).every((s, i) => s >= scores[i + 1]), 'then strongest first');
  const hub = data.papers[250].id; const touching = graphProjection(data, detail, {maxConceptNodes: 5, selectedPaperId: hub}); const priority = data.edges.filter(e => e.source === hub || e.target === hub).length; assert.ok(touching.paperEdges.slice(0, priority).every(e => e.data.source === hub || e.data.target === hub), 'edges of the selected paper come first');
  const small = graphProjection(data, {...detail, edges: data.edges.slice(0, 10)}, {maxConceptNodes: 5}); assert.equal(small.paperStats.displayedEdges, 10); assert.equal(small.paperStats.hiddenEdges, 0);
});
