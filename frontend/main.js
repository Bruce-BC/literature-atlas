import {AtlasApi, buildEntityPatch, isCompatibleSnapshot} from './api.js';
import {displayLabel, indexSnapshot, safeText} from './decoder.js';
import {sampleDetail} from './sampling.js';
import {applyPalette, readPaletteContext} from './palette.js';
import {renderCircular, renderList, renderRelationList, renderG6Overview, renderG6AllPaperOverview, viewportPointCount} from './render.js';
import {renderNetworks} from './network-views.js';
import {createCameraStore} from './camera-policy.js';
import {computePaperDensity} from './paper-density.js';
import {renderDensityPanel} from './density-view.js';
import {renderCategoryExplorer} from './category-explorer.js';
import {fieldPaperCounts, clampNetworkZoom, fieldPath, endpointLabel, fieldPresentation} from './network-style.js';
import {renderFieldLegend} from './network-style.js';
import {renderSelection} from './selection-view.js';
import {VIEW_EVENTS, createViewState, reduceViewState, legacyAliases, sourceKey, recipeOf, viewStateSnapshot} from './view-state.js';
import {allPaperOverviewProjection} from './all-paper-overview.js';
import {trace} from './trace.js';
import paletteAsset from '../assets/palettes.json';

const parentOrigin = 'app://obsidian.md';
const state = {snapshot: null, viewState: null, detail: null, api: null, settings: null, configUnavailable: [], taxonomyRevision: 0, overlaysRevision: 0, status: null, token: '', launchId: null, pid: null, parentOrigin, readySent: false, connectionSeq: 0, pollInFlight: false, pollController: null, renderer: 'cytoscape', colorMode: 'category', paperDensity: null, paperStats: null, densitySnapshot: null, densityKey: null, overviewRenderer: 'ribbon', categoryPages: new Map(), categorySource: null, overviewView: null, g6PaperView: null, g6ConceptView: null, cyPaperView: null, cyConceptView: null, cameraStore: createCameraStore(), paletteContext: null, childPages: new Map(), fitVisited: new Set(), treeDisclosure: new Set(), renderedRevision: null, sourceChanged: false, g6Overview: null, g6Paper: null, g6Concept: null, theme: 'system', palette: 'atlas', paperRelation: 'citation', relationTypes: new Set(['citation']), fraction: 0.35, query: '', fieldId: null, fieldPair: null, conceptId: null, selectedPaperId: null, selectedConceptId: null, selectedFieldId: null, selectedEdgeId: null, expandedFields: new Set(), zoom: 1, panX: 0, panY: 0, cyPaper: null, cyConcept: null, circular: null, pollTimer: null, statusTimer: null};
window.__ATLAS_PALETTES__ = paletteAsset;
const byId = id => document.getElementById(id);
const root = byId('atlas-app');
const rendererControl = document.createElement('section'); rendererControl.className = 'atlas-card atlas-renderer-controls'; rendererControl.setAttribute('aria-label', 'Network renderer'); const rendererLabel = document.createElement('label'); rendererLabel.textContent = 'Network renderer '; const rendererSelect = document.createElement('select'); rendererSelect.id = 'atlas-renderer'; for (const [value, label] of [['cytoscape', '기본 Cytoscape'], ['g6', 'AntV G6 · Force']]) { const option = document.createElement('option'); option.value = value; option.textContent = label; rendererSelect.append(option); } rendererLabel.append(rendererSelect); const overviewLabel = document.createElement('label'); overviewLabel.textContent = 'Overview '; const overviewSelect = document.createElement('select'); overviewSelect.id = 'atlas-overview-renderer'; for (const [value, label] of [['ribbon', 'Circular ribbons'], ['g6', 'AntV G6 · 분야별 집계 · 전체 논문 반영'], ['all-papers', '전체 논문 지도'], ['category', '분야별 펼치기']]) { const option = document.createElement('option'); option.value = value; option.textContent = label; overviewSelect.append(option); } overviewLabel.append(overviewSelect); const rendererNote = document.createElement('span'); rendererNote.className = 'atlas-note'; rendererNote.textContent = 'Field overview uses complete source aggregates; detail views use the current sample.'; const zoomGroup = document.createElement('span'); zoomGroup.className = 'atlas-network-zoom'; for (const [id, text] of [['atlas-network-zoom-out', '−'], ['atlas-network-zoom-reset', 'Fit'], ['atlas-network-zoom-in', '+']]) { const button = document.createElement('button'); button.type = 'button'; button.id = id; button.textContent = text; button.setAttribute('aria-label', `Network ${text === 'Fit' ? 'fit' : text === '+' ? 'zoom in' : 'zoom out'}`); zoomGroup.append(button); } const fitNewButton = document.createElement('button'); fitNewButton.type = 'button'; fitNewButton.id = 'atlas-fit-new-content'; fitNewButton.textContent = 'Fit new content'; fitNewButton.hidden = true; const moreButton = document.createElement('button'); moreButton.type = 'button'; moreButton.id = 'atlas-overview-more'; moreButton.hidden = true; moreButton.textContent = 'Load more papers'; const overviewCounters = document.createElement('span'); overviewCounters.id = 'atlas-overview-counters'; overviewCounters.className = 'atlas-note'; overviewCounters.setAttribute('aria-live', 'polite'); rendererControl.append(rendererLabel, overviewLabel, rendererNote, overviewCounters, moreButton, fitNewButton, zoomGroup); root.querySelector('main')?.before(rendererControl);
const overviewContainer = document.createElement('div'); overviewContainer.className = 'atlas-network'; overviewContainer.style.height = '100%'; overviewContainer.id = 'atlas-field-network'; overviewContainer.hidden = true; document.getElementById('atlas-circular').parentElement.append(overviewContainer);
const categoryExplorer = document.createElement('div'); categoryExplorer.id = 'atlas-category-explorer'; categoryExplorer.className = 'atlas-category-explorer'; categoryExplorer.hidden = true; overviewContainer.parentElement.after(categoryExplorer);
const densityTools = document.createElement('section'); densityTools.className = 'atlas-card atlas-density-controls';
const colorLabel = document.createElement('label'); colorLabel.textContent = 'Paper color '; const colorSelect = document.createElement('select'); colorSelect.id = 'atlas-color-mode'; for (const [value,label] of [['category','Category'],['density','Connection degree']]) { const option=document.createElement('option');option.value=value;option.textContent=label;colorSelect.append(option); } colorLabel.append(colorSelect);densityTools.append(colorLabel);
const densityDetails=document.createElement('details');densityDetails.id='atlas-density-panel';const densitySummary=document.createElement('summary');densitySummary.textContent='Full eligible-paper connection density';const densityBody=document.createElement('div');densityBody.id='atlas-density-content';densityDetails.append(densitySummary,densityBody);densityTools.append(densityDetails);root.querySelector('main')?.before(densityTools);
const els = {
  token: byId('atlas-token-input'), connect: byId('atlas-connect'), connection: byId('atlas-connection'), status: byId('atlas-status'), appStatus: byId('atlas-app-status'), modelPolicy: byId('atlas-model-policy'), renderer: byId('atlas-renderer'), palette: byId('atlas-palette'), theme: byId('atlas-theme'), query: byId('atlas-search'), relation: byId('atlas-relation'), sample: byId('atlas-sample'), sampleValue: byId('atlas-sample-value'), reset: byId('atlas-reset'), zoomOut: byId('atlas-zoom-out'), zoomReset: byId('atlas-zoom-reset'), zoomIn: byId('atlas-zoom-in'), fieldList: byId('atlas-field-list'), fieldLegend: byId('atlas-field-legend'), paperList: byId('atlas-paper-list'), conceptList: byId('atlas-concept-list'), relationList: byId('atlas-relation-list'), detail: byId('atlas-detail'), focusCard: byId('atlas-focus-card'), focusSummary: byId('atlas-focus-summary'), svg: byId('atlas-circular'), paperNetwork: byId('atlas-paper-network'), conceptNetwork: byId('atlas-concept-network'), sampleNotice: byId('atlas-sampling'), overviewCount: byId('atlas-overview-count'), breadcrumb: byId('atlas-breadcrumb'), provider: byId('atlas-provider'), threshold: byId('atlas-threshold'), topK: byId('atlas-topk'), indexed: byId('atlas-indexed'), openalex: byId('atlas-openalex'), saveSettings: byId('atlas-save-settings'), taxonomyNew: byId('atlas-taxonomy-new'), taxonomyForm: byId('atlas-taxonomy-form'), overlayForm: byId('atlas-overlay-form')
};

