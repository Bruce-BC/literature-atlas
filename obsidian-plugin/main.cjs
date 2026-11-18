'use strict';

const path = require('node:path');
const {existsSync} = require('node:fs');
const {BridgeManager} = require('./bridge-manager.cjs');
const {exportManagedPaper, exportManagedVaultPaper} = require('./managed-export.cjs');
const {makeLoopbackUrl, isLoopbackOrigin, OBSIDIAN_ORIGIN, isAllowedZoteroUrl} = require('./security.cjs');

const obsidian = require('obsidian');
const electron = require('electron');

const VIEW_TYPE = 'literature-atlas';
const PLUGIN_ID = 'literature-atlas';
// Obsidian prefixes registered command IDs with the plugin ID at runtime.
const COMMAND_ID = 'open-atlas';
const FULL_COMMAND_ID = `${PLUGIN_ID}:${COMMAND_ID}`;
const DEFAULT_SETTINGS = Object.freeze({pythonPath: 'python3', pollInterval: 30});

function zoteroItemUrl(paper) {
  if (!paper || typeof paper.key !== 'string' || !/^[A-Za-z0-9]{1,64}$/.test(paper.key)) throw new Error('registered paper has no safe Zotero key');
  const libraryId = Number(paper.libraryID);
  if (!Number.isInteger(libraryId) || libraryId < 0) throw new Error('registered paper has no safe library id');
  return libraryId > 0 ? `zotero://select/groups/${libraryId}/items/${paper.key}` : `zotero://select/library/items/${paper.key}`;
}

function makeElement(container, tag, className) {
  const element = container && container.ownerDocument ? container.ownerDocument.createElement(tag) : (typeof document !== 'undefined' ? document.createElement(tag) : null);
  if (!element) return null;
  if (className) element.className = className;
  container?.appendChild?.(element);
  return element;
}

function clearElement(element) {
  if (!element) return;
  while (element.firstChild) element.removeChild(element.firstChild);
}

class AtlasView extends obsidian.ItemView {
  constructor(leaf, plugin) { super(leaf); this.plugin = plugin; this.iframe = null; this.frameReady = false; this.statusEl = null; }
  getViewType() { return VIEW_TYPE; }
  getDisplayText() { return 'Literature Atlas'; }
  async onOpen() {
    const container = this.containerEl;
    clearElement(container);
    this.statusEl = makeElement(container, 'div', 'literature-atlas-status');
    if (this.statusEl) this.statusEl.textContent = 'Starting Literature Atlas…';
    this.iframe = makeElement(container, 'iframe', 'literature-atlas-frame');
    if (!this.iframe) return;
    this.iframe.setAttribute('title', 'Literature Atlas');
    this.iframe.setAttribute('sandbox', 'allow-scripts allow-same-origin');
    const frame = this.iframe;
    frame.addEventListener?.('load', () => { if (this.iframe === frame) this.bootstrapFrame(); });
    this.plugin.attachFrame(this);
    try {
      await this.plugin.ensureBridge();
      this.setFrameSource();
    } catch { this.setStatus('Bridge offline. Retrying…'); }
  }
  setFrameSource() {
    if (!this.iframe || !this.plugin.bridge.bootstrap) return;
    // Credentials are transferred with postMessage only. This URL intentionally has no query/hash.
    const url = makeLoopbackUrl(this.plugin.bridge.bootstrap.port, '/index.html');
    if (this.iframe.src === url && this.frameLaunchId === this.plugin.bridge.bootstrap.launchId) return;
    this.frameReady = false;
    this.frameLaunchId = this.plugin.bridge.bootstrap.launchId;
    this.iframe.src = url;
  }
  bootstrapFrame() {
    const bootstrap = this.plugin.bridge.bootstrap;
    if (!bootstrap || !this.iframe?.contentWindow) return;
    this.iframe.contentWindow.postMessage({type: 'atlas:bootstrap', token: bootstrap.token, launchId: bootstrap.launchId, pid: bootstrap.pid, parentOrigin: this.plugin.bridge.parentOrigin}, `http://127.0.0.1:${bootstrap.port}`);
  }
  acceptReady(message) {
    if (!message || message.type !== 'atlas:ready' || message.launchId !== this.plugin.bridge.bootstrap?.launchId) return false;
    this.frameReady = true;
    this.setStatus('Literature Atlas ready');
    this.plugin.notifyStatus();
    return true;
  }
  setStatus(message) { if (this.statusEl) this.statusEl.textContent = String(message || ''); }
  async onClose() { this.plugin.detachFrame(this); this.iframe = null; }
}

