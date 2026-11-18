import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {decodeSnapshot} from '../../frontend/decoder.js';
import {sampleDetail, stableHash} from '../../frontend/sampling.js';
import {circularLayout, graphProjection, overviewProjection} from '../../frontend/projection.js';
import {buildEntityPatch, isCompatibleSnapshot} from '../../frontend/api.js';

const fixture = decodeSnapshot(JSON.parse(await readFile(new URL('../fixtures/snapshot.json', import.meta.url))));

test('poll snapshot guard accepts same-source recovery and rejects epoch retargets', () => {
  const recovered = structuredClone(fixture); recovered.revision = 9;
  assert.equal(isCompatibleSnapshot(fixture, recovered), true);
  const swapped = structuredClone(recovered); swapped.source.epoch = 'epoch-other';
  assert.equal(isCompatibleSnapshot(fixture, swapped), false);
});

test('overlay edits produce entity-scoped dirty patches', () => {
  const baseline = {aliases: ['A'], fieldIds: ['field:a'], pinned: true};
  assert.deepEqual(buildEntityPatch(baseline, {aliases: ['A'], fieldIds: ['field:a', 'field:b'], pinned: true}), {fieldIds: ['field:a', 'field:b']});
  assert.deepEqual(buildEntityPatch(baseline, {aliases: ['A'], fieldIds: ['field:a'], pinned: false}), {pinned: false});
});

test('decoder fails closed on missing required IDs and ignores unknown fields', () => {
  assert.equal(fixture.papers.length, 2);
  assert.equal(fixture.fixtureUnknownField, undefined);
  assert.equal(Array.isArray(fixture.overview.keywordAggregate?.pairs), true);
  const broken = structuredClone(fixture); delete broken.papers[0].id;
  assert.throws(() => decodeSnapshot(broken), /missing required fields/);
});

test('sampling is deterministic, nested, and retains selected one-hop neighborhood', () => {
  const small = sampleDetail(fixture, {fraction: .25, seed: 'test', selectedIds: ['zotero:fixture-server:0:A1']});
  const repeated = sampleDetail(fixture, {fraction: .25, seed: 'test', selectedIds: ['zotero:fixture-server:0:A1']});
  const full = sampleDetail(fixture, {fraction: 1, seed: 'test'});
  assert.deepEqual(small.papers.map(row => row.id), repeated.papers.map(row => row.id));
  assert.equal(small.papers.some(row => row.id === 'zotero:fixture-server:0:B2'), true);
  assert.equal(full.eligiblePapers, fixture.papers.length);
  assert.equal(full.eligibleEdges, fixture.edges.length);
  assert.equal(stableHash('same'), stableHash('same'));
});

test('selected retention does not expand transitively through a chain', () => {
  const chain = structuredClone(fixture); chain.papers.push({id: 'zotero:fixture-server:0:C3', key: 'C3', libraryID: 0, title: 'Chain', year: 2024, authors: [], keywords: [], fieldIds: ['field:fixture-server:epoch-001:topology'], conceptIds: [], attachmentIds: [], textStatus: 'missing'}); chain.edges.push({id: 'edge:fixture-server:epoch-001:B2:C3:semantic', source: 'zotero:fixture-server:0:B2', target: 'zotero:fixture-server:0:C3', type: 'semantic', directed: false, score: 1, provenance: {provider: 'test', method: 'chain'}, evidenceTier: 'metadata', verified: false});
  const sampled = sampleDetail(chain, {fraction: 0, seed: 'chain', selectedIds: ['zotero:fixture-server:0:A1'], relationTypes: ['semantic']});
  assert.deepEqual(sampled.papers.map(row => row.id).sort(), ['zotero:fixture-server:0:A1', 'zotero:fixture-server:0:B2'].sort());
});

test('overview projection keeps backend aggregate truth independent of sampled detail', () => {
  const overview = overviewProjection(fixture);
  const layout = circularLayout(overview, 640, 520);
  assert.equal(overview.totalPapers, fixture.overview.totalPapers);
  assert.equal(overview.totalEdges, fixture.overview.totalEdges);
  assert.equal(layout.arcs.length, fixture.fields.length);
  assert.equal(layout.links.length, fixture.overview.ribbons.length);
  assert.equal(layout.links[0].value, 1, 'primary display-field allocation counts one edge');
});

test('field filter includes descendants while preserving all memberships', () => {
  const nested = structuredClone(fixture); nested.fields[1].parentIds = [nested.fields[0].id]; nested.papers[1].fieldIds = [nested.fields[1].id];
  const detail = sampleDetail(nested, {fraction: 1, filter: {fieldId: nested.fields[0].id}, relationTypes: ['semantic']});
  assert.equal(detail.eligiblePapers, 2);
  assert.equal(nested.papers[0].fieldIds.length, 2);
});

test('concept keyword edges stay in concept network', () => {
  const concept = structuredClone(fixture); concept.concepts.push({id: 'concept:fixture-server:epoch-001:transport', label: {en: 'Transport'}, aliases: [], source: 'extracted'}); concept.papers[0].conceptIds.push('concept:fixture-server:epoch-001:transport'); concept.edges.push({id: 'edge:fixture-server:epoch-001:kl:transport:keyword', source: 'concept:fixture-server:epoch-001:kl', target: 'concept:fixture-server:epoch-001:transport', type: 'keyword', directed: false, score: 1, provenance: {provider: 'cooccurrence', method: 'unique-paper-count'}, evidenceTier: 'metadata', verified: true, cooccurrenceCount: 1});
  const graph = graphProjection(concept, sampleDetail(concept, {fraction: 1, relationTypes: ['semantic', 'keyword']}));
  assert.equal(graph.conceptEdges.some(edge => edge.data.relation === 'concept-cooccurrence'), true);
});

test('full keyword aggregate supplies focused concept edges without unpacking all pairs', () => {
  const aggregate = structuredClone(fixture); aggregate.edges = aggregate.edges.filter(edge => edge.type !== 'keyword'); aggregate.concepts.push({id: 'concept:fixture-server:epoch-001:transport', label: {en: 'Transport'}, aliases: [], source: 'extracted'}); aggregate.overview.keywordAggregate = {conceptIds: ['concept:fixture-server:epoch-001:kl', 'concept:fixture-server:epoch-001:transport'], pairs: [[0, 1, 7]], encoding: 'concept-index-pairs'};
  const graph = graphProjection(aggregate, sampleDetail(aggregate, {fraction: 1, relationTypes: ['semantic']}), {maxConceptNodes: 1, selectedConceptIds: ['concept:fixture-server:epoch-001:kl']});
  assert.equal(graph.conceptStats.totalEdges, 1);
  assert.equal(graph.conceptEdges.some(edge => edge.data.relation === 'concept-cooccurrence'), true);
});

test('graph projection preserves typed relation direction and actual nodes', () => {
  const detail = sampleDetail(fixture, {fraction: 1, seed: 'graph'});
  const graph = graphProjection(fixture, detail);
  assert.equal(graph.paperNodes.length, detail.papers.length);
  assert.equal(graph.paperEdges.length, detail.edges.length);
  assert.equal(graph.paperEdges.find(edge => edge.data.relation === 'semantic').data.directed, false);
  assert.equal(graph.conceptNodes.length + graph.conceptPaperNodes.length > 0, true);
});
