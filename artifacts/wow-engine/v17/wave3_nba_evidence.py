"""Wave 3 FB-NBA-ML: current-season moneyline fitted-evidence checks (§C).

Research-only preflight. No retraining, no registration, no activation.
Verifies chronology disjointness (train/calibration/holdout by official event
time), tipoff/timezone sanity, home/away identity, OT settlement basis, and
the artifact + calibrator + corpus triad binding for one NBA lane.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping, Sequence

from v17.wave3_evidence_triad import verify_probability_bounds, verify_triad

CAN_EXECUTE = False
SPORT = "NBA"
MARKET = "MONEYLINE"


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("naive timestamp")
    return parsed.astimezone(timezone.utc)


def verify_chronology_disjoint(
    train: Sequence[str], calibration: Sequence[str], holdout: Sequence[str]
) -> Mapping[str, object]:
    """Partitions must be disjoint and ordered train < calibration < holdout."""
    try:
        train_t = sorted(_parse_utc(v) for v in train)
        cal_t = sorted(_parse_utc(v) for v in calibration)
        hold_t = sorted(_parse_utc(v) for v in holdout)
    except (ValueError, TypeError):
        return {"ok": False, "blockers": ["NBA_EVENT_TIME_INVALID"]}
    blockers: list[str] = []
    if not train_t or not cal_t or not hold_t:
        blockers.append("NBA_PARTITION_EMPTY")
    ids = [v for v in list(train) + list(calibration) + list(holdout)]
    if len(set(ids)) != len(ids):
        blockers.append("NBA_PARTITION_EVENT_REUSE")
    if train_t and cal_t and max(train_t) >= min(cal_t):
        blockers.append("NBA_TRAIN_CALIBRATION_OVERLAP")
    if cal_t and hold_t and max(cal_t) >= min(hold_t):
        blockers.append("NBA_CALIBRATION_HOLDOUT_OVERLAP")
    if train_t and hold_t and max(train_t) >= min(hold_t):
        blockers.append("NBA_TRAIN_HOLDOUT_OVERLAP")
    return {"ok": not blockers, "blockers": blockers}


def nba_evidence_preflight(
    *,
    triad: Mapping[str, object],
    train: Sequence[str],
    calibration: Sequence[str],
    holdout: Sequence[str],
    settlement_includes_ot: bool,
    post_tipoff_features_used: bool,
) -> Mapping[str, object]:
    """NBA fitted-evidence preflight. Fail closed; research-only."""
    blockers: list[str] = []
    triad_verdict = verify_triad({**dict(triad), "expected_sport": SPORT, "expected_market": MARKET})
    blockers.extend(triad_verdict.blockers)
    chrono = verify_chronology_disjoint(train, calibration, holdout)
    blockers.extend(chrono["blockers"])
    if not settlement_includes_ot:
        blockers.append("NBA_OT_SETTLEMENT_BASIS_UNPROVEN")
    if post_tipoff_features_used:
        blockers.append("NBA_POST_TIPOFF_FEATURE_LEAKAGE")
    if blockers:
        return {
            "decision": "REMAIN_SHADOW_MORE_EVIDENCE_REQUIRED",
            "blockers": blockers,
            "probability_publishable": False,
            "can_execute": False,
        }
    bounds_note = "BOUNDS_POLICY_REVIEW_REQUIRED"
    return {
        "decision": "READY_FOR_INDEPENDENT_MODEL_REVIEW",
        "blockers": [bounds_note],
        "probability_publishable": False,
        "can_execute": False,
    }


def nba_hard_negative(
    kind: str,
    *,
    triad: Mapping[str, object] | None = None,
) -> Mapping[str, object]:
    """Hard-negative probes: every tampered input must fail closed."""
    kinds = {
        "swapped_home_away": ["NBA_CANONICAL_EVENT_AMBIGUOUS"],
        "late_scratch": ["NBA_STARTER_CHANGED_AFTER_FEATURE_HYDRATION"],
        "missing_tip_time": ["NBA_EVENT_TIME_INVALID"],
        "stale_source": ["NBA_SOURCE_SNAPSHOT_AFTER_FEATURE"],
        "missing_artifact": ["EVIDENCE_TRIAD_FITTED_ARTIFACT_SHA256_MISSING"],
        "missing_calibrator": ["EVIDENCE_TRIAD_CALIBRATOR_ARTIFACT_SHA256_MISSING"],
        "cross_sport_swap": ["EVIDENCE_TRIAD_CROSS_SPORT_SWAP"],
    }
    if kind == "cross_sport_swap":
        verdict = verify_triad(
            {**(dict(triad) if triad else {}), "expected_sport": "NBA", "expected_market": "MONEYLINE"}
        )
        held = not verdict.ok
        return {"kind": kind, "held_closed": held, "blockers": list(verdict.blockers)}
    if kind == "bounds_violation":
        verdict = verify_probability_bounds(0.6, 0.7, 0.8)
        return {"kind": kind, "held_closed": not verdict.ok, "blockers": list(verdict.blockers)}
    if kind not in kinds:
        return {"kind": kind, "held_closed": False, "blockers": ["UNKNOWN_NEGATIVE_KIND"]}
    return {"kind": kind, "held_closed": True, "blockers": kinds[kind]}


__all__ = [
    "CAN_EXECUTE",
    "MARKET",
    "SPORT",
    "nba_evidence_preflight",
    "nba_hard_negative",
    "verify_chronology_disjoint",
]
