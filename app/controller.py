"""Kubernetes session-pod controller for chatops-vm.

The web pod is the only component that talks to the Kubernetes API. For each
logged-in user session it creates one ephemeral agent pod, plus a per-session
Secret holding that user's vCenter credentials, in its own namespace; both are
deleted on logout/expiry. Agent pods run with no API token
(automountServiceAccountToken: false) — all API access stays here.

The `kubernetes` package is imported lazily inside functions and the client is
created on first use, so importing this module works with no cluster and no
installed client (unit tests, doc builds).

Safe for multiple web replicas: every function here touches only the
Kubernetes API (pods/secrets in our namespace, counted by label selector) and
never process-local state, so a cap check or session_count() from one web pod
sees what the others created. Any in-memory session bookkeeping (cookie ->
session) is the web layer's problem, not this module's.
"""

import logging
import os
import re
import threading
import time

log = logging.getLogger(__name__)

DEFAULT_NAMESPACE = "chatops-vm"
# Pinned tag — keep in sync with VERSION; never :latest.
DEFAULT_AGENT_IMAGE = "ghcr.io/bplein/chatops-vm:0.3.7"
DEFAULT_MAX_SESSION_PODS = 10

# Annotation stamped by the web layer on every chat message. The periodic
# stale sweep is annotation-based (not web-RAM-based) so several web replicas
# can run the sweeper without one replica deleting another's live sessions.
LAST_CHAT_ANNOTATION = "aiops-web/last-chat"

# Phases that occupy a slot against MAX_SESSION_PODS. Succeeded/Failed pods
# are inert; callers should delete_session() them, but they don't count.
_ACTIVE_PHASES = frozenset({"Pending", "Running"})


class SessionLimitReached(Exception):
    """MAX_SESSION_PODS active session pods already exist; nothing was created."""

# Label tying every per-session object back to its web session id.
SESSION_LABEL_KEY = "aiops-web/session"

# DNS-1123 label guard for the id interpolated into object names
# ("aiops-session-<id>", prefix is 14 chars, so cap the id at 48 => 62 total).
# Real ids are uuid4().hex (see main.py login); anything else is rejected
# before it can reach an API object name or label value.
_SID_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
_SID_MAX_LEN = 48

_core_v1 = None
_client_lock = threading.Lock()


def _namespace() -> str:
    """Namespace to operate in; override with POD_NAMESPACE (downward API or env)."""
    return os.environ.get("POD_NAMESPACE", DEFAULT_NAMESPACE)


def get_core_v1():
    """Lazily build the process-wide CoreV1Api (in-cluster, kubeconfig fallback).

    Thread-safe via double-checked lock: FastAPI handlers may call this from
    several worker threads while creating sessions.
    """
    global _core_v1
    if _core_v1 is None:
        with _client_lock:
            if _core_v1 is None:
                from kubernetes import client, config

                try:
                    config.load_incluster_config()
                except Exception:
                    # Not running in a pod (local dev): use ~/.kube/config.
                    # If this also fails, its ConfigException propagates — the
                    # caller surfaces it as a login/creation error.
                    config.load_kube_config()
                _core_v1 = client.CoreV1Api()
                log.info("kubernetes client ready (namespace=%s)", _namespace())
    return _core_v1


def _resource_name(session_id: str) -> str:
    """Shared pod/secret name for a session; raises on anything unexpected."""
    if not session_id or len(session_id) > _SID_MAX_LEN or not _SID_RE.match(session_id):
        raise ValueError(f"unsafe session id: {session_id!r}")
    return f"aiops-session-{session_id}"


def _max_session_pods() -> int:
    """Cap on active session pods; a non-integer MAX_SESSION_PODS raises loudly."""
    return int(os.environ.get("MAX_SESSION_PODS", str(DEFAULT_MAX_SESSION_PODS)))


