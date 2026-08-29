from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from ncaaf_evidence_readiness import (
    EVENT_SCOPED_KINDS,
    REQUIRED_SCOPES,
    TEAM_SCOPED_KINDS,
    assess_pregame_evidence,
)


def _row(event_id: str, kickoff: datetime, kind: str, scope: str) -> dict:
    return {
        "official_event_id": event_id,
        "event_start_time": kickoff.isoformat(),
        "evidence_kind": kind,
        "scope": scope,
        "evidence_timestamp": (kickoff - timedelta(hours=2)).isoformat(),
        "provenance_grade": "A",
        "blocker_codes": [],
    }


def _complete(event_id: str, kickoff: datetime) -> list[dict]:
    rows = [
        _row(event_id, kickoff, kind, scope)
        for kind in TEAM_SCOPED_KINDS
        for scope in REQUIRED_SCOPES
    ]
    rows.extend(_row(event_id, kickoff, kind, "EVENT") for kind in EVENT_SCOPED_KINDS)
    return rows


def test_complete_evidence_is_ready():
    kickoff = datetime(2026, 9, 5, 19, 0, tzinfo=timezone.utc)
    result = assess_pregame_evidence(
        official_event_id="cfb-1",
        event_start_time=kickoff.isoformat(),
        rows=_complete("cfb-1", kickoff),
    )
    assert result.ready is True
    assert result.missing_slots == ()
    assert result.satisfied_slots == result.required_slots
    assert result.can_execute is False
    assert result.probability_publishable is False


def test_missing_qb_status_fails_closed():
    kickoff = datetime(2026, 9, 5, 19, 0, tzinfo=timezone.utc)
    rows = [r for r in _complete("cfb-2", kickoff) if not (r["evidence_kind"] == "QB_STATUS" and r["scope"] == "AWAY")]
    result = assess_pregame_evidence(
        official_event_id="cfb-2",
        event_start_time=kickoff.isoformat(),
        rows=rows,
    )
    assert result.ready is False
    assert "QB_STATUS:AWAY" in result.missing_slots
    assert "NCAAF_PREGAME_EVIDENCE_INCOMPLETE" in result.blocker_codes


def test_post_kickoff_and_unverified_rows_are_rejected():
    kickoff = datetime(2026, 9, 5, 19, 0, tzinfo=timezone.utc)
    rows = _complete("cfb-3", kickoff)
    for row in rows:
        if row["evidence_kind"] == "WEATHER":
            row["evidence_timestamp"] = (kickoff + timedelta(minutes=1)).isoformat()
        if row["evidence_kind"] == "QB_STATUS" and row["scope"] == "HOME":
            row["provenance_grade"] = "UNVERIFIED"
    result = assess_pregame_evidence(
        official_event_id="cfb-3",
        event_start_time=kickoff.isoformat(),
        rows=rows,
    )
    assert result.ready is False
    assert "WEATHER:EVENT" in result.missing_slots
    assert "QB_STATUS:HOME" in result.missing_slots
    assert result.rejected_rows == 2
    assert "NCAAF_PREGAME_EVIDENCE_ROWS_REJECTED" in result.blocker_codes


def test_sql_contract_is_service_role_only_and_pregame():
    sql = Path(__file__).with_name("ncaaf_pregame_evidence.sql").read_text()
    lowered = sql.lower()
    assert "enable row level security" in lowered
    assert "revoke all on table public.wow_ncaaf_pregame_evidence from anon, authenticated" in lowered
    assert "security invoker" in lowered
    assert "evidence_timestamp < event_start_time" in lowered
    assert "can_execute = false" in lowered
    assert "probability_publishable" in lowered
