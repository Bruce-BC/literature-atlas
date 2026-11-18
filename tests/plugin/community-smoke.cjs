'use strict';

// Install the three release assets in an isolated vault and load the actual
// plugin with a minimal host facade. The Python bridge is a real subprocess.
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const Module = require('node:module');
const {createHash} = require('node:crypto');
const {execFileSync} = require('node:child_process');
const root = path.resolve(__dirname, '../..');
const source = path.resolve(process.argv[2] || path.join(root, '.artifacts/community-plugin'));
const python = process.env.ATLAS_TEST_PYTHON || 'python3';
const vault = fs.mkdtempSync(path.join(os.tmpdir(), 'atlas-community-smoke-'));
const pluginDir = path.join(vault, '.obsidian', 'plugins', 'literature-atlas');

class Plugin {
  constructor(app, manifest) { this.app = app; this.manifest = manifest; }
  async loadData() { return {pythonPath: python}; }
  addSettingTab() {}
  registerView() {}
  addCommand() {}
  registerEvent() {}
}
const originalLoad = Module._load;
Module._load = function(request, ...args) {
  if (request === 'obsidian') return {Plugin, ItemView: class {}, PluginSettingTab: class {}};
  if (request === 'electron') return {};
  return originalLoad.call(this, request, ...args);
};

(async () => {
  let plugin;
  try {
    fs.mkdirSync(path.join(pluginDir, '.local'), {recursive: true});
    fs.writeFileSync(path.join(pluginDir, '.local', 'keep'), 'cache');
    fs.writeFileSync(path.join(pluginDir, 'data.json'), 'settings');
    fs.writeFileSync(path.join(vault, 'unowned-note.md'), 'note');
    execFileSync(python, [path.join(root, 'scripts/install_obsidian.py'), '--vault', vault, '--source', source], {cwd: root, stdio: 'pipe'});
    const installed = fs.readdirSync(pluginDir).sort();
    if (installed.join(',') !== '.local,data.json,main.js,manifest.json,styles.css') throw new Error('installer did not copy exactly three assets');
    const hash = file => createHash('sha256').update(fs.readFileSync(file)).digest('hex');
    if (!['main.js', 'manifest.json', 'styles.css'].every(name => hash(path.join(pluginDir, name)) === hash(path.join(source, name)))) throw new Error('installed asset integrity mismatch');
    const Atlas = require(path.join(pluginDir, 'main.js'));
    const app = {workspace: {on() {}, detachLeavesOfType() {}}, vault: {adapter: {basePath: vault}}};
    plugin = new Atlas(app, {dir: pluginDir});
    await plugin.onload();
    const bootstrap = await plugin.bridge.start();
    const health = await plugin.bridge._health(bootstrap);
    const web = await fetch(`http://127.0.0.1:${bootstrap.port}/index.html`);
    const appScript = await fetch(`http://127.0.0.1:${bootstrap.port}/app.js`);
    const checks = {
      runtimeInside: plugin.runtimeDir.startsWith(fs.realpathSync(pluginDir) + path.sep),
      healthIdentity: health.pid === bootstrap.pid,
      webServed: web.ok,
      appServed: appScript.ok,
      dataPreserved: fs.readFileSync(path.join(pluginDir, '.local', 'keep'), 'utf8') === 'cache' && fs.readFileSync(path.join(pluginDir, 'data.json'), 'utf8') === 'settings' && fs.readFileSync(path.join(vault, 'unowned-note.md'), 'utf8') === 'note',
      providerNotOverridden: !plugin.bridge.commandArgs('fixture').includes('--embedding-provider'),
      noLooseResources: !fs.existsSync(path.join(pluginDir, 'bridge')) && !fs.existsSync(path.join(pluginDir, 'web'))
    };
    if (Object.values(checks).some(value => !value)) throw new Error('community plugin smoke check failed');
    const child = plugin.bridge.child;
    plugin.onunload();
    if (child.exitCode === null && child.signalCode === null) await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('bridge did not exit')), 4000);
      child.once('exit', () => { clearTimeout(timer); resolve(); });
    });
    console.log(`Community plugin smoke: ${Object.keys(checks).length + 2} checks passed`);
  } finally {
    if (plugin?.bridge?.child) plugin.onunload();
    fs.rmSync(vault, {recursive: true, force: true});
  }
})().catch(() => { console.error('Community plugin smoke failed'); process.exitCode = 1; });
