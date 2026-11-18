import test from 'node:test';
import assert from 'node:assert/strict';
import {createTrace, TRACE_SCHEMA} from '../../frontend/trace.js';
import {VIEW_EVENTS, createViewState, reduceViewState} from '../../frontend/view-state.js';

// Adversarial private-looking values are ASSEMBLED AT RUNTIME: the repository holds no private-looking path or credential literals.
const PRIVATE_PATH_PREFIX = ['', 'Users', ''].join('/');
const PRIVATE_PATH = ['', 'Users', 'someone', 'Zotero', 'storage', 'ABC', 'file.pdf'].join('/');
const VAULT_PATH = ['', 'Users', 'x', 'vault'].join('/');
const AUTH_SCHEME = ['Bear', 'er'].join('');
const PRIVATE_TOKEN = [AUTH_SCHEME, ['abcdef', 'ghijkl', 'mnop'].join('')].join(' ');

const reducerEntry = (state, event) => ({event: event.type, revision: state.revision, selectedKind: state.selected?.kind || 'none', scopeKind: state.scope.kind, conceptConstraint: Boolean(state.scope.conceptId), cameraReason: state.camera.reason, expandedCount: state.expanded.size, changed: true});
const snapshot = {schemaVersion: 1, source: {serverId: 's', epoch: 'e'}, fields: [{id: 'f:a', parentIds: []}], papers: [{id: 'p:1', fieldIds: ['f:a'], conceptIds: []}], concepts: [], edges: []};

test('positive emission: real reducer transitions become whitelisted entries in order', () => {
  const trace = createTrace({capacity: 50}); let state = createViewState(snapshot);
  for (const event of [{type: VIEW_EVENTS.FIELD_SELECT, id: 'f:a'}, {type: VIEW_EVENTS.PAPER_SELECT, id: 'p:1'}, {type: VIEW_EVENTS.REPRESENTATIVE_TOGGLE, id: 'f:a'}, {type: VIEW_EVENTS.RESET}]) { state = reduceViewState(state, event, snapshot); assert.equal(trace.emit('reducer', reducerEntry(state, event)), true); }
  const {entries, retained, rejected} = trace.read();
  assert.equal(retained, 4); assert.equal(rejected, 0);
  assert.deepEqual(entries.map(e => e.event), ['FIELD_SELECT', 'PAPER_SELECT', 'REPRESENTATIVE_TOGGLE', 'RESET']);
  assert.deepEqual(entries.map(e => e.seq), [1, 2, 3, 4]); assert.deepEqual(entries.map(e => e.revision), [1, 2, 3, 4]);
  assert.equal(entries[1].selectedKind, 'paper'); assert.equal(entries[3].cameraReason, 'fit'); assert.equal(entries[3].scopeKind, 'library');
  for (const entry of entries) assert.deepEqual(Object.keys(entry).filter(k => k !== 'seq' && k !== 'type').sort(), Object.keys(TRACE_SCHEMA.reducer).sort());
  assert.equal(trace.emit('snapshot', {sameSource: false, recipe: 'atlas-graph-v3', papers: 3, concepts: 4, fields: 5}), true);
  assert.equal(trace.emit('snapshot', {sameSource: true, papers: 3, concepts: 4, fields: 5}), true); // recipe is optional
  assert.equal(trace.emit('g6', {view: 'paper', created: 1, topologyChanges: 2, styleUpdates: 3, layoutRuns: 2, layoutsFinished: 1, failedUpdates: 0}), true);
  assert.equal(trace.emit('render', {revision: 7, eligiblePapers: 10, visiblePapers: 4, paperNodes: 4, paperEdges: 3, conceptNodes: 5, conceptEdges: 2, overviewNodes: 6, candidateEdges: 3, eligibleEdges: 9}), true);
});

test('retention is bounded: the ring drops the oldest entries and counts them', () => {
  const trace = createTrace({capacity: 10});
  for (let i = 1; i <= 1000; i += 1) trace.emit('render', {revision: i, eligiblePapers: 1, visiblePapers: 1, paperNodes: 1, paperEdges: 0, conceptNodes: 0, conceptEdges: 0, overviewNodes: 0, candidateEdges: 0, eligibleEdges: 0});
  const out = trace.read(); assert.equal(out.retained, 10); assert.equal(out.entries.length, 10); assert.equal(out.dropped, 990);
  assert.deepEqual(out.entries.map(e => e.revision), [991, 992, 993, 994, 995, 996, 997, 998, 999, 1000]); assert.equal(out.entries[0].seq, 991);
  assert.equal(createTrace({capacity: 1e9}).read().capacity, 1000, 'capacity itself is clamped'); assert.equal(createTrace({capacity: 0}).read().capacity, 1);
  const snap = trace.read(); snap.entries[0].revision = -1; snap.entries.length = 0; assert.equal(trace.read().entries[0].revision, 991, 'read() hands out copies');
  trace.clear(); assert.equal(trace.read().retained, 0);
});

