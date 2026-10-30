"""Read-only historical MLB pitcher-strikeout evaluation on official game identities.

This is a RESEARCH-ONLY diagnostic. Settlement-source game IDs are usable only
AFTER events for grouping; they must never hydrate a pregame scoring feature.
All original predictions and authoritative settlements remain immutable.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from math import log, sqrt
import re
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

CAN_EXECUTE = False
MODEL_FAMILY = "MLB_PITCHER_SO_FAILURE_PATH_NB_V1"
_SOURCE_PATH = re.compile(r"^/api/v1(?:\.1)?/game/([0-9]+)/feed/live/?$")
_BAD_EVENT = re.compile(r"synthetic|fixture|probe|canary|test", re.I)


def _time(value: Any) -> datetime | None:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _game_pk(value: Any) -> str | None:
    try:
        uri = urlsplit(str(value))
        match = _SOURCE_PATH.fullmatch(uri.path)
        return match.group(1) if uri.scheme == "https" and uri.hostname == "statsapi.mlb.com" and match else None
    except (TypeError, ValueError):
        return None


def _line(value: Any) -> str | None:
    try:
        number = Decimal(str(value))
        return str(number.normalize()) if number.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def _finite_probability(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if 0 < number < 1 else None


def _row(raw: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Require actual frozen pregame identity, a verified postgame source and one binary settlement."""
    if str(raw.get("sport") or "").upper() != "MLB" or raw.get("model_family") != MODEL_FAMILY:
        return None, "OUT_OF_SCOPE"
    if raw.get("probability_publishable") is not True:
        return None, "ARCHIVED_PUBLICATION_FALSE"
    if raw.get("void") is True or raw.get("push") is True:
        return None, "VOID_OR_PUSH"
    if not isinstance(raw.get("hit"), bool):
        return None, "OUTCOME_UNRESOLVED"
    game_pk = _game_pk(raw.get("settlement_source"))
    if game_pk is None:
        return None, "OFFICIAL_GAME_ID_UNVERIFIED"
    start = _time(raw.get("event_start_time"))
    model = _time(raw.get("model_timestamp"))
    created = _time(raw.get("created_at"))
    settled = _time(raw.get("settlement_timestamp"))
    locked = _time(raw.get("locked_at")) if raw.get("locked_at") is not None else None
    if not all((start, model, created, settled)):
        return None, "TIMESTAMP_UNVERIFIED"
    if model >= start or created >= start or settled < start or (locked is not None and locked >= start):
        return None, "NOT_FROZEN_PREGAME"
    if _BAD_EVENT.search(str(raw.get("event_id") or "")):
        return None, "SYNTHETIC_OR_PROBE"
    player = str(raw.get("player") or "").strip().casefold()
    stat = str(raw.get("stat_type") or "").strip().upper()
    direction = str(raw.get("direction") or "").strip().upper()
    line = _line(raw.get("line"))
    pid = str(raw.get("prediction_id") or "").strip()
    artifact = str(raw.get("model_artifact_version") or "").strip()
    calibrator = str(raw.get("calibration_version") or "").strip()
    probability = _finite_probability(raw.get("calibrated_probability"))
    if not all((player, stat, direction in {"MORE", "LESS"}, line, pid, artifact, calibrator)):
        return None, "EXACT_IDENTITY_UNVERIFIED"
    if probability is None:
        return None, "PROBABILITY_INVALID"
    if stat != "PITCHER_STRIKEOUTS":
        return None, "OUT_OF_SCOPE"
    # A name is only a retrospective surrogate; never assert certified player identity.
    base = (game_pk, player, stat, line, artifact, calibrator)
    thesis = (*base, direction)
    try:
        actual = Decimal(str(raw.get("actual_stat"))) if raw.get("actual_stat") is not None else None
        if actual is not None and not actual.is_finite():
            actual = None
    except InvalidOperation:
        actual = None
    return {
        "thesis": thesis, "base": base, "game_pk": game_pk,
        "event_id": str(raw.get("event_id")), "prediction_id": pid,
        "model_time": model, "created": created, "probability": probability,
        "hit": int(raw["hit"]), "actual": actual,
    }, None


