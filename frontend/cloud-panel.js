import {cloudGroups, subgroupLPA, UNFILED_CLOUD_ID} from './collection-clouds.js';
import {displayLabel} from './decoder.js';
import {TIER_STYLE} from './edge-strength.js';

/**
 * Collection cloud panel (AC-7..AC-10).  `cloudPanelModel` is pure (no DOM) and reads
 * ONLY the full snapshot: it never sees the detail sample, so the same snapshot gives the
 * same model for every detail fraction.  `renderCloudPanel` writes text with `textContent`.
 *
 * Concept distinctiveness of a cloud C (n papers) against the whole library (N papers):
 *   score(c) = p_C(c) * ln(p_C(c) / p_L(c)),  p_C = a/n, p_L = A/N
 * where a / A count papers carrying concept c inside C / in the library (each paper once).
 * It is the concept's term of KL(C || library): frequent AND over-represented concepts
 * rank first.  Ties: score desc, a desc, label asc, id asc.  Auto sub-group labels use the
 * same score with the cloud as background.
 */
export const PANEL_LIMITS = Object.freeze({concepts: 8, papers: 5, related: 5, autoLabelConcepts: 2, subItems: 8});
export const OTHER_LABEL = '기타 · 자동';
export const DIRECT_LABEL = '직접 소속';
export const NO_YEAR_LABEL = '연도 없음';

const cmp = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
const ids = rows => new Set((rows || []).map(String));
const scoreOf = edge => (edge.score !== null && edge.score !== undefined && Number.isFinite(Number(edge.score)) ? Number(edge.score) : 1);
const yearOf = paper => { const year = Number.parseInt(paper?.year, 10); return Number.isFinite(year) && year > 0 ? year : null; };

// Display-only inline markup Zotero keeps in titles (rich-text subset).  Text processing only:
// a title is never parsed into the DOM, and any other '<...>' stays visible as text.
const DISPLAY_TAG = /<\/?(?:b|i|em|strong|sub|sup|u|sc|span)(?:\s[^<>]*)?>/gi;
export const stripDisplayTags = text => String(text ?? '').replace(DISPLAY_TAG, '').replace(/\s+/g, ' ').trim();

/**
 * Noise rule (AC-9, D2-16S default = filter): a label starting with a digit, an MSC-like
 * code (`^\d{2}[A-Z]\d{2}`), a comma-separated list of codes, or 2 characters or fewer.
 */
export function isNoiseLabel(label) {
  const text = String(label ?? '').trim();
  if ([...text].length <= 2) return true;
  if (/^\d/.test(text) || /^\d{2}[A-Z]\d{2}/.test(text)) return true;
  return text.includes(',') && text.split(',').every(part => /^\s*[A-Za-z]*\d[\w.-]*\s*$/.test(part));
}

/** Decoded ledger buckets: `suppressed` is always excluded (incl. user exclusions); `protected`/`user` are exempt from the noise rule. */
export function ledgerSets(snapshot) {
  const ledger = snapshot?.conceptQualityLedger || {}; const suppressed = new Set(); const exempt = new Set();
  const idOf = entry => String(typeof entry === 'string' ? entry : entry?.conceptId ?? entry?.id);
  const put = (status, entry) => { if (status === 'suppressed') suppressed.add(idOf(entry)); else if (status === 'protected' || status === 'user') exempt.add(idOf(entry)); };
  for (const status of ['suppressed', 'protected', 'user']) for (const entry of Array.isArray(ledger[status]) ? ledger[status] : []) put(status, entry);
  for (const entry of Array.isArray(ledger.entries) ? ledger.entries : []) put(entry?.status, entry);
  return {suppressed, exempt};
}

