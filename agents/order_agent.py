"""
agents/order_agent.py

Specialist agent for order lifecycle:
  - create_order
  - get_order_history
  - cancel_order
"""
from __future__ import annotations
import re
from typing import Any, Dict, List

from agents.base_agent import BaseAgent
from mcp_server import TOOLS


class OrderAgent(BaseAgent):

    TOOLS = ["create_order", "get_order_history", "cancel_order"]

    SYSTEM_PROMPT = """You are the Orders specialist at JustBooks — \
you handle checkout, order history, and cancellations.

You have access to these tools:
{tools}

Rules:
- When invoking a tool respond with ONLY this JSON (no commentary):
  {{"tool": "<tool_name>", "args": {{...}}}}
- After a tool result, give a clear, friendly confirmation.
- For checkout always confirm the order ID, total, and estimated delivery.
- Cancellations are only allowed within 48 hours of placement.
- session_id is always provided automatically — do not ask for it.
"""

    # ── Mock fallback ─────────────────────────────────────────────────────────

    def _mock(self, messages: List[Dict[str, Any]], session_id: str) -> str:
        text = messages[-1]["content"].lower()

        if re.search(r"\border history\b|\bmy orders\b|\bpast orders\b", text):
            result = TOOLS["get_order_history"](session_id)
            if not result.get("orders"):
                return "You don't have any orders yet. Add books to your cart and checkout!"
            lines = ["📦 **Your Order History:**\n"]
            for o in result["orders"]:
                lines.append(
                    f"• Order **{o['order_id']}** — ${o['total']:.2f} "
                    f"— Status: {o['status']} — Est. Delivery: {o['estimated_delivery']}"
                )
            return "\n".join(lines)

        cancel_match = re.search(r"cancel\s+(ORD-[A-Z0-9]+)", text, re.IGNORECASE)
        if cancel_match:
            order_id = cancel_match.group(1).upper()
            result = TOOLS["cancel_order"](session_id, order_id)
            return result.get("message") or result.get("error", "Unknown error.")

        if re.search(r"\bcheckout\b|\bplace order\b|\bbuy now\b|\border now\b", text):
            addr_match = re.search(r"(?:to|address[:\s]+)([\w\s,]+\d{5,})", text)
            address = addr_match.group(1).strip() if addr_match else "123 Main Street, New York, NY 10001"
            result = TOOLS["create_order"](session_id, address)
            if result.get("error"):
                return result["error"]
            return (
                f"🎉 **{result['message']}**\n\n"
                f"Order ID: **{result['order_id']}**\n"
                f"Total: **${result['total']:.2f}**\n"
                f"Estimated Delivery: {result['estimated_delivery']}\n"
                f"Status: {result['status']}"
            )

        return "I can help you place an order, view your order history, or cancel an order. What would you like?"
