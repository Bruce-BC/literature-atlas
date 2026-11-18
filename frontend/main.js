import {AtlasApi, buildEntityPatch, isCompatibleSnapshot} from './api.js';
import {displayLabel, indexSnapshot, safeText} from './decoder.js';
import {sampleDetail} from './sampling.js';
import {applyPalette} from './palette.js';
import {renderCircular, renderList, renderNetworks, renderRelationList} from './render.js';
import paletteAsset from '../assets/palettes.json';

const parentOrigin = 'app://obsidian.md';
const state = {snapshot: null, detail: null, api: null, settings: null, configUnavailable: [], taxonomyRevision: 0, overlaysRevision: 0, status: null, token: '', launchId: null, pid: null, parentOrigin, readySent: false, connectionSeq: 0, pollInFlight: false, pollController: null, theme: 'system', palette: 'atlas', paperRelation: 'citation', relationTypes: new Set(['citation']), fraction: 0.35, query: '', fieldId: null, fieldPair: null, conceptId: null, selectedPaperId: null, selectedConceptId: null, selectedFieldId: null, selectedEdgeId: null, expandedFields: new Set(), zoom: 1, panX: 0, panY: 0, cyPaper: null, cyConcept: null, cyPositions: null, conceptPositions: null, cyZoom: null, cyPan: null, circular: null, pollTimer: null, statusTimer: null};
window.__ATLAS_PALETTES__ = paletteAsset;
const byId = id => document.getElementById(id);
const root = byId('atlas-app');
const els = {
  token: byId('atlas-token-input'), connect: byId('atlas-connect'), connection: byId('atlas-connection'), status: byId('atlas-status'), appStatus: byId('atlas-app-status'), modelPolicy: byId('atlas-model-policy'), palette: byId('atlas-palette'), theme: byId('atlas-theme'), query: byId('atlas-search'), relation: byId('atlas-relation'), sample: byId('atlas-sample'), sampleValue: byId('atlas-sample-value'), reset: byId('atlas-reset'), zoomOut: byId('atlas-zoom-out'), zoomReset: byId('atlas-zoom-reset'), zoomIn: byId('atlas-zoom-in'), fieldList: byId('atlas-field-list'), fieldLegend: byId('atlas-field-legend'), paperList: byId('atlas-paper-list'), conceptList: byId('atlas-concept-list'), relationList: byId('atlas-relation-list'), detail: byId('atlas-detail'), focusCard: byId('atlas-focus-card'), focusSummary: byId('atlas-focus-summary'), svg: byId('atlas-circular'), paperNetwork: byId('atlas-paper-network'), conceptNetwork: byId('atlas-concept-network'), sampleNotice: byId('atlas-sampling'), overviewCount: byId('atlas-overview-count'), breadcrumb: byId('atlas-breadcrumb'), provider: byId('atlas-provider'), threshold: byId('atlas-threshold'), topK: byId('atlas-topk'), indexed: byId('atlas-indexed'), openalex: byId('atlas-openalex'), saveSettings: byId('atlas-save-settings'), taxonomyNew: byId('atlas-taxonomy-new'), taxonomyForm: byId('atlas-taxonomy-form'), overlayForm: byId('atlas-overlay-form')
};

function setStatus(message, kind = '') { if (els.status) { els.status.textContent = message; els.status.dataset.status = kind; } }
function messageParent(payload) { if (window.parent !== window && state.parentOrigin && window.location.origin !== state.parentOrigin) window.parent.postMessage(payload, state.parentOrigin); }
function currentOrigin() { return location.origin; }
async function readConfiguration(resource) { try { return await resource(); } catch { state.configUnavailable.push(true); return null; } }
function showConfigurationAvailability() { const disabled = state.configUnavailable.length > 0; for (const control of [els.provider, els.threshold, els.topK, els.indexed, els.openalex, els.saveSettings]) if (control) control.disabled = disabled; for (const form of [els.taxonomyForm, els.overlayForm]) form?.querySelectorAll('input,select,button').forEach(control => { control.disabled = disabled; }); if (disabled) els.appStatus.textContent = 'Graph loaded read-only · configuration endpoints unavailable'; }
async function loadSnapshotForConnection(api, connectionSeq) { let lastError; let lastSnapshot; for (let attempt = 0; attempt < 30; attempt += 1) { if (connectionSeq !== state.connectionSeq) throw new Error('connection superseded'); try { const snapshot = await api.snapshot({projection: 'full', sample: 1}); if (snapshot?.schemaVersion === 1) { lastSnapshot = snapshot; if (snapshot.papers.length > 0 || attempt >= 10) return snapshot; } } catch (error) { lastError = error; } await new Promise(resolve => setTimeout(resolve, 150)); } if (lastSnapshot) return lastSnapshot; throw lastError || new Error('snapshot unavailable during bridge startup'); }

