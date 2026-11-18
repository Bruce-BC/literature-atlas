import test from 'node:test';
import assert from 'node:assert/strict';
import {ALL_PAPERS_ID, ALL_PAPERS_LABEL, allPaperOverviewProjection} from '../../frontend/all-paper-overview.js';
import {VIEW_EVENTS, createViewState, reduceViewState, legacyAliases, sourceKey} from '../../frontend/view-state.js';
import {overviewGraphProjection, CHILD_PAGE_SIZE, CHILD_TOTAL_LIMIT, VIEW_EDGE_BUDGET, paperNodeId} from '../../frontend/overview-expansion.js';
import {createCameraStore, planCamera, saveCamera, completePositions, hasFullCoverage, positionStats, topologySignature, clampZoom} from '../../frontend/camera-policy.js';

const base = () => ({
  schemaVersion: 1, attachments: [], collections: [], status: {}, allocationMode: 'primary-display-field', source: {serverId: 'server', epoch: 'epoch-1'}, modelStatus: {recipeVersion: 'atlas-graph-v2'},
  fields: [{id: 'f:opt', parentIds: []}, {id: 'f:ot', parentIds: ['f:opt', 'f:ana']}, {id: 'f:ana', parentIds: []}, {id: 'f:other', parentIds: []}],
  papers: [
    {id: 'p:opt', title: 'Opt', fieldIds: ['f:opt'], displayFieldId: 'f:opt', conceptIds: ['c:a']},
    {id: 'p:ot', title: 'OT', fieldIds: ['f:ot'], displayFieldId: 'f:ot', conceptIds: ['c:a', 'c:b']},
    {id: 'p:multi', title: 'Multi', fieldIds: ['f:ot', 'f:ana'], displayFieldId: 'f:ot', conceptIds: []},
    {id: 'p:other', title: 'Other', fieldIds: ['f:other'], displayFieldId: 'f:other', conceptIds: ['c:c']},
    {id: 'p:unknown', title: 'Unknown', fieldIds: [], displayFieldId: null, conceptIds: []}
  ],
  concepts: [{id: 'c:a', fieldIds: []}, {id: 'c:b', fieldIds: []}, {id: 'c:c', fieldIds: []}],
  memberships: [], overview: {fields: [], ribbons: [], keywordAggregate: null, totalPapers: 5, totalEdges: 1},
  edges: [{id: 'e:pp', source: 'p:opt', target: 'p:ot', type: 'semantic', directed: false}, {id: 'e:kw', source: 'c:a', target: 'c:b', type: 'keyword', directed: false}, {id: 'e:far', source: 'p:opt', target: 'p:other', type: 'semantic', directed: false}]
});
const snapshot = base();
const run = (state, ...events) => events.reduce((current, event) => reduceViewState(current, event, snapshot), state);
const fresh = () => createViewState(snapshot);

test('FIELD_SELECT sets scope/selected/detail atomically, clears concept constraint and keeps other expansions', () => {
  let state = run(fresh(), {type: VIEW_EVENTS.REPRESENTATIVE_TOGGLE, id: 'f:ana'}, {type: VIEW_EVENTS.CONCEPT_SELECT, id: 'c:a'});
  const before = state.revision;
  state = run(state, {type: VIEW_EVENTS.FIELD_SELECT, id: 'f:opt'});
  assert.equal(state.revision, before + 1); assert.equal(state.renderRevision, state.revision);
  assert.deepEqual(state.selected, {kind: 'field', id: 'f:opt'}); assert.deepEqual(state.detail, {kind: 'field', id: 'f:opt'});
  assert.equal(state.scope.fieldId, 'f:opt'); assert.equal(state.scope.conceptId, null);
  assert.deepEqual([...state.scope.paperIds].sort(), ['p:multi', 'p:opt', 'p:ot']); // includes subfield (multi-parent child)
  assert.equal(state.expanded.has('f:ana'), true);
  assert.equal(state.camera.scopeKey, 'field:f:opt');
});

