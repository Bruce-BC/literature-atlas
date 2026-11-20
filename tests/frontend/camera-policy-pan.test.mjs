import test from 'node:test';
import assert from 'node:assert/strict';
import {AtlasCameraPolicy, PAN_MIN_VISIBLE, ZOOM_RANGE, clampPan} from '../../frontend/camera-policy.js';

const viewport = {width: 800, height: 600};
const bbox = {x1: 0, y1: 0, x2: 400, y2: 300};
// Overlap of the screen-space bbox with the viewport on one axis (screen = model * zoom + pan).
const overlap = (lo, hi, zoom, pan, size) => Math.min(hi * zoom + pan, size) - Math.max(lo * zoom + pan, 0);
const visible = ({viewport: v, bbox: b, zoom}, pan) => ({x: overlap(b.x1, b.x2, zoom, pan.x, v.width), y: overlap(b.y1, b.y2, zoom, pan.y, v.height)});
const close = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-9, `${actual} != ${expected}`);

test('clampPan is exported on the AtlasCameraPolicy object', () => {
  assert.equal(AtlasCameraPolicy.clampPan, clampPan); assert.equal(PAN_MIN_VISIBLE, 48);
});

test('in-range pan is returned unchanged', () => {
  assert.deepEqual(clampPan({viewport, bbox, zoom: 1, pan: {x: 100, y: 50}}), {x: 100, y: 50});
});

test('bounds each direction: half the bbox (>= 48 px) stays visible', () => {
  const input = {viewport, bbox, zoom: 1}; // 400 x 300 bbox in an 800 x 600 viewport: 200 / 150 px must stay visible
  const right = clampPan({...input, pan: {x: 1e6, y: 0}}); close(right.x, viewport.width - 200); close(visible(input, right).x, 200);
  const left = clampPan({...input, pan: {x: -1e6, y: 0}}); close(left.x, 200 - 400); close(visible(input, left).x, 200);
  const down = clampPan({...input, pan: {x: 0, y: 1e6}}); close(down.y, viewport.height - 150); close(visible(input, down).y, 150);
  const up = clampPan({...input, pan: {x: 0, y: -1e6}}); close(up.y, 150 - 300); close(visible(input, up).y, 150);
  const corner = clampPan({...input, pan: {x: -1e9, y: 1e9}}); const seen = visible(input, corner); close(seen.x, 200); close(seen.y, 150);
  for (const pan of [{x: 1e6, y: 1e6}, {x: -1e6, y: -1e6}, {x: 1e6, y: -1e6}]) { const any = visible(input, clampPan({...input, pan})); assert.ok(any.x >= 48 && any.y >= 48, 'AC-14 floor'); }
});

test('a bbox larger than the viewport always covers it, and its edges stay reachable', () => {
  const input = {viewport, bbox: {x1: 0, y1: 0, x2: 4000, y2: 3000}, zoom: 1};
  for (const pan of [{x: 1e6, y: 1e6}, {x: -1e6, y: -1e6}]) { const seen = visible(input, clampPan({...input, pan})); close(seen.x, 800); close(seen.y, 600); }
  assert.deepEqual(clampPan({...input, pan: {x: -3200, y: -2400}}), {x: -3200, y: -2400}, 'the far bbox corner can be brought to the viewport corner');
  assert.deepEqual(clampPan({...input, pan: {x: -1000, y: -1000}}), {x: -1000, y: -1000}, 'panning inside a large graph is free');
  const mid = {viewport, bbox: {x1: 0, y1: 0, x2: 1000, y2: 700}, zoom: 1}; // between 1x and 2x the viewport: half the bbox (centre on screen)
  const seen = visible(mid, clampPan({...mid, pan: {x: 1e6, y: -1e6}})); close(seen.x, 500); close(seen.y, 350);
});

test('bbox offset from the origin is honoured', () => {
  const input = {viewport, bbox: {x1: 1000, y1: -500, x2: 1200, y2: -300}, zoom: 2};
  for (const pan of [{x: 1e7, y: 1e7}, {x: -1e7, y: -1e7}]) { const seen = visible(input, clampPan({...input, pan})); close(seen.x, 200); close(seen.y, 200); } // 400 x 400 screen px
});

