"""Wave 3 FB-NHL-ML: reproducible full-game moneyline evidence checks (§D).

Research-only. Full-game WIN includes OT/shootout under exact settlement, not
regulation 1X2. Reconciles the historical train/calibration/test accounting
(6,128 unique-game total; rejects the contradictory 8,580 claim), verifies
game-ID uniqueness across partitions, and holds on goalie/source-rights until
a permitted goalie source plus second corroboration are proven.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from v17.wave3_evidence_triad import verify_triad

CAN_EXECUTE = False
SPORT = "NHL"
MARKET = "MONEYLINE"
CANONICAL_UNIQUE_TOTAL = 6128
CONTRADICTED_TOTAL = 8580


def reconcile_partition_accounting(
    train_ids: Sequence[str], calibration_ids: Sequence[str], test_ids: Sequence[str]
) -> Mapping[str, object]:
    """Unique-game accounting: total must equal 6,128; reject 8,580."""
    train = list(train_ids)
    cal = list(calibration_ids)
    test = list(test_ids)
    total = len(train) + len(cal) + len(test)
    unique = len(set(train) | set(cal) | set(test))
    blockers: list[str] = []
    if total != unique:
        blockers.append("NHL_PARTITION_EVENT_REUSE")
    if unique != CANONICAL_UNIQUE_TOTAL:
        blockers.append(f"NHL_UNIQUE_TOTAL_{unique}_NE_{CANONICAL_UNIQUE_TOTAL}")
    if total == CONTRADICTED_TOTAL or unique == CONTRADICTED_TOTAL:
        blockers.append("NHL_CONTRADICTED_8580_TOTAL_REJECTED")
    overlap = (set(train) & set(cal)) | (set(cal) & set(test)) | (set(train) & set(test))
    if overlap:
        blockers.append("NHL_DUPLICATE_GAME_CONFLICT")
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
) -> Mapping[str, object]:
    """NHL evidence preflight. Fail closed; research-only HOLD by default."""
    blockers: list[str] = []
    triad_verdict = verify_triad({**dict(triad), "expected_sport": SPORT, "expected_market": MARKET})
    blockers.extend(triad_verdict.blockers)
    accounting = reconcile_partition_accounting(train_ids, calibration_ids, test_ids)
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
