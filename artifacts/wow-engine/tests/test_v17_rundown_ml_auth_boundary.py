from __future__ import annotations

from dataclasses import replace

from v17 import rundown_ml_board as board


def test_ml_discovery_repairs_stale_rundown_transport_before_snapshot(monkeypatch):
    original = board.sources.PROVIDERS["RUNDOWN"]
    stale_endpoints = dict(original.endpoints)
    stale_endpoints["sports"] = "/api/v1/sports"
    board.sources.PROVIDERS["RUNDOWN"] = replace(
        original,
        key_envs=("RUNDOWN_API_KEY", "WOW_RUNDOWN_API_KEY", "THERUNDOWN_API_KEY"),
        auth_style="query",
        auth_name="key",
        endpoints=stale_endpoints,
    )

    monkeypatch.setattr(board, "snapshot_dates", lambda: ["2026-09-20"])
    observed: dict[str, object] = {}

    def fake_snapshot(*_args, **_kwargs):
        provider = board.sources.PROVIDERS["RUNDOWN"]
        observed.update(
            auth_style=provider.auth_style,
            auth_name=provider.auth_name,
            key_envs=provider.key_envs,
            sports_endpoint=provider.endpoints["sports"],
        )
        return board.sources.MarketEvidenceResult(
            ok=False,
            provider="RUNDOWN",
            capability="events",
            code="RUNDOWN_HTTP_401",
        )

    monkeypatch.setattr(board.live, "get_sport_date_odds_snapshot", fake_snapshot)
    try:
        _rows, codes = board._collect_rundown("baseball_mlb")
    finally:
        board.sources.PROVIDERS["RUNDOWN"] = original

    assert observed == {
        "auth_style": "header",
        "auth_name": "X-TheRundown-Key",
        "key_envs": ("THERUNDOWN_API_KEY", "RUNDOWN_API_KEY", "WOW_RUNDOWN_API_KEY"),
        "sports_endpoint": "/api/v2/sports",
    }
    assert codes == ["RUNDOWN_HTTP_401"]


def test_ml_discovery_auth_repair_does_not_change_execution_governance(monkeypatch):
    monkeypatch.setattr(board, "snapshot_dates", lambda: [])
    rows, codes = board._collect_rundown("baseball_mlb")
    assert rows == []
    assert codes == []
    assert board.sources.CAN_EXECUTE is False