function setStatus(message, kind = '') { if (els.status) { els.status.textContent = message; els.status.dataset.status = kind; } }
function messageParent(payload) { if (window.parent !== window && state.parentOrigin && window.location.origin !== state.parentOrigin) window.parent.postMessage(payload, state.parentOrigin); }
function currentOrigin() { return location.origin; }
async function readConfiguration(resource) { try { return await resource(); } catch { state.configUnavailable.push(true); return null; } }
function showConfigurationAvailability() { const disabled = state.configUnavailable.length > 0; for (const control of [els.provider, els.threshold, els.topK, els.indexed, els.openalex, els.saveSettings]) if (control) control.disabled = disabled; for (const form of [els.taxonomyForm, els.overlayForm]) form?.querySelectorAll('input,select,button').forEach(control => { control.disabled = disabled; }); if (disabled) els.appStatus.textContent = 'Graph loaded read-only · configuration endpoints unavailable'; }
const g6Wrappers = () => [state.overviewView?.g6, state.g6PaperView?.g6, state.g6ConceptView?.g6].filter(Boolean);
const cyGraphs = () => [state.cyPaper, state.cyConcept].filter(Boolean);
function zoomNetworks(ratio) { for (const wrapper of g6Wrappers()) wrapper.zoomBy(ratio); for (const graph of cyGraphs()) graph.zoom(clampNetworkZoom(graph.zoom() * ratio)); }
function fitNetworks() { for (const wrapper of g6Wrappers()) wrapper.fit(); for (const graph of cyGraphs()) graph.fit(undefined, 24); updateFitNewContent(true); }
function fitNewContent() { for (const view of [state.overviewView, state.g6PaperView, state.g6ConceptView]) { if (view?.fitNewContentAvailable) { view.g6.fitNewContent(); view.fitNewContentAvailable = false; } } for (const view of [state.cyPaperView, state.cyConceptView]) { if (view?.fitNewContentAvailable) { view.cy.fit(undefined, 24); view.fitNewContentAvailable = false; } } updateFitNewContent(true); }
function updateFitNewContent(cleared = false) { const available = !cleared && [state.overviewView, state.g6PaperView, state.g6ConceptView, state.cyPaperView, state.cyConceptView].some(view => view?.fitNewContentAvailable); fitNewButton.hidden = !available; }
/** Read-only facade over a graph: observation only, never exposes mutating methods. */
function debugGraph(graph) { if (!graph) return null; if (graph.getNodeData) return Object.freeze({on: (type, handler) => graph.on(type, handler), getNodeData: () => graph.getNodeData(), getEdgeData: () => graph.getEdgeData(), getZoom: () => graph.getZoom(), getElementPosition: id => graph.getElementPosition(id), getClientByCanvas: position => graph.getClientByCanvas(position)}); return Object.freeze({getNodeData: () => graph.nodes().map(node => node.data()), getEdgeData: () => graph.edges().map(edge => edge.data()), getZoom: () => graph.zoom(), getPan: () => ({...graph.pan()}), getElementPosition: id => { const position = graph.getElementById(id).position(); return [position.x, position.y, 0]; }, getClientByCanvas: position => { const rect = graph.container().getBoundingClientRect(); const pan = graph.pan(); const zoom = graph.zoom(); return [rect.left + position[0] * zoom + pan.x, rect.top + position[1] * zoom + pan.y, 0]; }}); }
async function loadSnapshotForConnection(api, connectionSeq) { let lastError; let lastSnapshot; for (let attempt = 0; attempt < 30; attempt += 1) { if (connectionSeq !== state.connectionSeq) throw new Error('connection superseded'); try { const snapshot = await api.snapshot({projection: 'full', sample: 1}); if (snapshot?.schemaVersion === 1) { lastSnapshot = snapshot; if (snapshot.papers.length > 0 || attempt >= 10) return snapshot; } } catch (error) { lastError = error; } await new Promise(resolve => setTimeout(resolve, 150)); } if (lastSnapshot) return lastSnapshot; throw lastError || new Error('snapshot unavailable during bridge startup'); }


/** Backend multi-facets (subject/method/tool/application) with primary + evidence tier; never raw evidence spans. */
function facetDetailRows(snapshot, paperId) {
  const row = (snapshot.paperFacets || []).find(item => item.paperId === paperId); if (!row) return [];
  const entities = new Map((snapshot.facetCatalog?.entities || []).flatMap(facet => [[facet.id, facet], [facet.facetId, facet]].filter(([id]) => id)));
  const primary = new Set(Object.values(row.primaryByFacet || {}).filter(Boolean)); const byKind = new Map();
  for (const membership of row.directMemberships || []) { const facet = entities.get(membership.facetId); const kind = membership.kind || facet?.kind || 'facet'; const label = typeof facet?.label === 'string' ? facet.label : facet?.label?.en || facet?.label?.ko || membership.facetId; const tier = membership.evidenceRef?.evidenceTier || membership.origin; const text = `${label}${primary.has(membership.facetId) ? ' ★ primary' : ''}${tier ? ` · ${tier}` : ''}`; (byKind.get(kind) || byKind.set(kind, []).get(kind)).push(text); }
  return ['subject', 'method', 'tool', 'application'].filter(kind => byKind.has(kind)).map(kind => [kind[0].toUpperCase() + kind.slice(1), byKind.get(kind).join('; ')]);
}

