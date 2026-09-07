"""WOW V17 postmortem / retrospective backend.

This module is deliberately observability-first. It records what worked, what
failed, exact settlement margins, card economics, and targeted patch candidates.
It never alters sporting probability, qualification floors, terminal ceilings,
or execution authority.

Core retro rule:
    preserve -> refine -> regression-check

Post-event rows without an exact immutable pregame recommendation link are
retrospective evidence only. Their probability fields are forced to null and
they are excluded from governed calibration.
"""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections import defaultdict
from datetime import date
from typing import Any, Callable, Optional

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

CAN_EXECUTE = False
_POSTMORTEM_NAMESPACE = uuid.UUID("3559d026-2843-4774-86cc-97dc0cba497b")
_ALLOWED_RESULTS = {"WIN", "LOSS", "PUSH", "VOID"}
_ALL_OR_NOTHING_DEFAULT_MARKERS = ("POWER", "ALL_OR_NOTHING")


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _finite(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    out = float(value)
    return out if math.isfinite(out) else None


def _prob(value: Any) -> Optional[float]:
    out = _finite(value)
    if out is None or not 0.0 <= out <= 1.0:
        return None
    return out


def _canonical_hash(value: Any) -> str:
    blob = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def signed_margin(side: str, line: Optional[float], actual: Optional[float]) -> Optional[float]:
    """Signed result margin from the selected side's perspective."""
    line_f = _finite(line)
    actual_f = _finite(actual)
    if line_f is None or actual_f is None:
        return None
    side_n = str(side or "").strip().upper()
    if side_n in {"MORE", "OVER"}:
        return actual_f - line_f
    if side_n in {"LESS", "UNDER"}:
        return line_f - actual_f
    return None


def classify_margin(*, side: str, line: Optional[float], actual: Optional[float], settled_result: str) -> str:
    result = str(settled_result or "").strip().upper()
    if result == "PUSH":
        return "PUSH"
    if result == "VOID":
        return "VOID"
    if line is None:
        return "BINARY_WINNER" if result == "WIN" else "BINARY_LOSS"
    margin = signed_margin(side, line, actual)
    if margin is None:
        return "NOT_APPLICABLE"
    if margin < 0:
        return "LARGE_MISS" if margin <= -2.0 else "MISS"
    if margin <= 0.5:
        return "NEAR_BOUNDARY"
    if margin <= 1.5:
        return "NARROW_CLEAR"
    return "COMFORTABLE_CLEAR"


def payout_diagnostics(*, entry_cost: float, gross_return: float, all_or_nothing: bool) -> dict[str, Optional[float]]:
    entry = float(entry_cost)
    returned = float(gross_return)
    gross_multiplier = (returned / entry) if entry > 0 else None
    roi = ((returned - entry) / entry) if entry > 0 else None
    break_even = None
    if all_or_nothing and entry > 0 and returned > 0:
        break_even = entry / returned
    return {
        "gross_multiplier": gross_multiplier,
        "roi": roi,
        "break_even_joint_probability": break_even,
    }


class PostmortemLegInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_key: str
    position_reference: str
    sport: str
    league: Optional[str] = None
    event_id: Optional[str] = None
    participant: str
    opponent: Optional[str] = None
    market: str
    side: str
    selection: str
    exact_line: Optional[float] = None
    actual_stat: Optional[float] = None
    official_result: Optional[str] = None
    settled_result: str
    observed_path: Optional[str] = None
    settlement_source: str
    settlement_evidence_ref: Optional[str] = None
    recommendation_record_id: Optional[str] = None
    observed_bf: Optional[int] = None
    outs_after_top3: Optional[int] = None
    top_order_reach_events: Optional[int] = None

    @model_validator(mode="after")
    def validate_leg(self):
        self.settled_result = self.settled_result.strip().upper()
        if self.settled_result not in _ALLOWED_RESULTS:
            raise ValueError("settled_result must be WIN, LOSS, PUSH, or VOID")
        if self.recommendation_record_id:
            uuid.UUID(self.recommendation_record_id)
            if not self.event_id:
                raise ValueError("linked pregame recommendation requires exact event_id")
        if self.observed_bf is not None and self.observed_bf < 0:
            raise ValueError("observed_bf cannot be negative")
        if self.outs_after_top3 is not None and not 0 <= self.outs_after_top3 <= 3:
            raise ValueError("outs_after_top3 must be 0..3")
        if self.top_order_reach_events is not None and self.top_order_reach_events < 0:
            raise ValueError("top_order_reach_events cannot be negative")
        return self


class PostmortemPositionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position_reference: str
    position_structure: str
    underlying_market_count: int = Field(ge=1)
    entry_cost: float = Field(ge=0)
    gross_return: float = Field(ge=0)
    all_or_nothing: Optional[bool] = None

    @model_validator(mode="after")
    def infer_structure(self):
        if self.all_or_nothing is None:
            text = self.position_structure.upper()
            self.all_or_nothing = any(marker in text for marker in _ALL_OR_NOTHING_DEFAULT_MARKERS)
        return self


class PatchCandidateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patch_key: str
    scope: str
    change_class: str = "DIAGNOSTIC_ONLY"
    proposed_change: str
    preserve_targets: list[str] = Field(default_factory=list)
    regression_checks: list[str] = Field(default_factory=list)
    broad_tightening: bool = False
    probability_change_allowed: bool = False
    qualification_floor_change_allowed: bool = False

    @model_validator(mode="after")
    def validate_change_class(self):
        allowed = {"DIAGNOSTIC_ONLY", "TARGETED_MODEL", "TARGETED_ECONOMICS", "TARGETED_STRUCTURE"}
        self.change_class = self.change_class.strip().upper()
        if self.change_class not in allowed:
            raise ValueError(f"change_class must be one of {sorted(allowed)}")
        return self


class PostmortemRunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    research_run_id: str
    slate_date: date
    source_type: str
    source_ref: Optional[str] = None
    preserve: list[str] = Field(min_length=1)
    refine: list[str] = Field(default_factory=list)
    regression_checks: list[str] = Field(min_length=1)
    legs: list[PostmortemLegInput] = Field(min_length=1, max_length=200)
    positions: list[PostmortemPositionInput] = Field(min_length=1, max_length=100)
    patch_candidates: list[PatchCandidateInput] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def validate_reconciliation(self):
        if len({leg.row_key for leg in self.legs}) != len(self.legs):
            raise ValueError("postmortem leg row_key values must be unique")
        if len({pos.position_reference for pos in self.positions}) != len(self.positions):
            raise ValueError("position_reference values must be unique")
        position_refs = {pos.position_reference for pos in self.positions}
        missing = sorted({leg.position_reference for leg in self.legs} - position_refs)
        if missing:
            raise ValueError(f"legs reference missing positions: {missing}")
        counts: dict[str, int] = defaultdict(int)
        for leg in self.legs:
            counts[leg.position_reference] += 1
        for pos in self.positions:
            if counts[pos.position_reference] != pos.underlying_market_count:
                raise ValueError(
                    f"{pos.position_reference}: underlying_market_count "
                    f"{pos.underlying_market_count} != leg count {counts[pos.position_reference]}"
                )
        if len({patch.patch_key for patch in self.patch_candidates}) != len(self.patch_candidates):
            raise ValueError("patch_key values must be unique")
        return self


def _extract_tail_probability(display_payload: Any, key: str) -> Optional[float]:
    if not isinstance(display_payload, dict):
        return None
    candidate_paths = (
        display_payload,
        display_payload.get("model_evidence"),
        display_payload.get("specialist"),
        display_payload.get("probability_package"),
    )
    for node in candidate_paths:
        if isinstance(node, dict):
            value = _prob(node.get(key))
            if value is not None:
                return value
    return None


def _validate_recommendation_identity(leg: PostmortemLegInput, recommendation: dict[str, Any]) -> None:
    checks = {
        "sport": (_norm(leg.sport), _norm(recommendation.get("sport"))),
        "participant": (_norm(leg.participant), _norm(recommendation.get("participant"))),
        "market": (_norm(leg.market), _norm(recommendation.get("market_family"))),
        "selection": (_norm(leg.selection), _norm(recommendation.get("selection"))),
        "event_id": (_norm(leg.event_id), _norm(recommendation.get("event_id"))),
    }
    mismatch = [name for name, (left, right) in checks.items() if not left or left != right]
    if mismatch:
        raise ValueError("recommendation identity mismatch: " + ",".join(sorted(mismatch)))


def normalize_leg(
    leg: PostmortemLegInput,
    *,
    run_id: uuid.UUID,
    recommendation: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Normalize one leg without allowing caller-owned probability backfill."""
    margin = signed_margin(leg.side, leg.exact_line, leg.actual_stat)
    margin_class = classify_margin(
        side=leg.side,
        line=leg.exact_line,
        actual=leg.actual_stat,
        settled_result=leg.settled_result,
    )
    hit = True if leg.settled_result == "WIN" else False if leg.settled_result == "LOSS" else None

    probability_fields: dict[str, Any] = {
        "governed_prediction_table": None,
        "governed_prediction_id": None,
        "raw_probability": None,
        "calibrated_probability": None,
        "lower_bound": None,
        "upper_bound": None,
        "failure_path_score": None,
        "pregame_bf_ge_5_probability": None,
        "pregame_bf_ge_6_probability": None,
    }

    if recommendation is None:
        record_status = "NO_MATCHED_IMMUTABLE_PREGAME_RECORD_FOUND"
        process = "PROCESS_UNVERIFIED_NO_IMMUTABLE_PREGAME_RECORD"
        capture_timing = "POST_EVENT_RETROACTIVE"
        calibration_eligible = False
        excluded = True
        recommendation_id = None
    else:
        _validate_recommendation_identity(leg, recommendation)
        recommendation_id = str(recommendation["recommendation_record_id"])
        if str(recommendation.get("capture_timing") or "").upper() != "PREGAME":
            record_status = "RETROSPECTIVE_RECOMMENDATION_RECORD"
            process = "RETROSPECTIVE_RECORD_EXCLUDED_FROM_CALIBRATION"
            capture_timing = "POST_EVENT_RETROACTIVE"
            calibration_eligible = False
            excluded = True
        else:
            record_status = "MATCHED_IMMUTABLE_PREGAME_RECORD"
            capture_timing = "PREGAME"
            governed_id = recommendation.get("governed_prediction_id")
            governed_table = recommendation.get("governed_prediction_table")
            calibration_eligible = bool(recommendation.get("calibration_eligible") is True and governed_id)
            excluded = not calibration_eligible
            process = (
                "MATCHED_GOVERNED_PREGAME_RECORD"
                if governed_id
                else "MATCHED_PREGAME_RECOMMENDATION_NO_GOVERNED_MODEL_LINK"
            )
            if governed_id:
                probability_fields.update(
                    {
                        "governed_prediction_table": governed_table,
                        "governed_prediction_id": governed_id,
                        "raw_probability": _prob(recommendation.get("model_probability")),
                        "calibrated_probability": _prob(recommendation.get("calibrated_probability")),
                        "lower_bound": _prob(recommendation.get("calibrated_probability_lower_bound")),
                        "upper_bound": None,
                        "failure_path_score": None,
                        "pregame_bf_ge_5_probability": _extract_tail_probability(
                            recommendation.get("display_payload"), "P_BF_GE_5"
                        ),
                        "pregame_bf_ge_6_probability": _extract_tail_probability(
                            recommendation.get("display_payload"), "P_BF_GE_6"
                        ),
                    }
                )

    observed_bf = leg.observed_bf
    bf_ge_5 = None if observed_bf is None else observed_bf >= 5
    bf_ge_6 = None if observed_bf is None else observed_bf >= 6
    tail_diagnostics = {
        "observed_bf": observed_bf,
        "outs_after_top3": leg.outs_after_top3,
        "top_order_reach_events": leg.top_order_reach_events,
        "observed_bf_ge_5": bf_ge_5,
        "observed_bf_ge_6": bf_ge_6,
        "pregame_bf_ge_6_status": (
            "AVAILABLE_FROM_IMMUTABLE_PREGAME_EVIDENCE"
            if probability_fields["pregame_bf_ge_6_probability"] is not None
            else "NOT_AVAILABLE_DO_NOT_BACKFILL"
        ),
    }

    return {
        "postmortem_leg_id": str(uuid.uuid5(_POSTMORTEM_NAMESPACE, f"{run_id}|leg|{leg.row_key}")),
        "postmortem_run_id": str(run_id),
        "row_key": leg.row_key,
        "position_reference": leg.position_reference,
        "recommendation_record_id": recommendation_id,
        "sport": leg.sport,
        "league": leg.league,
        "event_id": leg.event_id,
        "participant": leg.participant,
        "opponent": leg.opponent,
        "market": leg.market,
        "side": leg.side,
        "selection": leg.selection,
        "exact_line": leg.exact_line,
        "actual_stat": leg.actual_stat,
        "official_result": leg.official_result,
        "settled_result": leg.settled_result,
        "hit": hit,
        "margin_to_line": margin,
        "margin_class": margin_class,
        "observed_path": leg.observed_path,
        "observed_bf": observed_bf,
        "outs_after_top3": leg.outs_after_top3,
        "top_order_reach_events": leg.top_order_reach_events,
        "bf_ge_5": bf_ge_5,
        "bf_ge_6": bf_ge_6,
        "tail_diagnostics": tail_diagnostics,
        **probability_fields,
        "prediction_record_status": record_status,
        "process_classification": process,
        "capture_timing": capture_timing,
        "calibration_eligible": calibration_eligible,
        "excluded_from_calibration": excluded,
        "settlement_source": leg.settlement_source,
        "settlement_evidence_ref": leg.settlement_evidence_ref,
        "can_execute": False,
    }


def build_postmortem_payload(
    batch: PostmortemRunInput,
    *,
    recommendation_records: Optional[dict[str, dict[str, Any]]] = None,
) -> dict[str, Any]:
    recommendation_records = recommendation_records or {}
    run_id = uuid.uuid5(_POSTMORTEM_NAMESPACE, f"run|{batch.research_run_id}")

    normalized_legs: list[dict[str, Any]] = []
    for leg in batch.legs:
        recommendation = None
        if leg.recommendation_record_id:
            recommendation = recommendation_records.get(leg.recommendation_record_id)
            if recommendation is None:
                raise ValueError(f"recommendation_record_id not found: {leg.recommendation_record_id}")
        normalized_legs.append(normalize_leg(leg, run_id=run_id, recommendation=recommendation))

    by_position: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for leg in normalized_legs:
        by_position[leg["position_reference"]].append(leg)

    total_entry = sum(float(pos.entry_cost) for pos in batch.positions)
    total_return = sum(float(pos.gross_return) for pos in batch.positions)
    net_profit = total_return - total_entry

    normalized_positions: list[dict[str, Any]] = []
    for pos in batch.positions:
        legs = by_position[pos.position_reference]
        counts = {result: 0 for result in _ALLOWED_RESULTS}
        for leg in legs:
            counts[leg["settled_result"]] += 1
        decided = counts["WIN"] + counts["LOSS"]
        hit_rate = (counts["WIN"] / decided) if decided else None
        economics = payout_diagnostics(
            entry_cost=pos.entry_cost,
            gross_return=pos.gross_return,
            all_or_nothing=bool(pos.all_or_nothing),
        )
        statuses = {leg["prediction_record_status"] for leg in legs}
        if statuses == {"MATCHED_IMMUTABLE_PREGAME_RECORD"}:
            attribution_status = "MATCHED_PREGAME_RECORD"
        elif statuses <= {
            "NO_MATCHED_IMMUTABLE_PREGAME_RECORD_FOUND",
            "RETROSPECTIVE_RECOMMENDATION_RECORD",
        }:
            attribution_status = "RETROSPECTIVE_UNVERIFIED"
        else:
            attribution_status = "MIXED"
        normalized_positions.append(
            {
                "postmortem_position_id": str(
                    uuid.uuid5(_POSTMORTEM_NAMESPACE, f"{run_id}|position|{pos.position_reference}")
                ),
                "postmortem_run_id": str(run_id),
                "position_reference": pos.position_reference,
                "position_structure": pos.position_structure,
                "underlying_market_count": pos.underlying_market_count,
                "all_or_nothing": bool(pos.all_or_nothing),
                "entry_cost": float(pos.entry_cost),
                "gross_return": float(pos.gross_return),
                "net_profit": float(pos.gross_return - pos.entry_cost),
                "roi": economics["roi"],
                "gross_multiplier": economics["gross_multiplier"],
                "break_even_joint_probability": economics["break_even_joint_probability"],
                "leg_wins": counts["WIN"],
                "leg_losses": counts["LOSS"],
                "leg_pushes": counts["PUSH"],
                "leg_voids": counts["VOID"],
                "leg_hit_rate": hit_rate,
                "all_legs_hit": counts["LOSS"] == 0 and counts["PUSH"] == 0 and counts["VOID"] == 0,
                "capital_share": float(pos.entry_cost) / total_entry if total_entry > 0 else None,
                "profit_contribution_share": (
                    float(pos.gross_return - pos.entry_cost) / net_profit if net_profit != 0 else None
                ),
                "economics_status": "DIAGNOSTIC_ONLY",
                "attribution_status": attribution_status,
                "excluded_from_calibration": True,
                "can_execute": False,
            }
        )

    wins = sum(1 for leg in normalized_legs if leg["settled_result"] == "WIN")
    losses = sum(1 for leg in normalized_legs if leg["settled_result"] == "LOSS")
    pushes = sum(1 for leg in normalized_legs if leg["settled_result"] == "PUSH")
    voids = sum(1 for leg in normalized_legs if leg["settled_result"] == "VOID")
    matched = sum(
        1 for leg in normalized_legs if leg["prediction_record_status"] == "MATCHED_IMMUTABLE_PREGAME_RECORD"
    )
    retrospective = len(normalized_legs) - matched
    calib = sum(1 for leg in normalized_legs if leg["calibration_eligible"])

    lane_acc: dict[str, dict[str, int]] = defaultdict(
        lambda: {"rows": 0, "wins": 0, "losses": 0, "pushes": 0, "voids": 0}
    )
    for leg in normalized_legs:
        key = f'{leg["sport"]}:{leg["market"]}'
        lane_acc[key]["rows"] += 1
        bucket = {
            "WIN": "wins",
            "LOSS": "losses",
            "PUSH": "pushes",
            "VOID": "voids",
        }[leg["settled_result"]]
        lane_acc[key][bucket] += 1
    lane_summary = dict(lane_acc)

    summary = {
        "legs": len(normalized_legs),
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "voids": voids,
        "leg_hit_rate": wins / (wins + losses) if (wins + losses) else None,
        "positions": len(normalized_positions),
        "profitable_positions": sum(1 for pos in normalized_positions if pos["net_profit"] > 0),
        "non_losing_positions": sum(1 for pos in normalized_positions if pos["net_profit"] >= 0),
        "total_entry": total_entry,
        "total_return": total_return,
        "net_profit": net_profit,
        "roi": net_profit / total_entry if total_entry > 0 else None,
        "matched_pregame_count": matched,
        "retrospective_count": retrospective,
        "calibration_eligible_count": calib,
        "lane_summary": lane_summary,
        "retro_principle": "PRESERVE_REFINE_REGRESSION_CHECK",
        "probability_backfill_allowed": False,
        "global_tightening_applied": False,
        "can_execute": False,
    }

    normalized_patches: list[dict[str, Any]] = []
    for patch in batch.patch_candidates:
        status = "REQUIRES_EXPLICIT_GOVERNANCE" if patch.broad_tightening else "PROPOSED"
        normalized_patches.append(
            {
                "patch_candidate_id": str(
                    uuid.uuid5(_POSTMORTEM_NAMESPACE, f"{run_id}|patch|{patch.patch_key}")
                ),
                "postmortem_run_id": str(run_id),
                "patch_key": patch.patch_key,
                "scope": patch.scope,
                "change_class": patch.change_class,
                "proposed_change": patch.proposed_change,
                "preserve_targets": patch.preserve_targets,
                "regression_checks": patch.regression_checks,
                "broad_tightening": patch.broad_tightening,
                "probability_change_allowed": patch.probability_change_allowed,
                "qualification_floor_change_allowed": patch.qualification_floor_change_allowed,
                "status": status,
                "can_execute": False,
            }
        )

    run_payload = {
        "postmortem_run_id": str(run_id),
        "research_run_id": batch.research_run_id,
        "slate_date": batch.slate_date.isoformat(),
        "source_type": batch.source_type,
        "source_ref": batch.source_ref,
        "total_legs": len(normalized_legs),
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "voids": voids,
        "positions_count": len(normalized_positions),
        "profitable_positions": summary["profitable_positions"],
        "non_losing_positions": summary["non_losing_positions"],
        "total_entry": total_entry,
        "total_return": total_return,
        "net_profit": net_profit,
        "roi": summary["roi"],
        "matched_pregame_count": matched,
        "retrospective_count": retrospective,
        "calibration_eligible_count": calib,
        "preserve_items": batch.preserve,
        "refine_items": batch.refine,
        "regression_checks": batch.regression_checks,
        "summary": summary,
        "process_status": "COMPLETED" if retrospective == 0 else "COMPLETED_WITH_BLOCKERS",
        "can_execute": False,
    }

    hash_material = {
        "run": {k: v for k, v in run_payload.items() if k != "payload_hash"},
        "legs": normalized_legs,
        "positions": normalized_positions,
        "patches": normalized_patches,
    }
    run_payload["payload_hash"] = _canonical_hash(hash_material)

    return {
        "run": run_payload,
        "legs": normalized_legs,
        "positions": normalized_positions,
        "patches": normalized_patches,
        "summary": summary,
        "can_execute": False,
    }


def _load_recommendation_records(client: Any, ids: list[str]) -> dict[str, dict[str, Any]]:
    if not ids:
        return {}
    result = (
        client.table("wow_recommendation_records")
        .select(
            "recommendation_record_id,row_key,sport,event_id,participant,market_family,"
            "selection,capture_timing,calibration_eligible,probability_publishable,"
            "model_probability,calibrated_probability,calibrated_probability_lower_bound,"
            "governed_prediction_table,governed_prediction_id,display_payload"
        )
        .in_("recommendation_record_id", ids)
        .execute()
    )
    rows = result.data or []
    return {str(row["recommendation_record_id"]): dict(row) for row in rows}


def install_postmortem_routes(
    app: FastAPI,
    *,
    auth_dependency: Depends,
    get_client_fn: Callable[[], Any],
) -> None:
    @app.post("/v17/postmortem-runs", dependencies=[auth_dependency])
    def record_postmortem(batch: PostmortemRunInput):
        client = get_client_fn()
        ids = sorted(
            {leg.recommendation_record_id for leg in batch.legs if leg.recommendation_record_id}
        )
        try:
            recommendation_records = _load_recommendation_records(client, ids)
            payload = build_postmortem_payload(batch, recommendation_records=recommendation_records)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "POSTMORTEM_IDENTITY_OR_RECONCILIATION_FAILED",
                    "message": str(exc),
                    "can_execute": False,
                },
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "POSTMORTEM_RECOMMENDATION_LOOKUP_FAILED", "can_execute": False},
            ) from exc

        try:
            result = client.rpc(
                "wow_record_postmortem_run",
                {
                    "p_run": payload["run"],
                    "p_legs": payload["legs"],
                    "p_positions": payload["positions"],
                    "p_patches": payload["patches"],
                },
            ).execute()
            persisted = result.data or {}
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "POSTMORTEM_LEDGER_WRITE_FAILED", "can_execute": False},
            ) from exc

        if not isinstance(persisted, dict) or persisted.get("reconciliation_pass") is not True:
            raise HTTPException(
                status_code=503,
                detail={"code": "POSTMORTEM_LEDGER_WRITE_UNPROVEN", "can_execute": False},
            )
        return {
            "code": "POSTMORTEM_LEDGER_WRITE_PASS",
            **persisted,
            "summary": payload["summary"],
            "principle": "PRESERVE_REFINE_REGRESSION_CHECK",
            "global_tightening_applied": False,
            "probability_backfill_allowed": False,
            "can_execute": False,
        }

    @app.get("/v17/postmortem-runs/{research_run_id}", dependencies=[auth_dependency])
    def get_postmortem(research_run_id: str):
        client = get_client_fn()
        try:
            runs = (
                client.table("wow_postmortem_runs")
                .select("*")
                .eq("research_run_id", research_run_id)
                .limit(1)
                .execute()
            ).data or []
            if not runs:
                raise HTTPException(
                    status_code=404,
                    detail={"code": "POSTMORTEM_RUN_NOT_FOUND", "can_execute": False},
                )
            run = dict(runs[0])
            run_id = run["postmortem_run_id"]
            legs = (
                client.table("wow_postmortem_legs")
                .select("*")
                .eq("postmortem_run_id", run_id)
                .order("row_key")
                .execute()
            ).data or []
            positions = (
                client.table("wow_postmortem_positions")
                .select("*")
                .eq("postmortem_run_id", run_id)
                .order("position_reference")
                .execute()
            ).data or []
            patches = (
                client.table("wow_postmortem_patch_candidates")
                .select("*")
                .eq("postmortem_run_id", run_id)
                .order("patch_key")
                .execute()
            ).data or []
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "POSTMORTEM_LEDGER_READ_FAILED", "can_execute": False},
            ) from exc
        return {
            "run": run,
            "legs": legs,
            "positions": positions,
            "patch_candidates": patches,
            "can_execute": False,
        }


__all__ = [
    "CAN_EXECUTE",
    "PatchCandidateInput",
    "PostmortemLegInput",
    "PostmortemPositionInput",
    "PostmortemRunInput",
    "build_postmortem_payload",
    "classify_margin",
    "install_postmortem_routes",
    "payout_diagnostics",
    "signed_margin",
]
