"""Synthetic numerical and adversarial regressions; never real sports forecasts."""
from copy import deepcopy

from challenger import CAN_EXECUTE, combat_style_mixture_shadow, market_dependency_shadow


def fc(**changes):
    row = {
        "prediction_id": "target", "sport": "BOXING", "market_role": "FAVORITE",
        "immutable_model_timestamp": "2026-11-14T18:00:00+00:00",
        "calibrated_probability": 0.88, "calibrated_lower_bound": 0.82,
        "market_prior_weight": 0.7, "independent_model_probability": 0.75,
    }
    row.update(changes)
    return row


def history():
    rows = []
    for i in range(34):
        for dependent in (True, False):
            rows.append({
                "prediction_id": f"hist-{int(dependent)}-{i}",
                "event_id": f"event-{int(dependent)}-{i}",
                "sport": "BOXING", "market_role": "FAVORITE",
                "immutable_pregame": True,
                "immutable_model_timestamp": "2026-10-10T17:00:00Z",
                "event_start_timestamp": "2026-10-10T20:00:00Z",
                "official_settlement_timestamp": "2026-10-10T22:00:00Z",
                "calibrated_probability": .88 if dependent else .70,
                "market_prior_weight": .7 if dependent else .2,
                "official_binary_outcome": 0 if i < 6 else 1,
            })
    return rows


def regimes():
    return [
        {"regime": "neutral", "regime_probability": .7, "event_id": "bout-001", "frozen_at": "2026-11-14T17:00:00Z",
         "conditional_win_probability": .95, "conditional_win_stress_floor": .87,
         "fitted_artifact_hash": "sha256:synth-a", "pregame_evidence_hash": "sha256:synth-b",
         "adverse_style_path": False},
        {"regime": "chain_wrestling", "regime_probability": .3, "event_id": "bout-001", "frozen_at": "2026-11-14T17:00:00Z",
         "conditional_win_probability": .10, "conditional_win_stress_floor": .05,
         "fitted_artifact_hash": "sha256:synth-a", "pregame_evidence_hash": "sha256:synth-b",
         "adverse_style_path": True},
    ]


def features():
    return {"takedown_defense_vs_chain_wrestling_delta": -.2,
            "feature_snapshot_hash": "sha256:synth-f",
            "event_id": "bout-001", "frozen_at": "2026-11-14T17:00:00Z"}


def test_market_dependent_requires_sample():
    result = market_dependency_shadow(fc(), [])
    assert result["status"] == "INSUFFICIENT_SAMPLE"
    assert result["shadow_lower_bound"] is None
    assert not result["publication_allowed"]


def test_market_dependency_preserves_inputs_and_point():
    candidate = fc()
    before = deepcopy(candidate)
    result = market_dependency_shadow(candidate, history())
    assert candidate == before
    assert result["status"] == "CHALLENGER_DIAGNOSTIC_ONLY"
    assert result["shadow_lower_bound"] <= before["calibrated_lower_bound"]
    assert result["original_calibrated_probability"] == 0.88
    assert result["cohort_n"] == {"dependent": 34, "independent": 34}
    assert result["probability_mutated"] is False


def test_nondependent_preserves_lower_bound():
    result = market_dependency_shadow(fc(market_prior_weight=.49), history())
    assert result["status"] == "NOT_MARKET_DEPENDENT"
    assert result["shadow_lower_bound"] == .82


def test_missing_independent_probability_fails_closed():
    assert market_dependency_shadow(fc(independent_model_probability=None), history())["status"] == "MODEL_INPUTS_INSUFFICIENT"


def test_future_settlement_not_used():
    rows = history()
    for record in rows:
        record["official_settlement_timestamp"] = "2026-12-01T22:00:00Z"
    result = market_dependency_shadow(fc(), rows)
    assert result["status"] == "INSUFFICIENT_SAMPLE"
    assert result["cohort_n"] == {"dependent": 0, "independent": 0}


def test_market_role_isolation():
    rows = history()
    for record in rows:
        record["market_role"] = "UPSET"
    result = market_dependency_shadow(fc(), rows)
    assert result["status"] == "INSUFFICIENT_SAMPLE"
    assert result["cohort_n"] == {"dependent": 0, "independent": 0}


