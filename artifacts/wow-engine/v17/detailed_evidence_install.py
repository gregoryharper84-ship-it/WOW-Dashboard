"""Install the V17 detailed-evidence extension on the canonical production app.

The canonical Render entrypoint remains api_ncaaf_acceptance:app.  This module
is invoked from the existing V17 daily-route installer after the legacy prop
and team/event routes already exist.  It is additive, idempotent, and never
owns sporting probability or terminal publication.
"""
from __future__ import annotations

import uuid
from contextvars import ContextVar
from hashlib import sha256
from typing import Any, Optional

from fastapi import Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

import pick_request_runtime as pick_runtime
from v17 import team_event_request_runtime as team_event_runtime
from v17.detailed_evidence_runtime import (
    DetailedEvidenceEnvelope,
    compile_feature_candidates,
    evidence_fingerprint,
    evidence_summary,
    validate_detailed_evidence,
)

_INSTALLED_ATTR = "_wow_v17_detailed_evidence_installed"
_FEATURE_PATCH_ATTR = "_wow_v17_detailed_feature_patch_installed"
_SNAPSHOT_PATCH_ATTR = "_wow_v17_detailed_snapshot_patch_installed"
_TEAM_PATCH_ATTR = "_wow_v17_detailed_team_patch_installed"

_detailed_by_row_key: ContextVar[dict[str, DetailedEvidenceEnvelope]] = ContextVar(
    "wow_v17_detailed_evidence_by_row_key", default={}
)


class DetailedRawPropEvidence(pick_runtime.RawPropEvidence):
    model_config = ConfigDict(extra="forbid")
    detailed_evidence: DetailedEvidenceEnvelope | None = None


class DetailedPickRequestRow(pick_runtime.PickRequestRow):
    model_config = ConfigDict(extra="forbid")
    evidence: DetailedRawPropEvidence | None = None


class DetailedPickRequestBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: Optional[str] = None
    rows: list[DetailedPickRequestRow] = Field(min_length=1, max_length=50)


def _patch_model_features(market_api: Any) -> None:
    if getattr(market_api, _FEATURE_PATCH_ATTR, False):
        return
    original = market_api._model_features

    def model_features_with_detailed_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
        features = dict(original(evidence))
        detailed = evidence.get("detailed_evidence")
        if isinstance(detailed, dict):
            features["detailed_evidence"] = detailed
            features["detailed_feature_candidates"] = compile_feature_candidates(detailed)
        return features

    market_api._model_features = model_features_with_detailed_evidence
    setattr(market_api, _FEATURE_PATCH_ATTR, True)


def _patch_snapshot_payload() -> None:
    if getattr(pick_runtime, _SNAPSHOT_PATCH_ATTR, False):
        return
    original = pick_runtime._snapshot_payload

    def snapshot_payload_with_detailed_evidence(row: Any, normalized: dict[str, Any]):
        snapshot_id, base_fingerprint, persisted = original(row, normalized)
        row_key = str(getattr(row, "row_key", "") or "")
        detailed = _detailed_by_row_key.get().get(row_key)
        if detailed is None:
            return snapshot_id, base_fingerprint, persisted

        detail_payload = detailed.model_dump(mode="json", exclude_none=True)
        detail_fingerprint = evidence_fingerprint(detailed)
        combined_fingerprint = sha256(
            f"{base_fingerprint}:{detail_fingerprint}".encode("utf-8")
        ).hexdigest()
        combined_snapshot_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"wow-prop-evidence:{combined_fingerprint}")
        )
        enriched = dict(persisted)
        enriched["source_snapshot_id"] = combined_snapshot_id
        enriched["detailed_evidence"] = detail_payload
        enriched["detailed_evidence_fingerprint"] = detail_fingerprint
        return combined_snapshot_id, combined_fingerprint, enriched

    pick_runtime._snapshot_payload = snapshot_payload_with_detailed_evidence
    setattr(pick_runtime, _SNAPSHOT_PATCH_ATTR, True)


def _patch_team_event_canonicalizer() -> None:
    if getattr(team_event_runtime, _TEAM_PATCH_ATTR, False):
        return
    original = team_event_runtime._canonicalize_public_mlb_request

    def canonicalize_with_detailed_evidence(req: Any, event_api: Any):
        supplied = None
        if isinstance(getattr(req, "sport_specific_evidence", None), dict):
            supplied = req.sport_specific_evidence.get("v17_detailed_evidence")

        canonical = original(req, event_api)
        if supplied is None:
            return canonical

        try:
            detailed = validate_detailed_evidence(
                supplied,
                event_id=req.official_event_id,
                sport=req.sport,
                event_start_time=req.event_start_time_utc,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "DETAILED_EVIDENCE_INVALID",
                    "event_key": req.event_key,
                    "blocker": str(exc),
                    "probability_publishable": False,
                    "can_execute": False,
                },
            ) from exc

        merged = dict(canonical.sport_specific_evidence or {})
        merged["v17_detailed_evidence"] = detailed.model_dump(mode="json", exclude_none=True)
        merged["v17_detailed_feature_candidates"] = compile_feature_candidates(detailed)
        merged["v17_detailed_evidence_summary"] = evidence_summary(detailed)
        return canonical.model_copy(update={"sport_specific_evidence": merged})

    team_event_runtime._canonicalize_public_mlb_request = canonicalize_with_detailed_evidence
    setattr(team_event_runtime, _TEAM_PATCH_ATTR, True)


