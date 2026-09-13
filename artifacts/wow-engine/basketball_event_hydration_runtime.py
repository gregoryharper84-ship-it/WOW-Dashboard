"""Hydrate real NBA/WNBA settled team games from BallDontLie into V17 stores.

Research/training only. No scoring, publication, recommendation, wager, or execution.
can_execute=False unconditional.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from typing import Any, Iterable

import requests

can_execute: bool = False

BASE_URLS = {
    "NBA": "https://api.balldontlie.io/v1/games",
    "WNBA": "https://api.balldontlie.io/wnba/v1/games",
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
        body = resp.json()
        data = body.get("data") or []
        rows.extend(data)
        cursor = (body.get("meta") or {}).get("next_cursor")
        if not cursor:
            break
        params = {**params, "cursor": cursor}
    return rows


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


def hydrate(sport: str, seasons: Iterable[int], client=None) -> dict[str, Any]:
    sport = sport.upper().strip()
    if sport not in TABLES:
        raise BasketballHydrationError(f"unsupported sport {sport}")
    client = client or _client()
    retrieved_at = datetime.now(timezone.utc).isoformat()
    total_raw = total_settled = 0
    per_season: dict[int, int] = {}
    for season in seasons:
        raw = fetch_games(sport, int(season))
        total_raw += len(raw)
        normalized = [r for g in raw if (r := normalize_game(g, sport, retrieved_at)) is not None]
        per_season[int(season)] = len(normalized)
        total_settled += len(normalized)
        for offset in range(0, len(normalized), 250):
            client.table(TABLES[sport]).upsert(normalized[offset:offset + 250], on_conflict="game_id").execute()
    return {
        "sport": sport,
        "seasons": [int(s) for s in seasons],
        "raw_rows": total_raw,
        "settled_rows": total_settled,
        "per_season": per_season,
        "can_execute": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sport", required=True, choices=["NBA", "WNBA"])
    parser.add_argument("--seasons", required=True, help="comma-separated season years")
    args = parser.parse_args()
    seasons = [int(x.strip()) for x in args.seasons.split(",") if x.strip()]
    print(json.dumps(hydrate(args.sport, seasons), sort_keys=True))


if __name__ == "__main__":
    main()
