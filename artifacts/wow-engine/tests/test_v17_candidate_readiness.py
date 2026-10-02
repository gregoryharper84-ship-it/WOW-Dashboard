from __future__ import annotations

import pytest

from v17.candidate_readiness import (
    CandidateState,
    InvalidCandidateTransition,
    acquired_candidate,
    readiness_status,
    transition_candidate,
)


def _offer():
    return {
        "provider_offer_id": "abc-123",
        "player": "Patrick Mahomes II",
        "market": "PASSING_YARDS",
        "line": 265.5,
        "event_start_time": "2026-10-02T20:15:00Z",
    }


def test_candidate_id_is_deterministic_for_identical_offer_replay():
    first = acquired_candidate(source_feed="prizepicks", sport="NFL", raw_payload=_offer())
    second = acquired_candidate(source_feed="PrizePicks", sport="nfl", raw_payload=_offer())

    assert first.candidate_id == second.candidate_id
    assert first.raw_payload_hash == second.raw_payload_hash
    assert first.state == CandidateState.ACQUIRED
    assert first.can_execute is False


def test_candidate_id_changes_when_material_offer_payload_changes():
    changed = dict(_offer(), line=270.5)
    first = acquired_candidate(source_feed="prizepicks", sport="NFL", raw_payload=_offer())
    second = acquired_candidate(source_feed="prizepicks", sport="NFL", raw_payload=changed)

    assert first.candidate_id != second.candidate_id


def test_success_path_advances_one_governed_state_at_a_time():
    entry = acquired_candidate(source_feed="prizepicks", sport="NFL", raw_payload=_offer())

    entry, r1 = transition_candidate(
        entry,
        CandidateState.RECONCILED,
        canonical_event_id="wow:nfl:20261002:kc:den",
        canonical_player_id="player:mahomes",
        canonical_market_id="NFL:PASSING_YARDS",
    )
    entry, r2 = transition_candidate(
        entry,
        CandidateState.HYDRATED,
        feature_snapshot_id="snapshot:123",
    )
    entry, r3 = transition_candidate(
        entry,
        CandidateState.SPECIALIST_ASSIGNED,
        specialist_id="NFL_PASSING_YARDS_V17",
    )
    entry, r4 = transition_candidate(entry, CandidateState.EVALUATED)
    entry, r5 = transition_candidate(entry, CandidateState.QUALIFIED_FOR_REDUCER)

    assert entry.state == CandidateState.QUALIFIED_FOR_REDUCER
    assert readiness_status(entry)["qualified_for_reducer"] is True
    assert all(receipt.can_execute is False for receipt in (r1, r2, r3, r4, r5))
    assert len({r.transition_sha256 for r in (r1, r2, r3, r4, r5)}) == 5


def test_unresolved_identity_is_typed_terminal_and_cannot_be_reopened():
    entry = acquired_candidate(source_feed="prizepicks", sport="NFL", raw_payload=_offer())
    entry, receipt = transition_candidate(
        entry,
        CandidateState.REJECTED_UNRESOLVED_IDENTITY,
        reason="PROP_EVENT_IDENTITY_CONFLICT:NFL_CANONICAL_EVENT_ID_MISMATCH",
    )

    assert receipt.reason == "PROP_EVENT_IDENTITY_CONFLICT:NFL_CANONICAL_EVENT_ID_MISMATCH"
    assert entry.state == CandidateState.REJECTED_UNRESOLVED_IDENTITY

    with pytest.raises(InvalidCandidateTransition):
        transition_candidate(entry, CandidateState.RECONCILED)


def test_illegal_state_jump_fails_closed():
    entry = acquired_candidate(source_feed="prizepicks", sport="NFL", raw_payload=_offer())

    with pytest.raises(InvalidCandidateTransition):
        transition_candidate(entry, CandidateState.HYDRATED)


def test_rejection_requires_explicit_reason():
    entry = acquired_candidate(source_feed="prizepicks", sport="NFL", raw_payload=_offer())

    with pytest.raises(InvalidCandidateTransition):
        transition_candidate(entry, CandidateState.REJECTED_UNRESOLVED_IDENTITY)


def test_readiness_surface_contains_no_probability_or_execution_authority():
    entry = acquired_candidate(source_feed="prizepicks", sport="NFL", raw_payload=_offer())
    payload = readiness_status(entry)

    assert payload["can_execute"] is False
    assert "probability" not in payload
    assert "implied_probability" not in payload
    assert "model_probability" not in payload