def test_no_postevent_backfill():
    rows = history()
    for record in rows:
        record["immutable_pregame"] = False
    assert market_dependency_shadow(fc(), rows)["status"] == "INSUFFICIENT_SAMPLE"


def test_invalid_probability_or_naive_timestamp():
    assert market_dependency_shadow(fc(calibrated_probability=1.12), history())["status"] == "MODEL_INPUTS_INSUFFICIENT"
    assert market_dependency_shadow(fc(immutable_model_timestamp="2026-11-14"), history())["status"] == "MODEL_INPUTS_INSUFFICIENT"


def test_duplicate_prediction_receipt_not_counted_twice():
    records = history()
    result = market_dependency_shadow(fc(), records + [deepcopy(records[0])]*20)
    assert result["cohort_n"] == {"dependent": 34, "independent": 34}


def test_mma_mixture_arithmetic():
    result = combat_style_mixture_shadow(event_id="bout-001", event_start_timestamp="2026-11-14T23:00:00Z", sport="MMA", regimes=regimes(), style_features=features())
    assert result["status"] == "CHALLENGER_DIAGNOSTIC_ONLY"
    assert abs(result["mixture_probability_shadow"] - .695) < 1e-10
    assert abs(result["stress_floor_shadow"] - .624) < 1e-10
    assert abs(result["failure_path_score"] - .27) < 1e-10
    assert result["material_failure_path"]
    assert not result["publication_allowed"]


def test_boxing_requires_boxing_feature():
    boxing = {"high_guard_pressure_jab_absorption": .4, "feature_snapshot_hash": "sha256:synth-feature",
              "event_id": "bout-001", "frozen_at": "2026-11-14T17:00:00Z"}
    assert combat_style_mixture_shadow(event_id="bout-001", event_start_timestamp="2026-11-14T23:00:00Z", sport="BOXING", regimes=regimes(), style_features=boxing)["status"] == "CHALLENGER_DIAGNOSTIC_ONLY"
    assert combat_style_mixture_shadow(event_id="bout-001", event_start_timestamp="2026-11-14T23:00:00Z", sport="BOXING", regimes=regimes(), style_features=features())["status"] == "MODEL_INPUTS_INSUFFICIENT"


def test_missing_provenance_is_blocked():
    r = regimes()
    r[1].pop("fitted_artifact_hash")
    assert combat_style_mixture_shadow(event_id="bout-001", event_start_timestamp="2026-11-14T23:00:00Z", sport="MMA", regimes=r, style_features=features())["status"] == "SOURCE_PROVENANCE_MISSING"
    f = features()
    f.pop("feature_snapshot_hash")
    assert combat_style_mixture_shadow(event_id="bout-001", event_start_timestamp="2026-11-14T23:00:00Z", sport="MMA", regimes=regimes(), style_features=f)["status"] == "SOURCE_PROVENANCE_MISSING"


def test_non_exhaustive_or_overlapping_regime_weights():
    r = regimes()
    r[0]["regime_probability"] = .8
    assert combat_style_mixture_shadow(event_id="bout-001", event_start_timestamp="2026-11-14T23:00:00Z", sport="MMA", regimes=r, style_features=features())["status"] == "MODEL_INPUTS_INSUFFICIENT"


def test_missing_style_inputs_or_unknown_sport():
    assert combat_style_mixture_shadow(event_id="bout-001", event_start_timestamp="2026-11-14T23:00:00Z", sport="MMA", regimes=[], style_features=features())["status"] == "MODEL_INPUTS_INSUFFICIENT"
    assert combat_style_mixture_shadow(event_id="bout-001", event_start_timestamp="2026-11-14T23:00:00Z", sport="SOCCER", regimes=regimes(), style_features=features())["status"] == "MODEL_INPUTS_INSUFFICIENT"
    assert combat_style_mixture_shadow(event_id="bout-001", event_start_timestamp="2026-11-14T23:00:00Z", sport="MMA", regimes=regimes(), style_features={})["status"] == "MODEL_INPUTS_INSUFFICIENT"


def test_stress_floor_cannot_exceed_probability():
    r = regimes()
    r[0]["conditional_win_stress_floor"] = .98
    assert combat_style_mixture_shadow(event_id="bout-001", event_start_timestamp="2026-11-14T23:00:00Z", sport="MMA", regimes=r, style_features=features())["status"] == "MODEL_INPUTS_INSUFFICIENT"


