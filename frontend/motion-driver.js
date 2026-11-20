import {buildTimeline, cameraDecision, coalesceInput, forcedFinishAt, interruption, returnTarget, transitionKind} from './motion.js';
import {panelGhost} from './cloud-panel.js';
import {NETWORK_ZOOM_RANGE} from './network-style.js';

/**
 * Thin DOM/RAF driver of the collection-map transitions (plan motion-and-edges §3).  Every decision comes from the pure
 * `motion.js`; this module only reads the committed state, captures what the commit replaces (previous frame, previous
 * panel nodes, previous lines) and runs the decorations.
 *
 * Contract with the app (main.js):
 *  - `begin(source, {origin})` is called synchronously in the input handler BEFORE the first dispatch: it removes the
 *    in-flight transition at once (no queue), captures the old frame (T5: synchronous drawImage of the G6 layer canvases,
 *    never toDataURL), and sets `data-motion-state="running"` on the map container.  A second begin of the same input
 *    (a point click dispatches twice) joins it.
 *  - The app then commits the real state exactly as without motion (view-state, G6 queue, panel DOM, focus) and calls
 *    `play()` at the end of `renderAll`, which decides the camera (forced layout read after the panel DOM), builds the
 *    timeline and starts the decorations.
 *  - `reset()` (snapshot replacement, overview teardown) removes everything at once without animation.
 * Idle (`data-motion-state="idle"`) = no tween in the G6 wrapper AND every animation in the map row subtree (overlay,
 * panel, old-frame image, panel ghosts) finished (each `finished`, rejection = finished; never `transitionend`) AND the
 * wrapper's update queue idle.  The forced finish (timeline end + 100 ms) removes decorations and ends the tween; idle
 * still waits for the queue.  Reduced motion: zero timeline, no decorations, no image, instant (still clamped) camera.
 */
export const prefersReducedMotion = (win = globalThis) => Boolean(win?.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches);