function renderDetail(entity, kind = '', target = els.detail) {
  if (!target) return; target.replaceChildren();
  if (!entity) { const empty = document.createElement('p'); empty.className = 'atlas-empty'; empty.textContent = 'Select a field, paper, concept, or relationship.'; target.append(empty); return; }
  const heading = document.createElement('h3'); heading.textContent = displayLabel(entity); target.append(heading);
  const dl = document.createElement('dl');
  const embedding = state.snapshot.modelStatus || {}; const truncation = embedding.truncated?.[entity.id]; const facetRows = kind === 'paper' ? facetDetailRows(state.snapshot, entity.id) : [];
  const rows = kind === 'paper' ? [['Type', 'Paper'], ['Authors', (entity.authors || []).join(', ') || '—'], ['Year', entity.year], ['DOI', entity.doi], ['Evidence', entity.textStatus], ['Fields', state.snapshot.memberships.filter(row => row.paperId === entity.id).map(row => `${fieldPath(state.snapshot, row.fieldId)} (${row.method}, ${row.score ?? '—'})`).join('; ') || '—'], ['Model', `${embedding.provider || 'unknown'} @ ${embedding.revision || '—'}${truncation ? ' · truncated' : ''}`], ['Source', state.snapshot.source.alias || state.snapshot.source.id]] : kind === 'concept' ? [['Type', 'Concept'], ['Aliases', (entity.aliases || []).join(', ') || '—'], ['Source', entity.source]] : kind === 'edge' ? [['Type', entity.type], ['Direction', entity.directed ? `${endpointLabel(state.snapshot, entity.source)} → ${endpointLabel(state.snapshot, entity.target)}` : `${endpointLabel(state.snapshot, entity.source)} ↔ ${endpointLabel(state.snapshot, entity.target)}`], ['Score', entity.score], ['Provenance', entity.provenance?.provider], ['Method', entity.provenance?.method], ['Evidence tier', entity.evidenceTier], ['Verified', entity.verified ? 'yes' : 'no'], ['References', entity.retrievalProvenance ? JSON.stringify(entity.retrievalProvenance) : '—']] : [['Type', 'Field'], ['Memberships', state.snapshot.memberships.filter(row => row.fieldId === entity.id).length], ['Source', entity.source], ['Aliases', (entity.aliases || []).join(', ') || '—']];
  rows.push(...facetRows);
  if (kind === 'paper' && state.paperDensity) { const degree = state.paperDensity.rows.find(row => row.id === entity.id); if (degree) rows.push(['Observed neighbors', `${degree.degree} / ${Math.max(0, state.paperDensity.counts.papers - 1)} possible`]); }
  for (const [term, value] of rows) { const dt = document.createElement('dt'); dt.textContent = term; const dd = document.createElement('dd'); dd.textContent = safeText(value); dl.append(dt, dd); } target.append(dl);
  if (kind === 'paper') {
    const actions = document.createElement('div'); actions.className = 'atlas-actions';
    const zotero = document.createElement('button'); zotero.type = 'button'; zotero.textContent = 'Zotero에서 열기'; zotero.addEventListener('click', () => openPaper(entity, 'item'));
    actions.append(zotero);
    for (const attachmentId of entity.attachmentIds || []) { const pdf = document.createElement('button'); pdf.type = 'button'; pdf.textContent = 'PDF 열기'; pdf.addEventListener('click', () => openPaper(entity, 'pdf', attachmentId)); actions.append(pdf); }
    target.append(actions);
    if (entity.abstract) { const abstract = document.createElement('p'); abstract.className = 'atlas-abstract'; abstract.textContent = entity.abstract; target.append(abstract); }
  }
}
function renderFocusSummary(entity, kind) { if (!els.focusCard || !els.focusSummary) return; if (!entity) { els.focusCard.hidden = true; return; } els.focusCard.hidden = false; els.focusSummary.textContent = `${kind}: ${displayLabel(entity)}`; }
function populateEditors(entity, kind) { if (!entity) return; const taxonomy = els.taxonomyForm; if (taxonomy && (kind === 'field' || kind === 'concept')) { taxonomy.elements.entityId.value = entity.id || ''; taxonomy.elements.entityType.value = kind; taxonomy.elements.label.value = displayLabel(entity); taxonomy.elements.aliases.value = (entity.aliases || []).join(', '); taxonomy.elements.parentId.value = (entity.parentIds || []).join(', '); } const overlay = els.overlayForm; if (overlay && (kind === 'paper' || kind === 'concept' || kind === 'field')) { overlay.elements.entityId.value = entity.id || ''; overlay.elements.entityType.value = kind; overlay.elements.alias.value = (entity.aliases || []).join(', '); overlay.elements.alias.disabled = kind === 'paper'; overlay.elements.fieldId.value = kind === 'paper' ? (entity.fieldIds || []).join(', ') : ''; overlay.elements.pinned.checked = Boolean(entity.pinned); state.overlayBaseline = {aliases: (entity.aliases || []).slice(), fieldIds: (entity.fieldIds || []).slice(), pinned: Boolean(entity.pinned)}; } }

