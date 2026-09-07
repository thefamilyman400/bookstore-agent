import os
import unittest
from datetime import datetime, timedelta, timezone

os.environ.setdefault("JWT_SECRET", "test-secret-key-for-unit-testing-32chars!")

from mcp_server import (
    search_books,
    get_book_detail,
    list_genres,
    add_to_cart,
    view_cart,
    remove_from_cart,
    create_order,
    get_order_history,
    cancel_order,
)
from db import get_db_connection


class TestMCPTools(unittest.TestCase):

    def setUp(self):
        with get_db_connection() as conn:
            conn.execute("DELETE FROM carts")
            conn.execute("DELETE FROM orders")
            conn.commit()
        self.session_id = "test_user_session_001"

    def test_list_genres(self):
        res = list_genres()
        self.assertIn("genres", res)
        self.assertGreater(len(res["genres"]), 0)

    def test_get_book_detail(self):
        res = get_book_detail("B001")
        self.assertEqual(res["id"], "B001")
        self.assertIn("title", res)
        self.assertIn("price", res)
        self.assertIn("estimated_delivery", res)

    def test_get_book_detail_not_found(self):
        res = get_book_detail("NON_EXISTENT_ID")
        self.assertIn("error", res)

    def test_cart_operations(self):
        # Add to cart (distinct book items in cart)
        add_res = add_to_cart(self.session_id, "B001", quantity=2)
        self.assertEqual(add_res["cart_items"], 1)
        self.assertEqual(add_res["cart_total"], 25.98)

        # Add a second distinct book
        add_res2 = add_to_cart(self.session_id, "B002", quantity=1)
        self.assertEqual(add_res2["cart_items"], 2)

        # View cart
        view_res = view_cart(self.session_id)
        self.assertEqual(view_res["item_count"], 2)
        self.assertEqual(len(view_res["items"]), 2)
        self.assertEqual(view_res["items"][0]["book_id"], "B001")
        self.assertEqual(view_res["items"][0]["quantity"], 2)

        # Remove B001
        remove_res = remove_from_cart(self.session_id, "B001")
        self.assertEqual(remove_res["cart_items"], 1)

        # Remove B002
        remove_from_cart(self.session_id, "B002")
        view_empty = view_cart(self.session_id)
        self.assertEqual(len(view_empty["items"]), 0)

    def test_checkout_and_order_history(self):
        add_to_cart(self.session_id, "B001", quantity=1)
        order_res = create_order(self.session_id, delivery_address="123 Test Lane, City, 10001")

        self.assertEqual(order_res["status"], "Confirmed")
        self.assertIn("order_id", order_res)
        order_id = order_res["order_id"]

        # Cart should now be empty
        empty_cart = view_cart(self.session_id)
        self.assertEqual(len(empty_cart["items"]), 0)

        # Order should appear in history
        history = get_order_history(self.session_id)
        self.assertEqual(history["count"], 1)
        self.assertEqual(history["orders"][0]["order_id"], order_id)

    def test_cancel_order_within_48_hours(self):
        add_to_cart(self.session_id, "B001", quantity=1)
        order_res = create_order(self.session_id, delivery_address="123 Test Lane")
        order_id = order_res["order_id"]

        cancel_res = cancel_order(self.session_id, order_id)
        self.assertIn("message", cancel_res)
        self.assertIn("cancelled", cancel_res["message"].lower())

    def test_cancel_order_after_48_hours_blocked(self):
        add_to_cart(self.session_id, "B001", quantity=1)
        order_res = create_order(self.session_id, delivery_address="123 Test Lane")
        order_id = order_res["order_id"]

        # Manually age the order past 48 hours in the DB
        past_time = datetime.now(timezone.utc) - timedelta(hours=49)
        with get_db_connection() as conn:
            conn.execute("UPDATE orders SET placed_at = ? WHERE order_id = ?", (past_time.isoformat(), order_id))
            conn.commit()

        cancel_res = cancel_order(self.session_id, order_id)
        self.assertIn("error", cancel_res)
        self.assertIn("48 hours", cancel_res["error"])


if __name__ == "__main__":
    unittest.main()
