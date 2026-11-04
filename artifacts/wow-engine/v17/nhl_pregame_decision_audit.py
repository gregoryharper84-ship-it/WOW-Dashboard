"""Evidence-only NHL pregame decision-quality audit for LLP Daily readback.

Nov 3 postmortem: evaluate two-sided goalie, injuries/replacements, rest and
matchup context before calling a selection high-confidence. This audits frozen
metadata only: it does not score, recalibrate, change a winner, promote a model,
validate executable prices, or authorize a wager.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Any, Mapping

CAN_EXECUTE = False
AUDIT_VERSION = "NHL_PREGAME_DECISION_QUALITY_V1"

GOALIE_MAX_AGE = timedelta(hours=4)
INJURY_MAX_AGE = timedelta(hours=24)
REST_MAX_AGE = timedelta(days=7)
MATCHUP_MAX_AGE = timedelta(hours=48)

_MATCHUP_FIELDS = {
    "home_elo": (500, 3000),
    "away_elo": (500, 3000),
    "home_goalie_sv_pct": (0.75, 1.0),
    "away_goalie_sv_pct": (0.75, 1.0),
    "home_pp_pct": (0, 1),
    "away_pp_pct": (0, 1),
    "home_pk_pct": (0, 1),
    "away_pk_pct": (0, 1),
    "home_home_win_pct": (0, 1),
    "away_away_win_pct": (0, 1),
}
_CONFIRMED = frozenset({"CONFIRMED", "CONFIRMED_STARTER", "OFFICIAL_STARTER"})


def _map(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _utc(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.utcoffset() is None:
            return None
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _fresh(value: Any, start: datetime | None, limit: timedelta) -> bool:
    observed = _utc(value)
    return bool(start and observed and timedelta(0) < start - observed <= limit)


def _valid_number(value: Any, bounds: tuple[float, float]) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return isfinite(number) and bounds[0] <= number <= bounds[1]


def _source_pinned(value: Mapping[str, Any]) -> bool:
    # A claimed source ID is *metadata*, not independent attestation.
    return bool(str(value.get("source_id") or "").strip()
                and str(value.get("source_snapshot_id") or "").strip())


def _goalies_ok(evidence: Mapping[str, Any], start: datetime | None) -> bool:
    goalies = _map(evidence.get("starting_goalies"))
    for side in ("home", "away"):
        goalie = _map(goalies.get(side))
        if (not str(goalie.get("goalie_id") or "").strip()
                or str(goalie.get("starting_status") or "").strip().upper() not in _CONFIRMED
                or not _source_pinned(goalie)
                or not _fresh(goalie.get("as_of"), start, GOALIE_MAX_AGE)):
            return False
    return str(_map(goalies.get("home")).get("goalie_id")) != str(
        _map(goalies.get("away")).get("goalie_id")
    )


def _injuries_ok(evidence: Mapping[str, Any], event: Mapping[str, Any],
                 start: datetime | None) -> bool:
    injuries = _map(evidence.get("injury_report"))
    teams = injuries.get("team_coverage")
    team_set = {str(t).strip().upper() for t in teams} if isinstance(teams, list) else set()
    expected = {str(event.get(side) or "").strip().upper()
                for side in ("home_team", "away_team")}
    replacements = _map(injuries.get("replacement_review"))
    return bool(
        injuries.get("status") == "CURRENT" and _source_pinned(injuries)
        and _fresh(injuries.get("as_of"), start, INJURY_MAX_AGE)
        and expected <= team_set and replacements.get("home") is True
        and replacements.get("away") is True
    )


def _rest_ok(evidence: Mapping[str, Any], start: datetime | None) -> tuple[bool, list[str]]:
    rest = _map(evidence.get("rest_travel"))
    if not (_source_pinned(rest) and _fresh(rest.get("as_of"), start, REST_MAX_AGE)):
        return False, []
    b2b: list[str] = []
    for side in ("home", "away"):
        item = _map(rest.get(side))
        days = item.get("rest_days")
        consecutive = item.get("back_to_back")
        if not (_valid_number(days, (0, 14)) and isinstance(consecutive, bool)):
            return False, []
        if consecutive and days != 0:
            return False, []
        if consecutive:
            b2b.append(side.upper())
    return True, b2b


def _matchup_ok(evidence: Mapping[str, Any], start: datetime | None) -> bool:
    matchup = _map(evidence.get("matchup_context"))
    return bool(_source_pinned(matchup)
                and _fresh(matchup.get("as_of"), start, MATCHUP_MAX_AGE)
                and all(_valid_number(matchup.get(key), bounds)
                        for key, bounds in _MATCHUP_FIELDS.items()))


def audit_nhl_pregame_decision(
    *, event: Mapping[str, Any], row: Mapping[str, Any],
    bundle: Mapping[str, Any],
) -> dict[str, Any]:
    """Add non-authoritative decision-quality context to an existing NHL page.

    Upstream winner certification is read, never reconstructed. Caller-supplied
    odds, popularity, external forecast and arbitrary positive-EV flags are
    ignored: an independent verified price/edge route is required for value.
    """
    if str(event.get("sport") or "").upper() != "NHL":
        raise ValueError("NHL_DECISION_AUDIT_WRONG_SPORT")
    detail = _map(row.get("detail"))
    result = _map(detail.get("result"))
    evidence = _map(result.get("sport_specific_evidence"))
    if not evidence:
        evidence = _map(detail.get("sport_specific_evidence"))
    start = _utc(event.get("event_start_time_utc"))

    blockers: list[str] = []
    if not _goalies_ok(evidence, start):
        blockers.append("NHL_STARTING_GOALIES_NOT_CONFIRMED_AND_SOURCE_PINNED")
    if not _injuries_ok(evidence, event, start):
        blockers.append("NHL_INJURY_AND_REPLACEMENT_REVIEW_INCOMPLETE")
    rest_ok, back_to_back_sides = _rest_ok(evidence, start)
    if not rest_ok:
        blockers.append("NHL_REST_BACK_TO_BACK_CONTEXT_UNVERIFIED")
    if not _matchup_ok(evidence, start):
        blockers.append("NHL_TWO_SIDED_MATCHUP_CONTEXT_INCOMPLETE")

    winner = next(
        (market for market in (bundle.get("markets") or [])
         if isinstance(market, Mapping) and market.get("market_family") == "OUTRIGHT_WINNER"
         and market.get("status") == "GOVERNED_RECEIPT_VISIBLE"
         and market.get("evidence_visible") is True),
        None,
    )
    # This readback cannot attest to an executable sportsbook price, vig, fees,
    # or edge. It must not assert a value advantage even with a fitted winner.
    return {
        "contract": AUDIT_VERSION,
        "status": "STRUCTURED_PREGAME_CONTEXT_PRESENT_NOT_CERTIFIED"
                  if not blockers else "PREGAME_CONTEXT_INCOMPLETE",
        "context_blockers": blockers,
        "back_to_back_sides": back_to_back_sides,
        "winner_receipt_status": "GOVERNED_RECEIPT_PRESENT"
                                 if winner else "NO_CERTIFIED_FITTED_WINNER_RECEIPT",
        "value_status": "INDEPENDENT_PRICE_AND_EDGE_VERIFICATION_REQUIRED",
        "value_claim_allowed": False,
        "confidence_ranking_generated": False,
        "postgame_information_used": False,
        "probability_modified": False,
        "can_execute": False,
    }


__all__ = ["AUDIT_VERSION", "CAN_EXECUTE", "audit_nhl_pregame_decision"]
