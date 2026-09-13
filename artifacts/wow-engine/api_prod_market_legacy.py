"""Market-lane compatibility layer for the governed WOW production API.

Builds on api_prod and replaces only POST /score-prop so exact two-way market
quotes can traverse the existing engine. Generic player props are scored only
through WOW_PROP_FITTED_MODEL_V1's certified, direction-free discrete PMF
contract. Missing/invalid market evidence is a MARKET HOLD, never a reason to
erase an otherwise publishable sporting-model probability. can_execute remains
false unconditionally.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Optional
import uuid

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict

import api_prod as prod
from market import MarketQuote
from prop_discrete_engine import PropCalibrationUnavailable, score_discrete_prop_end_to_end
from prop_distribution_contract import PropDistributionContractError, PropInferenceRequest
from prop_evidence_repair import repair_prop_evidence
from prop_fitted_provider import PropFittedProviderUnavailable
from prop_market_audit import audit_candidate_market
from prop_settlement import (
    LINEUP_PAYOUT_CONTEXT_REQUIRED,
    NO_VIG_UNAVAILABLE,
    SETTLEMENT_RULE_UNRESOLVED,
    SettlementRule,
    settle_prop_probability,
)
from prop_settlement_registry import (
    SETTLEMENT_RULE_CONFLICT,
    SettlementRuleResolution,
    normalize_provider,
    resolve_prop_settlement_rule,
)
from qualification_policy_v2 import classify_prop_probability
from prop_terminal_reducer_v2 import reduce_prop_terminal
from wolfram_arithmetic_auditor import PASS as WOLFRAM_PASS
from wolfram_arithmetic_auditor import LEDGER_WRITE_UNPROVEN as WOLFRAM_LEDGER_WRITE_UNPROVEN
from wolfram_arithmetic_auditor import audit_claims as audit_wolfram_claims
from wolfram_arithmetic_auditor import audit_enabled as wolfram_audit_enabled
from wolfram_arithmetic_auditor import persist_audit as persist_wolfram_audit

import prop_calibration_adapters
import prop_model_adapters
import prop_model_adapters_pitching_outs
import prop_model_adapters_pitch_composition
import prop_model_adapters_plate_appearances

# Production adapter registration is code-controlled: these are the only
# WOW_PROP_FITTED_MODEL_V1 model-family/calibrator adapters this process
# will ever serve, and registering them is a required startup side effect,
# not test-only wiring (see each module's own tests for isolated coverage).
prop_model_adapters.register()
prop_model_adapters_pitching_outs.register()
prop_model_adapters_pitch_composition.register()
prop_model_adapters_plate_appearances.register()
prop_calibration_adapters.register()


PROP_FEATURE_SCHEMA_VERSION = "PROP_FEATURES_V1"


class MarketQuoteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    side: str
    american_odds: float
    line: float
    settlement_basis: str
    retrieved_at: str
    participant: str
    stat: str
    period: str
    event_id: str
    provider: Optional[str] = None


class SettlementRuleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    settlement_basis: str
    boundary_operator: str
    equality_treatment: str
    void_treatment: str = "RETURN_STAKE"
    rule_version: str
    source: str
    void_probability_mass: float = 0.0
    money_semantics: str = "FIXED_ODDS_RETURN_STAKE"


class ScorePropRequest(prod.ScorePropRequest):
    market_side_a: Optional[MarketQuoteInput] = None
    market_side_b: Optional[MarketQuoteInput] = None
    settlement_provider: Optional[str] = None
    # Backward-compatible caller observation only. This can cross-check but
    # never becomes the operative server rule.
    settlement_rule: Optional[SettlementRuleInput] = None


app = FastAPI(
    title=prod.app.title,
    version=prod.app.version,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.router.routes.extend(
    route
    for route in prod.app.router.routes
    if not (
        getattr(route, "path", None) == "/score-prop"
        and "POST" in (getattr(route, "methods", set()) or set())
    )
)
app.exception_handlers.update(prod.app.exception_handlers)


def _to_market_quote(value: Optional[MarketQuoteInput]) -> Optional[MarketQuote]:
    if value is None:
        return None
    return MarketQuote(**value.model_dump())


def _to_settlement_rule(value: Optional[SettlementRuleInput]) -> Optional[SettlementRule]:
    if value is None:
        return None
    return SettlementRule(**value.model_dump())


def _candidate_american_odds(direction: str, side_a: Optional[MarketQuote], side_b: Optional[MarketQuote]) -> Optional[float]:
    target = str(direction or "").upper()
    aliases = {"MORE": {"MORE", "OVER"}, "LESS": {"LESS", "UNDER"}}
    for quote in (side_a, side_b):
        if quote is not None and str(quote.side or "").upper() in aliases.get(target, set()):
            return float(quote.american_odds)
    return None


def _settlement_lane(row: Any, req: ScorePropRequest, rule: Optional[SettlementRule], resolution: SettlementRuleResolution, audited_a: Optional[MarketQuote], audited_b: Optional[MarketQuote]) -> dict[str, Any]:
    result = settle_prop_probability(
        direction=req.direction,
        probability_more=getattr(row, "probability_more", None),
        probability_less=getattr(row, "probability_less", None),
        equality_probability=getattr(row, "push_probability", None),
        rule=rule,
        american_odds=_candidate_american_odds(req.direction, audited_a, audited_b),
    )
    return {
        "status": result.status,
        "blocker": resolution.blocker if resolution.status != "PASS" else result.blocker,
        "rule_resolution_status": resolution.status,
        "rule_authority": resolution.authority,
        "provider": resolution.provider,
        "rule_id": resolution.rule_id,
        "source_ref": resolution.source_ref,
        "source_hash": resolution.source_hash,
        "observed_rule_status": resolution.observed_rule_status,
        "p_win": result.p_win,
        "p_loss": result.p_loss,
        "p_push": result.p_push,
        "p_void": result.p_void,
        "graded_probability": result.graded_probability,
        "conditional_win_probability": result.conditional_win_probability,
        "american_odds": result.american_odds,
        "profit_multiple": result.profit_multiple,
        "break_even_unconditional": result.break_even_unconditional,
        "break_even_conditional_graded": result.break_even_conditional_graded,
        "expected_profit_per_unit_staked": result.expected_profit_per_unit_staked,
        "rule_version": result.rule_version,
        "source": result.source,
        "void_treatment": result.void_treatment,
        "money_semantics": result.money_semantics,
        "money_context_required": result.money_context_required,
        "blocks_model_probability": False,
        "can_execute": False,
    }


def _effective_money_lane(row: Any, settlement_lane: dict[str, Any]) -> dict[str, Any]:
    lane = _money_lane(row)
    if settlement_lane.get("status") != "PASS":
        lane = dict(lane)
        lane["status"] = "HOLD"
        lane["settlement_blocker"] = settlement_lane.get("blocker") or SETTLEMENT_RULE_UNRESOLVED
    elif settlement_lane.get("money_context_required") is True:
        lane = dict(lane)
        lane["status"] = "HOLD"
        lane["settlement_blocker"] = LINEUP_PAYOUT_CONTEXT_REQUIRED
    return lane


def _market_lane_with_audit(row: Any, market_audit: Any) -> dict[str, Any]:
    lane = _market_lane(row)
    lane["candidate_audit_status"] = market_audit.status
    lane["candidate_audit_blocker"] = market_audit.blocker
    if market_audit.status != "PASS":
        lane["status"] = "HOLD"
    if lane["status"] != "PASS" and not lane.get("candidate_audit_blocker"):
        lane["candidate_audit_blocker"] = NO_VIG_UNAVAILABLE
    return lane


def _market_lane(row: Any) -> dict[str, Any]:
    available = bool(getattr(row, "market_prior_available", False))
    quality = getattr(row, "market_prior_quality", None) or "NO_QUALIFYING_MARKET"
    status = "PASS" if available and quality == "EXACT_TWO_WAY_NO_VIG" else "HOLD"
    return {
        "status": status,
        "quality": quality,
        "market_prior_available": available,
        "market_prior_probability": getattr(row, "market_prior_probability", None),
        "market_prior_weight": getattr(row, "market_prior_weight", 0.0),
        "market_prior_weight_source": getattr(row, "market_prior_weight_source", None),
        "reference_market_probability_raw": getattr(row, "reference_market_probability_raw", None),
        "reference_market_side": getattr(row, "reference_market_side", None),
        "reference_market_price": getattr(row, "reference_market_price", None),
        "blocks_model_probability": False,
        "can_execute": False,
    }


def _money_lane(row: Any) -> dict[str, Any]:
    status = getattr(row, "money_lane_status", None) or "PAYOUT_UNRESOLVED"
    return {
        "status": "PASS" if status == "RESOLVED" else "HOLD",
        "money_lane_status": status,
        "blocks_model_probability": False,
        "can_execute": False,
    }


def _american_implied_probability(odds: float) -> float:
    value = float(odds)
    if value == 0:
        raise ValueError("american odds must be non-zero")
    return 100.0 / (value + 100.0) if value > 0 else abs(value) / (abs(value) + 100.0)


def _wolfram_arithmetic_claims(
    row: Any,
    *,
    direction: str,
    market_audit: Any,
    settlement_lane: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build only server-owned arithmetic expressions from governed outputs."""
    claims: list[dict[str, Any]] = []
    probabilities = [
        getattr(row, "probability_more", None),
        getattr(row, "probability_less", None),
        getattr(row, "push_probability", None),
    ]
    if all(value is not None for value in probabilities):
        claims.append({
            "claim_id": "model-probability-normalization",
            "template_id": "PROBABILITY_TOTAL",
            "inputs": {"probabilities": probabilities},
            "reported_result": 1.0,
        })

    side_a = getattr(market_audit, "side_a", None)
    side_b = getattr(market_audit, "side_b", None)
    if side_a is not None and side_b is not None:
        raw_a = _american_implied_probability(side_a.american_odds)
        raw_b = _american_implied_probability(side_b.american_odds)
        claims.extend((
            {
                "claim_id": "market-side-a-implied-probability",
                "template_id": "AMERICAN_ODDS_IMPLIED_PROBABILITY",
                "inputs": {"american_odds": side_a.american_odds},
                "reported_result": raw_a,
            },
            {
                "claim_id": "market-side-b-implied-probability",
                "template_id": "AMERICAN_ODDS_IMPLIED_PROBABILITY",
                "inputs": {"american_odds": side_b.american_odds},
                "reported_result": raw_b,
            },
            {
                "claim_id": "market-hold",
                "template_id": "MARKET_HOLD",
                "inputs": {"q_a": raw_a, "q_b": raw_b},
                "reported_result": raw_a + raw_b - 1.0,
            },
        ))
        target = str(direction or "").upper()
        selected_is_a = str(side_a.side or "").upper() == target
        selected, opposing = (raw_a, raw_b) if selected_is_a else (raw_b, raw_a)
        market_prior = getattr(row, "market_prior_probability", None)
        if market_prior is not None:
            claims.append({
                "claim_id": "two-way-no-vig",
                "template_id": "TWO_WAY_NO_VIG",
                "inputs": {"q_selected": selected, "q_opposing": opposing},
                "reported_result": market_prior,
            })

    settlement_probabilities = [
        settlement_lane.get("p_win"),
        settlement_lane.get("p_loss"),
        settlement_lane.get("p_push"),
        settlement_lane.get("p_void"),
    ]
    if settlement_lane.get("status") == "PASS" and all(value is not None for value in settlement_probabilities):
        claims.append({
            "claim_id": "settlement-probability-normalization",
            "template_id": "PROBABILITY_TOTAL",
            "inputs": {"probabilities": settlement_probabilities},
            "reported_result": 1.0,
        })
        conditional = settlement_lane.get("conditional_win_probability")
        if conditional is not None:
            claims.append({
                "claim_id": "conditional-graded-win-probability",
                "template_id": "TWO_WAY_NO_VIG",
                "inputs": {
                    "q_selected": settlement_lane["p_win"],
                    "q_opposing": settlement_lane["p_loss"],
                },
                "reported_result": conditional,
            })

    profit_multiple = settlement_lane.get("profit_multiple")
    if settlement_lane.get("expected_profit_per_unit_staked") is not None and profit_multiple is not None:
        claims.append({
            "claim_id": "fixed-odds-expected-profit",
            "template_id": "FIXED_ODDS_EXPECTED_PROFIT",
            "inputs": {
                "p_win": settlement_lane["p_win"],
                "p_loss": settlement_lane["p_loss"],
                "profit_multiple": profit_multiple,
            },
            "reported_result": settlement_lane["expected_profit_per_unit_staked"],
        })
    if settlement_lane.get("break_even_unconditional") is not None and profit_multiple is not None:
        claims.append({
            "claim_id": "fixed-odds-break-even-unconditional",
            "template_id": "FIXED_ODDS_BREAK_EVEN_UNCONDITIONAL",
            "inputs": {
                "refundable_probability": (settlement_lane.get("p_push") or 0.0) + (settlement_lane.get("p_void") or 0.0),
                "profit_multiple": profit_multiple,
            },
            "reported_result": settlement_lane["break_even_unconditional"],
        })
    return claims


