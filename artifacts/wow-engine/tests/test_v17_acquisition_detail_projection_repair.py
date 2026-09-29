from __future__ import annotations

import copy

import pytest

from v17.acquisition_detail_projection_repair import (
    normalize_nested_fallback_union_detail,
)


def _attempt(
    path_id: str,
    ordinal: int,
    *,
    path_state: str,
    blocker_code: str | None = None,
    originating_blocker_code: str | None = None,
    upstream_status: int | None = None,
    content_type_class: str | None = None,
    credential_alias: str | None = None,
) -> dict:
    return {
        "ordinal": ordinal,
        "path_id": path_id,
        "path_state": path_state,
        "blocker_code": blocker_code,
        "originating_blocker_code": originating_blocker_code,
        "upstream_status": upstream_status,
        "content_type_class": content_type_class,
        "credential_alias": credential_alias,
    }


def _attempts() -> list[dict]:
    return [
        _attempt(
            "ESPN_SCOREBOARD",
            1,
            path_state="SUCCEEDED_WITH_ROWS",
            upstream_status=200,
            content_type_class="JSON",
        ),
        _attempt(
            "ODDS_PROXY",
            2,
            path_state="FAILED_TYPED",
            blocker_code="ODDS_API_UPSTREAM_HTTP_401",
            upstream_status=401,
            content_type_class="JSON",
            credential_alias="ODDS_API_KEY",
        ),
        _attempt(
            "RUNDOWN",
            3,
            path_state="SUCCEEDED_WITH_ROWS",
            upstream_status=200,
            content_type_class="JSON",
            credential_alias="RUNDOWN_API_KEY",
        ),
    ]


def _union_detail(paths=("ESPN_SCOREBOARD", "ODDS_PROXY", "RUNDOWN")) -> dict:
    attempts = _attempts()
    if paths != ("ESPN_SCOREBOARD", "ODDS_PROXY", "RUNDOWN"):
        attempts = [
            _attempt(
                path,
                index,
                path_state="SUCCEEDED_WITH_ROWS" if index == len(paths) else "FAILED_TYPED",
            )
            for index, path in enumerate(paths, start=1)
        ]
    return {
        "family": "MLB",
        "target_key": "RUNDOWN|1|MLB|REGULAR_SEASON",
        "primary_path_id": "ESPN_SCOREBOARD",
        "primary_path_state": "SUCCEEDED_WITH_ROWS",
        "primary_blocker_code": None,
        "primary_upstream_status": 200,
        "primary_content_type_class": "JSON",
        "primary_provider_alias": None,
        "fallback_path_id": "GOVERNED_FALLBACK_UNION",
        "fallback_path_state": "SUCCEEDED_WITH_ROWS",
        # The aggregate union previously exposed the first downstream fallback's
        # observability here. Durable projection is defined by the last concrete
        # fallback attempt, so these values intentionally contradict the terminal
        # attempt and reproduce the production defect.
        "fallback_blocker_code": "ODDS_API_UPSTREAM_HTTP_401",
        "fallback_upstream_status": 401,
        "fallback_content_type_class": "JSON",
        "fallback_provider_alias": "ODDS_API_KEY",
        "fallback_status": "SUCCEEDED",
        "exhaustion_status": "PATHS_NOT_EXHAUSTED",
        "attempts": attempts,
        "can_execute": False,
    }


def test_exact_nested_fallback_union_normalizes_full_terminal_projection():
    detail = _union_detail()
    original = copy.deepcopy(detail)

    normalized = normalize_nested_fallback_union_detail(detail)

    assert normalized is not detail
    assert normalized["primary_path_id"] == "ESPN_SCOREBOARD"
    assert normalized["primary_path_state"] == "SUCCEEDED_WITH_ROWS"
    assert normalized["primary_blocker_code"] is None
    assert normalized["primary_upstream_status"] == 200
    assert normalized["primary_content_type_class"] == "JSON"
    assert normalized["primary_provider_alias"] is None

    assert normalized["fallback_path_id"] == "RUNDOWN"
    assert normalized["fallback_path_state"] == "SUCCEEDED_WITH_ROWS"
    assert normalized["fallback_blocker_code"] is None
    assert normalized["fallback_upstream_status"] == 200
    assert normalized["fallback_content_type_class"] == "JSON"
    assert normalized["fallback_provider_alias"] == "RUNDOWN_API_KEY"

    # Aggregate outcome and complete ordered provenance remain untouched.
    assert normalized["fallback_status"] == original["fallback_status"]
    assert normalized["exhaustion_status"] == original["exhaustion_status"]
    assert normalized["attempts"] == original["attempts"]
    assert detail == original
    assert normalized["can_execute"] is False


def test_originating_blocker_wins_over_attempt_blocker_in_projection():
    detail = _union_detail()
    detail["attempts"][-1]["path_state"] = "FAILED_TYPED"
    detail["attempts"][-1]["blocker_code"] = "DOWNSTREAM_WRAPPER"
    detail["attempts"][-1]["originating_blocker_code"] = "RUNDOWN_HTTP_503"

    normalized = normalize_nested_fallback_union_detail(detail)

    assert normalized["fallback_blocker_code"] == "RUNDOWN_HTTP_503"


@pytest.mark.parametrize(
    "paths",
    [
        ("ESPN_SCOREBOARD", "RUNDOWN"),
        ("ODDS_PROXY", "RUNDOWN"),
        ("ESPN_SCOREBOARD", "RUNDOWN", "ODDS_PROXY"),
    ],
)
def test_unsupported_union_shapes_remain_unchanged_and_fail_closed_upstream(paths):
    detail = _union_detail(paths=paths)

    normalized = normalize_nested_fallback_union_detail(detail)

    assert normalized is detail
    assert normalized["fallback_path_id"] == "GOVERNED_FALLBACK_UNION"


def test_non_union_summary_is_not_rewritten():
    detail = _union_detail()
    detail["fallback_path_id"] = "RUNDOWN"

    normalized = normalize_nested_fallback_union_detail(detail)

    assert normalized is detail
    assert normalized["fallback_path_id"] == "RUNDOWN"
