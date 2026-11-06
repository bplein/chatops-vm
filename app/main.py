"""vmware-aiops-web: per-session Claude chat over the vmware-aiops MCP server.

Each browser session:
  - verifies vCenter creds (pyVmomi SmartConnect) at login,
  - holds the password only in server RAM (keyed by an unguessable session id),
  - spawns a ClaudeSDKClient whose MCP stdio server (`vmware-aiops mcp`) gets
    that session's config path + password via env (per-server env passing),
  - exposes ONLY the MCP tools to Claude (no Bash/file tools in the container).

Nothing is persisted: sessions live in memory; a container restart logs everyone out.
"""

import asyncio
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from itsdangerous import BadSignature, URLSafeSerializer

from pages import CHAT_PAGE, LOGIN_PAGE

log = logging.getLogger("web")

APP = FastAPI(title="vmware-aiops-web")
SESSION_TTL_S = 4 * 3600
MAX_SESSIONS = 8

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
SESSIONS_DIR = DATA_DIR / "sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

# Cookie signing key: per-process random (restart = everyone logged out, by design).
_SER = URLSafeSerializer(uuid.uuid4().hex, salt="aiops-web")
MODEL = os.environ.get("WEB_MODEL", "Kimi-K3-lab-claude-compatible[1m]")

SYSTEM = (
    "You are vSphere Ops, a chat assistant embedded in a web UI. You administer VMware "
    "vSphere through the MCP tools of the connected `vsphere` server (the vmware-aiops "
    "package). Stay inside those tools; never claim you ran something you did not. Be "
    "concise. Prefer tables for inventory. For any write operation, the tools themselves "
    "enforce confirm= gates: show the blast-radius preview plainly and proceed only when "
    "the user explicitly confirms. You have no filesystem, shell, or other tools."
)

SESSIONS: dict[str, dict] = {}
_LOCK = asyncio.Lock()


def _scan_sid(request: Request) -> str | None:
    cookie = request.cookies.get("aisession")
    if not cookie:
        return None
    try:
        return _SER.loads(cookie)
    except BadSignature:
        return None


def _get_session(sid: str) -> dict | None:
    s = SESSIONS.get(sid)
    if s and time.time() - s["last_active"] < SESSION_TTL_S:
        return s
    return None


def _verify_vcenter(host: str, username: str, password: str, verify_ssl: bool) -> str | None:
    """Return None on success, an error string otherwise."""
    from pyVim.connect import Disconnect, SmartConnect

    try:
        import ssl

        ctx = None
        if not verify_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        si = SmartConnect(host=host, user=username, pwd=password, sslContext=ctx)
        Disconnect(si)
        return None
    except Exception as e:  # any failure means "can't log in"
        msg = str(e)
        if re.search(r"invalid login|incorrect user name or password", msg, re.I):
            return "Login failed: invalid username or password."
        return f"Could not connect to vCenter: {msg[:200]}"


def _write_session_config(s: dict) -> Path:
    path = SESSIONS_DIR / s["id"]
    path.mkdir(exist_ok=True)
    cfg = path / "config.yaml"
    cfg.write_text(
        "targets:\n"
        "  - name: web\n"
        f"    host: {s['host']}\n"
        "    type: vcenter\n"
        f"    username: {s['username']}\n"
        "    port: 443\n"
        f"    verify_ssl: {str(s['verify_ssl']).lower()}\n"
        "scanner: {enabled: false}\n"
        "notify: {webhook_url: ''}\n"
    )
    cfg.chmod(0o600)
    return cfg


async def _dispose_client(s: dict) -> None:
    client = s.get("client")
    s["client"] = None
    if client:
        try:
            await client.disconnect()
        except Exception:
            log.exception("client disconnect failed")


async def _ensure_client(s: dict) -> None:
    if s.get("client"):
        return
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

    cfg = _write_session_config(s)
    options = ClaudeAgentOptions(
        system_prompt={"type": "custom", "prompt": SYSTEM, "snapshot": True},
        model=MODEL,
        permission_mode="bypassPermissions",
        allowed_tools=["mcp__vsphere"],
        cwd=str(SESSIONS_DIR / s["id"]),
        mcp_servers={
            "vsphere": {
                "type": "stdio",
                "command": "vmware-aiops",
                "args": ["mcp"],
                "env": {
                    "VMWARE_AIOPS_CONFIG": str(cfg),
                    "VMWARE_WEB_PASSWORD": s["password"],
                },
            }
        },
        include_partial_messages=True,
        max_turns=25,
        setting_sources=[],
    )
    client = ClaudeSDKClient(options=options)
    await client.connect()
    s["client"] = client


