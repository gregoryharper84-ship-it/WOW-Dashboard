"""Regressions for pregame NFL evidence boundaries, using only HTTP fixtures."""
from __future__ import annotations

import csv
import copy
import hashlib
import io
import json
from datetime import datetime, timedelta, timezone

import pytest

import nfl_prop_auto_hydration as nfl


class _Response:
    def __init__(self, payload=None, *, content=b"", status=200):
        self.status_code = status
        self._payload = payload
        self.content = content
        self.text = content.decode("utf-8")

    def json(self):
        if self._payload is None:
            raise AssertionError("CSV must be parsed as source bytes")
        return self._payload


def _event(season, week, date, *, season_type=2, event_id=None, completed=True, opponent="BUF"):
    return {
        "id": event_id or f"{season}-{season_type}-{week}",
        "date": date,
        "season": {"year": season, "type": season_type},
        "seasonType": {"id": str(season_type), "type": season_type},
        "week": {"number": week},
        "status": {"type": {"state": "post" if completed else "pre", "completed": completed}},
        "competitions": [{
            "competitors": [
                {"homeAway": "home", "team": {"abbreviation": "KC"}},
                {"homeAway": "away", "team": {"abbreviation": opponent}},
            ],
            "status": {"type": {"state": "post" if completed else "pre", "completed": completed}},
        }],
    }


def _row(season, week, *, value=None, season_type="REG", opponent="BUF", game_id=None):
    return {
        "player_display_name": "Test Player", "season": str(season), "week": str(week),
        "season_type": season_type, "game_id": game_id or f"{season}_{week:02d}_{opponent}_KC",
        "team": "KC", "opponent_team": opponent, "position": "WR",
        "receiving_yards": str(week if value is None else value), "targets": "5",
    }


def _regular(season, weeks, *, value_offset=0):
    rows, events = [], []
    for week in weeks:
        date = datetime(season, 9, 1, 17, tzinfo=timezone.utc) + timedelta(weeks=week - 1)
        rows.append(_row(season, week, value=value_offset + week))
        events.append(_event(season, week, date.isoformat()))
    return rows, events


class _Sources:
    """Exact URL/parameter fixture: unexpected acquisition paths fail loudly."""
    def __init__(self, target, cohorts, schedules, *, statuses=None):
        self.target = target
        self.cohorts = cohorts
        self.schedules = schedules
        self.statuses = statuses or {}
        self.requests = []
        self.csv_bytes = {}
        for season, rows in cohorts.items():
            out = io.StringIO()
            writer = csv.DictWriter(out, fieldnames=list(rows[0]) if rows else list(_row(season, 1)))
            writer.writeheader()
            writer.writerows(rows)
            self.csv_bytes[season] = out.getvalue().encode()

    def __call__(self, url, *, params, headers, timeout, follow_redirects):
        del headers, timeout, follow_redirects
        self.requests.append((url, dict(params)))
        if url == nfl.ESPN_SEARCH_URL:
            return _Response({"results": [{"type": "player", "contents": [{
                "displayName": "Test Player", "description": "NFL", "uid": "s:20~l:28~a:123",
            }]}]})
        if url == f"{nfl.ESPN_CORE_BASE}/athletes/123":
            return _Response({"team": {"$ref": f"{nfl.ESPN_CORE_BASE}/teams/12"}})
        if url == f"{nfl.ESPN_CORE_BASE}/teams/12":
            return _Response({"abbreviation": "KC"})
        if url == nfl.ESPN_SCOREBOARD_URL:
            assert len(str(params["dates"])) == 8
            return _Response({"events": [self.target]})
        if "stats_player_week_" in url:
            season = int(url.rsplit("_", 1)[1].removesuffix(".csv"))
            if season in self.statuses:
                return _Response(status=self.statuses[season])
            return _Response(content=self.csv_bytes[season])
        if url.endswith("/teams/KC/schedule"):
            key = (int(params["season"]), int(params["seasontype"]))
            if key in self.statuses:
                return _Response(status=self.statuses[key])
            # Root season can be current even when the per-event historical season
            # is correct in ESPN's actual schedule response.
            return _Response({"season": {"year": 2099}, "events": self.schedules.get(key, [])})
        raise AssertionError(f"unexpected source URL: {url}")


