"""MCP — Model Context Protocol client (no external SDK; JSON-RPC by hand).

Two transports (v1):
  stdio  — spawn the configured command, newline-delimited JSON-RPC over
           stdin/stdout. Covers servers like `npx -y <package>` locally.
  http   — streamable HTTP via httpx: JSON POSTs with an optional
           Mcp-Session-Id, Accept: application/json, text/event-stream.

Supported methods: initialize, notifications/initialized, tools/list,
tools/call. Live connections live in a process-level registry so repeated
chats reuse the same server process. Connection config comes from the
mcp_servers table; secrets (headers) never leave the backend.
"""
import asyncio
import json
import logging
import os
import re
import shlex
from typing import Any, Awaitable, Callable

import httpx

from .. import models

_log = logging.getLogger("ashtra.mcp")

PROTOCOL_VERSION = "2025-06-18"
FALLBACK_PROTOCOL = "2024-11-05"
CONNECT_TIMEOUT = 12.0     # initialize + tools/list budget
TOOL_TIMEOUT = 20.0        # single tools/call


class McpError(RuntimeError):
    pass


def _short(e: BaseException) -> str:
    s = str(e).strip() or type(e).__name__
    return s[:300]


def _conn_id(row) -> str:
    return f"{row.user_id}:{row.id}"


# ---------------------------------------------------------------- stdio ----

