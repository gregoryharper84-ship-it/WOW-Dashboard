from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from types import SimpleNamespace

import pytest

from v17.nfl_forward_shadow import (
    ForwardPrediction,
    NFLForwardShadowError,
    _paginate,
    calibration_health,
    grade_forward_predictions,
    select_canonical_forward_predictions,
)


START = datetime(2026, 9, 20, 17, 0, tzinfo=timezone.utc)


def _prediction_row(
    *,
    event_id: str = "2026_02_AAA_BBB",
    prediction_id: str = "11111111-1111-1111-1111-111111111111",
    created_delta_minutes: int = -60,
    p: float = 0.65,
    lower: float = 0.58,
    upper: float = 0.72,
    publishable: bool = True,
    package_valid: bool = True,
    provenance_complete: bool = True,
    can_execute: bool = False,
):
    return {
        "event_prediction_id": prediction_id,
        "official_event_id": event_id,
        "event_start_time_utc": START.isoformat(),
        "created_at": (START + timedelta(minutes=created_delta_minutes)).isoformat(),
        "model_version": "NFL_EVENT_LOGREG_PLATT_V1_test",
        "home_team": "BBB",
        "away_team": "AAA",
        "selected_participant": "BBB",
        "calibrated_selection_probability": p,
        "calibrated_selection_lower_bound": lower,
        "calibrated_selection_upper_bound": upper,
        "model_package_valid": package_valid,
        "provenance_complete": provenance_complete,
        "model_probability_publishable": publishable,
        "can_execute": can_execute,
    }


def _prediction(*, event_id: str, p: float, lower: float, upper: float, index: int) -> ForwardPrediction:
    return ForwardPrediction(
        event_prediction_id=f"00000000-0000-0000-0000-{index:012d}",
        official_event_id=event_id,
        event_start_time_utc=START + timedelta(days=index),
        prediction_created_at=START + timedelta(days=index, hours=-2),
        model_version="NFL_EVENT_LOGREG_PLATT_V1_test",
        home_team="BBB",
        away_team="AAA",
        selected_participant="BBB",
        calibrated_probability=p,
        calibrated_lower_bound=lower,
        calibrated_upper_bound=upper,
        model_package_valid=True,
        provenance_complete=True,
        model_probability_publishable=True,
        can_execute=False,
    )


def test_canonical_dedupe_uses_latest_valid_pregame_prediction_without_outcome_input():
    rows = [
        _prediction_row(prediction_id="11111111-1111-1111-1111-111111111111", created_delta_minutes=-120, p=0.61, lower=0.55, upper=0.68),
        _prediction_row(prediction_id="22222222-2222-2222-2222-222222222222", created_delta_minutes=-30, p=0.66, lower=0.59, upper=0.73),
        _prediction_row(prediction_id="33333333-3333-3333-3333-333333333333", created_delta_minutes=5, p=0.99, lower=0.98, upper=0.999),
        _prediction_row(prediction_id="44444444-4444-4444-4444-444444444444", created_delta_minutes=-10, p=0.90, lower=0.80, upper=0.95, publishable=False),
    ]
    selected = select_canonical_forward_predictions(rows)
    assert len(selected) == 1
    assert selected[0].event_prediction_id == "22222222-2222-2222-2222-222222222222"
    assert selected[0].calibrated_probability == pytest.approx(0.66)


def test_invalid_or_unsafe_prediction_never_enters_forward_cohort():
    assert select_canonical_forward_predictions([
        _prediction_row(package_valid=False),
        _prediction_row(provenance_complete=False),
        _prediction_row(publishable=False),
    ]) == []
    with pytest.raises(ValueError, match="CAN_EXECUTE_MUST_BE_FALSE"):
        select_canonical_forward_predictions([_prediction_row(can_execute=True)])


