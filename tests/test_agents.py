"""
tests/test_agents.py

Tests the multi-agent layer:
  - CatalogueAgent mock fallback
  - CartAgent mock fallback
  - OrderAgent mock fallback
  - Orchestrator intent routing (mock path, no API key required)
"""
import os
import unittest

os.environ.setdefault("JWT_SECRET", "test-secret-key-for-unit-testing-32chars!")
# Ensure no GROQ key so all agents run mock fallback
os.environ.pop("GROQ_API_KEY", None)

from agents.catalogue_agent import CatalogueAgent
from agents.cart_agent import CartAgent
from agents.order_agent import OrderAgent
from agents.orchestrator import run_agent, _classify_intent_mock
from db import get_db_connection


SESSION = "agent_test_session_001"


def _msg(text: str):
    return [{"role": "user", "content": text}]


class TestCatalogueAgent(unittest.TestCase):

    def setUp(self):
        self.agent = CatalogueAgent()

    def test_list_genres(self):
        reply = self.agent.run(_msg("list all genres"), SESSION)
        self.assertIn("genres", reply.lower())

    def test_book_detail(self):
        reply = self.agent.run(_msg("details B001"), SESSION)
        # Reply contains title/author/price but not necessarily the raw ID
        self.assertIn("$", reply)
        self.assertIn("Genre:", reply)

    def test_book_detail_not_found(self):
        reply = self.agent.run(_msg("details B999"), SESSION)
        self.assertIn("not found", reply.lower())

    def test_search_returns_results(self):
        reply = self.agent.run(_msg("I want a gripping psychological thriller"), SESSION)
        self.assertIn("$", reply)  # price present in results


class TestCartAgent(unittest.TestCase):

    def setUp(self):
        with get_db_connection() as conn:
            conn.execute("DELETE FROM carts WHERE session_id = ?", (SESSION,))
            conn.commit()
        self.agent = CartAgent()

    def test_view_empty_cart(self):
        reply = self.agent.run(_msg("show my cart"), SESSION)
        self.assertIn("empty", reply.lower())

    def test_add_to_cart(self):
        reply = self.agent.run(_msg("add B001"), SESSION)
        self.assertIn("Added", reply)

    def test_add_then_view(self):
        self.agent.run(_msg("add B002"), SESSION)
        reply = self.agent.run(_msg("view my cart"), SESSION)
        self.assertIn("Total", reply)

    def test_remove_from_cart(self):
        self.agent.run(_msg("add B003"), SESSION)
        reply = self.agent.run(_msg("remove B003"), SESSION)
        self.assertIn("removed", reply.lower())


class TestOrderAgent(unittest.TestCase):

    def setUp(self):
        with get_db_connection() as conn:
            conn.execute("DELETE FROM carts WHERE session_id = ?", (SESSION,))
            conn.execute("DELETE FROM orders WHERE session_id = ?", (SESSION,))
            conn.commit()
        self.agent = OrderAgent()

    def test_order_history_empty(self):
        reply = self.agent.run(_msg("show my orders"), SESSION)
        # Either "no orders" or "don't have any orders"
        self.assertTrue(
            "no orders" in reply.lower() or "don't have any orders" in reply.lower()
        )

    def test_checkout(self):
        from mcp_server import TOOLS
        TOOLS["add_to_cart"](SESSION, "B001")
        reply = self.agent.run(_msg("checkout to 10 Test Road, London, SW1A 1AA"), SESSION)
        self.assertIn("ORD-", reply)

    def test_cancel_nonexistent_order(self):
        reply = self.agent.run(_msg("cancel ORD-FFFFFFFF"), SESSION)
        self.assertIn("not found", reply.lower())


class TestOrchestratorRouting(unittest.TestCase):
    """Tests intent classification and end-to-end routing (mock path)."""

    def setUp(self):
        with get_db_connection() as conn:
            conn.execute("DELETE FROM carts WHERE session_id = ?", (SESSION,))
            conn.execute("DELETE FROM orders WHERE session_id = ?", (SESSION,))
            conn.commit()

    def test_intent_catalogue(self):
        self.assertEqual(_classify_intent_mock("find me a sci-fi book"), "catalogue")

    def test_intent_cart(self):
        self.assertEqual(_classify_intent_mock("add B005 to cart"), "cart")

    def test_intent_cart_view(self):
        self.assertEqual(_classify_intent_mock("show my cart"), "cart")

    def test_intent_order_checkout(self):
        self.assertEqual(_classify_intent_mock("I want to checkout"), "order")

    def test_intent_order_history(self):
        self.assertEqual(_classify_intent_mock("show my order history"), "order")

    def test_intent_chitchat(self):
        self.assertEqual(_classify_intent_mock("hello"), "chitchat")

    def test_orchestrator_chitchat_reply(self):
        reply = run_agent(_msg("hello"), SESSION)
        self.assertIn("JustBooks", reply)

    def test_orchestrator_catalogue_reply(self):
        reply = run_agent(_msg("recommend a fantasy adventure book"), SESSION)
        self.assertIn("$", reply)

    def test_orchestrator_cart_reply(self):
        reply = run_agent(_msg("view my cart"), SESSION)
        self.assertIn("cart", reply.lower())

    def test_orchestrator_order_reply(self):
        reply = run_agent(_msg("show my orders"), SESSION)
        self.assertIn("order", reply.lower())


if __name__ == "__main__":
    unittest.main()
