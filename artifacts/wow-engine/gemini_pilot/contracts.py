"""Fail-closed read-only contracts for the Gemini challenger pilot.

This module does not grant any trading, repository-write, database-write, or
release authority. A well-formed model response is an unverified finding.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceSource(StrictModel):
    source_id: str = Field(min_length=1, max_length=200)
    uri: str = Field(min_length=1, max_length=2048)
    kind: Literal["KALSHI_RULE", "SETTLEMENT_SOURCE", "OFFICIAL_WEATHER",
                  "MODEL_GUIDANCE", "GITHUB", "CI", "OTHER"]
    available_at: AwareDatetime
    retrieved_at: AwareDatetime
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PilotTask(StrictModel):
    contract_version: Literal["wow.gemini-pilot.task.v1"] = "wow.gemini-pilot.task.v1"
    task_id: str = Field(min_length=1, max_length=120)
    lane: Literal["KALSHI_CONTRACT_AUDIT", "WOW_DEFECT_DIAGNOSIS"]
    decision_time: AwareDatetime
    scope: str = Field(min_length=5, max_length=10000)
    source_material: str = Field(min_length=1, max_length=75000)
    evidence: list[EvidenceSource] = Field(min_length=1, max_length=30)
    can_execute: Literal[False] = False
    dry_run_only: Literal[True] = True

    @model_validator(mode="after")
    def enforce_point_in_time(self):
        ids = [e.source_id for e in self.evidence]
        if len(ids) != len(set(ids)):
            raise ValueError("DUPLICATE_SOURCE_ID")
        if any(e.available_at > self.decision_time for e in self.evidence):
            raise ValueError("POST_DECISION_EVIDENCE")
        if any(e.retrieved_at > self.decision_time for e in self.evidence):
            raise ValueError("POST_DECISION_RETRIEVAL")
        if any(e.available_at > e.retrieved_at for e in self.evidence):
            raise ValueError("SOURCE_NOT_AVAILABLE_AT_RETRIEVAL")
        return self


class Finding(StrictModel):
    claim: str = Field(min_length=5, max_length=3000)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    severity: Literal["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"]
    recommended_check: str = Field(min_length=3, max_length=2000)


class ModelAssessment(StrictModel):
    findings: list[Finding] = Field(default_factory=list, max_length=40)
    contradictions: list[str] = Field(default_factory=list, max_length=20)
    missing_evidence: list[str] = Field(default_factory=list, max_length=30)
    narrative: str = Field(min_length=12, max_length=5000)

    @model_validator(mode="after")
    def explanation_required(self):
        if not self.narrative.strip():
            raise ValueError("EMPTY_ASSESSMENT_EXPLANATION")
        return self


class ReceiptEvidence(StrictModel):
    source_id: str = Field(min_length=1, max_length=200)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    available_at: AwareDatetime
    retrieved_at: AwareDatetime


class PilotReceipt(StrictModel):
    contract_version: Literal["wow.gemini-pilot.receipt.v1"] = "wow.gemini-pilot.receipt.v1"
    task_id: str
    lane: Literal["KALSHI_CONTRACT_AUDIT", "WOW_DEFECT_DIAGNOSIS"]
    provider: Literal["gemini"] = "gemini"
    outcome: Literal["CHALLENGER_FINDINGS_UNVERIFIED", "BLOCKED"]
    assessment: ModelAssessment | None = None
    blocker: str | None = None
    source_ids: list[str] = Field(min_length=1, max_length=30)
    evidence_provenance: list[ReceiptEvidence] = Field(min_length=1, max_length=30)
    decision_time: AwareDatetime
    task_input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_id: str | None = None
    sdk_version: str | None = None
    prompt_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    can_execute: Literal[False] = False
    verified_by_independent_reviewer: Literal[False] = False

    @model_validator(mode="after")
    def coherent_state(self):
        if self.outcome == "BLOCKED" and not self.blocker:
            raise ValueError("BLOCKER_REQUIRED")
        if self.outcome == "CHALLENGER_FINDINGS_UNVERIFIED":
            if self.assessment is None:
                raise ValueError("ASSESSMENT_REQUIRED")
            if not self.model_id:
                raise ValueError("MODEL_ID_REQUIRED")
        provenance_ids = [item.source_id for item in self.evidence_provenance]
        if (len(set(self.source_ids)) != len(self.source_ids)
                or len(set(provenance_ids)) != len(provenance_ids)
                or set(self.source_ids) != set(provenance_ids)):
            raise ValueError("RECEIPT_SOURCE_PROVENANCE_MISMATCH")
        if any(item.available_at > self.decision_time or item.retrieved_at > self.decision_time
               or item.available_at > item.retrieved_at for item in self.evidence_provenance):
            raise ValueError("RECEIPT_POINT_IN_TIME_INVALID")
        if self.assessment:
            allowed = set(self.source_ids)
            for item in self.assessment.findings:
                if not set(item.evidence_ids).issubset(allowed):
                    raise ValueError("UNREGISTERED_EVIDENCE_REFERENCE")
        return self
