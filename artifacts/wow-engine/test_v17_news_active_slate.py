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


def _gkg_blocked(*args, **kwargs):
    return SimpleNamespace(
        ok=False, code="SOURCE_NO_VALID_NEWS", status=200,
        observed_at=AT.isoformat(), retry_after_seconds=None, data={})


def test_rate_limit_is_one_doc_attempt_plus_gkg_fallback_without_claiming_recovery(
        monkeypatch):
    doc_calls = []
    gkg_calls = []

    def blocked(*args, **kwargs):
        doc_calls.append(1)
        return SimpleNamespace(
            ok=False, code="SOURCE_RATE_LIMITED", status=429,
            observed_at=AT.isoformat(), retry_after_seconds=None, data={})

    def gkg_fail(*args, **kwargs):
        gkg_calls.append(1)
        return _gkg_blocked()

    monkeypatch.setattr(runner, "fetch_gdelt_news", blocked)
    monkeypatch.setattr(runner, "fetch_gdelt_gkg_news", gkg_fail)
    receipt = runner.discover(AT, sport="basketball_nba")
    assert doc_calls == [1]
    assert gkg_calls == [1]
    assert receipt["source_status"] == "BLOCKED"
    assert receipt["source_code"] == "SOURCE_RATE_LIMITED"
    assert receipt["attempt_count"] == 2
    assert receipt["attempts"][0]["channel"] == "GDELT_DOC"
    assert receipt["attempts"][1]["channel"] == "GDELT_GKG_DUMP"
    assert receipt["total_wait_seconds"] == 0
    assert receipt["retry_deferred"] is True
    assert receipt["fallback_status"] == "SOURCE_NO_VALID_NEWS"
    assert receipt["article_count"] == 0
    assert receipt["model_rescore_verified"] is False
    assert receipt["can_execute"] is False


def test_retry_after_is_reported_but_never_slept_on(monkeypatch):
    def blocked(*args, **kwargs):
        return SimpleNamespace(ok=False, code="SOURCE_RATE_LIMITED", status=429,
                               retry_after_seconds=1000,
                               observed_at=AT.isoformat(), data={})
    monkeypatch.setattr(runner, "fetch_gdelt_news", blocked)
    monkeypatch.setattr(runner, "fetch_gdelt_gkg_news", _gkg_blocked)
    receipt = runner.discover(AT, sport="icehockey_nhl")
    assert receipt["source_code"] == "SOURCE_RATE_LIMITED"
    assert receipt["attempt_count"] == 2
    assert receipt["retry_after_seconds"] == 1000
    assert receipt["attempts"][0]["retry_after_seconds"] == 1000
    assert receipt["total_wait_seconds"] == 0
    assert receipt["retry_policy"]["same_run_doc_retry"] is False
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
    monkeypatch.setattr(
        runner, "fetch_gdelt_gkg_news",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("gkg unused")))
    receipt = runner.discover(AT, sport="icehockey_nhl")
    assert calls == [1]
    assert receipt["source_status"] == "OK"
    assert receipt["article_count"] == 1
    assert receipt["attempt_count"] == 1
    assert receipt["acquisition_path"] == "GDELT_DOC"
    assert receipt["fallback_status"] == "NOT_ATTEMPTED"
    assert receipt["retry_deferred"] is False
    assert receipt["prediction_authority"] is False
    assert receipt["historical_pregame_eligible"] is False
    assert receipt["model_feature_eligible"] is False


def test_timeout_preserves_source_failure_and_tries_gkg_once(monkeypatch):
    doc_calls = []
    gkg_calls = []

    def timed_out(*args, **kwargs):
        doc_calls.append(1)
        return SimpleNamespace(
            ok=False, code="SOURCE_TIMEOUT", status=None,
            observed_at=AT.isoformat(), retry_after_seconds=None, data={})

    def gkg_fail(*args, **kwargs):
        gkg_calls.append(1)
        return _gkg_blocked()

    monkeypatch.setattr(runner, "fetch_gdelt_news", timed_out)
    monkeypatch.setattr(runner, "fetch_gdelt_gkg_news", gkg_fail)
    receipt = runner.discover(AT, sport="soccer")
    assert doc_calls == [1]
    assert gkg_calls == [1]
    assert receipt["source_code"] == "SOURCE_TIMEOUT"
    assert receipt["source_status"] == "BLOCKED"
    assert receipt["attempt_count"] == 2
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


