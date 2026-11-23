"""Regression: NFL Daily autonomous prop discovery and new-vs-reused accounting.

The incident's Daily run reported ``NFL autonomous prop discovery: false``,
``new candidates acquired: 0`` and ``previously stored slate rows reused: 3``.
These tests pin the repaired producer boundary: NFL candidates are discovered
and persisted from the certified ESPN event/roster route map, and a reused
canonical snapshot is never reported as new acquisition.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from v17 import daily_prop_discovery as discovery
from v17 import nfl_daily_prop_acquisition as subject

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
KICKOFF = datetime(2026, 11, 2, 23, 0, tzinfo=timezone.utc)
SLATE_DATE = "2026-11-02"

# median 51.0 -> deterministic half-point discovery line 51.5
GAME_LOG = [40.0, 44.0, 47.0, 49.0, 50.0, 52.0, 55.0, 58.0, 61.0, 65.0]


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

    def limit(self, _value):
        return self

    def upsert(self, row, on_conflict=None):
        self.pending = dict(row)
        self.db.conflicts.append(on_conflict)
        return self

    def execute(self):
        if self.pending is not None:
            self.db.upserts.append(self.pending)
            return _Result([self.pending])
        identity = (
            self.filters.get("event_id"),
            self.filters.get("sport"),
            self.filters.get("player"),
            self.filters.get("stat_type"),
        )
        return _Result(
            [{"source_snapshot_id": "existing"}] if identity in self.db.existing else []
        )


class _DB:
    def __init__(self, existing=None):
        self.existing = set(existing or ())
        self.upserts = []
        self.conflicts = []

    def table(self, name):
        assert name == "wow_prop_evidence_snapshots"
        return _Query(self)


def _candidate(player="Jaxon Smith-Njigba", stat_type="RECEIVING_YARDS"):
    return {
        "provider_event_id": "401873036",
        "canonical_event_id": "2026_08_CHI_SEA",
        "event_start_time": KICKOFF.isoformat(),
        "teams": [{"team_id": "1", "abbr": "SEA"}, {"team_id": "2", "abbr": "CHI"}],
        "player": player,
        "player_id": player.replace(" ", "-"),
        "position": "WR",
        "team": "SEA",
        "opponent": "CHI",
        "stat_type": stat_type,
    }


def _raw():
    """Minimal governed evidence payload; the validator seam is stubbed below."""
    return {
        "captured_at": NOW.isoformat(),
        "game_log": list(GAME_LOG),
        "box_score_log": [{"game": index} for index in range(10)],
        "role_status": {
            "status": "ACTIVE",
            "opponent": "CHI",
            "provider_event_id": "401873036",
            "verified_canonical_event_id": "2026_08_CHI_SEA",
        },
        "role_timestamp": NOW.isoformat(),
        "opportunity_ledger": {"status": "READY"},
        "source_timestamps": {
            "ESPN_NFL_IDENTITY_SCOREBOARD": NOW.isoformat(),
            "NFLVERSE_WEEKLY_PLAYER_STATS": NOW.isoformat(),
        },
        "evidence_version": "PROP_EVIDENCE_V1",
        "rate_provenance": "NFL_ESPN_IDENTITY_NFLVERSE_STATS_V1",
    }


def _install(monkeypatch, candidates):
    monkeypatch.setattr(subject, "auto_hydrate_prop_evidence", lambda **_kwargs: _raw())
    monkeypatch.setattr(
        subject, "_validate_evidence", lambda row, _stat: {"event_id": row.event_id}
    )
    monkeypatch.setattr(
        subject,
        "_snapshot_payload",
        lambda row, _normalized: (
            f"snapshot-{row.player}-{row.stat_type}",
            "fp",
            {"source_snapshot_id": f"snapshot-{row.player}-{row.stat_type}"},
        ),
    )
    return list(candidates)


def test_discovers_and_persists_new_nfl_candidates(monkeypatch: pytest.MonkeyPatch):
    candidates = _install(monkeypatch, [_candidate()])
    result = subject.acquire_daily_nfl_prop_snapshots(
        db=_DB(),
        requested_date=SLATE_DATE,
        requested_timezone="America/Chicago",
        max_candidates=4,
        now=NOW,
        http_get=lambda *_a, **_k: object(),
        discover_candidates=lambda *_a, **_k: candidates,
    )

    assert result["blockers"] == []
    assert result["status"] == "COMPLETED"
    assert result["attempted"] == 1
    assert result["persisted"] == 1
    assert result["new_candidates_acquired"] == 1
    assert result["reused_snapshot_rows"] == 0
    assert result["source_type"] == "AUTONOMOUS_DISCOVERY"
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False
    receipt = result["receipts"][0]
    assert receipt["write_status"] == "SNAPSHOT_WRITE_SUCCEEDED"
    assert receipt["event_id"] == "2026_08_CHI_SEA"
    assert receipt["line"] == 51.5


def test_existing_snapshot_is_reused_not_reported_as_new(monkeypatch: pytest.MonkeyPatch):
    candidate = _candidate()
    candidates = _install(monkeypatch, [candidate])
    db = _DB(
        existing={
            (
                candidate["canonical_event_id"],
                "NFL",
                candidate["player"],
                candidate["stat_type"],
            )
        }
    )
    result = subject.acquire_daily_nfl_prop_snapshots(
        db=db,
        requested_date=SLATE_DATE,
        requested_timezone="America/Chicago",
        max_candidates=4,
        now=NOW,
        http_get=lambda *_a, **_k: object(),
        discover_candidates=lambda *_a, **_k: candidates,
    )

    assert result["already_captured"] == 1
    assert result["reused_snapshot_rows"] == 1
    assert result["new_candidates_acquired"] == 0
    assert result["persisted"] == 0
    assert db.upserts == []
    assert result["receipts"][0]["write_status"] == "EXISTING_SNAPSHOT_REUSED"


def test_hard_budget_caps_attempts(monkeypatch: pytest.MonkeyPatch):
    candidates = _install(
        monkeypatch,
        [
            _candidate(),
            _candidate("Luther Burden III"),
            _candidate("D.K. Metcalf"),
        ],
    )
    result = subject.acquire_daily_nfl_prop_snapshots(
        db=_DB(),
        requested_date=SLATE_DATE,
        requested_timezone="America/Chicago",
        max_candidates=2,
        now=NOW,
        http_get=lambda *_a, **_k: object(),
        discover_candidates=lambda *_a, **_k: candidates,
    )

    assert result["attempted"] == 2
    assert result["persisted"] == 2


def test_discovery_failure_is_typed_not_empty(monkeypatch: pytest.MonkeyPatch):
    _install(monkeypatch, [])

    def boom(*_args, **_kwargs):
        raise RuntimeError("espn unavailable")

    result = subject.acquire_daily_nfl_prop_snapshots(
        db=_DB(),
        requested_date=SLATE_DATE,
        requested_timezone="America/Chicago",
        max_candidates=4,
        now=NOW,
        http_get=lambda *_a, **_k: object(),
        discover_candidates=boom,
    )

    assert result["status"] == "DATA_UNOBTAINABLE"
    assert result["blockers"] == ["NFL_PROP_DISCOVERY_FAILED:RuntimeError"]


def test_off_slate_kickoff_is_excluded(monkeypatch: pytest.MonkeyPatch):
    late = {**_candidate(), "event_start_time": "2026-11-04T01:00:00+00:00"}
    candidates = _install(monkeypatch, [late])
    result = subject.acquire_daily_nfl_prop_snapshots(
        db=_DB(),
        requested_date=SLATE_DATE,
        requested_timezone="America/Chicago",
        max_candidates=4,
        now=NOW,
        http_get=lambda *_a, **_k: object(),
        discover_candidates=lambda *_a, **_k: candidates,
    )

    assert result["attempted"] == 0
    assert result["discovered_candidates"] == 0
    assert result["status"] == "COMPLETED"


def test_dispatcher_keeps_one_global_budget_and_sums_sports(monkeypatch: pytest.MonkeyPatch):
    seen: list[tuple[str, int]] = []

    def producer_for(sport, attempted, persisted):
        def producer(**kwargs):
            seen.append((sport, kwargs["max_candidates"]))
            return {
                "status": "COMPLETED",
                "attempted": attempted,
                "persisted": persisted,
                "new_candidates_acquired": persisted,
                "reused_snapshot_rows": 0,
                "discovered_candidates": attempted,
                "held": 0,
                "snapshot_write_succeeded": persisted,
                "snapshot_write_failed": 0,
                "explicit_prewrite_exclusions": 0,
                "already_captured": 0,
                "receipts": [],
                "blockers": [],
                "candidate_source": f"{sport}_SOURCE",
                "line_source": "DISCOVERY_LINE",
                "stat_types": [],
                "can_execute": False,
            }

        return producer

    monkeypatch.setitem(discovery._PRODUCERS, "NFL", producer_for("NFL", 2, 2))
    monkeypatch.setitem(discovery._PRODUCERS, "MLB", producer_for("MLB", 1, 1))

    result = discovery.acquire_daily_prop_snapshots(
        db=object(),
        requested_date=SLATE_DATE,
        requested_timezone="America/Chicago",
        max_candidates=3,
        now=NOW,
        http_get=lambda *_a, **_k: object(),
    )

    assert seen[0][0] == "NFL"
    assert sum(budget for _sport, budget in seen) <= 3
    assert result["attempted"] == 3
    assert result["new_candidates_acquired"] == 3
    assert result["zero_new_candidates_reason"] is None
    assert result["candidate_sources"] == {"NFL": "NFL_SOURCE", "MLB": "MLB_SOURCE"}
    assert result["can_execute"] is False


def test_dispatcher_names_why_nothing_new_arrived(monkeypatch: pytest.MonkeyPatch):
    def reused_only(**_kwargs):
        return {
            "status": "COMPLETED",
            "attempted": 2,
            "persisted": 0,
            "already_captured": 2,
            "reused_snapshot_rows": 2,
            "discovered_candidates": 2,
            "held": 0,
            "snapshot_write_succeeded": 0,
            "snapshot_write_failed": 0,
            "explicit_prewrite_exclusions": 0,
            "receipts": [],
            "blockers": [],
            "can_execute": False,
        }

    monkeypatch.setitem(discovery._PRODUCERS, "NFL", reused_only)
    monkeypatch.setitem(discovery._PRODUCERS, "MLB", reused_only)

    result = discovery.acquire_daily_prop_snapshots(
        db=object(),
        requested_date=SLATE_DATE,
        requested_timezone="America/Chicago",
        max_candidates=4,
        now=NOW,
        http_get=lambda *_a, **_k: object(),
    )

    assert result["reused_snapshot_rows"] == 4
    assert result["new_candidates_acquired"] == 0
    assert result["zero_new_candidates_reason"] == "ALL_DISCOVERED_CANDIDATES_ALREADY_CAPTURED"


def test_dispatcher_one_failing_sport_does_not_void_the_lane(monkeypatch: pytest.MonkeyPatch):
    def boom(**_kwargs):
        raise RuntimeError("nfl outage")

    def ok(**_kwargs):
        return {
            "status": "COMPLETED",
            "attempted": 1,
            "persisted": 1,
            "discovered_candidates": 1,
            "held": 0,
            "snapshot_write_succeeded": 1,
            "snapshot_write_failed": 0,
            "explicit_prewrite_exclusions": 0,
            "receipts": [],
            "blockers": [],
            "can_execute": False,
        }

    monkeypatch.setitem(discovery._PRODUCERS, "NFL", boom)
    monkeypatch.setitem(discovery._PRODUCERS, "MLB", ok)

    result = discovery.acquire_daily_prop_snapshots(
        db=object(),
        requested_date=SLATE_DATE,
        requested_timezone="America/Chicago",
        max_candidates=4,
        now=NOW,
        http_get=lambda *_a, **_k: object(),
    )

    assert result["status"] == "COMPLETED"
    assert result["persisted"] == 1
    assert "NFL:DAILY_PROP_DISCOVERY_FAILED:RuntimeError" in result["blockers"]
    assert result["sport_results"]["NFL"]["status"] == "DATA_UNOBTAINABLE"
