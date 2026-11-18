import {readFile, readdir} from 'node:fs/promises';
import {existsSync} from 'node:fs';
import {join, resolve} from 'node:path';
import {execFileSync} from 'node:child_process';
import {tmpdir} from 'node:os';

const root = resolve(new URL('..', import.meta.url).pathname);
const pythonLauncher = process.platform === 'darwin' ? {command: 'arch', prefix: ['-arm64', 'python3']} : {command: 'python3', prefix: []};
const required = ['docs/snapshot.schema.json', 'docs/implementation-interfaces.md', 'tests/fixtures/snapshot.json', 'tests/fixtures/demo-snapshot.json', 'assets/palettes.json', 'assets/palette-sources.json', 'frontend/index.html', 'frontend/main.js', 'frontend/styles.css', 'obsidian-plugin/main.cjs', 'obsidian-plugin/manifest.json', 'obsidian-plugin/styles.css', 'atlas/__main__.py', 'atlas/cli.py', 'atlas/server.py'];
for (const rel of required) if (!existsSync(join(root, rel))) throw new Error(`missing required contract asset: ${rel}`);
for (const rel of ['docs/snapshot.schema.json', 'tests/fixtures/snapshot.json', 'tests/fixtures/demo-snapshot.json', 'assets/palettes.json', 'assets/palette-sources.json']) JSON.parse(await readFile(join(root, rel), 'utf8'));

const jsFiles = [];
async function collect(dir) {
  for (const entry of await readdir(dir, {withFileTypes: true})) {
    const path = join(dir, entry.name);
    if (entry.isDirectory() && !['node_modules', 'dist'].includes(entry.name)) await collect(path);
    else if (entry.isFile() && /\.(?:js|mjs|cjs)$/.test(entry.name)) jsFiles.push(path);
  }
}
for (const rel of ['frontend', 'obsidian-plugin', 'scripts', 'tests']) if (existsSync(join(root, rel))) await collect(join(root, rel));
for (const path of jsFiles) execFileSync(process.execPath, ['--check', path], {stdio: 'inherit'});

const pyFiles = [];
async function collectPy(dir) {
  for (const entry of await readdir(dir, {withFileTypes: true})) {
    const path = join(dir, entry.name);
    if (entry.isDirectory() && !['__pycache__', '.venv'].includes(entry.name)) await collectPy(path);
    else if (entry.isFile() && entry.name.endsWith('.py')) pyFiles.push(path);
  }
}
for (const rel of ['atlas', 'scripts', 'tests']) if (existsSync(join(root, rel))) await collectPy(join(root, rel));
const cache = join(tmpdir(), `literature-atlas-pycheck-${process.pid}`);
execFileSync(pythonLauncher.command, [...pythonLauncher.prefix, '-m', 'py_compile', '-q', ...pyFiles], {env: {...process.env, PYTHONPYCACHEPREFIX: cache}, stdio: 'inherit'});

const frontendTests = jsFiles.filter((path) => path.includes('/tests/frontend/') && path.endsWith('.test.mjs'));
const pluginTests = jsFiles.filter((path) => path.includes('/tests/plugin/') && path.endsWith('.test.mjs'));
if (!frontendTests.length || !pluginTests.length) throw new Error('frontend and plugin tests are required; empty lanes are not green');
execFileSync(pythonLauncher.command, [...pythonLauncher.prefix, '-c', "import json,sys,jsonschema\ns=json.load(open(sys.argv[1])); validator=jsonschema.Draft202012Validator(s)\nfor name in sys.argv[2:]: validator.validate(json.load(open(name)))\n", join(root, 'docs/snapshot.schema.json'), join(root, 'tests/fixtures/snapshot.json'), join(root, 'tests/fixtures/demo-snapshot.json')], {stdio: 'inherit'});
const bad = new RegExp([
  'zotero' + '\\.sqlite',
  '\\.' + 'zotero' + '/',
  'Bearer' + '\\s+[A-Za-z0-9._-]{12,}',
  '/' + 'Users/[^/]+/',
  '\\.' + 'pdf(?:$|["\'])',
].join('|'), 'i');
const source = await readFile(join(root, 'docs/implementation-interfaces.md'), 'utf8');
if (bad.test(source)) throw new Error('sensitive value pattern in interface contract');
console.log(`contract and syntax checks passed (${jsFiles.length} JS, ${pyFiles.length} Python files)`);
