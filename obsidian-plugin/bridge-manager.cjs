'use strict';

const path = require('node:path');
const {spawn: nodeSpawn} = require('node:child_process');
const {
  OBSIDIAN_ORIGIN, LOOPBACK_HOST, parseBootstrap, makeLaunchId, makeLoopbackUrl,
  isLoopbackOrigin, sanitizeDiagnostic, isAllowedZoteroUrl
} = require('./security.cjs');

const DEFAULT_VERSION = '0.1.0';
const MIN_POLL_INTERVAL = 5;
const MAX_POLL_INTERVAL = 300;
const MAX_BACKOFF = 300;

function validatePollInterval(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number < MIN_POLL_INTERVAL || number > MAX_POLL_INTERVAL) throw new Error(`poll interval must be ${MIN_POLL_INTERVAL}-${MAX_POLL_INTERVAL} seconds`);
  return number;
}

function pythonVersion(text) {
  const match = String(text).match(/Python\s+(\d+)\.(\d+)\.(\d+)/i);
  return match ? [Number(match[1]), Number(match[2]), Number(match[3])] : null;
}

function isSupportedPython(text) {
  const version = pythonVersion(text);
  return Boolean(version && (version[0] > 3 || (version[0] === 3 && version[1] >= 11)));
}

class BridgeManager {
  constructor(options = {}) {
    this.pythonPath = options.pythonPath || 'python3';
    this.pluginDir = path.resolve(options.pluginDir || __dirname);
    this.bridgeDir = path.resolve(options.bridgeDir || path.join(this.pluginDir, 'bridge'));
    this.dataDir = path.resolve(options.dataDir || path.join(this.pluginDir, '.local'));
    this.webRoot = path.resolve(options.webRoot || path.join(this.pluginDir, 'web'));
    this.pollInterval = validatePollInterval(options.pollInterval ?? 30);
    this.parentOrigin = options.parentOrigin || OBSIDIAN_ORIGIN;
    if (this.parentOrigin !== OBSIDIAN_ORIGIN) throw new Error('unsupported Obsidian parent origin');
    this.expectedVersion = String(options.expectedVersion || DEFAULT_VERSION);
    this.spawn = options.spawn || nodeSpawn;
    this.fetch = options.fetch || ((...args) => globalThis.fetch(...args));
    this.log = options.log || (() => {});
    this.setTimer = options.setTimer || ((callback, delay) => setTimeout(callback, delay));
    this.clearTimer = options.clearTimer || ((timer) => clearTimeout(timer));
    this.child = null;
    this.bootstrap = null;
    this.ready = false;
    this.paperCount = null;
    this.phase = 'stopped';
    this.failureCount = 0;
    this.restartTimer = null;
    this.watcherTimer = null;
    this.stopping = false;
    this.onStatus = options.onStatus || (() => {});
    this.startPromise = null;
    this.probeChild = null;
    this.probeReject = null;
    this.stopTimer = null;
    this.retiringChildren = new Set();
  }

  status() {
    return {
      phase: this.phase,
      ready: this.ready,
      frameReady: false,
      port: this.bootstrap ? this.bootstrap.port : null,
      provider: 'e5',
      paperCount: Number.isInteger(this.paperCount) ? this.paperCount : null,
      ownedPid: this.bootstrap ? this.bootstrap.pid : null,
      launchId: this.bootstrap ? this.bootstrap.launchId : null
    };
  }

