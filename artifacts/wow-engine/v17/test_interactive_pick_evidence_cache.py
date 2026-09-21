from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import v17.interactive_pick_hydration as subject
from pick_request_runtime_core import PickRequestBatch, RawPropEvidence


class _BaseApi:
    @staticmethod
    def _controlling_specialist_provider(_sport, _stat):
        return {"controlling_specialist": "wow.mlb-prop-expert"}


class _Query:
    def __init__(self, record):
        self.record = record

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        return SimpleNamespace(data=[self.record] if self.record is not None else [])


class _Db:
    def __init__(self, record):
        self.record = record

    def table(self, _name):
        return _Query(self.record)


class _Prod:
    PROP_CAPABILITY_KEY = "PROP"
    base_api = _BaseApi()

    def __init__(self, record):
        self.record = record

    @staticmethod
    def _runtime_capability(_key):
        return {"capability_status": "AVAILABLE"}

    def get_client(self):
        return _Db(self.record)


class _Market:
    def __init__(self, record):
        self.prod = _Prod(record)

    @staticmethod
    def _prop_route_artifact(_sport, _stat):
        return {"ok": True, "code": "PROP_CERTIFIED_MODEL_ARTIFACT_READY"}


def _batch(now: datetime) -> PickRequestBatch:
    event_start = now + timedelta(hours=2)
    return PickRequestBatch(
        response_mode="COMPACT",
        rows=[
            {
                "row_key": "cached-more",
                "event_id": "MLB:CACHE:1",
                "event_start_time": event_start.isoformat(),
                "sport": "MLB",
                "player": "Cache Pitcher",
                "stat_type": "PITCHER_STRIKEOUTS",
                "line": 4.5,
                "direction": "MORE",
                "source_type": "NORMALIZED",
            },
            {
                "row_key": "cached-less",
                "event_id": "MLB:CACHE:1",
                "event_start_time": event_start.isoformat(),
                "sport": "MLB",
                "player": "Cache Pitcher",
                "stat_type": "PITCHER_STRIKEOUTS",
                "line": 4.5,
                "direction": "LESS",
                "source_type": "NORMALIZED",
            },
        ],
    )


def _record(now: datetime, *, age_seconds: int) -> dict:
    captured = now - timedelta(seconds=age_seconds)
    event_start = now + timedelta(hours=2)
    return {
        "source_snapshot_id": "snapshot-cache-1",
        "captured_at": captured.isoformat(),
        "event_id": "MLB:CACHE:1",
        "event_start_time": event_start.isoformat(),
        "sport": "MLB",
        "player": "Cache Pitcher",
        "stat_type": "PITCHER_STRIKEOUTS",
        "line": 4.5,
        "game_log": [5.0] * 10,
        "box_score_log": [{"game": index} for index in range(10)],
        "role_status": {"status": "STARTER"},
        "role_timestamp": captured.isoformat(),
        "opportunity_ledger": {"status": "PASS"},
        "source_timestamps": {"official": captured.isoformat()},
        "evidence_version": "PROP_EVIDENCE_V1",
        "hydration_status": "PASS",
        "blockers": [],
    }


def _fresh_external_evidence(now: datetime) -> RawPropEvidence:
    captured = now - timedelta(seconds=5)
    return RawPropEvidence(
        captured_at=captured.isoformat(),
        game_log=[6.0] * 10,
        box_score_log=[{"game": index} for index in range(10)],
        role_status={"status": "STARTER"},
        role_timestamp=captured.isoformat(),
        opportunity_ledger={"status": "PASS"},
        source_timestamps={"official": captured.isoformat()},
        rate_provenance="OFFICIAL_TEST",
    )


def test_fresh_immutable_snapshot_skips_external_hydration_for_direction_pair(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setenv("WOW_INTERACTIVE_PROP_HYDRATION_WORKERS", "4")
    monkeypatch.setenv("WOW_INTERACTIVE_PROP_EVIDENCE_MAX_AGE_SECONDS", "300")

    def should_not_run(_row):
        raise AssertionError("fresh immutable evidence must avoid duplicate external acquisition")

    monkeypatch.setattr(subject, "_hydrate", should_not_run)
    prepared = subject.prehydrate_batch(_batch(now), market_api=_Market(_record(now, age_seconds=30)))
    assert prepared.rows[0].evidence is not None
    assert prepared.rows[1].evidence is prepared.rows[0].evidence
    assert prepared.rows[0].evidence.rate_provenance.startswith("REUSED_IMMUTABLE_SNAPSHOT:")


def test_stale_snapshot_falls_back_to_external_hydration(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setenv("WOW_INTERACTIVE_PROP_HYDRATION_WORKERS", "4")
    monkeypatch.setenv("WOW_INTERACTIVE_PROP_EVIDENCE_MAX_AGE_SECONDS", "60")
    called = {"count": 0}

    def hydrate(_row):
        called["count"] += 1
        return _fresh_external_evidence(now)

    monkeypatch.setattr(subject, "_hydrate", hydrate)
    prepared = subject.prehydrate_batch(_batch(now), market_api=_Market(_record(now, age_seconds=120)))
    assert called["count"] == 1
    assert prepared.rows[0].evidence is not None
    assert prepared.rows[1].evidence is prepared.rows[0].evidence
    assert prepared.rows[0].evidence.rate_provenance == "OFFICIAL_TEST"