def _hydrate(source, target_time, *, capture=None):
    return nfl.hydrate_nfl_prop_evidence(
        player="Test Player", stat_type="RECEIVING_YARDS", event_start_time=target_time,
        now=capture or datetime.fromisoformat(target_time.replace("Z", "+00:00")) - timedelta(days=1),
        http_get=source,
    )


def test_history_excludes_target_and_future_games_using_official_dates():
    rows26, events26 = _regular(2026, range(1, 19))
    rows25, events25 = _regular(2025, range(1, 19), value_offset=100)
    source = _Sources(
        _event(2026, 3, "2026-09-15T17:00:00Z", completed=False),
        {2026: rows26, 2025: rows25}, {(2026, 2): events26, (2025, 2): events25},
    )

    result = _hydrate(source, "2026-09-15T17:00:00Z")

    assert result["game_log"] == [111.0, 112.0, 113.0, 114.0, 115.0, 116.0, 117.0, 118.0, 1.0, 2.0]
    assert result["box_score_log"][-1]["date"] == "2026-09-08T17:00:00+00:00"
    assert all(datetime.fromisoformat(row["date"]) < datetime(2026, 9, 15, 17, tzinfo=timezone.utc)
               for row in result["box_score_log"])


@pytest.mark.parametrize("season_type,provider_week", [(2, 18), (3, 1)])
def test_january_history_uses_verified_provider_season(season_type, provider_week):
    rows, events = _regular(2026, range(1, 19))
    source = _Sources(
        _event(2026, provider_week, "2027-01-10T17:00:00Z", season_type=season_type, completed=False),
        {2026: rows}, {(2026, 2): events}, statuses={2027: 404},
    )

    result = _hydrate(source, "2027-01-10T17:00:00Z")

    assert result["game_log"] == [9.0, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0]
    assert result["role_status"]["provider_season"] == 2026
    assert not any("stats_player_week_2027.csv" in url for url, _params in source.requests)


def test_postseason_preserves_both_week_namespaces_without_inventing_dates():
    rows, events = _regular(2026, range(1, 19))
    rows += [_row(2026, 19, season_type="POST", opponent="IND"),
             _row(2026, 20, season_type="POST", opponent="DEN"),
             _row(2026, 21, season_type="POST", opponent="BAL")]
    playoffs = [
        _event(2026, 1, "2027-01-10T17:00:00Z", season_type=3, opponent="IND"),
        _event(2026, 2, "2027-01-17T17:00:00Z", season_type=3, opponent="DEN"),
        _event(2026, 3, "2027-01-24T17:00:00Z", season_type=3, opponent="BAL"),
    ]
    source = _Sources(
        _event(2026, 2, "2027-01-17T17:00:00Z", season_type=3, opponent="DEN", completed=False),
        {2026: rows}, {(2026, 2): events, (2026, 3): playoffs}, statuses={2027: 404},
    )

    result = _hydrate(source, "2027-01-17T17:00:00Z")

    assert result["game_log"] == [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0]
    prior = result["box_score_log"][-1]
    assert prior["date"] == "2027-01-10T17:00:00+00:00"
    assert prior["week"] == 19
    assert prior["season_type"] == "POST"
    assert prior["provider_week"] == 1
    assert prior["provider_season_type"] == 3
    assert prior["provider_event_id"] == "2026-3-1"


