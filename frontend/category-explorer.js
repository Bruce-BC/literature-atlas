import {displayLabel} from './decoder.js';
import {categoryForPaper, fieldPresentation} from './network-style.js';

const NS = 'http://www.w3.org/2000/svg';
export const CATEGORY_PAGE_SIZE = 24;
export const CATEGORY_TOTAL_LIMIT = 150;

/** Primary assignments are disjoint; additional memberships remain in paper detail. */
export function categoryGroups(snapshot, eligibleIds, palette = {}) {
  const eligible = new Set(eligibleIds); const groups = new Map(snapshot.fields.map(field => [field.id, {id: field.id, label: displayLabel(field), color: fieldPresentation(field, snapshot, palette).color, papers: []}]));
  const unclassified = {id: '__unclassified__', label: 'Unclassified', color: '#64748b', papers: []};
  for (const paper of snapshot.papers) if (eligible.has(paper.id)) (groups.get(categoryForPaper(paper, snapshot, palette).id) || unclassified).papers.push(paper);
  const output = [...groups.values()]; if (unclassified.papers.length) output.push(unclassified);
  for (const group of output) group.papers.sort((a,b) => a.id.localeCompare(b.id));
  return output;
}

function svgElement(tag, attributes = {}) { const node = document.createElementNS(NS, tag); for (const [key,value] of Object.entries(attributes)) node.setAttribute(key, String(value)); return node; }

export function renderCategoryExplorer(container, snapshot, eligibleIds, expanded, callbacks) {
  const computed = getComputedStyle(document.getElementById('atlas-app') || document.documentElement); const palette = {series: Array.from({length:6}, (_, index) => computed.getPropertyValue(`--atlas-series-${index+1}-hex`).trim() || '#64748b')};
  const groups = categoryGroups(snapshot, eligibleIds, palette); container.replaceChildren();
  let remaining = CATEGORY_TOTAL_LIMIT;
  const counts = new Map();
  for (const group of groups) {
    const page = expanded.get(group.id); const offset = (page || 0) * CATEGORY_PAGE_SIZE; const requested = page === undefined ? 0 : CATEGORY_PAGE_SIZE; const count = Math.min(requested, Math.max(0, group.papers.length - offset), remaining); remaining -= count; counts.set(group.id, count);
  }
  for (const group of groups) {
    const card = document.createElement('section'); card.className = 'atlas-category-card';
    const title = document.createElement('h3'); title.textContent = `${group.label} · ${group.papers.length} papers`; card.append(title);
    const graph = svgElement('svg', {viewBox: '0 0 240 220', role: 'group', 'aria-label': `${group.label} papers`, class: 'atlas-category-graph'});
    const count = counts.get(group.id); const offset = (expanded.get(group.id) || 0) * CATEGORY_PAGE_SIZE; const papers = group.papers.slice(offset,offset+count);
    const center = [120, 105];
    papers.forEach((paper, i) => { const ring = i < 12 ? 1 : 2; const ringIndex = ring === 1 ? i : i - 12; const ringCount = ring === 1 ? Math.min(papers.length, 12) : Math.min(papers.length - 12, 12); const angle = (ringIndex / Math.max(1, ringCount)) * Math.PI * 2 - Math.PI / 2; const radius = ring === 1 ? 52 : 84; const x = center[0] + Math.cos(angle) * radius; const y = center[1] + Math.sin(angle) * radius; graph.append(svgElement('line', {x1:center[0],y1:center[1],x2:x,y2:y,class:'atlas-category-link'})); const node=svgElement('circle',{cx:x,cy:y,r:7,fill:group.color,class:'atlas-category-paper','data-category-child':paper.id,'data-paper-id':paper.id,tabindex:0,role:'button','aria-label':paper.title}); const tooltip=svgElement('title');tooltip.textContent=paper.title;node.append(tooltip);const select=()=>callbacks.selectPaper(paper);node.addEventListener('click',select);node.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();select();}});graph.append(node); });
    const anchor=svgElement('circle',{cx:center[0],cy:center[1],r:24,fill:group.color,class:'atlas-category-anchor','data-category-anchor':group.id,'aria-expanded':String(expanded.has(group.id)),tabindex:0,role:'button','aria-label':`${group.label}: ${count ? 'collapse' : 'expand'} papers`}); const toggle=()=>callbacks.toggle(group.id, expanded.has(group.id)); anchor.addEventListener('click',toggle);anchor.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();toggle();}});graph.append(anchor);card.append(graph);
    const status=document.createElement('p');status.textContent=count ? `${offset + 1}–${offset + count} / ${group.papers.length} papers` : `0/${group.papers.length} shown`;card.append(status);
    const actions=document.createElement('div'); actions.className='atlas-category-actions';
    const toggleButton=document.createElement('button');toggleButton.type='button';if(expanded.has(group.id))toggleButton.dataset.categoryCollapse=group.id;toggleButton.textContent=expanded.has(group.id)?'Collapse':'Expand';toggleButton.disabled=!group.papers.length;toggleButton.addEventListener('click',toggle);actions.append(toggleButton);
    if (expanded.has(group.id) && offset > 0) { const previous=document.createElement('button');previous.type='button';previous.textContent='Previous';previous.addEventListener('click',()=>callbacks.page(group.id, -1));actions.append(previous); }
    if (expanded.has(group.id) && offset + count < group.papers.length) { const next=document.createElement('button');next.type='button';next.dataset.categoryShowMore=group.id;next.textContent='Next';next.disabled=count === 0;next.title=next.disabled?'Collapse another field to free display space':'';next.addEventListener('click',()=>callbacks.page(group.id, 1));actions.append(next); }
    card.append(actions);container.append(card);
  }
  return {groups, shown:[...counts.values()].reduce((a,b)=>a+b,0), limit:CATEGORY_TOTAL_LIMIT};
}
