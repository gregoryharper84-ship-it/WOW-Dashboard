#!/usr/bin/env python3
"""Read-only WNBA 2026 offline-training source/role/time probe.

Uses pinned SportsDataverse release assets:
* official WNBA Stats LeagueGameLog player rows for chronology/outcomes,
* official WNBA Stats BoxScoreTraditionalV3 player rows for role evidence,
* ESPN WNBA schedule rows only for exact historical event start time.

A player is a starter from the V3 ``position`` field only if the complete
corpus proves exactly five non-empty-position players per team-game. Exact tip
time is accepted only through an unambiguous date + exact team-pair join. No
fuzzy matching or fabricated timestamps are permitted.

LeagueGameLog rows without a player identity are non-materializable under the
governed historical-player contract. They are counted and excluded before join
completeness is measured; no identifiable player row is discarded.

This script does not fit, register, publish, score, or execute anything.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sys
import urllib.request
from collections import Counter
from datetime import datetime
from typing import Iterable

PLAYER_LOG_URL = (
    "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/"
    "wnba_stats_player_game_logs/player_game_logs_2026.csv"
)
PLAYER_LOG_SHA256 = "f326bd597a607a574de488b153d76032ee5ec9c4cacd36c8380f229ed96e6288"
PLAYER_BOX_URL = (
    "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/"
    "wnba_stats_player_boxscores/player_boxscores_2026.csv"
)
PLAYER_BOX_SHA256 = "88e0654dff131c88c08b6c66b692e0ae6e7e11a04a18d99a96e4a4722518e49d"
SCHEDULE_URL = (
    "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/"
    "espn_wnba_schedules/wnba_schedule_2026.csv"
)
SCHEDULE_SHA256 = "e77056e882e545d891662e41b64a7bf9106bf221ee256bc6d33cf0b7672e000a"
CAN_EXECUTE = False


def _download(url: str, expected_sha256: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "WOW-Research/1.0"})
    with urllib.request.urlopen(req, timeout=60) as response:
        payload = response.read()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != expected_sha256:
        raise RuntimeError(f"SOURCE_HASH_MISMATCH expected={expected_sha256} actual={digest}")
    return payload


def _rows(payload: bytes) -> tuple[list[str], list[dict[str, str]]]:
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")))
    return list(reader.fieldnames or []), list(reader)


def _pick(columns: Iterable[str], aliases: Iterable[str]) -> str | None:
    folded = {c.casefold(): c for c in columns}
    for alias in aliases:
        hit = folded.get(alias.casefold())
        if hit:
            return hit
    return None


def _norm_id(value: object) -> str:
    text = str(value or "").strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text


def _positive_minutes(value: object) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return False
    if raw.upper().startswith("PT"):
        return raw not in {"PT0M", "PT0M0S", "PT0M00S", "PT0M00.00S"}
    if ":" in raw:
        try:
            mm, ss = raw.split(":", 1)
            return float(mm) * 60 + float(ss) > 0
        except ValueError:
            return False
    try:
        return float(raw) > 0
    except ValueError:
        return False


def _date_only(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    # Preserve exact calendar date semantics while tolerating ISO timestamps.
    token = raw[:10]
    try:
        return datetime.fromisoformat(token).date().isoformat()
    except ValueError:
        for fmt in ("%m/%d/%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(raw.split()[0], fmt).date().isoformat()
            except ValueError:
                pass
    return ""


def _aware_tip(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed


def _team(value: object) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").strip().upper())


def _log_game_key(game_date: object, team_abbr: object, matchup: object) -> tuple[str, tuple[str, str]] | None:
    date_key = _date_only(game_date)
    team = _team(team_abbr)
    raw_matchup = " ".join(str(matchup or "").strip().upper().split())
    if not date_key or not team or not raw_matchup:
        return None
    # WNBA LeagueGameLog uses forms such as "DAL vs. PHO" and "DAL @ PHO".
    match = re.match(r"^([A-Z0-9]+)\s+(?:VS\.?|@)\s+([A-Z0-9]+)$", raw_matchup)
    if not match:
        return None
    left, right = _team(match.group(1)), _team(match.group(2))
    if team != left or not right or left == right:
        return None
    return date_key, tuple(sorted((left, right)))


def main() -> int:
    log_cols, logs = _rows(_download(PLAYER_LOG_URL, PLAYER_LOG_SHA256))
    box_cols, boxes = _rows(_download(PLAYER_BOX_URL, PLAYER_BOX_SHA256))
    schedule_cols, schedules = _rows(_download(SCHEDULE_URL, SCHEDULE_SHA256))

    log_game = _pick(log_cols, ["game_id", "gameId"])
    log_player = _pick(log_cols, ["player_id", "person_id", "personId"])
    log_team = _pick(log_cols, ["team_id", "teamId"])
    log_team_abbr = _pick(log_cols, ["team_abbreviation", "teamAbbreviation", "team_abbr"])
    log_date = _pick(log_cols, ["game_date", "gameDate"])
    log_matchup = _pick(log_cols, ["matchup", "MATCHUP"])
    log_player_name = _pick(log_cols, ["player_name", "playerName"])

    box_game = _pick(box_cols, ["game_id", "gameId"])
    box_player = _pick(box_cols, ["player_id", "person_id", "personId"])
    box_team = _pick(box_cols, ["team_id", "teamId"])
    box_position = _pick(box_cols, ["position", "POSITION"])
    box_minutes = _pick(box_cols, ["minutes", "min", "MIN"])

    schedule_date = _pick(schedule_cols, ["game_date", "gameDate", "date"])
    schedule_tip = _pick(schedule_cols, ["game_date_time", "gameDateTime", "date_time", "dateTime", "start_date", "startDate"])
    schedule_home = _pick(schedule_cols, [
        "home_team_abbreviation", "home_team_abbrev", "home_team_abbr", "home_abbreviation",
        "homeTeamAbbreviation", "home_team", "homeTeam",
    ])
    schedule_away = _pick(schedule_cols, [
        "away_team_abbreviation", "away_team_abbrev", "away_team_abbr", "away_abbreviation",
        "awayTeamAbbreviation", "away_team", "awayTeam",
    ])

    required_stats = {
        "pts": _pick(log_cols, ["pts", "points"]),
        "reb": _pick(log_cols, ["reb", "rebounds_total", "reboundsTotal"]),
        "ast": _pick(log_cols, ["ast", "assists"]),
        "three_pm": _pick(log_cols, ["fg3m", "three_pointers_made", "threePointersMade"]),
        "minutes": _pick(log_cols, ["min", "minutes"]),
    }
    required = {
        "log_game": log_game, "log_player": log_player, "log_team": log_team,
        "log_team_abbr": log_team_abbr, "log_date": log_date, "log_matchup": log_matchup,
        "log_player_name": log_player_name,
        "box_game": box_game, "box_player": box_player, "box_team": box_team,
        "box_position": box_position, "box_minutes": box_minutes,
        "schedule_date": schedule_date, "schedule_tip": schedule_tip,
        "schedule_home": schedule_home, "schedule_away": schedule_away,
        **required_stats,
    }
    schema_blockers = [f"MISSING_COLUMN:{k}" for k, v in required.items() if not v]
    report: dict[str, object] = {
        "source": "WNBA_STATS_PINNED_2026_PLUS_PINNED_ESPN_EVENT_TIME",
        "player_log_sha256": PLAYER_LOG_SHA256,
        "player_box_sha256": PLAYER_BOX_SHA256,
        "schedule_sha256": SCHEDULE_SHA256,
        "player_log_columns": log_cols,
        "player_box_columns": box_cols,
        "schedule_columns": schedule_cols,
        "player_log_n": len(logs),
        "player_box_n": len(boxes),
        "schedule_row_n": len(schedules),
        "schema_blockers": schema_blockers,
        "starter_semantics": "NONEMPTY_V3_POSITION_ONLY_IF_EXACTLY_FIVE_PER_TEAM_GAME",
        "event_time_semantics": "EXACT_DATE_PLUS_EXACT_TEAM_PAIR_ONLY_NO_FUZZY_MATCHING",
        "probability_publishable": False,
        "runtime_model_status": "MODEL_UNAVAILABLE",
        "can_execute": CAN_EXECUTE,
    }
    if schema_blockers:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 2

    assert all(required.values())

    # Role evidence gate.
    box_index: dict[tuple[str, str], dict[str, str]] = {}
    duplicate_box_keys = 0
    team_game_counts: Counter[tuple[str, str]] = Counter()
    team_game_starter_counts: Counter[tuple[str, str]] = Counter()
    for row in boxes:
        key = (_norm_id(row[box_game]), _norm_id(row[box_player]))
        if not all(key):
            continue
        if key in box_index:
            duplicate_box_keys += 1
            continue
        box_index[key] = row
        if not _positive_minutes(row[box_minutes]):
            continue
        tg = (_norm_id(row[box_game]), _norm_id(row[box_team]))
        team_game_counts[tg] += 1
        if str(row[box_position] or "").strip():
            team_game_starter_counts[tg] += 1

    starter_count_histogram = Counter(team_game_starter_counts.get(tg, 0) for tg in team_game_counts)
    bad_team_games = [
        {"game_id": g, "team_id": t, "played_n": team_game_counts[(g, t)], "position_n": team_game_starter_counts.get((g, t), 0)}
        for g, t in sorted(team_game_counts)
        if team_game_starter_counts.get((g, t), 0) != 5
    ]

    # Exact event-time enrichment gate.
    schedule_index: dict[tuple[str, tuple[str, str]], datetime] = {}
    schedule_duplicate_keys: Counter[tuple[str, tuple[str, str]]] = Counter()
    schedule_invalid_tip_n = 0
    schedule_invalid_key_n = 0
    for row in schedules:
        date_key = _date_only(row[schedule_date])
        home, away = _team(row[schedule_home]), _team(row[schedule_away])
        tip = _aware_tip(row[schedule_tip])
        if not date_key or not home or not away or home == away:
            schedule_invalid_key_n += 1
            continue
        if tip is None:
            schedule_invalid_tip_n += 1
            continue
        key = (date_key, tuple(sorted((home, away))))
        if key in schedule_index:
            schedule_duplicate_keys[key] += 1
            continue
        schedule_index[key] = tip

    identityless_played_n = 0
    identityless_examples: list[dict[str, str]] = []
    eligible_played_logs: list[dict[str, str]] = []
    for row in logs:
        if not _positive_minutes(row[required_stats["minutes"]]):
            continue
        player_id = _norm_id(row[log_player])
        if not player_id:
            identityless_played_n += 1
            if len(identityless_examples) < 5:
                identityless_examples.append({
                    "game_id": _norm_id(row[log_game]),
                    "team_id": _norm_id(row[log_team]),
                    "player_name": str(row[log_player_name] or "").strip(),
                })
            continue
        eligible_played_logs.append(row)

    joined_n = starter_true_n = date_missing_n = stat_invalid_n = 0
    schedule_joined_n = schedule_key_invalid_n = schedule_unmatched_n = 0
    joined_player_counts: Counter[str] = Counter()
    unmatched_examples: list[tuple[str, str]] = []
    schedule_unmatched_examples: list[object] = []
    wnba_game_to_schedule_key: dict[str, tuple[str, tuple[str, str]]] = {}
    conflicting_wnba_game_keys: list[dict[str, object]] = []
    for row in eligible_played_logs:
        key = (_norm_id(row[log_game]), _norm_id(row[log_player]))
        box = box_index.get(key)
        if box is None:
            if len(unmatched_examples) < 10:
                unmatched_examples.append(key)
            continue
        if not str(row[log_date] or "").strip():
            date_missing_n += 1
            continue
        game_key = _log_game_key(row[log_date], row[log_team_abbr], row[log_matchup])
        if game_key is None:
            schedule_key_invalid_n += 1
            if len(schedule_unmatched_examples) < 10:
                schedule_unmatched_examples.append({"game_id": key[0], "reason": "INVALID_WNBA_GAME_KEY", "matchup": row[log_matchup]})
            continue
        previous = wnba_game_to_schedule_key.get(key[0])
        if previous is not None and previous != game_key:
            if len(conflicting_wnba_game_keys) < 10:
                conflicting_wnba_game_keys.append({"game_id": key[0], "first": previous, "later": game_key})
            continue
        wnba_game_to_schedule_key[key[0]] = game_key
        if game_key not in schedule_index:
            schedule_unmatched_n += 1
            if len(schedule_unmatched_examples) < 10:
                schedule_unmatched_examples.append({"game_id": key[0], "reason": "NO_EXACT_SCHEDULE_MATCH", "game_key": game_key})
            continue
        schedule_joined_n += 1
        try:
            for name in ("pts", "reb", "ast", "three_pm"):
                value = float(row[required_stats[name]])
                if value < 0 or value != int(value):
                    raise ValueError(name)
        except (TypeError, ValueError):
            stat_invalid_n += 1
            continue
        joined_n += 1
        joined_player_counts[key[1]] += 1
        starter_true_n += int(bool(str(box[box_position] or "").strip()))

    role_join_rate = joined_n / len(eligible_played_logs) if eligible_played_logs else 0.0
    schedule_join_rate = schedule_joined_n / len(eligible_played_logs) if eligible_played_logs else 0.0
    unique_wnba_games = len(wnba_game_to_schedule_key)
    unique_schedule_matched_games = len({k for k in wnba_game_to_schedule_key.values() if k in schedule_index})
    eligible_players = sum(1 for n in joined_player_counts.values() if n >= 20)
    report.update({
        "identityless_played_nonplayer_row_n": identityless_played_n,
        "identityless_examples": identityless_examples,
        "eligible_identified_played_player_log_n": len(eligible_played_logs),
        "box_index_n": len(box_index),
        "duplicate_box_key_n": duplicate_box_keys,
        "team_game_n": len(team_game_counts),
        "starter_position_count_histogram": dict(sorted(starter_count_histogram.items())),
        "bad_team_game_n": len(bad_team_games),
        "bad_team_game_examples": bad_team_games[:10],
        "schedule_index_n": len(schedule_index),
        "schedule_duplicate_key_n": sum(schedule_duplicate_keys.values()),
        "schedule_duplicate_examples": [str(k) for k in list(schedule_duplicate_keys)[:10]],
        "schedule_invalid_key_n": schedule_invalid_key_n,
        "schedule_invalid_tip_n": schedule_invalid_tip_n,
        "schedule_key_invalid_player_row_n": schedule_key_invalid_n,
        "schedule_unmatched_player_row_n": schedule_unmatched_n,
        "schedule_unmatched_examples": schedule_unmatched_examples,
        "conflicting_wnba_game_key_n": len(conflicting_wnba_game_keys),
        "conflicting_wnba_game_key_examples": conflicting_wnba_game_keys,
        "unique_wnba_game_n": unique_wnba_games,
        "unique_schedule_matched_game_n": unique_schedule_matched_games,
        "schedule_joined_player_row_n": schedule_joined_n,
        "schedule_join_rate": round(schedule_join_rate, 6),
        "joined_n": joined_n,
        "join_rate": round(role_join_rate, 6),
        "starter_true_n": starter_true_n,
        "unique_joined_player_n": len(joined_player_counts),
        "eligible_player_n_ge_20_games": eligible_players,
        "date_missing_n": date_missing_n,
        "stat_invalid_n": stat_invalid_n,
        "unmatched_examples": unmatched_examples,
    })

    blockers: list[str] = []
    if duplicate_box_keys:
        blockers.append("WNBA_V3_PLAYER_BOX_JOIN_NOT_UNIQUE")
    if bad_team_games:
        blockers.append("WNBA_V3_POSITION_STARTER_INVARIANT_FAILED")
    if schedule_duplicate_keys:
        blockers.append("WNBA_EVENT_TIME_JOIN_NOT_UNIQUE")
    if schedule_invalid_tip_n:
        blockers.append("WNBA_EVENT_TIME_TIMEZONE_INVALID")
    if schedule_key_invalid_n or conflicting_wnba_game_keys:
        blockers.append("WNBA_EVENT_IDENTITY_UNRESOLVED")
    if schedule_join_rate < 1.0 or unique_schedule_matched_games != unique_wnba_games:
        blockers.append("WNBA_EVENT_TIME_JOIN_INCOMPLETE")
    if joined_n < 500:
        blockers.append("WNBA_TRAINING_ROWS_BELOW_MINIMUM")
    if eligible_players == 0:
        blockers.append("WNBA_PLAYER_HISTORY_BELOW_MINIMUM")
    if role_join_rate < 0.99:
        blockers.append("WNBA_V3_ROLE_JOIN_COMPLETENESS_BELOW_99_PERCENT")
    if date_missing_n:
        blockers.append("WNBA_GAME_DATE_INCOMPLETE")
    if stat_invalid_n:
        blockers.append("WNBA_TRAINING_STAT_INVALID")

    report["blockers"] = blockers
    report["training_status"] = "READY_FOR_OFFLINE_FIT" if not blockers else "TRAINING_DATA_UNAVAILABLE"
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0 if not blockers else 3


if __name__ == "__main__":
    sys.exit(main())
