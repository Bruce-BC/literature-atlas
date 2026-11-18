import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {decodeSnapshot} from '../../frontend/decoder.js';
import {computePaperDensity} from '../../frontend/paper-density.js';

const fixture = decodeSnapshot(JSON.parse(await readFile(new URL('../fixtures/snapshot.json', import.meta.url))));

function graph({papers, edges}) {
  return {
    ...structuredClone(fixture),
    papers: papers.map(id => ({id, title: id, fieldIds: [], conceptIds: []})),
    fields: [],
    memberships: [],
    edges: edges.map((edge, index) => ({id: `edge-${index}`, ...edge})),
  };
}

test('semantic density is undirected, deduplicated, and includes isolates', () => {
  const snapshot = graph({
    papers: ['a', 'b', 'c', 'd'],
    edges: [
      {source: 'a', target: 'b', type: 'semantic', directed: false, score: .8},
      {source: 'b', target: 'a', type: 'semantic', directed: false, score: .7},
      {source: 'a', target: 'a', type: 'semantic', directed: false, score: 1},
      {source: 'a', target: 'missing', type: 'semantic', directed: false, score: 1},
      {source: 'a', target: 'b', type: 'citation', directed: true, score: 1},
    ],
  });
  const result = computePaperDensity(snapshot);
  assert.equal(result.relationType, 'semantic');
  assert.equal(result.directed, false);
  assert.equal(result.counts.papers, 4);
  assert.equal(result.counts.edges, 1);
  assert.equal(result.counts.possibleEdges, 6);
  assert.equal(result.density, 1 / 6);
  assert.equal(result.rows.find(row => row.id === 'a').degree, 1);
  assert.equal(result.rows.find(row => row.id === 'c').degree, 0);
  assert.equal(result.rows.find(row => row.id === 'c').normalizedDegree, 0);
  assert.equal(result.coverage.missingEdgesNotInferred, true);
});

test('citation density keeps reciprocal directed edges separate and reports in/out degree', () => {
  const snapshot = graph({
    papers: ['a', 'b', 'c'],
    edges: [
      {source: 'a', target: 'b', type: 'citation', directed: true, score: .5},
      {source: 'a', target: 'b', type: 'citation', directed: true, score: .4},
      {source: 'b', target: 'a', type: 'citation', directed: true, score: .6},
      {source: 'a', target: 'a', type: 'citation', directed: true, score: 1},
    ],
  });
  const result = computePaperDensity(snapshot, {relationType: 'citation'});
  assert.equal(result.counts.edges, 2);
  assert.equal(result.counts.possibleEdges, 6);
  assert.equal(result.density, 1 / 3);
  assert.deepEqual(result.rows.find(row => row.id === 'a'), {
    id: 'a', title: 'a', category: result.rows.find(row => row.id === 'a').category,
    degree: 1, normalizedDegree: .5, inDegree: 1, outDegree: 1,
    inNormalizedDegree: .5, outNormalizedDegree: .5, strength: 2,
    weightedStrength: 1.1, categoryId: 'unknown', categoryLabel: 'Unknown field',
    categoryPath: ['Unknown field'], categoryColor: '#64748b', categoryInferred: false,
  });
});

test('citation strength counts both incident directions and invalid scores use unit weight', () => {
  const snapshot = graph({
    papers: ['a', 'b'],
    edges: [
      {source: 'a', target: 'b', type: 'citation', directed: true, score: null},
      {source: 'b', target: 'a', type: 'citation', directed: true, score: 'not-a-score'},
    ],
  });
  const result = computePaperDensity(snapshot, {relationType: 'citation'});
  for (const row of result.rows) {
    assert.equal(row.degree, 1);
    assert.equal(row.strength, 2);
    assert.equal(row.weightedStrength, 2);
  }
});

test('eligible IDs bound papers and edges while preserving source identity', () => {
  const snapshot = structuredClone(fixture);
  snapshot.papers.push({id: 'z', title: 'z', fieldIds: [], conceptIds: []});
  snapshot.edges.push({id: 'semantic-z', source: fixture.papers[0].id, target: 'z', type: 'semantic', directed: false});
  const result = computePaperDensity(snapshot, {eligiblePaperIds: new Set([fixture.papers[0].id])});
  assert.equal(result.counts.papers, 1);
  assert.equal(result.counts.edges, 0);
  assert.equal(result.source.serverId, fixture.source.serverId);
  assert.equal(result.source.epoch, fixture.source.epoch);
});

test('category matrix includes all zero cells, isolates, and stable row order', () => {
  const snapshot = structuredClone(fixture);
  snapshot.papers = [
    {...snapshot.papers[0], id: 'a', fieldIds: [snapshot.fields[0].id]},
    {...snapshot.papers[1], id: 'b', fieldIds: [snapshot.fields[1].id]},
    {...snapshot.papers[1], id: 'c', fieldIds: [snapshot.fields[1].id]},
  ];
  snapshot.edges = [{id: 'ab', source: 'a', target: 'b', type: 'semantic', directed: false}];
  const result = computePaperDensity(snapshot);
  assert.deepEqual(result.categoryMatrix.rowOrder, snapshot.fields.map(field => field.id).sort());
  assert.equal(result.categoryMatrix.cells.length, 4);
  const analysisTopology = result.categoryMatrix.cells.find(cell => cell.sourceCategoryId === snapshot.fields[0].id && cell.targetCategoryId === snapshot.fields[1].id);
  const topologyAnalysis = result.categoryMatrix.cells.find(cell => cell.sourceCategoryId === snapshot.fields[1].id && cell.targetCategoryId === snapshot.fields[0].id);
  assert.equal(analysisTopology.numerator, 1);
  assert.equal(topologyAnalysis.numerator, 1);
  assert.equal(analysisTopology.possible, 2);
  const topologyWithin = result.categoryMatrix.cells.find(cell => cell.sourceCategoryId === snapshot.fields[1].id && cell.targetCategoryId === snapshot.fields[1].id);
  assert.equal(topologyWithin.numerator, 0);
  assert.equal(topologyWithin.possible, 1);
});

test('snapshot is not mutated and unsupported relation layers fail closed', () => {
  const snapshot = structuredClone(fixture);
  const before = JSON.stringify(snapshot);
  assert.throws(() => computePaperDensity(snapshot, {relationType: 'keyword'}), /unsupported paper relation/);
  computePaperDensity(snapshot);
  assert.equal(JSON.stringify(snapshot), before);
});