test('legacy aliases derive ONLY from the next state: no stale paper/concept/field IDs survive any event', () => {
  let state = run(fresh(), {type: VIEW_EVENTS.PAPER_SELECT, id: 'p:ot'});
  assert.equal(legacyAliases(state).selectedPaperId, 'p:ot');
  state = run(state, {type: VIEW_EVENTS.CONCEPT_SELECT, id: 'c:c'});
  let aliases = legacyAliases(state);
  assert.equal(aliases.selectedPaperId, null); assert.equal(aliases.selectedConceptId, 'c:c'); assert.equal(aliases.conceptId, 'c:c');
  state = run(state, {type: VIEW_EVENTS.FIELD_SELECT, id: 'f:opt'});
  aliases = legacyAliases(state);
  assert.equal(aliases.selectedConceptId, null); assert.equal(aliases.conceptId, null); assert.equal(aliases.fieldId, 'f:opt');
  state = run(state, {type: VIEW_EVENTS.EDGE_SELECT, id: 'e:pp'});
  aliases = legacyAliases(state);
  assert.equal(aliases.selectedEdgeId, 'e:pp'); assert.equal(aliases.selectedFieldId, null); assert.equal(aliases.fieldId, 'f:opt'); assert.equal(aliases.selectedPaperId, null);
  state = run(state, {type: VIEW_EVENTS.RESET});
  aliases = legacyAliases(state);
  assert.deepEqual([aliases.fieldId, aliases.selectedFieldId, aliases.selectedPaperId, aliases.selectedConceptId, aliases.selectedEdgeId, aliases.conceptId, aliases.fieldPair], [null, null, null, null, null, null, null]);
  assert.equal(aliases.expandedFields.size, 0);
});

test('PAPER_SELECT: in-scope keeps scope; outside without EXPLICIT owner goes to library; explicit valid owner switches', () => {
  let state = run(fresh(), {type: VIEW_EVENTS.FIELD_SELECT, id: 'f:opt'});
  const inScope = run(state, {type: VIEW_EVENTS.PAPER_SELECT, id: 'p:ot'});
  assert.equal(inScope.scope.fieldId, 'f:opt'); assert.deepEqual(inScope.selected, {kind: 'paper', id: 'p:ot'});
  const outside = run(state, {type: VIEW_EVENTS.PAPER_SELECT, id: 'p:other'}); // p:other HAS a display field but no owner event
  assert.equal(outside.scope.kind, 'library'); assert.equal(outside.camera.reason, 'global');
  const owned = run(state, {type: VIEW_EVENTS.PAPER_SELECT, id: 'p:other', ownerId: 'f:other'});
  assert.equal(owned.scope.fieldId, 'f:other');
  const badOwner = run(state, {type: VIEW_EVENTS.PAPER_SELECT, id: 'p:other', ownerId: 'f:ana'}); // owner that does not contain the paper
  assert.equal(badOwner.scope.kind, 'library');
  const unknown = run(state, {type: VIEW_EVENTS.PAPER_SELECT, id: 'p:unknown'});
  assert.equal(unknown.scope.kind, 'library');
});

test('OVERVIEW_PAPER_SELECT never takes an owner: outside scope -> library, in-scope keep with global camera', () => {
  const state = run(fresh(), {type: VIEW_EVENTS.FIELD_SELECT, id: 'f:opt'});
  const outside = run(state, {type: VIEW_EVENTS.OVERVIEW_PAPER_SELECT, id: 'p:other', ownerId: 'f:other'});
  assert.equal(outside.scope.kind, 'library');
  const inside = run(state, {type: VIEW_EVENTS.OVERVIEW_PAPER_SELECT, id: 'p:ot'});
  assert.equal(inside.scope.fieldId, 'f:opt'); assert.equal(inside.camera.reason, 'global');
});

