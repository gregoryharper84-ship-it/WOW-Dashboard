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
_MARKET_ALIASES = {
    "ML": "MONEYLINE", "MONEYLINE": "MONEYLINE",
    "SPREAD": "POINT_SPREAD", "POINT_SPREAD": "POINT_SPREAD",
    "TOTAL": "GAME_TOTAL", "OVER_UNDER": "GAME_TOTAL", "GAME_TOTAL": "GAME_TOTAL",
    "PROP": "PLAYER_PROP", "PLAYER_PROP": "PLAYER_PROP",
    "PLAYER_POINTS": "PLAYER_PROP", "PLAYER_REBOUNDS": "PLAYER_PROP",
    "PLAYER_ASSISTS": "PLAYER_PROP",
}


def _market(raw: Any) -> str:
    key = _text(raw).upper()
    return _MARKET_ALIASES.get(key, key)



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
    market = _market(leg.get("market_family"))
    side = _text(leg.get("side")).upper()
    period = _text(leg.get("market_period")).upper()
    if not all((sport, event, market, side, period)):
        return None
    return (sport, event, market, period, side,
            _line_key(leg.get("exact_line")),
            _text(leg.get("official_player_id")).casefold(),
            _text(leg.get("stat_type")).upper(),
            _text(leg.get("selected_team_id")).upper(),
            _text(leg.get("settlement_basis")).upper())


def _fixture_blockers(leg: Mapping[str, Any], as_of: datetime, start: datetime) -> list[str]:
    """Screen caller-supplied official fixture provenance, without authenticating it.

    A sportsbook quote, player matchup narrative or yesterday's fixture cannot
    supply the required scheduled official event for this research card.
    """
    fixture = leg.get("official_fixture_evidence")
    if not isinstance(fixture, Mapping):
        return ["OFFICIAL_FIXTURE_EVIDENCE_MISSING"]
    observed = _time(fixture.get("verified_at_utc"))
    scheduled = _time(fixture.get("event_start_utc"))
    if (not _text(fixture.get("official_source_id"))
            or not _text(fixture.get("manifest_receipt_id"))
            or observed is None or observed > as_of or observed >= start
            or scheduled != start
            or _text(fixture.get("sport")).upper() != _text(leg.get("sport")).upper()
            or _text(fixture.get("official_event_id")) != _text(leg.get("official_event_id"))
            or any(_text(fixture.get(k)) != _text(leg.get(k))
                   for k in ("home_team_id", "away_team_id")
                   if _text(leg.get(k)))):
        return ["OFFICIAL_FIXTURE_IDENTITY_OR_CHRONOLOGY_UNVERIFIED"]
    if _text(fixture.get("event_status")).upper() not in {"SCHEDULED", "NOT_STARTED"}:
        return ["OFFICIAL_FIXTURE_NOT_SCHEDULED"]
    return []


