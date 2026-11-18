import {decodeSnapshot} from './decoder.js';

const safeOrigin = origin => /^https?:\/\/127\.0\.0\.1(?::\d+)?$/.test(origin) || /^https?:\/\/localhost(?::\d+)?$/.test(origin);
const safeUrl = value => {
  try { const url = new URL(value, location.href); return ['http:', 'https:', 'zotero:'].includes(url.protocol) ? url.href : null; } catch { return null; }
};
export function isCompatibleSnapshot(current, next) { return Boolean(current?.source?.serverId && next?.source?.serverId && current.source.serverId === next.source.serverId && current.source.epoch === next.source.epoch); }
export function buildEntityPatch(baseline = {}, current = {}) { const patch = {}; for (const key of ['aliases', 'fieldIds', 'pinned']) if (JSON.stringify(baseline[key] ?? null) !== JSON.stringify(current[key] ?? null)) patch[key] = current[key]; return patch; }

export class AtlasApi {
  constructor({baseUrl = location.origin, token = ''} = {}) {
    this.baseUrl = baseUrl.replace(/\/$/, '');
    this.token = token;
    this.revision = -1;
    if (!safeOrigin(this.baseUrl) && this.baseUrl !== location.origin) throw new Error('Atlas API must use a loopback origin');
  }
  headers() { return this.token ? {'Authorization': `Bearer ${this.token}`, 'Accept': 'application/json'} : {'Accept': 'application/json'}; }
  async request(path, options = {}) {
    const response = await fetch(`${this.baseUrl}${path}`, {...options, headers: {...this.headers(), ...(options.headers || {})}});
    if (!response.ok) throw new Error(`Atlas request failed (${response.status})`);
    return response.json();
  }
  health() { return this.request('/health'); }
  status(options = {}) { return this.request('/api/v1/status', options); }
  async snapshot({projection = 'full', sample = 1, signal} = {}) {
    const raw = await this.request(`/api/v1/snapshot?projection=${encodeURIComponent(projection)}&sample=${encodeURIComponent(sample)}`, {signal});
    return decodeSnapshot(raw);
  }
  paper(id) { return this.request(`/api/v1/papers/${encodeURIComponent(id)}`); }
  attachmentUrl(id) { return this.request(`/api/v1/attachments/${encodeURIComponent(id)}/url`).then(body => safeUrl(body.url || body.target)); }
  settings() { return this.request('/api/v1/settings'); }
  taxonomy() { return this.request('/api/v1/taxonomy'); }
  overlays() { return this.request('/api/v1/overlays'); }
  updateSettings(settings, revision) { const payload = {provider: settings.provider, threshold: settings.threshold, topK: settings.topK, includeIndexedText: Boolean(settings.includeIndexedText), openAlex: Boolean(settings.openAlex)}; return this.request('/api/v1/settings', {method: 'PUT', headers: {'Content-Type': 'application/json', 'If-Match': String(revision)}, body: JSON.stringify({settings: payload, revision})}); }
  createTaxonomy(taxonomy, revision) { return this.request('/api/v1/taxonomy', {method: 'POST', headers: {'Content-Type': 'application/json', 'If-Match': String(revision)}, body: JSON.stringify({taxonomy, revision})}); }
  updateTaxonomy(entityPatch, revision) { return this.request('/api/v1/taxonomy', {method: 'PUT', headers: {'Content-Type': 'application/json', 'If-Match': String(revision)}, body: JSON.stringify({...entityPatch, revision})}); }
  updateOverlay(entityPatch, revision) { return this.request('/api/v1/overlays', {method: 'PUT', headers: {'Content-Type': 'application/json', 'If-Match': String(revision)}, body: JSON.stringify({...entityPatch, revision})}); }
}

export const AtlasApiTools = {safeUrl, isCompatibleSnapshot, buildEntityPatch};
