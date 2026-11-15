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
