from types import SimpleNamespace

from v17.mlb_team_event_hydration import resolve_mlb_team_event_evidence


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, rows):
        self.rows = list(rows)
        self.filters = []
        self.max_rows = None

    def select(self, *_a, **_k):
        return self

    def eq(self, field, value):
        self.filters.append((field, value))
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, value):
        self.max_rows = int(value)
        return self

    def execute(self):
        rows = self.rows
        for field, value in self.filters:
            rows = [row for row in rows if row.get(field) == value]
        if self.max_rows is not None:
            rows = rows[: self.max_rows]
        return _Result(rows)


class _Db:
    def __init__(self, event_rows):
        self.event_rows = list(event_rows)

    def table(self, name):
        if name == "wow_mlb_forward_shadow_events":
            return _Query(self.event_rows)
        if name == "wow_mlb_v2d_frozen_spec":
            return _Query([])
        return _Query([])


class _Api:
    def __init__(self, rows):
        self.rows = rows

    def get_client(self):
        return _Db(self.rows)


def _req(**updates):
    base = dict(
        official_event_id="823408",
        requested_slate_date="2026-09-27",
        event_start_time_utc="2026-09-27T18:30:00Z",
        home_team="Philadelphia Phillies",
        away_team="Tampa Bay Rays",
        source_snapshot_id="caller-snapshot",
        latest_material_update_timestamp="2026-09-27T18:00:00Z",
        sport_specific_evidence={},
    )
    base.update(updates)
    return SimpleNamespace(**base)


def _row(**updates):
    base = dict(
        official_event_id="823408",
        official_date="2026-09-27",
        event_start_time="2026-09-27T18:30:00Z",
        event_status="Pre-Game",
        home_team="Philadelphia Phillies",
        away_team="Tampa Bay Rays",
        venue_name="Citizens Bank Park",
        home_probable_pitcher="Zack Wheeler",
        away_probable_pitcher="Nick Martinez",
        snapshot_id="canonical-snapshot",
        snapshot_timestamp="2026-09-27T18:15:00Z",
        feature_hydration_status="NOT_STARTED",
    )
    base.update(updates)
    return base


def test_populated_unhydrated_row_reports_hydration_not_missing_identity_fields():
    result = resolve_mlb_team_event_evidence(_req(), event_api=_Api([_row()]))

    assert result["ok"] is False
    assert result["code"] == "MLB_TEAM_EVENT_HYDRATION_NOT_READY"
    assert result["feature_hydration_status"] == "NOT_STARTED"
    assert result["missing_fields"] == ["feature_hydration_status"]
    assert result["canonical_source_snapshot_id"] == "canonical-snapshot"
    assert result["can_execute"] is False


def test_unhydrated_canonical_row_cannot_be_bypassed_by_complete_caller_evidence():
    req = _req(
        sport_specific_evidence={
            "venue": "Citizens Bank Park",
            "home_starting_pitcher": "Zack Wheeler",
            "away_starting_pitcher": "Nick Martinez",
            "home_starter_status": "PROBABLE",
            "away_starter_status": "PROBABLE",
            "home_lineup_status": "PROJECTED",
            "away_lineup_status": "PROJECTED",
        }
    )
    result = resolve_mlb_team_event_evidence(req, event_api=_Api([_row()]))

    assert result["ok"] is False
    assert result["code"] == "MLB_TEAM_EVENT_HYDRATION_NOT_READY"
    assert result["can_execute"] is False


def test_missing_snapshot_timestamp_is_reported_as_true_canonical_incompleteness():
    result = resolve_mlb_team_event_evidence(
        _req(), event_api=_Api([_row(snapshot_timestamp=None)])
    )

    assert result["ok"] is False
    assert result["code"] == "MLB_TEAM_EVENT_CANONICAL_SNAPSHOT_INCOMPLETE"
    assert "snapshot_timestamp" in result["missing_fields"]
    assert result["can_execute"] is False


def test_no_matching_raw_row_remains_canonical_snapshot_unavailable():
    result = resolve_mlb_team_event_evidence(_req(), event_api=_Api([]))

    assert result["ok"] is False
    assert result["code"] == "MLB_TEAM_EVENT_CANONICAL_SNAPSHOT_UNAVAILABLE"