const libraryCache = new WeakMap();
function library(snapshot) {
  let cached = libraryCache.get(snapshot); if (cached) return cached;
  const papers = snapshot?.papers || []; const paperById = new Map(papers.map(paper => [String(paper.id), paper]));
  const conceptById = new Map((snapshot?.concepts || []).map(concept => [String(concept.id), concept]));
  const conceptsOf = new Map(papers.map(paper => [String(paper.id), [...ids(paper.conceptIds)]]));
  const groups = cloudGroups(snapshot); const cloudsOf = new Map();
  for (const cloud of groups) for (const id of cloud.paperIds) (cloudsOf.get(id) || cloudsOf.set(id, []).get(id)).push(cloud.id);
  const edges = (snapshot?.edges || []).filter(edge => paperById.has(String(edge.source)) && paperById.has(String(edge.target)) && String(edge.source) !== String(edge.target));
  cached = {paperById, conceptById, conceptsOf, libraryCounts: countConcepts(conceptsOf, paperById.keys()), groups, cloudById: new Map(groups.map(cloud => [cloud.id, cloud])), cloudsOf, edges, ledger: ledgerSets(snapshot), models: new Map()};
  libraryCache.set(snapshot, cached); return cached;
}

function countConcepts(conceptsOf, paperIds) { const counts = new Map(); for (const id of paperIds) for (const conceptId of conceptsOf.get(id) || []) counts.set(conceptId, (counts.get(conceptId) || 0) + 1); return counts; }

/** Top `limit` distinctive concepts of `paperIds` against a background (`backgroundCounts` over `backgroundSize` papers). */
function distinctiveConcepts(lib, paperIds, backgroundCounts, backgroundSize, limit) {
  const n = paperIds.length; if (!n || !backgroundSize) return [];
  const rows = [];
  for (const [conceptId, a] of countConcepts(lib.conceptsOf, paperIds)) {
    const concept = lib.conceptById.get(conceptId); if (!concept || lib.ledger.suppressed.has(conceptId)) continue;
    const label = displayLabel(concept); if (!lib.ledger.exempt.has(conceptId) && isNoiseLabel(label)) continue;
    const A = Math.max(a, backgroundCounts.get(conceptId) || 0); const p = a / n;
    rows.push({id: conceptId, label, count: a, libraryCount: A, score: Number((p * Math.log(p / (A / backgroundSize))).toFixed(6))});
  }
  return rows.sort((x, y) => y.score - x.score || y.count - x.count || cmp(x.label, y.label) || cmp(x.id, y.id)).slice(0, limit);
}

/** Child collections (rolled up through `parentId`) partition the cloud: each paper goes to the first child (size desc, id asc) holding it; the rest is 'direct'. */
function collectionItems(snapshot, cloud) {
  const collections = snapshot?.collections || []; const children = collections.filter(row => row.parentId != null && String(row.parentId) === cloud.id);
  if (!children.length) return null;
  const byParent = new Map(); for (const row of collections) if (row.parentId != null) (byParent.get(String(row.parentId)) || byParent.set(String(row.parentId), []).get(String(row.parentId))).push(row);
  const inCloud = new Set(cloud.paperIds);
  const rolled = child => { const out = new Set(); const seen = new Set(); const queue = [child]; while (queue.length) { const row = queue.shift(); if (seen.has(String(row.id))) continue; seen.add(String(row.id)); for (const id of ids(row.paperIds)) if (inCloud.has(id)) out.add(id); queue.push(...(byParent.get(String(row.id)) || [])); } return out; };
  const rows = children.map(child => ({id: String(child.id), label: String(child.label ?? child.id), members: rolled(child)})).filter(row => row.members.size).sort((a, b) => b.members.size - a.members.size || cmp(a.id, b.id));
  if (!rows.length) return null;
  const taken = new Set(); const items = rows.map(row => { const paperIds = [...row.members].filter(id => !taken.has(id)).sort(cmp); paperIds.forEach(id => taken.add(id)); return {id: row.id, label: row.label, count: paperIds.length, total: row.members.size, auto: false, other: false, paperIds}; });
  const direct = cloud.paperIds.filter(id => !taken.has(id));
  if (direct.length) items.push({id: `direct:${cloud.id}`, label: DIRECT_LABEL, count: direct.length, total: direct.length, auto: false, other: true, paperIds: direct});
  return items.filter(item => item.count > 0);
}

