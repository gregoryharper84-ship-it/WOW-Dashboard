#!/usr/bin/env python3
"""Read-only WNBA 2026 offline-training source/join probe.

Downloads two fixed SportsDataverse release assets that are documented as
WNBA Stats (stats.wnba.com) derivatives, verifies pinned SHA-256 digests, and
reports whether player-game stat rows can be joined to explicit starter-role
rows. This script does not fit/register/publish a model and cannot execute.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import sys
import urllib.request
from collections import Counter
from typing import Iterable

PLAYER_LOG_URL = (
    "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/"
    "wnba_stats_player_game_logs/player_game_logs_2026.csv"
)
PLAYER_LOG_SHA256 = "f326bd597a607a574de488b153d76032ee5ec9c4cacd36c8380f229ed96e6288"
ROSTER_URL = (
    "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/"
    "wnba_stats_game_rosters/game_rosters_2026.csv"
)
ROSTER_SHA256 = "c96e867ca5a5caa2ba8f2cd6333f8c79dda2b18f3bdaa52f918048bce1371477"
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
    fieldnames = list(reader.fieldnames or [])
    return fieldnames, list(reader)


def _pick(columns: Iterable[str], aliases: Iterable[str]) -> str | None:
    exact = {c: c for c in columns}
    folded = {c.casefold(): c for c in columns}
    for alias in aliases:
        if alias in exact:
            return exact[alias]
        if alias.casefold() in folded:
            return folded[alias.casefold()]
    return None


def _norm_id(value: object) -> str:
    text = str(value or "").strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text


def _starter(value: object) -> bool | None:
    text = str(value or "").strip().casefold()
    if text in {"true", "t", "1", "yes", "y", "starter", "start"}:
        return True
    if text in {"false", "f", "0", "no", "n", "bench"}:
        return False
    return None


def main() -> int:
    log_payload = _download(PLAYER_LOG_URL, PLAYER_LOG_SHA256)
    roster_payload = _download(ROSTER_URL, ROSTER_SHA256)
    log_cols, logs = _rows(log_payload)
    roster_cols, rosters = _rows(roster_payload)

    log_game = _pick(log_cols, ["game_id", "GAME_ID", "gameId"])
    log_player = _pick(log_cols, ["player_id", "PLAYER_ID", "person_id", "personId"])
    roster_game = _pick(roster_cols, ["game_id", "GAME_ID", "gameId"])
    roster_player = _pick(roster_cols, ["player_id", "PLAYER_ID", "person_id", "personId"])
    roster_starter = _pick(roster_cols, ["starter", "is_starter", "isStarter", "STARTER"])

    required_log_stats = {
        "minutes": _pick(log_cols, ["min", "minutes", "MIN"]),
        "pts": _pick(log_cols, ["pts", "PTS"]),
        "reb": _pick(log_cols, ["reb", "REB"]),
        "ast": _pick(log_cols, ["ast", "AST"]),
        "three_pm": _pick(log_cols, ["fg3m", "FG3M", "three_pm"]),
    }

    schema_blockers: list[str] = []
    for label, col in {
        "log_game": log_game,
        "log_player": log_player,
        "roster_game": roster_game,
        "roster_player": roster_player,
        "roster_starter": roster_starter,
        **required_log_stats,
    }.items():
        if not col:
            schema_blockers.append(f"MISSING_COLUMN:{label}")

    report: dict[str, object] = {
        "source": "SPORTSDATAVERSE_WNBA_STATS_DERIVATIVE_PINNED_2026",
        "player_log_sha256": PLAYER_LOG_SHA256,
        "roster_sha256": ROSTER_SHA256,
        "player_log_columns": log_cols,
        "roster_columns": roster_cols,
        "player_log_n": len(logs),
        "roster_n": len(rosters),
        "schema_blockers": schema_blockers,
        "probability_publishable": False,
        "runtime_model_status": "MODEL_UNAVAILABLE",
        "can_execute": CAN_EXECUTE,
    }
    if schema_blockers:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 2

    assert log_game and log_player and roster_game and roster_player and roster_starter
    roster_index: dict[tuple[str, str], bool] = {}
    duplicate_roster_keys = 0
    starter_unparseable = 0
    for row in rosters:
        key = (_norm_id(row.get(roster_game)), _norm_id(row.get(roster_player)))
        if not all(key):
            continue
        parsed = _starter(row.get(roster_starter))
        if parsed is None:
            starter_unparseable += 1
            continue
        if key in roster_index:
            duplicate_roster_keys += 1
            continue
        roster_index[key] = parsed

    played_logs = []
    player_counts: Counter[str] = Counter()
    for row in logs:
        try:
            minutes = float(row.get(required_log_stats["minutes"] or "") or 0)
        except ValueError:
            continue
        if minutes <= 0:
            continue
        played_logs.append(row)
        player_counts[_norm_id(row.get(log_player))] += 1

    joined = 0
    starters = 0
    unmatched_examples: list[tuple[str, str]] = []
    joined_player_counts: Counter[str] = Counter()
    for row in played_logs:
        key = (_norm_id(row.get(log_game)), _norm_id(row.get(log_player)))
        starter = roster_index.get(key)
        if starter is None:
            if len(unmatched_examples) < 10:
                unmatched_examples.append(key)
            continue
        joined += 1
        starters += int(starter)
        joined_player_counts[key[1]] += 1

    join_rate = joined / len(played_logs) if played_logs else 0.0
    eligible_players = sum(1 for n in joined_player_counts.values() if n >= 20)
    report.update(
        {
            "played_player_log_n": len(played_logs),
            "roster_index_n": len(roster_index),
            "duplicate_roster_key_n": duplicate_roster_keys,
            "starter_unparseable_n": starter_unparseable,
            "joined_n": joined,
            "join_rate": round(join_rate, 6),
            "starter_true_n": starters,
            "unique_joined_player_n": len(joined_player_counts),
            "eligible_player_n_ge_20_games": eligible_players,
            "unmatched_examples": unmatched_examples,
        }
    )

    blockers: list[str] = []
    if duplicate_roster_keys:
        blockers.append("WNBA_ROSTER_JOIN_NOT_UNIQUE")
    if starter_unparseable:
        blockers.append("WNBA_STARTER_ROLE_UNPARSEABLE")
    if joined < 500:
        blockers.append("WNBA_TRAINING_ROWS_BELOW_MINIMUM")
    if eligible_players == 0:
        blockers.append("WNBA_PLAYER_HISTORY_BELOW_MINIMUM")
    if join_rate < 0.99:
        blockers.append("WNBA_ROLE_JOIN_COMPLETENESS_BELOW_99_PERCENT")
    report["blockers"] = blockers
    report["training_status"] = "READY_FOR_OFFLINE_FIT" if not blockers else "TRAINING_DATA_UNAVAILABLE"
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if not blockers else 3


if __name__ == "__main__":
    sys.exit(main())
