/**
 * Single source of truth for graph selection, focus, scope, representative
 * expansion, detail target and revision.  Renderers consume this state and
 * dispatch the events below; hover and keyboard focus never dispatch.
 *
 * Legacy flat fields used by older renderers (selectedPaperId, fieldId, ...)
 * are derived by `legacyAliases(next)` from the *next* reducer state only, so a
 * transition can never leave a stale ID behind.
 */
export const VIEW_EVENTS = Object.freeze({
  FIELD_SELECT: 'FIELD_SELECT',
  PAPER_SELECT: 'PAPER_SELECT',
  OVERVIEW_PAPER_SELECT: 'OVERVIEW_PAPER_SELECT',
  CONCEPT_SELECT: 'CONCEPT_SELECT',
  EDGE_SELECT: 'EDGE_SELECT',
  REPRESENTATIVE_TOGGLE: 'REPRESENTATIVE_TOGGLE',
  RESET: 'RESET',
  SNAPSHOT_REPLACE: 'SNAPSHOT_REPLACE',
  // Ninth, additive event: a circular-ribbon click filters by an (unordered)
  // field pair, which is not a snapshot edge.  It follows the same contract.
  FIELD_PAIR_SELECT: 'FIELD_PAIR_SELECT'
});

export const CAMERA_REASONS = Object.freeze({PRESERVE: 'preserve', FIT_NEW_SCOPE: 'fit-new-scope', GLOBAL: 'global', FIT: 'fit', RESET: 'reset'});

const LIBRARY = Object.freeze({kind: 'library', fieldId: null, conceptId: null, fieldPair: null, paperIds: null});
const indexCache = new WeakMap();

/** Recipe identity: top-level `recipe` if the backend sets it, else the authoritative `modelStatus.recipeVersion`. */
export const recipeOf = snapshot => snapshot?.recipe ?? snapshot?.modelStatus?.recipeVersion ?? '';
export const sourceKey = snapshot => JSON.stringify([snapshot?.source?.serverId || snapshot?.source?.id || '', snapshot?.source?.epoch || '', recipeOf(snapshot)]);

function indexOf(snapshot) {
  let cached = indexCache.get(snapshot || {});
  if (cached) return cached;
  const papers = new Map((snapshot?.papers || []).map(row => [row.id, row]));
  const fields = new Map((snapshot?.fields || []).map(row => [row.id, row]));
  const concepts = new Map((snapshot?.concepts || []).map(row => [row.id, row]));
  const edges = new Map((snapshot?.edges || []).map(row => [row.id, row]));
  const children = new Map(); for (const field of fields.values()) for (const parent of field.parentIds || []) { const rows = children.get(parent) || []; rows.push(field.id); children.set(parent, rows); }
  cached = {papers, fields, concepts, edges, children, descendants: new Map(), conceptPapers: null};
  if (snapshot && typeof snapshot === 'object') indexCache.set(snapshot, cached);
  return cached;
}

export function fieldDescendants(snapshot, fieldId) {
  const index = indexOf(snapshot); if (index.descendants.has(fieldId)) return index.descendants.get(fieldId);
  const result = new Set([fieldId]); const queue = [fieldId];
  while (queue.length) for (const child of index.children.get(queue.shift()) || []) if (!result.has(child)) { result.add(child); queue.push(child); }
  index.descendants.set(fieldId, result); return result;
}

const conceptPapers = (snapshot, conceptId) => {
  const index = indexOf(snapshot);
  if (!index.conceptPapers) { index.conceptPapers = new Map(); for (const paper of index.papers.values()) for (const id of paper.conceptIds || []) { const set = index.conceptPapers.get(id) || new Set(); set.add(paper.id); index.conceptPapers.set(id, set); } }
  return index.conceptPapers.get(conceptId) || new Set();
};

export function scopeKey(scope) {
  if (!scope || scope.kind !== 'field') return scope?.fieldPair ? `pair:${[...scope.fieldPair].sort().join('|')}` : 'library';
  return `field:${scope.fieldId}${scope.conceptId ? `|concept:${scope.conceptId}` : ''}`;
}

