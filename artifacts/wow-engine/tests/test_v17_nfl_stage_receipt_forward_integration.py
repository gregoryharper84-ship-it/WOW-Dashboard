"""Invocation-based regression tests for the NFL stage receipt.

The previous coverage asserted that certain strings appeared in the source file.
That proves nothing about the object the engine actually returns. These tests
call ``attach_nfl_daily_stage_receipt`` with a coherent persisted prediction,
producer receipt and final refresh, and inspect the returned receipt.
"""
from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from types import SimpleNamespace

from v17.nfl_daily_stage_receipts import attach_nfl_daily_stage_receipt

PRED = "11111111-1111-4111-8111-111111111111"
SCORE = "22222222-2222-4222-8222-222222222222"
SOURCE = "33333333-3333-4333-8333-333333333333"
REFRESH = "44444444-4444-4444-8444-444444444444"
MODEL = "7fee1209-0bed-4bd0-aa0f-fc7a881eff80"
VERSION = "NFL_EVENT_LOGREG_PLATT_V1_5d603a873fbb"
EVENT = "2026_07_SF_ATL"
EVENT_KEY = "NFL:2026_07_SF_ATL"
SLATE = "2026-10-25"
RUN_ID = "55555555-5555-4555-8555-555555555555"
IDENTITY = "nfl:2026_07_SF_ATL:SF"
FEATURE_HASH = "a" * 64
CHECKSUM = "b" * 64
MATERIAL_TS = "2026-10-24T16:00:00+00:00"
MODEL_TS = "2026-10-25T13:00:00+00:00"
CHECKED_TS = "2026-10-25T14:00:00+00:00"
KICKOFF = "2026-10-25T17:00:00+00:00"
HOME_TEAM = "Atlanta Falcons"
AWAY_TEAM = "San Francisco 49ers"

LOGIT = -1.5
PLATT_A = 1.1
PLATT_B = -0.05
MARGIN = 0.0565


def _sigmoid(x):
    return 1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, x))))


RAW_HOME = _sigmoid(LOGIT)
HOME = _sigmoid(PLATT_A * LOGIT + PLATT_B)
HOME_LOWER = max(0.001, HOME - MARGIN)
HOME_UPPER = min(0.999, HOME + MARGIN)
AWAY = 1.0 - HOME
AWAY_LOWER = max(0.001, 1.0 - HOME_UPPER)
AWAY_UPPER = min(0.999, 1.0 - HOME_LOWER)