test('NaN or missing input yields a finite, safe pan', () => {
  assert.deepEqual(clampPan(), {x: 0, y: 0});
  assert.deepEqual(clampPan({viewport, bbox, zoom: NaN, pan: {x: 12, y: -7}}), {x: 12, y: -7}, 'invalid zoom keeps a finite pan');
  assert.deepEqual(clampPan({viewport: {width: 0, height: 0}, bbox, zoom: 1, pan: {x: 5e6, y: 5}}), {x: 5e6, y: 5}, 'hidden (zero-size) container never rewrites the camera');
  assert.deepEqual(clampPan({viewport, bbox: null, zoom: 1, pan: {x: 3, y: 4}}), {x: 3, y: 4}, 'no nodes: nothing to clamp against');
  assert.deepEqual(clampPan({viewport, bbox: {x1: NaN, y1: 0, x2: 1, y2: 1}, zoom: 1, pan: {x: Infinity, y: NaN}}), {x: 0, y: 0});
  const centred = clampPan({viewport, bbox, zoom: 1, pan: {x: NaN, y: undefined}});
  assert.deepEqual(centred, {x: 200, y: 150}, 'non-finite pan with a valid bbox centres the bbox');
  assert.ok(Number.isFinite(centred.x) && Number.isFinite(centred.y));
});

test('zoom at the 0.2 floor is honoured (bbox scales to screen px)', () => {
  const input = {viewport, bbox: {x1: -2000, y1: -2000, x2: 2000, y2: 2000}, zoom: ZOOM_RANGE[0]}; // 800 x 800 screen px
  for (const pan of [{x: 1e6, y: -1e6}, {x: -1e6, y: 1e6}]) { const seen = visible(input, clampPan({...input, pan})); close(seen.x, 400); close(seen.y, 400); }
  const small = {viewport, bbox: {x1: 0, y1: 0, x2: 100, y2: 100}, zoom: ZOOM_RANGE[0]}; // 20 x 20 screen px: the whole bbox counts
  const seen = visible(small, clampPan({...small, pan: {x: 1e6, y: 1e6}})); close(seen.x, 20); close(seen.y, 20);
});

test('single node (zero-size bbox) is kept inside the viewport', () => {
  const input = {viewport, bbox: {x1: 50, y1: 70, x2: 50, y2: 70}, zoom: 1.5};
  for (const pan of [{x: 1e6, y: 1e6}, {x: -1e6, y: -1e6}]) {
    const next = clampPan({...input, pan}); const sx = 50 * 1.5 + next.x; const sy = 70 * 1.5 + next.y;
    assert.ok(sx >= 0 && sx <= viewport.width && sy >= 0 && sy <= viewport.height, `node at ${sx},${sy}`);
  }
});

test('the 48 px floor is min(48, bbox width, bbox height)', () => {
  const thin = {viewport, bbox: {x1: 0, y1: 0, x2: 400, y2: 20}, zoom: 1}; // 20 px tall: floor 20 px, so y keeps the whole 20 px, x keeps half (200)
  const seen = visible(thin, clampPan({...thin, pan: {x: -1e6, y: -1e6}})); close(seen.x, 200); close(seen.y, 20);
  const small = {viewport, bbox: {x1: 0, y1: 0, x2: 30, y2: 40}, zoom: 1}; // 30 px wide: floor 30 px on both axes (above half of 40)
  const seenSmall = visible(small, clampPan({...small, pan: {x: 1e6, y: 1e6}})); close(seenSmall.x, 30); close(seenSmall.y, 30);
  const flat = {viewport, bbox: {x1: 0, y1: 0, x2: 90, y2: 200}, zoom: 1}; // floor 48 beats half of 90 (45)
  close(visible(flat, clampPan({...flat, pan: {x: 1e6, y: 0}})).x, 48);
  const tiny = {viewport: {width: 20, height: 600}, bbox, zoom: 1}; // a viewport narrower than 48 px caps the requirement
  close(visible(tiny, clampPan({...tiny, pan: {x: 1e6, y: 0}})).x, 20);
});

test('clampPan is idempotent', () => {
  for (const pan of [{x: 1e6, y: -1e6}, {x: -3e3, y: 77}, {x: 100, y: 50}, {x: NaN, y: 1e9}]) {
    for (const input of [{viewport, bbox, zoom: 1}, {viewport, bbox: {x1: 50, y1: 70, x2: 50, y2: 70}, zoom: 0.2}, {viewport, bbox, zoom: 3}]) {
      const once = clampPan({...input, pan}); assert.deepEqual(clampPan({...input, pan: once}), once);
    }
  }
});
