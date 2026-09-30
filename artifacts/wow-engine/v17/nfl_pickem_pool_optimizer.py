"""V17 NFL pick'em selection layer downstream of the governed NFL win model.

This module never produces sporting probabilities. It consumes a completed,
publishable governed NFL probability package and converts the controlling
specialist's selected participant into a required pick'em choice. Sportsbook
price, implied probability, pool popularity, and generic reasoning are never
used to alter the sporting probability or the selected participant.
"""
from __future__ import annotations

from math import isclose, isfinite
from typing import Any, Iterable, Mapping

from nfl_event_model_contract import CONTROLLING_SPECIALIST
from v17.llp_governed_package_scoring import (
    MODEL_INPUTS_INSUFFICIENT,
    MODEL_OUTPUT_INVALID,
    MODEL_SCORER_FAILED,
    MODEL_UNAVAILABLE,
    PASS,
    STALE_MODEL_OUTPUT,
    validate_governed_scoring_package,
)

BRANCH_ID = "NFL_PICKEM_POOL_V1"
DECISION_OBJECTIVE = "MAX_EXPECTED_CORRECT"
CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"

PICKEM_READY = "PICKEM_READY"
PICKEM_BLOCKED = "PICKEM_BLOCKED"
PICKEM_BOARD_READY = "PICKEM_BOARD_READY"
PICKEM_BOARD_PARTIAL = "PICKEM_BOARD_PARTIAL"
PICKEM_BOARD_INCOMPLETE = "PICKEM_BOARD_INCOMPLETE"
PICKEM_BOARD_EMPTY = "PICKEM_BOARD_EMPTY"

_TYPED_MODEL_FAILURES = frozenset({
    MODEL_INPUTS_INSUFFICIENT,
    MODEL_OUTPUT_INVALID,
    MODEL_SCORER_FAILED,
    MODEL_UNAVAILABLE,
    STALE_MODEL_OUTPUT,
})
_EPS = 1e-9


def _text(value: Any) -> str | None:
    token = str(value or "").strip()
    return token or None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    return parsed if isfinite(parsed) else None


def _event_identity(row: Mapping[str, Any]) -> dict[str, str | None]:
    envelope = row.get("candidate_envelope")
    envelope = envelope if isinstance(envelope, Mapping) else {}
    return {
        "official_event_id": _text(
            envelope.get("official_event_id")
            or row.get("canonical_event_id")
            or row.get("official_event_id")
        ),
        "sport": _text(envelope.get("sport") or row.get("sport")),
        "league": _text(envelope.get("league") or row.get("league")),
        "home_team": _text(envelope.get("home_team") or row.get("home_team")),
        "away_team": _text(envelope.get("away_team") or row.get("away_team")),
    }


def confidence_band(probability: float) -> str:
    """Selection-layer label only; never alters the model probability."""
    if probability >= 0.70:
        return "STRONG"
    if probability >= 0.60:
        return "MODERATE"
    if probability >= 0.55:
        return "LEAN"
    return "TOSS_UP"


def selection_volatility_band(probability_gap: float) -> str:
    """Descriptive band from the model's two-sided probability gap."""
    if probability_gap <= 0.04 + _EPS:
        return "HIGH"
    if probability_gap <= 0.10 + _EPS:
        return "MODERATE"
    return "LOW"


def _blocked(
    row: Mapping[str, Any],
    *,
    source_status: str,
    blockers: Iterable[str],
    identity: Mapping[str, str | None] | None = None,
) -> dict[str, Any]:
    ident = dict(identity or _event_identity(row))
    return {
        "branch_id": BRANCH_ID,
        "status": PICKEM_BLOCKED,
        "source_model_status": source_status,
        "blockers": sorted({_text(v) for v in blockers if _text(v)}),
        **ident,
        "pool_pick": None,
        "pickem_selection_eligible": False,
        "market_probability_used": False,
        "sportsbook_price_used": False,
        "pool_popularity_used": False,
        "can_execute": False,
    }


