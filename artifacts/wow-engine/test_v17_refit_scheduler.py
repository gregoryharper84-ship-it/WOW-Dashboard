"""Candidate-only recalibration constraints with deliberately synthetic fixtures."""
from datetime import datetime, timedelta, timezone
from copy import deepcopy

import pytest

from v17.refit_scheduler import build_recalibration_candidate, RefitEvidenceError


def proposal():
    return dict(proposal_id="example-proposal", proposal_type="RECALIBRATION_CHALLENGER",
                target_key="PROP|NFL|NFL|PLAYER_YARDS|YARDS|TEST|v1|cal-v1",
                automatic_promotion=False, can_execute=False)


def rows(n=160, p=0.75):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    out = []
    for k in range(n):
        game = start + timedelta(days=k)
        out.append(dict(source_prediction_id=f"prediction-{k}",
                        official_event_id=f"game-{k}",
                        cohort_key=proposal()["target_key"],
                        probability_source="calibrated_probability",
                        model_timestamp=(game - timedelta(hours=3)).isoformat(),
                        event_start_time=game.isoformat(),
                        settlement_timestamp=(game + timedelta(hours=4)).isoformat(),
                        probability=p, outcome_target=int(k % 2 == 0)))
    return out


def assert_code(code, callable_):
    with pytest.raises(RefitEvidenceError) as err:
        callable_()
    assert err.value.code == code


def test_reproducible_candidate_only_and_paired_metrics():
    rs = rows()
    old = deepcopy(rs)
    a = build_recalibration_candidate(proposal(), rs)
    b = build_recalibration_candidate(proposal(), list(reversed(rs)))
    assert a == b
    assert rs == old
    assert a["fit_event_n"] >= 50
    assert a["diagnostic_event_n"] >= 30
    assert a["fitted_intercept"] < 0
    assert a["delta_brier"] > 0
    assert a["delta_log_loss"] > 0
    assert a["probability_publishable"] is False
    assert a["automatic_promotion"] is False
    assert a["eligible_for_governed_review"] is False
    assert a["can_execute"] is False
    assert a["lower_bound_reliability_status"] == "NOT_EVALUATED"


def test_fail_closed_on_different_proposal_and_broken_forecast():
    p = proposal()
    p["proposal_type"] = "MODEL_FEATURE_CHALLENGER"
    assert_code("REFIT_PROPOSAL_TYPE_UNSUPPORTED", lambda: build_recalibration_candidate(p, rows()))
    r = rows()
    r[0]["model_timestamp"] = r[0]["settlement_timestamp"]
    assert_code("REFIT_TEMPORAL_IDENTITY_INVALID", lambda: build_recalibration_candidate(proposal(), r))


def test_fail_closed_on_wrong_probability_cohort_and_duplicate():
    r = rows()
    r[0]["probability_source"] = "raw_model_probability"
    assert_code("REFIT_NOT_CALIBRATED_FORECAST", lambda: build_recalibration_candidate(proposal(), r))
    r = rows()
    r[0]["cohort_key"] = "different"
    assert_code("REFIT_COHORT_MISMATCH", lambda: build_recalibration_candidate(proposal(), r))
    r = rows()
    r.append(dict(r[0]))
    assert_code("REFIT_DUPLICATE_PREDICTION", lambda: build_recalibration_candidate(proposal(), r))


def test_event_grouping_and_settlement_cutoff_no_leakage():
    r = rows()
    r[10]["settlement_timestamp"] = datetime(2026, 7, 1, tzinfo=timezone.utc).isoformat()
    v = build_recalibration_candidate(proposal(), r)
    assert v["excluded_unmatured_training_n"] == 1
    second = dict(rows()[0])
    second["source_prediction_id"] = "extra"
    second["probability"] = 0.60
    v2 = build_recalibration_candidate(proposal(), rows() + [second])
    assert v2["fit_prediction_n"] > v2["fit_event_n"]


def test_insufficient_data_cannot_create_a_candidate():
    assert_code("REFIT_EVENT_SAMPLE_INSUFFICIENT", lambda: build_recalibration_candidate(proposal(), rows(40)))


def test_event_or_model_identity_protection():
    r = rows()
    r[1]["outcome_target"] = None
    assert_code("REFIT_BINARY_SETTLEMENT_REQUIRED", lambda: build_recalibration_candidate(proposal(), r))
    r = rows()
    r[2]["probability"] = float("nan")
    assert_code("REFIT_PROBABILITY_INVALID", lambda: build_recalibration_candidate(proposal(), r))
    r = rows()
    r[2]["model_timestamp"] = "2026-01-01"
    assert_code("REFIT_MODEL_TIME_REQUIRED", lambda: build_recalibration_candidate(proposal(), r))


def test_proposal_safety_flags_are_mandatory():
    p = proposal()
    p["automatic_promotion"] = True
    assert_code("REFIT_PROPOSAL_SAFETY_INVALID", lambda: build_recalibration_candidate(p, rows()))
