'use strict';

const fs = require('node:fs');
const path = require('node:path');
const {createHash} = require('node:crypto');

const MAX_FILES = 512;
const MAX_FILE_BYTES = 16 * 1024 * 1024;
const MAX_TOTAL_BYTES = 48 * 1024 * 1024;
const sha256 = (value) => createHash('sha256').update(value).digest('hex');
const isHash = (value) => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
const allowedPath = (name) => typeof name === 'string' && /^(?:bridge\/atlas\/[A-Za-z0-9_-]+\.py|web\/(?:[A-Za-z0-9_-]+\/)*[A-Za-z0-9_.-]+)$/.test(name) && !name.split('/').some((part) => part === '.' || part === '..');

function validateBundle(bundle) {
  if (!bundle || bundle.version !== 1 || !isHash(bundle.digest) || !Array.isArray(bundle.files) || bundle.files.length === 0 || bundle.files.length > MAX_FILES) throw new Error('Literature Atlas bundled resources are invalid');
  const names = new Set();
  let total = 0;
  for (const file of bundle.files) {
    if (!file || !allowedPath(file.path) || names.has(file.path) || !isHash(file.sha256) || typeof file.content !== 'string') throw new Error('Literature Atlas bundled resource entry is invalid');
    names.add(file.path);
    const size = Buffer.byteLength(file.content, 'utf8');
    total += size;
    if (size > MAX_FILE_BYTES || total > MAX_TOTAL_BYTES || sha256(Buffer.from(file.content, 'utf8')) !== file.sha256) throw new Error('Literature Atlas bundled resource integrity check failed');
  }
  for (const required of ['bridge/atlas/__main__.py', 'web/index.html', 'web/app.js']) if (!names.has(required)) throw new Error('Literature Atlas bundled resources are incomplete');
  const manifest = bundle.files.map(({path: resourcePath, sha256: hash}) => ({path: resourcePath, sha256: hash}));
  if (sha256(JSON.stringify(manifest)) !== bundle.digest) throw new Error('Literature Atlas bundled resource digest mismatch');
  return bundle;
}

function requireDirectory(directory) {
  const stat = fs.lstatSync(directory);
  if (!stat.isDirectory() || stat.isSymbolicLink()) throw new Error('Literature Atlas runtime path is not a safe directory');
}

function resolvePluginDir(vaultBase, configDir, pluginId) {
  if (typeof vaultBase !== 'string' || !vaultBase || typeof configDir !== 'string' || !configDir || path.isAbsolute(configDir) || configDir.split(/[\\/]/).some(part => !part || part === '.' || part === '..') || !/^[a-z0-9-]+$/.test(pluginId)) throw new Error('Literature Atlas vault plugin location is invalid');
  let cursor = fs.realpathSync(vaultBase);
  requireDirectory(cursor);
  for (const segment of [...configDir.split(/[\\/]/), 'plugins', pluginId]) {
    cursor = path.join(cursor, segment);
    requireDirectory(cursor);
  }
  return cursor;
}

function ensureDirectory(directory) {
  try { fs.mkdirSync(directory, {mode: 0o700}); } catch (error) { if (error.code !== 'EEXIST') throw error; }
  requireDirectory(directory);
}

function verifyInstalled(root, files) {
  requireDirectory(root);
  for (const file of files) {
    let cursor = root;
    const segments = file.path.split('/');
    for (const segment of segments.slice(0, -1)) { cursor = path.join(cursor, segment); requireDirectory(cursor); }
    const target = path.join(cursor, segments.at(-1));
    const stat = fs.lstatSync(target);
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size > MAX_FILE_BYTES || sha256(fs.readFileSync(target)) !== file.sha256) throw new Error('Literature Atlas installed runtime integrity check failed');
  }
}

function extractBundle(pluginDir, bundle) {
  validateBundle(bundle);
  requireDirectory(pluginDir);
  const base = fs.realpathSync(pluginDir);
  requireDirectory(base);
  const runtime = path.join(base, '.runtime');
  ensureDirectory(runtime);
  const destination = path.join(runtime, bundle.digest);
  if (fs.existsSync(destination)) { verifyInstalled(destination, bundle.files); return destination; }
  const staging = fs.mkdtempSync(path.join(runtime, '.atlas-stage-'));
  try {
    for (const file of bundle.files) {
      let cursor = staging;
      const segments = file.path.split('/');
      for (const segment of segments.slice(0, -1)) { cursor = path.join(cursor, segment); ensureDirectory(cursor); }
      fs.writeFileSync(path.join(cursor, segments.at(-1)), file.content, {encoding: 'utf8', mode: 0o600, flag: 'wx'});
    }
    verifyInstalled(staging, bundle.files);
    try { fs.renameSync(staging, destination); } catch (error) { if (!['EEXIST', 'ENOTEMPTY'].includes(error.code)) throw error; verifyInstalled(destination, bundle.files); }
    return destination;
  } finally { if (fs.existsSync(staging)) fs.rmSync(staging, {recursive: true, force: true}); }
}

function resolveRuntime(pluginDir, bundle) {
  if (bundle !== undefined) return extractBundle(pluginDir, bundle);
  try {
    requireDirectory(pluginDir);
    const base = fs.realpathSync(pluginDir);
    for (const relative of ['bridge/atlas/__main__.py', 'web/index.html', 'web/app.js']) {
      let cursor = base;
      const segments = relative.split('/');
      for (const part of segments.slice(0, -1)) { cursor = path.join(cursor, part); requireDirectory(cursor); }
      const stat = fs.lstatSync(path.join(cursor, segments.at(-1)));
      if (!stat.isFile() || stat.isSymbolicLink()) throw new Error('unsafe resource');
    }
    return base;
  } catch { throw new Error('Literature Atlas packaged resources are unavailable'); }
}

module.exports = {validateBundle, extractBundle, resolveRuntime, resolvePluginDir, MAX_FILES, MAX_FILE_BYTES, MAX_TOTAL_BYTES};
