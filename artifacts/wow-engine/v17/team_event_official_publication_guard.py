"""Fail-closed admission guard for official V17 team/event leaderboards.

This module is intentionally downstream of sporting inference. It never creates,
changes, calibrates, or substitutes a probability. Its only job is to prevent a
research/shadow/held probability package from being presented as an official
WOW V17 winner/upset ranking or official card selection.

Incident basis: 2026-09-04 Athletics @ Mariners (MLB event 823093). A forward
shadow research score existed and was correctly marked probability_publishable
=false, yet was later surfaced conversationally as a strongest/#1 selection.
The sporting miss itself is not the patch trigger; the publication-boundary
promotion is.

can_execute=false remains invariant.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping


OFFICIAL_TERMINAL_REDUCER = "V17_TERMINAL_REDUCER"
_SHADOW_STATUS_TOKENS = (
    "SHADOW_SCORED",
    "FORWARD_SHADOW",
    "RESEARCH_ONLY",
    "PASS_RESEARCH_BOUND",
)


@dataclass(frozen=True)
class OfficialPublicationAudit:
    eligible: bool
    blockers: tuple[str, ...]
    can_execute: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "official_rank_eligible": self.eligible,
            "blockers": list(self.blockers),
            "can_execute": False,
        }


def _finite_probability(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or not 0.0 < parsed < 1.0:
        return None
    return parsed


def _status_values(row: Mapping[str, Any]) -> tuple[str, ...]:
    keys = (
        "score_status",
        "status",
        "model_status",
        "probability_status",
        "calibration_status",
        "home_bound_status",
        "away_bound_status",
        "sporting_probability_status",
    )
    values: list[str] = []
    for key in keys:
        value = row.get(key)
        if value is not None:
            values.append(str(value).strip().upper())
    return tuple(values)


def _has_shadow_or_research_status(row: Mapping[str, Any]) -> bool:
    return any(
        token in status
        for status in _status_values(row)
        for token in _SHADOW_STATUS_TOKENS
    )


def _terminal_authority(row: Mapping[str, Any]) -> str | None:
    direct = row.get("global_terminal_authority")
    if direct is not None:
        return str(direct)
    reducer_input = row.get("terminal_reducer_input")
    if isinstance(reducer_input, Mapping):
        nested = reducer_input.get("global_terminal_reducer")
        if nested is not None:
            return str(nested)
    return None


def _probability_bounds_blockers(row: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    for side in ("home", "away"):
        calibrated = _finite_probability(row.get(f"calibrated_{side}_probability"))
        lower = _finite_probability(row.get(f"calibrated_{side}_lower_bound"))
        upper = _finite_probability(row.get(f"calibrated_{side}_upper_bound"))
        if calibrated is None or lower is None or upper is None:
            blockers.append(f"{side.upper()}_CALIBRATED_PACKAGE_INCOMPLETE")
            continue
        if not lower < calibrated < upper:
            blockers.append(f"{side.upper()}_CALIBRATED_BOUND_NOT_STRICT")
    if not str(row.get("bounds_method_version") or "").strip():
        blockers.append("BOUNDS_METHOD_VERSION_MISSING")
    return blockers


def _failure_path_blockers(row: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    package = row.get("favorite_failure_paths_json")
    if not isinstance(package, Mapping):
        blockers.append("FAVORITE_FAILURE_PATH_PACKAGE_MISSING")
        return blockers

    regimes = package.get("regimes")
    if not isinstance(regimes, list) or len(regimes) < 2:
        blockers.append("FAVORITE_FAILURE_REGIMES_INSUFFICIENT")
    else:
        valid_regimes = 0
        for regime in regimes:
            if not isinstance(regime, Mapping) or not str(regime.get("name") or "").strip():
                continue
            probability = regime.get("loss_joint_probability")
            try:
                p = float(probability)
            except (TypeError, ValueError):
                continue
            if math.isfinite(p) and 0.0 <= p <= 1.0:
                valid_regimes += 1
        if valid_regimes < 2:
            blockers.append("FAVORITE_FAILURE_REGIMES_NOT_NUMERIC")

    largest = row.get("largest_favorite_loss_path") or package.get("largest_favorite_loss_path")
    if not str(largest or "").strip():
        blockers.append("LARGEST_FAVORITE_LOSS_PATH_MISSING")

    failure_probability = row.get("favorite_failure_path_probability")
    if failure_probability is None:
        failure_probability = package.get("favorite_failure_path_probability")
    try:
        failure_p = float(failure_probability)
    except (TypeError, ValueError):
        failure_p = math.nan
    if not math.isfinite(failure_p) or not 0.0 <= failure_p <= 1.0:
        blockers.append("FAVORITE_FAILURE_PATH_PROBABILITY_INVALID")

    if not str(row.get("regime_model_version") or package.get("schema_version") or "").strip():
        blockers.append("FAILURE_REGIME_MODEL_VERSION_MISSING")
    return blockers


def _mlb_lineup_blockers(row: Mapping[str, Any]) -> list[str]:
    if str(row.get("sport") or row.get("league") or "").strip().upper() not in {
        "MLB",
        "BASEBALL_MLB",
    }:
        return []

    lineup = row.get("lineup_context")
    if not isinstance(lineup, Mapping):
        return ["MLB_CONFIRMED_LINEUP_CONTEXT_MISSING"]
    blockers: list[str] = []
    if str(lineup.get("status") or "").strip().upper() != "CONFIRMED":
        blockers.append("MLB_LINEUP_NOT_CONFIRMED_IN_MODEL_PACKAGE")
    if not str(lineup.get("lineup_identity_sha256") or "").strip():
        blockers.append("MLB_LINEUP_IDENTITY_FINGERPRINT_MISSING")
    if not str(lineup.get("model_version") or "").strip():
        blockers.append("MLB_LINEUP_MODEL_VERSION_MISSING")
    return blockers


def audit_official_team_event_publication(row: Mapping[str, Any]) -> OfficialPublicationAudit:
    """Return whether a completed row may enter an official V17 leaderboard.

    Research/shadow probabilities may remain visible in a diagnostic section,
    but they cannot be ranked, called a best pick, or admitted to an official
    card by this guard.
    """
    blockers: list[str] = []

    if row.get("can_execute") is not False:
        blockers.append("CAN_EXECUTE_INVARIANT_NOT_PROVEN")
    if row.get("probability_publishable") is not True:
        blockers.append("PROBABILITY_NOT_PUBLISHABLE")
    if row.get("rank_eligible") is not True:
        blockers.append("RANK_ELIGIBILITY_NOT_PROVEN")
    if row.get("model_probability_publishable") is not True:
        blockers.append("MODEL_PROBABILITY_NOT_PUBLISHABLE")
    if _has_shadow_or_research_status(row):
        blockers.append("SHADOW_OR_RESEARCH_STATUS_NOT_OFFICIAL")

    if _terminal_authority(row) != OFFICIAL_TERMINAL_REDUCER:
        blockers.append("V17_TERMINAL_REDUCER_RECEIPT_MISSING")

    calibration_health = str(row.get("calibration_health_status") or "").strip().upper()
    if calibration_health != "PASS":
        blockers.append("CALIBRATION_HEALTH_NOT_PASS")

    if row.get("model_valid_after_latest_update") is not True:
        blockers.append("MODEL_NOT_VALID_AFTER_LATEST_UPDATE")
    if not str(row.get("model_inputs_hash") or "").strip():
        blockers.append("MODEL_INPUTS_HASH_MISSING")

    blockers.extend(_probability_bounds_blockers(row))
    blockers.extend(_failure_path_blockers(row))
    blockers.extend(_mlb_lineup_blockers(row))

    unique = tuple(sorted(set(blockers)))
    return OfficialPublicationAudit(eligible=not unique, blockers=unique, can_execute=False)


def official_team_event_rank_eligible(row: Mapping[str, Any]) -> bool:
    """Convenience predicate for leaderboard/card builders."""
    return audit_official_team_event_publication(row).eligible
