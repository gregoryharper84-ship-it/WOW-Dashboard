from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Lock
from types import SimpleNamespace
import time

import pytest

import nfl_prop_auto_hydration as nfl
import prop_auto_hydration_router as router
from prop_auto_hydration import PropAutoHydrationError
from pick_request_runtime_core import PickRequestBatch, PickRequestRow
import v17.interactive_pick_hydration as interactive


def _market_api():
    base_api = SimpleNamespace(
        _controlling_specialist_provider=lambda sport, stat: {
            "controlling_specialist": "WOW_PROP_SPECIALIST"
        }
    )
    prod = SimpleNamespace(
        base_api=base_api,
        PROP_CAPABILITY_KEY="PROP",
        _runtime_capability=lambda _key: {"capability_status": "AVAILABLE"},
    )
    return SimpleNamespace(
        prod=prod,
        _prop_route_artifact=lambda sport, stat: {
            "ok": True,
            "code": "PROP_CERTIFIED_MODEL_ARTIFACT_READY",
        },
    )


def _evidence(*, player: str, event_id: str, opponent: str = "IND") -> dict:
    captured = datetime.now(timezone.utc) - timedelta(minutes=5)
    return {
        "captured_at": captured.isoformat(),
        "game_log": [200.0 + i for i in range(10)],
        "box_score_log": [{"game": i + 1} for i in range(10)],
        "role_status": {
            "status": "ACTIVE_CURRENT_ESPN_ROSTER",
            "canonical_event_id": event_id,
            "provider_event_ids": {"ESPN": "401872945"},
            "identity_binding_status": "PASS",
            "opponent": opponent,
            "player": player,
        },
        "role_timestamp": captured.isoformat(),
        "opportunity_ledger": {"status": "READY"},
        "source_timestamps": {"STRESS_FIXTURE": captured.isoformat()},
        "evidence_version": "PROP_EVIDENCE_V1",
        "rate_provenance": "STRESS_FIXTURE",
    }


def _row(
    index: int,
    *,
    player: str,
    event_id: str = "WOW:NFL:2026-09-21:TEST",
    opponent: str = "IND",
    direction: str = "MORE",
    line: float = 221.5,
) -> PickRequestRow:
    return PickRequestRow(
        row_key=f"stress-{index}",
        event_id=event_id,
        event_start_time="2030-09-21T00:20:00+00:00",
        sport="NFL",
        player=player,
        stat_type="PASSING_YARDS",
        line=line,
        direction=direction,
        source_type="PASTED_BOARD",
        platform="STRESS",
        opponent=opponent,
    )


def test_50_rows_25_unique_identities_collapse_to_25_fetches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WOW_INTERACTIVE_PROP_HYDRATION_WORKERS", "8")
    calls: list[str] = []
    lock = Lock()

    def hydrate(**kwargs):
        with lock:
            calls.append(str(kwargs["player"]))
        time.sleep(0.003)
        return _evidence(
            player=str(kwargs["player"]),
            event_id=str(kwargs["canonical_event_id"]),
            opponent=str(kwargs.get("opponent") or "IND"),
        )

    monkeypatch.setattr(interactive, "auto_hydrate_prop_evidence", hydrate)
    rows: list[PickRequestRow] = []
    for i in range(25):
        player = f"Stress Player {i:02d}"
        rows.append(_row(i * 2, player=player, direction="MORE", line=220.5 + i))
        rows.append(_row(i * 2 + 1, player=player, direction="LESS", line=220.5 + i))

    batch = PickRequestBatch(request_id="stress-50", response_mode="COMPACT", rows=rows)
    result = interactive.prehydrate_batch(batch, market_api=_market_api())

    assert len(calls) == 25
    assert len(set(calls)) == 25
    assert all(row.evidence is not None for row in result.rows)
    for i in range(0, 50, 2):
        assert result.rows[i].evidence == result.rows[i + 1].evidence


