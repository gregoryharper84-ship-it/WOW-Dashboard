"""TDD: preserve an early NFL capture opportunity and expose evidence holds."""
from datetime import datetime, timezone
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from prospective import parse_fixture
from runner import MAX_LOOKAHEAD_HOURS, capture_clock_eligible, status_from_counts


def thursday():
    return dict(game_id="2026_11_IND_HOU", season="2026", week="11",
                gameday="2026-11-19", gametime="20:15", home_team="HOU",
                away_team="IND", game_type="REG", home_score="", away_score="")


def test_wednesday_morning_sweep_includes_real_thursday_night_game():
    # 12:17 UTC Wednesday is 36h 58m before 01:15 UTC Friday kickoff.
    sweep = datetime(2026, 11, 18, 12, 17, tzinfo=timezone.utc)
    kickoff = "2026-11-20T01:15:00Z"
    assert MAX_LOOKAHEAD_HOURS >= 38
    assert capture_clock_eligible(kickoff, sweep)
    event = parse_fixture(thursday(), sweep, horizon_hours=MAX_LOOKAHEAD_HOURS)
    assert event is not None and event["official_event_id"] == "2026_11_IND_HOU"


def test_extended_window_still_rejects_started_or_future_too_early():
    sweep = datetime(2026, 11, 18, 12, 17, tzinfo=timezone.utc)
    assert not capture_clock_eligible("2026-11-20T13:17:01Z", sweep)
    assert not capture_clock_eligible("2026-11-18T12:17:00Z", sweep)
    assert parse_fixture(thursday() | {"home_score": "7"}, sweep,
                         horizon_hours=MAX_LOOKAHEAD_HOURS) is None


def test_all_eligible_held_is_visible_and_fails_closed():
    result = status_from_counts(2, 0, 0, 2)
    assert result["status"] == "EVIDENCE_ALL_HELD"
    assert result["run_ok"] is False
    assert result["evidence_held"] == 2
    assert result["can_execute"] is False


def test_idle_and_already_frozen_still_count_as_healthy():
    assert status_from_counts(0, 0, 0, 0)["run_ok"] is True
    assert status_from_counts(1, 0, 1, 0)["run_ok"] is True
    assert status_from_counts(2, 1, 0, 1)["run_ok"] is True


def test_bad_capture_accounting_fails_closed():
    result = status_from_counts(3, 1, 0, 1)
    assert result["run_ok"] is False
    assert result["status"] == "CAPTURE_ACCOUNTING_INCONSISTENT"


def test_workflow_avoids_minute_zero_and_alarms_after_grading():
    workflow = (HERE.parents[4] / ".github/workflows/wow-v17-nfl-prospective-shadow.yml").read_text()
    assert re.search(r'cron:\s*["\']17 12,18 \* 9-12,1-2 \*["\']', workflow)
    assert "Verify capture receipt is not all-held" in workflow
    assert "NFL_PROSPECTIVE_ALL_ELIGIBLE_HELD" in workflow
    assert 'retention-days: 90' in workflow
    assert 'WOW_CAN_EXECUTE: "false"' in workflow


def test_all_held_receipt_explains_why_without_raw_exception_details():
    import runner
    assert hasattr(runner, "capture_hold_reason"), "runner must classify an evidence hold"
    assert runner.capture_hold_reason(ValueError("NFL_TARGET_EVENT_FEATURE_MISMATCH")) == "NFL_TARGET_EVENT_FEATURE_MISMATCH"
    assert runner.capture_hold_reason(ValueError("SECRET=untrusted")) == "NFL_CAPTURE_FEATURE_INVALID"
    assert runner.capture_hold_reason(KeyError("password")) == "NFL_CAPTURE_INPUT_MISSING"
    counts = {"NFL_TARGET_EVENT_FEATURE_MISMATCH": 1, "NFL_CAPTURE_INPUT_MISSING": 1}
    result = status_from_counts(2, 0, 0, 2, hold_reason_counts=counts)
    assert result["status"] == "EVIDENCE_ALL_HELD"
    assert result["run_ok"] is False
    assert result["evidence_hold_reason_counts"] == {
        "NFL_CAPTURE_INPUT_MISSING": 1, "NFL_TARGET_EVENT_FEATURE_MISMATCH": 1
    }
    assert "password" not in repr(result) and "SECRET" not in repr(result)
    assert result["can_execute"] is False


def test_hold_reason_counts_cannot_launder_impossible_accounting_or_unknown_codes():
    from runner import status_from_counts
    result = status_from_counts(
        2, 0, 0, 2,
        hold_reason_counts={"NFL_TARGET_EVENT_FEATURE_MISMATCH": 1}
    )
    assert result["status"] == "CAPTURE_ACCOUNTING_INCONSISTENT"
    assert result["run_ok"] is False
    invalid = status_from_counts(
        1, 0, 0, 1,
        hold_reason_counts={"untrusted user raw string": 1}
    )
    assert invalid["status"] == "CAPTURE_ACCOUNTING_INCONSISTENT"
    assert invalid["run_ok"] is False
    assert "untrusted" not in repr(invalid)


def test_actual_all_held_capture_returns_prior_history_reason(monkeypatch):
    import runner
    import sys
    from types import SimpleNamespace
    now = datetime(2026, 11, 18, 12, 17, tzinfo=timezone.utc)
    old = {
        "season": "2026", "gameday": "2026-11-15",
        "home_team": "HOU", "away_team": "IND",
        "home_score": "10", "away_score": "7",
    }
    source_rows = [old, thursday()]
    snapshot = {"fetched_at": "2026-11-18T11:17:00Z",
                "content_sha256": "a" * 64}
    monkeypatch.setattr(runner, "_schedule", lambda db: (snapshot, source_rows))
    monkeypatch.setattr(runner, "_training_inputs", lambda db: [])
    monkeypatch.setattr(
        runner, "fit_research_challenger",
        lambda rows: {"artifact_sha256": "b" * 64},
    )
    monkeypatch.setattr(runner, "load_completed_history", lambda db: ([], []))
    feature = {"features": {"home_season_prior_games": 0,
                            "away_season_prior_games": 1}}
    monkeypatch.setitem(
        sys.modules, "nfl_event_prediction_features",
        SimpleNamespace(build_prediction_feature_row=lambda **kwargs: feature),
    )

    class EmptyQuery:
        def table(self, table_name):
            assert table_name == runner.TABLE
            return self

        def select(self, *args):
            return self

        def eq(self, *args):
            return self

        def limit(self, *args):
            return self

        def execute(self):
            return SimpleNamespace(data=[])

    result = runner.run_capture(EmptyQuery(), now=now, dry_run=True)
    assert result["eligible_events"] == 1
    assert result["evidence_held"] == 1
    assert result["status"] == "EVIDENCE_ALL_HELD"
    assert result["evidence_hold_reason_counts"] == {
        "NFL_SEASON_PRIOR_HISTORY_MISMATCH": 1
    }
    assert result["run_ok"] is False
    assert result["can_execute"] is False
