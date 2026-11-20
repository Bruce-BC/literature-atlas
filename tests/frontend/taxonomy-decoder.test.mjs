import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {decodeSnapshot} from '../../frontend/decoder.js';

const fixture = JSON.parse(await readFile(new URL('../fixtures/snapshot.json', import.meta.url)));
const source = fixture.source;
const ghostConcept = `concept:${source.id}:${source.epoch}:burden`;   // suppressed: resolvable via ledger, not a graph node

function extension() {
  const subject = 'subject:bioinformatics';
  const tool = 'tool:qiime2';
  return {
    facetCatalog: {version: 'facet-catalog-v3', namespace: `atlas:${source.id}:${source.epoch}`, entities: [
      {id: `facet:${source.id}:${source.epoch}:subject:bioinformatics`, facetId: subject, ontologyKey: subject, kind: 'subject', label: {en: 'Bioinformatics'}, broaderIds: [], aliases: [], origin: 'builtin'},
      {id: `facet:${source.id}:${source.epoch}:tool:qiime2`, facetId: tool, ontologyKey: tool, kind: 'tool', label: {en: 'QIIME 2'}, broaderIds: [], aliases: [], origin: 'builtin'},
    ]},
    paperFacets: [{paperId: fixture.papers[0].id, directMemberships: [{facetId: `facet:${source.id}:${source.epoch}:subject:bioinformatics`, ontologyKey: subject, entityId: `facet:${source.id}:${source.epoch}:subject:bioinformatics`, kind: 'subject', scoreKind: 'evidence', score: .9, origin: 'rule', evidenceRef: {sourceId: source.id, sourceEpoch: source.epoch}}], primaryByFacet: {subject: `facet:${source.id}:${source.epoch}:subject:bioinformatics`}}],
    conceptQualityLedger: {version: 'concept-quality-v3', entries: [{id: ghostConcept, status: 'suppressed', reason: 'context-free-boilerplate'}], raw: [ghostConcept], suppressed: [ghostConcept], counts: {raw: 1, active: 0, suppressed: 1, protected: 0, user: 0}},
    fieldStats: {version: 'field-stats-v3', paperCount: 2, rows: fixture.fields.map(field => ({fieldId: field.id, direct: 1, inclusive: 1, primary: 1, directPaperIds: [fixture.papers[0].id], inclusivePaperIds: [fixture.papers[0].id]})), unknown: 0, knownPrimary: 2, invariants: {knownPrimaryPlusUnknownEqualsPaperCount: true}},
    taxonomyState: {status: 'valid-custom', sourceId: source.id, sourceEpoch: source.epoch},
    taxonomyRedirects: {'field:fixture-server:epoch-001:old': fixture.fields[0].id},
  };
}

test('legacy snapshots still decode and extension metadata is preserved deeply', () => {
  const decoded = decodeSnapshot(fixture);
  assert.equal(decoded.schemaVersion, 1);
  assert.equal(decoded.source.epoch, source.epoch);
  assert.equal(decoded.fixtureUnknownField, undefined);
  const value = {...fixture, ...extension(), recipe: 'atlas-graph-v3', generation: 4};
  const decodedV2 = decodeSnapshot(value);
  assert.equal(decodedV2.facetCatalog.entities.length, 2);
  assert.equal(decodedV2.taxonomyState.sourceEpoch, source.epoch);
  assert.equal(decodedV2.recipe, 'atlas-graph-v3');
  value.facetCatalog.entities[0].label.en = 'mutated';
  assert.equal(decodedV2.facetCatalog.entities[0].label.en, 'Bioinformatics');
});

test('decoder rejects cross-kind parents, cycles, duplicate references, and bad colors', () => {
  const base = {...fixture, ...extension()};
  const crossKind = structuredClone(base);
  crossKind.facetCatalog.entities[1].broaderIds = [crossKind.facetCatalog.entities[0].facetId];
  assert.throws(() => decodeSnapshot(crossKind), /crosses facet kinds/);
  const cycle = structuredClone(base);
  cycle.facetCatalog.entities[0].broaderIds = [cycle.facetCatalog.entities[0].facetId];
  assert.throws(() => decodeSnapshot(cycle), /cannot parent itself|cycle/);
  const duplicate = structuredClone(base);
  duplicate.paperFacets[0].directMemberships.push({...duplicate.paperFacets[0].directMemberships[0]});
  assert.throws(() => decodeSnapshot(duplicate), /duplicates facet/);
  const color = structuredClone(base);
  color.categoryColorRegistry = {version: 1, namespace: 'atlas', entries: [{categoryKey: fixture.fields[0].id, ontologyKey: fixture.fields[0].id, kind: 'field', family: 'blue', siblingIndex: 0, depthVariant: 0, origin: 'builtin', oklchByPaletteTheme: {light: 'oklch(0.5 0.1 20)'}, hexByPaletteTheme: {light: '#nothex'}}]};
  assert.throws(() => decodeSnapshot(color), /6-digit hex/);
});