function renderDetail(entity, kind = '') {
  if (!els.detail) return; els.detail.replaceChildren();
  if (!entity) { const empty = document.createElement('p'); empty.className = 'atlas-empty'; empty.textContent = 'Select a field, paper, concept, or relationship.'; els.detail.append(empty); return; }
  const heading = document.createElement('h3'); heading.textContent = displayLabel(entity); els.detail.append(heading);
  const dl = document.createElement('dl');
  const embedding = state.snapshot.modelStatus || {}; const truncation = embedding.truncated?.[entity.id]; const rows = kind === 'paper' ? [['Type', 'Paper'], ['Authors', (entity.authors || []).join(', ') || '—'], ['Year', entity.year], ['DOI', entity.doi], ['Evidence', entity.textStatus], ['Fields', state.snapshot.memberships.filter(row => row.paperId === entity.id).map(row => `${row.fieldId} (${row.method}, ${row.score ?? '—'})`).join('; ') || '—'], ['Model', `${embedding.provider || 'unknown'} @ ${embedding.revision || '—'}${truncation ? ' · truncated' : ''}`], ['Source', state.snapshot.source.alias || state.snapshot.source.id]] : kind === 'concept' ? [['Type', 'Concept'], ['Aliases', (entity.aliases || []).join(', ') || '—'], ['Source', entity.source]] : kind === 'edge' ? [['Type', entity.type], ['Direction', entity.directed ? `${entity.source} → ${entity.target}` : `${entity.source} ↔ ${entity.target}`], ['Score', entity.score], ['Provenance', entity.provenance?.provider], ['Method', entity.provenance?.method], ['Evidence tier', entity.evidenceTier], ['Verified', entity.verified ? 'yes' : 'no'], ['References', entity.retrievalProvenance ? JSON.stringify(entity.retrievalProvenance) : '—']] : [['Type', 'Field'], ['Memberships', state.snapshot.memberships.filter(row => row.fieldId === entity.id).length], ['Source', entity.source], ['Aliases', (entity.aliases || []).join(', ') || '—']];
  for (const [term, value] of rows) { const dt = document.createElement('dt'); dt.textContent = term; const dd = document.createElement('dd'); dd.textContent = safeText(value); dl.append(dt, dd); } els.detail.append(dl);
  if (kind === 'paper') {
    const actions = document.createElement('div'); actions.className = 'atlas-actions';
    const zotero = document.createElement('button'); zotero.type = 'button'; zotero.textContent = 'Zotero에서 열기'; zotero.addEventListener('click', () => openPaper(entity, 'item'));
    actions.append(zotero);
    for (const attachmentId of entity.attachmentIds || []) { const pdf = document.createElement('button'); pdf.type = 'button'; pdf.textContent = 'PDF 열기'; pdf.addEventListener('click', () => openPaper(entity, 'pdf', attachmentId)); actions.append(pdf); }
    els.detail.append(actions);
    if (entity.abstract) { const abstract = document.createElement('p'); abstract.className = 'atlas-abstract'; abstract.textContent = entity.abstract; els.detail.append(abstract); }
  }
}
function renderFocusSummary(entity, kind) { if (!els.focusCard || !els.focusSummary) return; if (!entity) { els.focusCard.hidden = true; return; } els.focusCard.hidden = false; els.focusSummary.textContent = `${kind}: ${displayLabel(entity)}`; }
function populateEditors(entity, kind) { if (!entity) return; const taxonomy = els.taxonomyForm; if (taxonomy && (kind === 'field' || kind === 'concept')) { taxonomy.elements.entityId.value = entity.id || ''; taxonomy.elements.entityType.value = kind; taxonomy.elements.label.value = displayLabel(entity); taxonomy.elements.aliases.value = (entity.aliases || []).join(', '); taxonomy.elements.parentId.value = (entity.parentIds || []).join(', '); } const overlay = els.overlayForm; if (overlay && (kind === 'paper' || kind === 'concept' || kind === 'field')) { overlay.elements.entityId.value = entity.id || ''; overlay.elements.entityType.value = kind; overlay.elements.alias.value = (entity.aliases || []).join(', '); overlay.elements.alias.disabled = kind === 'paper'; overlay.elements.fieldId.value = kind === 'paper' ? (entity.fieldIds || []).join(', ') : ''; overlay.elements.pinned.checked = Boolean(entity.pinned); state.overlayBaseline = {aliases: (entity.aliases || []).slice(), fieldIds: (entity.fieldIds || []).slice(), pinned: Boolean(entity.pinned)}; } }

