from __future__ import annotations

from datetime import datetime, timezone

import pytest

from v17 import daily_prop_acquisition as subject

NOW = datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc)
EVENT = datetime(2026, 9, 28, 23, 0, tzinfo=timezone.utc)


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


def _candidate(player: str = "Starter One"):
    return {
        "event_id": "MLB:123456",
        "event_start_time": EVENT.isoformat(),
        "player": player,
        "opponent": "TWO",
    }


def _raw(stat_type: str):
    base = {
        "PITCHER_STRIKEOUTS": 5.0,
        "PITCHING_OUTS": 18.0,
        "STRIKES_THROWN": 60.0,
        "BALLS_THROWN": 30.0,
    }[stat_type]
    return {
        "captured_at": NOW.isoformat(),
        "game_log": [base + float(i % 3) for i in range(10)],
        "box_score_log": [{"game": i} for i in range(10)],
        "role_status": {"status": "CONFIRMED_STARTER"},
        "role_timestamp": NOW.isoformat(),
        "opportunity_ledger": {"status": "READY"},
        "source_timestamps": {"MLB_STATS_API": NOW.isoformat()},
        "evidence_version": "PROP_EVIDENCE_V1",
        "rate_provenance": "MLB_STATS_API_OFFICIAL_V1",
    }


def _install_common(monkeypatch: pytest.MonkeyPatch, players=None):
    monkeypatch.setattr(subject, "_request_schedule", lambda *_a, **_k: {"dates": []})
    monkeypatch.setattr(
        subject,
        "_schedule_pitchers",
        lambda *_a, **_k: players or [_candidate()],
    )
    monkeypatch.setattr(subject, "_validate_evidence", lambda row, stat: row.evidence.model_dump())
    monkeypatch.setattr(
        subject,
        "_snapshot_payload",
        lambda row, normalized: (
            f"snapshot-{row.player}-{row.stat_type}",
            f"fingerprint-{row.player}-{row.stat_type}",
            {
                "source_snapshot_id": f"snapshot-{row.player}-{row.stat_type}",
                "event_id": row.event_id,
                "event_start_time": row.event_start_time,
                "sport": row.sport,
                "player": row.player,
                "stat_type": row.stat_type,
                "line": row.line,
                "side": row.direction,
                "hydration_status": "PASS",
            },
        ),
    )


def test_certified_pitcher_routes_are_all_seeded(monkeypatch: pytest.MonkeyPatch):
    _install_common(monkeypatch)
    hydrated = []

    def hydrate(**kwargs):
        hydrated.append(kwargs["stat_type"])
        return _raw(kwargs["stat_type"])

    monkeypatch.setattr(subject, "auto_hydrate_prop_evidence", hydrate)
    db = _DB()
    result = subject.acquire_daily_prop_snapshots(
        db=db,
        requested_date="2026-09-28",
        requested_timezone="America/Chicago",
        max_candidates=20,
        now=NOW,
        http_get=lambda *_a, **_k: None,
    )

    assert hydrated == list(subject.STAT_TYPES)
    assert result["stat_types"] == list(subject.STAT_TYPES)
    assert result["attempted"] == 4
    assert result["persisted"] == 4
    assert result["can_execute"] is False
    assert {row[0]["stat_type"] for row in db.writes} == set(subject.STAT_TYPES)


def test_max_candidates_remains_hard_row_budget(monkeypatch: pytest.MonkeyPatch):
    _install_common(monkeypatch, [_candidate("Starter One"), _candidate("Starter Two")])
    hydrated = []
    monkeypatch.setattr(
        subject,
        "auto_hydrate_prop_evidence",
        lambda **kwargs: hydrated.append((kwargs["player"], kwargs["stat_type"])) or _raw(kwargs["stat_type"]),
    )
    db = _DB()
    result = subject.acquire_daily_prop_snapshots(
        db=db,
        requested_date="2026-09-28",
        requested_timezone="America/Chicago",
        max_candidates=5,
        now=NOW,
        http_get=lambda *_a, **_k: None,
    )

    assert result["attempted"] == 5
    assert len(hydrated) == 5
    assert hydrated[:4] == [("Starter One", stat) for stat in subject.STAT_TYPES]
    assert hydrated[4] == ("Starter Two", "PITCHER_STRIKEOUTS")


def test_one_workload_route_hydration_failure_does_not_drop_siblings(monkeypatch: pytest.MonkeyPatch):
    _install_common(monkeypatch)

    def hydrate(**kwargs):
        if kwargs["stat_type"] == "STRIKES_THROWN":
            raise subject.PropAutoHydrationError("PROP_HISTORY_INSUFFICIENT", "held")
        return _raw(kwargs["stat_type"])

    monkeypatch.setattr(subject, "auto_hydrate_prop_evidence", hydrate)
    db = _DB()
    result = subject.acquire_daily_prop_snapshots(
        db=db,
        requested_date="2026-09-28",
        requested_timezone="America/Chicago",
        max_candidates=4,
        now=NOW,
        http_get=lambda *_a, **_k: None,
    )

    assert result["attempted"] == 4
    assert result["persisted"] == 3
    assert result["held"] == 1
    assert result["status"] == "COMPLETED_WITH_ROW_BLOCKERS"
    assert any("STRIKES_THROWN:PROP_HISTORY_INSUFFICIENT" in blocker for blocker in result["blockers"])
    assert {row[0]["stat_type"] for row in db.writes} == {
        "PITCHER_STRIKEOUTS",
        "PITCHING_OUTS",
        "BALLS_THROWN",
    }


def test_receipts_are_route_distinct_even_when_lines_match():
    candidate = _candidate()
    strikeout = subject._base_receipt(candidate, "PITCHER_STRIKEOUTS")
    outs = subject._base_receipt(candidate, "PITCHING_OUTS")
    assert strikeout["stat_type"] != outs["stat_type"]
    assert strikeout["can_execute"] is False
    assert outs["can_execute"] is False
