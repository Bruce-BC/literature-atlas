import {cp, mkdir, readdir, rm, writeFile} from 'node:fs/promises';
import {existsSync} from 'node:fs';
import {join, resolve} from 'node:path';
import {build} from 'esbuild';
import {builtinModules} from 'node:module';

const root = resolve(new URL('..', import.meta.url).pathname);
const required = [
  'frontend/index.html', 'frontend/main.js', 'frontend/styles.css',
  'obsidian-plugin/main.cjs', 'obsidian-plugin/manifest.json', 'obsidian-plugin/styles.css',
  'atlas/__main__.py', 'atlas/cli.py', 'atlas/server.py',
];
const missing = required.filter((rel) => !existsSync(join(root, rel)));
if (missing.length) throw new Error(`build requires complete source; missing: ${missing.join(', ')}`);
const dist = join(root, 'dist');
await rm(dist, {recursive: true, force: true});
await mkdir(join(dist, 'web'), {recursive: true});
await mkdir(join(dist, 'obsidian-plugin', 'bridge', 'atlas'), {recursive: true});
await build({entryPoints: [join(root, 'frontend', 'main.js')], bundle: true, format: 'esm', platform: 'browser', outfile: join(dist, 'web', 'app.js'), sourcemap: false, minify: false});
await build({entryPoints: [join(root, 'obsidian-plugin', 'main.cjs')], bundle: true, format: 'cjs', platform: 'node', outfile: join(dist, 'obsidian-plugin', 'main.js'), external: ['obsidian', 'electron', ...builtinModules, ...builtinModules.map((name) => `node:${name}`)], sourcemap: false, minify: false});
await cp(join(root, 'frontend', 'index.html'), join(dist, 'web', 'index.html'));
await cp(join(root, 'frontend', 'styles.css'), join(dist, 'web', 'styles.css'));
await cp(join(root, 'obsidian-plugin', 'manifest.json'), join(dist, 'obsidian-plugin', 'manifest.json'));
await cp(join(root, 'obsidian-plugin', 'styles.css'), join(dist, 'obsidian-plugin', 'styles.css'));
for (const name of await readdir(join(root, 'atlas'))) if (name.endsWith('.py')) await cp(join(root, 'atlas', name), join(dist, 'obsidian-plugin', 'bridge', 'atlas', name));
await cp(join(root, 'assets'), join(dist, 'web', 'assets'), {recursive: true});
await cp(join(dist, 'web'), join(dist, 'obsidian-plugin', 'web'), {recursive: true});
await writeFile(join(dist, 'manifest.json'), JSON.stringify({format: 1, app: 'obsidian-plugin/web/app.js', plugin: 'obsidian-plugin/main.js', bridge: 'obsidian-plugin/bridge/atlas'}, null, 2) + '\n');
console.log(JSON.stringify({dist: 'dist', files: required.length}));
