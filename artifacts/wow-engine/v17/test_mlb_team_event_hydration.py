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

    def select(self, *_a, **_k): return self

    def eq(self, field, value):
        self.filters.append((field, value))
        return self

    def order(self, *_a, **_k): return self

    def limit(self, value, *_a, **_k):
        self.max_rows = value
        return self

    def execute(self):
        rows = [
            row for row in self.rows
            if all(row.get(field) == value for field, value in self.filters)
        ]
        if self.max_rows is not None:
            rows = rows[: self.max_rows]
        return _Result(rows)


class _Db:
    def __init__(self, rows): self.rows = rows
    def table(self, _name): return _Query(self.rows)


class _Api:
    def __init__(self, rows): self.rows = rows
    def get_client(self): return _Db(self.rows)


def _req(**updates):
    base = dict(
        official_event_id="823983",
        requested_slate_date="2099-09-02",
        event_start_time_utc="2099-09-03T01:38:00Z",
        home_team="Los Angeles Angels",
        away_team="New York Yankees",
        source_snapshot_id="KALSHI-SNAPSHOT-823983",
        sport_specific_evidence={},
    )
    base.update(updates)
    return SimpleNamespace(**base)


def _row(**updates):
    base = dict(
        official_event_id="823983",
        official_date="2099-09-02",
        event_start_time="2099-09-03T01:38:00Z",
        event_status="Scheduled",
        home_team="Los Angeles Angels",
        away_team="New York Yankees",
        venue_name="Angel Stadium",
        home_probable_pitcher="Reid Detmers",
        away_probable_pitcher="Cam Schlittler",
        snapshot_id="c2419143-687b-4bef-b9b5-1f589f497b56",
        snapshot_timestamp="2026-09-02T22:32:00Z",
        feature_hydration_status="PASS",
    )
    base.update(updates)
    return base


def test_823983_shape_hydrates_all_required_event_fields_from_canonical_snapshot():
    result = resolve_mlb_team_event_evidence(_req(), event_api=_Api([_row()]))
    assert result["ok"] is True
    assert result["code"] == "MLB_TEAM_EVENT_CANONICAL_EVIDENCE_READY"
    assert result["evidence"] == {
        "venue": "Angel Stadium",
        "official_event_status": "Scheduled",
        "home_starting_pitcher": "Reid Detmers",
        "away_starting_pitcher": "Cam Schlittler",
        "home_starter_status": "PROBABLE",
        "away_starter_status": "PROBABLE",
        "home_lineup_status": "PROJECTED",
        "away_lineup_status": "PROJECTED",
    }
    assert result["canonical_source_snapshot_id"] == "c2419143-687b-4bef-b9b5-1f589f497b56"
    assert result["canonical_official_event_id"] == "823983"
    assert result["caller_source_snapshot_id"] == "KALSHI-SNAPSHOT-823983"
    assert result["can_execute"] is False


def test_caller_cannot_override_canonical_starter():
    req = _req(sport_specific_evidence={"away_starting_pitcher": "Someone Else"})
    result = resolve_mlb_team_event_evidence(req, event_api=_Api([_row()]))
    assert result["ok"] is False
    assert result["code"] == "MLB_TEAM_EVENT_CALLER_EVIDENCE_CONTRADICTS_CANONICAL"
    assert result["identity_mismatches"] == ["away_starting_pitcher"]


def test_event_identity_mismatch_fails_closed():
    result = resolve_mlb_team_event_evidence(
        _req(home_team="Different Home"), event_api=_Api([_row()])
    )
    assert result["ok"] is False
    assert result["code"] == "MLB_TEAM_EVENT_CANONICAL_IDENTITY_MISMATCH"
    assert "home_team" in result["identity_mismatches"]


def test_hydration_not_ready_is_not_mislabeled_as_missing_venue_or_starters():
    result = resolve_mlb_team_event_evidence(
        _req(), event_api=_Api([_row(feature_hydration_status="NOT_STARTED")])
    )
    assert result["ok"] is False
    assert result["code"] == "MLB_TEAM_EVENT_HYDRATION_NOT_READY"
    assert result["feature_hydration_status"] == "NOT_STARTED"
    assert result["missing_fields"] == []
    assert result["canonical_official_event_id"] == "823983"
    assert result["can_execute"] is False


def test_hydration_barrier_cannot_be_bypassed_by_complete_caller_evidence():
    req = _req(
        latest_material_update_timestamp="2026-09-02T22:32:00Z",
        sport_specific_evidence={
            "venue": "Angel Stadium",
            "home_starting_pitcher": "Reid Detmers",
            "away_starting_pitcher": "Cam Schlittler",
        },
    )
    result = resolve_mlb_team_event_evidence(
        req, event_api=_Api([_row(feature_hydration_status="NOT_STARTED")])
    )
    assert result["ok"] is False
    assert result["code"] == "MLB_TEAM_EVENT_HYDRATION_NOT_READY"
    assert result["can_execute"] is False


def test_true_raw_canonical_field_absence_reports_precise_missing_fields():
    result = resolve_mlb_team_event_evidence(
        _req(),
        event_api=_Api([_row(feature_hydration_status="NOT_STARTED", venue_name=None)]),
    )
    assert result["ok"] is False
    assert result["code"] == "MLB_TEAM_EVENT_CANONICAL_SNAPSHOT_INCOMPLETE"
    assert result["missing_fields"] == ["venue_name"]
    assert result["can_execute"] is False


def test_missing_raw_snapshot_timestamp_is_diagnosed_before_model_invocation():
    result = resolve_mlb_team_event_evidence(
        _req(),
        event_api=_Api([_row(feature_hydration_status="NOT_STARTED", snapshot_timestamp=None)]),
    )
    assert result["ok"] is False
    assert result["code"] == "MLB_TEAM_EVENT_CANONICAL_SNAPSHOT_INCOMPLETE"
    assert result["missing_fields"] == ["snapshot_timestamp"]
    assert result["can_execute"] is False


def test_missing_canonical_snapshot_does_not_fall_back_to_caller_evidence():
    req = _req(
        sport_specific_evidence={
            "venue": "Angel Stadium",
            "home_starting_pitcher": "Reid Detmers",
            "away_starting_pitcher": "Cam Schlittler",
            "home_starter_status": "PROBABLE",
            "away_starter_status": "PROBABLE",
            "home_lineup_status": "PROJECTED",
            "away_lineup_status": "PROJECTED",
        }
    )
    result = resolve_mlb_team_event_evidence(req, event_api=_Api([]))
    assert result["ok"] is False
    assert result["code"] == "MLB_TEAM_EVENT_CANONICAL_SNAPSHOT_UNAVAILABLE"
