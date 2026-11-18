import copy
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from jsonschema import validate

from atlas.graph import GraphEngine
from atlas.embeddings import EmbeddingProvider, document_text


def _fixture():
    return {
        "serverId": "graph-test-server",
        "epoch": "epoch-1",
        "alias": "test",
        "papers": [
            {"key": "A", "libraryID": 0, "title": "Optimal transport", "abstract": "graphs", "fieldIds": ["analysis", "topology"], "keywords": ["optimal transport", "KL divergence"]},
            {"key": "B", "libraryID": 0, "title": "Graph transport", "abstract": "graphs", "fieldIds": ["topology"], "keywords": ["optimal transport", "KL divergence"]},
            {"key": "C", "libraryID": 0, "title": "Klebsiella", "abstract": None, "fieldIds": [], "keywords": ["Klebsiella"]},
        ],
        "taxonomy": {"fields": [
            {"id": "analysis", "label": {"en": "Analysis", "ko": "해석학"}, "parentIds": []},
            {"id": "topology", "label": {"en": "Topology", "ko": "위상수학"}, "parentIds": ["analysis"]},
        ]},
    }


def _build(tmp_path, provider="fake", previous=None):
    fixture = _fixture()
    ids = [f"zotero:graph-test-server:0:{key}" for key in ("A", "B", "C")]
    return GraphEngine(tmp_path, provider=provider).build(
        fixture["papers"], source={"id": fixture["serverId"], **{k: fixture[k] for k in ("serverId", "epoch", "alias")}},
        taxonomy=fixture["taxonomy"],
        settings={"semanticThreshold": 0.95, "fakeVectors": {ids[0]: [1, 0], ids[1]: [0.99, 0.1], ids[2]: [0, 1]}},
        previous=previous,
    )


