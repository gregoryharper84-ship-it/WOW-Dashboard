from types import SimpleNamespace

import team_event_request_runtime as runtime


class Query:
    def __init__(self, rows):
        self.rows = rows
        self.filters = []

    def select(self, *_args):
        return Query(self.rows)

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args):
        return self

    def execute(self):
        rows = [r for r in self.rows if all(str(r.get(k)) == str(v) for k, v in self.filters)]
        return SimpleNamespace(data=rows)


class DB:
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        assert name == "wow_mlb_forward_shadow_events"
        return Query(self.rows)


def row(**overrides):
    data = dict(
        research_run_id="wow-scout-test",
        objective_lane="OUTRIGHT_WIN_PROBABILITY",
        sport="MLB",
        league="MLB",
        event_key="MLB:espn-401816943",
        event_state="PREGAME",
        event_date="2026-09-15",
        timezone="America/Chicago",
        price_required_for_objective=False,
        event_start_time_utc="2026-09-15T23:10:00+00:00",
        home_team="Cleveland Guardians",
        away_team="Detroit Tigers",
    )
    data.update(overrides)
    return runtime.TeamEventRequestRow(**data)


def canonical_event(event_id="777777"):
    return {
        "official_event_id": event_id,
        "official_date": "2026-09-15",
        "event_start_time": "2026-09-15T23:10:00+00:00",
        "home_team": "Cleveland Guardians",
        "away_team": "Detroit Tigers",
        "venue_name": "Progressive Field",
        "home_probable_pitcher": "Home Starter",
        "away_probable_pitcher": "Away Starter",
        "snapshot_id": "snap-1",
        "snapshot_timestamp": "2026-09-15T15:00:00+00:00",
        "feature_hydration_status": "PASS",
    }


def test_hydrate_resolves_espn_scout_key_by_strict_canonical_identity():
    event = runtime._hydrate(DB([canonical_event()]), row())
    assert event is not None
    assert event["official_event_id"] == "777777"
    assert event["feature_hydration_status"] == "PASS"


def test_hydrate_does_not_fallback_without_complete_scout_identity():
    assert runtime._hydrate(DB([canonical_event()]), row(event_start_time_utc=None)) is None


def test_hydrate_rejects_ambiguous_canonical_identity():
    rows = [canonical_event("777777"), canonical_event("888888")]
    assert runtime._hydrate(DB(rows), row()) is None


def test_score_request_preserves_scout_event_key_but_uses_canonical_official_id():
    request = runtime._score_request(row(), canonical_event())
    assert request["event_key"] == "MLB:espn-401816943"
    assert request["official_event_id"] == "777777"
    assert request["source_snapshot_id"] == "snap-1"