test('unknown, nested, free-text and private fields are never retained; invalid entries are rejected whole', () => {
  const trace = createTrace(); const good = {event: 'PAPER_SELECT', revision: 3, selectedKind: 'paper', scopeKind: 'field', conceptConstraint: false, cameraReason: 'preserve', expandedCount: 0, changed: true};
  const poisoned = {...good, title: 'Private paper title about phage therapy', abstract: 'private abstract', span: 'secret evidence span', path: PRIVATE_PATH, token: PRIVATE_TOKEN, itemId: 'zotero:server:0:ABCD1234', label: 'my private label', nested: {title: 'x', deep: {token: 't'}}, list: ['a'], snapshot: {papers: [{title: 'whole object'}]}};
  assert.equal(trace.emit('reducer', poisoned), true);
  const text = JSON.stringify(trace.read());
  for (const secret of ['Private paper', 'private abstract', 'secret evidence', PRIVATE_PATH_PREFIX, AUTH_SCHEME, PRIVATE_TOKEN, 'zotero:server', 'my private label', 'whole object', 'nested', 'itemId', 'token']) assert.ok(!text.includes(secret), `${secret} leaked`);
  assert.deepEqual(Object.keys(trace.read().entries[0]).sort(), ['cameraReason', 'changed', 'conceptConstraint', 'event', 'expandedCount', 'revision', 'scopeKind', 'selectedKind', 'seq', 'type']);
  // free text where an enum is required (e.g. a title smuggled into `event`) rejects the entire entry
  for (const bad of [{...good, event: 'Private paper title'}, {...good, scopeKind: VAULT_PATH}, {...good, selectedKind: 'zotero:s:0:KEY'}, {...good, revision: -1}, {...good, revision: 1.5}, {...good, revision: '3'}, {...good, changed: 'yes'}, {...good, cameraReason: undefined}, (({event, ...rest}) => rest)(good)]) assert.equal(trace.emit('reducer', bad), false);
  assert.equal(trace.emit('unknown-type', good), false); assert.equal(trace.emit('reducer', null), false); assert.equal(trace.emit('reducer', [good]), false); assert.equal(trace.emit('reducer', 'PAPER_SELECT'), false);
  assert.equal(trace.emit('snapshot', {sameSource: true, recipe: `my private recipe name ${VAULT_PATH}`, papers: 1, concepts: 1, fields: 1}), false, 'an invalid optional recipe string is rejected, not logged');
  assert.equal(trace.emit('g6', {view: 'zotero:server:0:KEY', created: 1, topologyChanges: 0, styleUpdates: 0, layoutRuns: 0, layoutsFinished: 0, failedUpdates: 0}), false);
  assert.ok(trace.read().rejected >= 13); assert.equal(trace.read().retained, 1);
});

test('the module is local-only: no network, console or storage APIs in its source', async () => {
  const {readFile} = await import('node:fs/promises'); const source = await readFile(new URL('../../frontend/trace.js', import.meta.url), 'utf8');
  for (const forbidden of ['fetch(', 'XMLHttpRequest', 'sendBeacon', 'WebSocket', 'console.', 'localStorage', 'sessionStorage', 'indexedDB', 'postMessage']) assert.ok(!source.includes(forbidden), `${forbidden} must not appear in trace.js`);
});

test('recipe must be exactly atlas-graph-v<1-3 digits>: suffixes, tokens, whitespace/newlines and look-alikes are rejected whole', () => {
  const entry = recipe => ({sameSource: true, recipe, papers: 1, concepts: 1, fields: 1});
  const accepted = createTrace();
  for (const ok of ['atlas-graph-v3', 'atlas-graph-v1', 'atlas-graph-v12', 'atlas-graph-v999']) assert.equal(accepted.emit('snapshot', entry(ok)), true, ok);
  assert.deepEqual(accepted.read().entries.map(e => e.recipe), ['atlas-graph-v3', 'atlas-graph-v1', 'atlas-graph-v12', 'atlas-graph-v999']);
  const rejected = createTrace();
  const negatives = ['atlas-graph-v3-recipe-test', 'atlas-graph-v3.1', 'atlas-graph-v3+build', 'atlas-graph-v3_x', `atlas-graph-v3-${PRIVATE_TOKEN}`, `atlas-graph-v3 ${AUTH_SCHEME}`, 'atlas-graph-v3\n', 'atlas-graph-v3\r\n', '\natlas-graph-v3', ' atlas-graph-v3', 'atlas-graph-v3 ', 'atlas-graph-v3\u2028', 'atlas-graph-v1000', 'atlas-graph-v', 'atlas-graph-v-3', 'atlas-graph-v\u0663', 'ATLAS-GRAPH-V3', 'atlas-graph-vv3', 'xatlas-graph-v3', `atlas-graph-v3${VAULT_PATH}`, '', 3, null, {recipe: 'atlas-graph-v3'}, ['atlas-graph-v3']];
  for (const bad of negatives) assert.equal(rejected.emit('snapshot', entry(bad)), false, JSON.stringify(bad));
  assert.equal(rejected.read().retained, 0); assert.equal(rejected.read().rejected, negatives.length);
  assert.ok(!JSON.stringify(rejected.read()).includes(AUTH_SCHEME), 'rejected values are never stored');
  assert.equal(rejected.emit('snapshot', {sameSource: true, papers: 1, concepts: 1, fields: 1}), true, 'recipe stays optional when absent');
});
