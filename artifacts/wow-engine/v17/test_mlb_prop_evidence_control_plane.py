from __future__ import annotations

from datetime import datetime, timezone

import pytest

from v17 import mlb_prop_evidence_control_plane as subject

NOW = datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc)
EVENT = datetime(2026, 9, 28, 23, 0, tzinfo=timezone.utc)


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db):
        self.db = db
        self.filters = {}
        self.pending = None

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def limit(self, _n):
        return self

    def upsert(self, row, on_conflict=None):
        self.pending = dict(row)
        self.db.upserts.append((self.pending, on_conflict))
        return self

    def execute(self):
        if self.pending is not None:
            return _Result([self.pending])
        identity = (
            self.filters.get("event_id"),
            self.filters.get("sport"),
            self.filters.get("player"),
            self.filters.get("stat_type"),
        )
        return _Result([{"source_snapshot_id": "existing"}] if identity in self.db.existing else [])


class _DB:
    def __init__(self, existing=None):
        self.existing = set(existing or [])
        self.upserts = []

    def table(self, name):
        assert name == "wow_prop_evidence_snapshots"
        return _Query(self)


def _candidate(player="Starter One", player_id="1001"):
    return {
        "event_id": "MLB:123456",
        "official_game_pk": "123456",
        "event_start_time": EVENT.isoformat(),
        "player": player,
        "player_id": player_id,
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


def _install_common(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(subject, "_request_schedule", lambda *_a, **_k: {"dates": []})
    monkeypatch.setattr(subject, "_schedule_pitchers", lambda *_a, **_k: [_candidate(), _candidate("Starter Two", "2002")])
    monkeypatch.setattr(subject, "auto_hydrate_prop_evidence", lambda **kwargs: _raw(kwargs["stat_type"]))
    monkeypatch.setattr(subject, "_validate_evidence", lambda row, stat: row.evidence.model_dump())
    monkeypatch.setattr(
        subject,
        "_snapshot_payload",
        lambda row, normalized: (
            f"snapshot-{row.player}-{row.stat_type}",
            "fp",
            {
                "source_snapshot_id": f"snapshot-{row.player}-{row.stat_type}",
                "event_id": row.event_id,
                "event_start_time": row.event_start_time,
                "sport": row.sport,
                "player": row.player,
                "stat_type": row.stat_type,
                "line": row.line,
            },
        ),
    )


def test_rotating_window_is_bounded_and_fail_closed(monkeypatch: pytest.MonkeyPatch):
    _install_common(monkeypatch)
    db = _DB()
    result = subject.acquire_mlb_forward_evidence_batch(
        subject.MLBForwardEvidenceRequest(
            requested_date="2026-09-28",
            candidate_offset=2,
            max_candidates=3,
        ),
        db=db,
        now=NOW,
        http_get=lambda *_a, **_k: object(),
    )
    assert result["total_candidates"] == 8
    assert result["window_candidate_n"] == 3
    assert result["attempted"] == 3
    assert result["persisted"] == 3
    assert result["next_offset"] == 5
    assert result["probability_publishable"] is False
    assert result["automatic_certification"] is False
    assert result["automatic_promotion"] is False
    assert result["can_execute"] is False


def test_existing_exact_event_player_stat_skips_hydration(monkeypatch: pytest.MonkeyPatch):
    _install_common(monkeypatch)
    calls = []
    monkeypatch.setattr(subject, "auto_hydrate_prop_evidence", lambda **kwargs: calls.append(kwargs) or _raw(kwargs["stat_type"]))
    db = _DB(existing={
        ("MLB:123456", "MLB", "Starter One", "PITCHER_STRIKEOUTS"),
        ("MLB:123456", "MLB", "Starter One", "PITCHING_OUTS"),
    })
    result = subject.acquire_mlb_forward_evidence_batch(
        subject.MLBForwardEvidenceRequest(
            requested_date="2026-09-28",
            candidate_offset=0,
            max_candidates=4,
        ),
        db=db,
        now=NOW,
        http_get=lambda *_a, **_k: object(),
    )
    assert result["attempted"] == 4
    assert result["already_captured"] == 2
    assert result["persisted"] == 2
    assert len(calls) == 2


def test_typed_hydration_failure_is_row_scoped(monkeypatch: pytest.MonkeyPatch):
    _install_common(monkeypatch)

    def hydrate(**kwargs):
        if kwargs["stat_type"] == "STRIKES_THROWN":
            raise subject.PropAutoHydrationError("PROP_HISTORY_INSUFFICIENT", "held")
        return _raw(kwargs["stat_type"])

    monkeypatch.setattr(subject, "auto_hydrate_prop_evidence", hydrate)
    db = _DB()
    result = subject.acquire_mlb_forward_evidence_batch(
        subject.MLBForwardEvidenceRequest(
            requested_date="2026-09-28",
            candidate_offset=0,
            max_candidates=4,
        ),
        db=db,
        now=NOW,
        http_get=lambda *_a, **_k: object(),
    )
    assert result["attempted"] == 4
    assert result["persisted"] == 3
    assert result["held"] == 1
    assert result["status"] == "COMPLETED_WITH_ROW_BLOCKERS"
    assert any("STRIKES_THROWN:PROP_HISTORY_INSUFFICIENT" in value for value in result["blockers"])
    assert result["can_execute"] is False


def test_flatten_covers_only_certified_pitcher_workload_routes():
    rows = subject._flatten([_candidate()])
    assert [stat for _, stat in rows] == list(subject.STAT_TYPES)
    assert set(subject.STAT_TYPES) == {
        "PITCHER_STRIKEOUTS",
        "PITCHING_OUTS",
        "STRIKES_THROWN",
        "BALLS_THROWN",
    }

class _WriteFailQuery(_Query):
    def execute(self):
        if self.pending is not None:
            raise RuntimeError("synthetic write failure")
        return super().execute()


class _WriteFailDB(_DB):
    def table(self, name):
        assert name == "wow_prop_evidence_snapshots"
        return _WriteFailQuery(self)


def test_empty_strikeout_history_is_prewrite_hold_not_snapshot_write_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    _install_common(monkeypatch)

    def hydrate(**kwargs):
        raw = _raw(kwargs["stat_type"])
        if kwargs["stat_type"] == "PITCHER_STRIKEOUTS":
            raw["game_log"] = []
        return raw

    monkeypatch.setattr(subject, "auto_hydrate_prop_evidence", hydrate)
    db = _DB()
    result = subject.acquire_mlb_forward_evidence_batch(
        subject.MLBForwardEvidenceRequest(
            requested_date="2026-09-28",
            candidate_offset=0,
            max_candidates=1,
        ),
        db=db,
        now=NOW,
        http_get=lambda *_a, **_k: object(),
    )

    assert result["attempted"] == 1
    assert result["held"] == 1
    assert result["persisted"] == 0
    assert result["snapshot_write_failed"] == 0
    assert result["status"] == "COMPLETED_WITH_ROW_BLOCKERS"
    assert result["blockers"] == [
        "Starter One:PITCHER_STRIKEOUTS:MLB_FORWARD_EVIDENCE_PREWRITE_ERROR:ValueError"
    ]
    assert db.upserts == []
    assert result["can_execute"] is False


def test_actual_snapshot_upsert_failure_owns_run_invalid_write_status(
    monkeypatch: pytest.MonkeyPatch,
):
    _install_common(monkeypatch)
    db = _WriteFailDB()
    result = subject.acquire_mlb_forward_evidence_batch(
        subject.MLBForwardEvidenceRequest(
            requested_date="2026-09-28",
            candidate_offset=0,
            max_candidates=1,
        ),
        db=db,
        now=NOW,
        http_get=lambda *_a, **_k: object(),
    )

    assert result["attempted"] == 1
    assert result["held"] == 1
    assert result["persisted"] == 0
    assert result["snapshot_write_failed"] == 1
    assert result["status"] == "RUN_INVALID_PROP_SNAPSHOT_WRITE_FAILURE"
    assert result["blockers"] == [
        "Starter One:PITCHER_STRIKEOUTS:MLB_FORWARD_SNAPSHOT_WRITE_FAILED:RuntimeError"
    ]
    assert result["can_execute"] is False

