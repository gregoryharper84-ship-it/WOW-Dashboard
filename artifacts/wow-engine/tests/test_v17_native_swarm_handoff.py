"""Native Swarm opt-in handoff: zero authority, zero provider calls, summary only."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from v17.sport_scout_enrichment import enrich_candidate, _native_swarm_summary
from tools import wow_specialized_swarm as native


def candidate(**overrides):
    return {
        "sport_key": "basketball_nba", "official_event_id": "test-future-event",
        "commence_time": "2099-01-01T00:00:00Z",
        "home_team": "Home", "away_team": "Away",
        "can_execute": False, **overrides,
    }


def receipt():
    return {
        "schema_version": "wow.v17.native_swarm.research.v1",
        "status": "RESEARCH_ONLY_HOLD",
        "receipt_sha256": "a" * 64,
        "policy": {"can_execute": False},
        "external_llm_calls": 0,
        "paid_provider_calls": 0,
        "network_calls": 0,
        "persisted_to_supabase": False,
        "production_verified": False,
        "worker_reports": [{"worker_id": str(i)} for i in range(5)],
        "wave_reviews": [{"all_structurally_valid": True}],
        "handoff": {
            "can_execute": False, "may_publish_or_qualify": False,
            "probability_authority": False, "specialist_rescore_verified": False,
            "source_screened_research_facts": [{
                "feature": "opponent_adjusted_strength",
                "value": "private-source-only-value",
                "source_id": "permitted-source",
            }],
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
