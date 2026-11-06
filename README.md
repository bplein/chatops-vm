# vmware-aiops-web

A web chat UI for the upstream [VMware-AIops](https://github.com/vmware-skills/VMware-AIops) MCP server — chat-ops administer vSphere from a browser. Built on the [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk).

## How it works

Each browser session signs in with vCenter credentials:

1. The app verifies the credentials against the vCenter once (`SmartConnect`), then stores the password in **server memory only**, keyed by an unguessable session id.
2. The session lazily spawns a persistent `ClaudeSDKClient` with one MCP stdio server (`vmware-aiops mcp`). That server's environment carries the per-session vCenter password; the Claude chat model calls only these MCP tools (no shell, no filesystem).
3. Passwords are never written to disk, never logged, never shared between sessions. Sign-out, session expiry (4h idle), or a container restart drops them.

**Hard rule:** all model API use happens under HTTPS session scope; concurrent users get fully separate Claude sessions.

## Run locally

```
pip install -r requirements.txt
DATA_DIR=/tmp/aiops-web python app/main.py    # http on :8080 (the session cookie is Secure; use behind TLS — see the k8s Route)
```

## Deploy to OpenShift (lab)

```
oc apply -f deploy/k8s/vmware-aiops-web.yaml
oc -n vmware-aiops-web create secret generic claude-gateway \
  --from-literal=ANTHROPIC_AUTH_TOKEN='<gpulab gateway token>'
```

Route host: `vmware-aiops-web-vmware-aiops-web.apps.ocp1.plein.cna.lab` (edge TLS, redirect).

The `web-config` ConfigMap pins the model backend to the lab gateway (`gpulab.purestorage.com`) and the default model `Kimi-K3-lab-claude-compatible[1m]`; override either via the ConfigMap.

## Container

Built by GitHub Actions to `ghcr.io/bplein/vmware-aiops-web` (public; pulls need no auth). `python:3.12-slim` + node + `@anthropic-ai/claude-code` CLI + `vmware-aiops==2.4.2` MCP server. Tags: `latest`, `sha-<sha>`; a `v*` tag adds the version tag.

## Related repos

- Upstream: [vmware-skills/VMware-AIops](https://github.com/vmware-skills/VMware-AIops) (MCP server + skill)
- Sibling (resource-pool dashboard): [bplein/vmware-collector](https://github.com/bplein/vmware-collector)
