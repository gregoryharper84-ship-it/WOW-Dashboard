"""Contract-first tests: deliberately red before bridge implementation."""
import hashlib
import json
from copy import deepcopy

import pytest

from opponent_challenger_bridge import (
    score_existing_team_state_candidate,
    bridge_team_script_to_prop,
    build_paired_forward_row,
    hash_payload,
    CAN_EXECUTE,
    TERMINAL_AUTHORITY,
)


def artifact():
    model={"model_family":"NFL_DYNAMIC_TEAM_STATE_LOGIT_V2",
           "artifact_format":"STANDARDIZED_LOGISTIC_JSON_V1",
           "feature_names":["opponent_strength_edge","rest_edge"],
           "scaler_mean":[0.0,0.0],"scaler_scale":[1.0,2.0],
           "intercept":0.0,"coefficients":[1.0,2.0],
           "train_end_index":400,"calibration_end_index":500,
           "split_policy":"CHRONOLOGICAL_60_20_20"}
    return {"sport":"NFL","model_artifact_version":"NFL_DYNAMIC_TEAM_STATE_LOGIT_V2_abc",
            "artifact_payload":model, "artifact_checksum":hash_payload(model),
            "calibrator_payload":{"method":"IDENTITY_RAW_PROBABILITY_V1","training_n":100,"bins":[]},
            "calibrator_content_hash": hash_payload({"method":"IDENTITY_RAW_PROBABILITY_V1","training_n":100,"bins":[]}),
            "model_created_at_utc":"2026-11-16T12:00:00Z",
            "fit_cutoff_utc":"2026-10-01T00:00:00Z",
            "calibration_cutoff_utc":"2026-11-10T00:00:00Z",
            "research_screen_pass":False,"source_review_status":"REQUIRED",
            "lifecycle_state":"CANDIDATE","promoted":False,"active":False}


def features():
    vec={"opponent_strength_edge":1.0,"rest_edge":1.0}
    return {"sport":"NFL","official_event_id":"nfl-2026-11-19-001",
            "home_team":"KC","away_team":"DEN",
            "event_start_utc":"2026-11-19T01:00:00Z",
            "snapshot_at_utc":"2026-11-18T19:00:00Z",
            "feature_as_of_utc":"2026-11-18T18:00:00Z",
            "feature_source_id":"official-pregame-archive-xyz",
            "features":vec,"feature_content_hash":hash_payload(vec),
            "feature_schema_version":"NFL_DYNAMIC_TEAM_STATE_FEATURES_V2"}


def team_states():
    return {"sport":"NFL","official_event_id":"nfl-2026-11-19-001",
            "event_start_utc":"2026-11-19T01:00:00Z",
            "snapshot_at_utc":"2026-11-18T19:30:00Z",
            "source_type":"LLP_TEAM_BETTING_ENGINE",
            "team_event_model_artifact_hash":"sha256:llpfit",
            "scenario_receipt_id":"frozen-state-1",
            "scenario_frozen_at_utc":"2026-11-18T18:00:00Z",
            "scenario_fit_cutoff_utc":"2026-11-15T00:00:00Z",
            "probability_basis":"FITTED_TEAM_STATE", "team":"KC",
            "states":[{"state":"LEADING","weight":0.3},
                      {"state":"TIED","weight":0.2},
                      {"state":"TRAILING","weight":0.5}]}


def prop_states():
    return {"sport":"NFL","official_event_id":"nfl-2026-11-19-001",
            "event_start_utc":"2026-11-19T01:00:00Z",
            "snapshot_at_utc":"2026-11-18T19:30:00Z",
            "source_type":"WOW_PROP_LANE","team":"KC","player_id":"p1",
            "stat":"RUSH_ATTEMPTS","exact_line":18.5,
            "prop_fit_artifact_hash":"sha256:propfit",
            "prop_fit_cutoff_utc":"2026-11-15T00:00:00Z",
            "prop_source_snapshot_hash":"sha256:propinputs",
            "states":[{"state":"LEADING","expected_count":24,"count_variance":8,"p_over_exact_line":0.78},
                      {"state":"TIED","expected_count":19,"count_variance":7,"p_over_exact_line":0.5},
                      {"state":"TRAILING","expected_count":11,"count_variance":6,"p_over_exact_line":0.12}]}


