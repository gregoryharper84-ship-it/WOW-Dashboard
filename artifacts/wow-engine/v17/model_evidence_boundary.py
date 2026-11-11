"""Serve-time evidence-triad gate for scored V17 team/event output.

The registry readers answer whether one exact model version has a verified
artifact, calibrator and pregame corpus. This module applies that answer at the
scoring boundary: the scored package is preserved for diagnosis, but no row may
claim publication, ranking, official status or a non-hold terminal ceiling while
its triad is incomplete, unresolved or unreadable.

It never edits a probability, bound, coefficient or settlement fact, never
promotes an artifact, and keeps ``can_execute`` false everywhere.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
from typing import Any, Callable, Mapping

from v17.model_evidence_bundle_contract import ModelBundleScope
from v17.model_evidence_registry_readers import (
    assess_registry_model_bundle,
    lane_configured,
)

CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
HOLD = "MODEL_QUALIFIED_HOLD"

COMPLETE = "COMPLETE"
INCOMPLETE = "INCOMPLETE"
IDENTITY_UNRESOLVED = "IDENTITY_UNRESOLVED"
EVIDENCE_UNAVAILABLE = "EVIDENCE_UNAVAILABLE"
STATUSES = (COMPLETE, INCOMPLETE, IDENTITY_UNRESOLVED, EVIDENCE_UNAVAILABLE)

TRIAD_INCOMPLETE = "MODEL_EVIDENCE_TRIAD_INCOMPLETE"
TRIAD_IDENTITY_UNRESOLVED = "MODEL_EVIDENCE_TRIAD_IDENTITY_UNRESOLVED"
TRIAD_READER_UNAVAILABLE = "MODEL_EVIDENCE_TRIAD_READER_UNAVAILABLE"
TRIAD_LANE_NOT_CONFIGURED = "MODEL_EVIDENCE_TRIAD_LANE_NOT_CONFIGURED"

# Only labels that assert approval may be replaced by the hold ceiling. Any other
# upstream label is already at least as strict and is preserved verbatim.
_APPROVED_TERMINALS = frozenset({
    "FINAL_APPROVED",
    "APPROVED",
    "PUBLISHABLE",
    "QUALIFIED",
    "MONEY_QUALIFIED",
    "MARKET_VERIFIED_HOLD",
    "PASS",
    "COMPLETE",
})

# Reports that reached the guard through the scoring boundary are sealed here.
# A payload cannot elevate itself by asserting bundle_complete: the seal binds the
# report id to a per-process key and to the exact report digest, and a report whose
# bundle_complete field is altered no longer matches its digest.
_SEAL_KEY = secrets.token_hex(32)
_SEALED_REPORTS: dict[str, tuple[str, str]] = {}
_SEAL_LOCK = threading.Lock()
_MAX_SEALED_REPORTS = 4096

AssessmentCallable = Callable[[Any, ModelBundleScope], Mapping[str, Any]]

# Acceptance/test seam only. Production leaves this None so the real governed
# registry readers always run; it exists so a fixture can declare a verified
# registry without fabricating database rows.
EVIDENCE_ASSESSOR: AssessmentCallable | None = None


def _text(value: Any) -> str:
    return str(value or "").strip()


def triad_required(*, sport: Any = None, candidate_family: Any = None) -> bool:
    """Require the triad at the trusted team/event publication boundary.

    This entrypoint is exclusively a TEAM_EVENT official-publication guard.
    A request/body field cannot turn off this requirement by clearing sport,
    inventing a sport alias, or claiming candidate_family=UPSET. The scorer
    manifest still controls which fitted specialist may produce a result;
    this function only refuses to treat missing identity as an exemption.
    """
    return True


def _report_digest(report: Mapping[str, Any]) -> str:
    # Bind the *entire* issued assessment and its exact event/model identity,
    # including component sources, missing fields and typed blockers. Only the
    # two seal fields are excluded to avoid circular hashing.
    canonical = json.dumps(
        {
            key: value
            for key, value in report.items()
            if key not in {"report_id", "report_digest"}
        },
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hmac.new(
        _SEAL_KEY.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def seal_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Bind a boundary-produced report to this process for downstream verification."""
    sealed = dict(report)
    report_id = secrets.token_hex(12)
    digest = _report_digest(sealed)
    with _SEAL_LOCK:
        while len(_SEALED_REPORTS) >= _MAX_SEALED_REPORTS:
            _SEALED_REPORTS.pop(next(iter(_SEALED_REPORTS)))
        _SEALED_REPORTS[report_id] = (_text(sealed.get("sport")).upper(), digest)
    sealed["report_id"] = report_id
    sealed["report_digest"] = digest
    return sealed


