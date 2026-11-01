"""Typed diagnosis of an unproven NFL forward certification.

The gate in v17/nfl_daily_stage_receipts.py answered every distinct cause of a
forward-certification HOLD with one bare ``False``. That made genuine missing
forward evidence indistinguishable from a champion-registry or publication
policy defect. These tests pin the typed diagnosis, the truthful cohort count
and the unchanged fail-closed decision.
"""
from types import SimpleNamespace

import pytest

from v17.nfl_daily_stage_receipts import (
    _forward_certification_diagnosis,
    _forward_certification_proven,
)

ROW = {
    "model_artifact_id": "7fee1209-0bed-4bd0-aa0f-fc7a881eff80",
    "model_version": "NFL_EVENT_LOGREG_PLATT_V1_5d603a873fbb",
}


class _Query:
    def __init__(self, rows, filter_field=None, fail_range=False):
        self.rows = list(rows)
        self.filter_field = filter_field
        self.fail_range = fail_range

    def select(self, *_a, **_kw):
        return self

    def eq(self, field, value):
        if field == self.filter_field:
            self.rows = [r for r in self.rows if r.get(field) == value]
        return self

    def neq(self, field, value):
        if field == self.filter_field:
            self.rows = [r for r in self.rows if r.get(field) != value]
        return self

    def order(self, *_a, **_kw):
        return self

    def limit(self, value):
        self.rows = self.rows[:value]
        return self

    def range(self, start, end):
        if self.fail_range:
            raise RuntimeError("pagination read failed")
        self.rows = self.rows[start:end + 1]
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class _Db:
    """Coherent fake of the three tables the diagnosis reads."""

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
        other_versions=(),
        duplicates=0,
        fail_range=False,
        health_rows=None,
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
        self.health_rows = [self.health] if health_rows is None else health_rows
        self.grades = [
            {
                "official_event_id": f"2026_WK{i:02d}_AAA_BBB",
                "model_version": ROW["model_version"],
            }
            for i in range(unique_games)
        ]
        # Repeated grade rows for one event must never inflate the cohort.
        self.grades += [
            {
                "official_event_id": "2026_WK00_AAA_BBB",
                "model_version": ROW["model_version"],
            }
            for _ in range(duplicates)
        ]
        self.grades += [
            {"official_event_id": f"other-{v}-{i}", "model_version": v}
            for v in other_versions
            for i in range(3)
        ]
        self.fail_range = fail_range

    def table(self, name):
        return {
            "wow_nfl_event_fitted_model_artifacts": _Query([self.model]),
            "wow_nfl_forward_calibration_health": _Query(self.health_rows),
            "wow_nfl_forward_shadow_grades": _Query(
                self.grades, filter_field="model_version", fail_range=self.fail_range
            ),
        }[name]


FULL_HEALTH = dict(
    graded=140,
    status="PASS",
    recommendation="FORWARD_EVIDENCE_PASS",
    health_blockers=(),
    unique_games=140,
)


def test_observed_production_state_reports_the_short_cohort_by_count_not_flag():
    proven, blockers, evidence = _forward_certification_diagnosis(_Db(), ROW)

    assert proven is False
    assert "NFL_FORWARD_GRADED_EVENTS_BELOW_MINIMUM" in blockers
    assert "NFL_FORWARD_HEALTH_GRADED_N_BELOW_MINIMUM" in blockers
    assert "NFL_FORWARD_HEALTH_STATUS_NOT_PASS" in blockers
    assert "NFL_FORWARD_HEALTH_BLOCKERS_PRESENT" in blockers
    # A short cohort is insufficient evidence, not an established mismatch.
    assert "NFL_FORWARD_COHORT_MODEL_VERSION_MISMATCH" not in blockers
    assert evidence["graded_events"] == 67
    assert evidence["graded_events_exact"] is True
    assert evidence["health_graded_n"] == 67
    assert evidence["minimum_forward_required"] == 100


def test_registry_publication_authority_conflict_is_reported_as_a_policy_conflict():
    proven, blockers, evidence = _forward_certification_diagnosis(_Db(), ROW)

    assert proven is False
    assert "NFL_ARTIFACT_PUBLICATION_FLAG_NOT_GRANTED" in blockers
    assert "NFL_REGISTRY_PUBLICATION_AUTHORITY_CONFLICT" in blockers
    assert evidence["artifact_publication_activation"] == "SCHEMA_CONSTRAINED_FALSE"
    assert evidence["diagnostic_classification"] == "NFL_REGISTRY_PUBLICATION_AUTHORITY_CONFLICT"


def test_a_fully_healthy_cohort_still_cannot_authorize_itself():
    proven, blockers, evidence = _forward_certification_diagnosis(
        _Db(**FULL_HEALTH), ROW
    )

    assert proven is False
    # The only remaining blocker is the unusable publication authority path.
    assert blockers == [
        "NFL_ARTIFACT_PUBLICATION_FLAG_NOT_GRANTED",
        "NFL_REGISTRY_PUBLICATION_AUTHORITY_CONFLICT",
    ]
    assert evidence["graded_events"] == 140


