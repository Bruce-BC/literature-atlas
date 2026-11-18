import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdtemp, readFile, writeFile, mkdir, rm, symlink, readdir, realpath} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createRequire} from 'node:module';
const require = createRequire(import.meta.url);
const {BridgeManager} = require('../../obsidian-plugin/bridge-manager.cjs');
const security = require('../../obsidian-plugin/security.cjs');
const exportsApi = require('../../obsidian-plugin/managed-export.cjs');
const {parseBootstrap, makeLoopbackUrl, isLoopbackOrigin, isAllowedZoteroUrl, sanitizeDiagnostic} = security;
const {stableExportId, exportManagedPaper, exportManagedVaultPaper} = exportsApi;
const {extractBundle, resolveRuntime, resolvePluginDir} = require('../../obsidian-plugin/runtime-resources.cjs');

const digest = value => createHash('sha256').update(value).digest('hex');
function resourceBundle(extra = []) {
  const files = [['bridge/atlas/__main__.py', 'print(1)'], ['web/index.html', '<html></html>'], ['web/app.js', 'void 0'], ...extra].map(([path, content]) => ({path, sha256: digest(content), content}));
  return {version: 1, files, digest: digest(JSON.stringify(files.map(({path, sha256}) => ({path, sha256})) ))};
}

test('vault alias is accepted while plugin root and ancestors cannot be symlinks', async () => {
  const root = await mkdtemp(join(tmpdir(), 'atlas-vault-anchor-'));
  try {
    const vault = join(root, 'vault'), alias = join(root, 'alias'), outside = join(root, 'outside');
    await mkdir(join(vault, '.obsidian', 'plugins', 'literature-atlas'), {recursive: true});
    await mkdir(outside);
    await symlink(vault, alias);
    const expected = join(await realpath(vault), '.obsidian', 'plugins', 'literature-atlas');
    assert.equal(resolvePluginDir(alias, '.obsidian', 'literature-atlas'), expected);
    assert.throws(() => resolvePluginDir(alias, '../outside', 'literature-atlas'), /invalid/);
    await rm(join(vault, '.obsidian', 'plugins', 'literature-atlas'), {recursive: true});
    await symlink(outside, join(vault, '.obsidian', 'plugins', 'literature-atlas'));
    assert.throws(() => resolvePluginDir(alias, '.obsidian', 'literature-atlas'), /safe directory/);
    assert.throws(() => extractBundle(join(vault, '.obsidian', 'plugins', 'literature-atlas'), resourceBundle()), /safe directory/);
    await rm(join(vault, '.obsidian', 'plugins', 'literature-atlas'));
    await rm(join(vault, '.obsidian', 'plugins'), {recursive: true});
    await symlink(outside, join(vault, '.obsidian', 'plugins'));
    assert.throws(() => resolvePluginDir(alias, '.obsidian', 'literature-atlas'), /safe directory/);
  } finally { await rm(root, {recursive: true, force: true}); }
});

test('bundled runtime extracts and verifies three-file installation while preserving user data', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'atlas-resource-'));
  try {
    await mkdir(join(dir, '.local'));
    await writeFile(join(dir, '.local', 'keep'), 'cache');
    await writeFile(join(dir, 'data.json'), 'settings');
    assert.throws(() => resolveRuntime(dir, undefined));
    const bundle = resourceBundle();
    const runtime = resolveRuntime(dir, bundle);
    assert.equal(runtime, join(await realpath(dir), '.runtime', bundle.digest));
    assert.equal(await readFile(join(runtime, 'web', 'app.js'), 'utf8'), 'void 0');
    assert.equal(resolveRuntime(dir, bundle), runtime);
    const manager = new BridgeManager({pluginDir: dir, runtimeDir: runtime});
    assert.equal(manager.bridgeDir, join(runtime, 'bridge'));
    assert.equal(manager.dataDir, join(dir, '.local'));
    assert.equal(manager.status().provider, null);
    assert.equal(manager.commandArgs('test').includes('--embedding-provider'), false);
    const explicit = new BridgeManager({pluginDir: dir, runtimeDir: runtime, provider: 'e5'});
    assert.equal(explicit.commandArgs('test')[explicit.commandArgs('test').indexOf('--embedding-provider') + 1], 'e5');
    assert.equal(await readFile(join(dir, '.local', 'keep'), 'utf8'), 'cache');
    assert.equal(await readFile(join(dir, 'data.json'), 'utf8'), 'settings');
    await writeFile(join(runtime, 'web', 'app.js'), 'tampered');
    assert.throws(() => resolveRuntime(dir, bundle), /integrity/);
    await rm(join(runtime, 'web', 'app.js'));
    await symlink(join(dir, 'data.json'), join(runtime, 'web', 'app.js'));
    assert.throws(() => resolveRuntime(dir, bundle), /integrity/);
  } finally { await rm(dir, {recursive: true, force: true}); }
});