def verify_sealed_report(
    report: Any,
    *,
    sport: Any = None,
    official_event_id: Any = None,
    model_artifact_version: Any = None,
) -> bool:
    """Only a complete report sealed for *this event and model* can authorize."""
    if not isinstance(report, Mapping) or report.get("bundle_complete") is not True:
        return False
    if report.get("status") != COMPLETE or report.get("blockers"):
        return False
    if (
        report.get("model_kind") != "TEAM_EVENT"
        or not _text(sport)
        or not _text(official_event_id)
        or not _text(model_artifact_version)
        or _text(report.get("official_event_id")) != _text(official_event_id)
        or _text(report.get("model_artifact_version")) != _text(model_artifact_version)
    ):
        return False
    report_id = _text(report.get("report_id"))
    digest = _text(report.get("report_digest"))
    if not report_id or not digest:
        return False
    with _SEAL_LOCK:
        sealed = _SEALED_REPORTS.get(report_id)
    if sealed is None:
        return False
    sealed_sport, sealed_digest = sealed
    if sealed_sport != _text(sport).upper():
        return False
    if not hmac.compare_digest(sealed_digest, _report_digest(report)):
        return False
    return hmac.compare_digest(sealed_digest, digest)


def _mlb_identity() -> dict[str, str]:
    try:
        from v17.mlb_event_feature_contract import (
            FEATURE_SCHEMA_VERSION,
            MODEL_ARTIFACT_VERSION,
            MODEL_FAMILY,
        )
    except Exception:  # noqa: BLE001 - an unavailable contract is not identity
        return {}
    return {
        "model_family": str(MODEL_FAMILY),
        "model_artifact_version": str(MODEL_ARTIFACT_VERSION),
        "feature_schema_version": str(FEATURE_SCHEMA_VERSION),
    }


def _nfl_identity() -> dict[str, str]:
    try:
        from nfl_event_model_contract import FEATURE_SCHEMA_VERSION
    except Exception:  # noqa: BLE001 - an unavailable contract is not identity
        return {}
    # NFL declares no repository-wide artifact version; the served model must
    # name its exact version. Schema remains the governed P2 contract.
    return {"feature_schema_version": str(FEATURE_SCHEMA_VERSION)}


def evidence_db_client(event_api: Any) -> Any:
    """Return the governed read client for this boundary, or None.

    ``None`` is deliberately distinguishable from an empty database: the caller
    reports an unreadable evidence store as a blocker instead of "no evidence".
    """
    if event_api is None:
        return None
    if hasattr(event_api, "table"):
        return event_api
    get_client = getattr(event_api, "get_client", None)
    if not callable(get_client):
        return None
    try:
        return get_client()
    except Exception:  # noqa: BLE001 - a failed client is not a verified client
        return None