class AtlasSettingTab extends obsidian.PluginSettingTab {
  display() {
    const container = this.containerEl;
    if (!container) return;
    clearElement(container);
    new obsidian.Setting(container)
      .setName('Python executable')
      .setDesc('Absolute Python 3.11+ executable, or python3 to use the system default.')
      .addText((text) => text.setValue(this.plugin.settings.pythonPath).onChange(async (value) => {
        try { await this.plugin.updateSettings({pythonPath: value.trim()}); } catch { text.setValue(this.plugin.settings.pythonPath); }
      }));
    new obsidian.Setting(container)
      .setName('Automatic watcher interval')
      .setDesc('Seconds between bridge health/reconnect checks (5–300).')
      .addText((text) => text.setValue(String(this.plugin.settings.pollInterval)).onChange(async (value) => {
        try { await this.plugin.updateSettings({pollInterval: Number(value)}); } catch { text.setValue(String(this.plugin.settings.pollInterval)); }
      }));
  }
}

class LiteratureAtlasPlugin extends obsidian.Plugin {
  constructor(app, manifest) {
    super(app, manifest);
    this.settings = {...DEFAULT_SETTINGS};
    this.views = new Set();
    this.messageHandler = (event) => this.handleMessage(event);
    this.bridge = null;
    this.lastError = null;
    this.lastBridgePort = null;
  }

  async onload() {
    const saved = await this.loadData().catch(() => null);
    if (saved && typeof saved === 'object') {
      if (typeof saved.pythonPath === 'string') this.settings.pythonPath = saved.pythonPath;
      if (saved.pollInterval !== undefined) this.settings.pollInterval = saved.pollInterval;
    }
    const manifestDir = typeof this.manifest?.dir === 'string' ? this.manifest.dir : '';
    const manifestCandidate = path.isAbsolute(manifestDir) && existsSync(manifestDir) ? manifestDir : '';
    const vaultBase = this.app?.vault?.adapter?.basePath;
    const vaultConfig = this.app?.vault?.configDir || '.obsidian';
    const vaultCandidate = vaultBase ? path.join(vaultBase, vaultConfig, 'plugins', PLUGIN_ID) : '';
    const relativeManifest = vaultBase && manifestDir.startsWith('/') ? path.join(vaultBase, manifestDir.slice(1)) : '';
    const candidate = [manifestCandidate, relativeManifest, vaultCandidate].find((value) => value && existsSync(value));
    if (!candidate) throw new Error('Literature Atlas packaged directory is unavailable');
    this.pluginDir = path.resolve(candidate);
    this.bridge = this.makeBridge();
    this.addSettingTab?.(new AtlasSettingTab(this.app, this));
    this.registerView(VIEW_TYPE, (leaf) => { const view = new AtlasView(leaf, this); this.views.add(view); return view; });
    this.addCommand({id: COMMAND_ID, name: 'Open Literature Atlas', callback: () => this.activateView()});
    this.registerEvent?.(this.app?.workspace?.on?.('layout-change', () => this.notifyStatus()));
    if (typeof window !== 'undefined') {
      if (this.registerDomEvent) this.registerDomEvent(window, 'message', this.messageHandler);
      else window.addEventListener('message', this.messageHandler);
    }
    // Bridge start and watcher are intentionally background work during plugin load.
    this.ensureBridge().catch((error) => { this.lastError = error; });
    this.bridge.startWatcher();
  }

  makeBridge() {
    return new BridgeManager({
      pluginDir: this.pluginDir,
      pythonPath: this.settings.pythonPath,
      pollInterval: this.settings.pollInterval,
      parentOrigin: OBSIDIAN_ORIGIN,
      onStatus: (status) => {
        if (status.phase === 'ready' && status.port && (status.port !== this.lastBridgePort || status.launchId !== this.lastBridgeLaunchId)) {
          this.lastBridgePort = status.port;
          this.lastBridgeLaunchId = status.launchId;
          for (const view of this.views) { view.frameReady = false; view.setFrameSource(); }
        }
        for (const view of this.views) view.setStatus(status.phase === 'ready' ? 'Starting frame…' : `Bridge ${status.phase}`);
        this.notifyStatus();
      }
    });
  }

  async updateSettings(patch) {
    const next = {...this.settings, ...patch};
    // Construction validates the bounded interval; validatePython rejects unsupported interpreters.
    const candidate = new BridgeManager({pluginDir: this.pluginDir, pythonPath: next.pythonPath, pollInterval: next.pollInterval, parentOrigin: OBSIDIAN_ORIGIN});
    await candidate.validatePython();
    this.settings = next;
    await this.saveData(this.settings);
    const previous = this.bridge;
    previous?.stop();
    this.bridge = this.makeBridge();
    this.bridge.startWatcher();
    await this.ensureBridge();
  }

  async ensureBridge() {
    if (!this.bridge) throw new Error('plugin is not loaded');
    try { const result = await this.bridge.start(); this.lastError = null; for (const view of this.views) view.setFrameSource(); return result; }
    catch (error) { this.lastError = error; throw error; }
  }

  async activateView() {
    const workspace = this.app?.workspace;
    if (!workspace) return;
    let leaf = workspace.getLeavesOfType?.(VIEW_TYPE)?.[0];
    if (!leaf) { leaf = workspace.getRightLeaf?.(false) || workspace.getLeaf?.('tab'); await leaf?.setViewState?.({type: VIEW_TYPE, active: true}); }
    if (leaf) workspace.revealLeaf?.(leaf);
  }