def _apply_wolfram_arithmetic_gate(
    *,
    row: Any,
    direction: str,
    market_audit: Any,
    market_lane: dict[str, Any],
    settlement_lane: dict[str, Any],
    money_lane: dict[str, Any],
    prediction_id: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    required = wolfram_audit_enabled()
    claims = _wolfram_arithmetic_claims(
        row,
        direction=direction,
        market_audit=market_audit,
        settlement_lane=settlement_lane,
    )
    audit = audit_wolfram_claims(claims, required=required)
    verdict = str(audit.get("verdict") or "WOLFRAM_OUTPUT_INVALID")
    audit = dict(audit)
    if required:
        try:
            stored = persist_wolfram_audit(
                prod.get_client(),
                prediction_id=prediction_id,
                audit=audit,
            )
            audit["ledger_write"] = "PASS"
            audit["arithmetic_audit_id"] = stored["arithmetic_audit_id"]
            audit["audit_payload_hash"] = stored["audit_payload_hash"]
        except Exception:
            audit["provider_verdict"] = verdict
            audit["verdict"] = WOLFRAM_LEDGER_WRITE_UNPROVEN
            audit["ledger_write"] = "UNPROVEN"
            verdict = WOLFRAM_LEDGER_WRITE_UNPROVEN
    else:
        audit["ledger_write"] = "NOT_REQUIRED"

    market_lane = dict(market_lane)
    settlement_lane = dict(settlement_lane)
    money_lane = dict(money_lane)
    for lane in (market_lane, settlement_lane, money_lane):
        lane["wolfram_arithmetic_audit"] = verdict
        lane["arithmetic_verified"] = verdict == WOLFRAM_PASS

    if required and verdict != WOLFRAM_PASS:
        if market_lane.get("status") == "PASS":
            market_lane["status"] = "HOLD"
            market_lane["candidate_audit_blocker"] = verdict
        money_lane["status"] = "HOLD"
        money_lane["money_lane_status"] = verdict
        money_lane["arithmetic_audit_blocker"] = verdict
        settlement_lane["arithmetic_audit_blocker"] = verdict
    return market_lane, settlement_lane, money_lane, audit


def _probability_qualification(row: Any, market_lane: dict[str, Any], money_lane: dict[str, Any], settlement_lane: dict[str, Any]) -> dict[str, Any]:
    qualification = classify_prop_probability(
        calibrated_probability=getattr(row, "calibrated_probability", None),
        calibrated_lower_bound=getattr(row, "calibrated_probability_lower_bound", None),
        calibration_status=getattr(row, "calibration_status", None),
        blockers=getattr(row, "data_gaps", None) or [],
        probability_publishable=bool(getattr(row, "probability_publishable", False)),
    )
    blockers = list(qualification.blockers)
    if market_lane.get("status") != "PASS":
        blockers.append("MARKET_DATA_UNAVAILABLE")
    if money_lane.get("status") != "PASS":
        blockers.append("PAYOUT_UNRESOLVED")
    if settlement_lane.get("status") != "PASS":
        blockers.append("SETTLEMENT_RULE_UNRESOLVED")
    terminal = reduce_prop_terminal(
        proposed_label=qualification.terminal_label,
        blockers=blockers,
        model_evaluated=True,
    )
    return {
        "terminal_label": terminal.terminal_label,
        "confidence_tier": qualification.confidence_tier,
        "rank_eligible": qualification.rank_eligible,
        "model_supported": qualification.model_supported,
        "model_evaluated": terminal.model_evaluated,
        "pick_rejected": terminal.pick_rejected,
        "verdict_class": terminal.verdict_class,
        "infrastructure_blocked": terminal.infrastructure_blocked,
        "downstream_money_evaluation_allowed": qualification.downstream_money_evaluation_allowed,
        "final_approved_allowed": False,
        "blockers": list(terminal.blockers),
        "can_execute": False,
    }


def _aware_event_start(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PROP_EVENT_START_INVALID",
                "probability_publishable": False,
                "can_execute": False,
            },
        ) from exc
    if parsed.utcoffset() is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PROP_EVENT_START_INVALID",
                "probability_publishable": False,
                "can_execute": False,
            },
        )
    return parsed


