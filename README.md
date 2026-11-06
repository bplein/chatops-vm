# vmware-aiops-web

A web chat UI for the upstream [VMware-AIops](https://github.com/vmware-skills/VMware-AIops) Claude skill — chat-ops administers vCenter from a browser.

## Vision

Each visitor gets their own isolated Claude session served from inside the container, with the vmware-aiops skill (and/or MCP) loaded. No accounts, no stored credentials:

1. The user authenticates per session with vCenter **URL + username + password**.
2. URL and username are kept as cookies; the password is asked for **every session** and never persisted.
3. From there the user chats, and Claude administers their vCenter via the loaded skill.

**Hard requirement:** all API use and authentication must be bound to the HTTPS session, so multiple simultaneous users can share the same frontend with independent sessions. Nothing crosses sessions.

## Status

Empty scaffold. Stack and structure are undecided — options and tradeoffs will be recorded here before code lands.

## Related repos

- Upstream skill/package: [vmware-skills/VMware-AIops](https://github.com/vmware-skills/VMware-AIops)
- Resource-pool dashboard (same lab): [bplein/vmware-collector](https://github.com/bplein/vmware-collector)