function renderFieldTree() {
  const fields = state.snapshot.fields; const byParent = new Map(fields.map(field => [field.id, []]));
  for (const field of fields) { const parent = (field.parentIds || []).find(id => byParent.has(id)); if (parent) byParent.get(parent).push(field); }
  const selected = fields.find(row => row.id === (state.fieldId || state.selectedFieldId)); const ancestors = []; let current = selected;
  while (current?.parentIds?.[0]) { const parent = fields.find(row => row.id === current.parentIds[0]); if (!parent) break; ancestors.unshift(parent); current = parent; }
  for (const ancestor of ancestors) state.treeDisclosure.add(ancestor.id);
  els.fieldList.replaceChildren();
  const append = (field, depth) => {
    const children = byParent.get(field.id) || []; const row = document.createElement('div'); row.className = 'atlas-tree-row'; row.style.paddingInlineStart = `${depth * 12}px`;
    if (children.length) { const toggle = document.createElement('button'); toggle.type = 'button'; toggle.className = 'atlas-tree-toggle'; toggle.textContent = state.treeDisclosure.has(field.id) ? '⌄' : '›'; toggle.setAttribute('aria-label', `${state.treeDisclosure.has(field.id) ? 'Collapse' : 'Expand'} ${displayLabel(field)}`); toggle.addEventListener('click', () => { state.treeDisclosure.has(field.id) ? state.treeDisclosure.delete(field.id) : state.treeDisclosure.add(field.id); renderFieldTree(); }); row.append(toggle); } else { const spacer = document.createElement('span'); spacer.className = 'atlas-tree-spacer'; row.append(spacer); }
    const select = document.createElement('button'); select.type = 'button'; select.className = 'atlas-list-item'; select.dataset.entityId = field.id; select.setAttribute('aria-pressed', String(state.selectedFieldId === field.id)); select.dataset.scopeActive = String(state.fieldId === field.id); if (state.fieldId === field.id) select.setAttribute('aria-current', 'true'); select.textContent = displayLabel(field); select.addEventListener('click', () => handleSelect({kind: 'field', value: field})); row.append(select); els.fieldList.append(row);
    if (state.treeDisclosure.has(field.id)) for (const child of children) append(child, depth + 1);
  };
  for (const field of fields.filter(field => !(field.parentIds || []).some(parent => byParent.has(parent)))) append(field, 0);
  if (selected) els.breadcrumb.textContent = ['All fields', ...ancestors.map(displayLabel), displayLabel(selected)].join(' / '); else els.breadcrumb.textContent = 'All fields';
}

async function openPaper(paper, kind, attachmentId = null) {
  if (!paper?.id || !state.snapshot.papers.some(row => row.id === paper.id)) return;
  if (window.parent !== window && state.parentOrigin === parentOrigin) { messageParent({type: 'atlas:open', kind, paperId: paper.id, ...(attachmentId ? {attachmentId} : {}), launchId: state.launchId}); return; }
  try {
    const body = kind === 'pdf' && attachmentId ? await state.api.attachmentUrl(attachmentId) : await state.api.paper(paper.id).then(response => response.url || response.zoteroUrl || response.itemUrl);
    const key = /^[A-Za-z0-9]{1,64}$/.test(String(paper.key || '')) ? String(paper.key) : null; const derived = key ? (Number(paper.libraryID) === 0 ? `zotero://select/library/items/${encodeURIComponent(key)}` : `zotero://select/groups/${encodeURIComponent(String(paper.libraryID))}/items/${encodeURIComponent(key)}`) : null;
    const url = typeof body === 'string' && /^(https?:|zotero:)/.test(body) ? body : (kind === 'item' ? derived : null);
    if (!url) throw new Error('registered URL unavailable');
    window.open(url, '_blank', 'noopener,noreferrer');
  } catch (error) { setStatus(`Open action unavailable: ${error.message}`, 'error'); }
}

function updateDensity(detail) {
  if (!state.snapshot || (!densityDetails.open && state.colorMode !== 'density')) { state.paperDensity = null; return; }
  const key = JSON.stringify([state.paperRelation, detail.eligiblePaperIds]);
  if (!state.paperDensity || state.densitySnapshot !== state.snapshot || state.densityKey !== key) { state.paperDensity = computePaperDensity(state.snapshot, {relationType: state.paperRelation, eligiblePaperIds: detail.eligiblePaperIds}); state.densitySnapshot = state.snapshot; state.densityKey = key; }
  if (densityDetails.open) renderDensityPanel(densityBody, state.paperDensity);
}

function renderCategoryView() {
  if (!state.snapshot || !state.detail || state.overviewRenderer !== 'category') return;
  renderCategoryExplorer(categoryExplorer, state.snapshot, state.detail.eligiblePaperIds, state.categoryPages, {palette: state.paletteContext, toggle: (id, open) => { if (open) state.categoryPages.delete(id); else state.categoryPages.set(id, 0); renderCategoryView(); }, page: (id, delta) => { state.categoryPages.set(id, Math.max(0, (state.categoryPages.get(id) || 0) + delta)); renderCategoryView(); }, selectPaper: paper => handleSelect({kind: 'paper', value: paper})});
}

function ensureViewState() {
  if (!state.snapshot) return null;
  if (!state.viewState) state.viewState = createViewState(state.snapshot);
  return state.viewState;
}

/** Legacy flat fields are DERIVED from the next reducer state only. */
function applyAliases(next) {
  const aliases = legacyAliases(next);
  state.selectedFieldId = aliases.selectedFieldId; state.fieldId = aliases.fieldId; state.selectedPaperId = aliases.selectedPaperId; state.selectedConceptId = aliases.selectedConceptId; state.selectedEdgeId = aliases.selectedEdgeId; state.conceptId = aliases.conceptId; state.fieldPair = aliases.fieldPair; state.expandedFields = aliases.expandedFields;
}

function dispatchView(event) {
  ensureViewState();
  if (!state.viewState) return null;
  const previous = state.viewState; const next = reduceViewState(previous, event, state.snapshot);
  state.viewState = next; applyAliases(next);
  trace.emit('reducer', {event: event?.type, revision: next.revision, selectedKind: next.selected?.kind || 'none', scopeKind: next.scope.kind, conceptConstraint: Boolean(next.scope.conceptId), cameraReason: next.camera.reason, expandedCount: next.expanded.size, changed: next !== previous});
  return next;
}

/** Every snapshot replacement goes through the reducer so stale IDs are cleared or redirected. */
function applySnapshot(next) {
  const hadView = Boolean(state.viewState); state.snapshot = next; ensureViewState();
  const sameSource = state.viewState.source === sourceKey(next);
  state.viewState = reduceViewState(state.viewState, {type: VIEW_EVENTS.SNAPSHOT_REPLACE}, next); applyAliases(state.viewState);
  trace.emit('reducer', {event: VIEW_EVENTS.SNAPSHOT_REPLACE, revision: state.viewState.revision, selectedKind: state.viewState.selected?.kind || 'none', scopeKind: state.viewState.scope.kind, conceptConstraint: Boolean(state.viewState.scope.conceptId), cameraReason: state.viewState.camera.reason, expandedCount: state.viewState.expanded.size, changed: true});
  trace.emit('snapshot', {sameSource: hadView && sameSource, ...(recipeOf(next) ? {recipe: String(recipeOf(next))} : {}), papers: next.papers.length, concepts: next.concepts.length, fields: next.fields.length});
  const fieldIds = new Set(next.fields.map(field => field.id));
  if (!sameSource) { state.fitVisited.clear(); state.childPages.clear(); state.treeDisclosure.clear(); state.categoryPages.clear(); }
  for (const id of [...state.childPages.keys()]) if (!fieldIds.has(id)) state.childPages.delete(id);
  for (const id of [...state.treeDisclosure]) if (!fieldIds.has(id)) state.treeDisclosure.delete(id);
}