test('bundled runtime rejects unsafe entries, integrity errors, symlinks and incomplete extraction', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'atlas-resource-'));
  const outside = await mkdtemp(join(tmpdir(), 'atlas-outside-'));
  try {
    await writeFile(join(outside, 'keep'), 'safe');
    for (const name of ['../escape', '/absolute', 'web/../../escape', 'web/app.js']) {
      assert.throws(() => extractBundle(dir, resourceBundle([[name, 'bad']])));
    }
    const invalid = resourceBundle(); invalid.files[0].content = 'tampered';
    assert.throws(() => extractBundle(dir, invalid), /integrity/);
    const badDigest = resourceBundle(); badDigest.digest = '0'.repeat(64);
    assert.throws(() => extractBundle(dir, badDigest), /digest/);
    const missing = resourceBundle(); missing.files.pop();
    assert.throws(() => extractBundle(dir, missing), /incomplete/);
    assert.throws(() => extractBundle(dir, resourceBundle([['web/conflict', 'file'], ['web/conflict/nested', 'conflict']])));
    assert.deepEqual(await readdir(join(dir, '.runtime')), []);
    await rm(join(dir, '.runtime'), {recursive: true});
    await symlink(outside, join(dir, '.runtime'));
    assert.throws(() => extractBundle(dir, resourceBundle()), /safe directory/);
    assert.throws(() => new BridgeManager({pluginDir: dir, runtimeDir: join(dir, '.runtime')}), /inside the plugin/);
    assert.equal(await readFile(join(outside, 'keep'), 'utf8'), 'safe');
  } finally { await rm(dir, {recursive: true, force: true}); await rm(outside, {recursive: true, force: true}); }
});

test('production entry fails explicitly outside a real Obsidian host', () => {
  assert.throws(() => execFileSync(process.execPath, ['-e', "require('./obsidian-plugin/main.cjs')"], {cwd: fileURLToPath(new URL('../..', import.meta.url)), stdio: 'pipe'}));
});

