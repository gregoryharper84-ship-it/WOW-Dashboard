"""NCAAB discovery aliases become canonical only after a schedule match.

The Nov 9, 2026 slate in the diagnostic (Radford at Tennessee, Grambling at
Texas, Wagner at UConn, North Florida at Miami, Oakland at Alabama) is the
regression. Identity resolution must not copy the ESPN alias, and it must not
make an uncertified NCAAB row rank-eligible.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from v17 import ncaab_event_identity as identity
from v17.cross_sport_winner_discovery import (
    DiscoveryInventory,
    DiscoveredEvent,
    IDENTITY_UNRESOLVED,
    MODEL_UNAVAILABLE,
    route_discovered_slate,
)
from v17.team_event_sport_parity import canonicalize_ncaab_discovery_identity

# Synthetic tip-time fixtures retaining the reported Nov 9 schedule IDs.
# These test times are not assertions about the real fixtures.
NOV9_SCHEDULE = [
    {
        "game_id": "401906849",
        "game_date": "2026-11-09",
        "game_date_time": "2026-11-09 19:00:00-05:00",
        "time_valid": True,
        "home_id": "2633",
        "away_id": "2515",
        "home_display_name": "Tennessee Volunteers",
        "away_display_name": "Radford Highlanders",
    },
    {
        "game_id": "401906865",
        "game_date": "2026-11-09",
        "game_date_time": "2026-11-09 19:00:00-05:00",
        "time_valid": True,
        "home_id": "251",
        "away_id": "2755",
        "home_display_name": "Texas Longhorns",
        "away_display_name": "Grambling Tigers",
    },
    {
        "game_id": "401920686",
        "game_date": "2026-11-09",
        "game_date_time": "2026-11-09 19:00:00-05:00",
        "time_valid": True,
        "home_id": "41",
        "away_id": "2681",
        "home_display_name": "UConn Huskies",
        "away_display_name": "Wagner Seahawks",
    },
    {
        "game_id": "401921720",
        "game_date": "2026-11-09",
        "game_date_time": "2026-11-09 19:00:00-05:00",
        "time_valid": True,
        "home_id": "2390",
        "away_id": "2454",
        "home_display_name": "Miami Hurricanes",
        "away_display_name": "North Florida Ospreys",
    },
    {
        "game_id": "401906759",
        "game_date": "2026-11-09",
        "game_date_time": "2026-11-09 19:00:00-05:00",
        "time_valid": True,
        "home_id": "333",
        "away_id": "2473",
        "home_display_name": "Alabama Crimson Tide",
        "away_display_name": "Oakland Golden Grizzlies",
    },
]


def _fetch(_year):
    """Return deterministic NCAAB schedule rows without an HTTP call."""
    return list(NOV9_SCHEDULE)


def test_schedule_asset_uses_season_ending_year():
    """Check season-ending-year conversion across November and spring."""
    assert identity.schedule_season_year(datetime(2026, 11, 9, tzinfo=timezone.utc)) == 2027
    assert identity.schedule_season_year(datetime(2027, 3, 7, tzinfo=timezone.utc)) == 2027
    assert identity.schedule_season_year(datetime(2026, 4, 6, tzinfo=timezone.utc)) == 2026


@pytest.mark.parametrize(
    ("home_id", "away_id", "game_id"),
    [
        ("2633", "2515", "401906849"),
        ("251", "2755", "401906865"),
        ("41", "2681", "401920686"),
        ("2390", "2454", "401921720"),
        ("333", "2473", "401906759"),
    ],
)
def test_diagnostic_matchups_resolve_through_schedule_not_discovery_alias(home_id, away_id, game_id):
    """Confirm all five schedule IDs, never an unverified provider event alias."""
    # 00:00Z on Nov 10 is still the Nov 9 local slate. The discovery alias is a
    # different id and must not be adopted.
    out = identity.resolve_ncaab_current_event_identity(
        event_start_time="2026-11-10T00:00:00Z",
        home_team_alias=f"espn-{home_id}",
        away_team_alias=away_id,
        fetcher=_fetch,
    )
    assert out["event_id"] == f"espn-{game_id}"
    assert out["event_id"] != "espn-999999999"
    assert out["home_team_id"] == f"espn-{home_id}"
    assert out["away_team_id"] == f"espn-{away_id}"
    assert out["identity_provider"] == "SPORTSDATAVERSE_ESPN"
    assert out["prediction_authority"] is False
    assert out["probability_publishable"] is False
    assert out["rank_eligible"] is False
    assert out["can_execute"] is False


def test_swapped_or_unknown_teams_fail_closed():
    """Hold swapped ordered teams or incomplete ESPN team aliases."""
    with pytest.raises(identity.NCAABEventIdentityError) as swapped:
        identity.resolve_ncaab_current_event_identity(
            event_start_time="2026-11-10T00:00:00Z",
            home_team_alias="2515",
            away_team_alias="2633",
            fetcher=_fetch,
        )
    assert swapped.value.code == "NCAAB_CANONICAL_EVENT_NOT_FOUND"

    with pytest.raises(identity.NCAABEventIdentityError) as missing:
        identity.resolve_ncaab_current_event_identity(
            event_start_time="2026-11-10T00:00:00Z",
            home_team_alias="",
            away_team_alias="2515",
            fetcher=_fetch,
        )
    assert missing.value.code == "NCAAB_PROVIDER_TEAM_ALIAS_INVALID"


def test_two_schedule_ids_for_the_same_teams_stay_ambiguous():
    """Reject overlapping schedule IDs when uniqueness cannot be proved."""
    def duplicate(_year):
        """Expose conflicting same-team games to prove ambiguity remains held."""
        return [
            {"game_id": "1", "game_date": "2026-11-09", "game_date_time": "2026-11-09 19:00:00-05:00", "time_valid": True, "home_id": "2633", "away_id": "2515"},
            {"game_id": "2", "game_date": "2026-11-10", "game_date_time": "2026-11-09 19:10:00-05:00", "time_valid": True, "home_id": "2633", "away_id": "2515"},
        ]

    with pytest.raises(identity.NCAABEventIdentityError) as exc:
        identity.resolve_ncaab_current_event_identity(
            event_start_time="2026-11-10T00:00:00Z",
            home_team_alias="2633",
            away_team_alias="2515",
            fetcher=duplicate,
        )
    assert exc.value.code == "NCAAB_CANONICAL_EVENT_AMBIGUOUS"


def test_schedule_fetch_parses_required_columns_and_rejects_drift(monkeypatch):
    """Distinguish valid CSV, missing mandatory columns and source outage."""
    class Response:
        def __init__(self, status, text):
            self.status_code = status
            self.text = text

    class Session:
        def get(self, url, headers=None, timeout=None):
            assert url.endswith("mbb_schedule_2027.csv")
            assert headers["User-Agent"] == "WOW-V17-NCAAB-Identity/1.0"
            return Response(
                200,
                "game_id,game_date,home_id,away_id,game_date_time,time_valid\n"
                "401906849,2026-11-09,2633,2515,2026-11-09 19:00:00-05:00,True\n",
            )

    rows = identity.fetch_ncaab_schedule(2027, session=Session())
    assert rows == [
        {"game_id": "401906849", "game_date": "2026-11-09", "home_id": "2633", "away_id": "2515", "game_date_time": "2026-11-09 19:00:00-05:00", "time_valid": "True"}
    ]

    class DriftSession:
        def get(self, url, headers=None, timeout=None):
            return Response(200, "game_id,home_name\n1,Tennessee\n")

    with pytest.raises(identity.NCAABEventIdentityError) as drift:
        identity.fetch_ncaab_schedule(2027, session=DriftSession())
    assert drift.value.code == "NCAAB_SCHEDULE_SCHEMA_DRIFT"

    class DownSession:
        def get(self, url, headers=None, timeout=None):
            return Response(503, "")

    with pytest.raises(identity.NCAABEventIdentityError) as down:
        identity.fetch_ncaab_schedule(2027, session=DownSession())
    assert down.value.code == "NCAAB_SCHEDULE_SOURCE_UNAVAILABLE"


def test_unready_schedule_source_fails_closed(monkeypatch):
    """Do not fetch schedule bytes if the source entitlement is unready."""
    monkeypatch.setattr(
        identity,
        "source_readiness",
        lambda _source: type("Ready", (), {
            "ready_for_candidate_training": False,
            "blockers": ("MODEL_SOURCE_NOT_AUTHORIZED_FOR_FITTED_TRAINING",),
        })(),
    )

    class Session:
        def get(self, *args, **kwargs):
            raise AssertionError("blocked source must not be fetched")

    with pytest.raises(identity.NCAABEventIdentityError) as exc:
        identity.fetch_ncaab_schedule(2027, session=Session())
    assert exc.value.code == "NCAAB_SCHEDULE_SOURCE_NOT_READY"


def _alias_event(*, home_id="2633", away_id="2515", provider_event_id="999999999"):
    """Build an alias-only pregame NCAAB event with no publication authority."""
    return DiscoveredEvent(
        sport="NCAAB",
        league="NCAAB",
        sport_key="basketball_ncaab",
        official_event_id=None,
        home_team="Tennessee Volunteers",
        away_team="Radford Highlanders",
        commence_time_utc="2026-11-10T00:00:00Z",
        event_status="PREGAME",
        source="DISCOVERY_FEED",
        provider="ESPN_SCOREBOARD",
        raw={
            "provider_event_id": provider_event_id,
            "_wow_secondary_event_id": provider_event_id,
            "_wow_secondary_home_team_id": home_id,
            "_wow_secondary_away_team_id": away_id,
            "official_event_id": None,
            "prediction_authority": False,
        },
    )


def test_discovery_alias_is_rewritten_only_after_schedule_match(monkeypatch):
    """Prove source-schedule canonicalization retains the discovery alias."""
    original = identity.resolve_ncaab_current_event_identity

    def offline(**kwargs):
        return original(**{**kwargs, "fetcher": _fetch})

    monkeypatch.setattr(identity, "resolve_ncaab_current_event_identity", offline)
    out = canonicalize_ncaab_discovery_identity(_alias_event())
    assert out.official_event_id == "espn-401906849"
    assert out.official_event_id != "espn-999999999"
    assert out.raw["provider_event_id"] == "999999999"
    assert out.raw["canonical_identity_status"] == "CANONICAL_RESOLVED"
    assert out.raw["canonical_identity_market_features_used"] is False
    assert out.raw["canonical_identity_schedule_game_id"] == "401906849"
    assert out.raw["canonical_identity_schedule_tip_utc"] == "2026-11-10T00:00:00+00:00"
    assert out.raw["canonical_identity_tip_drift_seconds"] == 0
    assert out.raw["canonical_identity_home_team_id"] == "espn-2633"
    assert out.raw["canonical_identity_away_team_id"] == "espn-2515"
    assert out.raw["canonical_identity_source_asset"].endswith("mbb_schedule_2027.csv")
    assert out.raw["prediction_authority"] is False


def test_missing_team_alias_stays_unresolved_even_when_names_are_present():
    """Never infer team IDs from unsourced display names."""
    event = _alias_event()
    event = DiscoveredEvent(
        sport=event.sport,
        league=event.league,
        sport_key=event.sport_key,
        official_event_id="espn-999999999",
        home_team=event.home_team,
        away_team=event.away_team,
        commence_time_utc=event.commence_time_utc,
        event_status=event.event_status,
        source=event.source,
        provider=event.provider,
        raw={"provider_event_id": "999999999"},
    )
    out = canonicalize_ncaab_discovery_identity(event)
    assert out.official_event_id is None
    assert out.raw["canonical_identity_blocker"] == "NCAAB_PROVIDER_TEAM_ALIAS_MISSING"


def test_resolved_identity_can_reach_model_routing_without_becoming_a_pick(monkeypatch):
    """Preserve a MODEL_UNAVAILABLE hold after legitimate identity resolution."""
    original = identity.resolve_ncaab_current_event_identity

    def offline(**kwargs):
        return original(**{**kwargs, "fetcher": _fetch})

    monkeypatch.setattr(identity, "resolve_ncaab_current_event_identity", offline)
    event = canonicalize_ncaab_discovery_identity(_alias_event())
    inventory = DiscoveryInventory(
        requested_slate_date="2026-11-09",
        requested_timezone="America/New_York",
        events=[event],
    )
    rows = route_discovered_slate(
        inventory,
        resolve_model=lambda _event: None,
        score_row=lambda *_args, **_kwargs: pytest.fail("uncertified NCAAB model must not be invoked"),
    )
    assert len(rows) == 1
    assert rows[0].bucket == MODEL_UNAVAILABLE
    assert rows[0].bucket != IDENTITY_UNRESOLVED
    assert rows[0].rank_eligible is False
    assert rows[0].probability_publishable is False
    assert rows[0].detail["model_invoked"] is False
    assert rows[0].detail["reason"] == "NCAAB_TEAM_EVENT_FITTED_MODEL_OR_ADAPTER_UNAVAILABLE"
    assert rows[0].as_dict()["can_execute"] is False


def test_unmatched_alias_remains_identity_unresolved_and_unscored():
    """An unresolved alias must not invoke even a supplied scorer."""
    identity_event = DiscoveredEvent(
        sport="NCAAB",
        league="NCAAB",
        sport_key="basketball_ncaab",
        official_event_id=None,
        home_team="Nobody",
        away_team="Else",
        commence_time_utc="2026-11-10T00:00:00Z",
        event_status="PREGAME",
        source="DISCOVERY_FEED",
        provider="ESPN_SCOREBOARD",
        raw={
            "_wow_secondary_home_team_id": "1",
            "_wow_secondary_away_team_id": "2",
        },
    )

    def fail(**_kwargs):
        raise identity.NCAABEventIdentityError("NCAAB_CANONICAL_EVENT_NOT_FOUND")

    import v17.ncaab_event_identity as module
    original = module.resolve_ncaab_current_event_identity
    module.resolve_ncaab_current_event_identity = fail
    try:
        unresolved = canonicalize_ncaab_discovery_identity(identity_event)
    finally:
        module.resolve_ncaab_current_event_identity = original

    inventory = DiscoveryInventory(
        requested_slate_date="2026-11-09",
        requested_timezone="America/New_York",
        events=[unresolved],
    )
    rows = route_discovered_slate(
        inventory,
        resolve_model=lambda _event: object(),
        score_row=lambda *_args, **_kwargs: pytest.fail("identity failure must not score"),
    )
    assert unresolved.official_event_id is None
    assert unresolved.raw["canonical_identity_blocker"] == "NCAAB_CANONICAL_EVENT_NOT_FOUND"
    assert rows[0].bucket == IDENTITY_UNRESOLVED
    assert rows[0].detail["model_invoked"] is False
    assert rows[0].rank_eligible is False

def test_wrong_tip_and_wrong_date_cannot_establish_identity():
    """Reject schedule rows with wrong tip, date, season or validity flags."""
    for patch in (
        {"game_date_time": "2026-11-09 15:00:00-05:00"},
        {"game_date": "2026-11-11", "game_date_time": "2026-11-11 19:00:00-05:00"},
        {"time_valid": False},
        {"season": 2026},
        {"game_date_time": ""},
        {"game_date_time": "not-a-time"},
    ):
        with pytest.raises(identity.NCAABEventIdentityError) as exc:
            identity.resolve_ncaab_current_event_identity(
                event_start_time="2026-11-10T00:00:00Z",
                home_team_alias="2633", away_team_alias="2515",
                fetcher=lambda _year: [{**NOV9_SCHEDULE[0], **patch}],
            )
        assert exc.value.code == "NCAAB_CANONICAL_EVENT_NOT_FOUND"


@pytest.mark.parametrize("value", ["2026-11-10T00:00:00", "not-a-time"])
def test_discovery_tip_without_utc_is_invalid(value):
    """Fail closed on malformed or timezone-naive discovery tips."""
    with pytest.raises(identity.NCAABEventIdentityError) as exc:
        identity.resolve_ncaab_current_event_identity(
            event_start_time=value, home_team_alias="2633",
            away_team_alias="2515", fetcher=_fetch,
        )
    assert exc.value.code == "NCAAB_EVENT_TIME_INVALID"


def test_schedule_naive_datetime_uses_documented_new_york_timezone():
    """Normalize the source's offset-free Eastern tip to UTC."""
    row = {**NOV9_SCHEDULE[0], "game_date_time": "2026-11-09 19:00:00"}
    out = identity.resolve_ncaab_current_event_identity(
        event_start_time="2026-11-10T00:00:00Z",
        home_team_alias="2633", away_team_alias="2515",
        fetcher=lambda _year: [row],
    )
    assert out["matched_schedule_tip_utc"] == "2026-11-10T00:00:00+00:00"
    assert out["identity_tip_drift_seconds"] == 0


