from __future__ import annotations

from types import SimpleNamespace

import pytest

import prop_auto_hydration_router as router
from prop_auto_hydration import PropAutoHydrationError


START = "2030-09-21T00:20:00+00:00"


def _row(event_id: str, *, opponent: str = "IND") -> SimpleNamespace:
    return SimpleNamespace(
        sport="NFL",
        player="Patrick Mahomes",
        event_start_time=START,
        event_id=event_id,
        opponent=opponent,
    )


def _provider_evidence() -> dict:
    return {
        "captured_at": "2030-09-20T23:50:00+00:00",
        "game_log": [200.0] * 10,
        "box_score_log": [{"game": i} for i in range(10)],
        "role_status": {
            "status": "ACTIVE_CURRENT_ESPN_ROSTER",
            "team": "KC",
            "opponent": "IND",
            "event_id": "401872945",
        },
        "role_timestamp": "2030-09-20T23:50:00+00:00",
        "opportunity_ledger": {"status": "READY"},
        "source_timestamps": {"TEST": "2030-09-20T23:50:00+00:00"},
        "evidence_version": "PROP_EVIDENCE_V1",
        "rate_provenance": "TEST_ONLY",
        "hydration_provider": router.NFL_PROVIDER,
    }


def test_legacy_fallback_fails_closed_when_request_context_has_conflicting_canonical_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_called = False

    def should_not_fetch(**_kwargs):
        nonlocal provider_called
        provider_called = True
        return _provider_evidence()

    monkeypatch.setattr(router._nfl, "hydrate_nfl_prop_evidence", should_not_fetch)
    rows = [_row("WOW:NFL:A"), _row("WOW:NFL:B")]

    with router.hydration_request_context(rows):
        with pytest.raises(PropAutoHydrationError) as exc_info:
            # This mirrors pick_request_runtime_core's legacy fallback call: it
            # has player/start/stat but no canonical_event_id or opponent args.
            router.auto_hydrate_prop_evidence(
                sport="NFL",
                player="Patrick Mahomes",
                stat_type="PASSING_YARDS",
                event_start_time=START,
            )

    assert provider_called is False
    assert exc_info.value.code == "PROP_EVENT_IDENTITY_CONFLICT"
    assert exc_info.value.detail["identity_binding_status"] == "AMBIGUOUS_CANONICAL_EVENT"
    assert exc_info.value.detail["conflicting_event_ids"] == ["WOW:NFL:A", "WOW:NFL:B"]


def test_explicit_canonical_identity_still_wins_inside_ambiguous_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        router._nfl,
        "hydrate_nfl_prop_evidence",
        lambda **_kwargs: _provider_evidence(),
    )
    rows = [_row("WOW:NFL:A"), _row("WOW:NFL:B")]

    with router.hydration_request_context(rows):
        result = router.auto_hydrate_prop_evidence(
            sport="NFL",
            player="Patrick Mahomes",
            stat_type="PASSING_YARDS",
            event_start_time=START,
            canonical_event_id="WOW:NFL:A",
            opponent="Indianapolis Colts",
        )

    role = result["role_status"]
    assert role["canonical_event_id"] == "WOW:NFL:A"
    assert role["provider_event_ids"] == {"ESPN": "401872945"}
    assert role["identity_binding_status"] == "PASS"


def test_legacy_fallback_keeps_canonical_identity_when_context_is_unambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        router._nfl,
        "hydrate_nfl_prop_evidence",
        lambda **_kwargs: _provider_evidence(),
    )
    rows = [_row("WOW:NFL:A"), _row("WOW:NFL:A")]

    with router.hydration_request_context(rows):
        result = router.auto_hydrate_prop_evidence(
            sport="NFL",
            player="Patrick Mahomes",
            stat_type="PASSING_YARDS",
            event_start_time=START,
        )

    role = result["role_status"]
    assert role["canonical_event_id"] == "WOW:NFL:A"
    assert role["provider_event_ids"] == {"ESPN": "401872945"}
    assert role["identity_binding_status"] == "PASS"