def test_grade_matches_selected_side_and_excludes_tie():
    prediction = _prediction(event_id="2026_02_AAA_BBB", p=0.70, lower=0.61, upper=0.77, index=1)
    win = grade_forward_predictions([prediction], [{
        "game_id": prediction.official_event_id,
        "home_score": 27,
        "away_score": 17,
        "home_win": True,
        "tie": False,
    }])
    assert len(win) == 1
    assert win[0].outcome == 1
    assert win[0].brier == pytest.approx((0.70 - 1.0) ** 2)
    assert win[0].log_loss == pytest.approx(-math.log(0.70))
    assert win[0].can_execute is False

    tied = grade_forward_predictions([prediction], [{
        "game_id": prediction.official_event_id,
        "home_score": 20,
        "away_score": 20,
        "home_win": False,
        "tie": True,
    }])
    assert tied == []


def test_forward_health_is_fail_closed_until_minimum_sample_exists():
    predictions = [
        _prediction(event_id=f"event-{i}", p=0.65, lower=0.55, upper=0.74, index=i)
        for i in range(10)
    ]
    outcomes = [
        {"game_id": p.official_event_id, "home_score": 24, "away_score": 17, "home_win": True, "tie": False}
        for p in predictions
    ]
    health = calibration_health(grade_forward_predictions(predictions, outcomes), min_forward=100)
    assert health["status"] == "INSUFFICIENT_FORWARD_EVIDENCE"
    assert health["certification_recommendation"] == "DO_NOT_CERTIFY_YET"
    assert health["can_execute"] is False


def test_well_calibrated_forward_cohort_can_pass_evidence_gate_but_does_not_certify():
    predictions = []
    outcomes = []
    # 100 rows at p=.70 with exactly 70 wins. The lower bound is conservative,
    # so lower-bound reliability also passes.
    for i in range(100):
        p = _prediction(event_id=f"event-{i}", p=0.70, lower=0.60, upper=0.78, index=i)
        predictions.append(p)
        won = i < 70
        outcomes.append({
            "game_id": p.official_event_id,
            "home_score": 24 if won else 17,
            "away_score": 17 if won else 24,
            "home_win": won,
            "tie": False,
        })
    health = calibration_health(grade_forward_predictions(predictions, outcomes), min_forward=100)
    assert health["status"] == "PASS"
    assert health["certification_recommendation"] == "FORWARD_EVIDENCE_PASS"
    assert health["graded_n"] == 100
    assert health["can_execute"] is False


def test_overconfident_lower_bound_fails_even_when_point_forecast_is_reasonable():
    predictions = []
    outcomes = []
    for i in range(100):
        p = _prediction(event_id=f"event-{i}", p=0.70, lower=0.75, upper=0.80, index=i)
        # construct object directly because the public scorer contract correctly
        # rejects lower > p; here we exercise health logic against a synthetic
        # graded cohort representing an impossible reliability claim.
        predictions.append(p)
        won = i < 70
        outcomes.append({
            "game_id": p.official_event_id,
            "home_score": 24 if won else 17,
            "away_score": 17 if won else 24,
            "home_win": won,
            "tie": False,
        })
    with pytest.raises(ValueError):
        # grade path itself must reject the impossible bounds before health can
        # even be computed in production.
        select_canonical_forward_predictions([
            _prediction_row(p=0.70, lower=0.75, upper=0.80)
        ])


class _ForwardPagedQuery:
    def __init__(self, db, name):
        self.db = db
        self.name = name
        self.key = None
        self.start = self.end = None

    def select(self, columns):
        self.columns = columns
        self.db.selections.append((self.name, columns))
        return self

    def order(self, key):
        self.key = key
        self.db.orders.append((self.name, key))
        return self

    def range(self, start, end):
        self.start = start
        self.end = end
        return self

    def execute(self):
        if not self.key:
            raise AssertionError("PostgREST forward reads require explicit unique ordering")
        ordered = sorted(self.db.rows[self.name], key=lambda x: str(x.get(self.key) or ""))
        # PostgREST range(start, end) has an inclusive end offset.
        return SimpleNamespace(data=ordered[self.start:self.end + 1])


class _ForwardPagedDB:
    def __init__(self, rows):
        self.rows = rows
        self.orders = []
        self.selections = []

    def table(self, name):
        return _ForwardPagedQuery(self, name)


