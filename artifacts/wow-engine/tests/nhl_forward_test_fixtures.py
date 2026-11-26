"""Shared synthetic NHL fixtures; no import of HTTP/batch runtime or test modules."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import v17.nhl_fitted_specialist as nhl
from v17.nhl_provenance import SOURCE_PAYLOAD_HASH_SCOPE, provenance_envelope_hash

FIXTURE = Path(__file__).parent / "fixtures" / "nhl_fitted_candidate_20261107.json"
NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
START = datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc)
SEASON = "20262027"


def _candidate_row() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _event(index: int, home: str, away: str, home_score: int, away_score: int) -> dict:
    start = datetime(2026, 10, 1, tzinfo=timezone.utc) + timedelta(days=index)
    event_id = f"202602{index:04d}"
    outcome = {
        "home_score": home_score,
        "away_score": away_score,
        "positive_outcome": home_score > away_score,
        "game_type": 2,
        "neutral_site": False,
        "game_state": "FINAL",
        "provenance_version": nhl.SOURCE_PROVENANCE_VERSION,
        "source_payload_commitment": hashlib.sha256(
            f"nhl-raw-{index}".encode()
        ).hexdigest(),
        "payload_hash_scope": SOURCE_PAYLOAD_HASH_SCOPE,
    }
    envelope = provenance_envelope_hash(
        official_event_id=event_id,
        season=SEASON,
        event_start_time=start.isoformat(),
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
        "official_event_id": event_id,
        "season": SEASON,
        "event_start_time": start.isoformat(),
        "home_participant": home,
        "away_participant": away,
        "outcome_json": outcome,
        "source_provider": "NHL_PUBLIC_WEB_API",
        "source_uri": f"https://api-web.nhle.com/v1/club-schedule-season/{home}/20262027",
        "source_payload_sha256": envelope,
    }


def _events() -> list[dict]:
    events: list[dict] = []
    for index in range(1, 11):
        if index % 2:
            events.append(_event(index, "BOS", "TOR", 3, 2))
        else:
            events.append(_event(index, "NYR", "MTL", 2, 3))
    return events


def _timeline_receipt(
    events: list[dict],
    *,
    home: str = "BOS",
    away: str = "NYR",
    target_start: datetime = START,
    season: str = SEASON,
) -> dict:
    """Test-authority full-season prior receipt for a synthetic timeline."""
    del home, away  # retained for call-site compatibility
    prior_ids: set[str] = set()
    for event in events:
        start = datetime.fromisoformat(str(event["event_start_time"]).replace("Z", "+00:00"))
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if start >= target_start:
            continue
        prior_ids.add(str(event["official_event_id"]))
    return {
        "provenance_complete": True,
        "season": str(season),
        "settled_prior_event_ids": sorted(prior_ids),
        "verified_at": NOW.isoformat(),
        "source": "TEST_TIMELINE_RECEIPT",
    }


def _reconstruct(events: list[dict], **kwargs):
    params = {
        "official_event_id": "2026029001",
        "event_start_time": START.isoformat(),
        "home_team": "BOS",
        "away_team": "NYR",
        "season": SEASON,
        "source_timeline_receipt": kwargs.get(
            "source_timeline_receipt", _timeline_receipt(events)
        ),
    }
    params.update(kwargs)
    return nhl.reconstruct_nhl_pregame_features(events, **params)