test('view navigates once per bridge port and ignores a closed frame load', async () => {
  const Module = require('node:module');
  const originalLoad = Module._load;
  let AtlasView, LiteratureAtlasPlugin;
  try {
    Module._load = function(request, ...args) {
      if (request === 'obsidian') return {ItemView: class {}, Plugin: class {}, PluginSettingTab: class {}};
      if (request === 'electron') return {};
      return originalLoad.call(this, request, ...args);
    };
    ({AtlasView, LiteratureAtlasPlugin} = require('../../obsidian-plugin/main.cjs'));
  } finally { Module._load = originalLoad; }
  let load, assignments = 0, messages = 0, lastMessage, src = '';
  const frame = {setAttribute() {}, addEventListener(event, callback) { load = callback; }, contentWindow: {postMessage(message, origin) { messages++; lastMessage = {message, origin}; }}, get src() { return src; }, set src(value) { assignments++; src = value; }};
  const container = {ownerDocument: {createElement(tag) { return tag === 'iframe' ? frame : {}; }}, appendChild() {}};
  const plugin = {pluginDir: '.', settings: {pythonPath: 'python3', pollInterval: 30}, views: new Set(), notifyStatus() {}, attachFrame(view) { this.views.add(view); }, detachFrame(view) { this.views.delete(view); }, ensureBridge: async () => {}};
  plugin.bridge = LiteratureAtlasPlugin.prototype.makeBridge.call(plugin);
  plugin.bridge.bootstrap = {port: 43127, token: 'test', launchId: 'launch', pid: 1};
  const view = new AtlasView({}, plugin);
  view.containerEl = container;
  await view.onOpen();
  view.setFrameSource(); view.setFrameSource();
  assert.equal(assignments, 1);
  load();
  assert.equal(messages, 1);
  plugin.bridge.onStatus({phase: 'ready', port: 43127, launchId: 'launch'});
  assert.equal(assignments, 1);
  view.frameReady = true;
  plugin.bridge.bootstrap.launchId = 'new-launch';
  plugin.bridge.onStatus({phase: 'ready', port: 43127, launchId: 'new-launch'});
  assert.equal(assignments, 2);
  assert.equal(view.frameReady, false);
  load();
  assert.equal(messages, 2);
  assert.equal(lastMessage.message.launchId, 'new-launch');
  assert.equal(lastMessage.message.token, 'test');
  assert.equal(lastMessage.origin, 'http://127.0.0.1:43127');
  plugin.bridge.onStatus({phase: 'ready', port: 43127, launchId: 'new-launch'});
  assert.equal(assignments, 2);
  view.frameReady = true;
  plugin.bridge.bootstrap.port = 43128;
  view.setFrameSource();
  assert.equal(assignments, 3);
  assert.equal(view.frameReady, false);
  await view.onClose();
  load();
  assert.equal(messages, 2);
});

function childProcess({pid = 4321, line = null} = {}) {
  const child = new EventEmitter();
  child.pid = pid;
  child.stdout = new EventEmitter();
  child.stderr = new EventEmitter();
  child.killCount = 0;
  child.kill = () => { child.killCount += 1; child.emit('exit', 0, 'SIGTERM'); child.emit('close', 0, 'SIGTERM'); return true; };
  if (line) queueMicrotask(() => child.stdout.emit('data', `${line}\n`));
  return child;
}

function spawnFactory({pid = 4321, launchLine, version = 'Python 3.12.1'} = {}) {
  let calls = 0;
  return (command, args) => {
    calls += 1;
    const child = calls === 1 ? childProcess() : childProcess({pid, line: launchLine});
    if (calls === 1) queueMicrotask(() => { child.stdout.emit('data', `${version}\n`); child.emit('close', 0); });
    return child;
  };
}

test('bootstrap and URL boundaries reject malformed credentials and hostile targets', () => {
  const parsed = parseBootstrap(JSON.stringify({port: 43127, token: 'token-token-token-token', pid: 12345, launchId: 'launch-fixture'}));
  assert.equal(parsed.port, 43127);
  assert.throws(() => parseBootstrap('{"port":43127}'));
  assert.throws(() => parseBootstrap(JSON.stringify({...parsed, extra: true})));
  assert.equal(makeLoopbackUrl(43127, '/index.html'), 'http://127.0.0.1:43127/index.html');
  assert.equal(makeLoopbackUrl(43127, '/api/v1/snapshot?projection=overview&sample=0'), 'http://127.0.0.1:43127/api/v1/snapshot?projection=overview&sample=0');
  assert.throws(() => makeLoopbackUrl(43127, '/?token=secret'));
  assert.equal(isLoopbackOrigin('http://127.0.0.1:43127', 43127), true);
  assert.equal(isLoopbackOrigin('https://evil.example', 43127), false);
  assert.equal(isAllowedZoteroUrl('zotero://select/library/items/ABC123'), true);
  assert.equal(isAllowedZoteroUrl('zotero://open-pdf/library/items/ABC123'), true);
  assert.equal(isAllowedZoteroUrl('zotero://open-pdf/groups/123/items/ABC123'), true);
  assert.equal(isAllowedZoteroUrl('file:///Users/secret/paper.pdf'), false);
  assert.equal(isAllowedZoteroUrl('https://evil.example/?token=secret'), false);
  assert.doesNotMatch(sanitizeDiagnostic('Authorization: Bearer secret-token'), /secret-token/);
});