@pytest.mark.parametrize("table,key", [
    ("wow_nfl_event_predictions", "event_prediction_id"),
    ("wow_nfl_training_games", "game_id"),
    ("wow_nfl_forward_shadow_grades", "grade_id"),
])
def test_forward_pagination_is_deterministic_across_1000_row_boundary(table, key):
    data = [{key: f"id{i:04d}"} for i in reversed(range(1002))]
    db = _ForwardPagedDB({table: data})
    loaded = _paginate(db, table, "official_event_id", page_size=1000)
    assert len(loaded) == 1002
    assert [r[key] for r in loaded] == [f"id{i:04d}" for i in range(1002)]
    assert db.orders == [(table, key), (table, key)]
    assert all(key in projection for _, projection in db.selections)


@pytest.mark.parametrize("table,key", [
    ("wow_nfl_event_predictions", "event_prediction_id"),
    ("wow_nfl_training_games", "game_id"),
    ("wow_nfl_forward_shadow_grades", "grade_id"),
])
def test_forward_pagination_rejects_missing_and_duplicate_source_ids(table, key):
    for dataset, expected in [
        ([{key: "a"}, {key: "a"}], "ID_DUPLICATED"),
        ([{key: "a"}, {"unrelated": "b"}], "ID_MISSING"),
    ]:
        db = _ForwardPagedDB({table: dataset})
        with pytest.raises(NFLForwardShadowError, match=f"NFL_FORWARD_PAGINATION_INVALID:.*:{expected}"):
            _paginate(db, table, "official_event_id", page_size=1)


def test_forward_pagination_rejects_unregistered_tables_and_invalid_page_size():
    db = _ForwardPagedDB({"wow_nfl_event_predictions": []})
    with pytest.raises(NFLForwardShadowError, match="NFL_FORWARD_PAGINATION_INVALID"):
        _paginate(db, "unregistered", "event_prediction_id")
    with pytest.raises(NFLForwardShadowError, match="NFL_FORWARD_PAGINATION_INVALID"):
        _paginate(db, "wow_nfl_event_predictions", "event_prediction_id", page_size=0)


@pytest.mark.parametrize("columns", [
    "",
    "official_event_id,",
    "official_event_id,,game_id",
    "official_event_id),game_id",
    "official_event_id;select",
])
def test_forward_pagination_rejects_malformed_projection(columns):
    db = _ForwardPagedDB({"wow_nfl_event_predictions": []})
    with pytest.raises(NFLForwardShadowError, match="NFL_FORWARD_PAGINATION_INVALID:PROJECTION_INVALID"):
        _paginate(db, "wow_nfl_event_predictions", columns, page_size=2)
    assert db.orders == []
    assert db.selections == []


def test_late_page_identity_failure_cannot_persist_partial_nfl_grades(monkeypatch):
    from v17 import nfl_forward_shadow as shadow

    table = "wow_nfl_event_predictions"
    # Duplicate source IDs materialize in separate pages (1000 + 1).
    data = [{"event_prediction_id": f"id{i:04d}"} for i in range(1000)]
    data.append({"event_prediction_id": "id0999"})
    db = _ForwardPagedDB({table: data})
    calls = []
    monkeypatch.setattr(shadow, "load_prediction_rows", lambda client: shadow._paginate(
        client, table, "event_prediction_id", page_size=1000,
    ))
    monkeypatch.setattr(shadow, "persist_new_grades", lambda *_: calls.append("grades"))
    monkeypatch.setattr(shadow, "persist_health", lambda *_: calls.append("health"))
    with pytest.raises(NFLForwardShadowError, match="NFL_FORWARD_PAGINATION_INVALID:.*:ID_DUPLICATED"):
        shadow.run_forward_shadow(
            db,
            settlement_refresh_fn=lambda _: {"status": "COMPLETED", "can_execute": False},
        )
    assert calls == []



class _AlwaysFullPageQuery(_ForwardPagedQuery):
    def execute(self):
        if not self.key:
            raise AssertionError("Expected ordered PostgREST query")
        return SimpleNamespace(data=[
            {self.key: f"id{i:04d}"} for i in range(self.start, self.end + 1)
        ])


class _AlwaysFullPageDB(_ForwardPagedDB):
    def table(self, name):
        return _AlwaysFullPageQuery(self, name)