def _price_edge_blockers(leg: Mapping[str, Any], as_of: datetime, start: datetime) -> list[str]:
    """Contradiction screening only; this cannot authorize a positive-EV claim."""
    value = leg.get("research_value_evidence")
    asserted = _text(leg.get("value_claim")).upper() in {"POSITIVE_VALUE", "STRONG_VALUE", "ELITE_VALUE"}
    if not isinstance(value, Mapping):
        return ["RESEARCH_VALUE_CLAIM_UNSUPPORTED"] if asserted else []
    recorded = _time(value.get("observed_at_utc"))
    p = _number(value.get("estimated_selection_probability"), minimum=0)
    if (recorded is None or recorded > as_of or recorded >= start
            or not _text(value.get("source_id")) or p is None or p > 1
            or _text(value.get("sport")).upper() != _text(leg.get("sport")).upper()
            or _text(value.get("official_event_id")) != _text(leg.get("official_event_id"))
            or _market(value.get("market_family")) != _market(leg.get("market_family"))
            or _text(value.get("market_period")).upper() != _text(leg.get("market_period")).upper()
            or _text(value.get("side")).upper() != _text(leg.get("side")).upper()
            or _text(value.get("settlement_basis")).upper() != _text(leg.get("settlement_basis")).upper()
            or _line_key(value.get("exact_line")) != _line_key(leg.get("exact_line"))):
        return ["RESEARCH_VALUE_EVIDENCE_UNVERIFIED"]
    # Research forecasts must bind to the *same* market selection, not just
    # the same game/line. Otherwise another player's stat or the other team's
    # total can be laundered into an apparently favorable quoted edge.
    market = _market(leg.get("market_family"))
    if market in _PROPS and (
            not _text(leg.get("official_player_id"))
            or not _text(leg.get("stat_type"))
            or _text(value.get("official_player_id")) != _text(leg.get("official_player_id"))
            or _text(value.get("stat_type")).upper() != _text(leg.get("stat_type")).upper()):
        return ["RESEARCH_VALUE_PLAYER_STAT_MISMATCH"]
    if market == "TEAM_TOTAL" and (
            not _text(leg.get("selected_team_id"))
            or _text(value.get("selected_team_id")) != _text(leg.get("selected_team_id"))):
        return ["RESEARCH_VALUE_TEAM_MISMATCH"]
    quote = leg.get("market_quote")
    if not isinstance(quote, Mapping):
        return ["RESEARCH_VALUE_PRICE_UNVERIFIED"]
    decimal = _number(quote.get("decimal_odds"), minimum=1.00000001)
    if decimal is None:
        american = quote.get("american_odds")
        if type(american) is not int or -100 < american < 100:
            return ["RESEARCH_VALUE_PRICE_UNVERIFIED"]
        decimal = 1 + (100 / -american if american < 0 else american / 100)
    # Even a caller-claimed forecast greater than market breakeven is not
    # independently authenticated or a calibrated sporting probability.
    if p <= 1 / decimal:
        return ["RESEARCH_PRICE_EDGE_NOT_SUPPORTED"]
    return []


def _quote_blockers(leg: Mapping[str, Any], as_of: datetime, start: datetime) -> list[str]:
    quote = leg.get("market_quote")
    if not isinstance(quote, Mapping):
        return ["EXACT_MARKET_QUOTE_MISSING"]
    for field in ("sport", "official_event_id", "side", "market_period", "settlement_basis"):
        if _text(quote.get(field)).upper() != _text(leg.get(field)).upper():
            return ["EXACT_MARKET_QUOTE_IDENTITY_MISMATCH"]
    market = _market(leg.get("market_family"))
    if _market(quote.get("market_family")) != market:
        return ["EXACT_MARKET_QUOTE_IDENTITY_MISMATCH"]
    if market in _PROPS:
        if not _text(leg.get("official_player_id")) or not _text(leg.get("stat_type")):
            return ["PROP_PLAYER_OR_STAT_ID_MISSING"]
        if (_text(quote.get("official_player_id")) != _text(leg.get("official_player_id"))
                or _text(quote.get("stat_type")).upper() != _text(leg.get("stat_type")).upper()):
            return ["PROP_QUOTE_PLAYER_STAT_MISMATCH"]
    if market == "TEAM_TOTAL":
        if (not _text(leg.get("selected_team_id"))
                or _text(quote.get("selected_team_id")) != _text(leg.get("selected_team_id"))):
            return ["TEAM_TOTAL_QUOTE_TEAM_MISMATCH"]
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
    is_team_total = _market(leg.get("market_family")) == "TEAM_TOTAL"
    team = _text(leg.get("selected_team_id"))
    if is_team_total and (not team or team not in {
            _text(leg.get("home_team_id")), _text(leg.get("away_team_id"))}):
        missing.append("selected_team_id")
    if is_team_total and _text(details.get("selected_team_id")) != team:
        missing.append("team_score_process_binding")
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
        expected_key = "team_projected_score" if is_team_total else "projected_total_score"
        if _number(details.get(expected_key), minimum=0) is None:
            missing.append(expected_key)
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
            if is_team_total:
                estimate = possessions * (home_ppp if team == _text(leg.get("home_team_id"))
                                          else away_ppp) if team else None
            else:
                estimate = possessions * (home_ppp + away_ppp)
    else:
        estimate = _number(
            details.get("team_projected_score" if is_team_total else "projected_total_score"),
            minimum=0,
        )
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
        and (not is_team_total or _text(distribution.get("selected_team_id")) == team)
    )
    return (
        (["TOTAL_CONTEXT_MISSING"] if any(x != "point_estimate_opposes_selected_side" for x in missing) else [])
        + (["TOTAL_POINT_ESTIMATE_CONTRADICTS_SIDE"] if "point_estimate_opposes_selected_side" in missing else [])
        + (["TOTAL_DISTRIBUTION_UNVERIFIED"] if not dist_ok else [])
    )