/** Deterministic LPA sub-groups over the SAME intra-cloud relation edges the layout uses (groups < 3 merge into one '기타 · 자동'). */
function autoItems(lib, cloud, relation) {
  const inCloud = new Set(cloud.paperIds);
  const groups = subgroupLPA(cloud.paperIds, lib.edges.filter(edge => edge.type === relation && inCloud.has(String(edge.source)) && inCloud.has(String(edge.target))));
  const cloudCounts = countConcepts(lib.conceptsOf, cloud.paperIds);
  return groups.map((group, index) => {
    if (group.other) return {id: group.id, label: OTHER_LABEL, count: group.size, total: group.size, auto: true, other: true, paperIds: group.paperIds};
    const names = distinctiveConcepts(lib, group.paperIds, cloudCounts, cloud.size, PANEL_LIMITS.autoLabelConcepts).map(row => row.label);
    return {id: group.id, label: `${names.length ? names.join(' · ') : `그룹 ${index + 1}`} · 자동`, count: group.size, total: group.size, auto: true, other: false, paperIds: group.paperIds};
  });
}

/** Visible cut-off (F3): the `PANEL_LIMITS.subItems` largest rows (count desc, list order) in list order, then every 'other' row (기타 / 직접 소속) last, uncounted. */
function visibleSubItems(items) {
  const ranked = items.map((item, index) => ({item, index})).filter(row => !row.item.other).sort((a, b) => b.item.count - a.item.count || a.index - b.index).slice(0, PANEL_LIMITS.subItems);
  const shown = new Set(ranked.map(row => row.item.id));
  const visibleIds = [...items.filter(item => !item.other && shown.has(item.id)), ...items.filter(item => item.other)].map(item => item.id);
  return {visibleIds, moreCount: items.length - visibleIds.length};
}

