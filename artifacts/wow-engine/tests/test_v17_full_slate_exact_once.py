from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from v17 import cross_sport_resilience_overlay as resilience
from v17.full_slate_exact_once import (
    _FULL_SLATE_CONTINUATION_ACTIVE,
    _is_full_moneyline_request,
    _route_full_slate_in_batches,
)


ROOT = Path(__file__).resolve().parents[1]


def _event(index: int):
    return SimpleNamespace(
        sport="NFL",
        official_event_id=f"NFL-{index:02d}",
        home_team=f"HOME-{index:02d}",
        away_team=f"AWAY-{index:02d}",
        commence_time_utc="2026-09-27T17:00:00Z",
    )


def test_full_moneyline_and_compact_action_contract_auto_continue():
    assert _is_full_moneyline_request(
        SimpleNamespace(response_mode="FULL", lanes=["MONEYLINE"])
    )
    assert _is_full_moneyline_request(
        SimpleNamespace(
            response_mode="COMPACT",
            lanes=["MONEYLINE"],
            max_props=0,
        )
    )
    assert not _is_full_moneyline_request(
        SimpleNamespace(
            response_mode="COMPACT",
            lanes=["PROPS", "MONEYLINE"],
            max_props=0,
        )
    )
    assert not _is_full_moneyline_request(
        SimpleNamespace(
            response_mode="COMPACT",
            lanes=["MONEYLINE"],
            max_props=1,
        )
    )
    assert not _is_full_moneyline_request(
        SimpleNamespace(response_mode="FULL", lanes=["PROPS"])
    )


def test_more_than_one_model_budget_is_routed_exactly_once(monkeypatch):
    monkeypatch.setenv("WOW_CROSS_SPORT_MAX_MODEL_INVOCATIONS", "12")
    events = [_event(index) for index in range(16)]
    inventory = SimpleNamespace(events=list(events))
    routed_ids: list[str] = []
    batch_sizes: list[int] = []

    def original_route(current_inventory, *args, **kwargs):
        batch_sizes.append(len(current_inventory.events))
        assert resilience._effective_model_invocation_limit() == len(
            current_inventory.events
        )
        rows = []
        for event in current_inventory.events:
            routed_ids.append(event.official_event_id)
            rows.append({"official_event_id": event.official_event_id})
        return rows

    token = _FULL_SLATE_CONTINUATION_ACTIVE.set(True)
    try:
        rows = _route_full_slate_in_batches(
            original_route,
            inventory,
            batch_size=12,
            score_row=lambda *_args, **_kwargs: {},
        )
    finally:
        _FULL_SLATE_CONTINUATION_ACTIVE.reset(token)

    assert batch_sizes == [12, 4]
    assert len(rows) == 16
    assert routed_ids == [event.official_event_id for event in events]
    assert len(set(routed_ids)) == 16
    assert inventory.events == events


def test_non_continuation_canary_path_keeps_single_bounded_route_call():
    events = [_event(index) for index in range(16)]
    inventory = SimpleNamespace(events=list(events))
    calls = 0

    def original_route(current_inventory, *args, **kwargs):
        nonlocal calls
        calls += 1
        return list(current_inventory.events)

    rows = _route_full_slate_in_batches(
        original_route,
        inventory,
        batch_size=12,
        score_row=lambda *_args, **_kwargs: {},
    )

    assert calls == 1
    assert rows == events


def test_startup_order_installs_exact_once_after_resilience_before_quota():
    text = (ROOT / "v17_observability.py").read_text()
    resilience_call = text.index("install_cross_sport_resilience()")
    exact_once_call = text.index("install_full_slate_exact_once()")
    quota_call = text.index("install_quota_aware_degraded_discovery()")

    assert resilience_call < exact_once_call < quota_call
    assert "can_execute" not in text[exact_once_call:quota_call] or "can_execute=false" not in text[exact_once_call:quota_call]
