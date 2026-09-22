"""Cross-sport discovery resilience overlay for WOW V17.

This overlay hardens acquisition/orchestration only. It does not create or alter
sporting probabilities, calibration, rank eligibility, terminal authority, or
execution posture.

Repairs:
- use ESPN scoreboard schedule identity first for supported team sports so
  event discovery is not unnecessarily coupled to sportsbook/provider health;
- retain the existing odds-proxy path as a fallback when ESPN discovery fails;
- short-circuit downstream market-data discovery after an authoritative schedule
  response for ESPN-supported team sports;
- give multi-target families (notably soccer) a wider per-family expansion
  budget without allowing them to starve later sports;
- bound expensive model invocations and round-robin the discovered slate across
  sports so one high-volume sport cannot consume the entire scorer budget;
- preserve every discovered row as an explicit terminal row. Rows not scored
  because the bounded scorer budget is exhausted terminate as a typed governed
  hold rather than disappearing.

``can_execute`` remains false throughout.
"""
from __future__ import annotations

import os
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping

CAN_EXECUTE = False
RESILIENCE_CONTRACT_VERSION = "V17_CROSS_SPORT_RESILIENCE_V1"
MODEL_INVOCATION_BUDGET_REACHED = "MODEL_INVOCATION_BUDGET_REACHED"

_ESPN_FAMILY_KEYS: dict[str, str] = {
    "MLB": "baseball_mlb",
    "NFL": "americanfootball_nfl",
    "NCAAF": "americanfootball_ncaaf",
    "NBA": "basketball_nba",
    "WNBA": "basketball_wnba",
    "NCAAB": "basketball_ncaab",
    "NHL": "icehockey_nhl",
}


def _int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


def model_invocation_limit() -> int:
    return _int_env(
        "WOW_CROSS_SPORT_MAX_MODEL_INVOCATIONS",
        12,
        minimum=1,
        maximum=32,
    )


def multi_target_budget_seconds(base_budget: float) -> float:
    try:
        configured = float(
            os.environ.get("WOW_CROSS_SPORT_MULTI_TARGET_BUDGET_SECONDS", "24")
        )
    except ValueError:
        configured = 24.0
    configured = max(0.5, min(configured, 120.0))
    return max(float(base_budget), configured)


