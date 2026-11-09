"""chatops-vm: multi-user, per-session chat-ops for vSphere.

Flow: vCenter sign-in -> choose AI client (Claude Code / Gemini CLI / Codex CLI)
-> authenticate that client (API key paste, or Codex device-code)
-> chat; the client runs `vmware-aiops` MCP inside an ephemeral per-session pod.

Boundaries: vCenter password + AI credential live only in server RAM and the
session's k8s Secret (deleted on logout/expiry). Session pods get no k8s API
token. Web tier replicas are safe: session state lives in the cluster, and the
Route cookie pins a browser to the pod that served its login.
"""

import asyncio
import html
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from itsdangerous import BadSignature, URLSafeSerializer

from pages import CHAT_PAGE, LOGIN_PAGE
from pages_platform import AUTH_PAGE, PLATFORM_PAGE

# Developer-launched wrappers (e.g. local-claude.sh) export lab/model env that must
# NOT reach web users' Claude sessions. Remove anything a user's subprocess could
# otherwise inherit; the deployment env (manifest/secret) is the only source left.
_SCRUB = [
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_FABLE_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL",
    "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS",
    "CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN",
]
for _k in _SCRUB:
    os.environ.pop(_k, None)

log = logging.getLogger("web")

APP = FastAPI(title="chatops-vm")
SESSION_TTL_S = 4 * 3600
MAX_SESSIONS = int(os.environ.get("MAX_SESSIONS", "8"))

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
SESSIONS_DIR = DATA_DIR / "sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

_SER = URLSafeSerializer(uuid.uuid4().hex, salt="aiops-web")  # restart = logout all

try:
    from importlib.metadata import version as _pkgver
    AIOPS_VERSION = _pkgver("vmware-aiops")
except Exception:
    AIOPS_VERSION = "unknown"


def _with_version(page: str) -> str:
    return page.replace("__AIOPS_VERSION__", AIOPS_VERSION)

PLATFORMS = {"claude": "Claude Code", "gemini": "Gemini CLI", "codex": "Codex CLI"}

# Per-platform auth instructions, rendered into AUTH_PAGE. Written frankly:
# Claude API keys are billed by Anthropic; Gemini keys from AI Studio have a free tier;
# Codex device auth uses the user's own ChatGPT account.
AUTH_INSTRUCTIONS = {
    "claude": (
        "<p>Open a terminal on any machine where Claude Code is already signed in with your "
        "enterprise account (your laptop is fine) and run:</p>"
        "<div class='cmdrow'><code>claude setup-token</code>"
        "<button type='button' class='copybtn' onclick=\"navigator.clipboard.writeText('claude setup-token');this.textContent='copied';setTimeout(()=>this.textContent='copy',1500)\">copy</button></div>"
        "<p>Paste the token it prints below — it authorizes this session as <b>you</b>; "
        "no org API keys needed, and it lives only in this session's pod.</p>"
        "<form method='post' action='/session/auth/key'>"
        "<label>Claude token</label>"
        "<input type='password' name='api_key' autocomplete='off' required>"
        "<button type='submit'>Save and continue</button></form>"
    ),
    "gemini": (
        "<p>Gemini CLI needs a <b>Gemini API key</b> (Google AI Studio &rarr; Get API key); "
        "there is a free tier.</p>"
        "<form method='post' action='/session/auth/key'>"
        "<label>Gemini API key</label>"
        "<input type='password' name='api_key' autocomplete='off' required>"
        "<button type='submit'>Save and continue</button></form>"
    ),
    "codex": (
        "<p>Codex signs in with your ChatGPT account using a <b>device code</b> "
        "(nothing is pasted into this page).</p>"
        "<form method='post' action='/session/auth/device'>"
        "<button type='submit'>Start device sign-in</button></form>"
        "<p><i>Requires device-code login enabled in your ChatGPT security settings.</i></p>"
        "<div id='devstat' class='muted' style='margin-top:12px'></div>"
        "<script>(async()=>{const d=document.getElementById('devstat');"
        "while(true){try{const r=await fetch('/session/auth/device/poll');"
        "if(!r.ok)break;const j=await r.json();"
        "if(j.url)d.innerHTML='<p>Open <a href=\"'+j.url+'\" target=_blank rel=\"noreferrer\">this link</a>'+(j.code?' — enter code <b style=\"font-size:1.4em;letter-spacing:.12em\">'+j.code+'</b>':' — the sign-in code will appear here shortly')+'</p>';"
        "if(j.ready){d.innerHTML='<p>Signed in. Redirecting…</p>';window.location.replace('/chat');break;}"
        "}catch(e){break}"
        "await new Promise(r=>setTimeout(r,2500));}})();</script>"
    ),
}

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
    except Exception as e:
        msg = str(e)
        if re.search(r"invalid login|incorrect user name or password", msg, re.I):
            return "Login failed: invalid username or password."
        return f"Could not connect to vCenter: {msg[:200]}"


