import test from 'node:test';
import assert from 'node:assert/strict';
import {cloudPanelModel, cloudSelection, isNoiseLabel, stripDisplayTags, ledgerSets, OTHER_LABEL, DIRECT_LABEL, NO_YEAR_LABEL, PANEL_LIMITS} from '../../frontend/cloud-panel.js';
import {layoutCollectionClouds, UNFILED_CLOUD_ID} from '../../frontend/collection-clouds.js';
import {sampleDetail} from '../../frontend/sampling.js';
import {VIEW_EVENTS, createViewState, reduceViewState, legacyAliases, viewStateSnapshot} from '../../frontend/view-state.js';
import {createTrace} from '../../frontend/trace.js';

// Synthetic data only.
const P = i => `zotero:t:0:P${String(i).padStart(2, '0')}`; const C = key => `collection:t:0:${key}`; const K = key => `concept:t:e:${key}`;
let edgeSeq = 0; const edge = (a, b, type = 'semantic', score = 0.9) => ({id: `edge:t:${edgeSeq += 1}`, source: P(a), target: P(b), type, directed: type === 'citation', score});
const concept = (key, label) => ({id: K(key), label: {en: label}, aliases: [], source: 'extracted'});

function snapshot() {
  edgeSeq = 0;
  const papers = Array.from({length: 24}, (_, i) => ({id: P(i), title: `Synthetic paper ${i}`, year: 2010 + (i % 5), authors: [`Author ${i}`, 'Second'], fieldIds: [], conceptIds: []}));
  papers[3].year = null; papers[4].year = '2020'; papers[5].authors = []; papers[0].title = '<b>systemfit</b> for <i>E. coli</i> panels';
  const tag = (ids, key) => ids.forEach(i => papers[i].conceptIds.push(K(key)));
  tag([0, 1, 2, 3], 'phage'); tag([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15], 'common'); tag([0, 1], 'msc'); tag([0, 1, 2], 'list'); tag([0, 1, 2], 'sixteen'); tag([0, 1], 'ai'); tag([0, 1, 2], 'hidden'); tag([4, 5, 6], 'host'); tag([4, 5, 6], 'zeta');
  const concepts = [concept('phage', 'Phage therapy'), concept('common', 'Common topic'), concept('msc', '11M20'), concept('list', '14M25, 52B20'), concept('sixteen', '16S'), concept('ai', 'AI'), concept('host', 'Host range'), concept('zeta', 'Ztail term')];
  // A: P00..P09 (planted LPA groups {0,1,2,3} and {4,5,6}, leftovers 7,8,9); B: P08..P15 (P08, P09 shared); D (parent) with children; unfiled: P22, P23
  const collections = [
    {id: C('A'), label: 'Cloud A', paperIds: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9].map(P)},
    {id: C('B'), label: 'Cloud B', paperIds: [8, 9, 10, 11, 12, 13, 14, 15].map(P)},
    {id: C('D'), label: 'Parent D', parentId: null, paperIds: [16].map(P)},
    {id: C('D1'), label: 'Child D1', parentId: C('D'), paperIds: [17, 18, 19].map(P)},
    {id: C('D2'), label: 'Child D2', parentId: C('D'), paperIds: [19, 20].map(P)},
    {id: C('D2a'), label: 'Grandchild D2a', parentId: C('D2'), paperIds: [21].map(P)}
  ];
  const edges = [edge(0, 1), edge(0, 2), edge(1, 2), edge(2, 3), edge(1, 3), edge(4, 5), edge(5, 6), edge(4, 6),
    edge(9, 10), edge(7, 12), edge(7, 12, 'keyword', 1), edge(3, 11, 'citation'), edge(6, 16, 'citation'),
    edge(22, 0, 'semantic', 0.8), edge(22, 1, 'semantic', 0.85), edge(23, 17, 'semantic', 0.9), edge(23, 18, 'semantic', 0.8), edge(22, 23, 'semantic', 0.99)];
  const conceptQualityLedger = {version: 1, suppressed: [K('hidden')], protected: [K('sixteen')], user: [K('ai')]};
  return {schemaVersion: 1, source: {id: 's', serverId: 't', epoch: 'e'}, papers, concepts, collections, edges, conceptQualityLedger, memberships: [], fields: [], attachments: []};
}