test('bridge consumes one owned bootstrap and stops only the owned child', async () => {
  const bootstrap = {port: 43127, token: 'token-token-token-token', pid: 4321};
  const child = childProcess({pid: bootstrap.pid});
  let spawnCount = 0;
  const spawn = (command, args, options) => {
    spawnCount += 1;
    if (args[0] === '--version') {
      queueMicrotask(() => { child.stdout.emit('data', 'Python 3.12.1\n'); child.emit('close', 0); });
      return child;
    }
    assert.ok(args.includes('--watch-parent-stdin'));
    assert.deepEqual(options.stdio, ['pipe', 'pipe', 'pipe']);
    bootstrap.launchId = args[args.indexOf('--launch-id') + 1];
    queueMicrotask(() => child.stdout.emit('data', `${JSON.stringify(bootstrap)}\n`));
    return child;
  };
  const manager = new BridgeManager({spawn, fetch: async (url, options) => {
    assert.equal(new URL(url).hostname, '127.0.0.1');
    assert.equal(options.headers.Authorization, `Bearer ${bootstrap.token}`);
    if (String(url).includes('/snapshot?')) return {ok: true, json: async () => ({overview: {totalPapers: 2}})};
    return {ok: true, json: async () => ({pid: bootstrap.pid, launchId: bootstrap.launchId, version: '0.1.0', host: '127.0.0.1'})};
  }});
  // The generated launch nonce is unknown to this fixture, so derive it from args.
  const originalStart = manager.start.bind(manager);
  const result = await originalStart().catch((error) => { throw new Error(`owned startup failed: ${error.message}`); });
  assert.equal(result.pid, bootstrap.pid);
  assert.equal(manager.status().ownedPid, bootstrap.pid);
  assert.equal(manager.status().paperCount, 2);
  manager.stop();
  assert.equal(child.killCount, 1);
  assert.equal(manager.status().phase, 'stopped');
  assert.equal(spawnCount, 2);
});

test('health identity mismatch tears down the owned process and never adopts a port takeover', async () => {
  const child = childProcess({pid: 9876});
  let launchId;
  const spawn = (command, args) => {
    if (args[0] === '--version') {
      queueMicrotask(() => { child.stdout.emit('data', 'Python 3.12.1\n'); child.emit('close', 0); });
      return child;
    }
    launchId = args[args.indexOf('--launch-id') + 1];
    queueMicrotask(() => child.stdout.emit('data', JSON.stringify({port: 43128, token: 'token-token-token-token', pid: child.pid, launchId}) + '\n'));
    return child;
  };
  const manager = new BridgeManager({spawn, fetch: async () => ({ok: true, json: async () => ({pid: child.pid, launchId: 'different-launch', version: '0.1.0', host: '127.0.0.1'})})});
  await assert.rejects(manager.start(), /identity mismatch/);
  assert.equal(child.killCount, 1);
  assert.equal(manager.status().ownedPid, null);
  manager.stop();
});

test('concurrent starts share one owned launch and stop cancels a pending Python probe', async () => {
  const bootstrap = {port: 43129, token: 'token-token-token-token', pid: 7654};
  const child = childProcess({pid: bootstrap.pid});
  let spawnCount = 0;
  let releaseVersion;
  const versionReady = new Promise((resolve) => { releaseVersion = resolve; });
  const spawn = (command, args) => {
    spawnCount += 1;
    if (args[0] === '--version') {
      versionReady.then(() => { child.stdout.emit('data', 'Python 3.12.1\n'); child.emit('close', 0); });
      return child;
    }
    bootstrap.launchId = args[args.indexOf('--launch-id') + 1];
    queueMicrotask(() => child.stdout.emit('data', `${JSON.stringify(bootstrap)}\n`));
    return child;
  };
  const manager = new BridgeManager({spawn, fetch: async () => ({ok: true, json: async () => ({pid: bootstrap.pid, launchId: bootstrap.launchId, version: '0.1.0', host: '127.0.0.1'})})});
  const first = manager.start();
  const second = manager.start();
  assert.strictEqual(first, second);
  releaseVersion();
  await first;
  assert.equal(spawnCount, 2);
  manager.stop();
  assert.equal(child.killCount, 1);

  const pending = new BridgeManager({spawn: (command, args) => childProcess({pid: 8888}), fetch: async () => ({ok: true, json: async () => ({})})});
  const start = pending.start();
  pending.stop();
  await assert.rejects(start);
});

