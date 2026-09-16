"""Server-owned immutable forward evidence collector for Fantasy Score candidates.

This collector records pregame candidate forecasts for later settlement/calibration.
It does not fit, certify, promote, rank, publish, or execute a wager.  Candidate
packages are accepted only when the exact lane, controlling specialist, model
source hash, scoring-profile identity, simulation floor, and pregame timestamp are
present. Legacy/shadow/generic projections cannot enter the governed evidence set.

Both MORE and LESS may be persisted for audit. Readiness counts unique source
snapshots, not directional twins, so one underlying game/line cannot double the
200/500 calibration sample thresholds.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any, Callable
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from calibration import PHASE_B_MIN_N, PHASE_C_MIN_N

CAN_EXECUTE = False
PROVIDER = "WOW_FANTASY_SCORE_CANDIDATE_V1"
EVIDENCE_SOURCE_KIND = "IMMUTABLE_PREGAME_SETTLED"
DIRECTIONS = ("MORE", "LESS")
PAGE_SIZE = 1000
IN_FILTER_CHUNK_SIZE = 200
MIN_SIMULATIONS = 50_000


@dataclass(frozen=True)
class FantasyScoreLaneSpec:
    lane: str
    sport: str
    stat_type: str
    market_family: str
    controlling_specialist: str


LANE_SPECS: dict[str, FantasyScoreLaneSpec] = {
    "NFL": FantasyScoreLaneSpec(
        "NFL", "NFL", "FANTASY_SCORE", "NFL_DFS_FANTASY_SCORE",
        "wow.nfl-dfs-fantasy-score-expert",
    ),
    "NBA": FantasyScoreLaneSpec(
        "NBA", "NBA", "FANTASY_SCORE", "NBA_FANTASY_SCORE",
        "wow.nba-dfs-fantasy-score-expert",
    ),
    "WNBA": FantasyScoreLaneSpec(
        "WNBA", "WNBA", "FANTASY_SCORE", "WNBA_FANTASY_SCORE",
        "wow.wnba-dfs-fantasy-score-expert",
    ),
    "MLB_HITTER": FantasyScoreLaneSpec(
        "MLB_HITTER", "MLB", "HITTER_FANTASY_SCORE", "MLB_HITTER_FANTASY_SCORE",
        "wow.mlb-hitter-fantasy-score-expert",
    ),
    "MLB_PITCHER": FantasyScoreLaneSpec(
        "MLB_PITCHER", "MLB", "PITCHER_FANTASY_SCORE", "MLB_PITCHER_FANTASY_SCORE",
        "wow.mlb-pitcher-fantasy-score-expert",
    ),
}


class FantasyScoreForwardBoundaryError(RuntimeError):
    def __init__(self, boundary: str, error: BaseException) -> None:
        super().__init__(f"{boundary}: {type(error).__name__}")
        self.boundary = boundary
        self.error_type = type(error).__name__
        self.__cause__ = error


class FantasyScoreForwardCohortRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lanes: list[str] = Field(default_factory=lambda: list(LANE_SPECS))
    max_snapshots_per_lane: int = Field(default=25, ge=1, le=100)


def _db_call(boundary: str, call: Callable[[], Any]) -> Any:
    try:
        return call()
    except FantasyScoreForwardBoundaryError:
        raise
    except Exception as exc:
        raise FantasyScoreForwardBoundaryError(boundary, exc) from exc


def _paginate(boundary: str, build: Callable[[], Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start = 0
    while True:
        page = _db_call(
            boundary,
            lambda start=start: build().range(start, start + PAGE_SIZE - 1).execute().data or [],
        )
        batch = [dict(row) for row in page]
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            return rows
        start += PAGE_SIZE


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[i:i + size] for i in range(0, len(values), size)]


def _aware(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _sha256(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        return None
    return text


def _normalized_lanes(values: list[str]) -> list[str]:
    lanes: list[str] = []
    for raw in values:
        lane = str(raw or "").strip().upper()
        if lane not in LANE_SPECS:
            raise ValueError(f"unsupported Fantasy Score forward lane: {lane or '<missing>'}")
        if lane not in lanes:
            lanes.append(lane)
    if not lanes:
        raise ValueError("at least one Fantasy Score lane is required")
    return lanes


def _eligible_snapshots(db: Any, spec: FantasyScoreLaneSpec, limit: int, *, now: datetime) -> list[dict[str, Any]]:
    rows = _db_call(
        f"wow_prop_evidence_snapshots.select_fantasy_{spec.lane.lower()}",
        lambda: db.table("wow_prop_evidence_snapshots")
        .select(
            "source_snapshot_id,captured_at,event_id,event_start_time,sport,player,team,opponent,"
            "stat_type,line,hydration_status,blockers"
        )
        .eq("sport", spec.sport)
        .eq("stat_type", spec.stat_type)
        .eq("hydration_status", "PASS")
        .gt("event_start_time", now.isoformat())
        .order("event_start_time")
        .limit(limit * 3)
        .execute().data or [],
    )
    selected: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        captured = _aware(row.get("captured_at"))
        event_start = _aware(row.get("event_start_time"))
        if row.get("blockers") or not row.get("source_snapshot_id"):
            continue
        if captured is None or event_start is None or captured >= event_start or event_start <= now:
            continue
        selected.append(row)
        if len(selected) >= limit:
            break
    return selected


def _existing_keys(db: Any, spec: FantasyScoreLaneSpec) -> set[tuple[str, str]]:
    rows = _paginate(
        f"wow_predictions.select_fantasy_forward_{spec.lane.lower()}",
        lambda: db.table("wow_predictions")
        .select("source_snapshot_id,direction")
        .eq("fantasy_score_lane", spec.lane)
        .eq("market_family", spec.market_family)
        .eq("evidence_source_kind", EVIDENCE_SOURCE_KIND),
    )
    return {
        (str(row["source_snapshot_id"]), str(row["direction"]).upper())
        for row in rows
        if row.get("source_snapshot_id") and str(row.get("direction") or "").upper() in DIRECTIONS
    }


def _candidate_output(scored: dict[str, Any]) -> dict[str, Any]:
    for key in ("prediction", "research_model_output", "candidate_model_output"):
        value = scored.get(key)
        if isinstance(value, dict):
            return dict(value)
    return dict(scored)


def _prob(output: dict[str, Any], canonical: str, *aliases: str) -> float | None:
    for key in (canonical, *aliases):
        if output.get(key) is not None:
            return _finite(output.get(key))
    return None


def _prediction_id(lane: str, snapshot_id: str, direction: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"wow-v17-fantasy-forward:{lane}:{snapshot_id}:{direction}"))


def _build_prediction_payload(
    *,
    spec: FantasyScoreLaneSpec,
    snapshot: dict[str, Any],
    direction: str,
    scored: dict[str, Any],
    now: datetime,
) -> tuple[dict[str, Any] | None, list[str]]:
    output = _candidate_output(scored)
    blockers: list[str] = []

    if str(output.get("market_family") or "").upper() != spec.market_family:
        blockers.append("FANTASY_SCORE_MARKET_FAMILY_MISMATCH")
    if str(output.get("controlling_specialist") or "") != spec.controlling_specialist:
        blockers.append("FANTASY_SCORE_CONTROLLING_SPECIALIST_MISMATCH")

    model_version = str(output.get("model_version") or "").strip()
    model_source_sha = _sha256(output.get("model_source_sha256"))
    scoring_profile_id = str(output.get("scoring_profile_id") or "").strip()
    scoring_profile_sha = _sha256(output.get("scoring_profile_sha256"))
    if not model_version:
        blockers.append("FANTASY_SCORE_MODEL_VERSION_MISSING")
    if model_source_sha is None:
        blockers.append("FANTASY_SCORE_MODEL_SOURCE_HASH_MISSING_OR_INVALID")
    if not scoring_profile_id:
        blockers.append("FANTASY_SCORE_SCORING_PROFILE_ID_MISSING")
    if scoring_profile_sha is None:
        blockers.append("FANTASY_SCORE_SCORING_PROFILE_HASH_MISSING_OR_INVALID")

    simulations = int(output.get("simulation_count") or output.get("simulation_draws") or 0)
    if simulations < MIN_SIMULATIONS:
        blockers.append("FANTASY_SCORE_SIMULATION_FLOOR_NOT_MET")

    p_more = _prob(output, "P(MORE)", "probability_more", "raw_probability_more")
    p_less = _prob(output, "P(LESS)", "probability_less", "raw_probability_less")
    p_push = _prob(output, "P(PUSH)", "push_probability")
    if any(value is None or not 0.0 <= float(value) <= 1.0 for value in (p_more, p_less, p_push)):
        blockers.append("FANTASY_SCORE_DIRECTIONAL_PROBABILITIES_INVALID")
    elif abs(float(p_more) + float(p_less) + float(p_push) - 1.0) > 1e-8:
        blockers.append("FANTASY_SCORE_DIRECTIONAL_PROBABILITIES_NOT_NORMALIZED")

    raw_probability = _prob(output, "raw_candidate_probability", "raw_model_probability")
    expected = p_more if direction == "MORE" else p_less
    if raw_probability is None and expected is not None:
        raw_probability = expected
    if raw_probability is None or not 0.0 < float(raw_probability) < 1.0:
        blockers.append("FANTASY_SCORE_RAW_SIDE_PROBABILITY_INVALID")
    elif expected is not None and abs(float(raw_probability) - float(expected)) > 1e-8:
        blockers.append("FANTASY_SCORE_RAW_SIDE_PROBABILITY_MISMATCH")

    model_timestamp = _aware(
        output.get("model_timestamp") or scored.get("model_timestamp") or scored.get("scored_at")
    )
    event_start = _aware(snapshot.get("event_start_time"))
    captured_at = _aware(snapshot.get("captured_at"))
    if model_timestamp is None:
        blockers.append("FANTASY_SCORE_MODEL_TIMESTAMP_MISSING")
    if event_start is None or captured_at is None:
        blockers.append("FANTASY_SCORE_EVENT_OR_CAPTURE_TIMESTAMP_INVALID")
    elif captured_at >= event_start or now >= event_start:
        blockers.append("FANTASY_SCORE_NOT_PREGAME")
    if model_timestamp is not None and event_start is not None and model_timestamp >= event_start:
        blockers.append("FANTASY_SCORE_MODEL_TIMESTAMP_NOT_PREGAME")

    if output.get("probability_publishable") is not False:
        blockers.append("FANTASY_SCORE_CANDIDATE_MUST_BE_NONPUBLISHABLE")
    if output.get("rank_eligible") is not False:
        blockers.append("FANTASY_SCORE_CANDIDATE_MUST_BE_NONRANKABLE")
    if output.get("can_execute") is not False:
        blockers.append("FANTASY_SCORE_CAN_EXECUTE_MUST_BE_FALSE")

    fantasy_position = str(output.get("position") or scored.get("position") or "").strip().upper() or None
    if spec.lane == "NFL" and fantasy_position not in {"QB", "RB", "WR", "TE"}:
        blockers.append("NFL_FANTASY_POSITION_MISSING_OR_INVALID")

    if blockers:
        return None, blockers

    source_snapshot_id = str(snapshot["source_snapshot_id"])
    payload = {
        "prediction_id": _prediction_id(spec.lane, source_snapshot_id, direction),
        "event_id": snapshot["event_id"],
        "event_start_time": snapshot["event_start_time"],
        "model_timestamp": model_timestamp.isoformat(),
        "player": snapshot.get("player"),
        "team": snapshot.get("team"),
        "opponent": snapshot.get("opponent"),
        "sport": spec.sport,
        "market_type": "FANTASY_SCORE_CANDIDATE",
        "stat_type": spec.stat_type,
        "line": float(snapshot["line"]),
        "direction": direction,
        "source_snapshot_id": source_snapshot_id,
        "simulation_seed": int(output.get("seed") or 0),
        "simulation_draws": simulations,
        "raw_model_probability": float(raw_probability),
        "probability_more": float(p_more),
        "probability_less": float(p_less),
        "push_probability": float(p_push),
        "model_provider_identity": PROVIDER,
        "model_family": spec.market_family,
        "model_artifact_version": model_version,
        "model_artifact_checksum": model_source_sha,
        "fantasy_score_lane": spec.lane,
        "market_family": spec.market_family,
        "controlling_specialist": spec.controlling_specialist,
        "scoring_profile_id": scoring_profile_id,
        "scoring_profile_sha256": scoring_profile_sha,
        "evidence_source_kind": EVIDENCE_SOURCE_KIND,
        "fantasy_position": fantasy_position,
        "calibration_status": "CANDIDATE_UNCALIBRATED",
        "calibrated_probability": None,
        "calibrated_probability_lower_bound": None,
        "calibrated_probability_upper_bound": None,
        "probability_publishable": False,
        "probability_ceiling": "CALIBRATION_BLOCKED_NO_PUBLISH",
        "money_lane_status": "PAYOUT_UNRESOLVED",
        "data_gaps": [],
        "blockers": list(dict.fromkeys([
            *(output.get("blockers") or []),
            "FANTASY_SCORE_FORWARD_EVIDENCE_ONLY",
        ])),
        "locked_at": now.isoformat(),
    }
    return payload, []


def _persist_prediction(db: Any, payload: dict[str, Any]) -> None:
    _db_call(
        "wow_predictions.upsert_fantasy_forward_prediction",
        lambda: db.table("wow_predictions").upsert(
            payload, on_conflict="prediction_id", ignore_duplicates=True
        ).execute(),
    )


def _lane_predictions(db: Any, spec: FantasyScoreLaneSpec) -> list[dict[str, Any]]:
    return _paginate(
        f"wow_predictions.select_fantasy_evidence_{spec.lane.lower()}",
        lambda: db.table("wow_predictions")
        .select("prediction_id,source_snapshot_id,event_start_time,model_timestamp,direction")
        .eq("fantasy_score_lane", spec.lane)
        .eq("market_family", spec.market_family)
        .eq("evidence_source_kind", EVIDENCE_SOURCE_KIND),
    )


def _settled_ids(db: Any, prediction_ids: list[str]) -> set[str]:
    settled: set[str] = set()
    for chunk in _chunks(prediction_ids, IN_FILTER_CHUNK_SIZE):
        rows = _paginate(
            "wow_outcomes.select_fantasy_settled_predictions",
            lambda chunk=chunk: db.table("wow_outcomes")
            .select("prediction_id,actual_stat,settlement_timestamp,void")
            .in_("prediction_id", chunk),
        )
        settled.update(
            str(row["prediction_id"])
            for row in rows
            if row.get("prediction_id")
            and row.get("actual_stat") is not None
            and row.get("settlement_timestamp") is not None
            and row.get("void") is not True
        )
    return settled


def _lane_readiness(db: Any, spec: FantasyScoreLaneSpec) -> dict[str, Any]:
    predictions = _lane_predictions(db, spec)
    ids = [str(row["prediction_id"]) for row in predictions if row.get("prediction_id")]
    settled = _settled_ids(db, ids) if ids else set()
    forward_sources = {str(row["source_snapshot_id"]) for row in predictions if row.get("source_snapshot_id")}
    settled_sources = {
        str(row["source_snapshot_id"])
        for row in predictions
        if row.get("source_snapshot_id") and str(row.get("prediction_id")) in settled
    }
    settled_n = len(settled_sources)
    status = "PHASE_A_FORWARD_COHORT_BUILDING"
    if settled_n >= PHASE_C_MIN_N:
        status = "PHASE_C_THRESHOLD_REACHED_CALIBRATION_EVIDENCE_BUILD_REQUIRED"
    elif settled_n >= PHASE_B_MIN_N:
        status = "PHASE_B_THRESHOLD_REACHED_CALIBRATION_EVIDENCE_BUILD_REQUIRED"
    return {
        "lane": spec.lane,
        "status": status,
        "forward_prediction_source_n": len(forward_sources),
        "forward_settled_source_n": settled_n,
        "phase_b_min_settled_n": PHASE_B_MIN_N,
        "phase_c_min_settled_n": PHASE_C_MIN_N,
        "remaining_to_phase_b": max(0, PHASE_B_MIN_N - settled_n),
        "remaining_to_phase_c": max(0, PHASE_C_MIN_N - settled_n),
        "counting_basis": "UNIQUE_SOURCE_SNAPSHOT",
        "calibrator_fit_performed": False,
        "certification_performed": False,
        "promotion_performed": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def run_fantasy_score_forward_cohort(
    req: FantasyScoreForwardCohortRequest,
    *,
    db: Any,
    market_api: Any,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    lanes = _normalized_lanes(req.lanes)
    lane_results: list[dict[str, Any]] = []

    for lane in lanes:
        spec = LANE_SPECS[lane]
        snapshots = _eligible_snapshots(db, spec, req.max_snapshots_per_lane, now=now)
        existing = _existing_keys(db, spec)
        rows: list[dict[str, Any]] = []

        for snapshot in snapshots:
            for direction in DIRECTIONS:
                key = (str(snapshot["source_snapshot_id"]), direction)
                if key in existing:
                    rows.append({
                        "source_snapshot_id": key[0], "direction": direction,
                        "status": "SKIPPED_ALREADY_CAPTURED", "can_execute": False,
                    })
                    continue
                identity = {
                    "event_id": snapshot.get("event_id"),
                    "event_start_time": snapshot.get("event_start_time"),
                    "sport": spec.sport,
                    "player": snapshot.get("player"),
                    "team": snapshot.get("team"),
                    "opponent": snapshot.get("opponent"),
                    "stat_type": spec.stat_type,
                    "line": snapshot.get("line"),
                    "source_snapshot_id": snapshot.get("source_snapshot_id"),
                    "direction": direction,
                }
                try:
                    scored = market_api.score_prop(
                        market_api.ScorePropRequest(**identity), "WOW_BETTING_ENGINE"
                    )
                except HTTPException as exc:
                    detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
                    rows.append({
                        "source_snapshot_id": key[0], "direction": direction,
                        "status": "HELD_CANDIDATE_SCORER",
                        "detail": detail,
                        "probability_publishable": False,
                        "can_execute": False,
                    })
                    continue
                except Exception as exc:
                    rows.append({
                        "source_snapshot_id": key[0], "direction": direction,
                        "status": "HELD_CANDIDATE_SCORER_EXCEPTION",
                        "error_type": type(exc).__name__,
                        "probability_publishable": False,
                        "can_execute": False,
                    })
                    continue

                payload, package_blockers = _build_prediction_payload(
                    spec=spec,
                    snapshot=snapshot,
                    direction=direction,
                    scored=dict(scored),
                    now=now,
                )
                if payload is None:
                    rows.append({
                        "source_snapshot_id": key[0], "direction": direction,
                        "status": "HELD_FANTASY_SCORE_PACKAGE_INVALID",
                        "blockers": package_blockers,
                        "probability_publishable": False,
                        "can_execute": False,
                    })
                    continue
                _persist_prediction(db, payload)
                existing.add(key)
                rows.append({
                    "source_snapshot_id": key[0],
                    "direction": direction,
                    "prediction_id": payload["prediction_id"],
                    "status": "CAPTURED_FORWARD",
                    "probability_publishable": False,
                    "rank_eligible": False,
                    "can_execute": False,
                })

        expected = len(snapshots) * len(DIRECTIONS)
        completed = sum(row["status"] == "CAPTURED_FORWARD" for row in rows)
        skipped = sum(row["status"] == "SKIPPED_ALREADY_CAPTURED" for row in rows)
        held = len(rows) - completed - skipped
        balanced = len(rows) == expected == completed + skipped + held
        lane_results.append({
            "lane": lane,
            "snapshots_considered": len(snapshots),
            "directions_considered": expected,
            "captured_forward_predictions": completed,
            "skipped_already_captured": skipped,
            "held": held,
            "rows": rows,
            "row_reconciliation": {
                "rows_in": expected,
                "rows_completed": completed,
                "rows_skipped_already_captured": skipped,
                "rows_held": held,
                "balanced": balanced,
                "can_execute": False,
            },
            "calibration_readiness": _lane_readiness(db, spec),
            "can_execute": False,
        })

    balanced = all(item["row_reconciliation"]["balanced"] for item in lane_results)
    return {
        "terminal": True,
        "run_status": "COMPLETED" if balanced else "RECONCILIATION_FAILED",
        "evidence_source_kind": EVIDENCE_SOURCE_KIND,
        "lanes": lane_results,
        "calibrator_fit_performed": False,
        "certification_performed": False,
        "promotion_performed": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "EVIDENCE_SOURCE_KIND",
    "FantasyScoreForwardBoundaryError",
    "FantasyScoreForwardCohortRequest",
    "FantasyScoreLaneSpec",
    "LANE_SPECS",
    "run_fantasy_score_forward_cohort",
]