def install_v17_detailed_evidence(
    app: Any,
    *,
    auth_dependency: Any,
    market_api: Any,
) -> None:
    """Mount detailed evidence on the already-constructed canonical V17 app."""
    if getattr(app.state, _INSTALLED_ATTR, False):
        return

    _patch_model_features(market_api)
    _patch_snapshot_payload()
    _patch_team_event_canonicalizer()

    original_route = next(
        (
            route
            for route in app.router.routes
            if getattr(route, "path", None) == "/score-pick-request"
            and "POST" in (getattr(route, "methods", set()) or set())
        ),
        None,
    )
    if original_route is None:
        raise RuntimeError("V17_DETAILED_EVIDENCE_PICK_ROUTE_NOT_FOUND")
    original_endpoint = original_route.endpoint
    dependencies = list(getattr(original_route, "dependencies", None) or [])
    app.router.routes[:] = [route for route in app.router.routes if route is not original_route]

    @app.post(
        "/score-pick-request",
        dependencies=dependencies,
        operation_id="scoreWowPickRequest",
    )
    def score_pick_request_with_detailed_evidence(
        batch: DetailedPickRequestBatch,
        x_wow_model_identity: Optional[str] = Header(
            default=None, alias="X-WOW-Model-Identity"
        ),
    ):
        base_rows: list[pick_runtime.PickRequestRow] = []
        detail_map: dict[str, DetailedEvidenceEnvelope] = {}
        detail_summaries: dict[str, dict[str, Any]] = {}

        for index, input_row in enumerate(batch.rows):
            row_key = input_row.row_key or f"row-{index + 1}"
            row_payload = input_row.model_dump(mode="python")
            row_payload["row_key"] = row_key
            evidence_payload = row_payload.get("evidence")

            if isinstance(evidence_payload, dict):
                detailed_payload = evidence_payload.pop("detailed_evidence", None)
                if detailed_payload is not None:
                    try:
                        detailed = validate_detailed_evidence(
                            detailed_payload,
                            event_id=input_row.event_id,
                            sport=input_row.sport,
                            event_start_time=input_row.event_start_time,
                        )
                    except Exception as exc:
                        raise HTTPException(
                            status_code=422,
                            detail={
                                "code": "DETAILED_EVIDENCE_INVALID",
                                "row_key": row_key,
                                "blocker": str(exc),
                                "probability_publishable": False,
                                "can_execute": False,
                            },
                        ) from exc
                    detail_map[row_key] = detailed
                    detail_summaries[row_key] = evidence_summary(detailed)

            base_rows.append(pick_runtime.PickRequestRow.model_validate(row_payload))

        base_batch = pick_runtime.PickRequestBatch(
            request_id=batch.request_id,
            rows=base_rows,
        )
        token = _detailed_by_row_key.set(detail_map)
        try:
            result = original_endpoint(
                base_batch,
                x_wow_model_identity=x_wow_model_identity,
            )
        finally:
            _detailed_by_row_key.reset(token)

        if isinstance(result, dict) and isinstance(result.get("rows"), list):
            for outcome in result["rows"]:
                if not isinstance(outcome, dict):
                    continue
                key = str(outcome.get("row_key") or "")
                summary = detail_summaries.get(key)
                if summary is not None:
                    outcome["detailed_evidence"] = {
                        **summary,
                        "snapshot_status": (
                            "FROZEN" if outcome.get("source_snapshot_id") else "VALIDATED_NOT_FROZEN"
                        ),
                        "numerical_authority": "CONTROLLING_SPECIALIST_ADAPTER_ONLY",
                        "market_evidence_forwarded_to_model": False,
                        "can_execute": False,
                    }
            result["detailed_evidence_contract"] = {
                "version": "V17_DETAILED_EVIDENCE_V1",
                "rows_with_detailed_evidence": len(detail_map),
                "governed_probability_substitution_allowed": False,
                "market_evidence_separate": True,
                "can_execute": False,
            }
        return result

    @app.get(
        "/v17/detailed-evidence-contract",
        dependencies=[auth_dependency],
        operation_id="getWowV17DetailedEvidenceContract",
    )
    def get_v17_detailed_evidence_contract():
        return {
            "status": "ACTIVE",
            "contract_version": "V17_DETAILED_EVIDENCE_V1",
            "prop_boundary": "/score-pick-request",
            "team_event_boundary": "/score-team-event",
            "feature_candidate_statuses": [
                "MODEL_INPUT",
                "REGIME_INPUT",
                "CALIBRATION_INPUT",
            ],
            "non_model_statuses": ["MARKET_EVIDENCE", "EVIDENCE_ONLY"],
            "numerical_authority": "CONTROLLING_SPECIALIST_ADAPTER_ONLY",
            "market_evidence_separate": True,
            "probability_substitution_allowed": False,
            "global_terminal_authority": "V17_TERMINAL_REDUCER",
            "can_execute": False,
        }

    setattr(app.state, _INSTALLED_ATTR, True)


__all__ = [
    "DetailedPickRequestBatch",
    "DetailedPickRequestRow",
    "DetailedRawPropEvidence",
    "install_v17_detailed_evidence",
]
