"""NFL event-winner adapter and incumbent baseline for the shared evaluator."""
from __future__ import annotations

import json

import pytest

from v17 import challenger_evaluator as ev
from v17 import nfl_event_challenger_pairs as nfl

MV = "NFL_EVENT_LOGREG_PLATT_V1_test"
CH = "NFL_CHALLENGER_TEST"


def _grade(i, *, team="Houston Texans", p=0.65, lb=0.55, y=1, mv=MV, created="2026-11-10 09:00:00+00",
           start="2026-11-16 01:20:00+00", event=None):
    return {"event_prediction_id": f"pred-{i}", "official_event_id": event or f"2026_10_IND_HOU_{i}",
            "model_version": mv, "selected_participant": team, "calibrated_probability": p,
            "calibrated_lower_bound": lb, "outcome": y, "prediction_created_at": created, "event_start_time_utc": start}


def _challenger(i, *, side="HOU", p=0.7, lb=0.6, created="2026-11-12 00:00:00+00", event=None, outcome=None, cid=CH):
    return {"challenger_id": cid, "official_event_id": event or f"2026_10_IND_HOU_{i}", "selected_participant": side,
            "home_team": "HOU", "away_team": "IND", "calibrated_probability": p, "composite_lower_bound": lb,
            "prediction_created_at": created, "outcome": outcome}


def test_participant_canonicalization_matches_names_and_abbreviations():
    assert nfl._participant("HOU") == nfl._participant("Houston Texans") == nfl._participant(" houston texans ") == "HOU"
    assert nfl._participant("Atlanta Falcons") == "ATL"


def test_incumbent_rows_validate_and_count_exclusions():
    grades = [
        _grade(1), _grade(2, y=None), _grade(3, created="2026-11-17 00:00:00+00"), _grade(4, mv="OTHER"),
        _grade(5, event="dup"), _grade(6, event="dup"), {**_grade(7), "event_prediction_id": ""},
        _grade(8, created="not-a-time"), _grade(9, y=True),
    ]
    rows, excluded = nfl.incumbent_rows(grades, model_version=MV)
    assert [r["row_id"] for r in rows] == ["pred-1", "pred-5"]
    assert excluded == {
        "OUTCOME_NOT_BINARY": 2, "FORECAST_NOT_BEFORE_START": 1, "OTHER_MODEL_VERSION": 1,
        "DUPLICATE_INCUMBENT_EVENT": 1, "INCUMBENT_IDENTITY_MISSING": 1, "TIMESTAMP_INVALID": 1,
    }
    assert rows[0]["cohort"] == ev.PROSPECTIVE and rows[0]["market_family"] == nfl.MARKET_FAMILY
    assert "challenger_p" not in rows[0] and not any(k.startswith("_") for k in rows[0])


def test_paired_rows_orient_challenger_to_incumbent_side():
    grades = [_grade(1, team="Houston Texans", y=1), _grade(2, team="Indianapolis Colts", y=0)]
    challenger = [_challenger(1, side="HOU", p=0.7, lb=0.6), _challenger(2, side="HOU", p=0.7, lb=0.6)]
    rows, excluded = nfl.paired_rows(grades, challenger, model_version=MV, challenger_id=CH)
    by_id = {r["row_id"]: r for r in rows}
    assert by_id["pred-1"]["challenger_p"] == pytest.approx(0.7)
    assert by_id["pred-1"]["challenger_lower"] == 0.6
    # Incumbent picked IND; challenger's HOU 0.7 means IND 0.3, and its lower bound does not transfer.
    assert by_id["pred-2"]["challenger_p"] == pytest.approx(0.3)
    assert by_id["pred-2"]["challenger_lower"] is None
    assert excluded == {}


