import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

from atlas.server import AtlasHTTPServer
from atlas.storage import SQLiteStore
from atlas.sync import SyncManager
from atlas.zotero import ZoteroClient


def test_demo_settings_save_reload_and_explicit_provider_restart(tmp_path):
    fixture = json.loads((Path(__file__).parents[1] / 'fixtures/snapshot.json').read_text())
    store = SQLiteStore(tmp_path)
    manager = SyncManager(store, ZoteroClient(), demo_snapshot=fixture, embedding_provider='fake')
    manager.sync()
    def start(current):
        server = AtlasHTTPServer(('127.0.0.1', 0), current)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        return server, thread
    def call(server, method='GET', body=None, revision=0):
        req = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/v1/settings', method=method, data=None if body is None else json.dumps(body).encode(), headers={'Authorization': 'Bearer ' + server.token, 'Content-Type': 'application/json', 'If-Match': str(revision)})
        try:
            with urllib.request.urlopen(req) as response: return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)
    server, thread = start(manager)
    try:
        assert call(server)[1]['settings']['provider'] == 'tfidf'
        wanted = {'provider': 'tfidf', 'threshold': 0.91, 'topK': 7, 'includeIndexedText': True, 'openAlex': True}
        status, saved = call(server, 'PUT', {'settings': wanted}, revision=0)
        assert status == 200 and saved['revision'] == 1 and saved['settings'] == wanted
        assert call(server)[1]['settings'] == wanted
        assert call(server, 'PUT', {'settings': {'threshold': 0.5}}, revision=0)[0] == 409
        assert call(server)[1]['settings'] == wanted
    finally:
        server.shutdown(); server.server_close(); thread.join(); store.close_all()
    store = SQLiteStore(tmp_path)
    manager = SyncManager(store, ZoteroClient(), demo_snapshot=fixture, embedding_provider='e5')
    manager.sync()
    server, thread = start(manager)
    try:
        actual = call(server)[1]
        assert actual['revision'] == 1
        assert actual['settings'] == {**wanted, 'provider': 'e5'}
    finally:
        server.shutdown(); server.server_close(); thread.join(); store.close_all()
