"""Research-only opportunity-chain features for WOW_JS_STYLE_INTELLIGENCE_V17.

The module describes how much a prop depends on upstream opportunity creation
(team possession, territory, role, minutes, etc.) and how demanding the exact
settlement threshold is relative to a role-adjusted distribution.

It is deliberately not a sporting probability model. Outputs cannot publish a
probability, become rank eligible, change typed failures, or authorize execution.
Live capture state is also isolated so live box-score progress cannot leak into
pregame JS feature cohorts.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any, Mapping

WOW_JS_STYLE_INTELLIGENCE_V17 = "WOW_JS_STYLE_INTELLIGENCE_V17"
JS_STYLE_FEATURE_SCHEMA_V1 = "JS_STYLE_FEATURE_SCHEMA_V1"
JS_STYLE_RULESET_V1 = "JS_STYLE_RULESET_V1"

PREGAME = "PREGAME"
LIVE = "LIVE"
UNKNOWN = "UNKNOWN"

_DEPENDENCY_FIELDS = (
    "upstream_team_dependency",
    "player_role_dependency",
    "possession_dependency",
    "territory_dependency",
    "minutes_dependency",
)


class JsOpportunityChainError(ValueError):
    """Typed validation error for JS research evidence."""


def _finite(value: Any, *, field: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise JsOpportunityChainError(f"{field} must be numeric") from exc
    if not isfinite(out):
        raise JsOpportunityChainError(f"{field} must be finite")
    return out


def _unit_interval(value: Any, *, field: str) -> float:
    out = _finite(value, field=field)
    if not 0.0 <= out <= 1.0:
        raise JsOpportunityChainError(f"{field} must be between 0 and 1")
    return out


def _required_text(value: Any, *, field: str) -> str:
    text = str(value or "").strip().upper()
    if not text:
        raise JsOpportunityChainError(f"{field} is required")
    return text


def _parse_time(value: Any, *, field: str) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise JsOpportunityChainError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise JsOpportunityChainError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _capture_state(evidence: Mapping[str, Any]) -> dict[str, Any]:
    declared = str(evidence.get("capture_phase") or UNKNOWN).strip().upper()
    if declared not in {PREGAME, LIVE, UNKNOWN}:
        raise JsOpportunityChainError("capture_phase must be PREGAME, LIVE, or UNKNOWN")

    captured_at = _parse_time(evidence.get("captured_at"), field="captured_at")
    event_start = _parse_time(evidence.get("event_start_time"), field="event_start_time")
    original_selection = _parse_time(
        evidence.get("original_selection_time"), field="original_selection_time"
    )
    feature_snapshot = _parse_time(
        evidence.get("feature_snapshot_time"), field="feature_snapshot_time"
    )

    derived = declared
    if captured_at is not None and event_start is not None:
        derived = PREGAME if captured_at < event_start else LIVE

    selection_proven_pregame = bool(
        original_selection is not None
        and event_start is not None
        and original_selection < event_start
    )

    if feature_snapshot is None and derived == PREGAME:
        feature_snapshot = captured_at
    feature_snapshot_proven_pregame = bool(
        feature_snapshot is not None
        and event_start is not None
        and feature_snapshot < event_start
    )

    common = {
        "selection_style_evidence_eligible": True,
        "live_feature_inputs_allowed": False,
        "selection_proven_pregame": selection_proven_pregame,
        "feature_snapshot_proven_pregame": feature_snapshot_proven_pregame,
    }

    if event_start is None:
        return {
            **common,
            "capture_phase": derived,
            "pregame_training_eligible": False,
            "research_state": "JS_EVENT_START_UNAVAILABLE",
        }

    if derived == LIVE:
        if not selection_proven_pregame:
            return {
                **common,
                "capture_phase": LIVE,
                "pregame_training_eligible": False,
                "research_state": "JS_LIVE_CAPTURE_EXCLUDED_FROM_PREGAME_COHORT",
            }
        if not feature_snapshot_proven_pregame:
            return {
                **common,
                "capture_phase": LIVE,
                "pregame_training_eligible": False,
                "research_state": "JS_LIVE_CAPTURE_SELECTION_ONLY",
            }
        return {
            **common,
            "capture_phase": LIVE,
            "pregame_training_eligible": True,
            "research_state": "JS_RESEARCH_READY",
        }

    if derived == UNKNOWN:
        if selection_proven_pregame and feature_snapshot_proven_pregame:
            return {
                **common,
                "capture_phase": UNKNOWN,
                "pregame_training_eligible": True,
                "research_state": "JS_RESEARCH_READY",
            }
        return {
            **common,
            "capture_phase": UNKNOWN,
            "pregame_training_eligible": False,
            "research_state": "JS_CAPTURE_PHASE_UNKNOWN",
        }

    if not feature_snapshot_proven_pregame:
        return {
            **common,
            "capture_phase": PREGAME,
            "pregame_training_eligible": False,
            "research_state": "JS_PREGAME_FEATURE_SNAPSHOT_UNPROVEN",
        }

    return {
        **common,
        "capture_phase": PREGAME,
        "pregame_training_eligible": True,
        "research_state": "JS_RESEARCH_READY",
    }


def build_opportunity_chain_features(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Build descriptive opportunity-chain features from pregame-safe evidence.

    `threshold_burden_robust` is direction neutral:
      LESS -> (line - role_adjusted_median) / robust_dispersion
      MORE -> (role_adjusted_median - line) / robust_dispersion

    Positive values mean the selected direction has threshold room relative to the
    supplied distribution. Direction itself contributes no score.

    `opportunity_chain_dependency` is a descriptive 0..1 index built from five
    upstream dependency inputs plus inverse self-generation. It is not a hit rate,
    probability, confidence, calibration input, or rank authority.
    """
    direction = str(evidence.get("direction") or "").strip().upper()
    if direction not in {"MORE", "LESS"}:
        raise JsOpportunityChainError("direction must be MORE or LESS")

    period = _required_text(evidence.get("period"), field="period")
    line = _finite(evidence.get("exact_line"), field="exact_line")
    role_median = _finite(evidence.get("role_adjusted_median"), field="role_adjusted_median")
    dispersion = _finite(evidence.get("robust_dispersion"), field="robust_dispersion")
    if dispersion <= 0.0:
        raise JsOpportunityChainError("robust_dispersion must be > 0")

    dependencies = {
        field: _unit_interval(evidence.get(field), field=field)
        for field in _DEPENDENCY_FIELDS
    }
    self_generation = _unit_interval(
        evidence.get("stat_self_generation_score"),
        field="stat_self_generation_score",
    )

    dependency_mean = sum(dependencies.values()) / len(_DEPENDENCY_FIELDS)
    opportunity_chain_dependency = (
        0.75 * dependency_mean + 0.25 * (1.0 - self_generation)
    )

    if direction == "LESS":
        threshold_burden = (line - role_median) / dispersion
    else:
        threshold_burden = (role_median - line) / dispersion

    # Preserve sign while increasing descriptive weight when a stat depends more
    # heavily on upstream opportunity creation. This remains research metadata.
    opportunity_chain_burden = threshold_burden * (
        0.5 + 0.5 * opportunity_chain_dependency
    )

    if threshold_burden > 0.0:
        archetype = (
            "JS_OPPORTUNITY_CHAIN_LESS"
            if direction == "LESS"
            else "JS_FLOOR_MORE"
        )
    else:
        archetype = "JS_OPPORTUNITY_CHAIN_COUNTEREVIDENCE"

    capture = _capture_state(evidence)

    return {
        "js_style_version": WOW_JS_STYLE_INTELLIGENCE_V17,
        "feature_schema_version": JS_STYLE_FEATURE_SCHEMA_V1,
        "ruleset_version": JS_STYLE_RULESET_V1,
        "research_only": True,
        "js_probability_authority": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
        "direction": direction,
        "period": period,
        "exact_line": line,
        "role_adjusted_median": role_median,
        "robust_dispersion": dispersion,
        "threshold_burden_robust": threshold_burden,
        "opportunity_chain_dependency": opportunity_chain_dependency,
        "opportunity_chain_burden": opportunity_chain_burden,
        "stat_self_generation_score": self_generation,
        "archetype": archetype,
        **dependencies,
        **capture,
    }
