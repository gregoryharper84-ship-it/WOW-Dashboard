"""Server-clock attestation for pregame source captures (Incident #1950)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from v17.source_capture_attestation import (
    ATTESTATION_TABLE,
    SourceCaptureAttestationError,
    attest_bound_source_features,
    attest_source_capture,
    feature_payload_sha256,
)

T0 = datetime(2026, 11, 24, 18, 0, tzinfo=timezone.utc)
EVENT_START = T0 + timedelta(hours=6)
RAW = "a" * 64
FEATURES = {"elo_diff": 41.5, "rest_days_delta": 1.0, "home_back_to_back": 0.0}


class _DbError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class FakeAttestationDb:
    """Models the migration: BEFORE INSERT server clock, pregame check, unique key."""

    def __init__(self, server_now: datetime, *, insert_error: Exception | None = None,
                 read_rows: list | None = None):
        self.server_now = server_now
        self.insert_error = insert_error
        self.read_rows = read_rows
        self.rows: list[dict] = []
        self.insert_payloads: list[dict] = []

    def table(self, name):
        assert name == ATTESTATION_TABLE
        return _Query(self)


class _Query:
    def __init__(self, db):
        self.db, self.op, self.row, self.filters = db, None, None, {}

    def insert(self, row):
        self.op, self.row = "insert", dict(row)
        return self

    def select(self, *_args, **_kwargs):
        self.op = "select"
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def execute(self):
        db = self.db
        if self.op == "insert":
            db.insert_payloads.append(dict(self.row))
            if db.insert_error is not None:
                raise db.insert_error
            stored = dict(self.row)
            stored["received_at"] = db.server_now.isoformat()  # trigger: clock_timestamp()
            stored["attestation_id"] = str(uuid.uuid4())
            if datetime.fromisoformat(stored["received_at"]) >= datetime.fromisoformat(
                    stored["event_start_time_utc"]):
                raise _DbError("23514", "violates check constraint source_capture_pregame_only")
            key = ("sport", "official_event_id", "source_snapshot_id", "feature_payload_sha256")
            if any(all(r[k] == stored[k] for k in key) for r in db.rows):
                raise _DbError("23505", "duplicate key value violates unique constraint")
            db.rows.append(stored)
            return SimpleNamespace(data=[stored])
        if db.read_rows is not None:
            return SimpleNamespace(data=db.read_rows)
        rows = [r for r in db.rows if all(str(r.get(k)) == str(v) for k, v in self.filters.items())]
        return SimpleNamespace(data=rows)


def _attest(db, **overrides):
    kwargs = dict(sport="NBA", official_event_id="espn-401810123",
                  event_start_time_utc=EVENT_START.isoformat(), source_raw_sha256=RAW,
                  feature_sha256=feature_payload_sha256(FEATURES), now=T0)
    kwargs.update(overrides)
    return attest_source_capture(db, **kwargs)


def _bound_package(**audit_overrides):
    audit = {
        "source_replay_status": "RAW_CAPTURE_HASH_AND_EVENT_REPLAY_PASS",
        "timestamp_attestation": "NOT_ESTABLISHED",
        "source_raw_sha256": RAW,
        "feature_timestamp": (T0 - timedelta(minutes=1)).isoformat(),
        "dataset_id": "sha256:" + "b" * 64,
    }
    audit.update(audit_overrides)
    return {
        "status": "PASS", "sport": "NBA", "official_event_id": "espn-401810123",
        "event_start_time_utc": EVENT_START.isoformat(),
        "home_team_id": "espn-1", "away_team_id": "espn-2",
        "features": dict(FEATURES), "feature_audit": audit,
        "source_snapshot_id": "sha256:" + RAW,
        "probability_publishable": False, "rank_eligible": False, "can_execute": False,
    }


def test_verified_receipt_uses_server_clock_never_caller_time():
    db = FakeAttestationDb(T0 + timedelta(seconds=30))
    receipt = _attest(db)
    assert receipt["timestamp_attestation"] == "VERIFIED"
    assert receipt["attested_at"] == (T0 + timedelta(seconds=30)).isoformat()
    assert receipt["can_execute"] is False
    assert all("received_at" not in payload for payload in db.insert_payloads)


def test_idempotent_replay_keeps_the_original_server_time():
    db = FakeAttestationDb(T0 + timedelta(seconds=5))
    first = _attest(db)
    db.server_now = T0 + timedelta(hours=1)
    second = _attest(db, now=T0 + timedelta(hours=1))
    assert second["attested_at"] == first["attested_at"]
    assert second["attestation_id"] == first["attestation_id"]
    assert len(db.rows) == 1


def test_server_time_at_or_after_start_is_rejected():
    db = FakeAttestationDb(EVENT_START)  # caller clock is early; server is not
    with pytest.raises(SourceCaptureAttestationError) as err:
        _attest(db)
    assert err.value.code == "SOURCE_CAPTURE_ATTESTATION_WRITE_FAILED"
    assert db.rows == []


def test_readback_mismatch_fails_closed():
    forged = {"attestation_id": "x", "received_at": T0.isoformat(), "sport": "NBA",
              "official_event_id": "espn-401810123", "event_start_time_utc": EVENT_START.isoformat(),
              "source_snapshot_id": "sha256:" + RAW, "source_raw_sha256": "c" * 64,
              "feature_payload_sha256": feature_payload_sha256(FEATURES),
              "attestation_policy": "SUPABASE_SERVER_CLOCK_RECEIPT_V1", "can_execute": False}
    db = FakeAttestationDb(T0, read_rows=[forged])
    with pytest.raises(SourceCaptureAttestationError) as err:
        _attest(db)
    assert err.value.code == "SOURCE_CAPTURE_ATTESTATION_MISMATCH"


def test_implausible_server_clock_is_rejected():
    db = FakeAttestationDb(T0 + timedelta(hours=1))
    with pytest.raises(SourceCaptureAttestationError) as err:
        _attest(db)
    assert err.value.code == "SOURCE_CAPTURE_SERVER_CLOCK_IMPLAUSIBLE"


@pytest.mark.parametrize("overrides,code", [
    ({"source_raw_sha256": "not-a-hash"}, "SOURCE_CAPTURE_HASH_INVALID"),
    ({"feature_sha256": "Z" * 64}, "SOURCE_CAPTURE_HASH_INVALID"),
    ({"sport": "nba league"}, "SOURCE_CAPTURE_IDENTITY_INVALID"),
    ({"event_start_time_utc": "2026-11-24T18:00:00"}, "SOURCE_CAPTURE_EVENT_START_INVALID"),
    ({"now": EVENT_START}, "SOURCE_CAPTURE_EVENT_NOT_PREGAME"),
])
def test_invalid_inputs_never_write(overrides, code):
    db = FakeAttestationDb(T0)
    with pytest.raises(SourceCaptureAttestationError) as err:
        _attest(db, **overrides)
    assert err.value.code == code
    assert db.insert_payloads == []


def test_bound_package_upgrades_only_after_server_receipt():
    db = FakeAttestationDb(T0 + timedelta(seconds=20))
    out = attest_bound_source_features(db, _bound_package(), now=T0)
    audit = out["feature_audit"]
    assert audit["timestamp_attestation"] == "VERIFIED"
    assert audit["as_of"] == (T0 + timedelta(seconds=20)).isoformat()
    assert audit["timestamp_attestation_id"]
    assert out["probability_publishable"] is False and out["can_execute"] is False


def test_write_failure_keeps_not_established():
    db = FakeAttestationDb(T0, insert_error=RuntimeError("table missing"))
    out = attest_bound_source_features(db, _bound_package(), now=T0)
    assert out["feature_audit"]["timestamp_attestation"] == "NOT_ESTABLISHED"
    assert out["feature_audit"]["timestamp_attestation_failure"] == "SOURCE_CAPTURE_ATTESTATION_WRITE_FAILED"


def test_unreplayed_capture_is_never_attested():
    db = FakeAttestationDb(T0)
    out = attest_bound_source_features(
        db, _bound_package(source_replay_status="DERIVED_FROM_SETTLED_ROWS_RESEARCH_ONLY"), now=T0)
    assert out["feature_audit"]["timestamp_attestation"] == "NOT_ESTABLISHED"
    assert out["feature_audit"]["timestamp_attestation_failure"] == "SOURCE_CAPTURE_REPLAY_NOT_VERIFIED"
    assert db.insert_payloads == []


def test_snapshot_id_must_match_raw_hash():
    db = FakeAttestationDb(T0)
    package = _bound_package()
    package["source_snapshot_id"] = "sha256:" + "d" * 64
    out = attest_bound_source_features(db, package, now=T0)
    assert out["feature_audit"]["timestamp_attestation_failure"] == "SOURCE_CAPTURE_SNAPSHOT_ID_MISMATCH"
    assert db.insert_payloads == []


def _server_producer_output(package):
    audited = dict(package)
    audited["feature_audit"] = dict(package["feature_audit"], server_owned=True)
    return audited


def test_nba_scorer_boundary_accepts_attested_and_refuses_unattested(monkeypatch):
    from v17 import nba_current_pregame_scorer as scorer
    monkeypatch.setattr(scorer, "datetime", _FrozenDatetime)
    lineup = {"status": "TEST_ONLY_LINEUP_EVIDENCE"}
    unattested = _server_producer_output(_bound_package())
    assert scorer._validated_server_hydration(
        {"feature_package": unattested, "lineup_evidence": lineup}, EVENT_START.isoformat()) is None
    db = FakeAttestationDb(T0 + timedelta(seconds=20))
    attested = _server_producer_output(attest_bound_source_features(db, _bound_package(), now=T0))
    verified = scorer._validated_server_hydration(
        {"feature_package": attested, "lineup_evidence": lineup}, EVENT_START.isoformat())
    assert verified is not None
    package, _lineup, source_id = verified
    assert source_id == "sha256:" + RAW
    assert package["feature_audit"]["timestamp_attestation"] == "VERIFIED"


def test_ncaab_bridge_boundary_accepts_attested_and_refuses_unattested(monkeypatch):
    from v17 import team_event_bridge_runtime as bridge
    monkeypatch.setattr(bridge, "datetime", _FrozenDatetime)
    req = SimpleNamespace(official_event_id="espn-401927398", home_team="St. Bonaventure Bonnies",
                          away_team="Wright State Raiders", event_start_time_utc=EVENT_START.isoformat())

    def package(attestation_db):
        base = _bound_package()
        base.update(sport="NCAAB", official_event_id=req.official_event_id,
                    home_team=req.home_team, away_team=req.away_team)
        if attestation_db is not None:
            base = attest_bound_source_features(attestation_db, base, now=T0)
        return _server_producer_output(base)

    unattested = package(None)
    assert bridge._validated_ncaab_server_hydration(
        req, SimpleNamespace(hydrate_ncaab_features=lambda _r: unattested)) is None
    attested = package(FakeAttestationDb(T0 + timedelta(seconds=20)))
    out = bridge._validated_ncaab_server_hydration(
        req, SimpleNamespace(hydrate_ncaab_features=lambda _r: attested))
    assert out is not None and out[1] == "sha256:" + RAW


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return T0 + timedelta(minutes=5) if tz is None else (T0 + timedelta(minutes=5)).astimezone(tz)


def test_migration_is_server_clocked_pregame_only_and_append_only():
    sql = (Path(__file__).resolve().parents[1] / "migrations"
           / "20261124_v17_source_capture_attestations.sql").read_text()
    assert "new.received_at := pg_catalog.clock_timestamp();" in sql
    assert "before insert on public.wow_v17_source_capture_attestations" in sql
    assert "check (received_at < event_start_time_utc)" in sql
    assert "alter column received_at drop default" in sql
    assert "enable row level security" in sql
    assert "revoke all on table public.wow_v17_source_capture_attestations from public, anon, authenticated" in sql
    assert "revoke update, delete, truncate, references, trigger" in sql
    assert "grant select, insert on table public.wow_v17_source_capture_attestations to service_role" in sql
    grants = [line for line in sql.splitlines() if line.strip().lower().startswith("grant ")]
    assert grants == ["grant select, insert on table public.wow_v17_source_capture_attestations to service_role;"]
    assert "check (can_execute = false)" in sql