test('suppressed concepts resolve through the ledger without being active graph nodes', () => {
  const decoded = decodeSnapshot({...fixture, ...extension()});
  assert.equal(decoded.conceptQualityLedger.entries[0].id, ghostConcept);
  assert.deepEqual(decoded.conceptQualityLedger.suppressed, [ghostConcept]);
  assert.ok(!decoded.concepts.some(concept => concept.id === ghostConcept));
  const fake = structuredClone({...fixture, ...extension()});
  fake.concepts.push({id: ghostConcept, label: {en: 'burden'}, aliases: [], source: 'extracted'});
  assert.throws(() => decodeSnapshot(fake), /suppressed concept .* active graph node/);
});

test('ledger ownership buckets overlap active; only active-vs-suppressed is exclusive', () => {
  const real = fixture.concepts[0].id; const excluded = `concept:${source.id}:${source.epoch}:excluded-by-user`;
  const value = {...fixture, ...extension()};
  value.conceptQualityLedger = {version: 'concept-quality-v3',
    entries: [{id: real, status: 'protected', reason: 'user-owned', protectedBy: 'user'}, {id: excluded, status: 'suppressed', reason: 'user-excluded', protectedBy: 'user'}],
    raw: [real, excluded], active: [real], protected: [real], user: [real, excluded], suppressed: [excluded],
    counts: {raw: 2, active: 1, suppressed: 1, protected: 1, user: 2}};
  const decoded = decodeSnapshot(value);
  assert.deepEqual(decoded.conceptQualityLedger.user, [real, excluded]);
  const conflict = structuredClone(value); conflict.conceptQualityLedger.suppressed.push(real);
  assert.throws(() => decodeSnapshot(conflict), /conflicting statuses|active graph node/);
  const missing = structuredClone(value); missing.conceptQualityLedger.active.push(`concept:${source.id}:${source.epoch}:nope`);
  assert.throws(() => decodeSnapshot(missing), /unknown active concept|raw bucket/);
});

test('primaryByFacet must be an accepted direct membership of the same kind', () => {
  const value = {...fixture, ...extension()};
  const [subject, tool] = value.facetCatalog.entities.map(entity => entity.id);
  value.paperFacets[0].primaryByFacet = {subject, tool};            // tool primary without a direct tool membership
  assert.throws(() => decodeSnapshot(value), /not an accepted direct membership/);
  value.paperFacets[0].directMemberships.push({facetId: tool, kind: 'tool', origin: 'rule', score: 0.9});
  assert.doesNotThrow(() => decodeSnapshot(value));
  value.paperFacets[0].primaryByFacet = {subject: null};
  assert.doesNotThrow(() => decodeSnapshot(value));                 // abstention is explicit null
});

test('color registry accepts semantic ontology keys and every palette:theme key; rejects unknown theme keys', () => {
  const value = {...fixture, ...extension()};
  const themed = (hex) => Object.fromEntries(['atlas', 'calm', 'mocha'].flatMap(palette => ['light', 'dark'].map(theme => [`${palette}:${theme}`, hex])).concat([['system', hex]]));
  const entry = (categoryKey, kind = 'subject') => ({categoryKey, ontologyKey: categoryKey, kind, family: 'blue', siblingIndex: 0, depthVariant: 0, origin: 'generated',
    hexByPaletteTheme: themed('#336699'), oklchByPaletteTheme: Object.fromEntries(Object.keys(themed('')).map(key => [key, {oklch: 'oklch(0.5 0.1 250.00)', hex: '#336699'}]))});
  value.categoryColorRegistry = {version: 1, namespace: `atlas:${source.id}:${source.epoch}`, entries: [entry('subject:bioinformatics'), entry('tool:qiime2', 'tool')]};
  assert.doesNotThrow(() => decodeSnapshot(value));
  const bad = structuredClone(value); bad.categoryColorRegistry.entries[0].hexByPaletteTheme['neon:glow'] = '#112233';
  assert.throws(() => decodeSnapshot(bad), /invalid color theme/);
  const unknownKey = structuredClone(value); unknownKey.categoryColorRegistry.entries.push(entry('subject:unknown-nowhere'));
  assert.throws(() => decodeSnapshot(unknownKey), /not source-scoped/);
});

test('fieldStatsMeta is preserved when a classifier emits it separately', () => {
  const value = {...fixture, ...extension(), fieldStatsMeta: {version: 'field-stats-v3', unknown: 0}};
  assert.deepEqual(decodeSnapshot(value).fieldStatsMeta, {version: 'field-stats-v3', unknown: 0});
});

test('collection parentId: absent decodes as null, valid child accepted, bad references rejected', () => {
  const [parent, child] = fixture.collections;
  assert.equal(child.parentId, parent.id);
  const decoded = decodeSnapshot(fixture);
  assert.equal(decoded.collections.find(c => c.id === child.id).parentId, parent.id);
  const legacy = structuredClone(fixture);
  legacy.collections.forEach(c => { delete c.parentId; });
  assert.ok(decodeSnapshot(legacy).collections.every(c => c.parentId === null));
  const missing = structuredClone(fixture); missing.collections[1].parentId = 'collection:fixture-server:0:NOPE';
  assert.throws(() => decodeSnapshot(missing), /unknown parent/);
  const self = structuredClone(fixture); self.collections[0].parentId = self.collections[0].id;
  assert.throws(() => decodeSnapshot(self), /its own parent/);
  const cycle = structuredClone(fixture); cycle.collections[0].parentId = cycle.collections[1].id;
  assert.throws(() => decodeSnapshot(cycle), /cycle/);
});