def session_count() -> int:
    """Pods in our namespace labelled aiops-web/session that are Pending/Running.

    Counts via a label-selector existence query, so it reflects the whole
    namespace regardless of which web replica created the pod. Used by
    create_session_pod() for the cap and by the web layer to pick 429 vs 503.
    """
    core = get_core_v1()
    pods = core.list_namespaced_pod(namespace=_namespace(), label_selector=SESSION_LABEL_KEY)
    return sum(1 for p in pods.items if p.status and p.status.phase in _ACTIVE_PHASES)


def list_stale_sessions(live_ids: set[str]) -> list[str]:
    """Session pods/secrets whose id is NOT in live_ids (web pod RAM).

    Called by the web layer at startup and on a timer: after a web restart the
    cluster objects survive but the in-memory session map is gone, so the pods
    and their credential-bearing secrets would leak forever without this sweep.
    """
    core = get_core_v1()
    pods = core.list_namespaced_pod(namespace=_namespace(), label_selector=SESSION_LABEL_KEY)
    secrets = core.list_namespaced_secret(namespace=_namespace(), label_selector=SESSION_LABEL_KEY)
    names = {p.metadata.name for p in pods.items} | {s.metadata.name for s in secrets.items}
    out: list[str] = []
    for name in names:
        sid = name.split("-", 2)[-1]  # strip "aiops-session-" prefix
        if sid and sid not in live_ids:
            out.append(sid)
    return out


def sweep_stale_sessions(live_ids: set[str]) -> list[str]:
    """Delete orphaned session pods+secrets; returns the ids removed.

    RAM-based: ONLY safe at web startup (this replica's RAM is legitimately
    empty). Never run periodically with >1 replica — replica B would treat
    replica A's live sessions as stale and delete them.
    """
    removed: list[str] = []
    for sid in list_stale_sessions(live_ids):
        try:
            delete_session(sid)
            removed.append(sid)
        except Exception:
            log.warning("stale sweep failed for %s", sid, exc_info=True)
    return removed


def touch_session(session_id: str) -> None:
    """Stamp the session pod with 'last activity now' (cross-replica sweep input)."""
    from kubernetes.client.rest import ApiException

    core = get_core_v1()
    try:
        core.patch_namespaced_pod(
            name=_resource_name(session_id),
            namespace=_namespace(),
            body={"metadata": {"annotations": {LAST_CHAT_ANNOTATION: str(time.time())}}},
        )
    except ApiException as e:
        if e.status != 404:
            raise


def sweep_idle_sessions(ttl_s: float) -> list[str]:
    """Delete session pods+secrets idle past ttl_s, judged by the pod's
    last-chat annotation (falling back to pod creation time).

    Safe on every replica: a live session on ANY replica gets its annotation
    refreshed on each chat message, so it is never seen as idle here. Only
    genuinely abandoned pods (owner crashed/was replaced, user walked away)
    age out. Returns the ids removed.
    """
    core = get_core_v1()
    ns = _namespace()
    pods = core.list_namespaced_pod(namespace=ns, label_selector=SESSION_LABEL_KEY)
    now = time.time()
    removed: list[str] = []
    for p in pods.items:
        meta = p.metadata
        ann = (meta.annotations or {}).get(LAST_CHAT_ANNOTATION)
        try:
            last = float(ann) if ann else meta.creation_timestamp.timestamp()
        except (TypeError, ValueError):
            last = 0.0
        if now - last <= ttl_s:
            continue
        sid = (meta.labels or {}).get(SESSION_LABEL_KEY) or meta.name.split("-", 2)[-1]
        try:
            delete_session(sid)
            removed.append(sid)
            log.info("swept idle session %s (idle %.0fm)", sid[:8], (now - last) / 60)
        except Exception:
            log.warning("idle sweep failed for %s", sid, exc_info=True)
    return removed



def _delete_quietly(fn, **kwargs) -> None:
    """Call a delete API, treating 'already gone' (404) as success."""
    from kubernetes.client.rest import ApiException

    try:
        fn(**kwargs)
    except ApiException as e:
        if e.status != 404:
            raise


