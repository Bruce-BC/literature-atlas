import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from atlas.server import AtlasHTTPServer
from atlas.storage import SQLiteStore
from atlas.sync import SyncManager
from atlas.zotero import ZoteroClient


@pytest.fixture
def api(tmp_path):
    snapshot = json.loads((Path(__file__).parents[1] / "fixtures/snapshot.json").read_text())
    manager = SyncManager(SQLiteStore(tmp_path), ZoteroClient(), demo_snapshot=snapshot, embedding_provider="fake")
    manager.sync()
    server = AtlasHTTPServer(("127.0.0.1", 0), manager)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def post(label, revision=0, *, source="fixture-server", epoch="epoch-001", kind="concept", parents=None, aliases=None):
        body = {"sourceId": source, "sourceEpoch": epoch, "entityType": kind, "label": label, "aliases": aliases or [], "parentIds": parents or []}
        req = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/api/v1/taxonomy", data=json.dumps(body).encode(), headers={"Authorization": "Bearer " + server.token, "Content-Type": "application/json", "If-Match": str(revision)}, method="POST")
        try:
            with urllib.request.urlopen(req) as response: return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)
    yield manager, post
    server.shutdown(); server.server_close(); thread.join(); manager.store.close_all()


def test_builtin_duplicate_is_conflict_and_does_not_mutate_taxonomy(api):
    manager, post = api
    before = manager.store.read_json("taxonomy")
    status, result = post("KL divergence")
    assert status == 409
    assert result["existingEntityId"] == "concept:fixture-server:epoch-001:kl"
    assert manager.store.read_json("taxonomy") == before


def test_cross_source_and_epoch_are_rejected_before_write(api):
    manager, post = api
    before = manager.store.read_json("taxonomy")
    assert post("Unique", source="other")[0] == 400
    assert post("Unique", epoch="old")[0] == 400
    assert manager.store.read_json("taxonomy") == before


@pytest.mark.parametrize("label", ["ＫＬ—divergence", "Kullback–Leibler divergence", "relative entropy", "KL 발산"])
def test_curated_unicode_aliases_match_builtin_without_writes(api, label):
    manager, post = api
    before = manager.store.read_json("taxonomy")
    status, result = post(label)
    assert status == 409
    assert result["existingEntityId"] == "concept:fixture-server:epoch-001:kl"
    assert manager.store.read_json("taxonomy") == before


def test_different_field_parent_contexts_allow_homonyms(api):
    manager, post = api
    first = "field:fixture-server:epoch-001:analysis"
    second = "field:fixture-server:epoch-001:topology"
    assert post("Operator", kind="field", parents=[first])[0] == 200
    assert post("Operator", revision=1, kind="field", parents=[second])[0] == 200
    rows = manager.store.read_json("taxonomy")[1]["fields"]
    assert len(rows) == 2 and rows[0]["id"] != rows[1]["id"]
    assert post("operator", revision=2, kind="field", parents=[first])[0] == 409


def test_concurrent_equivalent_creates_cannot_both_commit(api):
    from concurrent.futures import ThreadPoolExecutor
    manager, post = api
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda label: post(label), ["A new concept", "a new concept"]))
    assert sorted(status for status, _ in results) == [200, 409]
    revision, taxonomy, _ = manager.store.read_json("taxonomy")
    assert revision == 1 and len(taxonomy["concepts"]) == 1


def test_pending_graph_still_checks_persisted_taxonomy(api):
    manager, post = api
    manager.request_reindex = lambda **kwargs: None
    assert post("Unindexed concept")[0] == 200
    before = manager.store.read_json("taxonomy")
    status, result = post("UNINDEXED concept", revision=1)
    assert status == 409 and result["existingEntityId"] == before[1]["concepts"][0]["id"]
    assert manager.store.read_json("taxonomy") == before


def test_provider_explicit_override_and_offline_model_policy(tmp_path):
    store = SQLiteStore(tmp_path)
    store.cas_json("settings", {"provider": "tfidf"}, 0)
    client = ZoteroClient()
    explicit = SyncManager(store, client, embedding_provider="e5")
    assert explicit.embedding_provider == "e5"
    assert explicit._engine().allow_download is False
    omitted = SyncManager(store, client)
    assert omitted.embedding_provider == "tfidf"
    store.close_all()


def test_provider_default_without_saved_setting_is_tfidf(tmp_path):
    store = SQLiteStore(tmp_path)
    assert SyncManager(store, ZoteroClient()).embedding_provider == "tfidf"
    store.close_all()


def test_saved_e5_and_explicit_tfidf_override(tmp_path):
    from atlas.cli import build_parser
    store = SQLiteStore(tmp_path)
    store.cas_json("settings", {"provider": "e5"}, 0)
    assert build_parser().parse_args(["serve"]).embedding_provider is None
    assert SyncManager(store, ZoteroClient()).embedding_provider == "e5"
    assert SyncManager(store, ZoteroClient(), embedding_provider="tfidf").embedding_provider == "tfidf"
    assert build_parser().parse_args(["serve", "--embedding-provider", "e5"]).embedding_provider == "e5"
    store.close_all()


def test_root_and_child_homonyms_remain_distinct(api):
    _, post = api
    parent = "field:fixture-server:epoch-001:analysis"
    assert post("Operator", kind="field")[0] == 200
    assert post("Operator", revision=1, kind="field", parents=[parent])[0] == 200


def test_e5_missing_cache_fails_local_only_without_network(tmp_path, monkeypatch):
    import socket
    import sys
    import types
    from atlas.embeddings import EmbeddingProvider
    calls = []
    def missing_model(*args, **kwargs):
        calls.append(kwargs)
        assert kwargs["local_files_only"] is True
        assert kwargs["model_kwargs"]["local_files_only"] is True
        assert kwargs["trust_remote_code"] is False
        raise FileNotFoundError("model not cached")
    monkeypatch.setitem(sys.modules, "sentence_transformers", types.SimpleNamespace(SentenceTransformer=missing_model))
    monkeypatch.setattr(socket.socket, "connect", lambda *args: pytest.fail("network access attempted"))
    provider = EmbeddingProvider(tmp_path, provider="e5", allow_download=False)
    provider._load_model()
    assert len(calls) == 1
    assert provider._model is None and "FileNotFoundError" in provider._model_error


def test_arbitrary_alias_uses_same_identity_rule_as_graph(api):
    manager, post = api
    assert post("Spectral thing", aliases=["Hidden synonym"])[0] == 200
    before = manager.store.read_json("taxonomy")
    status, result = post("hidden synonym", revision=1)
    assert status == 409 and result["existingEntityId"] == before[1]["concepts"][0]["id"]
    assert manager.store.read_json("taxonomy") == before
