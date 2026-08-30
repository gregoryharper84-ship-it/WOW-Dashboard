#!/usr/bin/env python3
"""Diagnostic-only WNBA Stats ↔ ESPN schedule identity comparison.

Read-only. Uses the same pinned 2026 source hashes as the readiness probe and
prints only identity/date metadata needed to audit explicit abbreviation/date
mappings. It never mutates model state and cannot execute.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import urllib.request
from collections import defaultdict
from datetime import datetime

LOG_URL = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/wnba_stats_player_game_logs/player_game_logs_2026.csv"
LOG_SHA = "f326bd597a607a574de488b153d76032ee5ec9c4cacd36c8380f229ed96e6288"
SCHEDULE_URL = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_wnba_schedules/wnba_schedule_2026.csv"
SCHEDULE_SHA = "e77056e882e545d891662e41b64a7bf9106bf221ee256bc6d33cf0b7672e000a"


def download(url: str, sha: str) -> list[dict[str, str]]:
    req = urllib.request.Request(url, headers={"User-Agent": "WOW-Research/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    actual = hashlib.sha256(data).hexdigest()
    if actual != sha:
        raise RuntimeError(f"hash mismatch {actual}")
    return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))


def team(v: object) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(v or "").strip().upper())


def date_only(v: object) -> str:
    raw = str(v or "").strip()
    if not raw:
        return ""
    try:
        return datetime.fromisoformat(raw[:10]).date().isoformat()
    except ValueError:
        return raw[:10]


def log_key(row: dict[str, str]):
    matchup = " ".join(str(row.get("matchup") or "").upper().split())
    m = re.match(r"^([A-Z0-9]+)\s+(?:VS\.?|@)\s+([A-Z0-9]+)$", matchup)
    if not m:
        return None
    return date_only(row.get("game_date")), tuple(sorted((team(m.group(1)), team(m.group(2)))))


def main() -> None:
    logs = download(LOG_URL, LOG_SHA)
    schedules = download(SCHEDULE_URL, SCHEDULE_SHA)
    wnba_team_names: dict[str, str] = {}
    for r in logs:
        abbr = team(r.get("team_abbreviation"))
        if abbr:
            wnba_team_names[abbr] = str(r.get("team_name") or "")
    espn_team_names: dict[str, str] = {}
    schedule_by_date: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in schedules:
        h, a = team(r.get("home_abbreviation")), team(r.get("away_abbreviation"))
        if h:
            espn_team_names[h] = str(r.get("home_display_name") or r.get("home_name") or "")
        if a:
            espn_team_names[a] = str(r.get("away_display_name") or r.get("away_name") or "")
        schedule_by_date[date_only(r.get("game_date"))].append(r)

    unique_wnba_games: dict[str, tuple[str, tuple[str, str]]] = {}
    for r in logs:
        gid = str(r.get("game_id") or "").strip()
        if gid and r.get("player_id") and gid not in unique_wnba_games:
            k = log_key(r)
            if k:
                unique_wnba_games[gid] = k

    unmatched = []
    for gid, key in sorted(unique_wnba_games.items()):
        date, pair = key
        same_date = []
        matched = False
        for s in schedule_by_date.get(date, []):
            spair = tuple(sorted((team(s.get("home_abbreviation")), team(s.get("away_abbreviation")))))
            same_date.append({"pair": spair, "home_name": s.get("home_display_name"), "away_name": s.get("away_display_name"), "tip": s.get("game_date_time")})
            if spair == pair:
                matched = True
        if not matched and len(unmatched) < 20:
            unmatched.append({"wnba_game_id": gid, "wnba_key": key, "espn_same_date": same_date})

    print(json.dumps({
        "wnba_teams": wnba_team_names,
        "espn_teams": espn_team_names,
        "wnba_unique_game_n": len(unique_wnba_games),
        "espn_schedule_date_n": len(schedule_by_date),
        "first_unmatched": unmatched,
    }, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