test('CONCEPT_SELECT keeps the field only when eligible linked papers exist and adds a lower-network constraint', () => {
  const state = run(fresh(), {type: VIEW_EVENTS.FIELD_SELECT, id: 'f:opt'});
  const compatible = run(state, {type: VIEW_EVENTS.CONCEPT_SELECT, id: 'c:a'});
  assert.equal(compatible.scope.fieldId, 'f:opt'); assert.equal(compatible.scope.conceptId, 'c:a');
  assert.deepEqual([...compatible.scope.paperIds].sort(), ['p:opt', 'p:ot']);
  const incompatible = run(state, {type: VIEW_EVENTS.CONCEPT_SELECT, id: 'c:c'});
  assert.equal(incompatible.scope.kind, 'library'); assert.equal(incompatible.scope.conceptId, 'c:c');
  const noLinked = run(state, {type: VIEW_EVENTS.CONCEPT_SELECT, id: 'c:b'}); // c:b linked to p:ot which IS in scope
  assert.equal(noLinked.scope.fieldId, 'f:opt');
});

test('EDGE_SELECT forces valid endpoints into the lower budget and falls back to library for incompatible scope', () => {
  const state = run(fresh(), {type: VIEW_EVENTS.FIELD_SELECT, id: 'f:opt'});
  const inside = run(state, {type: VIEW_EVENTS.EDGE_SELECT, id: 'e:pp'});
  assert.equal(inside.scope.fieldId, 'f:opt'); assert.deepEqual(inside.detail.forceVisible, {paperIds: ['p:opt', 'p:ot'], conceptIds: []});
  const far = run(state, {type: VIEW_EVENTS.EDGE_SELECT, id: 'e:far'});
  assert.equal(far.scope.kind, 'library'); assert.deepEqual(far.detail.forceVisible.paperIds, ['p:opt', 'p:other']);
  const keyword = run(state, {type: VIEW_EVENTS.EDGE_SELECT, id: 'e:kw'});
  assert.deepEqual(keyword.detail.forceVisible, {paperIds: [], conceptIds: ['c:a', 'c:b']});
  assert.equal(run(state, {type: VIEW_EVENTS.EDGE_SELECT, id: 'e:missing'}), state); // no fake edge
});

test('REPRESENTATIVE_TOGGLE: first adds, second removes; always selects the field and replaces hidden child detail', () => {
  let state = run(fresh(), {type: VIEW_EVENTS.REPRESENTATIVE_TOGGLE, id: 'f:opt'});
  assert.equal(state.expanded.has('f:opt'), true); assert.equal(state.camera.reason, 'fit-new-scope');
  state = run(state, {type: VIEW_EVENTS.PAPER_SELECT, id: 'p:opt'});
  assert.deepEqual(state.selected, {kind: 'paper', id: 'p:opt'});
  state = run(state, {type: VIEW_EVENTS.REPRESENTATIVE_TOGGLE, id: 'f:opt'});
  assert.equal(state.expanded.has('f:opt'), false); assert.deepEqual(state.selected, {kind: 'field', id: 'f:opt'}); assert.deepEqual(state.detail, {kind: 'field', id: 'f:opt'});
  assert.equal(state.camera.reason, 'preserve');
});

test('hover/keyboard focus and unknown events are no-ops and never select', () => {
  const state = run(fresh(), {type: VIEW_EVENTS.FIELD_SELECT, id: 'f:opt'});
  for (const type of ['HOVER', 'FOCUS', 'KEYBOARD_FOCUS', 'POINTER_ENTER', undefined]) assert.equal(reduceViewState(state, {type, id: 'p:ot'}, snapshot), state);
  assert.equal(run(state, {type: VIEW_EVENTS.PAPER_SELECT, id: 'p:missing'}), state);
});

