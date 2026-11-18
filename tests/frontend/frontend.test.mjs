import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {decodeSnapshot} from '../../frontend/decoder.js';
import {sampleDetail, stableHash} from '../../frontend/sampling.js';
import {circularLayout, graphProjection, overviewProjection} from '../../frontend/projection.js';
import {buildEntityPatch, isCompatibleSnapshot} from '../../frontend/api.js';
import {categoryForPaper, clampNetworkZoom, fieldPaperCounts, networkLegend} from '../../frontend/network-style.js';

const fixture = decodeSnapshot(JSON.parse(await readFile(new URL('../fixtures/snapshot.json', import.meta.url))));

test('poll snapshot guard accepts same-source recovery and rejects epoch retargets', () => {
  const recovered = structuredClone(fixture); recovered.revision = 9;
  assert.equal(isCompatibleSnapshot(fixture, recovered), true);
  const swapped = structuredClone(recovered); swapped.source.epoch = 'epoch-other';
  assert.equal(isCompatibleSnapshot(fixture, swapped), false);
});

test('overlay edits produce entity-scoped dirty patches', () => {
  const baseline = {aliases: ['A'], fieldIds: ['field:a'], pinned: true};
  assert.deepEqual(buildEntityPatch(baseline, {aliases: ['A'], fieldIds: ['field:a', 'field:b'], pinned: true}), {fieldIds: ['field:a', 'field:b']});
  assert.deepEqual(buildEntityPatch(baseline, {aliases: ['A'], fieldIds: ['field:a'], pinned: false}), {pinned: false});
});

test('network categories choose deepest deterministic membership and keep unknown neutral', () => {
  const nested = structuredClone(fixture); nested.fields[1].parentIds = [nested.fields[0].id]; nested.memberships.push({id: 'membership:extra', paperId: nested.papers[1].id, fieldId: nested.fields[0].id, method: 'user', score: 1, weight: 1});
  const category = categoryForPaper(nested.papers[1], nested, {series: ['#111111', '#222222', '#333333', '#444444', '#555555', '#666666']}); assert.equal(category.id, nested.fields[1].id); assert.equal(category.path.length, 2);
  const unknown = categoryForPaper({...nested.papers[1], fieldIds: []}, nested, {series: ['#111111']}); assert.equal(unknown.id, 'unknown'); assert.equal(networkLegend([category, unknown]).length, 2);
});

test('network zoom is clamped to the shared interactive range', () => { assert.equal(clampNetworkZoom(0.01), 0.2); assert.equal(clampNetworkZoom(9), 3); assert.equal(clampNetworkZoom(1.5), 1.5); });

test('field counts distinguish direct assignment from unique descendant coverage', () => {
  const nested = structuredClone(fixture); nested.fields[1].parentIds = [nested.fields[0].id]; nested.papers[1].fieldIds = [nested.fields[1].id]; nested.papers[1].displayFieldId = nested.fields[1].id; const counts = fieldPaperCounts(nested); assert.deepEqual(counts.get(nested.fields[0].id), {direct: 1, inclusive: 2}); assert.deepEqual(counts.get(nested.fields[1].id), {direct: 1, inclusive: 2});
});

test('decoder fails closed on missing required IDs and ignores unknown fields', () => {
  assert.equal(fixture.papers.length, 2);
  assert.equal(fixture.fixtureUnknownField, undefined);
  assert.equal(Array.isArray(fixture.overview.keywordAggregate?.pairs), true);
  const broken = structuredClone(fixture); delete broken.papers[0].id;
  assert.throws(() => decodeSnapshot(broken), /missing required fields/);
});

test('sampling is deterministic, nested, and retains selected one-hop neighborhood', () => {
  const small = sampleDetail(fixture, {fraction: .25, seed: 'test', selectedIds: ['zotero:fixture-server:0:A1']});
  const repeated = sampleDetail(fixture, {fraction: .25, seed: 'test', selectedIds: ['zotero:fixture-server:0:A1']});
  const full = sampleDetail(fixture, {fraction: 1, seed: 'test'});
  assert.deepEqual(small.papers.map(row => row.id), repeated.papers.map(row => row.id));
  assert.equal(small.papers.some(row => row.id === 'zotero:fixture-server:0:B2'), true);
  assert.equal(full.eligiblePapers, fixture.papers.length);
  assert.equal(full.eligibleEdges, fixture.edges.length);
  assert.equal(stableHash('same'), stableHash('same'));
});

