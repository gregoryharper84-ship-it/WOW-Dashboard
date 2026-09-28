from __future__ import annotations

from datetime import datetime, timezone

import pytest

from v17 import wnba_prop_evidence_acquisition as subject

NOW = datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc)
EVENT = datetime(2026, 9, 28, 23, 0, tzinfo=timezone.utc)


def _schedule(status: int = 1):
    return {
        "leagueSchedule": {
            "gameDates": [
                {
                    "games": [
                        {
                            "gameId": "1022600999",
                            "gameDateTimeUTC": EVENT.isoformat().replace("+00:00", "Z"),
                            "gameStatus": status,
                            "homeTeam": {
                                "teamId": "2",
                                "teamCity": "Beta",
                                "teamName": "Belles",
                                "teamTricode": "BBB",
                            },
                            "awayTeam": {
                                "teamId": "1",
                                "teamCity": "Alpha",
                                "teamName": "Aces",
                                "teamTricode": "AAA",
                            },
                        }
                    ]
                }
            ]
        }
    }


def test_schedule_players_uses_future_official_game_and_current_rosters(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        subject.wnba,
        "_roster",
        lambda team_id, season, http_get: [
            {"PLAYER": "Alpha Guard", "PLAYER_ID": "1001"}
        ] if str(team_id) == "1" else [
            {"PLAYER": "Beta Forward", "PLAYER_ID": "2001"}
        ],
    )
    rows = subject._schedule_players(
        _schedule(),
        requested_date="2026-09-28",
        requested_timezone="America/Chicago",
        now=NOW,
        http_get=lambda *_a, **_k: None,
    )
    assert [(row["player"], row["team"], row["opponent"]) for row in rows] == [
        ("Alpha Guard", "AAA", "BBB"),
        ("Beta Forward", "BBB", "AAA"),
    ]
    assert all(row["event_id"] == "WNBA:1022600999" for row in rows)


def test_started_games_do_not_seed_forward_candidates(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(subject.wnba, "_roster", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("roster not expected")))
    rows = subject._schedule_players(
        _schedule(status=3),
        requested_date="2026-09-28",
        requested_timezone="America/Chicago",
        now=NOW,
        http_get=lambda *_a, **_k: None,
    )
    assert rows == []


def test_candidate_line_is_discovery_half_point_not_market_price():
    assert subject._candidate_line([10, 11, 12, 13, 14, 15, 16, 17, 18, 19]) == 14.5
    assert subject._candidate_line([0, 0, 1, 1, 1, 2, 2, 2, 3, 3]) == 1.5


class _Query:
    def __init__(self, writes):
        self.writes = writes
        self.row = None

    def upsert(self, row, on_conflict=None):
        self.row = dict(row)
        self.writes.append((self.row, on_conflict))
        return self

    def execute(self):
        return type("Result", (), {"data": [self.row]})()


class _DB:
    def __init__(self):
        self.writes = []

    def table(self, name):
        assert name == "wow_prop_evidence_snapshots"
        return _Query(self.writes)


def _raw(stat_type: str):
    return {
        "captured_at": NOW.isoformat(),
        "game_log": [float(i) for i in range(10)],
        "box_score_log": [{"game": i} for i in range(10)],
        "role_status": {"status": "ACTIVE", "team_tricode": "AAA", "opponent_tricode": "BBB"},
        "role_timestamp": NOW.isoformat(),
        "opportunity_ledger": {"status": "READY", "availability_gate": "PASS"},
        "source_timestamps": {"WNBA_OFFICIAL": NOW.isoformat()},
        "evidence_version": "PROP_EVIDENCE_V1",
        "rate_provenance": "WNBA_OFFICIAL_STATS_CDN_INJURY_V1",
    }


