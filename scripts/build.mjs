import {cp, mkdir, readdir, readFile, rm, writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {existsSync} from 'node:fs';
import {join, resolve} from 'node:path';
import {build} from 'esbuild';
import {builtinModules} from 'node:module';

const root = resolve(new URL('..', import.meta.url).pathname);
const required = [
  'frontend/index.html', 'frontend/main.js', 'frontend/styles.css',
  'obsidian-plugin/main.cjs', 'obsidian-plugin/manifest.json', 'obsidian-plugin/styles.css',
  'manifest.json', 'versions.json', 'atlas/__main__.py', 'atlas/cli.py', 'atlas/server.py',
];
const missing = required.filter((rel) => !existsSync(join(root, rel)));
if (missing.length) throw new Error(`build requires complete source; missing: ${missing.join(', ')}`);
const canonicalManifest = JSON.parse(await readFile(join(root, 'manifest.json'), 'utf8'));
const sourceManifest = JSON.parse(await readFile(join(root, 'obsidian-plugin', 'manifest.json'), 'utf8'));
if (JSON.stringify(canonicalManifest) !== JSON.stringify(sourceManifest)) throw new Error('root manifest and plugin source manifest drifted');
const dist = join(root, 'dist');
await rm(dist, {recursive: true, force: true});
await mkdir(join(dist, 'web'), {recursive: true});
await mkdir(join(dist, 'obsidian-plugin', 'bridge', 'atlas'), {recursive: true});
const frontendBuild = await build({entryPoints: [join(root, 'frontend', 'main.js')], bundle: true, format: 'esm', platform: 'browser', outfile: join(dist, 'web', 'app.js'), metafile: true, sourcemap: false, minify: false});
await writeFile(join(dist, 'web-meta.json'), JSON.stringify(frontendBuild.metafile), 'utf8');
await build({entryPoints: [join(root, 'obsidian-plugin', 'main.cjs')], bundle: true, format: 'cjs', platform: 'node', outfile: join(dist, 'obsidian-plugin', 'main.js'), external: ['obsidian', 'electron', ...builtinModules, ...builtinModules.map((name) => `node:${name}`)], sourcemap: false, minify: false});
await cp(join(root, 'frontend', 'index.html'), join(dist, 'web', 'index.html'));
await cp(join(root, 'frontend', 'styles.css'), join(dist, 'web', 'styles.css'));
await cp(join(root, 'manifest.json'), join(dist, 'obsidian-plugin', 'manifest.json'));
await cp(join(root, 'obsidian-plugin', 'styles.css'), join(dist, 'obsidian-plugin', 'styles.css'));
for (const name of await readdir(join(root, 'atlas'))) if (name.endsWith('.py')) await cp(join(root, 'atlas', name), join(dist, 'obsidian-plugin', 'bridge', 'atlas', name));
await cp(join(root, 'assets'), join(dist, 'web', 'assets'), {recursive: true});
await cp(join(dist, 'web'), join(dist, 'obsidian-plugin', 'web'), {recursive: true});
const resourceFiles = [];
for (const name of (await readdir(join(dist, 'obsidian-plugin', 'bridge', 'atlas'))).sort()) {
  const path = join(dist, 'obsidian-plugin', 'bridge', 'atlas', name);
  if (name.endsWith('.py')) resourceFiles.push({path: `bridge/atlas/${name}`, content: await readFile(path, 'utf8')});
}
async function collectWeb(dir, prefix = 'web') {
  for (const entry of (await readdir(dir, {withFileTypes: true})).sort((a, b) => a.name.localeCompare(b.name))) {
    const path = join(dir, entry.name); const rel = `${prefix}/${entry.name}`;
    if (entry.isDirectory()) await collectWeb(path, rel);
    else if (!entry.name.endsWith('.map')) resourceFiles.push({path: rel, content: await readFile(path, 'utf8')});
  }
}
await collectWeb(join(dist, 'obsidian-plugin', 'web'));
resourceFiles.sort((a, b) => a.path.localeCompare(b.path));
const files = resourceFiles.map(({path, content}) => ({path, sha256: createHash('sha256').update(content, 'utf8').digest('hex'), content}));
const digest = createHash('sha256').update(JSON.stringify(files.map(({path, sha256}) => ({path, sha256})))).digest('hex');
const resourceContract = {version: 1, digest, files};
const bundledMain = await readFile(join(dist, 'obsidian-plugin', 'main.js'), 'utf8');
const bundledPackageInputs = Object.keys(JSON.parse(await readFile(join(dist, 'web-meta.json'), 'utf8')).inputs).filter((input) => input.includes('node_modules'));
const packageRoots = new Map();
for (const input of bundledPackageInputs) {
  const marker = input.split('node_modules/').pop();
  const parts = marker.split('/');
  const packageName = parts[0].startsWith('@') ? `${parts[0]}/${parts[1]}` : parts[0];
  const packageRoot = join(root, 'node_modules', packageName);
  if (existsSync(join(packageRoot, 'package.json'))) packageRoots.set(packageName, packageRoot);
}
const notices = [];
const vendoredProvenance = JSON.parse(await readFile(join(root, 'assets/licenses/provenance.json'), 'utf8'));
for (const [packageName, packageRoot] of packageRoots) {
  const metadata = JSON.parse(await readFile(join(packageRoot, 'package.json'), 'utf8'));
  const allowedLicenses = ['MIT', 'ISC', 'BSD-3-Clause', 'BSD-2-Clause', 'Apache-2.0', 'MPL-2.0', 'CC0-1.0', '0BSD', 'Unlicense'];
  if (!allowedLicenses.includes(metadata.license)) throw new Error(`unsupported or unknown license for bundled package ${packageName}: ${metadata.license}`);
  let licensePath = ['LICENSE', 'LICENSE.md', 'LICENSE.txt'].map((name) => join(packageRoot, name)).find((path) => existsSync(path)) || null;
  if (!licensePath) {
    const provenance = vendoredProvenance.find((row) => row.package === packageName && row.version === metadata.version && row.license === metadata.license);
    if (!provenance) throw new Error(`missing package-local license and reviewed vendored provenance for ${packageName}@${metadata.version}`);
    licensePath = join(root, provenance.vendoredPath);
    if (!existsSync(licensePath) || createHash('sha256').update(await readFile(licensePath)).digest('hex') !== provenance.licenseFileSHA256) throw new Error(`vendored license provenance hash mismatch for ${packageName}@${metadata.version}`);
  }
  if (!licensePath) throw new Error(`missing license text for bundled package ${packageName}`);
  notices.push({name: packageName, version: metadata.version, license: metadata.license, text: (await readFile(licensePath, 'utf8')).trim()});
}
notices.sort((a, b) => a.name.localeCompare(b.name));
const noticeBanner = `/*\n${notices.map((notice) => ` Third-party package: ${notice.name}@${notice.version} (${notice.license})\n ${notice.text.split('\n').join('\n ')}`).join('\n\n')}\n*/\n`;
const prefix = `${noticeBanner}const ATLAS_BUNDLED_RESOURCES = Object.freeze(${JSON.stringify(resourceContract)});\n`;
await writeFile(join(dist, 'obsidian-plugin', 'main.js'), prefix + bundledMain, 'utf8');
await mkdir(join(dist, 'community-plugin'), {recursive: true});
for (const name of ['main.js', 'manifest.json', 'styles.css']) await cp(join(dist, 'obsidian-plugin', name), join(dist, 'community-plugin', name));
await writeFile(join(dist, 'community-plugin', 'resource-contract.json'), JSON.stringify({version: 1, digest, files: files.map(({path, sha256}) => ({path, sha256}))}, null, 2) + '\n');
await writeFile(join(dist, 'manifest.json'), JSON.stringify({format: 1, app: 'obsidian-plugin/web/app.js', plugin: 'obsidian-plugin/main.js', bridge: 'obsidian-plugin/bridge/atlas'}, null, 2) + '\n');
console.log(JSON.stringify({dist: 'dist', files: required.length, bundledResources: files.length, resourceDigest: digest, community: 'dist/community-plugin'}));
