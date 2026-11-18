import {displayLabel, safeText} from './decoder.js';
import {fieldPresentation, renderFieldLegend} from './network-style.js';

const NS = 'http://www.w3.org/2000/svg';
const svgElement = (tag, attributes = {}) => { const node = document.createElementNS(NS, tag); for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value)); return node; };
const entityKind = (entity, snapshot = null) => entity?.kind || (entity?.paperId ? 'paper' : entity?.fieldId ? 'field' : entity?.source && entity?.target ? 'edge' : snapshot?.papers?.some(item => item.id === entity?.id) ? 'paper' : snapshot?.fields?.some(item => item.id === entity?.id) ? 'field' : 'concept');
const entityId = entity => entity?.id || entity?.value?.id || entity?.paperId || entity?.fieldId || null;
const resolveEntity = (snapshot, selection) => {
  const kind = selection?.kind || entityKind(selection, snapshot); const id = entityId(selection); if (!id) return {kind: null, entity: null};
  if (kind === 'paper') return {kind, entity: (snapshot.papers || []).find(item => item.id === id) || null};
  if (kind === 'concept') return {kind, entity: (snapshot.concepts || []).find(item => item.id === id) || null};
  if (kind === 'field') return {kind, entity: (snapshot.fields || []).find(item => item.id === id) || null};
  if (kind === 'edge' || kind === 'ribbon') return {kind: 'edge', entity: (snapshot.edges || []).find(item => item.id === id) || selection?.value || null};
  return {kind, entity: selection?.value || null};
};
let labelContext = null;
const endpointLabelOf = id => { const hit = [...(labelContext?.papers || []), ...(labelContext?.concepts || []), ...(labelContext?.fields || [])].find(item => item.id === id); return hit ? safeText(displayLabel(hit), id) : 'Unavailable item'; };
const labelFor = entity => entity?.source && entity?.target && entity?.type ? `${entity.type}: ${endpointLabelOf(entity.source)} ${entity.directed ? '→' : '↔'} ${endpointLabelOf(entity.target)}` : safeText(displayLabel(entity), entity?.id || 'Unknown');

function neighbors(snapshot, kind, selected) {
  const papers = snapshot.papers || []; const concepts = snapshot.concepts || []; const fields = snapshot.fields || []; const edges = snapshot.edges || []; const result = [];
  if (!selected) return result;
  if (kind === 'field') {
    for (const paper of papers.filter(paper => (paper.fieldIds || []).includes(selected.id)).slice(0, 12)) result.push({kind: 'paper', entity: paper, relation: 'paper'});
    for (const field of fields.filter(field => (field.parentIds || []).includes(selected.id) || (selected.parentIds || []).includes(field.id)).slice(0, 10)) result.push({kind: 'field', entity: field, relation: 'subfield'});
  } else if (kind === 'paper') {
    for (const concept of concepts.filter(concept => (selected.conceptIds || []).includes(concept.id)).slice(0, 10)) result.push({kind: 'concept', entity: concept, relation: 'concept'});
    for (const field of fields.filter(field => (selected.fieldIds || []).includes(field.id)).slice(0, 10)) result.push({kind: 'field', entity: field, relation: 'field'});
    for (const edge of edges.filter(edge => edge.source === selected.id || edge.target === selected.id).slice(0, 10)) { const id = edge.source === selected.id ? edge.target : edge.source; const paper = papers.find(item => item.id === id); if (paper) result.push({kind: 'paper', entity: paper, relation: edge.type || 'edge', edge}); }
  } else if (kind === 'concept') {
    for (const paper of papers.filter(paper => (paper.conceptIds || []).includes(selected.id)).slice(0, 12)) result.push({kind: 'paper', entity: paper, relation: 'paper'});
  } else if (kind === 'edge') {
    for (const id of [selected.source, selected.target]) {
      const paper = papers.find(item => item.id === id); const concept = concepts.find(item => item.id === id); const field = fields.find(item => item.id === id);
      const entity = paper || concept || field; if (entity) result.push({kind: paper ? 'paper' : concept ? 'concept' : 'field', entity, relation: 'endpoint'});
    }
  }
  const seen = new Set(); return result.filter(item => { const key = `${item.kind}:${item.entity.id}`; if (seen.has(key)) return false; seen.add(key); return true; });
}

function renderDefaultDetail(detail, kind, entity, snapshot) {
  const title = document.createElement('h3'); title.textContent = labelFor(entity); detail.append(title);
  const dl = document.createElement('dl'); const rows = kind === 'paper' ? [['Type', 'Paper'], ['Year', entity.year || '—'], ['DOI', entity.doi || '—']] : kind === 'field' ? [['Type', 'Field'], ['Members', (snapshot.memberships || []).filter(row => row.fieldId === entity.id).length]] : [['Type', kind === 'concept' ? 'Concept' : 'Relationship']];
  for (const [term, value] of rows) { const dt = document.createElement('dt'); dt.textContent = term; const dd = document.createElement('dd'); dd.textContent = safeText(value); dl.append(dt, dd); } detail.append(dl);
}

/**
 * Render a small, keyboard-accessible ego graph and detail drawer. The caller
 * owns reducer state and existing Zotero/PDF actions through `renderDetail`;
 * node and edge events return the same `{kind, value}` shape used elsewhere.
 */