def test_acquisition_persists_all_four_core_stats_and_never_publishes(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(subject, "_request_schedule", lambda **_kwargs: _schedule())
    monkeypatch.setattr(
        subject,
        "_schedule_players",
        lambda *_args, **_kwargs: [
            {
                "event_id": "WNBA:1022600999",
                "official_game_id": "1022600999",
                "event_start_time": EVENT.isoformat(),
                "player": "Alpha Guard",
                "player_id": "1001",
                "team": "AAA",
                "opponent": "BBB",
            }
        ],
    )
    hydrated_stats = []

    def hydrate(**kwargs):
        hydrated_stats.append(kwargs["stat_type"])
        return _raw(kwargs["stat_type"])

    monkeypatch.setattr(subject, "auto_hydrate_prop_evidence", hydrate)
    monkeypatch.setattr(subject, "_validate_evidence", lambda row, stat: row.evidence.model_dump())

    def snapshot(row, normalized):
        sid = f"snapshot-{row.stat_type}"
        return sid, f"fingerprint-{row.stat_type}", {
            "source_snapshot_id": sid,
            "captured_at": NOW.isoformat(),
            "event_id": row.event_id,
            "event_start_time": row.event_start_time,
            "sport": row.sport,
            "player": row.player,
            "stat_type": row.stat_type,
            "line": row.line,
            "side": row.direction,
            "hydration_status": "READY",
        }

    monkeypatch.setattr(subject, "_snapshot_payload", snapshot)
    db = _DB()
    result = subject.acquire_wnba_prop_snapshots(
        db=db,
        requested_date="2026-09-28",
        requested_timezone="America/Chicago",
        max_candidates=20,
        now=NOW,
        http_get=lambda *_a, **_k: None,
    )

    assert hydrated_stats == list(subject.CORE_STATS)
    assert result["attempted"] == 4
    assert result["hydrated"] == 4
    assert result["persisted"] == 4
    assert result["snapshot_write_succeeded"] == 4
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False
    assert len(db.writes) == 4
    assert {row[0]["stat_type"] for row in db.writes} == set(subject.CORE_STATS)


def test_typed_hydration_failure_is_row_scoped_and_does_not_stop_siblings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(subject, "_request_schedule", lambda **_kwargs: _schedule())
    monkeypatch.setattr(
        subject,
        "_schedule_players",
        lambda *_args, **_kwargs: [{
            "event_id": "WNBA:1022600999",
            "official_game_id": "1022600999",
            "event_start_time": EVENT.isoformat(),
            "player": "Alpha Guard",
            "player_id": "1001",
            "team": "AAA",
            "opponent": "BBB",
        }],
    )

    def hydrate(**kwargs):
        if kwargs["stat_type"] == "REBOUNDS":
            raise subject.wnba.WNBAPropHydrationError("WNBA_PLAYER_AVAILABILITY_NOT_CLEAR", "held")
        return _raw(kwargs["stat_type"])

    monkeypatch.setattr(subject, "auto_hydrate_prop_evidence", hydrate)
    monkeypatch.setattr(subject, "_validate_evidence", lambda row, stat: row.evidence.model_dump())
    monkeypatch.setattr(
        subject,
        "_snapshot_payload",
        lambda row, normalized: (
            f"snapshot-{row.stat_type}",
            "fp",
            {"source_snapshot_id": f"snapshot-{row.stat_type}", "stat_type": row.stat_type},
        ),
    )
    db = _DB()
    result = subject.acquire_wnba_prop_snapshots(
        db=db,
        requested_date="2026-09-28",
        requested_timezone="America/Chicago",
        max_candidates=20,
        now=NOW,
        http_get=lambda *_a, **_k: None,
    )
    assert result["attempted"] == 4
    assert result["persisted"] == 3
    assert result["held"] == 1
    assert result["status"] == "COMPLETED_WITH_ROW_BLOCKERS"
    assert any("WNBA_PLAYER_AVAILABILITY_NOT_CLEAR" in blocker for blocker in result["blockers"])
    assert result["can_execute"] is False
