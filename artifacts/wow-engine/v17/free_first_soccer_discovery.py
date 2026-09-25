"""Free-first Soccer discovery overlay for WOW V17.

This module is acquisition/orchestration only.  It lets configured Soccer
competition targets use ESPN's public scoreboard for schedule identity before a
paid provider is touched.  Public scoreboard identifiers remain provider aliases
and all returned rows remain research-only; this module never creates sporting
probability, calibration, rank eligibility, exact-line authority, or execution
authority.

The overlay installs *after* the existing cross-sport resilience and quota-aware
layers so it can extend their final acquisition surfaces without rewriting them.
Only ESPN competition slugs that have been independently documented are mapped.
Unverified targets deliberately fall through to the existing provider chain.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping

CAN_EXECUTE = False
CONTRACT_VERSION = "V17_FREE_FIRST_SOCCER_DISCOVERY_V1"

# league -> (internal sport key, ESPN sport, ESPN league slug, display title)
# Keep this list evidence-backed. A missing competition is preferable to a
# guessed public slug because discovery coverage must remain truthful.
SOCCER_ESPN_TARGETS: dict[str, tuple[str, str, str, str]] = {
    "MLS": ("soccer_mls", "soccer", "usa.1", "MLS"),
    "EPL": ("soccer_epl", "soccer", "eng.1", "English Premier League"),
    "FRA1": ("soccer_fra1", "soccer", "fra.1", "French Ligue 1"),
    "GER1": ("soccer_ger1", "soccer", "ger.1", "German Bundesliga"),
    "ESP1": ("soccer_esp1", "soccer", "esp.1", "Spanish LaLiga"),
    "ITA1": ("soccer_ita1", "soccer", "ita.1", "Italian Serie A"),
    "UEFACHAMP": (
        "soccer_uefachamp",
        "soccer",
        "uefa.champions",
        "UEFA Champions League",
    ),
    "UEFAEUROPA": (
        "soccer_uefaeuropa",
        "soccer",
        "uefa.europa",
        "UEFA Europa League",
    ),
    "FIFA": ("soccer_fifa", "soccer", "fifa.world", "FIFA World Cup"),
    "LIGAMX": ("soccer_ligamx", "soccer", "mex.1", "Liga MX"),
}


def _league(target: Any) -> str:
    return str(getattr(target, "league", "") or "").strip().upper()


def espn_sport_key(family: str, target: Any = None) -> str | None:
    """Return an evidence-backed ESPN key for one configured Soccer target."""
    if str(family or "").strip().upper() != "SOCCER":
        return None
    entry = SOCCER_ESPN_TARGETS.get(_league(target))
    return entry[0] if entry else None


def _install_secondary_mappings(secondary: Any) -> None:
    for internal_key, sport, league, title in SOCCER_ESPN_TARGETS.values():
        secondary.ESPN_SPORT_MAP[internal_key] = (sport, league, title)


def _iso(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _public_soccer_schedule_fetch(
    family: str,
    *,
    target: Any = None,
    started: datetime,
    horizon_hours: int,
) -> tuple[bool, list[Mapping[str, Any]], str | None]:
    """Fetch one Soccer competition schedule without consuming paid quota."""
    if str(family or "").strip().upper() != "SOCCER":
        return False, [], "PUBLIC_DISCOVERY_UNSUPPORTED_FOR_FAMILY"

    sport_key = espn_sport_key(family, target)
    if not sport_key:
        return False, [], "PUBLIC_DISCOVERY_UNVERIFIED_TARGET"

    from v17 import quota_aware_degraded_discovery as quota
    from v17 import scout_secondary_source as secondary

    context = quota._SCAN_CONTEXT.get()
    if context is not None:
        context["public_discovery_requests"] += 1

    end = started + timedelta(hours=horizon_hours)
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
    # A successful empty scoreboard is still a real schedule answer. Returning
    # success here intentionally prevents a paid call merely to prove the zero.
    return True, rows, None


def _wrap_odds_proxy_factory(
    original_factory: Callable[..., Callable[..., Iterable[Mapping[str, Any]]]],
) -> Callable[..., Callable[..., Iterable[Mapping[str, Any]]]]:
    """Put target-aware free Soccer discovery ahead of the existing paid path."""
    from v17 import cross_sport_discovery_feed as feed

    def factory(*args: Any, **kwargs: Any):
        base_fetch = original_factory(*args, **kwargs)
        started = kwargs.get("now") or datetime.now(timezone.utc)

        def fetch(family: str, target: Any = None) -> list[Mapping[str, Any]]:
            if str(family or "").strip().upper() == "SOCCER":
                public_ok, public_rows, _ = _public_soccer_schedule_fetch(
                    family,
                    target=target,
                    started=started,
                    horizon_hours=feed.horizon_hours(),
                )
                if public_ok:
                    return list(public_rows)
            return list(base_fetch(family, target) or ())

        setattr(fetch, "_wow_schedule_first", True)
        setattr(fetch, "_wow_free_first_soccer", True)
        setattr(fetch, "_wow_free_first_contract_version", CONTRACT_VERSION)
        return fetch

    return factory


def _wrap_union_factory(
    original_union_factory: Callable[..., Callable[..., Iterable[Mapping[str, Any]]]],
) -> Callable[..., Callable[..., Iterable[Mapping[str, Any]]]]:
    """Stop after successful free Soccer schedule discovery.

    The existing resilience union already does this for single-key team sports.
    Soccer is multi-target, so it needs the same short-circuit at the target-aware
    layer; otherwise the subsequent TheRundown feed would still be called.
    """

    def factory(*feeds: Callable[..., Iterable[Mapping[str, Any]]]):
        normal_union = original_union_factory(*feeds)
        fallback_union = original_union_factory(*feeds[1:]) if len(feeds) > 1 else None

        def fetch(family: str, target: Any = None) -> list[Mapping[str, Any]]:
            if str(family or "").strip().upper() == "SOCCER" and feeds:
                first = feeds[0]
                if getattr(first, "_wow_schedule_first", False):
                    try:
                        return list(first(family, target) or ())
                    except Exception:
                        if fallback_union is not None:
                            return list(fallback_union(family, target) or ())
                        raise
            return list(normal_union(family, target) or ())

        setattr(fetch, "_wow_free_first_soccer", True)
        setattr(fetch, "_wow_free_first_contract_version", CONTRACT_VERSION)
        return fetch

    return factory


def install_free_first_soccer_discovery() -> dict[str, Any]:
    """Install target-aware free Soccer schedule discovery after quota controls."""
    from v17 import cross_sport_discovery_feed as feed
    from v17 import scout_secondary_source as secondary

    if getattr(feed, "_v17_free_first_soccer_discovery_installed", False):
        return {
            "status": "ALREADY_INSTALLED",
            "contract_version": CONTRACT_VERSION,
            "can_execute": False,
        }

    _install_secondary_mappings(secondary)

    original_odds_proxy_factory = feed.odds_proxy_feed
    original_union_factory = feed.union_feed
    feed._v17_free_first_soccer_original_odds_proxy_feed = original_odds_proxy_factory
    feed._v17_free_first_soccer_original_union_feed = original_union_factory
    feed.odds_proxy_feed = _wrap_odds_proxy_factory(original_odds_proxy_factory)
    feed.union_feed = _wrap_union_factory(original_union_factory)
    feed._v17_free_first_soccer_discovery_installed = True

    return {
        "status": "INSTALLED",
        "contract_version": CONTRACT_VERSION,
        "family": "SOCCER",
        "verified_leagues": sorted(SOCCER_ESPN_TARGETS),
        "unverified_targets_fall_through": True,
        "prediction_authority": False,
        "exact_line_authority": False,
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "CONTRACT_VERSION",
    "SOCCER_ESPN_TARGETS",
    "espn_sport_key",
    "install_free_first_soccer_discovery",
]
