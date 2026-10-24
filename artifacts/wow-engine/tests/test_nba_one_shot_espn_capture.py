"""Unit tests only: mock ESPN responses, never call the network."""
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
import hashlib
import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock
import pytest

P = Path(__file__).resolve().parents[1] / "scripts/nba_one_shot_espn_capture.py"
spec = importlib.util.spec_from_file_location("nba_capture", P)
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)
NOW = datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc)
TARGET = "2026-10-22"

def _event(state="pre", dt="2026-10-23T00:00:00Z"):
    return {
        "id": "401001", "date": dt,
        "status": {"type": {"state": state, "completed": False}},
        "season": {"year": 2027, "type": 2, "slug": "regular-season"},
        "competitions": [{"competitors": [
            {"homeAway": "home", "team": {"id": "4", "displayName": "Chicago Bulls"}},
            {"homeAway": "away", "team": {"id": "15", "displayName": "Milwaukee Bucks"}},
        ]}],
    }


def payload(state="pre", dt="2026-10-23T00:00:00Z"):
    return json.dumps({"events": [_event(state, dt)]}).encode()

class Response:
    def __init__(self, raw=None, date=None, url=None, headers=None, status=200):
        self.raw = payload() if raw is None else raw
        self.url = url or capture.source_url(TARGET)
        self.status = status
        self.headers = headers if headers is not None else {
            "Date": date or format_datetime(NOW, usegmt=True),
            "Content-Type": "application/json"}
    def geturl(self): return self.url
    def getcode(self): return self.status
    def read(self, n): return self.raw[:n]
    def __enter__(self): return self
    def __exit__(self, *_): return False

class Opener:
    def __init__(self, response): self.response = response
    def open(self, req, timeout): return self.response

def test_capture_and_verified_hash_and_event_replay(tmp_path):
    got = capture.capture(TARGET, tmp_path, now=NOW, opener=Opener(Response()))
    assert got["raw_sha256"] == hashlib.sha256(payload()).hexdigest()
    assert len(got["events_pregame_eligible"]) == 1
    assert got["events_pregame_eligible"][0]["home"] == {"team_id": "4", "name": "Chicago Bulls"}
    assert got["events_pregame_eligible"][0]["away"] == {"team_id": "15", "name": "Milwaukee Bucks"}
    assert got["can_execute"] is False and got["probability_publishable"] is False
    assert capture.verify(tmp_path, TARGET)["status"] == "RAW_CAPTURE_HASH_AND_EVENT_REPLAY_PASS"

def test_verify_detects_raw_tampering(tmp_path):
    capture.capture(TARGET, tmp_path, now=NOW, opener=Opener(Response()))
    (tmp_path / f"nba_{TARGET}_espn_raw.json").write_bytes(b"{}")
    with pytest.raises(capture.CaptureFailure, match="CAPTURE_RAW_HASH_MISMATCH"):
        capture.verify(tmp_path, TARGET)

def test_duplicate_capture_refused(tmp_path):
    capture.capture(TARGET, tmp_path, now=NOW, opener=Opener(Response()))
    with pytest.raises(capture.CaptureFailure, match="CAPTURE_OUTPUT_ALREADY_EXISTS"):
        capture.capture(TARGET, tmp_path, now=NOW, opener=Opener(Response()))

def test_missing_date_header_refused():
    with pytest.raises(capture.CaptureFailure, match="SOURCE_HTTP_DATE_MISSING"):
        capture.fetch_source(TARGET, now=NOW,
            opener=Opener(Response(headers={"Content-Type": "application/json"})))

def test_stale_http_date_refused():
    with pytest.raises(capture.CaptureFailure, match="SOURCE_HTTP_DATE_STALE_OR_SKEWED"):
        capture.fetch_source(TARGET, now=NOW,
            opener=Opener(Response(date=format_datetime(NOW-timedelta(minutes=11), usegmt=True))))

def test_redirect_location_refused_even_with_untrusted_custom_opener():
    with pytest.raises(capture.CaptureFailure, match="SOURCE_REDIRECT_REFUSED"):
        capture.fetch_source(TARGET, now=NOW, opener=Opener(Response(url="https://evil.example/")))

def test_actual_redirect_handler_refuses():
    with pytest.raises(capture.CaptureFailure, match="SOURCE_REDIRECT_REFUSED"):
        capture.RefuseRedirect().redirect_request(Mock(), None, 302, "Moved", {}, "https://evil.example")

def test_bad_content_type_refused():
    with pytest.raises(capture.CaptureFailure, match="SOURCE_CONTENT_TYPE_INVALID"):
        capture.fetch_source(TARGET, now=NOW, opener=Opener(Response(headers={
            "Date": format_datetime(NOW, usegmt=True), "Content-Type": "text/html"})))

def test_started_game_never_pregame():
    info = capture.classify_events(payload(state="in"), now=NOW)
    assert info["events_pregame_eligible"] == []
    assert info["events_skipped"][0]["reason"] == "EVENT_NOT_FUTURE_PREGAME"

def test_min_lead_required_and_validated():
    info = capture.classify_events(payload(dt="2026-10-22T18:09:59Z"), now=NOW)
    assert info["events_pregame_eligible"] == []
    with pytest.raises(capture.CaptureFailure, match="BAD_MIN_LEAD"):
        capture.classify_events(payload(), now=NOW, min_lead_seconds=599)

