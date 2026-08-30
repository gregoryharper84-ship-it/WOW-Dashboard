"""Governed LIVE_UPSET probability runtime for WOW v16 Clean Core.

The lane is probability research only. It never executes wagers or orders and
it fails closed unless an exact LIVE serving champion, safe calibrator,
server-owned immutable live-state snapshot, predictive bounds artifact, and
immutable prediction write all pass.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any, Callable, Literal
from uuid import UUID, uuid4

import numpy as np
from fastapi import FastAPI
from pydantic import BaseModel, Field, field_validator

CAN_EXECUTE = False
LANE = "LIVE_EVENT_PROBABILITY"
MODE = "LIVE_UPSET"
MLB_MODEL_FAMILY = "MLB_LIVE_REMAINING_RUNS_V1"
MLB_FEATURE_SCHEMA = "MLB_LIVE_STATE_FEATURES_V1"
MLB_STATE_SCHEMA = "MLB_LIVE_STATE_V1"
MLB_MARKET_TYPE = "MONEYLINE"
MLB_SETTLEMENT_BASIS = "FULL_GAME_OUTRIGHT"
MLB_SIMULATION_DRAWS = 50_000
MLB_MAX_STATE_AGE_SECONDS = 20
ROLE_MAX_AGE_SECONDS = 30


class LiveScoreRequest(BaseModel):
    research_run_id: str = Field(min_length=1, max_length=128)
    official_event_id: str = Field(min_length=1, max_length=128)
    sport: str = Field(min_length=1, max_length=24)
    league: str = Field(min_length=1, max_length=64)
    exact_selection: str = Field(min_length=1, max_length=128)
    event_status: Literal["IN_PROGRESS", "SCHEDULED", "FINAL", "CANCELLED", "POSTPONED", "SUSPENDED"]
    settlement_rule: str = Field(min_length=1, max_length=512)
    source_snapshot_id: UUID
    live_snapshot_timestamp: datetime
    latest_material_update_at: datetime
    market_role: Literal["UNDERDOG", "FAVORITE", "EVEN", "CONFLICT"]
    market_role_source: str = Field(min_length=1, max_length=256)
    market_role_timestamp: datetime
    market_role_confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("sport")
    @classmethod
    def normalize_sport(cls, value: str) -> str:
        return value.strip().upper()


class LiveScoreResponse(BaseModel):
    research_run_id: str
    official_event_id: str
    sport: str
    exact_selection: str
    mode: str = MODE
    probability_mode: str = "LIVE"
    controlling_specialist: str | None = None
    model_family: str | None = None
    model_version: str | None = None
    calibration_version: str | None = None
    raw_probability: float | None = None
    unconditional_probability: float | None = None
    calibrated_probability: float | None = None
    calibrated_lower_bound: float | None = None
    calibrated_upper_bound: float | None = None
    failure_path_score: float | None = None
    main_failure_path: str | None = None
    upset_tier: str | None = None
    terminal_label: str
    blockers: list[str] = Field(default_factory=list)
    probability_publishable: bool = False
    rank_eligible: bool = False
    prediction_id: UUID | None = None
    model_timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    can_execute: bool = CAN_EXECUTE


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone-aware timestamp required")
    return value.astimezone(timezone.utc)


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return _aware_utc(value)
    return _aware_utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))


def _probability(value: float, name: str) -> float:
    value = float(value)
    if not math.isfinite(value) or not (0.0 < value < 1.0):
        raise ValueError(f"{name} must satisfy 0<p<1")
    return value


def _state_hash(state: dict[str, Any]) -> str:
    encoded = json.dumps(state, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _seed(req: LiveScoreRequest, state_hash: str) -> int:
    key = f"{req.research_run_id}|{req.official_event_id}|{req.exact_selection}|{state_hash}"
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)


def _single_row(result: Any) -> dict[str, Any] | None:
    data = getattr(result, "data", None)
    if isinstance(data, dict):
        return data
    if isinstance(data, list) and data:
        return data[0]
    return None


def _rpc_dict(db: Any, name: str, params: dict[str, Any]) -> dict[str, Any]:
    return _single_row(db.rpc(name, params).execute()) or {}


def _request_blockers(req: LiveScoreRequest, now: datetime) -> list[str]:
    blockers: list[str] = []
    now = _aware_utc(now)
    live_ts = _aware_utc(req.live_snapshot_timestamp)
    material_ts = _aware_utc(req.latest_material_update_at)
    role_ts = _aware_utc(req.market_role_timestamp)
    if req.event_status != "IN_PROGRESS":
        blockers.append("LIVE_EVENT_NOT_IN_PROGRESS")
    if req.sport != "MLB":
        blockers.append("LIVE_SPORT_MODEL_UNAVAILABLE")
    if req.settlement_rule != MLB_SETTLEMENT_BASIS:
        blockers.append("LIVE_SETTLEMENT_RULE_MISMATCH")
    if req.market_role == "CONFLICT":
        blockers.append("FAVORITE_STATUS_CONFLICT")
    elif req.market_role != "UNDERDOG":
        blockers.append("NOT_CURRENT_LIVE_UNDERDOG")
    if req.market_role_confidence < 0.65:
        blockers.append("LIVE_MARKET_ROLE_CONFIDENCE_LOW")
    state_age = (now - live_ts).total_seconds()
    if state_age < -5:
        blockers.append("LIVE_SNAPSHOT_FROM_FUTURE")
    elif state_age > MLB_MAX_STATE_AGE_SECONDS:
        blockers.append("LIVE_STATE_STALE")
    role_age = (now - role_ts).total_seconds()
    if role_age < -5:
        blockers.append("MARKET_ROLE_TIMESTAMP_FROM_FUTURE")
    elif role_age > ROLE_MAX_AGE_SECONDS:
        blockers.append("LIVE_MARKET_ROLE_STALE")
    if live_ts < material_ts:
        blockers.append("LIVE_SNAPSHOT_PREDATES_MATERIAL_UPDATE")
    return blockers


def _load_snapshot(db: Any, req: LiveScoreRequest) -> tuple[dict[str, Any] | None, list[str]]:
    row = _single_row(
        db.table("wow_live_state_snapshots")
        .select("*")
        .eq("source_snapshot_id", str(req.source_snapshot_id))
        .limit(1)
        .execute()
    )
    if not row:
        return None, ["LIVE_STATE_SNAPSHOT_NOT_FOUND"]
    blockers: list[str] = []
    if str(row.get("official_event_id")) != req.official_event_id:
        blockers.append("LIVE_SNAPSHOT_EVENT_ID_MISMATCH")
    if str(row.get("sport", "")).upper() != req.sport:
        blockers.append("LIVE_SNAPSHOT_SPORT_MISMATCH")
    if str(row.get("event_status")) != "IN_PROGRESS":
        blockers.append("LIVE_SNAPSHOT_NOT_IN_PROGRESS")
    if str(row.get("state_schema_version")) != MLB_STATE_SCHEMA:
        blockers.append("LIVE_STATE_SCHEMA_MISMATCH")
    state = row.get("state_json")
    if not isinstance(state, dict):
        return row, blockers + ["LIVE_STATE_JSON_INVALID"]
    if row.get("state_hash") != _state_hash(state):
        blockers.append("LIVE_STATE_HASH_MISMATCH")
    try:
        stored_ts = _parse_timestamp(row.get("snapshot_timestamp"))
        stored_material = _parse_timestamp(row.get("latest_material_update_at"))
    except Exception:
        return row, blockers + ["LIVE_STATE_TIMESTAMP_INVALID"]
    if abs((stored_ts - _aware_utc(req.live_snapshot_timestamp)).total_seconds()) > 1:
        blockers.append("LIVE_SNAPSHOT_TIMESTAMP_MISMATCH")
    if abs((stored_material - _aware_utc(req.latest_material_update_at)).total_seconds()) > 1:
        blockers.append("LIVE_MATERIAL_UPDATE_TIMESTAMP_MISMATCH")
    return row, blockers


def _server_role_blockers(req: LiveScoreRequest, state: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    role = str(state.get("market_role") or "")
    source = str(state.get("market_role_source") or "")
    try:
        confidence = float(state.get("market_role_confidence"))
        timestamp = _parse_timestamp(state.get("market_role_timestamp"))
    except Exception:
        return ["SERVER_LIVE_MARKET_ROLE_EVIDENCE_INVALID"]
    if role != "UNDERDOG":
        blockers.append("SERVER_LIVE_ROLE_NOT_UNDERDOG")
    if role != req.market_role:
        blockers.append("CALLER_SERVER_MARKET_ROLE_MISMATCH")
    if source != req.market_role_source:
        blockers.append("CALLER_SERVER_MARKET_ROLE_SOURCE_MISMATCH")
    if abs(confidence - req.market_role_confidence) > 1e-9:
        blockers.append("CALLER_SERVER_MARKET_ROLE_CONFIDENCE_MISMATCH")
    if abs((timestamp - _aware_utc(req.market_role_timestamp)).total_seconds()) > 1:
        blockers.append("CALLER_SERVER_MARKET_ROLE_TIMESTAMP_MISMATCH")
    return blockers


def _stage_0_5(db: Any, req: LiveScoreRequest, *, model_timestamp: datetime | None) -> dict[str, Any]:
    return _rpc_dict(
        db,
        "wow_mlb_stage_0_5_calibration_precheck",
        {
            "p_model_family": MLB_MODEL_FAMILY,
            "p_feature_schema_version": MLB_FEATURE_SCHEMA,
            "p_market_type": MLB_MARKET_TYPE,
            "p_settlement_basis": MLB_SETTLEMENT_BASIS,
            "p_requested_mode": "LIVE",
            "p_model_timestamp": model_timestamp.isoformat() if model_timestamp else None,
            "p_latest_material_update_at": _aware_utc(req.latest_material_update_at).isoformat(),
        },
    )


def _load_serving_artifact(db: Any, gate: dict[str, Any]) -> dict[str, Any] | None:
    version = gate.get("serving_model_version")
    if not version:
        return None
    row = _single_row(
        db.table("wow_mlb_event_fitted_model_artifacts")
        .select("*")
        .eq("provider_identity", "WOW_MLB_EVENT_FITTED_MODEL_V1")
        .eq("model_family", MLB_MODEL_FAMILY)
        .eq("model_artifact_version", version)
        .eq("sport", "MLB")
        .eq("market_type", MLB_MARKET_TYPE)
        .eq("settlement_basis", MLB_SETTLEMENT_BASIS)
        .eq("serving_mode", "LIVE")
        .eq("feature_schema_version", MLB_FEATURE_SCHEMA)
        .eq("lifecycle_state", "CHAMPION")
        .eq("active", True)
        .eq("promoted", True)
        .limit(1)
        .execute()
    )
    if not row:
        return None
    if row.get("state_schema_version") != MLB_STATE_SCHEMA:
        return None
    if not str(row.get("calibrator_id") or ""):
        return None
    return row


def _snapshot_binding_blockers(snapshot: dict[str, Any], artifact: dict[str, Any], gate: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    expected_version = str(gate.get("serving_model_version") or "")
    if str(snapshot.get("feature_model_family") or "") != MLB_MODEL_FAMILY:
        blockers.append("LIVE_FEATURE_MODEL_FAMILY_MISMATCH")
    if str(snapshot.get("feature_model_artifact_version") or "") != expected_version:
        blockers.append("LIVE_FEATURE_MODEL_VERSION_MISMATCH")
    if str(snapshot.get("feature_schema_version") or "") != MLB_FEATURE_SCHEMA:
        blockers.append("LIVE_FEATURE_SCHEMA_VERSION_MISMATCH")
    if str(snapshot.get("feature_artifact_checksum") or "") != str(artifact.get("artifact_checksum") or ""):
        blockers.append("LIVE_FEATURE_ARTIFACT_CHECKSUM_MISMATCH")
    return blockers


def _load_calibrator(db: Any, artifact: dict[str, Any]) -> dict[str, Any] | None:
    return _single_row(
        db.table("wow_calibrators")
        .select("*")
        .eq("calibrator_id", str(artifact["calibrator_id"]))
        .eq("sport", "MLB")
        .eq("market_family", MLB_MARKET_TYPE)
        .eq("model_family", MLB_MODEL_FAMILY)
        .eq("active", True)
        .eq("promoted", True)
        .eq("validation_status", "PASS")
        .eq("health_status", "PASS")
        .limit(1)
        .execute()
    )


def _validate_mlb_state(state: dict[str, Any]) -> list[str]:
    required = {
        "home_team", "away_team", "home_score", "away_score", "inning", "half", "outs",
        "home_remaining_runs_mean", "away_remaining_runs_mean", "home_extra_inning_win_probability",
    }
    missing = sorted(required.difference(state))
    if missing:
        return ["MISSING_LIVE_STATE:" + ",".join(missing)]
    try:
        inning = int(state["inning"])
        outs = int(state["outs"])
        home_score = int(state["home_score"])
        away_score = int(state["away_score"])
        home_mean = float(state["home_remaining_runs_mean"])
        away_mean = float(state["away_remaining_runs_mean"])
        extra = float(state["home_extra_inning_win_probability"])
    except (TypeError, ValueError):
        return ["INVALID_MLB_LIVE_STATE"]
    if (
        inning < 1
        or outs not in (0, 1, 2)
        or str(state["half"]).upper() not in ("TOP", "BOTTOM")
        or min(home_score, away_score) < 0
        or min(home_mean, away_mean) < 0
        or not 0 < extra < 1
    ):
        return ["INVALID_MLB_LIVE_STATE"]
    provenance = state.get("feature_provenance")
    if not isinstance(provenance, dict):
        return ["LIVE_FEATURE_PROVENANCE_MISSING"]
    for feature in (
        "home_remaining_runs_mean",
        "away_remaining_runs_mean",
        "home_extra_inning_win_probability",
    ):
        if not str(provenance.get(feature, "")).strip():
            return [f"LIVE_FEATURE_PROVENANCE_MISSING:{feature}"]
    return []


def _score_mlb(req: LiveScoreRequest, state: dict[str, Any], state_hash: str) -> dict[str, Any]:
    blockers = _validate_mlb_state(state)
    if blockers:
        return {"blockers": blockers}
    home_team = str(state["home_team"]).strip()
    away_team = str(state["away_team"]).strip()
    selection = req.exact_selection.strip()
    if selection.casefold() not in (home_team.casefold(), away_team.casefold()):
        return {"blockers": ["SELECTION_EVENT_IDENTITY_MISMATCH"]}
    home_score = int(state["home_score"])
    away_score = int(state["away_score"])
    home_mean = float(state["home_remaining_runs_mean"])
    away_mean = float(state["away_remaining_runs_mean"])
    extra_home = float(state["home_extra_inning_win_probability"])
    seed = _seed(req, state_hash)
    rng = np.random.default_rng(seed)
    home_final = home_score + rng.poisson(home_mean, MLB_SIMULATION_DRAWS)
    away_final = away_score + rng.poisson(away_mean, MLB_SIMULATION_DRAWS)
    home_reg_win = float(np.mean(home_final > away_final))
    tie = float(np.mean(home_final == away_final))
    away_reg_win = float(np.mean(home_final < away_final))
    home_win = _probability(
        min(max(home_reg_win + tie * extra_home, 1e-9), 1 - 1e-9),
        "raw_home_probability",
    )
    selected_home = selection.casefold() == home_team.casefold()
    selected = _probability(home_win if selected_home else 1 - home_win, "raw_selection_probability")
    opponent_reg = away_reg_win if selected_home else home_reg_win
    extra_loss = tie * (1 - extra_home if selected_home else extra_home)
    main_failure = (
        "TIE_THEN_EXTRA_INNING_LOSS"
        if extra_loss > opponent_reg
        else "OPPONENT_OUTSCORES_SELECTION_IN_REMAINING_STATE"
    )
    return {
        "blockers": [],
        "raw_probability": selected,
        "unconditional_probability": selected,
        "failure_path_score": 1 - selected,
        "main_failure_path": main_failure,
        "regime_probabilities": {
            "selection_win": selected,
            "opponent_regulation_win_path": float(opponent_reg),
            "tie_then_extra_inning_loss_path": float(extra_loss),
        },
        "simulation_seed": seed,
        "simulation_draws": MLB_SIMULATION_DRAWS,
    }


def _apply_calibrator(calibrator: dict[str, Any], raw_probability: float) -> float:
    """Apply only safe scalar calibration parameters.

    Serialized Python objects are deliberately unsupported at this production
    trust boundary. ISOTONIC_V1 therefore fails closed until a JSON numeric
    representation is separately reviewed and certified.
    """
    method = str(calibrator.get("calibration_method") or "")
    if method != "PLATT_TIME_SPLIT_V1":
        raise ValueError("LIVE_CALIBRATION_METHOD_UNSUPPORTED_SAFE_RUNTIME")
    a = calibrator.get("platt_a")
    b = calibrator.get("platt_b")
    if a is None or b is None:
        raise ValueError("LIVE_CALIBRATOR_PARAMETERS_MISSING")
    p = min(max(float(raw_probability), 1e-9), 1 - 1e-9)
    z = float(a) + float(b) * math.log(p / (1 - p))
    calibrated = 1 / (1 + math.exp(-z))
    return _probability(calibrated, "calibrated_probability")


def _apply_live_bounds(calibrator: dict[str, Any], calibrated: float, state_age_seconds: float) -> tuple[float, float, str]:
    bins = calibrator.get("live_bounds_json")
    if not isinstance(bins, list) or not bins:
        raise ValueError("LIVE_PREDICTIVE_BOUNDS_ARTIFACT_MISSING")
    match: dict[str, Any] | None = None
    for item in bins:
        if not isinstance(item, dict):
            continue
        try:
            p_min = float(item["p_min"])
            p_max = float(item["p_max"])
            max_age = float(item["max_state_age_seconds"])
        except (KeyError, TypeError, ValueError):
            continue
        if p_min <= calibrated <= p_max and state_age_seconds <= max_age:
            match = item
            break
    if match is None:
        raise ValueError("LIVE_PREDICTIVE_BOUNDS_BIN_UNAVAILABLE")
    lower = _probability(max(1e-9, calibrated - float(match["lower_delta"])), "calibrated_lower_bound")
    upper = _probability(min(1 - 1e-9, calibrated + float(match["upper_delta"])), "calibrated_upper_bound")
    if not lower <= calibrated <= upper:
        raise ValueError("LIVE_PREDICTIVE_BOUNDS_ORDER_INVALID")
    return lower, upper, str(match.get("confidence_level") or "LIVE_CALIBRATED")


def _upset_tier(lower: float) -> tuple[str, bool]:
    if lower >= 0.47:
        return "ELITE_UPSET_PROFILE", True
    if lower >= 0.43:
        return "STRONG_UPSET_PROFILE", True
    if lower >= 0.40:
        return "QUALIFIED_UPSET_PROFILE", True
    if lower >= 0.35:
        return "UPSET_WATCH", True
    return "UPSET_REJECT", False


def _insert_prediction(
    db: Any,
    req: LiveScoreRequest,
    state_hash: str,
    artifact: dict[str, Any],
    calibrator: dict[str, Any],
    score: dict[str, Any],
    calibrated: float,
    lower: float,
    upper: float,
    tier: str,
    scored_at: datetime,
) -> UUID:
    existing = _single_row(
        db.table("wow_live_probability_predictions")
        .select("prediction_id,exact_selection")
        .eq("research_run_id", req.research_run_id)
        .eq("official_event_id", req.official_event_id)
        .eq("mode", MODE)
        .limit(1)
        .execute()
    )
    if existing:
        raise ValueError("DUPLICATE_LIVE_EVENT_THESIS")
    prediction_id = uuid4()
    payload = {
        "prediction_id": str(prediction_id),
        "research_run_id": req.research_run_id,
        "official_event_id": req.official_event_id,
        "sport": req.sport,
        "league": req.league,
        "exact_selection": req.exact_selection,
        "mode": MODE,
        "event_status": "IN_PROGRESS",
        "settlement_rule": MLB_SETTLEMENT_BASIS,
        "source_snapshot_id": str(req.source_snapshot_id),
        "live_snapshot_timestamp": _aware_utc(req.live_snapshot_timestamp).isoformat(),
        "state_schema_version": MLB_STATE_SCHEMA,
        "state_hash": state_hash,
        "market_role": "UNDERDOG",
        "market_role_source": req.market_role_source,
        "market_role_timestamp": _aware_utc(req.market_role_timestamp).isoformat(),
        "market_role_confidence": req.market_role_confidence,
        "model_family": MLB_MODEL_FAMILY,
        "model_version": artifact["model_artifact_version"],
        "calibration_method": calibrator["calibration_method"],
        "calibration_version": calibrator["calibration_version"],
        "raw_probability": score["raw_probability"],
        "unconditional_probability": score["unconditional_probability"],
        "calibrated_probability": calibrated,
        "lower_bound": lower,
        "upper_bound": upper,
        "failure_path_score": score["failure_path_score"],
        "main_failure_path": score["main_failure_path"],
        "regime_probabilities_json": score["regime_probabilities"],
        "simulation_seed": score["simulation_seed"],
        "simulation_draws": score["simulation_draws"],
        "terminal_label": "MODEL_QUALIFIED_HOLD",
        "upset_tier": tier,
        "probability_publishable": True,
        "model_timestamp": scored_at.isoformat(),
        "can_execute": False,
    }
    result = db.table("wow_live_probability_predictions").insert(payload).execute()
    if not getattr(result, "data", None):
        raise RuntimeError("LIVE_IMMUTABLE_PREDICTION_WRITE_FAILED")
    return prediction_id


def _held(req: LiveScoreRequest, now: datetime, blockers: list[str], *, model_unavailable: bool = False, **extra: Any) -> LiveScoreResponse:
    return LiveScoreResponse(
        research_run_id=req.research_run_id,
        official_event_id=req.official_event_id,
        sport=req.sport,
        exact_selection=req.exact_selection,
        controlling_specialist="wow.mlb-live-game-win-probability-expert" if req.sport == "MLB" else None,
        model_family=MLB_MODEL_FAMILY if req.sport == "MLB" else None,
        terminal_label="MODEL_UNAVAILABLE" if model_unavailable else "RESEARCH_INTEREST",
        blockers=blockers,
        probability_publishable=False,
        rank_eligible=False,
        model_timestamp=now,
        can_execute=False,
        **extra,
    )


def score_live_event(req: LiveScoreRequest, db: Any, now: datetime | None = None) -> LiveScoreResponse:
    now = _aware_utc(now or datetime.now(timezone.utc))
    blockers = _request_blockers(req, now)
    if blockers:
        return _held(req, now, blockers, model_unavailable="LIVE_SPORT_MODEL_UNAVAILABLE" in blockers)

    snapshot, snapshot_blockers = _load_snapshot(db, req)
    if snapshot is None or snapshot_blockers:
        return _held(req, now, snapshot_blockers)
    state = snapshot["state_json"]
    role_blockers = _server_role_blockers(req, state)
    if role_blockers:
        return _held(req, now, role_blockers)
    state_hash = _state_hash(state)

    gate = _stage_0_5(db, req, model_timestamp=None)
    if not bool(gate.get("probability_publishable")) or gate.get("calibration_precheck_status") != "PASS":
        return _held(
            req,
            now,
            list(gate.get("blockers") or ["NO_VALID_CERTIFIED_LIVE_CHAMPION"]),
            model_unavailable=True,
        )
    artifact = _load_serving_artifact(db, gate)
    if artifact is None:
        return _held(req, now, ["CERTIFIED_LIVE_MODEL_ARTIFACT_UNAVAILABLE_OR_SCHEMA_MISMATCH"], model_unavailable=True)
    binding_blockers = _snapshot_binding_blockers(snapshot, artifact, gate)
    if binding_blockers:
        return _held(req, now, binding_blockers, model_unavailable=True, model_version=artifact.get("model_artifact_version"))
    calibrator = _load_calibrator(db, artifact)
    if calibrator is None or calibrator.get("calibration_version") != gate.get("serving_calibration_version"):
        return _held(req, now, ["SERVING_CALIBRATION_INVALID_OR_UNAVAILABLE"], model_unavailable=True)

    score = _score_mlb(req, state, state_hash)
    if score.get("blockers"):
        return _held(req, now, list(score["blockers"]), model_unavailable=True, model_version=artifact.get("model_artifact_version"))
    try:
        calibrated = _apply_calibrator(calibrator, score["raw_probability"])
        state_age = max(0.0, (now - _aware_utc(req.live_snapshot_timestamp)).total_seconds())
        lower, upper, _ = _apply_live_bounds(calibrator, calibrated, state_age)
    except (TypeError, ValueError, OverflowError) as exc:
        return _held(
            req,
            now,
            [str(exc)],
            model_unavailable=True,
            model_version=artifact.get("model_artifact_version"),
            calibration_version=calibrator.get("calibration_version"),
        )

    final_gate = _stage_0_5(db, req, model_timestamp=now)
    if (
        not bool(final_gate.get("probability_publishable"))
        or final_gate.get("calibration_precheck_status") != "PASS"
        or final_gate.get("serving_model_version") != gate.get("serving_model_version")
        or final_gate.get("serving_calibration_version") != gate.get("serving_calibration_version")
    ):
        return _held(
            req,
            now,
            list(final_gate.get("blockers") or ["LIVE_SERVING_STATE_CHANGED_RERUN_REQUIRED"]),
            model_unavailable=True,
            model_version=artifact.get("model_artifact_version"),
            calibration_version=calibrator.get("calibration_version"),
        )

    tier, rank_eligible = _upset_tier(lower)
    try:
        prediction_id = _insert_prediction(
            db, req, state_hash, artifact, calibrator, score, calibrated, lower, upper, tier, now
        )
    except ValueError as exc:
        return _held(req, now, [str(exc)])
    except Exception:
        return _held(req, now, ["LIVE_IMMUTABLE_PREDICTION_WRITE_FAILED"])

    return LiveScoreResponse(
        research_run_id=req.research_run_id,
        official_event_id=req.official_event_id,
        sport=req.sport,
        exact_selection=req.exact_selection,
        controlling_specialist="wow.mlb-live-game-win-probability-expert",
        model_family=MLB_MODEL_FAMILY,
        model_version=artifact["model_artifact_version"],
        calibration_version=calibrator["calibration_version"],
        raw_probability=score["raw_probability"],
        unconditional_probability=score["unconditional_probability"],
        calibrated_probability=calibrated,
        calibrated_lower_bound=lower,
        calibrated_upper_bound=upper,
        failure_path_score=score["failure_path_score"],
        main_failure_path=score["main_failure_path"],
        upset_tier=tier,
        terminal_label="MODEL_QUALIFIED_HOLD" if rank_eligible else "RESEARCH_INTEREST",
        blockers=[],
        probability_publishable=True,
        rank_eligible=rank_eligible,
        prediction_id=prediction_id,
        model_timestamp=now,
        can_execute=False,
    )


def live_probability_health(db: Any) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    dummy = LiveScoreRequest(
        research_run_id="health",
        official_event_id="health",
        sport="MLB",
        league="MLB",
        exact_selection="health",
        event_status="IN_PROGRESS",
        settlement_rule=MLB_SETTLEMENT_BASIS,
        source_snapshot_id=UUID("00000000-0000-0000-0000-000000000001"),
        live_snapshot_timestamp=now,
        latest_material_update_at=now,
        market_role="UNDERDOG",
        market_role_source="health",
        market_role_timestamp=now,
        market_role_confidence=1.0,
    )
    capabilities: dict[str, Any] = {}
    try:
        gate = _stage_0_5(db, dummy, model_timestamp=None)
        available = bool(gate.get("probability_publishable")) and gate.get("calibration_precheck_status") == "PASS"
        capabilities["MLB"] = {
            "status": "AVAILABLE" if available else "MODEL_UNAVAILABLE",
            "model_family": MLB_MODEL_FAMILY,
            "model_version": gate.get("serving_model_version"),
            "calibration_version": gate.get("serving_calibration_version"),
            "blockers": [] if available else list(gate.get("blockers") or ["NO_VALID_CERTIFIED_LIVE_CHAMPION"]),
        }
    except Exception:
        capabilities["MLB"] = {
            "status": "MODEL_UNAVAILABLE",
            "model_family": MLB_MODEL_FAMILY,
            "blockers": ["LIVE_STAGE_0_5_UNAVAILABLE"],
        }
    for sport in ("NBA", "WNBA", "NCAAB", "NHL", "SOCCER", "TENNIS", "NFL", "NCAAF", "GOLF", "MMA", "BOXING"):
        capabilities[sport] = {
            "status": "MODEL_UNAVAILABLE",
            "blockers": ["CERTIFIED_LIVE_SPORT_MODEL_NOT_YET_WIRED"],
        }
    return {
        "status": "OK",
        "lane": LANE,
        "mode": MODE,
        "capabilities": capabilities,
        "probability_publishable": any(v["status"] == "AVAILABLE" for v in capabilities.values()),
        "can_execute": False,
    }


def _has_route(app: FastAPI, path: str, method: str) -> bool:
    method = method.upper()
    return any(
        getattr(route, "path", None) == path
        and method in (getattr(route, "methods", set()) or set())
        for route in app.router.routes
    )


def install_live_probability_routes(
    app: FastAPI,
    *,
    auth_dependency: Any,
    db_client_fn: Callable[[], Any],
) -> None:
    """Idempotently install the two authenticated LIVE probability routes."""
    if not _has_route(app, "/live-probability/health", "GET"):
        @app.get(
            "/live-probability/health",
            dependencies=[auth_dependency],
            operation_id="getWowLiveProbabilityHealth",
        )
        def get_live_probability_health():
            return live_probability_health(db_client_fn())

    if not _has_route(app, "/score-live-event", "POST"):
        @app.post(
            "/score-live-event",
            response_model=LiveScoreResponse,
            dependencies=[auth_dependency],
            operation_id="scoreWowLiveEvent",
        )
        def post_score_live_event(req: LiveScoreRequest):
            return score_live_event(req, db_client_fn())

    app.openapi_schema = None
