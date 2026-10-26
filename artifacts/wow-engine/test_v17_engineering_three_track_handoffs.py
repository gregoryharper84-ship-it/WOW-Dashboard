"""Regression coverage for the three-track advisory engineering handoffs."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from v17.engineering_three_track_handoffs import (
    HandoffContractError, build_queue, main,
)

ROOT = Path(__file__).parent
CONFIG = ROOT / "v17" / "engineering_three_track_queue.json"
SHA = "a" * 40


def config():
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def recovery(**changes):
    row = {
        "incident_id": "1841",
        "root_cause": "Missing stage-proof producer",
        "typed_failure": "MODEL_STAGE_PROOF_MISSING",
        "source_sha": SHA,
        "reproduction_ref": "https://github.com/example/repo/issues/1841",
        "regression_ref": "tests/test_nfl_stage_proof.py",
        "owner_issue_ref": "#1841",
        "change_class": "B",
        "finding_status": "ROOT_CAUSE_CONFIRMED",
        "self_approved": False,
        "production_verified": False,
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
    }
    row.update(changes)
    return row


def science(**changes):
    row = {
        "incident_id": "1841",
        "upstream_source_sha": SHA,
        "experiment_id": "v17-nfl-stage-proof-challenger",
        "experiment_path": "artifacts/wow-engine/v17/experiments/nfl-stage-proof/",
        "hypothesis": "Repeated missing feature produces systematic bounds drift",
        "replay_ref": "artifacts/replay/nfl.json",
        "counterexample_ref": "artifacts/replay/holdout-good.json",
        "holdout_ref": "artifacts/replay/forward.json",
        "regression_ref": "test_experiments.py",
        "validation_status": "RESEARCH_VALIDATED",
        "production_behavior_change": False,
        "self_approved": False,
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
    }
    row.update(changes)
    return row


def test_initial_state_queues_all_tracks_without_claiming_dispatch():
    result = build_queue(config())
    assert [r["track"] for r in result["tracks"]] == [
        "TRACK_1_RECOVERY", "TRACK_2_MODEL_SCIENCE", "TRACK_3_PREVENTION"
    ]
    assert [r["issue_number"] for r in result["tracks"]] == [2078, 1128, 1563]
    assert [r["state"] for r in result["tracks"]] == [
        "QUEUED_IN_EXISTING_RECOVERY",
        "WAITING_FOR_TRACK_1_EVIDENCE",
        "WAITING_FOR_TRACK_2_EVIDENCE",
    ]
    assert result["status"] == "ADVISORY_QUEUE_NOT_DISPATCHED"
    assert result["production_promotion_allowed"] is False
    assert result["can_execute"] is False
    assert result["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert all(h["state"] == "WAITING_FOR_EVIDENCE" for h in result["handoffs"])


def test_root_cause_proof_enables_research_only_not_release():
    result = build_queue(config(), {"recovery": recovery()})
    assert result["tracks"][1]["state"] == "EVIDENCE_READY_RESEARCH_ONLY"
    assert result["tracks"][2]["state"] == "WAITING_FOR_TRACK_2_EVIDENCE"
    assert result["handoffs"][0]["state"] == "EVIDENCE_READY_UNVERIFIED"
    assert result["handoffs"][0]["approval_or_release_granted"] is False
    assert result["handoffs"][1]["state"] == "WAITING_FOR_EVIDENCE"


def test_validated_nonserving_science_handoffs_to_independent_sirt():
    results = build_queue(config(), {"recovery": recovery(), "science": science()})
    assert results["tracks"][2]["state"] == "EVIDENCE_READY_INDEPENDENT_ASSURANCE"
    assert results["handoffs"][1]["next_authority"] == "INDEPENDENT_SIRT"
    assert results["handoffs"][1]["approval_or_release_granted"] is False
    assert all(not r["can_execute"] for r in results["tracks"])


def test_deterministic_duplicate_same_evidence_same_fingerprint():
    proof = {"recovery": recovery(), "science": science()}
    first = build_queue(config(), proof)
    second = build_queue(config(), proof)
    assert first == second
    assert first["handoffs"][0]["fingerprint"] == second["handoffs"][0]["fingerprint"]
    assert first["handoffs"][1]["fingerprint"] == second["handoffs"][1]["fingerprint"]


@pytest.mark.parametrize("bad", [
    {"can_execute": True},
    {"terminal_authority": "ANOTHER_REDUCER"},
    {"control_plane": "SECOND_CONTROL_PLANE"},
    {"no_new_dispatcher": False},
])
def test_top_level_governance_fails_closed(bad):
    candidate = config()
    candidate.update(bad)
    with pytest.raises(HandoffContractError):
        build_queue(candidate)


@pytest.mark.parametrize("bad", [
    {"source_sha": "short"},
    {"finding_status": "UNPROVEN"},
    {"root_cause": ""},
    {"self_approved": True},
    {"change_class": ""},
    {"production_verified": True},
    {"can_execute": True},
])
def test_unproven_recovery_must_not_enter_track2(bad):
    with pytest.raises(HandoffContractError):
        build_queue(config(), {"recovery": recovery(**bad)})


def test_exact_integrated_deployed_sha_is_required_for_claimed_production_evidence():
    row = recovery(
        finding_status="PRODUCTION_EVIDENCE_RECORDED",
        production_verified=True,
        integrated_sha=SHA,
        deployed_sha="b" * 40,
        action_receipt_id="action-1",
        persistence_receipt_id="persist-1",
        qa_receipt_id="qa-1",
        release_receipt_id="release-1",
        independent_verification_id="sirt-1",
    )
    with pytest.raises(HandoffContractError, match="PRODUCTION_EXACT_SHA_UNPROVEN"):
        build_queue(config(), {"recovery": row})


@pytest.mark.parametrize("bad", [
    {"incident_id": "1950"},
    {"upstream_source_sha": "b" * 40},
    {"experiment_path": "artifacts/wow-engine/v17/serving/nfl.py"},
    {"experiment_path": "artifacts/wow-engine/v17/experiments/../serving.py"},
    {"validation_status": "DRAFT"},
    {"production_behavior_change": True},
    {"self_approved": True},
    {"holdout_ref": ""},
])
def test_unproven_science_must_not_enter_sirt(bad):
    with pytest.raises(HandoffContractError):
        build_queue(config(), {"recovery": recovery(), "science": science(**bad)})


def test_out_of_order_science_is_rejected_without_a_recovery_source():
    with pytest.raises(HandoffContractError, match="SCIENCE_WITHOUT_RECOVERY_HANDOFF"):
        build_queue(config(), {"science": science()})


@pytest.mark.parametrize("bad_tracks", [
    None,
    [],
    ["TRACK_1_RECOVERY", "TRACK_2_MODEL_SCIENCE", "TRACK_3_PREVENTION"],
    [{"id": "TRACK_1_RECOVERY"}, {"id": "TRACK_2_MODEL_SCIENCE"},
     {"id": "TRACK_3_PREVENTION"}, "unsafe-extra"],
    [{"id": "TRACK_1_RECOVERY"}, "unsafe-middle", {"id": "TRACK_3_PREVENTION"}],
])
def test_malformed_track_inventory_fails_closed_with_typed_error(bad_tracks):
    candidate = config()
    candidate["tracks"] = bad_tracks
    with pytest.raises(HandoffContractError, match="TRACK_ORDER_OR_COVERAGE_INVALID"):
        build_queue(candidate)


def test_closed_nfl_precedent_cannot_be_listed_as_active_dispatch_issue():
    scope = config()["tracks"][0]["scope"]
    assert "NFL #1841" not in scope
    assert "NFL #1562" in scope
    assert "NBA props #1988" in scope


def test_track_configuration_enforces_existing_issue_owners():
    c = config()
    c["tracks"][2]["tracking_issue"] = c["tracks"][1]["tracking_issue"]
    with pytest.raises(HandoffContractError, match="TRACK_ISSUE_INVALID_OR_DUPLICATE"):
        build_queue(c)


def test_cli_generates_queue_in_offline_mode(tmp_path):
    output = tmp_path / "queue.json"
    assert main(["--config", str(CONFIG), "--output", str(output)]) == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert len(result["tracks"]) == 3
    assert result["status"] == "ADVISORY_QUEUE_NOT_DISPATCHED"


def test_cli_blocks_on_invalid_receipt_file(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"science": science()}), encoding="utf-8")
    assert main(["--config", str(CONFIG), "--evidence", str(bad)]) == 2


def test_no_new_worker_release_permission_or_market_execution():
    source = (ROOT / "v17" / "engineering_three_track_handoffs.py").read_text()
    assert "def build_queue(" in source
    assert "SUBPROCESS" not in source
    assert "subprocess." not in source
    assert "requests.post(" not in source
    assert "terminal_authority" in source
    assert "production_promotion_allowed" in source