def test_rate_limit_is_typed_and_negatively_cached(monkeypatch):
    """Typed 429 failures must be cached without obscuring their blocker."""
    class Session:
        def get(self, *_args, **_kwargs):
            return type("Response", (), {"status_code": 429, "text": ""})()
    with pytest.raises(identity.NCAABEventIdentityError) as exc:
        identity.fetch_ncaab_schedule(2027, session=Session())
    assert exc.value.code == "NCAAB_SCHEDULE_SOURCE_RATE_LIMITED"
    identity.reset_cache()
    calls = []
    def failed(_year):
        """Count upstream 429 attempts for the negative-cache test."""
        calls.append(True)
        raise identity.NCAABEventIdentityError("NCAAB_SCHEDULE_SOURCE_RATE_LIMITED")
    monkeypatch.setattr(identity, "fetch_ncaab_schedule", failed)
    for _ in range(3):
        with pytest.raises(identity.NCAABEventIdentityError) as cached:
            identity._schedule_rows(2027)
        assert cached.value.code == "NCAAB_SCHEDULE_SOURCE_RATE_LIMITED"
    assert len(calls) == 1
    identity.reset_cache()


@pytest.mark.parametrize(
    ("offset_seconds", "resolved"),
    [(1800, True), (1801, False), (-1800, True), (-1801, False)],
)
def test_tip_drift_exact_boundary_stays_fail_closed(offset_seconds, resolved):
    """An inclusive 30m boundary must not accept one extra second."""
    from datetime import timedelta
    tip = datetime(2026, 11, 10, tzinfo=timezone.utc) + timedelta(seconds=offset_seconds)
    schedule = [{**NOV9_SCHEDULE[0], "game_date_time": tip.isoformat()}]
    args = dict(
        event_start_time="2026-11-10T00:00:00Z",
        home_team_alias="2633", away_team_alias="2515",
        fetcher=lambda _year: schedule,
    )
    if resolved:
        row = identity.resolve_ncaab_current_event_identity(**args)
        assert row["event_id"] == "espn-401906849"
        assert row["identity_tip_drift_seconds"] == 1800
        assert row["probability_publishable"] is False
    else:
        with pytest.raises(identity.NCAABEventIdentityError) as exc:
            identity.resolve_ncaab_current_event_identity(**args)
        assert exc.value.code == "NCAAB_CANONICAL_EVENT_NOT_FOUND"


