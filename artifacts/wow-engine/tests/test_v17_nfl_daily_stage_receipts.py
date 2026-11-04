"""NFL fitted-stage readback: positive proof and fail-closed counterexamples."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

from v17.nfl_daily_stage_receipts import (
    _forward_certification_proven,
    attach_nfl_daily_stage_receipt,
)
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


# --- Forward-certification gate: registry state + prospective cohort ----------
#
# `wow_nfl_event_fitted_model_artifacts` carries
#   constraint wow_nfl_event_fitted_registry_not_probability_publication
#     check (probability_publishable = false)
# so the registry can never grant publication. The gate must read the governed
# champion state and leave publication authority with the row-level
# model_probability_publishable flag.

FORWARD_VERSION = "NFL_EVENT_LOGREG_PLATT_V1_5d603a873fbb"


class _ForwardTable:
    def __init__(self, db, name):
        self.db = db
        self.name = name
        self.rows = None
        self.total = None

    def select(self, *_a, **_kw):
        return self

    def eq(self, field, value):
        if self.name == "wow_nfl_event_fitted_model_artifacts":
            self.rows = [r for r in self.db.artifacts if str(r.get("artifact_id")) == str(value)]
        elif self.name == "wow_nfl_forward_shadow_grades":
            self.rows = [r for r in self.db.grades if str(r.get("model_version")) == str(value)]
            self.total = len(self.rows)
        else:
            raise AssertionError(f"unexpected eq on {self.name}")
        return self

    def order(self, field, desc=False):
        if self.name == "wow_nfl_forward_shadow_grades":
            assert field == "official_event_id"
            self.rows = sorted(self.rows, key=lambda r: r["official_event_id"], reverse=desc)
        else:
            self.rows = list(self.db.health)
        return self

    def range(self, start, end):
        assert self.name == "wow_nfl_forward_shadow_grades"
        self.db.pages.append((start, end))
        selection = self.rows[start:end + 1]
        if self.db.response_cap is not None:
            selection = selection[:self.db.response_cap]
        self.rows = selection
        return self

    def limit(self, value):
        source = self.db.health if self.rows is None else self.rows
        self.rows = list(source)[:value]
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows or [], count=self.total)


class _FailingTable:
    def select(self, *_a, **_kw):
        return self

    def eq(self, *_a):
        return self

    def order(self, *_a, **_k):
        return self

    def range(self, *_a):
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        raise RuntimeError("database offline; not a pass")


class _ForwardDb:
    def __init__(self, *, artifacts, health, grades, fails=False, response_cap=None):
        self.artifacts = artifacts
        self.health = health
        self.grades = grades
        self.fails = fails
        self.response_cap = response_cap
        self.pages = []

    def table(self, name):
        return _FailingTable() if self.fails else _ForwardTable(self, name)


def _champion_artifact(**extra):
    artifact = {
        "artifact_id": MODEL,
        "active": True,
        "promoted": True,
        "lifecycle_state": "CHAMPION",
        "probability_publishable": False,
    }
    artifact.update(extra)
    return artifact


def _healthy_assessment(**extra):
    assessment = {
        "graded_n": 100,
        "minimum_forward_required": 100,
        "status": "PASS",
        "certification_recommendation": "FORWARD_EVIDENCE_PASS",
        "blockers": [],
    }
    assessment.update(extra)
    return assessment


def _grades(n, version=FORWARD_VERSION):
    # 70/100 realized wins for an immutable 70% model: actual forward
    # Brier/log loss/ECE/bias and lower-bound reliability all pass.
    return [
        {
            "official_event_id": f"2026_{i:02d}_SF_ATL",
            "model_version": version,
            "event_prediction_id": f"forward-prediction-{i}",
            "event_start_time_utc": "2026-10-25T17:00:00Z",
            "prediction_created_at": "2026-10-24T13:00:00Z",
            "selected_participant": "San Francisco 49ers",
            "calibrated_probability": 0.7,
            "calibrated_lower_bound": 0.65,
            "calibrated_upper_bound": 0.75,
            "outcome": int(i % 10 < 7),
            "source_game_id": f"forward-source-game-{i}",
        }
        for i in range(n)
    ]


def _forward_row(version=FORWARD_VERSION):
    return {"model_artifact_id": MODEL, "model_version": version}


def test_champion_registry_state_plus_minimum_cohort_proves_forward_certification():
    db = _ForwardDb(
        artifacts=[_champion_artifact()],
        health=[_healthy_assessment()],
        grades=_grades(100),
    )
    assert _forward_certification_proven(db, _forward_row()) is True


def test_paginated_champion_cohort_uses_complete_ordered_400_game_set():
    db = _ForwardDb(
        artifacts=[_champion_artifact()],
        health=[_healthy_assessment(graded_n=400)],
        grades=_grades(400),
    )
    assert _forward_certification_proven(db, _forward_row()) is True
    assert db.pages == [(0, 249), (250, 499)]


def test_server_response_cap_cannot_certify_truncated_champion_grades():
    # Simulate a PostgREST service that silently caps every 250-row request
    # to 100 rows despite reporting the exact 400-row model-version count.
    db = _ForwardDb(
        artifacts=[_champion_artifact()],
        health=[_healthy_assessment(graded_n=400)],
        grades=_grades(400),
        response_cap=100,
    )
    assert _forward_certification_proven(db, _forward_row()) is False
    assert db.pages == [(0, 249)]


def test_registry_publication_flag_is_never_the_forward_gate():
    # The column is schema-pinned to false in production; requiring true made the
    # gate unsatisfiable, while trusting true here would bypass the row-level
    # publication flag the package readback already enforces.
    for flag in (False, True):
        db = _ForwardDb(
            artifacts=[_champion_artifact(probability_publishable=flag)],
            health=[_healthy_assessment()],
            grades=_grades(100),
        )
        assert _forward_certification_proven(db, _forward_row()) is True


def test_non_champion_or_unpromoted_artifact_never_certifies():
    for override in (
        {"lifecycle_state": "CANDIDATE"},
        {"lifecycle_state": "PROSPECTIVE_CERTIFIED"},
        {"lifecycle_state": "RETIRED"},
        {"promoted": False},
        {"active": False},
    ):
        db = _ForwardDb(
            artifacts=[_champion_artifact(**override)],
            health=[_healthy_assessment()],
            grades=_grades(100),
        )
        assert _forward_certification_proven(db, _forward_row()) is False, override


def test_cohort_must_be_unique_and_bound_to_this_exact_model_version():
    cases = (
        # one game short of the required cohort
        _grades(99),
        # duplicates of the same game never add a second grade
        _grades(99) + [{"official_event_id": "2026_00_SF_ATL", "model_version": FORWARD_VERSION}],
        # a different champion's grades cannot authorize this model
        _grades(100, version="NFL_EVENT_LOGREG_PLATT_V1_other"),
    )
    for grades in cases:
        db = _ForwardDb(
            artifacts=[_champion_artifact()],
            health=[_healthy_assessment()],
            grades=grades,
        )
        assert _forward_certification_proven(db, _forward_row()) is False


def test_mixed_model_global_pass_cannot_hide_champion_own_bad_calibration():
    bad_champion = _grades(100)
    for grade in bad_champion:
        grade["calibrated_probability"] = 0.95
        grade["calibrated_lower_bound"] = 0.9
        grade["calibrated_upper_bound"] = 0.98
        grade["outcome"] = 0
    db = _ForwardDb(
        artifacts=[_champion_artifact()],
        # A's old grades can still give the shared health table a global PASS.
        health=[_healthy_assessment(graded_n=200)],
        grades=_grades(100, version="NFL_PRIOR_CHAMPION_A") + bad_champion,
    )
    assert _forward_certification_proven(db, _forward_row()) is False


def test_version_grades_require_complete_valid_unique_immutable_evidence():
    mutations = (
        lambda rows: rows[0].update(outcome=True),
        lambda rows: rows[0].update(outcome=None),
        lambda rows: rows[0].update(calibrated_probability=float("nan")),
        lambda rows: rows[0].update(prediction_created_at="2026-10-25T18:00:00Z"),
        lambda rows: rows[0].update(model_version="UNRELATED_MODEL"),
        lambda rows: rows[0].update(source_game_id=""),
    )
    for change in mutations:
        rows = _grades(100)
        change(rows)
        db = _ForwardDb(
            artifacts=[_champion_artifact()],
            health=[_healthy_assessment()],
            grades=rows,
        )
        assert _forward_certification_proven(db, _forward_row()) is False


def test_health_snapshot_must_recommend_forward_pass_with_no_blockers():
    for override in (
        {"status": "INSUFFICIENT_FORWARD_EVIDENCE"},
        {"certification_recommendation": "DO_NOT_CERTIFY_YET"},
        {"blockers": ["FORWARD_GRADED_N_80_LT_100"]},
        {"graded_n": 80},
        {"minimum_forward_required": 80},
        {"graded_n": "100"},
        {"minimum_forward_required": "100"},
    ):
        db = _ForwardDb(
            artifacts=[_champion_artifact()],
            health=[_healthy_assessment(**override)],
            grades=_grades(100),
        )
        assert _forward_certification_proven(db, _forward_row()) is False, override


def test_absent_duplicate_or_unreadable_forward_readback_never_certifies():
    for db in (
        _ForwardDb(artifacts=[], health=[_healthy_assessment()], grades=_grades(100)),
        _ForwardDb(
            artifacts=[_champion_artifact(), _champion_artifact()],
            health=[_healthy_assessment()],
            grades=_grades(100),
        ),
        _ForwardDb(artifacts=[_champion_artifact()], health=[], grades=_grades(100)),
        _ForwardDb(
            artifacts=[_champion_artifact()],
            health=[_healthy_assessment()],
            grades=_grades(100),
            fails=True,
        ),
    ):
        assert _forward_certification_proven(db, _forward_row()) is False