test('selected retention does not expand transitively through a chain', () => {
  const chain = structuredClone(fixture); chain.papers.push({id: 'zotero:fixture-server:0:C3', key: 'C3', libraryID: 0, title: 'Chain', year: 2024, authors: [], keywords: [], fieldIds: ['field:fixture-server:epoch-001:topology'], conceptIds: [], attachmentIds: [], textStatus: 'missing'}); chain.edges.push({id: 'edge:fixture-server:epoch-001:B2:C3:semantic', source: 'zotero:fixture-server:0:B2', target: 'zotero:fixture-server:0:C3', type: 'semantic', directed: false, score: 1, provenance: {provider: 'test', method: 'chain'}, evidenceTier: 'metadata', verified: false});
  const sampled = sampleDetail(chain, {fraction: 0, seed: 'chain', selectedIds: ['zotero:fixture-server:0:A1'], relationTypes: ['semantic']});
  assert.deepEqual(sampled.papers.map(row => row.id).sort(), ['zotero:fixture-server:0:A1', 'zotero:fixture-server:0:B2'].sort());
});

test('overview projection keeps backend aggregate truth independent of sampled detail', () => {
  const overview = overviewProjection(fixture);
  const layout = circularLayout(overview, 640, 520);
  assert.equal(overview.totalPapers, fixture.overview.totalPapers);
  assert.equal(overview.totalEdges, fixture.overview.totalEdges);
  assert.equal(layout.arcs.length, fixture.fields.length);
  assert.equal(layout.links.length, fixture.overview.ribbons.length);
  assert.equal(layout.links[0].value, 1, 'primary display-field allocation counts one edge');
});

test('field filter includes descendants while preserving all memberships', () => {
  const nested = structuredClone(fixture); nested.fields[1].parentIds = [nested.fields[0].id]; nested.papers[1].fieldIds = [nested.fields[1].id];
  const detail = sampleDetail(nested, {fraction: 1, filter: {fieldId: nested.fields[0].id}, relationTypes: ['semantic']});
  assert.equal(detail.eligiblePapers, 2);
  assert.equal(nested.papers[0].fieldIds.length, 2);
});

test('concept keyword edges stay in concept network', () => {
  const concept = structuredClone(fixture); concept.concepts.push({id: 'concept:fixture-server:epoch-001:transport', label: {en: 'Transport'}, aliases: [], source: 'extracted'}); concept.papers[0].conceptIds.push('concept:fixture-server:epoch-001:transport'); concept.edges.push({id: 'edge:fixture-server:epoch-001:kl:transport:keyword', source: 'concept:fixture-server:epoch-001:kl', target: 'concept:fixture-server:epoch-001:transport', type: 'keyword', directed: false, score: 1, provenance: {provider: 'cooccurrence', method: 'unique-paper-count'}, evidenceTier: 'metadata', verified: true, cooccurrenceCount: 1});
  const graph = graphProjection(concept, sampleDetail(concept, {fraction: 1, relationTypes: ['semantic', 'keyword']}));
  assert.equal(graph.conceptEdges.some(edge => edge.data.relation === 'concept-cooccurrence'), true);
});

test('full keyword aggregate supplies focused concept edges without unpacking all pairs', () => {
  const aggregate = structuredClone(fixture); aggregate.edges = aggregate.edges.filter(edge => edge.type !== 'keyword'); aggregate.concepts.push({id: 'concept:fixture-server:epoch-001:transport', label: {en: 'Transport'}, aliases: [], source: 'extracted'}); aggregate.overview.keywordAggregate = {conceptIds: ['concept:fixture-server:epoch-001:kl', 'concept:fixture-server:epoch-001:transport'], pairs: [[0, 1, 7]], encoding: 'concept-index-pairs'};
  const graph = graphProjection(aggregate, sampleDetail(aggregate, {fraction: 1, relationTypes: ['semantic']}), {maxConceptNodes: 2, selectedConceptIds: ['concept:fixture-server:epoch-001:kl']});
  assert.equal(graph.conceptStats.totalEdges, 1);
  assert.equal(graph.conceptEdges.some(edge => edge.data.relation === 'concept-cooccurrence'), true);
});

test('graph projection preserves typed relation direction and actual nodes', () => {
  const detail = sampleDetail(fixture, {fraction: 1, seed: 'graph'});
  const graph = graphProjection(fixture, detail);
  assert.equal(graph.paperNodes.length, detail.papers.length);
  assert.equal(graph.paperEdges.length, detail.edges.length);
  assert.equal(graph.paperEdges.find(edge => edge.data.relation === 'semantic').data.directed, false);
  assert.equal(graph.conceptNodes.length + graph.conceptPaperNodes.length > 0, true);
});