def _prop_period(stat_type: str) -> str:
    upper = str(stat_type or "").upper()
    return "FIRST_INNING" if "1IP" in upper or "FIRST_INNING" in upper else "FULL_GAME"


def _prop_route_artifact(sport: str, stat_type: str) -> dict[str, Any]:
    """Resolve the exact certified fitted-model route before model invocation.

    The aggregate PROP_PROBABILITY capability can be AVAILABLE when at least one
    governed prop family is operational. It must never imply that every
    sport/stat route is model-ready. Exact route readiness is therefore proven
    independently through the certified artifact registry.
    """
    try:
        result = prod.get_client().rpc(
            "wow_prop_certified_model_artifact",
            {
                "p_sport": str(sport).upper(),
                "p_stat_type": str(stat_type).upper(),
                "p_feature_schema_version": PROP_FEATURE_SCHEMA_VERSION,
            },
        ).execute()
    except Exception:
        return {
            "ok": False,
            "code": "PROP_MODEL_REGISTRY_UNAVAILABLE",
            "probability_publishable": False,
            "can_execute": False,
        }

    payload = result.data
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "code": "PROP_MODEL_REGISTRY_INVALID_RESPONSE",
            "probability_publishable": False,
            "can_execute": False,
        }
    return payload


def _server_owned_inference_request(req: ScorePropRequest, evidence: dict[str, Any], scored_at: str) -> PropInferenceRequest:
    """Build the provider identity without caller-controlled model metadata.

    Exact player/event/stat identity has already been locked by the Supabase
    evidence RPC. ``player_id`` is a deterministic internal key over sport and
    the exact evidence player name; no external-ID claim is made.
    """
    event_start = _aware_event_start(req.event_start_time)
    player = str(evidence.get("player") or req.player or "").strip()
    normalized_player = " ".join(player.casefold().split())
    player_id = "wow-name:" + sha256(f"{req.sport.upper()}|{normalized_player}".encode("utf-8")).hexdigest()
    period = _prop_period(req.stat_type)
    settlement_basis = "FIRST_INNING_PLAYER_STAT" if period == "FIRST_INNING" else "FULL_GAME_PLAYER_STAT"
    canonical_market = "|".join(
        (
            req.sport.upper(),
            req.event_id,
            normalized_player,
            req.stat_type.upper(),
            period,
            format(float(req.line), ".12g"),
            settlement_basis,
        )
    )
    market_identity_id = "wow-market:" + sha256(canonical_market.encode("utf-8")).hexdigest()
    evidence_snapshot_id = str(evidence.get("source_snapshot_id") or req.source_snapshot_id)
    return PropInferenceRequest(
        event_id=req.event_id,
        player_id=player_id,
        sport=req.sport,
        league_season=str(event_start.year),
        stat_type=req.stat_type,
        evidence_snapshot_id=evidence_snapshot_id,
        market_identity_id=market_identity_id,
        as_of_timestamp=scored_at,
        request_id=str(uuid.uuid4()),
        feature_schema_version=PROP_FEATURE_SCHEMA_VERSION,
    )


