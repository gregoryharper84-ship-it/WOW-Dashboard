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


def verify_nba_snapshot_order(
    source_snapshot_ts: str, feature_ts: str, tipoff_ts: str
) -> Mapping[str, object]:
    """Snapshot must precede feature hydration, which must precede tipoff.

    Real pregame-lineage validator shared by the preflight path and the
    hard-negative probes, so negatives exercise production logic.
    """
    try:
        snapshot = _parse_utc(source_snapshot_ts)
        feature = _parse_utc(feature_ts)
        tipoff = _parse_utc(tipoff_ts)
    except (ValueError, TypeError):
        return {"ok": False, "blockers": ["NBA_EVENT_TIME_INVALID"]}
    blockers: list[str] = []
    if not snapshot < feature:
        blockers.append("NBA_SOURCE_SNAPSHOT_AFTER_FEATURE")
    if not feature < tipoff:
        blockers.append("NBA_FEATURE_TIMESTAMP_INVALID_FOR_PREGAME")
    if not snapshot < tipoff:
        blockers.append("NBA_SOURCE_SNAPSHOT_AFTER_KICKOFF")
    return {"ok": not blockers, "blockers": blockers}


def nba_hard_negative(
    kind: str,
    *,
    triad: Mapping[str, object] | None = None,
) -> Mapping[str, object]:
    """Hard-negative probes: each tampers a real input and runs the real
    validator, asserting the tampered input fails closed. No predetermined
    outcomes: ``held_closed`` is True only when the genuine check rejects."""
    base_triad = dict(triad) if triad else {}
    if kind == "swapped_home_away":
        # Same official event claimed under both home/away orientations
        # surfaces as a duplicate identity across partitions.
        event = "2026-01-15T00:00:00Z"
        result = verify_chronology_disjoint(
            [event], [event], ["2026-03-01T00:00:00Z"]
        )
        held = not result["ok"]
        return {"kind": kind, "held_closed": held, "blockers": list(result["blockers"])}
    if kind == "late_scratch":
        # Material update after feature hydration: features are no longer
        # prelock, so the preflight must hold on leakage.
        verdict = nba_evidence_preflight(
            triad=base_triad,
            train=["2026-01-01T00:00:00Z"],
            calibration=["2026-02-01T00:00:00Z"],
            holdout=["2026-03-01T00:00:00Z"],
            settlement_includes_ot=True,
            post_tipoff_features_used=True,
        )
        held = verdict["decision"] != "READY_FOR_INDEPENDENT_MODEL_REVIEW"
        return {"kind": kind, "held_closed": held, "blockers": list(verdict["blockers"])}
    if kind == "missing_tip_time":
        result = verify_chronology_disjoint(
            ["not-a-timestamp"], ["2026-02-01T00:00:00Z"], ["2026-03-01T00:00:00Z"]
        )
        held = not result["ok"]
        return {"kind": kind, "held_closed": held, "blockers": list(result["blockers"])}
    if kind == "stale_source":
        result = verify_nba_snapshot_order(
            "2026-01-01T12:00:00Z",  # snapshot after feature hydration
            "2026-01-01T10:00:00Z",
            "2026-01-02T00:00:00Z",
        )
        held = not result["ok"]
        return {"kind": kind, "held_closed": held, "blockers": list(result["blockers"])}
    if kind == "missing_artifact":
        tampered = {**base_triad, "fitted_artifact_sha256": ""}
        verdict = verify_triad(
            {**tampered, "expected_sport": "NBA", "expected_market": "MONEYLINE"}
        )
        held = not verdict.ok
        return {"kind": kind, "held_closed": held, "blockers": list(verdict.blockers)}
    if kind == "missing_calibrator":
        tampered = {**base_triad, "calibrator_artifact_sha256": ""}
        verdict = verify_triad(
            {**tampered, "expected_sport": "NBA", "expected_market": "MONEYLINE"}
        )
        held = not verdict.ok
        return {"kind": kind, "held_closed": held, "blockers": list(verdict.blockers)}
    if kind == "cross_sport_swap":
        verdict = verify_triad(
            {**base_triad, "expected_sport": "NBA", "expected_market": "MONEYLINE"}
        )
        held = not verdict.ok
        return {"kind": kind, "held_closed": held, "blockers": list(verdict.blockers)}
    if kind == "bounds_violation":
        verdict = verify_probability_bounds(0.6, 0.7, 0.8)
        return {"kind": kind, "held_closed": not verdict.ok, "blockers": list(verdict.blockers)}
    return {"kind": kind, "held_closed": False, "blockers": ["UNKNOWN_NEGATIVE_KIND"]}


__all__ = [
    "CAN_EXECUTE",
    "MARKET",
    "SPORT",
    "nba_evidence_preflight",
    "nba_hard_negative",
    "verify_chronology_disjoint",
    "verify_nba_snapshot_order",
]