const entityFor = selection => { if (!selection || !state.snapshot) return null; const table = selection.kind === 'paper' ? state.snapshot.papers : selection.kind === 'field' ? state.snapshot.fields : selection.kind === 'concept' ? state.snapshot.concepts : selection.kind === 'edge' ? state.snapshot.edges : []; return table.find(row => row.id === selection.id) || null; };

function destroyOverview() { state.overviewView?.g6?.destroy(); state.overviewView = null; state.g6Overview = null; state.overviewMode = null; }

function renderAll() {
  if (!state.snapshot) return;
  ensureViewState(); const view = state.viewState;
  applyPalette(root, window.__ATLAS_PALETTES__ || {palettes: []}, state.palette, state.theme); state.paletteContext = readPaletteContext(root);
  const indexed = indexSnapshot(state.snapshot); const detailRelationTypes = state.relationTypes.has('citation') || state.relationTypes.has('semantic') ? [state.paperRelation] : [...state.relationTypes];
  const forcedPapers = view.detail?.forceVisible?.paperIds || [];
  const detail = sampleDetail(state.snapshot, {fraction: state.fraction, seed: 'atlas-ui', selectedIds: [state.selectedPaperId, ...forcedPapers].filter(Boolean), filter: {query: state.query, fieldId: state.fieldId, fieldPair: state.fieldPair, conceptId: state.conceptId}, relationTypes: detailRelationTypes}); state.detail = detail; updateDensity(detail);
  state.circular = renderCircular(els.svg, state.snapshot, {fieldId: state.fieldId, relationType: state.paperRelation, zoom: state.zoom, panX: state.panX, panY: state.panY, paletteContext: state.paletteContext});
  if (els.fieldLegend) renderFieldLegend(els.fieldLegend, state.snapshot, {selectedId: state.selectedFieldId, scopeId: state.fieldId, palette: state.paletteContext, onSelect: field => handleSelect({kind: 'field', value: field})});
  els.svg.toggleAttribute('hidden', state.overviewRenderer !== 'ribbon'); overviewContainer.hidden = !['g6', 'all-papers'].includes(state.overviewRenderer); overviewContainer.parentElement.hidden = state.overviewRenderer === 'category'; categoryExplorer.hidden = state.overviewRenderer !== 'category';
  const overviewCallbacks = {select: handleSelect, toggleRepresentative: id => handleSelect({kind: 'representative', value: state.snapshot.fields.find(field => field.id === id)}), error: error => setStatus(`Graph rendering failed: ${error.message || error}`, 'error')};
  if (state.overviewMode && state.overviewMode !== state.overviewRenderer) destroyOverview();
  if (state.overviewRenderer === 'category') { destroyOverview(); renderCategoryView(); }
  else if (state.overviewRenderer === 'g6') { state.overviewView = renderG6Overview(overviewContainer, state.snapshot, state, overviewCallbacks, state.overviewView); state.overviewMode = 'g6'; }
  else if (state.overviewRenderer === 'all-papers') { state.overviewView = renderG6AllPaperOverview(overviewContainer, state.snapshot, state, overviewCallbacks, state.overviewView); state.overviewMode = 'all-papers'; }
  else destroyOverview();
  state.g6Overview = state.overviewView?.g6?.graph || null;
  // First visit of a field scope fits once (on the same graph); later topology changes keep the camera and offer "Fit new content".
  if (state.overviewMode === 'g6' && view.camera.reason === 'fit-new-scope' && !state.fitVisited.has(view.camera.scopeKey)) { state.fitVisited.add(view.camera.scopeKey); const overview = state.overviewView; overview.g6.whenIdle().then(() => { if (state.overviewView === overview) { overview.g6.fitNewContent(); overview.fitNewContentAvailable = false; updateFitNewContent(); } }); }
  const networks = renderNetworks({paper: els.paperNetwork, concept: els.conceptNetwork}, state.snapshot, detail, state, {select: handleSelect, error: error => setStatus(`Graph rendering failed: ${error.message || error}`, 'error')});
  const g6 = state.renderer === 'g6'; state.g6PaperView = g6 ? networks.paper : null; state.g6ConceptView = g6 ? networks.concept : null; state.cyPaperView = g6 ? null : networks.paper; state.cyConceptView = g6 ? null : networks.concept;
  state.cyPaper = networks.cyPaper; state.cyConcept = networks.cyConcept; state.g6Paper = networks.g6Paper || null; state.g6Concept = networks.g6Concept || null; state.conceptStats = networks.graph.conceptStats; state.paperStats = networks.graph.paperStats;
  updateFitNewContent(); updateOverviewCounters();
  renderSelection(els.detail, state.snapshot, view.selected && view.selected.kind ? {kind: view.selected.kind, id: view.selected.id} : null, selection => handleSelect(selection), {palette: state.paletteContext, scopeId: state.fieldId, renderDetail: (target, entity, kind) => renderDetail(entity, kind, target)});
  const focused = entityFor(view.selected); renderFocusSummary(focused, view.selected?.kind || ''); if (focused) populateEditors(focused, view.selected.kind);
  renderFieldTree();
  renderList(els.paperList, detail.papers, paper => displayLabel(paper), paper => handleSelect({kind: 'paper', value: paper}), state.selectedPaperId);
  const conceptRows = state.snapshot.concepts.filter(concept => !state.query || `${displayLabel(concept)} ${(concept.aliases || []).join(' ')}`.toLocaleLowerCase().includes(state.query.toLocaleLowerCase())).sort((a, b) => a.id.localeCompare(b.id)); renderList(els.conceptList, conceptRows.slice(0, 300), concept => displayLabel(concept), concept => handleSelect({kind: 'concept', value: concept}), state.selectedConceptId); if (conceptRows.length > 300) { const note = document.createElement('p'); note.className = 'atlas-empty'; note.textContent = `${conceptRows.length - 300} more concepts available via search/focus`; els.conceptList.append(note); }
  renderRelationList(els.relationList, detail.edges, indexed.paperById, edge => handleSelect({kind: 'edge', value: edge}));
  els.sampleNotice.textContent = `${detail.visiblePapers} visible / ${detail.eligiblePapers} eligible papers · ${state.paperStats.displayedEdges} displayed / ${state.paperStats.candidateEdges} sampled / ${detail.eligibleEdges} eligible relationships · concepts ${state.conceptStats?.visibleNodes || 0} visible / ${state.conceptStats?.totalNodes || 0} total · ${detail.reason}${detail.mandatoryOverflow ? ` (${detail.mandatoryOverflow} mandatory neighborhood overflow)` : ''}${state.conceptStats?.neighborCandidates ? ` · ${state.conceptStats.visibleNeighbors}/${state.conceptStats.neighborCandidates} neighboring concepts shown${state.conceptStats.hiddenNeighbors ? ' · increase detail or search to focus' : ''}` : ''}${state.selectedConceptId && state.conceptStats?.directPaperCandidates ? ` · ${state.conceptStats.visibleConceptPapers}/${state.conceptStats.directPaperCandidates} connected papers shown` : ''}${state.conceptStats?.hiddenEdges || state.conceptStats?.hiddenMembershipEdges ? ` · keyword links ${state.conceptStats.visibleEdges}/${state.conceptStats.inducedEdges}, paper links ${state.conceptStats.visibleMembershipEdges}/${state.conceptStats.inducedMembershipEdges} shown` : ''}`;
  els.overviewCount.textContent = `Overview: ${state.snapshot.overview.totalPapers} papers · ${state.snapshot.overview.totalEdges} typed relationships · allocation ${state.snapshot.allocationMode}`;
  const model = state.snapshot.modelStatus || {}; const policy = model.keywordPolicy || state.snapshot.overview.keywordCooccurrence || {}; const policyText = [`Provider: ${model.provider || 'unknown'}`, `revision: ${model.revision || '—'}`, `status: ${model.status || 'unknown'}`, `keyword policy: top ${policy.topKPerPaper ?? '—'} / min shared papers ${policy.minCooccurrence ?? '—'} / candidates ${policy.candidatePairs ?? '—'} / displayed ${policy.displayedPairs ?? '—'}`, `all concepts retained: ${policy.allConceptsAndMembershipsRetained === false ? 'no' : 'yes'}`].join(' · '); if (els.modelPolicy) els.modelPolicy.textContent = policyText;
  if (model.failed || state.snapshot.status?.stale) els.appStatus.textContent = `Cached graph · ${model.failed ? `model failures: ${Object.keys(model.failures || {}).length}` : 'source is stale'}`;
  state.renderedRevision = view.revision; installDebug(); emitRenderTrace();
}