function renderFieldTree() {
  const fields = state.snapshot.fields; const byParent = new Map(fields.map(field => [field.id, []]));
  for (const field of fields) { const parent = (field.parentIds || []).find(id => byParent.has(id)); if (parent) byParent.get(parent).push(field); }
  const selected = fields.find(row => row.id === state.selectedFieldId); const ancestors = []; let current = selected;
  while (current?.parentIds?.[0]) { const parent = fields.find(row => row.id === current.parentIds[0]); if (!parent) break; ancestors.unshift(parent); current = parent; }
  for (const ancestor of ancestors) state.expandedFields.add(ancestor.id);
  els.fieldList.replaceChildren();
  const append = (field, depth) => {
    const children = byParent.get(field.id) || []; const row = document.createElement('div'); row.className = 'atlas-tree-row'; row.style.paddingInlineStart = `${depth * 12}px`;
    if (children.length) { const toggle = document.createElement('button'); toggle.type = 'button'; toggle.className = 'atlas-tree-toggle'; toggle.textContent = state.expandedFields.has(field.id) ? '⌄' : '›'; toggle.setAttribute('aria-label', `${state.expandedFields.has(field.id) ? 'Collapse' : 'Expand'} ${displayLabel(field)}`); toggle.addEventListener('click', () => { state.expandedFields.has(field.id) ? state.expandedFields.delete(field.id) : state.expandedFields.add(field.id); renderFieldTree(); }); row.append(toggle); } else { const spacer = document.createElement('span'); spacer.className = 'atlas-tree-spacer'; row.append(spacer); }
    const select = document.createElement('button'); select.type = 'button'; select.className = 'atlas-list-item'; select.dataset.entityId = field.id; select.setAttribute('aria-pressed', String(state.selectedFieldId === field.id)); select.textContent = displayLabel(field); select.addEventListener('click', () => handleSelect({kind: 'field', value: field})); row.append(select); els.fieldList.append(row);
    if (state.expandedFields.has(field.id)) for (const child of children) append(child, depth + 1);
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

function renderAll() {
  if (!state.snapshot) return;
  applyPalette(root, window.__ATLAS_PALETTES__ || {palettes: []}, state.palette, state.theme);
  const indexed = indexSnapshot(state.snapshot); const detailRelationTypes = state.relationTypes.has('citation') || state.relationTypes.has('semantic') ? [state.paperRelation] : [...state.relationTypes]; const detail = sampleDetail(state.snapshot, {fraction: state.fraction, seed: 'atlas-ui', selectedIds: state.selectedPaperId ? [state.selectedPaperId] : [], filter: {query: state.query, fieldId: state.fieldId, fieldPair: state.fieldPair, conceptId: state.conceptId}, relationTypes: detailRelationTypes}); state.detail = detail;
  state.circular = renderCircular(els.svg, state.snapshot, {fieldId: state.selectedFieldId, relationType: state.paperRelation, zoom: state.zoom, panX: state.panX, panY: state.panY});
  if (els.fieldLegend) { els.fieldLegend.replaceChildren(); state.snapshot.fields.forEach((field, index) => { const button = document.createElement('button'); button.type = 'button'; button.className = 'atlas-legend-field'; button.dataset.fieldId = field.id; const swatch = document.createElement('span'); swatch.className = `atlas-legend-swatch series-${index % 6 + 1}`; const text = document.createElement('span'); text.textContent = `${displayLabel(field)} · ${state.circular.projection.fields.find(item => item.id === field.id)?.value ?? 0}`; button.append(swatch, text); button.addEventListener('click', () => handleSelect({kind: 'field', value: field})); els.fieldLegend.append(button); }); }
  if (state.cyPaper) { state.cyPositions = Object.fromEntries(state.cyPaper.nodes().map(node => [node.id(), node.position()])); state.cyZoom = state.cyPaper.zoom(); state.cyPan = state.cyPaper.pan(); state.cyPaper.destroy(); } if (state.cyConcept) { state.conceptPositions = Object.fromEntries(state.cyConcept.nodes().map(node => [node.id(), node.position()])); state.cyConcept.destroy(); }
  const networks = renderNetworks({paper: els.paperNetwork, concept: els.conceptNetwork}, state.snapshot, detail, state, {select: handleSelect}); state.cyPaper = networks.cyPaper; state.cyConcept = networks.cyConcept; state.conceptStats = networks.graph.conceptStats;
  if (state.selectedPaperId) state.cyPaper.$id(state.selectedPaperId).select(); if (state.selectedConceptId) state.cyConcept.$id(state.selectedConceptId).select();
  if (state.cyZoom !== null) state.cyPaper.zoom(state.cyZoom); if (state.cyPan) state.cyPaper.pan(state.cyPan);
  renderFieldTree();
  renderList(els.paperList, detail.papers, paper => displayLabel(paper), paper => handleSelect({kind: 'paper', value: paper}), state.selectedPaperId);
  const conceptRows = state.snapshot.concepts.filter(concept => !state.query || `${displayLabel(concept)} ${(concept.aliases || []).join(' ')}`.toLocaleLowerCase().includes(state.query.toLocaleLowerCase())).sort((a, b) => a.id.localeCompare(b.id)); renderList(els.conceptList, conceptRows.slice(0, 300), concept => displayLabel(concept), concept => handleSelect({kind: 'concept', value: concept}), state.selectedConceptId); if (conceptRows.length > 300) { const note = document.createElement('p'); note.className = 'atlas-empty'; note.textContent = `${conceptRows.length - 300} more concepts available via search/focus`; els.conceptList.append(note); }
  renderRelationList(els.relationList, detail.edges, indexed.paperById, edge => handleSelect({kind: 'edge', value: edge}));
  els.sampleNotice.textContent = `${detail.visiblePapers} visible / ${detail.eligiblePapers} eligible papers · ${detail.visibleEdges} visible / ${detail.eligibleEdges} eligible relationships · concepts ${state.conceptStats?.visibleNodes || 0} visible / ${state.conceptStats?.totalNodes || 0} total · ${detail.reason}${detail.mandatoryOverflow ? ` (${detail.mandatoryOverflow} mandatory neighborhood overflow)` : ''}`;
  els.overviewCount.textContent = `Overview: ${state.snapshot.overview.totalPapers} papers · ${state.snapshot.overview.totalEdges} typed relationships · allocation ${state.snapshot.allocationMode}`;
  const model = state.snapshot.modelStatus || {}; const policy = model.keywordPolicy || state.snapshot.overview.keywordCooccurrence || {}; const policyText = [`Provider: ${model.provider || 'unknown'}`, `revision: ${model.revision || '—'}`, `status: ${model.status || 'unknown'}`, `keyword policy: top ${policy.topKPerPaper ?? '—'} / min shared papers ${policy.minCooccurrence ?? '—'} / candidates ${policy.candidatePairs ?? '—'} / displayed ${policy.displayedPairs ?? '—'}`, `all concepts retained: ${policy.allConceptsAndMembershipsRetained === false ? 'no' : 'yes'}`].join(' · '); if (els.modelPolicy) els.modelPolicy.textContent = policyText;
  if (model.failed || state.snapshot.status?.stale) els.appStatus.textContent = `Cached graph · ${model.failed ? `model failures: ${Object.keys(model.failures || {}).length}` : 'source is stale'}`;
  window.AtlasDebug = {getState: () => ({snapshot: state.snapshot, detail: state.detail, circular: state.circular, relationTypes: [...state.relationTypes], conceptStats: state.conceptStats}), getPaperGraph: () => state.cyPaper, getConceptGraph: () => state.cyConcept, getCircularSvg: () => els.svg, getActualCounts: () => ({paperNodes: state.cyPaper?.nodes().length || 0, paperEdges: state.cyPaper?.edges().length || 0, conceptNodes: state.cyConcept?.nodes().length || 0, conceptEdges: state.cyConcept?.edges().length || 0, circularPaths: els.svg?.querySelectorAll('path').length || 0, sampledNodes: state.detail?.papers.length || 0, sampledEdges: state.detail?.edges.length || 0})};
}

function handleSelect({kind, value}) {
  renderFocusSummary(value, kind); populateEditors(value, kind);
  if (kind === 'field') { state.selectedFieldId = value.id; state.fieldId = value.id; state.fieldPair = null; state.conceptId = null; state.selectedPaperId = null; state.selectedConceptId = null; renderDetail(value, 'field'); }
  if (kind === 'paper') { state.selectedPaperId = value.id; renderDetail(value, 'paper'); }
  if (kind === 'concept') { state.selectedConceptId = value.id; state.conceptId = value.id; state.fieldId = null; state.fieldPair = null; renderDetail(value, 'concept'); }
  if (kind === 'edge') { state.selectedEdgeId = value.id; renderDetail(value, 'edge'); }
  if (kind === 'ribbon') { state.selectedFieldId = null; state.fieldId = null; state.conceptId = null; state.fieldPair = [value.sourceFieldId, value.targetFieldId]; }
  renderAll();
}

async function connect(token = '') {
  try {
    state.connectionSeq += 1; const connectionSeq = state.connectionSeq; state.pollController?.abort(); state.pollInFlight = false;
    state.token = token || els.token.value.trim(); if (!state.token) throw new Error('Enter the loopback bearer token');
    state.api = new AtlasApi({baseUrl: currentOrigin(), token: state.token}); const health = await state.api.health();
    if (state.pid && health.pid && Number(health.pid) !== Number(state.pid)) throw new Error('bridge PID changed during bootstrap');
    if (state.launchId && health.launchId && health.launchId !== state.launchId) throw new Error('bridge launch nonce changed during bootstrap');
    if (health.parentOrigin && health.parentOrigin !== state.parentOrigin) throw new Error('bridge parent origin mismatch');
    state.launchId ||= health.launchId || null; state.pid ||= health.pid || null; state.snapshot = await loadSnapshotForConnection(state.api, connectionSeq); state.status = await state.api.status(); for (let attempt = 0; attempt < 10 && state.snapshot.papers.length === 0 && Number(state.status.revision || state.snapshot.revision || 0) < 2; attempt += 1) { await new Promise(resolve => setTimeout(resolve, 100)); state.snapshot = await state.api.snapshot({projection: 'full', sample: 1}); state.status = await state.api.status(); } state.configUnavailable = []; state.settings = await readConfiguration(() => state.api.settings()); const taxonomy = await readConfiguration(() => state.api.taxonomy()); const overlays = await readConfiguration(() => state.api.overlays()); state.taxonomyRevision = Number(taxonomy?.revision || 0); state.overlaysRevision = Number(overlays?.revision || 0); showConfigurationAvailability();
    if (state.settings) { const settings = state.settings.settings || state.settings; if (settings.provider) els.provider.value = settings.provider; if (settings.threshold !== undefined) els.threshold.value = settings.threshold; if (settings.topK !== undefined) els.topK.value = settings.topK; if (settings.includeIndexedText !== undefined) els.indexed.checked = settings.includeIndexedText; if (settings.openAlex !== undefined) els.openalex.checked = settings.openAlex; }
    if (!state.snapshot.edges.some(edge => edge.type === 'citation')) { state.paperRelation = 'semantic'; state.relationTypes = new Set(['semantic']); if (els.relation) for (const option of els.relation.options) option.selected = option.value === 'semantic'; }
    state.api.revision = Number(state.snapshot.revision || -1); els.connection.hidden = true; els.token.value = ''; setStatus(`Connected · ${state.snapshot.source.alias || state.snapshot.source.id}`, state.status.stale ? 'stale' : 'ok'); renderAll(); if (window.parent !== window && !state.readySent) { state.readySent = true; messageParent({type: 'atlas:ready', launchId: state.launchId}); } startPolling();
  } catch (error) { setStatus(error.message, 'error'); }
}

async function poll() {
  if (!state.api || state.pollInFlight) return; state.pollInFlight = true; const sequence = state.connectionSeq; const controller = new AbortController(); state.pollController = controller; const timeout = setTimeout(() => controller.abort(), 8000);
  try { const status = await state.api.status({signal: controller.signal}); if (sequence !== state.connectionSeq) return; state.status = status; const revision = Number(status.revision ?? status.snapshotRevision ?? -1); if (revision !== -1 && revision !== state.api.revision) { const next = await state.api.snapshot({projection: 'full', sample: 1, signal: controller.signal}); if (sequence !== state.connectionSeq || !isCompatibleSnapshot(state.snapshot, next)) return; state.snapshot = next; state.api.revision = revision; renderAll(); } setStatus(`${status.phase || 'idle'}${status.stale ? ' · offline cache' : ''}`, status.stale ? 'stale' : 'ok'); } catch { if (sequence === state.connectionSeq) setStatus('Offline · showing cached snapshot', 'stale'); } finally { clearTimeout(timeout); if (state.pollController === controller) state.pollController = null; state.pollInFlight = false; }
}
function startPolling() { clearInterval(state.statusTimer); state.statusTimer = setInterval(poll, 1500); poll(); }

async function saveSettings() { if (!state.api || !state.settings) return; try { state.settings = await state.api.updateSettings({provider: els.provider.value, threshold: Number(els.threshold.value), topK: Number(els.topK.value), includeIndexedText: els.indexed.checked, openAlex: els.openalex.checked}, state.settings.revision); setStatus('Settings saved', 'ok'); } catch (error) { setStatus(`Settings conflict: ${error.message}; reload required`, 'error'); } }
async function createTaxonomy(event) { event.preventDefault(); if (!state.api) return; const form = new FormData(els.taxonomyForm); const aliases = String(form.get('aliases') || '').split(',').map(value => value.trim()).filter(Boolean); const kind = form.get('entityType'); const labelValue = String(form.get('label') || ''); const label = {en: labelValue}; const parentIds = String(form.get('parentId') || '').split(',').map(value => value.trim()).filter(Boolean); const entityId = String(form.get('entityId') || ''); const source = {sourceId: state.snapshot.source.serverId, sourceEpoch: state.snapshot.source.epoch}; try { const response = entityId ? await state.api.updateTaxonomy({...source, entityId, patch: {label, aliases, parentIds}}, state.taxonomyRevision) : await state.api.createTaxonomy({...source, entityType: kind, label, aliases, parentIds}, state.taxonomyRevision); state.taxonomyRevision = Number(response.revision || state.taxonomyRevision); setStatus('Taxonomy updated; refreshing', 'ok'); state.snapshot = await state.api.snapshot({projection: 'full', sample: 1}); renderAll(); } catch (error) { setStatus(`Taxonomy update failed: ${error.message}; reload required`, 'error'); } }
async function updateOverlay(event) { event.preventDefault(); if (!state.api) return; const form = new FormData(els.overlayForm); const entityId = String(form.get('entityId')); const entityType = String(form.get('entityType')); const aliases = String(form.get('alias') || '').split(',').map(value => value.trim()).filter(Boolean); const fieldIds = String(form.get('fieldId') || '').split(',').map(value => value.trim()).filter(Boolean); const current = {aliases, fieldIds, pinned: form.get('pinned') === 'on'}; const patch = buildEntityPatch(state.overlayBaseline || {}, current); if (entityType === 'paper') delete patch.aliases; if (!Object.keys(patch).length) { setStatus('No overlay changes to save', 'ok'); return; } try { const response = await state.api.updateOverlay({sourceId: state.snapshot.source.serverId, sourceEpoch: state.snapshot.source.epoch, entityType, entityId, patch}, state.overlaysRevision); state.overlaysRevision = Number(response.revision || state.overlaysRevision); state.overlayBaseline = {...current}; setStatus('Overlay saved', 'ok'); } catch (error) { setStatus(`Overlay conflict: ${error.message}; reload required`, 'error'); } }

function wire() {
  els.connect?.addEventListener('click', () => connect()); els.query?.addEventListener('input', event => { state.query = event.target.value; renderAll(); });
  els.sample?.addEventListener('input', event => { state.fraction = Number(event.target.value) / 100; els.sampleValue.textContent = `${event.target.value}%`; renderAll(); });
  els.palette?.addEventListener('change', event => { state.palette = event.target.value; renderAll(); }); els.theme?.addEventListener('change', event => { state.theme = event.target.value; renderAll(); });
  els.relation?.addEventListener('change', event => { state.relationTypes = new Set([...event.target.selectedOptions].map(option => option.value)); state.paperRelation = state.relationTypes.has('citation') ? 'citation' : state.relationTypes.has('semantic') ? 'semantic' : [...state.relationTypes][0] || 'semantic'; renderAll(); });
  els.reset?.addEventListener('click', () => { state.fieldId = null; state.fieldPair = null; state.conceptId = null; state.selectedFieldId = null; state.selectedPaperId = null; state.selectedConceptId = null; state.selectedEdgeId = null; state.query = ''; if (els.query) els.query.value = ''; renderFocusSummary(null, ''); renderAll(); });
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