def test_paired_rows_exclusions_are_typed():
    grades = [_grade(i) for i in range(1, 8)]
    challenger = [
        _challenger(2, created="2026-11-17 00:00:00+00"),          # after kickoff
        _challenger(3), _challenger(3),                             # ambiguous
        _challenger(4, side="KC"),                                  # not a participant
        _challenger(5, p="0.7"),                                    # invalid probability
        _challenger(6, outcome=0),                                  # contradicts incumbent outcome
        _challenger(7, cid="OTHER"),                                # other challenger -> no row
    ]
    rows, excluded = nfl.paired_rows(grades, challenger, model_version=MV, challenger_id=CH)
    assert rows == []
    assert excluded == {
        "NO_CHALLENGER_ROW": 2, "CHALLENGER_FORECAST_NOT_BEFORE_START": 1, "AMBIGUOUS_CHALLENGER_ROWS": 1,
        "PARTICIPANT_MISMATCH": 1, "CHALLENGER_PROBABILITY_INVALID": 1, "OUTCOME_CONFLICT": 1,
    }


def test_paired_rows_feed_the_evaluator():
    grades = [_grade(i, p=0.6, y=i % 2) for i in range(1, 41)]
    challenger = [_challenger(i, p=0.75 if i % 2 else 0.4) for i in range(1, 41)]
    rows, excluded = nfl.paired_rows(grades, challenger, model_version=MV, challenger_id=CH)
    assert len(rows) == 40 and excluded == {}
    packet = ev.evaluate(rows)
    assert packet["recommendation"] == ev.NO_POLICY
    assert packet["cohorts"][ev.PROSPECTIVE]["n"] == 40
    assert packet["cohorts"][ev.PROSPECTIVE]["brier"]["paired_diff"]["mean"] < 0


def test_incumbent_baseline_and_proposal():
    grades = [_grade(i, p=0.5 + (i % 9) / 20, lb=0.5 + (i % 9) / 20 - 0.08, y=1 if i % 3 else 0) for i in range(60)]
    rows, _ = nfl.incumbent_rows(grades, model_version=MV)
    base = ev.incumbent_baseline(rows)
    assert base["kind"] == "INCUMBENT_BASELINE" and base["cohorts"][ev.HISTORICAL] == {"n": 0, "status": "EMPTY"}
    inc = base["cohorts"][ev.PROSPECTIVE]["incumbent"]
    assert inc["brier"] > 0 and inc["lower_bound"]["n"] == 60
    proposal = ev.propose_policy_from_baseline(base)
    assert proposal["status"] == "PROPOSED_UNAPPROVED" and proposal["min_prospective_n"] is None
    assert next(c for c in proposal["criteria"] if c["metric"] == "calibration.challenger.ece")["value"] == inc["ece"]
    assert all(proposal[k] is False for k in ("can_execute", "automatic_promotion", "promotion_attempted"))
    # An unapproved proposal used as policy changes nothing.
    assert ev.apply_policy({"market_family": nfl.MARKET_FAMILY, "cohorts": {}}, proposal)["recommendation"] == ev.NO_POLICY


def test_baseline_still_validates_challenger_when_present():
    rows, _ = nfl.incumbent_rows([_grade(1)], model_version=MV)
    with pytest.raises(ev.EvaluatorInputError):
        ev.incumbent_baseline([{**rows[0], "challenger_p": 1.0}])
    with pytest.raises(ev.EvaluatorInputError):
        ev.evaluate(rows)  # the paired evaluator still requires a challenger


def test_cli_round_trip(tmp_path, capsys):
    grades = tmp_path / "g.json"
    grades.write_text(json.dumps([_grade(i, y=i % 2) for i in range(10)]))
    out = tmp_path / "rows.jsonl"
    assert nfl.main(["--grades-json", str(grades), "--model-version", MV, "--out", str(out)]) == 0
    assert json.loads(capsys.readouterr().out)["rows"] == 10
    assert ev.main(["--rows-jsonl", str(out), "--baseline"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["proposed_policy"]["status"] == "PROPOSED_UNAPPROVED"
    assert nfl.main(["--grades-json", str(grades), "--model-version", MV, "--challenger-json", str(grades), "--out", str(out)]) == 2
