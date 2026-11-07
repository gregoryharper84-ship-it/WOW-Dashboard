"""Hydrate real NBA/WNBA settled team games into V17 research stores.

Primary source: BallDontLie when its credential/path is available.
First fallback: Sportsdataverse's open-licensed ESPN schedule releases.
Second fallback: ESPN public scoreboard, used only for settled games newer than
the currently persisted corpus. The monotonic-date fallback rule prevents a
provider switch from duplicating older BallDontLie rows under different IDs.

Research/training only. No scoring, publication, recommendation, wager, or
execution. can_execute=False unconditional.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo
import hashlib
import io
import json
import os
from typing import Any, Callable, Iterable

import requests

from v17.model_source_entitlements import source_readiness

can_execute: bool = False

BASE_URLS = {
    "NBA": "https://api.balldontlie.io/v1/games",
    "WNBA": "https://api.balldontlie.io/wnba/v1/games",
}
ESPN_BASE_URLS = {
    "NBA": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
    "WNBA": "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard",
}
SPORTSDATAVERSE_RELEASE_URLS = {
    "NBA": "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_nba_schedules/nba_schedule_{year}.csv",
    "WNBA": "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_wnba_schedules/wnba_schedule_{year}.csv",
}
TABLES = {
    "NBA": "wow_nba_training_games",
    "WNBA": "wow_wnba_training_games",
}
SPORTSDATAVERSE_REQUIRED_COLUMNS = {
    "game_id",
    "game_date",
    "game_date_time",
    "home_id",
    "away_id",
    "home_score",
    "away_score",
    "status_type_completed",
}


class BasketballHydrationError(RuntimeError):
    pass


class BasketballHydrationPartialWriteError(BasketballHydrationError):
    """A write may have persisted part of the corpus; never replay it this cycle."""


def _key() -> str:
    return os.getenv("balldontlie") or os.getenv("BALLDONTLIE_API_KEY") or ""


def _client():
    from supabase import create_client
    url = os.environ["SUPABASE_URL"]
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SERVICE_KEY")
    if not key:
        raise BasketballHydrationError("SUPABASE service credential unavailable")
    return create_client(url, key)


def fetch_games(sport: str, season: int, *, max_pages: int = 100) -> list[dict[str, Any]]:
    """Primary BallDontLie acquisition."""
    sport = sport.upper().strip()
    if sport not in BASE_URLS:
        raise BasketballHydrationError(f"unsupported sport {sport}")
    key = _key()
    if not key:
        raise BasketballHydrationError("BALLDONTLIE_API_KEY unavailable")
    url = BASE_URLS[sport]
    params: dict[str, Any] = {"seasons[]": season, "per_page": 100}
    rows: list[dict[str, Any]] = []
    for _ in range(max_pages):
        resp = requests.get(url, headers={"Authorization": key}, params=params, timeout=20)
        if resp.status_code == 429:
            raise BasketballHydrationError("BALLDONTLIE_RATE_LIMITED")
        if resp.status_code in (401, 403):
            raise BasketballHydrationError("BALLDONTLIE_AUTH_FAILED")
        if resp.status_code != 200:
            raise BasketballHydrationError(f"BALLDONTLIE_HTTP_{resp.status_code}")
        try:
            body = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise BasketballHydrationError("BALLDONTLIE_INVALID_JSON") from exc
        data = body.get("data") or []
        if not isinstance(data, list):
            raise BasketballHydrationError("BALLDONTLIE_INVALID_RESPONSE")
        rows.extend(data)
        cursor = (body.get("meta") or {}).get("next_cursor")
        if not cursor:
            break
        params = {**params, "cursor": cursor}
    return rows


def fetch_sportsdataverse_games(
    sport: str,
    season: int,
    *,
    session: Any = requests,
    timeout: int = 60,
) -> list[dict[str, Any]]:
    """Open-licensed Sportsdataverse schedule/result fallback.

    The source registry remains the authority on whether this dataset is valid
    for fitted-model development. Availability of the GitHub asset alone is not
    enough to authorize training use.
    """
    normalized = sport.upper().strip()
    if normalized not in SPORTSDATAVERSE_RELEASE_URLS:
        raise BasketballHydrationError(f"unsupported sport {normalized}")
    readiness = source_readiness("SPORTSDATAVERSE_ESPN")
    if not readiness.ready_for_candidate_training:
        blockers = ",".join(readiness.blockers) or "UNKNOWN"
        raise BasketballHydrationError(f"SPORTSDATAVERSE_ESPN_SOURCE_NOT_READY:{blockers}")
    year = int(season)
    url = SPORTSDATAVERSE_RELEASE_URLS[normalized].format(year=year)
    try:
        response = session.get(
            url,
            headers={"Accept": "text/csv", "User-Agent": "WOW-V17-Basketball-Research/1.0"},
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        raise BasketballHydrationError("SPORTSDATAVERSE_ESPN_REQUEST_FAILED") from exc
    if response.status_code == 429:
        raise BasketballHydrationError("SPORTSDATAVERSE_ESPN_RATE_LIMITED")
    if response.status_code != 200:
        raise BasketballHydrationError(f"SPORTSDATAVERSE_ESPN_HTTP_{response.status_code}")
    text = str(response.text or "").lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(text))
    fieldnames = {str(value or "").strip() for value in (reader.fieldnames or [])}
    missing = sorted(SPORTSDATAVERSE_REQUIRED_COLUMNS - fieldnames)
    if missing:
        raise BasketballHydrationError(
            "SPORTSDATAVERSE_ESPN_SCHEMA_DRIFT:" + ",".join(missing)
        )
    # This is the time this exact completed/not-completed snapshot was
    # acquired, not the event start or a caller-supplied historical cutoff.
    # Overwrite any same-named CSV column; provider fields cannot supply it.
    rows = [dict(row) for row in reader]
    observed_at = datetime.now(timezone.utc).isoformat()
    return [dict(row, _wow_source_observed_at=observed_at) for row in rows]


def fetch_espn_games(sport: str, season: int) -> list[dict[str, Any]]:
    """Public fallback for settled schedule/results evidence.

    ESPN date ranges are calendar-year based. ``season`` here therefore means
    the requested calendar year from the maintenance window; the persisted
    game_date is authoritative for chronological replay.
    """
    normalized = sport.upper().strip()
    if normalized not in ESPN_BASE_URLS:
        raise BasketballHydrationError(f"unsupported sport {normalized}")
    year = int(season)
    params = {"dates": f"{year}0101-{year}1231", "limit": 5000}
    try:
        resp = requests.get(
            ESPN_BASE_URLS[normalized],
            params=params,
            headers={"Accept": "application/json", "User-Agent": "WOW-V17-Basketball-Research/1.0"},
            timeout=30,
        )
    except Exception as exc:  # noqa: BLE001
        raise BasketballHydrationError("ESPN_SCOREBOARD_REQUEST_FAILED") from exc
    if resp.status_code != 200:
        raise BasketballHydrationError(f"ESPN_SCOREBOARD_HTTP_{resp.status_code}")
    try:
        body = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise BasketballHydrationError("ESPN_SCOREBOARD_INVALID_JSON") from exc
    events = body.get("events") if isinstance(body, dict) else None
    if not isinstance(events, list):
        raise BasketballHydrationError("ESPN_SCOREBOARD_INVALID_RESPONSE")
    return [event for event in events if isinstance(event, dict)]



def fetch_recent_espn_settlements(
    sport: str, *, now: datetime | None = None, lookback_days: int = 14,
    session: Any = requests, timeout: int = 30,
) -> list[dict[str, Any]]:
    """Bounded supplemental settled-game query when a schedule CSV lags.

    ESPN is existing authorized fallback research data, never model probability.
    Query only completed calendar days; source rows must still survive
    normalize_espn_game and the monotonic persisted-game date fence.
    """
    normalized = str(sport or "").strip().upper()
    if normalized not in ESPN_BASE_URLS:
        raise BasketballHydrationError("ESPN_RECENT_SPORT_UNSUPPORTED")
    if isinstance(lookback_days, bool) or not 1 <= lookback_days <= 14:
        raise BasketballHydrationError("ESPN_RECENT_LOOKBACK_INVALID")
    clock = now if now is not None else datetime.now(timezone.utc)
    if not isinstance(clock, datetime) or clock.utcoffset() is None:
        raise BasketballHydrationError("ESPN_RECENT_CLOCK_INVALID")
    last_completed_day = clock.astimezone(ZoneInfo("America/New_York")).date() - timedelta(days=1)
    first = last_completed_day - timedelta(days=lookback_days - 1)
    # The ESPN Site v2 scoreboard no longer reliably supports YYYYMMDD-YYYYMMDD
    # ranges (observed HTTP 400 in 2026). Fetch at most 14 single-day scoreboards,
    # one attempt each, and fail closed if any day cannot be reconciled.
    # This is bounded SOURCE acquisition, never sporting probability inference.
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for offset in range(lookback_days):
        requested_day = first + timedelta(days=offset)
        params = {"dates": requested_day.strftime("%Y%m%d"), "limit": 100}
        try:
            response = session.get(
                ESPN_BASE_URLS[normalized], params=params,
                headers={"Accept": "application/json", "User-Agent": "WOW-V17-Basketball-Research/1.0"},
                timeout=timeout,
            )
        except Exception as exc:
            raise BasketballHydrationError("ESPN_RECENT_SOURCE_FETCH_FAILED") from exc
        if response.status_code != 200:
            raise BasketballHydrationError(f"ESPN_RECENT_SOURCE_HTTP_{response.status_code}")
        try:
            payload = response.json()
        except Exception as exc:
            raise BasketballHydrationError("ESPN_RECENT_SOURCE_INVALID_JSON") from exc
        daily = payload.get("events") if isinstance(payload, dict) else None
        if (
            not isinstance(daily, list)
            or len(daily) >= 100
            or any(not isinstance(row, dict) for row in daily)
        ):
            raise BasketballHydrationError("ESPN_RECENT_SOURCE_INVALID_RESPONSE")
        for row in daily:
            event_id = str(row.get("id") or "").strip()
            if not event_id:
                raise BasketballHydrationError("ESPN_RECENT_EVENT_IDENTITY_MISSING")
            if event_id in seen:
                raise BasketballHydrationError("ESPN_RECENT_DUPLICATE_GAME_ID")
            try:
                event_at = datetime.fromisoformat(str(row["date"]).replace("Z", "+00:00"))
                if event_at.utcoffset() is None:
                    raise ValueError("naive")
                local_day = event_at.astimezone(ZoneInfo("America/New_York")).date()
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                raise BasketballHydrationError("ESPN_RECENT_EVENT_TIME_INVALID") from exc
            if local_day != requested_day:
                raise BasketballHydrationError("ESPN_RECENT_SOURCE_EVENT_WRONG_DATE")
            seen.add(event_id)
            events.append(row)
    return events


def normalize_game(raw: dict[str, Any], sport: str, retrieved_at: str) -> dict[str, Any] | None:
    status = str(raw.get("status") or "").lower()
    if not any(token in status for token in ("final", "post", "complete")):
        return None
    hs = raw.get("home_team_score")
    aws = raw.get("visitor_team_score")
    if hs is None or aws is None or int(hs) == int(aws):
        return None
    home = raw.get("home_team") or {}
    away = raw.get("visitor_team") or {}
    gid = str(raw.get("id") or "").strip()
    date_text = str(raw.get("date") or raw.get("datetime") or "")[:10]
    if not gid or not date_text or not home.get("id") or not away.get("id"):
        return None
    payload_sha = hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    return {
        "game_id": gid,
        "season": int(raw.get("season") or int(date_text[:4])),
        "game_date": date_text,
        "status": str(raw.get("status") or "Final"),
        "home_team_id": str(home["id"]),
        "away_team_id": str(away["id"]),
        "home_score": int(hs),
        "away_score": int(aws),
        "home_win": int(hs) > int(aws),
        "source_provider": "BALLDONTLIE",
        "source_endpoint": BASE_URLS[sport],
        "source_retrieved_at": retrieved_at,
        "source_payload_sha256": payload_sha,
        "settled": True,
        "updated_at": retrieved_at,
    }


def normalize_sportsdataverse_game(
    raw: dict[str, Any],
    sport: str,
    retrieved_at: str,
    *, source_release_year: int | None = None,
) -> dict[str, Any] | None:
    """Admit observed settled outcomes only when source identity is consistent."""
    completed = str(raw.get("status_type_completed") or "").strip().lower() in {"1", "true", "yes"}
    # A completed flag can contradict an explicit postponed/cancelled status.
    # Never turn a non-settlement into an official training outcome.
    status_text = " ".join(
        str(raw.get(field) or "").strip().lower()
        for field in ("status_type_description", "status_type_name")
    ).replace("_", " ").replace("-", " ")
    if not completed or any(token in status_text for token in ("postpon", "cancel", "no contest")):
        return None
    # Optional status fields on older snapshots may be absent, but explicit
    # active/scheduled evidence must never be overridden by a completed flag.
    state = str(raw.get("status_type_state") or "").strip().lower()
    name = str(raw.get("status_type_name") or "").strip().upper()
    description = str(raw.get("status_type_description") or "").strip().lower()
    if (
        (state and state != "post")
        or (name and name != "STATUS_FINAL" and not name.startswith("STATUS_FINAL_"))
        or (description and description.split("/", 1)[0] != "final")
    ):
        return None
    # SportsDataverse includes preseason and postseason schedules in the same
    # source family. A completed game is not automatically a regular-season
    # training observation. Preserve older snapshots without this field.
    if "season_type" in raw:
        season_type = raw["season_type"]
        if isinstance(season_type, bool) or str(season_type).strip() not in {"2", "2.0"}:
            return None
    event_id = str(raw.get("game_id") or raw.get("id") or "").strip()
    date_text = str(raw.get("game_date") or raw.get("date") or "")[:10]
    home_id = str(raw.get("home_id") or "").strip()
    away_id = str(raw.get("away_id") or "").strip()
    try:
        home_decimal = Decimal(str(raw.get("home_score")).strip())
        away_decimal = Decimal(str(raw.get("away_score")).strip())
        if any(
            not score.is_finite() or score < 0 or score != score.to_integral_value()
            for score in (home_decimal, away_decimal)
        ):
            return None
        home_score = int(home_decimal)
        away_score = int(away_decimal)
    except (TypeError, ValueError, OverflowError, InvalidOperation):
        return None
    if not event_id or not date_text or not home_id or not away_id or home_score == away_score:
        return None
    # A final flag proves completion only at the acquisition of that exact
    # snapshot. Neither a calendar rollover nor elapsed time proves a final.
    # Missing acquisition evidence must be reacquired, never backdated.
    try:
        game_day = date.fromisoformat(date_text)
        captured = datetime.fromisoformat(str(retrieved_at).replace("Z", "+00:00"))
        observed_text = str(raw.get("_wow_source_observed_at") or "")
        observed = datetime.fromisoformat(observed_text.replace("Z", "+00:00"))
        if captured.utcoffset() is None or observed.utcoffset() is None:
            return None
        tipoff_text = str(raw.get("game_date_time") or "").strip()
        tipoff = datetime.fromisoformat(tipoff_text.replace("Z", "+00:00"))
        if tipoff.utcoffset() is None:
            tipoff = tipoff.replace(tzinfo=ZoneInfo("America/New_York"))
        if (
            tipoff.astimezone(ZoneInfo("America/New_York")).date() != game_day
            or not tipoff.astimezone(timezone.utc) < observed.astimezone(timezone.utc)
            <= captured.astimezone(timezone.utc)
        ):
            return None
    except (TypeError, ValueError, OverflowError):
        return None
    normalized = sport.upper().strip()
    try:
        year = int(date_text[:4])
    except ValueError:
        return None
    # Missing season is different from the game's calendar year: October 2026
    # belongs to the NBA schedule release ending in 2027. The immutable source
    # release parameter must be the default whenever the CSV omits a season.
    try:
        season_text = str(raw.get("season") or "").strip()
        season_decimal = Decimal(season_text) if season_text else None
        if season_decimal is not None and (
            not season_decimal.is_finite()
            or season_decimal != season_decimal.to_integral_value()
        ):
            return None
        season = (
            int(season_decimal) if season_decimal is not None
            else int(source_release_year) if source_release_year is not None
            else year
        )
        release_year = int(source_release_year) if source_release_year is not None else season
        if season != release_year:
            return None
    except (ValueError, TypeError, OverflowError, InvalidOperation):
        return None
    source_url = SPORTSDATAVERSE_RELEASE_URLS[normalized].format(year=release_year)
    payload_sha = hashlib.sha256(
        json.dumps({k: v for k, v in raw.items() if k != "_wow_source_observed_at"}, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    return {
        "game_id": f"espn-{event_id}",
        "season": season,
        "game_date": date_text,
        "status": str(raw.get("status_type_description") or raw.get("status_type_name") or "Final"),
        "home_team_id": f"espn-{home_id}",
        "away_team_id": f"espn-{away_id}",
        "home_score": home_score,
        "away_score": away_score,
        "home_win": home_score > away_score,
        "source_provider": "SPORTSDATAVERSE_ESPN",
        "source_endpoint": source_url,
        "source_retrieved_at": observed_text,
        "source_payload_sha256": payload_sha,
        "settled": True,
        "updated_at": observed_text,
    }


def _espn_competitors(raw: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    competitions = raw.get("competitions") or []
    competition = competitions[0] if competitions and isinstance(competitions[0], dict) else {}
    home = away = None
    for competitor in competition.get("competitors") or []:
        if not isinstance(competitor, dict):
            continue
        if competitor.get("homeAway") == "home":
            home = competitor
        elif competitor.get("homeAway") == "away":
            away = competitor
    return home, away


def normalize_espn_game(raw: dict[str, Any], sport: str, retrieved_at: str) -> dict[str, Any] | None:
    status = raw.get("status") if isinstance(raw.get("status"), dict) else {}
    status_type = status.get("type") if isinstance(status.get("type"), dict) else {}
    completed = status_type.get("completed") is True
    # Exact completed flag is mandatory. Substring "post" also matches
    # POSTPONED and is not settled sporting evidence.
    if not completed:
        return None

    # ESPN October scoreboards contain both preseason and the newly started
    # regular season. Mixing exhibitions into the fitted regular-season
    # historical cohort is a model-regime/data-leakage error. ESPN's frozen
    # scoreboard explicitly carries season.type=2, slug=regular-season.
    season = raw.get("season") if isinstance(raw.get("season"), dict) else {}
    if str(season.get("type") or "") != "2" or season.get("slug") != "regular-season":
        return None

    home, away = _espn_competitors(raw)
    if not isinstance(home, dict) or not isinstance(away, dict):
        return None
    home_team = home.get("team") if isinstance(home.get("team"), dict) else {}
    away_team = away.get("team") if isinstance(away.get("team"), dict) else {}
    event_id = str(raw.get("id") or "").strip()
    # ESPN event timestamps are UTC, but NBA/WNBA historical game_date is
    # the North American basketball calendar day (ET), matching fitted replay
    # and the SportsDataverse official game_date field.
    try:
        event_at = datetime.fromisoformat(str(raw.get("date") or "").replace("Z", "+00:00"))
        retrieved = datetime.fromisoformat(str(retrieved_at or "").replace("Z", "+00:00"))
        if event_at.utcoffset() is None or retrieved.utcoffset() is None or event_at > retrieved:
            return None
        date_text = event_at.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        return None
    home_id = str(home_team.get("id") or "").strip()
    away_id = str(away_team.get("id") or "").strip()
    try:
        home_score = int(float(str(home.get("score"))))
        away_score = int(float(str(away.get("score"))))
    except (TypeError, ValueError):
        return None
    if not event_id or not date_text or not home_id or not away_id or home_score == away_score:
        return None

    payload_sha = hashlib.sha256(
        json.dumps(raw, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    return {
        "game_id": f"espn-{event_id}",
        "season": int(date_text[:4]),
        "game_date": date_text,
        "status": str(status_type.get("description") or status_type.get("name") or "Final"),
        "home_team_id": f"espn-{home_id}",
        "away_team_id": f"espn-{away_id}",
        "home_score": home_score,
        "away_score": away_score,
        "home_win": home_score > away_score,
        "source_provider": "ESPN_SCOREBOARD",
        "source_endpoint": ESPN_BASE_URLS[sport],
        "source_retrieved_at": retrieved_at,
        "source_payload_sha256": payload_sha,
        "settled": True,
        "updated_at": retrieved_at,
    }


def _latest_persisted_game_date(client: Any, sport: str) -> date | None:
    result = (
        client.table(TABLES[sport])
        .select("game_date")
        .eq("settled", True)
        .order("game_date", desc=True)
        .limit(1)
        .execute()
    )
    rows = list(result.data or [])
    if not rows or not rows[0].get("game_date"):
        return None
    return date.fromisoformat(str(rows[0]["game_date"])[:10])


def _persist_rows(client: Any, sport: str, rows: list[dict[str, Any]]) -> int:
    """Count genuinely new IDs; refuse duplicates and uncertain partial writes."""
    all_ids = [str(row.get("game_id") or "") for row in rows]
    # Validate the *whole invocation* before the first database write; two
    # repeated IDs in different 100-row batches are still duplicates.
    if any(not identifier for identifier in all_ids) or len(all_ids) != len(set(all_ids)):
        raise BasketballHydrationError("BASKETBALL_SETTLED_GAME_ID_INVALID")

    new_ids: set[str] = set()
    write_attempted = False
    try:
        for offset in range(0, len(rows), 100):
            batch = rows[offset:offset + 100]
            ids = all_ids[offset:offset + 100]
            result = (
                client.table(TABLES[sport]).select("game_id")
                .in_("game_id", ids).execute()
            )
            if not isinstance(getattr(result, "data", None), list):
                raise BasketballHydrationError("BASKETBALL_EXISTING_GAME_IDS_UNVERIFIED")
            existing = {
                str(row.get("game_id") or "") for row in result.data
                if isinstance(row, dict)
            }
            new_ids.update(set(ids) - existing)
            # A failed/ambiguous response may still represent a committed write.
            write_attempted = True
            client.table(TABLES[sport]).upsert(batch, on_conflict="game_id").execute()
    except Exception as exc:
        if write_attempted:
            raise BasketballHydrationPartialWriteError(
                "BASKETBALL_HYDRATION_PARTIAL_WRITE_REPLAY_FORBIDDEN"
            ) from exc
        raise
    return len(new_ids)



def _sportsdataverse_regular_season_start(rows: list[dict[str, Any]], release_year: int) -> str | None:
    """Read opening time from the acquired schedule, withholding ambiguous calendars."""
    starts: dict[str, datetime] = {}
    try:
        for row in rows:
            season_type = str(row.get("season_type") or "").strip()
            if season_type in {"1", "1.0", "3", "3.0"}:
                continue
            if season_type not in {"2", "2.0"}:
                return None
            season = str(row.get("season") or release_year).strip()
            if Decimal(season) != release_year:
                return None
            event_id = str(row.get("game_id") or "").strip()
            home = str(row.get("home_id") or "").strip()
            away = str(row.get("away_id") or "").strip()
            if not event_id or not home or not away or home == away:
                return None
            game_day = date.fromisoformat(str(row.get("game_date") or "")[:10])
            instant = datetime.fromisoformat(str(row.get("game_date_time") or "").replace("Z", "+00:00"))
            if instant.utcoffset() is None:
                instant = instant.replace(tzinfo=ZoneInfo("America/New_York"))
                if instant.replace(fold=0).utcoffset() != instant.replace(fold=1).utcoffset():
                    return None
            if instant.astimezone(ZoneInfo("America/New_York")).date() != game_day:
                return None
            instant = instant.astimezone(timezone.utc)
            if event_id in starts and starts[event_id] != instant:
                return None
            starts[event_id] = instant
    except (ValueError, TypeError, OverflowError, InvalidOperation):
        return None
    return min(starts.values()).isoformat() if starts else None


def hydrate(
    sport: str, seasons: Iterable[int], client=None, *, now: datetime | None = None
) -> dict[str, Any]:
    """Acquire and persist canonical settled evidence with source and calendar metadata."""
    sport = sport.upper().strip()
    if sport not in TABLES:
        raise BasketballHydrationError(f"unsupported sport {sport}")
    clock = now if now is not None else datetime.now(timezone.utc)
    if not isinstance(clock, datetime) or clock.utcoffset() is None:
        raise BasketballHydrationError("BASKETBALL_HYDRATION_CLOCK_INVALID")
    clock = clock.astimezone(timezone.utc)
    client = client or _client()
    retrieved_at = clock.isoformat()
    total_raw = total_settled = new_settled = 0
    per_season: dict[int, int] = {}
    source_by_season: dict[int, str] = {}
    regular_season_start_by_season: dict[int, str] = {}
    fallback_reasons: dict[int, str] = {}
    latest_before = _latest_persisted_game_date(client, sport)
    recent_supplement: dict[int, str] = {}

    prior_season_persisted = False
    try:
        for season in seasons:
            year = int(season)
            raw: list[dict[str, Any]]
            normalizer: Callable[[dict[str, Any], str, str], dict[str, Any] | None]
            provider: str
            primary_error: BasketballHydrationError | None = None
            sportsdataverse_error: BasketballHydrationError | None = None
            try:
                if sport == "NBA":
                    # The persisted fitted NBA corpus and canonical game
                    # resolver both use SportsDataverse ESPN identities.
                    # BallDontLie's bare numeric IDs are NOT the same teams.
                    # Pin NBA training to this already-authorized namespace
                    # until an independently verified crosswalk exists.
                    raw = fetch_sportsdataverse_games(sport, year)
                    normalizer = (
                        lambda row, league, at, release_year=year:
                        normalize_sportsdataverse_game(
                            row, league, at, source_release_year=release_year
                        )
                    )
                    provider = "SPORTSDATAVERSE_ESPN"
                else:
                    # WNBA retains its existing BDL primary + SDV fallback.
                    raw = fetch_games(sport, year)
                    if not raw:
                        raise BasketballHydrationError("BALLDONTLIE_EMPTY_SEASON")
                    normalizer = normalize_game
                    provider = "BALLDONTLIE"
            except BasketballHydrationError as exc:
                if sport == "NBA":
                    # An available unverified BDL ID is not a replacement
                    # for unavailable canonical NBA source evidence.
                    raise BasketballHydrationError(
                        "NBA_CANONICAL_SDV_SOURCE_UNAVAILABLE:" + str(exc)
                    ) from exc
                primary_error = exc
                try:
                    raw = fetch_sportsdataverse_games(sport, year)
                    normalizer = lambda row, league, at, release_year=year: normalize_sportsdataverse_game(
                        row, league, at, source_release_year=release_year
                    )
                    provider = "SPORTSDATAVERSE_ESPN"
                except BasketballHydrationError as source_exc:
                    sportsdataverse_error = source_exc
                    # Raw ESPN scoreboard data is PUBLIC RESEARCH, not in the
                    # approved fitted-training source registry. Do not populate
                    # a model corpus by silently adopting an unentitled source.
                    entitlement = source_readiness("ESPN_SCOREBOARD")
                    if not entitlement.ready_for_candidate_training:
                        raise BasketballHydrationError(
                            "BASKETBALL_FRESH_SOURCE_UNAVAILABLE "
                            f"primary={str(primary_error)} "
                            f"sportsdataverse={str(sportsdataverse_error)} "
                            "espn=ESPN_SCOREBOARD_TRAINING_NOT_ENTITLED:"
                            + ",".join(entitlement.blockers)
                        ) from source_exc
                    try:
                        # ESPN scoreboard queries take a calendar year,
                        # not the SDV NBA season-ending asset year. With an
                        # NBA 2026-27 request in Nov 2026 use the 2026 board.
                        # Access remains independently source-entitlement gated.
                        espn_calendar_year = (
                            max(year - 1, min(clock.year, year))
                            if sport == "NBA" else year
                        )
                        raw = fetch_espn_games(sport, espn_calendar_year)
                    except BasketballHydrationError as fallback_exc:
                        raise BasketballHydrationError(
                            "BASKETBALL_FRESH_SOURCE_UNAVAILABLE "
                            f"primary={str(primary_error)} "
                            f"sportsdataverse={str(sportsdataverse_error)} "
                            f"espn={str(fallback_exc)}"
                        ) from fallback_exc
                    normalizer = normalize_espn_game
                    provider = "ESPN_SCOREBOARD"
                fallback_reasons[year] = str(primary_error)

            if sport == "NBA" and provider == "SPORTSDATAVERSE_ESPN":
                opening = _sportsdataverse_regular_season_start(raw, year)
                if opening is not None:
                    regular_season_start_by_season[year] = opening
            total_raw += len(raw)
            # Live acquisition finishes after the cycle starts. An explicit
            # replay cutoff remains fixed and cannot borrow a later snapshot.
            if now is None:
                retrieved_at = datetime.now(timezone.utc).isoformat()
            normalized_rows = [r for g in raw if (r := normalizer(g, sport, retrieved_at)) is not None]

            # Keep ESPN scoreboard evidence separately typed from open-licensed
            # SportsDataverse training. Never auto-promote an unregistered public
            # source to fitted historical training, even if it returns scores.
            has_new = latest_before is None or any(
                date.fromisoformat(str(row["game_date"])[:10]) > latest_before for row in normalized_rows
            )
            # NBA season assets are labelled by their ending year: from
            # October 2026, 2027 is current and 2026 is completed.
            current_season_label = (
                clock.year + 1 if sport == "NBA" and clock.month >= 10 else clock.year
            )
            # The recent-scoreboard supplement is valid only while this
            # league has a season underway. Offseason gaps are not missing
            # settlements, and must never become freshness/source holds.
            in_season = (
                clock.month in (11, 12, 1, 2, 3, 4, 5, 6)
                if sport == "NBA" else clock.month in (5, 6, 7, 8, 9, 10)
            )
            if (
                provider == "SPORTSDATAVERSE_ESPN"
                and in_season
                and year >= current_season_label and latest_before is not None
                and 2 <= (clock.date() - latest_before).days <= 200
                and not has_new
            ):
                # No certified ESPN-to-SportsDataverse settlement provider
                # crosswalk exists for NBA. A fetch cannot yield an admissible
                # fitted-training row, so do not consume 14 source requests
                # or turn an impossible supplement into partial-write failure.
                if sport == "NBA":
                    recent_supplement[year] = (
                        "SOURCE_BLOCKED:NBA_TEAM_PROVIDER_CROSSWALK_UNVERIFIED"
                    )
                elif not (entitlement := source_readiness("ESPN_SCOREBOARD")).ready_for_candidate_training:
                    recent_supplement[year] = (
                        "SOURCE_BLOCKED:ESPN_SCOREBOARD_TRAINING_NOT_ENTITLED:"
                        + ",".join(entitlement.blockers)
                    )
                else:
                    # A recent ESPN supplement covers at most 14 complete
                    # Eastern calendar days. A June corpus cannot safely jump
                    # straight to yesterday's games in October: doing so would
                    # move the monotonic date fence past an unacquired gap.
                    last_completed_et = (
                        clock.astimezone(ZoneInfo("America/New_York")).date()
                        - timedelta(days=1)
                    )
                    first_covered_et = last_completed_et - timedelta(days=13)
                    if latest_before < first_covered_et - timedelta(days=1):
                        # Never silently omit a requested season from the
                        # acquisition result. Emit a typed hydration blocker
                        # and do not advance the persisted date fence.
                        raise BasketballHydrationError(
                            "ESPN_RECENT_WINDOW_GAP_NOT_COVERED"
                        )
                    try:
                        recent = fetch_recent_espn_settlements(sport, now=clock)
                        supplemental_rows = [
                            row for raw_event in recent
                            if (row := normalize_espn_game(raw_event, sport, retrieved_at)) is not None
                            and date.fromisoformat(row["game_date"]) > latest_before
                        ]
                        seen_recent: set[str] = set()
                        for row in supplemental_rows:
                            if row["game_id"] in seen_recent:
                                raise BasketballHydrationError("ESPN_RECENT_DUPLICATE_GAME_ID")
                            seen_recent.add(row["game_id"])
                        if supplemental_rows:
                            normalized_rows = supplemental_rows
                            provider = "ESPN_SCOREBOARD"
                            recent_supplement[year] = "SETTLED_SOURCE_UPDATED"
                        else:
                            recent_supplement[year] = "NO_NEW_SETTLEMENT"
                    except BasketballHydrationError as exc:
                        recent_supplement[year] = "SOURCE_BLOCKED:" + str(exc)

            if provider != "BALLDONTLIE" and latest_before is not None:
                # Provider IDs are not globally comparable. Restrict fallback writes
                # to rows newer than the existing corpus so old BDL rows cannot be
                # duplicated under ESPN-derived IDs.
                normalized_rows = [
                    row for row in normalized_rows
                    if date.fromisoformat(str(row["game_date"])[:10]) > latest_before
                ]
            # Authorization to read ESPN scoreboard history does not certify
            # compatibility with the fitted NBA corpus. The current NBA
            # resolver uses ESPN team identities, while this provider can
            # introduce a different, unverified ID namespace into stored
            # seasons. Do not pollute the durable fitted-training ledger.
            if sport == "NBA" and provider == "ESPN_SCOREBOARD" and normalized_rows:
                raise BasketballHydrationError(
                    "NBA_TEAM_PROVIDER_CROSSWALK_UNVERIFIED"
                )
            per_season[year] = len(normalized_rows)
            source_by_season[year] = provider
            total_settled += len(normalized_rows)
            new_settled += _persist_rows(client, sport, normalized_rows)
            if normalized_rows:
                prior_season_persisted = True

    except BasketballHydrationPartialWriteError:
        raise
    except Exception as exc:
        if prior_season_persisted:
            # Earlier seasons were committed but this refresh did not finish.
            # Do not replay an incompletely refreshed corpus.
            raise BasketballHydrationPartialWriteError(
                "BASKETBALL_HYDRATION_PARTIAL_WRITE_REPLAY_FORBIDDEN"
            ) from exc
        raise

    latest_after = _latest_persisted_game_date(client, sport)
    return {
        "sport": sport,
        "seasons": [int(s) for s in seasons],
        "raw_rows": total_raw,
        "settled_rows": total_settled,
        "per_season": per_season,
        "source_by_season": source_by_season,
        "regular_season_start_by_season": regular_season_start_by_season,
        "fallback_reasons": fallback_reasons,
        "recent_supplement_status": recent_supplement,
        "fresh_acquisition_new_settled_games": new_settled,
        "latest_game_date_before": latest_before.isoformat() if latest_before else None,
        "latest_game_date_after": latest_after.isoformat() if latest_after else None,
        "can_execute": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sport", required=True, choices=["NBA", "WNBA"])
    parser.add_argument("--seasons", required=True, help="comma-separated calendar years")
    args = parser.parse_args()
    seasons = [int(x.strip()) for x in args.seasons.split(",") if x.strip()]
    print(json.dumps(hydrate(args.sport, seasons), sort_keys=True))


if __name__ == "__main__":
    main()
