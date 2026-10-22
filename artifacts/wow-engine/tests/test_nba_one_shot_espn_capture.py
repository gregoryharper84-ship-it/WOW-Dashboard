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

def payload(state="pre", dt="2026-10-23T00:00:00Z"):
    return json.dumps({"events": [{"id": "401001", "date": dt,
                       "status": {"type": {"state": state}}}]}).encode()

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
    event = {"id": "1", "date": "2026-10-23T00:00:00Z",
             "status": {"type": {"state": "pre"}}}
    with pytest.raises(capture.CaptureFailure, match="SOURCE_EVENT_IDENTITY_DUPLICATE"):
        capture.classify_events(json.dumps({"events": [event, event]}).encode(), now=NOW)

def test_invalid_target_date():
    with pytest.raises(capture.CaptureFailure, match="BAD_TARGET_DATE"):
        capture.source_url("../../today")