@APP.get("/healthz")
async def healthz():
    return {"ok": True, "sessions": len(SESSIONS)}


@APP.get("/", response_class=HTMLResponse)
async def root(request: Request):
    sid = _scan_sid(request)
    if not sid or not _get_session(sid):
        return HTMLResponse(LOGIN_PAGE.replace("__ERROR__", ""))
    return RedirectResponse("/chat")


@APP.post("/login")
async def login(host: str = Form(...), username: str = Form(...),
                password: str = Form(...), verify_ssl: bool = Form(False)):
    host = host.strip()
    username = username.strip()
    err = await asyncio.to_thread(_verify_vcenter, host, username, password, verify_ssl)
    if err:
        page = LOGIN_PAGE.replace("__ERROR__", f'<p class="err">{err}</p>')
        return HTMLResponse(page, status_code=401)
    sid = uuid.uuid4().hex
    SESSIONS[sid] = {"id": sid, "host": host, "username": username,
                     "password": password, "verify_ssl": verify_ssl,
                     "created": time.time(), "last_active": time.time(), "client": None}
    if len(SESSIONS) > MAX_SESSIONS:  # evict oldest; disconnect in background
        oldest = min(SESSIONS, key=lambda k: SESSIONS[k]["last_active"])
        if oldest != sid:
            old = SESSIONS.pop(oldest)
            asyncio.ensure_future(_dispose_client(old))
    resp = RedirectResponse("/chat", status_code=303)
    resp.set_cookie("aisession", _SER.dumps(sid), httponly=True, secure=True, samesite="lax")
    return resp


@APP.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    sid = _scan_sid(request)
    if not sid or not _get_session(sid):
        return RedirectResponse("/")
    s = SESSIONS[sid]
    return HTMLResponse(CHAT_PAGE.replace("__HOST__", s["host"]).replace("__USER__", s["username"]))


@APP.get("/logout")
async def logout(request: Request):
    sid = _scan_sid(request)
    s = SESSIONS.pop(sid, None) if sid else None
    if s:
        asyncio.ensure_future(_dispose_client(s))
    resp = RedirectResponse("/")
    resp.delete_cookie("aisession")
    return resp


@APP.post("/api/chat")
async def api_chat(request: Request):
    sid = _scan_sid(request)
    if not sid or not _get_session(sid):
        return JSONResponse({"error": "not signed in"}, status_code=401)
    s = SESSIONS[sid]
    s["last_active"] = time.time()
    body = await request.json()
    message = (body.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": "empty"}, status_code=400)

    from claude_agent_sdk import AssistantMessage, ResultMessage, StreamEvent

    async def pump():
        def emit(payload: dict):
            return f"data: {json.dumps(payload)}\n\n".encode()

        async with _LOCK:  # one SSE stream drives one SDK client at a time
            try:
                await _ensure_client(s)
                client = s["client"]
                await client.query(message)
                streamed_any = False
                async for item in client.receive_response():
                    if isinstance(item, StreamEvent):
                        d = item.event.get("delta", {})
                        if d.get("type") == "text_delta" and d.get("text"):
                            streamed_any = True
                            yield emit({"text": d["text"]})
                    elif isinstance(item, AssistantMessage):
                        if not streamed_any:
                            for b in item.content:
                                if getattr(b, "type", None) == "text":
                                    yield emit({"text": b.text})
                    elif isinstance(item, ResultMessage):
                        if item.is_error:
                            yield emit({"error": item.result or item.subtype})
                        if item.permission_denials:
                            yield emit({"note": f"{len(item.permission_denials)} action(s) were gated"})
                        yield emit({"done": True})
            except Exception as e:
                log.exception("chat failed")
                yield emit({"error": f"{type(e).__name__}: {str(e)[:300]}"})

    return StreamingResponse(pump(), media_type="text/event-stream")


if __name__ == "__main__":
    uvicorn.run(APP, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