export function renderSelection(container, snapshot, selection, onSelect = () => {}, options = {}) {
  if (!container) return {nodes: [], edges: [], selected: null};
  container.replaceChildren(); container.classList.add('atlas-selection-view'); labelContext = snapshot;
  const {kind, entity: selected} = resolveEntity(snapshot, selection); const related = neighbors(snapshot, kind, selected); const heading = document.createElement('div'); heading.className = 'atlas-selection-heading'; const title = document.createElement('h3'); title.textContent = selected ? `Selection · ${labelFor(selected)}` : 'Selection'; heading.append(title); container.append(heading);
  if (!selected) { const empty = document.createElement('p'); empty.className = 'atlas-empty'; empty.textContent = 'Select a field, paper, concept, or relationship.'; container.append(empty); return {nodes: [], edges: [], selected: null}; }
  const graph = svgElement('svg', {class: 'atlas-selection-graph', viewBox: '0 0 320 220', role: 'group', 'aria-label': `${labelFor(selected)} related entities`}); const center = {x: 160, y: 110}; const nodeItems = [{kind, entity: selected, relation: 'selected'}, ...related]; const points = nodeItems.map((item, index) => index === 0 ? center : {x: 160 + Math.cos((index - 1) * Math.PI * 2 / Math.max(1, related.length) - Math.PI / 2) * 78, y: 110 + Math.sin((index - 1) * Math.PI * 2 / Math.max(1, related.length) - Math.PI / 2) * 78});
  related.forEach((item, index) => {
    const edge = item.edge; const target = points[index + 1]; const name = edge ? `${edge.type || 'relationship'}: ${labelFor(selected)} ↔ ${labelFor(item.entity)}` : `${item.relation}: ${labelFor(selected)} → ${labelFor(item.entity)}`;
    const trigger = () => edge ? onSelect({kind: 'edge', value: edge}) : onSelect({kind: item.kind, value: item.entity});
    const group = svgElement('g', {class: 'atlas-selection-edge-group', tabindex: 0, role: 'button', 'aria-label': name, 'data-edge-kind': edge ? 'edge' : 'relation', ...(edge ? {'data-edge-id': edge.id} : {'data-target-id': item.entity.id})});
    group.append(svgElement('line', {class: 'atlas-selection-edge-hit', x1: center.x, y1: center.y, x2: target.x, y2: target.y}), svgElement('line', {class: `atlas-selection-edge${edge ? ' is-interactive' : ''}`, x1: center.x, y1: center.y, x2: target.x, y2: target.y}));
    group.addEventListener('click', trigger); group.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); trigger(); } }); graph.append(group);
  });
  const colors = item => item.kind === 'field' ? fieldPresentation(item.entity, snapshot, options.palette || {}).color : item.kind === 'paper' ? 'var(--atlas-series-1, #3c74ca)' : item.kind === 'concept' ? 'var(--atlas-series-4, #7b62c2)' : 'var(--atlas-series-5, #bb4b59)';
  const activate = item => onSelect({kind: item.kind, value: item.entity});
  nodeItems.forEach((item, index) => { const point = points[index]; const circle = svgElement('circle', {class: `atlas-selection-node${index === 0 ? ' is-selected' : ''}`, 'data-kind': item.kind, 'data-id': item.entity.id, cx: point.x, cy: point.y, r: index === 0 ? 24 : 13, fill: colors(item), tabindex: 0, role: 'button', 'aria-label': `${labelFor(item.entity)} · ${item.relation}`}); const tooltip = svgElement('title'); tooltip.textContent = labelFor(item.entity); circle.append(tooltip); const handler = () => activate(item); circle.addEventListener('click', handler); circle.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); handler(); } }); graph.append(circle); }); container.append(graph);
  const chips = document.createElement('div'); chips.className = 'atlas-selection-chips'; related.forEach(item => { const chip = document.createElement('button'); chip.type = 'button'; chip.className = 'atlas-selection-chip'; chip.textContent = labelFor(item.entity); chip.title = item.relation; chip.addEventListener('click', () => activate(item)); chips.append(chip); }); if (related.length) container.append(chips);
  const legendFields = kind === 'field' ? [selected, ...related.filter(item => item.kind === 'field').map(item => item.entity)] : related.filter(item => item.kind === 'field').map(item => item.entity);
  if (legendFields.length) { const legend = document.createElement('div'); legend.className = 'atlas-field-legend atlas-selection-legend'; legend.setAttribute('aria-label', 'Related categories'); renderFieldLegend(legend, snapshot, {fields: legendFields.filter((field, index, rows) => rows.findIndex(row => row.id === field.id) === index), selectedId: kind === 'field' ? selected.id : null, scopeId: options.scopeId || null, palette: options.palette || {}, onSelect: field => onSelect({kind: 'field', value: field})}); container.append(legend); }
  const detail = document.createElement('section'); detail.className = 'atlas-selection-detail'; detail.setAttribute('aria-live', 'polite'); if (typeof options.renderDetail === 'function') options.renderDetail(detail, selected, kind); else renderDefaultDetail(detail, kind, selected, snapshot); container.append(detail);
  return {nodes: nodeItems.map(item => ({kind: item.kind, id: item.entity.id, relation: item.relation})), edges: related.map(item => ({id: item.edge?.id || `${selected.id}:${item.entity.id}:${item.relation}`, source: selected.id, target: item.entity.id, relation: item.relation, original: item.edge || null})), selected: {kind, id: selected.id}, detail};
}

export const AtlasSelection = {renderSelection};
if (typeof window !== 'undefined') window.AtlasSelection = AtlasSelection;
