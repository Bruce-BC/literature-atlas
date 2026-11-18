import {categoryForPaper} from './network-style.js';

const RELATION_RULES = Object.freeze({
  semantic: Object.freeze({directed: false}),
  citation: Object.freeze({directed: true}),
});

// Indexing the immutable snapshot is the expensive part of this calculation.
// A WeakMap keeps this cache private to the current snapshot object and avoids
// retaining a Zotero corpus after its host has released it.
const snapshotCache = new WeakMap();

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function sourceIdentity(snapshot) {
  const source = snapshot?.source || {};
  return Object.freeze({
    id: source.id ?? null,
    serverId: source.serverId ?? source.id ?? null,
    epoch: source.epoch ?? null,
    revision: snapshot?.revision ?? null,
  });
}

function cachedSnapshot(snapshot) {
  if (!snapshot || typeof snapshot !== 'object') throw new TypeError('snapshot must be an object');
  let cached = snapshotCache.get(snapshot);
  if (cached) return cached;
  const papers = asArray(snapshot.papers).filter(paper => paper && typeof paper.id === 'string');
  const paperById = new Map(papers.map(paper => [paper.id, paper]));
  cached = {papers, paperById, source: sourceIdentity(snapshot)};
  snapshotCache.set(snapshot, cached);
  return cached;
}

function normalizedCategory(paper, snapshot) {
  const category = categoryForPaper(paper, snapshot);
  return {
    id: String(category.id || 'unknown'),
    label: String(category.label || 'Unknown field'),
    path: Array.isArray(category.path) ? [...category.path] : [String(category.label || 'Unknown field')],
    color: String(category.color || '#64748b'),
    inferred: Boolean(category.inferred),
  };
}

function stableCategoryOrder(a, b) {
  return a.id.localeCompare(b.id) || a.label.localeCompare(b.label);
}

function edgeKey(source, target, directed) {
  if (directed) return `${source}\u0000${target}`;
  return source < target ? `${source}\u0000${target}` : `${target}\u0000${source}`;
}

function finiteScore(edge) {
  const raw = edge?.score;
  if (raw == null || (typeof raw === 'string' && raw.trim() === '')) return null;
  const score = Number(raw);
  return Number.isFinite(score) && score >= 0 ? score : null;
}

function possibleEdgeCount(n, directed) {
  return directed ? n * Math.max(0, n - 1) : (n * Math.max(0, n - 1)) / 2;
}

function matrixPossible(sourceCount, targetCount, sameCategory, directed) {
  if (directed) return sameCategory ? sourceCount * Math.max(0, sourceCount - 1) : sourceCount * targetCount;
  return sameCategory ? (sourceCount * Math.max(0, sourceCount - 1)) / 2 : sourceCount * targetCount;
}

/**
 * Compute observed paper-to-paper connection density from the complete snapshot.
 *
 * This function intentionally does not accept a sampled detail object. The
 * graph's display budget can change without changing these totals. `rows` and
 * `categoryMatrix` describe observed edges only; absent edges are not inferred
 * as evidence of missing citations or semantic similarity.
 */