function emitRenderTrace() {
  const counts = state.overviewView?.projection?.counts; const actual = window.AtlasDebug?.getActualCounts?.() || {};
  trace.emit('render', {revision: state.viewState.revision, eligiblePapers: state.detail.eligiblePapers, visiblePapers: state.detail.visiblePapers, paperNodes: actual.paperNodes || 0, paperEdges: actual.paperEdges || 0, conceptNodes: actual.conceptNodes || 0, conceptEdges: actual.conceptEdges || 0, overviewNodes: actual.overviewNodes || 0, candidateEdges: state.paperStats?.candidateEdges || 0, eligibleEdges: state.detail.eligibleEdges});
  for (const [name, view] of [['overview', state.overviewView], ['paper', state.g6PaperView || state.cyPaperView], ['concept', state.g6ConceptView || state.cyConceptView]]) {
    const stats = view?.g6?.stats || view?.stats; if (!stats) continue;
    if (view.g6) trace.emit('g6', {view: name, created: stats.created, topologyChanges: stats.topologyChanges, styleUpdates: stats.styleUpdates, layoutRuns: stats.layoutRuns, layoutsFinished: stats.layoutsFinished, failedUpdates: stats.failedUpdates});
    trace.emit('graph', {view: name, reused: Boolean(stats.reused) || (view.g6 ? stats.styleUpdates > 0 && stats.topologyChanges === 0 : false), fitNewContent: Boolean(view.fitNewContentAvailable)});
  }
}

function updateOverviewCounters() {
  const projection = state.overviewView?.projection; moreButton.hidden = true; overviewCounters.textContent = '';
  if (state.overviewMode === 'all-papers' && projection) { const show = () => { const viewport = viewportPointCount(state.overviewView, overviewContainer); overviewCounters.textContent = `${projection.counters.active} active · ${viewport ?? '…'} in viewport · ${projection.counters.displayedEdges}/${projection.counters.eligibleEdges} edges`; if (viewport !== null) overviewContainer.dataset.viewportPoints = String(viewport); }; show(); const view = state.overviewView; view.g6.ready.then(() => { if (state.overviewView === view) show(); }); }
  if (state.overviewMode === 'g6' && projection) { const counts = projection.counts; overviewCounters.textContent = `${counts.fields} fields · ${counts.loadedPapers}/150 child papers · ${counts.displayedEdges}/${counts.edgeBudget} edges`; moreButton.hidden = ![...projection.remaining.values()].some(value => value > 0) || counts.loadedPapers >= 150; }
}

function installDebug() {
  const countOf = (cy, g6, kind) => cy ? (kind === 'nodes' ? cy.nodes().length : cy.edges().length) : g6?.graph ? (kind === 'nodes' ? g6.graph.getNodeData().length : g6.graph.getEdgeData().length) : 0;
  const allPaper = () => { if (!state.snapshot) return null; const projection = state.overviewMode === 'all-papers' && state.overviewView?.projection ? state.overviewView.projection : allPaperOverviewProjection(state.snapshot, {focusScope: state.viewState?.scope?.kind === 'field' ? state.viewState.scope : null, relation: state.paperRelation, categoryPalette: state.paletteContext || {}}); const viewport = state.overviewMode === 'all-papers' ? viewportPointCount(state.overviewView, overviewContainer) : null; return {...projection, counters: {...projection.counters, viewport: viewport ?? projection.counters.viewport}}; };
  window.AtlasDebug = Object.freeze({
    getState: () => ({snapshot: state.snapshot, detail: state.detail, circular: state.circular, relationTypes: [...state.relationTypes], conceptStats: state.conceptStats, renderer: state.renderer, overviewRenderer: state.overviewRenderer}),
    getViewState: () => viewStateSnapshot(state.viewState),
    getLowerProjection: () => state.viewState ? {revision: state.renderedRevision, viewRevision: state.viewState.revision, scopeKey: state.viewState.camera.scopeKey, selected: state.viewState.selected, paperNodes: countOf(state.cyPaper, state.g6Paper, 'nodes'), conceptNodes: countOf(state.cyConcept, state.g6Concept, 'nodes'), paperIds: (state.cyPaper ? state.cyPaper.nodes().map(node => node.id()) : state.g6Paper?.graph.getNodeData().map(node => node.id)) || []} : null,
    getPaperStats: () => state.paperStats ? {...state.paperStats} : null,
    getOverviewGraph: () => debugGraph(state.overviewView?.g6?.graph),
    getAllPaperOverview: allPaper,
    getOverviewProjection: () => state.overviewView?.projection ? {counts: state.overviewView.projection.counts || state.overviewView.projection.counters, expanded: [...(state.viewState?.expanded || [])], remaining: state.overviewView.projection.remaining ? Object.fromEntries(state.overviewView.projection.remaining) : null} : null,
    getG6Stats: () => { const of = view => view?.g6 ? {...view.g6.stats, appliedSignature: view.g6.signature, desiredSignature: view.g6.desiredSignature} : view?.stats || null; return {overview: of(state.overviewView), paper: of(state.g6PaperView || state.cyPaperView), concept: of(state.g6ConceptView || state.cyConceptView)}; },
    getCameraPlans: () => ({overview: state.overviewView?.plan || null, paper: (state.g6PaperView || state.cyPaperView)?.plan || null, concept: (state.g6ConceptView || state.cyConceptView)?.plan || null, fitNewContent: !fitNewButton.hidden}),
    getPaperGraph: () => debugGraph(state.cyPaper || state.g6Paper?.graph),
    getConceptGraph: () => debugGraph(state.cyConcept || state.g6Concept?.graph),
    getCircularSvg: () => els.svg,
    getActualCounts: () => ({paperNodes: countOf(state.cyPaper, state.g6Paper, 'nodes'), paperEdges: countOf(state.cyPaper, state.g6Paper, 'edges'), conceptNodes: countOf(state.cyConcept, state.g6Concept, 'nodes'), conceptEdges: countOf(state.cyConcept, state.g6Concept, 'edges'), overviewNodes: state.overviewView?.g6?.graph?.getNodeData().length || 0, overviewEdges: state.overviewView?.g6?.graph?.getEdgeData().length || 0, circularPaths: els.svg?.querySelectorAll('path').length || 0, sampledNodes: state.detail?.papers.length || 0, sampledEdges: state.detail?.edges.length || 0})
  });
}

