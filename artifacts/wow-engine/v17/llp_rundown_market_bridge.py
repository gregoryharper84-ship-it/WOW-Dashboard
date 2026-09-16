"""TheRundown -> LLP team/event market-context bridge.

TheRundown is evidence, never the controlling sporting-probability model. This
bridge refreshes the team/event request's existing market-prior contract before
LLP scoring so favorite/underdog/upset classification and any *certified* fitted
model market-prior component can consume current cross-book context.

The bridge also exposes typed opening/current/closing market evidence for LLP
postmortem/CLV workflows. Current prices are never mislabeled as closing prices:
closing evidence is fetched only from TheRundown's native V2 closing endpoint.

Invariants:
- exactly one sport-specific LLP fitted specialist owns sporting probability;
- TheRundown implied/no-vig values are market evidence, not governed probability;
- provider failure cannot become MODEL_UNAVAILABLE or erase a completed sporting
  probability;
- material favorite-role disagreement blocks market-relative ranking, not the
  underlying sporting probability package;
- opening/current/closing evidence remains separately typed;
- can_execute is always false.
"""
from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime, timezone
from statistics import median
from threading import RLock
from typing import Any

from v17 import market_evidence_native_live as live
from v17 import market_evidence_sources as sources

CAN_EXECUTE = False
BRIDGE_SOURCE = "RUNDOWN_MARKET_EVIDENCE"
_BRIDGE_LOCK = RLock()

# Provider-documented V2 full-game prematch market ids:
#   1 = moneyline, 2 = spread/handicap, 3 = total.
# Keep this separate from sources.rundown_winner_market_ids(), whose contract is
# deliberately operator-pinned for legacy winner-only scans.
_LLP_MAINLINE_MARKET_IDS = ("1", "2", "3")
_RUNDOWN_CLOSING_PATH = "/api/v2/sports/{sport_id}/closing/{date}"

_SPORT_KEYS = {
    "MLB": "baseball_mlb",
    "BASEBALL": "baseball_mlb",
    "BASEBALL_MLB": "baseball_mlb",
    "NFL": "americanfootball_nfl",
    "NCAAF": "americanfootball_ncaaf",
    "CFB": "americanfootball_ncaaf",
    "NBA": "basketball_nba",
    "WNBA": "basketball_wnba",
    "NCAAB": "basketball_ncaab",
    "CBB": "basketball_ncaab",
    "NHL": "icehockey_nhl",
    "MMA": "mma_mixed_martial_arts",
    "UFC": "mma_mixed_martial_arts",
    "EPL": "soccer_epl",
    "SOCCER_EPL": "soccer_epl",
}
_MARKET_RELATIVE_INTENTS = {"FAVORITE", "UNDERDOG", "UPSET"}


def enabled() -> bool:
    return os.environ.get("WOW_LLP_RUNDOWN_MARKET_ENABLED", "true").strip().lower() == "true"


def _norm(value: Any) -> str:
    return sources._norm(value)


def _sport_key(req: Any) -> str | None:
    league = str(getattr(req, "league", "") or "").strip().upper()
    sport = str(getattr(req, "sport", "") or "").strip().upper()
    return _SPORT_KEYS.get(league) or _SPORT_KEYS.get(sport)


def _affiliate_ids() -> tuple[str, ...] | None:
    """Optional operator pin; unset means all entitled books."""
    raw = os.environ.get("WOW_RUNDOWN_LLP_AFFILIATE_IDS", "").strip()
    if not raw:
        return None
    values = tuple(token.strip() for token in raw.split(",") if token.strip())
    return values or None


def _american_implied(price: Any) -> float | None:
    try:
        odds = float(price)
    except (TypeError, ValueError):
        return None
    if odds == 0:
        return None
    if odds > 0:
        return 100.0 / (odds + 100.0)
    return (-odds) / ((-odds) + 100.0)


def _event_match(req: Any, event: dict[str, Any]) -> bool:
    return (
        _norm(event.get("home_team")) == _norm(getattr(req, "home_team", None))
        and _norm(event.get("away_team")) == _norm(getattr(req, "away_team", None))
    )


