"""Fail-closed FULL_SEASON schedule coverage and re-ingest concurrency gates.

Independent official schedule evidence (including scheduled/unsettled games) is
the denominator for FULL_SEASON certification. A valid settled V2 subset must
never self-attest completeness. can_execute=false; no probability publication.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import nhl_candidate_pipeline as pipeline
import v17.nhl_fitted_specialist as fitted
import v17.nhl_source_reingest as reingest
from v17.nhl_provenance import SOURCE_PROVENANCE_VERSION

FIXTURE = Path(__file__).parent / "fixtures" / "nhl_2026_27_real_timeline.json"
SEASON = "20262027"
INGEST_SHA = "b" * 40
NOW = datetime(2026, 11, 12, 20, 0, tzinfo=timezone.utc)


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class _Query:
    def __init__(self, db, table):
        self.db = db
        self.table_name = table
        self.filters: list[tuple[str, object]] = []
        self.orders: list[tuple[str, bool]] = []
        self.limit_n: int | None = None
        self.mode = "select"
        self.payload: list[dict] = []
        self.on_conflict = ""
        self.ignore_duplicates = False

    def select(self, *args):
        self.mode = "select"
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def order(self, field, desc=False):
        self.orders.append((field, bool(desc)))
        return self

    def limit(self, value):
        self.limit_n = int(value)
        return self

    def delete(self):
        self.mode = "delete"
        return self

    def upsert(self, payload, *, on_conflict=None, ignore_duplicates=False):
        self.mode = "upsert"
        self.payload = [dict(item) for item in payload]
        self.on_conflict = str(on_conflict or "")
        self.ignore_duplicates = bool(ignore_duplicates)
        return self

    def execute(self):
        rows = self.db.rows.setdefault(self.table_name, [])
        if self.mode == "upsert":
            keys = tuple(key for key in self.on_conflict.split(",") if key)
            inserted: list[dict] = []
            for payload in self.payload:
                duplicate = any(
                    all(existing.get(key) == payload.get(key) for key in keys)
                    for existing in rows
                ) if keys else False
                if duplicate and self.ignore_duplicates:
                    continue
                if duplicate and keys:
                    if self.table_name == fitted.REINGEST_LEASE_TABLE:
                        # Concurrent lease acquisition fails closed instead of
                        # overwriting an active season lock.
                        raise RuntimeError("reingest_lease_conflict")
                    rows[:] = [
                        existing
                        for existing in rows
                        if not all(
                            existing.get(key) == payload.get(key) for key in keys
                        )
                    ]
                row = dict(payload)
                rows.append(row)
                inserted.append(row)
            return SimpleNamespace(data=inserted)
        if self.mode == "delete":
            kept = [
                row
                for row in rows
                if not all(row.get(key) == value for key, value in self.filters)
            ]
            deleted = len(rows) - len(kept)
            rows[:] = kept
            return SimpleNamespace(data=[{"deleted": deleted}])
        matched = [
            row for row in rows
            if all(row.get(key) == value for key, value in self.filters)
        ]
        for field, desc in reversed(self.orders):
            matched.sort(key=lambda row: str(row.get(field) or ""), reverse=desc)
        if self.limit_n is not None:
            matched = matched[: self.limit_n]
        return SimpleNamespace(data=matched)


class _FakeDB:
    def __init__(self):
        self.rows: dict[str, list[dict]] = {}

    def table(self, name):
        return _Query(self, name)


def _v2_row(raw: dict) -> dict:
    home = raw["homeTeam"]["abbrev"]
    uri = f"https://api-web.nhle.com/v1/club-schedule-season/{home}/{SEASON}"
    game = pipeline.normalize_game(
        raw, source_uri=uri, retrieved_at="2026-11-07T00:00:00+00:00"
    )
    assert game is not None
    planned = reingest.build_reingest_rows([game], ingest_code_sha=INGEST_SHA)[0]
    # Receipt construction reads the persisted shape (outcome_json), not the
    # pre-write flat re-ingest row.
    from v17.d1_candidate_registry import source_outcome_json

    return {
        "sport": "NHL",
        "official_event_id": planned["official_event_id"],
        "season": str(planned["season"]),
        "event_start_time": planned["event_start_time"],
        "home_participant": planned["home_team"],
        "away_participant": planned["away_team"],
        "outcome_json": source_outcome_json(planned),
        "source_payload_sha256": planned["source_payload_sha256"],
    }


def _scheduled_raw(*, game_id: str, start: str, home="BOS", away="TOR") -> dict:
    return {
        "id": int(game_id),
        "season": int(SEASON),
        "gameType": 2,
        "gameState": "FUT",
        "startTimeUTC": start,
        "neutralSite": False,
        "homeTeam": {"abbrev": home},
        "awayTeam": {"abbrev": away},
    }


def test_partial_settled_subset_cannot_mint_full_season_receipt():
    """RED: verified V2 subset alone must not certify FULL_SEASON."""
    fixture = _fixture()
    verified = [_v2_row(raw) for raw in fixture["games"][:8]]
    # Independent schedule knows more settled games than the verified subset.
    coverage = fitted.build_independent_schedule_coverage(
        [
            {
                "official_event_id": str(raw["id"]),
                "event_start_time": raw["startTimeUTC"],
                "game_state": raw["gameState"],
                "game_type": raw["gameType"],
            }
            for raw in fixture["games"]
        ]
        + [
            {
                "official_event_id": "2026029999",
                "event_start_time": "2026-12-01T00:00:00+00:00",
                "game_state": "FUT",
                "game_type": 2,
            }
        ],
        season=SEASON,
        retrieved_at=NOW.isoformat(),
    )
    with pytest.raises(fitted.NHLFittedSpecialistError) as caught:
        fitted.build_reingest_timeline_receipt(
            verified,
            season=SEASON,
            ingest_code_sha=INGEST_SHA,
            schedule_coverage=coverage,
        )
    assert "NHL_SOURCE_TIMELINE_INCOMPLETE" in caught.value.blockers
    assert caught.value.detail["reason"] == "SOURCE_TIMELINE_SCHEDULE_COVERAGE_INCOMPLETE"
    assert caught.value.detail["missing_settled_event_n"] > 0


def test_missing_schedule_coverage_fails_closed():
    fixture = _fixture()
    verified = [_v2_row(raw) for raw in fixture["games"]]
    with pytest.raises(fitted.NHLFittedSpecialistError) as caught:
        fitted.build_reingest_timeline_receipt(
            verified,
            season=SEASON,
            ingest_code_sha=INGEST_SHA,
            schedule_coverage=None,
        )
    assert caught.value.detail["reason"] == "SOURCE_TIMELINE_SCHEDULE_COVERAGE_MISSING"


def test_full_season_receipt_requires_matching_settled_ids_and_keeps_scheduled():
    fixture = _fixture()
    verified = [_v2_row(raw) for raw in fixture["games"]]
    scheduled = _scheduled_raw(
        game_id="2026029999", start="2026-12-01T00:00:00+00:00"
    )
    coverage = fitted.build_independent_schedule_coverage(
        [
            {
                "official_event_id": str(raw["id"]),
                "event_start_time": raw["startTimeUTC"],
                "game_state": raw["gameState"],
                "game_type": raw["gameType"],
            }
            for raw in fixture["games"]
        ]
        + [
            {
                "official_event_id": str(scheduled["id"]),
                "event_start_time": scheduled["startTimeUTC"],
                "game_state": scheduled["gameState"],
                "game_type": scheduled["gameType"],
            }
        ],
        season=SEASON,
        retrieved_at=NOW.isoformat(),
    )
    receipt = fitted.build_reingest_timeline_receipt(
        verified,
        season=SEASON,
        ingest_code_sha=INGEST_SHA,
        schedule_coverage=coverage,
    )
    assert receipt["season_scope"] == "FULL_SEASON"
    assert receipt["provenance_complete"] is True
    assert len(receipt["settled_events"]) == 32
    assert len(receipt["scheduled_events"]) == 1
    assert receipt["scheduled_events"][0]["official_event_id"] == "2026029999"
    assert receipt["schedule_digest"] == coverage["schedule_digest"]
    assert receipt["can_execute"] is False


def test_conflicting_schedule_identity_fails_closed():
    with pytest.raises(fitted.NHLFittedSpecialistError) as caught:
        fitted.build_independent_schedule_coverage(
            [
                {
                    "official_event_id": "2026020001",
                    "event_start_time": "2026-10-01T00:00:00+00:00",
                    "game_state": "OFF",
                    "game_type": 2,
                },
                {
                    "official_event_id": "2026020001",
                    "event_start_time": "2026-10-02T00:00:00+00:00",
                    "game_state": "OFF",
                    "game_type": 2,
                },
            ],
            season=SEASON,
            retrieved_at=NOW.isoformat(),
        )
    assert caught.value.detail["reason"] == "SOURCE_SCHEDULE_EVENT_IDENTITY_CONFLICT"


def test_persist_requires_schedule_digest_and_scheduled_events():
    fixture = _fixture()
    verified = [_v2_row(raw) for raw in fixture["games"]]
    coverage = fitted.build_independent_schedule_coverage(
        [
            {
                "official_event_id": str(raw["id"]),
                "event_start_time": raw["startTimeUTC"],
                "game_state": raw["gameState"],
                "game_type": raw["gameType"],
            }
            for raw in fixture["games"]
        ],
        season=SEASON,
        retrieved_at=NOW.isoformat(),
    )
    receipt = fitted.build_reingest_timeline_receipt(
        verified,
        season=SEASON,
        ingest_code_sha=INGEST_SHA,
        schedule_coverage=coverage,
    )
    broken = {**receipt, "schedule_digest": ""}
    with pytest.raises(fitted.NHLFittedSpecialistError) as caught:
        fitted.persist_nhl_season_timeline_receipt(_FakeDB(), broken)
    assert caught.value.detail["reason"] == "SOURCE_TIMELINE_SCHEDULE_DIGEST_MISSING"


def test_concurrent_reingest_lease_serializes_writers(monkeypatch):
    """Second writer for the same season must fail closed, not race."""
    db = _FakeDB()
    first = fitted.acquire_nhl_reingest_season_lease(
        db, season=SEASON, lease_token="writer-a", now_utc=NOW
    )
    assert first["status"] == "NHL_SOURCE_REINGEST_LEASE_ACQUIRED"
    second = fitted.acquire_nhl_reingest_season_lease(
        db, season=SEASON, lease_token="writer-b", now_utc=NOW
    )
    assert second["status"] == "BLOCKED"
    assert second["code"] == "NHL_SOURCE_REINGEST_IN_PROGRESS"
    fitted.release_nhl_reingest_season_lease(
        db, season=SEASON, lease_token="writer-a"
    )
    third = fitted.acquire_nhl_reingest_season_lease(
        db, season=SEASON, lease_token="writer-b", now_utc=NOW
    )
    assert third["status"] == "NHL_SOURCE_REINGEST_LEASE_ACQUIRED"


def test_source_rights_hold_blocks_live_write_without_approval(monkeypatch):
    monkeypatch.delenv("WOW_NHL_SOURCE_RIGHTS_APPROVED", raising=False)
    result = reingest.run_nhl_source_reingest(
        _FakeDB(),
        season=int(SEASON),
        teams=("BOS",),
        session=SimpleNamespace(get=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no fetch"))),
        ingest_code_sha=INGEST_SHA,
        dry_run=False,
        rights_approved=False,
    )
    assert result["status"] == "BLOCKED"
    assert result["code"] == "NHL_SOURCE_RIGHTS_HOLD"
    assert result["can_execute"] is False


def test_migration_forward_and_rollback_contract():
    root = Path(__file__).resolve().parents[1] / "migrations"
    forward = (root / "20261110_nhl_season_timeline_receipts.sql").read_text()
    rollback = (root / "20261110_nhl_season_timeline_receipts_rollback.sql").read_text()
    assert "wow_nhl_season_timeline_receipts" in forward
    assert "scheduled_events" in forward
    assert "schedule_digest" in forward
    assert "schedule_retrieved_at" in forward
    assert "wow_nhl_source_reingest_leases" in forward
    assert "can_execute boolean not null default false check (can_execute = false)" in forward
    assert "drop table if exists public.wow_nhl_source_reingest_leases" in rollback
    assert "drop table if exists public.wow_nhl_season_timeline_receipts" in rollback
    assert "probability_publishable" not in forward.lower()
    assert "rank_eligible=true" not in forward.lower()


def test_schedule_digest_is_stable_for_identical_coverage():
    events = [
        {
            "official_event_id": "2026020001",
            "event_start_time": "2026-10-01T00:00:00+00:00",
            "game_state": "OFF",
            "game_type": 2,
        },
        {
            "official_event_id": "2026029999",
            "event_start_time": "2026-12-01T00:00:00+00:00",
            "game_state": "FUT",
            "game_type": 2,
        },
    ]
    first = fitted.build_independent_schedule_coverage(
        events, season=SEASON, retrieved_at=NOW.isoformat()
    )
    second = fitted.build_independent_schedule_coverage(
        list(reversed(events)), season=SEASON, retrieved_at=NOW.isoformat()
    )
    assert first["schedule_digest"] == second["schedule_digest"]
    assert len(first["schedule_digest"]) == 64
    # Digest must not be a hand-waved boolean; it binds the exact event set.
    payload = json.dumps(
        {
            "season": SEASON,
            "events": sorted(
                (
                    {
                        "official_event_id": item["official_event_id"],
                        "event_start_time": item["event_start_time"],
                        "game_state": item["game_state"],
                    }
                    for item in events
                ),
                key=lambda row: (row["event_start_time"], row["official_event_id"]),
            ),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    assert first["schedule_digest"] == hashlib.sha256(payload.encode("utf-8")).hexdigest()
