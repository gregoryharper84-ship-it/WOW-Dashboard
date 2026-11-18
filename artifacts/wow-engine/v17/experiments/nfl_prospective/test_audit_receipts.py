"""Independent deterministic receipt checks, not a fitted model or source fetcher."""
from copy import deepcopy
from datetime import datetime, timezone
from math import log
from pathlib import Path
import json
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audit_receipts import audit_forecast, audit_grade, audit_cohort  # TDD: should fail before source exists

KICKOFF = "2026-11-20T01:15:00+00:00"
PRE = "2026-11-18T18:17:05+00:00"
AFTER = "2026-11-20T05:30:00+00:00"
MODEL = "a" * 64
FEATURE = "b" * 64
PRE_SCHEDULE = "c" * 64
POST_SCHEDULE = "d" * 64
ESPN = "e" * 64


def frozen(**changes):
    row = {
        "shadow_id": "11111111-1111-4111-8111-111111111111",
        "challenger_id": "NFL_PREGAME_ML_CHALLENGER_V1",
        "official_event_id": "2026_11_IND_HOU",
        "provider_event_id": "2026_11_IND_HOU",
        "event_start_time_utc": KICKOFF,
        "prediction_created_at": PRE,
        "created_at": PRE,
        "home_team": "HOU", "away_team": "IND", "selected_participant": "HOU",
        "calibrated_probability": 0.62,
        "local_wilson_lower": 0.55, "bootstrap_q10_lower": 0.53,
        "composite_lower_bound": 0.53,
        "source_feature_hash": FEATURE,
        "model_family": "NFL_PREGAME_ML_CHALLENGER_V1",
        "feature_schema_version": "NFL_PRIOR_FEATURES_V1",
        "training_cutoff": "2024-12-31", "calibration_season": 2025,
        "calibration_method": "PLATT_TIME_SPLIT_V1",
        "research_metadata": {
            "status": "PREGAME_SHADOW_UNCERTIFIED",
            "source_snapshot_id": "22222222-2222-4222-8222-222222222222",
            "source_snapshot_fetched_at": "2026-11-18T13:11:38+00:00",
            "source_snapshot_sha256": PRE_SCHEDULE,
            "feature_row_inputs_hash": FEATURE,
            "feature_source_sha256s": [PRE_SCHEDULE],
            "model_artifact_sha256": MODEL,
            "calibrated_home_probability": 0.62,
            "incumbent": {
                "event_prediction_id": "33333333-3333-4333-8333-333333333333",
                "model_version": "INCUMBENT_V1",
                "calibrated_home_probability": 0.58,
                "created_at": "2026-11-18T17:00:00+00:00",
                "event_start_time_utc": KICKOFF,
                "is_scientifically_certified": False,
            },
        },
        "lifecycle_state": "FORWARD_SHADOW",
        "outcome": None, "graded_at": None,
        "brier": None, "log_loss": None, "hit": None,
        "grade_source_manifest": {},
        "can_execute": False, "probability_publishable": False,
        "promotion_authorized": False, "terminal_authority": "V17_TERMINAL_REDUCER",
    }
    row.update(changes)
    return row


def independent_final(**changes):
    final = {
        "nflverse": {
            "game_id": "2026_11_IND_HOU", "event_start_time_utc": KICKOFF,
            "home_team": "HOU", "away_team": "IND", "home_score": 24,
            "away_score": 21, "raw_sha256": POST_SCHEDULE,
            "fetched_at": AFTER, "completed": True,
        },
        "espn": {
            "event_id": "512345", "event_start_time_utc": KICKOFF,
            "home_team": "HOU", "away_team": "IND", "home_score": 24,
            "away_score": 21, "raw_sha256": ESPN,
            "fetched_at": AFTER, "completed": True,
        },
    }
    final.update(changes)
    return final