def test_typed_negative_cache_expires_and_source_can_recover(monkeypatch):
    """A recovered source must be fetched after 30s, never pinned in error."""
    identity.reset_cache()
    clock = [100.0]
    calls = []
    def fetch(_year):
        """Fail the first scheduled fetch, then return source rows on recovery."""
        calls.append(clock[0])
        if len(calls) == 1:
            raise identity.NCAABEventIdentityError("NCAAB_SCHEDULE_SOURCE_RATE_LIMITED")
        return list(NOV9_SCHEDULE)
    monkeypatch.setattr(identity.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(identity, "fetch_ncaab_schedule", fetch)
    try:
        with pytest.raises(identity.NCAABEventIdentityError) as first:
            identity._schedule_rows(2027)
        assert first.value.code == "NCAAB_SCHEDULE_SOURCE_RATE_LIMITED"
        clock[0] = 129.999
        with pytest.raises(identity.NCAABEventIdentityError):
            identity._schedule_rows(2027)
        assert len(calls) == 1
        clock[0] = 130.001
        recovered = identity._schedule_rows(2027)
        assert recovered[0]["game_id"] == "401906849"
        assert len(calls) == 2
        clock[0] = 131.0
        assert identity._schedule_rows(2027) == recovered
        assert len(calls) == 2
    finally:
        identity.reset_cache()


def test_held_ncaab_routed_identity_survives_daily_persisted_detail(monkeypatch):
    """The real Daily row serializer must retain schedule proof with no model."""
    from types import SimpleNamespace
    from v17.daily_response_contract import persist_row_detail
    from v17.daily_snapshot_runtime import _terminal_row

    original = identity.resolve_ncaab_current_event_identity
    monkeypatch.setattr(
        identity, "resolve_ncaab_current_event_identity",
        lambda **kwargs: original(**{**kwargs, "fetcher": _fetch}),
    )
    event = canonicalize_ncaab_discovery_identity(_alias_event())
    inventory = DiscoveryInventory(
        requested_slate_date="2026-11-09", requested_timezone="America/New_York",
        events=[event],
    )
    routed = route_discovered_slate(
        inventory,
        resolve_model=lambda _event: None,
        score_row=lambda *_args, **_kwargs: pytest.fail("no fitted model"),
    )
    assert len(routed) == 1
    assert routed[0].bucket == MODEL_UNAVAILABLE
    data = routed[0].as_dict()
    assert data["canonical_identity_schedule_game_id"] == "401906849"
    assert data["canonical_identity_schedule_tip_utc"] == "2026-11-10T00:00:00+00:00"
    assert data["canonical_identity_tip_drift_seconds"] == 0
    assert data["canonical_identity_home_team_id"] == "espn-2633"
    assert data["canonical_identity_away_team_id"] == "espn-2515"
    assert data["canonical_identity_source_asset"].endswith("mbb_schedule_2027.csv")
    assert data["rank_eligible"] is False
    assert data["probability_publishable"] is False

    # Exact wrapper used by the production cross-sport parity handoff:
    # the five-key identity is deliberately compact, but "result" is
    # the full routed row and is written into immutable Daily detail.
    row = _terminal_row(
        "MONEYLINE",
        {key: data.get(key) for key in (
            "official_event_id", "commence_time_utc", "home_team", "away_team", "sport"
        )},
        data,
        {"final_terminal": "HELD"},
    )
    class CaptureDB:
        def __init__(self):
            self.rows = []
            self.selecting = False
        def table(self, _name):
            return self
        def upsert(self, rows, **_kwargs):
            self.rows = rows
            self.selecting = False
            return self
        def select(self, *_args):
            self.selecting = True
            return self
        def eq(self, *_args):
            return self
        def order(self, *_args):
            return self
        def range(self, *_args):
            return self
        def execute(self):
            """Return readback indices only after selecting persisted rows."""
            return SimpleNamespace(
                data=[{"row_index": r["row_index"]} for r in self.rows]
                if self.selecting else []
            )

    db = CaptureDB()
    receipt = persist_row_detail(db, run_id="ncaab-proof-regression", rows=[row])
    assert receipt["status"] == "PERSISTED"
    stored = db.rows[0]
    assert stored["row_status"] == "HELD"
    result = stored["detail"]["result"]
    for key in (
        "canonical_identity_schedule_game_id",
        "canonical_identity_schedule_tip_utc",
        "canonical_identity_tip_drift_seconds",
        "canonical_identity_home_team_id",
        "canonical_identity_away_team_id",
        "canonical_identity_source_asset",
    ):
        assert result[key] == data[key]
    assert result["model_status"] == MODEL_UNAVAILABLE
    assert result["rank_eligible"] is False
    assert result["probability_publishable"] is False
    assert stored["can_execute"] is False


def test_unresolved_ncaab_exposes_only_typed_blocker_not_untrusted_raw_payload():
    """The persisted hold must neither leak source prices nor forge proof."""
    event = _alias_event()
    raw = dict(event.raw)
    raw.update({
        "canonical_identity_status": "ALIAS_ONLY_UNRESOLVED",
        "canonical_identity_blocker": "NCAAB_CANONICAL_EVENT_NOT_FOUND",
        "canonical_identity_source_asset": "forged-source",
        "price": -999,
    })
    from dataclasses import replace
    unresolved = replace(event, raw=raw, official_event_id=None)
    data = unresolved.identity()
    assert data["canonical_identity_blocker"] == "NCAAB_CANONICAL_EVENT_NOT_FOUND"
    assert "canonical_identity_source_asset" not in data
    assert "price" not in data
