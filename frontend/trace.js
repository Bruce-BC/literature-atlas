/**
 * Minimal, bounded, privacy-safe LOCAL transition trace (TAXV2-036).
 *
 * - ring buffer (default 200 entries, oldest dropped);
 * - every entry type has a fixed whitelist of fields with a typed validator:
 *   finite non-negative integers, booleans, or short strings that match a closed enum
 *   or a strict identifier pattern.  Anything else (unknown keys, nested objects,
 *   arrays, free text, paths, tokens, item IDs, titles/abstracts/spans, user labels)
 *   is DROPPED, never truncated or logged;
 * - no whole-object logging, no console output, no network: `read()` returns a deep
 *   copy for local inspection only.
 */
const int = value => Number.isInteger(value) && value >= 0 && value <= 1e9;
const bool = value => typeof value === 'boolean';
const oneOf = (...values) => value => typeof value === 'string' && values.includes(value);
// Exactly the backend recipe version form (atlas-graph-v<1-3 ASCII digits>), whole value only: no suffix, token, whitespace or newline.
const recipe = value => typeof value === 'string' && /^atlas-graph-v[0-9]{1,3}$/.test(value);

const EVENTS = ['FIELD_SELECT', 'PAPER_SELECT', 'OVERVIEW_PAPER_SELECT', 'CONCEPT_SELECT', 'EDGE_SELECT', 'REPRESENTATIVE_TOGGLE', 'RESET', 'SNAPSHOT_REPLACE', 'FIELD_PAIR_SELECT', 'CLOUD_SELECT', 'CLOUD_SUBITEM_SELECT'];
const KINDS = ['none', 'field', 'paper', 'concept', 'edge'];
const SCOPES = ['library', 'field', 'cloud'];
const CAMERA = ['preserve', 'fit-new-scope', 'global', 'fit', 'reset'];
const VIEWS = ['overview', 'paper', 'concept'];

export const TRACE_SCHEMA = Object.freeze({
  reducer: {event: oneOf(...EVENTS), revision: int, selectedKind: oneOf(...KINDS), scopeKind: oneOf(...SCOPES), conceptConstraint: bool, cameraReason: oneOf(...CAMERA), expandedCount: int, changed: bool},
  snapshot: {sameSource: bool, recipe, papers: int, concepts: int, fields: int},
  render: {revision: int, eligiblePapers: int, visiblePapers: int, paperNodes: int, paperEdges: int, conceptNodes: int, conceptEdges: int, overviewNodes: int, candidateEdges: int, eligibleEdges: int},
  g6: {view: oneOf(...VIEWS), created: int, topologyChanges: int, styleUpdates: int, layoutRuns: int, layoutsFinished: int, failedUpdates: int},
  graph: {view: oneOf(...VIEWS), reused: bool, fitNewContent: bool}
});

const OPTIONAL = Object.freeze({snapshot: ['recipe']});

export function createTrace({capacity = 200} = {}) {
  const limit = Math.max(1, Math.min(1000, Math.floor(capacity)));
  const ring = []; let sequence = 0; let dropped = 0; let rejected = 0;
  function sanitize(type, fields) {
    const schema = TRACE_SCHEMA[type]; if (!schema || !fields || typeof fields !== 'object' || Array.isArray(fields)) return null;
    const clean = {};
    for (const [key, validate] of Object.entries(schema)) {
      const present = Object.prototype.hasOwnProperty.call(fields, key);
      if (present && validate(fields[key])) clean[key] = fields[key];
      else if (!(OPTIONAL[type] || []).includes(key) || present) return null; // a required field is missing/invalid, or an optional one is present but invalid: reject the whole entry, never log it partially
    }
    return clean;
  }
  return {
    emit(type, fields) {
      const clean = sanitize(type, fields);
      if (!clean) { rejected += 1; return false; }
      sequence += 1; ring.push({seq: sequence, type, ...clean});
      if (ring.length > limit) { ring.shift(); dropped += 1; }
      return true;
    },
    read() { return {capacity: limit, retained: ring.length, dropped, rejected, entries: ring.map(entry => ({...entry}))}; },
    clear() { ring.length = 0; }
  };
}

export const trace = createTrace();
if (typeof window !== 'undefined') window.AtlasTrace = Object.freeze({read: () => trace.read()});
