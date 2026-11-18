import {indexSnapshot} from './decoder.js';
import {stableHash} from './sampling.js';
import {categoryForConcept, categoryForPaper, fieldPresentation} from './network-style.js';

const keywordCache = new WeakMap();
export function fieldFamily(field, fields = [field]) { return fieldPresentation(field, {fields}).family; }

export function overviewProjection(snapshotInput, relationType = 'semantic') {
  const {snapshot, fieldById, paperById} = indexSnapshot(snapshotInput);
  const byParent = new Map(snapshot.fields.map(field => [field.id, []])); for (const field of snapshot.fields) { const parent = (field.parentIds || [])[0]; if (parent && byParent.has(parent)) byParent.get(parent).push(field); }
  const ordered = []; const visit = field => { ordered.push(field); for (const child of byParent.get(field.id) || []) visit(child); }; for (const field of snapshot.fields) if (!(field.parentIds || [])[0] || !byParent.has((field.parentIds || [])[0])) visit(field);
  const fields = ordered.map((field, index) => {
    const supplied = (snapshot.overview.fields || []).find(row => row.fieldId === field.id);
    return {id: field.id, label: field.label, value: Number(supplied?.paperWeight || 0), family: fieldFamily(field, snapshot.fields), parentIds: field.parentIds || [], depth: field.parentIds?.length || 0};
  });
  const edgeRows = snapshot.edges.filter(edge => edge.type === relationType && paperById.has(edge.source) && paperById.has(edge.target));
  const ribbonsByKey = new Map();
  for (const edge of edgeRows) {
    const source = paperById.get(edge.source); const target = paperById.get(edge.target);
    const normalized = snapshot.allocationMode === 'normalized-multifield'; const sourceFields = normalized ? (source.fieldIds?.length ? source.fieldIds : [source.displayFieldId].filter(Boolean)) : [source.displayFieldId].filter(Boolean); const targetFields = normalized ? (target.fieldIds?.length ? target.fieldIds : [target.displayFieldId].filter(Boolean)) : [target.displayFieldId].filter(Boolean); const denominator = Math.max(1, sourceFields.length * targetFields.length);
    for (const sourceFieldId of sourceFields) for (const targetFieldId of targetFields) {
      if (!fieldById.has(sourceFieldId) || !fieldById.has(targetFieldId) || sourceFieldId === targetFieldId) continue;
      const directed = Boolean(edge.directed); const key = directed ? `${sourceFieldId}>${targetFieldId}` : [sourceFieldId, targetFieldId].sort().join('|');
      const existing = ribbonsByKey.get(key) || {sourceFieldId: directed ? sourceFieldId : key.split('|')[0], targetFieldId: directed ? targetFieldId : key.split('|')[1], weight: 0, directed, relationType}; existing.weight += 1 / denominator; ribbonsByKey.set(key, existing);
    }
  }
  const computed = [...ribbonsByKey.values()]; const fallback = (snapshot.overview.ribbons || []).filter(row => fieldById.has(row.sourceFieldId) && fieldById.has(row.targetFieldId)).map(row => ({...row, value: Number(row.weight || 0), directed: false, relationType: 'overview'}));
  const ribbons = ((relationType === 'overview' || (!snapshot.papers.length && fallback.length)) ? fallback : computed).map(row => ({...row, value: Number(row.value ?? row.weight ?? 0)}));
  const fieldIds = new Set(fields.map(row => row.id));
  const memberships = snapshot.memberships.filter(row => fieldIds.has(row.fieldId) && paperById.has(row.paperId));
  return {fields, ribbons, totalPapers: Number(snapshot.overview.totalPapers || snapshot.papers.length), totalEdges: Number(snapshot.overview.totalEdges || snapshot.edges.length), memberships, allocationMode: snapshot.allocationMode};
}

