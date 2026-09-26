import numpy as np
import pytest

from v17.experiments import ncaaf_publication_bound_challenger as challenger


def _candidate():
    return {
        "candidate_id": "candidate-1",
        "sport": "NCAAF",
        "league": "NCAAF",
        "model_family": challenger.SUPPORTED_MODEL_FAMILY,
        "model_artifact_version": "artifact-v1",
        "training_dataset_hash": "a" * 64,
        "artifact_checksum": "b" * 64,
        "feature_schema_version": "schema-v1",
        "training_rows": 20,
        "calibration_rows": 50,
        "test_rows": 30,
        "lifecycle_state": "CANDIDATE",
        "promoted": False,
        "active": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
        "artifact_payload": {
            "feature_names": ["x"],
            "scaler_mean": [0.0],
            "scaler_scale": [1.0],
            "coefficients": [1.0],
            "intercept": 0.0,
        },
        "calibrator_payload": {
            "method": "IDENTITY_RAW_PROBABILITY_V1",
            "training_n": 50,
            "bins": [],
        },
    }


def _rows():
    values = np.linspace(-2.5, 2.5, 100)
    # Mostly align outcomes with the fitted direction while retaining misses.
    outcomes = [(value + (0.55 if i % 11 == 0 else 0.0)) > 0 for i, value in enumerate(values)]
    return [
        {
            "official_event_id": f"event-{i:03d}",
            "event_start_time": f"2026-01-01T{i % 24:02d}:{i % 60:02d}:00+00:00",
            "features": {"x": float(x)},
            "outcome_json": {"home_win": bool(y)},
            "market_features_used": False,
            "can_execute": False,
        }
        for i, (x, y) in enumerate(zip(values, outcomes))
    ]


def test_bound_challenger_is_non_serving_event_dynamic_and_uses_untouched_test(monkeypatch):
    candidate = _candidate()
    rows = _rows()
    monkeypatch.setattr(challenger, "_exact_candidate", lambda db, candidate_id: candidate)
    monkeypatch.setattr(
        challenger,
        "_verified_receipt",
        lambda db, candidate: {
            "receipt_id": "receipt-1",
            "evidence_sha256": "c" * 64,
            "probability_publishable": False,
            "can_execute": False,
        },
    )
    monkeypatch.setattr(challenger, "_paginate_rows", lambda db, candidate: rows)

    result = challenger.run_ncaaf_publication_bound_challenger(object(), "candidate-1")

    assert result["status"] == "EXPERIMENT_COMPLETE"
    assert result["split"] == {"train_n": 20, "calibration_n": 50, "untouched_test_n": 30}
    assert result["untouched_test"]["n"] == 30
    assert result["bound_method"] == challenger.BOUND_METHOD
    assert 0.015 <= result["calibration_error_margin"] <= 0.08
    widths = result["dynamic_width_summary"]
    assert widths["min"] < widths["max"]
    assert result["review_checks"]["event_dynamic_width_nonconstant"] is True
    assert result["recommendation_state"] in {
        "CHALLENGER_PASS_GOVERNED_REVIEW_REQUIRED",
        "CHALLENGER_FAIL_REVIEW_REQUIRED",
    }
    assert result["final_refresh_contract"]["material_or_unresolved_change_action"] == "MODEL_QUALIFIED_HOLD"
    assert result["final_refresh_contract"]["manual_probability_adjustment_allowed"] is False
    assert result["automatic_certification"] is False
    assert result["automatic_promotion"] is False
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["database_mutated"] is False
    assert result["production_registry_mutated"] is False
    assert result["global_terminal_reducer"] == "V17_TERMINAL_REDUCER"
    assert result["can_execute"] is False


def test_artifact_scoring_matches_standardized_logistic():
    candidate = _candidate()
    rows = [
        {"features": {"x": -1.5}, "market_features_used": False, "can_execute": False},
        {"features": {"x": 1.5}, "market_features_used": False, "can_execute": False},
    ]
    probabilities = challenger._artifact_probabilities(candidate, rows)
    expected = np.asarray([1.0 / (1.0 + np.exp(1.5)), 1.0 / (1.0 + np.exp(-1.5))])
    assert np.allclose(probabilities, expected, atol=1e-12, rtol=0.0)


def test_event_bounds_vary_with_leverage():
    candidate = _candidate()
    rows = _rows()
    probabilities, logits, design = challenger._artifact_design(candidate, rows)
    covariance, _rank = challenger._parameter_covariance(design[:20], probabilities[:20])
    lower, upper, se = challenger._event_bounds(
        logits=logits,
        design=design,
        covariance=covariance,
        calibration_margin=0.02,
    )
    assert np.all(lower < probabilities)
    assert np.all(upper > probabilities)
    assert float(np.max(se) - np.min(se)) > 0.0


def test_counterexample_bins_report_lower_bound_reliability():
    lower = np.asarray([0.10, 0.20, 0.30, 0.40, 0.50, 0.60], dtype=float)
    correct = np.asarray([1, 1, 1, 0, 1, 1], dtype=float)
    bins = challenger._counterexample_bins(lower, correct, bins=3)
    assert len(bins) == 3
    assert sum(row["n"] for row in bins) == 6
    assert all("reliability_margin" in row for row in bins)


def test_candidate_must_remain_inert(monkeypatch):
    candidate = _candidate()
    candidate["active"] = True

    class Result:
        data = [candidate]

    class Query:
        def select(self, *args, **kwargs): return self
        def eq(self, *args, **kwargs): return self
        def limit(self, *args, **kwargs): return self
        def execute(self): return Result()

    class DB:
        def table(self, *args, **kwargs): return Query()

    with pytest.raises(challenger.NCAAFPublicationBoundExperimentError) as exc:
        challenger._exact_candidate(DB(), "candidate-1")
    assert exc.value.code == "NCAAF_BOUND_CANDIDATE_INERTNESS_VIOLATION"


def test_non_identity_calibrator_fails_closed(monkeypatch):
    candidate = _candidate()
    candidate["calibrator_payload"]["method"] = "EMPIRICAL_WILSON_BINS_V1"

    class Result:
        data = [candidate]

    class Query:
        def select(self, *args, **kwargs): return self
        def eq(self, *args, **kwargs): return self
        def limit(self, *args, **kwargs): return self
        def execute(self): return Result()

    class DB:
        def table(self, *args, **kwargs): return Query()

    with pytest.raises(challenger.NCAAFPublicationBoundExperimentError) as exc:
        challenger._exact_candidate(DB(), "candidate-1")
    assert exc.value.code == "NCAAF_BOUND_CALIBRATOR_UNSUPPORTED"


def test_source_replay_receipt_is_mandatory(monkeypatch):
    candidate = _candidate()

    class Result:
        data = []

    class Query:
        def select(self, *args, **kwargs): return self
        def eq(self, *args, **kwargs): return self
        def order(self, *args, **kwargs): return self
        def limit(self, *args, **kwargs): return self
        def execute(self): return Result()

    class DB:
        def table(self, *args, **kwargs): return Query()

    with pytest.raises(challenger.NCAAFPublicationBoundExperimentError) as exc:
        challenger._verified_receipt(DB(), candidate)
    assert exc.value.code == "NCAAF_BOUND_SOURCE_REPLAY_NOT_VERIFIED"
