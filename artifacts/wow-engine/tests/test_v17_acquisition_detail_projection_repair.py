from __future__ import annotations

import copy

import pytest

from v17.acquisition_detail_projection_repair import (
    normalize_nested_fallback_union_detail,
)


def _attempt(path_id: str, ordinal: int) -> dict:
    return {
        "ordinal": ordinal,
        "path_id": path_id,
        "path_state": "FAILED_TYPED" if path_id != "RUNDOWN" else "SUCCEEDED_WITH_ROWS",
        "blocker_code": None,
        "originating_blocker_code": None,
        "upstream_status": None,
        "content_type_class": None,
        "credential_alias": None,
    }


def _union_detail(paths=("ESPN_SCOREBOARD", "ODDS_PROXY", "RUNDOWN")) -> dict:
    return {
        "family": "MLB",
        "target_key": "RUNDOWN|1|MLB|REGULAR_SEASON",
        "fallback_path_id": "GOVERNED_FALLBACK_UNION",
        "fallback_path_state": "SUCCEEDED_WITH_ROWS",
        "attempts": [_attempt(path, index) for index, path in enumerate(paths, start=1)],
        "can_execute": False,
    }


def test_exact_nested_fallback_union_normalizes_to_terminal_concrete_path():
    detail = _union_detail()
    original = copy.deepcopy(detail)

    normalized = normalize_nested_fallback_union_detail(detail)

    assert normalized is not detail
    assert normalized["fallback_path_id"] == "RUNDOWN"
    assert normalized["fallback_path_state"] == "SUCCEEDED_WITH_ROWS"
    assert normalized["attempts"] == original["attempts"]
    assert detail == original
    assert normalized["can_execute"] is False


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
