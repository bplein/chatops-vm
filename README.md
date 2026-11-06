# chatops-vm

A multi-user web chat-ops UI for vSphere. Pick an AI client, sign in with your vCenter credentials, and drive the environment from the browser.

## Clients supported

- **Claude Code** — paste the token from `claude setup-token` on any machine where Claude Code is already signed in with your enterprise account. No org API key needed; tokens live only in your session pod's environment.
- **Gemini CLI** — bring a Gemini API key from Google AI Studio (has a free tier).
- **Codex CLI** — sign in with your ChatGPT account via device code, in Codex's native headless flow.
- **OpenCode** is deliberately deferred.

## Architecture

Per connected user:

- one **ephemeral pod** in the cluster: sleeps idle, has your vCenter creds via a dedicated in-namespace Secret, gets its own writable `/data`, no Kubernetes API token, non-root, drop-ALL caps, seccomp RuntimeDefault;
- your model choice/credential applies **only to your session's CLI service account**;
- logout / 4 idle hours / TTL expiry kills the pod and deletes the Secret.

The web tier is replicas × sticky-route; everything session-stateful lives in the cluster, so a crashed pod doesn't take your login page away. A startup and periodic sweep in the web tier removes cluster objects left behind by a dead web pod.

The lab-deployed Claude sessions honor the vSphere-gated upstream skill: `vmware-aiops mcp` runs inside your session pod; the chat model can only call the MCP tools (no bash, no host filesystem).

## Deploy (lab)

```
oc apply -f deploy/k8s/chatops-vm-rbac.yaml
oc apply -f deploy/k8s/chatops-vm.yaml
```

No image pull secret needed (GHCR package is public). No shared token secret needed: users bring the model credential.

Route host: `chatops-vm-chatops-vm.apps.ocp1.plein.cna.lab` (edge TLS + redirect; cookie-based session affinity pins each browser to its web pod).

## Run locally

```
pip install -r requirements.txt
DATA_DIR=/tmp/chatops-vm python app/main.py    # binds http :8080
```

You need a kubeconfig pointing at the cluster where session pods should live (controller falls back to `~/.kube/config` outside a pod).

## Related repos

- Upstream skill/MCP: [vmware-skills/VMware-AIops](https://github.com/vmware-skills/VMware-AIops) (MIT, community project — not a VMware product)
- Sibling dashboard: [bplein/vmware-collector](https://github.com/bplein/vmware-collector)