test('reader-facing details resolve category paths and relationship endpoints', async () => {
  const {fieldPath, endpointLabel} = await import('../../frontend/network-style.js');
  const data = structuredClone(fixture); data.fields[1].parentIds = [data.fields[0].id];
  assert.match(fieldPath(data, data.fields[1].id), /\s\/\s/);
  assert.equal(fieldPath(data, 'missing'), 'Unknown field');
  assert.equal(endpointLabel(data, data.papers[0].id), data.papers[0].title);
  assert.equal(endpointLabel(data, 'missing'), 'Unavailable item');
});

test('papers shown in concept network keep their assigned category', () => {
  const graph = graphProjection(fixture, {papers: fixture.papers, edges: fixture.edges}, {categoryPalette: {series: ['#123456']}});
  for (const node of graph.conceptPaperNodes) {
    const paper = fixture.papers.find(item => item.id === node.data.id);
    assert.equal(node.data.categoryId, categoryForPaper(paper, fixture, {series: ['#123456']}).id);
    assert.ok(node.data.categoryColor);
  }
});

test('network legend groups nodes by actual field rather than individual titles', async () => {
  const {networkLegendForNodes} = await import('../../frontend/network-style.js');
  const entries = networkLegendForNodes([{id: 'paper-1', label: 'Title one', categoryId: 'math', categoryLabel: 'Mathematics', categoryColor: '#123456'}, {id: 'paper-2', label: 'Title two', categoryId: 'math', categoryLabel: 'Mathematics', categoryColor: '#123456'}, {id: 'paper-3', label: 'Title three'}]);
  assert.equal(entries.length, 2); const math = entries.find(item => item.id === 'math'); assert.equal(math.label, 'Mathematics'); assert.equal(math.color, '#123456'); assert.equal(math.count, 2); assert.equal(entries.find(item => item.id === 'unknown').color, '#64748b');
});

test('paper concept seeds do not expand another hop; explicit concept focus adds only one hop', () => {
  const data = structuredClone(fixture); data.concepts = ['x', 'y', 'z'].map(id => ({...data.concepts[0], id: `concept:${id}`, label: {en: id}})); const ids = data.concepts.map(item => item.id);
  data.edges = [{id:'xy', source:ids[0], target:ids[1], type:'keyword'}, {id:'yz', source:ids[1], target:ids[2], type:'keyword'}]; data.overview.keywordAggregate = {conceptIds:[], pairs:[], encoding:'concept-index-pairs'};
  const detail = {papers:[], edges:[]};
  const paperFocused = graphProjection(data, detail, {maxConceptNodes:1, mandatoryConceptIds:[ids[0]]});
  assert.deepEqual(paperFocused.conceptNodes.map(node => node.data.id), [ids[0]]);
  const conceptFocused = graphProjection(data, detail, {maxConceptNodes:2, selectedConceptIds:[ids[0]]});
  assert.deepEqual(new Set(conceptFocused.conceptNodes.map(node => node.data.id)), new Set([ids[0], ids[1]]));
});

test('high-degree concept focus is bounded with truthful neighbor counts', () => {
  const data=structuredClone(fixture); data.concepts=Array.from({length:1410},(_,i)=>({...data.concepts[0],id:`c${i}`,label:{en:`Concept ${i}`}}));
  data.edges=[]; data.overview.keywordAggregate={conceptIds:data.concepts.map(c=>c.id),pairs:Array.from({length:1409},(_,i)=>[0,i+1,i+1]),encoding:'concept-index-pairs'};
  const graph=graphProjection(data,{papers:[],edges:[]},{maxConceptNodes:105,selectedConceptIds:['c0']});
  assert.equal(graph.conceptNodes.length,105); assert.equal(graph.conceptStats.neighborCandidates,1409); assert.equal(graph.conceptStats.visibleNeighbors,104); assert.equal(graph.conceptStats.hiddenNeighbors,1305); assert.ok(graph.conceptNodes.some(node=>node.data.id==='c1409'));
});

