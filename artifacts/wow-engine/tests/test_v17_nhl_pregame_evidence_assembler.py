"""Fail-closed NHL Action pregame evidence assembler — Incident #1952."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from v17.nhl_pregame_evidence_assembler import assemble_nhl_pregame_action_evidence
from v17.nhl_starter_goalie_confirmation import (
    ATTESTATION_FIELD,
    TrustedProvider,
    attest_receipt,
)


# Evaluation must sit inside the 90-minute starter verification window.
EVENT_START = datetime(2026, 11, 26, 21, 0, tzinfo=timezone.utc)
NOW = EVENT_START - timedelta(minutes=30)
EVENT_ID = "2026021234"
ATTESTATION_KEY = b"nhl-assembler-test-attestation-key!"  # 32 bytes
TRUSTED = {
    "NHL_GAMECENTER": TrustedProvider("NHL_GAMECENTER", "NHL_COM"),
    "TEAM_OFFICIAL": TrustedProvider("TEAM_OFFICIAL", "CLUB_SITE"),
}


def _identity(**overrides) -> dict:
    row = {
        "event_id": EVENT_ID,
        "home_team": "BOS",
        "away_team": "NYR",
        "event_start_time": EVENT_START.isoformat(),
        "identity_provider": "NHL_PUBLIC_WEB_API",
        "identity_source": "https://api-web.nhle.com/v1/club-schedule-season/BOS/20262027",
        "identity_match_sha256": "a" * 64,
        "identity_verified_at": (NOW - timedelta(minutes=2)).isoformat(),
        "neutral_site": False,
        "season": "20262027",
        "market_features_used": False,
        "prediction_authority": False,
        "can_execute": False,
    }
    row.update(overrides)
    return row


def _payload() -> dict:
    return {
        "id": int(EVENT_ID),
        "gameState": "FUT",
        "startTimeUTC": EVENT_START.isoformat(),
        "homeTeam": {"abbrev": "BOS"},
        "awayTeam": {"abbrev": "NYR"},
    }


class _Response:
    status_code = 200

    def __init__(self):
        self.url = f"https://api-web.nhle.com/v1/gamecenter/{EVENT_ID}/boxscore"
        self.content = json.dumps(_payload()).encode()


def _get(*_a, **_k):
    return _Response()


def _receipt(source: str, family: str, snap: str, digit: str) -> dict:
    return {
        "source_authority": "OFFICIAL",
        "source_id": source,
        "provider_family": family,
        "source_snapshot_id": snap,
        "source_payload_sha256": digit * 64,
        "official_event_id": EVENT_ID,
        "home_team_id": "BOS",
        "away_team_id": "NYR",
        "event_start_time_utc": EVENT_START.isoformat(),
        "as_of": (NOW - timedelta(minutes=5)).isoformat(),
        "home": {"goalie_id": "8480000", "starting_status": "CONFIRMED"},
        "away": {"goalie_id": "8481111", "starting_status": "CONFIRMED"},
    }


def _attested_receipts() -> list[dict]:
    rows = [
        _receipt("NHL_GAMECENTER", "NHL_COM", "snap-a", "a"),
        _receipt("TEAM_OFFICIAL", "CLUB_SITE", "snap-b", "b"),
    ]
    for row in rows:
        row[ATTESTATION_FIELD] = attest_receipt(row, ATTESTATION_KEY)
    return rows


def test_missing_trusted_registry_and_injury_hold_even_with_official_gamecenter():
    result = assemble_nhl_pregame_action_evidence(
        resolved_identity=_identity(),
        as_of_utc=NOW,
        get=_get,
    )
    assert result["status"] == "HELD"
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False
    assert result["certification_claimed"] is False
    assert result["fitted_specialist_invoked"] is False
    blockers = set(result["blockers"])
    assert "NHL_INJURY_REPORT_UNAVAILABLE" in blockers
    assert "NHL_STARTER_SOURCE_NOT_TRUSTED" in blockers or "NHL_STARTER_RECEIPTS_INCOMPLETE" in blockers
    assert "NHL_STARTING_GOALIE_NOT_CONFIRMED" in blockers
    evidence = result["sport_specific_evidence"]
    assert evidence["settlement_basis"] == "FULL_GAME_MONEYLINE_INCLUDING_OVERTIME_SHOOTOUT"
    assert evidence["source_snapshot_id"].startswith("sha256:")
    assert evidence["source_content_sha256"] == hashlib.sha256(_Response().content).hexdigest()


def test_dual_attested_starters_and_injury_assemble_without_publishing():
    result = assemble_nhl_pregame_action_evidence(
        resolved_identity=_identity(),
        as_of_utc=NOW,
        starter_provider_receipts=_attested_receipts(),
        trusted_providers=TRUSTED,
        attestation_key=ATTESTATION_KEY,
        injury_report={"home": [], "away": [], "source": "TEST_FIXTURE"},
        get=_get,
    )
    assert result["status"] == "ASSEMBLED"
    assert result["blockers"] == []
    assert result["code"] == "NHL_PREGAME_ACTION_EVIDENCE_ASSEMBLED"
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False
    assert result["certification_claimed"] is False
    evidence = result["sport_specific_evidence"]
    assert evidence["goalie_status"] == "CONFIRMED"
    assert evidence["goalie_identity_snapshot_id"].startswith("sha256:")
    assert evidence["injury_report"]["source"] == "TEST_FIXTURE"
    assert evidence["settlement_basis"] == "FULL_GAME_MONEYLINE_INCLUDING_OVERTIME_SHOOTOUT"
    assert len(evidence["starter_provider_receipts"]) == 2


def test_single_provider_receipt_never_confirms_starter():
    one = _attested_receipts()[:1]
    result = assemble_nhl_pregame_action_evidence(
        resolved_identity=_identity(),
        as_of_utc=NOW,
        starter_provider_receipts=one,
        trusted_providers=TRUSTED,
        attestation_key=ATTESTATION_KEY,
        injury_report={"status": "NONE"},
        get=_get,
    )
    assert result["status"] == "HELD"
    assert "NHL_STARTER_RECEIPTS_INCOMPLETE" in result["blockers"]
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_gamecenter_identity_mismatch_blocks_snapshot():
    def bad_get(*_a, **_k):
        resp = _Response()
        payload = _payload()
        payload["homeTeam"] = {"abbrev": "TOR"}
        resp.content = json.dumps(payload).encode()
        return resp

    result = assemble_nhl_pregame_action_evidence(
        resolved_identity=_identity(),
        as_of_utc=NOW,
        injury_report={"status": "NONE"},
        starter_provider_receipts=_attested_receipts(),
        trusted_providers=TRUSTED,
        attestation_key=ATTESTATION_KEY,
        get=bad_get,
    )
    assert result["status"] == "HELD"
    assert "NHL_SOURCE_SNAPSHOT_MISSING" in result["blockers"] or (
        "NHL_GAMECENTER_CANONICAL_IDENTITY_MISMATCH" in result["blockers"]
    )
    assert result["can_execute"] is False


@pytest.mark.parametrize(
    ("overrides", "blocker"),
    [
        ({"official_event_id": "2026021235"}, "NHL_STARTER_EVENT_IDENTITY_MISMATCH"),
        ({"home_team_id": "TOR"}, "NHL_STARTER_EVENT_IDENTITY_MISMATCH"),
        ({"away_team_id": "TOR"}, "NHL_STARTER_EVENT_IDENTITY_MISMATCH"),
        (
            {"event_start_time_utc": (EVENT_START + timedelta(minutes=10)).isoformat()},
            "NHL_STARTER_START_TIME_DISAGREEMENT",
        ),
    ],
)
def test_starters_must_match_resolved_game(overrides, blocker):
    receipts = _attested_receipts()
    for receipt in receipts:
        receipt.update(overrides)
        receipt[ATTESTATION_FIELD] = attest_receipt(receipt, ATTESTATION_KEY)

    result = assemble_nhl_pregame_action_evidence(
        resolved_identity=_identity(),
        as_of_utc=NOW,
        starter_provider_receipts=receipts,
        trusted_providers=TRUSTED,
        attestation_key=ATTESTATION_KEY,
        injury_report={"source": "TEST_FIXTURE"},
        get=_get,
    )

    assert result["status"] == "HELD"
    assert blocker in result["blockers"]
    assert "NHL_STARTING_GOALIE_NOT_CONFIRMED" in result["blockers"]
    assert "goalie_identity_snapshot_id" not in result["sport_specific_evidence"]


@pytest.mark.parametrize("neutral_site", [None, 1, "true"])
def test_malformed_neutral_site_holds(neutral_site):
    result = assemble_nhl_pregame_action_evidence(
        resolved_identity=_identity(neutral_site=neutral_site),
        as_of_utc=NOW,
        starter_provider_receipts=_attested_receipts(),
        trusted_providers=TRUSTED,
        attestation_key=ATTESTATION_KEY,
        injury_report={"source": "TEST_FIXTURE"},
        get=_get,
    )

    assert result["status"] == "HELD"
    assert "NHL_NEUTRAL_SITE_UNVERIFIED" in result["blockers"]
    assert "neutral_site" not in result["sport_specific_evidence"]
