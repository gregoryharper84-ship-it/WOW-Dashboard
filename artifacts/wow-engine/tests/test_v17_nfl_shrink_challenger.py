import math
from datetime import datetime, timezone
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


def _pred(pid, home, away, sel, *, version="NFL_EVENT_LOGREG_PLATT_V1_fixture",
          kickoff="2026-10-12T17:00:00+00:00"):
    return SimpleNamespace(
        event_prediction_id=pid, home_team=home, away_team=away,
        selected_participant=sel, model_version=version,
        event_start_time_utc=kickoff,
    )


def _grade(pid, p, y, *, version="NFL_EVENT_LOGREG_PLATT_V1_fixture",
           kickoff="2026-10-12T17:00:00+00:00",
           predicted_at="2026-10-12T14:00:00+00:00"):
    return SimpleNamespace(
        event_prediction_id=pid, calibrated_probability=p,
        outcome=y, model_version=version, event_start_time_utc=kickoff,
        prediction_created_at=predicted_at,
    )


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
    assert row["cohort_key"] == ev["cohort_key"]
    assert row["cohort_key"].startswith("NFL_ML_FORWARD_SHADOW_")
    assert row["cohort_key"].endswith("_N1")
    assert len(ev["cohort_sha256"]) == 64
    assert ev["model_version"] == "NFL_EVENT_LOGREG_PLATT_V1_fixture"


def test_daily_job_isolates_challenger_failure(monkeypatch):
    monkeypatch.setattr(ch, "evaluate", lambda *a: (_ for _ in ()).throw(ValueError("x")))
    out = shadow._evaluate_shrink_challenger(object(), [], [])
    assert out["status"] == "FAILED" and out["reason_code"] == "NFL_SHADOW_CHALLENGER_VALUEERROR"


