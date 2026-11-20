import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {contrastRatio} from '../../frontend/network-style.js';
import {HUE_SLOTS, assignCloudHues, cloudTones, cvdDeltaE, hexToOklab, neighbourStats, oklabDeltaE, oklchToLinear, oklchToOklab, separateNeighbours, simulateCvd, slotHue} from '../../frontend/cloud-colors.js';

const palettes = JSON.parse(await readFile(new URL('../../assets/palettes.json', import.meta.url))).palettes;
const contexts = palettes.flatMap(palette => ['light', 'dark'].map(theme => ({id: palette.id, theme, ...palette.themes[theme]})));
const tonesFor = ctx => Array.from({length: HUE_SLOTS}, (_, slot) => cloudTones({hue: slotHue(slot, ctx.id), theme: ctx.theme, panelHex: ctx.panel, textHex: ctx.text}));
const lab = tone => oklchToOklab(tone.oklch);
const hueOf = ([, a, b]) => (Math.atan2(b, a) * 180 / Math.PI + 360) % 360;
const hueGap = (a, b) => { const d = Math.abs(a - b) % 360; return Math.min(d, 360 - d); };
const report = (name, rows) => console.log(`# ${name}: ${rows.map(([key, value]) => `${key}=${value.toFixed(4)}`).join(' ')}`);
const lcg = seed => { let state = seed >>> 0; return () => { state = (Math.imul(state, 1664525) + 1013904223) >>> 0; return state / 4294967296; }; };
const syntheticId = n => `collection:synthetic:0:C${String(n).padStart(2, '0')}`;
const syntheticClouds = random => { const count = 8 + Math.floor(random() * 9); const ids = new Set(); while (ids.size < count) ids.add(Math.floor(random() * 100)); return {clouds: [...ids].map(n => ({id: syntheticId(n), size: 1 + Math.floor(random() * 200), unfiled: false})), ids}; };

test('AE-1: stroke and point tones 4 slots apart stay distinguishable (24 slots x 3 palettes x 2 themes)', () => {
  const rows = [];
  for (const ctx of contexts) {
    const tones = tonesFor(ctx); let stroke = Infinity; let point = Infinity;
    for (let slot = 0; slot < HUE_SLOTS; slot += 1) { const other = tones[(slot + 4) % HUE_SLOTS]; stroke = Math.min(stroke, oklabDeltaE(lab(tones[slot].stroke), lab(other.stroke)), oklabDeltaE(tones[slot].stroke.hex, other.stroke.hex)); point = Math.min(point, oklabDeltaE(lab(tones[slot].point), lab(other.point)), oklabDeltaE(tones[slot].point.hex, other.point.hex)); }
    assert.ok(stroke >= 0.08, `${ctx.id}/${ctx.theme} stroke dE ${stroke}`); assert.ok(point >= 0.07, `${ctx.id}/${ctx.theme} point dE ${point}`);
    rows.push([`${ctx.id}/${ctx.theme}:stroke`, stroke], [`${ctx.id}/${ctx.theme}:point`, point]);
  }
  report('AE-1 min dE (60deg)', rows);
});

test('AE-2: shared lightness per tone, in sRGB gamut, chroma-only reduction keeps hue', () => {
  let maxShift = 0; let maxRoundTrip = 0;
  for (const ctx of contexts) {
    const tones = tonesFor(ctx); const all = tone => [tone.fill, tone.stroke, tone.point, ...tone.pointVariants, tone.selectedFill];
    for (const role of ['fill', 'stroke', 'point', 'selectedFill']) { const Ls = tones.map(tone => tone[role].oklch[0]); assert.equal(new Set(Ls).size, 1, `${ctx.id}/${ctx.theme} ${role} L differs`); const hexL = tones.map(tone => hexToOklab(tone[role].hex)[0]); assert.ok(Math.max(...hexL) - Math.min(...hexL) < 0.006, `${ctx.id}/${ctx.theme} ${role} rendered L spread`); }
    for (const step of [0, 1, 2]) assert.equal(new Set(tones.map(tone => tone.pointVariants[step].oklch[0])).size, 1);
    tones.forEach((tone, slot) => { for (const t of all(tone)) {
      const linear = oklchToLinear(...t.oklch); assert.ok(linear.every(value => value >= 0 && value <= 1), `${ctx.id}/${ctx.theme} slot ${slot} out of gamut`);
      const roundTrip = oklabDeltaE(lab(t), t.hex); maxRoundTrip = Math.max(maxRoundTrip, roundTrip); assert.ok(roundTrip < 0.005, `${ctx.id}/${ctx.theme} slot ${slot} hex drifts ${roundTrip}`);
      if (t.oklch[1] >= 0.08) { const shift = hueGap(hueOf(lab(t)), slotHue(slot, ctx.id)); maxShift = Math.max(maxShift, shift); assert.ok(shift <= 0.5, `${ctx.id}/${ctx.theme} slot ${slot} hue shift ${shift}`); }
    } });
  }
  report('AE-2', [['maxHueShiftDeg', maxShift], ['maxHexRoundTripDE', maxRoundTrip]]);
});