test('noise rule: numeric-leading, MSC-like, code lists and <= 2 chars are noise; ordinary labels are not', () => {
  for (const label of ['11M20', '14M25, 52B20', '16S', '000 bacterial', 'AI', 'x', '', 'Q11, Q12', '62H30']) assert.equal(isNoiseLabel(label), true, label);
  for (const label of ['Phage therapy', 'Ras, Raf, MEK', 'COVID-19', 'p53', 'KL divergence', '합성 개념']) assert.equal(isNoiseLabel(label), false, label);
});

test('display-only title tags are stripped as text; anything else stays visible text', () => {
  assert.equal(stripDisplayTags('<b>systemfit</b> for <i>E. coli</i> panels'), 'systemfit for E. coli panels');
  assert.equal(stripDisplayTags('A<sub>2</sub>B<sup>+</sup> <span class="nocase">pH</span> <SC>x</SC>'), 'A2B+ pH x');
  assert.equal(stripDisplayTags('<img src=x onerror=alert(1)> & 한국어'), '<img src=x onerror=alert(1)> & 한국어');
  assert.equal(stripDisplayTags(null), '');
});

test('ledger: suppressed always excluded, protected/user exempt from the noise rule (incl. entries[])', () => {
  const sets = ledgerSets({conceptQualityLedger: {suppressed: ['a'], protected: [{conceptId: 'b'}], entries: [{conceptId: 'c', status: 'user'}, {conceptId: 'd', status: 'suppressed'}]}});
  assert.deepEqual([...sets.suppressed].sort(), ['a', 'd']); assert.deepEqual([...sets.exempt].sort(), ['b', 'c']);
  const model = cloudPanelModel(snapshot(), C('A'));
  const labels = model.concepts.map(row => row.label);
  assert.ok(labels.includes('16S'), 'a protected numeric-leading concept survives the noise rule');
  assert.ok(labels.includes('AI'), 'a user-owned short concept survives the noise rule');
  assert.ok(!labels.includes('11M20') && !labels.includes('14M25, 52B20'), 'unprotected codes are filtered');
  assert.ok(!model.concepts.some(row => row.id === K('hidden')), 'suppressed concepts never appear');
  assert.ok(model.concepts.length <= PANEL_LIMITS.concepts);
});

test('concept distinctiveness ranking: p_C * ln(p_C / p_L), ties by count then label then id', () => {
  const model = cloudPanelModel(snapshot(), C('A'));
  const byId = new Map(model.concepts.map(row => [row.id, row]));
  const expected = (a, n, A, N) => Number(((a / n) * Math.log((a / n) / (A / N))).toFixed(6));
  assert.equal(byId.get(K('phage')).score, expected(4, 10, 4, 24));
  assert.equal(byId.get(K('common')).score, expected(10, 10, 16, 24));
  for (let i = 1; i < model.concepts.length; i += 1) { const [a, b] = [model.concepts[i - 1], model.concepts[i]]; assert.ok(a.score > b.score || (a.score === b.score && (a.count > b.count || (a.count === b.count && a.label <= b.label))), `${a.label} before ${b.label}`); }
  // 'Host range' and 'Ztail term' tie exactly (same papers): label order decides
  const host = model.concepts.findIndex(row => row.id === K('host')); const zeta = model.concepts.findIndex(row => row.id === K('zeta'));
  assert.equal(model.concepts[host].score, model.concepts[zeta].score); assert.ok(host < zeta);
  assert.equal(model.concepts[0].id, K('common'), 'in every cloud paper (p_C 1) vs 2/3 of the library outranks Phage therapy (0.4 vs 1/6)');
  assert.ok(model.concepts[0].score > byId.get(K('phage')).score);
});

