from __future__ import annotations

import v17.posthog_observability as obs
from v17.posthog_observability import (
    capture_engineering_incident,
    engineering_failure_fingerprint,
    engineering_incident_properties,
)


def test_engineering_fingerprint_is_stable() -> None:
    left = {"sport": "NFL", "market_family": "ML", "scorer_stage": "hydrate"}
    right = {"scorer_stage": "hydrate", "market_family": "ML", "sport": "NFL"}
    assert engineering_failure_fingerprint(left) == engineering_failure_fingerprint(right)


def test_engineering_fingerprint_separates_stages() -> None:
    hydrate = {"sport": "NFL", "market_family": "ML", "scorer_stage": "hydrate"}
    score = {"sport": "NFL", "market_family": "ML", "scorer_stage": "score"}
    assert engineering_failure_fingerprint(hydrate) != engineering_failure_fingerprint(score)


def test_incident_properties_allowlist_and_keep_fingerprint_stable() -> None:
    left = {
        "sport": "NFL",
        "market_family": "ML",
        "scorer_stage": "hydrate",
        "terminal_code": "PROVIDER_REQUEST_FAILED",
        "run_id": "run-one",
        "authorization": "Bearer secret",
        "request_body": {"probability": 0.91},
        "exception_message": "token=secret",
    }
    right = dict(left, run_id="run-two", authorization="different")
    first = engineering_incident_properties(left)
    second = engineering_incident_properties(right)

    assert first["run_id"] == "run-one"
    assert second["run_id"] == "run-two"
    assert first["wow_failure_fingerprint"] == second["wow_failure_fingerprint"]
    assert first["can_execute"] is False
    assert first["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert "authorization" not in first
    assert "request_body" not in first
    assert "exception_message" not in first


def test_capture_is_fail_open_when_posthog_is_unconfigured(monkeypatch) -> None:
    monkeypatch.setattr(obs, "_POSTHOG_CLIENT", None)
    receipt = capture_engineering_incident(
        {"sport": "MLB", "market_family": "ML", "scorer_stage": "discovery"}
    )
    assert receipt["status"] == "DISABLED_NOT_CONFIGURED"
    assert receipt["captured"] is False
    assert receipt["can_execute"] is False


def test_capture_sends_only_safe_properties(monkeypatch) -> None:
    captured = {}

    class FakePosthog:
        def capture(self, event, distinct_id, properties):
            captured.update(
                {"event": event, "distinct_id": distinct_id, "properties": properties}
            )

    monkeypatch.setattr(obs, "_POSTHOG_CLIENT", FakePosthog())
    receipt = capture_engineering_incident(
        {
            "sport": "WNBA",
            "market_family": "ML",
            "scorer_stage": "acquisition",
            "terminal_code": "PROVIDER_RATE_LIMITED",
            "run_id": "run-123",
            "authorization": "Bearer never-send",
            "raw_provider_body": "never-send",
        }
    )

    assert receipt["status"] == "CAPTURED"
    assert receipt["captured"] is True
    assert captured["event"] == "wow engineering incident"
    assert captured["properties"]["run_id"] == "run-123"
    assert captured["properties"]["can_execute"] is False
    assert "authorization" not in captured["properties"]
    assert "raw_provider_body" not in captured["properties"]
