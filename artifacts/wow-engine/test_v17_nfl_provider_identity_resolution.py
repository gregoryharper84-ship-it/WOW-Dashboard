from types import SimpleNamespace

import v17.nfl_team_event_specialist as specialist


SNAPSHOT = {
    "snapshot_id": "00000000-0000-0000-0000-000000000111",
    "fetched_at": "2026-09-14T01:10:00+00:00",
    "content_sha256": "schedule-sha",
}


def _req(**updates):
    values = {
        "official_event_id": "5ad8135dc2b5f27de0b777acd317855a",
        "requested_slate_date": "2026-09-14",
        "requested_timezone": "America/Chicago",
        "event_start_time_utc": "2026-09-15T00:15:00Z",
        "home_team": "Kansas City Chiefs",
        "away_team": "Denver Broncos",
        "settlement_basis": "FULL_GAME_OUTRIGHT",
    }
    values.update(updates)
    return SimpleNamespace(**values)


def _schedule():
    return [
        {
            "game_id": "2026_01_DEN_KC",
            "season": "2026",
            "week": "1",
            "gameday": "2026-09-14",
            "home_team": "KC",
            "away_team": "DEN",
            "home_score": "",
            "away_score": "",
            "stadium": "Arrowhead Stadium",
        },
        {
            "game_id": "2025_18_KC_DEN",
            "season": "2025",
            "week": "18",
            "gameday": "2026-01-04",
            "home_team": "DEN",
            "away_team": "KC",
            "home_score": "17",
            "away_score": "14",
            "stadium": "Empower Field",
        },
    ]


def test_provider_hash_resolves_to_canonical_nflverse_event(monkeypatch):
    monkeypatch.setattr(specialist, "_load_latest_schedule_snapshot", lambda db: (SNAPSHOT, _schedule()))
    result = specialist.resolve_nfl_team_event_evidence(_req(), db=object())

    assert result["ok"] is True
    assert result["canonical_event_id"] == "2026_01_DEN_KC"
    assert result["provider_event_id"] == "5ad8135dc2b5f27de0b777acd317855a"
    assert result["canonical_home_team"] == "KC"
    assert result["canonical_away_team"] == "DEN"
    assert result["identity_resolution"] == "PROVIDER_ID_TO_CANONICAL_SCHEDULE_MATCH"
    assert result["evidence"]["canonical_game_id"] == "2026_01_DEN_KC"
    assert result["evidence"]["provider_event_id"] == "5ad8135dc2b5f27de0b777acd317855a"


def test_exact_canonical_id_still_wins_with_full_team_names(monkeypatch):
    monkeypatch.setattr(specialist, "_load_latest_schedule_snapshot", lambda db: (SNAPSHOT, _schedule()))
    result = specialist.resolve_nfl_team_event_evidence(
        _req(official_event_id="2026_01_DEN_KC"),
        db=object(),
    )

    assert result["ok"] is True
    assert result["canonical_event_id"] == "2026_01_DEN_KC"
    assert result["identity_resolution"] == "EXACT_CANONICAL_EVENT_ID"


def test_provider_match_fails_closed_when_canonical_match_is_ambiguous(monkeypatch):
    duplicate = dict(_schedule()[0])
    duplicate["game_id"] = "2026_01_DUP_DEN_KC"
    monkeypatch.setattr(
        specialist,
        "_load_latest_schedule_snapshot",
        lambda db: (SNAPSHOT, [*_schedule(), duplicate]),
    )
    result = specialist.resolve_nfl_team_event_evidence(_req(), db=object())

    assert result["ok"] is False
    assert result["failure_code"] == "MODEL_INPUTS_INSUFFICIENT"
    assert result["code"] == "NFL_PROVIDER_EVENT_ID_CANONICAL_MATCH_AMBIGUOUS"


def test_provider_match_fails_closed_on_team_conflict(monkeypatch):
    monkeypatch.setattr(specialist, "_load_latest_schedule_snapshot", lambda db: (SNAPSHOT, _schedule()))
    result = specialist.resolve_nfl_team_event_evidence(
        _req(home_team="Las Vegas Raiders"),
        db=object(),
    )

    assert result["ok"] is False
    assert result["failure_code"] == "MODEL_INPUTS_INSUFFICIENT"
    assert result["code"] == "NFL_PROVIDER_EVENT_ID_CANONICAL_MATCH_NOT_FOUND"


def test_event_start_must_land_on_requested_local_slate_date(monkeypatch):
    monkeypatch.setattr(specialist, "_load_latest_schedule_snapshot", lambda db: (SNAPSHOT, _schedule()))
    result = specialist.resolve_nfl_team_event_evidence(
        _req(event_start_time_utc="2026-09-16T00:15:00Z"),
        db=object(),
    )

    assert result["ok"] is False
    assert result["failure_code"] == "MODEL_INPUTS_INSUFFICIENT"
    assert result["code"] == "NFL_EVENT_START_SLATE_DATE_MISMATCH"