def _producer_receipt():
    receipt = {
        "schema_version": "NFL_CALIBRATION_OPERATION_V1",
        "producer": "nfl_event_model_v17.score_feature_row",
        "status": "COMPLETED",
        "can_execute": False,
        "research_run_id": RUN_ID,
        "prediction_identity_key": IDENTITY,
        "official_event_id": EVENT,
        "event_key": EVENT_KEY,
        "requested_slate_date": SLATE,
        "model_artifact_id": MODEL,
        "model_version": VERSION,
        "calibration_method": "PLATT_TIME_SPLIT_V1",
        "calibration_version": "NFL_PLATT_TIME_SPLIT_V1",
        "uncertainty_method": "CALIBRATION_HOLDOUT_ERROR_MARGIN_V1",
        "feature_row_hash": FEATURE_HASH,
        "source_snapshot_id": SOURCE,
        "artifact_checksum": CHECKSUM,
        "model_timestamp": MODEL_TS,
        "source_snapshot_timestamp": MATERIAL_TS,
        "event_start_time_utc": KICKOFF,
        "calibration_training_n": 285,
        "platt_input_logit": LOGIT,
        "platt_a": PLATT_A,
        "platt_b": PLATT_B,
        "uncertainty_margin": MARGIN,
        "raw_home_probability": RAW_HOME,
        "calibrated_home_probability": HOME,
        "calibrated_away_probability": AWAY,
        "calibrated_home_lower_bound": HOME_LOWER,
        "calibrated_home_upper_bound": HOME_UPPER,
        "calibrated_away_lower_bound": AWAY_LOWER,
        "calibrated_away_upper_bound": AWAY_UPPER,
    }
    digest = hashlib.sha256(
        json.dumps(
            receipt, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    ).hexdigest()
    receipt["evidence_sha256"] = digest
    return receipt


def _fixture():
    req = SimpleNamespace(
        official_event_id=EVENT,
        event_key=EVENT_KEY,
        home_team=HOME_TEAM,
        away_team=AWAY_TEAM,
        requested_slate_date=SLATE,
        source_snapshot_id=SOURCE,
        latest_material_update_timestamp=MATERIAL_TS,
        event_start_time_utc=KICKOFF,
    )
    row = {
        "event_prediction_id": PRED,
        "score_snapshot_id": SCORE,
        "official_event_id": EVENT,
        "event_key": EVENT_KEY,
        "requested_slate_date": SLATE,
        "event_start_time_utc": KICKOFF,
        "selected_participant": AWAY_TEAM,
        "source_snapshot_id": SOURCE,
        "source_snapshot_timestamp": MATERIAL_TS,
        "latest_material_update_timestamp": MATERIAL_TS,
        "model_timestamp": MODEL_TS,
        "model_artifact_id": MODEL,
        "model_version": VERSION,
        "calibration_method": "PLATT_TIME_SPLIT_V1",
        "calibration_version": "NFL_PLATT_TIME_SPLIT_V1",
        "calibration_health_status": "PASS",
        "calibration_training_n": 285,
        "uncertainty_method": "CALIBRATION_HOLDOUT_ERROR_MARGIN_V1",
        "research_run_id": RUN_ID,
        "prediction_identity_key": IDENTITY,
        "feature_row_hash": FEATURE_HASH,
        "calibrated_selection_probability": AWAY,
        "calibrated_selection_lower_bound": AWAY_LOWER,
        "calibrated_selection_upper_bound": AWAY_UPPER,
        "raw_home_probability": RAW_HOME,
        "calibrated_home_probability": HOME,
        "calibrated_away_probability": AWAY,
        "calibrated_home_lower_bound": HOME_LOWER,
        "calibrated_home_upper_bound": HOME_UPPER,
        "calibrated_away_lower_bound": AWAY_LOWER,
        "calibrated_away_upper_bound": AWAY_UPPER,
        "model_package_valid": True,
        "model_probability_publishable": True,
        "provenance_complete": True,
        "can_execute": False,
        "model_output_snapshot": {
            "artifact_checksum": CHECKSUM,
            "calibration_operation_receipt": _producer_receipt(),
        },
    }
    governed = {
        "event_prediction_id": PRED,
        "score_snapshot_id": SCORE,
        "selected_participant": AWAY_TEAM,
        "model_artifact_id": MODEL,
        "model_version": VERSION,
        "model_timestamp": MODEL_TS,
        "calibration_method": "PLATT_TIME_SPLIT_V1",
        "calibration_version": "NFL_PLATT_TIME_SPLIT_V1",
        "calibrated_selection_probability": AWAY,
        "rank_calibrated_lower_bound": AWAY_LOWER,
        "calibrated_away_upper_bound": AWAY_UPPER,
        "llp_probability_audit_result": "PASS_PROBABILITY_AUDIT",
        "event_mutex_status": "PASS",
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "terminal_label": "FINAL_APPROVED",
        "rank_eligible": True,
        "probability_publishable": True,
        "can_execute": False,
        "llp_governance": {
            "final_refresh": {
                "source": "NFL_CANONICAL_SCHEDULE_REQUERY",
                "final_refresh_status": "PASS",
                "reasons": [],
                "probability_invalidated": False,
                "rerun_required": False,
                "can_execute": False,
                "refresh_source_snapshot_id": REFRESH,
                "scored_source_snapshot_id": SOURCE,
                "model_timestamp": MODEL_TS,
                "checked_at": CHECKED_TS,
            },
        },
    }
    return req, row, governed


class _Query:
    def __init__(self, rows, filter_field=None, fail_range=False):
        self.rows = list(rows)
        self.filter_field = filter_field
        self.fail_range = fail_range

    def select(self, *_a, **_kw):
        return self

    def eq(self, field, value):
        if self.filter_field is None or field == self.filter_field:
            self.rows = [r for r in self.rows if r.get(field) == value]
        return self

    def neq(self, field, value):
        if self.filter_field == field:
            self.rows = [r for r in self.rows if r.get(field) != value]
        return self

    def order(self, *_a, **_kw):
        return self

    def limit(self, value):
        self.rows = self.rows[:value]
        return self

    def range(self, start, end):
        if self.fail_range:
            raise RuntimeError("grade pagination failed")
        self.rows = self.rows[start:end + 1]
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class _Db:
    def __init__(self, row, *, graded_events=67, health=None, fail_grades=False):
        self.row = row
        self.graded_events = graded_events
        self.health = health or {
            "graded_n": graded_events,
            "minimum_forward_required": 100,
            "status": "INSUFFICIENT_FORWARD_EVIDENCE",
            "certification_recommendation": "DO_NOT_CERTIFY_YET",
            "blockers": ["FORWARD_GRADED_N_67_LT_100"] if graded_events < 100 else [],
        }
        self.model = {
            "artifact_id": MODEL,
            "active": True,
            "promoted": True,
            "probability_publishable": False,
        }
        self.grades = [
            {"official_event_id": f"2026_WK{i:02d}_AAA_BBB", "model_version": VERSION}
            for i in range(graded_events)
        ]
        self.fail_grades = fail_grades

    def table(self, name):
        if name == "wow_nfl_event_predictions":
            return _Query([self.row] if self.row is not None else [])
        if name == "wow_nfl_event_fitted_model_artifacts":
            return _Query([self.model])
        if name == "wow_nfl_forward_calibration_health":
            return _Query([self.health])
        if name == "wow_nfl_forward_shadow_grades":
            return _Query(
                self.grades, filter_field="model_version", fail_range=self.fail_grades
            )
        raise AssertionError(name)


def _run(row=None, **kwargs):
    req, base_row, governed = _fixture()
    return attach_nfl_daily_stage_receipt(
        db=_Db(base_row if row is None else row, **kwargs),
        req=req,
        governed=governed,
    )


def test_case1_valid_upstream_stages_with_insufficient_forward_evidence():
    """Upstream proof is valid, so forward certification is really evaluated."""
    result = _run()
    receipt = result["nfl_stage_receipt"]

    assert receipt["status"] == "PASS"
    assert receipt["package_readback_status"] == "PASS"
    assert receipt["final_refresh_stage"] == "PASS"
    assert receipt["forward_certification_status"] == "HOLD"

    blockers = receipt["forward_certification_blockers"]
    assert "NFL_FORWARD_GRADED_EVENTS_BELOW_MINIMUM" in blockers
    assert "NFL_REGISTRY_PUBLICATION_AUTHORITY_CONFLICT" in blockers
    assert "NFL_FORWARD_CERTIFICATION_QUERY_FAILED" not in blockers

    evidence = receipt["forward_certification_evidence"]
    assert evidence["graded_events"] == 67
    assert evidence["minimum_forward_required"] == 100
    assert evidence["model_version"] == VERSION

    assert "NFL_FORWARD_CERTIFICATION_UNPROVEN" in result["blockers"]
    assert result["terminal_label"] == "MODEL_QUALIFIED_HOLD"
    assert result["terminal_ceiling"] == "MODEL_QUALIFIED_HOLD"
    assert result["rank_eligible"] is False
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False
    assert receipt["can_execute"] is False


def test_case2_missing_producer_proof_never_manufactures_forward_failure():
    req, row, governed = _fixture()
    row = deepcopy(row)
    row["model_output_snapshot"] = None

    result = _run(row=row)
    receipt = result["nfl_stage_receipt"]

    assert receipt["forward_certification_status"] == "NOT_CHECKED"
    assert receipt["forward_certification_blockers"] == []
    assert receipt["forward_certification_evidence"] == {}
    assert "NFL_DYNAMIC_CALIBRATION_PRODUCER_RECEIPT_MISSING" in receipt["blockers"]
    assert "NFL_STAGE_PROOF_UNPROVEN" in result["blockers"]
    assert "NFL_FORWARD_CERTIFICATION_UNPROVEN" not in result.get("blockers", [])
    assert result["terminal_label"] == "MODEL_QUALIFIED_HOLD"
    assert result["rank_eligible"] is False
    assert result["can_execute"] is False


def test_case3_healthy_forward_evidence_cannot_bypass_publication_authority():
    result = _run(
        graded_events=140,
        health={
            "graded_n": 140,
            "minimum_forward_required": 100,
            "status": "PASS",
            "certification_recommendation": "FORWARD_EVIDENCE_PASS",
            "blockers": [],
        },
    )
    receipt = result["nfl_stage_receipt"]

    assert receipt["forward_certification_status"] == "HOLD"
    assert receipt["forward_certification_blockers"] == [
        "NFL_ARTIFACT_PUBLICATION_FLAG_NOT_GRANTED",
        "NFL_REGISTRY_PUBLICATION_AUTHORITY_CONFLICT",
    ]
    assert result["terminal_label"] == "MODEL_QUALIFIED_HOLD"
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["can_execute"] is False


def test_case4_failed_readback_stays_typed_and_held():
    result = _run(fail_grades=True)
    receipt = result["nfl_stage_receipt"]

    assert receipt["status"] == "PASS"
    assert receipt["forward_certification_status"] == "HOLD"
    assert "NFL_FORWARD_GRADE_COHORT_QUERY_FAILED" in (
        receipt["forward_certification_blockers"]
    )
    assert "NFL_FORWARD_CERTIFICATION_UNPROVEN" in result["blockers"]
    assert result["terminal_label"] == "MODEL_QUALIFIED_HOLD"
    assert result["can_execute"] is False


def test_case4b_absent_prediction_never_emits_a_forward_verdict():
    result = _run(row=None)
    # row=None means the exact persisted prediction is unavailable
    req, _row, governed = _fixture()
    result = attach_nfl_daily_stage_receipt(
        db=_Db(None), req=req, governed=governed
    )
    receipt = result["nfl_stage_receipt"]

    assert receipt["status"] == "HOLD"
    assert "forward_certification_status" not in receipt
    assert result["terminal_label"] == "MODEL_QUALIFIED_HOLD"
    assert result["can_execute"] is False