def _latest_timestamp(values: list[str]) -> str:
    parsed: list[tuple[datetime, str]] = []
    for value in values:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.utcoffset() is not None:
                parsed.append((dt.astimezone(timezone.utc), str(value)))
        except (TypeError, ValueError):
            continue
    if parsed:
        return max(parsed, key=lambda item: item[0])[1]
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _market_from_event(req: Any, event: dict[str, Any]) -> dict[str, Any]:
    """Build the existing current H2H market-prior contract."""
    home_name = str(getattr(req, "home_team", ""))
    away_name = str(getattr(req, "away_team", ""))
    home_norm = _norm(home_name)
    away_norm = _norm(away_name)
    pairs: list[tuple[float, float]] = []
    timestamps: list[str] = []
    books_used: list[str] = []
    three_way = False

    for book in event.get("bookmakers") or []:
        if not isinstance(book, dict):
            continue
        for market in book.get("markets") or []:
            if not isinstance(market, dict) or market.get("key") != "h2h":
                continue
            home_price = None
            away_price = None
            extra_named_outcome = False
            for outcome in market.get("outcomes") or []:
                if not isinstance(outcome, dict):
                    continue
                name = _norm(outcome.get("name"))
                if name == home_norm:
                    home_price = outcome.get("price")
                elif name == away_norm:
                    away_price = outcome.get("price")
                elif name:
                    extra_named_outcome = True
            if extra_named_outcome:
                three_way = True
                continue
            home_implied = _american_implied(home_price)
            away_implied = _american_implied(away_price)
            if home_implied is None or away_implied is None:
                continue
            total = home_implied + away_implied
            if total <= 0:
                continue
            pairs.append((home_implied / total, away_implied / total))
            books_used.append(str(book.get("title") or book.get("key") or "UNKNOWN"))
            stamp = market.get("last_update") or book.get("last_update")
            if stamp:
                timestamps.append(str(stamp))

    # Mixed two-way/three-way source observations are a market-definition
    # conflict, not permission to discard the draw and manufacture a binary
    # prior from whichever book happened to omit it.
    if three_way:
        return {
            "status": "THREE_WAY_MARKET_UNSUPPORTED_FOR_BINARY_PRIOR",
            "provider": BRIDGE_SOURCE,
            "prediction_authority": False,
            "market_role_evidence_only": True,
            "can_execute": False,
        }
    if not pairs:
        return {
            "status": "NO_EXACT_H2H_PRICES",
            "provider": BRIDGE_SOURCE,
            "prediction_authority": False,
            "market_role_evidence_only": True,
            "can_execute": False,
        }

    home = sum(pair[0] for pair in pairs) / len(pairs)
    away = sum(pair[1] for pair in pairs) / len(pairs)
    timestamp = _latest_timestamp(timestamps)
    event_id = str(event.get("id") or getattr(req, "official_event_id", "UNKNOWN"))
    favorite = home_name if home > away else away_name if away > home else None
    return {
        "status": "EXACT_LINE",
        "provider": BRIDGE_SOURCE,
        "snapshot_id": f"rundown:{event_id}:{timestamp}",
        "timestamp": timestamp,
        "home_probability": home,
        "away_probability": away,
        "quality": "CROSS_BOOK_NO_VIG",
        "source": BRIDGE_SOURCE,
        "book_count": len(pairs),
        "books_used": books_used,
        "favorite": favorite,
        "prediction_authority": False,
        "market_role_evidence_only": True,
        "can_execute": False,
    }


