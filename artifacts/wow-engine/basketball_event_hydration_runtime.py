"""Hydrate real NBA/WNBA settled team games into V17 research stores.

Primary source: BallDontLie when its credential/path is available.
Fallback source: ESPN public scoreboard, used only for settled games newer than
the currently persisted corpus. The monotonic-date fallback rule prevents a
provider switch from duplicating older BallDontLie rows under different IDs.

Research/training only. No scoring, publication, recommendation, wager, or
execution. can_execute=False unconditional.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
import os
from typing import Any, Callable, Iterable

import requests

can_execute: bool = False

BASE_URLS = {
    "NBA": "https://api.balldontlie.io/v1/games",
    "WNBA": "https://api.balldontlie.io/wnba/v1/games",
}
ESPN_BASE_URLS = {
    "NBA": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
    "WNBA": "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard",
}
TABLES = {
    "NBA": "wow_nba_training_games",
    "WNBA": "wow_wnba_training_games",
}


class BasketballHydrationError(RuntimeError):
    pass


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
    status_text = str(
        status_type.get("name")
        or status_type.get("description")
        or status_type.get("detail")
        or ""
    ).lower()
    if not completed and not any(token in status_text for token in ("final", "complete", "post")):
        return None

    home, away = _espn_competitors(raw)
    if not isinstance(home, dict) or not isinstance(away, dict):
        return None
    home_team = home.get("team") if isinstance(home.get("team"), dict) else {}
    away_team = away.get("team") if isinstance(away.get("team"), dict) else {}
    event_id = str(raw.get("id") or "").strip()
    date_text = str(raw.get("date") or "")[:10]
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


def _persist_rows(client: Any, sport: str, rows: list[dict[str, Any]]) -> None:
    for offset in range(0, len(rows), 250):
        client.table(TABLES[sport]).upsert(rows[offset:offset + 250], on_conflict="game_id").execute()


def hydrate(sport: str, seasons: Iterable[int], client=None) -> dict[str, Any]:
    sport = sport.upper().strip()
    if sport not in TABLES:
        raise BasketballHydrationError(f"unsupported sport {sport}")
    client = client or _client()
    retrieved_at = datetime.now(timezone.utc).isoformat()
    total_raw = total_settled = 0
    per_season: dict[int, int] = {}
    source_by_season: dict[int, str] = {}
    fallback_reasons: dict[int, str] = {}
    latest_before = _latest_persisted_game_date(client, sport)

    for season in seasons:
        year = int(season)
        raw: list[dict[str, Any]]
        normalizer: Callable[[dict[str, Any], str, str], dict[str, Any] | None]
        provider: str
        primary_error: BasketballHydrationError | None = None
        try:
            raw = fetch_games(sport, year)
            normalizer = normalize_game
            provider = "BALLDONTLIE"
        except BasketballHydrationError as exc:
            primary_error = exc
            try:
                raw = fetch_espn_games(sport, year)
            except BasketballHydrationError as fallback_exc:
                raise BasketballHydrationError(
                    f"BASKETBALL_FRESH_SOURCE_UNAVAILABLE primary={str(primary_error)} fallback={str(fallback_exc)}"
                ) from fallback_exc
            normalizer = normalize_espn_game
            provider = "ESPN_SCOREBOARD"
            fallback_reasons[year] = str(primary_error)

        total_raw += len(raw)
        normalized = [r for g in raw if (r := normalizer(g, sport, retrieved_at)) is not None]
        if provider == "ESPN_SCOREBOARD" and latest_before is not None:
            # Provider IDs are not globally comparable. Restrict fallback writes
            # to rows newer than the existing corpus so old BDL rows cannot be
            # duplicated under ESPN IDs.
            normalized = [
                row for row in normalized
                if date.fromisoformat(str(row["game_date"])[:10]) > latest_before
            ]
        per_season[year] = len(normalized)
        source_by_season[year] = provider
        total_settled += len(normalized)
        _persist_rows(client, sport, normalized)

    latest_after = _latest_persisted_game_date(client, sport)
    return {
        "sport": sport,
        "seasons": [int(s) for s in seasons],
        "raw_rows": total_raw,
        "settled_rows": total_settled,
        "per_season": per_season,
        "source_by_season": source_by_season,
        "fallback_reasons": fallback_reasons,
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