def _iso(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _round_robin_events(events: Iterable[Any]) -> list[Any]:
    """Interleave sport families while preserving within-sport provider order."""
    queues: dict[str, deque[Any]] = defaultdict(deque)
    sport_order: list[str] = []
    for event in events:
        sport = str(getattr(event, "sport", "") or "UNKNOWN").upper()
        if sport not in queues:
            sport_order.append(sport)
        queues[sport].append(event)

    ordered: list[Any] = []
    remaining = True
    while remaining:
        remaining = False
        for sport in sport_order:
            queue = queues[sport]
            if queue:
                ordered.append(queue.popleft())
                remaining = True
    return ordered


def _schedule_first_fetch(
    original_fetch: Callable[..., Iterable[Mapping[str, Any]]],
    *,
    started: datetime,
    horizon_hours: int,
) -> Callable[..., Iterable[Mapping[str, Any]]]:
    """Use ESPN only for schedule identity; fall back to the existing feed on failure."""
    from v17 import scout_secondary_source as secondary

    end = started + timedelta(hours=horizon_hours)

    def fetch(family: str, target: Any = None) -> list[Mapping[str, Any]]:
        normalized = str(family or "").upper()
        sport_key = _ESPN_FAMILY_KEYS.get(normalized)
        if sport_key:
            result = secondary.secondary_for_request(
                f"/odds-api/v4/sports/{sport_key}/events",
                {
                    "dateFormat": "iso",
                    "commenceTimeFrom": _iso(started),
                    "commenceTimeTo": _iso(end),
                },
                {},
                primary_failure=None,
            )
            if getattr(result, "ok", False):
                rows: list[Mapping[str, Any]] = []
                for raw in getattr(result, "data", None) or []:
                    if not isinstance(raw, Mapping):
                        continue
                    row = dict(raw)
                    row["discovery_provider"] = "ESPN_SCOREBOARD"
                    row["source_provider"] = "ESPN_SCOREBOARD"
                    row["research_only"] = True
                    row["prediction_authority"] = False
                    row["can_execute"] = False
                    rows.append(row)
                return rows
        return list(original_fetch(family, target) or ())

    setattr(fetch, "_wow_schedule_first", True)
    setattr(fetch, "_wow_resilience_contract_version", RESILIENCE_CONTRACT_VERSION)
    return fetch


def install_cross_sport_resilience() -> dict[str, Any]:
    """Install schedule-first discovery and bounded cross-sport scoring."""
    from v17 import cross_sport_discovery_feed as feed
    from v17 import cross_sport_winner_discovery as discovery

    if getattr(discovery, "_v17_cross_sport_resilience_installed", False):
        return {
            "status": "ALREADY_INSTALLED",
            "contract_version": RESILIENCE_CONTRACT_VERSION,
            "can_execute": False,
        }

    original_odds_proxy_feed = feed.odds_proxy_feed
    original_union_feed = feed.union_feed
    original_discover = discovery.discover_winner_slate
    original_route = discovery.route_discovered_slate
    original_scan = discovery.run_cross_sport_winner_scan

    def resilient_odds_proxy_feed(*args: Any, **kwargs: Any):
        original_fetch = original_odds_proxy_feed(*args, **kwargs)
        started = kwargs.get("now") or datetime.now(timezone.utc)
        return _schedule_first_fetch(
            original_fetch,
            started=started,
            horizon_hours=feed.horizon_hours(),
        )

    def resilient_union_feed(*feeds: Callable[..., Iterable[Mapping[str, Any]]]):
        normal_union = original_union_feed(*feeds)

        def fetch(family: str, target: Any = None) -> list[Mapping[str, Any]]:
            normalized = str(family or "").upper()
            # The first cross-sport feed is the schedule-first adapter above. For
            # supported team sports, a successful schedule response is sufficient
            # to answer "what events exist" and should not trigger a second market
            # provider call. If it raises, preserve the existing union failover.
            if normalized in _ESPN_FAMILY_KEYS and feeds:
                first = feeds[0]
                if getattr(first, "_wow_schedule_first", False):
                    try:
                        return list(first(family, target) or ())
                    except Exception:
                        pass
            return list(normal_union(family, target) or ())

        setattr(fetch, "_wow_resilience_contract_version", RESILIENCE_CONTRACT_VERSION)
        return fetch

    def resilient_discover(*args: Any, **kwargs: Any):
        call_kwargs = dict(kwargs)
        if call_kwargs.get("budget_seconds") is None:
            call_kwargs["budget_seconds"] = multi_target_budget_seconds(
                discovery.discovery_budget_seconds()
            )
        return original_discover(*args, **call_kwargs)

    def resilient_route(inventory: Any, *args: Any, **kwargs: Any):
        score_row = kwargs.get("score_row")
        if not callable(score_row):
            return original_route(inventory, *args, **kwargs)

        limit = model_invocation_limit()
        attempts = 0

        def bounded_score(event: Any, model: Any):
            nonlocal attempts
            if attempts >= limit:
                return {
                    "code": MODEL_INVOCATION_BUDGET_REACHED,
                    "model_status": MODEL_INVOCATION_BUDGET_REACHED,
                    "reason": MODEL_INVOCATION_BUDGET_REACHED,
                    "requested_model_invocation_limit": limit,
                    "model_invoked": False,
                    "probability_publishable": False,
                    "rank_eligible": False,
                    "can_execute": False,
                }
            attempts += 1
            return score_row(event, model)

        ordered = _round_robin_events(list(getattr(inventory, "events", []) or []))
        original_events = getattr(inventory, "events", None)
        inventory.events = ordered
        call_kwargs = dict(kwargs)
        call_kwargs["score_row"] = bounded_score
        try:
            return original_route(inventory, *args, **call_kwargs)
        finally:
            inventory.events = original_events

    def resilient_scan(*args: Any, **kwargs: Any):
        result = dict(original_scan(*args, **kwargs))
        rows = [row for row in (result.get("rows") or []) if isinstance(row, Mapping)]
        budget_holds = 0
        for row in rows:
            detail = row.get("detail") if isinstance(row.get("detail"), Mapping) else {}
            if (
                row.get("model_status") == MODEL_INVOCATION_BUDGET_REACHED
                or detail.get("reason") == MODEL_INVOCATION_BUDGET_REACHED
                or detail.get("code") == MODEL_INVOCATION_BUDGET_REACHED
            ):
                budget_holds += 1
        result["cross_sport_resilience"] = {
            "contract_version": RESILIENCE_CONTRACT_VERSION,
            "schedule_first_families": sorted(_ESPN_FAMILY_KEYS),
            "model_invocation_limit": model_invocation_limit(),
            "rows_held_by_model_invocation_budget": budget_holds,
            "multi_target_budget_seconds": multi_target_budget_seconds(
                discovery.discovery_budget_seconds()
            ),
            "market_probability_substitution_allowed": False,
            "global_terminal_authority": "V17_TERMINAL_REDUCER",
            "can_execute": False,
        }
        result["can_execute"] = False
        return result

    feed.odds_proxy_feed = resilient_odds_proxy_feed
    feed.union_feed = resilient_union_feed
    discovery.discover_winner_slate = resilient_discover
    discovery.route_discovered_slate = resilient_route
    discovery.run_cross_sport_winner_scan = resilient_scan

    discovery._v17_cross_sport_resilience_original_odds_proxy_feed = original_odds_proxy_feed
    discovery._v17_cross_sport_resilience_original_union_feed = original_union_feed
    discovery._v17_cross_sport_resilience_original_discover = original_discover
    discovery._v17_cross_sport_resilience_original_route = original_route
    discovery._v17_cross_sport_resilience_original_scan = original_scan
    discovery._v17_cross_sport_resilience_installed = True

    return {
        "status": "INSTALLED",
        "contract_version": RESILIENCE_CONTRACT_VERSION,
        "schedule_first_families": sorted(_ESPN_FAMILY_KEYS),
        "model_invocation_limit": model_invocation_limit(),
        "multi_target_budget_seconds": multi_target_budget_seconds(
            discovery.discovery_budget_seconds()
        ),
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "MODEL_INVOCATION_BUDGET_REACHED",
    "RESILIENCE_CONTRACT_VERSION",
    "_round_robin_events",
    "_schedule_first_fetch",
    "install_cross_sport_resilience",
    "model_invocation_limit",
    "multi_target_budget_seconds",
]