def select_pickem_game(row: Mapping[str, Any]) -> dict[str, Any]:
    """Convert one governed NFL result into one required pick'em selection.

    This function is deliberately downstream-only. It does not call a model,
    construct a probability, compare sportsbook prices, or override the model's
    selected participant.
    """
    identity = _event_identity(row)
    source_code = _text(row.get("code"))
    if (
        source_code in _TYPED_MODEL_FAILURES
        and row.get("sporting_probability_completed") is not True
    ):
        return _blocked(
            row,
            source_status=source_code,
            blockers=row.get("blockers") or [source_code],
            identity=identity,
        )

    missing_identity = [
        name
        for name in ("official_event_id", "home_team", "away_team")
        if not identity.get(name)
    ]
    if missing_identity:
        return _blocked(
            row,
            source_status=MODEL_INPUTS_INSUFFICIENT,
            blockers=[f"PICKEM_{name.upper()}_MISSING" for name in missing_identity],
            identity=identity,
        )
    if (
        str(identity.get("sport") or "").upper() != "NFL"
        or str(identity.get("league") or "").upper() != "NFL"
    ):
        return _blocked(
            row,
            source_status=MODEL_INPUTS_INSUFFICIENT,
            blockers=["PICKEM_NFL_IDENTITY_REQUIRED"],
            identity=identity,
        )

    audit = validate_governed_scoring_package(row)
    if audit.status != PASS:
        return _blocked(
            row,
            source_status=audit.status,
            blockers=audit.blockers,
            identity=identity,
        )

    governance_blockers: list[str] = []
    if row.get("sporting_probability_completed") is not True:
        governance_blockers.append("PICKEM_SPORTING_PROBABILITY_NOT_COMPLETED")
    if row.get("probability_fields_withheld") is True:
        governance_blockers.append("PICKEM_PROBABILITY_FIELDS_WITHHELD")
    if row.get("probability_publishable") is not True:
        governance_blockers.append("PICKEM_PROBABILITY_NOT_PUBLISHABLE")
    if row.get("rank_eligible") is not True:
        governance_blockers.append("PICKEM_SOURCE_ROW_NOT_RANK_ELIGIBLE")
    if _text(row.get("terminal_label")) != "FINAL_APPROVED":
        governance_blockers.append("PICKEM_SOURCE_TERMINAL_NOT_FINAL_APPROVED")
    if _text(row.get("global_terminal_authority")) != TERMINAL_AUTHORITY:
        governance_blockers.append("PICKEM_TERMINAL_AUTHORITY_MISMATCH")
    if row.get("can_execute") is not False:
        governance_blockers.append("PICKEM_CAN_EXECUTE_MUST_BE_FALSE")
    if governance_blockers:
        return _blocked(
            row,
            source_status="PICKEM_GOVERNANCE_NOT_FINAL",
            blockers=governance_blockers,
            identity=identity,
        )

    selected = _text(row.get("selected_participant"))
    opponent = _text(row.get("opponent"))
    home = str(identity["home_team"])
    away = str(identity["away_team"])
    if (
        selected not in {home, away}
        or opponent not in {home, away}
        or selected == opponent
    ):
        return _blocked(
            row,
            source_status=MODEL_OUTPUT_INVALID,
            blockers=["PICKEM_SELECTED_PARTICIPANT_IDENTITY_INVALID"],
            identity=identity,
        )

    home_p = _number(row.get("calibrated_home_probability"))
    away_p = _number(row.get("calibrated_away_probability"))
    selected_p = _number(row.get("calibrated_selection_probability"))
    if home_p is None or away_p is None or selected_p is None:
        return _blocked(
            row,
            source_status=MODEL_OUTPUT_INVALID,
            blockers=["PICKEM_TWO_SIDED_CALIBRATED_PROBABILITY_REQUIRED"],
            identity=identity,
        )
    if not (
        0.0 <= home_p <= 1.0
        and 0.0 <= away_p <= 1.0
        and 0.0 <= selected_p <= 1.0
    ):
        return _blocked(
            row,
            source_status=MODEL_OUTPUT_INVALID,
            blockers=["PICKEM_PROBABILITY_OUT_OF_RANGE"],
            identity=identity,
        )
    if not isclose(home_p + away_p, 1.0, abs_tol=_EPS):
        return _blocked(
            row,
            source_status=MODEL_OUTPUT_INVALID,
            blockers=["PICKEM_OUTCOME_SPACE_NOT_NORMALIZED"],
            identity=identity,
        )

    expected_selected_p = home_p if selected == home else away_p
    max_p = max(home_p, away_p)
    if (
        not isclose(selected_p, expected_selected_p, abs_tol=_EPS)
        or not isclose(selected_p, max_p, abs_tol=_EPS)
    ):
        return _blocked(
            row,
            source_status=MODEL_OUTPUT_INVALID,
            blockers=["PICKEM_SELECTION_MODEL_OUTPUT_MISMATCH"],
            identity=identity,
        )
    if (
        audit.calibrated_probability is None
        or not isclose(selected_p, audit.calibrated_probability, abs_tol=_EPS)
    ):
        return _blocked(
            row,
            source_status=MODEL_OUTPUT_INVALID,
            blockers=["PICKEM_GOVERNED_PACKAGE_SELECTION_MISMATCH"],
            identity=identity,
        )

    gap = abs(home_p - away_p)
    return {
        "branch_id": BRANCH_ID,
        "decision_objective": DECISION_OBJECTIVE,
        "status": PICKEM_READY,
        **identity,
        "pool_pick": selected,
        "opponent": opponent,
        "selected_probability": selected_p,
        "calibrated_lower_bound": audit.calibrated_lower_bound,
        "calibrated_upper_bound": audit.calibrated_upper_bound,
        "home_probability": home_p,
        "away_probability": away_p,
        "probability_gap": gap,
        "confidence_band": confidence_band(selected_p),
        "selection_volatility_band": selection_volatility_band(gap),
        "source_prediction_id": audit.identifier,
        "immutable_model_timestamp": audit.immutable_model_timestamp,
        "source_terminal_label": row.get("terminal_label"),
        "controlling_specialist": CONTROLLING_SPECIALIST,
        "pickem_selection_eligible": True,
        "market_probability_used": False,
        "sportsbook_price_used": False,
        "pool_popularity_used": False,
        "can_execute": False,
    }


