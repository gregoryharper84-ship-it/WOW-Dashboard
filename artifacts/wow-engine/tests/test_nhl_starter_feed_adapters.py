"""Offline fixtures for the two server-owned NHL starter feed adapters."""
from datetime import datetime, timedelta, timezone
import types

import pytest

from v17.nhl_pregame_evidence_boundary import FULL_GAME_SETTLEMENT, REQUIRED
from v17.nhl_pregame_orchestration import orchestrate_nhl_pregame_gate
from v17.nhl_starter_feed_adapters import (
    CLUB_ENDPOINT_ENV,
    LEAGUE_ENDPOINT_ENV,
    NHLStarterSourceError,
    build_official_starter_adapters,
    collect_club_lineup_receipt,
    collect_league_roster_receipt,
)

NOW = datetime(2026, 11, 10, 17, 0, tzinfo=timezone.utc)
EVENT_ID = "2026020219"
START = NOW + timedelta(hours=3)
CLUB_URL = f"https://www.nhl.com/clubs/bos/lineup/{EVENT_ID}.json"
LEAGUE_URL = f"https://api-web.nhle.com/v1/roster-designation/{EVENT_ID}"


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


def club_payload(**overrides) -> dict:
    row = {
        "eventId": EVENT_ID,
        "startTimeUTC": START.isoformat(),
        "observedAt": (NOW - timedelta(minutes=10)).isoformat(),
        "homeTeam": {"abbrev": "BOS"},
        "awayTeam": {"abbrev": "NYR"},
        "startingGoalies": {
            "home": {"playerId": 8478402, "status": "CONFIRMED"},
            "away": {"playerId": 8479318, "status": "CONFIRMED"},
        },
    }
    row.update(overrides)
    return row


def league_payload(**overrides) -> dict:
    row = {
        "game": {"id": int(EVENT_ID), "startTimeUTC": START.isoformat()},
        "updatedAt": (NOW - timedelta(minutes=12)).isoformat(),
        "teams": {"home": "BOS", "away": "NYR"},
        "goaltenders": [
            {"playerId": 8478402, "teamAbbrev": "BOS", "designation": "STARTER", "status": "CONFIRMED"},
            {"playerId": 8479318, "teamAbbrev": "NYR", "designation": "STARTER", "status": "CONFIRMED"},
            {"playerId": 8470000, "teamAbbrev": "BOS", "designation": "BACKUP", "status": "CONFIRMED"},
        ],
    }
    row.update(overrides)
    return row


class Response:
    def __init__(self, data, *, status=200, url=CLUB_URL, raw=None, headers=None):
        self.status_code = status
        self.url = url
        self.content = raw if raw is not None else __import__("json").dumps(data).encode()
        self.headers = headers or {}


def getter(payload, *, status=200, url=CLUB_URL, raw=None, headers=None):
    calls = []

    def get(target, **kwargs):
        calls.append((target, kwargs))
        return Response(payload, status=status, url=target if url is None else url, raw=raw, headers=headers)

    get.calls = calls
    return get


def club(**kwargs):
    get = kwargs.pop("get", None) or getter(club_payload())
    return collect_club_lineup_receipt(
        identity=identity(), endpoint=CLUB_URL, as_of_utc=NOW, get=get, **kwargs
    )


def league(**kwargs):
    get = kwargs.pop("get", None) or getter(league_payload(), url=LEAGUE_URL)
    return collect_league_roster_receipt(
        identity=identity(), endpoint=LEAGUE_URL, as_of_utc=NOW, get=get, **kwargs
    )


def test_club_adapter_emits_a_bound_canonical_receipt():
    calls = getter(club_payload())
    result = collect_club_lineup_receipt(
        identity=identity(), endpoint=CLUB_URL, as_of_utc=NOW, get=calls
    )
    assert result["provider"] == "NHL_OFFICIAL_CLUB_FEED"
    assert result["source_kind"] == "STARTING_LINEUP"
    assert result["official_event_id"] == EVENT_ID
    assert result["home_team"] == "BOS" and result["away_team"] == "NYR"
    assert result["home_goalie_id"] == "8478402"
    assert result["away_goalie_id"] == "8479318"
    assert result["starting_status"] == "CONFIRMED"
    assert len(result["source_payload_sha256"]) == 64
    assert result["source_snapshot_id"].startswith("nhl-official-club-feed-")
    assert result["can_execute"] is False
    assert result["prediction_authority"] is False
    assert calls.calls[0][1]["allow_redirects"] is False


def test_league_adapter_emits_a_distinct_receipt_from_a_distinct_schema():
    result = league()
    assert result["provider"] == "NHL_OFFICIAL_LINEUP_FEED"
    assert result["source_kind"] == "OFFICIAL_ROSTER_DESIGNATION"
    assert result["home_goalie_id"] == "8478402"
    assert result["away_goalie_id"] == "8479318"
    assert result["source_snapshot_id"].startswith("nhl-official-lineup-feed-")


