"""
MCP Server — stdio JSON-RPC 2.0 transport.

Can be used in two ways:
  1. As a subprocess MCP server (run directly: python mcp_server.py)
     Speaks JSON-RPC 2.0 over stdin/stdout.
     Supports: initialize, tools/list, tools/call

  2. Imported as a module by agents that call tool functions directly.
     The TOOLS dict and individual functions remain importable.
"""
from __future__ import annotations
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from rag_store import semantic_search, BOOKS
from db import (
    get_cart_items,
    save_cart_item,
    remove_cart_item as db_remove_cart_item,
    clear_cart as db_clear_cart,
    save_order as db_save_order,
    get_orders_by_session,
    get_order_by_id,
    update_order_status,
)


def _book_by_id(book_id: str) -> Optional[Dict[str, Any]]:
    return next((b for b in BOOKS if b["id"] == book_id), None)


# ═════════════════════════════════════════════════════════════════════════════
# TOOL IMPLEMENTATIONS
# ═════════════════════════════════════════════════════════════════════════════

def search_books(query: str, top_k: int = 5) -> Dict[str, Any]:
    """RAG-powered semantic book search."""
    results = semantic_search(query, top_k=top_k)
    if not results:
        return {"results": [], "message": "No books found matching your query."}
    return {
        "results": [
            {
                "id": b["id"],
                "title": b["title"],
                "author": b["author"],
                "genre": b["genre"],
                "price": b["price"],
                "rating": b["rating"],
                "relevance_score": b["score"],
            }
            for b in results
        ],
        "count": len(results),
    }


def get_book_detail(book_id: str) -> Dict[str, Any]:
    """Get full details of a specific book by ID."""
    book = _book_by_id(book_id)
    if not book:
        return {"error": f"Book '{book_id}' not found."}
    delivery = (datetime.now() + timedelta(days=3)).strftime("%B %d, %Y")
    return {
        "id": book["id"],
        "title": book["title"],
        "author": book["author"],
        "author_bio": book.get("author_bio", ""),
        "genre": book["genre"],
        "price": book["price"],
        "rating": book["rating"],
        "synopsis": book["synopsis"],
        "tags": book["tags"],
        "estimated_delivery": delivery,
        "in_stock": True,
    }


def list_genres() -> Dict[str, Any]:
    """List all available genres in the catalogue."""
    genres = sorted(set(b["genre"] for b in BOOKS))
    return {"genres": genres, "count": len(genres)}


def add_to_cart(session_id: str, book_id: str, quantity: int = 1) -> Dict[str, Any]:
    """Add a book to the shopping cart."""
    book = _book_by_id(book_id)
    if not book:
        return {"error": f"Book '{book_id}' not found."}
    save_cart_item(
        session_id,
        {"book_id": book_id, "title": book["title"], "author": book["author"], "price": book["price"]},
        quantity=quantity,
    )
    cart = get_cart_items(session_id)
    total = sum(i["price"] * i["quantity"] for i in cart)
    return {
        "message": f"Added '{book['title']}' to cart.",
        "cart_items": len(cart),
        "cart_total": round(total, 2),
    }


def view_cart(session_id: str) -> Dict[str, Any]:
    """View current cart contents."""
    cart = get_cart_items(session_id)
    if not cart:
        return {"message": "Your cart is empty.", "items": [], "total": 0.0}
    total = sum(i["price"] * i["quantity"] for i in cart)
    return {"items": cart, "item_count": len(cart), "total": round(total, 2)}


def remove_from_cart(session_id: str, book_id: str) -> Dict[str, Any]:
    """Remove a book from the cart."""
    success = db_remove_cart_item(session_id, book_id)
    cart = get_cart_items(session_id)
    if success:
        return {"message": "Item removed from cart.", "cart_items": len(cart)}
    return {"error": "Item not found in cart."}


def create_order(session_id: str, delivery_address: str) -> Dict[str, Any]:
    """Checkout and create an order from the current cart."""
    cart = get_cart_items(session_id)
    if not cart:
        return {"error": "Cannot create order — cart is empty."}
    order_id = f"ORD-{uuid.uuid4().hex[:8].upper()}"
    total = sum(i["price"] * i["quantity"] for i in cart)
    delivery = (datetime.now() + timedelta(days=3)).strftime("%B %d, %Y")
    order = {
        "order_id": order_id,
        "session_id": session_id,
        "items": list(cart),
        "delivery_address": delivery_address,
        "total": round(total, 2),
        "status": "Confirmed",
        "estimated_delivery": delivery,
        "placed_at": datetime.now(timezone.utc).isoformat(),
    }
    db_save_order(order)
    db_clear_cart(session_id)
    return {
        "message": "Order placed successfully!",
        "order_id": order_id,
        "total": round(total, 2),
        "estimated_delivery": delivery,
        "status": "Confirmed",
    }