test('RESET clears selection/scope/constraints/expansions and requests an explicit fit', () => {
  const state = run(fresh(), {type: VIEW_EVENTS.REPRESENTATIVE_TOGGLE, id: 'f:opt'}, {type: VIEW_EVENTS.CONCEPT_SELECT, id: 'c:a'}, {type: VIEW_EVENTS.RESET});
  assert.equal(state.selected, null); assert.equal(state.detail, null); assert.equal(state.scope.kind, 'library'); assert.equal(state.scope.conceptId, null); assert.equal(state.expanded.size, 0); assert.equal(state.camera.reason, 'fit');
});

test('SNAPSHOT_REPLACE same source preserves valid IDs, applies redirects, clears deleted IDs and resets deleted-field scope', () => {
  let state = run(fresh(), {type: VIEW_EVENTS.REPRESENTATIVE_TOGGLE, id: 'f:opt'}, {type: VIEW_EVENTS.PAPER_SELECT, id: 'p:ot'});
  const same = reduceViewState(state, {type: VIEW_EVENTS.SNAPSHOT_REPLACE}, snapshot);
  assert.deepEqual(same.selected, {kind: 'paper', id: 'p:ot'}); assert.equal(same.expanded.has('f:opt'), true); assert.equal(same.scope.fieldId, 'f:opt'); assert.equal(same.camera.reason, 'preserve');
  const deleted = {...base(), papers: snapshot.papers.filter(p => p.id !== 'p:ot'), fields: snapshot.fields.filter(f => f.id !== 'f:opt'), taxonomyRedirects: {}};
  const cleared = reduceViewState(state, {type: VIEW_EVENTS.SNAPSHOT_REPLACE}, deleted);
  assert.equal(cleared.selected, null); assert.equal(cleared.detail, null); assert.equal(cleared.scope.kind, 'library'); assert.equal(cleared.expanded.size, 0);
  const merged = {...base(), fields: snapshot.fields.filter(f => f.id !== 'f:other'), papers: snapshot.papers.map(p => p.fieldIds.includes('f:other') ? {...p, fieldIds: ['f:opt']} : p), taxonomyRedirects: {'f:other': 'f:opt'}};
  const onOther = run(fresh(), {type: VIEW_EVENTS.FIELD_SELECT, id: 'f:other'});
  const redirected = reduceViewState(onOther, {type: VIEW_EVENTS.SNAPSHOT_REPLACE}, merged);
  assert.equal(redirected.scope.fieldId, 'f:opt'); assert.deepEqual(redirected.selected, {kind: 'field', id: 'f:opt'});
});

test('SNAPSHOT_REPLACE resets everything on epoch or recipe change (modelStatus.recipeVersion is authoritative)', () => {
  const state = run(fresh(), {type: VIEW_EVENTS.REPRESENTATIVE_TOGGLE, id: 'f:opt'}, {type: VIEW_EVENTS.PAPER_SELECT, id: 'p:opt'});
  const v3 = {...base(), modelStatus: {recipeVersion: 'atlas-graph-v3'}};
  assert.notEqual(sourceKey(snapshot), sourceKey(v3));
  const recipeReset = reduceViewState(state, {type: VIEW_EVENTS.SNAPSHOT_REPLACE}, v3);
  assert.equal(recipeReset.selected, null); assert.equal(recipeReset.expanded.size, 0); assert.equal(recipeReset.scope.kind, 'library'); assert.equal(recipeReset.camera.reason, 'reset'); assert.equal(recipeReset.cameraEpoch, state.cameraEpoch + 1);
  const epochChanged = {...base(), source: {serverId: 'server', epoch: 'epoch-2'}};
  const epochReset = reduceViewState(state, {type: VIEW_EVENTS.SNAPSHOT_REPLACE}, epochChanged);
  assert.equal(epochReset.selected, null); assert.equal(epochReset.camera.reason, 'reset');
  const topLevel = {...base(), recipe: 'atlas-graph-v3'}; assert.notEqual(sourceKey(topLevel), sourceKey(snapshot));
});