def test_huge_market_handoff_uses_compact_event_sibling_without_loading_quotes(
    monkeypatch, tmp_path
):
    import json
    import sys
    from v17.nightly_multiscout import compact_news_slate_manifest

    # A sparse >64 MB file stands in for the actual 596 MB handoff.
    # If parsed, the one-byte invalid JSON footer would fail.
    full = tmp_path / "model-handoff.json"
    with full.open("wb") as stream:
        stream.seek(64_000_001)
        stream.write(b"!")
    now = datetime.now(timezone.utc)
    candidate = {
        "sport_key": "basketball_ncaab",
        "official_event_id": "provider-1",
        "provider_event_id": "provider-1",
        "official_event_identity_verified": False,
        "commence_time": (now + timedelta(hours=6)).isoformat(),
        "market_evidence": [{"bloat": "unused"}],
    }
    payload = {"model_handoff": {
        "team_event_candidates": [candidate],
        "prop_candidates": [dict(candidate)],
    }}
    compact = compact_news_slate_manifest(payload)
    assert compact["candidate_rows_total"] == 2
    assert compact["model_handoff"]["team_event_candidates"][0]["official_event_id"] is None
    assert compact["model_handoff"]["team_event_candidates"][0]["provider_event_id"] == "provider-1"
    assert "market_evidence" not in str(compact)
    (tmp_path / "news-active-slate.json").write_text(json.dumps(compact))
    calls = []
    def discover_stub(at, *, sport=None):
        calls.append(sport)
        return {
            "source_status": "OK", "source_code": "SOURCE_FETCH_OK",
            "sport_key": sport, "articles": [], "attempt_count": 1,
            "acquisition_path": "GDELT_DOC", "fallback_status": "NOT_ATTEMPTED",
            "retry_deferred": False, "prediction_authority": False,
            "can_execute": False, "model_feature_eligible": False,
        }
    monkeypatch.setattr(runner, "discover", discover_stub)
    dest = tmp_path / "receipt.json"
    monkeypatch.setattr(sys, "argv", [
        "gdelt", "--candidate-manifest", str(full), "--output", str(dest),
    ])
    assert runner.main() == 0
    receipt = json.loads(dest.read_text())
    assert calls == ["basketball_ncaab"]
    assert receipt["selection_policy"] == "ACTIVE_SLATE_ROTATION"
    assert receipt["slate_manifest_status"] == "ACTIVE_SPORT_SELECTED"
    assert receipt["slate_manifest_source"] == "COMPACT_SIBLING"
    assert receipt["slate_active_event_counts"] == {"basketball_ncaab": 1}
    assert receipt["can_execute"] is False


def test_missing_compact_sibling_reports_native_selection_blocker(
    monkeypatch, tmp_path
):
    import json
    import sys
    full = tmp_path / "model-handoff.json"
    with full.open("wb") as stream:
        stream.seek(64_000_001)
        stream.write(b"!")
    calls = []
    def discover_stub(at, *, sport=None):
        calls.append(sport)
        return {"source_status": "BLOCKED", "source_code": "SOURCE_RATE_LIMITED",
                "sport_key": sport, "articles": [], "attempt_count": 1,
                "acquisition_path": "GDELT_DOC",
                "fallback_status": "SOURCE_NO_VALID_NEWS",
                "retry_deferred": True, "prediction_authority": False,
                "can_execute": False}
    monkeypatch.setattr(runner, "discover", discover_stub)
    dest = tmp_path / "receipt.json"
    monkeypatch.setattr(sys, "argv", [
        "gdelt", "--candidate-manifest", str(full), "--output", str(dest),
    ])
    assert runner.main() == 0
    receipt = json.loads(dest.read_text())
    assert calls == [None]  # Preserve active-season rotation when slate missing.
    assert receipt["selection_policy"] == "SLATE_UNAVAILABLE_ACTIVE_SEASON_ROTATION"
    assert receipt["slate_manifest_status"] == "NEWS_COMPACT_MANIFEST_MISSING"
    assert receipt["source_code"] == "SOURCE_RATE_LIMITED"
    assert receipt["attempt_count"] == 1
    assert receipt["can_execute"] is False
