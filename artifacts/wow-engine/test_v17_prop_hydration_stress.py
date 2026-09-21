from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import nfl_prop_auto_hydration as nfl
import prop_auto_hydration_router as router
from prop_auto_hydration import PropAutoHydrationError
import v17.interactive_pick_hydration as interactive


def _raw_evidence(*, role_status: dict | None = None) -> dict:
    captured = datetime.now(timezone.utc) - timedelta(minutes=5)
    return {
        "captured_at": captured.isoformat(),
        "game_log": [1.0] * 10,
        "box_score_log": [{"date": f"2026-09-{day:02d}"} for day in range(1, 11)],
        "role_status": role_status or {"status": "READY"},
        "role_timestamp": captured.isoformat(),
        "opportunity_ledger": {"status": "READY"},
        "source_timestamps": {"TEST_SOURCE": captured.isoformat()},
        "evidence_version": "PROP_EVIDENCE_V1",
        "rate_provenance": "TEST_ONLY",
    }


def _row(
    *,
    player: str,
    event_id: str,
    direction: str,
    opponent: str = "KC",
    stat_type: str = "PASSING_YARDS",
) -> interactive.PickRequestRow:
    return interactive.PickRequestRow(
        event_id=event_id,
        event_start_time="2030-09-21T00:20:00+00:00",
        sport="NFL",
        player=player,
        stat_type=stat_type,
        line=250.5,
        direction=direction,
        opponent=opponent,
    )


