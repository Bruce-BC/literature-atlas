import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from atlas.graph import MAX_REDIRECT_CHAIN, MAX_TAXONOMY_REDIRECTS_PER_KIND
from atlas.server import AtlasHTTPServer
from atlas.storage import SQLiteStore
from atlas.sync import SyncManager
from atlas.zotero import ZoteroClient


def test_resolver_survives_restart_and_stale_writes_are_conflicts(tmp_path):
    fixture = json.loads((Path(__file__).parents[1] / 'fixtures/snapshot.json').read_text())
    old = 'concept:fixture-server:epoch-001:old-kl'
    canonical = 'concept:fixture-server:epoch-001:kl'
    chain = 'concept:fixture-server:epoch-001:chain'
    cycle_a, cycle_b = 'concept:fixture-server:epoch-001:cycle-a', 'concept:fixture-server:epoch-001:cycle-b'
    dangling = 'concept:fixture-server:epoch-001:dangling'
    foreign_hop = 'concept:fixture-server:epoch-001:foreign-hop'
    fixture['taxonomyRedirects'] = {old: canonical, chain: old, cycle_a: cycle_b, cycle_b: cycle_a,
                                    dangling: 'concept:fixture-server:epoch-001:absent', foreign_hop: 'concept:other:epoch-001:elsewhere'}
    store = SQLiteStore(tmp_path)
    manager = SyncManager(store, ZoteroClient(), demo_snapshot=fixture, embedding_provider='fake')
    manager.sync(); store.close_all()
    store = SQLiteStore(tmp_path)
    manager = SyncManager(store, ZoteroClient(), demo_snapshot=fixture, embedding_provider='fake')
    manager.sync()
    server = AtlasHTTPServer(('127.0.0.1', 0), manager)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    def call(path, method='GET', body=None, revision=0):
        request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}' + path, method=method,
            data=None if body is None else json.dumps(body).encode(), headers={'Authorization': 'Bearer ' + server.token, 'Content-Type': 'application/json', 'If-Match': str(revision)})
        try:
            with urllib.request.urlopen(request) as response: return response.status, json.load(response)
        except urllib.error.HTTPError as error: return error.code, json.load(error)
    try:
        before = store.read_json('taxonomy')
        status, data = call('/api/v1/taxonomy/resolve?' + urllib.parse.urlencode({'entityType': 'concept', 'entityId': old}))
        assert status == 200 and data['canonicalId'] == canonical and data['redirected'] is True
        assert data['sourceId'] == 'fixture-server' and data['sourceEpoch'] == 'epoch-001'
        redirects = call('/api/v1/taxonomy')[1]['redirects']
        assert redirects[old] == canonical and redirects[chain] == canonical
        for invalid in (cycle_a, cycle_b, dangling, foreign_hop):
            assert invalid not in redirects
            assert call('/api/v1/taxonomy/resolve?' + urllib.parse.urlencode({'entityType':'concept', 'entityId':invalid}))[0] == 404
        status, live = call('/api/v1/taxonomy/resolve?' + urllib.parse.urlencode({'entityType':'concept', 'entityId':canonical}))
        assert status == 200 and live['redirected'] is False
        status, stale = call('/api/v1/taxonomy', 'PUT', {'sourceId':'fixture-server', 'sourceEpoch':'epoch-001', 'entityId':old, 'patch':{'aliases':['new']}})
        assert status == 409 and stale['canonicalId'] == canonical
        status, data = call('/api/v1/overlays', 'PUT', {'sourceId':'fixture-server', 'sourceEpoch':'epoch-001', 'entityType':'concept', 'entityId':old, 'patch':{'pinned':True}})
        assert status == 409 and data['canonicalId'] == canonical
        assert store.read_json('taxonomy') == before and store.read_json('overlays')[0] == 0
        foreign = 'concept:other:epoch-001:old-kl'
        assert call('/api/v1/taxonomy/resolve?' + urllib.parse.urlencode({'entityType':'concept', 'entityId':foreign}))[0] == 404
        assert call('/api/v1/taxonomy/resolve?' + urllib.parse.urlencode({'entityType':'field', 'entityId':old}))[0] == 404
        over = dict(fixture)
        over['taxonomyRedirects'] = {old: canonical, **{f'concept:fixture-server:epoch-001:extra-{i}': canonical for i in range(MAX_TAXONOMY_REDIRECTS_PER_KIND)}}
        store.publish_snapshot(over, int(store.state()['generation']))
        status, data = call('/api/v1/taxonomy/resolve?' + urllib.parse.urlencode({'entityType':'concept', 'entityId':old}))
        assert status == 503 and 'limit exceeded' in data['error']
        status, _ = call('/api/v1/overlays', 'PUT', {'sourceId':'fixture-server', 'sourceEpoch':'epoch-001', 'entityType':'concept', 'entityId':canonical, 'patch':{'pinned':True}})
        assert status == 503 and store.read_json('overlays')[0] == 0
        chain_items = [f'concept:fixture-server:epoch-001:link-{i}' for i in range(MAX_REDIRECT_CHAIN + 1)]
        over['taxonomyRedirects'] = {old: canonical, **{chain_items[i]: chain_items[i+1] for i in range(len(chain_items)-1)}, chain_items[-1]: canonical}
        store.publish_snapshot(over, int(store.state()['generation']))
        status, data = call('/api/v1/taxonomy/resolve?' + urllib.parse.urlencode({'entityType':'concept', 'entityId':old}))
        assert status == 503 and 'chain exceeds' in data['error']
        assert store.read_json('taxonomy') == before and store.read_json('overlays')[0] == 0
    finally:
        server.shutdown(); server.server_close(); thread.join(); store.close_all()