def test_challenger_optional_import_failure_is_isolated(monkeypatch):
    import builtins

    original_import = builtins.__import__

    def unavailable(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "v17" and "nfl_shrink_challenger" in (fromlist or ()):
            raise ModuleNotFoundError("shadow research helper unavailable")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", unavailable)
    result = shadow._evaluate_shrink_challenger(object(), [], [])
    assert result == {
        "status": "FAILED",
        "reason_code": "NFL_SHADOW_CHALLENGER_MODULENOTFOUNDERROR",
        "can_execute": False,
    }


def test_advisory_challenger_ledger_unique_cohort_migration_is_fail_closed():
    from pathlib import Path

    sql = (
        Path(__file__).resolve().parents[1]
        / "migrations" / "20261028_nfl_shadow_challenger_unique_cohort.sql"
    ).read_text(encoding="utf-8").lower()
    assert "having count(*) > 1" in sql
    assert "raise exception 'nfl_shadow_challenger_duplicate_cohort_preexists'" in sql
    assert "create unique index if not exists" in sql
    assert "on public.wow_intelligence_challenger_evaluations (challenger_id, cohort_key)" in sql
    assert "delete from" not in sql and "update public." not in sql


def test_operational_workflow_checks_feature_and_advisory_receipts():
    from pathlib import Path

    workflow = (
        Path(__file__).resolve().parents[3]
        / ".github" / "workflows" / "wow-v17-nfl-forward-shadow.yml"
    ).read_text(encoding="utf-8")
    assert 'feature_refresh.get("status") == "COMPLETED"' in workflow
    assert "feature_rows_labeled" in workflow
    assert 'feature_refresh.get("latest_labeled_gameday")' in workflow
    assert 'refresh.get("latest_gameday")' in workflow
    assert 'challenger.get("status") == "EVALUATED"' in workflow
    assert 'challenger.get("automatic_promotion_allowed") is False' in workflow
    assert "tests/test_v17_nfl_shrink_challenger.py" in workflow
    assert '"can_execute": False' in workflow


def test_research_diagnostics_keep_model_uncertified_with_small_cohort():
    preds = [_pred(str(i), "H", "A", "H") for i in range(10)]
    grades = [_grade(str(i), 0.80, 1 if i < 5 else 0) for i in range(10)]
    result = ch.evaluate(preds, grades)
    assert result["champion_ece_10"] == pytest.approx(0.3)
    assert result["challenger_ece_10"] < result["champion_ece_10"]
    assert "FORWARD_GRADED_N_10_LT_100" in result["provisional_certification_failures"]
    assert "FORWARD_CALIBRATION_BIAS_ABOVE_0_08" in result["provisional_certification_failures"]
    assert "CHALLENGER_LOWER_BOUND_NOT_INDEPENDENTLY_VALIDATED" in result["provisional_certification_failures"]
    assert result["challenger_lower_bound_certified"] is False
    assert result["certification_recommendation"] == "DO_NOT_CERTIFY_YET"
    assert result["automatic_promotion_allowed"] is False
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


@pytest.mark.parametrize("selected,p,y", [
    ("UNKNOWN", 0.7, 1),
    ("H", 1.0, 1),
    ("H", float("nan"), 1),
    ("H", 0.7, 2),
])
def test_invalid_research_inputs_fail_closed(selected, p, y):
    with pytest.raises(ValueError):
        ch.evaluate([_pred("x", "H", "A", selected)], [_grade("x", p, y)])


def test_forward_certification_diagnostics_reveal_failures_below_minimum():
    from types import SimpleNamespace

    grades = [
        SimpleNamespace(
            model_version="NFL_EVENT_LOGREG_PLATT_V1_fixture",
            calibrated_probability=0.80,
            calibrated_lower_bound=0.75,
            outcome=0,
            brier=0.64,
            log_loss=-math.log(0.20),
        )
        for _ in range(12)
    ]
    health = shadow.calibration_health(grades, min_forward=100)
    assert health["status"] == "INSUFFICIENT_FORWARD_EVIDENCE"
    assert health["blockers"] == ["FORWARD_GRADED_N_12_LT_100"]
    assert health["certification_recommendation"] == "DO_NOT_CERTIFY_YET"
    assert set(health["provisional_quality_failures"]) == {
        "FORWARD_BRIER_NOT_BELOW_0_25",
        "FORWARD_LOG_LOSS_NOT_BELOW_LOG2",
        "FORWARD_ECE_ABOVE_0_12",
        "FORWARD_CALIBRATION_BIAS_ABOVE_0_08",
        "FORWARD_LOWER_BOUND_RELIABILITY_FAILED",
    }
    assert health["research_diagnostic_only"] is True
    assert health["can_execute"] is False


def test_forward_certification_mixed_model_versions_never_aggregate_into_pass():
    from types import SimpleNamespace

    grades = [
        SimpleNamespace(model_version="fitted_A"),
        SimpleNamespace(model_version="fitted_B"),
    ]
    health = shadow.calibration_health(grades, min_forward=1)
    assert health["status"] == "MIXED_MODEL_VERSION_HOLD"
    assert health["certification_recommendation"] == "DO_NOT_CERTIFY_YET"
    assert health["blockers"] == ["FORWARD_MIXED_MODEL_VERSION_COHORT"]
    assert health["can_execute"] is False


def test_home_away_calibration_segments_are_research_only():
    preds = [
        _pred("home-a", "H", "A", "H"),
        _pred("home-b", "H", "A", "H"),
        _pred("away-a", "H", "A", "A"),
    ]
    grades = [
        _grade("home-a", 0.7, 1),
        _grade("home-b", 0.7, 0),
        _grade("away-a", 0.6, 1),
    ]
    out = ch.evaluate(preds, grades)
    slices = out["selection_side_diagnostics"]
    assert slices["selected_home"]["n"] == 2
    assert slices["selected_home"]["champion_mean"] == pytest.approx(0.7)
    assert slices["selected_home"]["observed_win_rate"] == pytest.approx(0.5)
    assert slices["selected_home"]["champion_bias"] == pytest.approx(0.2)
    assert slices["selected_away"]["n"] == 1
    assert slices["selected_away"]["champion_mean"] == pytest.approx(0.6)
    assert slices["selected_away"]["observed_win_rate"] == pytest.approx(1.0)
    assert out["certification_recommendation"] == "DO_NOT_CERTIFY_YET"
    assert out["automatic_promotion_allowed"] is False
    assert out["probability_publishable"] is False


def test_empty_selection_side_has_no_misleading_zero_accuracy():
    out = ch.evaluate([_pred("only", "H", "A", "H")], [_grade("only", 0.65, 1)])
    other = out["selection_side_diagnostics"]["selected_away"]
    assert other["n"] == 0
    assert other["observed_win_rate"] is None
    assert other["champion_bias"] is None


def test_research_cohort_binds_exact_single_model_version():
    preds = [
        _pred("a", "H", "A", "H", version="fitted_A"),
        _pred("b", "H", "A", "H", version="fitted_B"),
    ]
    grades = [
        _grade("a", 0.65, 1, version="fitted_A"),
        _grade("b", 0.65, 0, version="fitted_B"),
    ]
    with pytest.raises(ValueError, match="MIXED_MODEL_VERSIONS"):
        ch.evaluate(preds, grades)
    with pytest.raises(ValueError, match="MODEL_VERSION_MISMATCH"):
        ch.evaluate([preds[0]], [_grade("a", 0.65, 1, version="fitted_B")])


def test_research_cohort_key_distinguishes_equal_size_event_sets_and_versions():
    a = ch.evaluate([_pred("a", "H", "A", "H")], [_grade("a", 0.65, 1)])
    b = ch.evaluate([_pred("b", "H", "A", "H")], [_grade("b", 0.65, 1)])
    c = ch.evaluate([_pred("a", "H", "A", "H", version="new_model")],
                    [_grade("a", 0.65, 1, version="new_model")])
    assert len({a["cohort_key"], b["cohort_key"], c["cohort_key"]}) == 3
    db = _DB()
    for cohort in (a, b, c):
        assert ch.persist(db, cohort) is True
        assert ch.persist(db, cohort) is False
    assert len(db.rows) == 3
    assert all(r["authority"] == "ADVISORY_ONLY" and r["can_execute"] is False for r in db.rows)


def test_research_duplicate_grades_fail_closed():
    pred = _pred("a", "H", "A", "H")
    g = _grade("a", 0.7, 1)
    with pytest.raises(ValueError, match="DUPLICATE_GRADE"):
        ch.evaluate([pred], [g, g])
    with pytest.raises(ValueError, match="DUPLICATE_PREDICTION_ID"):
        ch.evaluate([pred, pred], [g])


def test_challenger_only_evaluates_genuine_post_registration_games():
    # One September settled game, plus Oct 9's kickoff (intraday freeze
    # unknown), must NEVER enter post-registration holdout metrics.
    pre = _pred("pre", "H", "A", "H", kickoff="2026-09-27T17:00:00Z")
    on_day = _pred("on-day", "H", "A", "H", kickoff="2026-10-09T00:15:00Z")
    post = _pred("post", "H", "A", "H")
    grade_pre = _grade("pre", 0.99, 0, kickoff="2026-09-27T17:00:00Z",
                      predicted_at="2026-09-27T14:00:00Z")
    grade_on = _grade("on-day", 0.99, 0, kickoff="2026-10-09T00:15:00Z",
                     predicted_at="2026-10-08T22:00:00Z")
    grade_post = _grade("post", 0.7, 1)
    output = ch.evaluate([pre, on_day, post], [grade_pre, grade_on, grade_post])
    assert output["n"] == 1
    assert output["excluded_before_prospective_cutoff_n"] == 2
    assert output["prospective_start_utc"] == "2026-10-10T00:00:00+00:00"
    assert output["champion_brier"] == pytest.approx(0.09)
    assert output["eligible_for_review"] is False
    db = _DB()
    assert ch.persist(db, output) is True
    assert db.rows[0]["holdout_n"] == 1
    assert db.rows[0]["cohort_key"] == output["cohort_key"]


def test_challenger_pre_registration_only_cohort_not_evaluated_or_persisted():
    pred = _pred("old", "H", "A", "H", kickoff="2026-09-27T17:00:00Z")
    grade = _grade("old", 0.8, 0, kickoff="2026-09-27T17:00:00Z",
                   predicted_at="2026-09-27T14:00:00Z")
    output = ch.evaluate([pred], [grade])
    assert output["status"] == "NO_GRADED_GAMES"
    assert output["n"] == 0
    assert output["excluded_before_prospective_cutoff_n"] == 1
    assert ch.persist(_DB(), output) is False


def test_challenger_missing_timestamp_or_post_kickoff_prediction_fails_closed():
    pred = _pred("x", "H", "A", "H")
    missing = _grade("x", 0.7, 1)
    del missing.event_start_time_utc
    with pytest.raises((ValueError, TypeError)):
        ch.evaluate([pred], [missing])
    postkick = _grade("x", 0.7, 1, predicted_at="2026-10-12T17:00:00Z")
    with pytest.raises(ValueError, match="CHRONOLOGY_INVALID"):
        ch.evaluate([pred], [postkick])
