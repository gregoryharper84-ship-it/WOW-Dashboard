"""Offline fixtures for the server-owned NHL pregame orchestration step."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import types

import pytest

from v17.nhl_pregame_evidence_boundary import FULL_GAME_SETTLEMENT, REQUIRED
from v17.nhl_pregame_orchestration import (
    NHLPregameOrchestrationError,
    RECEIPT_TABLE,
    StarterReceiptAdapter,
    orchestrate_nhl_pregame_gate,
)

NOW = datetime(2026, 11, 10, 17, 0, tzinfo=timezone.utc)
EVENT_ID = "2026020219"
START = NOW + timedelta(hours=3)
MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "20261110_nhl_pregame_starter_receipts.sql"
)


def identity() -> dict:
    return {
        "event_id": EVENT_ID,
        "event_start_time": START.isoformat(),
        "home_team": "BOS",
        "away_team": "NYR",
        "identity_provider": "NHL_PUBLIC_WEB_API",
        "identity_source": "https://api-web.nhle.com/v1/club-schedule-season/BOS/20262027",
        "identity_verified_at": (NOW - timedelta(minutes=2)).isoformat(),
        "market_features_used": False,
        "prediction_authority": False,
        "can_execute": False,
    }


def evidence() -> dict:
    row = {key: "verified" for key in REQUIRED}
    row.update(
        official_event_id=EVENT_ID,
        home_team="BOS",
        away_team="NYR",
        event_start_time_utc=START.isoformat(),
        goalie_status="UNVERIFIED",
        goalie_status_observed_at=None,
        goalie_identity_snapshot_id=None,
        source_snapshot_id="source-1",
        settlement_basis=FULL_GAME_SETTLEMENT,
        home_elo=1510.0,
        away_elo=1490.0,
        home_goalie_sv_pct=0.91,
        away_goalie_sv_pct=0.92,
        home_pp_pct=0.21,
        away_pk_pct=0.80,
        injury_report={"status": "CURRENT"},
        rest_travel={"status": "CURRENT"},
        calibration_artifact={"artifact_type": "V17_BINARY_CALIBRATION"},
    )
    return row


def adapter(provider, snapshot, payload_sha, *, source_kind="STARTING_GOALIE_DESIGNATION", receipt_provider=None, **overrides):
    def collect(_identity, _as_of):
        row = {
            "official_event_id": EVENT_ID,
            "provider": receipt_provider,
            "source_snapshot_id": snapshot,
            "source_payload_sha256": payload_sha,
            "observed_at": (NOW - timedelta(minutes=10)).isoformat(),
            "event_id": EVENT_ID,
            "event_start_time_utc": START.isoformat(),
            "home_team": "BOS",
            "away_team": "NYR",
            "home_goalie_id": "8478402",
            "away_goalie_id": "8479318",
            "starting_status": "CONFIRMED",
        }
        row.update(overrides)
        return row

    return StarterReceiptAdapter(provider=provider, source_kind=source_kind, collect=collect)


def default_adapters(**overrides):
    return [
        adapter("NHL_PUBLIC_WEB_API", "snap-1", "a" * 64, **overrides),
        adapter(
            "NHL_OFFICIAL_CLUB_FEED",
            "snap-2",
            "b" * 64,
            source_kind="OFFICIAL_ROSTER_DESIGNATION",
        ),
    ]


class _Query:
    def __init__(self, db):
        self.db = db

    def upsert(self, rows, on_conflict=None, ignore_duplicates=False):
        if self.db.fail:
            raise RuntimeError("db down")
        self.db.calls.append(
            {
                "table": self.db.table_name,
                "rows": rows,
                "on_conflict": on_conflict,
                "ignore_duplicates": ignore_duplicates,
            }
        )
        return self

    def execute(self):
        return types.SimpleNamespace(data=list(self.db.calls[-1]["rows"]))


class FakeDb:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = []
        self.table_name = None

    def table(self, name):
        self.table_name = name
        return _Query(self)


def run(*, db=None, adapters=None, evidence_row=None, identity_row=None, as_of=NOW):
    return orchestrate_nhl_pregame_gate(
        db=db if db is not None else FakeDb(),
        identity=identity_row if identity_row is not None else identity(),
        evidence=evidence_row if evidence_row is not None else evidence(),
        adapters=adapters if adapters is not None else default_adapters(),
        as_of_utc=as_of,
    )


def test_two_official_adapters_persist_receipts_then_pass_the_gate():
    db = FakeDb()
    result = run(db=db)
    assert result["status"] == "PREGAME_EVIDENCE_COMPLETE_NOT_CERTIFICATION"
    assert result["pregame_gate"]["status"] == "INPUT_EVIDENCE_PASS_NOT_CERTIFICATION"
    assert result["goalie_confirmation"]["status"] == "STARTER_GOALIE_CONFIRMED_RESEARCH_ONLY"
    assert result["receipt_persistence"]["persisted"] == 2
    assert result["gate_invoked"] is True
    assert result["fitted_specialist_invoked"] is False
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["can_execute"] is False
    # Receipts are appended immutably and never overwrite a prior capture.
    assert len(db.calls) == 1
    assert db.calls[0]["table"] == RECEIPT_TABLE
    assert db.calls[0]["on_conflict"] == "receipt_identity_key"
    assert db.calls[0]["ignore_duplicates"] is True
    assert db.calls[0]["rows"][0]["can_execute"] is False
    assert db.calls[0]["rows"][0]["prediction_authority"] is False


def test_gamecenter_participation_adapter_is_rejected_at_registration():
    with pytest.raises(NHLPregameOrchestrationError) as exc:
        adapter(
            "NHL_PUBLIC_WEB_API",
            "box-1",
            "a" * 64,
            source_kind="GAMECENTER_BOXSCORE_PARTICIPATION",
        )
    assert "NHL_PREGAME_ADAPTER_NOT_A_STARTER_SOURCE" in str(exc.value)


def test_unknown_provider_adapter_is_rejected_at_registration():
    with pytest.raises(NHLPregameOrchestrationError):
        adapter("SOME_SCRAPER", "snap-1", "a" * 64)


def test_one_adapter_cannot_collect_and_nothing_is_persisted():
    db = FakeDb()
    result = run(
        db=db,
        adapters=[adapter("NHL_PUBLIC_WEB_API", "snap-1", "a" * 64)],
    )
    assert result["status"] == "HELD"
    assert result["code"] == "NHL_PREGAME_ORCHESTRATION_ADAPTERS_INSUFFICIENT"
    assert result["gate_invoked"] is False
    assert db.calls == []


def test_same_provider_twice_is_not_two_independent_sources():
    db = FakeDb()
    result = run(
        db=db,
        adapters=[
            adapter("NHL_PUBLIC_WEB_API", "snap-1", "a" * 64),
            adapter("NHL_PUBLIC_WEB_API", "snap-2", "b" * 64),
        ],
    )
    assert result["status"] == "HELD"
    assert result["code"] == "NHL_PREGAME_ORCHESTRATION_ADAPTERS_INSUFFICIENT"
    assert db.calls == []


def test_failed_collection_holds_and_never_invents_a_receipt():
    def boom(_identity, _as_of):
        raise TimeoutError("upstream")

    failing = StarterReceiptAdapter(
        provider="NHL_PUBLIC_WEB_API",
        source_kind="STARTING_GOALIE_DESIGNATION",
        collect=boom,
    )
    db = FakeDb()
    result = run(db=db, adapters=[failing, *default_adapters()[1:]])
    assert result["status"] == "HELD"
    assert any(
        blocker.startswith("NHL_PREGAME_STARTER_SOURCE_UNOBTAINABLE")
        for blocker in result["blockers"]
    )
    # Only the one real capture is persisted; the failure never becomes evidence.
    assert result["receipt_persistence"]["persisted"] == 1


def test_persistence_failure_holds_before_the_gate_is_called():
    result = run(db=FakeDb(fail=True))
    assert result["status"] == "HELD"
    assert result["code"] == "NHL_PREGAME_RECEIPT_PERSISTENCE_FAILED"
    assert result["gate_invoked"] is False
    assert result["pregame_gate"] is None
    assert result["can_execute"] is False


def test_disagreeing_official_goalies_hold_at_the_gate():
    db = FakeDb()
    adapters = default_adapters()
    result = run(
        db=db,
        adapters=[
            adapters[0],
            adapter(
                "NHL_OFFICIAL_CLUB_FEED",
                "snap-2",
                "b" * 64,
                source_kind="OFFICIAL_ROSTER_DESIGNATION",
                home_goalie_id="8479999",
            ),
        ],
    )
    assert result["status"] == "HELD"
    assert "NHL_STARTING_GOALIE_CONFLICT" in result["blockers"]
    assert result["goalie_confirmation"]["status"] == "HELD"
    assert result["pregame_gate"]["status"] == "HELD"
    # Both conflicting captures are still persisted as evidence.
    assert result["receipt_persistence"]["persisted"] == 2


def test_caller_supplied_confirmed_goalie_is_overwritten_when_confirmation_fails():
    claimed = evidence()
    claimed.update(
        goalie_status="CONFIRMED",
        goalie_status_observed_at=NOW.isoformat(),
        goalie_identity_snapshot_id="c" * 64,
    )
    conflict = adapter(
        "NHL_OFFICIAL_CLUB_FEED",
        "snap-2",
        "b" * 64,
        source_kind="OFFICIAL_ROSTER_DESIGNATION",
        home_goalie_id="8479999",
    )
    result = run(
        adapters=[adapter("NHL_PUBLIC_WEB_API", "snap-1", "a" * 64), conflict],
        evidence_row=claimed,
    )
    assert result["status"] == "HELD"
    assert "NHL_GOALIE_CONFIRMATION_RECEIPT_NOT_CONFIRMED" in result["blockers"]
    assert result["probability_publishable"] is False


def test_receipt_claiming_another_provider_is_rejected():
    adapters = default_adapters()
    result = run(
        adapters=[
            adapter(
                "NHL_PUBLIC_WEB_API",
                "snap-1",
                "a" * 64,
                receipt_provider="NHL_OFFICIAL_LINEUP_FEED",
            ),
            adapters[1],
        ]
    )
    assert result["status"] == "HELD"
    assert any(
        blocker.startswith("NHL_PREGAME_RECEIPT_PROVIDER_MISMATCH")
        for blocker in result["blockers"]
    )


def test_naive_as_of_is_a_programmer_error():
    with pytest.raises(NHLPregameOrchestrationError):
        run(as_of=datetime(2026, 11, 10, 17, 0))


def test_receipt_migration_is_append_only_and_non_executable():
    sql = MIGRATION.read_text().lower()
    assert "enable row level security" in sql
    assert "revoke all privileges" in sql
    assert "grant select, insert" in sql
    for forbidden in ("to anon", "to authenticated", "grant update", "grant delete"):
        assert forbidden not in sql
    assert "check (can_execute = false)" in sql
    assert "check (prediction_authority = false)" in sql