async def _dispose(s: dict) -> None:
    s_id = s.get("id")
    if not s_id:
        return
    if s.get("pod_started"):
        def _cleanup():
            import controller
            controller.delete_session(s_id)
        await asyncio.to_thread(_cleanup)


async def _sweeper() -> None:
    while True:
        await asyncio.sleep(600)
        now = time.time()
        dead = [sid for sid, s in SESSIONS.items() if now - s["last_active"] > SESSION_TTL_S]
        for sid in dead:
            log.info("disposing idle session %s", sid[:8])
            s = SESSIONS.pop(sid, None)
            if s:
                await _dispose(s)
        # cluster-side idle sweep: annotation-based, so it is safe to run from
        # every replica (a live session on ANOTHER replica is refreshed by its
        # own chats and never looks idle here). A RAM-based sweep would let one
        # replica delete another replica's live sessions.
        try:
            await asyncio.to_thread(_sweep_idle_cluster)
        except Exception:
            log.exception("periodic idle-session sweep failed")


def _sweep_idle_cluster():
    import controller
    for sid in controller.sweep_idle_sessions(SESSION_TTL_S):
        SESSIONS.pop(sid, None)


@APP.on_event("startup")
async def _start_sweeper():
    # Cluster-side sweep: on restart, web RAM is empty but session pods/secrets survive.
    def _sweep():
        import controller
        controller.sweep_stale_sessions(set(SESSIONS.keys()))
    try:
        await asyncio.to_thread(_sweep)
    except Exception:
        log.exception("startup stale-session sweep failed")
    APP.state.sweeper = asyncio.create_task(_sweeper())


@APP.get("/healthz")
async def healthz():
    return {"ok": True, "sessions": len(SESSIONS)}


_PREFILL_MAX_AGE = 90 * 24 * 3600  # convenience cookies only; never the password


def _login_page(request: Request, error: str = "") -> HTMLResponse:
    page = LOGIN_PAGE.replace("__ERROR__", error)
    page = page.replace("__HOSTVALUE__", html.escape(request.cookies.get("aivc_host", ""), quote=True))
    page = page.replace("__USERVALUE__", html.escape(request.cookies.get("aivc_user", ""), quote=True))
    return HTMLResponse(_with_version(page))


@APP.get("/", response_class=HTMLResponse)
async def root(request: Request):
    sid = _scan_sid(request)
    if not sid or not _get_session(sid):
        return _login_page(request)
    return RedirectResponse("/platform")


@APP.post("/login")
async def login(request: Request, host: str = Form(...), username: str = Form(...),
                password: str = Form(...), verify_ssl: bool = Form(False)):
    host = host.strip()
    username = username.strip()
    err = await asyncio.to_thread(_verify_vcenter, host, username, password, verify_ssl)
    if err:
        return _login_page(request, error=f'<p class="err">{html.escape(err)}</p>')
    sid = uuid.uuid4().hex
    SESSIONS[sid] = {"id": sid, "host": host, "username": username,
                     "password": password, "verify_ssl": verify_ssl,
                     "created": time.time(), "last_active": time.time(),
                     "platform": None, "authed": False, "pod_started": False,
                     "api_key": None}
    if len(SESSIONS) > MAX_SESSIONS:
        oldest = min(SESSIONS, key=lambda k: SESSIONS[k]["last_active"])
        if oldest != sid:
            old = SESSIONS.pop(oldest)
            asyncio.ensure_future(_dispose(old))
    resp = RedirectResponse("/platform", status_code=303)
    resp.set_cookie("aisession", _SER.dumps(sid), httponly=True, secure=True, samesite="lax")
    # Convenience prefill for next login. Username/host are identifiers, not secrets —
    # the password is NEVER cookied (that's the product promise).
    resp.set_cookie("aivc_host", host, httponly=True, secure=True, samesite="lax",
                    max_age=_PREFILL_MAX_AGE)
    resp.set_cookie("aivc_user", username, httponly=True, secure=True, samesite="lax",
                    max_age=_PREFILL_MAX_AGE)
    return resp


