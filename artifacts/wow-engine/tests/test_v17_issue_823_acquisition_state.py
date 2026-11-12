from __future__ import annotations

import json
from pathlib import Path

import pytest

from v17 import quota_aware_degraded_discovery as quota

FIXTURE = Path(__file__).parent / "fixtures" / "issue_823_acquisition_incomplete.json"
_MODEL_CODES = {"MODEL_UNAVAILABLE", "MODEL_SCORER_FAILED", "MODEL_INPUTS_INSUFFICIENT", "MODEL_OUTPUT_INVALID"}


def _result(rows, blockers):
    return {"discovery": {"acquisition_audit": rows, "source_blockers": blockers}}


def _unsupported(family):
    return (
        {"family": family, "request_status": "NO_CONFIGURED_DISCOVERY_FEED",
         "blocker_if_any": "NO_CONFIGURED_DISCOVERY_FEED", "coverage_complete": False},
        {"scope": "family", "sport": family, "status": "NO_CONFIGURED_DISCOVERY_FEED"},
    )


def test_fixture_is_consumed_and_invariants_hold():
    fixture = json.loads(FIXTURE.read_text())
    assert fixture["can_execute"] is False
    assert fixture["invariants"]["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert fixture["invariants"]["silent_row_loss_allowed"] is False
    assert fixture["invariants"]["probability_substitution_allowed"] is False

    cases = fixture["cases"]
    assert {c["sport"] for c in cases} == {"MLB", "WNBA", "NHL", "SOCCER", "TENNIS", "MMA"}
    audits, blockers = [], []
    for case in cases:
        if case["stage"] == "acquisition":
            audits.append({"family": case["sport"], "request_status": "PROVIDER_REQUEST_FAILED",
                           "blocker_if_any": "PROVIDER_REQUEST_FAILED", "coverage_complete": False})
            blockers.append({"sport": case["sport"], "status": "PROVIDER_REQUEST_FAILED"})
        else:
            audit, blocker = _unsupported(case["sport"])
            audits.append(audit)
            blockers.append(blocker)
    result = _result(audits, blockers)

    states = quota._per_sport_acquisition_state(result)
    assert set(states) == {c["sport"] for c in cases}
    for case in cases:
        state = states[case["sport"]]
        assert state["can_execute"] is False
        assert state["blocker_code"] not in _MODEL_CODES
        if case["stage"] == "acquisition":
            assert (state["state"], state["blocker_code"]) == ("FAILED", "PROVIDER_REQUEST_FAILED")
        else:
            assert (state["state"], state["blocker_code"]) == (
                "CONFIGURED_UNSUPPORTED", "NO_CONFIGURED_DISCOVERY_FEED")
    assert quota._coverage_status(result, quota._new_context()) == "PARTIAL_OR_UNPROVEN"


@pytest.mark.parametrize(
    "family", ["SOCCER", "TENNIS", "MMA", "PGA", "BOXING", "CRICKET"]
)
def test_unsupported_families_are_never_silent_or_proven(family):
    audit, blocker = _unsupported(family)
    result = _result(
        [{"family": "NFL", "request_status": "EVENTS_RETURNED", "coverage_complete": True}, audit],
        [blocker],
    )
    states = quota._per_sport_acquisition_state(result)
    assert states["NFL"]["state"] == "ACQUIRED"
    assert states[family]["state"] == "CONFIGURED_UNSUPPORTED"
    assert quota._coverage_status(result, quota._new_context()) == "PARTIAL_OR_UNPROVEN"


def test_no_configured_path_detail_alone_makes_coverage_incomplete():
    result = {"discovery": {
        "acquisition_audit": [],
        "source_blockers": [],
        "acquisition_details": [{"family": "MMA", "exhaustion_status": "NO_CONFIGURED_PATH"}],
    }}
    assert quota._coverage_status(result, quota._new_context()) == "PARTIAL_OR_UNPROVEN"


def test_complete_board_stays_proven():
    result = _result(
        [{"family": "NFL", "request_status": "EVENTS_RETURNED", "coverage_complete": True}], []
    )
    assert quota._coverage_status(result, quota._new_context()) == "PROVEN_FOR_CONFIGURED_DISCOVERY_SOURCES"
    assert quota._per_sport_acquisition_state(result)["NFL"]["state"] == "ACQUIRED"