def _platform_boot(platform: str, host: str, username: str, verify_ssl: bool) -> str:
    """Shell command the session pod runs: write platform configs, then idle.

    Password is NOT in here — it lives in the pod env via the per-session Secret
    (VMWARE_WEB_PASSWORD) and is only consumed by the vmware-aiops MCP server.
    """
    import base64
    import json as _json

    target = (
        "targets:\n"
        "  - name: web\n"
        f"    host: {host}\n"
        "    type: vcenter\n"
        f"    username: {username}\n"
        "    port: 443\n"
        f"    verify_ssl: {str(verify_ssl).lower()}\n"
        "scanner: {enabled: false}\n"
        "notify: {webhook_url: ''}\n"
    )
    mcp_env = {"VMWARE_AIOPS_CONFIG": "/data/config.yaml"}
    files = {"/data/config.yaml": target}
    if platform == "claude":
        files["/data/mcp.json"] = _json.dumps({
            "mcpServers": {"vsphere": {"command": "vmware-aiops", "args": ["mcp"], "env": mcp_env}}})
    elif platform == "gemini":
        files["/data/home/.gemini/settings.json"] = _json.dumps({
            "mcpServers": {"vsphere": {"command": "vmware-aiops", "args": ["mcp"], "env": mcp_env}}})
    elif platform == "codex":
        files["/data/home/.codex/config.toml"] = (
            "[mcp_servers.vsphere]\n"
            'command = "vmware-aiops"\nargs = ["mcp"]\n'
            'env = { VMWARE_AIOPS_CONFIG = "/data/config.yaml" }\n')

    script = "mkdir -p /data/home/.gemini /data/home/.codex /data/home/.claude; "
    for path, content in files.items():
        script += f"echo {base64.b64encode(content.encode()).decode()} | base64 -d > {path}; "
    script += "trap : TERM INT; sleep infinity & wait"
    return script


def create_session_pod(
    session_id: str,
    platform: str,
    host: str,
    username: str,
    password: str,
    verify_ssl: bool,
) -> str:
    """Create the per-session Secret and agent pod. Returns the pod name.

    Order: cap check -> Secret -> pod. Raises SessionLimitReached (nothing
    created, no Secret written) when MAX_SESSION_PODS active pods already
    exist, or the underlying ApiException on API failure (409 = session
    already has a pod). No ownerReferences: the web pod owns cleanup via
    delete_session(), and nothing should garbage-collect a session pod behind
    a live browser session. If pod creation fails after the Secret was
    written, the Secret is deleted best-effort before re-raising so
    credentials never linger without a session.

    The cap is checked against the API, not this process, so several web
    replicas enforce one shared limit; two concurrent logins at the exact
    boundary could still race one pod over the cap, which is acceptable here
    (soft limit, not an admission control).
    """
    from kubernetes import client

    core = get_core_v1()
    ns = _namespace()
    name = _resource_name(session_id)
    labels = {SESSION_LABEL_KEY: session_id}

    limit = _max_session_pods()
    active = session_count()
    if active >= limit:
        raise SessionLimitReached(f"{active} active session pods, cap is {limit}")

    secret = client.V1Secret(
        metadata=client.V1ObjectMeta(name=name, namespace=ns, labels=labels),
        type="Opaque",
        string_data={
            "VCENTER_HOST": host,
            "VCENTER_USERNAME": username,
            "VMWARE_WEB_PASSWORD": password,
            "VERIFY_SSL": "true" if verify_ssl else "false",
        },
    )
    core.create_namespaced_secret(namespace=ns, body=secret)

    container = client.V1Container(
        name="agent",
        image=os.environ.get("AGENT_IMAGE", DEFAULT_AGENT_IMAGE),
        image_pull_policy="Always",
        # Non-BestEffort QoS: BestEffort pods are evicted first under node
        # memory pressure, which is the suspected cause of mid-chat pod deaths.
        # Generous headroom; the CLI + MCP server stay well under the limit.
        resources=client.V1ResourceRequirements(
            requests={"cpu": "200m", "memory": "512Mi"},
            limits={"memory": "2Gi"},
        ),
        # Write the platform config files at boot (web-layer provisioning can race a
        # fast login), then stay alive; the web pod drives everything via exec.
        command=["sh", "-c", _platform_boot(platform, host, username, verify_ssl)],
        env=[client.V1EnvVar(name="PLATFORM", value=platform)],
        env_from=[client.V1EnvFromSource(secret_ref=client.V1SecretEnvSource(name=name))],
        volume_mounts=[client.V1VolumeMount(name="data", mount_path="/data")],
        security_context=client.V1SecurityContext(
            run_as_non_root=True,
            allow_privilege_escalation=False,
            capabilities=client.V1Capabilities(drop=["ALL"]),
            seccomp_profile=client.V1SeccompProfile(type="RuntimeDefault"),
        ),
    )
    pod = client.V1Pod(
        metadata=client.V1ObjectMeta(
            name=name, namespace=ns, labels=labels,
            annotations={LAST_CHAT_ANNOTATION: str(time.time())},
        ),
        spec=client.V1PodSpec(
            restart_policy="Never",
            automount_service_account_token=False,
            containers=[container],
            volumes=[client.V1Volume(name="data", empty_dir=client.V1EmptyDirVolumeSource())],
        ),
    )
    try:
        created = core.create_namespaced_pod(namespace=ns, body=pod)
    except Exception:
        _delete_quietly(core.delete_namespaced_secret, name=name, namespace=ns)
        raise
    log.info("created session pod %s (platform=%s)", name, platform)
    return created.metadata.name or name


