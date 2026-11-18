import {indexSnapshot} from './decoder.js';

export function stableHash(input) {
  const text = String(input);
  let hash = 2166136261;
  for (let i = 0; i < text.length; i += 1) { hash ^= text.charCodeAt(i); hash = Math.imul(hash, 16777619); }
  return hash >>> 0;
}

export function stableOrder(rows, seed = '') {
  return [...rows].sort((a, b) => {
    const ah = stableHash(`${seed}|${a.id}`); const bh = stableHash(`${seed}|${b.id}`);
    return ah - bh || String(a.id).localeCompare(String(b.id));
  });
}

function oneHop(snapshot, selectedIds, edgeTypes = null) {
  const origins = new Set(selectedIds); const keep = new Set(origins);
  for (const edge of snapshot.edges) {
    if (edgeTypes && !edgeTypes.has(edge.type)) continue;
    if (origins.has(edge.source)) keep.add(edge.target);
    if (origins.has(edge.target)) keep.add(edge.source);
  }
  return keep;
}

/** Filter first, then deterministically retain selected IDs and one-hop neighbors. */
export function sampleDetail(snapshotInput, options = {}) {
  const {snapshot} = indexSnapshot(snapshotInput);
  const relationTypes = new Set(options.relationTypes || ['citation', 'semantic', 'keyword', 'related']);
  const filter = options.filter || {};
  const query = String(filter.query || '').trim().toLocaleLowerCase();
  const fieldId = filter.fieldId || null;
  const conceptId = filter.conceptId || null;
  const fieldPair = filter.fieldPair || null;
  const descendants = new Set(fieldId ? [fieldId] : []);
  if (fieldId) { let changed = true; while (changed) { changed = false; for (const field of snapshot.fields) if ((field.parentIds || []).some(parent => descendants.has(parent)) && !descendants.has(field.id)) { descendants.add(field.id); changed = true; } } }
  const eligible = snapshot.papers.filter(paper => {
    if (fieldId && !paper.fieldIds.some(id => descendants.has(id))) return false;
    if (conceptId && !paper.conceptIds.includes(conceptId)) return false;
    if (fieldPair && !(paper.fieldIds.includes(fieldPair[0]) || paper.fieldIds.includes(fieldPair[1]))) return false;
    if (query) {
      const haystack = [paper.title, paper.doi, ...(paper.authors || []), ...(paper.keywords || [])].join(' ').toLocaleLowerCase();
      if (!haystack.includes(query)) return false;
    }
    return true;
  });
  const eligibleIds = new Set(eligible.map(row => row.id));
  const eligibleConceptIds = new Set(eligible.flatMap(row => row.conceptIds || []));
  function edgeInScope(edge, paperSet, conceptSet) { return (paperSet.has(edge.source) && paperSet.has(edge.target)) || (edge.type === 'keyword' && conceptSet.has(edge.source) && conceptSet.has(edge.target)); }
  const requestedFraction = options.fraction === undefined ? 1 : Math.max(0, Math.min(1, Number(options.fraction)));
  const requestedCount = requestedFraction >= 1 ? eligible.length : Math.max(0, Math.ceil(eligible.length * requestedFraction));
  const selectedIds = new Set((options.selectedIds || []).filter(id => eligibleIds.has(id)));
  const retainedIds = oneHop({ ...snapshot, edges: snapshot.edges.filter(edge => relationTypes.has(edge.type) && eligibleIds.has(edge.source) && eligibleIds.has(edge.target)) }, selectedIds, relationTypes);
  const seed = `${snapshot.source.epoch}|${options.seed || 'atlas'}|${JSON.stringify(filter)}|${[...relationTypes].sort().join(',')}`;
  const ordered = stableOrder(eligible.filter(paper => !retainedIds.has(paper.id)), seed);
  const targetCount = Math.min(eligible.length, Math.max(requestedCount, retainedIds.size));
  const selected = new Set([...retainedIds, ...ordered.slice(0, Math.max(0, targetCount - retainedIds.size)).map(row => row.id)]);
  const papers = eligible.filter(row => selected.has(row.id));
  const selectedConcepts = new Set(papersConceptIds(papers, selected)); const edges = snapshot.edges.filter(edge => relationTypes.has(edge.type) && edgeInScope(edge, selected, selectedConcepts));
  function papersConceptIds(rows, ids) { return rows.filter(row => ids.has(row.id)).flatMap(row => row.conceptIds || []); }
  const eligibleEdgeCount = snapshot.edges.filter(edge => relationTypes.has(edge.type) && edgeInScope(edge, eligibleIds, eligibleConceptIds)).length;
  const overflow = Math.max(0, retainedIds.size - requestedCount);
  return {
    papers, edges, eligiblePapers: eligible.length, visiblePapers: papers.length, hiddenPapers: eligible.length - papers.length,
    eligibleEdges: eligibleEdgeCount,
    visibleEdges: edges.length, hiddenEdges: eligibleEdgeCount - edges.length,
    requestedFraction, requestedCount, retainedCount: retainedIds.size, mandatoryOverflow: overflow,
    reason: overflow ? 'selected paper neighborhood exceeds the requested sample budget' : requestedFraction >= 1 ? 'all eligible papers shown' : 'deterministic ID-hash sample'
  };
}

export const AtlasSampling = {stableHash, stableOrder, sampleDetail};
if (typeof window !== 'undefined') window.AtlasSampling = AtlasSampling;
