import copy
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from jsonschema import validate

from atlas.graph import GraphEngine, canonical_entity_key, _canonicalize_entities, _id, resolve_taxonomy_id, _validated_redirects, MAX_TAXONOMY_REDIRECTS_PER_KIND, MAX_REDIRECT_CHAIN
from atlas.embeddings import EmbeddingProvider, document_text



class CanonicalResolutionTests(unittest.TestCase):
    def row(self, name, label, parents=(), aliases=(), source="user"):
        return {"id": name, "label": {"en": label}, "parentIds": list(parents),
                "aliases": list(aliases), "source": source}

    def resolve(self, rows):
        return _canonicalize_entities({row["id"]: row for row in rows}, "field", "s", "e")

    def test_unicode_only_labels_are_distinct_and_normalized(self):
        values = ("정보기하", "위상수학", "几何", "γεωμετρία")
        keys = [canonical_entity_key("field", {"ko": value}) for value in values]
        self.assertEqual(keys, list(values))
        self.assertEqual(len(set(keys)), len(values))
        self.assertEqual(canonical_entity_key("field", {"ko": " ＡＢＣ–Ｄ "}), "abc d")
        rows = [self.row(str(i), value) for i, value in enumerate(values)]
        result, redirects = self.resolve(rows)
        self.assertEqual(len(result), 4)
        self.assertFalse(redirects)

    def test_root_child_and_multicontext_homonyms_stay_separate(self):
        rows = [self.row("p", "Parent"), self.row("q", "Other"),
                self.row("root", "Example"), self.row("child", "Example", ["p"]),
                self.row("multi", "Example", ["p", "q"])]
        result, redirects = self.resolve(rows)
        self.assertEqual(len(result), 5)
        self.assertFalse(redirects)

    def test_alias_chain_and_parent_redirect_fixpoint_are_order_independent(self):
        rows = [self.row("p1", "Parent"), self.row("p2", "Parent"),
                self.row("a", "Alpha", ["p1"], ["Beta"]),
                self.row("b", "Beta", ["p2"], ["Gamma"]),
                self.row("c", "Gamma", ["p1"])]
        result, redirects = self.resolve(rows)
        reverse, _ = self.resolve(list(reversed(rows)))
        self.assertEqual(result, reverse)
        self.assertEqual(len(result), 2)
        self.assertEqual({redirects.get(i, i) for i in ("a", "b", "c")}, {"a"})
        again, _ = self.resolve(list(result.values()))
        self.assertEqual(result, again)

    def test_dash_variants_and_curated_model_legacy_migration(self):
        model = _id("field", "s", "e", "kl-divergence")
        rows = [self.row("parent", "Information"), self.row(model, "KL Divergence", ["parent"], source="model"),
                self.row("legacy", "Kullback-Leibler divergence"),
                self.row("en", "Kullback–Leibler divergence")]
        result, redirects = self.resolve(rows)
        self.assertEqual(len(result), 2)
        self.assertEqual(redirects["legacy"], model)
        self.assertEqual(redirects["en"], model)
        self.assertEqual(result[model]["parentIds"], ["parent"])
        self.assertEqual(canonical_entity_key("field", "Kullback-Leibler divergence"), "kl-divergence")

    def test_real_build_remaps_legacy_field_pins_and_paper_memberships(self):
        source = {"id": "s", "serverId": "s", "epoch": "e"}
        legacy = "field:s:e:legacy-kl"
        previous = {"source": source, "fields": [self.row(legacy, "Kullback-Leibler divergence")],
                    "concepts": [], "papers": [], "memberships": [], "edges": []}
        with tempfile.TemporaryDirectory() as directory:
            snapshot = GraphEngine(Path(directory), provider="fake").build(
                [{"key": "A", "title": "A", "fieldIds": [legacy]}], source=source,
                previous=previous, overlays={"pins": {legacy: True}}, settings={"fakeVectors": {}})
        canonical = _id("field", "s", "e", "kl-divergence")
        fields = [f for f in snapshot["fields"] if f["id"] == canonical]
        self.assertEqual(len(fields), 1)
        self.assertTrue(fields[0]["pinned"])
        self.assertIn(canonical, snapshot["papers"][0]["fieldIds"])
        self.assertEqual(snapshot["taxonomyRedirects"][legacy], canonical)

    def test_display_field_redirect_does_not_recreate_orphan(self):
        source = {"id": "s", "serverId": "s", "epoch": "e"}
        rows = [self.row("field:s:e:kl-a", "KL Divergence"), self.row("field:s:e:kl-b", "Kullback-Leibler divergence")]
        with tempfile.TemporaryDirectory() as directory:
            snapshot = GraphEngine(Path(directory), provider="fake").build(
                [{"key": "A", "title": "A", "fieldIds": ["field:s:e:kl-b"],
                  "displayFieldId": "field:s:e:kl-b"}], source=source,
                taxonomy={"fields": rows}, settings={"fakeVectors": {}})
        canonical = _id("field", "s", "e", "kl-divergence")
        self.assertEqual(snapshot["papers"][0]["displayFieldId"], canonical)
        self.assertNotIn("field:s:e:kl-b", {f["id"] for f in snapshot["fields"]})
        self.assertNotIn("field:s:e:kl-b", {f["fieldId"] for f in snapshot["overview"]["fields"]})

    def test_classification_hint_does_not_merge_broader_field_with_method(self):
        rows = [self.row("p", "Math"), self.row("a", "Optimization", ["p"], ["optimal transport"]),
                self.row("b", "Optimal Transport", ["p"])]
        result, redirects = self.resolve(rows)
        self.assertEqual(len(result), 3)
        self.assertNotEqual(redirects.get("a", "a"), redirects.get("b", "b"))
        self.assertEqual(canonical_entity_key("field", "Optimization", ["optimal transport"]), "optimization")
        source = {"id": "s", "serverId": "s", "epoch": "e"}
        with tempfile.TemporaryDirectory() as directory:
            snapshot = GraphEngine(Path(directory), provider="fake").build(
                [{"key": "A", "title": "optimal transport"}], source=source,
                settings={"fakeVectors": {}})
        labels = {f["id"]: f["label"]["en"] for f in snapshot["fields"]}
        assigned = {labels[f] for f in snapshot["papers"][0]["fieldIds"]}
        # v3: direct membership is the specific facet; Optimization is its
        # ancestor and appears only in inclusive counts (not as a direct field).
        self.assertIn("Optimal Transport", assigned)
        stats = {r["fieldId"]: r for r in snapshot["fieldStats"]["rows"]}
        optimization = next(f["id"] for f in snapshot["fields"] if f["label"]["en"] == "Optimization")
        self.assertEqual((stats[optimization]["direct"], stats[optimization]["inclusive"]), (0, 1))
        self.assertIn("Optimal Transport", assigned)

    def test_redirects_persist_across_reindex_and_old_references_resolve(self):
        source = {"id": "s", "serverId": "s", "epoch": "e"}
        old = "field:s:e:b"
        rows = [self.row("field:s:e:a", "KL Divergence"), self.row(old, "Kullback-Leibler divergence")]
        with tempfile.TemporaryDirectory() as directory:
            engine = GraphEngine(Path(directory), provider="fake")
            first = engine.build([{"key": "A", "title": "A", "fieldIds": [old]}], source=source,
                                 taxonomy={"fields": rows}, settings={"fakeVectors": {}})
            target = first["taxonomyRedirects"][old]
            second = engine.build([{"key": "A", "title": "A", "fieldIds": [old], "displayFieldId": old}],
                                  source=source, taxonomy={"fields": first["fields"]}, previous=first,
                                  overlays={"fieldAliases": {old: ["retained"]}, "pins": {old: True}},
                                  settings={"fakeVectors": {}})
            third = engine.build([{"key": "A", "title": "A", "fieldIds": [old]}], source=source,
                                 taxonomy={"fields": second["fields"]}, previous=second, settings={"fakeVectors": {}})
        self.assertEqual(second["taxonomyRedirects"][old], target)
        self.assertEqual(third["taxonomyRedirects"][old], target)
        self.assertEqual(second["papers"][0]["displayFieldId"], target)
        self.assertNotIn(old, {f["id"] for f in second["fields"]})
        field = next(f for f in second["fields"] if f["id"] == target)
        self.assertTrue(field["pinned"])
        self.assertIn("retained", field["aliases"])

    def test_redirect_chain_compression_rejects_cycles_foreign_and_deleted_targets(self):
        source = {"id": "s", "serverId": "s", "epoch": "e"}
        target = "field:s:e:target"
        rows = [self.row(target, "Target")]
        prior = {"source": source, "fields": rows, "concepts": [], "taxonomyRedirects": {
            "field:s:e:a": "field:s:e:b", "field:s:e:b": target,
            "field:s:e:c": "field:s:e:d", "field:s:e:d": "field:s:e:c",
            "field:s:e:foreign": "field:other:e:z", "field:s:e:deleted": "field:s:e:gone",
            "concept:s:e:wrongtype": target}}
        result = _validated_redirects(prior, "s", "e")
        self.assertEqual(result, {"field:s:e:a": target, "field:s:e:b": target})
        self.assertEqual(resolve_taxonomy_id("field:s:e:a", prior["taxonomyRedirects"]), target)
        self.assertIsNone(resolve_taxonomy_id("field:s:e:c", prior["taxonomyRedirects"]))
        self.assertEqual(_validated_redirects(prior, "s", "other"), {})
        prior["fields"] = []
        self.assertEqual(_validated_redirects(prior, "s", "e"), {})

    def test_redirect_limits_fail_explicitly_without_truncation(self):
        source = {"id": "s", "serverId": "s", "epoch": "e"}
        target = "field:s:e:target"
        prior = {"source": source, "fields": [self.row(target, "Target")], "concepts": [],
                 "taxonomyRedirects": {f"field:s:e:{i}": target for i in range(MAX_TAXONOMY_REDIRECTS_PER_KIND)}}
        self.assertEqual(len(_validated_redirects(prior, "s", "e")), MAX_TAXONOMY_REDIRECTS_PER_KIND)
        prior["taxonomyRedirects"]["field:s:e:overflow"] = target
        with self.assertRaisesRegex(ValueError, "redirects exceed"):
            _validated_redirects(prior, "s", "e")
        chain = {str(i): str(i + 1) for i in range(MAX_REDIRECT_CHAIN)}
        self.assertEqual(resolve_taxonomy_id("0", chain), str(MAX_REDIRECT_CHAIN))
        chain[str(MAX_REDIRECT_CHAIN)] = "overflow"
        with self.assertRaisesRegex(ValueError, "chain exceeds"):
            resolve_taxonomy_id("0", chain)
        self.assertIsNone(resolve_taxonomy_id("a", {"a": "b", "b": "a"}))

    def test_cycle_cleanup_and_no_dangling_parents(self):
        rows = [self.row("a", "A", ["b", "missing"]), self.row("b", "B", ["a"])]
        result, _ = self.resolve(rows)
        self.assertFalse(result["a"]["parentIds"] == ["b"] and result["b"]["parentIds"] == ["a"])
        self.assertTrue(all(p in result for row in result.values() for p in row["parentIds"]))

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

    def test_public_canonical_identity_helper_handles_bilingual_labels(self):
        self.assertEqual(canonical_entity_key("concept", {"en": "KL Divergence", "ko": "KL 발산"}), "kl-divergence")
        self.assertEqual(canonical_entity_key("field", "Kullback–Leibler divergence"), "kl-divergence")

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

    def test_canonical_registry_merges_kl_ot_life_synonyms_and_remaps_references(self):
        source = {"id": "dup", "serverId": "dup", "epoch": "e1"}
        taxonomy = {"fields": [
            {"id": "field:dup:e1:kl-a", "label": {"en": "KL Divergence"}, "parentIds": []},
            {"id": "field:dup:e1:kl-b", "label": {"en": "Kullback-Leibler divergence"}, "parentIds": []},
            {"id": "field:dup:e1:ot-a", "label": {"en": "Optimal Transport"}, "parentIds": []},
            {"id": "field:dup:e1:ot-b", "label": {"en": "OT"}, "parentIds": []},
            {"id": "field:dup:e1:bio-a", "label": {"en": "Life Science"}, "parentIds": []},
            {"id": "field:dup:e1:bio-b", "label": {"en": "생명과학"}, "parentIds": []},
        ], "concepts": [
            {"id": "concept:dup:e1:kl-a", "label": {"en": "KL Divergence"}, "aliases": []},
            {"id": "concept:dup:e1:kl-b", "label": {"en": "Kullback–Leibler divergence"}, "aliases": []},
        ]}
        papers = [{"key": "A", "title": "A", "fieldIds": ["field:dup:e1:kl-a", "field:dup:e1:kl-b", "field:dup:e1:ot-a", "field:dup:e1:bio-a"], "conceptIds": ["concept:dup:e1:kl-a", "concept:dup:e1:kl-b"]}]
        with tempfile.TemporaryDirectory() as directory:
            snapshot = GraphEngine(Path(directory), provider="fake").build(papers, source=source, taxonomy=taxonomy, settings={"fakeVectors": {}})
        field_labels = [field["label"]["en"] for field in snapshot["fields"]]
        self.assertEqual(sum(label == "KL Divergence" for label in field_labels), 1)
        self.assertEqual(sum(label == "Optimal Transport" for label in field_labels), 1)
        self.assertEqual(sum(label == "Life Science" for label in field_labels), 1)
        self.assertEqual(len([concept for concept in snapshot["concepts"] if concept["label"]["en"] == "KL Divergence"]), 1)
        self.assertEqual(len(snapshot["papers"][0]["conceptIds"]), 1)
        self.assertTrue(snapshot["taxonomyRedirects"])

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
        self.assertTrue(any(m["method"] == "model" and m["modelVersion"] == "taxonomy-v4" and m["provenance"]["label"] == "rule-based" for m in snapshot["memberships"]))

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

