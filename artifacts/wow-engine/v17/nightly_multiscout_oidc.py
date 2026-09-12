"""Run Nightly Multi-Scout with refreshable GitHub Actions OIDC source auth.

If the legacy proxy bearer is configured it remains authoritative and unchanged.
Otherwise this wrapper mints a short-lived GitHub OIDC token before proxy calls;
the OIDC client caches only briefly, so long scans refresh automatically.
Transient proxy cold-start failures on the OIDC path are retried without
weakening typed auth/governance failures. Vendor 401/403 responses that arrive
after caller authentication are scoped to the affected source/event rather than
terminating the entire multi-sport scan. If the primary Odds API path remains
unavailable after governed retries, supported team/event requests can fail over
to a secondary live research feed. Secondary evidence never becomes model
probability, exact-line authority, or executable betting authority.
Scout itself remains discovery-only and can_execute=false.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

# The post-merge GitHub workflow executes this wrapper directly from the
# artifacts/wow-engine working directory (``python v17/nightly_multiscout_oidc.py``).
# In that mode Python puts only ``.../wow-engine/v17`` on sys.path, so the
# package-level ``from v17 ...`` import below would fail before any governed
# acquisition logic could run. Add only the package parent for direct-script
# compatibility; module/import execution is unchanged.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v17 import nightly_multiscout as scout
from v17.github_actions_oidc_client import GitHubOIDCMintError, mint_github_actions_oidc
from v17.scout_secondary_source import secondary_for_request

TRANSIENT_HTTP_STATUSES = {500, 502, 503, 504}
TRANSIENT_SOURCE_CODES = {
    "URLError",
    "TimeoutError",
    "ConnectionError",
    "ConnectionRefusedError",
    "ConnectionResetError",
    "RemoteDisconnected",
}
AUTH_FAILURE_CODES = {
    "WOW_SCOUT_SOURCE_AUTH_UNCONFIGURED",
    "ODDS_PROXY_AUTH_REQUIRED",
    "ODDS_PROXY_AUTH_INVALID",
}


def _retry_attempts() -> int:
    try:
        return max(1, int(os.environ.get("WOW_SCOUT_SOURCE_RETRY_ATTEMPTS", "3")))
    except ValueError:
        return 3


def _retry_base_seconds() -> float:
    try:
        return max(0.0, float(os.environ.get("WOW_SCOUT_SOURCE_RETRY_BASE_SECONDS", "2")))
    except ValueError:
        return 2.0


def _is_transient(result: scout.FetchResult) -> bool:
    if result.ok:
        return False
    if result.status in TRANSIENT_HTTP_STATUSES:
        return True
    return result.status is None and str(result.code or "") in TRANSIENT_SOURCE_CODES


def _primary_failure_label(result: scout.FetchResult) -> str:
    code = str(result.code or "UNKNOWN")
    return f"{code}:HTTP_{result.status}" if result.status is not None else code


def _eligible_for_secondary(result: scout.FetchResult) -> bool:
    if result.ok:
        return False
    code = str(result.code or "")
    if code in AUTH_FAILURE_CODES or code.startswith("GITHUB_ACTIONS_OIDC"):
        return False
    # A secondary independent source is appropriate for upstream entitlement,
    # quota, transient network, and upstream service failures. It is never used
    # to bypass caller authentication/governance failures.
    return result.status in {401, 403, 429, 500, 502, 503, 504} or _is_transient(result)


def configure_source_failure_scope() -> None:
    # The initial /sports request already fails closed immediately on caller auth
    # failure. On later event/market requests, an upstream 401/403 can represent
    # vendor endpoint entitlement (for example ODDS_API_FEATURED_ODDS_FALLBACK_ERROR),
    # not a loss of GitHub-OIDC authority. Keep 429 terminal only when no valid
    # secondary source can satisfy the exact research request.
    scout.TERMINAL_SOURCE_HTTP_STATUSES = {429}


def install_refreshable_oidc_proxy_auth() -> None:
    # Preserve the existing legacy bearer path exactly. The Sept. 12 cold-start
    # incident occurred on the GitHub-OIDC path, so resilience belongs there.
    if os.environ.get("WOW_ODDS_PROXY_ACTION_KEY"):
        return

    original = scout.proxy_get
    original_bookmaker_rows = scout.bookmaker_rows
    event_context: dict[str, dict[str, Any]] = {}

    def _remember_event_context(path: str, data: Any) -> None:
        if not path.endswith("/events") or not isinstance(data, list):
            return
        for event in data:
            if isinstance(event, dict) and event.get("id"):
                event_context[str(event["id"])] = {
                    "home_team": event.get("home_team"),
                    "away_team": event.get("away_team"),
                    "commence_time": event.get("commence_time"),
                }

    def _bookmaker_rows(payload: Any) -> list[dict[str, Any]]:
        rows = original_bookmaker_rows(payload)
        marker = payload.get("_wow_secondary_source") if isinstance(payload, dict) else None
        if not isinstance(marker, dict):
            return rows
        for row in rows:
            row.update({
                "source_provider": marker.get("provider"),
                "source_provider_detail": marker.get("provider_detail"),
                "source_tier": marker.get("source_tier"),
                "primary_source_failure": marker.get("primary_source_failure"),
                "prediction_authority": False,
                "exact_line_authority": False,
                "research_only": True,
                "can_execute": False,
            })
        return rows

    def _proxy_get(path: str, params: dict[str, Any] | None = None) -> scout.FetchResult:
        attempts = _retry_attempts()
        base_seconds = _retry_base_seconds()
        last_result: scout.FetchResult | None = None

        for attempt in range(1, attempts + 1):
            try:
                os.environ["WOW_GITHUB_OIDC_TOKEN"] = mint_github_actions_oidc()
            except GitHubOIDCMintError as exc:
                # Authentication/governance failures remain fail-closed and
                # are never relabeled as a transient source outage.
                return scout.FetchResult(False, code=str(exc))

            last_result = original(path, params)
            if last_result.ok:
                _remember_event_context(path, last_result.data)
                return last_result
            if not _is_transient(last_result) or attempt == attempts:
                break

            if base_seconds:
                time.sleep(base_seconds * attempt)

        final = last_result or scout.FetchResult(False, code="SCOUT_SOURCE_RETRY_EXHAUSTED")
        if not _eligible_for_secondary(final):
            return final

        secondary = secondary_for_request(
            path,
            params,
            event_context,
            primary_failure=_primary_failure_label(final),
        )
        if not secondary.ok:
            return final

        _remember_event_context(path, secondary.data)
        return scout.FetchResult(True, secondary.data, secondary.status or 200, code="SECONDARY_SOURCE_USED")

    scout.proxy_get = _proxy_get
    scout.bookmaker_rows = _bookmaker_rows


def main() -> int:
    configure_source_failure_scope()
    install_refreshable_oidc_proxy_auth()
    return scout.main()


if __name__ == "__main__":
    sys.exit(main())
