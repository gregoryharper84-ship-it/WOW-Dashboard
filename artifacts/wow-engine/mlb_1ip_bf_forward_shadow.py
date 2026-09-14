"""Automated forward-shadow capture and settlement for MLB 1IP batters faced.

This job is research-only. It discovers official probable starters, hydrates the
same recent-10 BF evidence used by the fitted historical validation, freezes one
immutable pregame prediction per pitcher/event/artifact, and later grades that
prediction from official MLB play-by-play.

It does not publish governed probabilities, rank props, construct cards, or
execute wagers/orders. can_execute is permanently false.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
from supabase import create_client

from mlb_1ip_bf_model import MODEL_FAMILY, RECENT_HISTORY_LIMIT, score_pitcher
from mlb_1ip_live_acquisition import hydrate_mlb_1ip_bf_recent_history
from prop_auto_hydration import MLB_STATS_API_BASE, PropAutoHydrationError, _aware, _int, _request_json

CAN_EXECUTE = False
EXPECTED_ARTIFACT_CHECKSUM = "6dfda83565d5af658408261c49645ee83345afa75a08590d8a2af2dc9a46ae73"
ARTIFACT_PATH = Path(__file__).parent / "model_artifacts" / "mlb_1ip_bf_shadow_candidate_v1.json"
PREDICTION_TABLE = "wow_mlb_1ip_bf_shadow_predictions"
OUTCOME_TABLE = "wow_mlb_1ip_bf_shadow_outcomes"
SOURCE_PROVIDER = "MLB_STATS_API_OFFICIAL_V1"


def _load_artifact(path: Path = ARTIFACT_PATH) -> dict[str, Any]:
    artifact = json.loads(path.read_text())
    if artifact.get("model_family") != MODEL_FAMILY:
        raise RuntimeError("MLB_1IP_BF_ARTIFACT_MODEL_FAMILY_INVALID")
    if artifact.get("artifact_checksum") != EXPECTED_ARTIFACT_CHECKSUM:
        raise RuntimeError("MLB_1IP_BF_ARTIFACT_CHECKSUM_INVALID")
    if int(artifact.get("recent_history_limit") or 0) != RECENT_HISTORY_LIMIT:
        raise RuntimeError("MLB_1IP_BF_ARTIFACT_HISTORY_WINDOW_INVALID")
    if artifact.get("lifecycle_state") != "HISTORICALLY_VALIDATED_FORWARD_SHADOW_REQUIRED":
        raise RuntimeError("MLB_1IP_BF_ARTIFACT_LIFECYCLE_INVALID")
    if artifact.get("probability_publishable") is not False:
        raise RuntimeError("MLB_1IP_BF_ARTIFACT_PUBLICATION_GUARD_INVALID")
    if artifact.get("rank_eligible") is not False or artifact.get("can_execute") is not False:
        raise RuntimeError("MLB_1IP_BF_ARTIFACT_GOVERNANCE_INVALID")
    return artifact


def _client() -> Any:
    url = os.environ["SUPABASE_URL"]
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not key:
        raise RuntimeError("SUPABASE_SERVICE_KEY_MISSING")
    return create_client(url, key)


def _pregame_games(now: datetime, http_get: Callable[..., Any]) -> list[dict[str, Any]]:
    start_date = (now - timedelta(days=1)).date().isoformat()
    end_date = (now + timedelta(days=2)).date().isoformat()
    payload = _request_json(
        f"{MLB_STATS_API_BASE}/schedule",
        params={
            "sportId": "1",
            "startDate": start_date,
            "endDate": end_date,
            "hydrate": "probablePitcher,team",
        },
        http_get=http_get,
    )
    games: list[dict[str, Any]] = []
    for block in payload.get("dates") or []:
        for game in (block or {}).get("games") or []:
            game_pk = _int(game.get("gamePk"))
            if game_pk <= 0:
                continue
            try:
                event_start = _aware(str(game.get("gameDate")))
            except Exception:
                continue
            status = game.get("status") or {}
            abstract = str(status.get("abstractGameState") or "").upper()
            if event_start <= now or abstract in {"FINAL", "LIVE"}:
                continue
            games.append(game)
    return games


def _probable_pitchers(game: dict[str, Any]) -> list[dict[str, Any]]:
    teams = game.get("teams") or {}
    out: list[dict[str, Any]] = []
    for side in ("away", "home"):
        node = teams.get(side) or {}
        probable = node.get("probablePitcher") or {}
        pitcher_id = _int(probable.get("id"))
        name = str(probable.get("fullName") or "").strip()
        if pitcher_id <= 0 or not name:
            continue
        team = node.get("team") or {}
        out.append({
            "side": side.upper(),
            "pitcher_id": pitcher_id,
            "pitcher_name": name,
            "team_id": _int(team.get("id")),
            "team_name": str(team.get("name") or ""),
        })
    return out


def _existing_prediction(client: Any, checksum: str, event_id: str, pitcher_id: int) -> bool:
    response = (
        client.table(PREDICTION_TABLE)
        .select("prediction_id")
        .eq("artifact_checksum", checksum)
        .eq("official_event_id", event_id)
        .eq("pitcher_id", pitcher_id)
        .limit(1)
        .execute()
    )
    return bool(response.data)


def capture_pregame(
    *,
    client: Any,
    artifact: dict[str, Any],
    now: datetime,
    http_get: Callable[..., Any] = httpx.get,
) -> dict[str, int]:
    counters = {
        "games_seen": 0,
        "pitchers_seen": 0,
        "already_frozen": 0,
        "history_insufficient": 0,
        "capture_failed": 0,
        "predictions_frozen": 0,
    }
    games = _pregame_games(now, http_get)
    counters["games_seen"] = len(games)
    checksum = str(artifact["artifact_checksum"])
    alpha = float(artifact["alpha"])
    league_prior = dict(artifact["league_prior"])

    for game in games:
        event_id = str(_int(game.get("gamePk")))
        try:
            event_start = _aware(str(game.get("gameDate")))
        except Exception:
            continue
        for pitcher in _probable_pitchers(game):
            counters["pitchers_seen"] += 1
            pitcher_id = int(pitcher["pitcher_id"])
            if _existing_prediction(client, checksum, event_id, pitcher_id):
                counters["already_frozen"] += 1
                continue
            try:
                evidence = hydrate_mlb_1ip_bf_recent_history(
                    pitcher_id=pitcher_id,
                    event_start_time=event_start.isoformat(),
                    http_get=http_get,
                    now=now,
                )
                counts = {k: int(v) for k, v in (evidence.get("recent_bf_counts") or {}).items()}
                scored = score_pitcher(
                    pitcher_id=pitcher_id,
                    league_prior=league_prior,
                    pitcher_counts={pitcher_id: counts},
                    alpha=alpha,
                )
            except PropAutoHydrationError as exc:
                if exc.code == "MLB_1IP_PRIOR_SAMPLE_INSUFFICIENT":
                    counters["history_insufficient"] += 1
                else:
                    counters["capture_failed"] += 1
                continue
            except Exception:
                counters["capture_failed"] += 1
                continue

            source_ts = str(evidence.get("captured_at") or now.isoformat())
            payload = {
                "official_event_id": event_id,
                "event_start_time": event_start.isoformat(),
                "pitcher_id": pitcher_id,
                "pitcher_name": pitcher["pitcher_name"],
                "model_family": MODEL_FAMILY,
                "artifact_checksum": checksum,
                "recent_history_n": int(scored["pitcher_history_n"]),
                "p_bf_3": float(scored["P_BF_3"]),
                "p_bf_4": float(scored["P_BF_4"]),
                "p_bf_ge_5": float(scored["P_BF_GE_5"]),
                "p_more_3_5": float(scored["P_MORE_3_5"]),
                "p_more_4_5": float(scored["P_MORE_4_5"]),
                "prediction_timestamp": now.isoformat(),
                "source_timestamp": source_ts,
                "source_provider": SOURCE_PROVIDER,
                "source_snapshot": {
                    "side": pitcher["side"],
                    "team_id": pitcher["team_id"],
                    "team_name": pitcher["team_name"],
                    "recent_bf_history": evidence.get("recent_bf_history") or [],
                    "recent_bf_counts": evidence.get("recent_bf_counts") or {},
                    "history_limit": evidence.get("history_limit"),
                },
                "shadow_status": "FROZEN_PREGAME",
                "probability_publishable": False,
                "rank_eligible": False,
                "can_execute": False,
            }
            try:
                client.table(PREDICTION_TABLE).insert(payload).execute()
                counters["predictions_frozen"] += 1
            except Exception:
                # A concurrent cron/retry may win the unique-key race. Recheck
                # before classifying it as a persistence failure.
                if _existing_prediction(client, checksum, event_id, pitcher_id):
                    counters["already_frozen"] += 1
                else:
                    counters["capture_failed"] += 1
    return counters


def _game_status(game_pk: int, http_get: Callable[..., Any]) -> dict[str, Any]:
    payload = _request_json(
        f"{MLB_STATS_API_BASE}/schedule",
        params={"sportId": "1", "gamePk": int(game_pk)},
        http_get=http_get,
    )
    game = (((payload.get("dates") or [{}])[0].get("games") or [{}])[0])
    return game if isinstance(game, dict) else {}


def _half_key(about: dict[str, Any]) -> str:
    raw = str(about.get("halfInning") or "").strip().upper()
    if raw in {"TOP", "BOTTOM"}:
        return raw
    if about.get("isTopInning") is True:
        return "TOP"
    if about.get("isTopInning") is False:
        return "BOTTOM"
    return "UNKNOWN"


def _actual_opener_bf(game_pk: int, pitcher_id: int, http_get: Callable[..., Any]) -> tuple[str, int | None, dict[str, Any]]:
    payload = _request_json(
        f"{MLB_STATS_API_BASE}/game/{int(game_pk)}/playByPlay",
        params={},
        http_get=http_get,
    )
    first_pitcher_by_half: dict[str, int] = {}
    bf_by_half: dict[str, int] = {}
    for play in payload.get("allPlays") or []:
        about = play.get("about") or {}
        matchup = play.get("matchup") or {}
        if _int(about.get("inning")) != 1:
            continue
        half = _half_key(about)
        if half == "UNKNOWN":
            continue
        current_pitcher = _int(((matchup.get("pitcher") or {}).get("id")))
        if current_pitcher <= 0:
            continue
        opener = first_pitcher_by_half.setdefault(half, current_pitcher)
        if current_pitcher == opener:
            bf_by_half[half] = bf_by_half.get(half, 0) + 1

    target_halves = [half for half, pid in first_pitcher_by_half.items() if pid == int(pitcher_id)]
    evidence = {
        "game_pk": int(game_pk),
        "first_pitcher_by_half": first_pitcher_by_half,
        "bf_by_half": bf_by_half,
    }
    if not target_halves:
        return "STARTER_CHANGED", None, evidence
    half = target_halves[0]
    actual_bf = int(bf_by_half.get(half, 0))
    if actual_bf <= 0:
        return "NO_OFFICIAL_BF", None, evidence
    return "GRADED", actual_bf, evidence


def _binary_grade(probability: float, actual: bool) -> tuple[float, float]:
    p = min(1.0 - 1e-12, max(1e-12, float(probability)))
    y = 1.0 if actual else 0.0
    brier = (p - y) ** 2
    log_loss = -(y * math.log(p) + (1.0 - y) * math.log(1.0 - p))
    return brier, log_loss


def _outcome_exists(client: Any, prediction_id: str) -> bool:
    response = (
        client.table(OUTCOME_TABLE)
        .select("outcome_id")
        .eq("prediction_id", prediction_id)
        .limit(1)
        .execute()
    )
    return bool(response.data)


def settle_completed(
    *,
    client: Any,
    artifact: dict[str, Any],
    now: datetime,
    http_get: Callable[..., Any] = httpx.get,
) -> dict[str, int]:
    checksum = str(artifact["artifact_checksum"])
    response = (
        client.table(PREDICTION_TABLE)
        .select("*")
        .eq("artifact_checksum", checksum)
        .lt("event_start_time", now.isoformat())
        .order("event_start_time")
        .limit(200)
        .execute()
    )
    rows = list(response.data or [])
    counters = {
        "predictions_due": len(rows),
        "already_settled": 0,
        "not_final": 0,
        "graded": 0,
        "starter_changed": 0,
        "excluded": 0,
        "settlement_failed": 0,
    }

    for row in rows:
        prediction_id = str(row["prediction_id"])
        if _outcome_exists(client, prediction_id):
            counters["already_settled"] += 1
            continue
        try:
            game_pk = int(row["official_event_id"])
            game = _game_status(game_pk, http_get)
            status = game.get("status") or {}
            abstract = str(status.get("abstractGameState") or "").upper()
            detailed = str(status.get("detailedState") or "").upper()
            if abstract != "FINAL":
                if "POSTPON" in detailed:
                    disposition = "POSTPONED"
                elif "CANCEL" in detailed:
                    disposition = "CANCELED"
                else:
                    counters["not_final"] += 1
                    continue
                actual_bf = None
                settlement_evidence = {"game_status": status}
            else:
                disposition, actual_bf, settlement_evidence = _actual_opener_bf(
                    game_pk,
                    int(row["pitcher_id"]),
                    http_get,
                )

            payload: dict[str, Any] = {
                "prediction_id": prediction_id,
                "disposition": disposition,
                "actual_bf": actual_bf,
                "hit_more_3_5": None,
                "hit_more_4_5": None,
                "brier_3_5": None,
                "log_loss_3_5": None,
                "brier_4_5": None,
                "log_loss_4_5": None,
                "settlement_source": "MLB_STATS_API_OFFICIAL_PLAYBYPLAY_V1",
                "outcome_timestamp": now.isoformat(),
                "settlement_payload": {"game_status": status, "evidence": settlement_evidence},
                "can_execute": False,
            }
            if disposition == "GRADED" and actual_bf is not None:
                hit35 = actual_bf >= 4
                hit45 = actual_bf >= 5
                b35, l35 = _binary_grade(float(row["p_more_3_5"]), hit35)
                b45, l45 = _binary_grade(float(row["p_more_4_5"]), hit45)
                payload.update({
                    "hit_more_3_5": hit35,
                    "hit_more_4_5": hit45,
                    "brier_3_5": b35,
                    "log_loss_3_5": l35,
                    "brier_4_5": b45,
                    "log_loss_4_5": l45,
                })
            client.table(OUTCOME_TABLE).insert(payload).execute()
            if disposition == "GRADED":
                counters["graded"] += 1
            elif disposition == "STARTER_CHANGED":
                counters["starter_changed"] += 1
            else:
                counters["excluded"] += 1
        except Exception:
            counters["settlement_failed"] += 1
    return counters


def run_once(
    *,
    client: Any | None = None,
    now: datetime | None = None,
    http_get: Callable[..., Any] = httpx.get,
) -> dict[str, Any]:
    ts = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    db = client or _client()
    artifact = _load_artifact()
    capture = capture_pregame(client=db, artifact=artifact, now=ts, http_get=http_get)
    settlement = settle_completed(client=db, artifact=artifact, now=ts, http_get=http_get)
    health_response = db.rpc(
        "wow_mlb_1ip_bf_shadow_health",
        {"p_artifact_checksum": artifact["artifact_checksum"]},
    ).execute()
    return {
        "status": "FORWARD_SHADOW_ACTIVE",
        "artifact_checksum": artifact["artifact_checksum"],
        "model_family": artifact["model_family"],
        "capture": capture,
        "settlement": settlement,
        "health": health_response.data,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


if __name__ == "__main__":
    print(json.dumps(run_once(), sort_keys=True, default=str))
