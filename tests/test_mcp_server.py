"""
tests/test_mcp_server.py

Tests the mcp_server.py as a real stdio JSON-RPC 2.0 MCP server
by spawning it as a subprocess (MCPClient) and as direct imports.
"""
import os
import unittest

os.environ.setdefault("JWT_SECRET", "test-secret-key-for-unit-testing-32chars!")

from agents.base_agent import MCPClient
from mcp_server import TOOLS, TOOL_SCHEMAS
from db import get_db_connection


class TestMCPServerDirect(unittest.TestCase):
    """Tool functions imported directly — no subprocess."""

    def setUp(self):
        with get_db_connection() as conn:
            conn.execute("DELETE FROM carts")
            conn.execute("DELETE FROM orders")
            conn.commit()
        self.session_id = "mcp_direct_test_session"

    def test_tool_schemas_have_required_fields(self):
        names = {s["name"] for s in TOOL_SCHEMAS}
        expected = {
            "search_books", "get_book_detail", "list_genres",
            "add_to_cart", "view_cart", "remove_from_cart",
            "create_order", "get_order_history", "cancel_order",
        }
        self.assertEqual(names, expected)
        for schema in TOOL_SCHEMAS:
            self.assertIn("inputSchema", schema)
            self.assertIn("description", schema)

    def test_all_tools_registered(self):
        for schema in TOOL_SCHEMAS:
            self.assertIn(schema["name"], TOOLS)
            self.assertTrue(callable(TOOLS[schema["name"]]))

    def test_search_books_direct(self):
        result = TOOLS["search_books"]("mystery thriller")
        self.assertIn("results", result)
        self.assertIsInstance(result["results"], list)

    def test_list_genres_direct(self):
        result = TOOLS["list_genres"]()
        self.assertIn("genres", result)
        self.assertGreater(len(result["genres"]), 0)


class TestMCPServerSubprocess(unittest.TestCase):
    """Full JSON-RPC 2.0 round-trip via MCPClient subprocess."""

    def setUp(self):
        with get_db_connection() as conn:
            conn.execute("DELETE FROM carts")
            conn.execute("DELETE FROM orders")
            conn.commit()
        self.session_id = "mcp_subprocess_test_session"

    def test_initialize_handshake(self):
        with MCPClient() as client:
            # If __enter__ succeeds the handshake completed without error
            self.assertIsNotNone(client)

    def test_tools_list(self):
        with MCPClient() as client:
            tools = client.list_tools()
        self.assertIsInstance(tools, list)
        self.assertEqual(len(tools), 9)
        names = {t["name"] for t in tools}
        self.assertIn("search_books", names)
        self.assertIn("cancel_order", names)

    def test_call_list_genres(self):
        with MCPClient() as client:
            result = client.call_tool("list_genres", {})
        self.assertIn("genres", result)
        self.assertGreater(len(result["genres"]), 0)

    def test_call_search_books(self):
        with MCPClient() as client:
            result = client.call_tool("search_books", {"query": "science fiction", "top_k": 3})
        self.assertIn("results", result)
        self.assertLessEqual(len(result["results"]), 3)

    def test_call_unknown_tool_returns_error(self):
        with MCPClient() as client:
            result = client.call_tool("nonexistent_tool", {})
        self.assertIn("error", result)

    def test_call_add_and_view_cart(self):
        with MCPClient() as client:
            add_res = client.call_tool("add_to_cart", {
                "session_id": self.session_id, "book_id": "B001", "quantity": 1,
            })
            self.assertIn("cart_items", add_res)
            view_res = client.call_tool("view_cart", {"session_id": self.session_id})
        self.assertEqual(view_res["item_count"], 1)

    def test_call_checkout(self):
        with MCPClient() as client:
            client.call_tool("add_to_cart", {
                "session_id": self.session_id, "book_id": "B002", "quantity": 1,
            })
            order_res = client.call_tool("create_order", {
                "session_id": self.session_id,
                "delivery_address": "42 Baker Street, London, SW1A 1AA",
            })
        self.assertEqual(order_res["status"], "Confirmed")
        self.assertIn("order_id", order_res)


if __name__ == "__main__":
    unittest.main()