  attachFrame(view) { this.views.add(view); }
  detachFrame(view) { view.frameReady = false; this.views.delete(view); }

  frameForEvent(event) {
    for (const view of this.views) if (view.iframe?.contentWindow === event.source) return view;
    return null;
  }

  handleMessage(event) {
    const view = this.frameForEvent(event);
    const bootstrap = this.bridge?.bootstrap;
    if (!view || !bootstrap || !isLoopbackOrigin(event.origin, bootstrap.port)) return false;
    if (!event.data || event.data.launchId !== bootstrap.launchId) return false;
    if (event.data.type === 'atlas:ready') return view.acceptReady(event.data);
    if (event.data.type !== 'atlas:open') return false;
    const {kind, paperId, attachmentId} = event.data;
    if (!['item', 'pdf', 'export'].includes(kind)) return false;
    if (typeof paperId !== 'string' || paperId.length > 512 || (attachmentId !== undefined && (typeof attachmentId !== 'string' || attachmentId.length > 512))) return false;
    this.performOpen({kind, paperId, attachmentId}).catch((error) => { this.lastError = error; view.setStatus('Unable to open requested record'); });
    return true;
  }

  async performOpen({kind, paperId, attachmentId}) {
    const paper = await this.bridge.registeredPaper(paperId);
    if (kind === 'item') return this.openExternal(zoteroItemUrl(paper));
    if (kind === 'pdf') {
      if (typeof attachmentId !== 'string' || !Array.isArray(paper.attachmentIds) || !paper.attachmentIds.includes(attachmentId)) throw new Error('attachment is not registered for this paper');
      const url = await this.bridge.registeredAttachmentUrl(attachmentId);
      if (!isAllowedZoteroUrl(url) || !url.startsWith('zotero://open-pdf/')) throw new Error('registered attachment is not an allowed Zotero PDF target');
      return this.openExternal(url);
    }
    return this.exportPaper(paper);
  }

  async openExternal(url) {
    if (!isAllowedZoteroUrl(url) || !url.startsWith('zotero://')) throw new Error('external URL rejected');
    if (!electron?.shell?.openExternal) throw new Error('desktop shell unavailable');
    return electron.shell.openExternal(url);
  }

  async exportPaper(paper) {
    if (this.app?.vault && typeof this.app.vault.getAbstractFileByPath === 'function') return exportManagedVaultPaper({paper, vault: this.app.vault});
    const adapter = this.app?.vault?.adapter;
    if (!adapter) throw new Error('vault adapter unavailable');
    const root = this.app.vault.adapter.basePath || this.app.vault.getRoot?.()?.path;
    if (!root || !path.isAbsolute(root)) throw new Error('vault root unavailable');
    return exportManagedPaper({paper, root, adapter: {
      exists: (filePath) => adapter.exists(filePath),
      read: (filePath) => adapter.read(filePath),
      write: (filePath, content) => adapter.write(filePath, content),
      mkdir: async (dir) => { try { await adapter.mkdir(dir); } catch { /* an existing managed directory is fine */ } }
    }});
  }

  getStatus() {
    const status = this.bridge ? this.bridge.status() : {phase: 'stopped', ready: false, frameReady: false, port: null, provider: 'e5', paperCount: null, ownedPid: null, launchId: null};
    const frameReady = [...this.views].some((view) => view.frameReady);
    return {phase: status.phase, ready: Boolean(status.ready), frameReady, port: status.port, provider: status.provider, paperCount: Number.isInteger(status.paperCount) ? status.paperCount : null, ownedPid: status.ownedPid, launchId: status.launchId, hasError: Boolean(this.lastError)};
  }

  notifyStatus() { return this.getStatus(); }

  onunload() {
    if (typeof window !== 'undefined' && !this.registerDomEvent) window.removeEventListener?.('message', this.messageHandler);
    // Detach only this plugin's registered leaves so reload cannot reuse an old
    // view instance/iframe that still points at a retired bridge.
    this.app?.workspace?.detachLeavesOfType?.(VIEW_TYPE);
    this.bridge?.stop();
    this.views.clear();
  }
}

module.exports = LiteratureAtlasPlugin;
module.exports.default = LiteratureAtlasPlugin;
module.exports.LiteratureAtlasPlugin = LiteratureAtlasPlugin;
module.exports.AtlasView = AtlasView;
module.exports.AtlasSettingTab = AtlasSettingTab;
module.exports.VIEW_TYPE = VIEW_TYPE;
module.exports.COMMAND_ID = COMMAND_ID;
module.exports.FULL_COMMAND_ID = FULL_COMMAND_ID;
module.exports.PLUGIN_ID = PLUGIN_ID;
module.exports.zoteroItemUrl = zoteroItemUrl;
module.exports.BridgeManager = BridgeManager;
module.exports.BridgeLifecycle = BridgeManager;
Object.assign(module.exports, require('./security.cjs'), require('./managed-export.cjs'));