def audit_mlb_strikeout_forecasts(
    records: Iterable[Mapping[str, Any]], *, selection: str = "latest"
) -> dict[str, Any]:
    """Pure audit: every result is advisory and never a governed sporting probability."""
    if selection not in {"earliest", "latest"}:
        raise ValueError("SELECTION_MUST_BE_EARLIEST_OR_LATEST")
    rejected: Counter[str] = Counter()
    eligible = []
    for raw in records:
        parsed, error = _row(raw)
        if error:
            rejected[error] += 1
        else:
            eligible.append(parsed)
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    game_aliases: dict[str, set[str]] = defaultdict(set)
    for r in eligible:
        groups[r["thesis"]].append(r)
        game_aliases[r["game_pk"]].add(r["event_id"])
    # A mismatched official outcome across aliases is a hard hold, never averaged.
    conflicts = [k for k, rows in groups.items()
                 if len({r["hit"] for r in rows}) > 1 or
                 len({r["actual"] for r in rows if r["actual"] is not None}) > 1]
    base_result: dict[str, Any] = {
        "schema_version": "WOW17_CANONICAL_HISTORICAL_RESEARCH_V1",
        "status": "HOLD" if conflicts else "RESEARCH_ONLY",
        "authority": "ADVISORY_ONLY", "certified": False, "can_execute": False,
        "selection": selection, "eligible_forecast_rows": len(eligible),
        "rejected_rows": sum(rejected.values()), "rejection_reasons": dict(sorted(rejected.items())),
        "raw_event_keys": len({r["event_id"] for r in eligible}),
        "official_game_clusters": len(game_aliases),
        "official_games_with_aliases": sum(len(v) > 1 for v in game_aliases.values()),
        "canonical_directional_theses": len(groups),
        "canonical_base_markets": len({r["base"] for r in eligible}),
        "conflicting_official_theses": len(conflicts),
        "identity_level": "OFFICIAL_GAME_ID_AND_PLAYER_NAME_RESEARCH_ONLY",
        "readiness": "FORWARD_CALIBRATION_AND_INDEPENDENT_HOLDOUT_UNVERIFIED",
    }
    if conflicts or not groups:
        if not groups:
            base_result["status"] = "HOLD"
        base_result["blocker"] = "OFFICIAL_SETTLEMENT_CONFLICT" if conflicts else "NO_VERIFIED_ROWS"
        return base_result
    selected = []
    for rows in groups.values():
        ordered = sorted(rows, key=lambda r: (r["model_time"], r["created"], r["prediction_id"]))
        selected.append(ordered[-1] if selection == "latest" else ordered[0])
    losses = [(r["probability"] - r["hit"]) ** 2 for r in selected]
    brier = sum(losses) / len(losses)
    log_loss = sum(-log(r["probability"] if r["hit"] else 1 - r["probability"]) for r in selected) / len(selected)
    gain = 0.25 - brier
    per_game: dict[str, list[float]] = defaultdict(list)
    for r, loss in zip(selected, losses):
        per_game[r["game_pk"]].append(0.25 - loss)
    cluster_se = None
    if len(per_game) > 1:
        n, g = len(selected), len(per_game)
        cluster_se = sqrt(g / (g - 1) * sum(
            (sum(a) - len(a) * gain) ** 2 for a in per_game.values()
        )) / n
    base_result.update({
        "brier_score": brier, "log_loss": log_loss,
        "constant_half_brier": 0.25, "brier_gain_against_constant_half": gain,
        "approx_game_cluster_se": cluster_se,
        "approx_game_cluster_normal_ci95": [gain - 1.96 * cluster_se, gain + 1.96 * cluster_se]
        if cluster_se is not None else None,
        "selected_prediction_ids_sha256": sha256("\n".join(sorted(r["prediction_id"] for r in selected)).encode()).hexdigest(),
        "independence_warning": "MORE_LESS_AND_SAME_GAME_CORRELATED; NOT_AN_INDEPENDENT_BET_CARD",
    })
    return base_result