export function circularLayout(projection, width, height) {
  const radius = Math.max(95, Math.min(width, height) / 2 - 62);
  const total = projection.fields.reduce((sum, field) => sum + Math.max(0, field.value), 0) || projection.fields.length || 1;
  let angle = -Math.PI / 2;
  const arcs = projection.fields.map(field => {
    const span = Math.max(0.065, (Math.max(0, field.value) / total) * Math.PI * 2 * 0.94);
    const arc = {...field, startAngle: angle, endAngle: angle + span}; angle += span + (Math.PI * 2 - projection.fields.reduce((s, f) => s + Math.max(0.065, (Math.max(0, f.value) / total) * Math.PI * 2 * 0.94), 0)) / Math.max(1, projection.fields.length); return arc;
  });
  const byId = new Map(arcs.map(arc => [arc.id, arc]));
  const links = projection.ribbons.map(ribbon => ({...ribbon, sourceArc: byId.get(ribbon.sourceFieldId), targetArc: byId.get(ribbon.targetFieldId)})).filter(link => link.sourceArc && link.targetArc);
  const sourceUsed = new Map(); const targetUsed = new Map(); const incidentUsed = new Map();
  for (const link of links) {
    const sourceSpan = link.sourceArc.endAngle - link.sourceArc.startAngle; const targetSpan = link.targetArc.endAngle - link.targetArc.startAngle;
    const sourceTotal = projection.ribbons.filter(row => row.sourceFieldId === link.sourceFieldId).reduce((sum, row) => sum + Number(row.value || 0), 0) || link.value; const targetTotal = projection.ribbons.filter(row => row.targetFieldId === link.targetFieldId).reduce((sum, row) => sum + Number(row.value || 0), 0) || link.value;
    const incidentTotal = fieldId => projection.ribbons.filter(row => !row.directed && (row.sourceFieldId === fieldId || row.targetFieldId === fieldId)).reduce((sum, row) => sum + Number(row.value || 0), 0) || link.value;
    const sourceIncident = link.directed ? sourceTotal : incidentTotal(link.sourceFieldId); const targetIncident = link.directed ? targetTotal : incidentTotal(link.targetFieldId); const sourceOffset = (link.directed ? sourceUsed.get(link.sourceFieldId) : incidentUsed.get(link.sourceFieldId)) || 0; const targetOffset = (link.directed ? targetUsed.get(link.targetFieldId) : incidentUsed.get(link.targetFieldId)) || 0; const sourceStart = link.sourceArc.startAngle + sourceSpan * sourceOffset / sourceIncident; const targetStart = link.targetArc.startAngle + targetSpan * targetOffset / targetIncident; link.sourceAngles = [sourceStart, sourceStart + sourceSpan * link.value / sourceIncident]; link.targetAngles = [targetStart, targetStart + targetSpan * link.value / targetIncident]; if (link.directed) { sourceUsed.set(link.sourceFieldId, sourceOffset + link.value); targetUsed.set(link.targetFieldId, targetOffset + link.value); } else { incidentUsed.set(link.sourceFieldId, sourceOffset + link.value); incidentUsed.set(link.targetFieldId, targetOffset + link.value); }
  }
  return {radius, arcs, links};
}