test('AE-3: contrast and fill visibility for every slot, palette and theme', () => {
  const rows = [];
  for (const ctx of contexts) {
    const min = {strokePanel: Infinity, strokeFill: Infinity, pointFill: Infinity, label: Infinity, fillPanel: Infinity};
    tonesFor(ctx).forEach((tone, slot) => {
      const where = `${ctx.id}/${ctx.theme} slot ${slot}`; assert.equal(tone.label.background, tone.fill.hex); assert.equal(tone.label.text, ctx.text);
      const values = {strokePanel: contrastRatio(tone.stroke.hex, ctx.panel), strokeFill: contrastRatio(tone.stroke.hex, tone.fill.hex), pointFill: Math.min(...tone.pointVariants.map(variant => contrastRatio(variant.hex, tone.fill.hex))), label: contrastRatio(tone.label.text, tone.label.background), fillPanel: oklabDeltaE(tone.fill.hex, ctx.panel)};
      assert.ok(values.strokePanel >= 3, `${where} stroke/panel ${values.strokePanel}`); assert.ok(values.strokeFill >= 3, `${where} stroke/fill ${values.strokeFill}`);
      assert.ok(values.pointFill >= 3, `${where} point/fill ${values.pointFill}`); assert.ok(values.label >= 4.5, `${where} label ${values.label}`); assert.ok(values.fillPanel >= 0.035, `${where} fill/panel dE ${values.fillPanel}`);
      for (const key of Object.keys(min)) min[key] = Math.min(min[key], values[key]);
    });
    rows.push(...Object.entries(min).map(([key, value]) => [`${ctx.id}/${ctx.theme}:${key}`, value]));
  }
  report('AE-3 minima', rows);
});

test('point variants step one direction only and selected fill is more chromatic', () => {
  for (const ctx of contexts) {
    const tone = tonesFor(ctx)[3]; const Ls = tone.pointVariants.map(variant => variant.oklch[0]); const direction = ctx.theme === 'dark' ? 1 : -1;
    assert.ok((Ls[1] - Ls[0]) * direction > 0 && (Ls[2] - Ls[1]) * direction > 0, `${ctx.id}/${ctx.theme} variants ${Ls}`);
    assert.ok(tone.selectedFill.oklch[1] > tone.fill.oklch[1]); assert.equal(tone.selectedFill.oklch[0], tone.fill.oklch[0]);
  }
});

test('unfiled cloud is achromatic in every tone', () => {
  const assignment = assignCloudHues([{id: 'collection:synthetic:0:C01'}, {id: 'cloud:unfiled', unfiled: true}]);
  assert.deepEqual(assignment.get('cloud:unfiled'), {slot: null, hue: null, unfiled: true});
  for (const ctx of contexts) {
    const tone = cloudTones({hue: null, theme: ctx.theme, panelHex: ctx.panel, unfiled: true});
    for (const t of [tone.fill, tone.stroke, tone.point, ...tone.pointVariants, tone.selectedFill]) { const [, a, b] = hexToOklab(t.hex); assert.ok(t.oklch[1] <= 0.02 && Math.hypot(a, b) <= 0.02, `${ctx.id}/${ctx.theme} unfiled chroma`); }
  }
});

test('AE-7: assignment is order-, size- and palette-slot-invariant with unique slots', () => {
  const random = lcg(7); const {clouds} = syntheticClouds(random); clouds.push({id: 'cloud:unfiled', size: 40, unfiled: true});
  const base = assignCloudHues(clouds);
  for (let round = 0; round < 10; round += 1) { const shuffled = [...clouds].sort(() => random() - 0.5); assert.deepEqual(assignCloudHues(shuffled), base); }
  assert.deepEqual(assignCloudHues(clouds.map(cloud => ({...cloud, size: cloud.size * 7 + 3}))), base);
  const slots = [...base.values()].filter(value => !value.unfiled).map(value => value.slot); assert.equal(new Set(slots).size, slots.length);
  const calm = assignCloudHues(clouds, {paletteId: 'calm'}); for (const [id, value] of base) assert.equal(calm.get(id).slot, value.slot);
  const full = Array.from({length: 24}, (_, n) => ({id: syntheticId(n)})); assert.equal(new Set([...assignCloudHues(full).values()].map(value => value.slot)).size, 24);
});

