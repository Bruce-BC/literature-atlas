// Preserve only positions that still belong to the current source snapshot.
export function updateG6Views(previousSource, views, snapshot, captures = {}) {
  const source = JSON.stringify([snapshot.source?.serverId, snapshot.source?.epoch]);
  const same = source === previousSource;
  const valid = {
    paper: new Set(snapshot.papers.map(item => item.id)),
    overview: new Set(snapshot.fields.map(item => item.id)),
    concept: new Set([...snapshot.papers, ...snapshot.concepts].map(item => item.id))
  };
  const next = {};
  for (const kind of Object.keys(valid)) {
    const old = same ? views[kind] : null;
    const captured = same ? captures[kind] : null;
    if (!old && !captured) continue;
    const positions = {...old?.positions, ...captured?.positions};
    for (const id of Object.keys(positions)) if (!valid[kind].has(id)) delete positions[id];
    next[kind] = {...old, ...captured, positions};
  }
  return {source, views: next};
}
