from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from v17.mlb_game_winner_shadow_db_runner import (
    _automatic_boundaries,
    _require_shadow_flags,
    _rows_by,
    _iso_datetime,
)
from v17.mlb_game_winner_shadow_evaluation import (
    RETROSPECTIVE_PROVENANCE,
    EvidenceRow,
    EvaluationEvidenceError,
)


def _evidence(day: int) -> EvidenceRow:
    return EvidenceRow(
        event_id=f"game-{day:04d}",
        event_start_time=datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(days=day),
        feature_row={},
        home_win=bool(day % 2),
        provenance_status=RETROSPECTIVE_PROVENANCE,
    )


def test_iso_datetime_normalizes_zulu_to_utc():
    value = _iso_datetime("2026-09-06T18:10:00Z")
    assert value.tzinfo == timezone.utc
    assert value.isoformat() == "2026-09-06T18:10:00+00:00"


def test_shadow_flags_require_can_execute_false():
    _require_shadow_flags({"research_only": True, "can_execute": False}, source="x")
    with pytest.raises(EvaluationEvidenceError, match="GOVERNANCE_EXECUTION_FLAG_INVALID"):
        _require_shadow_flags({"research_only": True, "can_execute": True}, source="x")


def test_shadow_flags_require_research_only_when_present():
    with pytest.raises(EvaluationEvidenceError, match="GOVERNANCE_RESEARCH_FLAG_INVALID"):
        _require_shadow_flags({"research_only": False, "can_execute": False}, source="x")
    # Grades do not carry research_only in the live schema.
    _require_shadow_flags({"can_execute": False}, source="grade")


def test_rows_by_rejects_duplicate_immutable_identity():
    with pytest.raises(EvaluationEvidenceError, match="duplicate_id=a"):
        _rows_by([{"id": "a"}, {"id": "a"}], "id")


def test_automatic_boundaries_are_strict_and_leave_holdout():
    rows = [_evidence(i) for i in range(100)]
    train_end, calibration_end = _automatic_boundaries(rows)
    assert train_end < calibration_end
    assert train_end < rows[-1].event_start_time
    assert calibration_end < rows[-1].event_start_time


def test_automatic_boundaries_fail_on_tiny_cohort():
    with pytest.raises(EvaluationEvidenceError, match="retrospective<3"):
        _automatic_boundaries([_evidence(1), _evidence(2)])