def incumbent():
    return {"sport":"NFL","official_event_id":"nfl-2026-11-19-001",
            "event_start_utc":"2026-11-19T01:00:00Z",
            "prediction_created_at_utc":"2026-11-18T18:00:00Z",
            "receipt_id":"frozen-champion-001", "model_artifact_hash":"sha256:incumbent",
            "selected_participant":"KC","market_family":"MONEYLINE",
            "market_period":"FULL_GAME","settlement_basis":"INCLUDE_OT",
            "probability":0.63,"calibrated_lower_bound":0.55,
            "source_snapshot_hash":"sha256:independent"}


def official_result():
    return {"sport":"NFL","official_event_id":"nfl-2026-11-19-001",
            "finalized_at_utc":"2026-11-19T04:00:00Z",
            "source_type":"INDEPENDENT_OFFICIAL","source_id":"nfl-final-001",
            "selected_participant":"KC","outcome":True,
            "market_family":"MONEYLINE","market_period":"FULL_GAME",
            "settlement_basis":"INCLUDE_OT"}


def test_fitted_logistic_identity_and_research_only():
    import math
    r=score_existing_team_state_candidate(features(),artifact())
    assert r["status"] == "RESEARCH_SCREEN_FAILED"
    assert r["candidate_probability"]==pytest.approx(1/(1+math.exp(-2)))
    assert r["calibrated_lower_bound"] is None
    assert r["publishable"] is False
    assert r["can_execute"] is False


def test_artifact_checksum_and_feature_hash_rejection():
    a=artifact(); a["artifact_payload"]["intercept"]=50
    assert score_existing_team_state_candidate(features(),a)["candidate_probability"] is None
    f=features();f["features"]["rest_edge"]=-99
    assert score_existing_team_state_candidate(f,artifact())["candidate_probability"] is None


def test_no_post_event_or_post_model_evidence():
    f=features();f["snapshot_at_utc"]="2026-11-19T02:00:00Z"
    assert score_existing_team_state_candidate(f,artifact())["status"]=="INVALID_PREGAME_PROVENANCE"
    a=artifact();a["model_created_at_utc"]="2026-11-19T00:00:00Z"
    assert score_existing_team_state_candidate(features(),a)["status"]=="MODEL_NOT_AVAILABLE_AT_SNAPSHOT"


def test_missing_or_extra_opponent_features_are_rejected():
    f=features();f["features"]["extra"] =1;f["feature_content_hash"]=hash_payload(f["features"])
    assert score_existing_team_state_candidate(f,artifact())["status"]=="FEATURE_SCHEMA_MISMATCH"


def test_feature_and_model_finite_checks():
    f=features();f["features"]["rest_edge"]=float('nan')
    assert score_existing_team_state_candidate(f,artifact())["candidate_probability"] is None
    a=artifact();a["artifact_payload"]["scaler_scale"]=[1,0];a["artifact_checksum"]=hash_payload(a["artifact_payload"])
    assert score_existing_team_state_candidate(features(),a)["candidate_probability"] is None


def test_empirical_calibrator_matches_nearest_center_contract():
    a=artifact();a["calibrator_payload"]={"method":"EMPIRICAL_WILSON_BINS_V1","bins":[
        {"raw_mean":.20,"calibrated_probability":.30},
        {"raw_mean":.90,"calibrated_probability":.85}]}
    a["calibrator_content_hash"] = hash_payload(a["calibrator_payload"])
    r=score_existing_team_state_candidate(features(),a)
    assert r["candidate_probability"]==pytest.approx(.85)


def test_prop_bridge_uses_independent_team_score_scenarios():
    r=bridge_team_script_to_prop(team_states(),prop_states())
    assert r["status"]=="CONDITIONAL_USAGE_RESEARCH_ONLY"
    assert r["expected_count"]==pytest.approx(16.5)
    assert r["p_over"]==pytest.approx(.3*.78+.2*.5+.5*.12)
    assert r["publication_allowed"] is False


def test_prop_bridge_rejects_moneyline_proxy_and_cross_event():
    t=team_states();t["probability_basis"]="IMPLIED_FROM_MONEYLINE"
    assert bridge_team_script_to_prop(t,prop_states())["p_over"] is None
    p=prop_states();p["official_event_id"]="other"
    assert bridge_team_script_to_prop(team_states(),p)["p_over"] is None


