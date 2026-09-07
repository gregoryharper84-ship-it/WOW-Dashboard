import json

import pytest

from v17 import nightly_multiscout_delivery as delivery


def sample_payload():
    return {
        "schema_version": "wow.v17.nightly_multiscout.v1",
        "status": "DISCOVERY_COMPLETE",
        "generated_at": "2026-09-07T13:15:00Z",
        "window": {"from": "2026-09-07T13:15:00Z", "to": "2026-09-09T01:15:00Z"},
        "scout_team": ["BOARD_SCOUT", "CONTRARIAN_RED_TEAM_SCOUT"],
        "coverage": [{"scope": "sport", "sport": "baseball_mlb", "status": "SCANNED"}],
        "model_handoff": {
            "parallel_discovery_router_required": True,
            "team_event_candidates": [{"official_event_id": "evt-1", "research_ceiling": "RESEARCH_INTEREST"}],
            "prop_candidates": [{"official_event_id": "evt-1", "research_ceiling": "RESEARCH_INTEREST"}],
        },
        "governance": {
            "sportsbook_implied_probability_is_model_probability": False,
            "scout_consensus_is_model_probability": False,
            "can_execute": False,
        },
        "model_handoff_ready": True,
    }


def test_stamp_payload_adds_exact_recoverable_run_identity(monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ID", "12345")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    monkeypatch.setenv("GITHUB_SHA", "abc123")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_WORKFLOW", "wow-v17-nightly-multiscout")

    stamped = delivery.stamp_payload(sample_payload(), "wow-scout-12345-2")

    assert stamped["run_id"] == "wow-scout-12345-2"
    assert stamped["research_run_id"] == "wow-scout-12345-2"
    assert stamped["delivery"]["artifact_name"] == "wow-v17-multiscout-wow-scout-12345-2"
    assert stamped["delivery"]["github_run_url"].endswith("/owner/repo/actions/runs/12345")
    assert stamped["delivery"]["retention_days"] == 90
    assert stamped["reconciliation"]["candidate_rows_total"] == 2


def test_persist_writes_legacy_run_specific_manifest_and_latest_pointer(tmp_path):
    input_path = tmp_path / "model-handoff.json"
    input_path.write_text(json.dumps(sample_payload()), encoding="utf-8")

    manifest = delivery.persist(input_path, tmp_path, "wow-scout-local-001")

    assert manifest["run_id"] == "wow-scout-local-001"
    assert (tmp_path / "model-handoff.json").exists()
    assert (tmp_path / "model-handoff-wow-scout-local-001.json").exists()
    assert (tmp_path / "run-manifest.json").exists()
    assert (tmp_path / "latest-pointer.json").exists()

    legacy = json.loads(input_path.read_text(encoding="utf-8"))
    pointer = json.loads((tmp_path / "latest-pointer.json").read_text(encoding="utf-8"))
    assert legacy["run_id"] == "wow-scout-local-001"
    assert pointer["run_id"] == "wow-scout-local-001"
    assert manifest["governance"]["research_ceiling"] == "RESEARCH_INTEREST"
    assert manifest["governance"]["scout_probability_authority"] is False
    assert manifest["governance"]["can_execute"] is False


def test_invalid_run_id_fails_closed():
    with pytest.raises(ValueError):
        delivery.safe_run_id("bad run id")


def test_delivery_rejects_execution_enabled_payload(tmp_path):
    payload = sample_payload()
    payload["governance"]["can_execute"] = True
    input_path = tmp_path / "model-handoff.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError):
        delivery.persist(input_path, tmp_path, "wow-scout-local-002")