test('sub-items: auto LPA groups (< 3 merged into one 기타 · 자동) sum to the unique count and match the layout module', () => {
  const snap = snapshot(); const model = cloudPanelModel(snap, C('A'));
  assert.equal(model.subItems.kind, 'auto');
  assert.equal(model.subItems.items.reduce((sum, item) => sum + item.count, 0), model.size); assert.equal(model.size, 10);
  assert.deepEqual(model.subItems.items.map(item => item.count), [4, 3, 3]);
  const other = model.subItems.items.at(-1); assert.equal(other.label, OTHER_LABEL); assert.ok(other.auto && other.other);
  assert.match(model.subItems.items[0].label, /^Phage therapy · .+ · 자동$/, 'an auto group is labelled by its most distinctive concepts and marked automatic');
  assert.ok(model.subItems.items.every(item => item.auto));
  assert.deepEqual(new Set(model.subItems.items.flatMap(item => item.paperIds)), new Set(model.paperIds));
  const layout = layoutCollectionClouds(snap, {relation: 'semantic', useMemo: false});
  assert.deepEqual(layout.clouds.find(cloud => cloud.id === C('A')).subgroups.map(group => group.size), model.subItems.items.map(item => item.count));
});

test('sub-items: child collections (parentId rollup) partition the cloud; the rest is 직접 소속', () => {
  const model = cloudPanelModel(snapshot(), C('D'));
  assert.equal(model.subItems.kind, 'collections'); assert.equal(model.size, 6);
  assert.deepEqual(model.subItems.items.map(item => [item.label, item.count, item.total, item.auto]), [['Child D1', 3, 3, false], ['Child D2', 2, 3, false], [DIRECT_LABEL, 1, 1, false]]);
  assert.equal(model.subItems.items.reduce((sum, item) => sum + item.count, 0), model.size);
  assert.deepEqual(model.subItems.items[1].paperIds, [P(20), P(21)], 'the grandchild rolls up; the shared paper stays with the larger child');
});

// F3: many automatic sub-groups. Groups of sizes 6,5,5,4,4,4,3,3,3,3,3 (11 groups, each a semantic clique) + 3 isolated papers (기타).
function manyGroupsSnapshot() {
  edgeSeq = 0; const sizes = [6, 5, 5, 4, 4, 4, 3, 3, 3, 3, 3]; const papers = []; const edges = []; let next = 0;
  for (const size of sizes) { const members = Array.from({length: size}, () => next++); for (const i of members) papers.push({id: P(i), title: `Many ${i}`, year: 2015, authors: ['A'], fieldIds: [], conceptIds: []}); for (let a = 0; a < members.length; a += 1) for (let b = a + 1; b < members.length; b += 1) edges.push(edge(members[a], members[b])); }
  for (let k = 0; k < 3; k += 1) { papers.push({id: P(next), title: `Lone ${next}`, year: 2015, authors: ['A'], fieldIds: [], conceptIds: []}); next += 1; }
  return {schemaVersion: 1, source: {id: 's', serverId: 't', epoch: 'e'}, papers, concepts: [], collections: [{id: C('M'), label: 'Many', paperIds: papers.map(row => row.id)}], edges, memberships: [], fields: [], attachments: []};
}

test('F3 sub-items: the model keeps every group and marks the 8 largest + 기타 as visible (기타 last, not counted)', () => {
  assert.equal(PANEL_LIMITS.subItems, 8);
  const model = cloudPanelModel(manyGroupsSnapshot(), C('M')); const items = model.subItems.items;
  assert.equal(items.length, 12, 'full list: 11 groups + 기타'); assert.equal(items.reduce((sum, item) => sum + item.count, 0), model.size);
  assert.equal(model.subItems.visibleIds.length, 9); assert.equal(model.subItems.moreCount, 3);
  assert.equal(model.subItems.visibleIds.at(-1), items.at(-1).id); assert.ok(items.at(-1).other, '기타 stays visible as the last row');
  const largest = items.filter(item => !item.other).map((item, index) => [item, index]).sort((a, b) => b[0].count - a[0].count || a[1] - b[1]).slice(0, 8).map(([item]) => item.id);
  assert.deepEqual(new Set(model.subItems.visibleIds.slice(0, -1)), new Set(largest), 'the 8 largest groups');
  assert.ok(items.filter(item => !model.subItems.visibleIds.includes(item.id)).every(item => item.count === 3 && !item.other));
  const small = cloudPanelModel(snapshot(), C('A')); assert.deepEqual(small.subItems.visibleIds, small.subItems.items.map(item => item.id)); assert.equal(small.subItems.moreCount, 0, 'short lists show everything');
  const hiddenId = items.find(item => !model.subItems.visibleIds.includes(item.id)).id;
  assert.equal(cloudPanelModel(manyGroupsSnapshot(), C('M'), {subItemId: hiddenId}).selectedSubItemId, hiddenId, 'a remainder group can still be selected');
});

