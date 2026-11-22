from __future__ import annotations

from types import SimpleNamespace
import hashlib
import json

import pytest

from v17.nhl_event_identity import (
    NHLEventIdentityError,
    resolve_nhl_current_event_identity,
    season_id,
    season_start_year,
    team_abbreviation,
)


class _Response:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload
        self.content = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")

    def json(self):
        return self.payload


class _Session:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _Response(self.payload)


def _payload():
    return {
        "games": [
            {
                "id": 2026020041,
                "season": 20262027,
                "gameType": 2,
                "gameState": "FUT",
                "startTimeUTC": "2026-10-08T00:00:00Z",
                "homeTeam": {"abbrev": "DAL"},
                "awayTeam": {"abbrev": "PIT"},
            }
        ]
    }


def test_team_aliases_include_current_utah_and_tampa():
    assert team_abbreviation("Utah Mammoth") == "UTA"
    assert team_abbreviation("Tampa Bay Lightning") == "TBL"
    assert team_abbreviation("Dallas Stars") == "DAL"


def test_season_identity_uses_nhl_cross_year_contract():
    from datetime import datetime, timezone

    assert season_start_year(datetime(2026, 10, 7, tzinfo=timezone.utc)) == 2026
    assert season_start_year(datetime(2027, 2, 1, tzinfo=timezone.utc)) == 2026
    assert season_id(2026) == 20262027


def test_exact_first_party_match_returns_nhl_game_id():
    session = _Session(_payload())
    out = resolve_nhl_current_event_identity(
        event_start_time="2026-10-08T00:00:00Z",
        home_team="Dallas Stars",
        away_team="Pittsburgh Penguins",
        session=session,
    )
    assert out["event_id"] == "2026020041"
    assert out["identity_provider"] == "NHL_PUBLIC_WEB_API"
    assert out["canonical_identity_source_sha256"] == hashlib.sha256(
        json.dumps(_payload(), separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    assert out["canonical_identity_source_byte_count"] > 0
    assert out["market_features_used"] is False
    assert out["prediction_authority"] is False
    assert out["can_execute"] is False
    assert "/club-schedule-season/DAL/20262027" in session.calls[0][0]


def test_start_time_mismatch_stays_unresolved():
    with pytest.raises(NHLEventIdentityError) as exc:
        resolve_nhl_current_event_identity(
            event_start_time="2026-10-08T03:00:00Z",
            home_team="Dallas Stars",
            away_team="Pittsburgh Penguins",
            session=_Session(_payload()),
        )
    assert exc.value.code == "NHL_CANONICAL_EVENT_NOT_FOUND"


def test_participant_mismatch_never_fuzzy_promotes_identity():
    with pytest.raises(NHLEventIdentityError) as exc:
        resolve_nhl_current_event_identity(
            event_start_time="2026-10-08T00:00:00Z",
            home_team="Dallas Cowboys",
            away_team="Pittsburgh Penguins",
            session=_Session(_payload()),
        )
    assert exc.value.code == "NHL_EVENT_PARTICIPANT_IDENTITY_UNRESOLVED"


def test_duplicate_official_matches_fail_closed():
    payload = _payload()
    payload["games"].append({**payload["games"][0], "id": 2026020999})
    with pytest.raises(NHLEventIdentityError) as exc:
        resolve_nhl_current_event_identity(
            event_start_time="2026-10-08T00:00:00Z",
            home_team="Dallas Stars",
            away_team="Pittsburgh Penguins",
            session=_Session(payload),
        )
    assert exc.value.code == "NHL_CANONICAL_EVENT_AMBIGUOUS"



def test_source_digest_changes_when_unselected_schedule_content_changes():
    base = _payload()
    altered = _payload()
    altered["scheduleRevision"] = "not-used-in-identity-match"
    kwargs = {
        "event_start_time": "2026-10-08T00:00:00Z",
        "home_team": "Dallas Stars",
        "away_team": "Pittsburgh Penguins",
    }
    first = resolve_nhl_current_event_identity(**kwargs, session=_Session(base))
    second = resolve_nhl_current_event_identity(**kwargs, session=_Session(altered))
    assert first["event_id"] == second["event_id"] == "2026020041"
    assert first["canonical_identity_source_sha256"] != second["canonical_identity_source_sha256"]
    assert first["can_execute"] is False
    assert second["prediction_authority"] is False


def test_missing_or_invalid_official_body_cannot_be_hashed_from_reserialized_json():
    class MissingBytes(_Session):
        def get(self, url, **kwargs):
            response = _Response(self.payload)
            del response.content
            return response

    class CorruptBytes(_Session):
        def get(self, url, **kwargs):
            response = _Response(self.payload)
            response.content = b"<html>not official schedule JSON</html>"
            return response

    kwargs = {
        "event_start_time": "2026-10-08T00:00:00Z",
        "home_team": "Dallas Stars",
        "away_team": "Pittsburgh Penguins",
    }
    with pytest.raises(NHLEventIdentityError) as missing:
        resolve_nhl_current_event_identity(**kwargs, session=MissingBytes(_payload()))
    assert missing.value.code == "NHL_OFFICIAL_SOURCE_BYTES_UNAVAILABLE"
    with pytest.raises(NHLEventIdentityError) as invalid:
        resolve_nhl_current_event_identity(**kwargs, session=CorruptBytes(_payload()))
    assert invalid.value.code == "NHL_OFFICIAL_SOURCE_RESPONSE_INVALID"


def test_first_party_30_minute_boundary_and_started_game_fail_closed():
    kwargs = {"home_team": "Dallas Stars", "away_team": "Pittsburgh Penguins"}
    for offset, accepted in [(-29, True), (29, True), (30, True), (31, False)]:
        from datetime import datetime, timedelta, timezone
        official = datetime(2026, 10, 8, 0, tzinfo=timezone.utc)
        when = (official + timedelta(minutes=offset)).isoformat()
        if accepted:
            out = resolve_nhl_current_event_identity(
                **kwargs, event_start_time=when, session=_Session(_payload())
            )
            assert out["event_id"] == "2026020041"
        else:
            with pytest.raises(NHLEventIdentityError) as exc:
                resolve_nhl_current_event_identity(
                    **kwargs, event_start_time=when, session=_Session(_payload())
                )
            assert exc.value.code == "NHL_CANONICAL_EVENT_NOT_FOUND"

    already_started = _payload()
    already_started["games"][0]["gameState"] = "LIVE"
    with pytest.raises(NHLEventIdentityError) as exc:
        resolve_nhl_current_event_identity(
            **kwargs, event_start_time="2026-10-08T00:00:00Z",
            session=_Session(already_started),
        )
    assert exc.value.code == "NHL_EVENT_NOT_PREGAME"
