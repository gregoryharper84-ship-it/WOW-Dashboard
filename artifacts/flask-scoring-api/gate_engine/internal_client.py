"""
gate_engine/internal_client.py
================================
Auth-correct helpers for calling WOW Flask routes from internal Python code.

All WOW backend routes that require authentication use the single unified
contract:

  @require_api_key routes  →  X-API-Key: <SCORING_API_KEY>
      e.g. /wow/odds/events, /wow/odds/event-markets, /wow/odds/event-odds,
           /wow/odds/quota-status, /wow/kalshi/category-scan, /wow/mlb/pitcher,
           /wow/l10/v2

MIGRATION NOTE (2026-08-14):
  /wow/odds/* routes were previously protected by _verify_wow_action_key
  (X-WOW-Action-Key / GPT_ACTION_SECRET).  That auth surface has been retired.
  All four odds routes now require X-API-Key (SCORING_API_KEY).
  action_get() is preserved as an alias to scoring_get() for backward
  compatibility with existing callers; it will be removed in a future cleanup.

Using the wrong header results in a 401 that is classified as
AUTH_CONTRACT_FAIL (not a backend outage, not NO_PLAY).

Usage
-----
    from gate_engine.internal_client import scoring_get

    data, status, err = scoring_get("/wow/odds/events", {"sport": "baseball_mlb"})
    if err == AUTH_CONTRACT_FAIL:
        # wrong secret configured — not a backend failure
        ...

Design
------
- Never logs credential values.
- Returns (parsed_dict, http_status_code, error_class_or_None).
- error_class is one of the AUTH_CONTRACT_FAIL / FETCH_FAILED / PARSE_FAILED
  constants; never becomes NO_PLAY or model rejection.
- Base URL is derived from PORT env var (dev/production portability).
- Credentials are read from env at call time (not at import time) so tests can
  patch os.environ without module reload.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

# Error class constants — never map to NO_PLAY or model rejection
AUTH_CONTRACT_FAIL = "AUTH_CONTRACT_FAIL"
FETCH_FAILED       = "FETCH_FAILED"
PARSE_FAILED       = "PARSE_FAILED"
OK                 = "OK"


def _base_url() -> str:
    """Construct the local server base URL from PORT env var."""
    port = os.environ.get("PORT", "25643")
    return f"http://127.0.0.1:{port}"


def _do_get(
    path: str,
    params: Optional[Dict[str, Any]],
    headers: Dict[str, str],
    timeout: int = 30,
) -> Tuple[Optional[Dict], int, Optional[str]]:
    """
    Perform an HTTP GET and return (body_dict, status_code, error_class).

    Never raises; any exception is mapped to (None, 0, FETCH_FAILED).
    Never logs credential header values.
    """
    try:
        import requests as _req  # local import — avoids module-level side effects
    except ImportError:
        try:
            import urllib.request as _urllib
            import urllib.parse as _urlparse
            import json as _json
            url = _base_url() + path
            if params:
                url = url + "?" + _urlparse.urlencode(params)
            req = _urllib.Request(url, headers=headers)
            with _urllib.urlopen(req, timeout=timeout) as resp:
                status = resp.status
                body   = _json.loads(resp.read().decode())
            return body, status, None
        except Exception:
            return None, 0, FETCH_FAILED

    try:
        url = _base_url() + path
        resp = _req.get(url, params=params or {}, headers=headers, timeout=timeout)
        status = resp.status_code
        if status == 401:
            return None, status, AUTH_CONTRACT_FAIL
        try:
            body = resp.json()
        except Exception:
            return None, status, PARSE_FAILED
        return body, status, None
    except Exception:
        return None, 0, FETCH_FAILED


# ── Public API ────────────────────────────────────────────────────────────────

def scoring_get(
    path: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: int = 30,
) -> Tuple[Optional[Dict], int, Optional[str]]:
    """GET a route protected by @require_api_key (X-API-Key / SCORING_API_KEY).

    Reads SCORING_API_KEY from env at call time.  Never logs the key value.
    Returns (body_dict, status_code, error_class).

    Routes: /wow/odds/events, /wow/odds/event-markets, /wow/odds/event-odds,
            /wow/odds/quota-status, /wow/kalshi/category-scan,
            /wow/mlb/pitcher, /wow/l10/v2, etc.
    """
    api_key = os.environ.get("SCORING_API_KEY", "")
    if not api_key:
        return None, 0, AUTH_CONTRACT_FAIL  # misconfigured server
    headers = {
        "X-API-Key": api_key,
        "Accept": "application/json",
    }
    return _do_get(path, params, headers, timeout)


def action_get(
    path: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: int = 30,
) -> Tuple[Optional[Dict], int, Optional[str]]:
    """Backward-compatibility alias for scoring_get().

    DEPRECATED: action_get() previously sent X-WOW-Action-Key
    (GPT_ACTION_SECRET) to /wow/odds/* routes.  Those routes now require
    X-API-Key (SCORING_API_KEY) — the same contract as all other @require_api_key
    routes.  This alias delegates to scoring_get() so existing callers continue
    to work without modification.  Will be removed in a future cleanup pass.

    Auth guard: GPT_ACTION_SECRET is still checked for presence so that a
    misconfigured action key is reported as AUTH_CONTRACT_FAIL rather than
    masking as a generic FETCH_FAILED.

    Returns (body_dict, status_code, error_class).
    """
    gpt_secret = os.environ.get("GPT_ACTION_SECRET", "")
    if not gpt_secret:
        return None, 0, AUTH_CONTRACT_FAIL
    return scoring_get(path, params, timeout)