  async validatePython() {
    if (!this.pythonPath || (!path.isAbsolute(this.pythonPath) && this.pythonPath !== 'python3' && this.pythonPath !== 'python')) throw new Error('python path must be an executable name or absolute path');
    if (typeof this.spawn !== 'function') throw new Error('spawn unavailable');
    const output = await new Promise((resolve, reject) => {
      let child;
      let settled = false;
      const timeout = this.setTimer(() => {
        if (settled) return;
        settled = true;
        if (child && typeof child.kill === 'function') { try { child.kill('SIGTERM'); } catch {} }
        this.probeChild = null;
        this.probeReject = null;
        reject(new Error('Python version probe timeout'));
      }, 5000);
      timeout?.unref?.();
      const finish = (error, value) => {
        if (settled) return;
        settled = true;
        this.clearTimer(timeout);
        this.probeChild = null;
        this.probeReject = null;
        if (error) reject(error); else resolve(value);
      };
      this.probeReject = (error) => finish(error);
      try { child = this.spawn(this.pythonPath, ['--version'], {cwd: this.bridgeDir, shell: false, windowsHide: true}); this.probeChild = child; } catch (error) { finish(error); return; }
      let text = '';
      const onData = (chunk) => { text += String(chunk); };
      if (child.stdout && child.stdout.on) child.stdout.on('data', onData);
      if (child.stderr && child.stderr.on) child.stderr.on('data', onData);
      child.once('error', (error) => finish(error));
      child.once('close', (code) => code === 0 ? finish(null, text) : finish(new Error('configured Python exited unsuccessfully')));
    });
    if (!isSupportedPython(output)) throw new Error('Python 3.11 or newer is required');
    return output.trim();
  }

  commandArgs(launchId) {
    return ['-m', 'atlas', 'serve', '--data-dir', this.dataDir, '--web-root', this.webRoot, '--port', '0', '--poll-interval', String(this.pollInterval), '--launch-id', launchId, '--parent-origin', this.parentOrigin, '--embedding-provider', 'e5', '--watch-parent-stdin'];
  }

  _log(value) { this.log(sanitizeDiagnostic(value, [this.bootstrap && this.bootstrap.token].filter(Boolean))); }

  _attachStderr(child) {
    if (!child.stderr || !child.stderr.on) return;
    let pending = '';
    child.stderr.on('data', (chunk) => {
      pending += String(chunk);
      if (pending.length > 4096) pending = pending.slice(-4096);
      const lines = pending.split(/\r?\n/);
      pending = lines.pop() || '';
      for (const line of lines) this._log(line);
    });
  }

  _readBootstrap(child) {
    return new Promise((resolve, reject) => {
      let buffer = '';
      let settled = false;
      const timeout = this.setTimer(() => { if (!settled) { settled = true; reject(new Error('bridge bootstrap timeout')); } }, 10000);
      const fail = (error) => { if (settled) return; settled = true; this.clearTimer(timeout); reject(error); };
      const done = (line) => { if (settled) return; settled = true; this.clearTimer(timeout); try { resolve(parseBootstrap(line)); } catch (error) { reject(error); } };
      if (!child.stdout || !child.stdout.on) { fail(new Error('bridge stdout unavailable')); return; }
      child.stdout.on('data', (chunk) => {
        if (settled) return;
        buffer += String(chunk);
        const newline = buffer.indexOf('\n');
        if (newline < 0) { if (buffer.length > 4096) fail(new Error('bridge bootstrap too large')); return; }
        const line = buffer.slice(0, newline).replace(/\r$/, '');
        done(line);
      });
      child.once('error', fail);
      child.once('close', (code) => { if (!settled) fail(new Error(`bridge exited before bootstrap (${code ?? 'unknown'})`)); });
    });
  }

  async _health(bootstrap = this.bootstrap) {
    if (!bootstrap || typeof this.fetch !== 'function') throw new Error('bridge health unavailable');
    const url = makeLoopbackUrl(bootstrap.port, '/health');
    if (!isLoopbackOrigin(new URL(url).origin, bootstrap.port)) throw new Error('health URL is not loopback');
    const response = await this.fetch(url, {headers: {Authorization: `Bearer ${bootstrap.token}`, Origin: this.parentOrigin}, signal: AbortSignal.timeout ? AbortSignal.timeout(3000) : undefined});
    if (!response || !response.ok) throw new Error('bridge health check failed');
    const health = await response.json();
    if (!health || String(health.launchId) !== bootstrap.launchId || Number(health.pid) !== bootstrap.pid || String(health.version) !== this.expectedVersion) throw new Error('bridge health identity mismatch');
    if (health.host && health.host !== LOOPBACK_HOST) throw new Error('bridge health host mismatch');
    if (Number.isInteger(health.paperCount) && health.paperCount >= 0) this.paperCount = health.paperCount;
    return health;
  }

