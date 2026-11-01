"""Typed diagnosis of an unproven NFL forward certification.

The gate in v17/nfl_daily_stage_receipts.py used to answer every distinct cause
of a forward-certification HOLD with one bare ``False``. That made genuine
missing forward evidence indistinguishable from a champion-registry or
publication-policy defect, which is exactly how this gate was misreported as a
missing boolean. This module pins the typed diagnosis without changing the
decision: every case below still HOLDs and can_execute stays false.
"""
from pathlib import Path
from types import SimpleNamespace

from v17.nfl_daily_stage_receipts import (
    _forward_certification_diagnosis,
    _forward_certification_proven,
)

ROW = {
    "model_artifact_id": "7fee1209-0bed-4bd0-aa0f-fc7a881eff80",
    "model_version": "NFL_EVENT_LOGREG_PLATT_V1_5d603a873fbb",
}


class _Query:
    def __init__(self, rows, filter_field=None):
        self.rows = list(rows)
        self.filter_field = filter_field

    def select(self, *_a, **_kw):
        return self

    def eq(self, field, value):
        if field == self.filter_field:
            self.rows = [row for row in self.rows if row.get(field) == value]
        return self

    def order(self, *_a, **_kw):
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class _Db:
    def __init__(
        self,
        *,
        approved=False,
        graded=67,
        status="INSUFFICIENT_FORWARD_EVIDENCE",
        recommendation="DO_NOT_CERTIFY_YET",
        health_blockers=("FORWARD_GRADED_N_67_LT_100",),
        unique_games=67,
        minimum=100,
        active=True,
        promoted=True,
    ):
        self.model = {
            "active": active,
            "promoted": promoted,
            "probability_publishable": approved,
        }
        self.health = {
            "graded_n": graded,
            "minimum_forward_required": minimum,
            "status": status,
            "certification_recommendation": recommendation,
            "blockers": list(health_blockers),
        }
        self.grades = [
            {
                "official_event_id": f"2026_WK{i:02d}_AAA_BBB",
                "model_version": ROW["model_version"],
            }
            for i in range(unique_games)
        ]

    def table(self, name):
        return {
            "wow_nfl_event_fitted_model_artifacts": _Query([self.model]),
            "wow_nfl_forward_calibration_health": _Query([self.health]),
            "wow_nfl_forward_shadow_grades": _Query(
                self.grades, filter_field="model_version"
            ),
        }[name]


def test_genuine_short_forward_cohort_is_reported_as_a_count_not_a_flag():
    """The observed production state: 67 graded events of 100 required."""
    proven, blockers, evidence = _forward_certification_diagnosis(_Db(), ROW)

    assert proven is False
    assert "NFL_FORWARD_GRADED_EVENTS_BELOW_MINIMUM" in blockers
    assert "NFL_FORWARD_HEALTH_GRADED_N_BELOW_MINIMUM" in blockers
    assert "NFL_FORWARD_HEALTH_STATUS_NOT_PASS" in blockers
    assert evidence["graded_events"] == 67
    assert evidence["health_graded_n"] == 67
    assert evidence["minimum_forward_required"] == 100
    assert evidence["model_version"] == ROW["model_version"]


def test_schema_locked_artifact_publication_flag_is_reported_as_a_policy_conflict():
    """probability_publishable is CHECK-constrained false on the artifact table.

    The gate still holds; it must say why instead of looking like a missing
    boolean that an operator could "fix" by writing true into the registry.
    """
    proven, blockers, evidence = _forward_certification_diagnosis(
        _Db(approved=False), ROW
    )

    assert proven is False
    assert "NFL_ARTIFACT_PUBLICATION_FLAG_NOT_GRANTED" in blockers
    assert "NFL_REGISTRY_PUBLICATION_AUTHORITY_CONFLICT" in blockers
    assert evidence["artifact_publication_activation"] == "SCHEMA_CONSTRAINED_FALSE"
    assert evidence["diagnostic_classification"] == "NFL_REGISTRY_PUBLICATION_AUTHORITY_CONFLICT"


def test_a_fully_healthy_graded_cohort_still_cannot_promote_itself():
    proven, blockers, evidence = _forward_certification_diagnosis(
        _Db(
            approved=False,
            graded=140,
            status="PASS",
            recommendation="FORWARD_EVIDENCE_PASS",
            health_blockers=(),
            unique_games=140,
        ),
        ROW,
    )

    # Only the unsatisfiable artifact-publication conjunct remains, so the
    # decision stays HOLD and no other blocker is invented.
    assert proven is False
    assert blockers == [
        "NFL_ARTIFACT_PUBLICATION_FLAG_NOT_GRANTED",
        "NFL_REGISTRY_PUBLICATION_AUTHORITY_CONFLICT",
    ]
    assert evidence["graded_events"] == 140


def test_diagnosis_and_boolean_view_never_diverge():
    for db in (
        _Db(),
        _Db(approved=True),
        _Db(approved=True, graded=140, status="PASS",
            recommendation="FORWARD_EVIDENCE_PASS", health_blockers=(),
            unique_games=140),
        _Db(active=False),
        _Db(promoted=False),
        _Db(unique_games=99),
    ):
        proven, blockers, _evidence = _forward_certification_diagnosis(db, ROW)
        assert _forward_certification_proven(db, ROW) is proven
        assert proven is (blockers == [])


def test_grades_recorded_only_under_a_different_model_version_are_never_borrowed():
    """A global health snapshot from another version must not authorize this row."""
    proven, blockers, evidence = _forward_certification_diagnosis(
        _Db(unique_games=0), ROW
    )

    assert proven is False
    assert "NFL_FORWARD_COHORT_MODEL_VERSION_MISMATCH" in blockers
    assert "NFL_FORWARD_GRADED_EVENTS_BELOW_MINIMUM" in blockers
    assert evidence["graded_events"] == 0


def test_minimum_cohort_policy_below_one_hundred_fails_closed():
    proven, blockers, _evidence = _forward_certification_diagnosis(
        _Db(minimum=50), ROW
    )

    assert proven is False
    assert "NFL_FORWARD_MINIMUM_COHORT_INVALID" in blockers


def test_missing_health_row_is_a_registry_shape_blocker_not_model_absence():
    class _Empty(_Db):
        def table(self, name):
            if name == "wow_nfl_forward_calibration_health":
                return _Query([])
            return super().table(name)

    proven, blockers, _evidence = _forward_certification_diagnosis(_Empty(), ROW)
    assert proven is False
    assert blockers == ["NFL_FORWARD_CERTIFICATION_REGISTRY_SHAPE_INVALID"]


def test_stage_receipt_surfaces_forward_evidence_and_keeps_execution_false():
    source = (
        Path(__file__).parents[1] / "v17" / "nfl_daily_stage_receipts.py"
    ).read_text()

    assert '"forward_certification_blockers": list(forward_blockers)' in source
    assert '"forward_certification_evidence": dict(forward_evidence)' in source
    assert "*forward_blockers," in source
    # No publication or execution path may be introduced by the diagnosis.
    assert "can_execute = True" not in source
    assert "can_execute=True" not in source