function cloudModel(snapshot, lib, cloud, relation) {
  const inCloud = new Set(cloud.paperIds);
  const subItems = collectionItems(snapshot, cloud); const kind = subItems ? 'collections' : 'auto';
  const items = subItems || autoItems(lib, cloud, relation);
  const concepts = distinctiveConcepts(lib, cloud.paperIds, lib.libraryCounts, lib.paperById.size, PANEL_LIMITS.concepts);
  const neighbours = new Map(cloud.paperIds.map(id => [id, new Set()]));
  const related = new Map(); const suggestion = new Map();
  for (const edge of lib.edges) {
    const source = String(edge.source); const target = String(edge.target); const type = edge.type;
    if (type === 'semantic' && inCloud.has(source) && inCloud.has(target)) { neighbours.get(source).add(target); neighbours.get(target).add(source); }
    if (type !== 'semantic' && type !== 'citation') continue; // keyword co-occurrence and other types are never mixed in
    const others = new Set(); if (inCloud.has(source)) for (const id of lib.cloudsOf.get(target) || []) others.add(id); if (inCloud.has(target)) for (const id of lib.cloudsOf.get(source) || []) others.add(id); others.delete(cloud.id);
    for (const id of others) { const row = related.get(id) || related.set(id, {semantic: 0, citation: 0}).get(id); row[type] += 1; }
    if (cloud.unfiled && type === 'semantic') for (const id of others) { const row = suggestion.get(id) || suggestion.set(id, {score: 0, edges: 0}).get(id); row.score += scoreOf(edge); row.edges += 1; }
  }
  const paperRows = cloud.paperIds.map(id => { const paper = lib.paperById.get(id); return {id, title: stripDisplayTags(displayLabel(paper)) || id, year: yearOf(paper), firstAuthor: String((paper?.authors || [])[0] ?? '') || '—', degree: neighbours.get(id).size}; })
    .sort((a, b) => b.degree - a.degree || (b.year ?? -Infinity) - (a.year ?? -Infinity) || cmp(a.id, b.id));
  const yearCounts = new Map(); let noYear = 0; for (const row of paperRows) { if (row.year === null) noYear += 1; else yearCounts.set(row.year, (yearCounts.get(row.year) || 0) + 1); }
  const years = [...yearCounts].sort((a, b) => a[0] - b[0]).map(([year, count]) => ({year, label: String(year), count})); if (noYear) years.push({year: null, label: NO_YEAR_LABEL, count: noYear});
  const cloudRow = id => lib.cloudById.get(id);
  const relatedRows = [...related].map(([id, row]) => ({cloudId: id, label: cloudRow(id).label, size: cloudRow(id).size, unfiled: cloudRow(id).unfiled, semantic: row.semantic, citation: row.citation}))
    .sort((a, b) => b.semantic - a.semantic || b.citation - a.citation || b.size - a.size || cmp(a.cloudId, b.cloudId)).slice(0, PANEL_LIMITS.related);
  let nearest = null;
  if (cloud.unfiled) { const best = [...suggestion].filter(([id]) => !cloudRow(id).unfiled).map(([id, row]) => ({cloudId: id, label: cloudRow(id).label, size: cloudRow(id).size, score: Number(row.score.toFixed(6)), edges: row.edges})).sort((a, b) => b.score - a.score || b.size - a.size || cmp(a.cloudId, b.cloudId))[0]; nearest = best || {cloudId: null}; }
  return {cloudId: cloud.id, label: cloud.label, unfiled: cloud.unfiled, size: cloud.size, paperIds: cloud.paperIds, subItems: {kind, items, ...visibleSubItems(items)}, concepts, rankedPapers: paperRows, years, related: relatedRows, suggestion: nearest};
}

/**
 * Panel model for `cloudId` (null when the cloud does not exist).  `relation` is the
 * relation the map layout uses (sub-groups match the drawn clumps); representative
 * papers always use the intra-cloud SEMANTIC degree.  Cached per snapshot.
 */
export function cloudPanelModel(snapshot, cloudId, {relation = 'semantic', subItemId = null, highlightPaperId = null} = {}) {
  if (!snapshot || !cloudId) return null; const lib = library(snapshot); const cloud = lib.cloudById.get(String(cloudId)); if (!cloud) return null;
  const key = `${cloud.id}\u0000${relation}`; const base = lib.models.get(key) || lib.models.set(key, cloudModel(snapshot, lib, cloud, relation)).get(key);
  const highlight = highlightPaperId && cloud.paperIds.includes(String(highlightPaperId)) ? String(highlightPaperId) : null;
  const papers = base.rankedPapers.slice(0, PANEL_LIMITS.papers).map(row => ({...row, highlighted: row.id === highlight}));
  const highlighted = highlight && !papers.some(row => row.highlighted) ? {...base.rankedPapers.find(row => row.id === highlight), highlighted: true} : null;
  const selectedSubItemId = base.subItems.items.some(item => item.id === subItemId) ? subItemId : null;
  const {rankedPapers, ...rest} = base;
  return {...rest, papers, highlightedPaper: highlighted, highlightPaperId: highlight, selectedSubItemId};
}

/** Event payload for CLOUD_SELECT from the same cloud grouping the map uses. */
export function cloudSelection(snapshot, cloudId) { const cloud = cloudId ? library(snapshot).cloudById.get(String(cloudId)) : null; return cloud ? {cloudId: cloud.id, paperIds: cloud.paperIds} : null; }

// ---- DOM -------------------------------------------------------------------------------------------------------
const el = (doc, tag, className = '', text = null) => { const node = doc.createElement(tag); if (className) node.className = className; if (text !== null) node.textContent = String(text); return node; };
const section = (doc, title, key) => { const node = el(doc, 'section', 'atlas-cloud-panel-section'); node.dataset.section = key; node.append(el(doc, 'h3', '', title)); return node; };

