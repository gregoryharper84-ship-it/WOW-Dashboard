"""Genuine NFL fitted calibration producer evidence is distinct from model health."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from types import SimpleNamespace

import numpy as np

from nfl_event_features_p2 import FEATURE_ORDER, FEATURE_SCHEMA_VERSION
from nfl_event_model_v17 import score_feature_row
from test_v17_nfl_daily_stage_receipts import _fixture, _prove


def _scorer_output():
    req, row, governed = _fixture()
    n = len(FEATURE_ORDER)
    # Fixed illustrative coefficients; this exercises the *real* fitted math
    # and producer code, not a projected sportsbook probability or stage flag.
    model = SimpleNamespace(
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        feature_order=tuple(FEATURE_ORDER),
        feature_order_hash="test-only-order-hash",
        scaler_mean=np.zeros(n),
        scaler_scale=np.ones(n),
        coefficients=np.zeros(n),
        intercept=math.log(0.1948 / 0.8052),
        platt_a=1.0,
        platt_b=0.0,
        uncertainty_margin=0.0559,
        artifact_id=row["model_artifact_id"],
        artifact_checksum="sha256-fixture-model-bundle",
        model_artifact_version=row["model_version"],
        calibration_version=row["calibration_version"],
        calibration_training_n=row["calibration_training_n"],
        bounds_method_version=row["uncertainty_method"],
        training_dataset_hash="test-only-history-hash",
    )
    feature = {
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_order": list(FEATURE_ORDER),
        "feature_vector": [0.0] * n,
        "row_inputs_hash": "immutable-feature-hash",
        "training_eligible": True,
    }
    output = score_feature_row(model, feature)
    receipt = output["calibration_operation_receipt"]
    assert receipt["producer"] == "nfl_event_model_v17.score_feature_row"
    assert receipt["status"] == "COMPLETED"
    assert receipt["model_timestamp"] == output["model_timestamp"]
    assert receipt["feature_row_hash"] == feature["row_inputs_hash"]
    assert receipt["can_execute"] is False
    assert abs(output["calibrated_away_probability"] - 0.8052) < 1e-10
    return req, row, governed, output


def _bound_evidence():
    req, row, governed, output = _scorer_output()
    req.research_run_id = "run-exact-one"
    row["research_run_id"] = req.research_run_id
    row["prediction_identity_key"] = "immutable-prediction-key"
    row["feature_row_hash"] = output["calibration_operation_receipt"]["feature_row_hash"]
    for field in (
        "raw_home_probability", "calibrated_home_probability",
        "calibrated_away_probability", "calibrated_home_lower_bound",
        "calibrated_home_upper_bound", "calibrated_away_lower_bound",
        "calibrated_away_upper_bound",
    ):
        row[field] = output[field]
    produced = {
        **output["calibration_operation_receipt"],
        "model_timestamp": row["model_timestamp"],
        "research_run_id": row["research_run_id"],
        "prediction_identity_key": row["prediction_identity_key"],
        "official_event_id": row["official_event_id"],
        "event_key": row["event_key"],
        "requested_slate_date": row["requested_slate_date"],
        "event_start_time_utc": row["event_start_time_utc"],
        "source_snapshot_id": row["source_snapshot_id"],
        "source_snapshot_timestamp": row["source_snapshot_timestamp"],
    }
    encoded = json.dumps(produced, sort_keys=True, separators=(",", ":"), allow_nan=False)
    produced["evidence_sha256"] = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    row["model_output_snapshot"] = {
        **output,
        "model_timestamp": row["model_timestamp"],
        "calibration_operation_receipt": produced,
    }
    governed["llp_governance"]["final_refresh"] = {
        "source": "NFL_CANONICAL_SCHEDULE_REQUERY",
        "final_refresh_status": "PASS",
        "reasons": [],
        "checked_at": "2026-10-25T13:10:00+00:00",
        "model_timestamp": row["model_timestamp"],
        "scored_source_snapshot_id": row["source_snapshot_id"],
        "refresh_source_snapshot_id": row["source_snapshot_id"],
        "probability_invalidated": False,
        "rerun_required": False,
        "can_execute": False,
    }
    return req, row, governed


def test_actual_platt_operation_and_readback_prove_stage_but_not_forward_certification():
    req, row, governed = _bound_evidence()
    outcome = _prove(req, row, governed)
    assert outcome["nfl_stage_receipt"]["status"] == "PASS"
    assert outcome["nfl_stage_receipt"]["dynamic_calibration_stage"] == "PASS"
    assert outcome["nfl_stage_receipt"]["forward_certification_status"] == "HOLD"
    assert outcome["dynamic_calibration_complete"] is True
    assert outcome["probability_package_valid"] is True
    assert outcome["calibrated_lower_bound"] == row["calibrated_selection_lower_bound"]
    assert outcome["rank_eligible"] is False
    assert outcome["probability_publishable"] is False
    assert outcome["can_execute"] is False
    assert "NFL_FORWARD_CERTIFICATION_UNPROVEN" in outcome["blockers"]


def test_producer_proof_mismatch_or_forged_stage_cannot_promote():
    req, row, governed = _bound_evidence()
    for mutation in ("hash", "event", "model", "feature", "number", "missing"):
        changed = deepcopy(row)
        rec = changed["model_output_snapshot"]["calibration_operation_receipt"]
        if mutation == "hash":
            rec["evidence_sha256"] = "unverified"
        elif mutation == "event":
            rec["official_event_id"] = "wrong-match"
        elif mutation == "model":
            rec["model_artifact_id"] = "other-model"
        elif mutation == "feature":
            rec["feature_row_hash"] = "other-features"
        elif mutation == "number":
            rec["calibrated_home_probability"] = 0.9
        else:
            del changed["model_output_snapshot"]["calibration_operation_receipt"]
        outcome = _prove(req, changed, governed)
        assert outcome["nfl_stage_receipt"]["status"] == "HOLD", mutation
        assert outcome["dynamic_calibration_complete"] is False
        assert outcome["rank_eligible"] is False
        assert outcome["can_execute"] is False


def test_final_refresh_must_be_real_current_and_identity_matched():
    req, row, governed = _bound_evidence()
    for field, value in (
        ("final_refresh_status", "HOLD"),
        ("source", "SCHEDULED_STATUS_ONLY"),
        ("scored_source_snapshot_id", "different-snapshot"),
        ("model_timestamp", "2026-10-24T13:00:00Z"),
        ("checked_at", "2026-10-25T18:00:00+00:00"),
        ("refresh_source_snapshot_id", None),
        ("probability_invalidated", True),
        ("can_execute", True),
    ):
        edited = deepcopy(governed)
        edited["llp_governance"]["final_refresh"][field] = value
        outcome = _prove(req, row, edited)
        assert outcome["nfl_stage_receipt"]["status"] == "HOLD", field
        assert "NFL_FINAL_REFRESH_SOURCE_PROOF_UNPROVEN" in outcome["nfl_stage_receipt"]["blockers"]
        assert outcome["dynamic_calibration_complete"] is True  # independent stage
        assert outcome["can_execute"] is False


def test_pregame_receipt_does_not_retroactively_attest_reused_old_predictions():
    req, row, governed = _bound_evidence()
    existing = deepcopy(row)
    existing["model_output_snapshot"].pop("calibration_operation_receipt")
    outcome = _prove(req, existing, governed)
    assert outcome["nfl_stage_receipt"]["status"] == "HOLD"
    assert "NFL_DYNAMIC_CALIBRATION_PRODUCER_RECEIPT_MISSING" in outcome["nfl_stage_receipt"]["blockers"]
    assert outcome["rank_eligible"] is False


class _Query:
    def __init__(self, rows, *, filter_field=None):
        self.rows = rows
        self.filter_field = filter_field

    def select(self, *_a):
        return self

    def eq(self, field, value):
        if field == self.filter_field:
            self.rows = [row for row in self.rows if row.get(field) == value]
        return self

    def neq(self, field, value):
        if field == self.filter_field:
            self.rows = [row for row in self.rows if row.get(field) != value]
        return self

    def order(self, *_a, **_kw):
        return self

    def limit(self, *_a):
        return self

    def range(self, start, end):
        self.rows = self.rows[start:end + 1]
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class _CertifiedDb:
    def __init__(self, approved, graded, status="PASS", grade_version=None, unique_games=None):
        self.model = {
            "active": True, "promoted": True,
            "probability_publishable": approved,
        }
        self.health = {
            "graded_n": graded, "minimum_forward_required": 100,
            "status": status,
            "certification_recommendation": "FORWARD_EVIDENCE_PASS",
            "blockers": [],
        }
        count = graded if unique_games is None else unique_games
        self.grades = [
            {
                "official_event_id": f"nfl-game-{i}",
                "model_version": grade_version or "NFL_EVENT_LOGREG_PLATT_V1",
            }
            for i in range(count)
        ]

    def table(self, name):
        if name == "wow_nfl_event_fitted_model_artifacts":
            return _Query([self.model])
        if name == "wow_nfl_forward_calibration_health":
            return _Query([self.health])
        if name == "wow_nfl_forward_shadow_grades":
            return _Query(self.grades, filter_field="model_version")
        raise AssertionError(name)


def test_forward_cohort_and_explicit_champion_authorization_are_both_required():
    from v17.nfl_daily_stage_receipts import _forward_certification_proven

    _, row, _ = _bound_evidence()
    # Honest 53/100 forward cohort cannot become certified by setting a flag.
    assert _forward_certification_proven(_CertifiedDb(True, 53), row) is False
    # Even a fully graded healthy cohort cannot silently promote a champion.
    assert _forward_certification_proven(_CertifiedDb(False, 100), row) is False
    assert _forward_certification_proven(_CertifiedDb(True, 100, "FAIL"), row) is False
    assert _forward_certification_proven(_CertifiedDb(True, 100), row) is True
    # Never borrow a global PASS from another fitted model's settled events.
    assert _forward_certification_proven(
        _CertifiedDb(True, 100, grade_version="NFL_OTHER_MODEL_VERSION"), row
    ) is False
    # A cohort can report 100 yet have too few distinct exact-version grades.
    assert _forward_certification_proven(
        _CertifiedDb(True, 100, unique_games=53), row
    ) is False