test('representative papers: intra-cloud semantic degree desc -> year desc -> id; title tags stripped; first author', () => {
  const model = cloudPanelModel(snapshot(), C('A'));
  // degree 3: P02 (2012) before P01 (2011); degree 2: P04 ('2020') > P06 (2011) > P00 (2010) = P05 (2010, id later) > P03 (no year)
  assert.deepEqual(model.papers.map(row => row.id), [P(2), P(1), P(4), P(6), P(0)]);
  assert.deepEqual(model.papers.map(row => row.degree), [3, 3, 2, 2, 2]);
  assert.equal(model.papers[4].title, 'systemfit for E. coli panels'); assert.equal(model.papers[0].firstAuthor, 'Author 2');
  assert.equal(model.papers.length, PANEL_LIMITS.papers);
  const tail = cloudPanelModel(snapshot(), C('A'), {highlightPaperId: P(9)});
  assert.equal(tail.highlightedPaper.id, P(9), 'a highlighted paper outside the top 5 is still listed'); assert.equal(tail.highlightPaperId, P(9));
  assert.equal(cloudPanelModel(snapshot(), C('A'), {highlightPaperId: P(15)}).highlightPaperId, null, 'a paper outside the cloud is never highlighted');
  assert.equal(cloudPanelModel(snapshot(), C('A'), {highlightPaperId: P(1)}).papers.find(row => row.id === P(1)).highlighted, true);
  const noAuthor = cloudPanelModel(snapshot(), C('A'), {highlightPaperId: P(5)}); assert.equal([...noAuthor.papers, noAuthor.highlightedPaper].filter(Boolean).find(row => row.id === P(5)).firstAuthor, '—');
});

test('year distribution: ascending years, string years parsed, missing years in a 연도 없음 bucket, sums to the cloud', () => {
  const model = cloudPanelModel(snapshot(), C('A'));
  assert.deepEqual(model.years.map(row => [row.label, row.count]), [['2010', 2], ['2011', 2], ['2012', 2], ['2013', 1], ['2014', 1], ['2020', 1], [NO_YEAR_LABEL, 1]]);
  assert.equal(model.years.at(-1).year, null); assert.equal(model.years.reduce((sum, row) => sum + row.count, 0), model.size);
});

test('related clouds: semantic and citation counted separately (explicit 0), keyword never mixed in, top 5 order', () => {
  const model = cloudPanelModel(snapshot(), C('A'));
  const rows = Object.fromEntries(model.related.map(row => [row.cloudId, row]));
  assert.deepEqual({semantic: rows[C('B')].semantic, citation: rows[C('B')].citation}, {semantic: 2, citation: 1}, 'P09-P10 (P09 shared) and P07-P12 semantic; the P07-P12 keyword edge is ignored');
  assert.deepEqual({semantic: rows[C('D')].semantic, citation: rows[C('D')].citation}, {semantic: 0, citation: 1});
  assert.deepEqual({semantic: rows[UNFILED_CLOUD_ID].semantic, citation: rows[UNFILED_CLOUD_ID].citation}, {semantic: 2, citation: 0});
  assert.deepEqual(model.related.map(row => row.cloudId), [C('B'), UNFILED_CLOUD_ID, C('D')]);
  assert.ok(model.related.length <= PANEL_LIMITS.related && model.suggestion === null, 'core clouds carry no suggestion');
});

