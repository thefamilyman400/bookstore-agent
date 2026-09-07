"""
agents/base_agent.py

MCPClient  — thin subprocess wrapper that speaks JSON-RPC 2.0 to mcp_server.py
             over stdin/stdout.  One instance per agent call (stateless).

BaseAgent  — shared Groq tool-call loop + mock fallback that every
             specialist agent inherits.
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional


# ── MCPClient ─────────────────────────────────────────────────────────────────

class MCPClient:
    """
    Spawns mcp_server.py as a subprocess and communicates via
    newline-delimited JSON-RPC 2.0 over its stdin/stdout.

    Usage:
        with MCPClient() as client:
            tools = client.list_tools()
            result = client.call_tool("search_books", {"query": "sci-fi"})
    """

    MCP_SERVER = os.path.join(os.path.dirname(os.path.dirname(__file__)), "mcp_server.py")

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._req_id = 0

    def __enter__(self) -> "MCPClient":
        self._proc = subprocess.Popen(
            [sys.executable, self.MCP_SERVER],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        # MCP handshake
        self._send("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "bookstore-agent", "version": "1.0"},
        })
        self._recv()  # consume initialize response
        # Send initialized notification (no response expected)
        self._proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}) + "\n"
        )
        self._proc.stdin.flush()
        return self

    def __exit__(self, *_: Any) -> None:
        if self._proc:
            try:
                self._proc.stdin.close()
                self._proc.wait(timeout=5)
            except Exception:
                self._proc.kill()

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _send(self, method: str, params: Dict[str, Any]) -> int:
        req_id = self._next_id()
        msg = json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        self._proc.stdin.write(msg + "\n")
        self._proc.stdin.flush()
        return req_id

    def _recv(self) -> Dict[str, Any]:
        line = self._proc.stdout.readline()
        return json.loads(line)

    def list_tools(self) -> List[Dict[str, Any]]:
        """Return the list of tool schemas from the MCP server."""
        self._send("tools/list", {})
        response = self._recv()
        return response.get("result", {}).get("tools", [])

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        """Call a tool and return the parsed result dict."""
        self._send("tools/call", {"name": name, "arguments": arguments})
        response = self._recv()
        if "error" in response:
            return {"error": response["error"].get("message", "MCP error")}
        content = response.get("result", {}).get("content", [])
        if content:
            return json.loads(content[0]["text"])
        return {}


# ── BaseAgent ─────────────────────────────────────────────────────────────────

class BaseAgent:
    """
    Base class for all specialist agents.

    Subclasses declare:
      - TOOLS: list[str]        — MCP tool names this agent may call
      - SYSTEM_PROMPT: str      — role description for the LLM
      - _mock(messages, session_id) -> str  — rule-based fallback
    """

    TOOLS: List[str] = []
    SYSTEM_PROMPT: str = "You are a helpful bookstore assistant."
    MAX_TURNS: int = 5

    def _tool_schemas_text(self) -> str:
        """Build a compact tool list string for the system prompt."""
        # Import here to avoid circular dependency at module load time
        from mcp_server import TOOL_SCHEMAS
        lines = []
        for t in TOOL_SCHEMAS:
            if t["name"] in self.TOOLS:
                props = t.get("inputSchema", {}).get("properties", {})
                params = ", ".join(props.keys()) if props else "none"
                lines.append(f"- {t['name']}({params}): {t['description']}")
        return "\n".join(lines)

    def run(self, messages: List[Dict[str, Any]], session_id: str) -> str:
        """Entry point called by the orchestrator."""
        if os.environ.get("GROQ_API_KEY"):
            return self._groq_run(messages, session_id)
        return self._mock(messages, session_id)

    def _groq_run(self, messages: List[Dict[str, Any]], session_id: str) -> str:
        import groq as groq_sdk

        client = groq_sdk.Groq(api_key=os.environ["GROQ_API_KEY"])
        system = self.SYSTEM_PROMPT.format(tools=self._tool_schemas_text())
        history: List[Dict[str, str]] = [{"role": "system", "content": system}]
        for m in messages:
            history.append({"role": m["role"], "content": m["content"]})

        with MCPClient() as mcp:
            for _ in range(self.MAX_TURNS):
                response = client.chat.completions.create(
                    model=os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
                    messages=history,
                    temperature=0.3,
                )
                msg = response.choices[0].message
                reply = (msg.content or getattr(msg, 'reasoning', None) or '').strip()
                tool_call = _parse_tool_call(reply)
                if tool_call:
                    name = tool_call.get("tool")
                    args = tool_call.get("args", {})
                    # Auto-inject session_id for tools that need it
                    if name in self.TOOLS:
                        from mcp_server import TOOL_SCHEMAS
                        for schema in TOOL_SCHEMAS:
                            if schema["name"] == name:
                                if "session_id" in schema.get("inputSchema", {}).get("properties", {}):
                                    args.setdefault("session_id", session_id)
                                break
                    result = mcp.call_tool(name, args)
                    history.append({"role": "assistant", "content": reply})
                    history.append({"role": "user", "content": f"Tool result: {json.dumps(result)}"})
                else:
                    return reply

        return reply  # max turns exhausted

    def _mock(self, messages: List[Dict[str, Any]], session_id: str) -> str:
        raise NotImplementedError("Subclasses must implement _mock()")


# ── Shared helpers ────────────────────────────────────────────────────────────

def _parse_tool_call(text: str) -> Optional[Dict[str, Any]]:
    import re
    try:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            data = json.loads(match.group())
            if "tool" in data:
                return data
    except Exception:
        pass
    return None
