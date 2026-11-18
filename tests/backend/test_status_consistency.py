import json
from pathlib import Path

from atlas.storage import SQLiteStore
from atlas.sync import GRAPH_RECIPE_VERSION, SyncManager
from atlas.zotero import ZoteroClient


def test_cached_status_restores_only_matching_ready_model(tmp_path):
    fixture = json.loads((Path(__file__).parents[1] / 'fixtures/snapshot.json').read_text())
    store = SQLiteStore(tmp_path)
    state, _ = store.ensure_source('fixture-server', 44, epoch_override='epoch-001')
    fixture['modelStatus'] = {'provider':'e5', 'status':'ready', 'dimensions':384, 'recipeVersion':GRAPH_RECIPE_VERSION}
    store.publish_snapshot(fixture, state['generation'])
    store.close_all()
    store = SQLiteStore(tmp_path)
    manager = SyncManager(store, ZoteroClient(), embedding_provider='e5')
    assert manager.status['modelStatus'] == fixture['modelStatus']
    assert manager.status['stale'] is True  # cached status does not assert online sync
    class Busy:
        def done(self): return False
    manager._future = Busy()
    assert manager.status['modelStatus']['state'] == 'pending'
    assert manager.status['indexing'] is True
    manager._future = None
    other = SyncManager(store, ZoteroClient(), embedding_provider='tfidf')
    assert other.status['modelStatus']['state'] == 'pending'
    manager._set_status(modelStatus={'provider':'e5','state':'error'})
    assert manager.status['modelStatus']['state'] == 'error'
    store.ensure_source('another-server', 44)
    fresh = SyncManager(store, ZoteroClient(), embedding_provider='e5')
    assert fresh.status['modelStatus']['state'] == 'pending'
    store.close_all()


def test_enrichment_failure_publishes_coherent_status_and_ignores_old_generation(tmp_path):
    fixture = json.loads((Path(__file__).parents[1] / 'fixtures/snapshot.json').read_text())
    store = SQLiteStore(tmp_path)
    state, _ = store.ensure_source('fixture-server', 44, epoch_override='epoch-001')
    store.publish_snapshot(fixture, state['generation'])
    class Boom:
        def build(self, *args, **kwargs): raise RuntimeError('private error content')
    manager = SyncManager(store, ZoteroClient(), embedding_provider='e5', graph_engine=Boom())
    manager._set_status(phase='idle', stale=False, message=None)
    manager._enrich(state['generation'], state)
    snapshot = manager.snapshot()
    status = manager.status
    assert snapshot['status']['phase'] == status['phase'] == 'error'
    assert snapshot['status']['stale'] is status['stale'] is True
    assert status['lastError'] == 'RuntimeError' and 'private error content' not in str(status)
    # A fast worker may fail before sync() writes its final idle status.
    manager._set_status(phase='idle', stale=False, message=None)
    assert manager.status['phase'] == 'error' and manager.status['stale'] is True
    # A true source outage keeps its own offline state.
    manager._set_status(phase='offline', stale=True, message='source unavailable')
    assert manager.status['phase'] == 'offline'
    before = manager.status
    store.bump_generation()
    manager._enrich(state['generation'], state)
    assert manager.status == before
    store.close_all()