def graded(**changes):
    row = frozen()
    row.update({
        "lifecycle_state": "GRADED", "outcome": 1, "hit": True,
        "graded_at": AFTER, "brier": (0.62-1)**2,
        "log_loss": -log(0.62),
        "grade_source_manifest": {
            "status": "TWO_PROVIDER_RECONCILED_NOT_LEAGUE_CERTIFIED",
            "sources": ["ESPN", "NFLVERSE"],
            "nflverse_content_sha256": POST_SCHEDULE,
            "espn_raw_sha256": ESPN, "espn_event_id": "512345",
            "home_score": 24, "away_score": 21,
        },
    })
    row.update(changes)
    return row


def test_valid_frozen_row_is_accepted_as_uncertified_pregame():
    a = audit_forecast(frozen())
    assert a["status"] == "PREGAME_RECEIPT_VALID_UNCERTIFIED"
    assert a["ok"] is True
    assert a["paired_incumbent_eligible"] is True
    assert a["can_execute"] is False and a["scientifically_certified"] is False


@pytest.mark.parametrize("field,value", [
    ("created_at", KICKOFF),
    ("prediction_created_at", KICKOFF),
    ("created_at", "2026-11-20T01:15:01+00:00"),
    ("created_at", "2026-11-18T18:17:05"),  # naive dates cannot certify chronology
])
def test_rejects_late_or_ambiguous_forecast_timestamps(field, value):
    assert audit_forecast(frozen(**{field: value}))["ok"] is False


def test_bad_model_digest_and_feature_identity_are_rejected():
    a = frozen()
    a["research_metadata"]["model_artifact_sha256"] = "BAD"
    assert audit_forecast(a)["ok"] is False
    b = frozen(source_feature_hash="x" * 64)
    assert audit_forecast(b)["ok"] is False


def test_forged_safety_flags_rejected():
    for f in ("can_execute", "probability_publishable", "promotion_authorized"):
        assert audit_forecast(frozen(**{f: True}))["ok"] is False


def test_false_terminal_authority_rejected():
    assert audit_forecast(frozen(terminal_authority="OTHER"))["ok"] is False


def test_stale_schedule_source_and_wrong_model_split_rejected():
    a = frozen()
    a["research_metadata"]["source_snapshot_fetched_at"] = "2026-11-11T12:00:00+00:00"
    assert audit_forecast(a)["ok"] is False
    assert audit_forecast(frozen(training_cutoff="2026-11-01"))["ok"] is False


def test_late_or_missing_incumbent_not_paired_but_forecast_acceptable():
    row = frozen()
    row["research_metadata"]["incumbent"]["created_at"] = KICKOFF
    result = audit_forecast(row)
    assert result["ok"] is True and result["paired_incumbent_eligible"] is False
    assert audit_forecast(frozen(research_metadata={**frozen()["research_metadata"], "incumbent": None}))["paired_incumbent_eligible"] is False


def test_graded_row_with_two_independent_sources_recomputes_grade():
    out = audit_grade(graded(), independent_final())
    assert out["status"] == "GRADE_MATH_AND_SUPPLIED_SOURCES_MATCH_RESEARCH_ONLY"
    assert out["ok"] is True and out["independently_league_certified"] is False
    assert out["paired_incumbent_eligible"] is True
    assert out["paired_brier_delta"] < 0
    assert out["paired_log_loss_delta"] < 0


def test_missing_external_final_never_establishes_independent_grade():
    a = audit_grade(graded(), None)
    assert a["ok"] is False and a["status"] == "INDEPENDENT_FINAL_EVIDENCE_MISSING"


def test_ungraded_forecast_is_not_claimed_as_a_completed_grade():
    assert audit_grade(frozen(), independent_final())["status"] == "AWAITING_PERSISTED_GRADE"


def test_provider_disagreement_or_tied_result_rejected():
    final = independent_final()
    final["espn"]["away_score"] = 22
    assert audit_grade(graded(), final)["ok"] is False
    tie = independent_final()
    for source in tie.values():
        source["home_score"] = source["away_score"]
    assert audit_grade(graded(), tie)["ok"] is False


def test_manifest_source_hash_mismatch_rejected():
    final = independent_final()
    final["espn"]["raw_sha256"] = "f" * 64
    assert audit_grade(graded(), final)["ok"] is False