def test_malformed_source_shape_fails_not_a_pick():
    with pytest.raises(capture.CaptureFailure, match="SOURCE_EVENTS_MALFORMED"):
        capture.classify_events(b"{}", now=NOW)

def test_duplicate_event_id_fails():
    event = _event()
    event["id"] = "1"
    with pytest.raises(capture.CaptureFailure, match="SOURCE_EVENT_IDENTITY_DUPLICATE"):
        capture.classify_events(json.dumps({"events": [event, event]}).encode(), now=NOW)

def test_invalid_target_date():
    with pytest.raises(capture.CaptureFailure, match="BAD_TARGET_DATE"):
        capture.source_url("../../today")


@pytest.mark.parametrize("fault", [
    "no_competitions", "empty_competitors", "one_competitor", "duplicate_home",
    "duplicate_team_id", "missing_team_id", "missing_display_name", "unknown_side",
])
def test_malformed_espn_home_away_identity_fails_closed(fault):
    event = _event()
    c = event["competitions"][0]["competitors"]
    if fault == "no_competitions":
        del event["competitions"]
    elif fault == "empty_competitors":
        event["competitions"][0]["competitors"] = []
    elif fault == "one_competitor":
        event["competitions"][0]["competitors"] = c[:1]
    elif fault == "duplicate_home":
        c[1]["homeAway"] = "home"
    elif fault == "duplicate_team_id":
        c[1]["team"]["id"] = c[0]["team"]["id"]
    elif fault == "missing_team_id":
        del c[1]["team"]["id"]
    elif fault == "missing_display_name":
        c[1]["team"]["displayName"] = ""
    elif fault == "unknown_side":
        c[1]["homeAway"] = "neutral"
    with pytest.raises(capture.CaptureFailure, match="SOURCE_TEAM_IDENTITY_MALFORMED"):
        capture.classify_events(json.dumps({"events": [event]}).encode(), now=NOW)


def test_receipt_team_identity_tampering_rejected_even_if_raw_hash_unchanged(tmp_path):
    got = capture.capture(TARGET, tmp_path, now=NOW, opener=Opener(Response()))
    path = tmp_path / f"nba_{TARGET}_capture_receipt.json"
    receipt = json.loads(path.read_text())
    receipt["events_pregame_eligible"][0]["home"]["team_id"] = "999"
    path.write_text(json.dumps(receipt))
    assert got["events_pregame_eligible"][0]["home"]["team_id"] == "4"
    with pytest.raises(capture.CaptureFailure, match="CAPTURE_RECEIPT_EVENT_MISMATCH"):
        capture.verify(tmp_path, TARGET)


@pytest.mark.parametrize(("change", "expected"), [
    ("completed_contradiction", "EVENT_NOT_FUTURE_PREGAME"),
    ("wrong_date", "EVENT_WRONG_NBA_LOCAL_DATE"),
    ("preseason", "EVENT_REGIME_NOT_REGULAR_SEASON"),
    ("postseason", "EVENT_REGIME_NOT_REGULAR_SEASON"),
    ("short_lead", "MIN_LEAD_NOT_MET"),
])
def test_source_research_classification_rejects_off_slate_nonpregame_regimes(change, expected):
    event = _event()
    if change == "completed_contradiction":
        event["status"]["type"]["completed"] = True
    elif change == "wrong_date":
        # 04:30 UTC is after midnight on Oct23 in NBA's Eastern date.
        event["date"] = "2026-10-23T04:30:00Z"
    elif change == "preseason":
        event["season"] = {"year": 2027, "type": 1, "slug": "preseason"}
    elif change == "postseason":
        event["season"] = {"year": 2027, "type": 3, "slug": "post-season"}
    elif change == "short_lead":
        event["date"] = "2026-10-22T18:05:00Z"
    info = capture.classify_events(
        json.dumps({"events": [event]}).encode(), now=NOW, target_date=TARGET,
    )
    assert info["events_pregame_eligible"] == []
    assert info["events_skipped"][0]["reason"] == expected


@pytest.mark.parametrize(("field", "value", "code"), [
    ("status", {"type": {"state": "pre"}}, "SOURCE_EVENT_STATUS_MALFORMED"),
    ("status", {"type": {"completed": False}}, "SOURCE_EVENT_STATUS_MALFORMED"),
    ("season", None, "SOURCE_SEASON_IDENTITY_MALFORMED"),
    ("season", [], "SOURCE_SEASON_IDENTITY_MALFORMED"),
])
def test_missing_source_status_or_season_never_passes_to_identity_receipt(field, value, code):
    event = _event()
    event[field] = value
    with pytest.raises(capture.CaptureFailure, match=code):
        capture.classify_events(
            json.dumps({"events": [event]}).encode(), now=NOW, target_date=TARGET,
        )


def test_local_calendar_day_is_bound_in_original_and_replayed_receipt(tmp_path):
    manifest = capture.capture(TARGET, tmp_path, now=NOW, opener=Opener(Response()))
    assert len(manifest["events_pregame_eligible"]) == 1
    assert capture.verify(tmp_path, TARGET)["status"] == "RAW_CAPTURE_HASH_AND_EVENT_REPLAY_PASS"
    manifest_path = tmp_path / f"nba_{TARGET}_capture_receipt.json"
    stored = json.loads(manifest_path.read_text())
    stored["events_pregame_eligible"] = []
    manifest_path.write_text(json.dumps(stored))
    with pytest.raises(capture.CaptureFailure, match="CAPTURE_RECEIPT_EVENT_MISMATCH"):
        capture.verify(tmp_path, TARGET)
