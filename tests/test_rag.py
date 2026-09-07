import unittest
from rag_store import semantic_search, BOOKS, BACKEND


class TestRAGStore(unittest.TestCase):

    def test_catalogue_loaded(self):
        self.assertGreater(len(BOOKS), 0)
        sample = BOOKS[0]
        self.assertIn("id", sample)
        self.assertIn("title", sample)
        self.assertIn("genre", sample)
        self.assertIn("price", sample)

    def test_semantic_search_returns_results(self):
        results = semantic_search("science fiction space adventure", top_k=3)
        self.assertIsInstance(results, list)
        self.assertEqual(len(results), 3)
        self.assertIn("title", results[0])
        self.assertIn("score", results[0])

    def test_semantic_search_ordering(self):
        results = semantic_search("dune frank herbert", top_k=5)
        self.assertGreater(len(results), 0)
        # Results should have decreasing or equal relevance scores
        scores = [b["score"] for b in results]
        self.assertEqual(scores, sorted(scores, reverse=True))


if __name__ == "__main__":
    unittest.main()