test('unfiled cloud: nearest core cloud = largest summed semantic score -> larger cloud -> id; none -> 제안 없음 (cloudId null)', () => {
  const snap = snapshot(); const model = cloudPanelModel(snap, UNFILED_CLOUD_ID);
  assert.equal(model.unfiled, true); assert.equal(model.size, 2);
  assert.equal(model.suggestion.cloudId, C('D')); assert.equal(model.suggestion.score, Number((0.9 + 0.8).toFixed(6))); assert.equal(model.suggestion.edges, 2);
  const tie = snapshot(); tie.edges = tie.edges.filter(row => !row.source.endsWith('P23')).concat([edge(23, 10, 'semantic', 0.8), edge(23, 11, 'semantic', 0.85)]);
  const tied = cloudPanelModel(tie, UNFILED_CLOUD_ID); assert.equal(tied.suggestion.cloudId, C('A'), 'A (10 papers) beats B (8 papers) at an equal score sum');
  const none = snapshot(); none.edges = none.edges.filter(row => !row.source.endsWith('P22') && !row.source.endsWith('P23'));
  assert.deepEqual(cloudPanelModel(none, UNFILED_CLOUD_ID).suggestion, {cloudId: null});
});

test('the model reads the full snapshot only: identical for every detail-sample fraction and never mutates the snapshot', () => {
  const baseline = JSON.stringify(cloudPanelModel(snapshot(), C('A'), {highlightPaperId: P(2)}));
  for (const fraction of [0, 0.1, 0.35, 1]) {
    const snap = snapshot(); const before = JSON.stringify(snap);
    const detail = sampleDetail(snap, {fraction, seed: 'atlas-ui', selectedIds: [P(2)], filter: {}, relationTypes: ['semantic']});
    assert.equal(JSON.stringify(cloudPanelModel(snap, C('A'), {highlightPaperId: P(2)})), baseline, `fraction ${fraction}`);
    assert.equal(JSON.stringify(snap), before);
    if (fraction < 1) assert.ok(detail.papers.length < snap.papers.length);
  }
  assert.equal(cloudPanelModel(snapshot(), 'collection:t:0:missing'), null); assert.equal(cloudSelection(snapshot(), null), null);
  assert.deepEqual(cloudSelection(snapshot(), C('D')).paperIds, [16, 17, 18, 19, 20, 21].map(P));
});

// ---- reducer -------------------------------------------------------------------------------------------------
const run = (state, snap, ...events) => events.reduce((current, event) => reduceViewState(current, event, snap), state);

test('CLOUD_SELECT: cloud scope dims only, keeps the library camera, keeps an in-cloud paper and drops an outside one', () => {
  const snap = snapshot(); const a = cloudSelection(snap, C('A'));
  let state = run(createViewState(snap), snap, {type: VIEW_EVENTS.PAPER_SELECT, id: P(15)});
  state = run(state, snap, {type: VIEW_EVENTS.CLOUD_SELECT, ...a});
  assert.equal(state.scope.kind, 'cloud'); assert.equal(state.scope.cloudId, C('A')); assert.equal(state.scope.paperIds.size, 10);
  assert.equal(state.selected, null, 'a paper outside the cloud is deselected (label click selects no paper)');
  assert.equal(state.camera.scopeKey, 'library'); assert.equal(state.camera.reason, 'preserve');
  const aliases = legacyAliases(state); assert.equal(aliases.selectedCloudId, C('A')); assert.equal(aliases.fieldId, null); assert.equal(aliases.conceptId, null);
  // point click order: CLOUD_SELECT then OVERVIEW_PAPER_SELECT keeps the cloud scope
  state = run(state, snap, {type: VIEW_EVENTS.OVERVIEW_PAPER_SELECT, id: P(3)});
  assert.deepEqual(state.selected, {kind: 'paper', id: P(3)}); assert.equal(state.scope.kind, 'cloud');
  // panel paper click inside the cloud keeps it; switching cloud keeps a shared paper
  assert.equal(run(state, snap, {type: VIEW_EVENTS.PAPER_SELECT, id: P(1)}).scope.cloudId, C('A'));
  const shared = run(run(state, snap, {type: VIEW_EVENTS.PAPER_SELECT, id: P(8)}), snap, {type: VIEW_EVENTS.CLOUD_SELECT, ...cloudSelection(snap, C('B'))});
  assert.deepEqual(shared.selected, {kind: 'paper', id: P(8)}); assert.equal(shared.scope.cloudId, C('B'));
  // a paper outside the cloud (lower network / list) leaves the cloud scope
  const outside = run(state, snap, {type: VIEW_EVENTS.PAPER_SELECT, id: P(15)}); assert.equal(outside.scope.kind, 'library'); assert.equal(legacyAliases(outside).selectedCloudId, null);
  // invalid input is a no-op
  assert.equal(run(state, snap, {type: VIEW_EVENTS.CLOUD_SELECT, cloudId: C('X'), paperIds: ['nope']}), state);
  const snapshotView = viewStateSnapshot(state); assert.ok(Array.isArray(snapshotView.scope.paperIds) && snapshotView.scope.subPaperIds === null);
});

