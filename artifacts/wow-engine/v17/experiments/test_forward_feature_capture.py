"""Synthetic, no-network regression tests for forward capture."""
from datetime import datetime, timezone, timedelta
import pytest
from forward_feature_capture import build_capture, write_once, CaptureError

NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)


def event():
    return dict(
        sport="MLB", official_event_id="823164", event_status="SCHEDULED",
        historical_reconstruction=False,
        event_start_utc=(NOW + timedelta(hours=3)).isoformat(),
        feature_schema_version="MLB_TEST_V1", model_family="MLB_RESEARCH_ONLY",
        features={"home_prior_win_rate": 0.5},
        source_evidence=[{
            "provider": "OFFICIAL_TEST_SOURCE", "source_ref": "evidence-001",
            "payload_sha256": "a" * 64,
            "observed_at_utc": (NOW-timedelta(minutes=7)).isoformat(),
            "retrieved_at_utc": (NOW-timedelta(minutes=2)).isoformat(),
        }],
    )


def test_capture_digest_and_write_once(tmp_path):
    result = build_capture(event(), observed_now=NOW)
    assert result["status"] == "CAPTURED_REQUIRES_INDEPENDENT_SOURCE_VERIFICATION"
    assert result["can_execute"] is False
    assert len(result["feature_snapshot_sha256"]) == 64
    write_once(tmp_path/"capture.json", result)
    with pytest.raises(FileExistsError):
        write_once(tmp_path/"capture.json", result)


@pytest.mark.parametrize("field,value,code", [
    ("event_status", "LIVE", "EVENT_NOT_VERIFIED_SCHEDULED"),
    ("historical_reconstruction", True, "RETROSPECTIVE_CAPTURE_FORBIDDEN"),
    ("features", {"postgame_score": float("nan")}, "FEATURE_VECTOR_INVALID"),
])
def test_ineligible_capture_rejected(field, value, code):
    request = event()
    request[field] = value
    with pytest.raises(CaptureError, match=code):
        build_capture(request, observed_now=NOW)


def test_capture_after_kickoff_rejected():
    request = event()
    request["event_start_utc"] = (NOW-timedelta(minutes=1)).isoformat()
    with pytest.raises(CaptureError, match="PREGAME_CAPTURE_CLOSED"):
        build_capture(request, observed_now=NOW)


def test_future_source_observation_rejected():
    request = event()
    request["source_evidence"][0]["retrieved_at_utc"] = (NOW+timedelta(seconds=1)).isoformat()
    with pytest.raises(CaptureError, match="SOURCE_CHRONOLOGY_INVALID"):
        build_capture(request, observed_now=NOW)
