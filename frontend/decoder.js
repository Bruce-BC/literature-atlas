const ARRAY_KEYS = ['papers', 'fields', 'concepts', 'memberships', 'edges', 'attachments', 'collections'];
const ID_RE = /^[A-Za-z0-9:_./@+%-]+$/;
const FACET_KINDS = new Set(['subject', 'method', 'tool', 'application']);
const COLOR_RE = /^#[0-9a-f]{6}$/i;
const COLOR_THEMES = new Set(['light', 'dark', 'system']);
// Registry theme keys are `light|dark|system` or `<paletteId>:<light|dark>` (e.g. `atlas:dark`).
const isThemeKey = key => COLOR_THEMES.has(key) || /^[a-z0-9][a-z0-9_-]*:(?:light|dark)$/.test(key);

const isRecord = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const hasId = value => isRecord(value) && typeof value.id === 'string' && value.id.length > 0 && value.id.length <= 512 && ID_RE.test(value.id);
const asArray = value => Array.isArray(value) ? value : [];
// Snapshots are JSON payloads, but deep-copying here prevents a caller from
// mutating the response object (or retaining prototype-bearing values) after
// validation.  No decoded value is ever interpolated into HTML by this module.
const copy = value => {
  if (Array.isArray(value)) return value.map(copy);
  if (isRecord(value)) return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, copy(item)]));
  return value;
};
const fail = message => { throw new TypeError(message); };
const assertId = (value, path) => {
  if (typeof value !== 'string' || value.length < 1 || value.length > 512 || !ID_RE.test(value)) fail(`${path} must be a valid ID`);
  return value;
};
const assertNonEmptyString = (value, path) => {
  if (typeof value !== 'string' || !value.trim()) fail(`${path} must be a non-empty string`);
  return value;
};
const assertInteger = (value, path, minimum = 0) => {
  if (!Number.isInteger(value) || value < minimum) fail(`${path} must be an integer >= ${minimum}`);
  return value;
};
const assertArray = (value, path) => { if (!Array.isArray(value)) fail(`${path} must be an array`); return value; };
const assertRecord = (value, path) => { if (!isRecord(value)) fail(`${path} must be an object`); return value; };
const scopedToSource = (id, source) => {
  if (!source || typeof id !== 'string') return false;
  const sourceParts = [source.id, source.serverId].filter(Boolean).map(String);
  return sourceParts.some(part => id === part || id.includes(`:${part}:`) || id.startsWith(`${part}:`));
};
const assertSourceRef = (value, source, path) => {
  if (!isRecord(value)) fail(`${path} must be an object`);
  if (value.sourceId !== undefined && value.sourceId !== source.id && value.sourceId !== source.serverId) fail(`${path}.sourceId does not match snapshot source`);
  if (value.sourceEpoch !== undefined && value.sourceEpoch !== source.epoch) fail(`${path}.sourceEpoch does not match snapshot source epoch`);
};
const assertUnique = (rows, selector, path) => {
  const seen = new Set();
  rows.forEach((row, index) => { const id = selector(row); if (seen.has(id)) fail(`${path}[${index}] duplicates ${id}`); seen.add(id); });
  return seen;
};
const assertHex = (value, path) => { if (typeof value !== 'string' || !COLOR_RE.test(value)) fail(`${path} must be a 6-digit hex color`); };
const assertNoCycle = (nodes, parentIds, path) => {
  const state = new Map();
  const visit = id => {
    if (state.get(id) === 1) fail(`${path} contains a cycle at ${id}`);
    if (state.get(id) === 2) return;
    state.set(id, 1);
    for (const parent of parentIds.get(id) || []) visit(parent);
    state.set(id, 2);
  };
  for (const id of nodes) visit(id);
};
const validateRedirects = (value, source, knownIds, path = 'taxonomyRedirects') => {
  if (value === undefined) return undefined;
  assertRecord(value, path);
  const redirects = {};
  for (const [from, to] of Object.entries(value)) {
    assertId(from, `${path}.${from}`); assertId(to, `${path}.${from}`);
    if (!scopedToSource(from, source) || (!knownIds.has(to) && !scopedToSource(to, source))) fail(`${path} contains a cross-source ID`);
    redirects[from] = to;
  }
  const resolve = (start, trail = new Set()) => {
    if (trail.has(start)) fail(`${path} contains a redirect cycle at ${start}`);
    const target = redirects[start]; return target && redirects[target] ? resolve(target, new Set([...trail, start])) : target;
  };
  for (const key of Object.keys(redirects)) resolve(key);
  return redirects;
};
const validateFacetCatalog = (catalog, source, knownIds) => {
  if (catalog === undefined) return undefined;
  assertRecord(catalog, 'facetCatalog'); if (!(Number.isInteger(catalog.version) && catalog.version >= 1) && !(typeof catalog.version === 'string' && /^facet-catalog-v\d+$/.test(catalog.version))) fail('facetCatalog.version is invalid');
  if (catalog.namespace !== undefined) assertNonEmptyString(catalog.namespace, 'facetCatalog.namespace');
  if (catalog.sourceId !== undefined && catalog.sourceId !== source.id && catalog.sourceId !== source.serverId) fail('facetCatalog.sourceId does not match snapshot source');
  if (catalog.sourceEpoch !== undefined && catalog.sourceEpoch !== source.epoch) fail('facetCatalog.sourceEpoch does not match snapshot source epoch');
  if (catalog.inputState !== undefined && !['absent', 'uninitialized', 'intentional-empty-valid', 'valid-custom', 'invalid', 'disabled'].includes(catalog.inputState)) fail('facetCatalog.inputState is invalid');
  const entities = assertArray(catalog.entities, 'facetCatalog.entities');
  const namespaceScoped = typeof catalog.namespace === 'string' && [source.id, source.serverId, source.epoch].every(part => catalog.namespace.includes(String(part)));
  const ids = assertUnique(entities, row => assertId(row?.id, 'facetCatalog entity id'), 'facetCatalog.entities');
  const semanticIds = new Set(); const kindById = new Map(); const kindBySemanticId = new Map(); const parents = new Map();
  entities.forEach((entity, index) => {
    assertRecord(entity, `facetCatalog.entities[${index}]`);
    const kind = entity.kind; if (!FACET_KINDS.has(kind)) fail(`facetCatalog.entities[${index}].kind is invalid`);
    if (entity.facetId !== undefined) { assertId(entity.facetId, `facetCatalog.entities[${index}].facetId`); if (semanticIds.has(entity.facetId)) fail(`facetCatalog.entities[${index}] duplicates facetId ${entity.facetId}`); semanticIds.add(entity.facetId); kindBySemanticId.set(entity.facetId, kind); }
    if (entity.ontologyKey !== undefined) assertId(entity.ontologyKey, `facetCatalog.entities[${index}].ontologyKey`);
    kindById.set(entity.id, kind); assertNonEmptyString(typeof entity.label === 'string' ? entity.label : entity.label?.en || entity.label?.ko, `facetCatalog.entities[${index}].label`);
    const broaderIds = entity.broaderIds === undefined ? [] : assertArray(entity.broaderIds, `facetCatalog.entities[${index}].broaderIds`);
    const parentSet = new Set(); for (const parent of broaderIds) { assertId(parent, `facetCatalog.entities[${index}].broaderIds`); if (parent === entity.id) fail(`facetCatalog entity ${entity.id} cannot parent itself`); parentSet.add(parent); }
    parents.set(entity.id, [...parentSet]);
    if (entity.facetId !== undefined) parents.set(entity.facetId, [...parentSet]);
    if (entity.aliases !== undefined) assertArray(entity.aliases, `facetCatalog.entities[${index}].aliases`).forEach((alias, aliasIndex) => assertNonEmptyString(alias, `facetCatalog.entities[${index}].aliases[${aliasIndex}]`));
    if (entity.legacyFieldId !== undefined && entity.legacyFieldId !== null) assertId(entity.legacyFieldId, `facetCatalog.entities[${index}].legacyFieldId`);
    if (!scopedToSource(entity.id, source) && !knownIds.has(entity.id) && !(namespaceScoped && entity.id.startsWith(`${kind}:`))) fail(`facetCatalog entity ${entity.id} is not source-scoped`);
  });
  for (const [id, parentIds] of parents) for (const parent of parentIds) { if (!ids.has(parent) && !semanticIds.has(parent)) fail(`facetCatalog entity ${id} references unknown broaderId ${parent}`); const childKind = kindById.get(id) || kindBySemanticId.get(id); const parentKind = kindById.get(parent) || kindBySemanticId.get(parent); if (parentKind !== childKind) fail(`facetCatalog hierarchy crosses facet kinds at ${id}`); }
  assertNoCycle(new Set([...ids, ...semanticIds]), parents, 'facetCatalog hierarchy');
  return copy(catalog);
};
const validatePaperFacets = (rows, source, paperIds, facetCatalog) => {
  if (rows === undefined) return undefined;
  const values = assertArray(rows, 'paperFacets'); const facetIds = new Set((facetCatalog?.entities || []).flatMap(entity => [entity.id, entity.facetId, entity.ontologyKey].filter(Boolean))); const kindById = new Map((facetCatalog?.entities || []).flatMap(entity => [[entity.id, entity.kind], [entity.facetId, entity.kind], [entity.ontologyKey, entity.kind]].filter(([id]) => id))); const canonicalOf = new Map((facetCatalog?.entities || []).flatMap(entity => [[entity.id, entity.id], [entity.facetId, entity.id], [entity.ontologyKey, entity.id]].filter(([id]) => id)));
  assertUnique(values, row => assertId(row?.paperId, 'paperFacets.paperId'), 'paperFacets');
  values.forEach((row, index) => {
    assertRecord(row, `paperFacets[${index}]`); if (!paperIds.has(row.paperId)) fail(`paperFacets[${index}] references unknown paper`);
    const direct = assertArray(row.directMemberships || [], `paperFacets[${index}].directMemberships`); const seen = new Set(); const directCanonical = new Set(direct.map(membership => canonicalOf.get(membership?.facetId)).filter(Boolean));
    direct.forEach((membership, membershipIndex) => { assertRecord(membership, `paperFacets[${index}].directMemberships[${membershipIndex}]`); assertId(membership.facetId, 'paper facetId'); if (!facetIds.has(membership.facetId)) fail(`paperFacets[${index}] references unknown facet ${membership.facetId}`); if (seen.has(membership.facetId)) fail(`paperFacets[${index}] duplicates facet ${membership.facetId}`); seen.add(membership.facetId); if (membership.ontologyKey !== undefined) { assertId(membership.ontologyKey, 'paper facet ontologyKey'); if (!facetIds.has(membership.ontologyKey)) fail(`paperFacets[${index}] references unknown ontologyKey ${membership.ontologyKey}`); if (kindById.get(membership.ontologyKey) !== kindById.get(membership.facetId)) fail(`paperFacets[${index}] ontologyKey kind mismatch`); } if (membership.kind !== undefined && kindById.get(membership.facetId) !== membership.kind) fail(`paperFacets[${index}] facet kind mismatch`); if (membership.score !== undefined && membership.score !== null && (typeof membership.score !== 'number' || !Number.isFinite(membership.score) || membership.score < 0 || membership.score > 1)) fail('paper facet score must be in [0,1]'); if (membership.evidenceRef !== undefined) assertSourceRef(membership.evidenceRef, source, `paperFacets[${index}].directMemberships[${membershipIndex}].evidenceRef`); if (membership.origin !== undefined) assertNonEmptyString(membership.origin, 'paper facet origin'); });
    if (row.primaryByFacet !== undefined) { assertRecord(row.primaryByFacet, `paperFacets[${index}].primaryByFacet`); for (const [kind, facetId] of Object.entries(row.primaryByFacet)) { if (!FACET_KINDS.has(kind)) fail(`paperFacets[${index}] has invalid primary facet kind ${kind}`); if (facetId === null) continue; assertId(facetId, `paperFacets[${index}].primaryByFacet.${kind}`); if (!facetIds.has(facetId) || kindById.get(facetId) !== kind) fail(`paperFacets[${index}].primaryByFacet.${kind} references an incompatible facet`); if (!directCanonical.has(canonicalOf.get(facetId))) fail(`paperFacets[${index}].primaryByFacet.${kind} is not an accepted direct membership`); } }
  });
  return copy(values);
};
const validateConceptQualityLedger = (ledger, source, conceptIds) => {
  if (ledger === undefined) return undefined;
  assertRecord(ledger, 'conceptQualityLedger'); if (ledger.version !== undefined && !((Number.isInteger(ledger.version) && ledger.version >= 1) || (typeof ledger.version === 'string' && /^concept-quality-v\d+$/.test(ledger.version)))) fail('conceptQualityLedger.version is invalid');
  // raw/protected/user are ownership buckets that intentionally overlap `active`
  // (and `user` may overlap `suppressed` for user-excluded concepts).  Only
  // active-like vs suppressed is mutually exclusive.
  const statusNames = ['raw', 'active', 'suppressed', 'protected', 'user'];
  const activeLike = new Set(); const suppressed = new Set(); const raw = new Set(); const byBucket = {protected: new Set(), user: new Set(), active: new Set()};
  const idOf = (entry, path) => { const conceptId = typeof entry === 'string' ? entry : entry?.conceptId ?? entry?.id; assertId(conceptId, `${path}.conceptId`); if (!scopedToSource(conceptId, source) && !conceptIds.has(conceptId)) fail(`${path} contains a cross-source concept ID`); return conceptId; };
  const checkEntry = (entry, status, path) => {
    const conceptId = idOf(entry, path);
    if (isRecord(entry) && entry.status !== undefined && entry.status !== status && !(path.includes('.entries[') )) fail(`${path}.status does not match ledger bucket`);
    if (status === 'raw') raw.add(conceptId);
    else if (status === 'suppressed') suppressed.add(conceptId);
    else if (status === 'active') { activeLike.add(conceptId); byBucket.active.add(conceptId); }
    else { byBucket[status].add(conceptId); if (status === 'protected') activeLike.add(conceptId); }
    return conceptId;
  };
  for (const status of statusNames) if (ledger[status] !== undefined) assertArray(ledger[status], `conceptQualityLedger.${status}`).forEach((entry, index) => checkEntry(entry, status, `conceptQualityLedger.${status}[${index}]`));
  if (ledger.entries !== undefined) assertArray(ledger.entries, 'conceptQualityLedger.entries').forEach((entry, index) => { assertRecord(entry, `conceptQualityLedger.entries[${index}]`); const status = entry.status; if (!statusNames.includes(status)) fail(`conceptQualityLedger.entries[${index}].status is invalid`); checkEntry(entry, status, `conceptQualityLedger.entries[${index}]`); if (entry.reason !== undefined) assertNonEmptyString(entry.reason, 'concept quality reason'); if (entry.protectedBy !== undefined) { const owners = Array.isArray(entry.protectedBy) ? entry.protectedBy : [entry.protectedBy]; owners.forEach(owner => assertNonEmptyString(owner, 'concept quality protectedBy')); } if (entry.evidenceRefs !== undefined) assertArray(entry.evidenceRefs, 'concept quality evidenceRefs').forEach((ref, refIndex) => assertSourceRef(ref, source, `conceptQualityLedger.entries[${index}].evidenceRefs[${refIndex}]`)); });
  for (const id of activeLike) if (suppressed.has(id)) fail(`conceptQualityLedger has conflicting statuses for ${id}`);
  // Active and protected concepts must be real graph nodes; suppressed ones resolve through the ledger only.
  for (const id of activeLike) if (!conceptIds.has(id)) fail(`conceptQualityLedger references unknown active concept ${id}`);
  for (const id of byBucket.user) if (!suppressed.has(id) && !conceptIds.has(id)) fail(`conceptQualityLedger references unknown active concept ${id}`);
  for (const id of suppressed) if (conceptIds.has(id)) fail(`conceptQualityLedger suppressed concept ${id} is present as an active graph node`);
  if (raw.size) for (const id of [...activeLike, ...suppressed]) if (!raw.has(id)) fail(`conceptQualityLedger raw bucket is missing ${id}`);
  if (ledger.counts !== undefined) { assertRecord(ledger.counts, 'conceptQualityLedger.counts'); for (const status of statusNames) if (ledger.counts[status] !== undefined) assertInteger(ledger.counts[status], `conceptQualityLedger.counts.${status}`); }
  return copy(ledger);
};
const validateColorRegistry = (registry, source, knownIds) => {
  if (registry === undefined) return undefined;
  assertRecord(registry, 'categoryColorRegistry'); assertInteger(registry.version, 'categoryColorRegistry.version', 1); assertNonEmptyString(registry.namespace, 'categoryColorRegistry.namespace');
  const entries = assertArray(registry.entries, 'categoryColorRegistry.entries'); const seenKeys = new Set(); const seenOntology = new Set();
  entries.forEach((entry, index) => { assertRecord(entry, `categoryColorRegistry.entries[${index}]`); assertId(entry.categoryKey, `categoryColorRegistry.entries[${index}].categoryKey`); assertId(entry.ontologyKey, `categoryColorRegistry.entries[${index}].ontologyKey`); if (seenKeys.has(entry.categoryKey) || seenOntology.has(entry.ontologyKey)) fail(`categoryColorRegistry.entries[${index}] duplicates a category key`); seenKeys.add(entry.categoryKey); seenOntology.add(entry.ontologyKey); if (!['field', ...FACET_KINDS].includes(entry.kind)) fail(`categoryColorRegistry.entries[${index}].kind is invalid`); if (!scopedToSource(entry.categoryKey, source) && !knownIds.has(entry.categoryKey)) fail(`categoryColorRegistry.entries[${index}] is not source-scoped`); assertInteger(entry.siblingIndex, `categoryColorRegistry.entries[${index}].siblingIndex`); assertInteger(entry.depthVariant, `categoryColorRegistry.entries[${index}].depthVariant`); assertRecord(entry.hexByPaletteTheme, `categoryColorRegistry.entries[${index}].hexByPaletteTheme`); assertRecord(entry.oklchByPaletteTheme, `categoryColorRegistry.entries[${index}].oklchByPaletteTheme`); for (const [theme, color] of Object.entries(entry.hexByPaletteTheme)) { if (!isThemeKey(theme)) fail(`categoryColorRegistry.entries[${index}] has invalid color theme`); assertHex(color, `categoryColorRegistry.entries[${index}].hexByPaletteTheme.${theme}`); } for (const [theme, value] of Object.entries(entry.oklchByPaletteTheme)) { if (!isThemeKey(theme)) fail(`categoryColorRegistry.entries[${index}] has invalid OKLCH theme`); if (typeof value === 'string') { if (!/^oklch\([^)]*\)$/.test(value)) fail(`categoryColorRegistry.entries[${index}].oklchByPaletteTheme.${theme} is invalid`); } else if (isRecord(value)) { if (typeof value.oklch !== 'string' || !/^oklch\([^)]*\)$/.test(value.oklch)) fail(`categoryColorRegistry.entries[${index}].oklchByPaletteTheme.${theme} is invalid`); if (value.hex !== undefined) assertHex(value.hex, `categoryColorRegistry.entries[${index}].oklchByPaletteTheme.${theme}.hex`); } else fail(`categoryColorRegistry.entries[${index}].oklchByPaletteTheme.${theme} is invalid`); } });
  return copy(registry);
};
const validateFieldStats = (stats, fieldIds) => {
  if (stats === undefined) return undefined;
  const rows = Array.isArray(stats) ? stats : isRecord(stats) ? (Array.isArray(stats.rows) ? stats.rows : Object.entries(stats).filter(([key]) => key !== 'version' && key !== 'paperCount' && key !== 'unknown' && key !== 'unknownPaperIds' && key !== 'knownPrimary' && key !== 'invariants').map(([fieldId, row]) => ({...(isRecord(row) ? row : {}), fieldId: row?.fieldId || fieldId}))) : fail('fieldStats must be an array or object map');
  assertUnique(rows, row => assertId(row?.fieldId, 'fieldStats.fieldId'), 'fieldStats');
  rows.forEach((row, index) => { if (!fieldIds.has(row.fieldId)) fail(`fieldStats[${index}] references unknown field ${row.fieldId}`); for (const key of ['direct', 'inclusive', 'primary', 'unknown']) if (row[key] !== undefined) assertInteger(row[key], `fieldStats[${index}].${key}`); for (const key of ['directPaperIds', 'inclusivePaperIds']) if (row[key] !== undefined) assertArray(row[key], `fieldStats[${index}].${key}`).forEach((id, idIndex) => assertId(id, `fieldStats[${index}].${key}[${idIndex}]`)); });
  return copy(stats);
};
const labelText = label => {
  if (typeof label === 'string') return label;
  if (!isRecord(label)) return '';
  return typeof label.en === 'string' && label.en ? label.en : typeof label.ko === 'string' ? label.ko : '';
};

