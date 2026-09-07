"""
agents/orchestrator.py

Orchestrator Agent — classifies user intent and delegates to the
appropriate specialist sub-agent:

  catalogue  →  CatalogueAgent  (search, details, genres)
  cart       →  CartAgent       (add, remove, view cart)
  order      →  OrderAgent      (checkout, history, cancel)
  chitchat   →  handled inline

Public entry point:
    from agents.orchestrator import run_agent
    reply = run_agent(messages, session_id)
"""
from __future__ import annotations
import os
import re
from typing import Any, Dict, List

from agents.catalogue_agent import CatalogueAgent
from agents.cart_agent import CartAgent
from agents.order_agent import OrderAgent


# ── Intent keywords for mock/fast routing ────────────────────────────────────
_CART_RE = re.compile(
    r"\badd\s+[Bb]\d{3}\b"
    r"|\bremove\s+[Bb]\d{3}\b"
    r"|\bmy cart\b|\bview cart\b|\bshow cart\b|\bsee cart\b"
)
_ORDER_RE = re.compile(
    r"\bcheckout\b|\bplace order\b|\bbuy now\b|\border now\b"
    r"|\border history\b|\bmy orders\b|\bpast orders\b"
    r"|\bcancel\s+ORD-"
    , re.IGNORECASE,
)
_CHITCHAT_RE = re.compile(
    r"^\s*(hi|hello|hey|thanks|thank you|bye|goodbye|help|what can you do)\b"
    , re.IGNORECASE,
)

_CATALOGUE_AGENT = CatalogueAgent()
_CART_AGENT      = CartAgent()
_ORDER_AGENT     = OrderAgent()


# ── Groq-based intent classifier ─────────────────────────────────────────────
_INTENT_SYSTEM = """You are an intent router for a bookstore assistant.
Classify the user message into exactly one of these intents:
  catalogue  — searching for books, book details, genres, authors
  cart       — adding/removing/viewing items in the shopping cart
  order      — checkout, order history, cancelling an order
  chitchat   — greetings, help, anything else

Reply with a single word: catalogue, cart, order, or chitchat."""


def _classify_intent_groq(user_text: str) -> str:
    import groq as groq_sdk
    client = groq_sdk.Groq(api_key=os.environ["GROQ_API_KEY"])
    response = client.chat.completions.create(
        model=os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
        messages=[
            {"role": "system", "content": _INTENT_SYSTEM},
            {"role": "user",   "content": user_text},
        ],
        temperature=0.0,
        max_tokens=50,
    )
    raw = (response.choices[0].message.content or "").strip().lower()
    # Extract the first word in case the model adds punctuation or explanation
    for word in raw.split():
        clean = word.strip(".,!:;\"'")
        if clean in ("catalogue", "cart", "order", "chitchat"):
            return clean
    # Fallback to rule-based if model response is unusable
    return _classify_intent_mock(user_text)


def _classify_intent_mock(user_text: str) -> str:
    """Rule-based fallback classifier (no API key required)."""
    if _CART_RE.search(user_text):
        return "cart"
    if _ORDER_RE.search(user_text):
        return "order"
    if _CHITCHAT_RE.match(user_text):
        return "chitchat"
    return "catalogue"


def _classify_intent(user_text: str) -> str:
    if os.environ.get("GROQ_API_KEY"):
        try:
            return _classify_intent_groq(user_text)
        except Exception:
            pass
    return _classify_intent_mock(user_text)


# ── Chitchat handler ──────────────────────────────────────────────────────────
def _chitchat(user_text: str) -> str:
    text = user_text.lower().strip()
    if re.match(r"(hi|hello|hey)\b", text):
        return (
            "👋 Welcome to **JustBooks**! I'm your AI bookseller and concierge.\n\n"
            "Here's what I can do for you:\n"
            "• 🔍 **Find books** — describe a genre, mood, or theme\n"
            "• 📖 **Book details** — type `details B001`\n"
            "• 🛒 **Cart** — `add B001`, `remove B001`, or `view my cart`\n"
            "• 📦 **Orders** — `checkout`, `my orders`, or `cancel ORD-XXXXXXXX`\n\n"
            "What are you in the mood for today?"
        )
    if re.match(r"(thanks|thank you)\b", text):
        return "You're very welcome! Happy reading 📚"
    if re.match(r"(bye|goodbye)\b", text):
        return "Goodbye! Come back whenever you need your next great read 📖"
    return (
        "I'm your JustBooks AI assistant. I can help you:\n"
        "• Discover books by genre, mood, or theme\n"
        "• View book details and author info\n"
        "• Manage your cart and place orders\n\n"
        "What would you like to explore?"
    )


# ── Public entry point ────────────────────────────────────────────────────────
def run_agent(messages: List[Dict[str, Any]], session_id: str) -> str:
    """
    Classify the latest user message, delegate to the right sub-agent,
    and return its reply.
    """
    user_text = messages[-1]["content"] if messages else ""
    intent = _classify_intent(user_text)

    if intent == "cart":
        return _CART_AGENT.run(messages, session_id)
    if intent == "order":
        return _ORDER_AGENT.run(messages, session_id)
    if intent == "chitchat":
        return _chitchat(user_text)
    # default → catalogue
    return _CATALOGUE_AGENT.run(messages, session_id)
