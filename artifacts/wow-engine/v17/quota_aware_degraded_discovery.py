"""Quota-aware degraded discovery for WOW V17 cross-sport winner scans.

Acquisition/orchestration only. This module never creates sporting probability,
calibration, market consensus, rank eligibility, or execution authority.

It extends the existing schedule-first resilience layer with per-scan paid-call
accounting, definitive provider circuit breakers, explicit coverage truth, and a
strict provider-alias boundary for public scoreboard identities.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping

CAN_EXECUTE = False
CONTRACT_VERSION = "V17_QUOTA_AWARE_DEGRADED_DISCOVERY_V1"

AVAILABLE = "AVAILABLE"
RATE_LIMITED = "RATE_LIMITED"
QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
AUTH_FAILURE = "AUTH_FAILURE"
TEMPORARILY_UNAVAILABLE = "TEMPORARILY_UNAVAILABLE"
DISABLED_BY_POLICY = "DISABLED_BY_POLICY"

# A generic 429 is deliberately not a run-long circuit: provider-specific retry
# policy owns burst throttling. Only definitive quota/auth/policy evidence trips.
_CIRCUIT_STATES = frozenset({QUOTA_EXHAUSTED, AUTH_FAILURE, DISABLED_BY_POLICY})
_SCAN_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar(
    "wow_v17_quota_aware_discovery_context", default=None
)


def _new_context() -> dict[str, Any]:
    return {
        "providers": {},
        "paid_provider_calls_attempted": 0,
        "paid_provider_calls_succeeded": 0,
        "paid_provider_calls_blocked_by_quota_policy": 0,
        "paid_provider_calls_saved_by_cache": 0,
        "paid_provider_calls_saved_by_model_prefilter": 0,
        "paid_provider_calls_saved_by_free_discovery": 0,
        "public_discovery_requests": 0,
        "public_discovery_successes": 0,
        "market_rows_enriched": 0,
    }


def _provider_state(context: dict[str, Any], provider: str) -> dict[str, Any]:
    key = str(provider).upper()
    return context.setdefault("providers", {}).setdefault(
        key,
        {
            "status": AVAILABLE,
            "reason_code": None,
            "circuit_open": False,
            "calls_attempted": 0,
            "calls_succeeded": 0,
            "calls_blocked": 0,
        },
    )


def classify_provider_failure(code: Any) -> str:
    """Classify only explicit provider evidence; never guess quota exhaustion."""
    token = str(code or "").strip().upper()
    if any(marker in token for marker in (
        "QUOTA_EXHAUSTED", "QUOTA_EXCEEDED", "MONTHLY_LIMIT", "MONTHLY_QUOTA",
        "DATA_POINTS_EXHAUSTED", "DATAPOINTS_EXHAUSTED", "PLAN_LIMIT",
    )):
        return QUOTA_EXHAUSTED
    if any(marker in token for marker in (
        "HTTP_401", "HTTP_403", "AUTH_FAILURE", "AUTH_FAILED", "UNAUTHORIZED", "FORBIDDEN",
    )):
        return AUTH_FAILURE
    if any(marker in token for marker in (
        "DEACTIVATED", "DISABLED_BY_POLICY", "CREDENTIAL_UNCONFIGURED", "PROVIDER_DISABLED",
    )):
        return DISABLED_BY_POLICY
    if any(marker in token for marker in ("429", "RATE_LIMIT", "THROTTLED")):
        return RATE_LIMITED
    return TEMPORARILY_UNAVAILABLE


def _before_paid_call(provider: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]:
    context = _SCAN_CONTEXT.get()
    if context is None:
        return None, None, None
    state = _provider_state(context, provider)
    if state.get("circuit_open") is True:
        context["paid_provider_calls_blocked_by_quota_policy"] += 1
        state["calls_blocked"] += 1
        reason = str(state.get("reason_code") or state.get("status") or "PROVIDER_BLOCKED")
        return context, state, f"{str(provider).upper()}_CIRCUIT_OPEN:{reason}"
    context["paid_provider_calls_attempted"] += 1
    state["calls_attempted"] += 1
    return context, state, None


def _paid_success(context: dict[str, Any] | None, state: dict[str, Any] | None) -> None:
    if context is None or state is None:
        return
    context["paid_provider_calls_succeeded"] += 1
    state["calls_succeeded"] += 1
    state["status"] = AVAILABLE
    state["reason_code"] = None


def _paid_failure(context: dict[str, Any] | None, state: dict[str, Any] | None, code: Any) -> str:
    status = classify_provider_failure(code)
    if context is not None and state is not None:
        state["status"] = status
        state["reason_code"] = str(code or "") or None
        if status in _CIRCUIT_STATES:
            state["circuit_open"] = True
    return status


def _public_schedule_fetch(
    family: str,
    *,
    started: datetime,
    horizon_hours: int,
) -> tuple[bool, list[Mapping[str, Any]], str | None]:
    """Use the existing research-only ESPN scoreboard where configured."""
    from v17 import cross_sport_resilience_overlay as resilience
    from v17 import scout_secondary_source as secondary

    sport_key = resilience._ESPN_FAMILY_KEYS.get(str(family or "").upper())
    if not sport_key:
        return False, [], "PUBLIC_DISCOVERY_UNSUPPORTED_FOR_FAMILY"

    context = _SCAN_CONTEXT.get()
    if context is not None:
        context["public_discovery_requests"] += 1

    end = started + timedelta(hours=horizon_hours)
    result = secondary.secondary_for_request(
        f"/odds-api/v4/sports/{sport_key}/events",
        {
            "dateFormat": "iso",
            "commenceTimeFrom": started.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "commenceTimeTo": end.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        },
        {},
        primary_failure=None,
    )
    if not getattr(result, "ok", False):
        return False, [], str(getattr(result, "code", None) or "PUBLIC_DISCOVERY_FAILED")

    if context is not None:
        context["public_discovery_successes"] += 1
        context["paid_provider_calls_saved_by_free_discovery"] += 1

    rows: list[Mapping[str, Any]] = []
    for raw in getattr(result, "data", None) or []:
        if not isinstance(raw, Mapping):
            continue
        row = dict(raw)
        row["discovery_provider"] = "ESPN_SCOREBOARD"
        row["source_provider"] = "ESPN_SCOREBOARD"
        row["provider_event_id"] = row.get("_wow_secondary_event_id") or row.get("id")
        row["research_only"] = True
        row["prediction_authority"] = False
        row["exact_line_authority"] = False
        row["can_execute"] = False
        rows.append(row)
    return True, rows, None


def _quota_aware_odds_proxy_factory(*args: Any, **kwargs: Any):
    """Free schedule first; paid Odds proxy only when public discovery cannot answer."""
    from v17 import cross_sport_discovery_feed as feed
    from v17 import cross_sport_winner_discovery as discovery
    from v17.nightly_multiscout import proxy_get as default_proxy_get

    original_paid_factory = getattr(
        discovery, "_v17_cross_sport_resilience_original_odds_proxy_feed", None
    )
    if not callable(original_paid_factory):
        raise RuntimeError("QUOTA_AWARE_ODDS_PROXY_ORIGINAL_UNAVAILABLE")

    underlying_proxy_get = kwargs.pop("proxy_get", None) or default_proxy_get

    def metered_proxy_get(path: str, params: Any = None):
        context, state, blocked = _before_paid_call("ODDS_PROXY")
        if blocked:
            class BlockedResult:
                ok = False
                code = blocked
                data = None
            return BlockedResult()
        result = underlying_proxy_get(path, params)
        if getattr(result, "ok", False):
            _paid_success(context, state)
        else:
            _paid_failure(context, state, getattr(result, "code", None) or "ODDS_PROXY_REQUEST_FAILED")
        return result

    paid_fetch = original_paid_factory(*args, proxy_get=metered_proxy_get, **kwargs)
    started = kwargs.get("now") or datetime.now(timezone.utc)

    def fetch(family: str, target: Any = None) -> list[Mapping[str, Any]]:
        public_ok, public_rows, _ = _public_schedule_fetch(
            family, started=started, horizon_hours=feed.horizon_hours()
        )
        if public_ok:
            return list(public_rows)
        return list(paid_fetch(family, target) or ())

    setattr(fetch, "_wow_schedule_first", True)
    setattr(fetch, "_wow_quota_aware_provider", "ODDS_PROXY")
    setattr(fetch, "_wow_quota_aware_contract_version", CONTRACT_VERSION)
    return fetch


def _quota_aware_rundown_factory(*args: Any, **kwargs: Any):
    from v17 import cross_sport_discovery_feed as feed
    from v17 import cross_sport_winner_discovery as discovery

    original_factory = getattr(feed, "_v17_quota_aware_original_rundown_board_feed", None)
    if not callable(original_factory):
        raise RuntimeError("QUOTA_AWARE_RUNDOWN_ORIGINAL_UNAVAILABLE")
    base_fetch = original_factory(*args, **kwargs)

    def fetch(family: str, target: Any = None) -> list[Mapping[str, Any]]:
        context, state, blocked = _before_paid_call("RUNDOWN")
        if blocked:
            raise discovery.DiscoveryFeedError(blocked)
        try:
            rows = list(base_fetch(family, target) or ())
        except discovery.DiscoveryFeedError as exc:
            _paid_failure(context, state, exc.code)
            raise
        except Exception as exc:
            _paid_failure(context, state, type(exc).__name__)
            raise
        _paid_success(context, state)
        return rows

    setattr(fetch, "_wow_quota_aware_provider", "RUNDOWN")
    setattr(fetch, "_wow_quota_aware_contract_version", CONTRACT_VERSION)
    return fetch


def _normalize_public_alias(raw: Mapping[str, Any], event: Any):
    """A public scoreboard provider ID is an alias, never canonical official identity."""
    provider = str(raw.get("discovery_provider") or raw.get("source_provider") or "").upper()
    if provider != "ESPN_SCOREBOARD":
        return event
    alias = raw.get("provider_event_id") or raw.get("_wow_secondary_event_id") or raw.get("id")
    updated_raw = dict(getattr(event, "raw", {}) or {})
    updated_raw["provider_event_id"] = alias
    updated_raw["official_event_id"] = None
    return replace(
        event,
        official_event_id=None,
        provider="ESPN_SCOREBOARD",
        provider_sport_id=None,
        raw=updated_raw,
    )


def _coverage_status(result: Mapping[str, Any], context: Mapping[str, Any]) -> str:
    discovery_payload = result.get("discovery") if isinstance(result.get("discovery"), Mapping) else {}
    audits = discovery_payload.get("acquisition_audit") or []
    source_blockers = discovery_payload.get("source_blockers") or []
    incomplete = bool(source_blockers)
    for row in audits:
        if not isinstance(row, Mapping):
            continue
        if row.get("coverage_complete") is False:
            incomplete = True
        if str(row.get("request_status") or "").upper() in {
            "NO_CONFIGURED_DISCOVERY_FEED", "PROVIDER_REQUEST_FAILED", "PROVIDER_RATE_LIMITED",
            "PROVIDER_SCHEMA_FAILURE", "DISCOVERY_BUDGET_EXHAUSTED",
        }:
            incomplete = True
    for state in (context.get("providers") or {}).values():
        if isinstance(state, Mapping) and state.get("circuit_open") is True:
            incomplete = True
    return "PARTIAL_OR_UNPROVEN" if incomplete else "PROVEN_FOR_CONFIGURED_DISCOVERY_SOURCES"


def install_quota_aware_degraded_discovery() -> dict[str, Any]:
    """Install after cross-sport resilience so this wraps the final acquisition surfaces."""
    from v17 import cross_sport_discovery_feed as feed
    from v17 import cross_sport_winner_discovery as discovery

    if getattr(discovery, "_v17_quota_aware_degraded_discovery_installed", False):
        return {"status": "ALREADY_INSTALLED", "contract_version": CONTRACT_VERSION, "can_execute": False}

    original_odds = feed.odds_proxy_feed
    original_rundown = feed.rundown_board_feed
    original_normalize = discovery.normalize_discovered_event
    original_scan = discovery.run_cross_sport_winner_scan
    feed._v17_quota_aware_original_odds_proxy_feed = original_odds
    feed._v17_quota_aware_original_rundown_board_feed = original_rundown

    def normalized(raw: Mapping[str, Any], *args: Any, **kwargs: Any):
        return _normalize_public_alias(raw, original_normalize(raw, *args, **kwargs))

    def scan(*args: Any, **kwargs: Any) -> dict[str, Any]:
        context = _new_context()
        token = _SCAN_CONTEXT.set(context)
        try:
            result = dict(original_scan(*args, **kwargs))
        finally:
            _SCAN_CONTEXT.reset(token)
        result["quota_aware_acquisition"] = {
            "contract_version": CONTRACT_VERSION,
            "mode": "QUOTA_AWARE_DEGRADED_DISCOVERY_MODE",
            "provider_states": context["providers"],
            "paid_provider_calls_attempted": context["paid_provider_calls_attempted"],
            "paid_provider_calls_succeeded": context["paid_provider_calls_succeeded"],
            "paid_provider_calls_blocked_by_quota_policy": context["paid_provider_calls_blocked_by_quota_policy"],
            "paid_provider_calls_saved_by_cache": context["paid_provider_calls_saved_by_cache"],
            "paid_provider_calls_saved_by_model_prefilter": context["paid_provider_calls_saved_by_model_prefilter"],
            "paid_provider_calls_saved_by_free_discovery": context["paid_provider_calls_saved_by_free_discovery"],
            "public_discovery_requests": context["public_discovery_requests"],
            "public_discovery_successes": context["public_discovery_successes"],
            "market_rows_enriched": context["market_rows_enriched"],
            "probability_only_paid_market_calls_required": False,
            "market_probability_substitution_allowed": False,
            "generic_reasoning_probability_substitution_allowed": False,
            "can_execute": False,
        }
        result["BOARD_COVERAGE_STATUS"] = _coverage_status(result, context)
        result["can_execute"] = False
        return result

    feed.odds_proxy_feed = _quota_aware_odds_proxy_factory
    feed.rundown_board_feed = _quota_aware_rundown_factory
    discovery.normalize_discovered_event = normalized
    discovery.run_cross_sport_winner_scan = scan
    discovery._v17_quota_aware_original_normalize = original_normalize
    discovery._v17_quota_aware_original_scan = original_scan
    discovery._v17_quota_aware_degraded_discovery_installed = True
    return {
        "status": "INSTALLED",
        "contract_version": CONTRACT_VERSION,
        "mode": "QUOTA_AWARE_DEGRADED_DISCOVERY_MODE",
        "definitive_circuit_states": sorted(_CIRCUIT_STATES),
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": False,
    }


__all__ = [
    "AUTH_FAILURE", "AVAILABLE", "CAN_EXECUTE", "CONTRACT_VERSION", "DISABLED_BY_POLICY",
    "QUOTA_EXHAUSTED", "RATE_LIMITED", "TEMPORARILY_UNAVAILABLE",
    "classify_provider_failure", "install_quota_aware_degraded_discovery",
]
