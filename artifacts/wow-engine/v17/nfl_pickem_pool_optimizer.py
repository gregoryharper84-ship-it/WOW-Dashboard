"""V17 NFL pick'em selection layer downstream of the governed NFL win model.

This module never produces sporting probabilities. It consumes a completed
sporting-probability result from the controlling NFL fitted specialist and turns
that already-selected participant into a required pick'em choice.

Pick'em selection eligibility is intentionally different from betting/publication
eligibility. A valid completed model result may be held below FINAL_APPROVED by
market, ledger, or downstream publication governance and still answer the
pool-only question "which team has the higher governed win probability?". The
source terminal is preserved verbatim, never upgraded, and ``can_execute`` stays
false. Typed model failures, stale/withheld output, hard rejection labels, and
malformed probability packages still fail closed.
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
    STALE_MODEL_OUTPUT,
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

PICKEM_REVIEW_HIGH_CONFIDENCE_HOLD = "HIGH_CONFIDENCE_HOLD"
PICKEM_REVIEW_STANDARD_HOLD = "STANDARD_HOLD"
PICKEM_REVIEW_MODEL_SIDE_FRAGILITY = "MODEL_SIDE_FRAGILITY_REVIEW"
PICKEM_REVIEW_TOSS_UP = "TOSS_UP_REVIEW"

_TYPED_MODEL_FAILURES = frozenset({
    MODEL_INPUTS_INSUFFICIENT,
    MODEL_OUTPUT_INVALID,
    MODEL_SCORER_FAILED,
    MODEL_UNAVAILABLE,
    STALE_MODEL_OUTPUT,
    "MODEL_ROUTE_UNSUPPORTED",
    "RUN_INVALID_ACQUISITION_INCOMPLETE",
})

# These are probability-bearing ladder states, not pick'em approvals. The source
# state is carried into output unchanged. Stale/reject/purge variants are not in
# this allow-list and therefore cannot silently become a pool pick.
_PICKEM_PROBABILITY_BEARING_TERMINALS = frozenset({
    "MODEL_QUALIFIED_HOLD",
    "MARKET_VERIFIED_HOLD",
    "MONEY_QUALIFIED",
    "FINAL_APPROVED",
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


def _source_snapshot_id(row: Mapping[str, Any]) -> str | None:
    envelope = row.get("candidate_envelope")
    envelope = envelope if isinstance(envelope, Mapping) else {}
    acquisition = row.get("canonical_acquisition")
    acquisition = acquisition if isinstance(acquisition, Mapping) else {}
    return _text(
        row.get("source_snapshot_id")
        or envelope.get("source_snapshot_id")
        or acquisition.get("source_snapshot_id")
    )


def _model_timestamp(row: Mapping[str, Any]) -> str | None:
    return _text(row.get("immutable_model_timestamp") or row.get("model_timestamp"))


def _typed_failure(row: Mapping[str, Any]) -> str | None:
    candidates = (
        row.get("code"),
        row.get("terminal_status"),
        row.get("model_status"),
        row.get("failure_code"),
    )
    for value in candidates:
        token = str(value or "").strip().upper()
        if token in _TYPED_MODEL_FAILURES:
            return token
    for blocker in row.get("blockers") or ():
        token = str(blocker or "").strip().upper()
        if token in _TYPED_MODEL_FAILURES or token.startswith("STALE_MODEL_OUTPUT"):
            return STALE_MODEL_OUTPUT if token.startswith("STALE_MODEL_OUTPUT") else token
    return None


def _selection_bounds(
    row: Mapping[str, Any],
    *,
    selected: str,
    home: str,
) -> tuple[float | None, float | None, list[str]]:
    side = "home" if selected == home else "away"
    lower_candidates = [
        _number(row.get("calibrated_selection_lower_bound")),
        _number(row.get("rank_calibrated_lower_bound")),
        _number(row.get("calibrated_lower_bound")),
        _number(row.get(f"calibrated_{side}_lower_bound")),
    ]
    upper_candidates = [
        _number(row.get("calibrated_selection_upper_bound")),
        _number(row.get("calibrated_upper_bound")),
        _number(row.get(f"calibrated_{side}_upper_bound")),
    ]
    lowers = [value for value in lower_candidates if value is not None]
    uppers = [value for value in upper_candidates if value is not None]
    blockers: list[str] = []
    if not lowers:
        blockers.append("PICKEM_CALIBRATED_SELECTION_LOWER_BOUND_REQUIRED")
    if not uppers:
        blockers.append("PICKEM_CALIBRATED_SELECTION_UPPER_BOUND_REQUIRED")
    if lowers and any(not isclose(value, lowers[0], abs_tol=_EPS) for value in lowers[1:]):
        blockers.append("PICKEM_SELECTION_LOWER_BOUND_ALIAS_MISMATCH")
    if uppers and any(not isclose(value, uppers[0], abs_tol=_EPS) for value in uppers[1:]):
        blockers.append("PICKEM_SELECTION_UPPER_BOUND_ALIAS_MISMATCH")
    return (lowers[0] if lowers else None, uppers[0] if uppers else None, blockers)


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


def _model_disagreement(row: Mapping[str, Any]) -> float | None:
    """Read governed scorer disagreement when exposed; never manufacture it."""
    value = _number(row.get("model_disagreement"))
    if value is None or not 0.0 <= value <= 1.0:
        return None
    return value


def pickem_review_profile(
    *,
    selected_probability: float,
    lower_bound: float,
    upper_bound: float,
    probability_gap: float,
    model_disagreement: float | None,
) -> dict[str, Any]:
    """Classify review priority without changing probability or the pool pick.

    This is a downstream learning/triage overlay. Thresholds route analyst/model
    review only; they are not fitted-model coefficients, calibration thresholds,
    or an alternate winner model.
    """
    interval_width = max(0.0, upper_bound - lower_bound)
    reasons: list[str] = []
    if selected_probability < 0.55 - _EPS:
        reasons.append("TOSS_UP_POINT_PROBABILITY")
    if probability_gap <= 0.10 + _EPS:
        reasons.append("NARROW_TWO_SIDED_GAP")
    if lower_bound < 0.55 - _EPS:
        reasons.append("LOWER_BOUND_BELOW_55")
    if interval_width >= 0.10 - _EPS:
        reasons.append("WIDE_CALIBRATION_INTERVAL")
    if model_disagreement is not None and model_disagreement >= 0.05 - _EPS:
        reasons.append("MATERIAL_MODEL_DISAGREEMENT")

    strong_hold = (
        selected_probability >= 0.68 - _EPS
        and lower_bound >= 0.60 - _EPS
        and probability_gap >= 0.16 - _EPS
        and (model_disagreement is None or model_disagreement < 0.05 - _EPS)
    )
    if strong_hold:
        review_class = PICKEM_REVIEW_HIGH_CONFIDENCE_HOLD
        postmortem_action = "PRESERVE_UNLESS_COHORT_EVIDENCE"
    elif selected_probability < 0.55 - _EPS:
        review_class = PICKEM_REVIEW_TOSS_UP
        postmortem_action = "DEEP_REVIEW_NO_AUTOMATIC_FLIP"
    elif reasons:
        review_class = PICKEM_REVIEW_MODEL_SIDE_FRAGILITY
        postmortem_action = "DEEP_REVIEW_NO_AUTOMATIC_FLIP"
    else:
        review_class = PICKEM_REVIEW_STANDARD_HOLD
        postmortem_action = "PRESERVE"

    return {
        "pickem_review_class": review_class,
        "pickem_review_reasons": sorted(set(reasons)),
        "calibration_interval_width": round(interval_width, 6),
        "model_disagreement": model_disagreement,
        "model_disagreement_available": model_disagreement is not None,
        "postmortem_learning_action": postmortem_action,
        "review_overlay_can_change_pool_pick": False,
        "review_overlay_can_change_probability": False,
    }


def _blocked(
    row: Mapping[str, Any],
    *,
    source_status: str,
    blockers: Iterable[str],
    identity: Mapping[str, str | None] | None = None,
) -> dict[str, Any]:
    ident = dict(identity or _event_identity(row))
    source_terminal = _text(row.get("terminal_label"))
    return {
        "branch_id": BRANCH_ID,
        "status": PICKEM_BLOCKED,
        "source_model_status": source_status,
        "source_terminal_label": source_terminal,
        "source_probability_publishable": row.get("probability_publishable") is True,
        "source_rank_eligible": row.get("rank_eligible") is True,
        "blockers": sorted({_text(value) for value in blockers if _text(value)}),
        **ident,
        "pool_pick": None,
        "pickem_selection_eligible": False,
        "market_probability_used": False,
        "sportsbook_price_used": False,
        "pool_popularity_used": False,
        "source_terminal_upgraded": False,
        "can_execute": False,
    }


def select_pickem_game(row: Mapping[str, Any]) -> dict[str, Any]:
    """Convert one completed governed NFL sporting result into one pool pick.

    This function is deliberately downstream-only. It does not call a model,
    construct a probability, compare sportsbook prices, or upgrade the source
    terminal. A modeled hold can answer a pick'em pool question while remaining
    ineligible for betting publication/ranking.
    """
    identity = _event_identity(row)
    failure = _typed_failure(row)
    if failure is not None:
        return _blocked(
            row,
            source_status=failure,
            blockers=row.get("blockers") or [failure],
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

    source_terminal = str(row.get("terminal_label") or "").strip().upper()
    sporting_status = str(row.get("sporting_probability_status") or "").strip().upper()
    governance_blockers: list[str] = []
    if row.get("sporting_probability_completed") is not True:
        governance_blockers.append("PICKEM_SPORTING_PROBABILITY_NOT_COMPLETED")
    if not sporting_status.startswith("COMPLETED"):
        governance_blockers.append("PICKEM_SPORTING_PROBABILITY_STATUS_NOT_COMPLETED")
    if row.get("probability_fields_withheld") is True:
        governance_blockers.append("PICKEM_PROBABILITY_FIELDS_WITHHELD")
    if row.get("model_probability_available") is False:
        governance_blockers.append("PICKEM_MODEL_PROBABILITY_NOT_AVAILABLE")
    if source_terminal not in _PICKEM_PROBABILITY_BEARING_TERMINALS:
        governance_blockers.append("PICKEM_SOURCE_TERMINAL_NOT_PROBABILITY_BEARING")
    if _text(row.get("global_terminal_authority")) != TERMINAL_AUTHORITY:
        governance_blockers.append("PICKEM_TERMINAL_AUTHORITY_MISMATCH")
    if row.get("can_execute") is not False:
        governance_blockers.append("PICKEM_CAN_EXECUTE_MUST_BE_FALSE")
    if not _text(row.get("model_version") or row.get("model_artifact_version")):
        governance_blockers.append("PICKEM_MODEL_VERSION_REQUIRED")
    if not _model_timestamp(row):
        governance_blockers.append("PICKEM_MODEL_TIMESTAMP_REQUIRED")
    if not _text(row.get("calibration_method")):
        governance_blockers.append("PICKEM_CALIBRATION_METHOD_REQUIRED")
    if not _text(row.get("calibration_version")):
        governance_blockers.append("PICKEM_CALIBRATION_VERSION_REQUIRED")
    if not _source_snapshot_id(row):
        governance_blockers.append("PICKEM_SOURCE_SNAPSHOT_ID_REQUIRED")
    if governance_blockers:
        return _blocked(
            row,
            source_status="PICKEM_GOVERNED_SPORTING_PROBABILITY_NOT_VALID",
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
    selected_p = _number(
        row.get("calibrated_selection_probability")
        if row.get("calibrated_selection_probability") is not None
        else row.get("calibrated_probability")
    )
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

    lower, upper, bound_blockers = _selection_bounds(
        row,
        selected=selected,
        home=home,
    )
    if bound_blockers:
        return _blocked(
            row,
            source_status=MODEL_OUTPUT_INVALID,
            blockers=bound_blockers,
            identity=identity,
        )
    assert lower is not None and upper is not None
    if not (0.0 <= lower <= selected_p <= upper <= 1.0):
        return _blocked(
            row,
            source_status=MODEL_OUTPUT_INVALID,
            blockers=["PICKEM_CALIBRATED_PROBABILITY_BOUNDS_INVALID"],
            identity=identity,
        )

    gap = abs(home_p - away_p)
    review_profile = pickem_review_profile(
        selected_probability=selected_p,
        lower_bound=lower,
        upper_bound=upper,
        probability_gap=gap,
        model_disagreement=_model_disagreement(row),
    )
    return {
        "branch_id": BRANCH_ID,
        "decision_objective": DECISION_OBJECTIVE,
        "status": PICKEM_READY,
        **identity,
        "pool_pick": selected,
        "opponent": opponent,
        "selected_probability": selected_p,
        "calibrated_lower_bound": lower,
        "calibrated_upper_bound": upper,
        "home_probability": home_p,
        "away_probability": away_p,
        "probability_gap": gap,
        "confidence_band": confidence_band(selected_p),
        "selection_volatility_band": selection_volatility_band(gap),
        **review_profile,
        "source_prediction_id": _text(row.get("prediction_id") or row.get("event_prediction_id")),
        "immutable_model_timestamp": _model_timestamp(row),
        "source_snapshot_id": _source_snapshot_id(row),
        "source_model_status": _text(row.get("code") or row.get("terminal_status")),
        "source_terminal_label": row.get("terminal_label"),
        "source_probability_publishable": row.get("probability_publishable") is True,
        "source_rank_eligible": row.get("rank_eligible") is True,
        "source_blockers": list(row.get("blockers") or []),
        "controlling_specialist": CONTROLLING_SPECIALIST,
        "pickem_selection_eligible": True,
        "market_probability_used": False,
        "sportsbook_price_used": False,
        "pool_popularity_used": False,
        "source_terminal_upgraded": False,
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
            "high_confidence_hold_count": 0,
            "model_side_fragility_review_count": 0,
            "toss_up_review_count": 0,
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

    picks.sort(key=lambda item: str(item.get("official_event_id") or ""))
    blocked.sort(key=lambda item: str(item.get("official_event_id") or ""))
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
        "high_confidence_hold_count": sum(
            1 for pick in picks
            if pick.get("pickem_review_class") == PICKEM_REVIEW_HIGH_CONFIDENCE_HOLD
        ),
        "model_side_fragility_review_count": sum(
            1 for pick in picks
            if pick.get("pickem_review_class") == PICKEM_REVIEW_MODEL_SIDE_FRAGILITY
        ),
        "toss_up_review_count": sum(
            1 for pick in picks
            if pick.get("pickem_review_class") == PICKEM_REVIEW_TOSS_UP
        ),
        "picks": picks,
        "blocked": blocked,
        "market_probability_used": False,
        "sportsbook_price_used": False,
        "pool_popularity_used": False,
        "source_terminals_preserved": True,
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
    "PICKEM_REVIEW_HIGH_CONFIDENCE_HOLD",
    "PICKEM_REVIEW_MODEL_SIDE_FRAGILITY",
    "PICKEM_REVIEW_STANDARD_HOLD",
    "PICKEM_REVIEW_TOSS_UP",
    "build_pickem_board",
    "confidence_band",
    "pickem_review_profile",
    "select_pickem_game",
    "selection_volatility_band",
    "tiebreaker_capability",
]