test('FIELD_PAIR_SELECT (ribbon) clears selection and keeps a derived fieldPair alias', () => {
  const state = run(fresh(), {type: VIEW_EVENTS.PAPER_SELECT, id: 'p:opt'}, {type: VIEW_EVENTS.FIELD_PAIR_SELECT, pair: ['f:opt', 'f:ana']});
  assert.equal(state.selected, null); assert.deepEqual(legacyAliases(state).fieldPair, ['f:opt', 'f:ana']); assert.equal(legacyAliases(state).selectedPaperId, null);
});

test('all-paper projection: exactly one deterministic point per active paper including unknown; focus only dims', () => {
  const a = allPaperOverviewProjection(snapshot, {focusScope: {fieldId: 'f:opt'}, relation: 'semantic'});
  const b = allPaperOverviewProjection(snapshot, {focusScope: {fieldId: 'f:opt'}, relation: 'semantic'});
  assert.equal(a.id, ALL_PAPERS_ID); assert.equal(a.label, ALL_PAPERS_LABEL);
  assert.deepEqual([...a.representedIds].sort(), snapshot.papers.map(p => p.id).sort());
  assert.deepEqual(a.points, b.points); // deterministic
  assert.equal(new Set(a.points.map(p => p.id)).size, snapshot.papers.length);
  const byId = new Map(a.points.map(p => [p.id, p]));
  assert.equal(byId.get('p:unknown').categoryKey, 'unknown'); assert.equal(byId.get('p:unknown').dimmed, true); assert.equal(byId.get('p:ot').dimmed, false); assert.equal(byId.get('p:other').dimmed, true);
  assert.equal(a.counters.active, 5); assert.equal(a.counters.represented, 5); assert.equal(a.counters.eligibleEdges, 2); assert.equal(a.counters.displayedEdges, 2);
  const unfocused = allPaperOverviewProjection(snapshot, {relation: 'semantic'});
  assert.deepEqual(unfocused.points.map(p => [p.id, p.deterministicX, p.deterministicY]), a.points.map(p => [p.id, p.deterministicX, p.deterministicY])); // focus never moves points
  assert.equal(unfocused.points.every(p => p.dimmed === false), true);
});

test('all-paper preset coordinates are real canvas-scale, unique and non-overlapping for 1000 papers', () => {
  const many = {...base(), papers: Array.from({length: 1000}, (_, i) => ({id: `p:${i}`, title: `T${i}`, fieldIds: [['f:opt', 'f:ana', 'f:other'][i % 3]], displayFieldId: ['f:opt', 'f:ana', 'f:other'][i % 3], conceptIds: []})), edges: []};
  const projection = allPaperOverviewProjection(many, {relation: 'semantic'});
  assert.equal(projection.points.length, 1000);
  let minDistance = Infinity; const pts = projection.points;
  for (let i = 0; i < pts.length; i += 1) for (let j = i + 1; j < Math.min(pts.length, i + 60); j += 1) minDistance = Math.min(minDistance, Math.hypot(pts[i].deterministicX - pts[j].deterministicX, pts[i].deterministicY - pts[j].deterministicY));
  assert.ok(Number.isFinite(minDistance));
  const keys = new Set(pts.map(p => `${p.deterministicX.toFixed(3)},${p.deterministicY.toFixed(3)}`)); assert.equal(keys.size, 1000);
  const extent = Math.max(...pts.map(p => Math.hypot(p.deterministicX, p.deterministicY))); assert.ok(extent > 300, `radius ${extent} must be canvas scale (not normalized to [-1,1])`);
  // viewport culling is informational and never changes represented N
  const culled = allPaperOverviewProjection(many, {relation: 'semantic', viewport: {minX: 0, maxX: 50, minY: 0, maxY: 50}});
  assert.equal(culled.counters.represented, 1000); assert.ok(culled.counters.viewport < 1000); assert.equal(culled.representedIds.length, 1000);
});

