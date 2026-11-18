'use strict';

const crypto = require('node:crypto');
const path = require('node:path');
const {safeManagedPath} = require('./security.cjs');

const START = '<!-- literature-atlas:managed:start -->';
const END = '<!-- literature-atlas:managed:end -->';
const OWNER = '<!-- literature-atlas:managed -->';

function stableExportId(paperId) {
  if (typeof paperId !== 'string' || !paperId || paperId.length > 512) throw new Error('paper id required');
  return `paper-${crypto.createHash('sha256').update(paperId, 'utf8').digest('hex').slice(0, 32)}.md`;
}

function plainText(value, fallback = '') {
  return String(value ?? fallback).replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, '').replace(/\r?\n/g, ' ').trim();
}

function safeMarkdownText(value, fallback = '') {
  return plainText(value, fallback)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/([\\`*_\[\]#!|])/g, '\\$1');
}

function managedRegion(paper) {
  if (!paper || typeof paper !== 'object' || typeof paper.id !== 'string') throw new Error('paper record is required');
  const title = safeMarkdownText(paper.title, 'Untitled paper') || 'Untitled paper';
  const key = safeMarkdownText(paper.key);
  const year = Number.isInteger(paper.year) ? String(paper.year) : '';
  const authors = Array.isArray(paper.authors) ? paper.authors.map((author) => safeMarkdownText(author)).filter(Boolean).join(', ') : '';
  const id = safeMarkdownText(paper.id);
  const lines = [START, `title: ${title}`, key ? `zotero-key: ${key}` : null, year ? `year: ${year}` : null, authors ? `authors: ${authors}` : null, `paper-id: ${id}`, END].filter(Boolean);
  return lines.join('\n') + '\n';
}

function managedBody(paper) { return OWNER + '\n' + managedRegion(paper); }

function replaceOwnedRegion(existing, generated) {
  if (typeof existing !== 'string') return {content: generated, created: true, replaced: false};
  const start = existing.indexOf(START);
  const end = existing.indexOf(END);
  if (start < 0 || end < start) return {content: existing, created: false, replaced: false};
  const after = end + END.length;
  const replacement = generated.startsWith(OWNER + '\n') ? generated.slice((OWNER + '\n').length) : generated;
  return {content: existing.slice(0, start) + replacement.trimEnd() + existing.slice(after), created: false, replaced: true};
}

async function exportManagedPaper(options) {
  const {paper, root, adapter = {}, filePath: requestedPath} = options || {};
  const fileName = stableExportId(paper && paper.id);
  const ownedPath = safeManagedPath(root, fileName);
  const filePath = requestedPath || ownedPath;
  if (path.resolve(filePath) !== ownedPath) throw new Error('managed export path is not owned');
  const exists = typeof adapter.exists === 'function' ? await adapter.exists(filePath) : false;
  let existing = null;
  if (exists) {
    if (typeof adapter.read !== 'function') throw new Error('managed export adapter cannot read');
    existing = await adapter.read(filePath);
    // A user-created file at the stable path remains untouched unless it has our owner markers.
    if (!String(existing).includes(OWNER) && !String(existing).includes(START)) return {filePath, written: false, reason: 'unowned'};
  }
  const result = replaceOwnedRegion(existing, managedRegion(paper));
  if (!result.replaced && existing !== null) return {filePath, written: false, reason: 'unowned'};
  if (typeof adapter.write !== 'function') throw new Error('managed export adapter cannot write');
  if (typeof adapter.mkdir === 'function') await adapter.mkdir(path.dirname(filePath));
  await adapter.write(filePath, result.content);
  return {filePath, written: true, created: result.created, replaced: result.replaced};
}

async function exportManagedVaultPaper({paper, vault}) {
  if (!vault || typeof vault.getAbstractFileByPath !== 'function') throw new Error('vault API unavailable');
  const filePath = path.posix.join('Literature Atlas', 'Managed', stableExportId(paper && paper.id));
  const existingFile = vault.getAbstractFileByPath(filePath);
  let result;
  if (existingFile) {
    if (typeof vault.read !== 'function' || typeof vault.modify !== 'function') throw new Error('vault API cannot update managed export');
    result = replaceOwnedRegion(await vault.read(existingFile), managedRegion(paper));
    if (!result.replaced) return {filePath, written: false, reason: 'unowned'};
    await vault.modify(existingFile, result.content);
    return {filePath, written: true, created: false, replaced: true};
  }
  if (typeof vault.create !== 'function') throw new Error('vault API cannot create managed export');
  if (typeof vault.createFolder === 'function') {
    if (!vault.getAbstractFileByPath('Literature Atlas')) await vault.createFolder('Literature Atlas');
    if (!vault.getAbstractFileByPath('Literature Atlas/Managed')) await vault.createFolder('Literature Atlas/Managed');
  }
  await vault.create(filePath, managedBody(paper));
  return {filePath, written: true, created: true, replaced: false};
}

module.exports = {START, END, OWNER, stableExportId, plainText, safeMarkdownText, managedRegion, managedBody, replaceOwnedRegion, exportManagedPaper, exportManagedVaultPaper};
