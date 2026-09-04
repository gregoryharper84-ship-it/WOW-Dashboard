"""Governed V17 detailed evidence envelope utilities.

This module validates and freezes rich pregame research context before the
controlling specialist/model boundary.  It deliberately does *not* turn
research, market prices, hit rates, or narrative judgments into sporting
probability.  Only feature items explicitly typed MODEL_INPUT, REGIME_INPUT,
or CALIBRATION_INPUT are exposed as candidates to a certified specialist;
actual numerical use remains adapter/artifact-owned.

Market evidence remains a separate downstream contract.  can_execute is false
throughout the V17 host.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

FeatureStatus = Literal[
    "MODEL_INPUT",
    "REGIME_INPUT",
    "CALIBRATION_INPUT",
    "MARKET_EVIDENCE",
    "EVIDENCE_ONLY",
]
EvidenceStatus = Literal["AVAILABLE", "PARTIAL", "UNAVAILABLE", "NOT_APPLICABLE"]
MarketState = Literal["EXACT_LINE", "ADJACENT_LINE", "NO_MARKET"]

EVIDENCE_FAMILIES = (
    "recent_form",
    "head_to_head",
    "player_performance",
    "lineup_availability_depth",
    "tactical_style",
    "match_context_stakes",
    "environment",
    "officiating",
    "schedule_fatigue_travel",
    "advanced_statistics",
)

_RESERVED_GOVERNED_OUTPUT_NAMES = {
    "raw_model_probability",
    "model_probability",
    "calibrated_probability",
    "calibrated_lower_bound",
    "calibrated_upper_bound",
    "probability_publishable",
    "rank_eligible",
    "terminal_label",
    "terminal_status",
    "final_approved",
    "edge",
}


class DetailedEvidenceItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=1, max_length=160)
    value: Any = None
    feature_status: FeatureStatus
    source: str = Field(min_length=1, max_length=512)
    source_type: str | None = Field(default=None, max_length=96)
    as_of: str
    sample_size: int | None = Field(default=None, ge=0)
    data_quality: float | None = Field(default=None, ge=0.0, le=1.0)
    certainty: float | None = Field(default=None, ge=0.0, le=1.0)
    transform_version: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def _reject_governed_output_injection(self) -> "DetailedEvidenceItem":
        normalized = "_".join(self.name.strip().lower().replace("-", " ").split())
        if normalized in _RESERVED_GOVERNED_OUTPUT_NAMES:
            raise ValueError("GOVERNED_OUTPUT_FIELD_NOT_ALLOWED_AS_EVIDENCE")
        return self


class DetailedEvidenceFamily(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: EvidenceStatus
    sample_window: str | None = Field(default=None, max_length=160)
    sample_size: int | None = Field(default=None, ge=0)
    data_quality: float | None = Field(default=None, ge=0.0, le=1.0)
    certainty: float | None = Field(default=None, ge=0.0, le=1.0)
    items: list[DetailedEvidenceItem] = Field(default_factory=list, max_length=256)


class DetailedEvidenceFamilies(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recent_form: DetailedEvidenceFamily
    head_to_head: DetailedEvidenceFamily
    player_performance: DetailedEvidenceFamily
    lineup_availability_depth: DetailedEvidenceFamily
    tactical_style: DetailedEvidenceFamily
    match_context_stakes: DetailedEvidenceFamily
    environment: DetailedEvidenceFamily
    officiating: DetailedEvidenceFamily
    schedule_fatigue_travel: DetailedEvidenceFamily
    advanced_statistics: DetailedEvidenceFamily


class DetailedMarketEvidence(BaseModel):
    model_config = ConfigDict(extra="allow")

    market_state: MarketState
    platform: str | None = None
    market: str | None = None
    side: str | None = None
    line: float | str | None = None
    price: float | str | None = None
    source: str | None = None
    as_of: str | None = None
    implied_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    no_vig_probability: float | None = Field(default=None, ge=0.0, le=1.0)


class DetailedEvidenceEnvelope(BaseModel):
    model_config = ConfigDict(extra="allow")

    candidate_id: str = Field(min_length=1, max_length=256)
    sport: str = Field(min_length=1, max_length=64)
    event_id: str = Field(min_length=1, max_length=256)
    as_of: str
    controlling_specialist: str | None = Field(default=None, max_length=256)
    evidence_families: DetailedEvidenceFamilies
    market_evidence: DetailedMarketEvidence
    source_conflicts: list[str] = Field(default_factory=list, max_length=128)
    lineup_certainty: float | None = Field(default=None, ge=0.0, le=1.0)
    role_certainty: float | None = Field(default=None, ge=0.0, le=1.0)
    starter_certainty: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence_quality: float | None = Field(default=None, ge=0.0, le=1.0)
    notes: list[str] = Field(default_factory=list, max_length=128)


def _aware(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field}:INVALID_TIMESTAMP") from exc
    if parsed.utcoffset() is None:
        raise ValueError(f"{field}:TIMEZONE_REQUIRED")
    return parsed.astimezone(timezone.utc)


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def evidence_fingerprint(payload: DetailedEvidenceEnvelope | dict[str, Any]) -> str:
    if isinstance(payload, DetailedEvidenceEnvelope):
        data = payload.model_dump(mode="json", exclude_none=True)
    else:
        data = DetailedEvidenceEnvelope.model_validate(payload).model_dump(mode="json", exclude_none=True)
    return sha256(_canonical_json(data).encode("utf-8")).hexdigest()


def validate_detailed_evidence(
    payload: DetailedEvidenceEnvelope | dict[str, Any],
    *,
    event_id: str,
    sport: str,
    event_start_time: str,
    now: datetime | None = None,
) -> DetailedEvidenceEnvelope:
    """Validate identity + temporal provenance without interpreting evidence.

    The packet can be rich and sport-specific, but it may not claim a governed
    probability.  All timestamps must have been knowable before the event and
    not in the future at scoring time.
    """
    envelope = payload if isinstance(payload, DetailedEvidenceEnvelope) else DetailedEvidenceEnvelope.model_validate(payload)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    event_start = _aware(event_start_time, "event_start_time")
    envelope_as_of = _aware(envelope.as_of, "detailed_evidence.as_of")

    if str(envelope.event_id).strip() != str(event_id).strip():
        raise ValueError("DETAILED_EVIDENCE_EVENT_ID_MISMATCH")
    if str(envelope.sport).strip().upper() != str(sport).strip().upper():
        raise ValueError("DETAILED_EVIDENCE_SPORT_MISMATCH")
    if envelope_as_of > current:
        raise ValueError("DETAILED_EVIDENCE_AS_OF_IN_FUTURE")
    if envelope_as_of >= event_start:
        raise ValueError("DETAILED_EVIDENCE_NOT_PREGAME")

    for family_name in EVIDENCE_FAMILIES:
        family = getattr(envelope.evidence_families, family_name)
        if family.status in {"UNAVAILABLE", "NOT_APPLICABLE"} and family.items:
            raise ValueError(f"DETAILED_EVIDENCE_{family_name.upper()}_STATUS_CONFLICT")
        for item in family.items:
            item_as_of = _aware(item.as_of, f"detailed_evidence.{family_name}.{item.name}.as_of")
            if item_as_of > current:
                raise ValueError("DETAILED_EVIDENCE_ITEM_AS_OF_IN_FUTURE")
            if item_as_of >= event_start:
                raise ValueError("DETAILED_EVIDENCE_ITEM_NOT_PREGAME")
            if isinstance(item.value, float) and not math.isfinite(item.value):
                raise ValueError("DETAILED_EVIDENCE_NONFINITE_VALUE")

    if envelope.market_evidence.as_of:
        market_as_of = _aware(envelope.market_evidence.as_of, "detailed_evidence.market_evidence.as_of")
        if market_as_of > current:
            raise ValueError("DETAILED_MARKET_EVIDENCE_AS_OF_IN_FUTURE")
        if market_as_of >= event_start:
            raise ValueError("DETAILED_MARKET_EVIDENCE_NOT_PREGAME")

    return envelope


def compile_feature_candidates(envelope: DetailedEvidenceEnvelope | dict[str, Any]) -> dict[str, Any]:
    """Expose typed feature *candidates* without granting numerical authority.

    A fitted adapter must explicitly consume a candidate before it can alter a
    probability distribution.  MARKET_EVIDENCE and EVIDENCE_ONLY never enter
    this structure.
    """
    env = envelope if isinstance(envelope, DetailedEvidenceEnvelope) else DetailedEvidenceEnvelope.model_validate(envelope)
    buckets: dict[str, dict[str, Any]] = {
        "MODEL_INPUT": {},
        "REGIME_INPUT": {},
        "CALIBRATION_INPUT": {},
    }
    provenance: dict[str, Any] = {}
    for family_name in EVIDENCE_FAMILIES:
        family = getattr(env.evidence_families, family_name)
        for item in family.items:
            if item.feature_status not in buckets:
                continue
            key = f"{family_name}.{item.name.strip()}"
            buckets[item.feature_status][key] = item.value
            provenance[key] = {
                "source": item.source,
                "source_type": item.source_type,
                "as_of": item.as_of,
                "sample_size": item.sample_size,
                "data_quality": item.data_quality,
                "certainty": item.certainty,
                "transform_version": item.transform_version,
                "feature_status": item.feature_status,
            }
    return {
        **buckets,
        "provenance": provenance,
        "numerical_authority": "CONTROLLING_SPECIALIST_ADAPTER_ONLY",
        "market_evidence_forwarded_to_model": False,
        "can_execute": False,
    }


def evidence_summary(envelope: DetailedEvidenceEnvelope | dict[str, Any]) -> dict[str, Any]:
    env = envelope if isinstance(envelope, DetailedEvidenceEnvelope) else DetailedEvidenceEnvelope.model_validate(envelope)
    counts = {status: 0 for status in (
        "MODEL_INPUT", "REGIME_INPUT", "CALIBRATION_INPUT", "MARKET_EVIDENCE", "EVIDENCE_ONLY"
    )}
    family_status = {}
    for family_name in EVIDENCE_FAMILIES:
        family = getattr(env.evidence_families, family_name)
        family_status[family_name] = family.status
        for item in family.items:
            counts[item.feature_status] += 1
    return {
        "status": "VALIDATED",
        "fingerprint": evidence_fingerprint(env),
        "candidate_id": env.candidate_id,
        "event_id": env.event_id,
        "sport": env.sport.upper(),
        "as_of": env.as_of,
        "family_status": family_status,
        "feature_status_counts": counts,
        "market_state": env.market_evidence.market_state,
        "source_conflict_count": len(env.source_conflicts),
        "evidence_quality": env.evidence_quality,
        "lineup_certainty": env.lineup_certainty,
        "role_certainty": env.role_certainty,
        "starter_certainty": env.starter_certainty,
        "probability_claimed": False,
        "can_execute": False,
    }


__all__ = [
    "DetailedEvidenceEnvelope",
    "DetailedEvidenceFamilies",
    "DetailedEvidenceFamily",
    "DetailedEvidenceItem",
    "DetailedMarketEvidence",
    "compile_feature_candidates",
    "evidence_fingerprint",
    "evidence_summary",
    "validate_detailed_evidence",
]