def test_50_row_partial_failure_isolated_to_failed_identity_groups(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WOW_INTERACTIVE_PROP_HYDRATION_WORKERS", "8")

    def hydrate(**kwargs):
        player = str(kwargs["player"])
        ordinal = int(player.rsplit(" ", 1)[-1])
        if ordinal % 5 == 0:
            raise PropAutoHydrationError("STRESS_INJECTED_FAILURE", "intentional stress fault")
        return _evidence(
            player=player,
            event_id=str(kwargs["canonical_event_id"]),
            opponent=str(kwargs.get("opponent") or "IND"),
        )

    monkeypatch.setattr(interactive, "auto_hydrate_prop_evidence", hydrate)
    rows: list[PickRequestRow] = []
    for i in range(25):
        player = f"Stress Player {i}"
        rows.extend([
            _row(i * 2, player=player, direction="MORE"),
            _row(i * 2 + 1, player=player, direction="LESS"),
        ])

    result = interactive.prehydrate_batch(
        PickRequestBatch(request_id="stress-partial", rows=rows),
        market_api=_market_api(),
    )

    hydrated = sum(row.evidence is not None for row in result.rows)
    untouched = sum(row.evidence is None for row in result.rows)
    assert hydrated == 40
    assert untouched == 10


def test_same_player_same_start_different_canonical_events_never_share_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WOW_INTERACTIVE_PROP_HYDRATION_WORKERS", "4")
    calls: list[str] = []

    def hydrate(**kwargs):
        event_id = str(kwargs["canonical_event_id"])
        calls.append(event_id)
        return _evidence(player=str(kwargs["player"]), event_id=event_id)

    monkeypatch.setattr(interactive, "auto_hydrate_prop_evidence", hydrate)
    rows = [
        _row(1, player="Patrick Mahomes", event_id="WOW:NFL:A"),
        _row(2, player="Patrick Mahomes", event_id="WOW:NFL:B"),
    ]
    result = interactive.prehydrate_batch(PickRequestBatch(rows=rows), market_api=_market_api())

    assert sorted(calls) == ["WOW:NFL:A", "WOW:NFL:B"]
    assert result.rows[0].evidence.role_status["canonical_event_id"] == "WOW:NFL:A"
    assert result.rows[1].evidence.role_status["canonical_event_id"] == "WOW:NFL:B"


def test_opponent_alias_variants_all_bind_to_same_official_nfl_event(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        router._nfl,
        "hydrate_nfl_prop_evidence",
        lambda **_kwargs: {
            **_evidence(player="Patrick Mahomes", event_id="UNBOUND", opponent="IND"),
            "role_status": {
                "status": "ACTIVE_CURRENT_ESPN_ROSTER",
                "team": "KC",
                "opponent": "IND",
                "event_id": "401872945",
            },
            "hydration_provider": router.NFL_PROVIDER,
        },
    )

    aliases = ["IND", "Indianapolis Colts", "Colts", "Indianapolis"]
    for alias in aliases:
        result = router.auto_hydrate_prop_evidence(
            sport="NFL",
            player="Patrick Mahomes",
            stat_type="PASSING_YARDS",
            event_start_time="2030-09-21T00:20:00+00:00",
            opponent=alias,
            canonical_event_id="WOW:NFL:2030:IND@KC",
        )
        role = result["role_status"]
        assert role["canonical_event_id"] == "WOW:NFL:2030:IND@KC"
        assert role["provider_event_ids"] == {"ESPN": "401872945"}
        assert role["identity_binding_status"] == "PASS"


def test_wrong_opponent_still_fails_closed_under_alias_pressure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        router._nfl,
        "hydrate_nfl_prop_evidence",
        lambda **_kwargs: {
            **_evidence(player="Patrick Mahomes", event_id="UNBOUND", opponent="IND"),
            "role_status": {
                "status": "ACTIVE_CURRENT_ESPN_ROSTER",
                "team": "KC",
                "opponent": "IND",
                "event_id": "401872945",
            },
        },
    )

    with pytest.raises(PropAutoHydrationError) as exc_info:
        router.auto_hydrate_prop_evidence(
            sport="NFL",
            player="Patrick Mahomes",
            stat_type="PASSING_YARDS",
            event_start_time="2030-09-21T00:20:00+00:00",
            opponent="Seattle Seahawks",
            canonical_event_id="WOW:NFL:2030:IND@KC",
        )
    assert exc_info.value.code == "PROP_EVENT_IDENTITY_CONFLICT"


class _Response:
    status_code = 200

    def __init__(self, payload: dict):
        self._payload = payload

    def json(self):
        return self._payload


def _colts_chiefs_event(*, state: str = "pre") -> dict:
    return {
        "id": "401872945",
        "date": "2026-09-21T00:20:00Z",
        "status": {"type": {"state": state, "completed": state == "post"}},
        "competitions": [{
            "competitors": [
                {"team": {"abbreviation": "KC"}},
                {"team": {"abbreviation": "IND"}},
            ]
        }],
    }


def test_colts_chiefs_utc_rollover_survives_100_replays_and_duplicate_aliases() -> None:
    def http_get(url, *, params, headers, timeout, follow_redirects):
        del url, headers, timeout, follow_redirects
        date_key = str(params["dates"])
        if date_key in {"20260920", "20260921"}:
            return _Response({"events": [_colts_chiefs_event()]})
        return _Response({"events": []})

    for _ in range(100):
        result = nfl._target_event(
            event_start=datetime(2026, 9, 21, 0, 20, tzinfo=timezone.utc),
            team="KC",
            opponent="Indianapolis Colts",
            http_get=http_get,
        )
        assert result == {"event_id": "401872945", "team": "KC", "opponent": "IND"}


def test_multiple_distinct_matching_provider_events_fail_closed() -> None:
    duplicate = _colts_chiefs_event()
    conflicting = {**duplicate, "id": "401872946"}

    def http_get(url, *, params, headers, timeout, follow_redirects):
        del url, params, headers, timeout, follow_redirects
        return _Response({"events": [duplicate, conflicting]})

    with pytest.raises(nfl.NFLPropHydrationError) as exc_info:
        nfl._target_event(
            event_start=datetime(2026, 9, 21, 0, 20, tzinfo=timezone.utc),
            team="KC",
            opponent="IND",
            http_get=http_get,
        )
    assert exc_info.value.code == "PROP_EVENT_IDENTITY_CONFLICT"
    assert exc_info.value.detail["match_n"] == 2


def test_started_colts_chiefs_event_never_reenters_pregame_hydration() -> None:
    def http_get(url, *, params, headers, timeout, follow_redirects):
        del url, params, headers, timeout, follow_redirects
        return _Response({"events": [_colts_chiefs_event(state="in")]})

    with pytest.raises(nfl.NFLPropHydrationError) as exc_info:
        nfl._target_event(
            event_start=datetime(2026, 9, 21, 0, 20, tzinfo=timezone.utc),
            team="KC",
            opponent="IND",
            http_get=http_get,
        )
    assert exc_info.value.code == "EVENT_ALREADY_STARTED"
