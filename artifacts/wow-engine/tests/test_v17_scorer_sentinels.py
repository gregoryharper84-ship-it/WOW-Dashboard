from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "v17" / "scorer_sentinels.py"
spec = importlib.util.spec_from_file_location("v17_scorer_sentinels", MODULE_PATH)
assert spec and spec.loader
sentinels = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sentinels)


def test_sentinel_preserves_first_exact_failure_stage() -> None:
    receipt = sentinels.evaluate_sentinel(
        sport="NFL",
        surface="OUTRIGHT_WINNER",
        route="/score-team-event",
        specialist="wow.nfl-game-win-probability-expert",
        stages=[
            sentinels.stage_pass("DISCOVERY", stage_ms=2.0),
            sentinels.stage_pass("IDENTITY", stage_ms=1.0),
            sentinels.stage_blocked("HYDRATION", "NFL_FEATURE_ASSEMBLY_FAILED", stage_ms=12.0),
            sentinels.stage_blocked("MODEL_INVOKE", "MODEL_SCORER_FAILED", stage_ms=1.0),
        ],
    )

    assert receipt["status"] == "SENTINEL_BLOCKED"
    assert receipt["first_failing_stage"] == "HYDRATION"
    assert receipt["blocker"] == "NFL_FEATURE_ASSEMBLY_FAILED"
    assert receipt["can_execute"] is False
    assert receipt["terminal_authority"] == "V17_TERMINAL_REDUCER"


def test_wnba_source_blocker_is_not_rewritten_as_model_unavailable() -> None:
    receipt = sentinels.evaluate_sentinel(
        sport="WNBA",
        surface="SPREAD",
        route="/internal/v17/wnba-spread-forward-auto-canary",
        stages=[
            sentinels.stage_blocked(
                "DISCOVERY",
                "WNBA_SPREAD_CANARY_IDENTITY_SOURCE_UNAVAILABLE",
            ),
            sentinels.stage_not_applicable("MODEL_INVOKE"),
        ],
    )

    assert receipt["first_failing_stage"] == "DISCOVERY"
    assert receipt["blocker"] == "WNBA_SPREAD_CANARY_IDENTITY_SOURCE_UNAVAILABLE"
    assert receipt["blocker"] != "MODEL_UNAVAILABLE"


def test_healthy_sentinel_returns_pass_without_probability_fields() -> None:
    receipt = sentinels.evaluate_sentinel(
        sport="NFL",
        surface="PICKEM",
        route="/v17/nfl-pickem-run",
        stages=[
            sentinels.stage_pass("IDENTITY"),
            sentinels.stage_pass("HYDRATION"),
            sentinels.stage_pass("MODEL_REGISTRY"),
            sentinels.stage_pass("MODEL_INVOKE"),
            sentinels.stage_pass("PERSISTENCE"),
            sentinels.stage_pass("TERMINAL"),
        ],
    )

    assert receipt["status"] == "SENTINEL_PASS"
    assert receipt["first_failing_stage"] is None
    assert receipt["blocker"] is None
    assert "probability" not in receipt
    assert receipt["can_execute"] is False


def test_duplicate_stage_is_rejected() -> None:
    try:
        sentinels.evaluate_sentinel(
            sport="NFL",
            surface="PICKEM",
            route="/v17/nfl-pickem-run",
            stages=[sentinels.stage_pass("HYDRATION"), sentinels.stage_pass("HYDRATION")],
        )
    except ValueError as exc:
        assert "duplicate sentinel stage" in str(exc)
    else:
        raise AssertionError("duplicate stage must fail closed")


def test_blocked_stage_requires_exact_blocker() -> None:
    try:
        sentinels.SentinelStage(stage="MODEL_INVOKE", status="BLOCKED").validate()
    except ValueError as exc:
        assert "requires an exact blocker" in str(exc)
    else:
        raise AssertionError("missing blocker must fail closed")


def test_blocked_receipt_maps_into_failure_capsule_input() -> None:
    receipt = sentinels.evaluate_sentinel(
        sport="NFL",
        surface="OUTRIGHT_WINNER",
        route="/score-team-event",
        git_sha="abc123",
        specialist="wow.nfl-game-win-probability-expert",
        stages=[sentinels.stage_blocked("MODEL_INVOKE", "NFL_FITTED_SCORER_FAILED")],
    )

    capsule_input = sentinels.sentinel_failure_capsule_input(receipt)
    assert capsule_input == {
        "route": "/score-team-event",
        "sport": "NFL",
        "market_family": "OUTRIGHT_WINNER",
        "scorer_stage": "MODEL_INVOKE",
        "terminal_code": "NFL_FITTED_SCORER_FAILED",
        "git_sha": "abc123",
        "specialist": "wow.nfl-game-win-probability-expert",
        "model_version": None,
        "artifact_id": None,
    }


def test_passing_receipt_does_not_create_failure_capsule_input() -> None:
    receipt = sentinels.evaluate_sentinel(
        sport="MLB",
        surface="OUTRIGHT_WINNER",
        route="/score-team-event",
        stages=[sentinels.stage_pass("MODEL_INVOKE")],
    )
    assert sentinels.sentinel_failure_capsule_input(receipt) is None
