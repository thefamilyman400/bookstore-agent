# 📚 Agentic Bookstore — RAG + MCP + Vector DB + SQLite

A working prototype of an agentic e-commerce bookstore built using:
- **RAG** (Retrieval-Augmented Generation) — semantic book search over 100 books
- **MCP** (Model Context Protocol) — tools the agent can invoke
- **Vector DB** — in-memory TF-IDF cosine similarity store (upgrades to sentence-transformers automatically if installed)
- **SQLite Database** (`bookstore.db`) — persistent user accounts, shopping carts, orders, and token revocation
- **Groq LLM** (`llama-3.3-70b-versatile`) with key fetched from AWS Secrets Manager
- **FastAPI** backend + JustBooks-inspired storefront & AI assistant UI

---

## 🚀 Quick Start

### 1. Configure environment variables
Copy `.env.example` to `.env` or set environment variables:
```bash
# Required JWT secret
export JWT_SECRET="your-strong-random-jwt-secret"

# AWS region for Secrets Manager (default: ap-south-1)
export AWS_DEFAULT_REGION="ap-south-1"
```

> **Note on LLM / AWS Secrets Manager:**
> On startup, `app.py` automatically retrieves `GROQ_API_KEY` from AWS Secrets Manager ARN:
> `arn:aws:secretsmanager:ap-south-1:913524927483:secret:inc-assistant-api-keys-AKmRZ2`
> In production, authentication is handled via the attached instance IAM role (no static keys).
> If AWS or `GROQ_API_KEY` is unavailable, the agent falls back to a built-in rule-based mock LLM.

### 2. Install dependencies
```bash
cd bookstore-agent
pip install -r requirements.txt
```

### 3. (Optional) Install sentence-transformers for better semantic search
```bash
pip install sentence-transformers
```

### 4. Run the server
```bash
uvicorn app:app --reload --port 8000
```

### 5. Open your browser
```
http://localhost:8000
```

---

## 🏗️ Architecture

```
User (Chat UI)
    │
    ▼
FastAPI app.py          ← serves UI + REST /api/chat + JWT auth
    │
    ▼
agent.py                ← Orchestrator (Groq llama-3.3-70b-versatile or Mock LLM)
    │           │
    ▼           ▼
mcp_server.py       rag_store.py
(MCP Tools)         (Vector DB / RAG)
    │                   │
    ▼                   ▼
in-memory state     books.json (100 books)
(carts, orders)     TF-IDF / sentence-transformers
```

## 🛠️ MCP Tools Available

| Tool | Description |
|---|---|
| `search_books` | RAG-powered semantic search |
| `get_book_detail` | Full details + delivery estimate |
| `list_genres` | All available genres |
| `add_to_cart` | Add book to cart |
| `view_cart` | Show cart contents |
| `remove_from_cart` | Remove item |
| `create_order` | Checkout + place order |
| `get_order_history` | Past orders |
| `cancel_order` | Cancel within 48hrs |

## 💬 Example Queries
- *"Find me a gripping psychological thriller"*
- *"Recommend a book about space survival"*
- *"Add B008 to my cart"*
- *"Show details for B015"*
- *"View my cart"*
- *"Checkout to 42 Baker Street, London"*
- *"Cancel order ORD-XXXXXXXX"*