@APP.get("/platform", response_class=HTMLResponse)
async def platform_page(request: Request):
    sid = _scan_sid(request)
    if not sid or not _get_session(sid):
        return RedirectResponse("/")
    s = SESSIONS[sid]
    page = PLATFORM_PAGE.replace("__HOST__", html.escape(s["host"])).replace("__USER__", html.escape(s["username"]))
    return HTMLResponse(_with_version(page))


@APP.post("/session/start")
async def session_start(request: Request):
    sid = _scan_sid(request)
    if not sid or not _get_session(sid):
        return JSONResponse({"error": "not signed in"}, status_code=401)
    s = SESSIONS[sid]
    s["last_active"] = time.time()
    form = await request.form()
    platform = form.get("platform")
    if platform not in PLATFORMS:
        return RedirectResponse("/platform", status_code=303)
    s["platform"] = platform
    try:
        def _make():
            import controller
            controller.create_session_pod(s["id"], platform, s["host"], s["username"],
                                          s["password"], s["verify_ssl"])
        await asyncio.to_thread(_make)
        s["pod_started"] = True
    except Exception as e:
        name = type(e).__name__
        if name == "SessionLimitReached":
            return JSONResponse({"error": "Session limit reached — try again later."}, status_code=429)
        log.exception("session pod create failed")
        return JSONResponse({"error": f"could not start session: {e}"}, status_code=500)
    return RedirectResponse("/setup", status_code=303)


@APP.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    sid = _scan_sid(request)
    if not sid or not _get_session(sid):
        return RedirectResponse("/")
    s = SESSIONS[sid]
    platform = s.get("platform")
    if not platform:
        return RedirectResponse("/platform")
    instr = AUTH_INSTRUCTIONS.get(platform, "<p>No instructions for this platform.</p>")
    err = request.query_params.get("err", "")
    page = AUTH_PAGE.replace("__PLATFORM__", html.escape(PLATFORMS[platform]))
    page = page.replace("__INSTRUCTIONS__", instr)
    page = page.replace("__ERROR__", f'<p style="color:var(--danger);font-size:.875rem">{html.escape(err)}</p>' if err else "")
    return HTMLResponse(_with_version(page))


def _b64(text: str) -> str:
    import base64
    return base64.b64encode(text.encode()).decode()


