"""Offline tests for live-slate public news selection and bounded 429 recovery."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from v17.news_active_slate import select_active_slate
from v17 import public_gdelt_discovery as runner

AT = datetime(2026, 11, 18, 18, 0, tzinfo=timezone.utc)
SUPPORTED = tuple(key for key, _ in runner.SPORT_QUERIES)


def _row(sport, event_id, hours=6):
    return {
        "sport_key": sport,
        "official_event_id": event_id,
        "event_start_utc": (AT + timedelta(hours=hours)).isoformat(),
    }


def test_slate_selection_uses_only_future_identified_events_and_deduplicates():
    nba = _row("basketball_nba", "NBA-1")
    nhl = _row("icehockey_nhl", "NHL-1")
    slate = {"model_handoff": {
        "team_event_candidates": [nba, nhl, _row("soccer", "SOCCER-OLD", -2)],
        "prop_candidates": [nba, _row("basketball_nba", "NBA-2"),
                            _row("tennis", "TENNIS-DISTANT", 50),
                            {"sport_key": "basketball_ncaab", "official_event_id": "X"}],
    }}
    selected, counts = select_active_slate(slate, AT, SUPPORTED)
    assert selected in {"basketball_nba", "icehockey_nhl"}
    assert counts == {"basketball_nba": 2, "icehockey_nhl": 1}
    assert "soccer" not in counts


def test_slate_selection_rejects_naive_and_unidentified_events():
    payload = {"model_handoff": {
        "team_event_candidates": [
            {"sport_key": "basketball_nba", "official_event_id": "NBA-2",
             "event_start_utc": "2026-11-18T20:00:00"},
            {"sport_key": "icehockey_nhl",
             "event_start_utc": (AT + timedelta(hours=2)).isoformat()},
            _row("basketball_ncaab", "OLD", -1),
        ],
        "prop_candidates": [],
    }}
    assert select_active_slate(payload, AT, SUPPORTED) == (None, {})


def test_slate_single_sport_and_daily_rotation():
    one = {"model_handoff": {
        "team_event_candidates": [_row("basketball_nba", "NBA-A")],
        "prop_candidates": []}}
    assert select_active_slate(one, AT, SUPPORTED)[0] == "basketball_nba"
    with_three = {"model_handoff": {
        "team_event_candidates": [
            _row("basketball_nba", "NBA-A", 30),
            _row("icehockey_nhl", "NHL-A", 30),
            _row("basketball_ncaab", "NCAAB-A", 30)],
        "prop_candidates": []}}
    # Advancing one calendar day changes which active sport is served.
    first = select_active_slate(with_three, AT, SUPPORTED)[0]
    second = select_active_slate(with_three, AT + timedelta(days=1), SUPPORTED)[0]
    assert first != second


def test_bounded_429_ladder_never_converts_persistent_failure_to_ok(monkeypatch):
    calls, sleeps = [], []
    def blocked(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(
            ok=False, code="SOURCE_RATE_LIMITED",
            observed_at=AT.isoformat(), data={"articles": []})
    monkeypatch.setattr(runner, "fetch_gdelt_news", blocked)
    monkeypatch.setattr(runner.time, "sleep", sleeps.append)
    receipt = runner.discover(AT, sport="basketball_nba")
    assert len(calls) == 2 and sleeps == [900]
    assert receipt["source_status"] == "BLOCKED"
    assert receipt["source_code"] == "SOURCE_RATE_LIMITED"
    assert receipt["attempt_count"] == 2
    assert receipt["total_wait_seconds"] == 900
    assert receipt["article_count"] == 0
    assert receipt["model_rescore_verified"] is False
    assert receipt["can_execute"] is False


def test_429_recovers_after_one_paced_retry_without_predictive_authority(monkeypatch):
    calls, sleeps = [], []
    def fetch(*args, **kwargs):
        calls.append(1)
        ok = len(calls) == 2
        return SimpleNamespace(
            ok=ok, code="SOURCE_FETCH_OK" if ok else "SOURCE_RATE_LIMITED",
            observed_at=AT.isoformat(),
            data={"articles": [{"title": "discovery metadata"}]} if ok else {})
    monkeypatch.setattr(runner, "fetch_gdelt_news", fetch)
    monkeypatch.setattr(runner.time, "sleep", sleeps.append)
    receipt = runner.discover(AT, sport="icehockey_nhl")
    assert len(calls) == 2 and sleeps == [900]
    assert receipt["source_status"] == "OK"
    assert receipt["article_count"] == 1
    assert receipt["attempt_count"] == 2
    assert receipt["prediction_authority"] is False
    assert receipt["historical_pregame_eligible"] is False


def test_429_honours_retry_after_and_stays_bounded(monkeypatch):
    calls, sleeps = [], []
    def fetch(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(ok=False, code="SOURCE_RATE_LIMITED", status=429,
                               retry_after_seconds=1000, observed_at=AT.isoformat(),
                               data={})
    monkeypatch.setattr(runner, "fetch_gdelt_news", fetch)
    monkeypatch.setattr(runner.time, "sleep", sleeps.append)
    receipt = runner.discover(AT, sport="icehockey_nhl")
    assert len(calls) == 2
    assert sleeps == [1000]
    assert receipt["source_status"] == "BLOCKED"
    assert receipt["source_code"] == "SOURCE_RATE_LIMITED"
    assert receipt["attempt_count"] == 2
    assert receipt["total_wait_seconds"] == 1000
    assert [a["delay_before_seconds"] for a in receipt["attempts"]] == [0, 1000]
    assert receipt["retry_policy"]["measured_block_floor_seconds"] >= 735
    assert receipt["can_execute"] is False


def test_retry_ladder_is_paced_in_minutes_and_bounded(monkeypatch):
    sleeps = []
    monkeypatch.setattr(runner, "fetch_gdelt_news",
                        lambda *a, **k: SimpleNamespace(
                            ok=False, code="SOURCE_RATE_LIMITED", status=429,
                            retry_after_seconds=None, observed_at=AT.isoformat(), data={}))
    monkeypatch.setattr(runner.time, "sleep", sleeps.append)
    receipt = runner.discover(AT, sport="icehockey_nhl")
    assert sleeps == list(runner.RETRY_DELAYS_SECONDS)
    assert sum(sleeps) <= runner.MAX_TOTAL_WAIT_SECONDS
    assert min(sleeps) >= runner.RETRY_MEASUREMENT["measured_block_floor_seconds"]
    assert receipt["attempt_count"] == 1 + len(runner.RETRY_DELAYS_SECONDS)
    assert receipt["retry_policy"]["ladder_seconds"] == list(runner.RETRY_DELAYS_SECONDS)
    assert receipt["retry_policy"]["configured_by"] == "default"


def test_retry_wait_defaults_to_the_measured_floor_when_unconfigured():
    assert runner.resolve_retry_delays(None, {}) == ((900,), "default")


def test_retry_wait_env_var_can_shorten_or_disable_the_wait():
    assert runner.resolve_retry_delays(None, {runner.RETRY_WAIT_ENV: "0"}) == (
        (), f"env:{runner.RETRY_WAIT_ENV}")
    assert runner.resolve_retry_delays(None, {runner.RETRY_WAIT_ENV: "90,120"}) == (
        (90, 120), f"env:{runner.RETRY_WAIT_ENV}")


def test_retry_wait_cli_flag_wins_over_env_var():
    env = {runner.RETRY_WAIT_ENV: "0"}
    assert runner.resolve_retry_delays("60", env) == ((60,), "cli_flag")


# Only "0" disables retrying; word aliases are rejected rather than silently
# accepted so a typo cannot look like a deliberate disable.
@pytest.mark.parametrize("spec", ["", "abc", "-30", "0,60", "900,900", "1201",
                                  "off", "skip"])
def test_invalid_retry_wait_configuration_is_rejected(spec):
    with pytest.raises(ValueError):
        runner.parse_retry_delays(spec)


def test_disabled_retry_still_returns_an_honest_blocked_receipt(monkeypatch):
    calls, sleeps = [], []
    def blocked(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(ok=False, code="SOURCE_RATE_LIMITED", status=429,
                               retry_after_seconds=None, observed_at=AT.isoformat(),
                               data={"articles": []})
    monkeypatch.setattr(runner, "fetch_gdelt_news", blocked)
    monkeypatch.setattr(runner.time, "sleep", sleeps.append)
    receipt = runner.discover(AT, sport="basketball_nba", retry_delays=(),
                              retry_configured_by=f"env:{runner.RETRY_WAIT_ENV}")
    assert len(calls) == 1 and sleeps == []
    assert receipt["source_status"] == "BLOCKED"
    assert receipt["source_code"] == "SOURCE_RATE_LIMITED"
    assert receipt["attempt_count"] == 1
    assert receipt["total_wait_seconds"] == 0
    assert [a["delay_before_seconds"] for a in receipt["attempts"]] == [0]
    assert receipt["retry_policy"]["retries_disabled"] is True
    assert receipt["retry_policy"]["ladder_seconds"] == []
    assert receipt["retry_policy"]["configured_by"] == f"env:{runner.RETRY_WAIT_ENV}"
    assert receipt["can_execute"] is False


def test_shortened_ladder_is_what_actually_runs(monkeypatch):
    sleeps = []
    monkeypatch.setattr(runner, "fetch_gdelt_news",
                        lambda *a, **k: SimpleNamespace(
                            ok=False, code="SOURCE_RATE_LIMITED", status=429,
                            retry_after_seconds=None, observed_at=AT.isoformat(), data={}))
    monkeypatch.setattr(runner.time, "sleep", sleeps.append)
    receipt = runner.discover(AT, sport="basketball_nba", retry_delays=(45, 60),
                              retry_configured_by="cli_flag")
    assert sleeps == [45, 60]
    assert receipt["attempt_count"] == 3
    assert receipt["total_wait_seconds"] == 105
    assert receipt["retry_policy"]["ladder_seconds"] == [45, 60]
    assert receipt["source_status"] == "BLOCKED"


def test_cli_retry_flag_is_wired_into_the_run(monkeypatch, tmp_path):
    seen = {}
    def fake_discover(at, *, sport=None, client=None, retry_delays=None,
                      retry_configured_by="default"):
        seen["retry_delays"] = retry_delays
        seen["configured_by"] = retry_configured_by
        return {
            "source_status": "BLOCKED", "source_code": "SOURCE_RATE_LIMITED",
            "sport_key": sport, "articles": [], "article_count": 0,
            "attempt_count": 1, "total_wait_seconds": 0.0,
            "retry_policy": runner.build_retry_policy(
                retry_delays, retry_configured_by),
        }
    monkeypatch.setattr(runner, "discover", fake_discover)
    monkeypatch.setattr(sys, "argv", [
        "public_gdelt_discovery.py", "--sport", "basketball_nba",
        "--retry-wait-seconds", "0", "--output", str(tmp_path / "receipt.json")])
    assert runner.main() == 0
    assert seen == {"retry_delays": (), "configured_by": "cli_flag"}
    assert (tmp_path / "receipt.json").exists()


def test_scheduled_workflows_skip_the_long_wait_and_manual_runs_keep_it():
    root = Path(__file__).resolve().parents[2] / ".github" / "workflows"
    nightly = (root / "wow-v17-nightly-multiscout.yml").read_text()
    source = (root / "wow-v17-scout-source-refresh.yml").read_text()
    for raw in (nightly, source):
        assert f"{runner.RETRY_WAIT_ENV}:" in raw
    # Manual dispatch is the only trigger that pays the long wait; every
    # automated trigger (schedule, push, workflow_run) falls back to 0.
    assert "github.event_name == 'workflow_dispatch' && '900' || '0'" in source
    assert "github.event_name == 'workflow_dispatch' && '900' || '0'" in nightly
    assert "workflow_run:" in nightly and "push:" in nightly
    assert "--candidate-manifest" in nightly
    assert nightly.count("public-gdelt-receipt.json") >= 4
    assert "Report same-run news acquisition health (nonblocking)" in nightly
    assert "Report public news acquisition health (nonblocking)" in source
    assert "continue-on-error: true" in nightly
    assert "continue-on-error: true" in source
