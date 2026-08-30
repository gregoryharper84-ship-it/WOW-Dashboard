#!/usr/bin/env python3
"""Read-only WNBA 2026 offline-training source/role probe.

Uses two pinned SportsDataverse release assets produced from official WNBA Stats:
LeagueGameLog player rows for chronology and BoxScoreTraditionalV3 player rows
for per-game role evidence. A player is treated as a starter from the V3
``position`` field only if the complete season proves the structural invariant
of exactly five non-empty-position players for every team-game represented in
the played-player boxscore sample.

This script does not fit, register, publish, score, or execute anything.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import sys
import urllib.request
from collections import Counter, defaultdict
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
    text = payload.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
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
        # ISO 8601 duration from V3. PT0M00.00S is non-playing.
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


def main() -> int:
    log_cols, logs = _rows(_download(PLAYER_LOG_URL, PLAYER_LOG_SHA256))
    box_cols, boxes = _rows(_download(PLAYER_BOX_URL, PLAYER_BOX_SHA256))

    log_game = _pick(log_cols, ["game_id", "gameId"])
    log_player = _pick(log_cols, ["player_id", "person_id", "personId"])
    log_team = _pick(log_cols, ["team_id", "teamId"])
    log_date = _pick(log_cols, ["game_date", "gameDate"])

    box_game = _pick(box_cols, ["game_id", "gameId"])
    box_player = _pick(box_cols, ["player_id", "person_id", "personId"])
    box_team = _pick(box_cols, ["team_id", "teamId"])
    box_position = _pick(box_cols, ["position", "POSITION"])
    box_minutes = _pick(box_cols, ["minutes", "min", "MIN"])

    required_stats = {
        "pts": _pick(log_cols, ["pts", "points"]),
        "reb": _pick(log_cols, ["reb", "rebounds_total", "reboundsTotal"]),
        "ast": _pick(log_cols, ["ast", "assists"]),
        "three_pm": _pick(log_cols, ["fg3m", "three_pointers_made", "threePointersMade"]),
        "minutes": _pick(log_cols, ["min", "minutes"]),
    }

    required = {
        "log_game": log_game,
        "log_player": log_player,
        "log_team": log_team,
        "log_date": log_date,
        "box_game": box_game,
        "box_player": box_player,
        "box_team": box_team,
        "box_position": box_position,
        "box_minutes": box_minutes,
        **required_stats,
    }
    schema_blockers = [f"MISSING_COLUMN:{k}" for k, v in required.items() if not v]

    report: dict[str, object] = {
        "source": "WNBA_STATS_PINNED_2026_LEAGUEGAMELOG_PLUS_BOXSCORETRADITIONALV3",
        "player_log_sha256": PLAYER_LOG_SHA256,
        "player_box_sha256": PLAYER_BOX_SHA256,
        "player_log_columns": log_cols,
        "player_box_columns": box_cols,
        "player_log_n": len(logs),
        "player_box_n": len(boxes),
        "schema_blockers": schema_blockers,
        "starter_semantics": "NONEMPTY_V3_POSITION_ONLY_IF_EXACTLY_FIVE_PER_TEAM_GAME",
        "probability_publishable": False,
        "runtime_model_status": "MODEL_UNAVAILABLE",
        "can_execute": CAN_EXECUTE,
    }
    if schema_blockers:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 2

    assert all(required.values())
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

    played_logs: list[dict[str, str]] = []
    for row in logs:
        if _positive_minutes(row[required_stats["minutes"]]):
            played_logs.append(row)

    joined_n = 0
    starter_true_n = 0
    joined_player_counts: Counter[str] = Counter()
    unmatched_examples: list[tuple[str, str]] = []
    date_missing_n = 0
    stat_invalid_n = 0
    for row in played_logs:
        key = (_norm_id(row[log_game]), _norm_id(row[log_player]))
        box = box_index.get(key)
        if box is None:
            if len(unmatched_examples) < 10:
                unmatched_examples.append(key)
            continue
        if not str(row[log_date] or "").strip():
            date_missing_n += 1
            continue
        try:
            for name in ("pts", "reb", "ast", "three_pm"):
                value = float(row[required_stats[name]])
                if value < 0 or value != int(value):
                    raise ValueError(name)
        except (TypeError, ValueError):
            stat_invalid_n += 1
            continue
        joined_n += 1
        player_id = key[1]
        joined_player_counts[player_id] += 1
        starter_true_n += int(bool(str(box[box_position] or "").strip()))

    join_rate = joined_n / len(played_logs) if played_logs else 0.0
    eligible_players = sum(1 for n in joined_player_counts.values() if n >= 20)
    report.update({
        "played_player_log_n": len(played_logs),
        "box_index_n": len(box_index),
        "duplicate_box_key_n": duplicate_box_keys,
        "team_game_n": len(team_game_counts),
        "starter_position_count_histogram": dict(sorted(starter_count_histogram.items())),
        "bad_team_game_n": len(bad_team_games),
        "bad_team_game_examples": bad_team_games[:10],
        "joined_n": joined_n,
        "join_rate": round(join_rate, 6),
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
    if joined_n < 500:
        blockers.append("WNBA_TRAINING_ROWS_BELOW_MINIMUM")
    if eligible_players == 0:
        blockers.append("WNBA_PLAYER_HISTORY_BELOW_MINIMUM")
    if join_rate < 0.99:
        blockers.append("WNBA_V3_ROLE_JOIN_COMPLETENESS_BELOW_99_PERCENT")
    if date_missing_n:
        blockers.append("WNBA_GAME_DATE_INCOMPLETE")
    if stat_invalid_n:
        blockers.append("WNBA_TRAINING_STAT_INVALID")

    report["blockers"] = blockers
    report["training_status"] = "READY_FOR_OFFLINE_FIT" if not blockers else "TRAINING_DATA_UNAVAILABLE"
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if not blockers else 3


if __name__ == "__main__":
    sys.exit(main())
