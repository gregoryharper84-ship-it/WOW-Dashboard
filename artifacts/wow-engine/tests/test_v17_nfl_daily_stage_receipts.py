"""NFL fitted-stage readback: positive proof and fail-closed counterexamples."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

from v17.nfl_daily_stage_receipts import attach_nfl_daily_stage_receipt
from v17.llp_governed_package_scoring import validate_governed_scoring_package
from v17.full_board_stabilization import publication_chain_audit


PRED = "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"
SCORE = "bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb"
SOURCE = "cccccccc-cccc-4ccc-cccc-cccccccccccc"
MODEL = "dddddddd-dddd-4ddd-dddd-dddddddddddd"


def _fixture():
    req = SimpleNamespace(
        official_event_id="2026_07_SF_ATL",
        event_key="NFL:2026_07_SF_ATL",
        home_team="Atlanta Falcons",
        away_team="San Francisco 49ers",
        requested_slate_date="2026-10-25",
        source_snapshot_id=SOURCE,
        latest_material_update_timestamp="2026-10-24T16:00:00+00:00",
        event_start_time_utc="2026-10-25T17:00:00+00:00",
    )
    row = {
        "event_prediction_id": PRED,
        "score_snapshot_id": SCORE,
        "official_event_id": req.official_event_id,
        "event_key": req.event_key,
        "requested_slate_date": req.requested_slate_date,
        "event_start_time_utc": req.event_start_time_utc,
        "selected_participant": "San Francisco 49ers",
        "source_snapshot_id": SOURCE,
        "source_snapshot_timestamp": req.latest_material_update_timestamp,
        "latest_material_update_timestamp": req.latest_material_update_timestamp,
        "model_timestamp": "2026-10-25T13:00:00+00:00",
        "model_artifact_id": MODEL,
        "model_version": "NFL_EVENT_LOGREG_PLATT_V1",
        "calibration_method": "PLATT_TIME_SPLIT_V1",
        "calibration_version": "NFL_PLATT_TIME_SPLIT_V1",
        "calibration_health_status": "PASS",
        "calibration_training_n": 285,
        "uncertainty_method": "CALIBRATION_HOLDOUT_ERROR_MARGIN_V1",
        "calibrated_selection_probability": 0.8052,
        "calibrated_selection_lower_bound": 0.7493,
        "calibrated_selection_upper_bound": 0.8611,
        "model_package_valid": True,
        "model_probability_publishable": True,
        "provenance_complete": True,
        "can_execute": False,
    }
    governed = {
        "event_prediction_id": PRED,
        "score_snapshot_id": SCORE,
        "selected_participant": row["selected_participant"],
        "model_artifact_id": MODEL,
        "model_version": row["model_version"],
        "model_timestamp": row["model_timestamp"],
        "calibration_method": row["calibration_method"],
        "calibration_version": row["calibration_version"],
        "calibrated_selection_probability": 0.8052,
        "rank_calibrated_lower_bound": 0.7493,
        "calibrated_away_upper_bound": 0.8611,
        "llp_probability_audit_result": "PASS_PROBABILITY_AUDIT",
        "event_mutex_status": "PASS",
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "terminal_label": "FINAL_APPROVED",
        "rank_eligible": True,
        "probability_publishable": True,
        "can_execute": False,
        "llp_governance": {
            "final_refresh": {
                "final_refresh_status": "PASS",
                "reasons": [],
                "probability_invalidated": False,
                "rerun_required": False,
                "can_execute": False,
            },
        },
    }
    return req, row, governed


class FakeTable:
    def __init__(self, rows, fails=False):
        self.rows = rows
        self.fails = fails

    def select(self, *_a):
        return self

    def eq(self, field, val):
        assert field == "event_prediction_id"
        assert val == PRED
        return self

    def limit(self, value):
        assert value == 2
        return self

    def execute(self):
        if self.fails:
            raise RuntimeError("database offline; not a pass")
        return SimpleNamespace(data=self.rows)


class FakeDb:
    def __init__(self, rows, fails=False):
        self.obj = FakeTable(rows, fails)

    def table(self, name):
        assert name == "wow_nfl_event_predictions"
        return self.obj


def _prove(req, row, governed, *, fails=False, rows=None):
    return attach_nfl_daily_stage_receipt(
        db=FakeDb([row] if rows is None else rows, fails=fails),
        req=req,
        governed=governed,
    )


def test_exact_fitted_prediction_proves_only_numeric_package_not_dynamic_stage():
    req, row, governed = _fixture()
    result = _prove(req, row, governed)
    assert result["nfl_stage_receipt"]["status"] == "HOLD"
    assert result["nfl_stage_receipt"]["package_readback_status"] == "PASS"
    assert result["nfl_stage_receipt"]["dynamic_calibration_stage"] == "EVIDENCE_NOT_EMITTED"
    assert result["prediction_id"] == PRED
    assert result["probability_package_valid"] is True
    assert result["dynamic_calibration_complete"] is False
    assert result["calibration_status"] == "UNPROVEN"
    assert result["rank_eligible"] is False
    assert result["probability_publishable"] is False
    assert result["terminal_label"] == "MODEL_QUALIFIED_HOLD"
    assert result["upstream_terminal_label"] == "FINAL_APPROVED"
    assert result["rank_eligible_pre_audit"] is True
    assert result["calibrated_lower_bound"] == 0.7493
    assert result["can_execute"] is False
    assert validate_governed_scoring_package(result).status == "PASS"
    assert publication_chain_audit(result).status == "BLOCKED"


def test_no_readback_never_borrows_stage_pass_or_changes_probability():
    req, row, governed = _fixture()
    for rows, fails in (([], False), (None, True), ([row, row], False)):
        result = _prove(req, row, governed, rows=rows, fails=fails)
        assert result["nfl_stage_receipt"]["status"] == "HOLD"
        assert result["rank_eligible"] is False
        assert result["probability_publishable"] is False
        assert result["terminal_label"] == "MODEL_QUALIFIED_HOLD"
        assert result.get("probability_package_valid") is not True
        assert result.get("dynamic_calibration_complete") is not True
        assert result["calibrated_selection_probability"] == 0.8052
        assert result["can_execute"] is False


def test_wrong_event_or_snapshot_or_model_or_side_cannot_prove_stage():
    req, row, governed = _fixture()
    for field, invalid in (
        ("official_event_id", "2026_07_GB_DET"),
        ("score_snapshot_id", "from-another-run"),
        ("selected_participant", "Atlanta Falcons"),
        ("model_artifact_id", "from-another-specialist"),
        ("source_snapshot_id", "prior-game"),
        ("model_timestamp", "2026-10-25T12:00:00+00:00"),
    ):
        edited = deepcopy(row)
        edited[field] = invalid
        assert _prove(req, edited, governed)["nfl_stage_receipt"]["status"] == "HOLD"


def test_uncalibrated_and_stale_results_never_prove_dynamic_calibration():
    req, row, governed = _fixture()
    for field, invalid in (
        ("model_package_valid", False),
        ("can_execute", True),
        ("calibration_health_status", "HOLD"),
        ("calibration_training_n", 0),
        ("calibrated_selection_lower_bound", 0.92),
        ("calibrated_selection_probability", 0.91),
        ("model_timestamp", "2026-10-23T13:00:00+00:00"),
    ):
        edited = deepcopy(row)
        edited[field] = invalid
        result = _prove(req, edited, governed)
        assert result["nfl_stage_receipt"]["status"] == "HOLD"
        assert result.get("dynamic_calibration_complete") is not True
        assert result.get("probability_package_valid") is not True


def test_upstream_hold_is_never_upgraded_by_valid_model_stage_proof():
    req, row, governed = _fixture()
    governed["rank_eligible"] = False
    governed["probability_publishable"] = False
    governed["terminal_label"] = "MODEL_QUALIFIED_HOLD"
    result = _prove(req, row, governed)
    assert result["nfl_stage_receipt"]["status"] == "HOLD"
    assert result["nfl_stage_receipt"]["package_readback_status"] == "PASS"
    assert result["rank_eligible"] is False
    assert result["probability_publishable"] is False
    assert result["terminal_label"] == "MODEL_QUALIFIED_HOLD"
    assert publication_chain_audit(result).status == "BLOCKED"


def test_health_pass_or_borrowed_upstream_stage_boolean_does_not_certify_calibration():
    req, row, governed = _fixture()
    assert row["calibration_health_status"] == "PASS"
    assert row["model_probability_publishable"] is True
    governed["dynamic_calibration_complete"] = True  # potentially stale prior output
    governed["calibration_status"] = "PASS"
    result = _prove(req, row, governed)
    assert result["probability_package_valid"] is True
    assert result["dynamic_calibration_complete"] is False
    assert result["calibration_status"] == "UNPROVEN"
    assert result["nfl_stage_receipt"]["status"] == "HOLD"
    assert "NFL_DYNAMIC_CALIBRATION_PRODUCER_RECEIPT_MISSING" in result["nfl_stage_receipt"]["blockers"]
    assert publication_chain_audit(result).status == "BLOCKED"
    assert result["can_execute"] is False


def test_absent_prediction_does_not_reuse_upstream_calibration_pass():
    req, row, governed = _fixture()
    governed["dynamic_calibration_complete"] = True
    governed["calibration_status"] = "PASS"
    result = _prove(req, row, governed, rows=[])
    assert result.get("probability_package_valid") is not True
    assert result["dynamic_calibration_complete"] is False
    assert result["calibration_status"] == "UNPROVEN"
    assert result["nfl_stage_receipt"]["status"] == "HOLD"
    assert result["can_execute"] is False