export function createMotionDriver({container, panel, row, context}) {
  const doc = container.ownerDocument; const win = doc.defaultView || globalThis; container.dataset.motionState = 'idle';
  let pending = null; let active = null; let lastTransition = null; let settleToken = 0;
  const stats = {transitions: 0, coalesced: 0, interrupted: 0, forced: 0, resets: 0, oldFrameMs: null};
  const now = () => win.performance?.now?.() ?? Date.now();

  const pointOf = (projection, id) => { const point = projection?.cloudLayout?.points?.find(row => row.id === id); return point ? {x: point.x, y: point.y} : null; };
  /** The selected paper's focus point: the clicked replica, else its replica in the selected cloud (the engine's line start), else its first replica. */
  const focusPoint = (projection, {paperId, cloudId, pointId}) => {
    if (!paperId || !projection) return null; if (pointId) { const point = pointOf(projection, pointId); if (point) return point; }
    const points = (projection.nodeIdsByPaper?.get(paperId) || []).map(id => projection.cloudLayout.points.find(row => row.id === id)).filter(Boolean);
    const point = points.find(row => row.cloudId === cloudId) || points[0]; return point ? {x: point.x, y: point.y} : null;
  };
  const stateOf = ctx => ({cloudId: ctx.cloudId ?? null, paperId: ctx.paperId ?? null, subItemId: ctx.subItemId ?? null, pointId: ctx.pointId ?? null});
  const boxIn = (node, parent) => { const a = node.getBoundingClientRect(); const b = parent.getBoundingClientRect(); return {left: a.left - b.left, top: a.top - b.top, width: a.width, height: a.height, beside: a.left >= container.getBoundingClientRect().right - 1}; };

  /** T5: the four G6 layer canvases drawn synchronously into one canvas at their pixel size (devicePixelRatio included). */
  const captureOldFrame = wrapper => {
    const layers = [...container.querySelectorAll('canvas')].filter(layer => layer.width > 0 && layer.height > 0); const camera = wrapper?.camera?.(); if (!layers.length || !camera) return null;
    const started = now(); const image = doc.createElement('canvas'); image.width = layers[0].width; image.height = layers[0].height; const context2d = image.getContext('2d'); if (!context2d) return null;
    for (const layer of layers) context2d.drawImage(layer, 0, 0, image.width, image.height);
    image.className = 'atlas-motion-frame'; image.setAttribute('aria-hidden', 'true'); stats.oldFrameMs = Math.round((now() - started) * 100) / 100;
    return {image, camera, box: {left: container.offsetLeft, top: container.offsetTop, width: container.clientWidth, height: container.clientHeight}};
  };
  /** The old frame follows the camera tween with the SAME camera value: screen = (s0 - p0) * z / z0 + p. */
  const registerFrame = (frame, camera) => { if (!frame?.image.isConnected) return; const scale = camera.zoom / frame.camera.zoom; frame.image.style.transform = `matrix(${scale}, 0, 0, ${scale}, ${camera.x - frame.camera.x * scale}, ${camera.y - frame.camera.y * scale})`; };

  const removeActive = () => {
    const current = active; active = null; if (!current) return;
    clearTimeout(current.forced); for (const anim of current.anims) { try { anim.cancel(); } catch { /* gone */ } } for (const node of current.nodes) node.remove(); current.overlay?.clearDecorations();
  };
  const interrupt = (cause, ctx = context()) => { const plan = interruption(cause); if (active) stats.interrupted += cause === 'input' ? 1 : 0; if (plan.tween === 'jump') ctx?.wrapper?.finishTween('jump'); removeActive(); };

  const settle = () => {
    const token = settleToken += 1; const again = () => { if (token === settleToken) settle(); };
    if (pending) return;
    const ctx = context(); const wrapper = ctx?.wrapper;
    if (wrapper?.tweening) { wrapper.whenTween().then(again); return; }
    const running = row.getAnimations({subtree: true}).filter(anim => anim.playState !== 'finished' && anim.playState !== 'idle');
    if (running.length) { Promise.all(running.map(anim => anim.finished.catch(() => null))).then(again); return; }
    if (active) removeActive(); // every animation finished: drop the decoration elements now (no fill-forwards effect survives idle)
    if (ctx?.overlay?.decorationCount?.()) ctx.overlay.clearDecorations();
    if (wrapper && container.dataset.updateState === 'pending') { Promise.resolve(wrapper.whenIdle()).then(again, again); return; }
    container.dataset.motionState = 'idle';
  };

  function begin(source, {origin = null} = {}) {
    const ctx = context(); if (!ctx) return false;
    const merge = coalesceInput(pending, source); if (merge.join) { stats.coalesced += 1; return true; }
    interrupt('input', ctx);
    const reduced = prefersReducedMotion(win); const prev = stateOf(ctx); const panelOpen = !panel.hidden && panel.childElementCount > 0;
    const current = pending = {source, origin, reduced, prev, at: now(), prevPoint: focusPoint(ctx.projection, prev), edges: reduced ? null : ctx.overlay?.snapshotEdges?.() || null,
      panelNodes: panelOpen && !reduced ? [...panel.children] : null, panelScroll: panel.scrollTop, panelBox: panelOpen && !reduced ? boxIn(panel, row) : null, frame: reduced ? null : captureOldFrame(ctx.wrapper)};
    container.dataset.motionState = 'running';
    queueMicrotask(() => { if (pending === current) play(); }); // an input that never reached renderAll still ends its transition
    return true;
  }

  function play() {
    const p = pending; if (!p) return; pending = null;
    const ctx = context(); if (!ctx) { settle(); return; }
    const next = stateOf(ctx); const kind = transitionKind(p.prev, next, p.source); const projection = ctx.projection; const wrapper = ctx.wrapper;
    const nextPoint = focusPoint(projection, next); const camera = wrapper.camera();
    const viewport = {width: container.clientWidth, height: container.clientHeight}; // forced layout read, after the panel DOM of this commit
    const cloud = next.cloudId ? projection.clouds.find(row => row.id === next.cloudId) || null : null;
    const decision = kind === 'deselect' ? {move: false, reason: 'deselect', target: null} : cameraDecision({source: p.source, kind, camera, viewport, cloud, point: nextPoint, zoomRange: NETWORK_ZOOM_RANGE});
    const home = kind === 'deselect' && camera ? returnTarget(wrapper.cameraMemory, camera) : null;
    const panelOpen = !panel.hidden; const replaced = Boolean(p.panelNodes?.length) && p.panelNodes.every(node => !node.isConnected);
    const panelMode = p.reduced || kind === 'none' || kind === 'subitem' ? null : panelOpen && !p.panelNodes ? 'enter' : panelOpen && replaced ? 'cross' : !panelOpen && replaced ? 'recede' : null;
    const oldFrame = Boolean(p.frame) && kind !== 'none';
    const timeline = buildTimeline(kind, {reduced: p.reduced, camera: decision.move || Boolean(home), oldFrame, panelItems: panelMode === 'enter' ? panel.childElementCount : 1, panel: Boolean(panelMode)});
    stats.transitions += 1; lastTransition = {kind, source: p.source, reduced: p.reduced, end: timeline.end, tracks: timeline.tracks, camera: {move: decision.move, reason: decision.reason, target: decision.target, returning: Boolean(home), home}, oldFrame, panel: panelMode};
    const current = active = {anims: [], nodes: [], overlay: ctx.overlay, forced: null, frame: oldFrame ? p.frame : null};
    const track = anim => { if (anim) current.anims.push(anim); return anim; };
    // camera (one tween, same timeline; reduced -> duration 0 -> instant but identical final camera)
    const cameraTrack = timeline.tracks.camera || {start: 0, duration: 0}; const timing = {delay: cameraTrack.start, duration: cameraTrack.duration, onFrame: cam => registerFrame(current.frame, cam), onStop: () => { if (current.frame) { current.frame.image.remove(); current.frame = null; } }};
    if (decision.move) wrapper.moveCamera(decision.target, timing); else if (home) wrapper.returnCamera(timing);
    if (!p.reduced && kind !== 'none') try {
      // T5: the previous frame above the G6 canvas, under the labels, fading out; edges start drawing once it is <= 50 % opaque
      if (current.frame && timeline.tracks.oldFrame) { const {image, box} = current.frame; Object.assign(image.style, {left: `${box.left}px`, top: `${box.top}px`, width: `${box.width}px`, height: `${box.height}px`}); container.after(image); current.nodes.push(image); registerFrame(current.frame, wrapper.camera() || current.frame.camera); track(image.animate([{opacity: 1}, {opacity: 0}], {delay: timeline.tracks.oldFrame.start, duration: timeline.tracks.oldFrame.duration, easing: 'linear', fill: 'both'})); }
      for (const anim of ctx.overlay?.decorate?.({kind, tracks: timeline.tracks, retired: p.edges, source: p.source, origin: p.origin, prevPoint: p.prevPoint, nextPoint, cloudId: next.cloudId, prevCloudId: p.prev.cloudId}) || []) track(anim);
      const panelTrack = timeline.tracks.panel;
      if (panelMode === 'enter' && panelTrack) { const beside = panel.getBoundingClientRect().left >= container.getBoundingClientRect().right - 1; [...panel.children].forEach((child, index) => track(child.animate([{opacity: 0, transform: beside ? 'translateX(-14px)' : 'translateY(-10px)'}, {opacity: 1, transform: 'none'}], {delay: panelTrack.start + index * panelTrack.step, duration: panelTrack.duration, easing: 'ease-out', fill: 'backwards'}))); }
      if (panelMode === 'cross' && panelTrack) { const ghost = panelGhost(doc, p.panelNodes, {scrollTop: p.panelScroll}); ghost.style.top = `${panel.scrollTop}px`; ghost.style.height = `${panel.clientHeight}px`; panel.append(ghost); current.nodes.push(ghost); track(ghost.animate([{opacity: 1}, {opacity: 0}], {delay: panelTrack.start, duration: panelTrack.duration, easing: 'ease-in-out', fill: 'both'})); }
      if (panelMode === 'recede' && panelTrack && p.panelBox) { const box = p.panelBox; const ghost = panelGhost(doc, p.panelNodes, {scrollTop: p.panelScroll}); Object.assign(ghost.style, {left: `${box.left}px`, top: `${box.top}px`, width: `${box.width}px`, height: `${box.height}px`}); row.append(ghost); current.nodes.push(ghost); track(ghost.animate([{opacity: 1, transform: 'none'}, {opacity: 0, transform: box.beside ? 'translateX(14px)' : 'translateY(10px)'}], {delay: panelTrack.start, duration: panelTrack.duration, easing: 'ease-in', fill: 'both'})); }
      if (timeline.tracks.item) { const item = panel.querySelector('.atlas-cloud-subitem[aria-pressed="true"]'); if (item) track(item.animate([{transform: 'scale(0.97)', opacity: 0.7}, {transform: 'none', opacity: 1}], {delay: timeline.tracks.item.start, duration: timeline.tracks.item.duration, easing: 'ease-out', fill: 'backwards'})); }
      if (current.anims.length) Promise.allSettled(current.anims.map(anim => anim.finished)).then(() => { if (active === current) { removeActive(); settle(); } });
      current.forced = setTimeout(() => { if (active !== current) return; stats.forced += 1; wrapper.finishTween('forced'); removeActive(); settle(); }, forcedFinishAt(timeline)); // safety: decorations go, the tween ends; idle still waits for the queue
    } catch (error) { if (active === current) removeActive(); console.error(error); } // a failed decoration never leaves data-motion-state stuck at running: the state was already committed
    settle();
  }

  /** Snapshot replacement / overview teardown: everything goes at once, without animation. */
  function reset() { stats.resets += 1; pending = null; interrupt('reset'); settle(); }

  function debug() {
    const ctx = context(); const wrapper = ctx?.wrapper; const captured = wrapper?.capture?.();
    return {state: container.dataset.motionState, reduced: prefersReducedMotion(win), pending: Boolean(pending), tweening: wrapper?.tweening || 0, tweenFrames: wrapper?.stats.tweenFrames || 0, tweens: wrapper?.stats.tweens || 0, tweensStopped: wrapper?.stats.tweensStopped || 0, tweensForced: wrapper?.stats.tweensForced || 0, instantMoves: wrapper?.stats.instantMoves || 0,
      lastTween: wrapper?.lastTween || null, lastTransition: lastTransition ? JSON.parse(JSON.stringify(lastTransition)) : null, memory: wrapper?.cameraMemory || null, camera: wrapper?.camera?.() || null, captured: captured && Array.isArray(captured.position) ? {zoom: captured.zoom, x: captured.position[0], y: captured.position[1]} : null,
      decorations: ctx?.overlay?.decorationCount?.() || 0, clones: doc.querySelectorAll('.atlas-motion-clone').length, oldFrames: doc.querySelectorAll('.atlas-motion-frame').length, animations: row.getAnimations({subtree: true}).length, stats: {...stats}};
  }

  return {begin, play, reset, settle, debug, get state() { return container.dataset.motionState; }};
}

