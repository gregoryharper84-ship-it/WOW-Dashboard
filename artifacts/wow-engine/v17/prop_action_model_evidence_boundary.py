"""Exact registered PLAYER_PROP evidence-triad gate for V17 Action scoring.

Read-only. The certified route identity and the server-owned inference identity
are required before a fitted prop model may be invoked or persisted. No client
model metadata, odds, estimates or raw research can substitute for evidence.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from fastapi import HTTPException

from v17.model_evidence_bundle_contract import ModelBundleScope
from v17.model_evidence_registry_readers import assess_registry_model_bundle

CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
INCOMPLETE = "MODEL_EVIDENCE_TRIAD_INCOMPLETE"
UNRESOLVED = "MODEL_EVIDENCE_TRIAD_IDENTITY_UNRESOLVED"
READER_UNAVAILABLE = "MODEL_EVIDENCE_TRIAD_READER_UNAVAILABLE"

AssessmentReader = Callable[[Any, ModelBundleScope], Mapping[str, Any]]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _hold(code: str, reasons: list[str], *, component_sources: Any = None,
          missing_component_fields: Any = None) -> None:
    """Raise a typed, non-scoring hold; never leak a database exception."""
    raise HTTPException(
        status_code=409,
        detail={
            "code": code,
            "blocker_code": reasons[0] if reasons else code,
            "blockers": list(dict.fromkeys(reasons or [code])),
            "failed_contract_scope": ["MODEL_EVIDENCE_TRIAD"],
            "terminal_label": "MODEL_EVIDENCE_TRIAD_BLOCKED",
            "component_sources": (
                component_sources if isinstance(component_sources, Mapping) else {}
            ),
            "missing_component_fields": (
                missing_component_fields
                if isinstance(missing_component_fields, Mapping) else {}
            ),
            "model_evaluated": False,
            "specialist_invoked": False,
            "scoring_attempted": False,
            "probability_publishable": False,
            "rank_eligible": False,
            "global_terminal_authority": TERMINAL_AUTHORITY,
            "can_execute": False,
        },
    )


def require_prop_action_triad(
    *,
    db: Any,
    req: Any,
    route_artifact: Mapping[str, Any],
    inference_request: Any,
    scored_at: str,
    period: str,
    assessor: AssessmentReader | None = None,
) -> dict[str, Any]:
    """Read the exact receipt-bound triad prior to fitted prop inference.

    `inference_request.player_id` must come from the server's frozen-evidence
    identity resolution; a public player name is not a canonical player ID.
    Unknown route fields block; they cannot be borrowed from another model.
    The Action's numeric line is kept as its literal value and never shifted to
    an adjacent threshold (a "7+" string cannot pass the numeric Action schema).
    """
    if not isinstance(route_artifact, Mapping):
        _hold(UNRESOLVED, ["MODEL_BUNDLE_ROUTE_INVALID"])
    family = _text(route_artifact.get("model_family"))
    version = _text(route_artifact.get("model_artifact_version"))
    schema = _text(route_artifact.get("feature_schema_version"))
    player_id = _text(getattr(inference_request, "player_id", None))
    sport = _text(getattr(req, "sport", None)).upper()
    stat = _text(getattr(req, "stat_type", None)).upper()
    direction = _text(getattr(req, "direction", None)).upper()
    event_id = _text(getattr(req, "event_id", None))
    event_start = _text(getattr(req, "event_start_time", None))
    if (
        not all((family, version, schema, player_id, sport, stat, direction,
                 event_id, event_start, _text(period), _text(scored_at)))
        or not isinstance(getattr(req, "line", None), (int, float))
        or type(req.line) is bool
    ):
        _hold(UNRESOLVED, ["MODEL_BUNDLE_SCOPE_IDENTITY_UNAVAILABLE"])
    if db is None:
        _hold(READER_UNAVAILABLE, ["MODEL_BUNDLE_DB_UNAVAILABLE"])

    scope = ModelBundleScope(
        model_kind="PLAYER_PROP",
        sport=sport,
        model_family=family,
        model_artifact_version=version,
        feature_schema_version=schema,
        official_event_id=event_id,
        event_start_time=event_start,
        prediction_timestamp=scored_at,
        market_family="PLAYER_PROP",
        player_id=player_id,
        stat_type=stat,
        period=_text(period),
        line=str(req.line),
        direction=direction,
    )
    try:
        report = (assessor or assess_registry_model_bundle)(db, scope)
    except Exception:
        _hold(READER_UNAVAILABLE, ["MODEL_BUNDLE_VERIFICATION_RECEIPT_READ_FAILED"])

    if not isinstance(report, Mapping):
        _hold(READER_UNAVAILABLE, ["MODEL_BUNDLE_READER_CONTRACT_INVALID"])
    # An assessor must return the same *exact* model, not a neighbouring
    # event/player/stat/line. Never trust a claimed PASS or registry columns.
    if any((
        report.get("model_kind") != scope.model_kind,
        report.get("sport") != scope.sport,
        report.get("model_family") != scope.model_family,
        report.get("model_artifact_version") != scope.model_artifact_version,
    )):
        _hold(READER_UNAVAILABLE, ["MODEL_BUNDLE_READER_CONTRACT_INVALID"])

    reasons = report.get("blockers")
    if not isinstance(reasons, (list, tuple)) or not all(
        isinstance(reason, str) and reason for reason in reasons
    ):
        _hold(READER_UNAVAILABLE, ["MODEL_BUNDLE_READER_CONTRACT_INVALID"])
    if (
        report.get("status") != "READY_FOR_INDEPENDENT_REVIEW"
        or report.get("bundle_complete") is not True
        or bool(reasons)
        or report.get("probability_publishable") is not False
        or report.get("rank_eligible") is not False
        or report.get("can_execute") is not False
    ):
        _hold(
            INCOMPLETE,
            list(reasons) or ["MODEL_BUNDLE_EVIDENCE_UNVERIFIED"],
            component_sources=report.get("component_sources"),
            missing_component_fields=report.get("missing_component_fields"),
        )

    # This only clears an evidence *precondition*. It is not a certified pick,
    # valid probability, calibration/lower bound, or reducer decision.
    return {
        "status": "READY_FOR_INDEPENDENT_REVIEW",
        "model_evidence_triad_complete": True,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
    }


__all__ = [
    "CAN_EXECUTE", "TERMINAL_AUTHORITY", "INCOMPLETE", "UNRESOLVED",
    "READER_UNAVAILABLE", "require_prop_action_triad",
]