test('filtered concept candidates come from all eligible papers, not unrelated library concepts', () => {
  const data=structuredClone(fixture); data.concepts=['x','y','z'].map(id=>({...data.concepts[0],id,label:{en:id}})); data.edges=[]; data.overview.keywordAggregate={conceptIds:[],pairs:[],encoding:'concept-index-pairs'};
  const graph=graphProjection(data,{papers:[],edges:[],filterActive:true,eligibleConceptIds:['x','y']},{maxConceptNodes:3});
  assert.deepEqual(new Set(graph.conceptNodes.map(node=>node.data.id)),new Set(['x','y'])); assert.equal(graph.conceptStats.totalNodes,3); assert.equal(graph.conceptStats.eligibleNodes,2);
});

test('high-degree concept and long paper concept lists respect detail budgets', () => {
  const data=structuredClone(fixture); data.concepts=Array.from({length:210},(_,i)=>({...data.concepts[0],id:`c${i}`,label:{en:`Concept ${i}`}}));
  data.papers=Array.from({length:780},(_,i)=>({...data.papers[0],id:`p${i}`,conceptIds:['c0']})); data.edges=[];data.overview.keywordAggregate={conceptIds:[],pairs:[],encoding:'concept-index-pairs'};
  const sampled=sampleDetail(data,{fraction:.35,filter:{conceptId:'c0'}}); const graph=graphProjection(data,sampled,{maxConceptNodes:105,selectedConceptIds:['c0']});
  assert.equal(sampled.eligiblePapers,780); assert.equal(sampled.papers.length,273); assert.equal(graph.conceptPaperNodes.length,150); assert.equal(graph.conceptStats.directPaperCandidates,780);
  const many=graphProjection(data,{papers:[],edges:[]},{maxConceptNodes:105,mandatoryConceptIds:data.concepts.map(c=>c.id)}); assert.equal(many.conceptNodes.length,105);
});

test('category exploration assigns eligible papers once by primary field', async () => {
  const {categoryGroups}=await import('../../frontend/category-explorer.js'); const data=structuredClone(fixture);
  const extra={...data.papers[0],id:'paper:extra',displayFieldId:null,fieldIds:[]};data.papers.push(extra);
  const ids=[data.papers[0].id,extra.id];const groups=categoryGroups(data,ids);const shown=groups.flatMap(group=>group.papers.map(paper=>paper.id));
  assert.deepEqual(new Set(shown),new Set(ids));assert.equal(shown.length,2);assert.equal(groups.find(group=>group.id==='__unclassified__').papers[0].id,extra.id);
});

test('equal depth and weight prefer the primary display field across graph views', () => {
  const data=structuredClone(fixture); const paper={...data.papers[0],fieldIds:[data.fields[0].id,data.fields[1].id],displayFieldId:data.fields[1].id};
  data.memberships=data.memberships.filter(row=>row.paperId!==paper.id);const category=categoryForPaper(paper,data,{series:['#123456']});
  assert.equal(category.id,data.fields[1].id);
});

test('density matrix text has readable contrast for all ramp bins and zero cells', async () => {
  const {densityRamp,densityForeground}=await import('../../frontend/density-view.js');
  const luminance=hex=>{const c=[1,3,5].map(i=>parseInt(hex.slice(i,i+2),16)/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4);return .2126*c[0]+.7152*c[1]+.0722*c[2]};
  for(const bg of [...densityRamp().map(item=>item.hex),'#d1d5db']) { const fg=densityForeground(bg); const x=luminance(bg),y=luminance(fg); assert.ok((Math.max(x,y)+.05)/(Math.min(x,y)+.05)>=4.5); }
});

test('field presentation matches paper and explorer colors across order and rename', async () => {
  const {fieldPresentation}=await import('../../frontend/network-style.js');const {categoryGroups}=await import('../../frontend/category-explorer.js');
  const data=structuredClone(fixture);data.fields[1].parentIds=[data.fields[0].id];const field=data.fields[1];const paper={...data.papers[0],fieldIds:[field.id],displayFieldId:field.id};data.papers=[paper];
  const palette={series:['#102030','#204060','#306090','#4080c0','#50a0e0','#60c0ff']};const style=fieldPresentation(field,data,palette);
  assert.equal(categoryForPaper(paper,data,palette).color,style.color);assert.equal(categoryGroups(data,[paper.id],palette).find(group=>group.id===field.id).color,style.color);assert.equal(style.depth,1);
  const reordered={...data,fields:[...data.fields].reverse().map(row=>({...row,label:{en:'renamed'}}))};assert.equal(fieldPresentation(reordered.fields.find(row=>row.id===field.id),reordered,palette).color,style.color);
});
