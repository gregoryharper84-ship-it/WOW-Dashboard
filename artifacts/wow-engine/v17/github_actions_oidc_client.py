"""GitHub Actions OIDC client for the V17 Multi-Scout workflow.

The workflow receives GitHub's request URL/token only when `id-token: write` is
explicitly granted.  This helper exchanges that ephemeral runner credential for
a short-lived JWT with the fixed WOW Multi-Scout audience. No WOW application
secret is stored in GitHub.
"""
from __future__ import annotations

import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from github_actions_oidc import AUDIENCE

_CACHE_TOKEN: str | None = None
_CACHE_AT: float = 0.0
CACHE_SECONDS = 120.0


class GitHubOIDCMintError(RuntimeError):
    """Typed failure for runner-side OIDC minting."""


def mint_github_actions_oidc(*, force: bool = False) -> str:
    global _CACHE_TOKEN, _CACHE_AT
    now = time.monotonic()
    if not force and _CACHE_TOKEN and now - _CACHE_AT < CACHE_SECONDS:
        return _CACHE_TOKEN

    request_url = str(os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL") or "").strip()
    request_token = str(os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN") or "").strip()
    if not request_url or not request_token:
        raise GitHubOIDCMintError("GITHUB_OIDC_REQUEST_CONTEXT_UNAVAILABLE")

    separator = "&" if "?" in request_url else "?"
    url = f"{request_url}{separator}audience={AUDIENCE}"
    request = Request(
        url,
        headers={"Authorization": f"Bearer {request_token}", "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise GitHubOIDCMintError(f"GITHUB_OIDC_MINT_HTTP_{exc.code}") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise GitHubOIDCMintError("GITHUB_OIDC_MINT_TRANSPORT_FAILED") from exc

    token = payload.get("value") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not token.strip():
        raise GitHubOIDCMintError("GITHUB_OIDC_MINT_RESPONSE_INVALID")
    _CACHE_TOKEN = token.strip()
    _CACHE_AT = now
    return _CACHE_TOKEN


def reset_oidc_cache() -> None:
    global _CACHE_TOKEN, _CACHE_AT
    _CACHE_TOKEN = None
    _CACHE_AT = 0.0


__all__ = ["GitHubOIDCMintError", "mint_github_actions_oidc", "reset_oidc_cache"]
