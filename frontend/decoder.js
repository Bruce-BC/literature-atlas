const ARRAY_KEYS = ['papers', 'fields', 'concepts', 'memberships', 'edges', 'attachments', 'collections'];
const ID_RE = /^[A-Za-z0-9:_./@+%-]+$/;

const isRecord = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const hasId = value => isRecord(value) && typeof value.id === 'string' && value.id.length > 0 && value.id.length <= 512 && ID_RE.test(value.id);
const asArray = value => Array.isArray(value) ? value : [];
const copy = value => isRecord(value) ? {...value} : value;
const labelText = label => {
  if (typeof label === 'string') return label;
  if (!isRecord(label)) return '';
  return typeof label.en === 'string' && label.en ? label.en : typeof label.ko === 'string' ? label.ko : '';
};

/** Decode the v1 contract without trusting unknown response fields. */
export function decodeSnapshot(value) {
  if (!isRecord(value) || value.schemaVersion !== 1) throw new TypeError('snapshot schemaVersion 1 is required');
  if (!isRecord(value.source) || typeof value.source.id !== 'string' || typeof value.source.serverId !== 'string' || typeof value.source.epoch !== 'string') throw new TypeError('snapshot source identity is required');
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
  const ids = Object.fromEntries(ARRAY_KEYS.map(key => [key, new Set(entities[key].map(row => row.id))]));
  const paperIds = ids.papers;
  for (const membership of entities.memberships) {
    if (!paperIds.has(membership.paperId) || !ids.fields.has(membership.fieldId)) throw new TypeError(`membership ${membership.id} references an unknown entity`);
  }
  for (const edge of entities.edges) { const endpointIds = edge.type === 'keyword' ? new Set([...paperIds, ...ids.concepts]) : paperIds; if (!endpointIds.has(edge.source) || !endpointIds.has(edge.target)) throw new TypeError(`edge ${edge.id} references an unknown ${edge.type === 'keyword' ? 'concept or paper' : 'paper'}`); }
  for (const attachment of entities.attachments) if (attachment.parentId && !paperIds.has(attachment.parentId)) throw new TypeError(`attachment ${attachment.id} references an unknown paper`);
  const overview = copy(value.overview);
  return {
    schemaVersion: 1, source: copy(value.source), status: copy(value.status),
    papers: entities.papers, fields: entities.fields, concepts: entities.concepts,
    memberships: entities.memberships, edges: entities.edges, attachments: entities.attachments,
    collections: entities.collections, overview, modelStatus: copy(value.modelStatus || {}),
    allocationMode: value.allocationMode, revision: Number.isInteger(value.revision) ? value.revision : 0
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
  return {
    snapshot: decoded,
    paperById: new Map(decoded.papers.map(row => [row.id, row])), fieldById: new Map(decoded.fields.map(row => [row.id, row])),
    conceptById: new Map(decoded.concepts.map(row => [row.id, row])), attachmentById: new Map(decoded.attachments.map(row => [row.id, row])),
    membershipByPaper: new Map(decoded.papers.map(paper => [paper.id, decoded.memberships.filter(row => row.paperId === paper.id)]))
  };
}

export const AtlasSnapshotDecoder = {decodeSnapshot, displayLabel, safeText, indexSnapshot};
if (typeof window !== 'undefined') window.AtlasSnapshotDecoder = AtlasSnapshotDecoder;