  async _refreshPaperCount() {
    if (!this.bootstrap || typeof this.fetch !== 'function') return;
    try {
      const response = await this.fetch(makeLoopbackUrl(this.bootstrap.port, '/api/v1/snapshot?projection=overview&sample=0'), {headers: {Authorization: `Bearer ${this.bootstrap.token}`, Origin: this.parentOrigin}});
      if (!response?.ok) return;
      const payload = await response.json();
      const count = payload?.overview?.totalPapers ?? (Array.isArray(payload?.papers) ? payload.papers.length : null);
      if (Number.isInteger(count) && count >= 0) this.paperCount = count;
    } catch { /* status remains redacted and available without a count */ }
  }

  start() {
    if (this.startPromise) return this.startPromise;
    const promise = this._start();
    this.startPromise = promise;
    promise.finally(() => { if (this.startPromise === promise) this.startPromise = null; }).catch(() => {});
    return promise;
  }

  async _start() {
    if (this.child && this.bootstrap) {
      try { await this._health(); return this.bootstrap; }
      catch (error) { this._failOwnedBridge(error); throw error; }
    }
    this.stopping = false;
    this.phase = 'starting'; this.onStatus(this.status());
    await this.validatePython();
    if (this.stopping) throw new Error('bridge startup cancelled');
    const launchId = makeLaunchId();
    const child = this.spawn(this.pythonPath, this.commandArgs(launchId), {cwd: this.bridgeDir, shell: false, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe']});
    if (!child || !Number.isInteger(child.pid) || child.pid < 1) throw new Error('bridge process did not provide a pid');
    this.child = child;
    this._attachStderr(child);
    child.once('exit', (code, signal) => this._onExit(child, code, signal));
    try {
      const bootstrap = await this._readBootstrap(child);
      if (bootstrap.pid !== child.pid || bootstrap.launchId !== launchId) throw new Error('bridge bootstrap ownership mismatch');
      this.bootstrap = bootstrap;
      await this._health(bootstrap);
      await this._refreshPaperCount();
      this.ready = true; this.phase = 'ready'; this.failureCount = 0; this.onStatus(this.status());
      return bootstrap;
    } catch (error) {
      this._log(error.message);
      this.ready = false; this.phase = 'error'; this.onStatus(this.status());
      this._terminateOwned(child);
      this.child = null; this.bootstrap = null;
      throw error;
    }
  }

  _terminateOwned(child) {
    if (!child || child !== this.child || typeof child.kill !== 'function') return false;
    if (this.bootstrap && Number(child.pid) !== Number(this.bootstrap.pid)) return false;
    try { child.kill('SIGTERM'); this.retiringChildren.add(child); this._scheduleForcedKill(child); return true; } catch { return false; }
  }

  _scheduleForcedKill(child) {
    const timer = this.setTimer(() => {
      if (child.exitCode == null && typeof child.kill === 'function') { try { child.kill('SIGKILL'); } catch {} }
      this.retiringChildren.delete(child);
    }, 2000);
    timer?.unref?.();
  }

  _failOwnedBridge(error) {
    this._log(error?.message || 'bridge health check failed');
    const owned = this.child;
    if (owned && owned === this.child) this._terminateOwned(owned);
    this.child = null; this.bootstrap = null; this.ready = false; this.phase = 'offline';
    this.onStatus(this.status());
    if (!this.stopping) this.scheduleRestart();
  }

  _onExit(child, code, signal) {
    if (child !== this.child) return;
    this.retiringChildren.delete(child);
    if (this.stopTimer) { this.clearTimer(this.stopTimer); this.stopTimer = null; }
    this._log(`bridge exited (${code ?? 'unknown'}${signal ? `/${signal}` : ''})`);
    this.child = null; this.bootstrap = null; this.ready = false;
    if (this.stopping) { this.phase = 'stopped'; this.onStatus(this.status()); return; }
    this.phase = 'offline'; this.onStatus(this.status());
    this.scheduleRestart();
  }

  scheduleRestart() {
    if (this.stopping || this.restartTimer) return;
    const delay = Math.min(MAX_BACKOFF * 1000, Math.max(1000, 1000 * (2 ** this.failureCount++)));
    this.restartTimer = this.setTimer(async () => { this.restartTimer = null; try { await this.start(); } catch { this.scheduleRestart(); } }, delay);
    this.restartTimer?.unref?.();
  }

  startWatcher() {
    if (this.watcherTimer || this.stopping) return;
    const tick = async () => {
      this.watcherTimer = null;
      if (this.stopping) return;
      if (this.ready) { try { await this._health(); } catch (error) { this._failOwnedBridge(error); } }
      else if (!this.child) { try { await this.start(); } catch { this.scheduleRestart(); } }
      if (!this.stopping) { this.watcherTimer = this.setTimer(tick, this.pollInterval * 1000); this.watcherTimer?.unref?.(); }
    };
    this.watcherTimer = this.setTimer(tick, this.pollInterval * 1000);
    this.watcherTimer?.unref?.();
  }

  stop() {
    this.stopping = true;
    if (this.restartTimer) { this.clearTimer(this.restartTimer); this.restartTimer = null; }
    if (this.watcherTimer) { this.clearTimer(this.watcherTimer); this.watcherTimer = null; }
    if (this.probeChild && typeof this.probeChild.kill === 'function') { try { this.probeChild.kill('SIGTERM'); } catch {} }
    if (this.probeReject) this.probeReject(new Error('bridge startup cancelled'));
    const owned = this.child;
    this.ready = false;
    if (owned && owned === this.child) {
      this._terminateOwned(owned);
      this.stopTimer = this.setTimer(() => {
        if (this.child === owned && owned.exitCode == null && typeof owned.kill === 'function') { try { owned.kill('SIGKILL'); } catch {} }
        this.retiringChildren.delete(owned);
      }, 2000);
      this.stopTimer?.unref?.();
    }
    if (!owned) { this.child = null; this.bootstrap = null; }
    this.phase = 'stopped'; this.onStatus(this.status());
  }

  async registeredPaper(paperId) {
    if (!this.ready || !this.bootstrap || typeof paperId !== 'string' || !paperId) throw new Error('bridge is not ready');
    const encoded = encodeURIComponent(paperId);
    const response = await this.fetch(makeLoopbackUrl(this.bootstrap.port, `/api/v1/papers/${encoded}`), {headers: {Authorization: `Bearer ${this.bootstrap.token}`, Origin: this.parentOrigin}});
    if (!response || !response.ok) throw new Error('paper is not registered');
    return response.json();
  }

  async registeredAttachmentUrl(attachmentId) {
    if (!this.ready || !this.bootstrap || typeof attachmentId !== 'string' || !attachmentId) throw new Error('bridge is not ready');
    const response = await this.fetch(makeLoopbackUrl(this.bootstrap.port, `/api/v1/attachments/${encodeURIComponent(attachmentId)}/url`), {headers: {Authorization: `Bearer ${this.bootstrap.token}`, Origin: this.parentOrigin}});
    if (!response || !response.ok) throw new Error('attachment is not registered');
    const payload = await response.json();
    if (!payload || !isAllowedZoteroUrl(payload.url)) throw new Error('attachment target rejected');
    return payload.url;
  }
}

module.exports = {BridgeManager, validatePollInterval, pythonVersion, isSupportedPython};
