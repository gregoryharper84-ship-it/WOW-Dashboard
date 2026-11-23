from __future__ import annotations

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
    """Only body bytes: the module must never need a separate parse."""

    status_code = 200

    def __init__(self, payload, body=None):
        self.content = json.dumps(payload).encode() if body is None else body


class _Session:
    def __init__(self, payload, body=None):
        self.payload = payload
        self.body = body
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _Response(self.payload, self.body)


class _NoBodyResponse:
    status_code = 200


class _NoBodySession:
    def get(self, url, **kwargs):
        return _NoBodyResponse()


class _StaleParseResponse(_Response):
    """Body bytes that disagree with what a separate .json() would return."""

    def __init__(self, body, payload):
        super().__init__(None, body)
        self._payload = payload

    def json(self):
        return self._payload


class _StaleParseSession:
    def __init__(self, body, payload):
        self.body = body
        self.payload = payload

    def get(self, url, **kwargs):
        return _StaleParseResponse(self.body, self.payload)


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


def _montreal_payload():
    return {
        "games": [
            {
                "id": 2026020379,
                "season": 20262027,
                "gameType": 2,
                "gameState": "FUT",
                "startTimeUTC": "2026-11-24T00:30:00Z",
                "homeTeam": {"abbrev": "MTL"},
                "awayTeam": {"abbrev": "LAK"},
            }
        ]
    }


def test_accented_participant_names_fold_to_the_contract_abbreviation():
    assert team_abbreviation("Montréal Canadiens") == "MTL"
    assert team_abbreviation("MONTREAL CANADIENS") == "MTL"
    assert team_abbreviation("Montréal Canadiens") == "MTL"


def test_accented_participants_resolve_instead_of_silently_refusing():
    session = _Session(_montreal_payload())
    out = resolve_nhl_current_event_identity(
        event_start_time="2026-11-24T00:30:00Z",
        home_team="Montréal Canadiens",
        away_team="Los Angeles Kings",
        session=session,
    )
    assert out["event_id"] == "2026020379"
    assert out["home_team"] == "MTL"
    assert "/club-schedule-season/MTL/20262027" in session.calls[0][0]


def test_accent_folding_never_fuzzy_promotes_a_wrong_club():
    with pytest.raises(NHLEventIdentityError) as exc:
        resolve_nhl_current_event_identity(
            event_start_time="2026-11-24T00:30:00Z",
            home_team="Montréal Canadiens",
            away_team="Boston Bruins",
            session=_Session(_montreal_payload()),
        )
    assert exc.value.code == "NHL_CANONICAL_EVENT_NOT_FOUND"


def test_identity_digest_is_the_sha256_of_the_exact_body():
    body = json.dumps(_payload(), indent=2).encode()
    kwargs = dict(
        event_start_time="2026-10-08T00:00:00Z",
        home_team="Dallas Stars",
        away_team="Pittsburgh Penguins",
    )
    first = resolve_nhl_current_event_identity(
        session=_Session(_payload(), body=body), **kwargs
    )
    second = resolve_nhl_current_event_identity(
        session=_Session(_payload(), body=body), **kwargs
    )
    assert first["identity_source_sha256"] == hashlib.sha256(body).hexdigest()
    assert first["identity_source_sha256"] == second["identity_source_sha256"]
    assert first["identity_verified_at"] != second["identity_verified_at"]


def test_identity_digest_binds_bytes_not_the_parsed_payload():
    """Json-equivalent bodies must not share a digest.

    The session returns the identical parsed dict in both cases, so a digest of
    the payload structure would collide here. Hashed response bytes do not.
    """
    compact = json.dumps(_payload(), separators=(",", ":")).encode()
    pretty = json.dumps(_payload(), indent=4).encode()
    assert compact != pretty

    a = resolve_nhl_current_event_identity(
        event_start_time="2026-10-08T00:00:00Z",
        home_team="Dallas Stars",
        away_team="Pittsburgh Penguins",
        session=_Session(_payload(), body=compact),
    )
    b = resolve_nhl_current_event_identity(
        event_start_time="2026-10-08T00:00:00Z",
        home_team="Dallas Stars",
        away_team="Pittsburgh Penguins",
        session=_Session(_payload(), body=pretty),
    )
    assert a["event_id"] == b["event_id"] == "2026020041"
    assert a["identity_source_sha256"] != b["identity_source_sha256"]


def test_identity_is_matched_from_the_hashed_bytes_not_a_separate_parse():
    """A .json() that disagrees with .content must not supply the identity.

    The bytes are hashed and parsed once, so a stale or adapter-level parse
    cannot label one feed body with another body's digest.
    """
    body = json.dumps(_montreal_payload()).encode()
    out = resolve_nhl_current_event_identity(
        event_start_time="2026-11-24T00:30:00Z",
        home_team="Montréal Canadiens",
        away_team="Los Angeles Kings",
        session=_StaleParseSession(body, _payload()),  # DAL/PIT if .json() were used
    )
    assert out["event_id"] == "2026020379"
    assert out["identity_source_sha256"] == hashlib.sha256(body).hexdigest()


def test_response_without_body_bytes_fails_closed():
    with pytest.raises(NHLEventIdentityError) as exc:
        resolve_nhl_current_event_identity(
            event_start_time="2026-10-08T00:00:00Z",
            home_team="Dallas Stars",
            away_team="Pittsburgh Penguins",
            session=_NoBodySession(),
        )
    assert exc.value.code == "NHL_OFFICIAL_SOURCE_BODY_UNAVAILABLE"


def test_empty_response_body_fails_closed():
    with pytest.raises(NHLEventIdentityError) as exc:
        resolve_nhl_current_event_identity(
            event_start_time="2026-10-08T00:00:00Z",
            home_team="Dallas Stars",
            away_team="Pittsburgh Penguins",
            session=_Session(_payload(), body=b""),
        )
    assert exc.value.code == "NHL_OFFICIAL_SOURCE_BODY_UNAVAILABLE"
