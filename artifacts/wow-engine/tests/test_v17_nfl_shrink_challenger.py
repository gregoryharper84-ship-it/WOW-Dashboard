import math
from types import SimpleNamespace

import pytest

from v17 import nfl_forward_shadow as shadow
from v17 import nfl_shrink_challenger as ch


def test_constants_are_the_preregistered_values():
    assert (ch.CHALLENGER_ID, ch.SHRINK, ch.BASE_HOME_RATE, ch.PREREGISTERED_ON) == (
        "NFL_ML_SHRINK075_V1", 0.75, 0.541301, "2026-10-09")


def test_transform_matches_replay_formula_and_shrinks_toward_base():
    logit = lambda p: math.log(p / (1 - p))
    for p in (0.2, 0.5, 0.65, 0.9):
        expected = 1 / (1 + math.exp(-(0.75 * logit(p) + 0.25 * logit(0.541301))))
        assert ch.challenger_home_probability(p) == pytest.approx(expected)
        assert abs(ch.challenger_home_probability(p) - 0.541301) <= abs(p - 0.541301)
    assert ch.challenger_home_probability(0.541301) == pytest.approx(0.541301)


def _pred(pid, home, away, sel):
    return SimpleNamespace(event_prediction_id=pid, home_team=home, away_team=away, selected_participant=sel)


def _grade(pid, p, y):
    return SimpleNamespace(event_prediction_id=pid, calibrated_probability=p, outcome=y)


def test_evaluate_orients_home_and_away_selections_correctly():
    preds = [_pred("h", "A", "B", "A"), _pred("a", "C", "D", "D")]
    grades = [_grade("h", 0.70, 1), _grade("a", 0.70, 0)]
    out = ch.evaluate(preds, grades)
    ch_home_sel = ch.challenger_home_probability(0.70)
    ch_away_sel = 1 - ch.challenger_home_probability(0.30)
    assert out["n"] == 2
    assert out["challenger_brier"] == pytest.approx(((ch_home_sel - 1) ** 2 + (ch_away_sel - 0) ** 2) / 2)
    assert out["champion_brier"] == pytest.approx((0.09 + 0.49) / 2)
    assert out["brier_improvement"] == pytest.approx(out["champion_brier"] - out["challenger_brier"])
    assert out["eligible_for_review"] is False and out["automatic_promotion_allowed"] is False


def test_overconfident_losses_favor_challenger():
    preds = [_pred(str(i), "H", "A", "H") for i in range(10)]
    grades = [_grade(str(i), 0.80, 1 if i < 5 else 0) for i in range(10)]
    assert ch.evaluate(preds, grades)["brier_improvement"] > 0


def test_no_graded_games():
    assert ch.evaluate([], [])["status"] == "NO_GRADED_GAMES"


class _DB:
    def __init__(self):
        self.rows = []

    def table(self, name):
        db = self

        class Q:
            def select(self, *_a):
                self.f = {}
                return self

            def eq(self, k, v):
                self.f[k] = v
                return self

            def limit(self, *_a):
                return self

            def insert(self, row):
                db.rows.append(row)
                return SimpleNamespace(execute=lambda: None)

            def execute(self):
                return SimpleNamespace(data=[r for r in db.rows if all(r.get(k) == v for k, v in self.f.items())])
        return Q()


def test_persist_is_advisory_and_idempotent_per_cohort_size():
    db = _DB()
    ev = ch.evaluate([_pred("h", "A", "B", "A")], [_grade("h", 0.7, 1)])
    assert ch.persist(db, ev) is True
    assert ch.persist(db, ev) is False
    row = db.rows[0]
    assert row["authority"] == "ADVISORY_ONLY" and row["automatic_promotion_allowed"] is False
    assert row["review_status"] == "SHADOW_VALIDATING" and row["can_execute"] is False
    assert row["cohort_key"] == "NFL_ML_FORWARD_SHADOW_N1"


def test_daily_job_isolates_challenger_failure(monkeypatch):
    monkeypatch.setattr(ch, "evaluate", lambda *a: (_ for _ in ()).throw(ValueError("x")))
    out = shadow._evaluate_shrink_challenger(object(), [], [])
    assert out["status"] == "FAILED" and out["reason_code"] == "NFL_SHADOW_CHALLENGER_VALUEERROR"