test('AE-7: more than 24 clouds reuse slots evenly', () => {
  const assignment = assignCloudHues(Array.from({length: 30}, (_, n) => ({id: syntheticId(n)}))); const usage = new Map();
  for (const {slot} of assignment.values()) usage.set(slot, (usage.get(slot) || 0) + 1);
  assert.equal(usage.size, 24); assert.ok(Math.max(...usage.values()) <= 2);
});

test('AE-7: adding one cloud changes at most 2 existing hues in >= 95 of 100 seeded runs', () => {
  const histogram = new Map(); let violations = 0;
  for (let seed = 1; seed <= 100; seed += 1) {
    const random = lcg(seed); const {clouds, ids} = syntheticClouds(random); let extra; do extra = Math.floor(random() * 100); while (ids.has(extra));
    const before = assignCloudHues(clouds); const after = assignCloudHues([...clouds, {id: syntheticId(extra), size: 5}]);
    const changed = [...before].filter(([id, value]) => after.get(id).slot !== value.slot).length; histogram.set(changed, (histogram.get(changed) || 0) + 1); if (changed > 2) violations += 1;
  }
  console.log(`# AE-7 add-one changed-hue histogram: ${JSON.stringify(Object.fromEntries([...histogram].sort((a, b) => a[0] - b[0])))} violations=${violations}`);
  assert.ok(violations <= 5, `violations ${violations}`);
});

test('CVD simulation keeps greys and collapses red/green under deuteranopia', () => {
  for (const grey of ['#000000', '#808080', '#ffffff']) for (const type of ['protanopia', 'deuteranopia']) assert.ok(oklabDeltaE(simulateCvd(grey, type), grey) < 0.01, `${type} ${grey}`);
  const red = '#d62728'; const green = '#2ca02c'; const normal = oklabDeltaE(red, green);
  assert.ok(oklabDeltaE(simulateCvd(red, 'deuteranopia'), simulateCvd(green, 'deuteranopia')) < normal / 2);
  assert.ok(cvdDeltaE(red, green) < normal / 2); assert.ok(cvdDeltaE('#1f77b4', '#ff7f0e') > 0.1);
  assert.throws(() => simulateCvd(red, 'tritanopia'));
});

test('neighbourStats measures hand-built adjacency without changing the assignment', () => {
  const clouds = [{id: 'collection:synthetic:0:C01', size: 30}, {id: 'collection:synthetic:0:C02', size: 10}, {id: 'collection:synthetic:0:C03', size: 5}, {id: 'cloud:unfiled', size: 4, unfiled: true}];
  // C01-C02 gap 20 (adjacent), C02-C03 gap 70 (adjacent at gap 40), C01-C03 gap 230 (not), unfiled ignored.
  const centers = new Map([['collection:synthetic:0:C01', {cx: 0, cy: 0, r: 50}], ['collection:synthetic:0:C02', {cx: 100, cy: 0, r: 30}], ['collection:synthetic:0:C03', {cx: 100, cy: 120, r: 20}], ['cloud:unfiled', {cx: 0, cy: 60, r: 5}]]);
  const assignment = new Map([['collection:synthetic:0:C01', {slot: 0, hue: 0}], ['collection:synthetic:0:C02', {slot: 2, hue: 30}], ['collection:synthetic:0:C03', {slot: 10, hue: 150}], ['cloud:unfiled', {slot: null, hue: null, unfiled: true}]]);
  const frozen = JSON.stringify([...assignment]); const stats = neighbourStats(clouds, centers, assignment);
  assert.equal(stats.adjacentPairs, 2); assert.equal(stats.closePairs, 1); assert.equal(JSON.stringify([...assignment]), frozen);
  const near = cloudTones({hue: 0}); const nearer = cloudTones({hue: 30});
  assert.ok(Math.abs(stats.minStrokeDeltaE - oklabDeltaE(lab(near.stroke), lab(nearer.stroke))) < 1e-12); assert.ok(stats.minPointDeltaE > 0 && stats.minCvdPointDeltaE > 0);
  assert.equal(neighbourStats(clouds, centers, assignment, {gap: 5}).adjacentPairs, 0);
  const separated = separateNeighbours(clouds, centers, assignment); assert.equal(separated.get('collection:synthetic:0:C01').slot, 0);
  assert.equal(neighbourStats(clouds, centers, separated).closePairs, 0); assert.equal(JSON.stringify([...assignment]), frozen);
});