export function buildScope(snapshot, {fieldId = null, conceptId = null, fieldPair = null} = {}) {
  const index = indexOf(snapshot);
  const cleanConcept = conceptId && index.concepts.has(conceptId) ? conceptId : null;
  if (fieldId && index.fields.has(fieldId)) {
    const ids = fieldDescendants(snapshot, fieldId); const paperIds = new Set();
    for (const paper of index.papers.values()) if ((paper.fieldIds || []).some(id => ids.has(id)) && (!cleanConcept || (paper.conceptIds || []).includes(cleanConcept))) paperIds.add(paper.id);
    return {kind: 'field', fieldId, conceptId: cleanConcept, fieldPair: null, paperIds};
  }
  if (Array.isArray(fieldPair) && fieldPair.length === 2 && fieldPair.every(id => index.fields.has(id))) return {...LIBRARY, conceptId: cleanConcept, fieldPair: [...fieldPair]};
  return {...LIBRARY, conceptId: cleanConcept};
}

function paperInScope(scope, snapshot, paperId) {
  const paper = indexOf(snapshot).papers.get(paperId); if (!paper) return false;
  if (scope.kind === 'field') return Boolean(scope.paperIds?.has(paperId));
  if (scope.conceptId && !(paper.conceptIds || []).includes(scope.conceptId)) return false;
  if (scope.fieldPair && !(paper.fieldIds || []).some(id => scope.fieldPair.includes(id))) return false;
  return true;
}

export function createViewState(snapshot = null) {
  return {source: sourceKey(snapshot), revision: 0, renderRevision: 0, selected: null, focused: null, detail: null, scope: {...LIBRARY}, expanded: new Set(), camera: {scopeKey: 'library', reason: CAMERA_REASONS.FIT_NEW_SCOPE, epoch: 0}, cameraRevision: 0, cameraEpoch: 0, lastEvent: null};
}

function commit(state, patch, type, cameraReason) {
  const revision = state.revision + 1; const scope = patch.scope || state.scope; const cameraRevision = cameraReason && cameraReason !== CAMERA_REASONS.PRESERVE ? state.cameraRevision + 1 : state.cameraRevision;
  const cameraEpoch = patch.cameraEpoch ?? state.cameraEpoch;
  return {...state, ...patch, scope, revision, renderRevision: revision, cameraRevision, cameraEpoch, camera: {scopeKey: scopeKey(scope), reason: cameraReason || CAMERA_REASONS.PRESERVE, epoch: cameraEpoch}, lastEvent: type};
}

const sel = (kind, id) => ({kind, id});

function resolveRedirect(snapshot, id) {
  const redirects = snapshot?.taxonomyRedirects; if (!redirects || !id) return id;
  let current = id; const seen = new Set();
  while (redirects[current] && !seen.has(current)) { seen.add(current); current = redirects[current]; }
  return current;
}
/** Concept merges are expressed ONLY by conceptQualityLedger.entries[].mergedInto (never taxonomyRedirects). Follow an unambiguous chain to a canonical concept that really exists; malformed/cyclic/foreign targets clear. */
export function ledgerMergeTarget(snapshot, conceptId) {
  const entries = snapshot?.conceptQualityLedger?.entries; if (!Array.isArray(entries)) return null;
  const known = indexOf(snapshot).concepts; let current = conceptId; const trail = new Set();
  for (let hop = 0; hop < 8; hop += 1) {
    if (trail.has(current)) return null; trail.add(current);
    const rows = entries.filter(entry => (entry?.conceptId ?? entry?.id) === current && entry.mergedInto); if (rows.length !== 1) return known.has(current) && current !== conceptId ? current : null; // ambiguous or end of chain
    const target = rows[0].mergedInto; if (typeof target !== 'string' || target === current) return null;
    if (known.has(target) && !entries.some(entry => (entry?.conceptId ?? entry?.id) === target && entry.mergedInto)) return target;
    current = target;
  }
  return null;
}
function existsOf(snapshot, kind, id) {
  const index = indexOf(snapshot); const table = kind === 'field' ? index.fields : kind === 'paper' ? index.papers : kind === 'concept' ? index.concepts : kind === 'edge' ? index.edges : null;
  return Boolean(table?.has(id));
}
function reconcileSelection(snapshot, selection) {
  if (!selection?.kind || !selection.id) return null;
  if (existsOf(snapshot, selection.kind, selection.id)) return sel(selection.kind, selection.id);
  if (selection.kind === 'concept') { const merged = ledgerMergeTarget(snapshot, selection.id); return merged ? sel('concept', merged) : null; }
  const target = resolveRedirect(snapshot, selection.id);
  return target !== selection.id && existsOf(snapshot, selection.kind, target) ? sel(selection.kind, target) : null;
}