class _StdioConn:
    def __init__(self, argv: list[str]):
        self.argv = argv
        self.proc: asyncio.subprocess.Process | None = None
        self._id = 0
        self._stderr_task: asyncio.Task | None = None

    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def start(self, timeout: float = CONNECT_TIMEOUT) -> dict:
        self.proc = await asyncio.create_subprocess_exec(
            *self.argv,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ})
        # keep stderr drained so the pipe can never block the server
        async def _drain():
            try:
                while True:
                    line = await self.proc.stderr.readline()
                    if not line:
                        break
                    _log.debug("mcp stderr: %s", line.decode(errors="replace").rstrip()[:200])
            except Exception:
                pass
        self._stderr_task = asyncio.create_task(_drain())
        try:
            result = await self._rpc(
                "initialize",
                {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                 "clientInfo": {"name": "azx", "version": "1.1"}},
                timeout=timeout)
            await self._notify("notifications/initialized")
            return result
        except Exception:
            await self.close()
            raise

    async def _write(self, msg: dict) -> None:
        if not self.alive():
            raise McpError("server process is not running")
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())
        await self.proc.stdin.drain()

    async def _rpc(self, method: str, params: dict | None = None,
                   timeout: float = TOOL_TIMEOUT) -> dict:
        self._id += 1
        mid = self._id
        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": mid, "method": method}
        if params is not None:
            msg["params"] = params
        await self._write(msg)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise McpError(f"timeout waiting for {method}")
            try:
                line = await asyncio.wait_for(self.proc.stdout.readline(), remaining)
            except asyncio.TimeoutError:
                raise McpError(f"timeout waiting for {method}")
            if not line:
                rc = self.proc.returncode
                raise McpError(f"server closed stdout (exit code {rc})")
            try:
                data = json.loads(line)
            except ValueError:
                continue  # non-JSON noise on stdout — ignore
            if data.get("id") != mid:
                continue  # notification or unrelated response
            if "error" in data:
                err = data["error"] or {}
                raise McpError(f"{method} failed: {err.get('message') or err}")
            return data.get("result") or {}

    async def _notify(self, method: str, params: dict | None = None) -> None:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        try:
            await self._write(msg)
        except Exception:
            pass

    async def rpc(self, method: str, params: dict | None = None,
                  timeout: float = TOOL_TIMEOUT) -> dict:
        return await self._rpc(method, params, timeout)

    async def close(self) -> None:
        proc = self.proc
        self.proc = None
        if self._stderr_task:
            self._stderr_task.cancel()
            self._stderr_task = None
        if proc and proc.returncode is None:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                await asyncio.wait_for(proc.wait(), 3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


# ----------------------------------------------------------------- http ----

class _HttpConn:
    def __init__(self, url: str, headers: dict | None = None):
        self.url = url
        self.headers = dict(headers or {})
        self.session_id: str | None = None
        self._id = 0
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=8.0))

    def alive(self) -> bool:
        return self.client is not None and not self.client.is_closed

    async def _post(self, msg: dict, timeout: float) -> dict | None:
        h = {"Content-Type": "application/json",
             "Accept": "application/json, text/event-stream",
             **self.headers}
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        r = await self.client.post(self.url, json=msg, headers=h, timeout=timeout)
        if r.status_code >= 400:
            raise McpError(f"HTTP {r.status_code}: {r.text[:200]}")
        sid = r.headers.get("mcp-session-id")
        if sid:
            self.session_id = sid
        if r.status_code == 202 or not r.content:
            return None
        ctype = r.headers.get("content-type", "")
        if "text/event-stream" in ctype:
            for line in r.text.splitlines():
                if line.startswith("data:"):
                    try:
                        obj = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    if obj.get("id") == msg.get("id"):
                        return obj
            raise McpError("no JSON-RPC response in SSE stream")
        try:
            return r.json()
        except ValueError:
            raise McpError(f"non-JSON response ({ctype or 'unknown type'})")

    async def _rpc(self, method: str, params: dict | None = None,
                   timeout: float = TOOL_TIMEOUT) -> dict:
        self._id += 1
        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            msg["params"] = params
        resp = await self._post(msg, timeout)
        if resp is None:
            raise McpError(f"{method}: server returned no response")
        if "error" in resp:
            err = resp["error"] or {}
            raise McpError(f"{method} failed: {err.get('message') or err}")
        return resp.get("result") or {}

    async def _notify(self, method: str, params: dict | None = None) -> None:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        try:
            await self._post(msg, 8.0)
        except Exception:
            pass

    async def start(self, timeout: float = CONNECT_TIMEOUT) -> dict:
        try:
            result = await self._rpc(
                "initialize",
                {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                 "clientInfo": {"name": "azx", "version": "1.1"}},
                timeout=timeout)
            await self._notify("notifications/initialized")
            return result
        except Exception:
            await self.close()
            raise

    async def rpc(self, method: str, params: dict | None = None,
                  timeout: float = TOOL_TIMEOUT) -> dict:
        return await self._rpc(method, params, timeout)

    async def close(self) -> None:
        client, self.client = self.client, None
        if client is None or client.is_closed:
            return
        # best-effort session teardown (spec: DELETE with session id)
        try:
            if self.session_id:
                await client.delete(
                    self.url, headers={"Mcp-Session-Id": self.session_id,
                                       "Accept": "application/json, text/event-stream"},
                    timeout=5.0)
        except Exception:
            pass
        try:
            await client.aclose()
        except Exception:
            pass


# ------------------------------------------------------------- registry ----

_conns: dict[str, Any] = {}


def _mk_conn(row):
    if row.transport == "http":
        if not (row.url or "").startswith(("http://", "https://")):
            raise McpError("URL must start with http:// or https://")
        return _HttpConn(row.url, row.headers or {})
    argv = shlex.split(row.command or "") + [str(a) for a in (row.args or [])]
    if not argv:
        raise McpError("Command is empty")
    return _StdioConn(argv)


async def connect(row) -> tuple[list[dict] | None, str | None]:
    """Spawn/handshake + tools/list. Updates row.status/tools/last_error
    (caller commits). Returns (tools, error)."""
    await disconnect(row)
    try:
        conn = _mk_conn(row)
        await conn.start(timeout=CONNECT_TIMEOUT)
        res = await conn.rpc("tools/list", {}, timeout=CONNECT_TIMEOUT)
        tools = [t for t in (res.get("tools") or []) if isinstance(t, dict) and t.get("name")]
        _conns[_conn_id(row)] = conn
        row.status = "connected"
        row.tools = tools
        row.last_error = None
        return tools, None
    except Exception as e:
        err = _short(e)
        row.status = "error"
        row.last_error = err
        _log.warning("mcp connect failed for %s: %s", row.name, err)
        return None, err