/** Decode the v1 contract without trusting unknown response fields. */
export function decodeSnapshot(value) {
  if (!isRecord(value) || value.schemaVersion !== 1) throw new TypeError('snapshot schemaVersion 1 is required');
  if (!isRecord(value.source) || typeof value.source.id !== 'string' || typeof value.source.serverId !== 'string' || typeof value.source.epoch !== 'string') throw new TypeError('snapshot source identity is required');
  const source = copy(value.source);
  if (!isRecord(value.status) || typeof value.status.phase !== 'string' || typeof value.status.stale !== 'boolean') throw new TypeError('snapshot status is required');
  for (const key of ARRAY_KEYS) if (!Array.isArray(value[key])) throw new TypeError(`snapshot ${key} array is required`);
  if (!isRecord(value.overview) || !Array.isArray(value.overview.fields) || !Array.isArray(value.overview.ribbons) || !isRecord(value.overview.keywordAggregate) || !Array.isArray(value.overview.keywordAggregate.conceptIds) || !Array.isArray(value.overview.keywordAggregate.pairs)) throw new TypeError('snapshot overview aggregate is required');
  if (!['primary-display-field', 'normalized-multifield'].includes(value.allocationMode)) throw new TypeError('snapshot allocationMode is required');
  const requiredEntityKeys = {
    papers: ['id', 'key', 'libraryID', 'title', 'authors', 'keywords', 'fieldIds', 'conceptIds', 'attachmentIds', 'textStatus'],
    fields: ['id', 'label', 'parentIds', 'aliases', 'source'], concepts: ['id', 'label', 'aliases', 'source'],
    memberships: ['id', 'paperId', 'fieldId', 'method', 'weight'],
    edges: ['id', 'source', 'target', 'type', 'directed', 'provenance', 'evidenceTier'],
    attachments: ['id', 'parentId', 'name', 'status'], collections: ['id', 'label', 'paperIds']
  };
  const entities = {};
  for (const key of ARRAY_KEYS) {
    entities[key] = value[key].map((row, index) => {
      if (!isRecord(row) || !hasId(row) || requiredEntityKeys[key].some(field => !(field in row))) throw new TypeError(`snapshot ${key}[${index}] has missing required fields`);
      return copy(row);
    });
  }
  for (const key of ARRAY_KEYS) assertUnique(entities[key], row => row.id, `snapshot ${key}`);
  const ids = Object.fromEntries(ARRAY_KEYS.map(key => [key, new Set(entities[key].map(row => row.id))]));
  const paperIds = ids.papers;
  const fieldParents = new Map();
  for (const field of entities.fields) {
    const parentIds = assertArray(field.parentIds, `field ${field.id}.parentIds`); const uniqueParents = new Set();
    for (const parent of parentIds) { assertId(parent, `field ${field.id}.parentIds`); if (!ids.fields.has(parent)) fail(`field ${field.id} references unknown parent ${parent}`); uniqueParents.add(parent); }
    if (uniqueParents.size !== parentIds.length) fail(`field ${field.id} contains duplicate parents`); fieldParents.set(field.id, [...uniqueParents]);
  }
  assertNoCycle(ids.fields, fieldParents, 'field hierarchy');
  for (const membership of entities.memberships) {
    if (!paperIds.has(membership.paperId) || !ids.fields.has(membership.fieldId)) throw new TypeError(`membership ${membership.id} references an unknown entity`);
  }
  const membershipPairs = new Set(); for (const membership of entities.memberships) { const pair = `${membership.paperId}\u0000${membership.fieldId}`; if (membershipPairs.has(pair)) fail(`membership ${membership.id} duplicates a paper-field assignment`); membershipPairs.add(pair); }
  for (const edge of entities.edges) { const endpointIds = edge.type === 'keyword' ? new Set([...paperIds, ...ids.concepts]) : paperIds; if (!endpointIds.has(edge.source) || !endpointIds.has(edge.target)) throw new TypeError(`edge ${edge.id} references an unknown ${edge.type === 'keyword' ? 'concept or paper' : 'paper'}`); }
  for (const attachment of entities.attachments) if (attachment.parentId && !paperIds.has(attachment.parentId)) throw new TypeError(`attachment ${attachment.id} references an unknown paper`);
  for (const collection of entities.collections) { const seen = new Set(); for (const paperId of assertArray(collection.paperIds, `collection ${collection.id}.paperIds`)) { if (!paperIds.has(paperId)) fail(`collection ${collection.id} references unknown paper ${paperId}`); if (seen.has(paperId)) fail(`collection ${collection.id} duplicates paper ${paperId}`); seen.add(paperId); } }
  const knownIds = new Set([...ids.papers, ...ids.fields, ...ids.concepts, ...ids.memberships, ...ids.edges, ...ids.attachments, ...ids.collections]);
  const facetCatalog = validateFacetCatalog(value.facetCatalog, source, knownIds);
  const facetIds = new Set((facetCatalog?.entities || []).flatMap(entity => [entity.id, entity.facetId, entity.ontologyKey].filter(Boolean)));
  const paperFacets = validatePaperFacets(value.paperFacets, source, paperIds, facetCatalog);
  const conceptQualityLedger = validateConceptQualityLedger(value.conceptQualityLedger, source, ids.concepts);
  const categoryColorRegistry = validateColorRegistry(value.categoryColorRegistry, source, new Set([...knownIds, ...facetIds]));
  const fieldStats = validateFieldStats(value.fieldStats, ids.fields);
  const taxonomyRedirects = validateRedirects(value.taxonomyRedirects, source, new Set([...knownIds, ...facetIds]));
  let taxonomyState;
  if (value.taxonomyState !== undefined) {
    taxonomyState = assertRecord(value.taxonomyState, 'taxonomyState');
    if (!['absent', 'uninitialized', 'intentional-empty-valid', 'valid-custom', 'invalid', 'disabled'].includes(taxonomyState.status)) fail('taxonomyState.status is invalid');
    if (taxonomyState.sourceId !== undefined && taxonomyState.sourceId !== source.id && taxonomyState.sourceId !== source.serverId) fail('taxonomyState.sourceId does not match snapshot source');
    if (taxonomyState.sourceEpoch !== undefined && taxonomyState.sourceEpoch !== source.epoch) fail('taxonomyState.sourceEpoch does not match snapshot source epoch');
    taxonomyState = copy(taxonomyState);
  }
  const overview = copy(value.overview);
  return {
    schemaVersion: 1, source, status: copy(value.status),
    papers: entities.papers, fields: entities.fields, concepts: entities.concepts,
    memberships: entities.memberships, edges: entities.edges, attachments: entities.attachments,
    collections: entities.collections, overview, modelStatus: copy(value.modelStatus || {}),
    allocationMode: value.allocationMode, revision: Number.isInteger(value.revision) ? value.revision : 0,
    ...(value.recipe !== undefined ? {recipe: copy(value.recipe)} : {}),
    ...(value.generation !== undefined ? {generation: copy(value.generation)} : {}),
    ...(facetCatalog !== undefined ? {facetCatalog} : {}),
    ...(paperFacets !== undefined ? {paperFacets} : {}),
    ...(conceptQualityLedger !== undefined ? {conceptQualityLedger} : {}),
    ...(categoryColorRegistry !== undefined ? {categoryColorRegistry} : {}),
    ...(fieldStats !== undefined ? {fieldStats} : {}),
    ...(isRecord(value.fieldStatsMeta) ? {fieldStatsMeta: copy(value.fieldStatsMeta)} : {}),
    ...(taxonomyState !== undefined ? {taxonomyState} : {}),
    ...(taxonomyRedirects !== undefined ? {taxonomyRedirects} : {})
  };
}

