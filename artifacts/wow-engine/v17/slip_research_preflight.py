"""Pregame research-only gate for cross-sport multi-market slip proposals.

Additive to slip_portfolio_optimizer; never scores a sporting probability, infers
a parlay joint probability, changes terminal V17 state, or places an order.
The candidate/quote/news fields below are *untrusted caller-supplied claims*.
Passing their shape checks is not independent verification or certification.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from math import isfinite
from typing import Any, Mapping, Sequence

CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
RESEARCH_CLASS = "RESEARCH_ONLY_UNCERTIFIED"
_TOTALS = {"GAME_TOTAL", "TEAM_TOTAL", "TOTAL", "OVER_UNDER"}
_PROPS = {"PLAYER_PROP", "PLAYER_POINTS", "PLAYER_REBOUNDS", "PLAYER_ASSISTS", "PROP"}
_STANDARD_MAX_LEGS = 3


def _time(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value.astimezone(timezone.utc) if value.tzinfo is not None and value.utcoffset() is not None else None


def _text(x: Any) -> str:
    return x.strip() if isinstance(x, str) else ""


def _number(value: Any, *, minimum: float | None = None) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if isfinite(result) and (minimum is None or result >= minimum) else None


def _line_key(raw: Any) -> str:
    num = _number(raw)
    return str(num) if num is not None else ""


def exact_key(leg: Mapping[str, Any]) -> tuple[str, ...] | None:
    """Bind cross-sport, exact event/market/period/side/line/player thesis."""
    sport = _text(leg.get("sport")).upper()
    event = _text(leg.get("official_event_id"))
    market = _text(leg.get("market_family")).upper()
    side = _text(leg.get("side")).upper()
    period = _text(leg.get("market_period")).upper()
    if not all((sport, event, market, side, period)):
        return None
    return (sport, event, market, period, side,
            _line_key(leg.get("exact_line")),
            _text(leg.get("official_player_id")).casefold(),
            _text(leg.get("settlement_basis")).upper())


def _quote_blockers(leg: Mapping[str, Any], as_of: datetime, start: datetime) -> list[str]:
    quote = leg.get("market_quote")
    if not isinstance(quote, Mapping):
        return ["EXACT_MARKET_QUOTE_MISSING"]
    for field in ("sport", "official_event_id", "market_family", "side", "market_period", "settlement_basis"):
        if _text(quote.get(field)).upper() != _text(leg.get(field)).upper():
            return ["EXACT_MARKET_QUOTE_IDENTITY_MISMATCH"]
    if _line_key(quote.get("exact_line")) != _line_key(leg.get("exact_line")):
        return ["EXACT_MARKET_QUOTE_LINE_MISMATCH"]
    qtime = _time(quote.get("quoted_at_utc"))
    max_age = _number(leg.get("max_quote_age_minutes"), minimum=0)
    if qtime is None or max_age is None or qtime > as_of or qtime >= start:
        return ["MARKET_QUOTE_CHRONOLOGY_UNVERIFIED"]
    if (as_of - qtime).total_seconds() / 60 > max_age:
        return ["MARKET_QUOTE_STALE"]
    if not _text(quote.get("quote_source_id")) or not _text(quote.get("bookmaker")):
        return ["MARKET_QUOTE_PROVENANCE_MISSING"]
    american = quote.get("american_odds")
    decimal = quote.get("decimal_odds")
    if ((type(american) is not int or (american > -100 and american < 100))
            and (_number(decimal, minimum=1.00000001) is None)):
        return ["MARKET_QUOTE_PRICE_UNVERIFIED"]
    return []


def _total_blockers(leg: Mapping[str, Any], as_of: datetime, start: datetime) -> list[str]:
    details = leg.get("total_evidence")
    if not isinstance(details, Mapping):
        return ["TOTAL_CONTEXT_MISSING", "TOTAL_DISTRIBUTION_UNVERIFIED"]
    fields = ("source_receipt_id", "opponent_efficiency_source_id",
              "lineup_scenario_source_id", "rest_travel_source_id",
              "tail_scenario_source_id")
    missing = [k for k in fields if not _text(details.get(k))]
    sport = _text(leg.get("sport")).upper()
    basketball = sport in {
        "NBA", "NCAAB", "WNBA", "BASKETBALL_NBA", "BASKETBALL_NCAAB",
        "BASKETBALL_WNBA",
    }
    if basketball:
        for key in ("projected_possessions", "home_adjusted_ppp", "away_adjusted_ppp"):
            if _number(details.get(key), minimum=0.000001) is None:
                missing.append(key)
    else:
        # NHL goals, NFL points, soccer goals, baseball runs, etc. must use
        # their *own* scoring process. No basketball possessions x PPP bridge.
        if not _text(details.get("sport_score_process_source_id")):
            missing.append("sport_score_process_source_id")
        if _number(details.get("projected_total_score"), minimum=0) is None:
            missing.append("projected_total_score")
    seen = _time(details.get("context_observed_at_utc"))
    if (seen is None or seen > as_of or seen >= start
            or _text(details.get("sport")).upper() != _text(leg.get("sport")).upper()
            or _text(details.get("official_event_id")) != _text(leg.get("official_event_id"))):
        missing.append("context_timestamp_or_identity")
    # Directional sanity only: independently fitted totals probabilities are
    # still required. Mean versus line is NOT a calibrated tail probability.
    line = _number(leg.get("exact_line"), minimum=0)
    side = _text(leg.get("side")).upper()
    estimate = None
    if basketball:
        possessions = _number(details.get("projected_possessions"), minimum=0.000001)
        home_ppp = _number(details.get("home_adjusted_ppp"), minimum=0.000001)
        away_ppp = _number(details.get("away_adjusted_ppp"), minimum=0.000001)
        if all(x is not None for x in (possessions, home_ppp, away_ppp)):
            estimate = possessions * (home_ppp + away_ppp)
    else:
        estimate = _number(details.get("projected_total_score"), minimum=0)
    if estimate is not None and line is not None:
        if ((side == "OVER" and estimate <= line)
                or (side == "UNDER" and estimate >= line)):
            missing.append("point_estimate_opposes_selected_side")
    # No marginal-distribution numbers or "certified" flags are fabricated here.
    distribution = details.get("fitted_total_distribution_receipt")
    dist_ok = (
        isinstance(distribution, Mapping)
        and bool(_text(distribution.get("immutable_pregame_receipt_id")))
        and bool(_text(distribution.get("fitted_model_hash")))
        and bool(_text(distribution.get("calibration_receipt_id")))
        and (_time(distribution.get("frozen_at_utc")) is not None)
        and _time(distribution.get("frozen_at_utc")) <= as_of
        and _time(distribution.get("frozen_at_utc")) < start
        and _text(distribution.get("sport")).upper() == _text(leg.get("sport")).upper()
        and _text(distribution.get("official_event_id")) == _text(leg.get("official_event_id"))
        and _line_key(distribution.get("exact_line")) == _line_key(leg.get("exact_line"))
        and _text(distribution.get("side")).upper() == _text(leg.get("side")).upper()
    )
    return (
        (["TOTAL_CONTEXT_MISSING"] if any(x != "point_estimate_opposes_selected_side" for x in missing) else [])
        + (["TOTAL_POINT_ESTIMATE_CONTRADICTS_SIDE"] if "point_estimate_opposes_selected_side" in missing else [])
        + (["TOTAL_DISTRIBUTION_UNVERIFIED"] if not dist_ok else [])
    )


def _player_blockers(leg: Mapping[str, Any], as_of: datetime, start: datetime) -> list[str]:
    if not _text(leg.get("official_player_id")):
        return ["PROP_PLAYER_ID_UNVERIFIED"]
    roster = leg.get("player_roster_evidence")
    if not isinstance(roster, Mapping):
        return ["PROP_ROSTER_EVIDENCE_MISSING"]
    seen = _time(roster.get("observed_at_utc"))
    if (seen is None or seen > as_of or seen >= start
            or not _text(roster.get("official_source_id"))
            or _text(roster.get("official_player_id")) != _text(leg.get("official_player_id"))
            or _text(roster.get("sport")).upper() != _text(leg.get("sport")).upper()
            or _text(roster.get("official_event_id")) != _text(leg.get("official_event_id"))
            or not _text(roster.get("player_team_id"))
            or _text(roster.get("player_team_id")) not in {
                _text(leg.get("home_team_id")), _text(leg.get("away_team_id"))
            }):
        return ["PROP_ROSTER_IDENTITY_OR_FRESHNESS_UNVERIFIED"]
    return []


def _news_blockers(leg: Mapping[str, Any], as_of: datetime, start: datetime) -> list[str]:
    news = leg.get("news_evidence")
    if not isinstance(news, Mapping):
        return ["NEWS_PREGAME_EVIDENCE_MISSING"]
    seen = _time(news.get("last_reviewed_at_utc"))
    if (seen is None or seen > as_of or seen >= start
            or not _text(news.get("independent_review_receipt_id"))
            or not _text(news.get("official_source_id"))
            or _text(news.get("sport")).upper() != _text(leg.get("sport")).upper()
            or _text(news.get("official_event_id")) != _text(leg.get("official_event_id"))):
        return ["NEWS_PREGAME_VERIFICATION_UNVERIFIED"]
    if news.get("material_change") is True and not _text(news.get("new_model_receipt_id")):
        return ["MATERIAL_NEWS_RESCORE_NOT_VERIFIED"]
    return []


def audit_research_slips(
    slips: Sequence[Mapping[str, Any]],
    *,
    as_of_utc: str,
    max_distinct_slips_per_leg: int = 1,
    max_standard_legs: int = _STANDARD_MAX_LEGS,
) -> dict[str, Any]:
    """Evaluate all proposed slips as one portfolio; input remains unchanged.

    No fields asserted by this audit are treated as externally authenticated.
    Missing research evidence is a HOLD; complete supplied evidence means
    'reviewable' only, never platform value, calibrated confidence, or execution.
    """
    at = _time(as_of_utc)
    if at is None:
        raise ValueError("RESEARCH_PREFLIGHT_AS_OF_INVALID")
    if max_distinct_slips_per_leg < 1 or max_standard_legs < 2:
        raise ValueError("RESEARCH_PREFLIGHT_LIMIT_INVALID")
    seen_exact: dict[tuple[str, ...], set[str]] = {}
    reports: list[dict[str, Any]] = []
    for idx, slip in enumerate(slips):
        if not isinstance(slip, Mapping):
            raise ValueError("RESEARCH_PREFLIGHT_SLIP_INVALID")
        slip_id = _text(slip.get("card_id")) or str(idx)
        legs = slip.get("legs")
        if not isinstance(legs, list):
            legs = []
        blockers = []
        if len(legs) < 2:
            blockers.append("RESEARCH_SLIP_TOO_FEW_LEGS")
        if len(legs) > max_standard_legs:
            blockers.append("LONG_SLIP_JOINT_PROBABILITY_UNVERIFIED")
        event_counts: Counter = Counter()
        leg_reports = []
        for i, leg in enumerate(legs):
            if not isinstance(leg, Mapping):
                leg_reports.append({"index": i, "blockers": ["RESEARCH_LEG_INVALID"]})
                blockers.append("RESEARCH_LEG_INVALID")
                continue
            reasons: list[str] = []
            identity = exact_key(leg)
            start = _time(leg.get("event_start_utc"))
            snapshot = _time(leg.get("source_snapshot_at_utc"))
            if identity is None or not _text(leg.get("source_snapshot_receipt_id")):
                reasons.append("CANONICAL_PREGAME_SOURCE_IDENTITY_MISSING")
            if start is None or snapshot is None or not snapshot <= at < start:
                reasons.append("PREGAME_CHRONOLOGY_UNVERIFIED")
            if _text(leg.get("market_family")).upper() not in _TOTALS | _PROPS | {"MONEYLINE", "ML", "POINT_SPREAD", "SPREAD"}:
                reasons.append("MARKET_FAMILY_UNSUPPORTED")
            market = _text(leg.get("market_family")).upper()
            if market in _TOTALS | _PROPS | {"POINT_SPREAD", "SPREAD"} and _number(leg.get("exact_line")) is None:
                reasons.append("EXACT_LINE_MISSING")
            if not _text(leg.get("settlement_basis")):
                reasons.append("EXACT_SETTLEMENT_RULE_MISSING")
            if start is not None:
                reasons.extend(_quote_blockers(leg, at, start))
                reasons.extend(_news_blockers(leg, at, start))
                if market in _TOTALS:
                    reasons.extend(_total_blockers(leg, at, start))
                if market in _PROPS:
                    reasons.extend(_player_blockers(leg, at, start))
            if identity:
                previous = seen_exact.setdefault(identity, set())
                if any(x != slip_id for x in previous) and len(previous) >= max_distinct_slips_per_leg:
                    reasons.append("CROSS_SLIP_DUPLICATE_THESIS_EXPOSURE")
                previous.add(slip_id)
                if identity[0] and identity[1]:
                    event_counts[(identity[0], identity[1])] += 1
            reasons = list(dict.fromkeys(reasons))
            leg_reports.append({"index": i, "row_id": leg.get("row_id"),
                                "blockers": reasons, "research_only": True,
                                "quote_independently_authenticated": False,
                                "news_independently_authenticated": False,
                                "model_probability_verified": False})
            blockers.extend(reasons)
        if any(x > 1 for x in event_counts.values()):
            blockers.append("SAME_EVENT_JOINT_DEPENDENCE_UNVERIFIED")
        if len({exact_key(l) for l in legs if isinstance(l, Mapping)}) < len(legs):
            blockers.append("INTRA_SLIP_DUPLICATE_THESIS")
        blockers = sorted(set(blockers))
        reports.append({
            "card_id": slip_id,
            "status": "RESEARCH_HOLD" if blockers else "RESEARCH_CONTEXT_REVIEWABLE_UNCERTIFIED",
            "blockers": blockers,
            "legs": leg_reports,
            "can_publish_as_certified": False,
            "model_rescore_verified": False,
            "joint_probability": None,
            "joint_probability_certified": False,
            "executable_quote_verified": False,
            "can_execute": False,
        })
    return {
        "schema_version": "wow.v17.slip_research_preflight.v1",
        "status": "RESEARCH_HOLD" if any(r["blockers"] for r in reports) else "RESEARCH_CONTEXT_REVIEWABLE_UNCERTIFIED",
        "as_of_utc": at.isoformat(),
        "cards": reports,
        "cross_slip_exact_exposure_counts": {
            "|".join(key): len(value) for key, value in sorted(seen_exact.items())
        },
        "research_only": True,
        "fitted_model_changed": False,
        "calibration_changed": False,
        "certified_sporting_probability": None,
        "joint_probability": None,
        "terminal_authority": TERMINAL_AUTHORITY,
        "prediction_authority": False,
        "can_execute": False,
    }
