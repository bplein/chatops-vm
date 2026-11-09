# ChatOps VM — project status

Updated: 2026-11-09 (~15:30 local)

## Where things stand

- Deployed on ocp1 namespace `chatops-vm`, Route
  `https://chatops-vm-chatops-vm.apps.ocp1.plein.cna.lab`, 2 web replicas,
  image pinned per release (never :latest).
- **0.3.8 is live and verified** (commit 2def7e5): both replicas on
  `ghcr.io/bplein/chatops-vm:0.3.8`, `govc 0.56.2` confirmed inside the
  running image, Route `haproxy.router.openshift.io/timeout: 15m`
  confirmed, index/healthz 200. Chat round-trip on 0.3.8 not yet retested
  by the user (needs their token in-browser).
  Release ladder since first deploy:
  - 0.3.3 — write session-pod configs (mcp.json etc.) at container boot
  - 0.3.4 — Claude conversation continuity (`--resume`), dead-pod revive,
    replica-safe idle sweep (the old sweeper let one web replica kill
    another's live sessions), session-pod resource requests
  - 0.3.5 — readable setup inputs, welcome line in chat log
  - 0.3.6 — fix 0.3.4 chat regression (`sh -c` argument bug), cookie
    Light/Dark theme (no System)
  - 0.3.7 — favicon (monochrome chevron+cursor, theme-aware SVG + PNGs)
  - 0.3.8 — **govc v0.56.2 in the agent image** + GOVC_* wired from the
    per-session Secret + Claude `Bash(govc:*)` + Route timeout 15m +
    vmware-aiops 2.4.2 → 2.4.6
- Architecture recap: per-user ephemeral session pod (Secret holds vCenter
  creds; pod has no k8s SA token); web tier drives everything via
  pods/exec; auth = user's own `claude setup-token` / Gemini API key /
  Codex device code; vCenter password never persisted, URL+username are
  non-secret prefill cookies.
- Verified end-to-end through 0.3.7 by user testing (login, Claude chat,
  vCenter queries). 0.3.8 pending: rollout check (govc in image, route
  annotation) + user retest of clone-with-pool.

## What the agent image contains (full inventory)

| Layer | Component | Source |
|---|---|---|
| vSphere | `vmware-aiops` 2.4.6 MCP server | upstream repo, PyPI-pinned |
| vSphere | pyVmomi | upstream's dependency; web tier also uses it for login verify |
| vSphere | **govc v0.56.2** (NEW 0.3.8) | govmomi releases; not upstream |
| Clients | claude-code, gemini-cli, codex | npm; user-chosen product decision |
| Platform | kubernetes python client | our architecture (session pods) |
| Providers | api.anthropic.com / generativelanguage (validation) + each CLI's own traffic under the user's token | per user-auth design |

## Open decisions (to address in next session)

1. **govc scope — the real drift question.** Upstream `vmware-aiops`
   `clone_vm` has no folder/resource-pool placement and no relocate tools;
   that limitation is what prompted adding govc. It trades the curated,
   mostly-read toolset for the full vSphere write surface, gated only by
   the logged-in user's own vCenter RBAC. Pick one:
   - **A. Keep govc as-is** — full API in the user's own ephemeral pod;
     hard limit is vCenter permissions.
   - **B. Prompt-level guardrail** — instruct clients to treat govc as
     read-only (`find/ls/vm.info` class) unless the user explicitly
     confirms a write.
   - **C. Drop govc, wait on upstream** — file/follow a VMware-AIops issue
     for `clone_vm -pool/-folder` params; check whether 2.4.6 already
     added tools 2.4.2 lacked (enumerate both MCP tool lists).
2. **Gemini/Codex conversation continuity.** Only Claude has `--resume`
   continuity; Gemini/Codex still one-shot per message. Their resume
   mechanisms need version-specific verification (`gemini --resume`,
   `codex exec resume`).
3. **Long-turn UX.** Route timeout is now 15m, but /api/chat is silent
   until the CLI answers. Consider streaming progress so users see the
   turn is alive.
4. **Deploy = logout.** Web RAM holds sessions; every rollout drops all
   sessions mid-chat. Acceptable for the lab? (Cluster-backed session
   state is a big change.)

## Housekeeping (pre-existing, unrelated to above)

- Delete stale images/packages: old `ghcr.io/bplein/vmware-aiops-web`,
  old Quay images, `ghcr.io/bplein/vmware-aiops`.
- vmware-collector: log string says "collected N pools" — terminology
  rule says "resource pools" (flagged, not yet changed).
