"""Adversarial no-network/no-write tests for exact official NBA start evidence."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from v17.nba_official_tipoff_preflight import (
    _verify_event,
    preflight,
    CAN_EXECUTE,
    SCOREBOARD_URL,
)

NOW = datetime(2026, 11, 12, 12, tzinfo=timezone.utc)


def stored(game="401909957", day="2026-11-10"):
    return {
        "game_id": "espn-" + game,
        "season": 2027,
        "game_date": day,
        "home_team_id": "espn-1",
        "away_team_id": "espn-2",
        "home_score": 111,
        "away_score": 100,
        "home_win": True,
        "source_provider": "SPORTSDATAVERSE_ESPN",
        "source_payload_sha256": "a" * 64,
        "scheduled_start_utc": None,
    }


def official(game="401909957"):
    return {
        "id": game,
        "date": "2026-11-11T00:00:00Z",  # Nov 10 Eastern
        "status": {"type": {"state": "post", "completed": True}},
        "competitions": [{
            "competitors": [
                {"homeAway": "home", "team": {"id": "1"}, "score": "111"},
                {"homeAway": "away", "team": {"id": "2"}, "score": "100"},
            ],
        }],
    }


class Response:
    status_code = 200
    def __init__(self, events):
        self.events = events
    def json(self):
        return {"events": self.events}


def feed(events, calls):
    def http_get(url, *, params, headers, timeout):
        assert timeout == 12
        assert url == SCOREBOARD_URL
        assert params["limit"] == 100
        calls.append(params["dates"])
        return Response(events)
    return http_get


def test_exact_official_id_teams_final_score_and_offset_pass_without_writes():
    assert CAN_EXECUTE is False
    row = stored()
    original = deepcopy(row)
    calls = []
    report = preflight([row], now=NOW, http_get=feed([official()], calls))
    assert report["balanced"] is True
    assert report["rows_in"] == report["rows_accounted"] == report["verified"] == 1
    assert report["db_writes"] == 0
    assert report["model_invoked"] is False
    assert report["probability_publishable"] is False
    assert report["can_execute"] is False
    assert calls == ["20261110"]
    receipt = report["receipts"][0]
    assert receipt["status"] == "NBA_OFFICIAL_TIPOFF_CORROBORATED"
    assert receipt["verified_scheduled_start_utc"] == "2026-11-11T00:00:00+00:00"
    assert receipt["source_observed_at"] == NOW.isoformat()
    assert len(receipt["source_payload_sha256"]) == 64
    assert row == original


@pytest.mark.parametrize("mutation,expected", [
    (lambda e: e.update(id="999"), "NBA_ESPN_EVENT_ID_MISMATCH"),
    (lambda e: e.update(date="2026-11-11T00:00:00"), "NBA_ESPN_EVENT_TIME_UNVERIFIED"),
    (lambda e: e.update(date="2026-11-12T00:00:00Z"), "NBA_ESPN_EVENT_CHRONOLOGY_CONFLICT"),
    (lambda e: e["status"]["type"].update(completed=False), "NBA_ESPN_SETTLEMENT_NOT_FINAL"),
    (lambda e: e["competitions"][0]["competitors"][0]["team"].update(id="3"), "NBA_ESPN_TEAMS_OR_SCORE_CONFLICT"),
    (lambda e: e["competitions"][0]["competitors"][1].update(score="99"), "NBA_ESPN_TEAMS_OR_SCORE_CONFLICT"),
    (lambda e: e["competitions"][0].update(competitors=[]), "NBA_ESPN_PARTICIPANTS_INVALID"),
])
def test_negative_source_variants_are_fail_closed(mutation, expected):
    event = official()
    mutation(event)
    result, verified_time = _verify_event(stored(), event, observed_at=NOW)
    assert result == expected
    assert verified_time is None


def test_no_source_event_and_duplicate_event_are_distinct():
    calls = []
    http = feed([], calls)
    a = preflight([stored()], now=NOW, http_get=http)
    assert a["receipts"][0]["status"] == "NBA_ESPN_EVENT_NOT_FOUND"
    b = preflight([stored()], now=NOW, http_get=feed([official(), official()], []))
    assert b["receipts"][0]["status"] == "NBA_ESPN_EVENT_AMBIGUOUS"
    assert a["verified"] == b["verified"] == 0


def test_failed_http_source_preserves_row_and_typed_blocker():
    def get(_url, *, params, headers, timeout):
        return type("Bad", (), {"status_code": 503})()
    report = preflight([stored()], now=NOW, http_get=get)
    assert report["verified"] == 0
    assert report["receipts"][0]["status"] == "NBA_ESPN_SOURCE_HTTP_503"
    assert report["rows_accounted"] == 1
    assert report["can_execute"] is False


def test_never_guess_timestamp_from_naive_or_mismatched_games():
    a = official()
    a["date"] = "2026-11-10T19:00:00"
    assert preflight([stored()], now=NOW, http_get=feed([a], []))["verified"] == 0
    b = stored()
    b["home_team_id"] = "espn-99"
    report = preflight([b], now=NOW, http_get=feed([official()], []))
    assert report["receipts"][0]["status"] == "NBA_ESPN_TEAMS_OR_SCORE_CONFLICT"
    assert report["receipts"][0]["verified_scheduled_start_utc"] is None


def test_all_rows_accounted_including_bounded_deferred_and_duplicates():
    rows = [stored(str(401909957 + n)) for n in range(5)]
    rows.append(deepcopy(rows[0]))
    calls = []
    report = preflight(rows, now=NOW, http_get=feed([official()], calls),
                       max_rows=2, max_days=1)
    assert report["rows_in"] == 6
    assert report["rows_accounted"] == 6
    assert report["balanced"] is True
    assert report["verified"] == 1
    assert report["deferred"] == 3
    assert report["source_days_attempted"] == 1
    assert report["receipts"][-1]["status"] == "NBA_PREFLIGHT_DUPLICATE_OR_MISSING_ID"
    assert calls == ["20261110"]
    assert all(r["can_execute"] is False for r in report["receipts"])


def test_day_budget_is_independently_bounded():
    rows = [stored("401909957", "2026-11-10"), stored("401909958", "2026-11-09")]
    calls = []
    report = preflight(rows, now=NOW, max_rows=2, max_days=1,
                       http_get=feed([official()], calls))
    assert report["deferred"] == 1
    assert report["receipts"][1]["status"] == "NBA_PREFLIGHT_DAY_BUDGET_DEFERRED"
    assert calls == ["20261110"]


def test_existing_timestamp_and_noneligible_provider_skip_source():
    rows = [stored(), stored("401909958")]
    rows[0]["scheduled_start_utc"] = "2026-11-11T00:00:00+00:00"
    rows[1]["source_provider"] = "OTHER"
    def fail(*_args, **_kwargs):
        raise AssertionError("UNNEEDED_EXTERNAL_FETCH")
    report = preflight(rows, now=NOW, http_get=fail)
    assert [r["status"] for r in report["receipts"]] == [
        "NBA_START_ALREADY_PRESENT", "NBA_PREFLIGHT_PROVIDER_NOT_IN_SCOPE",
    ]
    assert report["source_days_attempted"] == 0
    assert report["verified"] == 0


def test_stored_winner_conflict_blocks_even_with_matching_scores():
    bad = stored()
    bad["home_win"] = False
    result, t = _verify_event(bad, official(), observed_at=NOW)
    assert result == "NBA_ESPN_WINNER_CONFLICT" and t is None


def test_same_source_digest_is_deterministic_and_source_clock_distinct():
    reports = [
        preflight([stored()], now=NOW, http_get=feed([official()], []))
        for _ in range(2)
    ]
    a = reports[0]["receipts"][0]
    b = reports[1]["receipts"][0]
    assert a["source_payload_sha256"] == b["source_payload_sha256"]
    assert a["source_observed_at"] == b["source_observed_at"]
    assert a["source_payload_sha256"] != stored()["source_payload_sha256"]


def test_input_bounds_are_strict():
    for kwargs in ({"max_rows": 0}, {"max_rows": 51}, {"max_days": 0}, {"max_days": 8},
                   {"max_rows": True}):
        with pytest.raises(ValueError, match="NBA_PREFLIGHT_BUDGET_INVALID"):
            preflight([], now=NOW, **kwargs)
    with pytest.raises(ValueError, match="NBA_PREFLIGHT_CLOCK_NAIVE"):
        preflight([], now=datetime(2026, 11, 12))
