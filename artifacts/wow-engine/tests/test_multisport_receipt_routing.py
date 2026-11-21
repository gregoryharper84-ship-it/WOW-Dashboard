"""Feature switch is off by default; no legacy LLP publication regression."""
from __future__ import annotations

from v17 import llp_team_intelligence_readback as llp

UUID = "116f0d3a-1c2b-4e3f-9a4b-1f2c3d4e5f60"


def _event():
    return dict(sport="NFL", official_event_id="nfl-123", home_team="H",
                away_team="A", event_start_time_utc="2026-11-06T20:00:00Z")


def _winner():
    return dict(source_receipt_id=UUID, score_snapshot_id=UUID,
                reducer_receipt_id=UUID, side="H")


def test_experimental_ledger_off_by_default(monkeypatch):
    monkeypatch.delenv("WOW_V17_MULTISPORT_RECEIPT_READBACK_ENABLED", raising=False)
    def forbidden(*args, **kwargs):
        raise AssertionError("No experimental receipt route before approval")
    monkeypatch.setattr(llp, "verify_multisport_event_receipt", forbidden)
    assert llp._verify_governed_event_prediction(
        None, event=_event(), winner=_winner()
    ) == "LLP_EVENT_PREDICTION_LOOKUP_NOT_PERFORMED"


def test_experimental_ledger_routes_explicitly_only(monkeypatch):
    monkeypatch.setenv("WOW_V17_MULTISPORT_RECEIPT_READBACK_ENABLED", "1")
    calls=[]
    def fake(db, *, event, winner):
        calls.append((db,event,winner))
        return "LLP_EVENT_PREDICTION_LOOKUP_UNAVAILABLE"
    monkeypatch.setattr(llp, "verify_multisport_event_receipt", fake)
    db=object()
    assert llp._verify_governed_event_prediction(
        db, event=_event(), winner=_winner()
    ) == "LLP_EVENT_PREDICTION_LOOKUP_UNAVAILABLE"
    assert len(calls)==1 and calls[0][0] is db


def test_unverified_uuid_never_calls_experimental_adapter(monkeypatch):
    monkeypatch.setenv("WOW_V17_MULTISPORT_RECEIPT_READBACK_ENABLED", "1")
    def forbidden(*args, **kwargs):
        raise AssertionError("Malformed receipt must stop before lookup")
    monkeypatch.setattr(llp, "verify_multisport_event_receipt", forbidden)
    winner=_winner(); winner["source_receipt_id"]="not-uuid"
    assert llp._verify_governed_event_prediction(
        object(), event=_event(), winner=winner
    ) == "LLP_EVENT_PREDICTION_ID_UNVERIFIED"
