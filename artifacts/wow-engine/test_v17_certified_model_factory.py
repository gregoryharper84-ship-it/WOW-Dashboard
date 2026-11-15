"""Proof that the model factory reuses independent sport lanes without promotion."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v17 import certified_model_factory as factory


def _nba_ok():
    return {
        "status": "COMPLETE",
        "rows_updated": 1,
        "rows": [{
            "sport": "NBA",
            "status": "SHADOW_EVIDENCE_UPDATED",
            "hydration_status": "FRESH_ACQUISITION_UPDATED",
            "training_replay": {
                "persisted_status": "SHADOW",
                "model_artifact_version": "NBA-TEAM-EVENT-v1",
                "calibrator_version": "NBA-PLATT-v1",
                "provenance_preflight": {"provenance_complete": True},
                "freshness_preflight": {"freshness_status": "PASS"},
                "promotion_attempted": False,
                "can_execute": False,
            },
            "promotion_attempted": False,
            "probability_publishable": False,
            "can_execute": False,
        }],
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def _nhl_ok():
    return {
        "status": "CANDIDATE_EVIDENCE_UPDATED",
        "sport": "NHL",
        "research_screen_pass": True,
        "source_review_status": "REQUIRED",
        "candidate_preflight": {"status": "NHL_CANDIDATE_PREFLIGHT_READY", "source_event_n": 100, "training_feature_n": 100, "training_dataset_hash": "f"*64, "can_execute": False},
        "persistence": {"status": "D1_CANDIDATE_PACKAGE_PERSISTED", "candidate": {"candidate_id": "nhl-candidate-1", "model_artifact_version": "nhl-artifact-v1", "lifecycle_state": "CANDIDATE"}, "can_execute": False},
        "source_event_n": 100,
        "training_feature_n": 100,
        "model_family": "NHL_REGULAR_SEASON_LOGISTIC_V1",
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def test_nba_winner_calls_only_nba(monkeypatch):
    calls = []
    monkeypatch.setattr(factory, "_run_nba", lambda db, seasons: (calls.append(("NBA", db, seasons)), _nba_ok())[1])
    monkeypatch.setattr(factory, "_run_nhl", lambda *_: 1/0)
    receipt = factory.prepare_candidate_evidence(
        object(), sport="NBA", market="WINNER", source_head_sha="a"*40,
        seasons=(2025, 2026),
    )
    assert len(calls) == 1 and calls[0][0] == "NBA"
    assert receipt["status"] == "RESEARCH_EVIDENCE_PREPARED"
    assert receipt["native_status"] == "COMPLETE"
    assert receipt["certification_status"] == "NOT_CERTIFIED"
    assert receipt["probability_publishable"] is False
    assert receipt["can_execute"] is False
    assert len(receipt["evidence_sha256"]) == 64


def test_nhl_winner_calls_only_nhl(monkeypatch):
    calls = []
    monkeypatch.setattr(factory, "_run_nhl", lambda db, seasons, sha: (calls.append((db, seasons, sha)), _nhl_ok())[1])
    monkeypatch.setattr(factory, "_run_nba", lambda *_: 1/0)
    receipt = factory.prepare_candidate_evidence(
        object(), sport="NHL", market="WINNER", source_head_sha="b"*40,
        seasons=(2020, 2021),
    )
    assert calls and calls[0][2] == "b"*40
    assert receipt["status"] == "RESEARCH_EVIDENCE_PREPARED"
    assert receipt["certification_status"] == "NOT_CERTIFIED"
    assert receipt["native_evidence"]["source_review_status"] == "REQUIRED"
    assert receipt["native_evidence"]["candidate_id"] == "nhl-candidate-1"


def test_wrong_market_cannot_reuse_winner_model(monkeypatch):
    monkeypatch.setattr(factory, "_run_nba", lambda *_: 1/0)
    result = factory.prepare_candidate_evidence(
        object(), sport="NBA", market="SPREAD", source_head_sha="c"*40,
    )
    assert result["status"] == "HOLD"
    assert result["code"] == "MODEL_FACTORY_RECIPE_UNSUPPORTED"


def test_unimplemented_sport_stays_hold(monkeypatch):
    monkeypatch.setattr(factory, "_run_nba", lambda *_: 1/0)
    result = factory.prepare_candidate_evidence(
        object(), sport="SOCCER", market="WINNER", source_head_sha="c"*40,
    )
    assert result["status"] == "HOLD"
    assert result["code"] == "MODEL_FACTORY_RECIPE_UNSUPPORTED"


def test_bad_sha_and_seasons_fail_before_native_calls(monkeypatch):
    monkeypatch.setattr(factory, "_run_nba", lambda *_: 1/0)
    bad_sha = factory.prepare_candidate_evidence(
        object(), sport="NBA", market="WINNER", source_head_sha="unknown",
    )
    assert bad_sha["code"] == "MODEL_FACTORY_INPUT_INVALID"
    bad_seasons = factory.prepare_candidate_evidence(
        object(), sport="NBA", market="WINNER", source_head_sha="a"*40,
        seasons=(2026, 2026),
    )
    assert bad_seasons["code"] == "MODEL_FACTORY_INPUT_INVALID"


def test_native_blocker_is_preserved_without_fallback(monkeypatch):
    result = _nhl_ok()
    result.update(status="BLOCKED", code="NHL_CANDIDATE_PREFLIGHT_BLOCKED")
    monkeypatch.setattr(factory, "_run_nhl", lambda *_: result)
    receipt = factory.prepare_candidate_evidence(
        object(), sport="NHL", market="WINNER", source_head_sha="a"*40,
    )
    assert receipt["status"] == "HOLD"
    assert receipt["code"] == "NHL_CANDIDATE_PREFLIGHT_BLOCKED"


def test_failed_research_screen_not_ready(monkeypatch):
    result = _nhl_ok()
    result["research_screen_pass"] = False
    monkeypatch.setattr(factory, "_run_nhl", lambda *_: result)
    receipt = factory.prepare_candidate_evidence(
        object(), sport="NHL", market="WINNER", source_head_sha="a"*40,
    )
    assert receipt["status"] == "HOLD"
    assert receipt["code"] == "RESEARCH_SCREEN_FAILED"


def test_inertness_violation_at_any_nested_stage_fails_closed(monkeypatch):
    result = _nba_ok()
    result["rows"][0]["probability_publishable"] = True
    monkeypatch.setattr(factory, "_run_nba", lambda *_: result)
    receipt = factory.prepare_candidate_evidence(
        object(), sport="NBA", market="WINNER", source_head_sha="a"*40,
    )
    assert receipt["status"] == "HOLD"
    assert receipt["code"] == "MODEL_FACTORY_NATIVE_INERTNESS_VIOLATION"


def test_malformed_result_fails_closed(monkeypatch):
    monkeypatch.setattr(factory, "_run_nba", lambda *_: {"status":"COMPLETE", "can_execute":False})
    receipt = factory.prepare_candidate_evidence(
        object(), sport="NBA", market="WINNER", source_head_sha="a"*40,
    )
    assert receipt["status"] == "HOLD"
    assert receipt["code"] == "MODEL_FACTORY_NATIVE_RESULT_INVALID"


def test_native_exception_redacted(monkeypatch):
    monkeypatch.setattr(factory, "_run_nba", lambda *_: 1/0)
    receipt = factory.prepare_candidate_evidence(
        object(), sport="NBA", market="WINNER", source_head_sha="a"*40,
    )
    assert receipt["status"] == "HOLD"
    assert receipt["code"] == "MODEL_FACTORY_MAINTENANCE_EXCEPTION"
    assert "ZeroDivisionError" not in str(receipt)


def test_deep_nested_promotion_claim_fails_closed(monkeypatch):
    result = _nba_ok()
    result["rows"][0]["training_replay"] = {"artifact": {"automatic_certification": True}}
    monkeypatch.setattr(factory, "_run_nba", lambda *_: result)
    receipt = factory.prepare_candidate_evidence(
        object(), sport="NBA", market="WINNER", source_head_sha="a"*40,
    )
    assert receipt["status"] == "HOLD"
    assert receipt["code"] == "MODEL_FACTORY_NATIVE_INERTNESS_VIOLATION"


def test_nhl_missing_preflight_is_not_ready(monkeypatch):
    result = _nhl_ok()
    del result["candidate_preflight"]
    monkeypatch.setattr(factory, "_run_nhl", lambda *_: result)
    receipt = factory.prepare_candidate_evidence(
        object(), sport="NHL", market="WINNER", source_head_sha="a"*40,
    )
    assert receipt["status"] == "HOLD"
    assert receipt["code"] == "MODEL_FACTORY_NATIVE_RESULT_INVALID"


def test_nba_completed_wrapper_cannot_mask_unready_replay(monkeypatch):
    result = _nba_ok()
    result["rows"][0]["training_replay"] = {
        "persisted_status": "INPUT_INCOMPLETE",
        "provenance_preflight": {"provenance_complete": True},
        "freshness_preflight": {"freshness_status": "PASS"},
        "can_execute": False,
    }
    monkeypatch.setattr(factory, "_run_nba", lambda *_: result)
    receipt = factory.prepare_candidate_evidence(
        object(), sport="NBA", market="WINNER", source_head_sha="a"*40,
    )
    assert receipt["status"] == "HOLD"
    assert receipt["code"] == "MODEL_FACTORY_NATIVE_HOLD"


def test_nhl_success_without_persisted_candidate_identity_is_hold(monkeypatch):
    result = _nhl_ok()
    result["persistence"] = {"status": "D1_CANDIDATE_PACKAGE_PERSISTED", "candidate": {"candidate_id": None}}
    monkeypatch.setattr(factory, "_run_nhl", lambda *_: result)
    receipt = factory.prepare_candidate_evidence(
        object(), sport="NHL", market="WINNER", source_head_sha="a"*40,
    )
    assert receipt["status"] == "HOLD"
    assert receipt["code"] == "MODEL_FACTORY_NATIVE_RESULT_INVALID"


def test_nested_forbidden_authority_statuses_always_hold(monkeypatch):
    for field in ("promoted", "active", "rank_eligible"):
        result = _nhl_ok()
        result["persistence"]["candidate"][field] = True
        monkeypatch.setattr(factory, "_run_nhl", lambda *_args, result=result: result)
        receipt = factory.prepare_candidate_evidence(
            object(), sport="NHL", market="WINNER", source_head_sha="a" * 40,
        )
        assert receipt["status"] == "HOLD", field
        assert receipt["code"] == "MODEL_FACTORY_NATIVE_INERTNESS_VIOLATION", field
        assert receipt["rank_eligible"] is False
        assert receipt["probability_publishable"] is False
        assert receipt["can_execute"] is False


def test_false_native_authority_statuses_remain_safe():
    assert not factory._unsafe_native({"candidate": {"promoted": False, "active": False, "rank_eligible": False}})


def test_nba_independent_provenance_and_freshness_fail_closed(monkeypatch):
    for part, key, invalid in (
        ("provenance_preflight", "provenance_complete", False),
        ("freshness_preflight", "freshness_status", "STALE"),
    ):
        result = _nba_ok()
        result["rows"][0]["training_replay"][part][key] = invalid
        monkeypatch.setattr(factory, "_run_nba", lambda *_args, result=result: result)
        receipt = factory.prepare_candidate_evidence(
            object(), sport="NBA", market="WINNER", source_head_sha="a" * 40,
        )
        assert receipt["status"] == "HOLD", part
        assert receipt["code"] == "MODEL_FACTORY_NATIVE_HOLD", part
        assert receipt["rank_eligible"] is False
        assert receipt["probability_publishable"] is False
        assert receipt["can_execute"] is False


def test_nhl_independent_dataset_and_count_checks_fail_closed(monkeypatch):
    for field, value in (
        ("source_event_n", 99),
        ("training_feature_n", 99),
        ("training_dataset_hash", "not-a-sha"),
    ):
        result = _nhl_ok()
        result["candidate_preflight"][field] = value
        monkeypatch.setattr(factory, "_run_nhl", lambda *_args, result=result: result)
        receipt = factory.prepare_candidate_evidence(
            object(), sport="NHL", market="WINNER", source_head_sha="a" * 40,
        )
        assert receipt["status"] == "HOLD", field
        assert receipt["code"] == "MODEL_FACTORY_NATIVE_RESULT_INVALID", field
        assert receipt["rank_eligible"] is False
        assert receipt["probability_publishable"] is False
        assert receipt["can_execute"] is False


def test_nba_receipt_binds_versions_and_scope_to_native_replay(monkeypatch):
    result = _nba_ok()
    result["rows"][0]["training_replay"].update({
        "model_artifact_version": "NBA-MODEL-ONE", "calibrator_version": "NBA-CAL-ONE",
    })
    monkeypatch.setattr(factory, "_run_nba", lambda *_args, result=result: result)
    a = factory.prepare_candidate_evidence(object(), sport="NBA", market="WINNER", source_head_sha="a" * 40, seasons=(2025, 2026))
    assert a["native_evidence"]["model_artifact_version"] == "NBA-MODEL-ONE"
    assert a["native_evidence"]["calibrator_version"] == "NBA-CAL-ONE"
    assert a["native_evidence"]["requested_acquisition_seasons"] == [2025, 2026]
    assert a["native_evidence"]["fit_season_scope"] == "NATIVE_REPLAY_NOT_FILTERED_BY_REQUESTED_SEASONS"
    result["rows"][0]["training_replay"]["model_artifact_version"] = "NBA-MODEL-TWO"
    b = factory.prepare_candidate_evidence(object(), sport="NBA", market="WINNER", source_head_sha="a" * 40, seasons=(2025, 2026))
    assert a["evidence_sha256"] != b["evidence_sha256"]
    assert a["certification_status"] == b["certification_status"] == "NOT_CERTIFIED"


def test_invalid_nba_version_types_become_null(monkeypatch):
    result = _nba_ok()
    result["rows"][0]["training_replay"].update({
        "model_artifact_version": ["not-a-string"], "calibrator_version": 42,
    })
    monkeypatch.setattr(factory, "_run_nba", lambda *_args, result=result: result)
    a = factory.prepare_candidate_evidence(object(), sport="NBA", market="WINNER", source_head_sha="a" * 40)
    assert a["native_evidence"]["model_artifact_version"] is None
    assert a["native_evidence"]["calibrator_version"] is None
    assert a["status"] == "HOLD"
    assert a["code"] == "MODEL_FACTORY_NATIVE_RESULT_INVALID"
    assert a["probability_publishable"] is False
    assert a["can_execute"] is False


def test_nhl_receipt_contains_validated_dataset_hash(monkeypatch):
    result = _nhl_ok()
    monkeypatch.setattr(factory, "_run_nhl", lambda *_args: result)
    a = factory.prepare_candidate_evidence(object(), sport="NHL", market="WINNER", source_head_sha="a" * 40)
    assert a["native_evidence"]["training_dataset_hash"] == "f" * 64
    assert a["certification_status"] == "NOT_CERTIFIED"


def test_nba_nonblank_replay_versions_required_before_research_ready(monkeypatch):
    for field in ("model_artifact_version", "calibrator_version"):
        for invalid in (None, "", "   "):
            result = _nba_ok()
            result["rows"][0]["training_replay"][field] = invalid
            monkeypatch.setattr(factory, "_run_nba", lambda *_args, result=result: result)
            receipt = factory.prepare_candidate_evidence(
                object(), sport="NBA", market="WINNER", source_head_sha="a" * 40,
            )
            assert receipt["status"] == "HOLD", (field, invalid)
            assert receipt["code"] == "MODEL_FACTORY_NATIVE_RESULT_INVALID", (field, invalid)
            assert receipt["certification_status"] == "NOT_CERTIFIED"
            assert receipt["rank_eligible"] is False
            assert receipt["probability_publishable"] is False
            assert receipt["can_execute"] is False