def build_pickem_board(
    rows: Iterable[Mapping[str, Any]],
    *,
    expected_game_count: int | None = None,
    strategy_mode: str = DECISION_OBJECTIVE,
) -> dict[str, Any]:
    """Build a one-pick-per-event NFL pick'em board from governed model rows."""
    if strategy_mode != DECISION_OBJECTIVE:
        raise ValueError("PICKEM_STRATEGY_MODE_UNSUPPORTED")
    if expected_game_count is not None and (
        isinstance(expected_game_count, bool) or expected_game_count < 1
    ):
        raise ValueError("PICKEM_EXPECTED_GAME_COUNT_INVALID")

    materialized = [dict(row) for row in rows]
    if not materialized:
        return {
            "branch_id": BRANCH_ID,
            "decision_objective": DECISION_OBJECTIVE,
            "status": PICKEM_BOARD_EMPTY,
            "submission_ready": False,
            "expected_game_count": expected_game_count,
            "discovered_row_count": 0,
            "discovered_event_count": 0,
            "ready_pick_count": 0,
            "blocked_event_count": 0,
            "picks": [],
            "blocked": [],
            "can_execute": False,
        }

    grouped: dict[str, list[dict[str, Any]]] = {}
    missing_identity_rows: list[dict[str, Any]] = []
    for row in materialized:
        event_id = _event_identity(row).get("official_event_id")
        if event_id:
            grouped.setdefault(str(event_id), []).append(row)
        else:
            missing_identity_rows.append(row)

    picks: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for event_rows in grouped.values():
        if len(event_rows) != 1:
            blocked.append(
                _blocked(
                    event_rows[0],
                    source_status=MODEL_OUTPUT_INVALID,
                    blockers=[
                        "PICKEM_DUPLICATE_GOVERNED_EVENT_ROW",
                        f"PICKEM_DUPLICATE_COUNT_{len(event_rows)}",
                    ],
                )
            )
            continue
        result = select_pickem_game(event_rows[0])
        (picks if result["status"] == PICKEM_READY else blocked).append(result)

    for row in missing_identity_rows:
        blocked.append(select_pickem_game(row))

    discovered_event_count = len(grouped) + len(missing_identity_rows)
    expected_mismatch = (
        expected_game_count is not None and len(picks) != expected_game_count
    )
    if expected_mismatch:
        status = PICKEM_BOARD_INCOMPLETE
    elif blocked:
        status = PICKEM_BOARD_PARTIAL
    else:
        status = PICKEM_BOARD_READY

    return {
        "branch_id": BRANCH_ID,
        "decision_objective": DECISION_OBJECTIVE,
        "status": status,
        "submission_ready": status == PICKEM_BOARD_READY,
        "expected_game_count": expected_game_count,
        "discovered_row_count": len(materialized),
        "discovered_event_count": discovered_event_count,
        "ready_pick_count": len(picks),
        "blocked_event_count": len(blocked),
        "picks": picks,
        "blocked": blocked,
        "market_probability_used": False,
        "sportsbook_price_used": False,
        "pool_popularity_used": False,
        "can_execute": False,
    }


def tiebreaker_capability() -> dict[str, Any]:
    """Fail closed until a certified NFL full-game total-points model is registered."""
    return {
        "status": "UNAVAILABLE",
        "blocker": "NFL_CERTIFIED_FULL_GAME_TOTAL_MODEL_NOT_REGISTERED",
        "moneyline_probability_reuse_allowed": False,
        "sportsbook_total_substitution_allowed": False,
        "can_execute": False,
    }


__all__ = [
    "BRANCH_ID",
    "CAN_EXECUTE",
    "DECISION_OBJECTIVE",
    "PICKEM_BLOCKED",
    "PICKEM_BOARD_EMPTY",
    "PICKEM_BOARD_INCOMPLETE",
    "PICKEM_BOARD_PARTIAL",
    "PICKEM_BOARD_READY",
    "PICKEM_READY",
    "build_pickem_board",
    "confidence_band",
    "select_pickem_game",
    "selection_volatility_band",
    "tiebreaker_capability",
]
