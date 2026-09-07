"""
agents/cart_agent.py

Specialist agent for shopping cart management:
  - add_to_cart
  - view_cart
  - remove_from_cart
"""
from __future__ import annotations
import re
from typing import Any, Dict, List

from agents.base_agent import BaseAgent
from mcp_server import TOOLS


class CartAgent(BaseAgent):

    TOOLS = ["add_to_cart", "view_cart", "remove_from_cart"]

    SYSTEM_PROMPT = """You are the Shopping Cart specialist at JustBooks — \
you help customers manage what's in their basket.

You have access to these tools:
{tools}

Rules:
- When invoking a tool respond with ONLY this JSON (no commentary):
  {{"tool": "<tool_name>", "args": {{...}}}}
- After a tool result, give a concise, friendly confirmation.
- Always show the updated cart total after add/remove operations.
- session_id is always provided automatically — do not ask for it.
"""

    # ── Mock fallback ─────────────────────────────────────────────────────────

    def _mock(self, messages: List[Dict[str, Any]], session_id: str) -> str:
        text = messages[-1]["content"].lower()

        if re.search(r"\bmy cart\b|\bview cart\b|\bsee cart\b|\bshow cart\b", text):
            result = TOOLS["view_cart"](session_id)
            if not result.get("items"):
                return "Your cart is empty. Search for books to get started!"
            lines = [f"🛒 **Your Cart** ({result['item_count']} item(s)):\n"]
            for item in result["items"]:
                lines.append(
                    f"• {item['title']} by {item['author']} "
                    f"× {item['quantity']} — ${item['price'] * item['quantity']:.2f}"
                )
            lines.append(f"\n**Total: ${result['total']:.2f}**")
            return "\n".join(lines)

        remove_match = re.search(r"remove\s+([Bb]\d{3})", text)
        if remove_match:
            book_id = remove_match.group(1).upper()
            result = TOOLS["remove_from_cart"](session_id, book_id)
            return result.get("message") or result.get("error", "Unknown error.")

        add_match = re.search(r"add\s+([Bb]\d{3})\b", text)
        if add_match:
            book_id = add_match.group(1).upper()
            result = TOOLS["add_to_cart"](session_id, book_id)
            if result.get("error"):
                return result["error"]
            return (
                f"✅ {result['message']} "
                f"You now have {result['cart_items']} item(s) — "
                f"Cart total: **${result['cart_total']:.2f}**"
            )

        return "I can help you add, remove, or view items in your cart. What would you like to do?"
