'use strict';

const crypto = require('node:crypto');
const path = require('node:path');

const LOOPBACK_HOST = '127.0.0.1';
const OBSIDIAN_ORIGIN = 'app://obsidian.md';
const BOOTSTRAP_KEYS = Object.freeze(['launchId', 'pid', 'port', 'token']);

function exactKeys(value, keys) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const actual = Object.keys(value).sort();
  return actual.length === keys.length && actual.every((key, index) => key === [...keys].sort()[index]);
}

function parseBootstrap(line) {
  if (typeof line !== 'string' || line.length > 4096) throw new Error('invalid bridge bootstrap');
  let value;
  try { value = JSON.parse(line); } catch { throw new Error('invalid bridge bootstrap JSON'); }
  if (!exactKeys(value, BOOTSTRAP_KEYS)) throw new Error('bridge bootstrap keys mismatch');
  if (!Number.isInteger(value.port) || value.port < 1 || value.port > 65535) throw new Error('invalid bridge port');
  if (!Number.isInteger(value.pid) || value.pid < 1) throw new Error('invalid bridge pid');
  if (typeof value.token !== 'string' || value.token.length < 16 || value.token.length > 512) throw new Error('invalid bridge token');
  if (typeof value.launchId !== 'string' || !/^[A-Za-z0-9._:-]{8,256}$/.test(value.launchId)) throw new Error('invalid bridge launch id');
  return Object.freeze(value);
}

function makeLaunchId(randomBytes = crypto.randomBytes) {
  return randomBytes(18).toString('hex');
}

function makeLoopbackUrl(port, pathname = '/') {
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('invalid loopback port');
  if (typeof pathname !== 'string' || pathname.includes('#') || !pathname.startsWith('/') || /[?&](?:token|auth|authorization|bearer)=/i.test(pathname)) {
    throw new Error('bridge URL must not carry credentials');
  }
  return `http://${LOOPBACK_HOST}:${port}${pathname}`;
}

function isLoopbackOrigin(origin, port) {
  try {
    const url = new URL(origin);
    return url.protocol === 'http:' && url.hostname === LOOPBACK_HOST && Number(url.port) === Number(port) && url.username === '' && url.password === '' && url.search === '' && url.hash === '';
  } catch { return false; }
}

function sanitizeDiagnostic(value, secrets = []) {
  let text = String(value ?? '');
  for (const secret of secrets) if (secret) text = text.split(String(secret)).join('[redacted]');
  text = text.replace(/(authorization\s*:\s*bearer\s+|bearer\s+)[^\s,;]+/gi, '$1[redacted]');
  text = text.replace(/([?&]token=)[^&#\s]+/gi, '$1[redacted]');
  // Diagnostics must not become a source of local-vault or home-path disclosure.
  text = text.replace(/(?:\/Users|\/home|\/private|\/tmp|\/var|[A-Za-z]:\\)[^\s"']+/g, '[path-redacted]');
  return text.slice(0, 4096);
}

function isAllowedZoteroUrl(value) {
  if (typeof value !== 'string' || value.length > 2048 || /[\r\n]/.test(value)) return false;
  try {
    const url = new URL(value);
    if (url.protocol === 'zotero:') {
      return /^(select|open-pdf)$/.test(url.hostname) && /^(?:\/library\/items\/|\/groups\/[0-9]{1,16}\/items\/)[A-Za-z0-9]{1,64}$/.test(url.pathname) && !url.search && !url.hash;
    }
    if (url.protocol === 'http:' || url.protocol === 'https:') {
      return (url.hostname === '127.0.0.1' || url.hostname === 'localhost') && !url.username && !url.password;
    }
    return false;
  } catch { return false; }
}

function safeManagedPath(root, id) {
  if (typeof root !== 'string' || !path.isAbsolute(root)) throw new Error('managed export root must be absolute');
  if (typeof id !== 'string' || !id) throw new Error('managed export id required');
  const target = path.resolve(root, 'Literature Atlas', 'Managed', id);
  const managedRoot = path.resolve(root, 'Literature Atlas', 'Managed') + path.sep;
  if (!target.startsWith(managedRoot)) throw new Error('managed export escaped root');
  return target;
}

module.exports = {
  BOOTSTRAP_KEYS,
  LOOPBACK_HOST,
  OBSIDIAN_ORIGIN,
  exactKeys,
  parseBootstrap,
  makeLaunchId,
  makeLoopbackUrl,
  isLoopbackOrigin,
  sanitizeDiagnostic,
  isAllowedZoteroUrl,
  safeManagedPath,
  parseBootstrapLine: parseBootstrap,
  isAllowedExternalUrl: isAllowedZoteroUrl
};
