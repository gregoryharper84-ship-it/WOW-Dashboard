"""Native Swarm opt-in handoff: zero authority, zero provider calls, summary only."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from v17.sport_scout_enrichment import (enrich_candidate, _native_swarm_summary,
                                        request_one_verified_native_swarm_pilot)
from tools import wow_specialized_swarm as native


def candidate(**overrides):
    return {
        "sport_key": "basketball_nba", "official_event_id": "test-future-event",
        "commence_time": "2099-01-01T00:00:00Z",
        "home_team": "Home", "away_team": "Away",
        "can_execute": False, **overrides,
    }


def receipt():
    """Structurally realistic local runner receipt; fabricated facts are test-only."""
    from datetime import timedelta
    canonical = native.build_manifest(candidate(), max_parallel=2)
    event = canonical["event"]
    run_id = canonical["run_id"]
    fact = {
        "feature": "opponent_adjusted_strength",
        "value": "private-source-only-value",
        "official_event_id": event["official_event_id"],
        "source_id": "permitted-source",
        "source_class": "ESTABLISHED_STATS_PROVIDER",
        "source_rights_status": "CLEARED_FOR_RESEARCH",
        "captured_at": (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat(),
    }
    workers = [{
        "run_id": run_id,
        "sport_key": event["sport_key"],
        "official_event_id": event["official_event_id"],
        "worker_id": worker_id,
        "research_ceiling": "RESEARCH_INTEREST",
        "can_execute": False,
        "facts": [deepcopy(fact)] if index == 3 else [],
        "missing_evidence": ["availability_and_lineups:NOT_SCREENED"],
        "conflicts": [],
    } for index, worker_id in enumerate(native.WORKERS)]
    waves = []
    for i in range(3):
        segment = workers[i * 2:i * 2 + 2]
        waves.append({
            "wave": i + 1, "all_structurally_valid": True,
            "workers_checked": [worker["worker_id"] for worker in segment],
            "verdicts": [{
                "valid_report": True, "research_only": True,
                "may_publish": False, "can_execute": False,
                "facts_screened_structurally": len(worker["facts"]),
            } for worker in segment],
        })
    return {
        "schema_version": "wow.v17.native_swarm.research.v1",
        "status": "RESEARCH_ONLY_HOLD",
        "receipt_sha256": "a" * 64,
        "run_id": run_id, "event": event,
        "max_parallel": 2, "waves_completed": 3,
        "policy": {"can_execute": False},
        "external_llm_calls": 0,
        "paid_provider_calls": 0,
        "network_calls": 0,
        "persisted_to_supabase": False,
        "production_verified": False,
        "worker_reports": workers,
        "wave_reviews": waves,
        "matchup_discovery": {"missing_critical_features": ["availability_and_lineups"]},
        "handoff": {
            "can_execute": False, "may_publish_or_qualify": False,
            "probability_authority": False, "specialist_rescore_verified": False,
            "source_screened_research_facts": [deepcopy(fact)],
            "missing_evidence": ["availability_and_lineups:NOT_SCREENED"],
            "conflicts": [],
        },
    }

def test_default_existing_candidate_does_not_launch_swarm(monkeypatch):
    def should_not_launch(*args, **kwargs):
        pytest.fail("native Swarm must remain opt-in")
    monkeypatch.setattr(native, "run_swarm", should_not_launch)
    output = enrich_candidate(candidate(), "TEAM_EVENT")
    assert "native_swarm_summary" not in output
    assert output["can_execute"] is False
    assert output["research_workers_may_create_probability"] is False


def test_explicit_boolean_opt_in_only(monkeypatch):
    def should_not_launch(*args, **kwargs):
        pytest.fail("string opt-in is not authority")
    monkeypatch.setattr(native, "run_swarm", should_not_launch)
    output = enrich_candidate(candidate(native_swarm_requested="true"), "PROP")
    assert "native_swarm_summary" not in output


def test_genuine_opt_in_generates_only_bounded_metadata(monkeypatch):
    calls = []
    def fake_native(row, *, max_parallel):
        calls.append((row, max_parallel))
        return receipt()
    monkeypatch.setattr(native, "run_swarm", fake_native)
    output = enrich_candidate(candidate(native_swarm_requested=True), "PROP")
    summary = output["native_swarm_summary"]
    assert len(calls) == 1 and calls[0][1] == 2
    assert calls[0][0]["official_event_id"] == "test-future-event"
    assert summary["status"] == "RESEARCH_ONLY_HOLD"
    assert summary["research_facts_screened"] == 1
    assert summary["missing_evidence_count"] == 1
    assert summary["workers_completed"] == 5
    assert summary["receipt_sha256"] == "a" * 64
    assert "private-source-only-value" not in str(summary)
    assert "source_screened_research_facts" not in summary
    assert summary["external_llm_calls"] == summary["paid_provider_calls"] == 0
    assert summary["may_publish_or_qualify"] is False
    assert summary["specialist_rescore_verified"] is False
    assert output["can_execute"] is False


def test_incomplete_event_identity_fails_closed_without_interrupting_existing_scout(monkeypatch):
    def fail_input(*args, **kwargs):
        raise ValueError("source ID secret should never leak")
    monkeypatch.setattr(native, "run_swarm", fail_input)
    output = enrich_candidate(candidate(native_swarm_requested=True, commence_time=""), "TEAM_EVENT")
    summary = output["native_swarm_summary"]
    assert summary["reason_code"] == "NATIVE_SWARM_INPUT_INVALID"
    assert "secret" not in str(summary)
    assert summary["status"] == "RESEARCH_ONLY_HOLD"
    assert summary["can_execute"] is False
    assert output["matchup_discovery"]["review_requested"] is True


def test_unexpected_local_worker_failure_is_hold_not_model_unavailable(monkeypatch):
    def fail_runtime(*args, **kwargs):
        raise RuntimeError("token or provider error must not leak")
    monkeypatch.setattr(native, "run_swarm", fail_runtime)
    summary = _native_swarm_summary(candidate())
    assert summary["reason_code"] == "NATIVE_SWARM_LOCAL_RUNTIME_FAILED"
    assert "token" not in str(summary)
    assert summary["may_publish_or_qualify"] is False
    assert summary["can_execute"] is False


@pytest.mark.parametrize("bad", [
    {"status": "MODEL_QUALIFIED"},
    {"policy": None},
    {"handoff": {"may_publish_or_qualify": True}},
    {"external_llm_calls": 1},
    {"paid_provider_calls": 1},
    {"network_calls": 1},
    {"persisted_to_supabase": True},
    {"production_verified": True},
    {"worker_reports": [{}]},
    {"wave_reviews": [{"all_structurally_valid": False}]},
    {"handoff": {"specialist_rescore_verified": True}},
    {"receipt_sha256": {"source_payload": "secret-in-digest"}},
    {"receipt_sha256": "q" * 64},
    {"receipt_sha256": "a" * 63},
    {"worker_reports": [None] * 5},
    {"worker_reports": [{}] * 5},
    {"matchup_discovery": None},
    {"matchup_discovery": {"missing_critical_features": "invalid"}},
    {"wave_reviews": [{"all_structurally_valid": True}]},
])
def test_forged_or_malformed_native_receipts_cannot_claim_authority(monkeypatch, bad):
    fake = receipt()
    for field, value in bad.items():
        if field == "handoff":
            fake["handoff"].update(value)
        else:
            fake[field] = value
    monkeypatch.setattr(native, "run_swarm", lambda *a, **k: fake)
    summary = _native_swarm_summary(candidate())
    assert summary["status"] == "RESEARCH_ONLY_HOLD"
    assert summary["reason_code"] == "NATIVE_SWARM_LOCAL_RUNTIME_FAILED"
    assert summary["research_facts_screened"] == 0
    assert summary["can_execute"] is False
    assert summary["specialist_rescore_verified"] is False


def test_existing_handoff_not_affected_by_candidate_default(monkeypatch):
    monkeypatch.setattr(native, "run_swarm", lambda *args, **kwargs: pytest.fail("not enabled"))
    from v17.sport_scout_enrichment import enrich_handoff
    output = enrich_handoff({
        "can_execute": False,
        "model_handoff": {
            "team_event_candidates": [candidate()],
            "prop_candidates": [candidate(official_event_id="another-future-event")],
        },
    })
    assert all("native_swarm_summary" not in item for key in ("team_event_candidates", "prop_candidates")
               for item in output["model_handoff"][key])
    assert output["can_execute"] is False


def test_real_five_worker_local_path_is_research_hold_without_source_facts():
    """Exercise actual native coordinator with canonical V17 imports, no fake run_swarm."""
    output = enrich_candidate(candidate(native_swarm_requested=True), "TEAM_EVENT")
    summary = output["native_swarm_summary"]
    assert summary["status"] == "RESEARCH_ONLY_HOLD"
    assert summary["reason_code"] is None
    assert summary["workers_completed"] == 5
    assert summary["research_facts_screened"] == 0
    assert summary["missing_evidence_count"] > 0
    assert isinstance(summary["receipt_sha256"], str)
    assert len(summary["receipt_sha256"]) == 64
    assert summary["external_llm_calls"] == 0
    assert summary["paid_provider_calls"] == 0
    assert summary["prediction_authority"] is False
    assert summary["may_publish_or_qualify"] is False
    assert summary["independent_source_verification"] is False
    assert summary["can_execute"] is False
    assert output["matchup_discovery"]["review_status"] == "MATCHUP_EVIDENCE_NOT_OBSERVED"


def test_real_opt_in_handoff_preserves_non_opted_candidate_and_terminal_authority():
    from v17.sport_scout_enrichment import enrich_handoff
    out = enrich_handoff({
        "can_execute": False,
        "model_handoff": {
            "team_event_candidates": [candidate(native_swarm_requested=True)],
            "prop_candidates": [candidate(official_event_id="other-future-event")],
        },
    })
    event = out["model_handoff"]["team_event_candidates"][0]
    prop = out["model_handoff"]["prop_candidates"][0]
    assert event["native_swarm_summary"]["status"] == "RESEARCH_ONLY_HOLD"
    assert event["native_swarm_summary"]["workers_completed"] == 5
    assert "native_swarm_summary" not in prop
    assert out["governance"]["v17_terminal_reducer_is_terminal_authority"] is True
    assert out["can_execute"] is False
    assert out["model_handoff"]["team_event_candidates"][0]["can_execute"] is False



@pytest.mark.parametrize("mutation", [
    lambda r: r["worker_reports"][1].update(worker_id=r["worker_reports"][0]["worker_id"]),
    lambda r: r["worker_reports"][1].update(run_id="0" * 20),
    lambda r: r["worker_reports"][0].update(official_event_id="other-game"),
    lambda r: r["worker_reports"][0].update(can_execute=True),
    lambda r: r["worker_reports"][0].update(probability_authority=True),
    lambda r: r["worker_reports"][3]["facts"][0].update(source_rights_status="REVIEW_REQUIRED"),
    lambda r: r["wave_reviews"][0].update(workers_checked=[native.WORKERS[2], native.WORKERS[3]]),
    lambda r: r["wave_reviews"][0]["verdicts"][0].update(valid_report=False),
    lambda r: r["wave_reviews"][1]["verdicts"][0].update(may_publish=True),
    lambda r: r["handoff"].update(source_screened_research_facts=[]),
])
def test_native_worker_and_wave_attestation_rejects_forgery(monkeypatch, mutation):
    fake = receipt()
    mutation(fake)
    monkeypatch.setattr(native, "run_swarm", lambda *args, **kwargs: fake)
    summary = _native_swarm_summary(candidate())
    assert summary["status"] == "RESEARCH_ONLY_HOLD"
    assert summary["reason_code"] == "NATIVE_SWARM_LOCAL_RUNTIME_FAILED"
    assert summary["workers_completed"] == 0
    assert summary["research_facts_screened"] == 0
    assert summary["receipt_sha256"] is None
    assert "private-source-only-value" not in str(summary)


def test_malformed_digest_object_never_leaks_secret(monkeypatch):
    fake = receipt()
    fake["receipt_sha256"] = {"source_payload": "extremely-secret-value"}
    monkeypatch.setattr(native, "run_swarm", lambda *args, **kwargs: fake)
    summary = _native_swarm_summary(candidate())
    assert summary["reason_code"] == "NATIVE_SWARM_LOCAL_RUNTIME_FAILED"
    assert summary["receipt_sha256"] is None
    assert "extremely-secret-value" not in str(summary)



def test_forged_uncleared_rights_in_both_worker_and_handoff_are_held(monkeypatch):
    fake = receipt()
    fake["worker_reports"][3]["facts"][0]["source_rights_status"] = "REVIEW_REQUIRED"
    fake["handoff"]["source_screened_research_facts"][0]["source_rights_status"] = "REVIEW_REQUIRED"
    monkeypatch.setattr(native, "run_swarm", lambda *args, **kwargs: fake)
    summary = _native_swarm_summary(candidate())
    assert summary["reason_code"] == "NATIVE_SWARM_LOCAL_RUNTIME_FAILED"
    assert summary["research_facts_screened"] == 0
    assert summary["workers_completed"] == 0


def test_forged_matching_event_id_with_different_time_and_run_id_is_held(monkeypatch):
    fake = receipt()
    forged = native.build_manifest({
        **candidate(), "commence_time": "2100-01-01T00:00:00Z",
    }, max_parallel=2)
    fake["event"] = forged["event"]
    fake["run_id"] = forged["run_id"]
    for report in fake["worker_reports"]:
        report["run_id"] = forged["run_id"]
    monkeypatch.setattr(native, "run_swarm", lambda *args, **kwargs: fake)
    summary = _native_swarm_summary(candidate())
    assert summary["reason_code"] == "NATIVE_SWARM_LOCAL_RUNTIME_FAILED"
    assert summary["receipt_sha256"] is None
    assert summary["workers_completed"] == 0


def test_valid_candidate_but_actual_local_worker_invalid_is_runtime_hold(monkeypatch):
    original = native._local_worker

    def invalid_worker(*args, **kwargs):
        report = original(*args, **kwargs)
        if report["worker_id"] == native.WORKERS[0]:
            report["can_execute"] = True
        return report

    monkeypatch.setattr(native, "_local_worker", invalid_worker)
    summary = _native_swarm_summary(candidate())
    assert summary["reason_code"] == "NATIVE_SWARM_LOCAL_RUNTIME_FAILED"
    assert summary["status"] == "RESEARCH_ONLY_HOLD"
    assert summary["workers_completed"] == 0
    assert summary["can_execute"] is False


def test_malformed_candidate_time_is_input_hold_before_worker_admission(monkeypatch):
    monkeypatch.setattr(native, "run_swarm", lambda *args, **kwargs: pytest.fail("worker must not start"))
    summary = _native_swarm_summary(candidate(commence_time="bad timestamp"))
    assert summary["reason_code"] == "NATIVE_SWARM_INPUT_INVALID"
    assert summary["workers_completed"] == 0


def test_native_swarm_direct_cli_without_pythonpath(tmp_path):
    """The real workflow's direct v17 script entrypoint must launch local workers."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    engine_root = Path(__file__).resolve().parents[1]
    source = tmp_path / "scout-input.json"
    output = tmp_path / "scout-output.json"
    payload = {
        "can_execute": False,
        "model_handoff": {
            "team_event_candidates": [candidate(native_swarm_requested=True)],
            "prop_candidates": [],
        },
    }
    import json
    source.write_text(json.dumps(payload), encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": ""}
    result = subprocess.run(
        [sys.executable, str(engine_root / "v17/sport_scout_enrichment.py"),
         "--input", str(source), "--output", str(output)],
        cwd=engine_root,  # matches existing production workflow cwd
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-1500:]
    receipt = json.loads(output.read_text(encoding="utf-8"))
    summary = receipt["model_handoff"]["team_event_candidates"][0]["native_swarm_summary"]
    assert summary["status"] == "RESEARCH_ONLY_HOLD"
    assert summary["reason_code"] is None
    assert summary["workers_completed"] == 5
    assert summary["external_llm_calls"] == 0
    assert summary["paid_provider_calls"] == 0
    assert summary["can_execute"] is False
    assert receipt["can_execute"] is False


def _pilot_payload(*rows):
    return {
        "can_execute": False,
        "model_handoff": {
            "team_event_candidates": list(rows),
            "prop_candidates": [],
        },
    }


def _verified_nhl(**overrides):
    return {
        "sport_key": "icehockey_nhl",
        "official_event_id": "2026020385",
        "canonical_event_id": "2026020385",
        "official_event_identity_verified": True,
        "canonical_identity_status": "CANONICAL_RESOLVED",
        "commence_time": "2026-11-25T01:00:00Z",
        "home_team": "Edmonton Oilers",
        "away_team": "Vegas Golden Knights",
        "can_execute": False,
        **overrides,
    }


def test_verified_pilot_one_actual_canonical_event_not_mutating_input():
    now = datetime(2026, 11, 23, 23, tzinfo=timezone.utc)
    wrong = _verified_nhl(official_event_identity_verified=False)
    valid = _verified_nhl()
    source = _pilot_payload(wrong, valid)
    out = request_one_verified_native_swarm_pilot(source, now=now)
    rows = out["model_handoff"]["team_event_candidates"]
    assert out["native_swarm_pilot"]["status"] == "VERIFIED_EVENT_OPTED_IN"
    assert out["native_swarm_pilot"]["requested_count"] == 1
    assert rows[0].get("native_swarm_requested") is not True
    assert rows[1]["native_swarm_requested"] is True
    assert source["model_handoff"]["team_event_candidates"][1].get("native_swarm_requested") is None
    assert out["native_swarm_pilot"]["paid_provider_calls"] == 0
    assert out["native_swarm_pilot"]["can_execute"] is False


@pytest.mark.parametrize("invalid", [
    {"official_event_identity_verified": False},
    {"canonical_identity_status": "PROVIDER_EVENT_ID_UNVERIFIED"},
    {"canonical_event_id": "not-the-canonical-official-id"},
    {"can_execute": True},
    {"commence_time": "malformed"},
    {"commence_time": "2026-11-23T23:10:00Z"},
])
def test_verified_pilot_rejects_untrusted_or_imminent_event(invalid):
    now = datetime(2026, 11, 23, 23, tzinfo=timezone.utc)
    row = _verified_nhl()
    row.update(invalid)
    result = request_one_verified_native_swarm_pilot(_pilot_payload(row), now=now)
    assert result["native_swarm_pilot"]["status"] == "NO_VERIFIED_FUTURE_EVENT"
    assert result["native_swarm_pilot"]["requested_count"] == 0
    assert result["model_handoff"]["team_event_candidates"][0].get("native_swarm_requested") is not True


def test_verified_pilot_accepts_raw_scout_row_without_execution_key():
    # Real nightly_multiscout._team_event_candidate omits the row-level bit
    # until sport_scout_enrichment finalizes it.
    now = datetime(2026, 11, 23, 23, tzinfo=timezone.utc)
    raw = _verified_nhl()
    del raw["can_execute"]
    result = request_one_verified_native_swarm_pilot(_pilot_payload(raw), now=now)
    selected = result["model_handoff"]["team_event_candidates"][0]
    assert result["native_swarm_pilot"]["status"] == "VERIFIED_EVENT_OPTED_IN"
    assert selected["native_swarm_requested"] is True
    assert selected["can_execute"] is False
    assert "can_execute" not in raw


def test_verified_pilot_does_not_duplicate_an_existing_explicit_optin():
    now = datetime(2026, 11, 23, 23, tzinfo=timezone.utc)
    first = _verified_nhl(native_swarm_requested=True)
    second = _verified_nhl(official_event_id="2026020386", canonical_event_id="2026020386")
    result = request_one_verified_native_swarm_pilot(_pilot_payload(first, second), now=now)
    assert result["native_swarm_pilot"]["status"] == "EXPLICIT_CANDIDATE_OPT_IN_ALREADY_PRESENT"
    assert result["model_handoff"]["team_event_candidates"][1].get("native_swarm_requested") is None


def test_verified_pilot_rejects_execution_authority_before_admission():
    with pytest.raises(ValueError, match="SCOUT_HANDOFF_CAN_EXECUTE_MUST_BE_FALSE"):
        request_one_verified_native_swarm_pilot(
            {"can_execute": True, "model_handoff": {"team_event_candidates": [_verified_nhl()]}}
        )


def test_actual_local_swarm_on_verified_nhl_without_clearance_holds():
    row = _verified_nhl(
        commence_time="2099-01-01T00:00:00Z",
        native_swarm_requested=True,
    )
    result = enrich_candidate(row, "TEAM_EVENT")
    summary = result["native_swarm_summary"]
    assert summary["status"] == "RESEARCH_ONLY_HOLD"
    assert summary["workers_completed"] == 5
    assert summary["research_facts_screened"] == 0
    assert summary["independent_source_verification"] is False
    assert summary["paid_provider_calls"] == 0
    assert summary["external_llm_calls"] == 0
    assert summary["can_execute"] is False
    assert summary["may_publish_or_qualify"] is False
    assert len(summary["receipt_sha256"]) == 64


def test_live_scout_workflow_explicit_optin_and_archived_bounded_receipt():
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    workflow = (root / ".github/workflows/wow-v17-nightly-multiscout.yml").read_text()
    assert "--native-swarm-verified-pilot" in workflow
    assert "native-swarm-receipt.json" in workflow
    assert "NATIVE_SWARM_PILOT_RECEIPT_MISSING" in workflow


def test_pilot_wakes_on_the_actual_protected_deploy_controller():
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    workflow = (root / ".github/workflows/wow-v17-nightly-multiscout.yml").read_text()
    controller = (root / ".github/workflows/wow-v17-required-checks-deploy.yml").read_text()
    assert 'workflows: ["wow-v17-render-production-deploy", "wow-v17-required-checks-deploy"]' in workflow
    assert 'github.event.workflow_run.conclusion == \'success\'' in workflow
    assert 'github.event.workflow_run.head_branch == \'main\'' in workflow
    assert 'name: wow-v17-required-checks-deploy' in controller
    assert 'Deploy exact current main SHA once required checks are green' in controller


def test_controller_receipt_admission_rejects_no_deploy_and_foreign_sha():
    """Execute the actual production jq admission contract on negative receipts."""
    import json
    import shutil
    import subprocess
    from pathlib import Path

    if not shutil.which("jq"):
        pytest.skip("jq required to execute GitHub Actions receipt predicate")
    root = Path(__file__).resolve().parents[3]
    workflow = (root / ".github/workflows/wow-v17-nightly-multiscout.yml").read_text()
    assert "needs.verify.outputs.deploy_admitted == 'true'" in workflow
    assert "actions/download-artifact@v4" in workflow
    assert "github.event.workflow_run.id" in workflow
    # Single source of truth: extract the jq expression used in verify.
    expression = workflow.split("jq -e --arg sha \"$SOURCE_SHA\" '", 1)[1].split(
        "' \"$RECEIPT\"", 1)[0]
    expected = "a" * 40
    valid = {
        "status": "DEPLOY_LIVE_AND_HEALTHY",
        "main_sha": expected, "target_sha": expected,
        "can_execute": False,
        "readback": {
            "status": "WEB_WORKER_LIVE_SHA_AND_HEALTH_OBSERVED",
            "observed_main_sha": expected, "target_sha": expected,
            "can_execute": False, "health_live": "HTTP_200",
            "web_deploy_id": "dep-web", "worker_deploy_id": "dep-worker",
        },
    }
    def accepted(receipt, *, sha=expected):
        result = subprocess.run(
            ["jq", "-e", "--arg", "sha", sha, expression],
            input=json.dumps(receipt), text=True, capture_output=True,
            timeout=15, check=False,
        )
        return result.returncode == 0
    assert accepted(valid)
    for mutation in (
        {"status": "REQUIRED_CHECKS_PENDING_NO_DEPLOY"},
        {"status": "DEPLOY_ALREADY_PRESENT"},
        {"target_sha": "b" * 40},
        {"can_execute": True},
        {"readback": {**valid["readback"], "health_live": "HTTP_503"}},
        {"readback": {**valid["readback"], "observed_main_sha": "b" * 40}},
        {"readback": {**valid["readback"], "worker_deploy_id": ""}},
    ):
        assert not accepted({**valid, **mutation})
    assert not accepted(valid, sha="b" * 40)


def test_direct_cli_verified_pilot_generates_real_research_hold(tmp_path):
    import json
    import os
    from pathlib import Path
    import subprocess
    import sys

    engine_root = Path(__file__).resolve().parents[1]
    source = tmp_path / "scout-input.json"
    output = tmp_path / "scout-output.json"
    source.write_text(json.dumps(_pilot_payload(_verified_nhl(
        commence_time="2099-01-01T00:00:00Z"
    )) | {"can_execute": False}), encoding="utf-8")
    # Ensure root CLI sees the same controlled opt-in as GitHub Actions.
    result = subprocess.run(
        [sys.executable, str(engine_root / "v17/sport_scout_enrichment.py"),
         "--input", str(source), "--output", str(output),
         "--native-swarm-verified-pilot"],
        cwd=engine_root,
        env={**os.environ, "PYTHONPATH": ""},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-1500:]
    artifact = json.loads(output.read_text(encoding="utf-8"))
    assert artifact["native_swarm_pilot"]["status"] == "VERIFIED_EVENT_OPTED_IN"
    row = artifact["model_handoff"]["team_event_candidates"][0]
    assert row["native_swarm_requested"] is True
    assert row["native_swarm_summary"]["status"] == "RESEARCH_ONLY_HOLD"
    assert row["native_swarm_summary"]["workers_completed"] == 5
    assert row["native_swarm_summary"]["paid_provider_calls"] == 0
    assert artifact["can_execute"] is False
