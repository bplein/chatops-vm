"""Fail-closed boundary for browser-owned Claude authentication.

No deployment token, developer login, or API-key fallback is used. Enterprise
subscription login requires an approved integration and a documented auth flow.
Until that exists, this application must not start a Claude runtime.
"""

import os
from collections.abc import Mapping

AUTH_UNAVAILABLE = (
    "Claude sign-in is not connected. This application needs an approved "
    "per-user authentication integration before chat can start. "
    "It will not use the server's or developer's credentials."
)

# These are settings for the developer's local launcher, not browser users.
# Build a fresh child environment rather than modifying os.environ, which is
# shared by every request and every user in this process.
_SAFE_ENV_KEYS = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ")


def isolated_environment(home: str, tmp: str, parent: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return a minimal, credential-free environment for a future runtime.

    This is configuration hygiene, NOT an isolation boundary. Separate pods or
    equivalent OS-level isolation are required before running user agents.
    Never use this mapping as an overlay onto the inherited environment.
    """
    source = os.environ if parent is None else parent
    env = {key: source[key] for key in _SAFE_ENV_KEYS if key in source}
    env.update({"HOME": home, "TMPDIR": tmp, "PYTHONDONTWRITEBYTECODE": "1"})
    return env


def claude_auth_status() -> dict[str, str | bool]:
    return {"ready": False, "reason": "integration_required", "message": AUTH_UNAVAILABLE}