/** Endpoints of an edge that must stay inside the lower-network budget. */
export function edgeEndpoints(snapshot, edgeId) {
  const index = indexOf(snapshot); const edge = index.edges.get(edgeId); if (!edge) return {paperIds: [], conceptIds: []};
  const paperIds = []; const conceptIds = [];
  for (const id of [edge.source, edge.target]) { if (index.papers.has(id)) paperIds.push(id); else if (index.concepts.has(id)) conceptIds.push(id); }
  return {paperIds, conceptIds};
}

export function reduceViewState(state, event, snapshot) {
  const current = state || createViewState(snapshot);
  const type = event?.type;
  if (!Object.values(VIEW_EVENTS).includes(type)) return current; // hover/focus/unknown events never change selection
  const index = indexOf(snapshot);

  if (type === VIEW_EVENTS.SNAPSHOT_REPLACE) {
    const key = sourceKey(snapshot);
    if (current.source !== key) { const next = createViewState(snapshot); next.revision = current.revision + 1; next.renderRevision = next.revision; next.cameraRevision = current.cameraRevision + 1; next.cameraEpoch = current.cameraEpoch + 1; next.camera = {scopeKey: 'library', reason: CAMERA_REASONS.RESET, epoch: next.cameraEpoch}; next.lastEvent = type; return next; }
    const selected = reconcileSelection(snapshot, current.selected); const focused = reconcileSelection(snapshot, current.focused); const detail = reconcileSelection(snapshot, current.detail);
    const expanded = new Set(); for (const id of current.expanded) { const target = existsOf(snapshot, 'field', id) ? id : resolveRedirect(snapshot, id); if (existsOf(snapshot, 'field', target)) expanded.add(target); }
    const redirectedField = current.scope.fieldId ? (existsOf(snapshot, 'field', current.scope.fieldId) ? current.scope.fieldId : resolveRedirect(snapshot, current.scope.fieldId)) : null;
    const conceptConstraint = current.scope.conceptId ? (existsOf(snapshot, 'concept', current.scope.conceptId) ? current.scope.conceptId : ledgerMergeTarget(snapshot, current.scope.conceptId)) : null;
    const scope = redirectedField && existsOf(snapshot, 'field', redirectedField) ? buildScope(snapshot, {fieldId: redirectedField, conceptId: conceptConstraint}) : buildScope(snapshot, {conceptId: conceptConstraint, fieldPair: current.scope.fieldPair});
    return commit(current, {selected, focused, detail, expanded, scope}, type, scopeKey(scope) === scopeKey(current.scope) ? CAMERA_REASONS.PRESERVE : CAMERA_REASONS.FIT_NEW_SCOPE);
  }

  if (type === VIEW_EVENTS.RESET) return commit(current, {selected: null, focused: null, detail: null, scope: {...LIBRARY}, expanded: new Set()}, type, CAMERA_REASONS.FIT);

  const id = event?.id ?? event?.value?.id ?? null;

  if (type === VIEW_EVENTS.REPRESENTATIVE_TOGGLE) {
    if (!index.fields.has(id)) return current;
    const expanded = new Set(current.expanded); if (expanded.has(id)) expanded.delete(id); else expanded.add(id);
    const scope = buildScope(snapshot, {fieldId: id}); const selected = sel('field', id);
    return commit(current, {selected, focused: selected, detail: selected, scope, expanded}, type, scopeKey(scope) === scopeKey(current.scope) ? CAMERA_REASONS.PRESERVE : CAMERA_REASONS.FIT_NEW_SCOPE);
  }

  if (type === VIEW_EVENTS.FIELD_SELECT) {
    if (!index.fields.has(id)) return current;
    const scope = buildScope(snapshot, {fieldId: id}); const selected = sel('field', id);
    return commit(current, {selected, focused: selected, detail: selected, scope}, type, scopeKey(scope) === scopeKey(current.scope) ? CAMERA_REASONS.PRESERVE : CAMERA_REASONS.FIT_NEW_SCOPE);
  }

  if (type === VIEW_EVENTS.FIELD_PAIR_SELECT) {
    const pair = Array.isArray(event.pair) ? event.pair : null; const scope = buildScope(snapshot, {fieldPair: pair}); if (scope.fieldPair === null) return current;
    return commit(current, {selected: null, focused: null, detail: null, scope}, type, CAMERA_REASONS.FIT_NEW_SCOPE);
  }

  if (type === VIEW_EVENTS.PAPER_SELECT || type === VIEW_EVENTS.OVERVIEW_PAPER_SELECT) {
    if (!index.papers.has(id)) return current;
    const selected = sel('paper', id); let scope = current.scope; let camera = CAMERA_REASONS.PRESERVE;
    if (!paperInScope(current.scope, snapshot, id)) {
      // Only an EXPLICIT owner (the representative whose child was clicked) may move the scope.
      const owner = type === VIEW_EVENTS.PAPER_SELECT ? event.ownerId : null;
      if (owner && index.fields.has(owner) && paperInScope(buildScope(snapshot, {fieldId: owner}), snapshot, id)) { scope = buildScope(snapshot, {fieldId: owner}); camera = scopeKey(scope) === scopeKey(current.scope) ? CAMERA_REASONS.PRESERVE : CAMERA_REASONS.FIT_NEW_SCOPE; }
      else { scope = {...LIBRARY}; camera = CAMERA_REASONS.GLOBAL; }
    } else if (type === VIEW_EVENTS.OVERVIEW_PAPER_SELECT) camera = CAMERA_REASONS.GLOBAL;
    return commit(current, {selected, focused: selected, detail: selected, scope}, type, camera);
  }

  if (type === VIEW_EVENTS.CONCEPT_SELECT) {
    if (!index.concepts.has(id)) return current;
    const linked = conceptPapers(snapshot, id); const selected = sel('concept', id);
    const keepField = current.scope.kind === 'field' && [...linked].some(paperId => current.scope.paperIds?.has(paperId));
    const scope = keepField ? buildScope(snapshot, {fieldId: current.scope.fieldId, conceptId: id}) : buildScope(snapshot, {conceptId: id});
    return commit(current, {selected, focused: selected, detail: selected, scope}, type, scopeKey(scope) === scopeKey(current.scope) ? CAMERA_REASONS.PRESERVE : CAMERA_REASONS.FIT_NEW_SCOPE);
  }

  if (type === VIEW_EVENTS.EDGE_SELECT) {
    if (!index.edges.has(id)) return current;
    const selected = sel('edge', id); const {paperIds, conceptIds} = edgeEndpoints(snapshot, id);
    const compatible = current.scope.kind === 'field' && paperIds.every(paperId => current.scope.paperIds?.has(paperId)) && conceptIds.every(conceptId => [...conceptPapers(snapshot, conceptId)].some(paperId => current.scope.paperIds?.has(paperId)));
    const scope = compatible ? current.scope : {...LIBRARY};
    return commit(current, {selected, focused: selected, detail: {...selected, forceVisible: {paperIds, conceptIds}}, scope}, type, compatible ? CAMERA_REASONS.PRESERVE : CAMERA_REASONS.GLOBAL);
  }
  return current;
}