test('Python version probe is bounded and terminates a hung owned probe', async () => {
  const probe = new EventEmitter();
  probe.stdout = new EventEmitter();
  probe.stderr = new EventEmitter();
  probe.pid = 54321;
  probe.killCount = 0;
  probe.kill = () => { probe.killCount += 1; return true; };
  let timeoutCallback, timeoutDelay;
  const manager = new BridgeManager({
    spawn: () => probe,
    fetch: async () => ({ok: true, json: async () => ({})}),
    setTimer: (callback, delay) => { timeoutCallback = callback; timeoutDelay = delay; return {unref() {}}; },
    clearTimer: () => {}
  });
  const start = manager.start();
  assert.equal(timeoutDelay, 5000);
  timeoutCallback();
  await assert.rejects(start, /probe timeout/);
  assert.equal(probe.killCount, 1);
  manager.stop();
});

test('managed export uses a stable safe filename and preserves handwritten content', async () => {
  const root = await mkdtemp(join(tmpdir(), 'literature-atlas-plugin-'));
  const files = new Map();
  const adapter = {
    exists: async (file) => files.has(file),
    read: async (file) => files.get(file),
    mkdir: async () => {},
    write: async (file, content) => { files.set(file, content); }
  };
  const paper = {id: 'zotero:server:0:ABC', key: 'ABC', title: '../secret vault name', year: 2026, authors: ['Ada Example']};
  const filename = stableExportId(paper.id);
  assert.match(filename, /^paper-[a-f0-9]{32}\.md$/);
  await assert.rejects(exportManagedPaper({paper, root, filePath: join(root, '..', filename), adapter}), /not owned/);
  const first = await exportManagedPaper({paper, root, adapter});
  assert.equal(first.written, true);
  const existing = files.get(first.filePath);
  files.set(first.filePath, `# My notes\n\n${existing}My handwritten addition\n`);
  const updated = await exportManagedPaper({paper: {...paper, title: '<img src=x onerror=alert(1)> <!-- literature-atlas:managed:end --> ![](https://evil.example/x)'}, root, adapter});
  assert.equal(updated.replaced, true);
  assert.match(files.get(first.filePath), /^# My notes\n/);
  assert.match(files.get(first.filePath), /My handwritten addition/);
  assert.match(files.get(first.filePath), /&lt;img/);
  assert.doesNotMatch(files.get(first.filePath), /literature-atlas:managed:end --> !/);
  await writeFile(join(root, 'unrelated.md'), 'unchanged');
  assert.equal(await readFile(join(root, 'unrelated.md'), 'utf8'), 'unchanged');
  await rm(root, {recursive: true, force: true});
});

test('vault export stays in the owned relative folder and leaves an unowned note untouched', async () => {
  const files = new Map();
  const vault = {
    getAbstractFileByPath: (filePath) => files.has(filePath) ? {path: filePath} : null,
    read: async (file) => files.get(file.path),
    modify: async (file, content) => files.set(file.path, content),
    createFolder: async () => {},
    create: async (filePath, content) => { files.set(filePath, content); }
  };
  const paper = {id: 'zotero:server:0:XYZ', title: 'A paper'};
  const first = await exportManagedVaultPaper({paper, vault});
  assert.equal(first.filePath.startsWith('Literature Atlas/Managed/'), true);
  const path = first.filePath;
  files.set(path, '# User note\n');
  const second = await exportManagedVaultPaper({paper: {...paper, title: 'Changed'}, vault});
  assert.equal(second.written, false);
  assert.equal(files.get(path), '# User note\n');
});
