"""Additive V17 NCAAF fitted-probability boundary.

This bridge owns only NCAAF team/event requests.  It may expose a completed
sporting probability only after a certified fitted artifact, canonical pregame
feature snapshot, and active static calibrator all resolve.  The current NCAAF
trust stack does not yet contain a fitted final dynamic-calibration bound, so
ranking/publication remain fail-closed until that capability is certified.

No market probability or generic reasoning may substitute for any missing model
component.  can_execute is always false.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from fastapi import HTTPException

# Import registers the reviewed NCAAF_LOGISTIC_V1 adapter with the fitted provider.
import ncaaf_logistic_adapter  # noqa: F401
from ncaaf_fitted_provider import (
    NCAAFFittedProviderUnavailable,
    NCAAFInferenceRequest,
    infer_raw_probability,
    resolve_certified_artifact,
)
from ncaaf_static_calibrator import (
    NCAAFStaticCalibrationUnavailable,
    calibrate_from_registry,
)

NCAAF_ALIASES = frozenset({"NCAAF", "CFB", "COLLEGE_FOOTBALL", "NCAA_FOOTBALL"})
FEATURE_SCHEMA_VERSION = "NCAAF_FEATURES_V1"
CONTROLLING_SPECIALIST = "WOW_NCAAF_FITTED_MODEL_V1"
FINAL_BOUND_BLOCKER = "NCAAF_DYNAMIC_CALIBRATION_BOUND_NOT_CERTIFIED"
_PATCH_MARKER = "_v17_ncaaf_team_event_publication_installed"


def _ncaaf_sport(req: Any) -> bool:
    sport = str(getattr(req, "sport", "") or "").strip().upper().replace(" ", "_")
    league = str(getattr(req, "league", "") or "").strip().upper().replace(" ", "_")
    return sport in NCAAF_ALIASES and league in {"NCAAF", "CFB", "NCAA_FOOTBALL"}


def _aware(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _normalize_provider_error(exc: Exception) -> tuple[str, int]:
    raw = str(getattr(exc, "code", "") or "")
    if raw in {
        "NCAAF_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND",
        "NCAAF_MODEL_FAMILY_ADAPTER_UNAVAILABLE",
        "NCAAF_CERTIFIED_CALIBRATOR_NOT_FOUND",
    }:
        return "MODEL_UNAVAILABLE", 409
    if raw in {
        "NCAAF_EVENT_FEATURE_SNAPSHOT_NOT_FOUND",
        "NCAAF_EVENT_IDENTITY_MISMATCH",
        "NCAAF_TEAM_IDENTITY_MISMATCH",
        "NCAAF_FEATURE_SCHEMA_MISMATCH",
        "NCAAF_FEATURE_TIMESTAMP_MISMATCH",
        "NCAAF_PREGAME_EVIDENCE_INCOMPLETE",
    }:
        return "MODEL_INPUTS_INSUFFICIENT", 422
    if "INVALID" in raw or "NOT_NORMALIZED" in raw or "MISMATCH" in raw:
        return "MODEL_OUTPUT_INVALID", 500
    return "MODEL_SCORER_FAILED", 503


def _typed_failure(base: Any, req: Any, exc: Exception) -> HTTPException:
    code, status = _normalize_provider_error(exc)
    blocker = str(getattr(exc, "code", "") or type(exc).__name__)
    return HTTPException(
        status_code=status,
        detail=base._augment_detail(
            {
                "code": code,
                "blocker_code": blocker,
                "sport": "NCAAF",
                "league": "NCAAF",
                "failed_contract_scope": ["CONTROLLING_SPECIALIST"],
                "model_invoked": code != "MODEL_UNAVAILABLE",
                "market_probability_substitution_allowed": False,
                "generic_reasoning_substitution_allowed": False,
                "probability_publishable": False,
                "rank_eligible": False,
                "can_execute": False,
            },
            req,
        ),
    )


def _feature_row_from_rpc(db: Any, *, event_id: str, home: str, away: str) -> Mapping[str, Any] | None:
    try:
        result = db.rpc(
            "wow_ncaaf_latest_event_features",
            {
                "p_official_event_id": event_id,
                "p_feature_schema_version": FEATURE_SCHEMA_VERSION,
                "p_home_team": home,
                "p_away_team": away,
            },
        ).execute()
    except Exception as exc:
        raise NCAAFFittedProviderUnavailable(
            "NCAAF_EVENT_FEATURE_REGISTRY_UNAVAILABLE",
            "Could not read canonical NCAAF event features.",
        ) from exc
    payload = getattr(result, "data", None)
    if not isinstance(payload, Mapping):
        raise NCAAFFittedProviderUnavailable(
            "NCAAF_EVENT_FEATURE_REGISTRY_INVALID_RESPONSE",
            "Canonical NCAAF event-feature registry returned an invalid response.",
        )
    return payload if payload.get("ok") is True else None


def _candidate_feature_rows(db: Any, req: Any) -> list[Mapping[str, Any]]:
    try:
        result = (
            db.table("wow_ncaaf_event_feature_snapshots")
            .select("*")
            .eq("home_team", req.home_team)
            .eq("away_team", req.away_team)
            .eq("feature_schema_version", FEATURE_SCHEMA_VERSION)
            .order("feature_as_of", desc=True)
            .limit(20)
            .execute()
        )
    except Exception as exc:
        raise NCAAFFittedProviderUnavailable(
            "NCAAF_EVENT_FEATURE_REGISTRY_UNAVAILABLE",
            "Could not search canonical NCAAF event features.",
        ) from exc
    rows = getattr(result, "data", None)
    return [row for row in (rows or []) if isinstance(row, Mapping)]


def _resolve_features(req: Any, *, db: Any) -> tuple[str, Mapping[str, Any], str]:
    # Exact canonical IDs always win.  The caller-supplied ID may be a sportsbook
    # provider hash, so exact failure is followed by a strict team/kickoff match.
    exact = _feature_row_from_rpc(
        db,
        event_id=str(req.official_event_id),
        home=str(req.home_team),
        away=str(req.away_team),
    )
    if exact is not None:
        return str(exact["official_event_id"]), exact, "EXACT_CANONICAL_EVENT_ID"

    requested_start = _aware(req.event_start_time_utc)
    if requested_start is None:
        raise NCAAFFittedProviderUnavailable(
            "NCAAF_EVENT_IDENTITY_MISMATCH",
            "NCAAF kickoff timestamp is invalid.",
        )
    matches: list[Mapping[str, Any]] = []
    for row in _candidate_feature_rows(db, req):
        row_start = _aware(row.get("event_start_time"))
        if row_start is None:
            continue
        # Provider timestamps may differ by representation, but a canonical
        # game identity may not drift materially.  Ten minutes is an identity
        # tolerance only; it never changes any model feature.
        if abs((row_start - requested_start).total_seconds()) <= 600:
            matches.append(row)
    ids = {str(row.get("official_event_id") or "") for row in matches if row.get("official_event_id")}
    if len(ids) != 1:
        raise NCAAFFittedProviderUnavailable(
            "NCAAF_EVENT_IDENTITY_MISMATCH",
            "Provider event could not be resolved to exactly one canonical NCAAF event.",
        )
    canonical_id = next(iter(ids))
    canonical = _feature_row_from_rpc(
        db,
        event_id=canonical_id,
        home=str(req.home_team),
        away=str(req.away_team),
    )
    if canonical is None:
        raise NCAAFFittedProviderUnavailable(
            "NCAAF_EVENT_FEATURE_SNAPSHOT_NOT_FOUND",
            "Resolved NCAAF event has no current canonical pregame feature snapshot.",
        )
    return canonical_id, canonical, "TEAM_KICKOFF_CANONICAL_RESOLUTION"


def _score_static_probability(req: Any, *, db: Any) -> dict[str, Any]:
    # Resolve model capability before event inputs so a genuinely absent fitted
    # artifact remains MODEL_UNAVAILABLE rather than being mislabeled as an
    # acquisition problem.
    artifact = resolve_certified_artifact(db, feature_schema_version=FEATURE_SCHEMA_VERSION)
    if artifact is None:
        raise NCAAFFittedProviderUnavailable(
            "NCAAF_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND",
            "No certified NCAAF fitted-model artifact is active.",
        )

    canonical_id, features, identity_resolution = _resolve_features(req, db=db)
    feature_as_of = str(features.get("feature_as_of") or "")
    if not feature_as_of:
        raise NCAAFFittedProviderUnavailable(
            "NCAAF_FEATURE_TIMESTAMP_MISMATCH",
            "Canonical NCAAF feature snapshot has no feature_as_of timestamp.",
        )
    inference_request = NCAAFInferenceRequest(
        official_event_id=canonical_id,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        feature_as_of=feature_as_of,
        home_team=str(req.home_team),
        away_team=str(req.away_team),
    )
    raw = infer_raw_probability(db, request=inference_request, features=features)
    static = calibrate_from_registry(
        db,
        model_artifact_version=raw.model_artifact_version,
        raw_probability=raw.home_probability,
    )

    home_point = float(static.calibrated_probability)
    home_diag_lower = float(static.calibration_lower_diagnostic)
    home_diag_upper = float(static.calibration_upper_diagnostic)
    away_point = 1.0 - home_point
    away_diag_lower = 1.0 - home_diag_upper
    away_diag_upper = 1.0 - home_diag_lower
    home_selected = home_point >= away_point
    selected = str(req.home_team if home_selected else req.away_team)
    opponent = str(req.away_team if home_selected else req.home_team)
    selected_raw = float(raw.home_probability if home_selected else raw.away_probability)
    selected_point = home_point if home_selected else away_point

    return {
        "sport": "NCAAF",
        "league": "NCAAF",
        "official_event_id": canonical_id,
        "provider_event_id": str(req.official_event_id),
        "identity_resolution": identity_resolution,
        "event_start_time_utc": str(features.get("event_start_time") or req.event_start_time_utc),
        "home_team": str(req.home_team),
        "away_team": str(req.away_team),
        "selected_participant": selected,
        "opponent": opponent,
        "model_probability": selected_raw,
        "raw_home_probability": float(raw.home_probability),
        "raw_away_probability": float(raw.away_probability),
        "calibrated_probability": selected_point,
        "calibrated_selection_probability": selected_point,
        "calibrated_home_probability": home_point,
        "calibrated_away_probability": away_point,
        "static_calibration_home_lower_diagnostic": home_diag_lower,
        "static_calibration_home_upper_diagnostic": home_diag_upper,
        "static_calibration_away_lower_diagnostic": away_diag_lower,
        "static_calibration_away_upper_diagnostic": away_diag_upper,
        "calibrated_lower_bound": None,
        "calibrated_upper_bound": None,
        "model_artifact_id": raw.artifact_id,
        "model_artifact_version": raw.model_artifact_version,
        "model_family": artifact.model_family,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_as_of": feature_as_of,
        "calibration_method": static.calibration_method,
        "calibration_version": static.calibrator_version,
        "calibration_training_n": static.calibration_training_n,
        "calibration_health_status": static.calibration_health_status,
        "controlling_specialist": CONTROLLING_SPECIALIST,
        "sporting_probability_completed": True,
        "sporting_probability_status": "COMPLETED",
        "code": FINAL_BOUND_BLOCKER,
        "terminal_label": "MODEL_QUALIFIED_HOLD",
        "terminal_ceiling": "MODEL_QUALIFIED_HOLD",
        "blockers": [FINAL_BOUND_BLOCKER],
        "rank_eligible": False,
        "probability_publishable": False,
        "blend_publishable": False,
        "market_probability_substitution_allowed": False,
        "generic_reasoning_substitution_allowed": False,
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": False,
    }


def install_ncaaf_team_event_publication(team_event_module: Any) -> bool:
    """Wrap the active V17 scorer with the exact NCAAF fitted-model boundary."""
    if getattr(team_event_module, _PATCH_MARKER, False):
        return True
    original: Callable[..., dict[str, Any]] = team_event_module.score_team_event_request

    def score_team_event_request(req: Any, *, event_api: Any, canonical_hydration_required: bool = False) -> dict[str, Any]:
        if not _ncaaf_sport(req):
            return original(
                req,
                event_api=event_api,
                canonical_hydration_required=canonical_hydration_required,
            )
        try:
            route = team_event_module.resolve_host_route(req.requester_host_identity, req.candidate_family)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": str(exc), "probability_publishable": False, "can_execute": False},
            ) from exc
        if route.controlling_engine_identity != team_event_module.LLP_TEAM_BETTING_ENGINE:
            raise HTTPException(
                status_code=422,
                detail=team_event_module._augment_detail(
                    {
                        "code": "TEAM_EVENT_CONTROLLING_ENGINE_MISMATCH",
                        "probability_publishable": False,
                    },
                    req,
                ),
            )
        errors = team_event_module._base_errors(req)
        if errors:
            raise HTTPException(
                status_code=422,
                detail=team_event_module._augment_detail(
                    {"code": "TEAM_EVENT_CONTRACT_INVALID", "errors": errors, "probability_publishable": False},
                    req,
                ),
            )
        get_client = getattr(event_api, "get_client", None)
        if not callable(get_client):
            raise HTTPException(
                status_code=503,
                detail=team_event_module._augment_detail(
                    {"code": "NCAAF_EVENT_LEDGER_CLIENT_UNAVAILABLE", "probability_publishable": False},
                    req,
                ),
            )
        db = get_client()
        scout_research_barrier = team_event_module._run_mandatory_scout_research(req)
        try:
            scored = _score_static_probability(req, db=db)
        except (NCAAFFittedProviderUnavailable, NCAAFStaticCalibrationUnavailable) as exc:
            raise _typed_failure(team_event_module, req, exc) from exc
        scored["requester_host_identity"] = route.requester_host_identity
        scored["controlling_engine_identity"] = team_event_module.LLP_TEAM_BETTING_ENGINE
        scored["candidate_family"] = route.candidate_family
        scored["scout_research_barrier"] = scout_research_barrier
        return scored

    team_event_module.score_team_event_request = score_team_event_request
    setattr(team_event_module, _PATCH_MARKER, True)
    return True


__all__ = [
    "CONTROLLING_SPECIALIST",
    "FEATURE_SCHEMA_VERSION",
    "FINAL_BOUND_BLOCKER",
    "install_ncaaf_team_event_publication",
]