def test_each_adapter_rejects_the_other_schema():
    with pytest.raises(NHLStarterSourceError) as club_exc:
        club(get=getter(league_payload()))
    assert club_exc.value.code == "NHL_STARTER_FEED_SCHEMA_DRIFT"

    with pytest.raises(NHLStarterSourceError) as league_exc:
        league(get=getter(club_payload(), url=LEAGUE_URL))
    assert league_exc.value.code == "NHL_STARTER_FEED_SCHEMA_DRIFT"


def test_non_allowlisted_host_never_fetches():
    calls = getter(club_payload())
    with pytest.raises(NHLStarterSourceError) as exc:
        collect_club_lineup_receipt(
            identity=identity(),
            endpoint="https://evil.example/lineup.json",
            as_of_utc=NOW,
            get=calls,
        )
    assert exc.value.code == "NHL_STARTER_FEED_ENDPOINT_NOT_ALLOWLISTED"
    assert calls.calls == []


def test_http_redirect_and_oversize_payload_fail_typed():
    with pytest.raises(NHLStarterSourceError) as http_exc:
        club(get=getter(club_payload(), status=503))
    assert http_exc.value.code == "NHL_STARTER_FEED_HTTP_STATUS"

    with pytest.raises(NHLStarterSourceError) as redirect_exc:
        club(get=getter(club_payload(), status=302))
    assert redirect_exc.value.code == "NHL_STARTER_FEED_REDIRECT_REJECTED"

    with pytest.raises(NHLStarterSourceError) as size_exc:
        club(get=getter(club_payload()), max_bytes=10)
    assert size_exc.value.code == "NHL_STARTER_FEED_PAYLOAD_TOO_LARGE"


def test_transport_failure_is_typed_not_a_model_failure():
    def boom(*_args, **_kwargs):
        raise TimeoutError("upstream")

    with pytest.raises(NHLStarterSourceError) as exc:
        club(get=boom)
    assert exc.value.code == "NHL_STARTER_FEED_TRANSPORT_FAILED"


def test_cross_event_start_and_team_mismatches_hold():
    with pytest.raises(NHLStarterSourceError) as event_exc:
        club(get=getter(club_payload(eventId="2026020999")))
    assert event_exc.value.code == "NHL_STARTER_FEED_EVENT_MISMATCH"

    with pytest.raises(NHLStarterSourceError) as start_exc:
        club(get=getter(club_payload(startTimeUTC=(START + timedelta(hours=1)).isoformat())))
    assert start_exc.value.code == "NHL_STARTER_FEED_START_TIME_MISMATCH"

    with pytest.raises(NHLStarterSourceError) as team_exc:
        club(get=getter(club_payload(homeTeam={"abbrev": "NYR"}, awayTeam={"abbrev": "BOS"})))
    assert team_exc.value.code == "NHL_STARTER_FEED_TEAM_MISMATCH"


def test_after_kickoff_stale_and_missing_time_hold():
    with pytest.raises(NHLStarterSourceError) as kickoff_exc:
        collect_club_lineup_receipt(
            identity=identity(),
            endpoint=CLUB_URL,
            as_of_utc=START + timedelta(minutes=5),
            get=getter(club_payload(observedAt=(START + timedelta(minutes=1)).isoformat())),
        )
    assert kickoff_exc.value.code == "NHL_STARTER_FEED_AFTER_KICKOFF"

    with pytest.raises(NHLStarterSourceError) as stale_exc:
        club(get=getter(club_payload(observedAt=(NOW - timedelta(hours=2)).isoformat())))
    assert stale_exc.value.code == "NHL_STARTER_FEED_STALE"

    with pytest.raises(NHLStarterSourceError) as missing_exc:
        club(get=getter(club_payload(observedAt=None)))
    assert missing_exc.value.code == "NHL_STARTER_FEED_TIMESTAMP_MISSING"

    # A well-formed HTTP Date header is the server's clock, not observation
    # evidence, and must not rescue a payload that carries no timestamp.
    with pytest.raises(NHLStarterSourceError) as header_exc:
        club(
            get=getter(
                club_payload(observedAt=None),
                headers={"Date": "Tue, 10 Nov 2026 16:55:00 GMT"},
            )
        )
    assert header_exc.value.code == "NHL_STARTER_FEED_TIMESTAMP_MISSING"


def test_divergent_or_missing_goalie_status_holds():
    payload = club_payload()
    payload["startingGoalies"]["away"]["status"] = "PROJECTED"
    with pytest.raises(NHLStarterSourceError) as divergent:
        club(get=getter(payload))
    assert divergent.value.code == "NHL_STARTER_FEED_STATUS_DIVERGENT"

    payload = club_payload()
    payload["startingGoalies"]["away"]["status"] = ""
    with pytest.raises(NHLStarterSourceError) as missing:
        club(get=getter(payload))
    assert missing.value.code == "NHL_STARTER_FEED_STATUS_MISSING"

    # A goalie row that is not an object is schema drift, not an AttributeError.
    payload = club_payload()
    payload["startingGoalies"]["home"] = "CONFIRMED"
    with pytest.raises(NHLStarterSourceError) as drift:
        club(get=getter(payload))
    assert drift.value.code == "NHL_STARTER_FEED_SCHEMA_DRIFT"


