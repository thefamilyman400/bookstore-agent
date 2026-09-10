"""
FastAPI backend — serves the chat API and the static UI.
"""
from __future__ import annotations
import json
import logging
import logging.config
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List

# Load .env file before anything else so all env vars are available
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=False)

# ── Structured JSON logging (emitted before any other import so all loggers
#    inherit this config, including uvicorn's access log handler) ───────────────
_LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.config.dictConfig({
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": "logging.Formatter",
            "fmt": '{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}',
            "datefmt": "%Y-%m-%dT%H:%M:%S",
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "stream": "ext://sys.stdout",
        }
    },
    "root": {"level": _LOG_LEVEL, "handlers": ["console"]},
})

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)

# ── Load GROQ_API_KEY from AWS Secrets Manager at startup ─────────────────────
def _load_secrets() -> None:
    """Fetch GROQ_API_KEY from AWS Secrets Manager and inject into os.environ.

    1. If GROQ_API_KEY is already set (e.g. in local .env), use it directly.
    2. Otherwise, fetch from AWS Secrets Manager via IAM role / AWS profile.
    3. If AWS credentials are not available, log a warning and fallback to mock agent.
    """
    if os.environ.get("GROQ_API_KEY"):
        logger.info("Using GROQ_API_KEY from environment.")
        return

    arn = "arn:aws:secretsmanager:ap-south-1:913524927483:secret:inc-assistant-api-keys-AKmRZ2"
    region = os.environ.get("AWS_DEFAULT_REGION", "ap-south-1")
    try:
        client = boto3.client("secretsmanager", region_name=region)
        response = client.get_secret_value(SecretId=arn)
        secret = json.loads(response["SecretString"])
        groq_key = secret.get("GROQ_API_KEY")
        if not groq_key:
            logger.warning("GROQ_API_KEY not found in secret JSON from AWS Secrets Manager.")
            return
        os.environ["GROQ_API_KEY"] = groq_key
        logger.info("GROQ_API_KEY loaded from AWS Secrets Manager.")
    except (BotoCoreError, ClientError) as exc:
        logger.warning(
            "AWS Secrets Manager credentials unavailable (%s). "
            "Falling back to built-in rule-based Mock LLM.",
            exc,
        )

_load_secrets()

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agents.orchestrator import run_agent
from rag_store import BACKEND, BOOKS
from auth_service import router as auth_router, get_current_user

app = FastAPI(title="Agentic Bookstore", version="1.0")

# CORS origins: comma-separated list in CORS_ORIGINS env var, defaults to * for
# local dev. Set to your frontend domain(s) in production.
_cors_origins = os.environ.get("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Mount auth router  (/api/auth/register, /login, /logout, /refresh, /me)
app.include_router(auth_router)

# ── Serve static files (UI) ───────────────────────────────────────────────────
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ── Pydantic models ───────────────────────────────────────────────────────────
class Message(BaseModel):
    role: str          # "user" or "assistant"
    content: str

class ChatRequest(BaseModel):
    session_id: str
    messages: List[Message]


# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/")
def serve_ui():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/health", tags=["ops"])
def health():
    """Liveness and readiness probe endpoint for Kubernetes.
    Returns 200 as long as the process is running. Does NOT hit the DB or LLM —
    those are checked by the /api/info endpoint (used as a startup probe).
    """
    return {"status": "ok"}


@app.get("/api/info")
def info():
    model = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
    llm_name = f"Groq ({model})" if os.environ.get("GROQ_API_KEY") else "Rule-based Mock LLM"
    return {
        "total_books": len(BOOKS),
        "rag_backend": BACKEND,
        "llm": llm_name,
        "description": "JustBooks Inspired Agentic Bookstore — RAG + MCP + Vector DB",
    }


@app.post("/api/chat")
def chat(
    req: ChatRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Protected chat endpoint — requires a valid Bearer token from /api/auth/login."""
    if not req.messages:
        raise HTTPException(status_code=400, detail="messages list is empty")
    messages = [{"role": m.role, "content": m.content} for m in req.messages]
    # Prefix the user's name into the session so the agent can personalise
    session_id = f"{current_user['id']}:{req.session_id}"
    reply = run_agent(messages, session_id)
    return {"reply": reply, "session_id": req.session_id, "user": current_user["email"]}


@app.get("/api/session")
def new_session():
    """Generate a fresh session ID (call after login)."""
    return {"session_id": str(uuid.uuid4())}


# ── Direct cart endpoints (bypasses agent — used by UI buttons) ───────────────
from mcp_server import add_to_cart as _add_to_cart, view_cart as _view_cart

class CartAddRequest(BaseModel):
    session_id: str
    book_id: str
    quantity: int = 1

@app.post("/api/cart/add")
def cart_add(
    req: CartAddRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    session_id = f"{current_user['id']}:{req.session_id}"
    result = _add_to_cart(session_id, req.book_id, req.quantity)
    return result

@app.get("/api/cart")
def cart_view(
    session_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    full_session_id = f"{current_user['id']}:{session_id}"
    return _view_cart(full_session_id)


@app.get("/api/books")
def all_books(genre: str = "", q: str = "", limit: int = 100):
    books = BOOKS
    if genre and genre.lower() != "all":
        books = [b for b in books if genre.lower() in b["genre"].lower()]
    if q:
        query = q.lower()
        books = [
            b for b in books
            if query in b["title"].lower()
            or query in b["author"].lower()
            or query in b["genre"].lower()
            or any(query in tag.lower() for tag in b.get("tags", []))
        ]
    return {"books": books[:limit], "total": len(books)}