export function computePaperDensity(snapshot, options = {}) {
  const relationType = options?.relationType ?? 'semantic';
  const relation = RELATION_RULES[relationType];
  if (!relation) throw new RangeError(`unsupported paper relation: ${relationType}`);
  const cached = cachedSnapshot(snapshot);
  const allowed = options?.eligiblePaperIds == null
    ? new Set(cached.papers.map(paper => paper.id))
    : new Set(Array.from(options.eligiblePaperIds || []).filter(id => typeof id === 'string' && cached.paperById.has(id)));
  const papers = cached.papers.filter(paper => allowed.has(paper.id)).sort((a, b) => a.id.localeCompare(b.id));
  const paperIds = new Set(papers.map(paper => paper.id));
  const categoriesByPaper = new Map(papers.map(paper => [paper.id, normalizedCategory(paper, snapshot)]));

  // Deduplication is performed after endpoint validation. A duplicate edge is
  // one relation for degree, strength and category counts.
  const uniqueEdges = new Map();
  for (const edge of asArray(snapshot.edges)) {
    if (!edge || edge.type !== relationType || !paperIds.has(edge.source) || !paperIds.has(edge.target) || edge.source === edge.target) continue;
    const key = edgeKey(edge.source, edge.target, relation.directed);
    const previous = uniqueEdges.get(key);
    if (!previous) {
      uniqueEdges.set(key, {
        id: edge.id ?? key,
        source: edge.source,
        target: edge.target,
        relationType,
        directed: relation.directed,
        score: finiteScore(edge),
        weight: 1,
      });
    } else if (previous.score == null && finiteScore(edge) != null) {
      // Keep a useful score when one duplicate carries it, while retaining the
      // one-edge/unweighted count contract.
      previous.score = finiteScore(edge);
    }
  }
  const edges = [...uniqueEdges.values()].sort((a, b) => String(a.source).localeCompare(String(b.source)) || String(a.target).localeCompare(String(b.target)));

  const stats = new Map(papers.map(paper => [paper.id, {
    id: paper.id,
    title: paper.title ?? '',
    category: categoriesByPaper.get(paper.id),
    degree: 0,
    normalizedDegree: 0,
    inDegree: 0,
    outDegree: 0,
    inNormalizedDegree: 0,
    outNormalizedDegree: 0,
    // `strength` is deliberately unweighted: one observed relation = one.
    // For citation layers it counts incident directions; for semantic layers
    // it remains the unique-neighbor degree. `weightedStrength` is separate.
    strength: 0,
    weightedStrength: 0,
  }]));
  const neighbors = new Map(papers.map(paper => [paper.id, new Set()]));
  for (const edge of edges) {
    const source = stats.get(edge.source); const target = stats.get(edge.target);
    if (!source || !target) continue;
    neighbors.get(edge.source).add(edge.target);
    neighbors.get(edge.target).add(edge.source);
    if (relation.directed) {
      source.outDegree += 1; target.inDegree += 1;
    }
    const weight = edge.score == null ? 1 : edge.score;
    source.weightedStrength += weight;
    target.weightedStrength += weight;
  }
  const denominator = Math.max(0, papers.length - 1);
  const rows = papers.map(paper => {
    const row = stats.get(paper.id);
    row.degree = neighbors.get(paper.id).size;
    row.normalizedDegree = denominator ? row.degree / denominator : 0;
    row.inNormalizedDegree = denominator ? row.inDegree / denominator : 0;
    row.outNormalizedDegree = denominator ? row.outDegree / denominator : 0;
    row.strength = relation.directed ? row.inDegree + row.outDegree : row.degree;
    row.weightedStrength = Number(row.weightedStrength.toFixed(12));
    return {
      ...row,
      categoryId: row.category.id,
      categoryLabel: row.category.label,
      categoryPath: [...row.category.path],
      categoryColor: row.category.color,
      categoryInferred: row.category.inferred,
    };
  });

  const categoryMap = new Map();
  for (const row of rows) {
    const existing = categoryMap.get(row.categoryId);
    if (existing) existing.count += 1;
    else categoryMap.set(row.categoryId, {
      id: row.categoryId,
      label: row.categoryLabel,
      path: [...row.categoryPath],
      color: row.categoryColor,
      inferred: row.categoryInferred,
      count: 1,
    });
  }
  const categories = [...categoryMap.values()].sort(stableCategoryOrder);
  const categoryIds = categories.map(category => category.id);
  const categoryCounts = new Map(categories.map(category => [category.id, category.count]));
  const matrixCounts = new Map();
  const matrixKey = (source, target) => `${source}\u0000${target}`;
  for (const source of categoryIds) for (const target of categoryIds) matrixCounts.set(matrixKey(source, target), 0);
  for (const edge of edges) {
    const sourceCategory = categoriesByPaper.get(edge.source).id;
    const targetCategory = categoriesByPaper.get(edge.target).id;
    matrixCounts.set(matrixKey(sourceCategory, targetCategory), (matrixCounts.get(matrixKey(sourceCategory, targetCategory)) || 0) + 1);
    if (!relation.directed && sourceCategory !== targetCategory) {
      matrixCounts.set(matrixKey(targetCategory, sourceCategory), (matrixCounts.get(matrixKey(targetCategory, sourceCategory)) || 0) + 1);
    }
  }
  const categoryMatrixCells = [];
  for (const source of categories) for (const target of categories) {
    const same = source.id === target.id;
    const numerator = matrixCounts.get(matrixKey(source.id, target.id)) || 0;
    const possible = matrixPossible(source.count, target.count, same, relation.directed);
    categoryMatrixCells.push({
      sourceCategoryId: source.id,
      targetCategoryId: target.id,
      numerator,
      possible,
      density: possible ? numerator / possible : 0,
    });
  }

  const n = papers.length;
  const m = edges.length;
  const possible = possibleEdgeCount(n, relation.directed);
  const counts = {
    papers: n,
    paperCount: n,
    edges: m,
    edgeCount: m,
    possibleEdges: possible,
    possibleEdgeCount: possible,
    categories: categories.length,
    isolatedPapers: rows.filter(row => row.degree === 0).length,
  };
  return {
    source: cached.source,
    relationType,
    directed: relation.directed,
    rows,
    paperRows: rows,
    papers: rows,
    edges,
    counts,
    density: possible ? m / possible : 0,
    categories,
    categoryMatrix: {
      directed: relation.directed,
      rowOrder: categoryIds,
      cells: categoryMatrixCells,
    },
    coverage: {
      kind: 'observed-edges-only',
      observedEdgeCount: m,
      possibleEdgeCount: possible,
      missingEdgesNotInferred: true,
      citationCoverage: 'unknown',
      semanticCoverage: 'unknown',
    },
  };
}

export const PaperDensity = Object.freeze({computePaperDensity});
if (typeof window !== 'undefined') window.AtlasPaperDensity = PaperDensity;

