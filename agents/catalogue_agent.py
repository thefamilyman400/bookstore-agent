"""
agents/catalogue_agent.py

Specialist agent for book discovery:
  - search_books
  - get_book_detail
  - list_genres
"""
from __future__ import annotations
import re
from typing import Any, Dict, List

from agents.base_agent import BaseAgent
from mcp_server import TOOLS


class CatalogueAgent(BaseAgent):

    TOOLS = ["search_books", "get_book_detail", "list_genres"]

    SYSTEM_PROMPT = """You are the Book Discovery specialist at JustBooks — an enthusiastic, \
well-read librarian who helps customers find incredible books.

You have access to these tools:
{tools}

Rules:
- When invoking a tool respond with ONLY this JSON (no commentary):
  {{"tool": "<tool_name>", "args": {{...}}}}
- After receiving a tool result, synthesise a warm, richly formatted reply.
- Include book ID (e.g. B001), title, author, genre, rating ⭐, price $, and a short hook.
- For author questions add interesting background on writing style and themes.
"""

    # ── Mock fallback ─────────────────────────────────────────────────────────

    def _mock(self, messages: List[Dict[str, Any]], session_id: str) -> str:
        text = messages[-1]["content"].lower()

        if re.search(r"\bgenres?\b|\bcategor", text):
            result = TOOLS["list_genres"]()
            return (
                "Here are all genres in our catalogue:\n\n"
                + "\n".join(f"• {g}" for g in result["genres"])
                + "\n\nJust ask me to search for books in any of these genres!"
            )

        detail_match = re.search(r"detail[s]?\s+([Bb]\d{3})|([Bb]\d{3})\s+detail", text)
        if detail_match:
            book_id = (detail_match.group(1) or detail_match.group(2)).upper()
            b = TOOLS["get_book_detail"](book_id)
            if b.get("error"):
                return b["error"]
            bio = f"✍️ **About the Author:** {b['author_bio']}\n\n" if b.get("author_bio") else ""
            return (
                f"📖 **{b['title']}** by {b['author']}\n"
                f"Genre: {b['genre']} | Rating: ⭐ {b['rating']} | Price: ${b['price']}\n\n"
                f"**About the Book:** {b['synopsis']}\n\n"
                f"{bio}"
                f"Tags: {', '.join(b['tags'])}\n"
                f"Estimated Delivery: {b['estimated_delivery']}"
            )

        # Default: semantic search
        results = TOOLS["search_books"](text, top_k=5)
        if not results.get("results"):
            return (
                "I couldn't find books matching that query. Try describing the genre, "
                "mood, or theme — e.g. 'suspenseful mystery' or 'space adventure'."
            )
        lines = [f"📚 Here are {len(results['results'])} books I found:\n"]
        for b in results["results"]:
            lines.append(
                f"**[{b['id']}] {b['title']}** by {b['author']}\n"
                f"  Genre: {b['genre']} | ⭐ {b['rating']} | ${b['price']:.2f}\n"
            )
        lines.append("💡 Type `add B001` to add a book to cart, or `details B001` to learn more.")
        return "\n".join(lines)