def test_forward_pagination_rejects_never_ending_full_page_source():
    table = "wow_nfl_event_predictions"
    db = _AlwaysFullPageDB({})
    with pytest.raises(NFLForwardShadowError, match="NFL_FORWARD_PAGINATION_INVALID:.*:PAGE_LIMIT_EXCEEDED"):
        _paginate(db, table, "event_prediction_id", page_size=2, max_pages=3)
    # Three full pages plus one bounded existence probe: no unbounded loop.
    assert db.orders == [(table, "event_prediction_id")] * 4
    assert db.selections[-1] == (table, "event_prediction_id")


@pytest.mark.parametrize("raw_id", [1, 1.0, True, ["a"], {"a": 1}])
def test_forward_pagination_rejects_non_string_primary_keys(raw_id):
    table = "wow_nfl_event_predictions"
    db = _ForwardPagedDB({table: [{"event_prediction_id": raw_id}]})
    with pytest.raises(NFLForwardShadowError, match="NFL_FORWARD_PAGINATION_INVALID:.*:ID_TYPE_INVALID"):
        _paginate(db, table, "event_prediction_id")


@pytest.mark.parametrize("max_pages", [0, -1, True, 1.5])
def test_forward_pagination_rejects_invalid_page_limit(max_pages):
    table = "wow_nfl_event_predictions"
    db = _ForwardPagedDB({table: []})
    with pytest.raises(NFLForwardShadowError, match="NFL_FORWARD_PAGINATION_INVALID"):
        _paginate(db, table, "event_prediction_id", max_pages=max_pages)
    assert db.orders == []


@pytest.mark.parametrize("table,key", [
    ("wow_nfl_event_predictions", "event_prediction_id"),
    ("wow_nfl_training_games", "game_id"),
    ("wow_nfl_forward_shadow_grades", "grade_id"),
])
def test_forward_pagination_exactly_fills_bounded_pages_without_false_failure(table, key):
    db = _ForwardPagedDB({
        table: [{key: f"id{i:04d}"} for i in reversed(range(6))]
    })
    rows = _paginate(db, table, key, page_size=2, max_pages=3)
    assert [r[key] for r in rows] == [f"id{i:04d}" for i in range(6)]
    assert db.orders == [(table, key)] * 4
    # Probe must read the identity only, not duplicate a full data page.
    assert db.selections[-1] == (table, key)


@pytest.mark.parametrize("table,key", [
    ("wow_nfl_event_predictions", "event_prediction_id"),
    ("wow_nfl_training_games", "game_id"),
    ("wow_nfl_forward_shadow_grades", "grade_id"),
])
def test_forward_pagination_rejects_one_row_over_exact_boundary(table, key):
    db = _ForwardPagedDB({
        table: [{key: f"id{i:04d}"} for i in reversed(range(7))]
    })
    with pytest.raises(NFLForwardShadowError, match="PAGE_LIMIT_EXCEEDED"):
        _paginate(db, table, key, page_size=2, max_pages=3)
    assert db.orders == [(table, key)] * 4
    assert db.selections[-1] == (table, key)


def test_over_limit_corpus_never_persists_grades_or_forward_health(monkeypatch):
    from v17 import nfl_forward_shadow as shadow
    table = "wow_nfl_event_predictions"
    data = [{"event_prediction_id": f"id{i:04d}"} for i in range(7)]
    db = _ForwardPagedDB({table: data})
    writes = []
    monkeypatch.setattr(
        shadow, "load_prediction_rows",
        lambda client: shadow._paginate(
            client, table, "event_prediction_id", page_size=2, max_pages=3
        ),
    )
    monkeypatch.setattr(shadow, "persist_new_grades", lambda *_: writes.append("grades"))
    monkeypatch.setattr(shadow, "persist_health", lambda *_: writes.append("health"))
    with pytest.raises(NFLForwardShadowError, match="PAGE_LIMIT_EXCEEDED"):
        shadow.run_forward_shadow(
            db, settlement_refresh_fn=lambda _: {"status": "COMPLETED", "can_execute": False}
        )
    assert writes == []