export function displayLabel(entity, locale = 'en') {
  if (!entity) return '';
  if (typeof entity.title === 'string' && entity.title) return entity.title;
  const label = entity.label;
  if (isRecord(label) && typeof label[locale] === 'string' && label[locale]) return label[locale];
  return labelText(label) || (typeof entity.id === 'string' ? entity.id : '');
}

export function safeText(value, fallback = '—') {
  if (value === null || value === undefined || value === '') return fallback;
  return typeof value === 'string' || typeof value === 'number' ? String(value) : fallback;
}

export function indexSnapshot(snapshot) {
  const decoded = snapshot.schemaVersion === 1 && snapshot.source && snapshot.papers ? snapshot : decodeSnapshot(snapshot);
  const membershipByPaper = new Map(decoded.papers.map(paper => [paper.id, []]));
  for (const row of decoded.memberships) membershipByPaper.get(row.paperId)?.push(row);
  return {
    snapshot: decoded,
    paperById: new Map(decoded.papers.map(row => [row.id, row])), fieldById: new Map(decoded.fields.map(row => [row.id, row])),
    conceptById: new Map(decoded.concepts.map(row => [row.id, row])), attachmentById: new Map(decoded.attachments.map(row => [row.id, row])), membershipByPaper
  };
}

export const AtlasSnapshotDecoder = {decodeSnapshot, displayLabel, safeText, indexSnapshot};
if (typeof window !== 'undefined') window.AtlasSnapshotDecoder = AtlasSnapshotDecoder;
