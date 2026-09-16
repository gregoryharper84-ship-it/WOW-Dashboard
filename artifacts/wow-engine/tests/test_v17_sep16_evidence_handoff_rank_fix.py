from types import SimpleNamespace

from v17.sep16_evidence_handoff_rank_fix import install_evidence_handoff_rank_fix


def _req(intent="UPSET", **evidence):
    return SimpleNamespace(decision_intent=intent, sport_specific_evidence=evidence)


def _preservation(original):
    return SimpleNamespace(
        _PROBABILITY_ONLY_INTENTS={"WINNER", "BEST_SIDE"},
        _run_mlb_llp_governance_with_evidence_handoff=original,
    )


def test_installer_expands_handoff_to_all_supported_team_event_intents():
    def original(req, route, model_result, envelope=None, *, event_api):
        return {"rank_eligible": False, "probability_publishable": False, "can_execute": False}

    preservation = _preservation(original)
    assert install_evidence_handoff_rank_fix(preservation=preservation) is True
    assert preservation._PROBABILITY_ONLY_INTENTS == {
        "WINNER",
        "BEST_SIDE",
        "FAVORITE",
        "UNDERDOG",
        "UPSET",
    }


def test_market_relative_upset_now_traverses_existing_handoff_path():
    seen = []
    preservation = None

    def original(req, route, model_result, envelope=None, *, event_api):
        # Mirrors the existing preservation wrapper's intent guard. Before the
        # Sep-16 installer, UPSET would return without running hydration.
        if req.decision_intent not in preservation._PROBABILITY_ONLY_INTENTS:
            return {"handoff_ran": False, "rank_eligible": False, "can_execute": False}
        seen.append(req.decision_intent)
        return {"handoff_ran": True, "rank_eligible": False, "can_execute": False}

    preservation = _preservation(original)
    install_evidence_handoff_rank_fix(preservation=preservation)
    out = preservation._run_mlb_llp_governance_with_evidence_handoff(
        _req("UPSET"),
        SimpleNamespace(),
        {"independent_home_probability": 0.55, "independent_away_probability": 0.45},
        event_api=SimpleNamespace(),
    )

    assert seen == ["UPSET"]
    assert out["handoff_ran"] is True
    assert out["rank_eligible"] is False
    assert out["can_execute"] is False


def test_present_upstream_missing_downstream_is_typed_schema_mismatch():
    def original(req, route, model_result, envelope=None, *, event_api):
        return {
            "code": "LLP_EVENT_GOVERNANCE_NOT_PROVEN",
            "blockers": ["LLP_PROBABILITY_CLAIM_AUDIT_NOT_PROVEN"],
            "llp_governance": {
                "status": "HOLD",
                "probability_audit": {
                    "reasons": [
                        "INDEPENDENT_PROBABILITY_MISSING",
                        "FAVORITE_FAILURE_PATHS_MISSING",
                    ]
                },
            },
            "rank_eligible": False,
            "probability_publishable": False,
            "can_execute": False,
        }

    preservation = _preservation(original)
    install_evidence_handoff_rank_fix(preservation=preservation)
    out = preservation._run_mlb_llp_governance_with_evidence_handoff(
        _req("WINNER"),
        SimpleNamespace(),
        {
            "independent_home_probability": 0.61,
            "independent_away_probability": 0.39,
            "favorite_failure_paths_json": {"normal": 0.2},
        },
        event_api=SimpleNamespace(),
    )

    assert out["evidence_handoff_schema_mismatch"]["code"] == "V17_HANDOFF_SCHEMA_MISMATCH"
    assert out["evidence_handoff_schema_mismatch"]["contradictions"] == [
        "FAVORITE_FAILURE_PATHS_MISSING",
        "INDEPENDENT_PROBABILITY_MISSING",
    ]
    assert "V17_HANDOFF_SCHEMA_MISMATCH:INDEPENDENT_PROBABILITY_MISSING" in out["blockers"]
    assert out["rank_eligible"] is False
    assert out["probability_publishable"] is False
    assert out["can_execute"] is False


def test_confirmed_lineup_not_called_is_schema_mismatch_without_rank_upgrade():
    def original(req, route, model_result, envelope=None, *, event_api):
        return {
            "llp_governance": {"blockers": ["HOME_LINEUP_NOT_CALLED", "AWAY_LINEUP_NOT_CALLED"]},
            "rank_eligible": False,
            "probability_publishable": False,
            "can_execute": False,
        }

    preservation = _preservation(original)
    install_evidence_handoff_rank_fix(preservation=preservation)
    out = preservation._run_mlb_llp_governance_with_evidence_handoff(
        _req("BEST_SIDE", home_lineup_status="CONFIRMED", away_lineup_status="CONFIRMED"),
        SimpleNamespace(),
        {},
        event_api=SimpleNamespace(),
    )

    assert out["evidence_handoff_schema_mismatch"]["contradictions"] == [
        "AWAY_LINEUP_NOT_CALLED",
        "HOME_LINEUP_NOT_CALLED",
    ]
    assert out["rank_eligible"] is False
    assert out["can_execute"] is False


def test_real_calibration_mismatch_is_not_reclassified_as_schema_mismatch():
    def original(req, route, model_result, envelope=None, *, event_api):
        return {
            "code": "LLP_EVENT_GOVERNANCE_NOT_PROVEN",
            "llp_governance": {
                "status": "HOLD",
                "blockers": ["CALIBRATION_TRAINING_N_MISMATCH"],
            },
            "rank_eligible": False,
            "probability_publishable": False,
            "can_execute": False,
        }

    preservation = _preservation(original)
    install_evidence_handoff_rank_fix(preservation=preservation)
    out = preservation._run_mlb_llp_governance_with_evidence_handoff(
        _req("WINNER"),
        SimpleNamespace(),
        {"calibration_training_n": 1500, "calibration_version": "cal-v1"},
        event_api=SimpleNamespace(),
    )

    assert "evidence_handoff_schema_mismatch" not in out
    assert out["llp_governance"]["blockers"] == ["CALIBRATION_TRAINING_N_MISMATCH"]
    assert out["rank_eligible"] is False
    assert out["can_execute"] is False


def test_installer_is_idempotent():
    calls = []

    def original(req, route, model_result, envelope=None, *, event_api):
        calls.append(1)
        return {"rank_eligible": False, "probability_publishable": False, "can_execute": False}

    preservation = _preservation(original)
    assert install_evidence_handoff_rank_fix(preservation=preservation) is True
    wrapped = preservation._run_mlb_llp_governance_with_evidence_handoff
    assert install_evidence_handoff_rank_fix(preservation=preservation) is True
    assert preservation._run_mlb_llp_governance_with_evidence_handoff is wrapped

    preservation._run_mlb_llp_governance_with_evidence_handoff(
        _req("WINNER"), SimpleNamespace(), {}, event_api=SimpleNamespace()
    )
    assert calls == [1]