def get_order_history(session_id: str) -> Dict[str, Any]:
    """Get past orders for the session."""
    orders = get_orders_by_session(session_id)
    if not orders:
        return {"message": "No orders found.", "orders": []}
    return {"orders": orders, "count": len(orders)}


def cancel_order(session_id: str, order_id: str) -> Dict[str, Any]:
    """Cancel an order (within 48 hours of placement)."""
    order = get_order_by_id(session_id, order_id)
    if not order:
        return {"error": f"Order '{order_id}' not found."}
    if order["status"] == "Cancelled":
        return {"error": "Order is already cancelled."}
    placed_at_raw = order.get("placed_at")
    if placed_at_raw:
        try:
            placed_at = datetime.fromisoformat(placed_at_raw)
            if placed_at.tzinfo is None:
                placed_at = placed_at.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - placed_at > timedelta(hours=48):
                return {"error": "Order cannot be cancelled after 48 hours."}
        except (ValueError, TypeError):
            pass
    update_order_status(order_id, "Cancelled")
    return {
        "message": f"Order {order_id} has been cancelled.",
        "refund": f"${order['total']:.2f} will be refunded within 3-5 business days.",
    }


# ── Tool registry (importable by agents) ─────────────────────────────────────
TOOLS: Dict[str, Any] = {
    "search_books":      search_books,
    "get_book_detail":   get_book_detail,
    "list_genres":       list_genres,
    "add_to_cart":       add_to_cart,
    "view_cart":         view_cart,
    "remove_from_cart":  remove_from_cart,
    "create_order":      create_order,
    "get_order_history": get_order_history,
    "cancel_order":      cancel_order,
}

# ── MCP tool schemas (JSON Schema format for tools/list) ─────────────────────
TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "name": "search_books",
        "description": "Search books using natural language (RAG-powered semantic search).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language search query"},
                "top_k": {"type": "integer", "description": "Number of results (default 5)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_book_detail",
        "description": "Get full details, synopsis, tags, and estimated delivery for a book by ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "book_id": {"type": "string", "description": "Book ID e.g. B001"},
            },
            "required": ["book_id"],
        },
    },
    {
        "name": "list_genres",
        "description": "List all available genres in the book catalogue.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "add_to_cart",
        "description": "Add a book to the shopping cart.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "book_id": {"type": "string"},
                "quantity": {"type": "integer", "description": "Default 1"},
            },
            "required": ["session_id", "book_id"],
        },
    },
    {
        "name": "view_cart",
        "description": "View all items currently in the cart with total price.",
        "inputSchema": {
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "required": ["session_id"],
        },
    },
    {
        "name": "remove_from_cart",
        "description": "Remove a specific book from the cart.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "book_id": {"type": "string"},
            },
            "required": ["session_id", "book_id"],
        },
    },
    {
        "name": "create_order",
        "description": "Checkout: create a confirmed order from the cart and clear it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "delivery_address": {"type": "string"},
            },
            "required": ["session_id", "delivery_address"],
        },
    },
    {
        "name": "get_order_history",
        "description": "Retrieve all past orders placed in this session.",
        "inputSchema": {
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "required": ["session_id"],
        },
    },
    {
        "name": "cancel_order",
        "description": "Cancel an existing order by order ID (within 48 hours of placement).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "order_id": {"type": "string"},
            },
            "required": ["session_id", "order_id"],
        },
    },
]


# ═════════════════════════════════════════════════════════════════════════════
# JSON-RPC 2.0 stdio MCP server (only active when run directly)
# ═════════════════════════════════════════════════════════════════════════════

def _send(obj: Any) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _error(req_id: Any, code: int, message: str) -> None:
    _send({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


def _handle(request: Dict[str, Any]) -> None:
    req_id = request.get("id")
    method = request.get("method", "")
    params = request.get("params", {})

    if method == "initialize":
        _send({
            "jsonrpc": "2.0", "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "bookstore-mcp", "version": "1.0"},
            },
        })

    elif method == "tools/list":
        _send({"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOL_SCHEMAS}})

    elif method == "tools/call":
        tool_name = params.get("name")
        args = params.get("arguments", {})
        fn = TOOLS.get(tool_name)
        if not fn:
            _error(req_id, -32601, f"Unknown tool: {tool_name}")
            return
        try:
            result = fn(**args)
            _send({
                "jsonrpc": "2.0", "id": req_id,
                "result": {"content": [{"type": "text", "text": json.dumps(result)}]},
            })
        except Exception as exc:
            _error(req_id, -32603, str(exc))

    elif method == "notifications/initialized":
        pass  # no response for notifications

    else:
        if req_id is not None:
            _error(req_id, -32601, f"Method not found: {method}")


def _serve() -> None:
    """Read newline-delimited JSON-RPC messages from stdin and respond on stdout."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            _send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"Parse error: {exc}"}})
            continue
        _handle(request)


if __name__ == "__main__":
    _serve()