def _best_american(quotes: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the bettor-favorable American price (numerically greatest)."""
    usable = [q for q in quotes if isinstance(q.get("american_odds"), (int, float))]
    if not usable:
        return None
    return max(usable, key=lambda q: float(q["american_odds"]))


def _mainline_details(req: Any, event: dict[str, Any], *, snapshot_kind: str) -> dict[str, Any]:
    """Extract typed ML + game-total line-shopping evidence from one snapshot."""
    home_name = str(getattr(req, "home_team", ""))
    away_name = str(getattr(req, "away_team", ""))
    home_norm = _norm(home_name)
    away_norm = _norm(away_name)
    ml_home: list[dict[str, Any]] = []
    ml_away: list[dict[str, Any]] = []
    totals: list[dict[str, Any]] = []
    timestamps: list[str] = []

    for book in event.get("bookmakers") or []:
        if not isinstance(book, dict):
            continue
        bookmaker = str(book.get("title") or book.get("key") or "UNKNOWN")
        book_stamp = book.get("last_update")
        for market in book.get("markets") or []:
            if not isinstance(market, dict):
                continue
            market_stamp = market.get("last_update") or book_stamp
            if market_stamp:
                timestamps.append(str(market_stamp))
            key = str(market.get("key") or "")
            if key == "h2h":
                for outcome in market.get("outcomes") or []:
                    if not isinstance(outcome, dict):
                        continue
                    try:
                        price = float(outcome.get("price"))
                    except (TypeError, ValueError):
                        continue
                    quote = {
                        "bookmaker": bookmaker,
                        "american_odds": price,
                        "updated_at": market_stamp,
                    }
                    name = _norm(outcome.get("name"))
                    if name == home_norm:
                        ml_home.append(quote)
                    elif name == away_norm:
                        ml_away.append(quote)
            elif key == "totals":
                per_book: dict[str, Any] = {
                    "bookmaker": bookmaker,
                    "updated_at": market_stamp,
                    "over_point": None,
                    "over_price": None,
                    "under_point": None,
                    "under_price": None,
                }
                for outcome in market.get("outcomes") or []:
                    if not isinstance(outcome, dict):
                        continue
                    side = str(outcome.get("name") or "").strip().lower()
                    try:
                        point = float(outcome.get("point"))
                    except (TypeError, ValueError):
                        point = None
                    try:
                        price = float(outcome.get("price"))
                    except (TypeError, ValueError):
                        price = None
                    if side == "over":
                        per_book["over_point"] = point
                        per_book["over_price"] = price
                    elif side == "under":
                        per_book["under_point"] = point
                        per_book["under_price"] = price
                if per_book["over_point"] is not None or per_book["under_point"] is not None:
                    totals.append(per_book)

    home_best = _best_american(ml_home)
    away_best = _best_american(ml_away)
    total_points: list[float] = []
    for quote in totals:
        points = [
            float(value)
            for value in (quote.get("over_point"), quote.get("under_point"))
            if isinstance(value, (int, float))
        ]
        if points:
            total_points.append(sum(points) / len(points))

    lowest_over = None
    over_rows = [q for q in totals if isinstance(q.get("over_point"), (int, float))]
    if over_rows:
        lowest = min(float(q["over_point"]) for q in over_rows)
        candidates = [q for q in over_rows if float(q["over_point"]) == lowest]
        lowest_over = max(
            candidates,
            key=lambda q: float(q.get("over_price")) if isinstance(q.get("over_price"), (int, float)) else float("-inf"),
        )

    highest_under = None
    under_rows = [q for q in totals if isinstance(q.get("under_point"), (int, float))]
    if under_rows:
        highest = max(float(q["under_point"]) for q in under_rows)
        candidates = [q for q in under_rows if float(q["under_point"]) == highest]
        highest_under = max(
            candidates,
            key=lambda q: float(q.get("under_price")) if isinstance(q.get("under_price"), (int, float)) else float("-inf"),
        )

    return {
        "status": "AVAILABLE" if (ml_home or ml_away or totals) else "NO_MAINLINE_MARKETS",
        "snapshot_kind": snapshot_kind,
        "timestamp": _latest_timestamp(timestamps),
        "moneyline": {
            "home_team": home_name,
            "away_team": away_name,
            "home_quotes": ml_home,
            "away_quotes": ml_away,
            "home_best": home_best,
            "away_best": away_best,
            "book_count": len({q["bookmaker"] for q in [*ml_home, *ml_away]}),
        },
        "game_total": {
            "consensus_main_line": float(median(total_points)) if total_points else None,
            "consensus_method": "MEDIAN_MAIN_LINE_ACROSS_BOOKS" if total_points else None,
            "bookmaker_totals": totals,
            # Totals have side-dependent line shopping. Do not manufacture one
            # universal "best total" number.
            "lowest_over_line": lowest_over,
            "highest_under_line": highest_under,
            "book_count": len({q["bookmaker"] for q in totals}),
        },
        "prediction_authority": False,
        "market_role_evidence_only": True,
        "can_execute": False,
    }


def _provider_failure(result: sources.MarketEvidenceResult, *, snapshot_kind: str) -> dict[str, Any]:
    return {
        "status": "MARKET_DATA_UNOBTAINABLE",
        "snapshot_kind": snapshot_kind,
        "provider": BRIDGE_SOURCE,
        "reason_code": result.code,
        "rate_limit": result.rate_limit,
        "request_audit": result.request_audit,
        "prediction_authority": False,
        "can_execute": False,
    }


def _matched_event(req: Any, result: sources.MarketEvidenceResult, *, snapshot_kind: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not result.ok:
        return None, _provider_failure(result, snapshot_kind=snapshot_kind)
    matches = [event for event in (result.data or []) if isinstance(event, dict) and _event_match(req, event)]
    if len(matches) != 1:
        return None, {
            "status": "EVENT_NOT_FOUND" if not matches else "EVENT_IDENTITY_AMBIGUOUS",
            "snapshot_kind": snapshot_kind,
            "provider": BRIDGE_SOURCE,
            "match_count": len(matches),
            "prediction_authority": False,
            "can_execute": False,
        }
    return matches[0], None


def _current_snapshot(req: Any, sport_key: str, *, opener: Any = None) -> sources.MarketEvidenceResult:
    return live.get_sport_date_odds_snapshot(
        sport_key,
        str(getattr(req, "requested_slate_date", "")),
        capability="events",
        opener=opener,
        market_ids=_LLP_MAINLINE_MARKET_IDS,
        affiliate_ids=_affiliate_ids(),
        main_line=True,
        hide_closed=True,
    )


def _opening_snapshot(req: Any, sport_key: str, *, opener: Any = None) -> sources.MarketEvidenceResult:
    return live.get_sport_date_odds_snapshot(
        sport_key,
        str(getattr(req, "requested_slate_date", "")),
        capability="openers",
        opener=opener,
        market_ids=_LLP_MAINLINE_MARKET_IDS,
        affiliate_ids=_affiliate_ids(),
        main_line=None,
        hide_closed=None,
    )


def _install_closing_endpoint() -> bool:
    """Add the provider-documented V2 closing path without changing auth."""
    provider = sources.PROVIDERS.get("RUNDOWN")
    if provider is None:
        return False
    if provider.endpoints.get("closing") == _RUNDOWN_CLOSING_PATH:
        return True
    endpoints = dict(provider.endpoints)
    endpoints["closing"] = _RUNDOWN_CLOSING_PATH
    sources.PROVIDERS["RUNDOWN"] = replace(provider, endpoints=endpoints)
    return True


def _closing_snapshot(req: Any, sport_key: str, *, opener: Any = None) -> sources.MarketEvidenceResult:
    """Fetch native closing lines; never derive a close from a current quote."""
    if not _install_closing_endpoint():
        return sources._fail("RUNDOWN", "closing", "MARKET_EVIDENCE_ENDPOINT_UNCONFIGURED")
    resolved = sources.rundown_sport_id(sport_key, opener=opener)
    if not resolved.ok:
        return resolved
    params: dict[str, Any] = {
        "offset": str(sources.rundown_date_offset_minutes()),
        "market_ids": ",".join(_LLP_MAINLINE_MARKET_IDS),
    }
    affiliate_ids = _affiliate_ids()
    if affiliate_ids:
        params["affiliate_ids"] = ",".join(affiliate_ids)
    fetched = sources.fetch(
        "RUNDOWN",
        "closing",
        path_values={
            "sport_id": resolved.data,
            "date": str(getattr(req, "requested_slate_date", "")),
        },
        params=params,
        opener=opener,
    )
    if not fetched.ok:
        return fetched
    normalized = sources.normalize_market_payload(
        fetched.data,
        provider="RUNDOWN",
        capability="closing",
        sport_key=sport_key,
    )
    if normalized.ok:
        normalized.observed_at = fetched.observed_at
        normalized.endpoint = fetched.endpoint
    return normalized


def _best_price(details: dict[str, Any], side: str) -> float | None:
    best = ((details.get("moneyline") or {}).get(f"{side}_best") or {})
    value = best.get("american_odds")
    return float(value) if isinstance(value, (int, float)) else None


def _build_clv_compat(
    *,
    opening: dict[str, Any],
    current: dict[str, Any],
    current_prior: dict[str, Any],
) -> dict[str, Any]:
    """Compatibility payload; close remains pending during pregame scoring."""
    return {
        "open_odds": {
            "home": _best_price(opening, "home"),
            "away": _best_price(opening, "away"),
        },
        "current_odds": {
            "home": _best_price(current, "home"),
            "away": _best_price(current, "away"),
        },
        "close_odds": None,
        "closing_market_probability": None,
        "closing_status": "PENDING_NATIVE_CLOSING_SNAPSHOT",
        "open_snapshot_status": opening.get("status"),
        "current_snapshot_status": current.get("status"),
        "current_no_vig_probability": {
            "home": current_prior.get("home_probability"),
            "away": current_prior.get("away_probability"),
        } if current_prior.get("status") == "EXACT_LINE" else None,
        "provider": BRIDGE_SOURCE,
        "prediction_authority": False,
        "can_execute": False,
    }


def _prop_tagger_compat(opening: dict[str, Any], current: dict[str, Any], current_prior: dict[str, Any]) -> dict[str, Any]:
    """Schema-shaped aliases for downstream evidence consumers only.

    This does not route the row into WOW_PROP_LANE and carries no probability
    authority. Team/event ownership remains LLP_TEAM_BETTING_ENGINE.
    """
    return {
        "source": "odds_api",
        "provider": "RUNDOWN",
        "gameTotal": (current.get("game_total") or {}).get("consensus_main_line"),
        "bookmakerOdds": {
            "home": _best_price(current, "home"),
            "away": _best_price(current, "away"),
        },
        "openLine": {
            "home": _best_price(opening, "home"),
            "away": _best_price(opening, "away"),
        },
        "impliedProb": {
            "home": current_prior.get("home_probability"),
            "away": current_prior.get("away_probability"),
        } if current_prior.get("status") == "EXACT_LINE" else None,
        "compatibility_only": True,
        "controlling_route": "LLP_TEAM_BETTING_ENGINE",
        "prediction_authority": False,
        "can_execute": False,
    }


def resolve_rundown_market_context(req: Any, *, opener: Any = None) -> dict[str, Any]:
    """Resolve current LLP market prior plus opening/current main-line evidence."""
    if not enabled():
        return {"status": "DISABLED", "provider": BRIDGE_SOURCE, "can_execute": False}
    sport_key = _sport_key(req)
    if not sport_key:
        return {
            "status": "SPORT_NOT_MAPPED",
            "provider": BRIDGE_SOURCE,
            "prediction_authority": False,
            "can_execute": False,
        }

    current_result = _current_snapshot(req, sport_key, opener=opener)
    current_event, current_error = _matched_event(req, current_result, snapshot_kind="CURRENT")
    if current_error is not None:
        return current_error
    assert current_event is not None

    current_prior = _market_from_event(req, current_event)
    current_details = _mainline_details(req, current_event, snapshot_kind="CURRENT")

    # Opening evidence is deliberately non-blocking for sporting probability.
    # A plan/entitlement/provider failure is preserved under opening.status while
    # the current H2H market-prior contract remains usable when valid.
    opening_result = _opening_snapshot(req, sport_key, opener=opener)
    opening_event, opening_error = _matched_event(req, opening_result, snapshot_kind="OPENING")
    opening_details = (
        opening_error
        if opening_error is not None
        else _mainline_details(req, opening_event or {}, snapshot_kind="OPENING")
    )

    return {
        **current_prior,
        "current": current_details,
        "opening": opening_details,
        "clv_market_evidence": _build_clv_compat(
            opening=opening_details,
            current=current_details,
            current_prior=current_prior,
        ),
        "prop_tagger_compat": _prop_tagger_compat(opening_details, current_details, current_prior),
        "market_ids": list(_LLP_MAINLINE_MARKET_IDS),
        "probability_mutated_by_evidence": False,
        "can_execute": False,
    }


def resolve_rundown_closing_context(req: Any, *, opener: Any = None) -> dict[str, Any]:
    """Retrieve native TheRundown closing lines for LLP postmortem/CLV grading.

    This helper is intentionally separate from pregame scoring. It never uses the
    latest current quote as a synthetic close and never changes a historical
    sporting probability package.
    """
    if not enabled():
        return {"status": "DISABLED", "provider": BRIDGE_SOURCE, "can_execute": False}
    sport_key = _sport_key(req)
    if not sport_key:
        return {
            "status": "SPORT_NOT_MAPPED",
            "provider": BRIDGE_SOURCE,
            "prediction_authority": False,
            "can_execute": False,
        }
    result = _closing_snapshot(req, sport_key, opener=opener)
    event, error = _matched_event(req, result, snapshot_kind="CLOSING")
    if error is not None:
        return error
    assert event is not None
    prior = _market_from_event(req, event)
    details = _mainline_details(req, event, snapshot_kind="CLOSING")
    return {
        "status": "EXACT_CLOSING_LINE" if prior.get("status") == "EXACT_LINE" else prior.get("status"),
        "provider": BRIDGE_SOURCE,
        "timestamp": prior.get("timestamp"),
        "close_odds": {
            "home": _best_price(details, "home"),
            "away": _best_price(details, "away"),
        },
        "closing_market_probability": {
            "home": prior.get("home_probability"),
            "away": prior.get("away_probability"),
        } if prior.get("status") == "EXACT_LINE" else None,
        "closing": details,
        "market_ids": list(_LLP_MAINLINE_MARKET_IDS),
        "prediction_authority": False,
        "probability_mutated_by_bridge": False,
        "can_execute": False,
    }


def _favorite_from_prior(req: Any, prior: dict[str, Any] | None) -> str | None:
    prior = dict(prior or {})
    try:
        home = float(prior["home_probability"])
        away = float(prior["away_probability"])
    except (KeyError, TypeError, ValueError):
        return None
    if home == away:
        return None
    return str(getattr(req, "home_team", "")) if home > away else str(getattr(req, "away_team", ""))


def _as_market_prior(context: dict[str, Any]) -> dict[str, Any] | None:
    if context.get("status") != "EXACT_LINE":
        return None
    return {
        "home_probability": context["home_probability"],
        "away_probability": context["away_probability"],
        "timestamp": context["timestamp"],
        "quality": context["quality"],
        "source": context["source"],
        "snapshot_id": context["snapshot_id"],
        "book_count": context["book_count"],
    }


def install_llp_rundown_market_bridge(team_event_module: Any) -> bool:
    """Wrap LLP team/event scoring with a pre-score TheRundown market refresh."""
    if getattr(team_event_module, "_v17_llp_rundown_market_bridge_installed", False):
        return True
    original = getattr(team_event_module, "score_team_event_request", None)
    if not callable(original):
        return False

    def score_with_rundown(req: Any, *, event_api: Any, canonical_hydration_required: bool = False):
        with _BRIDGE_LOCK:
            caller_prior = dict(getattr(req, "market_prior", None) or {})
            caller_favorite = _favorite_from_prior(req, caller_prior)
            context = resolve_rundown_market_context(req)
            rundown_prior = _as_market_prior(context)
            rundown_favorite = context.get("favorite") if rundown_prior else None
            conflict = bool(caller_favorite and rundown_favorite and caller_favorite != rundown_favorite)

            if rundown_prior is not None:
                # Mutate the request object intentionally so the outer LLP
                # probability-preservation/upset wrapper sees the same refreshed
                # market context after the base scorer returns.
                req.market_prior = rundown_prior

            result = original(
                req,
                event_api=event_api,
                canonical_hydration_required=canonical_hydration_required,
            )
            if not isinstance(result, dict):
                return result

            out = dict(result)
            out["llp_rundown_market_evidence"] = {
                **context,
                "caller_market_source": caller_prior.get("source"),
                "caller_favorite": caller_favorite,
                "rundown_favorite": rundown_favorite,
                "favorite_status_conflict": conflict,
                "probability_mutated_by_bridge": False,
                "can_execute": False,
            }

            intent = str(getattr(req, "decision_intent", "WINNER") or "WINNER").upper()
            if conflict and intent in _MARKET_RELATIVE_INTENTS:
                out["rank_eligible"] = False
                out["blockers"] = sorted(set([*(out.get("blockers") or []), "FAVORITE_STATUS_CONFLICT"]))
                out["market_role_status"] = "SOURCE_CONFLICT"
                # Sporting probability is deliberately preserved. The conflict
                # blocks market-relative publication/ranking only.
            out["can_execute"] = False
            return out

    team_event_module.score_team_event_request = score_with_rundown
    team_event_module._v17_llp_rundown_market_bridge_original = original
    team_event_module._v17_llp_rundown_market_bridge_installed = True
    return True


__all__ = [
    "BRIDGE_SOURCE",
    "CAN_EXECUTE",
    "enabled",
    "install_llp_rundown_market_bridge",
    "resolve_rundown_closing_context",
    "resolve_rundown_market_context",
]
