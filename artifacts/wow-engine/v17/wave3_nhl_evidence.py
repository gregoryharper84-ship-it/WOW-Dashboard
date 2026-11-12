"""Wave 3 FB-NHL-ML: reproducible full-game moneyline evidence checks (§D).

Research-only. Full-game WIN includes OT/shootout under exact settlement, not
regulation 1X2. Reconciles the historical train/calibration/test accounting
(6,128 unique-game total; rejects the contradictory 8,580 claim), verifies
game-ID uniqueness across partitions, and holds on goalie/source-rights until
a permitted goalie source plus second corroboration are proven.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping, Sequence

from v17.wave3_evidence_triad import verify_triad

CAN_EXECUTE = False
SPORT = "NHL"
MARKET = "MONEYLINE"
CANONICAL_UNIQUE_TOTAL = 6128
CONTRADICTED_TOTAL = 8580


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("naive timestamp")
    return parsed.astimezone(timezone.utc)


def _is_official_id(value: object) -> bool:
    """Type guard for official game identity: non-blank strings only.

    No coercion: ``str(None)`` (``"None"``) must never count as identity,
    and unhashable values must be rejected before any set operation.
    """
    return isinstance(value, str) and bool(value.strip())


def reconcile_partition_accounting(
    train_ids: Sequence[str],
    calibration_ids: Sequence[str],
    test_ids: Sequence[str],
    *,
    train_times: Sequence[str] | None = None,
    calibration_times: Sequence[str] | None = None,
    test_times: Sequence[str] | None = None,
) -> Mapping[str, object]:
    """Unique-game accounting: total must equal 6,128; reject 8,580.

    Every partition must be non-empty: an empty calibration or test split
    cannot certify anything and fails closed. When event-time sequences are
    supplied they must run parallel to the ID sequences and prove the
    chronological order train < calibration < test; unverified chronology
    fails closed.
    """
    train = list(train_ids)
    cal = list(calibration_ids)
    test = list(test_ids)
    total = len(train) + len(cal) + len(test)
    blockers: list[str] = []
    if not train:
        blockers.append("NHL_TRAIN_PARTITION_EMPTY")
    if not cal:
        blockers.append("NHL_CALIBRATION_PARTITION_EMPTY")
    if not test:
        blockers.append("NHL_TEST_PARTITION_EMPTY")
    ids_valid = all(_is_official_id(game_id) for game_id in train + cal + test)
    if not ids_valid:
        blockers.append("NHL_GAME_ID_MISSING")
    # Set operations run only on validated string IDs: unhashable values
    # are already held above and must never crash the accounting.
    unique = -1
    if ids_valid:
        unique = len(set(train) | set(cal) | set(test))
        if total != unique:
            blockers.append("NHL_PARTITION_EVENT_REUSE")
        if total == CONTRADICTED_TOTAL or unique == CONTRADICTED_TOTAL:
            blockers.append("NHL_CONTRADICTED_8580_TOTAL_REJECTED")
        overlap = (set(train) & set(cal)) | (set(cal) & set(test)) | (set(train) & set(test))
        if overlap:
            blockers.append("NHL_DUPLICATE_GAME_CONFLICT")
    if unique != CANONICAL_UNIQUE_TOTAL:
        blockers.append(f"NHL_UNIQUE_TOTAL_{unique}_NE_{CANONICAL_UNIQUE_TOTAL}")
    time_groups = (train_times, calibration_times, test_times)
    if any(group is not None for group in time_groups):
        if train_times is None or calibration_times is None or test_times is None:
            blockers.append("NHL_PARTITION_CHRONOLOGY_UNVERIFIED")
        else:
            id_lists = (train, cal, test)
            time_lists = (list(train_times), list(calibration_times), list(test_times))
            try:
                parsed = [
                    sorted(_parse_utc(v) for v in times) for times in time_lists
                ]
                for id_list, time_list in zip(id_lists, time_lists):
                    if len(id_list) != len(time_list):
                        blockers.append("NHL_PARTITION_CHRONOLOGY_UNVERIFIED")
                        break
                else:
                    if max(parsed[0]) >= min(parsed[1]):
                        blockers.append("NHL_TRAIN_CALIBRATION_OVERLAP")
                    if max(parsed[1]) >= min(parsed[2]):
                        blockers.append("NHL_CALIBRATION_TEST_OVERLAP")
                    if max(parsed[0]) >= min(parsed[2]):
                        blockers.append("NHL_TRAIN_TEST_OVERLAP")
            except (ValueError, TypeError):
                blockers.append("NHL_EVENT_TIME_INVALID")
    return {
        "train_n": len(train),
        "calibration_n": len(cal),
        "test_n": len(test),
        "unique_total": unique,
        "ok": not blockers,
        "blockers": blockers,
    }


def nhl_evidence_preflight(
    *,
    triad: Mapping[str, object],
    train_ids: Sequence[str],
    calibration_ids: Sequence[str],
    test_ids: Sequence[str],
    settlement_includes_ot_shootout: bool,
    goalie_source_permitted: bool,
    goalie_second_corroboration: bool,
    goalie_confirmation_prelock: bool,
    train_times: Sequence[str] | None = None,
    calibration_times: Sequence[str] | None = None,
    test_times: Sequence[str] | None = None,
) -> Mapping[str, object]:
    """NHL evidence preflight. Fail closed; research-only HOLD by default.

    The triad binding proof is required, and partition event times are
    mandatory: a review-ready decision cannot omit chronology evidence.
    The standalone reconciliation also fails closed on empty partitions
    and ID reuse.
    """
    blockers: list[str] = []
    triad_verdict = verify_triad(
        {**dict(triad), "expected_sport": SPORT, "expected_market": MARKET},
        require_binding=True,
    )
    blockers.extend(triad_verdict.blockers)
    if (
        train_times is None
        or calibration_times is None
        or test_times is None
    ):
        blockers.append("NHL_PARTITION_CHRONOLOGY_UNVERIFIED")
    accounting = reconcile_partition_accounting(
        train_ids,
        calibration_ids,
        test_ids,
        train_times=train_times,
        calibration_times=calibration_times,
        test_times=test_times,
    )
    blockers.extend(accounting["blockers"])
    if not settlement_includes_ot_shootout:
        blockers.append("NHL_REGULATION_1X2_NOT_FULL_GAME_WIN")
    if not (goalie_source_permitted and goalie_second_corroboration):
        blockers.append("SOURCE_RIGHTS_OR_GOALIE_DATA_HOLD")
    if not goalie_confirmation_prelock:
        blockers.append("NHL_GOALIE_PRELOCK_CONFIRMATION_MISSING")
    if blockers:
        return {
            "decision": "REMAIN_SHADOW_MORE_EVIDENCE_REQUIRED",
            "accounting": accounting,
            "blockers": blockers,
            "probability_publishable": False,
            "can_execute": False,
        }
    return {
        "decision": "READY_FOR_INDEPENDENT_MODEL_REVIEW",
        "accounting": accounting,
        "blockers": [],
        "probability_publishable": False,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "CANONICAL_UNIQUE_TOTAL",
    "CONTRADICTED_TOTAL",
    "MARKET",
    "SPORT",
    "nhl_evidence_preflight",
    "reconcile_partition_accounting",
]
