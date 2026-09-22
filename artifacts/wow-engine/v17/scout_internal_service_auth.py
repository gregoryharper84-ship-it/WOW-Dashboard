"""Render-to-Render read-only Scout authentication.

The production engine may use WOW_ODDS_PROXY_INTERNAL_KEY to reach the read-only
odds acquisition chain without reusing the Custom GPT Action bearer. Server
runtime must enter that chain through the multi-provider odds router by default;
otherwise a primary-provider 429 bypasses the router's configured failover and
cross-sport discovery can collapse even though fallback acquisition is healthy.
Existing WOW_ODDS_PROXY_ACTION_KEY and GitHub OIDC behavior remains untouched.
This is an acquisition-only path and can never authorize wager execution.
"""
from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

CAN_EXECUTE = False
DEFAULT_ODDS_ROUTER_URL = "https://wow-odds-router.onrender.com"


def configure_server_acquisition_router(scout: Any) -> str:
    """Point API-server Scout calls at the read-only acquisition router.

    The GitHub Actions wrapper already applies this routing when its ``main()``
    entrypoint runs. The long-lived API server does not run that entrypoint, so
    its internal-service auth path must apply the same source routing explicitly.
    An emergency kill switch preserves the legacy source unchanged.
    """
    kill_switch = (
        os.environ.get("WOW_SCOUT_ODDS_ROUTER_KILL_SWITCH", "false")
        .strip()
        .lower()
        == "true"
    )
    if kill_switch:
        return str(getattr(scout, "PROXY_URL", ""))

    router_url = os.environ.get("WOW_ODDS_ROUTER_URL", DEFAULT_ODDS_ROUTER_URL).strip()
    if router_url:
        scout.PROXY_URL = router_url.rstrip("/")
    return str(getattr(scout, "PROXY_URL", ""))


def install_scout_internal_service_auth() -> bool:
    from v17 import nightly_multiscout as scout

    # Apply source routing even when the auth wrapper was installed earlier.
    # This keeps idempotent startup/reload paths on the router rather than the
    # direct primary proxy, without changing credentials or execution authority.
    configure_server_acquisition_router(scout)

    if getattr(scout, "_v17_internal_service_auth_installed", False):
        return True

    original = scout.proxy_get

    def proxy_get(path: str, params: dict[str, Any] | None = None) -> scout.FetchResult:
        # Preserve existing explicit/ephemeral credentials as authoritative.
        if os.environ.get("WOW_ODDS_PROXY_ACTION_KEY") or os.environ.get("WOW_GITHUB_OIDC_TOKEN"):
            return original(path, params)

        token = os.environ.get("WOW_ODDS_PROXY_INTERNAL_KEY")
        if not token:
            return original(path, params)

        query = urlencode({k: v for k, v in (params or {}).items() if v is not None})
        url = f"{scout.PROXY_URL}{path}" + (f"?{query}" if query else "")
        req = Request(
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        try:
            with urlopen(req, timeout=25) as response:
                return scout.FetchResult(
                    True,
                    json.loads(response.read().decode("utf-8")),
                    response.status,
                )
        except HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
                detail = payload.get("detail") if isinstance(payload, dict) else None
                detail = detail if isinstance(detail, dict) else {}
                code = payload.get("code") or detail.get("code")
            except Exception:
                code = None
            return scout.FetchResult(False, status=exc.code, code=code or f"HTTP_{exc.code}")
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            return scout.FetchResult(False, code=type(exc).__name__)

    scout.proxy_get = proxy_get
    scout._v17_internal_service_auth_original = original
    scout._v17_internal_service_auth_installed = True
    return True


__all__ = [
    "CAN_EXECUTE",
    "DEFAULT_ODDS_ROUTER_URL",
    "configure_server_acquisition_router",
    "install_scout_internal_service_auth",
]