/** Legacy flat aliases, derived ONLY from the next reducer state. */
export function legacyAliases(next) {
  const selected = next?.selected || null; const scope = next?.scope || LIBRARY;
  return {
    fieldId: scope.kind === 'field' ? scope.fieldId : null,
    // selectedFieldId is ONLY the canonical selection; fieldId is the scope/filter and may differ.
    selectedFieldId: selected?.kind === 'field' ? selected.id : null,
    selectedPaperId: selected?.kind === 'paper' ? selected.id : null,
    selectedConceptId: selected?.kind === 'concept' ? selected.id : null,
    selectedEdgeId: selected?.kind === 'edge' ? selected.id : null,
    conceptId: scope.conceptId || null,
    fieldPair: scope.fieldPair ? [...scope.fieldPair] : null,
    expandedFields: new Set(next?.expanded || [])
  };
}

export function viewStateSnapshot(state) {
  if (!state) return null;
  return {...state, expanded: [...state.expanded], scope: {...state.scope, paperIds: state.scope?.paperIds ? [...state.scope.paperIds] : null, fieldPair: state.scope?.fieldPair ? [...state.scope.fieldPair] : null}};
}

export const AtlasViewState = {VIEW_EVENTS, CAMERA_REASONS, createViewState, reduceViewState, legacyAliases, viewStateSnapshot, scopeKey, buildScope, fieldDescendants, edgeEndpoints, sourceKey};
if (typeof window !== 'undefined') window.AtlasViewState = AtlasViewState;
