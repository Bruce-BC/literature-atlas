import {mkdir, readdir, readFile, rm, stat, writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {join, resolve} from 'node:path';
import {execFileSync} from 'node:child_process';

const root = resolve(new URL('..', import.meta.url).pathname);
const out = join(root, '.artifacts');
await mkdir(out, {recursive: true});
const manifest = {format: 1, generatedAt: 'normalized', files: []};
async function walk(dir, prefix = '') {
  for (const name of (await readdir(dir)).sort()) {
    const path = join(dir, name); const rel = join(prefix, name);
    const info = await stat(path);
    if (info.isDirectory()) await walk(path, rel);
    else if (!rel.endsWith('.map') && !rel.endsWith('.log')) manifest.files.push({path: rel, sha256: createHash('sha256').update(await readFile(path)).digest('hex'), bytes: info.size});
  }
}
for (const top of ['dist', 'docs', 'assets', 'obsidian-plugin']) if (await stat(join(root, top)).catch(() => null)) await walk(join(root, top), top);
const requiredPackageFiles = ['dist/obsidian-plugin/main.js', 'dist/obsidian-plugin/manifest.json', 'dist/obsidian-plugin/styles.css', 'dist/obsidian-plugin/bridge/atlas', 'dist/obsidian-plugin/web/index.html', 'dist/obsidian-plugin/web/app.js', 'dist/obsidian-plugin/web/styles.css', 'dist/obsidian-plugin/web/assets'];
for (const rel of requiredPackageFiles) if (!await stat(join(root, rel)).catch(() => null)) throw new Error(`package requires built file: ${rel}`);
const zip = join(out, 'literature-atlas-plugin.zip');
const script = String.raw`import pathlib, sys, zipfile
root = pathlib.Path(sys.argv[1]); out = pathlib.Path(sys.argv[2])
mapping = {}
for p in (root / 'dist/obsidian-plugin').rglob('*'):
  if p.is_file() and p.suffix not in ('.map', '.log'):
    mapping[p] = p.relative_to(root / 'dist/obsidian-plugin').as_posix()
for rel in ('LICENSE', 'README.md'):
  p = root / rel
  if p.is_file(): mapping[p] = rel
notice = root / 'assets' / 'palette-sources.json'
if notice.is_file(): mapping[notice] = 'THIRD_PARTY_NOTICES/palette-sources.json'
third_party = root / 'docs' / 'THIRD_PARTY_NOTICES.md'
if third_party.is_file(): mapping[third_party] = 'THIRD_PARTY_NOTICES/THIRD_PARTY_NOTICES.md'
verification = root / 'docs' / 'palette-source-verification.json'
if verification.is_file(): mapping[verification] = 'THIRD_PARTY_NOTICES/palette-source-verification.json'
with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
  for p, rel in sorted(mapping.items(), key=lambda pair: pair[1]):
    info = zipfile.ZipInfo(rel, (1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    z.writestr(info, p.read_bytes())
`;
execFileSync('python3', ['-c', script, root, zip], {stdio: 'inherit'});
manifest.archive = {path: 'literature-atlas-plugin.zip', sha256: createHash('sha256').update(await readFile(zip)).digest('hex'), bytes: (await stat(zip)).size};
const communityOut = join(out, 'community-plugin');
await rm(communityOut, {recursive: true, force: true});
await mkdir(communityOut, {recursive: true});
for (const name of ['main.js', 'manifest.json', 'styles.css']) await writeFile(join(communityOut, name), await readFile(join(root, 'dist', 'community-plugin', name)));
const communityChecksums = [];
for (const name of ['main.js', 'manifest.json', 'styles.css']) communityChecksums.push(`${createHash('sha256').update(await readFile(join(communityOut, name))).digest('hex')}  ${name}`);
await writeFile(join(communityOut, 'SHA256SUMS'), communityChecksums.join('\n') + '\n');
manifest.community = {directory: 'community-plugin', files: ['main.js', 'manifest.json', 'styles.css'], checksums: communityChecksums};
await writeFile(join(out, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
console.log(JSON.stringify({artifact: '.artifacts/manifest.json', files: manifest.files.length}));