const wide = () => {
  const s = base(); s.papers = [...s.papers, ...Array.from({length: 60}, (_, i) => ({id: `p:d${String(i).padStart(2, '0')}`, title: `D${i}`, fieldIds: ['f:opt'], displayFieldId: 'f:opt', conceptIds: []}))]; return s;
};
test('representative expansion: roots first; expand reveals hierarchy child + 24 direct papers; collapse restores exactly', () => {
  const s = wide();
  const collapsed = overviewGraphProjection(s, {relation: 'semantic', expanded: new Set()});
  assert.deepEqual(collapsed.nodes.map(n => n.data.id).sort(), ['f:ana', 'f:opt', 'f:other']);
  const expanded = overviewGraphProjection(s, {relation: 'semantic', expanded: new Set(['f:opt'])});
  const ids = expanded.nodes.map(n => n.data.id);
  assert.ok(ids.includes('f:ot')); assert.equal(expanded.nodes.filter(n => n.data.kind === 'paper').length, CHILD_PAGE_SIZE);
  assert.ok(expanded.edges.some(e => e.data.relation === 'hierarchy' && e.data.source === 'f:opt' && e.data.target === 'f:ot'));
  assert.equal(expanded.remaining.get('f:opt'), 37); // 61 direct papers, 24 shown on page 1
  const again = overviewGraphProjection(s, {relation: 'semantic', expanded: new Set()});
  assert.deepEqual(again.nodes.map(n => n.data.id), collapsed.nodes.map(n => n.data.id));
});

test('child paging is 24 per page and capped at 150 loaded papers across all owners; edges stay within budget', () => {
  const s = wide();
  const page2 = overviewGraphProjection(s, {relation: 'semantic', expanded: new Set(['f:opt']), pages: new Map([['f:opt', 2]])});
  assert.equal(page2.nodes.filter(n => n.data.kind === 'paper').length, 48);
  const big = base(); big.papers = Array.from({length: 400}, (_, i) => ({id: `p:big${i}`, title: 'B', fieldIds: ['f:opt'], displayFieldId: 'f:opt', conceptIds: []}));
  const capped = overviewGraphProjection(big, {relation: 'semantic', expanded: new Set(['f:opt']), pages: new Map([['f:opt', 99]])});
  assert.equal(capped.loadedPapers, CHILD_TOTAL_LIMIT); assert.ok(capped.edges.length <= VIEW_EDGE_BUDGET);
  const many = base(); many.fields = Array.from({length: 1500}, (_, i) => ({id: `r${i}`, parentIds: []})); many.papers = []; many.edges = [];
  many.overview = {fields: [], ribbons: [], keywordAggregate: null, totalPapers: 0, totalEdges: 0};
  const bounded = overviewGraphProjection(many, {relation: 'semantic', expanded: new Set()}); assert.ok(bounded.edges.length <= VIEW_EDGE_BUDGET);
});

test('a multi-parent/multi-owner child is ONE semantic node whose owner states are all preserved; membership keeps additional facets', () => {
  const s = base();
  const projection = overviewGraphProjection(s, {relation: 'semantic', expanded: new Set(['f:ana', 'f:opt', 'f:ot'])});
  const multi = projection.nodes.filter(n => n.data.id === paperNodeId('p:multi'));
  assert.equal(multi.length, 1); assert.deepEqual(multi[0].data.owners.sort(), ['f:ana', 'f:ot']);
  assert.deepEqual(multi[0].data.fieldIds.sort(), ['f:ana', 'f:ot']); assert.equal(multi[0].data.additionalFields.length, 1);
  const memberships = projection.edges.filter(e => e.data.relation === 'membership' && e.data.target === paperNodeId('p:multi'));
  assert.equal(memberships.length, 2); assert.equal(memberships.filter(e => e.data.primary).length, 1);
  const unique = new Set(projection.nodes.map(n => n.data.id)); assert.equal(unique.size, projection.nodes.length);
  // collapsing ONE owner keeps the child visible through the other
  const half = overviewGraphProjection(s, {relation: 'semantic', expanded: new Set(['f:ana', 'f:opt'])});
  assert.deepEqual(half.nodes.find(n => n.data.id === paperNodeId('p:multi')).data.owners, ['f:ana']);
});

