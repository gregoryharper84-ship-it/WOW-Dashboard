"""TheRundown -> LLP team/event market-context bridge.

TheRundown is evidence, never the controlling sporting-probability model. This
bridge refreshes the team/event request's existing market-prior contract before
LLP scoring so favorite/underdog/upset classification and any *certified* fitted
model market-prior component can consume current cross-book context.

Invariants:
- exactly one sport-specific LLP fitted specialist owns sporting probability;
- TheRundown implied/no-vig values are market evidence, not governed probability;
- provider failure cannot become MODEL_UNAVAILABLE or erase a completed sporting
  probability;
- material favorite-role disagreement blocks market-relative ranking, not the
  underlying sporting probability package;
- can_execute is always false.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from threading import RLock
from typing import Any

from v17 import market_evidence_sources as sources

CAN_EXECUTE = False
BRIDGE_SOURCE = "RUNDOWN_MARKET_EVIDENCE"
_BRIDGE_LOCK = RLock()

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

    if three_way and not pairs:
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


def resolve_rundown_market_context(req: Any, *, opener: Any = None) -> dict[str, Any]:
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
    result = sources.rundown_market_evidence(
        sport_key,
        str(getattr(req, "requested_slate_date", "")),
        capability="events",
        opener=opener,
    )
    if not result.ok:
        return {
            "status": "MARKET_DATA_UNOBTAINABLE",
            "provider": BRIDGE_SOURCE,
            "reason_code": result.code,
            "prediction_authority": False,
            "can_execute": False,
        }
    matches = [event for event in (result.data or []) if isinstance(event, dict) and _event_match(req, event)]
    if len(matches) != 1:
        return {
            "status": "EVENT_NOT_FOUND" if not matches else "EVENT_IDENTITY_AMBIGUOUS",
            "provider": BRIDGE_SOURCE,
            "match_count": len(matches),
            "prediction_authority": False,
            "can_execute": False,
        }
    return _market_from_event(req, matches[0])


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
    "resolve_rundown_market_context",
]