def _provision_pod(s: dict) -> None:
    """Write per-platform config into the session pod (passwords stay pod-env only)."""
    import controller

    target_cfg = (
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
    writes: list[tuple[str, str]] = [("/data/config.yaml", target_cfg)]

    mcp_env = {"VMWARE_AIOPS_CONFIG": "/data/config.yaml"}
    platform = s["platform"]
    if platform == "claude":
        writes.append(("/data/mcp.json", json.dumps({
            "mcpServers": {"vsphere": {"command": "vmware-aiops", "args": ["mcp"], "env": mcp_env}}
        })))
    elif platform == "gemini":
        writes.append(("/data/home/.gemini/settings.json", json.dumps({
            "mcpServers": {"vsphere": {"command": "vmware-aiops", "args": ["mcp"], "env": mcp_env}}
        })))
    elif platform == "codex":
        # current Codex docs define $HOME/.codex/config.toml with [mcp_servers.<name>]
        writes.append(("/data/home/.codex/config.toml",
                       "[mcp_servers.vsphere]\ncommand = \"vmware-aiops\"\nargs = [\"mcp\"]\n"
                       "env = { VMWARE_AIOPS_CONFIG = \"/data/config.yaml\" }\n"))

    script = "mkdir -p /data/home/.gemini /data/home/.codex /data/home/.claude; "
    for path, content in writes:
        script += f"echo {_b64(content)} | base64 -d > {path}; "
    controller.exec_in_session(s["id"], ["sh", "-c", script])
    s["provisioned"] = True


@APP.get("/session/status")
async def session_status(request: Request):
    sid = _scan_sid(request)
    if not sid or not _get_session(sid):
        return JSONResponse({"error": "not signed in"}, status_code=401)
    s = SESSIONS[sid]
    s["last_active"] = time.time()
    platform = s.get("platform")
    if not platform:
        return {"ready": False, "detail": "platform not chosen"}
    def _phase():
        import controller
        return controller.session_status(s["id"])
    phase = await asyncio.to_thread(_phase)
    pod_ready = s.get("pod_started") and phase == "Running"
    if pod_ready and not s.get("provisioned"):
        try:
            await asyncio.to_thread(_provision_pod, s)
        except Exception:
            log.exception("provision failed")
            return {"ready": False, "phase": phase, "detail": "provisioning"}
    auth_ok = s["authed"] if platform != "codex" else s.get("codex_ready", False)
    ready = pod_ready and bool(auth_ok) and s.get("provisioned", False)
    detail = {"phase": phase, "authed": bool(auth_ok)}
    if s.get("codex_url"):
        detail["codex_url"] = s["codex_url"]
        detail["codex_code"] = s.get("codex_code")
    return {"ready": ready, **detail}


_VALIDATE_SCRIPT = """
import sys, urllib.request, urllib.error
key, platform = sys.argv[1], sys.argv[2]
try:
    if platform == "claude":
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/models?limit=1",
            headers={"authorization": "Bearer " + key, "anthropic-version": "2023-06-01"})
    else:
        req = urllib.request.Request(
            "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1",
            headers={"x-goog-api-key": key})
    urllib.request.urlopen(req, timeout=10)
    print("OK")
except urllib.error.HTTPError as e:
    print("HTTP", e.code)
except Exception as e:
    print("NET", str(e)[:120])
"""


def _validate_ai_credential(s: dict, api_key: str) -> str | None:
    """Check the credential from inside the session pod (exact egress path)."""
    import controller

    out = controller.exec_in_session(
        s["id"], ["python3", "-c", _VALIDATE_SCRIPT, api_key, s["platform"]])
    out = (out or "").strip()
    if out == "OK":
        return None
    if out.startswith("HTTP 401") or out.startswith("HTTP 403"):
        return "That credential was rejected by the provider (401/403). Check you pasted the whole token, not the surrounding output."
    if out.startswith("HTTP 429"):
        return "Provider rate-limited the check — try again in a minute."
    # Network failures: accept with a warning; a dead check shouldn't block setup.
    log.warning("credential validation inconclusive: %s", out[:120])


@APP.post("/session/auth/key")
async def auth_key(request: Request):
    sid = _scan_sid(request)
    if not sid or not _get_session(sid):
        return JSONResponse({"error": "not signed in"}, status_code=401)
    s = SESSIONS[sid]
    platform = s.get("platform")
    if platform not in ("claude", "gemini"):
        return JSONResponse({"error": "api key auth applies to Claude/Gemini"}, status_code=400)
    form = await request.form()
    api_key = re.sub(r"\s", "", form.get("api_key") or "")
    if not api_key:
        return RedirectResponse("/setup", status_code=303)
    try:
        err = await asyncio.to_thread(_validate_ai_credential, s, api_key)
    except Exception:
        log.exception("validation exec failed")
        err = None  # validation infra hiccup must not block setup
    if err:
        return RedirectResponse("/setup?err=" + __import__("urllib.parse", fromlist=["quote"]).quote(err), status_code=303)
    s["api_key"] = api_key
    s["authed"] = True
    s["last_active"] = time.time()
    return RedirectResponse("/chat", status_code=303)


@APP.post("/session/auth/device")
async def auth_device(request: Request):
    sid = _scan_sid(request)
    if not sid or not _get_session(sid):
        return JSONResponse({"error": "not signed in"}, status_code=401)
    s = SESSIONS[sid]
    if s.get("platform") != "codex":
        return JSONResponse({"error": "device auth applies to Codex"}, status_code=400)
    def _start():
        import controller
        out = controller.exec_in_session(
            s["id"],
            ["sh", "-c", "rm -f /data/device.log; (setsid codex login --device-auth > /data/device.log 2>&1 &) ; echo started"],
        )
        return out
    try:
        await asyncio.to_thread(_start)
    except Exception as e:
        return JSONResponse({"error": f"could not start device auth: {e}"}, status_code=500)
    s["codex_flow_started_at"] = time.time()
    s["codex_ready"] = False
    return RedirectResponse("/setup", status_code=303)


DEVICE_URL_RE = re.compile(r"(https://[a-zA-Z0-9./~_-]*openai[a-zA-Z0-9./~_?=&%-]*)")
DEVICE_CODE_RE = re.compile(r"(?:code|Code)[:\s]+([A-Z0-9-]{6,})")


@APP.get("/session/auth/device/poll")
async def auth_device_poll(request: Request):
    sid = _scan_sid(request)
    if not sid or not _get_session(sid):
        return JSONResponse({"error": "not signed in"}, status_code=401)
    s = SESSIONS[sid]
    if s.get("platform") != "codex":
        return JSONResponse({"error": "device auth applies to Codex"}, status_code=400)
    if time.time() - s.get("codex_flow_started_at", time.time()) > 600:
        return JSONResponse({"error": "device auth window expired"}, status_code=408)
    def _read():
        import controller
        log_read = controller.exec_in_session(s["id"], ["cat", "/data/device.log"])
        status = controller.exec_in_session(s["id"], ["codex", "login", "status"])
        return log_read, status
    try:
        log_text, status_text = await asyncio.to_thread(_read)
    except Exception as e:
        return JSONResponse({"error": str(e)[:200]}, status_code=502)
    if re.search(r"(not logged in|No auth)", status_text or "", re.I):
        ready = False
    else:
        ready = "logged in" in (status_text or "").lower() or "Authenticated" in (status_text or "")
    url = next(iter(DEVICE_URL_RE.findall(log_text or "")), None)
    code = next(iter(DEVICE_CODE_RE.findall(log_text or "")), None)
    if url:
        s["codex_url"] = url
    if code:
        s["codex_code"] = code
    if ready:
        s["codex_ready"] = True
        s["authed"] = True
    return {"ready": ready, "url": s.get("codex_url"), "code": s.get("codex_code")}


@APP.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    sid = _scan_sid(request)
    if not sid or not _get_session(sid):
        return RedirectResponse("/")
    s = SESSIONS[sid]
    st = await session_status(request)
    if not st.get("ready"):
        return RedirectResponse("/setup")
    return HTMLResponse(_with_version(
        CHAT_PAGE.replace("__HOST__", html.escape(s["host"]))
        .replace("__USER__", html.escape(s["username"]))))


async def _maybe_revive(s: dict, st: dict, request: Request) -> dict:
    """Recreate a dead session pod once, then re-check status.

    Key-based platforms (Claude/Gemini) recover silently: the credential lives
    in web RAM and is re-supplied on the next exec. Codex keeps its sign-in
    inside the pod, so a revived pod is unauthenticated and the user must redo
    setup. Conversation history (which lives in the pod) is not recoverable.
    Rate-limited to one attempt per 30s per session.
    """
    if not s.get("pod_started"):
        return st
    if st.get("phase") not in ("Failed", "Succeeded", "missing"):
        return st
    if time.time() - s.get("revive_ts", 0.0) < 30:
        return st
    s["revive_ts"] = time.time()
    platform = s.get("platform")
    if platform == "codex":
        s["authed"] = False
        s["codex_ready"] = False
        st = dict(st)
        st["detail"] = "pod exited; Codex sign-in was lost, redo setup"
        return st
    log.info("reviving dead session pod %s (phase=%s)", s["id"][:8], st.get("phase"))

    def _do():
        import controller
        controller.delete_session(s["id"])
        for _ in range(60):  # wait for the dead pod object to clear (same name)
            if controller.session_status(s["id"]) == "missing":
                break
            time.sleep(1)
        controller.create_session_pod(s["id"], platform, s["host"], s["username"],
                                      s["password"], s["verify_ssl"])
    try:
        await asyncio.to_thread(_do)
    except Exception:
        log.exception("session revive failed")
        return st
    s["provisioned"] = False
    s["claude_session"] = None
    return await session_status(request)


@APP.get("/logout")
async def logout(request: Request):
    sid = _scan_sid(request)
    s = SESSIONS.pop(sid, None) if sid else None
    if s:
        await _dispose(s)
    resp = RedirectResponse("/")
    resp.delete_cookie("aisession")
    return resp


@APP.post("/api/chat")
async def api_chat(request: Request):
    sid = _scan_sid(request)
    if not sid or not _get_session(sid):
        return JSONResponse({"error": "not signed in"}, status_code=401)
    s = SESSIONS[sid]
    st = await session_status(request)
    if not st.get("ready"):
        st = await _maybe_revive(s, st, request)
    if not st.get("ready"):
        why = st.get("detail") or st.get("phase") or st.get("error") or "starting"
        return JSONResponse({"error": f"session not ready ({why})"}, status_code=409)
    s["last_active"] = time.time()
    body = await request.json()
    message = (body.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": "empty"}, status_code=400)
    platform = s["platform"]

    if message.startswith("/model") and platform == "claude":
        # Claude's /model is a TUI command. In headless mode, per-message model change is a CLI flag.
        wanted = message.split(None, 1)[1:] or [""]
        s["model"] = wanted[0].strip() or None
        return {"text": f"Model for this session set to: {s['model'] or 'account default'}"}

    def _claude_cmd(prompt: str) -> str:
        resume_flag = f" --resume {s['claude_session']}" if s.get("claude_session") else ""
        model_flag = f" --model {s['model']}" if s.get("model") else ""
        return (
            f"printf '%s' {json.dumps(prompt)} | "
            f"ANTHROPIC_AUTH_TOKEN={json.dumps(s['api_key'])} "
            f"claude -p --output-format json{model_flag}{resume_flag}"
            f" --permission-mode bypassPermissions"
            f" --strict-mcp-config --mcp-config /data/mcp.json"
            f" --allowedTools 'mcp__vsphere'"
        )

    def _claude_result(raw: str) -> tuple[str, bool]:
        """Parse Claude's --output-format json envelope.

        Returns (reply_text, resume_broken). Tracks the conversation id so the
        next message continues this one (headless claude is stateless without
        --resume). resume_broken=True means the stored id no longer resolves.
        """
        data = None
        text = (raw or "").strip()
        try:
            data = json.loads(text)
        except ValueError:
            for line in reversed(text.splitlines()):
                line = line.strip()
                if line.startswith("{"):
                    try:
                        data = json.loads(line)
                        break
                    except ValueError:
                        continue
        if not isinstance(data, dict):
            return raw, False
        if data.get("session_id"):
            s["claude_session"] = data["session_id"]
        result = data.get("result") or ""
        if data.get("is_error") and "no conversation found" in result.lower():
            return "", True
        return (result or raw), False

    def _run(prompt: str) -> str:
        import controller
        # sh -c takes the command as its single next argument; anything after it
        # becomes $0/$1, so the prefix MUST be concatenated into one string.
        shell = "cd /data/home 2>/dev/null || cd /data; "
        if platform == "claude":
            out = controller.exec_in_session(s["id"], ["sh", "-c", shell + _claude_cmd(prompt)])
            text, broken = _claude_result(out)
            if broken:
                # Resumed a dead conversation (pod recreated? id expired): start
                # fresh rather than erroring; history restarts from this message.
                log.info("resume id %s gone; starting fresh conversation", str(s.get("claude_session"))[:8])
                s["claude_session"] = None
                out = controller.exec_in_session(s["id"], ["sh", "-c", shell + _claude_cmd(prompt)])
                text, _ = _claude_result(out)
            return text
        elif platform == "gemini":
            cmd = (
                f"printf '%s' {json.dumps(prompt)} | "
                f"GEMINI_API_KEY={json.dumps(s['api_key'])} gemini -p --output-format text"
            )
        else:
            cmd = f"codex exec --json --skip-git-repo-check < <(printf '%s' {json.dumps(prompt)})"
        return controller.exec_in_session(s["id"], ["sh", "-c", shell + cmd])

    try:
        out = await asyncio.to_thread(_run, message)
    except Exception as e:
        log.exception("chat exec failed")
        return JSONResponse({"error": f"{type(e).__name__}: {str(e)[:300]}"}, status_code=502)

    def _touch():
        try:
            import controller
            controller.touch_session(sid)
        except Exception:
            log.debug("activity touch failed", exc_info=True)
    asyncio.ensure_future(asyncio.to_thread(_touch))

    return {"text": out or "(no output)"}


if __name__ == "__main__":
    uvicorn.run(APP, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
