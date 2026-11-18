import math
import os
import tempfile
import unittest
from pathlib import Path

from atlas.embeddings import EmbeddingProvider
from atlas.graph import GraphEngine


@unittest.skipUnless(os.environ.get("ATLAS_REAL_E5") == "1", "set ATLAS_REAL_E5=1 for the separately provisioned local model smoke")
class RealE5SmokeTests(unittest.TestCase):
    def test_local_model_is_real_384d_and_symmetric(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = EmbeddingProvider(Path(directory), provider="e5", allow_download=False)
            related = provider.embed({"id": "related", "title": "graph neural networks", "abstract": "message passing molecular graph representations"})
            unrelated = provider.embed({"id": "unrelated", "title": "randomized clinical trial", "abstract": "patient outcomes and intervention"})
        self.assertFalse(related.failed, related.error)
        self.assertFalse(unrelated.failed, unrelated.error)
        self.assertEqual(related.provider, "e5")
        self.assertEqual(related.dimensions, 384)
        self.assertTrue(related.revision.endswith("614241f622f53c4eeff9890bdc4f31cfecc418b3"))
        self.assertTrue(related.model_card_hash.startswith("sha256:"))
        self.assertTrue(related.artifact_hash.startswith("sha256:"))
        self.assertNotEqual(related.model_card_hash, "sha256:fa149cce395872b35d7a8862526e4f8bd93cc6f7189fffd8d942ffd5afd5af83")
        self.assertTrue(related.prepared_input.startswith("query: "))
        self.assertTrue(unrelated.prepared_input.startswith("query: "))
        self.assertAlmostEqual(math.sqrt(sum(value * value for value in related.vector)), 1.0, places=5)

    def test_graph_reports_real_model_and_semantic_edges(self):
        papers = [
            {"key": "A", "title": "graph neural networks", "abstract": "message passing molecular graph representations"},
            {"key": "B", "title": "molecular graph representations", "abstract": "message passing networks learn graph structure"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            snapshot = GraphEngine(Path(directory), provider="e5", allow_download=False).build(
                papers, source={"id": "real-e5-smoke", "serverId": "real-e5-smoke", "epoch": "e1"},
                settings={"semanticThreshold": 0.80, "topK": 2},
            )
        self.assertFalse(snapshot["modelStatus"]["failed"])
        self.assertEqual(snapshot["modelStatus"]["provider"], "e5")
        self.assertEqual(snapshot["modelStatus"]["dimensions"], 384)
        self.assertTrue(any(edge["type"] == "semantic" for edge in snapshot["edges"]))