def _prop_context_blockers(leg: Mapping[str, Any], as_of: datetime, start: datetime) -> list[str]:
    """Require sourced opportunity, matchup, game-script and tail context for props."""
    evidence = leg.get("prop_matchup_evidence")
    if not isinstance(evidence, Mapping):
        return ["PROP_MATCHUP_CONTEXT_MISSING"]
    required = ("source_receipt_id", "opponent_efficiency_source_id",
                "role_minutes_source_id", "usage_source_id",
                "pace_game_script_source_id", "tail_scenario_source_id")
    observed = _time(evidence.get("observed_at_utc"))
    if (any(not _text(evidence.get(k)) for k in required)
            or observed is None or observed > as_of or observed >= start
            or _text(evidence.get("sport")).upper() != _text(leg.get("sport")).upper()
            or _text(evidence.get("official_event_id")) != _text(leg.get("official_event_id"))
            or _text(evidence.get("official_player_id")) != _text(leg.get("official_player_id"))
            or _text(evidence.get("stat_type")).upper() != _text(leg.get("stat_type")).upper()):
        return ["PROP_MATCHUP_CONTEXT_UNVERIFIED"]
    return []


def _player_blockers(leg: Mapping[str, Any], as_of: datetime, start: datetime) -> list[str]:
    if not _text(leg.get("official_player_id")):
        return ["PROP_PLAYER_ID_UNVERIFIED"]
    if not _text(leg.get("stat_type")):
        return ["PROP_STAT_ID_UNVERIFIED"]
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
    max_distinct_slips_per_event: int = 2,
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
    if max_distinct_slips_per_leg < 1 or max_distinct_slips_per_event < 1 or max_standard_legs < 2:
        raise ValueError("RESEARCH_PREFLIGHT_LIMIT_INVALID")
    seen_exact: dict[tuple[str, ...], set[str]] = {}
    seen_events: dict[tuple[str, str], set[str]] = {}
    seen_card_ids: set[str] = set()
    reports: list[dict[str, Any]] = []
    for idx, slip in enumerate(slips):
        if not isinstance(slip, Mapping):
            raise ValueError("RESEARCH_PREFLIGHT_SLIP_INVALID")
        slip_id = _text(slip.get("card_id")) or str(idx)
        legs = slip.get("legs")
        if not isinstance(legs, list):
            legs = []
        blockers = []
        if slip_id in seen_card_ids:
            blockers.append("RESEARCH_CARD_ID_DUPLICATE")
        seen_card_ids.add(slip_id)
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
            market = _market(leg.get("market_family"))
            if market in _TOTALS | _PROPS | {"POINT_SPREAD", "SPREAD"} and _number(leg.get("exact_line")) is None:
                reasons.append("EXACT_LINE_MISSING")
            if not _text(leg.get("settlement_basis")):
                reasons.append("EXACT_SETTLEMENT_RULE_MISSING")
            if start is not None:
                reasons.extend(_fixture_blockers(leg, at, start))
                reasons.extend(_quote_blockers(leg, at, start))
                reasons.extend(_price_edge_blockers(leg, at, start))
                reasons.extend(_news_blockers(leg, at, start))
                if market in _TOTALS:
                    reasons.extend(_total_blockers(leg, at, start))
                if market in _PROPS:
                    reasons.extend(_player_blockers(leg, at, start))
                    reasons.extend(_prop_context_blockers(leg, at, start))
            if identity:
                previous = seen_exact.setdefault(identity, set())
                if any(x != slip_id for x in previous) and len(previous) >= max_distinct_slips_per_leg:
                    reasons.append("CROSS_SLIP_DUPLICATE_THESIS_EXPOSURE")
                previous.add(slip_id)
                if identity[0] and identity[1]:
                    event_key = (identity[0], identity[1])
                    prior_cards = seen_events.setdefault(event_key, set())
                    if slip_id not in prior_cards and len(prior_cards) >= max_distinct_slips_per_event:
                        reasons.append("CROSS_SLIP_EVENT_HINGE_CONCENTRATION")
                    prior_cards.add(slip_id)
                    event_counts[event_key] += 1
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
        "cross_slip_event_exposure_counts": {
            "|".join(key): len(value) for key, value in sorted(seen_events.items())
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
