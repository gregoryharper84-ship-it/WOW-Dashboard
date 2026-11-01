"""WOW #1933: 2025 NFL regular-season independent ESPN schedule shadow audit.

One-off, bounded, public historical research. Never write data, stage training,
alter fitted models, modify market prices, or authorize wager execution.
The nflverse file is pinned upstream by the caller. ESPN is a separate
publisher used ONLY for final score / kickoff / team identity comparison.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any, Callable, Mapping
from urllib.parse import urlparse
from urllib.request import Request, build_opener
from urllib.error import HTTPError, URLError

from nflverse_historical_adapter import normalize_schedule_row
from v17.multi_source_historical_collector import CollectionBlocked
from v17.nfl_multi_source_pilot import _read_csv, REQUIRED_SCHEDULE
from v17.nfl_espn_independent_parity_1933 import _event_identity, _NoRedirects

CAN_EXECUTE = False
SEASON = 2025
WEEKS = tuple(range(1, 19))
MAX_PAYLOAD_BYTES = 5 * 1024 * 1024
EXPECTED_FINAL_GAMES = 272
ESPN_WEEK_URLS = {
    week: (
        "https://site.api.espn.com/apis/site/v2/sports/football/nfl/"
        f"scoreboard?dates=2025&seasontype=2&week={week}&limit=100"
    )
    for week in WEEKS
}


def fetch_espn_regular_week(week: int) -> bytes:
    """Reject arbitrary destinations, missing weeks, redirects and oversize files."""
    if type(week) is not int or week not in ESPN_WEEK_URLS:
        raise CollectionBlocked("NFL_ESPN_FULL_WEEK_NOT_ALLOWLISTED")
    url = ESPN_WEEK_URLS[week]
    parsed = urlparse(url)
    if (parsed.scheme != "https" or parsed.hostname != "site.api.espn.com"
            or parsed.path != "/apis/site/v2/sports/football/nfl/scoreboard"
            or parsed.query != f"dates=2025&seasontype=2&week={week}&limit=100"):
        raise CollectionBlocked("NFL_ESPN_FULL_ORIGIN_NOT_ALLOWED")
    try:
        with build_opener(_NoRedirects()).open(
            Request(url, headers={"Accept": "application/json",
                                  "User-Agent": "WOW-V17-offline-full-season-research/1.0"}),
            timeout=18,
        ) as response:
            result = response.read(MAX_PAYLOAD_BYTES + 1)
    except HTTPError as exc:
        raise CollectionBlocked("NFL_ESPN_FULL_HTTP_UNAVAILABLE", str(exc.code)) from exc
    except URLError as exc:
        raise CollectionBlocked("NFL_ESPN_FULL_NETWORK_UNAVAILABLE") from exc
    if not result or len(result) > MAX_PAYLOAD_BYTES:
        raise CollectionBlocked("NFL_ESPN_FULL_PAYLOAD_SIZE_INVALID")
    return result


def compare_2025_regular_season(
    *,
    schedule_csv: bytes,
    weekly_payloads: Mapping[int, bytes],
) -> dict[str, Any]:
    """Compute a typed result from exact byte inputs, never a governed pick.

    Caller must separately validate provenance/entitlements. JSON and fixture
    completeness are checked; no sportsbook/odds fields are used.
    """
    if set(weekly_payloads) != set(WEEKS):
        raise CollectionBlocked("NFL_ESPN_FULL_18_WEEK_PAYLOADS_REQUIRED")
    all_rows = _read_csv(schedule_csv, required=REQUIRED_SCHEDULE, label="schedules")
    fixtures: dict[int, list[tuple[dict[str, str], Any]]] = defaultdict(list)
    seen_game_ids = set()
    for row in all_rows:
        if str(row["season"]).strip() != str(SEASON) or row["game_type"] != "REG":
            continue
        try:
            fixture = normalize_schedule_row(row)
        except (TypeError, ValueError, KeyError) as exc:
            raise CollectionBlocked("NFL_ESPN_FULL_SCHEDULE_ROW_INVALID") from exc
        if row.get("home_score") in ("", None) or row.get("away_score") in ("", None):
            raise CollectionBlocked("NFL_ESPN_FULL_UNFINISHED_GAME_IN_SCOPE", fixture.game_id)
        if fixture.week not in WEEKS or fixture.game_id in seen_game_ids:
            raise CollectionBlocked("NFL_ESPN_FULL_BAD_WEEK_OR_DUPLICATE_GAME", fixture.game_id)
        seen_game_ids.add(fixture.game_id)
        fixtures[fixture.week].append((row, fixture))
    if len(seen_game_ids) != EXPECTED_FINAL_GAMES:
        raise CollectionBlocked("NFL_ESPN_FULL_SCHEDULE_EXPECTED_272", str(len(seen_game_ids)))

    weeks = []
    crosswalk = []
    problems = []
    seen_espn_ids: set[str] = set()
    for week in WEEKS:
        blob = weekly_payloads[week]
        if not isinstance(blob, bytes) or not blob or len(blob) > MAX_PAYLOAD_BYTES:
            raise CollectionBlocked("NFL_ESPN_FULL_WEEK_BYTES_INVALID", str(week))
        try:
            body = json.loads(blob)
        except (ValueError, UnicodeError) as exc:
            raise CollectionBlocked("NFL_ESPN_FULL_WEEK_JSON_INVALID", str(week)) from exc
        if not isinstance(body, dict) or not isinstance(body.get("events"), list):
            raise CollectionBlocked("NFL_ESPN_FULL_WEEK_SCHEMA_INVALID", str(week))
        events = [_event_identity(e, body) for e in body["events"]]
        current_ids = [item["event_id"] for item in events]
        if len(set(current_ids)) != len(current_ids):
            raise CollectionBlocked("NFL_ESPN_FULL_DUPLICATE_EVENT_IN_WEEK", str(week))
        if seen_espn_ids.intersection(current_ids):
            raise CollectionBlocked("NFL_ESPN_FULL_EVENT_REUSED_ACROSS_WEEKS", str(week))
        seen_espn_ids.update(current_ids)
        matched = missing = conflict = ambiguous = 0
        for row, game in fixtures[week]:
            options = [event for event in events
                       if event["season"] == SEASON and event["week"] == game.week
                       and event["home_team"] == game.home_team
                       and event["away_team"] == game.away_team
                       and abs((event["kickoff"] - game.event_start_time).total_seconds()) <= 90 * 60]
            if len(options) > 1:
                ambiguous += 1
                problems.append({"game_id": game.game_id, "week": week,
                                 "issue": "AMBIGUOUS_EVENT"})
                continue
            if not options or not options[0]["completed"]:
                missing += 1
                problems.append({"game_id": game.game_id, "week": week,
                                 "issue": "MISSING_OR_UNFINALIZED"})
                continue
            event = options[0]
            try:
                h, a = int(row["home_score"]), int(row["away_score"])
            except (ValueError, TypeError, KeyError) as exc:
                raise CollectionBlocked("NFL_ESPN_FULL_SCHEDULE_FINAL_SCORE_INVALID", game.game_id) from exc
            if h != event["home_score"] or a != event["away_score"]:
                conflict += 1
                problems.append({"game_id": game.game_id, "week": week,
                                 "issue": "FINAL_SCORE_CONFLICT"})
                continue
            matched += 1
            crosswalk.append({"nflverse_game_id": game.game_id,
                              "espn_event_id": event["event_id"], "week": week})
        weeks.append({
            "week": week, "source_url": ESPN_WEEK_URLS[week],
            "source_sha256": hashlib.sha256(blob).hexdigest(),
            "schedule_games": len(fixtures[week]), "espn_events": len(events),
            "matched": matched, "missing_or_unfinalized": missing,
            "score_conflicts": conflict, "ambiguous": ambiguous,
        })
    matched_total = sum(x["matched"] for x in weeks)
    status = ("MATCHED" if matched_total == EXPECTED_FINAL_GAMES
              and not problems and len(crosswalk) == EXPECTED_FINAL_GAMES
              else "PARTIAL_HOLD")
    return {
        "schema": "WOW_NFL_2025_ESPN_FULL_REGULAR_SHADOW_V1",
        "status": status, "season": SEASON, "scope": "2025_18_REGULAR_SEASON_WEEKS",
        "source_provider": "ESPN_NFL_SCOREBOARD_INDEPENDENT",
        "source_rights_state": "UNREVIEWED_FOR_STAGING_OR_TRAINING",
        "schedule_games": len(seen_game_ids),
        "matched_games": matched_total, "unresolved_games": len(problems),
        "weeks": weeks, "problems": problems,
        "crosswalk": crosswalk,
        "independent_full_regular_season_results_checked": status == "MATCHED",
        "not_pregame_features": True,
        "training_ready": False, "storage_write_authorized": False,
        "model_certified": False, "probability_publishable": False,
        "attestation_verified": False, "can_execute": False,
    }


def fetch_and_compare_2025(
    *, schedule_csv: bytes,
    fetch_week: Callable[[int], bytes] = fetch_espn_regular_week,
) -> dict[str, Any]:
    """18 bounded research-only requests, sequential, no retries or bypass."""
    return compare_2025_regular_season(
        schedule_csv=schedule_csv,
        weekly_payloads={week: fetch_week(week) for week in WEEKS},
    )
