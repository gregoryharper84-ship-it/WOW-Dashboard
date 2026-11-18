"""Governed spread receipt write boundary: schema, admission, insert-only."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from v17 import spread_receipt_registry as registry

START = datetime(2026, 11, 12, 23, 0, tzinfo=timezone.utc)


def _candidate(**overrides):
    candidate = {
        "sport": "NFL",
        "official_event_id": "nfl-123",
        "event_start_time_utc": START.isoformat(),
        "home_team": "DAL",
        "away_team": "ATL",
        "market_family": "POINT_SPREAD",
        "exact_line_side": "HOME",
        "exact_signed_spread": -6.5,
        "settlement_basis": "FULL_GAME_INCLUDING_OVERTIME",
        "line_source": "APPEND_ONLY_MARKET_LEDGER",
        "quote_source_id": "quote-8891",
        "quote_sportsbook": "Pinnacle",
        "quote_observed_at_utc": (START - timedelta(hours=2)).isoformat(),
        "provider_event_id": "rd-nfl-9101",
        "controlling_spread_specialist": "NFL_SPREAD_MARGIN_DISTRIBUTION_CHALLENGER_V1",
        "model_artifact_version": "NFL_SPREAD_MARGIN_RIDGE_EMPIRICAL_V1",
        "model_class": "CERTIFIED_FITTED",
        "model_version": "NFL_SPREAD_MARGIN_RIDGE_EMPIRICAL_V1",
        "calibration_version": "cal-v1",
        "p_cover": 0.54,
        "p_push": 0.02,
        "p_not_cover": 0.44,
        "calibrated_lower_bound": 0.47,
        "calibrated_upper_bound": 0.61,
        "model_timestamp": (START - timedelta(minutes=30)).isoformat(),
        "source_snapshot_timestamp": (START - timedelta(minutes=40)).isoformat(),
        "event_state": "SCHEDULED",
        "score_snapshot_status": "PASS",
        "source_receipt_id": "receipt-spread-1",
        "can_execute": False,
    }
    candidate.update(overrides)
    return candidate


def _rejection(candidate):
    with pytest.raises(registry.SpreadReceiptRejected) as excinfo:
        registry.admit_spread_receipt(candidate)
    return excinfo.value.code


def test_admitted_receipt_carries_canonical_identity_and_no_execution_authority():
    row, admission = registry.admit_spread_receipt(_candidate())

    assert admission["card_admission_eligible"] is True
    assert admission["can_execute"] is False
    assert admission["p_cover"] == 0.54
    assert row["admission_status"] == "PASS"
    assert row["model_class"] == "CERTIFIED_FITTED"
    assert row["exact_line_side"] == "HOME"
    assert row["exact_signed_spread"] == -6.5
    assert row["can_execute"] is False
    assert len(row["receipt_key"]) == 64
    assert len(row["content_sha256"]) == 64
    assert registry.SCHEMA_VERSION == "WOW_V17_SPREAD_CARD_RECEIPT_V1"


def test_publishability_is_derived_not_accepted_from_the_caller():
    # A caller claiming an unadmitted distribution is publishable cannot get it in.
    row, admission = registry.admit_spread_receipt(
        _candidate(probability_publishable=False, rank_eligible=False)
    )
    assert row["probability_publishable"] is True
    assert row["rank_eligible"] is True
    assert admission["card_admission_eligible"] is True


def test_research_receipts_stay_out_of_the_governed_relation():
    assert _rejection(_candidate(model_class="RESEARCH_SHADOW")) == "RECEIPT_MODEL_CLASS_NOT_CERTIFIED"


def test_distribution_must_normalize():
    code = _rejection(_candidate(p_cover=0.54, p_push=0.02, p_not_cover=0.43))
    assert code == "RECEIPT_DISTRIBUTION_NOT_NORMALIZED"


def test_bounds_must_be_interior_and_ordered_around_the_estimate():
    assert _rejection(_candidate(calibrated_lower_bound=0.60)) == "RECEIPT_BOUNDS_INCOHERENT"
    # A coherent triplet whose estimate sits outside its own calibrated bound.
    assert (
        _rejection(
            _candidate(p_cover=0.97, p_push=0.02, p_not_cover=0.01, calibrated_upper_bound=0.96)
        )
        == "RECEIPT_BOUNDS_INCOHERENT"
    )


def test_a_line_without_real_quote_provenance_cannot_be_stored():
    assert _rejection(_candidate(quote_source_id="")) == "RECEIPT_QUOTE_SOURCE_ID_REQUIRED"
    assert _rejection(_candidate(line_source="")) == "RECEIPT_LINE_SOURCE_REQUIRED"
    assert _rejection(_candidate(quote_sportsbook="")) == "RECEIPT_QUOTE_SPORTSBOOK_REQUIRED"


def test_a_quote_observed_after_kickoff_is_rejected():
    code = _rejection(_candidate(quote_observed_at_utc=(START + timedelta(minutes=5)).isoformat()))
    assert code == "RECEIPT_QUOTE_NOT_PREGAME"


def test_a_model_scored_after_kickoff_is_rejected():
    code = _rejection(_candidate(model_timestamp=(START + timedelta(minutes=1)).isoformat()))
    assert code == "RECEIPT_MODEL_TIMESTAMP_NOT_PREGAME"


def test_execution_capability_may_not_be_asserted():
    assert _rejection(_candidate(can_execute=True)) == "RECEIPT_EXECUTION_INVARIANT_UNPROVEN"
    missing = _candidate()
    missing.pop("can_execute")
    assert _rejection(missing) == "RECEIPT_EXECUTION_INVARIANT_UNPROVEN"


def test_unsupported_sport_and_market_are_rejected():
    assert _rejection(_candidate(sport="MLB")) == "RECEIPT_SPORT_UNSUPPORTED"
    assert _rejection(_candidate(market_family="MONEYLINE")) == "RECEIPT_MARKET_FAMILY_UNSUPPORTED"


def test_a_live_or_final_event_is_rejected():
    assert _rejection(_candidate(event_state="IN_PROGRESS")) == "RECEIPT_EVENT_NOT_PREGAME"


def test_a_naive_timestamp_is_rejected_rather_than_assumed_utc():
    code = _rejection(_candidate(model_timestamp="2026-11-12T22:30:00"))
    assert code == "RECEIPT_MODEL_TIMESTAMP_INVALID"


class _RecordingQuery:
    def __init__(self, client):
        self.client = client

    def upsert(self, rows, on_conflict=None, ignore_duplicates=False):
        self.client.upserts.append(
            {
                "rows": [dict(row) for row in rows],
                "on_conflict": on_conflict,
                "ignore_duplicates": ignore_duplicates,
            }
        )
        return self

    def execute(self):
        if self.client.fail:
            raise RuntimeError("relation missing")
        return SimpleNamespace(data=[])


class _RecordingClient:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.upserts = []
        self.touched = []

    def table(self, name):
        self.touched.append(name)
        return _RecordingQuery(self)


def test_persist_is_insert_only_and_idempotent_on_the_receipt_key():
    client = _RecordingClient()

    first = registry.persist_certified_spread_receipt(client, _candidate())
    second = registry.persist_certified_spread_receipt(client, _candidate())

    assert first["status"] == "PERSISTED"
    assert first["updates_allowed"] is False
    assert first["can_execute"] is False
    assert first["receipt_key"] == second["receipt_key"]
    assert client.touched == [registry.TABLE, registry.TABLE]
    assert len(client.upserts) == 2
    for call in client.upserts:
        assert call["on_conflict"] == "receipt_key"
        assert call["ignore_duplicates"] is True
        assert call["rows"][0]["can_execute"] is False


def test_persistence_failure_is_typed_and_never_silently_dropped():
    result = registry.persist_certified_spread_receipt(_RecordingClient(fail=True), _candidate())
    assert result["status"] == "PERSISTENCE_UNAVAILABLE"
    assert result["code"] == "SPREAD_RECEIPT_PERSISTENCE_UNAVAILABLE:RuntimeError"
    assert result["updates_allowed"] is False


class _ReadClient:
    def __init__(self, rows=None, *, fail=False):
        self.rows = rows or []
        self.fail = fail

    def table(self, name):
        assert name == registry.TABLE
        return self

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        if self.fail:
            raise RuntimeError("relation missing")
        return SimpleNamespace(data=self.rows)


def test_reader_returns_the_newest_admitted_receipt():
    row, reason = registry.read_certified_spread_receipt(
        _ReadClient([{"official_event_id": "nfl-123", "admission_status": "PASS", "p_cover": 0.54}]),
        sport="NFL",
        event_id="nfl-123",
    )
    assert reason is None
    assert row["p_cover"] == 0.54


def test_reader_fails_closed_with_typed_reasons():
    assert registry.read_certified_spread_receipt(
        _ReadClient([]), sport="NFL", event_id="nfl-123"
    ) == (None, "SPREAD_RECEIPT_NOT_PUBLISHED")
    assert registry.read_certified_spread_receipt(
        _ReadClient([{"admission_status": "FAILED"}]), sport="NFL", event_id="nfl-123"
    ) == (None, "SPREAD_RECEIPT_NOT_ADMITTED")
    assert registry.read_certified_spread_receipt(
        _ReadClient(fail=True), sport="NFL", event_id="nfl-123"
    ) == (None, "SPREAD_RECEIPT_REGISTRY_UNAVAILABLE:RuntimeError")
    assert registry.read_certified_spread_receipt(
        _ReadClient([]), sport="", event_id=""
    ) == (None, "SPREAD_RECEIPT_EVENT_IDENTITY_INCOMPLETE")


MIGRATION = Path(__file__).parent / "sql" / "20261112_v17_spread_card_receipts.sql"


def test_the_relation_declares_every_column_the_writer_emits():
    sql = MIGRATION.read_text(encoding="utf-8")
    row, _admission = registry.admit_spread_receipt(_candidate())
    for column in row:
        assert f"    {column} " in sql, f"migration is missing writer column {column}"


def test_the_relation_pins_the_fail_closed_guards():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "enable row level security" in sql
    assert "revoke all on table public.wow_v17_spread_card_receipts from anon, authenticated" in sql
    assert "check (\n        can_execute = false\n    )" in sql
    assert "before update on public.wow_v17_spread_card_receipts" in sql
    assert "before delete on public.wow_v17_spread_card_receipts" in sql
    assert "admission_status = 'PASS'" in sql
    assert "model_class = 'CERTIFIED_FITTED'" in sql