export function graphProjection(snapshotInput, detail, options = {}) {
  const {snapshot, conceptById, fieldById, paperById} = indexSnapshot(snapshotInput);
  const papers = detail?.papers || snapshot.papers;
  const paperIds = new Set(papers.map(paper => paper.id)); const categoryPalette = options.categoryPalette || {};
  const edges = (detail?.edges || snapshot.edges);
  const paperEdges = edges.filter(edge => (edge.type !== 'keyword' || (paperIds.has(edge.source) && paperIds.has(edge.target))) && paperIds.has(edge.source) && paperIds.has(edge.target));
  const allConcepts = snapshot.concepts; const conceptIds = new Set(allConcepts.map(concept => concept.id)); const aggregate = snapshot.overview.keywordAggregate;
  let cached = keywordCache.get(snapshot);
  if (!cached || cached.aggregate !== aggregate || cached.edges !== snapshot.edges || cached.concepts !== snapshot.concepts) {
    const explicitEdges = snapshot.edges.filter(edge => edge.type === 'keyword' && conceptIds.has(edge.source) && conceptIds.has(edge.target));
    const pairKey = (a, b) => a < b ? JSON.stringify([a, b]) : JSON.stringify([b, a]);
    const existingPairs = new Set(explicitEdges.map(edge => pairKey(edge.source, edge.target)));
    const aggregateEdges = [];
    if (aggregate && Array.isArray(aggregate.conceptIds) && Array.isArray(aggregate.pairs)) for (const pair of aggregate.pairs) {
      const ai = Array.isArray(pair) ? pair[0] : pair?.sourceIndex; const bi = Array.isArray(pair) ? pair[1] : pair?.targetIndex; const count = Number(Array.isArray(pair) ? pair[2] : pair?.count || 0); const source = aggregate.conceptIds[ai]; const target = aggregate.conceptIds[bi];
      if (conceptIds.has(source) && conceptIds.has(target) && !existingPairs.has(pairKey(source, target))) aggregateEdges.push({id: `keyword-aggregate:${source}:${target}`, source, target, type: 'keyword', cooccurrenceCount: count, aggregate: true});
    }
    cached = {aggregate, edges: snapshot.edges, concepts: snapshot.concepts, conceptEdgesAll: [...explicitEdges, ...aggregateEdges], orderedConcepts: [...allConcepts].sort((a, b) => stableHash(`${snapshot.source.epoch}|concept|${a.id}`) - stableHash(`${snapshot.source.epoch}|concept|${b.id}`) || a.id.localeCompare(b.id))}; keywordCache.set(snapshot, cached);
  }
  const {conceptEdgesAll, orderedConcepts} = cached;
  const maxConceptNodes = Math.max(1, Number(options.maxConceptNodes || 300)); const origins = new Set(options.selectedConceptIds || []);
  const mandatory = new Set([...(options.mandatoryConceptIds || []), ...origins]);
  const eligibleConcepts = detail?.filterActive ? new Set(detail.eligibleConceptIds || []) : new Set(conceptIds);
  for (const concept of allConcepts) if ((concept.fieldIds || []).includes(options.fieldId)) eligibleConcepts.add(concept.id);
  for (const id of mandatory) if (conceptIds.has(id)) eligibleConcepts.add(id);
  const neighbors = new Map();
  for (const edge of conceptEdgesAll) { let id = null; if (origins.has(edge.source) && !origins.has(edge.target)) id = edge.target; else if (origins.has(edge.target) && !origins.has(edge.source)) id = edge.source; if (id && conceptIds.has(id)) neighbors.set(id, (neighbors.get(id) || 0) + Number(edge.cooccurrenceCount || edge.count || edge.score || 1)); }
  const visibleConcepts = new Set([...origins].filter(id => conceptIds.has(id)));
  for (const id of [...mandatory].filter(id => !origins.has(id) && conceptIds.has(id)).sort()) if (visibleConcepts.size < maxConceptNodes) visibleConcepts.add(id);
  const rankedNeighbors = [...neighbors].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  for (const [id] of rankedNeighbors) if (visibleConcepts.size < maxConceptNodes) visibleConcepts.add(id);
  for (const concept of orderedConcepts) { if (visibleConcepts.size >= maxConceptNodes) break; if (eligibleConcepts.has(concept.id)) visibleConcepts.add(concept.id); }
  const visibleNeighbors = rankedNeighbors.filter(([id]) => visibleConcepts.has(id)).length;
  const concepts = allConcepts.filter(concept => visibleConcepts.has(concept.id)); const visibleConceptIds = new Set(concepts.map(concept => concept.id));
  const conceptPapers = papers.filter(paper => paper.conceptIds.some(id => visibleConceptIds.has(id))).sort((a, b) => stableHash(`${snapshot.source.epoch}|concept-paper|${a.id}`) - stableHash(`${snapshot.source.epoch}|concept-paper|${b.id}`) || a.id.localeCompare(b.id)); const priorityPapers = conceptPapers.filter(paper => paper.id === options.selectedPaperId || (paper.conceptIds || []).some(id => origins.has(id))); priorityPapers.sort((a, b) => Number(b.id === options.selectedPaperId) - Number(a.id === options.selectedPaperId)); const priorityIds = new Set(priorityPapers.map(paper => paper.id)); const visibleConceptPapers = [...priorityPapers, ...conceptPapers.filter(paper => !priorityIds.has(paper.id))].slice(0, 150); const visibleConceptPaperIds = new Set(visibleConceptPapers.map(paper => paper.id));
  const membershipEdges = concepts.flatMap(concept => visibleConceptPapers.filter(paper => visibleConceptPaperIds.has(paper.id) && paper.conceptIds.includes(concept.id)).map(paper => ({data: {id: `concept-link:${concept.id}:${paper.id}`, source: concept.id, target: paper.id, relation: 'concept-membership', directed: false}})));
  const selectedSpoke = edge => origins.has(edge.source || edge.data?.source) || origins.has(edge.target || edge.data?.target) || options.selectedPaperId && (edge.source === options.selectedPaperId || edge.target === options.selectedPaperId || edge.data?.source === options.selectedPaperId || edge.data?.target === options.selectedPaperId);
  membershipEdges.sort((a, b) => Number(Boolean(selectedSpoke(b))) - Number(Boolean(selectedSpoke(a))) || a.data.id.localeCompare(b.data.id));
  const shownMembership = membershipEdges.slice(0, 400);
  const inducedKeyword = conceptEdgesAll.filter(edge => visibleConceptIds.has(edge.source) && visibleConceptIds.has(edge.target));
  inducedKeyword.sort((a, b) => Number(Boolean(selectedSpoke(b))) - Number(Boolean(selectedSpoke(a))) || Number(b.cooccurrenceCount || b.count || b.score || 1) - Number(a.cooccurrenceCount || a.count || a.score || 1) || a.id.localeCompare(b.id));
  const shownKeyword = inducedKeyword.slice(0, 600);
  const paperCategories = new Map(papers.map(paper => [paper.id, categoryForPaper(paper, snapshot, categoryPalette)]));
  const papersByConcept = new Map(); for (const paper of papers) for (const id of paper.conceptIds || []) { const rows = papersByConcept.get(id) || []; rows.push(paper); papersByConcept.set(id, rows); }
  return {
    paperNodes: papers.map(paper => { const category = paperCategories.get(paper.id); return {data: {id: paper.id, label: paper.title, kind: 'paper', year: paper.year, textStatus: paper.textStatus, categoryId: category.id, categoryLabel: category.label, categoryPath: category.path.join(' / '), categoryColor: category.color, categoryInferred: category.inferred}}; }),
    paperEdges: paperEdges.map(edge => ({classes: edge.directed ? 'directed' : 'undirected', data: {id: edge.id, source: edge.source, target: edge.target, relation: edge.type, directed: edge.directed, score: edge.score, provenance: edge.provenance?.provider || '', evidenceTier: edge.evidenceTier}})),
    conceptNodes: concepts.map(concept => { const category = categoryForConcept(concept, papersByConcept.get(concept.id) || [], snapshot, categoryPalette); return {data: {id: concept.id, label: concept.label?.en || concept.label?.ko || concept.id, kind: 'concept', categoryId: category.id, categoryLabel: category.label, categoryPath: category.path.join(' / '), categoryColor: category.color, categoryInferred: category.inferred}}; }),
    conceptPaperNodes: visibleConceptPapers.map(paper => { const category = paperCategories.get(paper.id); return {data: {id: paper.id, label: paper.title, kind: 'paper', categoryId: category.id, categoryLabel: category.label, categoryPath: category.path.join(' / '), categoryColor: category.color, categoryInferred: category.inferred}}; }),
    conceptEdges: [
      ...shownMembership,
      ...shownKeyword.map(edge => ({data: {id: edge.id, source: edge.source, target: edge.target, relation: 'concept-cooccurrence', directed: false, count: edge.cooccurrenceCount || 1}}))
    ],
    paperById, fieldById, conceptById, conceptStats: {totalNodes: allConcepts.length, eligibleNodes: eligibleConcepts.size, neighborCandidates: neighbors.size, visibleNeighbors, directPaperCandidates: detail?.filterActive && origins.size ? detail.eligiblePapers : priorityPapers.length, visibleConceptPapers: visibleConceptPapers.length, hiddenNeighbors: Math.max(0, neighbors.size - visibleNeighbors), visibleNodes: concepts.length, hiddenNodes: Math.max(0, allConcepts.length - concepts.length), totalEdges: conceptEdgesAll.length, inducedEdges: inducedKeyword.length, visibleEdges: shownKeyword.length, hiddenEdges: inducedKeyword.length - shownKeyword.length, inducedMembershipEdges: membershipEdges.length, visibleMembershipEdges: shownMembership.length, hiddenMembershipEdges: membershipEdges.length - shownMembership.length, maxNodes: maxConceptNodes}
  };
}

export const AtlasProjection = {fieldFamily, overviewProjection, circularLayout, graphProjection};
if (typeof window !== 'undefined') window.AtlasProjection = AtlasProjection;