def test_league_ambiguous_unknown_team_and_partial_starters_hold():
    payload = league_payload()
    payload["goaltenders"].append(
        {"playerId": 8479999, "teamAbbrev": "BOS", "designation": "STARTER", "status": "CONFIRMED"}
    )
    with pytest.raises(NHLStarterSourceError) as ambiguous:
        league(get=getter(payload, url=LEAGUE_URL))
    assert ambiguous.value.code == "NHL_STARTER_FEED_GOALIE_AMBIGUOUS"

    payload = league_payload()
    payload["goaltenders"][1]["teamAbbrev"] = "CAR"
    with pytest.raises(NHLStarterSourceError) as foreign:
        league(get=getter(payload, url=LEAGUE_URL))
    assert foreign.value.code == "NHL_STARTER_FEED_TEAM_NOT_IN_EVENT"

    payload = league_payload()
    payload["goaltenders"] = payload["goaltenders"][:1]
    with pytest.raises(NHLStarterSourceError) as partial:
        league(get=getter(payload, url=LEAGUE_URL))
    assert partial.value.code == "NHL_STARTER_FEED_GOALIE_MISSING"


def test_league_status_is_passed_through_never_invented():
    payload = league_payload()
    for row in payload["goaltenders"]:
        if row["designation"] == "STARTER":
            row["status"] = "PROJECTED"
    result = league(get=getter(payload, url=LEAGUE_URL))
    assert result["starting_status"] == "PROJECTED"


def test_unconfigured_endpoints_fail_closed(monkeypatch):
    monkeypatch.delenv(CLUB_ENDPOINT_ENV, raising=False)
    monkeypatch.delenv(LEAGUE_ENDPOINT_ENV, raising=False)
    with pytest.raises(NHLStarterSourceError) as exc:
        build_official_starter_adapters()
    assert exc.value.code == "NHL_STARTER_FEED_ENDPOINT_UNCONFIGURED"


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


class _Query:
    def __init__(self, db):
        self.db = db

    def upsert(self, rows, on_conflict=None, ignore_duplicates=False):
        self.db.calls.append({"rows": rows, "on_conflict": on_conflict, "ignore_duplicates": ignore_duplicates})
        return self

    def execute(self):
        return types.SimpleNamespace(data=self.db.calls[-1]["rows"])


class FakeDb:
    def __init__(self):
        self.calls = []

    def table(self, _name):
        return _Query(self)


def test_built_adapters_drive_the_orchestration_end_to_end():
    def get(url, **_kwargs):
        if url == CLUB_URL:
            return Response(club_payload(), url=CLUB_URL)
        if url == LEAGUE_URL:
            return Response(league_payload(), url=LEAGUE_URL)
        raise AssertionError(url)

    adapters = build_official_starter_adapters(
        club_endpoint=CLUB_URL, league_endpoint=LEAGUE_URL, get=get
    )
    assert [row.provider for row in adapters] == [
        "NHL_OFFICIAL_CLUB_FEED",
        "NHL_OFFICIAL_LINEUP_FEED",
    ]
    db = FakeDb()
    result = orchestrate_nhl_pregame_gate(
        db=db,
        identity=identity(),
        evidence=evidence(),
        adapters=adapters,
        as_of_utc=NOW,
    )
    assert result["status"] == "PREGAME_EVIDENCE_COMPLETE_NOT_CERTIFICATION"
    assert result["goalie_confirmation"]["status"] == "STARTER_GOALIE_CONFIRMED_RESEARCH_ONLY"
    assert result["receipt_persistence"]["persisted"] == 2
    assert len(db.calls[0]["rows"]) == 2
    assert result["can_execute"] is False
    assert result["probability_publishable"] is False


def test_adapter_failure_through_orchestration_is_not_model_unavailable():
    def get(url, **_kwargs):
        if url == CLUB_URL:
            return Response(club_payload(), status=429, url=CLUB_URL)
        return Response(league_payload(), url=LEAGUE_URL)

    adapters = build_official_starter_adapters(
        club_endpoint=CLUB_URL, league_endpoint=LEAGUE_URL, get=get
    )
    result = orchestrate_nhl_pregame_gate(
        db=FakeDb(),
        identity=identity(),
        evidence=evidence(),
        adapters=adapters,
        as_of_utc=NOW,
    )
    assert result["status"] == "HELD"
    assert any(
        blocker.startswith("NHL_PREGAME_STARTER_SOURCE_UNOBTAINABLE:NHL_OFFICIAL_CLUB_FEED:NHL_STARTER_FEED_HTTP_STATUS")
        for blocker in result["blockers"]
    )
    assert "MODEL_UNAVAILABLE" not in str(result)
