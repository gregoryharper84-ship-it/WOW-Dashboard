"""Offline tests for live-slate public news selection and fail-closed 429 deferral."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

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


def test_rate_limit_is_one_attempt_and_never_claims_recovery(monkeypatch):
    calls = []
    def blocked(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(
            ok=False, code="SOURCE_RATE_LIMITED", status=429,
            observed_at=AT.isoformat(), retry_after_seconds=None, data={})
    monkeypatch.setattr(runner, "fetch_gdelt_news", blocked)
    receipt = runner.discover(AT, sport="basketball_nba")
    assert calls == [1]
    assert receipt["source_status"] == "BLOCKED"
    assert receipt["source_code"] == "SOURCE_RATE_LIMITED"
    assert receipt["attempt_count"] == 1
    assert receipt["attempts"] == [{
        "attempt": 1, "delay_before_seconds": 0,
        "http_status": 429, "source_code": "SOURCE_RATE_LIMITED",
        "observed_at": AT.isoformat(), "retry_after_seconds": None,
    }]
    assert receipt["total_wait_seconds"] == 0
    assert receipt["retry_deferred"] is True
    assert receipt["article_count"] == 0
    assert receipt["model_rescore_verified"] is False
    assert receipt["can_execute"] is False


def test_retry_after_is_reported_but_never_slept_on(monkeypatch):
    calls = []
    def blocked(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(ok=False, code="SOURCE_RATE_LIMITED", status=429,
                               retry_after_seconds=1000,
                               observed_at=AT.isoformat(), data={})
    monkeypatch.setattr(runner, "fetch_gdelt_news", blocked)
    receipt = runner.discover(AT, sport="icehockey_nhl")
    assert calls == [1]
    assert receipt["source_code"] == "SOURCE_RATE_LIMITED"
    assert receipt["attempt_count"] == 1
    assert receipt["retry_after_seconds"] == 1000
    assert receipt["attempts"][0]["retry_after_seconds"] == 1000
    assert receipt["total_wait_seconds"] == 0
    assert receipt["retry_policy"]["same_run_retry"] is False
    assert receipt["retry_policy"]["recovery_verified"] is False
    assert receipt["can_execute"] is False


def test_successful_one_request_never_grants_model_authority(monkeypatch):
    calls = []
    def accepted(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(
            ok=True, code="SOURCE_FETCH_OK", status=200, retry_after_seconds=None,
            observed_at=AT.isoformat(), data={"articles": [
                {"title": "Discovery metadata", "evidence_only": True}]})
    monkeypatch.setattr(runner, "fetch_gdelt_news", accepted)
    receipt = runner.discover(AT, sport="icehockey_nhl")
    assert calls == [1]
    assert receipt["source_status"] == "OK"
    assert receipt["article_count"] == 1
    assert receipt["attempt_count"] == 1
    assert receipt["retry_deferred"] is False
    assert receipt["prediction_authority"] is False
    assert receipt["historical_pregame_eligible"] is False
    assert receipt["model_feature_eligible"] is False


def test_timeout_preserves_source_failure_and_does_not_retry(monkeypatch):
    calls = []
    def timed_out(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(
            ok=False, code="SOURCE_TIMEOUT", status=None,
            observed_at=AT.isoformat(), data={})
    monkeypatch.setattr(runner, "fetch_gdelt_news", timed_out)
    receipt = runner.discover(AT, sport="soccer")
    assert calls == [1]
    assert receipt["source_code"] == "SOURCE_TIMEOUT"
    assert receipt["source_status"] == "BLOCKED"
    assert receipt["retry_deferred"] is False
    assert receipt["article_count"] == 0
    assert receipt["can_execute"] is False


def test_scout_workflows_keep_nonblocking_acquisition_and_audit():
    root = Path(__file__).resolve().parents[2] / ".github" / "workflows"
    nightly = (root / "wow-v17-nightly-multiscout.yml").read_text()
    source = (root / "wow-v17-scout-source-refresh.yml").read_text()
    assert "--candidate-manifest" in nightly
    assert nightly.count("public-gdelt-receipt.json") >= 4
    assert "Report same-run news acquisition health (nonblocking)" in nightly
    assert "Report public news acquisition health (nonblocking)" in source
    assert "continue-on-error: true" in nightly
    assert "continue-on-error: true" in source
