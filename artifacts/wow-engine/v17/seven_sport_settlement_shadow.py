"""Read-only sport settlement adapter for existing V17 aligned joint-score shadows.

Only interprets caller-supplied, untrusted research distributions. It does not
fit, simulate, recalibrate, certify, rank or publish a sporting probability.
The numerical source is joint_game_state_shadow.project_joint_shadow;
this adapter adds sport/period/tie/line and tennis-winner guardrails.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isclose, isfinite
from re import fullmatch
from typing import Any, Mapping

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_CERTIFICATION = False
CONTROLLING_ENGINE = "LLP_TEAM_BETTING_ENGINE"
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
CONTRACT = "LLP_SEVEN_SPORT_SETTLEMENT_SHADOW_RESEARCH_V1"

_BASIS = {
    "NFL": "FULL_GAME_INCLUDING_OVERTIME",
    "NCAAF": "FULL_GAME_INCLUDING_OVERTIME",
    "NBA": "FULL_GAME_INCLUDING_OVERTIME",
    "NCAAB": "FULL_GAME_INCLUDING_OVERTIME",
    "NHL": "FULL_GAME_INCLUDING_OVERTIME_SHOOTOUT",
    "SOCCER": "REGULATION",
    "TENNIS": "COMPLETED_MATCH_NO_RETIREMENT",
}


def _probability(value: Any) -> float:
    if type(value) not in (float, int) or not 0 <= value <= 1:
        raise ValueError("PROBABILITY_INVALID")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError("PROBABILITY_INVALID") from exc
    if not isfinite(number):
        raise ValueError("PROBABILITY_INVALID")
    return number


def _utc(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("PREGAME_CAPTURE_INVALID")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.utcoffset() is None:
            raise ValueError("missing timezone")
        return stamp.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("PREGAME_CAPTURE_INVALID") from exc


def _valid_line(value: Any, *, soccer: bool) -> float:
    if type(value) not in (float, int) or not -2000 <= value <= 2000:
        raise ValueError("EXACT_LINE_INVALID")
    result = float(value)
    if not isfinite(result):
        raise ValueError("EXACT_LINE_INVALID")
    scale = 4 if soccer else 2
    if abs(result * scale - round(result * scale)) > 1e-9:
        raise ValueError("EXACT_LINE_TICK_INVALID")
    return result


def _distribution(part: Any, *, labels: tuple[str, ...]) -> dict[str, float]:
    if not isinstance(part, Mapping) or any(label not in part for label in labels):
        raise ValueError("PROBABILITY_INVALID")
    values = {label: _probability(part[label]) for label in labels}
    if not isclose(sum(values.values()), 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("DISTRIBUTION_NOT_NORMALIZED")
    return values


def settle_seven_sport_shadow(
    *,
    sport: str,
    settlement_basis: str,
    projection: Mapping[str, Any],
    score_unit: str | None = None,
) -> dict[str, Any]:
    """Interpret one existing aligned-draw research summary without promotion.

    A claimed SHA is not independently verified fitted-model certification.
    Soccer quarter lines need underlying score draws for half outcomes;
    aggregate more/exact/less cannot reconstruct joint mass.
    """
    if not isinstance(sport, str) or sport.upper() not in _BASIS:
        raise ValueError("SPORT_UNSUPPORTED")
    sport = sport.upper()
    if settlement_basis != _BASIS[sport]:
        raise ValueError("SPORT_SETTLEMENT_BASIS_MISMATCH")
    if sport == "TENNIS":
        if score_unit != "MATCH_GAMES":
            raise ValueError("TENNIS_SCORE_UNIT_INVALID")
    elif score_unit not in (None, "FINAL_SCORE"):
        raise ValueError("SCORE_UNIT_INVALID")
    if not isinstance(projection, Mapping):
        raise ValueError("SOURCE_PROVENANCE_MISSING")
    # Bind the adapter to the source projector’s explicit sport/period/unit
    # claim. These are structural research claims, not authenticated receipts.
    required_source_unit = "MATCH_GAMES" if sport == "TENNIS" else "FINAL_SCORE"
    if (projection.get("source_sport") != sport
            or projection.get("source_settlement_basis") != settlement_basis
            or projection.get("source_score_unit") != required_source_unit):
        raise ValueError("SOURCE_SPORT_OR_SETTLEMENT_MISMATCH")
    if (projection.get("probability_publishable") is not False
            or projection.get("automatic_certification") is not False
            or projection.get("shadow_only") is not True):
        raise ValueError("UNAUTHORIZED_PROBABILITY_CLAIM")
    if projection.get("can_execute") is not False:
        raise ValueError("EXECUTION_INVARIANT_VIOLATION")
    if projection.get("terminal_authority") != TERMINAL_AUTHORITY:
        raise ValueError("TERMINAL_AUTHORITY_MISMATCH")
    if (not isinstance(projection.get("event_id"), str)
            or not projection["event_id"].strip()
            or not isinstance(projection.get("fitted_source_model_id"), str)
            or not projection["fitted_source_model_id"].strip()
            or not isinstance(projection.get("fitted_artifact_sha256"), str)
            or fullmatch(r"[0-9a-f]{64}", projection["fitted_artifact_sha256"]) is None):
        raise ValueError("SOURCE_PROVENANCE_MISSING")
    if (type(projection.get("draw_count")) is not int
            or projection["draw_count"] < 50_000):
        raise ValueError("DRAW_COUNT_INVALID")
    if _utc(projection.get("captured_at")) >= _utc(projection.get("event_start_at")):
        raise ValueError("PREGAME_CAPTURE_INVALID")

    money = _distribution(projection.get("moneyline"), labels=("home_win", "away_win", "draw"))
    if sport in {"NBA", "NCAAB", "NHL", "NCAAF"}:
        tie_rule = "NO_TIES"
    elif sport == "SOCCER":
        tie_rule = "THREE_WAY"
    elif sport == "NFL":
        tie_rule = "PUSH" if projection.get("moneyline_tie_rule") == "PUSH" else "THREE_WAY"
    else:
        tie_rule = "NO_WINNER_FROM_GAME_COUNTS"
    if sport != "TENNIS":
        if (projection.get("moneyline_tie_rule") != tie_rule
                or (tie_rule == "NO_TIES" and money["draw"] != 0)):
            raise ValueError("TIE_RULE_OR_DRAW_MASS_INVALID")

    spread = _distribution(projection.get("home_spread"), labels=("more", "exact", "less"))
    total = _distribution(projection.get("game_total"), labels=("more", "exact", "less"))
    soccer = sport == "SOCCER"
    spread_line = _valid_line(projection["home_spread"].get("line"), soccer=soccer)
    total_line = _valid_line(projection["game_total"].get("line"), soccer=soccer)
    if total_line < 0:
        raise ValueError("EXACT_LINE_INVALID")
    if soccer and (round(spread_line * 4) % 2 or round(total_line * 4) % 2):
        raise ValueError("ASIAN_QUARTER_REQUIRES_JOINT_DRAWS")
    if ((round(spread_line * 2) % 2 and spread["exact"] > 1e-12)
            or (round(total_line * 2) % 2 and total["exact"] > 1e-12)):
        raise ValueError("HALF_POINT_PUSH_MASS_INVALID")

    if sport != "TENNIS" and spread_line == 0 and not all(
        isclose(spread[key], money[comparison], rel_tol=0.0, abs_tol=1e-9)
        for key, comparison in (("more", "home_win"), ("exact", "draw"), ("less", "away_win"))
    ):
        raise ValueError("ZERO_HANDICAP_WINNER_SPREAD_MISMATCH")

    winner: dict[str, float | str] | None = None
    reasons: list[str] = []
    if sport != "TENNIS":
        winner = {
            "home": money["home_win"], "away": money["away_win"],
            "draw": money["draw"], "tie_settlement": tie_rule,
        }
    else:
        reasons.append("TENNIS_MATCH_WINNER_NOT_DERIVABLE_FROM_GAME_COUNTS")
    return {
        "contract": CONTRACT, "status": "RESEARCH_ONLY",
        "controlling_engine_identity": CONTROLLING_ENGINE,
        "terminal_authority": TERMINAL_AUTHORITY, "event_id": projection["event_id"],
        "sport": sport, "settlement_basis": settlement_basis,
        "source_receipt_id": None,
        "source_artifact_sha256_claimed": projection["fitted_artifact_sha256"],
        "source_artifact_proven_certified": False,
        "winner": winner,
        "spread": {
            "signed_home_line": spread_line, "p_cover": spread["more"],
            "p_push": spread["exact"], "p_not_cover": spread["less"],
        },
        "total": {
            "line": total_line, "p_over": total["more"],
            "p_push": total["exact"], "p_under": total["less"],
        },
        "reason_codes": tuple(reasons), "market_edge_established": False,
        "model_certified": False, "rank_eligible": False,
        "probability_publishable": False, "automatic_certification": False,
        "can_execute": False,
    }


__all__ = ["CAN_EXECUTE", "PROBABILITY_PUBLISHABLE", "AUTOMATIC_CERTIFICATION", "CONTRACT", "settle_seven_sport_shadow"]
