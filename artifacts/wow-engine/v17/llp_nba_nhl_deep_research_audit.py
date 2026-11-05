"""Read-only NBA/NHL deep-research provenance audit for LLP team-event views.

This does NOT fit a model, infer a win probability, calculate a numerical upset
volatility index, authorize a market, or upgrade a V17 terminal. It validates
the identity and timing of optional research context against an immutable
winner-model receipt and reports missing or newer evidence transparently.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

CAN_EXECUTE = False
SUPPORTED_SPORTS = frozenset({"NBA", "NHL"})
FACTOR_FAMILIES = {
    "NBA": (
        "SHOT_QUALITY_AND_PROCESS_VS_RESULTS",
        "PLAYER_VALUE_MINUTES_AND_REPLACEMENT",
        "LINEUP_CONTINUITY_AND_REGULARIZATION",
        "NEUTRAL_SCORE_STATE_EFFICIENCY",
        "PACE_SHOT_PROFILE_AND_SCORING_VARIANCE",
        "RIM_THREE_REBOUND_FOUL_BENCH_MATCHUP",
        "SCHEDULE_TRAVEL_AND_CIRCADIAN_LOAD",
        "OFFICIATING_AND_COMPETITION_STATE",
        "MARKET_DISAGREEMENT_ONLY_AS_AUDIT",
        "REGIME_SHIFT_AND_CALIBRATION_COVERAGE",
    ),
    "NHL": (
        "FIVE_ON_FIVE_XG_AND_PROCESS_VS_RESULTS",
        "GOALIE_START_PROBABILITY_AND_REPLACEMENT",
        "LINE_PAIR_AND_UNIT_CONTINUITY",
        "POWER_PLAY_VS_PENALTY_KILL",
        "SCORE_STATE_AND_PULLED_GOALIE_ADJUSTMENT",
        "SCORING_GOALIE_AND_PENALTY_VARIANCE",
        "REST_TRAVEL_AND_GOALIE_WORKLOAD",
        "TACTICAL_MATCHUP_AND_COMPETITION_STATE",
        "MARKET_DISAGREEMENT_ONLY_AS_AUDIT",
        "REGIME_SHIFT_AND_CALIBRATION_COVERAGE",
    ),
}
ALLOWED_FLAGS = {
    "NBA": frozenset({
        "LATE_SCRATCH_RISK", "STARTER_MINUTES_UNCERTAIN",
        "LINEUP_CONTINUITY_DISRUPTED", "SHOT_PROFILE_VARIANCE",
        "TRAVEL_FATIGUE_STATE", "NEUTRAL_STATE_FORM_CONFLICT",
        "MATERIAL_MARKET_DISAGREEMENT", "MODEL_REGIME_SHIFT",
    }),
    "NHL": frozenset({
        "STARTING_GOALIE_UNCONFIRMED", "GOALIE_REPLACEMENT_MISMATCH",
        "DEFENSIVE_PAIR_DISRUPTION", "POWER_PLAY_PENALTY_KILL_MISMATCH",
        "PULLED_GOALIE_STAT_DISTORTION", "HIGH_VARIANCE_GOALIE_STATE",
        "TRAVEL_FATIGUE_STATE", "MATERIAL_MARKET_DISAGREEMENT",
        "MODEL_REGIME_SHIFT",
    }),
}


def _instant(value: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def _token(value: Any) -> str:
    return str(value or "").strip()


def inspect_nba_nhl_deep_research(
    *, event: Mapping[str, Any], winner_receipt: Mapping[str, Any] | None,
    context: Any = None,
) -> dict[str, Any]:
    """Surface research evidence, never promote it to calibrated winner proof.

    A supplied context must match exact sport/event, champion model version,
    source snapshot and source winner receipt ID, with timezone-aware timestamps.
    The source fields are metadata assertions, not independent validation of
    provider content. A pregame observation newer than the model must trigger a
    fresh governed model run, never an LLM pick flip.
    """
    sport = _token(event.get("sport")).upper()
    if sport not in SUPPORTED_SPORTS:
        raise ValueError("LLP_DEEP_RESEARCH_NBA_NHL_ONLY")
    base: dict[str, Any] = {
        "status": "CONTEXT_NOT_SUPPLIED",
        "factor_families_documented": [],
        "factor_families_missing": list(FACTOR_FAMILIES[sport]),
        "analyst_review_flags": [],
        "analyst_review_required": False,
        "model_recheck_required": False,
        "upset_volatility_index": None,
        "upset_volatility_status": "NOT_FITTED_OR_CERTIFIED_HERE",
        "forecast_fragility": None,
        "forecast_fragility_status": "NOT_FITTED_OR_CERTIFIED_HERE",
        "independently_certified": False,
        "market_probability_used_for_winner": False,
        "can_execute": False,
    }
    if context is None:
        return base
    if not isinstance(context, Mapping):
        return {**base, "status": "CONTEXT_INVALID_PROVENANCE"}
    if not isinstance(winner_receipt, Mapping):
        return {**base, "status": "CONTROLLING_WINNER_RECEIPT_REQUIRED"}

    start = _instant(event.get("event_start_time_utc"))
    model_at = _instant(winner_receipt.get("model_timestamp"))
    source_at = _instant(winner_receipt.get("source_snapshot_timestamp"))
    observed = _instant(context.get("observed_at"))
    source_id = _token(winner_receipt.get("source_snapshot_id"))
    receipt_id = _token(winner_receipt.get("source_receipt_id"))
    model_version = _token(winner_receipt.get("model_version"))
    if (
        not _token(event.get("official_event_id"))
        or not source_id or not receipt_id or not model_version
        or start is None or model_at is None or source_at is None or observed is None
        or source_at > model_at or model_at >= start or observed >= start
        or _token(context.get("sport")).upper() != sport
        or _token(context.get("official_event_id")) != _token(event.get("official_event_id"))
        or _token(context.get("source_snapshot_id")) != source_id
        or _token(context.get("source_receipt_id")) != receipt_id
        or _token(context.get("model_version")) != model_version
    ):
        return {**base, "status": "CONTEXT_INVALID_PROVENANCE"}

    if observed > model_at:
        return {
            **base,
            "status": "MATERIAL_CONTEXT_NEWER_THAN_MODEL",
            "model_recheck_required": True,
            "analyst_review_required": True,
            "analyst_review_flags": ["FRESH_MODEL_SCORING_REQUIRED"],
        }

    factors = context.get("factors")
    if not isinstance(factors, Mapping):
        return {**base, "status": "CONTEXT_INVALID_FACTOR_RECEIPTS"}

    documented: list[str] = []
    flags: set[str] = set()
    for family in FACTOR_FAMILIES[sport]:
        source = factors.get(family)
        if not isinstance(source, Mapping):
            continue
        observed_factor = _instant(source.get("observed_at"))
        if (
            source.get("status") != "DOCUMENTED"
            or not _token(source.get("source_receipt_id"))
            or observed_factor is None or observed_factor > observed
        ):
            continue
        documented.append(family)
        incoming = source.get("review_flags")
        if isinstance(incoming, list):
            flags.update(
                item for item in incoming
                if isinstance(item, str) and item in ALLOWED_FLAGS[sport]
            )
    return {
        **base,
        "status": "SOURCE_CONTEXT_RECORDED_UNCERTIFIED",
        "factor_families_documented": documented,
        "factor_families_missing": [
            family for family in FACTOR_FAMILIES[sport] if family not in documented
        ],
        "analyst_review_flags": sorted(flags),
        "analyst_review_required": bool(flags),
        "observed_at": observed.isoformat(),
    }
