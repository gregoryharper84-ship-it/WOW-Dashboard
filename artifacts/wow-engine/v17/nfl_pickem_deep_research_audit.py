"""Read-only transfer of LLP deep-research context into NFL Pick'em triage.

This is NOT a fitted feature pipeline, variance model, calibration result or
certified sporting probability. It never changes a winner, bound or terminal.
The controlling NFL specialist owns scenario-weighted probabilities and must
independently certify any future volatility/fragility estimates.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

CAN_EXECUTE = False
RESEARCH_FACTOR_FAMILIES = (
    "PROCESS_VS_OUTCOME",
    "PLAYER_VALUE_AND_REPLACEMENT",
    "UNIT_CONTINUITY_AND_MATCHUP",
    "NEUTRAL_SCORE_STATE_EFFICIENCY",
    "SCHEDULE_TRAVEL_AND_CIRCADIAN_STRESS",
    "SPECIAL_TEAMS_AND_COACHING",
    "UPSET_VARIANCE_AND_SCENARIOS",
    "LINEUP_MIXTURE_AND_FORECAST_FRAGILITY",
    "MARKET_DISAGREEMENT_DIAGNOSTIC",
    "REGIME_SHIFT_AND_CALIBRATION_RELIABILITY",
)
ALLOWED_RECHECK_FLAGS = frozenset({
    "PROCESS_OUTCOME_DIVERGENCE",
    "QB_AVAILABILITY_UNCERTAIN",
    "OL_UNIT_DISRUPTION",
    "REPLACEMENT_MATCHUP_MISMATCH",
    "GARBAGE_TIME_DISTORTION",
    "HIGH_SCHEDULE_STRESS",
    "SPECIAL_TEAMS_MISMATCH",
    "HIGH_VARIANCE_UPSET_PATHWAY",
    "SCENARIO_SENSITIVITY",
    "MATERIAL_MARKET_DISAGREEMENT",
    "MODEL_REGIME_SHIFT",
})


def _instant(value: Any) -> datetime | None:
    try:
        result = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if result.tzinfo is None:
        return None
    return result.astimezone(timezone.utc)


def audit_nfl_pickem_deep_research(row: Mapping[str, Any]) -> dict[str, Any]:
    """Inspect optional provenance-bound *context*, not model probabilities.

    No supplied status is independently verified by this consumer. An absent
    context is visible as a coverage gap, never interpreted as a clean matchup.
    A newer pregame material observation requires a model recheck, not a flip.
    """
    base: dict[str, Any] = {
        "status": "CONTEXT_NOT_SUPPLIED",
        "factor_families_documented": [],
        "factor_families_missing": list(RESEARCH_FACTOR_FAMILIES),
        "review_flags": [],
        "model_recheck_required": False,
        "analyst_review_required": False,
        "independently_certified": False,
        "upset_volatility_index": None,
        "upset_volatility_model_status": "NOT_FITTED_OR_CERTIFIED_HERE",
        "forecast_fragility": None,
        "forecast_fragility_model_status": "NOT_FITTED_OR_CERTIFIED_HERE",
        "market_prices_used_for_selection": False,
        "can_execute": False,
    }
    context = row.get("pickem_deep_research_context")
    if context is None:
        return base
    if not isinstance(context, Mapping):
        return {**base, "status": "CONTEXT_INVALID_PROVENANCE"}

    envelope = row.get("candidate_envelope")
    envelope = envelope if isinstance(envelope, Mapping) else {}
    acquisition = row.get("canonical_acquisition")
    acquisition = acquisition if isinstance(acquisition, Mapping) else {}
    event_id = str(
        envelope.get("official_event_id")
        or row.get("canonical_event_id")
        or row.get("official_event_id")
        or ""
    ).strip()
    snapshot = str(
        row.get("source_snapshot_id") or envelope.get("source_snapshot_id")
        or acquisition.get("source_snapshot_id") or ""
    ).strip()
    version = str(row.get("model_version") or row.get("model_artifact_version") or "").strip()
    kickoff = _instant(
        envelope.get("event_start_time_utc") or row.get("event_start_time_utc")
    )
    modeled_at = _instant(row.get("immutable_model_timestamp") or row.get("model_timestamp"))
    observed_at = _instant(context.get("observed_at"))
    if (
        not event_id or not snapshot or not version
        or kickoff is None or modeled_at is None or observed_at is None
        or str(context.get("official_event_id") or "") != event_id
        or str(context.get("modeled_source_snapshot_id") or "") != snapshot
        or str(context.get("model_version") or "") != version
        or modeled_at >= kickoff or observed_at >= kickoff
    ):
        return {**base, "status": "CONTEXT_INVALID_PROVENANCE"}

    if observed_at > modeled_at:
        return {
            **base,
            "status": "MATERIAL_CONTEXT_NEWER_THAN_MODEL",
            "model_recheck_required": True,
            "analyst_review_required": True,
            "review_flags": ["PREGAME_MODEL_REFRESH_REQUIRED"],
        }

    factors = context.get("factors")
    if not isinstance(factors, Mapping):
        return {**base, "status": "CONTEXT_INVALID_FACTOR_RECEIPTS"}

    documented: list[str] = []
    flags: set[str] = set()
    for family in RESEARCH_FACTOR_FAMILIES:
        item = factors.get(family)
        if not isinstance(item, Mapping):
            continue
        if (
            item.get("status") != "DOCUMENTED"
            or not str(item.get("source_receipt_id") or "").strip()
        ):
            continue
        documented.append(family)
        candidates = item.get("review_flags")
        if isinstance(candidates, list):
            flags.update(
                flag for flag in candidates
                if isinstance(flag, str) and flag in ALLOWED_RECHECK_FLAGS
            )

    return {
        **base,
        "status": "SOURCE_CONTEXT_RECORDED_UNCERTIFIED",
        "factor_families_documented": documented,
        "factor_families_missing": [
            family for family in RESEARCH_FACTOR_FAMILIES if family not in documented
        ],
        "review_flags": sorted(flags),
        "model_recheck_required": False,
        "analyst_review_required": bool(flags),
        "observed_at": observed_at.isoformat(),
    }
