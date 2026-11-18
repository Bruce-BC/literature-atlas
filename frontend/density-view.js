// A sequential OKLCH blue ramp with static sRGB fallbacks for canvas renderers.
const RAMP = [
  {oklch:'oklch(0.91 0.04 250)', hex:'#cee4fc'},
  {oklch:'oklch(0.80 0.09 250)', hex:'#91c3f6'},
  {oklch:'oklch(0.68 0.15 250)', hex:'#449df0'},
  {oklch:'oklch(0.55 0.17 250)', hex:'#0073cf'},
  {oklch:'oklch(0.42 0.13 250)', hex:'#004e90'}
];
export function densityColor(value, max) { const ratio = max > 0 ? Math.max(0, Math.min(1, Number(value) / max)) : 0; return RAMP[Math.min(RAMP.length-1,Math.floor(ratio * RAMP.length))].hex; }
export function densityForeground(hex) { const channels = [1,3,5].map(index => parseInt(hex.slice(index,index+2),16)/255).map(value => value <= .04045 ? value/12.92 : ((value+.055)/1.055)**2.4); const luminance=.2126*channels[0]+.7152*channels[1]+.0722*channels[2]; return (luminance+.05)/.05 >= 1.05/(luminance+.05) ? '#000000' : '#ffffff'; }
export function densityRamp() { return RAMP.map(entry => ({...entry})); }
export function renderDensityPanel(container, density) {
  container.replaceChildren(); const description=document.createElement('p'); description.textContent=`${density.relationType} · ${density.counts.papers} eligible papers · ${density.counts.edges} observed links / ${density.counts.possibleEdges} possible · density ${(density.density*100).toFixed(2)}%`; container.append(description);
  const note=document.createElement('p');note.className='atlas-note';note.textContent='Computed from all eligible papers, independent of the detail sample. The matrix includes categories containing eligible papers. Zero means no observed link in this library; source coverage can be incomplete.';container.append(note);
  const legend=document.createElement('div');legend.className='atlas-density-legend';legend.setAttribute('aria-label','Connection density, low to high');legend.append('Low ');for(const color of RAMP){const swatch=document.createElement('span');swatch.className='atlas-density-swatch';swatch.style.backgroundColor=color.hex;legend.append(swatch)}legend.append(' High');container.append(legend);
  const wrap=document.createElement('div');wrap.className='atlas-density-table-wrap';const table=document.createElement('table');table.id='atlas-density-matrix';const head=document.createElement('tr');head.append(document.createElement('th'));for(const category of density.categories){const th=document.createElement('th');th.scope='col';th.textContent=category.label;head.append(th)}const thead=document.createElement('thead');thead.append(head);table.append(thead);const body=document.createElement('tbody');
  const cells=new Map(density.categoryMatrix.cells.map(cell=>[`${cell.sourceCategoryId}\u0000${cell.targetCategoryId}`,cell]));const max=Math.max(0,...density.categoryMatrix.cells.map(cell=>cell.density)); const domain=document.createElement('p');domain.className='atlas-note';domain.textContent=`Matrix scale: 0% to ${(max*100).toFixed(2)}% maximum observed category-pair density. Grey = zero observed; N/A = no possible pair.`;container.append(domain);
  for(const row of density.categories){const tr=document.createElement('tr');const th=document.createElement('th');th.scope='row';th.textContent=row.label;tr.append(th);for(const col of density.categories){const cell=cells.get(`${row.id}\u0000${col.id}`);const td=document.createElement('td');td.textContent=cell?.possible ? `${(cell.density*100).toFixed(1)}%` : 'N/A';td.title=cell?`${cell.numerator} observed / ${cell.possible} possible`:'';const background=cell?.possible && cell.density>0 ? densityColor(cell.density,max) : '#d1d5db';td.style.backgroundColor=background;td.style.color=densityForeground(background);tr.append(td)}body.append(tr)}table.append(body);wrap.append(table);container.append(wrap);
}