def test_safety_flags():
    assert CAN_EXECUTE is False
    for result in (
        market_dependency_shadow(fc(), history()),
        combat_style_mixture_shadow(event_id="bout-001", event_start_timestamp="2026-11-14T23:00:00Z", sport="MMA", regimes=regimes(), style_features=features()),
    ):
        assert result["can_execute"] is False
        assert result["automatic_parameter_mutation"] is False
        assert result["publication_allowed"] is False
        assert result["probability_mutated"] is False


def test_duplicate_event_distinct_prediction_ids_count_once():
    rows = history()
    rows[1]["event_id"] = rows[0]["event_id"]
    result = market_dependency_shadow(fc(), rows)
    assert result["cohort_n"] == {"dependent": 34, "independent": 33}


def test_adverse_regime_realization_distinct_from_loss_mass():
    rows = regimes()
    rows[0]["regime_probability"] = .75
    rows[1]["regime_probability"] = .25
    rows[1]["conditional_win_probability"] = .90
    rows[1]["conditional_win_stress_floor"] = .80
    result = combat_style_mixture_shadow(event_id="bout-001", event_start_timestamp="2026-11-14T23:00:00Z", sport="MMA", regimes=rows, style_features=features())
    assert abs(result["failure_path_score"] - .025) < 1e-10
    assert result["adverse_regime_probability_max"] == .25
    assert result["material_failure_path"] is True


def test_historical_postkickoff_receipt_rejected():
    rows = history()
    for record in rows:
        record["event_start_timestamp"] = "2026-10-10T15:00:00Z"
    assert market_dependency_shadow(fc(), rows)["status"] == "INSUFFICIENT_SAMPLE"


def test_conflicting_duplicate_receipts_fail_closed_regardless_of_order():
    original = history()
    conflict = dict(original[0])
    conflict["official_binary_outcome"] = 1
    for records in ([conflict] + original, original + [conflict]):
        out = market_dependency_shadow(fc(), records)
        assert out["status"] == "SOURCE_RECEIPT_CONFLICT"
        assert out["shadow_lower_bound"] is None
        assert not out["publication_allowed"]


def test_style_provenance_foreign_event_rejected():
    wrong_regime = regimes()
    wrong_regime[0]["event_id"] = "different-bout"
    out = combat_style_mixture_shadow(event_id="bout-001", event_start_timestamp="2026-11-14T23:00:00Z", sport="MMA", regimes=wrong_regime, style_features=features())
    assert out["status"] == "SOURCE_PROVENANCE_MISMATCH"
    wrong_features = features()
    wrong_features["event_id"] = "different-bout"
    out = combat_style_mixture_shadow(event_id="bout-001", event_start_timestamp="2026-11-14T23:00:00Z", sport="MMA", regimes=regimes(), style_features=wrong_features)
    assert out["status"] == "SOURCE_PROVENANCE_MISMATCH"


def test_style_provenance_captured_after_kickoff_rejected():
    late_regime = regimes()
    late_regime[0]["frozen_at"] = "2026-11-15T01:00:00Z"
    out = combat_style_mixture_shadow(event_id="bout-001", event_start_timestamp="2026-11-14T23:00:00Z", sport="MMA", regimes=late_regime, style_features=features())
    assert out["status"] == "SOURCE_PROVENANCE_LATE"
    late_features = features()
    late_features["frozen_at"] = "2026-11-15T01:00:00Z"
    out = combat_style_mixture_shadow(event_id="bout-001", event_start_timestamp="2026-11-14T23:00:00Z", sport="MMA", regimes=regimes(), style_features=late_features)
    assert out["status"] == "SOURCE_PROVENANCE_LATE"

def test_event_representative_latest_pregame_receipt_order_invariant():
    records = history()
    older = dict(records[0])
    older["prediction_id"] = "older-alternative-same-event"
    older["immutable_model_timestamp"] = "2026-10-10T16:00:00Z"
    older["market_prior_weight"] = 0.2
    older["calibrated_probability"] = 0.30
    a = market_dependency_shadow(fc(), [older] + records)
    b = market_dependency_shadow(fc(), list(reversed(records + [older])))
    assert a["status"] == b["status"] == "CHALLENGER_DIAGNOSTIC_ONLY"
    for field in ("shadow_empirical_excess_tail", "shadow_lower_bound", "cohort_n"):
        assert a[field] == b[field]