function handleSelect({kind, value, ownerId = null}) {
  if (!value || !state.snapshot) return;
  if (kind === 'ribbon') { dispatchView({type: VIEW_EVENTS.FIELD_PAIR_SELECT, pair: [value.sourceFieldId, value.targetFieldId]}); renderAll(); return; }
  const type = kind === 'representative' ? VIEW_EVENTS.REPRESENTATIVE_TOGGLE : kind === 'overview-paper' ? VIEW_EVENTS.OVERVIEW_PAPER_SELECT : kind === 'paper' ? VIEW_EVENTS.PAPER_SELECT : kind === 'field' ? VIEW_EVENTS.FIELD_SELECT : kind === 'concept' ? VIEW_EVENTS.CONCEPT_SELECT : kind === 'edge' ? VIEW_EVENTS.EDGE_SELECT : null;
  if (!type) return;
  dispatchView({type, id: value.id, ownerId});
  renderAll();
}

async function connect(token = '') {
  try {
    state.connectionSeq += 1; const connectionSeq = state.connectionSeq; state.pollController?.abort(); state.pollInFlight = false;
    state.token = token || els.token.value.trim() || state.token; state.sourceChanged = false; if (!state.token) throw new Error('Enter the loopback bearer token');
    state.api = new AtlasApi({baseUrl: currentOrigin(), token: state.token}); const health = await state.api.health();
    if (state.pid && health.pid && Number(health.pid) !== Number(state.pid)) throw new Error('bridge PID changed during bootstrap');
    if (state.launchId && health.launchId && health.launchId !== state.launchId) throw new Error('bridge launch nonce changed during bootstrap');
    if (health.parentOrigin && health.parentOrigin !== state.parentOrigin) throw new Error('bridge parent origin mismatch');
    state.launchId ||= health.launchId || null; state.pid ||= health.pid || null; state.snapshot = await loadSnapshotForConnection(state.api, connectionSeq); state.status = await state.api.status(); for (let attempt = 0; attempt < 10 && state.snapshot.papers.length === 0 && Number(state.status.revision || state.snapshot.revision || 0) < 2; attempt += 1) { await new Promise(resolve => setTimeout(resolve, 100)); state.snapshot = await state.api.snapshot({projection: 'full', sample: 1}); state.status = await state.api.status(); } state.configUnavailable = []; state.settings = await readConfiguration(() => state.api.settings()); const taxonomy = await readConfiguration(() => state.api.taxonomy()); const overlays = await readConfiguration(() => state.api.overlays()); state.taxonomyRevision = Number(taxonomy?.revision || 0); state.overlaysRevision = Number(overlays?.revision || 0); showConfigurationAvailability();
    if (state.settings) { const settings = state.settings.settings || state.settings; if (settings.provider) els.provider.value = settings.provider; if (settings.threshold !== undefined) els.threshold.value = settings.threshold; if (settings.topK !== undefined) els.topK.value = settings.topK; if (settings.includeIndexedText !== undefined) els.indexed.checked = settings.includeIndexedText; if (settings.openAlex !== undefined) els.openalex.checked = settings.openAlex; }
    if (!state.snapshot.edges.some(edge => edge.type === 'citation')) { state.paperRelation = 'semantic'; state.relationTypes = new Set(['semantic']); if (els.relation) for (const option of els.relation.options) option.selected = option.value === 'semantic'; }
    state.api.revision = Number(state.snapshot.revision || -1); els.connection.hidden = true; els.token.value = ''; setStatus(`Connected · ${state.snapshot.source.alias || state.snapshot.source.id}`, state.status.stale ? 'stale' : 'ok'); applySnapshot(state.snapshot); renderAll(); if (window.parent !== window && !state.readySent) { state.readySent = true; messageParent({type: 'atlas:ready', launchId: state.launchId}); } startPolling();
  } catch (error) { setStatus(error.message, 'error'); }
}

async function poll() {
  if (!state.api || state.pollInFlight) return; state.pollInFlight = true; const sequence = state.connectionSeq; const controller = new AbortController(); state.pollController = controller; const timeout = setTimeout(() => controller.abort(), 8000);
  try { const status = await state.api.status({signal: controller.signal}); if (sequence !== state.connectionSeq) return; state.status = status; const revision = Number(status.revision ?? status.snapshotRevision ?? -1); if (revision !== -1 && revision !== state.api.revision) { const next = await state.api.snapshot({projection: 'full', sample: 1, signal: controller.signal}); if (sequence !== state.connectionSeq) return; if (!isCompatibleSnapshot(state.snapshot, next)) { /* source fence: never auto-adopt a different library/epoch; require an explicit Connect */ state.sourceChanged = true; state.api.revision = revision; els.connection.hidden = false; setStatus('Source changed · press Connect to adopt the new library (selection and camera will reset)', 'stale'); return; } applySnapshot(next); state.api.revision = revision; renderAll(); } if (!state.sourceChanged) setStatus(`${status.phase || 'idle'}${status.stale ? ' · offline cache' : ''}`, status.stale ? 'stale' : 'ok'); } catch { if (sequence === state.connectionSeq) setStatus('Offline · showing cached snapshot', 'stale'); } finally { clearTimeout(timeout); if (state.pollController === controller) state.pollController = null; state.pollInFlight = false; }
}
function startPolling() { clearInterval(state.statusTimer); state.statusTimer = setInterval(poll, 1500); poll(); }