def test_a_new_model_with_no_settled_grades_is_not_called_a_version_mismatch():
    """A newly registered model may simply have nothing graded yet."""
    proven, blockers, evidence = _forward_certification_diagnosis(
        _Db(unique_games=0, graded=0, health_blockers=()), ROW
    )

    assert proven is False
    assert "NFL_FORWARD_COHORT_MODEL_VERSION_MISMATCH" not in blockers
    assert evidence["mismatch_evidence"] == []
    # Insufficient evidence is still an explicit, blocking classification.
    assert "NFL_FORWARD_GRADED_EVENTS_BELOW_MINIMUM" in blockers


def test_zero_grades_across_every_version_is_still_insufficient_evidence():
    proven, blockers, evidence = _forward_certification_diagnosis(
        _Db(unique_games=0, graded=0, health_blockers=()), ROW
    )

    assert proven is False
    assert evidence["graded_events"] == 0
    assert "NFL_FORWARD_COHORT_MODEL_VERSION_MISMATCH" not in blockers


def test_a_grade_cohort_recorded_only_under_another_version_is_a_real_mismatch():
    proven, blockers, evidence = _forward_certification_diagnosis(
        _Db(unique_games=0, other_versions=("NFL_EVENT_LOGREG_PLATT_V1_0d37c3ffd17d",)),
        ROW,
    )

    assert proven is False
    assert "NFL_FORWARD_COHORT_MODEL_VERSION_MISMATCH" in blockers
    assert evidence["mismatch_evidence"] == ["NFL_EVENT_LOGREG_PLATT_V1_0d37c3ffd17d"]
    assert evidence["graded_events"] == 0


@pytest.mark.parametrize(
    "unique_games,expected",
    [(0, 0), (67, 67), (99, 99), (100, 100), (140, 140), (250, 250)],
)
def test_cohort_counts_are_exact_and_never_truncated(unique_games, expected):
    _proven, _blockers, evidence = _forward_certification_diagnosis(
        _Db(unique_games=unique_games), ROW
    )

    assert evidence["graded_events"] == expected
    assert evidence["graded_events_exact"] is True


def test_a_250_game_cohort_behind_a_100_minimum_is_not_reported_as_200():
    _proven, _blockers, evidence = _forward_certification_diagnosis(
        _Db(unique_games=250, minimum=100), ROW
    )

    assert evidence["graded_events"] == 250
    assert evidence["minimum_forward_required"] == 100


def test_duplicate_grade_rows_never_inflate_the_cohort():
    _proven, _blockers, evidence = _forward_certification_diagnosis(
        _Db(unique_games=67, duplicates=40), ROW
    )

    assert evidence["graded_events"] == 67


def test_grades_for_mixed_versions_count_only_the_exact_version():
    _proven, _blockers, evidence = _forward_certification_diagnosis(
        _Db(unique_games=67, other_versions=("NFL_EVENT_LOGREG_PLATT_V1_0d37c3ffd17d",)),
        ROW,
    )

    assert evidence["graded_events"] == 67
    assert evidence["mismatch_evidence"] == []


def test_pagination_failure_is_typed_and_never_reported_as_model_absence():
    proven, blockers, _evidence = _forward_certification_diagnosis(
        _Db(fail_range=True), ROW
    )

    assert proven is False
    assert blockers == ["NFL_FORWARD_GRADE_COHORT_QUERY_FAILED"]


def test_a_page_cap_reports_a_lower_bound_instead_of_a_false_total(monkeypatch):
    from v17 import nfl_daily_stage_receipts as module

    monkeypatch.setattr(module, "_GRADE_PAGE_SIZE", 2)
    monkeypatch.setattr(module, "_GRADE_MAX_PAGES", 1)

    _proven, _blockers, evidence = _forward_certification_diagnosis(
        _Db(unique_games=3), ROW
    )

    assert evidence["graded_events"] == 2
    assert evidence["graded_events_exact"] is False


def test_minimum_cohort_policy_below_one_hundred_fails_closed():
    proven, blockers, _evidence = _forward_certification_diagnosis(
        _Db(minimum=50), ROW
    )

    assert proven is False
    assert "NFL_FORWARD_MINIMUM_COHORT_INVALID" in blockers


def test_missing_health_row_is_a_registry_shape_blocker_not_model_absence():
    proven, blockers, _evidence = _forward_certification_diagnosis(
        _Db(health_rows=[]), ROW
    )

    assert proven is False
    assert blockers == ["NFL_FORWARD_CERTIFICATION_REGISTRY_SHAPE_INVALID"]


def test_diagnosis_and_boolean_view_never_diverge():
    # Every fixture that honours the production CHECK constraint must HOLD.
    for db in (
        _Db(),
        _Db(active=False),
        _Db(promoted=False),
        _Db(unique_games=99),
        _Db(unique_games=0),
        _Db(unique_games=0, other_versions=("NFL_EVENT_LOGREG_PLATT_V1_OLD",)),
    ):
        proven, blockers, _evidence = _forward_certification_diagnosis(db, ROW)
        assert _forward_certification_proven(db, ROW) is proven
        assert proven is (blockers == [])
        assert proven is False

    # A registry that could set the flag is unreachable in the real schema, so
    # it exists only to prove the two views still agree on a positive case.
    for db in (_Db(approved=True), _Db(approved=True, **FULL_HEALTH)):
        proven, blockers, _evidence = _forward_certification_diagnosis(db, ROW)
        assert _forward_certification_proven(db, ROW) is proven
        assert proven is (blockers == [])