test('CLOUD_SELECT null clears the cloud (selection kept); clearSelection (close button) returns to the unselected map', () => {
  const snap = snapshot(); const state = run(createViewState(snap), snap, {type: VIEW_EVENTS.CLOUD_SELECT, ...cloudSelection(snap, C('A'))}, {type: VIEW_EVENTS.OVERVIEW_PAPER_SELECT, id: P(2)});
  const miss = run(state, snap, {type: VIEW_EVENTS.CLOUD_SELECT, cloudId: null});
  assert.equal(miss.scope.kind, 'library'); assert.deepEqual(miss.selected, {kind: 'paper', id: P(2)});
  const closed = run(state, snap, {type: VIEW_EVENTS.CLOUD_SELECT, cloudId: null, clearSelection: true});
  assert.equal(closed.scope.kind, 'library'); assert.equal(closed.selected, null); assert.equal(closed.detail, null);
  const idle = createViewState(snap); assert.equal(run(idle, snap, {type: VIEW_EVENTS.CLOUD_SELECT, cloudId: null}), idle, 'nothing to clear is a no-op');
});

test('CLOUD_SUBITEM_SELECT: dim filter inside the cloud scope, toggles off, ignores papers outside the cloud, no-op without a cloud', () => {
  const snap = snapshot(); const model = cloudPanelModel(snap, C('A')); const item = model.subItems.items[0];
  let state = run(createViewState(snap), snap, {type: VIEW_EVENTS.CLOUD_SELECT, ...cloudSelection(snap, C('A'))}, {type: VIEW_EVENTS.OVERVIEW_PAPER_SELECT, id: P(7)});
  assert.equal(run(createViewState(snap), snap, {type: VIEW_EVENTS.CLOUD_SUBITEM_SELECT, subItemId: item.id, paperIds: item.paperIds}).scope.kind, 'library');
  state = run(state, snap, {type: VIEW_EVENTS.CLOUD_SUBITEM_SELECT, subItemId: item.id, paperIds: [...item.paperIds, P(15)]});
  assert.equal(state.scope.subItemId, item.id); assert.deepEqual([...state.scope.subPaperIds].sort(), [...item.paperIds].sort());
  assert.deepEqual(state.selected, {kind: 'paper', id: P(7)}, 'dimming never changes the selection'); assert.equal(state.scope.paperIds.size, 10);
  const off = run(state, snap, {type: VIEW_EVENTS.CLOUD_SUBITEM_SELECT, subItemId: item.id, paperIds: item.paperIds}); assert.equal(off.scope.subItemId, null); assert.equal(off.scope.subPaperIds, null);
  assert.equal(run(state, snap, {type: VIEW_EVENTS.CLOUD_SUBITEM_SELECT, subItemId: 'subgroup:x', paperIds: [P(15)]}), state, 'only papers of the cloud can form a sub-item');
  // a cloud scope survives a same-source snapshot replace, restricted to papers that still exist
  const next = snapshot(); next.papers = next.papers.filter(row => row.id !== P(0));
  const replaced = reduceViewState(state, {type: VIEW_EVENTS.SNAPSHOT_REPLACE}, next); assert.equal(replaced.scope.kind, 'cloud'); assert.equal(replaced.scope.paperIds.size, 9); assert.ok(!replaced.scope.paperIds.has(P(0)));
});

test('trace accepts the cloud events and the cloud scope kind', () => {
  const trace = createTrace({capacity: 4});
  for (const event of ['CLOUD_SELECT', 'CLOUD_SUBITEM_SELECT']) assert.equal(trace.emit('reducer', {event, revision: 1, selectedKind: 'none', scopeKind: 'cloud', conceptConstraint: false, cameraReason: 'preserve', expandedCount: 0, changed: true}), true);
});
