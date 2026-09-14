"""Run Nightly Multi-Scout with refreshable GitHub Actions OIDC source auth.

If the legacy proxy bearer is configured it remains authoritative and unchanged.
Otherwise this wrapper mints a short-lived GitHub OIDC token before source calls;
the OIDC client caches only briefly, so long scans refresh automatically.
Transient source cold-start failures on the OIDC path are retried without
weakening typed auth/governance failures. The production source path uses the
read-only WOW odds acquisition router, which can preserve primary Odds API
coverage and fail over evidence acquisition without creating probability or
execution authority. Secondary evidence never becomes model probability,
exact-line authority, or executable betting authority.
Scout itself remains discovery-only and can_execute=false.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v17 import nightly_multiscout as scout
from v17 import market_evidence_sources as market_sources
from v17.github_actions_oidc_client import GitHubOIDCMintError, mint_github_actions_oidc
from v17.market_evidence_scout_bridge import market_evidence_for_request
from v17.scout_secondary_source import secondary_for_request

DEFAULT_ODDS_ROUTER_URL = "https://wow-odds-router.onrender.com"
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
    "ODDS_ROUTER_AUTH_REQUIRED",
    "ODDS_ROUTER_AUTH_INVALID",
}
SECONDARY_VENDOR_FAILURE_CODES = {
    "ODDS_API_FEATURED_ODDS_FALLBACK_ERROR",
    "ODDS_API_FEATURED_ODDS_FALLBACK_INVALID",
    "ODDS_API_KEY_UNCONFIGURED",
    "ODDS_API_UPSTREAM_ERROR",
    "ODDS_API_UPSTREAM_UNREACHABLE",
    "ODDS_API_UPSTREAM_NON_JSON",
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
    if code in SECONDARY_VENDOR_FAILURE_CODES:
        return True
    if result.status in TRANSIENT_HTTP_STATUSES:
        return True
    return _is_transient(result)


def configure_source_failure_scope() -> None:
    # Typed vendor failures are row/source blockers. Caller auth remains fail-closed
    # before this point; 429 remains the only slate-terminal source status here.
    scout.TERMINAL_SOURCE_HTTP_STATUSES = {429}


def configure_acquisition_router() -> None:
    """Route production Scout through the read-only multi-provider router.

    The existing workflow variable keeps its historical name, but this wrapper
    deliberately overrides the imported primary-proxy URL unless an emergency
    kill switch is set. The router accepts the same short-lived GitHub OIDC
    identity and remains evidence-only/can_execute=false.
    """
    kill_switch = os.environ.get("WOW_SCOUT_ODDS_ROUTER_KILL_SWITCH", "false").strip().lower() == "true"
    if kill_switch:
        return
    router_url = os.environ.get("WOW_ODDS_ROUTER_URL", DEFAULT_ODDS_ROUTER_URL).strip()
    if router_url:
        scout.PROXY_URL = router_url.rstrip("/")


def enable_research_market_evidence() -> None:
    """Keep the production Scout evidence lane on by default.

    This affects acquisition only. The market-evidence module remains
    research-only, prediction_authority=False, exact_line_authority=False and
    can_execute=False. An explicit emergency kill switch can still disable the
    provider tier without changing model/governance semantics.
    """
    kill_switch = os.environ.get("WOW_MARKET_EVIDENCE_KILL_SWITCH", "false").strip().lower() == "true"
    market_sources.ENABLED = not kill_switch
    os.environ["WOW_MARKET_EVIDENCE_ENABLED"] = "false" if kill_switch else "true"


def install_refreshable_oidc_proxy_auth() -> None:
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

        primary_failure = _primary_failure_label(final)
        secondary = secondary_for_request(path, params, event_context, primary_failure=primary_failure)
        if secondary.ok:
            _remember_event_context(path, secondary.data)
            return scout.FetchResult(True, secondary.data, secondary.status or 200, code="SECONDARY_SOURCE_USED")

        # Tertiary tier: subscription market-evidence feeds. Same research
        # ceiling as every other tier — evidence only, never probability.
        tertiary = market_evidence_for_request(path, params, event_context, primary_failure=primary_failure)
        if tertiary.ok:
            _remember_event_context(path, tertiary.data)
            return scout.FetchResult(True, tertiary.data, tertiary.status or 200, code="MARKET_EVIDENCE_SOURCE_USED")

        diagnostic = {
            "secondary_attempted": True,
            "secondary_provider": "ESPN_SCOREBOARD_RESEARCH_FALLBACK",
            "secondary_status": "FAILED",
            "secondary_reason_code": secondary.code,
            "secondary_http_status": secondary.status,
            "tertiary_attempted": True,
            "tertiary_provider": tertiary.provider,
            "tertiary_status": "FAILED",
            "tertiary_reason_code": tertiary.code,
            "tertiary_http_status": tertiary.status,
            "primary_reason_code": final.code,
            "primary_http_status": final.status,
        }
        return scout.FetchResult(False, data=diagnostic, status=final.status, code=final.code)

    scout.proxy_get = _proxy_get
    scout.bookmaker_rows = _bookmaker_rows


def main() -> int:
    configure_source_failure_scope()
    configure_acquisition_router()
    enable_research_market_evidence()
    install_refreshable_oidc_proxy_auth()
    return scout.main()


if __name__ == "__main__":
    sys.exit(main())