async def disconnect(row) -> None:
    conn = _conns.pop(_conn_id(row), None)
    if conn is not None:
        try:
            await conn.close()
        except Exception:
            pass


async def disconnect_all() -> None:
    for cid, conn in list(_conns.items()):
        _conns.pop(cid, None)
        try:
            await conn.close()
        except Exception:
            pass


def is_connected(row) -> bool:
    conn = _conns.get(_conn_id(row))
    return conn is not None and conn.alive()


async def _call(conn, tool_name: str, arguments: dict,
                timeout: float = TOOL_TIMEOUT) -> str:
    res = await conn.rpc("tools/call",
                         {"name": tool_name, "arguments": arguments or {}},
                         timeout=timeout)
    content = res.get("content") or []
    texts = [c.get("text", "") for c in content
             if isinstance(c, dict) and c.get("type") == "text"]
    if res.get("isError"):
        raise McpError("; ".join(t for t in texts if t) or "tool reported an error")
    if texts:
        return "\n".join(t for t in texts if t)[:8000]
    return json.dumps(res, ensure_ascii=False)[:4000]


async def call_tool(row, tool_name: str, arguments: dict,
                    timeout: float = TOOL_TIMEOUT) -> str:
    conn = _conns.get(_conn_id(row))
    if conn is None or not conn.alive():
        raise McpError(f"server '{row.name}' is not connected")
    return await _call(conn, tool_name, arguments, timeout)


# ------------------------------------------- chat integration (tool loop) ---

def _safe_tool_name(server: str, tool: str) -> str:
    """OpenAI function names: [A-Za-z0-9_-]{1,64}."""
    raw = f"{server}_{tool}"
    return re.sub(r"[^A-Za-z0-9_-]", "_", raw)[:64]


async def tools_for_chat(db, user_id: str
                         ) -> tuple[list[dict], Callable[[str, dict], Awaitable[str]] | None]:
    """Live tool schemas for the LLM + an executor, from this user's enabled,
    connected MCP servers. Lazily (re)connects rows whose process died.
    Returns ([openai tools], executor) — ([], None) when nothing is live."""
    try:
        rows = (db.query(models.MCPServer)
                .filter_by(user_id=user_id, enabled=True)
                .order_by(models.MCPServer.created_at).all())
    except Exception:
        return [], None
    if not rows:
        return [], None
    schemas: list[dict] = []
    mapping: dict[str, tuple[str, str]] = {}  # fn name → (conn id, original tool)
    any_live = False
    for row in rows:
        conn = _conns.get(_conn_id(row))
        if conn is None or not conn.alive():
            if conn is not None:
                _conns.pop(_conn_id(row), None)
                row.status = "disconnected"
            # lazy reconnect for rows that were connected before a restart;
            # rows in error wait for an explicit Connect in the UI
            if row.status == "error":
                continue
            tools, err = await connect(row)
            try:
                db.commit()
            except Exception:
                try:
                    db.rollback()
                except Exception:
                    pass
            if not tools:
                continue
            conn = _conns.get(_conn_id(row))
        any_live = True
        for t in (row.tools or []):
            orig = str(t.get("name") or "")
            if not orig:
                continue
            fname = _safe_tool_name(row.name or "server", orig)
            n = 2
            while fname in mapping:
                fname = _safe_tool_name(f"{row.name}_{n}", orig)
                n += 1
            mapping[fname] = (_conn_id(row), orig)
            schemas.append({
                "type": "function",
                "function": {
                    "name": fname,
                    "description": str(t.get("description") or f"MCP tool {orig}")[:1024],
                    "parameters": (t.get("inputSchema")
                                   or {"type": "object", "properties": {}}),
                },
            })
    if not any_live or not schemas:
        return [], None

    async def _execute(fn_name: str, arguments: dict) -> str:
        cid, orig = mapping[fn_name]
        conn2 = _conns.get(cid)
        if conn2 is None or not conn2.alive():
            raise McpError("server disconnected")
        return await _call(conn2, orig, arguments)

    return schemas, _execute