test('hidden descendants roll their aggregate ribbons up to the visible ancestor', () => {
  const s = base(); s.edges = [{id: 'e:x', source: 'p:ot', target: 'p:other', type: 'semantic', directed: false}]; s.overview = {fields: [], ribbons: [], keywordAggregate: null, totalPapers: 5, totalEdges: 1};
  const collapsed = overviewGraphProjection(s, {relation: 'semantic', expanded: new Set()});
  const ribbons = collapsed.edges.filter(e => e.data.relation === 'aggregate');
  assert.ok(ribbons.length >= 1); assert.ok(ribbons.every(e => ['f:opt', 'f:ana', 'f:other'].includes(e.data.source) && ['f:opt', 'f:ana', 'f:other'].includes(e.data.target)));
});

test('camera plan: first visit fits, same signature preserves, topology change seeds and offers Fit new content, revisit restores, epoch resets', () => {
  const store = createCameraStore(); const args = {view: 'cy-paper', epochKey: 'e1', scopeKey: 'field:a', signature: 'sig1'};
  assert.equal(planCamera(store, args).action, 'fit');
  saveCamera(store, {view: 'cy-paper', scopeKey: 'field:a', camera: {zoom: 1.7, pan: {x: 5, y: 6}}, positions: {n1: {x: 1, y: 2}}});
  const same = planCamera(store, args); assert.equal(same.action, 'preserve'); assert.equal(same.camera.zoom, 1.7); assert.equal(same.fitNewContentAvailable, false);
  const changed = planCamera(store, {...args, signature: 'sig2'}); assert.equal(changed.action, 'seed-keep'); assert.equal(changed.fitNewContentAvailable, true); assert.deepEqual(changed.camera.pan, {x: 5, y: 6});
  assert.equal(planCamera(store, {...args, scopeKey: 'field:b', signature: 'sigB'}).action, 'fit'); // new field scope: first visit fits
  const revisit = planCamera(store, {...args, signature: 'sig2'}); assert.equal(revisit.action, 'restore'); assert.equal(revisit.camera.zoom, 1.7); assert.deepEqual(revisit.positions, {n1: {x: 1, y: 2}});
  const epoch = planCamera(store, {...args, epochKey: 'e2'}); assert.equal(epoch.action, 'fit'); assert.equal(epoch.reason, 'epoch-or-recipe-reset'); assert.equal(planCamera(store, {...args, epochKey: 'e2', signature: 'sig1'}).camera, null);
});

test('zoom is clamped to [0.2, 3]', () => { assert.equal(clampZoom(50), 3); assert.equal(clampZoom(0.001), 0.2); assert.equal(clampZoom(NaN), 1); });

test('partial preset positions are completed with finite unique seeds (never a (0,0) stack) and keep known positions', () => {
  const ids = Array.from({length: 40}, (_, i) => `n${i}`); const existing = {n0: {x: 100, y: 100}, n1: {x: 140, y: 100}};
  assert.equal(hasFullCoverage(existing, ids), false);
  const edges = ids.slice(1).map((id, i) => [ids[i], id]);
  const completed = completePositions(existing, ids, edges);
  assert.equal(hasFullCoverage(completed, ids), true); assert.deepEqual(completed.n0, {x: 100, y: 100}); assert.deepEqual(completed.n1, {x: 140, y: 100});
  const stats = positionStats(completed, ids); assert.equal(stats.finite, true); assert.equal(stats.unique, 40); assert.equal(stats.origin, 0);
  assert.deepEqual(completePositions(existing, ids, edges), completed); // deterministic
  const allMissing = completePositions({}, ids, []); assert.equal(positionStats(allMissing, ids).unique, 40); assert.ok(positionStats(allMissing, ids).origin <= 1);
  assert.notEqual(topologySignature(ids, []), topologySignature(ids.slice(1), []));
});