def _model_features(evidence: dict[str, Any]) -> dict[str, Any]:
    """Expose only hydrated evidence to the reviewed model-family adapter.

    ``opponent_context`` is an additive, optional passthrough: the current
    wow_prop_evidence_snapshots acquisition contract does not populate it,
    so it is None until an ingestion pipeline adds it. Adapters that use it
    (e.g. the MLB pitcher strikeout adapter's opponent factor) must treat a
    missing value as "no adjustment", never fabricate one.
    """
    return {
        "game_log": evidence.get("game_log"),
        "box_score_log": evidence.get("box_score_log"),
        "role_status": evidence.get("role_status"),
        "role_timestamp": evidence.get("role_timestamp"),
        "opportunity_ledger": evidence.get("opportunity_ledger"),
        "source_timestamps": evidence.get("source_timestamps") or {},
        "evidence_version": evidence.get("evidence_version"),
        "rate_provenance": evidence.get("rate_provenance"),
        "captured_at": evidence.get("captured_at"),
        "opponent_context": evidence.get("opponent_context"),
    }


def _discrete_model_evidence(result: Any) -> dict[str, Any]:
    row = result.row
    artifact = result.inference.artifact
    distribution = result.inference.distribution
    coverage = distribution.coverage
    return {
        "provider_identity": getattr(row, "model_provider_identity", None),
        "model_family": getattr(row, "model_family", None),
        "model_artifact_version": getattr(row, "model_artifact_version", None),
        "model_artifact_checksum": getattr(row, "model_artifact_checksum", None),
        "bundle_fingerprint": getattr(row, "model_bundle_fingerprint", None),
        "model_lifecycle_state": getattr(row, "model_artifact_lifecycle_state", None),
        "feature_schema_version": getattr(row, "feature_schema_version", None),
        "feature_transform_version": getattr(row, "feature_transform_version", None),
        "specialist_version": getattr(row, "specialist_version", None),
        "certification_id": getattr(row, "certification_id", None),
        "distribution_type": getattr(row, "distribution_type", None),
        "probability_more": getattr(row, "probability_more", None),
        "probability_less": getattr(row, "probability_less", None),
        "push_probability": getattr(row, "push_probability", None),
        "coverage": {
            "in_distribution": coverage.in_distribution,
            "ood_score": coverage.ood_score,
            "coverage_failures": list(coverage.coverage_failures),
        },
        "training_rows": artifact.training_rows,
        "effective_sample_size": getattr(row, "effective_sample_size", None),
        "calibration_status": getattr(row, "calibration_status", None),
        "calibration_method": getattr(row, "calibration_method", None),
        "calibration_version": getattr(row, "calibration_version", None),
        "calibrated_probability": getattr(row, "calibrated_probability", None),
        "bounds_method_version": getattr(row, "bounds_method_version", None),
        "calibrated_probability_lower_bound": getattr(row, "calibrated_probability_lower_bound", None),
        "calibrated_probability_upper_bound": getattr(row, "calibrated_probability_upper_bound", None),
        "model_timestamp": getattr(row, "model_timestamp", None),
        "probability_publishable": bool(getattr(row, "probability_publishable", False)),
        # Advisory/explanatory only -- see RawDiscreteDistribution.
        # failure_path_evidence. Empty for adapters that report nothing
        # (the default); never a probability, bound, or terminal label.
        "failure_path_evidence": dict(getattr(distribution, "failure_path_evidence", {}) or {}),
        "can_execute": False,
    }


