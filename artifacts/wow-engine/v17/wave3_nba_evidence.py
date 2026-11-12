"""Wave 3 FB-NBA-ML: current-season moneyline fitted-evidence checks (§C).

Research-only preflight. No retraining, no registration, no activation.
Verifies chronology disjointness (train/calibration/holdout by official event
time), tipoff/timezone sanity, home/away identity, OT settlement basis, and
the artifact + calibrator + corpus triad binding for one NBA lane.

Review-ready decisions are mandatory-evidence: official event IDs, event
orientation records, full prelock lineage timestamps, lineup evidence and a
recomputable triad binding proof must all be present. Omission of any of them
fails closed — a lane cannot reach review on timestamps alone.
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


def _is_official_id(value: object) -> bool:
    """Type guard for official event identity: non-blank strings only.

    No coercion: ``str(None)`` (``"None"``) and ``str([])`` (``"[]"``) must
    never count as identity, and unhashable values must be rejected before
    any set/dict operation can crash on them.
    """
    return isinstance(value, str) and bool(value.strip())


def verify_chronology_disjoint(
    train: Sequence[str],
    calibration: Sequence[str],
    holdout: Sequence[str],
    *,
    train_ids: Sequence[str] | None = None,
    calibration_ids: Sequence[str] | None = None,
    holdout_ids: Sequence[str] | None = None,
) -> Mapping[str, object]:
    """Partitions must be disjoint and ordered train < calibration < holdout.

    Identity is checked on official event IDs whenever they are supplied;
    timestamp strings alone are never trusted as identity. When ID sequences
    are provided they must run parallel to the timestamp sequences.
    """
    try:
        train_t = sorted(_parse_utc(v) for v in train)
        cal_t = sorted(_parse_utc(v) for v in calibration)
        hold_t = sorted(_parse_utc(v) for v in holdout)
    except (ValueError, TypeError):
        return {"ok": False, "blockers": ["NBA_EVENT_TIME_INVALID"]}
    blockers: list[str] = []
    if not train_t or not cal_t or not hold_t:
        blockers.append("NBA_PARTITION_EMPTY")
    id_groups = (train_ids, calibration_ids, holdout_ids)
    if any(group is not None for group in id_groups):
        if (
            train_ids is None
            or calibration_ids is None
            or holdout_ids is None
        ):
            blockers.append("NBA_EVENT_ID_COVERAGE_INCOMPLETE")
        else:
            id_lists = (list(train_ids), list(calibration_ids), list(holdout_ids))
            time_lists = (list(train), list(calibration), list(holdout))
            for id_list, time_list in zip(id_lists, time_lists):
                if len(id_list) != len(time_list):
                    blockers.append("NBA_EVENT_ID_COUNT_MISMATCH")
                    break
                if any(not _is_official_id(v) for v in id_list):
                    blockers.append("NBA_EVENT_ID_MISSING")
                    break
            else:
                flat_ids = id_lists[0] + id_lists[1] + id_lists[2]
                if len(set(flat_ids)) != len(flat_ids):
                    blockers.append("NBA_PARTITION_EVENT_REUSE")
                for id_list in id_lists:
                    if len(set(id_list)) != len(id_list):
                        blockers.append("NBA_PARTITION_EVENT_REUSE")
                        break
    if all(group is None for group in id_groups):
        # Fallback identity only when no official event IDs are supplied.
        # Timestamp strings alone are never authoritative identity, so this
        # duplicate check applies solely to the ID-less fallback path.
        fallback_ids = [v for v in list(train) + list(calibration) + list(holdout)]
        if len(set(fallback_ids)) != len(fallback_ids):
            blockers.append("NBA_PARTITION_EVENT_REUSE")
    if train_t and cal_t and max(train_t) >= min(cal_t):
        blockers.append("NBA_TRAIN_CALIBRATION_OVERLAP")
    if cal_t and hold_t and max(cal_t) >= min(hold_t):
        blockers.append("NBA_CALIBRATION_HOLDOUT_OVERLAP")
    if train_t and hold_t and max(train_t) >= min(hold_t):
        blockers.append("NBA_TRAIN_HOLDOUT_OVERLAP")
    return {"ok": not blockers, "blockers": blockers}


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


def verify_nba_event_orientations(
    records: Sequence[Mapping[str, object]],
) -> Mapping[str, object]:
    """One official event ID must map to exactly one home/away orientation.

    The exact swapped-home/away failure — the same event claimed once as
    team A hosting team B and again as B hosting A — is an identity conflict
    even when every timestamp parses. Records carry ``official_event_id``,
    ``home_team_id`` and ``away_team_id``.
    """
    blockers: list[str] = []
    orientation_by_id: dict[str, tuple[str, str]] = {}
    for record in records:
        if not isinstance(record, Mapping):
            blockers.append("NBA_EVENT_IDENTITY_INCOMPLETE")
            continue
        event_id = record.get("official_event_id")
        home = record.get("home_team_id")
        away = record.get("away_team_id")
        if not (
            _is_official_id(event_id)
            and _is_official_id(home)
            and _is_official_id(away)
        ):
            blockers.append("NBA_EVENT_IDENTITY_INCOMPLETE")
            continue
        if str(home).strip() == str(away).strip():
            blockers.append("NBA_EVENT_IDENTITY_INCOMPLETE")
            continue
        orientation = (home, away)
        previous = orientation_by_id.get(event_id)
        if previous is None:
            orientation_by_id[event_id] = orientation
        elif previous != orientation:
            blockers.append("NBA_HOME_AWAY_ORIENTATION_CONFLICT")
    return {"ok": not blockers, "blockers": blockers}


def verify_nba_lineup_freshness(
    feature_ts: str, lineup_evidence: Mapping[str, object]
) -> Mapping[str, object]:
    """A material starter/rotation change at or after feature hydration voids
    prelock status. This is the exact late-scratch scenario: features hydrated
    from a rotation that is no longer current must hold, not score."""
    try:
        feature = _parse_utc(feature_ts)
    except (ValueError, TypeError):
        return {"ok": False, "blockers": ["NBA_EVENT_TIME_INVALID"]}
    changed = bool(lineup_evidence.get("rotation_changed", False))
    if not changed:
        return {"ok": True, "blockers": []}
    update_raw = lineup_evidence.get("latest_material_update_ts")
    if update_raw is None or not str(update_raw).strip():
        return {"ok": False, "blockers": ["NBA_LINEUP_UPDATE_TIME_UNVERIFIED"]}
    try:
        updated = _parse_utc(str(update_raw))
    except (ValueError, TypeError):
        return {"ok": False, "blockers": ["NBA_LINEUP_UPDATE_TIME_UNVERIFIED"]}
    if updated >= feature:
        return {
            "ok": False,
            "blockers": ["NBA_STARTER_CHANGED_AFTER_FEATURE_HYDRATION"],
        }
    return {"ok": True, "blockers": []}


def nba_evidence_preflight(
    *,
    triad: Mapping[str, object],
    train: Sequence[str],
    calibration: Sequence[str],
    holdout: Sequence[str],
    settlement_includes_ot: bool,
    post_tipoff_features_used: bool,
    train_ids: Sequence[str] | None = None,
    calibration_ids: Sequence[str] | None = None,
    holdout_ids: Sequence[str] | None = None,
    source_snapshot_ts: str | None = None,
    feature_ts: str | None = None,
    tipoff_ts: str | None = None,
    event_records: Sequence[Mapping[str, object]] | None = None,
    lineup_evidence: Mapping[str, object] | None = None,
    bounds_policy_approved: bool = False,
) -> Mapping[str, object]:
    """NBA fitted-evidence preflight. Fail closed; research-only.

    Mandatory evidence for any review-ready decision: official event IDs,
    event orientation records, complete prelock lineage timestamps, lineup
    evidence, an approved dynamic bounds policy and a recomputable triad
    binding proof. Omitting any of them is a blocker, not a silent pass;
    no READY decision ever carries an outstanding blocker.
    """
    blockers: list[str] = []
    triad_verdict = verify_triad(
        {**dict(triad), "expected_sport": SPORT, "expected_market": MARKET},
        require_binding=True,
    )
    blockers.extend(triad_verdict.blockers)
    if train_ids is None or calibration_ids is None or holdout_ids is None:
        blockers.append("NBA_EVENT_IDS_REQUIRED")
        chrono = verify_chronology_disjoint(train, calibration, holdout)
    else:
        chrono = verify_chronology_disjoint(
            train,
            calibration,
            holdout,
            train_ids=train_ids,
            calibration_ids=calibration_ids,
            holdout_ids=holdout_ids,
        )
    blockers.extend(chrono["blockers"])
    if not settlement_includes_ot:
        blockers.append("NBA_OT_SETTLEMENT_BASIS_UNPROVEN")
    if post_tipoff_features_used:
        blockers.append("NBA_POST_TIPOFF_FEATURE_LEAKAGE")
    if (
        source_snapshot_ts is None
        or feature_ts is None
        or tipoff_ts is None
    ):
        blockers.append("NBA_PRELOCK_LINEAGE_REQUIRED")
    else:
        lineage = verify_nba_snapshot_order(
            str(source_snapshot_ts), str(feature_ts), str(tipoff_ts)
        )
        blockers.extend(lineage["blockers"])
    if not event_records:
        blockers.append("NBA_EVENT_ORIENTATION_EVIDENCE_MISSING")
    else:
        orientations = verify_nba_event_orientations(event_records)
        blockers.extend(orientations["blockers"])
    if not lineup_evidence:
        blockers.append("NBA_LINEUP_EVIDENCE_MISSING")
    elif feature_ts is not None:
        freshness = verify_nba_lineup_freshness(str(feature_ts), lineup_evidence)
        blockers.extend(freshness["blockers"])
    if not bounds_policy_approved:
        blockers.append("NBA_DYNAMIC_BOUNDS_POLICY_NOT_APPROVED")
    if blockers:
        return {
            "decision": "REMAIN_SHADOW_MORE_EVIDENCE_REQUIRED",
            "blockers": blockers,
            "probability_publishable": False,
            "can_execute": False,
        }
    return {
        "decision": "READY_FOR_INDEPENDENT_MODEL_REVIEW",
        "blockers": [],
        "probability_publishable": False,
        "can_execute": False,
    }


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
        # Exact scenario: the same official event claimed twice with the
        # home/away orientation swapped.
        result = verify_nba_event_orientations([
            {"official_event_id": "espn-401999001", "home_team_id": "espn-1", "away_team_id": "espn-2"},
            {"official_event_id": "espn-401999001", "home_team_id": "espn-2", "away_team_id": "espn-1"},
        ])
        held = not result["ok"]
        return {"kind": kind, "held_closed": held, "blockers": list(result["blockers"])}
    if kind == "late_scratch":
        # Exact scenario: a material starter change timestamped after the
        # feature hydration it would invalidate.
        result = verify_nba_lineup_freshness(
            "2026-01-15T16:00:00Z",
            {
                "rotation_changed": True,
                "latest_material_update_ts": "2026-01-15T16:30:00Z",
            },
        )
        held = not result["ok"]
        return {"kind": kind, "held_closed": held, "blockers": list(result["blockers"])}
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
    "verify_nba_event_orientations",
    "verify_nba_lineup_freshness",
    "verify_nba_snapshot_order",
]