def build_evidence_scope(
    result: Mapping[str, Any],
    req: Any,
    *,
    sport: str,
) -> tuple[ModelBundleScope | None, tuple[str, ...]]:
    """Bind the served model identity to one exact evidence scope.

    Only identities the result actually served (or a governed repository
    contract declares) are used. A missing family/version stays unresolved; it is
    never inferred from a sport, a feature name or another model.
    """
    sport_key = _text(sport).upper()
    # Only use repository contracts belonging to this exact sport. Another
    # sport must provide its own served schema, never inherit NFL metadata.
    fallback = _mlb_identity() if sport_key == "MLB" else _nfl_identity() if sport_key == "NFL" else {}
    model_family = _text(result.get("model_family")) or fallback.get("model_family", "")
    artifact_version = (
        _text(result.get("model_artifact_version"))
        or _text(result.get("model_version"))
        or fallback.get("model_artifact_version", "")
    )
    feature_schema = (
        _text(result.get("feature_schema_version"))
        or fallback.get("feature_schema_version", "")
    )
    official_event_id = (
        _text(result.get("official_event_id"))
        or _text(result.get("canonical_event_id"))
        or _text(getattr(req, "official_event_id", ""))
    )
    event_start = _text(getattr(req, "event_start_time_utc", ""))
    prediction_at = (
        _text(result.get("immutable_model_timestamp"))
        or _text(result.get("model_timestamp"))
    )
    update_at = (
        _text(result.get("latest_material_update_timestamp"))
        or _text(getattr(req, "latest_material_update_timestamp", ""))
    )

    missing = tuple(
        name
        for name, value in (
            ("model_family", model_family),
            ("model_artifact_version", artifact_version),
            ("feature_schema_version", feature_schema),
            ("official_event_id", official_event_id),
            ("event_start_time_utc", event_start),
            ("prediction_timestamp", prediction_at),
        )
        if not value
    )
    if missing:
        return None, missing

    return (
        ModelBundleScope(
            model_kind="TEAM_EVENT",
            sport=_text(sport).upper(),
            model_family=model_family,
            model_artifact_version=artifact_version,
            feature_schema_version=feature_schema,
            market_family=None,
            official_event_id=official_event_id,
            event_start_time=event_start,
            prediction_timestamp=prediction_at,
            latest_material_update_timestamp=update_at or None,
        ),
        (),
    )


