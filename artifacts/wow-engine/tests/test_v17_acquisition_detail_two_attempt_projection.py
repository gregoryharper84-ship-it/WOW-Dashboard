from __future__ import annotations

from v17.acquisition_detail_projection_repair import (
    normalize_acquisition_detail_projection,
    normalize_nested_fallback_union_detail,
)


def _attempt(path_id: str, ordinal: int, *, state: str, blocker=None, status=None, alias=None):
    return {
        "ordinal": ordinal,
        "path_id": path_id,
        "path_state": state,
        "blocker_code": blocker,
        "originating_blocker_code": None,
        "upstream_status": status,
        "content_type_class": "JSON" if status is not None else None,
        "credential_alias": alias,
    }


def _two_attempt_union_detail() -> dict:
    return {
        "family": "NFL",
        "target_key": "RUNDOWN|2|NFL|REGULAR_SEASON",
        "primary_path_id": "ESPN_SCOREBOARD",
        "primary_path_state": "FAILED_TYPED",
        "primary_blocker_code": "ESPN_HTTP_403",
        "primary_upstream_status": 403,
        "primary_content_type_class": "JSON",
        "primary_provider_alias": None,
        "fallback_path_id": "GOVERNED_FALLBACK_UNION",
        "fallback_path_state": "SUCCEEDED_WITH_ROWS",
        "fallback_blocker_code": None,
        "fallback_upstream_status": 200,
        "fallback_content_type_class": "JSON",
        "fallback_provider_alias": "ODDS_API_PAID_KEY",
        "fallback_status": "FALLBACK_SUCCEEDED",
        "exhaustion_status": "PATHS_NOT_EXHAUSTED",
        "attempts": [
            _attempt(
                "ESPN_SCOREBOARD",
                1,
                state="FAILED_TYPED",
                blocker="ESPN_HTTP_403",
                status=403,
            ),
            _attempt(
                "ODDS_PROXY",
                2,
                state="SUCCEEDED_WITH_ROWS",
                status=200,
                alias="ODDS_API_PAID_KEY",
            ),
        ],
        "can_execute": False,
    }


def test_two_attempt_nested_union_projects_terminal_concrete_attempt():
    normalized = normalize_nested_fallback_union_detail(_two_attempt_union_detail())
    assert normalized["primary_path_id"] == "ESPN_SCOREBOARD"
    assert normalized["primary_path_state"] == "FAILED_TYPED"
    assert normalized["primary_blocker_code"] == "ESPN_HTTP_403"
    assert normalized["fallback_path_id"] == "ODDS_PROXY"
    assert normalized["fallback_path_state"] == "SUCCEEDED_WITH_ROWS"
    assert normalized["fallback_upstream_status"] == 200
    assert normalized["fallback_provider_alias"] == "ODDS_API_PAID_KEY"
    assert len(normalized["attempts"]) == 2
    assert normalized["can_execute"] is False


def test_dispatcher_applies_two_attempt_nested_union_projection():
    normalized = normalize_acquisition_detail_projection(_two_attempt_union_detail())
    assert normalized["fallback_path_id"] == "ODDS_PROXY"
    assert normalized["fallback_path_state"] == "SUCCEEDED_WITH_ROWS"
    assert normalized["attempts"][1]["path_id"] == "ODDS_PROXY"


def test_non_prefix_two_attempt_shape_remains_fail_closed():
    detail = _two_attempt_union_detail()
    detail["attempts"][1]["path_id"] = "RUNDOWN"
    normalized = normalize_acquisition_detail_projection(detail)
    assert normalized is detail
    assert normalized["fallback_path_id"] == "GOVERNED_FALLBACK_UNION"
