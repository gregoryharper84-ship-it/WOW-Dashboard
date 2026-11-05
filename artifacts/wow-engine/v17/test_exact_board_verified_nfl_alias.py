"""Exact-board identity accepts only the NFL boundary's own verified alias.

Production evidence (run b501567c, 2026-10-18): rows submitted with the ESPN
event id were resolved by the NFL identity boundary to the canonical nflverse id
(status=PASS), scored by the fitted specialist, then re-held as
PROP_EVENT_IDENTITY_CONFLICT because the outer exact-board check compared the
raw ESPN id to the canonical id. No probability is computed or changed here.
"""
from copy import deepcopy

import pytest

from v17.exact_board_reconciliation import (
    EXACT_BOARD_IDENTITY_MISMATCH,
    enforce_exact_board_identity,
)
from v17.pick_request_state_reliability_patch import (
    IDENTITY_CONFLICT_TERMINAL,
    _isolate_exact_identity_conflicts,
)
from v17.test_pick_request_state_reliability_patch import _scored, _source_row

ESPN_ID = "401873007"
CANONICAL = "2026_06_DAL_GB"


def _receipt(**overrides):
    receipt = {
        "status": "PASS",
        "canonical_event_id": CANONICAL,
        "source_event_id_alias": ESPN_ID,
        "provider_event_ids": {"ESPN": ESPN_ID},
        "identity_binding_status": "VERIFIED_PROVIDER_TO_CANONICAL",
        "can_execute": False,
    }
    receipt.update(overrides)
    return receipt


def _resolution(row_key="love-more", **overrides):
    return {"rows": {row_key: _receipt(**overrides)}, "probability_mutated": False, "can_execute": False}


def _inputs(observed_event=CANONICAL):
    source = [_source_row("love-more", ESPN_ID, "Jordan Love")]
    outcomes = [_scored("love-more", observed_event, "Jordan Love", "pred-love")]
    return source, outcomes


def test_verified_espn_alias_keeps_model_terminal_unchanged():
    source, outcomes = _inputs()
    isolated, mismatches = _isolate_exact_identity_conflicts(source, outcomes, _resolution())
    assert mismatches == []
    assert isolated == outcomes
    assert isolated[0]["terminal_status"] == "REJECTED"
    assert isolated[0]["code"] == "NO_LOW_PROBABILITY"
    assert isolated[0]["model_evaluated"] is True
    assert isolated[0]["can_execute"] is False


def test_verified_alias_response_balances_and_preserves_probability():
    source, outcomes = _inputs()
    out = enforce_exact_board_identity(
        {"rows": deepcopy(outcomes), "nfl_event_identity_resolution": _resolution(), "can_execute": False},
        source,
    )
    audit = out["exact_board_identity_reconciliation"]
    assert audit["balanced"] is True
    assert audit["mismatch_count"] == 0
    assert "completion_blocker" not in out
    assert out["rows"][0]["result"] == outcomes[0]["result"]
    assert out["can_execute"] is False


def test_without_identity_receipt_alias_is_still_held():
    source, outcomes = _inputs()
    isolated, mismatches = _isolate_exact_identity_conflicts(source, outcomes)
    assert [m["fields"] for m in mismatches] == [["event_id"]]
    assert isolated[0]["terminal_status"] == "HELD"
    assert isolated[0]["code"] == IDENTITY_CONFLICT_TERMINAL


@pytest.mark.parametrize(
    "resolution",
    [
        _resolution(status="BLOCKED"),
        _resolution(status="CANONICAL_INPUT_PENDING_PROVIDER_VERIFICATION"),
        _resolution(canonical_event_id="2026_06_NYG_PHI"),
        _resolution(source_event_id_alias="401873999"),
        _resolution(source_event_id_alias=None),
        _resolution(row_key="other-row"),
        {"rows": "not-a-mapping"},
    ],
)
def test_non_matching_or_unverified_receipt_never_creates_equivalence(resolution):
    source, outcomes = _inputs()
    isolated, mismatches = _isolate_exact_identity_conflicts(source, outcomes, resolution)
    assert [m["fields"] for m in mismatches] == [["event_id"]]
    assert isolated[0]["terminal_status"] == "HELD"
    assert isolated[0]["model_evaluated"] is False
    assert isolated[0]["probability_publishable"] is False


def test_scored_event_differing_from_verified_canonical_is_still_held():
    source, outcomes = _inputs(observed_event="2026_06_NYG_PHI")
    isolated, mismatches = _isolate_exact_identity_conflicts(source, outcomes, _resolution())
    assert [m["fields"] for m in mismatches] == [["event_id"]]
    assert isolated[0]["code"] == IDENTITY_CONFLICT_TERMINAL


def test_verified_alias_does_not_excuse_other_field_mismatch():
    source, outcomes = _inputs()
    outcomes[0]["result"]["prediction"]["line"] = 29.5
    out = enforce_exact_board_identity(
        {"rows": outcomes, "nfl_event_identity_resolution": _resolution(), "can_execute": False},
        source,
    )
    assert out["completion_blocker"] == EXACT_BOARD_IDENTITY_MISMATCH
    assert out["exact_board_identity_reconciliation"]["mismatches"][0]["fields"] == ["line"]