def test_prop_bridge_missing_state_fails_closed():
    t=team_states();t["states"].pop()
    assert bridge_team_script_to_prop(t,prop_states())["status"]=="SCENARIO_EVIDENCE_INSUFFICIENT"


def test_forward_receipt_pair_rejects_unfitted_calibration_claim():
    candidate=score_existing_team_state_candidate(features(),artifact())
    row=build_paired_forward_row(incumbent(),candidate,official_result())
    assert row["status"]=="RESEARCH_ONLY_UNCALIBRATED_PAIR"
    assert row["challenger_probability"] is not None
    assert row["challenger_lower_bound"] is None
    assert row["certification_eligible"] is False


def test_pair_rejects_mismatched_events_and_settlement():
    c=score_existing_team_state_candidate(features(),artifact())
    result=official_result();result["official_event_id"]="another"
    assert build_paired_forward_row(incumbent(),c,result)["status"]=="INVALID_PAIR"
    result=official_result();result["finalized_at_utc"]="2026-11-18T23:59:00Z"
    assert build_paired_forward_row(incumbent(),c,result)["status"]=="INVALID_PAIR"


def test_governance_never_mutates_inputs():
    a=artifact();f=features();t=team_states();p=prop_states();
    before=deepcopy([a,f,t,p])
    assert not CAN_EXECUTE and TERMINAL_AUTHORITY=="V17_TERMINAL_REDUCER"
    score_existing_team_state_candidate(f,a)
    bridge_team_script_to_prop(t,p)
    assert [a,f,t,p] == before


def test_away_side_uses_complement_of_home_probability():
    c=score_existing_team_state_candidate(features(),artifact())
    inc=incumbent(); inc["selected_participant"]="DEN"
    official=official_result(); official["selected_participant"]="DEN"
    r=build_paired_forward_row(inc,c,official)
    assert r["status"]=="RESEARCH_ONLY_UNCALIBRATED_PAIR"
    assert r["challenger_probability"]==pytest.approx(1-c["candidate_probability"])
    assert not r["certification_eligible"]


def test_production_model_is_never_eligible_for_shadow_candidate_scoring():
    a=artifact();a["promoted"]=True
    assert score_existing_team_state_candidate(features(),a)["status"]=="MODEL_LIFECYCLE_NOT_RESEARCH_CANDIDATE"


def test_identity_calibration_cannot_claim_precise_lower_bound():
    a=artifact();a["research_screen_pass"]=True
    r=score_existing_team_state_candidate(features(),a)
    assert r["status"]=="SHADOW_CANDIDATE_ONLY"
    assert r["calibrated_lower_bound"] is None
    assert r["candidate_probability_is_certified"] is False


def test_calibrator_tamper_and_missing_digest_are_rejected():
    a=artifact();a["calibrator_payload"]["method"]="EMPIRICAL_WILSON_BINS_V1"
    r=score_existing_team_state_candidate(features(),a)
    assert r["status"]=="CALIBRATOR_HASH_MISMATCH"
    assert r["candidate_probability"] is None
    a=artifact();a.pop("calibrator_content_hash")
    assert score_existing_team_state_candidate(features(),a)["status"]=="PROVENANCE_HASH_MISSING"


def test_pair_rejects_first_half_or_missing_settlement_period():
    c=score_existing_team_state_candidate(features(),artifact())
    inc=incumbent();result=official_result()
    inc["market_period"]=result["market_period"]="FIRST_HALF"
    assert build_paired_forward_row(inc,c,result)["status"]=="INVALID_PAIR"
    inc=incumbent();result=official_result()
    inc.pop("market_period");result.pop("market_period")
    assert build_paired_forward_row(inc,c,result)["status"]=="INVALID_PAIR"
    inc=incumbent();result=official_result()
    inc["settlement_basis"]=result["settlement_basis"]="REGULATION_ONLY"
    assert build_paired_forward_row(inc,c,result)["status"]=="INVALID_PAIR"


def test_pair_exposes_existing_champion_challenger_comparator_schema():
    c=score_existing_team_state_candidate(features(),artifact())
    out=build_paired_forward_row(incumbent(),c,official_result())
    assert out["status"]=="RESEARCH_ONLY_UNCALIBRATED_PAIR"
    assert out["outcome"] is True
    assert out["champion_probability"]==pytest.approx(.63)
    assert out["challenger_probability"]==pytest.approx(c["candidate_probability"])
    assert out["certification_eligible"] is False