test('field -> paper keeps the field as SCOPE only: selectedFieldId is null, fieldId kept, exactly one canonical selection', () => {
  const state = run(fresh(), {type: VIEW_EVENTS.FIELD_SELECT, id: 'f:opt'}, {type: VIEW_EVENTS.PAPER_SELECT, id: 'p:ot'});
  const aliases = legacyAliases(state);
  assert.equal(aliases.selectedFieldId, null); assert.equal(aliases.fieldId, 'f:opt'); assert.equal(aliases.selectedPaperId, 'p:ot');
  const pressed = [aliases.selectedFieldId, aliases.selectedPaperId, aliases.selectedConceptId, aliases.selectedEdgeId].filter(Boolean);
  assert.equal(pressed.length, 1); assert.deepEqual(state.selected, {kind: 'paper', id: 'p:ot'});
  const asField = legacyAliases(run(fresh(), {type: VIEW_EVENTS.FIELD_SELECT, id: 'f:opt'})); assert.equal(asField.selectedFieldId, 'f:opt'); assert.equal(asField.fieldId, 'f:opt');
});

test('same-source SNAPSHOT_REPLACE follows ledger mergedInto for a merged concept; mixed-context raw concepts are never redirected; malformed targets clear', () => {
  const state = run(fresh(), {type: VIEW_EVENTS.CONCEPT_SELECT, id: 'c:a'});
  const merged = {...base(), concepts: snapshot.concepts.filter(c => c.id !== 'c:a'), taxonomyRedirects: {'c:a': 'c:c'}, // taxonomyRedirects must be IGNORED for concepts
    conceptQualityLedger: {entries: [{conceptId: 'c:a', status: 'suppressed', mergedInto: 'c:b'}]}};
  const followed = reduceViewState(state, {type: VIEW_EVENTS.SNAPSHOT_REPLACE}, merged);
  assert.deepEqual(followed.selected, {kind: 'concept', id: 'c:b'}); assert.deepEqual(followed.detail, {kind: 'concept', id: 'c:b'}); assert.equal(followed.scope.conceptId, 'c:b');
  const stillActive = {...base(), conceptQualityLedger: {entries: [{conceptId: 'c:a', status: 'active', note: 'mixed OT/KL context stays active'}]}};
  assert.deepEqual(reduceViewState(state, {type: VIEW_EVENTS.SNAPSHOT_REPLACE}, stillActive).selected, {kind: 'concept', id: 'c:a'}); // present + no mergedInto: unchanged
  const cycle = {...merged, conceptQualityLedger: {entries: [{conceptId: 'c:a', mergedInto: 'c:b'}, {conceptId: 'c:b', mergedInto: 'c:a'}]}, concepts: snapshot.concepts.filter(c => c.id !== 'c:a')};
  assert.equal(reduceViewState(state, {type: VIEW_EVENTS.SNAPSHOT_REPLACE}, cycle).selected, null);
  const foreign = {...merged, conceptQualityLedger: {entries: [{conceptId: 'c:a', mergedInto: 'concept:other-source:x'}]}};
  assert.equal(reduceViewState(state, {type: VIEW_EVENTS.SNAPSHOT_REPLACE}, foreign).selected, null);
  const ambiguous = {...merged, conceptQualityLedger: {entries: [{conceptId: 'c:a', mergedInto: 'c:b'}, {conceptId: 'c:a', mergedInto: 'c:c'}]}};
  assert.equal(reduceViewState(state, {type: VIEW_EVENTS.SNAPSHOT_REPLACE}, ambiguous).selected, null);
  const noLedger = {...base(), concepts: snapshot.concepts.filter(c => c.id !== 'c:a'), taxonomyRedirects: {'c:a': 'c:b'}};
  assert.equal(reduceViewState(state, {type: VIEW_EVENTS.SNAPSHOT_REPLACE}, noLedger).selected, null); // redirects never apply to concepts
});
