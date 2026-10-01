from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "v17" / "failure_capsule.py"
spec = importlib.util.spec_from_file_location("v17_failure_capsule", MODULE_PATH)
assert spec and spec.loader
failure_capsule = importlib.util.module_from_spec(spec)
spec.loader.exec_module(failure_capsule)


def test_capsule_redacts_secret_like_fields_and_text() -> None:
    capsule = failure_capsule.build_failure_capsule(
        route="/score-team-event",
        sport="NFL",
        market_family="OUTRIGHT_WINNER",
        scorer_stage="MODEL_INVOKE",
        terminal_code="MODEL_SCORER_FAILED",
        exception_type="RemoteProtocolError",
        exception_message="authorization=top-secret bearer abc.def.ghi",
        provider_states={
            "authorization": "Bearer abc.def.ghi",
            "headers": {"X-API-KEY": "super-secret", "content-type": "application/json"},
        },
        stack_boundary=["request failed with Bearer abc.def.ghi"],
    )

    serialized = str(capsule).lower()
    assert "top-secret" not in serialized
    assert "abc.def.ghi" not in serialized
    assert "super-secret" not in serialized
    assert capsule["provider_states"]["authorization"] == failure_capsule.REDACTED
    assert capsule["provider_states"]["headers"]["X-API-KEY"] == failure_capsule.REDACTED
    assert capsule["can_execute"] is False
    assert capsule["terminal_authority"] == "V17_TERMINAL_REDUCER"


def test_fingerprint_is_stable_across_request_ids_and_uuid_noise() -> None:
    first = failure_capsule.build_failure_capsule(
        route="/score-team-event",
        sport="NFL",
        market_family="OUTRIGHT_WINNER",
        scorer_stage="HYDRATION",
        terminal_code="MODEL_INPUTS_INSUFFICIENT",
        exception_type="ValueError",
        exception_message="missing snapshot 123e4567-e89b-12d3-a456-426614174000",
        request_id="request-a",
    )
    second = failure_capsule.build_failure_capsule(
        route="/score-team-event",
        sport="NFL",
        market_family="OUTRIGHT_WINNER",
        scorer_stage="HYDRATION",
        terminal_code="MODEL_INPUTS_INSUFFICIENT",
        exception_type="ValueError",
        exception_message="missing snapshot 223e4567-e89b-12d3-a456-426614174999",
        request_id="request-b",
    )

    assert first["fingerprint"] == second["fingerprint"]


def test_fingerprint_keeps_distinct_failure_stages_separate() -> None:
    common = dict(
        route="/score-team-event",
        sport="NFL",
        market_family="OUTRIGHT_WINNER",
        terminal_code="MODEL_SCORER_FAILED",
        exception_type="RuntimeError",
        exception_message="scorer unavailable",
    )
    hydration = failure_capsule.build_failure_capsule(scorer_stage="HYDRATION", **common)
    scoring = failure_capsule.build_failure_capsule(scorer_stage="MODEL_INVOKE", **common)

    assert hydration["fingerprint"] != scoring["fingerprint"]


def test_duplicate_occurrence_merges_without_losing_identity() -> None:
    first = failure_capsule.build_failure_capsule(
        route="/v17/nfl-pickem-run",
        sport="NFL",
        market_family="PICKEM",
        scorer_stage="MODEL_INVOKE",
        terminal_code="MODEL_SCORER_FAILED",
        exception_type="RemoteProtocolError",
        exception_message="peer disconnected",
        observed_at="2026-10-01T12:00:00+00:00",
    )
    second = failure_capsule.build_failure_capsule(
        route="/v17/nfl-pickem-run",
        sport="NFL",
        market_family="PICKEM",
        scorer_stage="MODEL_INVOKE",
        terminal_code="MODEL_SCORER_FAILED",
        exception_type="RemoteProtocolError",
        exception_message="peer disconnected",
        observed_at="2026-10-01T13:00:00+00:00",
    )

    merged = failure_capsule.merge_occurrence(first, second)

    assert merged["occurrence_count"] == 2
    assert merged["fingerprint"] == first["fingerprint"]
    assert merged["first_observed_at"] == "2026-10-01T12:00:00+00:00"
    assert merged["last_observed_at"] == "2026-10-01T13:00:00+00:00"


def test_distinct_incidents_refuse_merge() -> None:
    first = failure_capsule.build_failure_capsule(
        sport="NFL",
        scorer_stage="HYDRATION",
        terminal_code="MODEL_INPUTS_INSUFFICIENT",
    )
    second = failure_capsule.build_failure_capsule(
        sport="WNBA",
        scorer_stage="HYDRATION",
        terminal_code="MODEL_INPUTS_INSUFFICIENT",
    )

    try:
        failure_capsule.merge_occurrence(first, second)
    except ValueError as exc:
        assert "fingerprints differ" in str(exc)
    else:
        raise AssertionError("distinct incidents must not merge")


def test_capsule_manifest_requires_explicit_deterministic_pytest_targets() -> None:
    blocked = failure_capsule.capsule_manifest_entry(
        failure_capsule.build_failure_capsule(
            sport="NFL",
            scorer_stage="MODEL_INVOKE",
            terminal_code="MODEL_SCORER_FAILED",
        )
    )
    ready = failure_capsule.capsule_manifest_entry(
        failure_capsule.build_failure_capsule(
            sport="NFL",
            scorer_stage="MODEL_INVOKE",
            terminal_code="MODEL_SCORER_FAILED",
            reproduction={
                "description": "seeded scorer failure",
                "pytest": ["tests/test_seeded_scorer_failure.py::test_exact_failure"],
            },
        )
    )

    assert blocked["status"] == "BLOCKED_WITH_EXACT_REASON"
    assert blocked["blocker"] == "FAILURE_CAPSULE_DETERMINISTIC_PYTEST_TARGETS_UNAVAILABLE"
    assert ready["status"] == "REPRODUCTION_READY"
    assert ready["pytest"] == ["tests/test_seeded_scorer_failure.py::test_exact_failure"]
    assert ready["can_execute"] is False
