"""Source-bound NFL second-refresh evidence, never borrowed stage proof.

A passing fitted package and a passing calendar refresh are not independently
sufficient for official publication; dynamic-calibration producer proof remains
unproven until the controlling specialist emits it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from v17.stage_proof_contract import (
    EVIDENCE_EMITTED,
    EVIDENCE_FAILED,
    EVIDENCE_NOT_EMITTED,
    EVIDENCE_CONFLICT,
    EVIDENCE_MALFORMED,
    EVIDENCE_STALE,
    STAGE_DYNAMIC_CALIBRATION,
    STAGE_FINAL_REFRESH,
    build_nfl_stage_proof_contract,
    contract_publication_block,
)

SOURCE_ID = "b46a5f95-01b6-4374-940e-1c91ddca8c0f"


def _refresh():
    return {
        "source": "NFL_CANONICAL_SCHEDULE_REQUERY",
        "checked_at": "2026-10-25T11:30:34+00:00",
        "model_timestamp": "2026-10-25T11:30:33+00:00",
        "scored_source_snapshot_id": SOURCE_ID,
        "refresh_source_snapshot_id": SOURCE_ID,
        "refresh_source_timestamp": "2026-10-24T16:09:13+00:00",
        "final_refresh_status": "PASS",
        "reasons": [],
        "probability_invalidated": False,
        "rerun_required": False,
        "can_execute": False,
    }


def _contract(receipt):
    return build_nfl_stage_proof_contract(
        research_run_id="v17-daily-current",
        event_key="NFL:2026_07_SF_ATL",
        official_event_id="2026_07_SF_ATL",
        score_snapshot_id="score-immutable",
        source_snapshot_id=SOURCE_ID,
        model_artifact_id="fitted-nfl-artifact",
        model_version="NFL_EVENT_LOGREG_PLATT_V1",
        package_validated=True,
        governance_from_bridge=True,
        probability_audit_result="PASS_PROBABILITY_AUDIT",
        event_mutex_status="PASS",
        calibration_health_status="PASS",
        final_refresh=receipt,
    )


def test_exact_second_requery_can_prove_refresh_but_not_dynamic_calibration():
    contract = _contract(_refresh())
    refresh = contract["stages"][STAGE_FINAL_REFRESH]
    assert refresh["evidence_type"] == EVIDENCE_EMITTED
    assert refresh["disposition"] == "PASS"
    assert refresh["authority"] == "v17.nfl_team_event_publication._nfl_final_refresh"
    assert refresh["bound_official_event_id"] == "2026_07_SF_ATL"
    assert refresh["bound_score_snapshot_id"] == "score-immutable"
    assert refresh["scored_source_snapshot_id"] == SOURCE_ID
    assert refresh["refresh_source_snapshot_id"] == SOURCE_ID
    assert refresh["borrowed_mlb_or_flask_receipt"] is False
    assert refresh["scheduled_event_status_accepted_as_proof"] is False
    assert contract["stages"][STAGE_DYNAMIC_CALIBRATION]["evidence_type"] == EVIDENCE_NOT_EMITTED
    assert contract_publication_block(contract) is not None
    assert contract["can_execute"] is False


@pytest.mark.parametrize(("change", "evidence_type"), [
    ({"final_refresh_status": "HOLD"}, EVIDENCE_FAILED),
    ({"source": "ESPN_SCOREBOARD"}, EVIDENCE_MALFORMED),
    ({"scored_source_snapshot_id": "some-other-snapshot"}, EVIDENCE_CONFLICT),
    ({"refresh_source_snapshot_id": None}, EVIDENCE_CONFLICT),
    ({"checked_at": "not-a-timestamp"}, EVIDENCE_MALFORMED),
    ({"refresh_source_timestamp": "2026-10-22T10:00:00+00:00"}, EVIDENCE_STALE),
    ({"probability_invalidated": True}, EVIDENCE_FAILED),
    ({"rerun_required": True}, EVIDENCE_FAILED),
    ({"can_execute": True}, EVIDENCE_MALFORMED),
])
def test_invalid_or_stale_producer_refresh_never_forges_stage_pass(change, evidence_type):
    receipt = {**_refresh(), **change}
    contract = _contract(receipt)
    assert contract["stages"][STAGE_FINAL_REFRESH]["evidence_type"] == evidence_type
    assert contract["stages"][STAGE_FINAL_REFRESH]["disposition"] == "FAIL"
    assert contract_publication_block(contract) is not None
    assert contract["can_execute"] is False


def test_missing_producer_refresh_stays_explicitly_unproven():
    contract = _contract(None)
    stage = contract["stages"][STAGE_FINAL_REFRESH]
    assert stage["evidence_type"] == EVIDENCE_NOT_EMITTED
    assert stage["disposition"] == "UNPROVEN"
    assert stage["reason"] == "NFL_FINAL_REFRESH_OPERATION_NOT_EXECUTED"


def test_source_executes_second_requery_before_stage_publication():
    source = (Path(__file__).resolve().parents[1] / "v17" / "nfl_team_event_publication.py").read_text()
    runner = source[source.index("def score_nfl_team_event_request("):]
    assert runner.index("refresh = _nfl_final_refresh(effective_req, result, db=db)") < runner.index(
        "governed = _govern("
    )
    assert "final_refresh=refresh" in runner
    assert "governed = _apply_nfl_final_refresh_ceiling(governed, refresh)" in runner
