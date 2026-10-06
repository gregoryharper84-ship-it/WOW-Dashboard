"""Historical point-in-time replay builders for Class-C live ML research."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Iterable

import httpx
import numpy as np
import pandas as pd

CAN_EXECUTE = False
NFL_PBP_URL = "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{season}.csv"
MLB_BASE = "https://statsapi.mlb.com/api/v1"

NFL_FEATURES = (
    "score_diff",
    "seconds_remaining",
    "remaining_fraction",
    "score_time_pressure",
    "possession_home",
    "down",
    "ydstogo",
    "field_position_home",
    "home_timeouts",
    "away_timeouts",
    "timeout_diff",
    "late_possession_home",
)
MLB_FEATURES = (
    "score_diff",
    "inning",
    "bottom_half",
    "outs",
    "base1",
    "base2",
    "base3",
    "offense_home",
    "late_score_pressure",
    "runners_on",
)


class ReplayDataError(RuntimeError):
    pass


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _get_bytes(url: str, *, params: dict[str, Any] | None = None, attempts: int = 3) -> tuple[bytes, str]:
    errors: list[str] = []
    with httpx.Client(follow_redirects=True, timeout=120.0, headers={"User-Agent": "WOW-Live-Challenger/1.0"}) as client:
        for attempt in range(1, attempts + 1):
            try:
                response = client.get(url, params=params)
                response.raise_for_status()
                return response.content, str(response.url)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{type(exc).__name__}:{exc}")
                if attempt < attempts:
                    time.sleep(0.4 * attempt)
    raise ReplayDataError("REPLAY_SOURCE_UNAVAILABLE:" + "|".join(errors[-3:]))


def _cached_csv(season: int, cache_dir: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"nfl_pbp_{season}.csv"
    requested = NFL_PBP_URL.format(season=season)
    if path.exists():
        raw = path.read_bytes()
        resolved = requested
    else:
        raw, resolved = _get_bytes(requested)
        path.write_bytes(raw)
    digest = _sha_bytes(raw)
    wanted = {
        "play_id", "game_id", "season", "qtr", "home_team", "away_team", "posteam",
        "game_seconds_remaining", "total_home_score", "total_away_score",
        "down", "ydstogo", "yardline_100", "home_timeouts_remaining",
        "away_timeouts_remaining",
    }
    frame = pd.read_csv(path, low_memory=False, usecols=lambda c: c in wanted)
    required = {
        "play_id", "game_id", "season", "qtr", "home_team", "away_team", "posteam",
        "game_seconds_remaining", "total_home_score", "total_away_score",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ReplayDataError("NFL_PBP_SCHEMA_CHANGED:" + ",".join(missing))
    return frame, {
        "season": season,
        "requested_url": requested,
        "resolved_url": resolved,
        "sha256": digest,
        "row_n": int(len(frame)),
    }


def build_nfl_replay(seasons: Iterable[int], cache_dir: str | Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    frames: list[pd.DataFrame] = []
    assets: list[dict[str, Any]] = []
    for season in sorted({int(v) for v in seasons}):
        frame, manifest = _cached_csv(season, Path(cache_dir))
        assets.append(manifest)
        frames.append(frame)
    raw = pd.concat(frames, ignore_index=True)

    numeric = [
        "play_id", "season", "qtr", "game_seconds_remaining", "total_home_score",
        "total_away_score", "down", "ydstogo", "yardline_100",
        "home_timeouts_remaining", "away_timeouts_remaining",
    ]
    for col in numeric:
        if col not in raw.columns:
            raw[col] = np.nan
        raw[col] = pd.to_numeric(raw[col], errors="coerce")

    raw = raw.sort_values(["game_id", "play_id"])
    final = raw.dropna(subset=["total_home_score", "total_away_score"]).groupby("game_id", sort=False).tail(1)
    final_home_win = {
        str(row.game_id): int(float(row.total_home_score) > float(row.total_away_score))
        for row in final.itertuples()
        if float(row.total_home_score) != float(row.total_away_score)
    }

    states = raw[
        raw["game_id"].astype(str).isin(final_home_win)
        & raw["qtr"].between(1, 4)
        & raw["game_seconds_remaining"].between(1, 3599)
        & raw["posteam"].notna()
        & raw["down"].notna()
        & raw["ydstogo"].notna()
        & raw["yardline_100"].notna()
        & raw["total_home_score"].notna()
        & raw["total_away_score"].notna()
    ].copy()
    states["minute_bucket"] = np.floor(states["game_seconds_remaining"] / 60.0).astype(int)
    states = states.sort_values(["game_id", "minute_bucket", "play_id"]).groupby(
        ["game_id", "minute_bucket"], as_index=False, sort=False
    ).tail(1)

    states["score_diff"] = states["total_home_score"] - states["total_away_score"]
    states["seconds_remaining"] = states["game_seconds_remaining"]
    states["remaining_fraction"] = states["seconds_remaining"] / 3600.0
    states["score_time_pressure"] = states["score_diff"] / np.sqrt((states["seconds_remaining"] / 60.0) + 1.0)
    states["possession_home"] = (states["posteam"] == states["home_team"]).astype(float)
    states["down"] = states["down"].astype(float)
    states["ydstogo"] = states["ydstogo"].clip(lower=0, upper=99).astype(float)
    offense_advantage = 50.0 - states["yardline_100"].astype(float)
    states["field_position_home"] = np.where(states["possession_home"] == 1.0, offense_advantage, -offense_advantage)
    states["home_timeouts"] = states["home_timeouts_remaining"].fillna(3).clip(0, 3)
    states["away_timeouts"] = states["away_timeouts_remaining"].fillna(3).clip(0, 3)
    states["timeout_diff"] = states["home_timeouts"] - states["away_timeouts"]
    states["late_possession_home"] = states["possession_home"] * (1.0 - states["remaining_fraction"])
    states["home_win"] = states["game_id"].astype(str).map(final_home_win)
    out = states[["game_id", "season", *NFL_FEATURES, "home_win"]].dropna().reset_index(drop=True)
    manifest = {
        "provider": "NFLVERSE_PUBLIC_DATA",
        "dataset": "PLAY_BY_PLAY",
        "assets": assets,
        "state_sampling": "LAST_SCRIMMAGE_STATE_PER_GAME_MINUTE_REGULATION_ONLY",
        "row_n": int(len(out)),
        "game_n": int(out["game_id"].nunique()),
        "feature_names": list(NFL_FEATURES),
        "external_win_probability_used": False,
        "market_features_used": False,
        "probability_publishable": False,
        "can_execute": False,
    }
    return out, manifest


def _mlb_schedule_game_pks(season: int) -> list[int]:
    raw, _ = _get_bytes(
        f"{MLB_BASE}/schedule",
        params={"sportId": 1, "season": season, "gameType": "R"},
    )
    payload = json.loads(raw)
    out: list[int] = []
    for block in payload.get("dates") or []:
        for game in (block or {}).get("games") or []:
            status = ((game.get("status") or {}).get("abstractGameState") or "").upper()
            pk = int(game.get("gamePk") or 0)
            if status == "FINAL" and pk > 0:
                out.append(pk)
    return sorted(set(out))


def _sample_game_pks(pks: list[int], max_games: int) -> list[int]:
    if len(pks) <= max_games:
        return pks
    indexes = np.linspace(0, len(pks) - 1, num=max_games, dtype=int)
    return [pks[int(i)] for i in indexes]


def _runner_id(node: dict[str, Any]) -> str:
    details = node.get("details") or {}
    runner = details.get("runner") or {}
    return str(runner.get("id") or runner.get("fullName") or "")


def _mlb_game_states(game_pk: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw, resolved = _get_bytes(f"{MLB_BASE}/game/{game_pk}/playByPlay")
    payload = json.loads(raw)
    plays = payload.get("allPlays") or []
    if len(plays) < 20:
        return [], {"game_pk": game_pk, "status": "TOO_FEW_PLAYS", "sha256": _sha_bytes(raw)}
    last_result = (plays[-1] or {}).get("result") or {}
    final_home = int(last_result.get("homeScore") or 0)
    final_away = int(last_result.get("awayScore") or 0)
    if final_home == final_away:
        return [], {"game_pk": game_pk, "status": "TIED_OR_INVALID_FINAL", "sha256": _sha_bytes(raw)}
    target = int(final_home > final_away)

    occupied: dict[str, str] = {}
    rows: list[dict[str, Any]] = []
    for index, play in enumerate(plays[:-1]):
        about = play.get("about") or {}
        result = play.get("result") or {}
        count = play.get("count") or {}
        inning = int(about.get("inning") or 0)
        half = str(about.get("halfInning") or "").upper()
        if inning <= 0 or half not in {"TOP", "BOTTOM"}:
            continue

        for movement in play.get("runners") or []:
            if not isinstance(movement, dict):
                continue
            rid = _runner_id(movement)
            move = movement.get("movement") or {}
            start = str(move.get("start") or "").upper()
            end = str(move.get("end") or "").upper()
            if rid and start in {"1B", "2B", "3B"} and occupied.get(start) == rid:
                occupied.pop(start, None)
            if rid:
                for base, current in list(occupied.items()):
                    if current == rid:
                        occupied.pop(base, None)
            if rid and end in {"1B", "2B", "3B"}:
                occupied[end] = rid

        home_score = int(result.get("homeScore") or 0)
        away_score = int(result.get("awayScore") or 0)
        outs = int(count.get("outs") or 0)
        # A third-out plate appearance is timestamped in the half that just
        # ended; the next wagerable state belongs to the next half. Skip that
        # ambiguous transition rather than relabeling it as zero outs.
        if outs >= 3:
            occupied.clear()
            continue
        bottom = int(half == "BOTTOM")
        score_diff = home_score - away_score
        runners_on = int("1B" in occupied) + int("2B" in occupied) + int("3B" in occupied)
        rows.append({
            "game_id": str(game_pk),
            "score_diff": float(score_diff),
            "inning": float(min(inning, 15)),
            "bottom_half": float(bottom),
            "outs": float(outs),
            "base1": float("1B" in occupied),
            "base2": float("2B" in occupied),
            "base3": float("3B" in occupied),
            "offense_home": float(bottom),
            "late_score_pressure": float(score_diff * min(inning, 12) / 9.0),
            "runners_on": float(runners_on),
            "home_win": target,
            "_play_index": index,
        })
    return rows, {
        "game_pk": game_pk,
        "status": "CAPTURED",
        "sha256": _sha_bytes(raw),
        "resolved_url": resolved,
        "state_n": len(rows),
    }


def build_mlb_replay(
    seasons: Iterable[int],
    *,
    max_games_per_season: int = 350,
    workers: int = 8,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    all_rows: list[dict[str, Any]] = []
    season_manifests: list[dict[str, Any]] = []
    for season in sorted({int(v) for v in seasons}):
        pks = _sample_game_pks(_mlb_schedule_game_pks(season), int(max_games_per_season))
        game_manifests: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=max(1, min(int(workers), 12))) as pool:
            futures = {pool.submit(_mlb_game_states, pk): pk for pk in pks}
            for future in as_completed(futures):
                pk = futures[future]
                try:
                    rows, manifest = future.result()
                except Exception as exc:  # noqa: BLE001
                    game_manifests.append({"game_pk": pk, "status": "SOURCE_FAILURE", "error_type": type(exc).__name__})
                    continue
                for row in rows:
                    row["season"] = season
                all_rows.extend(rows)
                game_manifests.append(manifest)
        season_manifests.append({
            "season": season,
            "requested_game_n": len(pks),
            "captured_game_n": sum(1 for x in game_manifests if x.get("status") == "CAPTURED"),
            "failed_game_n": sum(1 for x in game_manifests if x.get("status") == "SOURCE_FAILURE"),
            "manifest_sha256": hashlib.sha256(
                json.dumps(sorted(game_manifests, key=lambda x: x.get("game_pk", 0)), sort_keys=True).encode()
            ).hexdigest(),
        })
    frame = pd.DataFrame(all_rows)
    if frame.empty:
        raise ReplayDataError("MLB_REPLAY_EMPTY")
    frame = frame[["game_id", "season", *MLB_FEATURES, "home_win"]].dropna().reset_index(drop=True)
    manifest = {
        "provider": "MLB_STATS_API_OFFICIAL_PLAY_BY_PLAY",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "seasons": season_manifests,
        "state_sampling": "END_OF_PLATE_APPEARANCE_BEFORE_FINAL_PLAY",
        "row_n": int(len(frame)),
        "game_n": int(frame["game_id"].nunique()),
        "feature_names": list(MLB_FEATURES),
        "external_win_probability_used": False,
        "market_features_used": False,
        "probability_publishable": False,
        "can_execute": False,
    }
    return frame, manifest