async function saveSettings() { if (!state.api || !state.settings) return; try { state.settings = await state.api.updateSettings({provider: els.provider.value, threshold: Number(els.threshold.value), topK: Number(els.topK.value), includeIndexedText: els.indexed.checked, openAlex: els.openalex.checked}, state.settings.revision); setStatus('Settings saved', 'ok'); } catch (error) { setStatus(`Settings conflict: ${error.message}; reload required`, 'error'); } }
async function createTaxonomy(event) { event.preventDefault(); if (!state.api) return; const form = new FormData(els.taxonomyForm); const aliases = String(form.get('aliases') || '').split(',').map(value => value.trim()).filter(Boolean); const kind = form.get('entityType'); const labelValue = String(form.get('label') || ''); const label = {en: labelValue}; const parentIds = String(form.get('parentId') || '').split(',').map(value => value.trim()).filter(Boolean); const entityId = String(form.get('entityId') || ''); const source = {sourceId: state.snapshot.source.serverId, sourceEpoch: state.snapshot.source.epoch}; try { const response = entityId ? await state.api.updateTaxonomy({...source, entityId, patch: {label, aliases, parentIds}}, state.taxonomyRevision) : await state.api.createTaxonomy({...source, entityType: kind, label, aliases, parentIds}, state.taxonomyRevision); state.taxonomyRevision = Number(response.revision || state.taxonomyRevision); setStatus('Taxonomy updated; refreshing', 'ok'); applySnapshot(await state.api.snapshot({projection: 'full', sample: 1})); renderAll(); } catch (error) { setStatus(`Taxonomy update failed: ${error.message}; reload required`, 'error'); } }
async function updateOverlay(event) { event.preventDefault(); if (!state.api) return; const form = new FormData(els.overlayForm); const entityId = String(form.get('entityId')); const entityType = String(form.get('entityType')); const aliases = String(form.get('alias') || '').split(',').map(value => value.trim()).filter(Boolean); const fieldIds = String(form.get('fieldId') || '').split(',').map(value => value.trim()).filter(Boolean); const current = {aliases, fieldIds, pinned: form.get('pinned') === 'on'}; const patch = buildEntityPatch(state.overlayBaseline || {}, current); if (entityType === 'paper') delete patch.aliases; if (!Object.keys(patch).length) { setStatus('No overlay changes to save', 'ok'); return; } try { const response = await state.api.updateOverlay({sourceId: state.snapshot.source.serverId, sourceEpoch: state.snapshot.source.epoch, entityType, entityId, patch}, state.overlaysRevision); state.overlaysRevision = Number(response.revision || state.overlaysRevision); state.overlayBaseline = {...current}; setStatus('Overlay saved', 'ok'); } catch (error) { setStatus(`Overlay conflict: ${error.message}; reload required`, 'error'); } }

function wire() {
  els.connect?.addEventListener('click', () => connect()); els.query?.addEventListener('input', event => { state.query = event.target.value; renderAll(); });
  els.sample?.addEventListener('input', event => { state.fraction = Number(event.target.value) / 100; els.sampleValue.textContent = `${event.target.value}%`; renderAll(); });
  els.palette?.addEventListener('change', event => { state.palette = event.target.value; renderAll(); }); els.theme?.addEventListener('change', event => { state.theme = event.target.value; renderAll(); });
  colorSelect.addEventListener('change', event => { state.colorMode = event.target.value; renderAll(); });
  densityDetails.addEventListener('toggle', () => { if (state.detail) updateDensity(state.detail); });
  overviewSelect.addEventListener('change', event => { state.overviewRenderer = event.target.value; renderAll(); });
  els.renderer?.addEventListener('change', event => { state.renderer = event.target.value; renderAll(); });
  byId('atlas-network-zoom-in')?.addEventListener('click', () => zoomNetworks(1.2)); byId('atlas-network-zoom-out')?.addEventListener('click', () => zoomNetworks(0.8)); byId('atlas-network-zoom-reset')?.addEventListener('click', () => fitNetworks());
  fitNewButton.addEventListener('click', fitNewContent); moreButton.addEventListener('click', () => { const projection = state.overviewView?.projection; if (!projection) return; for (const [fieldId, remaining] of projection.remaining) if (remaining > 0) state.childPages.set(fieldId, (state.childPages.get(fieldId) || 1) + 1); renderAll(); });
  els.relation?.addEventListener('change', event => { state.relationTypes = new Set([...event.target.selectedOptions].map(option => option.value)); state.paperRelation = state.relationTypes.has('citation') ? 'citation' : state.relationTypes.has('semantic') ? 'semantic' : [...state.relationTypes][0] || 'semantic'; renderAll(); });
  els.reset?.addEventListener('click', () => { dispatchView({type: VIEW_EVENTS.RESET}); state.childPages.clear(); state.query = ''; if (els.query) els.query.value = ''; renderAll(); });
  els.zoomOut?.addEventListener('click', () => { state.zoom = Math.max(.6, state.zoom - .15); renderAll(); }); els.zoomIn?.addEventListener('click', () => { state.zoom = Math.min(2.5, state.zoom + .15); renderAll(); }); els.zoomReset?.addEventListener('click', () => { state.zoom = 1; state.panX = 0; state.panY = 0; renderAll(); });
  els.saveSettings?.addEventListener('click', saveSettings); els.taxonomyForm?.addEventListener('submit', createTaxonomy); els.taxonomyNew?.addEventListener('click', () => { els.taxonomyForm.reset(); els.taxonomyForm.elements.entityId.value = ''; }); els.overlayForm?.addEventListener('submit', updateOverlay);
  els.svg?.addEventListener('atlas:select', event => handleSelect(event.detail));
  const media = window.matchMedia?.('(prefers-color-scheme: dark)'); media?.addEventListener?.('change', () => { if (state.theme === 'system') renderAll(); });
}

window.addEventListener('message', event => {
  if (event.source !== window.parent || event.origin !== parentOrigin || !event.data || event.data.type !== 'atlas:bootstrap') return;
  const {token, launchId, pid, parentOrigin: configuredOrigin} = event.data;
  if (typeof token !== 'string' || typeof launchId !== 'string' || typeof pid !== 'number' || configuredOrigin !== parentOrigin) return;
  state.parentOrigin = configuredOrigin; state.launchId = launchId; state.pid = pid; connect(token);
});

wire();
if (els.palette && window.__ATLAS_PALETTES__) for (const palette of window.__ATLAS_PALETTES__.palettes || []) { const option = document.createElement('option'); option.value = palette.id; option.textContent = palette.name || palette.id; els.palette.append(option); }
