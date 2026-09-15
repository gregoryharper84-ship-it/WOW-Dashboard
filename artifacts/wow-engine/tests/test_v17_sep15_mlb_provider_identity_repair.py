from __future__ import annotations

from types import SimpleNamespace

from v17.mlb_team_event_hydration import resolve_mlb_team_event_evidence
from v17.sep15_runtime_contract_repairs import install_post_mlb_bridge_repairs


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, rows):
        self._rows = [dict(row) for row in rows]
        self._filters = []
        self._limit = None

    def select(self, *_a, **_k):
        return self

    def eq(self, field, value):
        self._filters.append((field, value))
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, value):
        self._limit = int(value)
        return self

    def execute(self):
        rows = self._rows
        for field, value in self._filters:
            rows = [row for row in rows if str(row.get(field)) == str(value)]
        if self._limit is not None:
            rows = rows[: self._limit]
        return _Result(rows)


class _Db:
    def __init__(self, rows):
        self.rows = rows

    def table(self, _name):
        return _Query(self.rows)


class _EventApi:
    def __init__(self, rows):
        self.rows = rows
        self.score_event = lambda req: {"status": "ORIGINAL", "can_execute": False}

    def get_client(self):
        return _Db(self.rows)


class _Req(SimpleNamespace):
    def model_copy(self, *, update):
        values = dict(self.__dict__)
        values.update(update)
        return _Req(**values)


def _req(**updates):
    base = dict(
        official_event_id="rundown-822849",
        requested_slate_date="2026-09-15",
        event_start_time_utc="2026-09-16T00:05:00Z",
        home_team="Texas Rangers",
        away_team="Boston Red Sox",
        source_snapshot_id="rundown-board-822849",
        latest_material_update_timestamp="2026-09-15T16:48:00Z",
        sport_specific_evidence={},
    )
    base.update(updates)
    return _Req(**base)


def _row(**updates):
    base = dict(
        official_event_id="822849",
        official_date="2026-09-15",
        event_start_time="2026-09-16T00:05:00Z",
        event_status="Scheduled",
        home_team="Texas Rangers",
        away_team="Boston Red Sox",
        venue_name="Globe Life Field",
        home_probable_pitcher="Jacob deGrom",
        away_probable_pitcher="Patrick Sandoval",
        snapshot_id="canonical-snapshot-822849",
        snapshot_timestamp="2026-09-15T16:47:00Z",
        feature_hydration_status="PASS",
    )
    base.update(updates)
    return base


def test_provider_event_id_resolves_to_unique_canonical_mlb_identity():
    result = resolve_mlb_team_event_evidence(_req(), event_api=_EventApi([_row()]))

    assert result["ok"] is True
    assert result["canonical_official_event_id"] == "822849"
    assert result["caller_official_event_id"] == "rundown-822849"
    assert result["canonical_identity_resolution"] == "PARTICIPANTS_START_SLATE"
    assert result["canonical_source_snapshot_id"] == "canonical-snapshot-822849"
    assert result["can_execute"] is False


def test_exact_canonical_event_id_stays_on_exact_lookup_path():
    result = resolve_mlb_team_event_evidence(
        _req(official_event_id="822849"), event_api=_EventApi([_row()])
    )

    assert result["ok"] is True
    assert result["canonical_official_event_id"] == "822849"
    assert result["canonical_identity_resolution"] == "EXACT_OFFICIAL_EVENT_ID"


def test_identity_join_fails_closed_when_two_official_events_match():
    rows = [
        _row(snapshot_id="snapshot-a"),
        _row(official_event_id="999999", snapshot_id="snapshot-b"),
    ]
    result = resolve_mlb_team_event_evidence(_req(), event_api=_EventApi(rows))

    assert result["ok"] is False
    assert result["code"] == "MLB_TEAM_EVENT_CANONICAL_IDENTITY_AMBIGUOUS"
    assert result["candidate_count"] == 2


def test_identity_join_does_not_match_different_participants():
    result = resolve_mlb_team_event_evidence(
        _req(),
        event_api=_EventApi([_row(home_team="Houston Astros")]),
    )

    assert result["ok"] is False
    assert result["code"] == "MLB_TEAM_EVENT_CANONICAL_SNAPSHOT_UNAVAILABLE"


def test_post_bridge_runtime_rewrites_provider_id_before_original_canonicalizer():
    event_api = _EventApi([_row()])
    market_api = SimpleNamespace(prod=SimpleNamespace(event_api=event_api))
    seen = {}

    def original_canonicalize(req, _event_api):
        seen["official_event_id"] = req.official_event_id
        return req

    team_runtime = SimpleNamespace(
        _canonicalize_public_mlb_request=original_canonicalize,
        _run_mlb_llp_governance=lambda *a, **k: {"rank_eligible": False, "can_execute": False},
    )

    assert install_post_mlb_bridge_repairs(
        market_api=market_api,
        team_runtime=team_runtime,
    ) is True

    out = team_runtime._canonicalize_public_mlb_request(_req(), event_api)
    assert out.official_event_id == "822849"
    assert seen["official_event_id"] == "822849"
    assert team_runtime._v17_sep15_provider_identity_repair_installed is True