def _report(
    status: str,
    *,
    blockers: tuple[str, ...],
    scope: ModelBundleScope | None,
    assessment: Mapping[str, Any] | None = None,
    model_kind: str = "TEAM_EVENT",
) -> dict[str, Any]:
    assessment = assessment or {}
    complete = status == COMPLETE and bool(assessment.get("bundle_complete"))
    # The kind must be the one actually assessed: the official-publication guard
    # accepts a sealed report only for TEAM_EVENT, so a prop or weather report
    # relabelled here could otherwise authorize a team-event publication.
    kind = (_text(scope.model_kind) if scope else "") or model_kind
    return {
        "status": status if not complete else COMPLETE,
        "bundle_complete": complete,
        "blockers": list(dict.fromkeys(blockers)),
        "component_sources": dict(assessment.get("component_sources") or {}),
        "missing_component_fields": dict(assessment.get("missing_component_fields") or {}),
        "model_kind": kind,
        "sport": scope.sport if scope else None,
        "official_event_id": scope.official_event_id if scope else None,
        "event_start_time": scope.event_start_time if scope else None,
        "prediction_timestamp": scope.prediction_timestamp if scope else None,
        "model_family": scope.model_family if scope else None,
        "model_artifact_version": scope.model_artifact_version if scope else None,
        "feature_schema_version": scope.feature_schema_version if scope else None,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


def evaluate_served_model_evidence(
    result: Mapping[str, Any],
    req: Any,
    *,
    sport: str,
    db: Any,
    assessor: AssessmentCallable | None = None,
) -> dict[str, Any]:
    """Read the triad for the model version this result actually served."""
    scope, missing = build_evidence_scope(result, req, sport=sport)
    if scope is None:
        return _report(
            IDENTITY_UNRESOLVED,
            blockers=(TRIAD_IDENTITY_UNRESOLVED, *[f"{TRIAD_IDENTITY_UNRESOLVED}:{name}" for name in missing]),
            scope=None,
        )
    return evaluate_scope_triad(scope, db=db, assessor=assessor)


def evaluate_scope_triad(
    scope: ModelBundleScope,
    *,
    db: Any,
    assessor: AssessmentCallable | None = None,
) -> dict[str, Any]:
    """Assess one exact scope and normalize it into a boundary report."""
    chosen = assessor or EVIDENCE_ASSESSOR
    if chosen is None:
        if db is None:
            return _report(
                EVIDENCE_UNAVAILABLE,
                blockers=(TRIAD_READER_UNAVAILABLE, "MODEL_BUNDLE_READER_CLIENT_UNAVAILABLE"),
                scope=scope,
            )
        chosen = assess_registry_model_bundle
    assessment = chosen(db, scope)
    blockers = tuple(str(code) for code in (assessment.get("blockers") or ()))
    if assessment.get("bundle_complete") is True and not blockers:
        return _report(COMPLETE, blockers=(), scope=scope, assessment=assessment)
    return _report(
        INCOMPLETE,
        blockers=(TRIAD_INCOMPLETE, *blockers),
        scope=scope,
        assessment=assessment,
    )


def evaluate_market_evidence_triad(
    *,
    model_kind: str,
    sport: str,
    db: Any,
    market_family: str | None = None,
    official_event_id: Any = None,
    event_start_time: Any = None,
    prediction_timestamp: Any = None,
    model_family: Any = None,
    model_artifact_version: Any = None,
    feature_schema_version: Any = None,
    assessor: AssessmentCallable | None = None,
    player_id: Any = None, stat_type: Any = None, period: Any = None,
    line: Any = None, direction: Any = None,
    station_id: Any = None, contract_id: Any = None,
) -> dict[str, Any]:
    """Read the triad for a prop/weather serving path.

    A lane with no declared artifact, calibrator and pregame reader cannot be
    verified at all, so it is reported as unconfigured rather than guessed from a
    similarly named table or from a research candidate registry.
    """
    if not lane_configured(model_kind, sport):
        return _report(
            INCOMPLETE,
            blockers=(
                TRIAD_LANE_NOT_CONFIGURED,
                "MODEL_BUNDLE_ARTIFACT_SOURCE_NOT_CONFIGURED",
                "MODEL_BUNDLE_CALIBRATOR_SOURCE_NOT_CONFIGURED",
                "MODEL_BUNDLE_PREGAME_CORPUS_SOURCE_NOT_CONFIGURED",
            ),
            scope=None,
            model_kind=model_kind,
        )
    scope = ModelBundleScope(
        model_kind=_text(model_kind),
        sport=_text(sport).upper(),
        model_family=_text(model_family),
        model_artifact_version=_text(model_artifact_version),
        feature_schema_version=_text(feature_schema_version),
        market_family=_text(market_family) or None,
        official_event_id=_text(official_event_id),
        event_start_time=_text(event_start_time),
        prediction_timestamp=_text(prediction_timestamp),
        latest_material_update_timestamp=None,
        player_id=player_id, stat_type=stat_type, period=period,
        line=line, direction=direction, station_id=station_id, contract_id=contract_id,
    )
    missing = tuple(
        name
        for name, value in (
            ("model_family", scope.model_family),
            ("model_artifact_version", scope.model_artifact_version),
            ("feature_schema_version", scope.feature_schema_version),
            ("official_event_id", scope.official_event_id),
            ("event_start_time_utc", scope.event_start_time),
            ("prediction_timestamp", scope.prediction_timestamp),
            *((name, getattr(scope, name)) for name in (
                ("player_id", "stat_type", "period", "line", "direction")
                if model_kind == "PLAYER_PROP" else
                ("station_id", "contract_id") if model_kind == "WEATHER_CONTRACT" else ())),
        )
        if not value
    )
    if missing:
        return _report(
            IDENTITY_UNRESOLVED,
            blockers=(
                TRIAD_IDENTITY_UNRESOLVED,
                *[f"{TRIAD_IDENTITY_UNRESOLVED}:{name}" for name in missing],
            ),
            scope=scope,
        )
    return evaluate_scope_triad(scope, db=db, assessor=assessor)


def gate_prop_probability_payload(payload: dict[str, Any], *, db: Any) -> dict[str, Any]:
    """Withhold prop publication unless the served prop triad is verified.

    The calibrated probability, bounds and calibration evidence are untouched;
    only the publication claim is withdrawn, with the typed blockers attached.
    """
    report = evaluate_market_evidence_triad(
        model_kind="PLAYER_PROP",
        sport=_text(payload.get("sport")),
        market_family=_text(payload.get("stat_type")) or None,
        player_id=payload.get("player_id"), stat_type=payload.get("stat_type"),
        period=payload.get("period"), line=payload.get("line"), direction=payload.get("direction"),
        official_event_id=payload.get("event_id"),
        event_start_time=payload.get("event_start_time"),
        prediction_timestamp=payload.get("model_timestamp"),
        model_family=payload.get("model_family"),
        model_artifact_version=payload.get("model_artifact_version"),
        feature_schema_version=payload.get("feature_schema_version"),
        db=db,
    )
    sealed = seal_report(report)
    payload["model_evidence_triad"] = sealed
    payload["evidence_triad_required"] = True
    payload["evidence_triad_status"] = sealed.get("status")
    if sealed.get("bundle_complete") is not True:
        payload["probability_publishable"] = False
        payload["triad_blockers"] = list(sealed.get("blockers") or ())
    return payload


def apply_evidence_triad_gate(
    outcome: Mapping[str, Any],
    report: Mapping[str, Any],
) -> dict[str, Any]:
    """Attach the triad report and withhold publication unless it is complete.

    The scored numbers remain intact for immutable diagnostic receipts; only
    publication, ranking and the terminal ceiling are forced to the held state.
    """
    out = dict(outcome)
    sealed = seal_report(report)
    # The registered scoring boundary creates these fields from its actual
    # request/served-model scope, never from a caller-issued triad flag.
    # Do not overwrite a conflicting scored identity; the official guard will
    # hold it because it cannot match the sealed scope.
    if sealed.get("model_kind") == "TEAM_EVENT":
        for field in ("sport", "official_event_id", "model_artifact_version"):
            if not _text(out.get(field)) and _text(sealed.get(field)):
                out[field] = sealed[field]
    out["model_evidence_triad"] = sealed
    out["evidence_triad_required"] = True
    out["evidence_triad_status"] = sealed.get("status")
    out["can_execute"] = False
    if sealed.get("bundle_complete") is True:
        return out

    blockers = [str(code) for code in (report.get("blockers") or ())]
    out["prepublication_claim"] = out.get("prepublication_claim") or {
        "probability_publishable": out.get("probability_publishable"),
        "rank_eligible": out.get("rank_eligible"),
        "terminal_label": out.get("terminal_label"),
    }
    out["probability_publishable"] = False
    out["rank_eligible"] = False

    existing = out.get("publication_blockers")
    prior = [str(code) for code in existing] if isinstance(existing, list) else []
    merged = list(dict.fromkeys([*prior, *blockers]))
    out["publication_blockers"] = merged

    reducer_input = dict(out.get("terminal_reducer_input") or {})
    # The strictest applicable upstream ceiling wins: an approved label from a
    # scorer or governance bridge cannot survive an incomplete triad, and an
    # already stricter non-approved label is never replaced by the model hold.
    label = _strictest_terminal(out.get("terminal_label"), HOLD)
    ceiling = _strictest_terminal(out.get("terminal_ceiling"), label)
    terminal_output = _strictest_terminal(reducer_input.get("terminal_output"), ceiling)
    status = _text(reducer_input.get("status")).upper()
    reducer_input.update({
        "status": "HOLD" if status in {"", "PASS", "COMPLETE", "OK", "APPROVED"} else status,
        "terminal_output": terminal_output,
        "global_terminal_reducer": reducer_input.get("global_terminal_reducer") or TERMINAL_AUTHORITY,
        "blockers": merged,
        "evidence_triad_status": report.get("status"),
        "evidence_triad_complete": False,
    })
    out["terminal_reducer_input"] = reducer_input
    out["terminal_label"] = label
    out["terminal_ceiling"] = ceiling
    return out


def _strictest_terminal(existing: Any, proposed: str) -> str:
    """Never upgrade a terminal: only replace an approval-asserting label."""
    label = _text(existing).upper()
    if not label or label in _APPROVED_TERMINALS:
        return proposed
    return label


__all__ = [
    "CAN_EXECUTE",
    "COMPLETE",
    "EVIDENCE_ASSESSOR",
    "EVIDENCE_UNAVAILABLE",
    "HOLD",
    "IDENTITY_UNRESOLVED",
    "INCOMPLETE",
    "STATUSES",
    "TERMINAL_AUTHORITY",
    "TRIAD_IDENTITY_UNRESOLVED",
    "TRIAD_INCOMPLETE",
    "TRIAD_LANE_NOT_CONFIGURED",
    "TRIAD_READER_UNAVAILABLE",
    "apply_evidence_triad_gate",
    "build_evidence_scope",
    "evaluate_market_evidence_triad",
    "evaluate_scope_triad",
    "evaluate_served_model_evidence",
    "evidence_db_client",
    "gate_prop_probability_payload",
    "seal_report",
    "triad_required",
    "verify_sealed_report",
]
