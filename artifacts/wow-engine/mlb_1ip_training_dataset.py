"""Historical MLB 1IP training-row builder from official play-by-play.

The output is research/training data only. It has no publication or execution
authority. Callers are expected to persist immutable source hashes before
training/certification.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable
import hashlib
import json
import httpx

from prop_auto_hydration import MLB_STATS_API_BASE, _int, _request_json
from mlb_1ip_artifact_pipeline import TrainingRow

CAN_EXECUTE = False


def _sha(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def season_game_pks(season: int, *, http_get: Callable[..., Any] = httpx.get) -> list[int]:
    payload = _request_json(
        f"{MLB_STATS_API_BASE}/schedule",
        params={"sportId": "1", "season": str(season), "gameType": "R"},
        http_get=http_get,
    )
    out: list[int] = []
    for block in payload.get("dates") or []:
        for game in (block or {}).get("games") or []:
            pk = _int(game.get("gamePk"))
            status = ((game.get("status") or {}).get("abstractGameState") or "").upper()
            if pk > 0 and status == "FINAL":
                out.append(pk)
    return out


def game_training_rows(game_pk: int, *, http_get: Callable[..., Any] = httpx.get) -> tuple[list[TrainingRow], dict[str, Any]]:
    payload = _request_json(f"{MLB_STATS_API_BASE}/game/{game_pk}/playByPlay", params={}, http_get=http_get)
    by_pitcher: dict[int, dict[str, int]] = {}
    for play in payload.get("allPlays") or []:
        about = play.get("about") or {}
        matchup = play.get("matchup") or {}
        if _int(about.get("inning")) != 1:
            continue
        pitcher_id = _int(((matchup.get("pitcher") or {}).get("id")))
        if pitcher_id <= 0:
            continue
        bucket = by_pitcher.setdefault(pitcher_id, {"bf": 0, "pitches": 0})
        pitches = sum(1 for ev in (play.get("playEvents") or []) if isinstance(ev, dict) and ev.get("isPitch") is True)
        if pitches > 0:
            bucket["bf"] += 1
            bucket["pitches"] += pitches
    rows = [TrainingRow(bf=v["bf"], pitches=v["pitches"]) for v in by_pitcher.values() if v["bf"] >= 3 and v["pitches"] >= 9]
    manifest = {"game_pk": game_pk, "rows": len(rows), "source_sha256": _sha(payload), "captured_at": datetime.now(timezone.utc).isoformat(), "can_execute": False}
    return rows, manifest


def build_season(season: int, *, http_get: Callable[..., Any] = httpx.get) -> tuple[list[TrainingRow], dict[str, Any]]:
    rows: list[TrainingRow] = []
    manifests: list[dict[str, Any]] = []
    for game_pk in season_game_pks(season, http_get=http_get):
        game_rows, manifest = game_training_rows(game_pk, http_get=http_get)
        rows.extend(game_rows)
        manifests.append(manifest)
    return rows, {"season": season, "games": len(manifests), "training_rows": len(rows), "manifest_sha256": _sha(manifests), "probability_publishable": False, "can_execute": False}