class GraphEngineTests(unittest.TestCase):
    def test_tokenizer_truncation_keeps_literal_query_prefix(self):
        class Tokenizer:
            def encode(self, text, **kwargs): return list(range(len(text.split())))
            def decode(self, ids, **kwargs): return "decoded content"
        prepared, truncated = document_text({"title": "title", "abstract": "long text"}, token_limit=2, tokenizer=Tokenizer())
        self.assertTrue(truncated)
        self.assertTrue(prepared.startswith("query: "))
        self.assertIn("[TRUNCATED]", prepared)

    def test_source_identity_is_backend_owned_and_required(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with self.assertRaises(ValueError):
                GraphEngine(path, provider="fake").build([{"key": "A", "title": "A"}], source={"serverId": "s"})
            self.assertFalse((path / "source-identity.json").exists())
            with self.assertRaises(ValueError):
                GraphEngine(path, provider="fake").build(
                    [], source={"id": "s", "serverId": "s", "epoch": "e1"},
                    taxonomy={"fields": [{"id": "field:s:old:foreign", "label": {"en": "Foreign"}}]},
                )

    def test_legacy_unicode_concept_id_migrates_without_dangling_references(self):
        source = {"id": "server", "serverId": "server", "epoch": "epoch"}
        old_id = "concept:server:epoch:한글-Ω/标签"
        paper_id = "zotero:server:0:A"
        previous = {
            "source": source,
            "fields": [],
            "concepts": [{"id": old_id, "label": {"en": "Legacy concept", "ko": "이전 개념"}, "aliases": ["legacy"], "source": "user"}],
            "papers": [{"id": paper_id, "key": "A", "conceptIds": [old_id]}],
            "memberships": [], "edges": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            snapshot = GraphEngine(Path(directory), provider="fake").build(
                [{"id": paper_id, "key": "A", "title": "A", "conceptIds": [old_id]}],
                source=source, previous=previous,
                overlays={"conceptAliases": {old_id: ["overlay alias"]}, "paperConcepts": {paper_id: [old_id]}, "pins": {old_id: True}},
                settings={"fakeVectors": {}},
            )
        concept = next(item for item in snapshot["concepts"] if item["label"]["en"] == "Legacy concept")
        self.assertNotEqual(concept["id"], old_id)
        self.assertTrue(concept["id"].startswith("concept:server:epoch:"))
        self.assertTrue(all(ord(char) < 128 for char in concept["id"]))
        self.assertIn(concept["id"], snapshot["papers"][0]["conceptIds"])
        self.assertEqual(concept["aliases"], ["legacy", "overlay alias"])
        self.assertTrue(concept["pinned"])
        with open(Path(__file__).parents[2] / "docs" / "snapshot.schema.json", encoding="utf-8") as schema_file:
            validate(snapshot, json.load(schema_file))

    def test_active_taxonomy_and_overlay_legacy_id_migrates_without_previous_snapshot(self):
        source = {"id": "server2", "serverId": "server2", "epoch": "epoch2"}
        old_id = "concept:server2:epoch2:生命科学 Ω"
        paper_id = "zotero:server2:0:A"
        taxonomy = {"concepts": [{"id": old_id, "label": {"en": "Life Science", "ko": "생명과학"}, "aliases": ["bio"], "source": "user"}]}
        overlays = {"conceptAliases": {old_id: ["overlay"]}, "paperConcepts": {paper_id: [old_id]}, "pins": {old_id: True}}
        with tempfile.TemporaryDirectory() as directory:
            snapshot = GraphEngine(Path(directory), provider="fake").build(
                [{"id": paper_id, "key": "A", "title": "A", "conceptIds": [old_id]}],
                source=source, taxonomy=taxonomy, overlays=overlays, settings={"fakeVectors": {}},
            )
        concept = next(item for item in snapshot["concepts"] if item["label"]["en"] == "Life Science")
        self.assertTrue(re.fullmatch(r"^[A-Za-z0-9:_./@+%-]+$", concept["id"]))
        self.assertIn(concept["id"], snapshot["papers"][0]["conceptIds"])
        self.assertIn("overlay", concept["aliases"])
        self.assertTrue(concept["pinned"])

    def test_graph_engines_reuse_provider_for_same_cache_partition(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            source = {"id": "reuse", "serverId": "reuse", "epoch": "e1"}
            first_engine = GraphEngine(path, provider="fake")
            first_engine.build([{"key": "A", "title": "A"}], source=source, settings={"fakeVectors": {}})
            second_engine = GraphEngine(path, provider="fake")
            second_engine.build([{"key": "B", "title": "B"}], source=source, settings={"fakeVectors": {}})
        self.assertIs(first_engine._provider_instance, second_engine._provider_instance)

    def test_corrupt_embedding_cache_recomputes_with_bounded_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            provider = EmbeddingProvider(path, provider="fake")
            first = provider.embed({"id": "A", "title": "cache", "abstract": "test"})
            cache_files = list((path / "embeddings").glob("*.json"))
            cache_files[0].write_text("{broken", encoding="utf-8")
            second = provider.embed({"id": "A", "title": "cache", "abstract": "test"})
        self.assertFalse(second.failed)
        self.assertEqual(first.input_hash, second.input_hash)
        self.assertEqual(provider._cache_error, "cache-read-JSONDecodeError")

    def test_loaded_tokenizer_failure_is_provider_failure_not_whitespace_ai(self):
        class BadTokenizer:
            def encode(self, *args, **kwargs): raise RuntimeError("tokenizer unavailable")
        with tempfile.TemporaryDirectory() as directory:
            provider = EmbeddingProvider(Path(directory), provider="e5", allow_download=False)
            provider._model = object()
            provider._tokenizer = BadTokenizer()
            result = provider.embed({"id": "A", "title": "title", "abstract": "abstract"})
        self.assertTrue(result.failed)
        self.assertEqual(result.dimensions, 0)
        self.assertIn("tokenizer-failure", result.error)

    def test_tfidf_uses_one_shared_corpus_basis_and_excludes_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = EmbeddingProvider(Path(directory), provider="tfidf")
            results = provider.embed_many([
                {"id": "aardvark", "title": "aardvark", "abstract": None},
                {"id": "zebra", "title": "zebra", "abstract": None},
                {"id": "shared", "title": "aardvark zebra", "abstract": None},
            ])
        self.assertEqual(len(results[0].vector), len(results[1].vector))
        cosine = sum(a * b for a, b in zip(results[0].vector, results[1].vector))
        self.assertLess(cosine, 0.5)
        self.assertEqual(results[0].provider, "tfidf")
        self.assertNotEqual(results[0].revision, "fixture-e5-revision")

    def test_build_is_pure_and_preserves_multimembership(self):
        fixture = _fixture()
        before = copy.deepcopy(fixture["papers"])
        with tempfile.TemporaryDirectory() as directory:
            snapshot = _build(Path(directory))
        self.assertEqual(fixture["papers"], before)
        paper = next(p for p in snapshot["papers"] if p["key"] == "A")
        self.assertEqual(len(paper["fieldIds"]), 2)
        self.assertTrue(paper["displayFieldId"].endswith(":analysis"))
        self.assertEqual(snapshot["allocationMode"], "primary-display-field")
        self.assertEqual(sum(row["paperWeight"] for row in snapshot["overview"]["fields"]), 3)

    def test_empty_taxonomy_gets_explainable_builtin_fields_and_unknown(self):
        papers = [
            {"key": "A", "title": "Optimal transport and entropy", "abstract": "information theory"},
            {"key": "B", "title": "An unrelated note", "abstract": ""},
        ]
        with tempfile.TemporaryDirectory() as directory:
            snapshot = GraphEngine(Path(directory), provider="fake").build(
                papers, source={"id": "builtin", "serverId": "builtin", "epoch": "e1"}, taxonomy={}, settings={"fakeVectors": {}}
            )
        labels = {field["label"]["en"] for field in snapshot["fields"]}
        self.assertIn("Optimization", labels)
        self.assertIn("Information Theory", labels)
        self.assertIn("Unclassified", labels)
        fields = {field["label"]["en"]: field for field in snapshot["fields"]}
        self.assertEqual(fields["Topology"]["parentIds"], [next(field["id"] for field in snapshot["fields"] if field["label"]["en"] == "Mathematics")])
        classified = next(p for p in snapshot["papers"] if p["key"] == "A")
        self.assertGreaterEqual(len(classified["fieldIds"]), 2)
        self.assertTrue(any(m["method"] == "model" and m["modelVersion"] == "term-rules-v1" and m["provenance"]["label"] == "rule-based" for m in snapshot["memberships"]))

    def test_indexed_attachment_does_not_upgrade_abstract_keyword_evidence(self):
        papers = [
            {"key": "A", "title": "A", "abstract": "metadata abstract", "textStatus": "indexed", "keywords": ["shared tag", "second tag"]},
            {"key": "B", "title": "B", "abstract": "metadata abstract", "textStatus": "indexed", "keywords": ["shared tag", "second tag"]},
        ]
        with tempfile.TemporaryDirectory() as directory:
            snapshot = GraphEngine(Path(directory), provider="fake").build(
                papers, source={"id": "tiers", "serverId": "tiers", "epoch": "e1"}, settings={"fakeVectors": {}}
            )
        metadata = [entry for paper in snapshot["papers"] for entry in paper["keywordMetadata"] if entry["label"] == "shared tag"]
        self.assertEqual({entry["evidenceTier"] for entry in metadata}, {"metadata"})
        keyword_edges = [edge for edge in snapshot["edges"] if edge["type"] == "keyword"]
        self.assertTrue(keyword_edges)
        self.assertEqual(keyword_edges[0]["evidenceTier"], "metadata")

    def test_edges_are_typed_and_canonical(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = _build(Path(directory))
        semantic = [edge for edge in snapshot["edges"] if edge["type"] == "semantic"]
        self.assertTrue(semantic)
        self.assertFalse(semantic[0]["directed"])
        self.assertLess(semantic[0]["source"], semantic[0]["target"])
        self.assertFalse(any(edge["type"] == "citation" for edge in snapshot["edges"]))
        keyword = [edge for edge in snapshot["edges"] if edge["type"] == "keyword"]
        self.assertTrue(keyword)
        self.assertTrue(keyword[0]["source"].startswith("concept:"))
        labels = {concept["label"]["en"] for concept in snapshot["concepts"]}
        self.assertIn("KL divergence", labels)
        self.assertIn("Klebsiella", labels)

    def test_explicit_citation_is_distinct_and_source_scoped(self):
        fixture = _fixture()
        papers = copy.deepcopy(fixture["papers"])
        papers[0]["references"] = [{"key": "B", "verified": True, "source": "pdf", "evidenceTier": "fulltext"}]
        with tempfile.TemporaryDirectory() as directory:
            snapshot = GraphEngine(Path(directory), provider="fake").build(
                papers, source={"id": "graph-test-server", "serverId": "graph-test-server", "epoch": "epoch-1"},
                taxonomy=fixture["taxonomy"], settings={"fakeVectors": {}}
            )
        citation = [edge for edge in snapshot["edges"] if edge["type"] == "citation"]
        self.assertEqual(len(citation), 1)
        self.assertTrue(citation[0]["directed"])
        self.assertEqual(citation[0]["evidenceTier"], "fulltext")
        self.assertEqual(citation[0]["provenance"]["provider"], "pdf-reference")

    def test_cache_hash_stable_and_provider_failure_keeps_semantic_edges(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            first = _build(path)
            second = _build(path, provider="e5", previous=first)
        self.assertEqual(first["modelStatus"]["inputHashes"], second["modelStatus"]["inputHashes"])
        if second["modelStatus"]["failed"]:
            self.assertTrue(any(edge["type"] == "semantic" and edge.get("stale") for edge in second["edges"]))

    def test_manual_alias_and_membership_survive_reindex_without_deduplication(self):
        fixture = _fixture()
        papers = copy.deepcopy(fixture["papers"][:2])
        taxonomy = copy.deepcopy(fixture["taxonomy"])
        taxonomy["concepts"] = [{"id": "user-kl", "label": {"en": "KL divergence", "ko": "KL 발산"}, "aliases": ["relative entropy"], "source": "user"}]
        overlays = {"conceptAliases": {"user-kl": ["KL", "relative entropy"]}, "paperFields": {"A": ["analysis", "topology"]}}
        with tempfile.TemporaryDirectory() as directory:
            engine = GraphEngine(Path(directory), provider="fake")
            first = engine.build(papers, source={"id": "graph-test-server", "serverId": "graph-test-server", "epoch": "epoch-1"}, taxonomy=taxonomy, overlays=overlays)
            second = engine.build(copy.deepcopy(papers), source={"id": "graph-test-server", "serverId": "graph-test-server", "epoch": "epoch-1"}, taxonomy=None, overlays=overlays, previous=first)
        first_a = next(p for p in first["papers"] if p["key"] == "A")
        second_a = next(p for p in second["papers"] if p["key"] == "A")
        self.assertEqual(first_a["fieldIds"], second_a["fieldIds"])
        self.assertEqual(first_a["conceptIds"], second_a["conceptIds"])
        self.assertEqual(len({m["fieldId"] for m in second["memberships"] if m["paperId"] == first_a["id"]}), 2)

    def test_backend_entity_overlay_alias_membership_and_pin_shape_is_consumed(self):
        papers = [{"key": "A", "title": "A", "fieldIds": []}]
        source = {"id": "entities", "serverId": "entities", "epoch": "e1"}
        paper_id = "zotero:entities:0:A"
        field_id = "field:entities:e1:analysis"
        concept_id = "concept:entities:e1:manual"
        taxonomy = {"fields": [{"id": field_id, "label": {"en": "Analysis"}, "parentIds": []}], "concepts": []}
        overlays = {"entities": {
            paper_id: {"entityType": "paper", "fieldIds": [field_id], "pinned": True,
                       "memberships": [{"fieldId": field_id, "method": "user", "score": 1}]},
            concept_id: {"entityType": "concept", "aliases": ["manual alias"], "pinned": True},
        }}
        with tempfile.TemporaryDirectory() as directory:
            snapshot = GraphEngine(Path(directory), provider="fake").build(
                papers, source=source, taxonomy=taxonomy, overlays=overlays, settings={"fakeVectors": {}}
            )
        paper = snapshot["papers"][0]
        self.assertEqual(paper["fieldIds"], [field_id])
        self.assertTrue(paper["pinned"])
        concept = next(item for item in snapshot["concepts"] if item["id"] == concept_id)
        self.assertIn("manual alias", concept["aliases"])
        self.assertTrue(concept["pinned"])

    def test_taxonomy_cycle_and_unverified_or_unknown_references_fail_closed(self):
        cycle = {"fields": [{"id": "a", "parentIds": ["b"]}, {"id": "b", "parentIds": ["a"]}]}
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                GraphEngine(Path(directory), provider="fake").build(
                    [{"key": "A", "title": "A"}], source={"serverId": "cycle", "epoch": "e1"}, taxonomy=cycle
                )
            papers = [{"key": "A", "title": "A", "references": [
                {"key": "missing", "verified": True}, {"key": "B", "verified": False},
                {"key": "B", "verified": True, "provider": "evil", "source": "user", "evidenceTier": "metadata"}, "arbitrary-string"
            ]}, {"key": "B", "title": "B"}]
            snapshot = GraphEngine(Path(directory), provider="fake").build(
                papers, source={"id": "refs", "serverId": "refs", "epoch": "e1"}, settings={"fakeVectors": {}}
            )
        self.assertFalse(any(edge["type"] == "citation" for edge in snapshot["edges"]))

    def test_openalex_cache_does_not_resurrect_deleted_endpoints(self):
        previous = {"edges": [{
            "id": "edge:oa:e1:zotero:oa:0:A:zotero:oa:0:B:citation",
            "source": "zotero:oa:0:A", "target": "zotero:oa:0:B", "type": "citation", "directed": True,
            "score": None, "provenance": {"provider": "openalex", "method": "referenced_works"},
            "evidenceTier": "metadata", "verified": True,
        }], "papers": []}
        with tempfile.TemporaryDirectory() as directory:
            snapshot = GraphEngine(Path(directory), provider="fake").build(
                [{"key": "A", "title": "A"}], source={"id": "oa", "serverId": "oa", "epoch": "e1"},
                settings={"openalexEnabled": True}, previous=previous
            )
        self.assertFalse(any(edge["type"] == "citation" for edge in snapshot["edges"]))
        self.assertEqual(snapshot["modelStatus"]["citationStatus"], "unknown")

    def test_openalex_opt_in_matches_only_known_doi_and_records_provenance(self):
        class Response:
            status = 200
            def __init__(self, body): self.body = body
            def read(self, limit=-1): return json.dumps(self.body).encode()[:limit]
            def __enter__(self): return self
            def __exit__(self, *args): return False

        def openalex(request, timeout):
            url = request.full_url
            if url.endswith("10.a"):
                return Response({"referenced_works": ["https://openalex.org/W2"]})
            if url.endswith("10.b"):
                return Response({"referenced_works": []})
            if url.endswith("W2"):
                return Response({"doi": "https://doi.org/10.b", "referenced_works": []})
            raise AssertionError(url)

        papers = [
            {"key": "A", "doi": "10.a", "title": "A"},
            {"key": "B", "doi": "10.b", "title": "B"},
        ]
        with tempfile.TemporaryDirectory() as directory, mock.patch("atlas.graph.urllib.request.urlopen", side_effect=openalex):
            snapshot = GraphEngine(Path(directory), provider="fake").build(
                papers, source={"id": "oa-live", "serverId": "oa-live", "epoch": "e1"}, settings={"openAlexEnabled": True}
            )
        citations = [edge for edge in snapshot["edges"] if edge["type"] == "citation"]
        self.assertEqual(len(citations), 1)
        self.assertEqual(citations[0]["provenance"]["provider"], "openalex")
        self.assertEqual(citations[0]["retrievalProvenance"]["method"], "referenced_works")
        self.assertEqual(snapshot["modelStatus"]["openAlexStatus"], "ready")