/**
 * Render `model` into `container` (the `<aside id="atlas-cloud-panel">`).  Callbacks:
 * `close()`, `selectPaper(paperId)`, `selectSubItem(item)`, `selectCloud(cloudId)`,
 * `renderPaperDetail(target, paperId)` (the app's existing detail + Zotero/PDF actions).
 * Focus is restored to the element with the same `data-focus-key`; `focusDetail` moves it to the detail heading.
 * `edgeLegend` = `tierLegend(edgeView)` rows ({tier, count}) of the paper lines drawn on the map; empty/null -> no legend (ME-6).
 */
export const TIER_LABELS = Object.freeze({strong: '강', medium: '중', weak: '약'});
export const EDGE_LEGEND_NOTE = '선 굵기는 이 라이브러리 안에서의 상대 강도입니다.';

export function renderCloudPanel(container, model, {close = () => {}, selectPaper = () => {}, selectSubItem = () => {}, selectCloud = () => {}, renderPaperDetail = null, focusDetail = false, edgeLegend = null, focusScroll = true} = {}) {
  const doc = container.ownerDocument || globalThis.document; const active = doc.activeElement; const focusKey = active && container.contains(active) ? active.dataset?.focusKey || null : null;
  const moreWasOpen = container.dataset.cloudId === model.cloudId && Boolean(container.querySelector?.('details.atlas-cloud-subitems-more')?.open); // a re-render of the same cloud keeps the user's disclosure state
  container.replaceChildren(); container.dataset.cloudId = model.cloudId; container.dataset.paperCount = String(model.size);
  const button = (className, text, key, onClick) => { const node = el(doc, 'button', className, text); node.type = 'button'; node.dataset.focusKey = key; node.addEventListener('click', onClick); return node; };
  const header = el(doc, 'header', 'atlas-cloud-panel-header'); const titles = el(doc, 'div');
  const heading = el(doc, 'h2', '', model.label); heading.id = 'atlas-cloud-panel-title'; heading.title = model.label;
  titles.append(heading, el(doc, 'p', 'atlas-note', `${model.size} papers${model.unfiled ? ' · 자동 구름 (컬렉션 없음)' : ''}`));
  const closeButton = button('atlas-cloud-panel-close', '×', 'close', close); closeButton.setAttribute('aria-label', '구름 요약 닫기');
  header.append(titles, closeButton); container.append(header);

  if (model.highlightPaperId && typeof renderPaperDetail === 'function') {
    const detail = el(doc, 'section', 'atlas-cloud-panel-detail'); detail.setAttribute('aria-live', 'polite'); detail.dataset.paperId = model.highlightPaperId; renderPaperDetail(detail, model.highlightPaperId);
    const title = detail.querySelector('h3'); if (title) { title.tabIndex = -1; title.dataset.focusKey = 'detail'; }
    container.append(detail);
  }

  if (edgeLegend?.length) {
    const legend = section(doc, '연결선 강도', 'edges'); const rows = el(doc, 'div', 'atlas-cloud-edge-legend'); const NS = 'http://www.w3.org/2000/svg';
    for (const row of edgeLegend) {
      const style = TIER_STYLE[row.tier]; if (!style) continue; const item = el(doc, 'div', 'atlas-cloud-edge-legend-row'); item.dataset.tier = row.tier; item.dataset.count = String(row.count);
      const svg = doc.createElementNS(NS, 'svg'); svg.setAttribute('width', '28'); svg.setAttribute('height', '10'); svg.setAttribute('aria-hidden', 'true'); const line = doc.createElementNS(NS, 'line');
      for (const [key, value] of [['x1', 2], ['y1', 5], ['x2', 26], ['y2', 5], ['stroke-width', style.width], ['stroke-opacity', style.opacity]]) line.setAttribute(key, String(value)); if (style.dash) line.setAttribute('stroke-dasharray', style.dash.join(' '));
      line.setAttribute('class', 'atlas-cloud-edge-sample'); svg.append(line); item.append(svg, el(doc, 'span', '', `${TIER_LABELS[row.tier]} · ${row.count}`)); rows.append(item);
    }
    legend.append(rows, el(doc, 'p', 'atlas-note', EDGE_LEGEND_NOTE)); container.append(legend);
  }

  // F3: the visible cut-off (8 largest + 'other' last) up front; the remainder behind ONE native <details> in the same section, opened when it holds the selected row.
  const collections = model.subItems.kind === 'collections'; const items = section(doc, collections ? '하위 컬렉션' : '하위 그룹 · 자동', 'subitems'); const itemList = el(doc, 'div', 'atlas-cloud-panel-list');
  const subRow = item => { const row = button('atlas-cloud-subitem', '', `subitem:${item.id}`, () => selectSubItem(item)); row.dataset.subitemId = item.id; row.dataset.count = String(item.count); row.dataset.auto = String(item.auto); row.setAttribute('aria-pressed', String(model.selectedSubItemId === item.id));
    row.append(el(doc, 'span', '', item.label), el(doc, 'small', '', item.total > item.count ? `${item.count} (총 ${item.total})` : item.count)); row.title = item.label; return row; };
  const visible = new Set(model.subItems.visibleIds || model.subItems.items.map(item => item.id)); const byId = new Map(model.subItems.items.map(item => [item.id, item]));
  for (const id of model.subItems.visibleIds || [...visible]) itemList.append(subRow(byId.get(id)));
  items.append(itemList);
  const rest = model.subItems.items.filter(item => !visible.has(item.id));
  if (rest.length) {
    const more = el(doc, 'details', 'atlas-cloud-subitems-more'); more.append(el(doc, 'summary', '', `${collections ? '하위 컬렉션' : '하위 그룹'} ${rest.length}개 더 보기`)); const moreList = el(doc, 'div', 'atlas-cloud-panel-list');
    for (const item of rest) moreList.append(subRow(item)); more.append(moreList); more.open = moreWasOpen || rest.some(item => item.id === model.selectedSubItemId); items.append(more);
  }
  container.append(items);

  const concepts = section(doc, '주요 개념', 'concepts'); const chips = el(doc, 'div', 'atlas-cloud-panel-chips');
  for (const concept of model.concepts) { const chip = el(doc, 'span', 'atlas-cloud-concept', concept.label); chip.dataset.conceptId = concept.id; chip.title = `${concept.count}/${model.size} papers in this cloud · ${concept.libraryCount} in the library`; chips.append(chip); }
  if (!model.concepts.length) chips.append(el(doc, 'p', 'atlas-empty', 'No distinctive concepts'));
  concepts.append(chips); container.append(concepts);

  const papers = section(doc, '대표 논문', 'papers'); const paperList = el(doc, 'div', 'atlas-cloud-panel-list');
  for (const paper of model.highlightedPaper ? [model.highlightedPaper, ...model.papers] : model.papers) {
    const row = button('atlas-cloud-paper', '', `paper:${paper.id}`, () => selectPaper(paper.id)); row.dataset.paperId = paper.id; row.setAttribute('aria-pressed', String(paper.highlighted)); if (paper.highlighted) row.setAttribute('aria-current', 'true');
    row.append(el(doc, 'span', '', paper.title), el(doc, 'small', '', `${paper.year ?? NO_YEAR_LABEL} · ${paper.firstAuthor}`)); paperList.append(row);
  }
  papers.append(paperList); container.append(papers);

  const years = section(doc, '연도 분포', 'years'); const bars = el(doc, 'div', 'atlas-cloud-years'); const peak = Math.max(1, ...model.years.map(row => row.count));
  for (const row of model.years) { const line = el(doc, 'div', 'atlas-cloud-year'); line.dataset.year = row.year === null ? 'none' : String(row.year); line.dataset.count = String(row.count); const bar = el(doc, 'span', 'atlas-cloud-year-bar'); bar.style.width = `${Math.round((row.count / peak) * 100)}%`; line.append(el(doc, 'span', '', row.label), el(doc, 'span', 'atlas-cloud-year-track'), el(doc, 'small', '', row.count)); line.children[1].append(bar); bars.append(line); }
  years.append(bars); container.append(years);

  const related = section(doc, '다른 구름과의 관계', 'related'); const relatedList = el(doc, 'div', 'atlas-cloud-panel-list');
  for (const row of model.related) { const item = button('atlas-cloud-related', '', `related:${row.cloudId}`, () => selectCloud(row.cloudId)); item.dataset.cloudId = row.cloudId; item.dataset.semantic = String(row.semantic); item.dataset.citation = String(row.citation); item.append(el(doc, 'span', '', row.label), el(doc, 'small', '', `semantic ${row.semantic} · citation ${row.citation}`)); relatedList.append(item); }
  if (!model.related.length) relatedList.append(el(doc, 'p', 'atlas-empty', 'No semantic or citation links to other clouds'));
  related.append(relatedList); container.append(related);

  if (model.unfiled) {
    const nearest = section(doc, '가장 가까운 코어 구름 (제안)', 'suggestion'); const text = model.suggestion?.cloudId ? `${model.suggestion.label} · semantic score ${model.suggestion.score.toFixed(2)} (${model.suggestion.edges} links)` : '제안 없음';
    const line = el(doc, 'p', 'atlas-cloud-suggestion', text); line.dataset.cloudId = model.suggestion?.cloudId || ''; nearest.append(line, el(doc, 'p', 'atlas-note', 'Display only: Zotero stays read-only.')); container.append(nearest);
  }
  const target = focusDetail ? container.querySelector('[data-focus-key="detail"]') : focusKey ? [...container.querySelectorAll('[data-focus-key]')].find(node => node.dataset.focusKey === focusKey) : null;
  target?.focus({preventScroll: !focusScroll}); // focusScroll false: the app positions the page itself (stacked layout)
}

