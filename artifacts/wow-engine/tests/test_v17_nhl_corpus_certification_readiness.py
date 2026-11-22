"""Fail-closed G1 corpus certification readiness for NHL Fix A.

Never certifies, promotes, publishes, or enables execution. Rights HOLD and
missing provenance/timeline/current-season hydration remain typed blockers.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import v17.nhl_corpus_certification_readiness as readiness
from v17.nhl_provenance import (
    SOURCE_PAYLOAD_HASH_SCOPE,
    SOURCE_PROVENANCE_VERSION,
    provenance_envelope_hash,
)


class _Query:
    def __init__(self, db, table):
        self.db = db
        self.table_name = table
        self.filters: list[tuple[str, object]] = []
        self.orders: list[tuple[str, bool]] = []
        self.limit_n: int | None = None
        self.range_: tuple[int, int] | None = None

    def select(self, *args):
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

    def range(self, start, end):
        self.range_ = (int(start), int(end))
        return self

    def execute(self):
        rows = [dict(item) for item in self.db.rows.get(self.table_name, [])]
        for key, value in self.filters:
            rows = [row for row in rows if row.get(key) == value]
        for field, desc in reversed(self.orders):
            rows.sort(key=lambda row: row.get(field) or "", reverse=desc)
        if self.range_ is not None:
            start, end = self.range_
            rows = rows[start : end + 1]
        elif self.limit_n is not None:
            rows = rows[: self.limit_n]
        return SimpleNamespace(data=rows)


class _FakeDB:
    def __init__(self, *, events=None, receipts=None):
        self.rows = {
            "wow_d1_source_events": list(events or []),
            "wow_nhl_season_timeline_receipts": list(receipts or []),
        }

    def table(self, name):
        return _Query(self, name)


NOW = datetime(2026, 11, 22, 17, 0, tzinfo=timezone.utc)


def _event(season: str, event_id: str, *, provenanced: bool = True) -> dict:
    start = "2024-10-10T00:00:00+00:00"
    home, away = "BOS", "PIT"
    home_score, away_score = 3, 2
    outcome = {
        "home_score": home_score,
        "away_score": away_score,
        "positive_outcome": True,
    }
    payload_hash = "c" * 64
    if provenanced:
        commitment = "b" * 64
        outcome.update(
            {
                "game_type": 2,
                "neutral_site": False,
                "game_state": "FINAL",
                "provenance_version": SOURCE_PROVENANCE_VERSION,
                "ingest_code_sha": "a" * 40,
                "source_payload_commitment": commitment,
                "payload_hash_scope": SOURCE_PAYLOAD_HASH_SCOPE,
            }
        )
        payload_hash = provenance_envelope_hash(
            official_event_id=event_id,
            season=season,
            event_start_time=start,
            home_team=home,
            away_team=away,
            home_score=home_score,
            away_score=away_score,
            game_type=2,
            game_state="FINAL",
            neutral_site=False,
        )
    return {
        "sport": "NHL",
        "season": season,
        "official_event_id": event_id,
        "event_start_time": start,
        "home_participant": home,
        "away_participant": away,
        "outcome_json": outcome,
        "source_provider": "NHL_PUBLIC_WEB_API",
        "source_uri": f"https://api-web.nhle.com/v1/club-schedule-season/{home}/{season}",
        "source_payload_sha256": payload_hash,
    }


def _receipt(
    season: str,
    *,
    verified_at: str | None = None,
    settled_events=None,
    scheduled_events=None,
) -> dict:
    return {
        "season": season,
        "provenance_complete": True,
        "season_scope": "FULL_SEASON",
        "settled_events": list(settled_events or []),
        "scheduled_events": list(scheduled_events or []),
        "schedule_digest": "d" * 64,
        "schedule_retrieved_at": verified_at or NOW.isoformat(),
        "verified_at": verified_at or NOW.isoformat(),
        "ingest_code_sha": "a" * 40,
        "source": "NHL_SOURCE_REINGEST",
    }


def test_readiness_never_authorizes_execution_or_publication():
    assert readiness.CAN_EXECUTE is False
    assert readiness.PROBABILITY_PUBLISHABLE is False
    assert readiness.REQUIRED_CORPUS_ROWS == 6128
    assert readiness.certification_training_season_ids() == (
        20212022,
        20222023,
        20232024,
        20242025,
        20252026,
    )


def test_g1a_blocks_without_provenance_and_timeline():
    db = _FakeDB(
        events=[_event("20212022", "2021020001", provenanced=False)],
        receipts=[],
    )
    report = readiness.assess_season_g1a(db, season=20212022)
    assert report["g1a_ready"] is False
    assert "NHL_SOURCE_PROVENANCE_INCOMPLETE" in report["blockers"]
    assert "NHL_SOURCE_TIMELINE_RECEIPT_MISSING" in report["blockers"]
    assert report["can_execute"] is False


def test_g1a_rejects_partial_v2_envelope_that_specialist_cannot_consume():
    row = _event("20212022", "2021020001", provenanced=True)
    row["outcome_json"]["source_payload_commitment"] = "not-a-hash"
    report = readiness.assess_season_g1a(
        _FakeDB(events=[row], receipts=[_receipt("20212022")]),
        season=20212022,
    )
    assert report["g1a_ready"] is False
    assert report["provenanced_event_n"] == 0
    assert "NHL_SOURCE_PROVENANCE_INCOMPLETE" in report["blockers"]


def test_g1b_blocks_when_current_season_missing():
    report = readiness.assess_season_g1b(_FakeDB(), season=20262027, as_of_utc=NOW)
    assert report["g1b_ready"] is False
    assert "NHL_CURRENT_SEASON_EVENTS_MISSING" in report["blockers"]
    assert "NHL_CURRENT_SEASON_TIMELINE_RECEIPT_MISSING" in report["blockers"]


def test_g1b_blocks_stale_receipt_and_unresolved_scheduled_coverage():
    event = _event("20262027", "2026020001", provenanced=True)
    stale = _receipt(
        "20262027",
        verified_at=(NOW - timedelta(hours=25)).isoformat(),
        settled_events=[
            {
                "official_event_id": "2026020001",
                "event_start_time": "2026-11-20T00:00:00+00:00",
            }
        ],
        scheduled_events=[
            {
                "official_event_id": "2026020099",
                "event_start_time": "2026-11-21T00:00:00+00:00",
                "game_state": "FUT",
            }
        ],
    )
    report = readiness.assess_season_g1b(
        _FakeDB(events=[event], receipts=[stale]),
        season=20262027,
        as_of_utc=NOW,
    )
    assert report["g1b_ready"] is False
    assert "NHL_CURRENT_SEASON_TIMELINE_RECEIPT_STALE" in report["blockers"]
    assert "NHL_CURRENT_SEASON_TIMELINE_COVERAGE_INCOMPLETE" in report["blockers"]


def test_aggregate_readiness_holds_on_rights_even_when_rows_complete():
    events = []
    receipts = []
    for season in readiness.certification_training_season_ids():
        token = str(season)
        events.append(_event(token, f"{token}0001", provenanced=True))
        receipts.append(_receipt(token))
    events.append(_event("20262027", "202620270001", provenanced=True))
    receipts.append(
        _receipt(
            "20262027",
            settled_events=[
                {
                    "official_event_id": "202620270001",
                    "event_start_time": "2026-11-20T00:00:00+00:00",
                }
            ],
            scheduled_events=[
                {
                    "official_event_id": "202620279999",
                    "event_start_time": "2026-11-23T00:00:00+00:00",
                    "game_state": "FUT",
                }
            ],
        )
    )
    db = _FakeDB(events=events, receipts=receipts)

    blocked = readiness.assess_nhl_corpus_certification_readiness(
        db, current_season=20262027, rights_approved=False, as_of_utc=NOW
    )
    assert blocked["status"] == "BLOCKED"
    assert blocked["g1a_ready"] is True
    assert blocked["g1b_ready"] is True
    assert blocked["source_rights_approved"] is False
    assert "NHL_SOURCE_RIGHTS_HOLD" in blocked["blockers"]
    assert blocked["corpus_rebuild_ready"] is False
    assert blocked["disposition"] == "WORKING_NOT_COMPLETE"
    assert blocked["can_execute"] is False
    assert blocked["probability_publishable"] is False

    ready = readiness.assess_nhl_corpus_certification_readiness(
        db, current_season=20262027, rights_approved=True, as_of_utc=NOW
    )
    assert ready["status"] == "G1_CORPUS_READY"
    assert ready["corpus_rebuild_ready"] is True
    assert ready["can_execute"] is False


def test_live_rebuild_fails_closed_without_rights(monkeypatch):
    calls: list[dict] = []

    def _fake_reingest(*args, **kwargs):
        calls.append(dict(kwargs))
        return {
            "status": "NHL_SOURCE_REINGEST_DRY_RUN_READY",
            "season": str(kwargs.get("season")),
            "can_execute": False,
            "probability_publishable": False,
        }

    monkeypatch.setattr(readiness, "run_nhl_source_reingest", _fake_reingest)
    result = readiness.run_nhl_g1_corpus_rebuild(
        _FakeDB(),
        training_start_years=(2021,),
        current_season=20262027,
        dry_run=False,
        rights_approved=False,
    )
    assert result["status"] == "BLOCKED"
    assert result["code"] == "NHL_SOURCE_RIGHTS_HOLD"
    assert calls == []
    assert result["can_execute"] is False


def test_dry_run_rebuild_invokes_each_season_without_claiming_certification(monkeypatch):
    seen: list[int] = []

    def _fake_reingest(db, **kwargs):
        seen.append(int(kwargs["season"]))
        return {
            "status": "NHL_SOURCE_REINGEST_DRY_RUN_READY",
            "season": str(kwargs["season"]),
            "can_execute": False,
            "probability_publishable": False,
        }

    monkeypatch.setattr(readiness, "run_nhl_source_reingest", _fake_reingest)
    monkeypatch.setattr(
        readiness,
        "assess_nhl_corpus_certification_readiness",
        lambda *args, **kwargs: {
            "status": "BLOCKED",
            "corpus_rebuild_ready": False,
            "blockers": ["NHL_SOURCE_PROVENANCE_INCOMPLETE"],
            "can_execute": False,
            "probability_publishable": False,
        },
    )
    result = readiness.run_nhl_g1_corpus_rebuild(
        _FakeDB(),
        training_start_years=(2021, 2022),
        current_season=20262027,
        dry_run=True,
        rights_approved=False,
    )
    assert result["status"] == "NHL_G1_CORPUS_REBUILD_DRY_RUN_READY"
    assert seen == [20212022, 20222023, 20262027]
    assert result["automatic_certification"] is False
    assert result["disposition"] == "WORKING_NOT_COMPLETE"
    assert result["can_execute"] is False


def test_readiness_route_requires_auth_and_stays_inert():
    app = FastAPI()

    def _deny():
        raise HTTPException(status_code=401, detail="denied")

    readiness.install_nhl_corpus_certification_readiness_routes(
        app,
        auth_dependency=_deny,
        db_client_fn=lambda: _FakeDB(),
    )
    client = TestClient(app)
    response = client.get("/internal/v17/nhl-corpus-certification-readiness")
    assert response.status_code == 401