@pytest.mark.parametrize("corruption", ["missing_date", "missing_id", "missing_season_type", "ambiguous", "wrong_team"])
def test_unverifiable_history_is_a_typed_block(corruption):
    rows, events = _regular(2026, range(1, 11))
    if corruption == "missing_date":
        events[-1].pop("date")
    elif corruption == "missing_id":
        events[-1].pop("id")
    elif corruption == "missing_season_type":
        rows[-1].pop("season_type")
    elif corruption == "wrong_team":
        events[-1]["competitions"][0]["competitors"][0]["team"]["abbreviation"] = "DAL"
    else:
        duplicate = dict(events[-1], id="different-official-id")
        events.append(duplicate)
    source = _Sources(_event(2026, 11, "2026-11-15T17:00:00Z", completed=False),
                      {2026: rows}, {(2026, 2): events})

    with pytest.raises(nfl.NFLPropHydrationError) as caught:
        _hydrate(source, "2026-11-15T17:00:00Z")

    assert caught.value.code == "NFL_PROP_HISTORY_CHRONOLOGY_UNVERIFIED"


def test_only_prior_completed_games_enter_the_recent_sample():
    rows, events = _regular(2026, range(1, 13))
    events[-2]["status"]["type"] = {"state": "in", "completed": False}
    events[-2]["competitions"][0]["status"]["type"] = {"state": "in", "completed": False}
    source = _Sources(_event(2026, 13, "2026-11-15T17:00:00Z", completed=False),
                      {2026: rows}, {(2026, 2): events})

    result = _hydrate(source, "2026-11-15T17:00:00Z")

    assert result["game_log"] == [float(n) for n in range(1, 11)]


def test_absent_current_season_can_use_verified_previous_season():
    rows, events = _regular(2025, range(1, 19))
    source = _Sources(_event(2026, 1, "2026-09-01T17:00:00Z", completed=False),
                      {2025: rows}, {(2025, 2): events}, statuses={2026: 404})

    result = _hydrate(source, "2026-09-01T17:00:00Z")

    assert result["game_log"] == [9.0, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0]
    assert {row["season"] for row in result["box_score_log"]} == {2025}


def test_missing_middle_season_fails_closed_instead_of_skipping_gap():
    rows26, events26 = _regular(2026, range(1, 3))
    rows24, events24 = _regular(2024, range(1, 19))
    source = _Sources(_event(2026, 3, "2026-09-15T17:00:00Z", completed=False),
                      {2026: rows26, 2024: rows24},
                      {(2026, 2): events26, (2024, 2): events24}, statuses={2025: 404})

    with pytest.raises(nfl.NFLPropHydrationError) as caught:
        _hydrate(source, "2026-09-15T17:00:00Z")

    assert caught.value.code == "NFL_PROP_HISTORY_CHRONOLOGY_UNVERIFIED"
    assert caught.value.detail["missing_seasons"] == [2025]


def test_missing_old_history_season_is_not_silent_when_no_older_csv_loads():
    rows26, events26 = _regular(2026, range(1, 3))
    source = _Sources(
        _event(2026, 3, "2026-09-15T17:00:00Z", completed=False),
        {2026: rows26}, {(2026, 2): events26},
        statuses={2025: 404, 2024: 404},
    )

    with pytest.raises(nfl.NFLPropHydrationError) as caught:
        _hydrate(source, "2026-09-15T17:00:00Z")

    assert caught.value.code == "NFL_PROP_HISTORY_CHRONOLOGY_UNVERIFIED"
    assert caught.value.detail["missing_seasons"] == [2025]
    assert any("stats_player_week_2025.csv" in url for url, _ in source.requests)
    assert not any("stats_player_week_2024.csv" in url for url, _ in source.requests)


@pytest.mark.parametrize("status", [401, 429, 500])
def test_stats_source_errors_cannot_be_replaced_by_previous_season(status):
    rows, events = _regular(2025, range(1, 19))
    source = _Sources(_event(2026, 1, "2026-09-01T17:00:00Z", completed=False),
                      {2025: rows}, {(2025, 2): events}, statuses={2026: status})

    with pytest.raises(nfl.NFLPropHydrationError) as caught:
        _hydrate(source, "2026-09-01T17:00:00Z")

    assert caught.value.code == "NFL_PROP_SOURCE_UNAVAILABLE"
    assert f"HTTP_{status}" in str(caught.value.detail["errors"])
    assert not any("stats_player_week_2025.csv" in url for url, _params in source.requests)