def test_fifty_row_pressure_deduplicates_to_twenty_five_identity_fetches(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    def hydrate(**kwargs):
        calls.append((kwargs["player"], kwargs["canonical_event_id"]))
        return _raw_evidence(
            role_status={
                "status": "ACTIVE_CURRENT_ESPN_ROSTER",
                "team": "IND",
                "opponent": "KC",
                "event_id": f"espn:{kwargs['player']}",
                "canonical_event_id": kwargs["canonical_event_id"],
                "provider_event_ids": {"ESPN": f"espn:{kwargs['player']}"},
                "identity_binding_status": "PASS",
            }
        )

    monkeypatch.setattr(interactive.pick_runtime, "auto_hydrate_prop_evidence", hydrate)
    rows: list[interactive.PickRequestRow] = []
    for index in range(25):
        player = f"Pressure Player {index:02d}"
        event_id = f"WOW:NFL:2030:EVENT:{index:02d}"
        rows.extend(
            [
                _row(player=player, event_id=event_id, direction="MORE"),
                _row(player=player, event_id=event_id, direction="LESS"),
            ]
        )

    batch = interactive.PickRequestBatch(request_id="stress-50", rows=rows)
    prefetched, errors, receipt = interactive.prehydrate_pick_batch(batch, max_workers=8)

    assert len(rows) == 50
    assert len(prefetched) == 50
    assert errors == {}
    assert len(calls) == 25
    assert receipt["unique_fetches"] == 25
    assert receipt["successful_fetches"] == 25
    assert receipt["prefetched_rows"] == 25
    assert receipt["reused_rows"] == 25
    assert receipt["failed_fetches"] == 0
    assert receipt["workers"] == 8
    assert receipt["can_execute"] is False


def test_partial_failure_isolated_to_one_identity_group(monkeypatch: pytest.MonkeyPatch) -> None:
    failing_player = "Pressure Player 07"

    def hydrate(**kwargs):
        if kwargs["player"] == failing_player:
            raise PropAutoHydrationError("NFL_PROP_SOURCE_UNAVAILABLE", "injected provider failure")
        return _raw_evidence(
            role_status={
                "status": "ACTIVE_CURRENT_ESPN_ROSTER",
                "team": "IND",
                "opponent": "KC",
                "event_id": f"espn:{kwargs['player']}",
                "canonical_event_id": kwargs["canonical_event_id"],
                "provider_event_ids": {"ESPN": f"espn:{kwargs['player']}"},
                "identity_binding_status": "PASS",
            }
        )

    monkeypatch.setattr(interactive.pick_runtime, "auto_hydrate_prop_evidence", hydrate)
    rows: list[interactive.PickRequestRow] = []
    for index in range(10):
        player = f"Pressure Player {index:02d}"
        event_id = f"WOW:NFL:2030:EVENT:{index:02d}"
        rows.extend(
            [
                _row(player=player, event_id=event_id, direction="MORE"),
                _row(player=player, event_id=event_id, direction="LESS"),
            ]
        )

    batch = interactive.PickRequestBatch(request_id="stress-partial", rows=rows)
    prefetched, errors, receipt = interactive.prehydrate_pick_batch(batch, max_workers=8)

    assert len(prefetched) == 18
    assert set(errors) == {"row-014", "row-015"}
    assert all(error.code == "NFL_PROP_SOURCE_UNAVAILABLE" for error in errors.values())
    assert receipt["unique_fetches"] == 10
    assert receipt["successful_fetches"] == 9
    assert receipt["failed_fetches"] == 1
    assert receipt["failure_codes"] == {"NFL_PROP_SOURCE_UNAVAILABLE": 1}
    assert receipt["false_global_failure_count"] == 0


def test_same_player_and_start_with_different_canonical_events_never_share_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def hydrate(**kwargs):
        event_id = kwargs["canonical_event_id"]
        calls.append(event_id)
        return _raw_evidence(
            role_status={
                "status": "ACTIVE_CURRENT_ESPN_ROSTER",
                "team": "IND",
                "opponent": "KC",
                "event_id": "espn-alias",
                "canonical_event_id": event_id,
                "provider_event_ids": {"ESPN": "espn-alias"},
                "identity_binding_status": "PASS",
            }
        )

    monkeypatch.setattr(interactive.pick_runtime, "auto_hydrate_prop_evidence", hydrate)
    rows = [
        _row(player="Same Player", event_id="WOW:NFL:EVENT:A", direction="MORE"),
        _row(player="Same Player", event_id="WOW:NFL:EVENT:B", direction="LESS"),
    ]
    batch = interactive.PickRequestBatch(request_id="stress-canonical-split", rows=rows)
    prefetched, errors, receipt = interactive.prehydrate_pick_batch(batch, max_workers=8)

    assert errors == {}
    assert calls == ["WOW:NFL:EVENT:A", "WOW:NFL:EVENT:B"]
    assert prefetched[0].evidence.role_status["canonical_event_id"] == "WOW:NFL:EVENT:A"
    assert prefetched[1].evidence.role_status["canonical_event_id"] == "WOW:NFL:EVENT:B"
    assert receipt["unique_fetches"] == 2
    assert receipt["reused_rows"] == 0


@pytest.mark.parametrize(
    "alias",
    ["IND", "Indianapolis Colts", "Colts", "Indianapolis"],
)
def test_nfl_opponent_alias_matrix_accepts_equivalent_identity(monkeypatch: pytest.MonkeyPatch, alias: str) -> None:
    monkeypatch.setattr(
        router._nfl,
        "hydrate_nfl_prop_evidence",
        lambda **_kwargs: {
            **_raw_evidence(),
            "role_status": {
                "status": "ACTIVE_CURRENT_ESPN_ROSTER",
                "team": "KC",
                "opponent": "IND",
                "event_id": "401872945",
            },
        },
    )

    result = router.auto_hydrate_prop_evidence(
        sport="NFL",
        player="Patrick Mahomes",
        stat_type="PASSING_YARDS",
        event_start_time="2030-09-21T00:20:00+00:00",
        opponent=alias,
        canonical_event_id="WOW:NFL:2030:IND@KC",
    )
    assert result["role_status"]["canonical_event_id"] == "WOW:NFL:2030:IND@KC"
    assert result["role_status"]["provider_event_ids"]["ESPN"] == "401872945"


def test_nfl_wrong_opponent_alias_still_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        router._nfl,
        "hydrate_nfl_prop_evidence",
        lambda **_kwargs: {
            **_raw_evidence(),
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
        "season": {"year": 2026},
        "week": {"number": 2},
        "status": {"type": {"state": state, "completed": state == "post"}},
        "competitions": [{
            "competitors": [
                {"homeAway": "home", "team": {"abbreviation": "KC"}},
                {"homeAway": "away", "team": {"abbreviation": "IND"}},
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
        assert result == {
            "event_id": "401872945",
            "team": "KC",
            "opponent": "IND",
            "provider_season": 2026,
            "provider_week": 2,
            "provider_home_team": "KC",
            "provider_away_team": "IND",
            "canonical_home_team": "KC",
            "canonical_away_team": "IND",
            "verified_canonical_event_id": "2026_02_IND_KC",
        }


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