def _raise_model_path_error(exc: Exception) -> None:
    code = getattr(exc, "code", None) or "PROP_DISCRETE_MODEL_UNAVAILABLE"
    status_code = 409 if code in {
        "PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND",
        "PROP_MODEL_REGISTRY_UNAVAILABLE",
        "PROP_MODEL_FAMILY_ADAPTER_UNAVAILABLE",
        "PROP_CALIBRATOR_ADAPTER_UNAVAILABLE",
    } else 422
    raise HTTPException(
        status_code=status_code,
        detail={
            "code": code,
            "message": str(exc),
            "model_path": "WOW_PROP_FITTED_MODEL_V1->RAW_DISCRETE_DISTRIBUTION->CALIBRATION->PERSISTENCE",
            "probability_publishable": False,
            "can_execute": False,
        },
    ) from exc


def _preflight_prop_route(
    req: ScorePropRequest,
    *,
    model_identity: str,
    lane: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fail closed on unsupported routes before expensive evidence hydration.

    P0 Pick Request reliability requires route readiness to be resolved first.
    A row with no controlling specialist, no aggregate capability, or no exact
    certified fitted-model artifact must terminate without calling the evidence
    hydrator. This keeps unsupported rows from consuming acquisition work and
    prevents their failures from masquerading as evidence-contract failures.
    """
    specialist = prod.base_api._controlling_specialist_provider(req.sport, req.stat_type)
    if specialist is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "SPECIALIST_ROUTING_UNAVAILABLE",
                "evidence_hydration": "NOT_ATTEMPTED_ROUTE_BLOCKED",
                "specialist_invoked": False,
                "probability_publishable": False,
                "can_execute": False,
            },
        )
    if specialist.get("controlling_specialist") == "MODEL_UNAVAILABLE":
        raise HTTPException(
            status_code=422,
            detail={
                "code": "MODEL_UNAVAILABLE",
                "controlling_specialist": "MODEL_UNAVAILABLE",
                "sport": specialist.get("sport"),
                "canonical_prop_type": specialist.get("canonical_prop_type"),
                "evidence_hydration": "NOT_ATTEMPTED_ROUTE_BLOCKED",
                "specialist_invoked": False,
                "probability_publishable": False,
                "can_execute": False,
            },
        )

    if lane.get("capability_status") != "AVAILABLE":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PROP_PROBABILITY_UNAVAILABLE",
                "governed_probability_capability": "UNAVAILABLE",
                "governed_probability_status": "NOT_PRODUCED",
                "capability_evidence": lane.get("evidence") or {},
                "evidence_hydration": "NOT_ATTEMPTED_ROUTE_BLOCKED",
                "controlling_specialist": specialist.get("controlling_specialist"),
                "specialist_invoked": False,
                "backend_traversal": {
                    "requester_model": model_identity,
                    "render": "PASS",
                    "supabase_capability": "PASS",
                    "supabase_evidence": "NOT_ATTEMPTED",
                    "controlling_specialist": "PASS",
                    "exact_route_artifact": "NOT_ATTEMPTED",
                    "governed_model": "BLOCKED",
                    "prediction_ledger_write": "NOT_ATTEMPTED",
                },
                "model_path": "WOW_PROP_FITTED_MODEL_V1->RAW_DISCRETE_DISTRIBUTION->CALIBRATION->PERSISTENCE",
                "probability_publishable": False,
                "can_execute": False,
            },
        )

    route_artifact = _prop_route_artifact(req.sport, req.stat_type)
    if route_artifact.get("ok") is not True or route_artifact.get("code") != "PROP_CERTIFIED_MODEL_ARTIFACT_READY":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "MODEL_UNAVAILABLE",
                "blocker_code": route_artifact.get("code") or "PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND",
                "governed_probability_capability": "UNAVAILABLE_FOR_EXACT_ROUTE",
                "governed_probability_status": "NOT_PRODUCED",
                "aggregate_prop_capability_status": lane.get("capability_status"),
                "requested_route": {
                    "sport": str(req.sport).upper(),
                    "stat_type": str(req.stat_type).upper(),
                    "feature_schema_version": PROP_FEATURE_SCHEMA_VERSION,
                },
                "route_artifact_evidence": route_artifact,
                "evidence_hydration": "NOT_ATTEMPTED_ROUTE_BLOCKED",
                "controlling_specialist": specialist.get("controlling_specialist"),
                "specialist_invoked": False,
                "backend_traversal": {
                    "requester_model": model_identity,
                    "render": "PASS",
                    "supabase_capability": "PASS",
                    "supabase_evidence": "NOT_ATTEMPTED",
                    "controlling_specialist": "PASS",
                    "exact_route_artifact": "BLOCKED",
                    "governed_model": "NOT_INVOKED",
                    "prediction_ledger_write": "NOT_ATTEMPTED",
                },
                "model_path": "WOW_PROP_FITTED_MODEL_V1->RAW_DISCRETE_DISTRIBUTION->CALIBRATION->PERSISTENCE",
                "probability_publishable": False,
                "can_execute": False,
            },
        )
    return specialist, route_artifact


@app.post(
    "/score-prop",
    dependencies=[Depends(prod._require_action_api_key)],
    operation_id="scoreWowProp",
)
def score_prop(
    req: ScorePropRequest,
    x_wow_model_identity: Optional[str] = Header(default=None, alias="X-WOW-Model-Identity"),
):
    """Governed player-prop scoring with explicit objective separation."""
    model_identity = prod._reject_llp_prop_identity(x_wow_model_identity)
    lane = prod._runtime_capability(prod.PROP_CAPABILITY_KEY)
    specialist, route_artifact = _preflight_prop_route(
        req,
        model_identity=model_identity,
        lane=lane,
    )

    evidence = repair_prop_evidence(
        req,
        primary_fetch=prod._prop_evidence,
        client=prod.get_client(),
    )
    if evidence.get("ok") is not True or evidence.get("code") != "PROP_EVIDENCE_READY":
        detail = dict(evidence)
        detail.setdefault("code", "RUN_INVALID_ACQUISITION_INCOMPLETE")
        detail["failure_class"] = "RUN_INVALID_ACQUISITION_INCOMPLETE"
        detail["route_preflight"] = "PASS"
        detail["exact_route_artifact"] = route_artifact.get("code")
        detail["controlling_specialist"] = specialist.get("controlling_specialist")
        detail["specialist_invoked"] = False
        detail["probability_publishable"] = False
        detail["can_execute"] = False
        raise HTTPException(status_code=422, detail=detail)

    scored_at = datetime.now(timezone.utc).isoformat()
    inference_request = _server_owned_inference_request(req, evidence, scored_at)
    effective_snapshot_id = str(evidence.get("source_snapshot_id") or req.source_snapshot_id)
    raw_market_a = _to_market_quote(req.market_side_a)
    raw_market_b = _to_market_quote(req.market_side_b)
    observed_rule = _to_settlement_rule(req.settlement_rule)

    provider_candidates = [
        normalize_provider(req.settlement_provider),
        normalize_provider(getattr(raw_market_a, "provider", None)),
        normalize_provider(getattr(raw_market_b, "provider", None)),
    ]
    provider_set = {value for value in provider_candidates if value}
    if len(provider_set) > 1:
        settlement_resolution = SettlementRuleResolution(
            status="HOLD", blocker=SETTLEMENT_RULE_CONFLICT, rule=None,
            authority=None, provider=None, rule_id=None, rule_version=None,
            source_ref=None, source_hash=None, money_semantics=None,
            observed_rule_status="NOT_EVALUATED_PROVIDER_CONFLICT", can_execute=False,
        )
    else:
        settlement_resolution = resolve_prop_settlement_rule(
            client=prod.get_client(),
            provider=next(iter(provider_set), None),
            sport=req.sport,
            stat_type=req.stat_type,
            period=_prop_period(req.stat_type),
            direction=req.direction,
            event_start_time=req.event_start_time,
            observed_rule=observed_rule,
        )
    settlement_rule = settlement_resolution.rule
    market_audit = audit_candidate_market(
        event_id=req.event_id,
        participant=str(evidence.get("player") or req.player),
        stat=req.stat_type,
        period=_prop_period(req.stat_type),
        line=req.line,
        settlement_rule=settlement_rule,
        side_a=raw_market_a,
        side_b=raw_market_b,
        # Exact line matching is server-owned. No caller may widen tolerance.
        line_tolerance=0.0,
    )
    try:
        result = score_discrete_prop_end_to_end(
            client=prod.get_client(),
            request=inference_request,
            event_start_time=req.event_start_time,
            player=str(evidence.get("player") or req.player),
            line=req.line,
            direction=req.direction,
            source_snapshot_id=effective_snapshot_id,
            features=_model_features(evidence),
            seed=req.seed,
            money_lane_status=req.money_lane_status,
            market_side_a=market_audit.side_a,
            market_side_b=market_audit.side_b,
        )
    except (PropFittedProviderUnavailable, PropDistributionContractError, PropCalibrationUnavailable) as exc:
        _raise_model_path_error(exc)

    if not result.row.probability_publishable:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PROP_MODEL_NOT_PUBLISHABLE",
                "data_gaps": result.row.data_gaps,
                "model_evidence": _discrete_model_evidence(result),
                "probability_publishable": False,
                "can_execute": False,
            },
        )

    persisted = prod.base_api._persist_fn(result.row)
    if not isinstance(persisted, dict) or not persisted.get("prediction_id"):
        raise HTTPException(
            status_code=500,
            detail={
                "code": "PROP_PREDICTION_LEDGER_WRITE_UNPROVEN",
                "probability_publishable": False,
                "can_execute": False,
            },
        )

    market_lane = _market_lane_with_audit(result.row, market_audit)
    settlement_lane = _settlement_lane(result.row, req, settlement_rule, settlement_resolution, market_audit.side_a, market_audit.side_b)
    money_lane = _effective_money_lane(result.row, settlement_lane)
    market_lane, settlement_lane, money_lane, arithmetic_audit = _apply_wolfram_arithmetic_gate(
        row=result.row,
        direction=req.direction,
        market_audit=market_audit,
        market_lane=market_lane,
        settlement_lane=settlement_lane,
        money_lane=money_lane,
        prediction_id=str(persisted["prediction_id"]),
    )
    probability_qualification = _probability_qualification(result.row, market_lane, money_lane, settlement_lane)
    return {
        "ok": True,
        "prediction": persisted,
        "acquisition_evidence": prod._visible_acquisition_evidence(evidence, req.line),
        "acquisition_repair": {
            "status": evidence.get("acquisition_repair_status"),
            "requested_source_snapshot_id": evidence.get("requested_source_snapshot_id") or str(req.source_snapshot_id),
            "effective_source_snapshot_id": evidence.get("effective_source_snapshot_id") or effective_snapshot_id,
            "attempts": evidence.get("acquisition_attempts") or [],
            "can_execute": False,
        },
        "model_evidence": _discrete_model_evidence(result),
        "probability_qualification": probability_qualification,
        "terminal_label": probability_qualification["terminal_label"],
        "pick_rejected": probability_qualification["pick_rejected"],
        "evidence": evidence,
        "objective_lanes": {
            "MODEL": {
                "status": "PASS",
                "probability_publishable": True,
                "can_execute": False,
            },
            "MARKET": market_lane,
            "SETTLEMENT": settlement_lane,
            "MONEY": money_lane,
            "ARITHMETIC_AUDIT": arithmetic_audit,
        },
        "backend_traversal": {
            "requester_model": model_identity,
            "render": "PASS",
            "supabase_capability": "PASS",
            "supabase_evidence": "PASS",
            "controlling_specialist": "PASS",
            "exact_route_artifact": "PASS",
            "governed_model": "PASS",
            "wolfram_arithmetic_audit": arithmetic_audit.get("verdict"),
            "wolfram_audit_ledger_write": arithmetic_audit.get("ledger_write"),
            "prediction_ledger_write": "PASS",
        },
        "route_preflight": {
            "status": "PASS",
            "certified_artifact_code": route_artifact.get("code"),
            "evidence_hydration_attempted": True,
            "can_execute": False,
        },
        "model_path": "WOW_PROP_FITTED_MODEL_V1->RAW_DISCRETE_DISTRIBUTION->CALIBRATION->PERSISTENCE",
        "probability_publishable": True,
        "can_execute": False,
    }


# Install the source-agnostic batch ingress only after the governed single-row
# scorer exists. The batch controller delegates every row back through
# score_prop; it never bypasses the existing specialist, hydration, model,
# calibration, persistence, market, or safety gates.
from pick_request_pipeline import install_pick_request_routes as _install_pick_request_routes

_install_pick_request_routes(
    app=app,
    score_prop_model=ScorePropRequest,
    score_prop_callable=score_prop,
    require_action_api_key=prod._require_action_api_key,
)


# Cross-sport write-before-display traceability. This records every terminal
# recommendation separately from the governed probability/calibration ledgers.
from recommendation_ledger_api import (
    install_recommendation_ledger_routes as _install_recommendation_ledger_routes,
)

_install_recommendation_ledger_routes(
    app=app,
    auth_dependency=Depends(prod._require_action_api_key),
    get_client_fn=prod.get_client,
)