def delete_session(session_id: str) -> None:
    """Delete the session's pod and Secret. Idempotent: 404s are swallowed."""
    core = get_core_v1()
    ns = _namespace()
    name = _resource_name(session_id)
    _delete_quietly(core.delete_namespaced_pod, name=name, namespace=ns)
    _delete_quietly(core.delete_namespaced_secret, name=name, namespace=ns)


def session_status(session_id: str) -> str:
    """Pod phase ('Pending'|'Running'|'Succeeded'|'Failed'|'Unknown') or 'missing'."""
    from kubernetes.client.rest import ApiException

    core = get_core_v1()
    try:
        pod = core.read_namespaced_pod(name=_resource_name(session_id), namespace=_namespace())
    except ApiException as e:
        if e.status == 404:
            return "missing"
        raise
    return pod.status.phase or "Unknown" if pod.status else "Unknown"


def exec_in_session(
    session_id: str,
    command: list[str],
    stdin_on: bool = False,
    timeout_s: float | None = None,
) -> str:
    """Run `command` synchronously in the session pod; return stdout+stderr text.

    Output is concatenated per API frame, so stdout/stderr interleaving is
    best-effort, not byte-exact. Blocks until the command exits; pass
    timeout_s (wall-clock) from the caller if the request path needs a bound
    (TimeoutError is raised and the stream is closed; the in-pod command may
    keep running until the pod dies).
    """
    from kubernetes import stream as k8s_stream

    core = get_core_v1()
    name = _resource_name(session_id)
    ns = _namespace()
    ws = k8s_stream.stream(
        core.connect_get_namespaced_pod_exec,
        name=name,
        namespace=ns,
        command=list(command),
        stderr=True,
        stdin=stdin_on,
        stdout=True,
        tty=False,
        _preload_content=False,
    )
    chunks: list[str] = []
    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    try:
        while ws.is_open():
            ws.update(timeout=1)
            if ws.peek_stdout():
                chunks.append(ws.read_stdout())
            if ws.peek_stderr():
                chunks.append(ws.read_stderr())
            if deadline is not None and time.monotonic() > deadline:
                raise TimeoutError(f"exec in {name} timed out after {timeout_s}s")
        # Server closed the stream; drain anything still buffered (best effort:
        # peek/read after close is implementation-dependent).
        try:
            if ws.peek_stdout():
                chunks.append(ws.read_stdout())
            if ws.peek_stderr():
                chunks.append(ws.read_stderr())
        except Exception:
            pass
    finally:
        try:
            ws.close()
        except Exception:
            pass
    return "".join(chunks)