/**
 * Cross-fade ghost (plan motion-and-edges §3.4): wraps the DETACHED previous panel content (the nodes `renderCloudPanel` replaced) so it can
 * fade out over the new content.  `aria-hidden`, `inert`, `pointer-events: none` (CSS), and EVERY `id`, `aria-live`, `tabindex` and focus key
 * stripped, so assistive technology, the keyboard and id selectors only ever see the new content.  `scrollTop` keeps the old scroll offset.
 */
export function panelGhost(doc, nodes, {scrollTop = 0} = {}) {
  const ghost = el(doc, 'div', 'atlas-motion-clone'); ghost.setAttribute('aria-hidden', 'true'); ghost.setAttribute('inert', '');
  const inner = el(doc, 'div', 'atlas-motion-clone-inner'); inner.style.transform = `translateY(${-Math.max(0, scrollTop)}px)`; for (const node of nodes) inner.append(node); ghost.append(inner);
  for (const node of [ghost, ...ghost.querySelectorAll('*')]) { for (const name of ['id', 'aria-live', 'tabindex', 'data-focus-key']) node.removeAttribute(name); }
  return ghost;
}

export const AtlasCloudPanel = {panelGhost, TIER_LABELS, EDGE_LEGEND_NOTE, PANEL_LIMITS, OTHER_LABEL, DIRECT_LABEL, NO_YEAR_LABEL, UNFILED_CLOUD_ID, stripDisplayTags, isNoiseLabel, ledgerSets, cloudPanelModel, cloudSelection, renderCloudPanel};
if (typeof window !== 'undefined') window.AtlasCloudPanel = AtlasCloudPanel;