@pytest.mark.parametrize("status", [404, 401, 429])
def test_required_schedule_errors_preserve_source_failure(status):
    rows, events = _regular(2026, range(1, 11))
    source = _Sources(_event(2026, 11, "2026-11-15T17:00:00Z", completed=False),
                      {2026: rows}, {(2026, 2): events}, statuses={(2026, 2): status})

    with pytest.raises(nfl.NFLPropHydrationError) as caught:
        _hydrate(source, "2026-11-15T17:00:00Z")

    assert caught.value.code == "NFL_PROP_SOURCE_UNAVAILABLE"
    assert f"HTTP_{status}" in str(caught.value.detail["errors"])


def test_history_retains_source_hashes_with_official_game_identity():
    rows, events = _regular(2026, range(1, 11))
    source = _Sources(_event(2026, 11, "2026-11-15T17:00:00Z", completed=False),
                      {2026: rows}, {(2026, 2): events})

    result = _hydrate(source, "2026-11-15T17:00:00Z")

    stats_hash = hashlib.sha256(source.csv_bytes[2026]).hexdigest()
    schedule_payload = {"season": {"year": 2099}, "events": events}
    schedule_hash = hashlib.sha256(json.dumps(schedule_payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    row = result["box_score_log"][-1]
    assert row["stats_source_sha256"] == stats_hash
    assert row["schedule_source_sha256"] == schedule_hash
    assert row["provider_event_id"] == "2026-2-10"
    assert row["schedule_source_url"] == "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/KC/schedule"
    assert f"2026:{stats_hash}" in result["rate_provenance"]
    assert f"2026:KC:REG:{schedule_hash}" in result["rate_provenance"]
    assert "ESPN_NFL_TEAM_SCHEDULE" in result["source_timestamps"]


def test_cutoff_uses_official_target_kickoff_within_request_time_tolerance():
    rows, events = _regular(2026, range(1, 13))
    events[-2]["date"] = "2026-11-15T17:00:00Z"
    events[-1]["date"] = "2026-11-15T17:30:00Z"
    source = _Sources(_event(2026, 11, "2026-11-15T17:00:00Z", completed=False),
                      {2026: rows}, {(2026, 2): events})

    result = _hydrate(source, "2026-11-15T18:00:00Z")

    assert result["game_log"] == [float(n) for n in range(1, 11)]
    assert result["role_status"]["provider_event_start_time"] == "2026-11-15T17:00:00+00:00"


def test_duplicate_stats_rows_cannot_count_as_additional_prior_games():
    rows, events = _regular(2026, range(1, 11))
    rows.append(dict(rows[-1]))
    source = _Sources(_event(2026, 11, "2026-11-15T17:00:00Z", completed=False),
                      {2026: rows}, {(2026, 2): events})

    with pytest.raises(nfl.NFLPropHydrationError) as caught:
        _hydrate(source, "2026-11-15T17:00:00Z")

    assert caught.value.code == "NFL_PROP_HISTORY_CHRONOLOGY_UNVERIFIED"


def test_malformed_official_season_type_is_a_typed_block():
    rows, events = _regular(2026, range(1, 11))
    events[-1]["seasonType"] = [3]
    events[-1]["season"].pop("type")
    source = _Sources(_event(2026, 11, "2026-11-15T17:00:00Z", completed=False),
                      {2026: rows}, {(2026, 2): events})

    with pytest.raises(nfl.NFLPropHydrationError) as caught:
        _hydrate(source, "2026-11-15T17:00:00Z")

    assert caught.value.code == "NFL_PROP_HISTORY_CHRONOLOGY_UNVERIFIED"


def test_capture_after_official_kickoff_cannot_use_request_time_tolerance():
    rows, events = _regular(2026, range(1, 11))
    source = _Sources(_event(2026, 11, "2026-11-15T17:00:00Z", completed=False),
                      {2026: rows}, {(2026, 2): events})

    with pytest.raises(nfl.NFLPropHydrationError) as caught:
        _hydrate(source, "2026-11-15T18:00:00Z", capture=datetime(2026, 11, 15, 17, 30, tzinfo=timezone.utc))

    assert caught.value.code == "EVENT_ALREADY_STARTED"


def test_live_schedule_cache_drops_unused_payload_columns_and_singleflights(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import time

    rows, events = _regular(2026, range(1, 11))
    events[0]["unused_blob"] = "x" * 32768
    events[0]["competitions"][0]["competitors"][0]["team"]["unused_logo_blob"] = "x" * 32768
    source = _Sources(_event(2026, 11, "2026-11-15T17:00:00Z", completed=False),
                      {2026: rows}, {(2026, 2): events})

    def fake_get(*args, **kwargs):
        time.sleep(0.02)
        return source(*args, **kwargs)

    monkeypatch.setattr(nfl.httpx, "get", fake_get)
    monkeypatch.setattr(nfl, "_SCHEDULE_CACHE", {})
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: nfl._cached_team_schedule(
            2026, "KC", "REG", http_get=fake_get, now_ts=1000.0,
        ), range(4)))

    assert len(source.requests) == 1
    assert len(json.dumps(results[0])) < 15000
    assert all(result == results[0] for result in results)


@pytest.mark.parametrize("field,value", [("week", 10.5), ("season", 2026.5), ("week", True)])
def test_nonintegral_official_identity_cannot_be_coerced_into_a_history_match(field, value):
    rows, events = _regular(2026, range(1, 11))
    events[-1][field]["number" if field == "week" else "year"] = value
    source = _Sources(_event(2026, 11, "2026-11-15T17:00:00Z", completed=False),
                      {2026: rows}, {(2026, 2): events})

    with pytest.raises(nfl.NFLPropHydrationError) as caught:
        _hydrate(source, "2026-11-15T17:00:00Z")

    assert caught.value.code == "NFL_PROP_HISTORY_CHRONOLOGY_UNVERIFIED"


def test_official_source_hashes_survive_existing_immutable_snapshot_boundary():
    from pick_request_runtime_core import PickRequestRow, _snapshot_payload

    rows, events = _regular(2026, range(1, 11))
    source = _Sources(_event(2026, 11, "2026-11-15T17:00:00Z", completed=False),
                      {2026: rows}, {(2026, 2): events})
    evidence = _hydrate(source, "2026-11-15T17:00:00Z")
    evidence.pop("hydration_provider")
    row = PickRequestRow(
        event_id="2026_11_BUF_KC", event_start_time="2026-11-15T17:00:00Z", sport="NFL",
        player="Test Player", stat_type="RECEIVING_YARDS", line=50.5, direction="MORE", evidence=evidence,
    )
    normalized = {"event_id": row.event_id, "event_start_time": row.event_start_time, "sport": row.sport,
                  "player": row.player, "stat_type": row.stat_type,
                  "captured_at": evidence["captured_at"], "role_timestamp": evidence["role_timestamp"],
                  "source_timestamps": evidence["source_timestamps"]}

    snapshot_id, fingerprint, persisted = _snapshot_payload(row, normalized)
    altered = row.model_copy(deep=True)
    altered.evidence.box_score_log[-1]["schedule_source_sha256"] = "f" * 64
    changed_id, changed_fingerprint, _ = _snapshot_payload(altered, normalized)

    assert persisted["box_score_log"][-1]["schedule_source_sha256"] == evidence["box_score_log"][-1]["schedule_source_sha256"]
    assert persisted["box_score_log"][-1]["stats_source_sha256"] == hashlib.sha256(source.csv_bytes[2026]).hexdigest()
    assert snapshot_id != changed_id
    assert fingerprint != changed_fingerprint
    assert persisted["can_execute"] is False


@pytest.mark.parametrize("official_type", [2.5, 3.5, True, False])
def test_fractional_or_boolean_official_season_type_is_a_typed_block(official_type):
    rows, events = _regular(2026, range(1, 11))
    schedules = {(2026, 2): events}
    if official_type == 3.5:
        rows[-1] = _row(2026, 19, season_type="POST", opponent="IND")
        schedules[(2026, 3)] = [_event(2026, 1, "2027-01-10T17:00:00Z", season_type=official_type, opponent="IND")]
    else:
        events[-1]["season"]["type"] = official_type
        events[-1]["seasonType"]["type"] = official_type
    source = _Sources(_event(2026, 2, "2027-01-17T17:00:00Z", season_type=3, completed=False),
                      {2026: rows}, schedules)

    with pytest.raises(nfl.NFLPropHydrationError) as caught:
        _hydrate(source, "2027-01-17T17:00:00Z")

    assert caught.value.code == "NFL_PROP_HISTORY_CHRONOLOGY_UNVERIFIED"


@pytest.mark.parametrize("conflict", ["week", "season", "season_type", "team", "date", "unrelated_event"])
def test_official_alias_conflicts_block_before_row_matching_filters(conflict):
    rows, events = _regular(2026, range(1, 11))
    if conflict == "unrelated_event":
        other = _event(2026, 12, "2026-12-01T17:00:00Z", event_id="unrelated-official-id")
        other["competitions"][0]["competitors"][0]["team"]["abbreviation"] = "DAL"
        events.append(other)
    alias = copy.deepcopy(events[-1])
    if conflict in {"week", "unrelated_event"}:
        alias["week"]["number"] += 1
    elif conflict == "season":
        alias["season"]["year"] = 2025
    elif conflict == "season_type":
        alias["season"]["type"] = 3
        alias["seasonType"]["type"] = 3
    elif conflict == "team":
        alias["competitions"][0]["competitors"][0]["team"]["abbreviation"] = "DAL"
    alias["date"] = "2026-12-08T17:00:00Z"
    events.append(alias)
    source = _Sources(_event(2026, 11, "2026-11-15T17:00:00Z", completed=False),
                      {2026: rows}, {(2026, 2): events})

    with pytest.raises(nfl.NFLPropHydrationError) as caught:
        _hydrate(source, "2026-11-15T17:00:00Z")

    assert caught.value.code == "NFL_PROP_HISTORY_CHRONOLOGY_UNVERIFIED"


@pytest.mark.parametrize("event_state,event_completed,competition_state,competition_completed", [
    ("post", True, "in", False), ("in", False, "post", True), ("post", True, "pre", False),
])
def test_conflicting_event_and_competition_completion_is_a_typed_block(
    event_state, event_completed, competition_state, competition_completed,
):
    rows, events = _regular(2026, range(1, 11))
    events[-1]["status"]["type"] = {"state": event_state, "completed": event_completed}
    events[-1]["competitions"][0]["status"]["type"] = {"state": competition_state, "completed": competition_completed}
    source = _Sources(_event(2026, 11, "2026-11-15T17:00:00Z", completed=False),
                      {2026: rows}, {(2026, 2): events})

    with pytest.raises(nfl.NFLPropHydrationError) as caught:
        _hydrate(source, "2026-11-15T17:00:00Z")

    assert caught.value.code == "NFL_PROP_HISTORY_CHRONOLOGY_UNVERIFIED"


def test_completion_disagreement_in_an_additional_competition_is_a_typed_block():
    rows, events = _regular(2026, range(1, 11))
    other = copy.deepcopy(events[-1]["competitions"][0])
    other["status"]["type"] = {"state": "in", "completed": False}
    events[-1]["competitions"].append(other)
    source = _Sources(_event(2026, 11, "2026-11-15T17:00:00Z", completed=False),
                      {2026: rows}, {(2026, 2): events})

    with pytest.raises(nfl.NFLPropHydrationError) as caught:
        _hydrate(source, "2026-11-15T17:00:00Z")

    assert caught.value.code == "NFL_PROP_HISTORY_CHRONOLOGY_UNVERIFIED"