def test_stored_brier_log_loss_and_outcome_must_match_independent_math():
    assert audit_grade(graded(brier=0.01), independent_final())["ok"] is False
    assert audit_grade(graded(log_loss=0.01), independent_final())["ok"] is False
    assert audit_grade(graded(outcome=0), independent_final())["ok"] is False


def test_grade_cannot_be_before_game_and_no_false_raw_digest():
    assert audit_grade(graded(graded_at=PRE), independent_final())["ok"] is False
    final = independent_final()
    final["nflverse"]["fetched_at"] = PRE
    assert audit_grade(graded(), final)["ok"] is False


def test_cohort_never_claims_certification_from_single_good_pair():
    a = audit_cohort([graded()], {"2026_11_IND_HOU": independent_final()})
    assert a["verified_grades"] == 1
    assert a["paired_count"] == 1
    assert a["scientifically_certified"] is False
    assert a["status"] == "PROSPECTIVE_SAMPLE_INSUFFICIENT"


def test_duplicate_event_rejected_from_cohort():
    assert audit_cohort([graded(), graded()], {"2026_11_IND_HOU": independent_final()})["status"] == "DUPLICATE_EVENT_OR_VERSION"


def test_mixed_challenger_model_family_fails_closed():
    other = graded(official_event_id="2026_11_NYJ_NE", challenger_id="ANOTHER")
    assert audit_cohort([graded(), other], {"2026_11_IND_HOU": independent_final()})["status"] == "MIXED_CHALLENGER_VERSION"


def test_cli_offline_acceptance_requires_explicit_provided_final(tmp_path):
    path = tmp_path / "rows.json"
    finals = tmp_path / "finals.json"
    path.write_text(json.dumps([graded()]))
    finals.write_text(json.dumps({"2026_11_IND_HOU": independent_final()}))
    cmd = [sys.executable, str(Path(__file__).parent / "audit_receipts.py"), "--rows", str(path), "--finals", str(finals)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    answer = json.loads(result.stdout)
    assert answer["verified_grades"] == 1 and not answer["scientifically_certified"]


def test_cohort_rejects_same_challenger_with_two_artifact_digests():
    second = frozen(official_event_id="2026_11_NYJ_NE", provider_event_id="2026_11_NYJ_NE")
    second["research_metadata"]["model_artifact_sha256"] = "f"*64
    a = audit_cohort([frozen(), second], {})
    assert a["status"] == "MIXED_MODEL_ARTIFACT_VERSION"


def test_malformed_grade_manifest_sources_yields_hold_not_exception():
    a = graded()
    a["grade_source_manifest"]["sources"] = 42
    assert audit_grade(a, independent_final())["ok"] is False


def test_malformed_event_id_in_cohort_yields_invalid_not_exception():
    a = graded(official_event_id=["2026_11_IND_HOU"])
    r = audit_cohort([a], {})
    assert r["verified_grades"] == 0
    assert r["scientifically_certified"] is False


def test_canonical_event_tokens_must_match_frozen_teams():
    bad = frozen(official_event_id="2026_11_NYJ_NE", provider_event_id="2026_11_NYJ_NE")
    a = audit_forecast(bad)
    assert a["ok"] is False
    assert "CANONICAL_EVENT_TEAM_CONTRADICTION" in a["reasons"]


def test_paired_cohort_rejects_mixed_incumbent_model_versions():
    second = frozen(official_event_id="2026_11_NYJ_NE", provider_event_id="2026_11_NYJ_NE",
                    home_team="NE", away_team="NYJ", selected_participant="NE")
    second["research_metadata"]["incumbent"]["model_version"] = "CHAMPION_V2"
    result = audit_cohort([frozen(), second], {})
    assert result["status"] == "MIXED_INCUMBENT_MODEL_VERSION"
    assert result["paired_count"] == 0


def test_unhashable_model_artifact_digest_returns_invalid_not_exception():
    bad = frozen()
    bad["research_metadata"]["model_artifact_sha256"] = []
    report = audit_cohort([bad], {})
    assert report["status"] == "MODEL_ARTIFACT_DIGEST_INVALID"
    assert report["verified_grades"] == 0
