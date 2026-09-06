"""V17 PrizePicks Game Winner cash-single promotion gate.

This module deliberately sits downstream of the LLP team/event sporting-probability
lane.  It does not alter, calibrate, or rank sporting probabilities.  It answers a
separate question: may an already-governed outright-winner result be promoted into
WOW's cash-single / profitability workflow at the exact current PrizePicks payout?

The gate is fail-closed.  Probability-only leaderboards remain available when this
gate fails.  No function in this module can place, route, modify, approve, or cancel
a wager.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import Any, Mapping

CAN_EXECUTE = False
DEFAULT_PROVISIONAL_SAFETY_BUFFER = 0.025
DEFAULT_MAX_PRICE_AGE_MINUTES = 10.0
DEFAULT_MAX_MARKET_DISAGREEMENT_PP = 0.10

_PASS_WORDS = {"PASS", "PASSED", "RESOLVED", "EXPLAINED", "PASS_EXPLAINED"}
_FINALIZATION_PASS = {"PASS", "PASSED", "WRITTEN", "COMPLETED"}


def _norm(value: Any) -> str:
    return str(value or "").strip().upper()


def _prob(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    if not isfinite(value) or value < 0.0 or value > 1.0:
        return None
    return value


def _positive(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if isfinite(value) and value > 0 else None


def _aware_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _age_minutes(timestamp: Any, as_of: datetime) -> float | None:
    parsed = _aware_datetime(timestamp)
    if parsed is None:
        return None
    return max(0.0, (as_of - parsed).total_seconds() / 60.0)


def _one_of(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if mapping.get(key) is not None:
            return mapping.get(key)
    return None


@dataclass(frozen=True)
class GameWinnerCashSingleDecision:
    candidate_id: str
    selected_participant: str
    probability_rank_eligible: bool
    sporting_probability_preserved: bool
    platform: str
    gross_multiplier: float | None
    platform_break_even_probability: float | None
    calibrated_probability: float | None
    calibrated_lower_bound: float | None
    calibrated_upper_bound: float | None
    market_no_vig_probability: float | None
    market_prior_weight: float | None
    active_safety_buffer: float
    lower_bound_platform_edge: float | None
    lower_bound_edge_after_buffer: float | None
    model_vs_market_lower_bound_difference: float | None
    platform_price_age_minutes: float | None
    market_price_age_minutes: float | None
    economic_gate_status: str
    finalization_gate_status: str
    cash_single_eligible: bool
    terminal_ceiling: str
    blockers: tuple[str, ...]
    can_execute: bool = CAN_EXECUTE

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_game_winner_cash_single(
    probability_result: Mapping[str, Any],
    economic_context: Mapping[str, Any],
    *,
    finalization_context: Mapping[str, Any] | None = None,
    as_of: datetime | None = None,
) -> GameWinnerCashSingleDecision:
    """Evaluate a PrizePicks one-pick Game Winner for cash-lane promotion.

    Required separation:
      * ``probability_result`` is the governed LLP sporting package.
      * ``economic_context`` is exact current PrizePicks + two-way market evidence.
      * ``finalization_context`` proves final refresh and immutable pregame write.

    A rejection here never rewrites the sporting probability or removes the row from
    the probability-only leaderboard.
    """
    now = as_of.astimezone(timezone.utc) if as_of else datetime.now(timezone.utc)
    finalization_context = finalization_context or {}
    blockers: list[str] = []

    candidate_id = str(_one_of(probability_result, "candidate_id", "event_key", "prediction_id") or "").strip()
    participant = str(_one_of(economic_context, "platform_selection", "selected_participant", "participant") or "").strip()
    platform = _norm(economic_context.get("platform"))
    market_family = _norm(_one_of(economic_context, "market_family", "market_type", "selection_type"))

    calibrated = _prob(_one_of(probability_result, "calibrated_probability", "selected_calibrated_probability"))
    lower = _prob(_one_of(probability_result, "calibrated_lower_bound", "selected_calibrated_lower_bound", "lower_bound"))
    upper = _prob(_one_of(probability_result, "calibrated_upper_bound", "selected_calibrated_upper_bound", "upper_bound"))
    raw = _prob(_one_of(probability_result, "model_probability", "raw_model_probability", "raw_probability"))
    probability_publishable = probability_result.get("probability_publishable") is True
    rank_eligible = probability_result.get("rank_eligible") is True
    sporting_preserved = any(value is not None for value in (raw, calibrated, lower))

    if not candidate_id:
        blockers.append("CANDIDATE_ID_UNRESOLVED")
    if not participant:
        blockers.append("PLATFORM_SELECTION_UNRESOLVED")
    if platform != "PRIZEPICKS":
        blockers.append("CASH_SINGLE_PLATFORM_NOT_PRIZEPICKS")
    if market_family not in {"GAME_WINNER", "OUTRIGHT_WINNER", "MONEYLINE", "MATCH_WINNER", "FIGHT_WINNER"}:
        blockers.append("CASH_SINGLE_MARKET_NOT_OUTRIGHT_WINNER")

    if calibrated is None:
        blockers.append("CALIBRATED_PROBABILITY_UNAVAILABLE")
    if lower is None:
        blockers.append("CALIBRATED_LOWER_BOUND_UNAVAILABLE")
    if calibrated is not None and lower is not None and lower > calibrated:
        blockers.append("CALIBRATED_INTERVAL_INVALID")
    if upper is not None and calibrated is not None and calibrated > upper:
        blockers.append("CALIBRATED_INTERVAL_INVALID")
    if not probability_publishable:
        blockers.append("PROBABILITY_PUBLICATION_NOT_PROVEN")
    if not rank_eligible:
        blockers.append("PROBABILITY_RANK_ELIGIBILITY_NOT_PROVEN")

    calibration_health = _norm(probability_result.get("calibration_health_status"))
    if calibration_health not in _PASS_WORDS:
        blockers.append("GAME_WINNER_CALIBRATION_HEALTH_NOT_PASS")

    market_prior_weight = _prob(probability_result.get("market_prior_weight"))
    if market_prior_weight is not None and market_prior_weight > 0.50:
        blockers.append("MARKET_DEPENDENT_MODEL")

    failure_path_status = _norm(probability_result.get("failure_path_status"))
    if failure_path_status and failure_path_status not in _PASS_WORDS | {"NOT_APPLICABLE"}:
        blockers.append("GAME_WINNER_FAILURE_PATH_NOT_PASS")

    gross_multiplier = _positive(_one_of(economic_context, "platform_gross_multiplier", "gross_multiplier", "multiplier"))
    break_even = None
    if gross_multiplier is None or gross_multiplier <= 1.0:
        blockers.append("PRIZEPICKS_GROSS_MULTIPLIER_INVALID")
    else:
        break_even = 1.0 / gross_multiplier

    safety_buffer = _prob(economic_context.get("active_safety_buffer"))
    if safety_buffer is None:
        safety_buffer = DEFAULT_PROVISIONAL_SAFETY_BUFFER

    max_price_age = _positive(economic_context.get("max_price_age_minutes")) or DEFAULT_MAX_PRICE_AGE_MINUTES
    pp_age = _age_minutes(_one_of(economic_context, "platform_capture_timestamp", "board_timestamp"), now)
    market_age = _age_minutes(_one_of(economic_context, "sportsbook_timestamp", "market_timestamp"), now)
    if pp_age is None:
        blockers.append("PRIZEPICKS_PRICE_TIMESTAMP_UNRESOLVED")
    elif pp_age > max_price_age:
        blockers.append("PRIZEPICKS_PRICE_STALE")
    if market_age is None:
        blockers.append("TWO_WAY_MARKET_TIMESTAMP_UNRESOLVED")
    elif market_age > max_price_age:
        blockers.append("TWO_WAY_MARKET_STALE")

    exact_market_verified = economic_context.get("exact_two_way_market_verified") is True
    if not exact_market_verified:
        blockers.append("EXACT_TWO_WAY_MARKET_NOT_VERIFIED")
    source_count = economic_context.get("sportsbook_source_count")
    if isinstance(source_count, bool) or not isinstance(source_count, int) or source_count < 1:
        blockers.append("TWO_WAY_MARKET_SOURCE_COUNT_INVALID")

    no_vig = _prob(_one_of(economic_context, "market_no_vig_probability", "no_vig_probability"))
    if no_vig is None:
        blockers.append("MARKET_NO_VIG_PROBABILITY_UNAVAILABLE")

    lower_edge = None if lower is None or break_even is None else lower - break_even
    edge_after_buffer = None if lower_edge is None else lower_edge - safety_buffer
    market_difference = None if lower is None or no_vig is None else lower - no_vig

    if lower_edge is not None and lower_edge <= 0:
        blockers.append("LOWER_BOUND_PLATFORM_EDGE_NON_POSITIVE")
    elif edge_after_buffer is not None and edge_after_buffer <= 0:
        blockers.append("LOWER_BOUND_EDGE_DOES_NOT_CLEAR_SAFETY_BUFFER")

    disagreement_limit = _prob(economic_context.get("max_market_disagreement_pp"))
    if disagreement_limit is None:
        disagreement_limit = DEFAULT_MAX_MARKET_DISAGREEMENT_PP
    disagreement_status = _norm(economic_context.get("market_model_disagreement_status"))
    model_only_disagreement = bool(
        no_vig is not None
        and break_even is not None
        and no_vig < break_even
        and edge_after_buffer is not None
        and edge_after_buffer > 0
    )
    large_disagreement = bool(
        calibrated is not None
        and no_vig is not None
        and abs(calibrated - no_vig) > disagreement_limit
    )
    if (model_only_disagreement or large_disagreement) and disagreement_status not in _PASS_WORDS:
        blockers.append("MODEL_ONLY_DISAGREEMENT_UNRESOLVED" if model_only_disagreement else "MODEL_MARKET_DISAGREEMENT_UNRESOLVED")

    economic_blockers = tuple(dict.fromkeys(blockers))
    economic_pass = not economic_blockers
    economic_status = "PASS" if economic_pass else "BLOCKED"

    final_blockers: list[str] = []
    refresh_status = _norm(finalization_context.get("final_refresh_status"))
    ledger_status = _norm(_one_of(finalization_context, "immutable_prediction_write_status", "prediction_ledger_write_status"))
    if refresh_status not in _FINALIZATION_PASS:
        final_blockers.append("FINAL_REFRESH_NOT_PASS")
    if ledger_status not in _FINALIZATION_PASS:
        final_blockers.append("IMMUTABLE_PREGAME_WRITE_NOT_PASS")

    all_blockers = tuple(dict.fromkeys([*economic_blockers, *final_blockers]))
    cash_eligible = economic_pass and not final_blockers
    finalization_status = "PASS" if not final_blockers else "BLOCKED"

    if cash_eligible:
        terminal_ceiling = "MARKET_VERIFIED_HOLD"
    elif "LOWER_BOUND_PLATFORM_EDGE_NON_POSITIVE" in all_blockers or "LOWER_BOUND_EDGE_DOES_NOT_CLEAR_SAFETY_BUFFER" in all_blockers:
        terminal_ceiling = "REJECT_NO_EDGE"
    elif any(value.startswith("MODEL_ONLY_DISAGREEMENT") or value.startswith("MODEL_MARKET_DISAGREEMENT") for value in all_blockers):
        terminal_ceiling = "MARKET_VERIFIED_HOLD"
    elif sporting_preserved:
        terminal_ceiling = "MODEL_QUALIFIED_HOLD"
    else:
        terminal_ceiling = "REJECT_DATA_QUALITY"

    return GameWinnerCashSingleDecision(
        candidate_id=candidate_id,
        selected_participant=participant,
        probability_rank_eligible=rank_eligible,
        sporting_probability_preserved=sporting_preserved,
        platform=platform,
        gross_multiplier=gross_multiplier,
        platform_break_even_probability=break_even,
        calibrated_probability=calibrated,
        calibrated_lower_bound=lower,
        calibrated_upper_bound=upper,
        market_no_vig_probability=no_vig,
        market_prior_weight=market_prior_weight,
        active_safety_buffer=safety_buffer,
        lower_bound_platform_edge=lower_edge,
        lower_bound_edge_after_buffer=edge_after_buffer,
        model_vs_market_lower_bound_difference=market_difference,
        platform_price_age_minutes=pp_age,
        market_price_age_minutes=market_age,
        economic_gate_status=economic_status,
        finalization_gate_status=finalization_status,
        cash_single_eligible=cash_eligible,
        terminal_ceiling=terminal_ceiling,
        blockers=all_blockers,
        can_execute=False,
    )


__all__ = [
    "CAN_EXECUTE",
    "DEFAULT_MAX_MARKET_DISAGREEMENT_PP",
    "DEFAULT_MAX_PRICE_AGE_MINUTES",
    "DEFAULT_PROVISIONAL_SAFETY_BUFFER",
    "GameWinnerCashSingleDecision",
    "evaluate_game_winner_cash_single",
]
