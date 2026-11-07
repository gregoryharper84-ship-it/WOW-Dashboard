"""NCAAF pregame moneyline restoration handoff.

This module joins the existing official-identity, feature-compiler, and fitted
candidate contracts. It does not promote a specialist, publish a probability,
substitute Elo or market prices, or waive a missing dynamic lower bound.

A current event is scored only after verified pregame inputs and a registered
fitted artifact. Anything short of that returns the native hold.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

from ncaaf_cfbd_hydrator import SourceSnapshot
from ncaaf_event_feature_handoff import (
    NCAAFEventFeatureHandoffUnavailable,
    score_current_event_snapshot,
)
from ncaaf_feature_compiler import (
    FEATURE_SCHEMA_VERSION,
    NCAAFFeatureCompileBlocked,
    compile_official_event_feature_row,
    materialize_complete_training_features,
)
from v17.ncaaf_event_identity import (
    NCAAFEventIdentityError,
    resolve_ncaaf_current_event_identity,
)

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
MINIMUM_COMPLETE_TRAINING_ROWS = 300

_INPUT_CODES = frozenset({
    "NCAAF_EVENT_FEATURE_SNAPSHOT_NOT_FOUND",
    "NCAAF_MODEL_FEATURE_EVIDENCE_INCOMPLETE",
    "NCAAF_MODEL_FEATURE_PAYLOAD_INVALID",
    "NCAAF_FEATURE_IDENTITY_INCOMPLETE",
    "NCAAF_EVENT_START_INVALID",
    "NCAAF_CURRENT_EVENT_CONTEXT_INCOMPLETE",
    "NCAAF_CURRENT_EVENT_CONTEXT_REFRESH_REQUIRED",
    "NCAAF_CURRENT_SOURCE_RECEIPT_INVALID",
    "NCAAF_CURRENT_EVENT_CONTEXT_REFRESH_INVALID",
})
_LEAKAGE_CODES = frozenset({
    "NCAAF_FEATURE_TEMPORAL_LEAKAGE",
    "NCAAF_EVENT_FEATURE_TEMPORAL_LEAKAGE",
})
_IDENTITY_CODES = frozenset({
    "NCAAF_EVENT_IDENTITY_MISMATCH",
    "NCAAF_EVENT_FEATURE_COHORT_MISMATCH",
    "NCAAF_CANONICAL_EVENT_NOT_FOUND",
    "NCAAF_CANONICAL_EVENT_AMBIGUOUS",
    "NCAAF_SEASON_MISMATCH",
    "NCAAF_SEASON_INVALID",
    "NCAAF_EVENT_TIME_INVALID",
})
_POSTGAME_ENDPOINTS = frozenset({"/games/players"})
_RATING_ENDPOINTS = frozenset({
    "/ratings/core",
    "/ratings/sp",
    "/ratings/srs",
    "/ratings/elo",
    "/ratings/fpi",
})


def _aware(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(
        str(value or "").replace("Z", "+00:00")
    )
    if parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _receipt(
    *,
    status: str,
    code: str,
    native_code: str,
    model_invoked: bool,
    blockers: Iterable[str] = (),
    detail: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "code": code,
        "native_code": native_code,
        "model_invoked": model_invoked,
        "blockers": list(dict.fromkeys(str(item) for item in blockers if str(item))),
        "detail": dict(detail or {}),
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "calibrated_lower_bound": None,
        "rank_eligible": False,
        "probability_publishable": False,
        "promoted": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "market_features_used": False,
        "can_execute": False,
    }


def _hold_from_code(code: str, *, detail: str = "", model_invoked: bool = False, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    native = code
    if code in _INPUT_CODES:
        failure = "MODEL_INPUTS_INSUFFICIENT"
    elif code in _LEAKAGE_CODES:
        failure = code
    elif code in _IDENTITY_CODES or code in {"NCAAF_EVENT_NOT_PREGAME"}:
        failure = code
    elif code in {"MODEL_UNAVAILABLE", "MODEL_SCORER_FAILED", "MODEL_OUTPUT_INVALID", "MODEL_INPUTS_INSUFFICIENT"}:
        failure = code
        native = detail or code
    else:
        failure = code
    blockers = [failure]
    if native and native != failure:
        blockers.append(native)
    return _receipt(
        status="HELD",
        code=failure,
        native_code=native,
        model_invoked=model_invoked,
        blockers=blockers,
        detail={"detail": detail, **dict(extra or {})},
    )


def classify_source_snapshot_for_pregame(
    snapshot: SourceSnapshot,
    *,
    event_start_time: str,
) -> dict[str, Any]:
    """Refuse to treat raw staging as a fitted pregame feature.

    Box scores, retrospective ratings, and any snapshot retrieved at or after
    kickoff stay source evidence only. This does not invent a replacement value.
    """
    kickoff = _aware(event_start_time)
    retrieved_at = _aware(snapshot.retrieved_at)
    blockers: list[str] = []
    if snapshot.endpoint in _POSTGAME_ENDPOINTS:
        blockers.append("NCAAF_POSTGAME_SOURCE_NOT_PREGAME_FEATURE")
    if snapshot.endpoint in _RATING_ENDPOINTS or "RETROSPECTIVE_RATING_NOT_PREGAME_FEATURE" in snapshot.blocker_codes:
        blockers.append("NCAAF_RATING_SNAPSHOT_NOT_FITTED_FEATURE")
    if retrieved_at >= kickoff:
        blockers.append("NCAAF_SOURCE_RETRIEVED_AFTER_KICKOFF")
    if snapshot.can_execute is not False:
        blockers.append("NCAAF_SOURCE_SNAPSHOT_EXECUTION_FLAG_INVALID")
    return {
        "admitted_as_pregame_feature": False,
        "source_receipt_only": True,
        "endpoint": snapshot.endpoint,
        "payload_sha256": snapshot.payload_sha256,
        "retrieved_at": retrieved_at.isoformat(),
        "event_start_time": kickoff.isoformat(),
        "blocker_codes": blockers or ["NCAAF_RAW_SNAPSHOT_NOT_A_FEATURE_ROW"],
        "probability_publishable": False,
        "can_execute": False,
    }


def _reject_contradictory_evidence(
    event: Mapping[str, Any],
    evidence_rows: Iterable[Mapping[str, Any]],
    *,
    now: Any | None = None,
) -> str | None:
    event_id = str(event.get("official_event_id") or "").strip()
    home = str(event.get("home_team") or "").strip().casefold()
    away = str(event.get("away_team") or "").strip().casefold()
    try:
        kickoff = _aware(event.get("event_start_time"))
        current_time = _aware(now) if now is not None else None
    except Exception:
        return "NCAAF_EVENT_START_INVALID"
    for row in evidence_rows:
        if str(row.get("official_event_id") or "") != event_id:
            continue
        try:
            evidence_at = _aware(row.get("evidence_timestamp"))
        except Exception:
            return "NCAAF_EVENT_START_INVALID"
        if evidence_at >= kickoff or (current_time is not None and evidence_at > current_time):
            return "NCAAF_FEATURE_TEMPORAL_LEAKAGE"
        try:
            if _aware(row.get("event_start_time")) != kickoff:
                return "NCAAF_EVENT_IDENTITY_MISMATCH"
        except Exception:
            return "NCAAF_EVENT_IDENTITY_MISMATCH"
        scope = str(row.get("scope") or "").upper()
        team = str(row.get("team") or "").strip().casefold()
        if scope == "HOME" and team != home:
            return "NCAAF_EVENT_IDENTITY_MISMATCH"
        if scope == "AWAY" and team != away:
            return "NCAAF_EVENT_IDENTITY_MISMATCH"
    return None


def establish_official_event_identity(
    *,
    event_start_time: str,
    home_team: str,
    away_team: str,
    season: int,
    now: Any,
    official_event_id: str | None = None,
    client: Any = None,
) -> dict[str, Any]:
    """Resolve one CFBD event and reject post-start or mismatched identity."""
    try:
        kickoff = _aware(event_start_time)
        current_time = _aware(now)
    except Exception:
        return _hold_from_code("NCAAF_EVENT_TIME_INVALID")
    if kickoff <= current_time:
        return _hold_from_code("NCAAF_EVENT_NOT_PREGAME")
    try:
        resolved = resolve_ncaaf_current_event_identity(
            event_start_time=kickoff.isoformat(),
            home_team=home_team,
            away_team=away_team,
            season=int(season),
            client=client,
        )
    except NCAAFEventIdentityError as exc:
        return _hold_from_code(exc.code, detail=exc.detail)
    resolved_kickoff = _aware(resolved["event_start_time"])
    if resolved_kickoff <= current_time:
        return _hold_from_code("NCAAF_EVENT_NOT_PREGAME", detail="RESOLVED_START_NOT_PREGAME")
    if int(resolved["season"]) != int(season):
        return _hold_from_code("NCAAF_SEASON_MISMATCH", detail=str(resolved["season"]))
    requested_id = str(official_event_id or "").strip()
    if requested_id and requested_id != str(resolved["event_id"]):
        return _hold_from_code("NCAAF_EVENT_IDENTITY_MISMATCH", detail="OFFICIAL_EVENT_ID")
    return {
        "status": "OFFICIAL_IDENTITY_RESOLVED",
        "code": "NCAAF_OFFICIAL_EVENT_IDENTITY_RESOLVED",
        "native_code": "CFBD_EXACT_PARTICIPANTS_START_MATCH",
        "official_event_id": str(resolved["event_id"]),
        "season": int(resolved["season"]),
        "event_start_time": resolved_kickoff.isoformat(),
        "home_team": str(resolved["home_team"]),
        "away_team": str(resolved["away_team"]),
        "neutral_site": resolved.get("neutral_site"),
        "model_invoked": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "promoted": False,
        "can_execute": False,
    }


def verify_pregame_inputs(
    event: Mapping[str, Any],
    evidence_rows: Iterable[Mapping[str, Any]],
    *,
    now: Any | None = None,
) -> dict[str, Any]:
    """Compile the existing feature schema or return the native input hold.

    Raw source snapshots are not accepted here. Only already normalized
    pregame evidence rows can become model inputs, and a post-start or
    mismatched row fails the event instead of being dropped.
    """
    rows = list(evidence_rows)
    contradiction = _reject_contradictory_evidence(event, rows, now=now)
    if contradiction:
        return _hold_from_code(contradiction)
    try:
        compiled = compile_official_event_feature_row(event, rows)
    except NCAAFFeatureCompileBlocked as exc:
        return _hold_from_code(exc.code, detail=",".join(exc.blockers))
    if compiled.get("market_home_no_vig") is not None or compiled.get("market_away_no_vig") is not None:
        return _hold_from_code("NCAAF_MARKET_FEATURES_PROHIBITED")
    try:
        if _aware(compiled["feature_as_of"]) >= _aware(event.get("event_start_time")):
            return _hold_from_code("NCAAF_FEATURE_TEMPORAL_LEAKAGE", detail="FEATURE_AS_OF_NOT_PREGAME")
    except Exception:
        return _hold_from_code("NCAAF_EVENT_START_INVALID")
    return {
        "status": "PREGAME_INPUTS_VERIFIED",
        "code": "NCAAF_PREGAME_INPUTS_VERIFIED",
        "native_code": "NCAAF_FEATURES_V1",
        "model_invoked": False,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_as_of": compiled["feature_as_of"],
        "feature_row": compiled,
        "market_features_used": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "promoted": False,
        "can_execute": False,
    }


def evaluate_fitted_challenger(
    db: Any,
    *,
    training_code_sha: str,
    materialize: Callable[[Any], Mapping[str, Any]] = materialize_complete_training_features,
    train: Callable[..., Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Fit the existing chronological challenger only when complete rows exist.

    Fewer than the runner's minimum complete pregame rows is
    ``MODEL_INPUTS_INSUFFICIENT``. Result-form, Elo, and market priors are not
    called. A successful fit remains an unpromoted candidate.
    """
    try:
        report = dict(materialize(db))
    except NCAAFFeatureCompileBlocked as exc:
        return _hold_from_code(exc.code, detail=",".join(exc.blockers), extra={"stage": "FEATURE_COMPILATION"})
    except Exception as exc:
        return _hold_from_code(
            "NCAAF_FEATURE_COMPILATION_FAILED",
            detail=type(exc).__name__,
            extra={"stage": "FEATURE_COMPILATION"},
        )
    complete = int(report.get("complete_feature_rows") or 0)
    games_seen = int(report.get("games_seen") or 0)
    evidence_seen = int(report.get("evidence_rows_seen") or 0)
    if complete < MINIMUM_COMPLETE_TRAINING_ROWS:
        # Match the candidate runner: no games or no evidence rows is an empty
        # corpus. A non-empty corpus below the minimum stays insufficient.
        native = (
            "NCAAF_TRAINING_DATA_EMPTY"
            if games_seen <= 0 or evidence_seen <= 0
            else "NCAAF_COMPLETE_TRAINING_ROWS_INSUFFICIENT"
        )
        return _hold_from_code(
            "MODEL_INPUTS_INSUFFICIENT",
            detail=native,
            extra={
                "complete_feature_rows": complete,
                "games_seen": games_seen,
                "evidence_rows_seen": evidence_seen,
                "minimum_complete_rows": MINIMUM_COMPLETE_TRAINING_ROWS,
                "feature_compilation": report,
                "challenger_fit": False,
            },
        )
    if not isinstance(training_code_sha, str) or len(training_code_sha.strip()) < 7:
        return _hold_from_code(
            "NCAAF_TRAINING_CODE_SHA_REQUIRED",
            model_invoked=False,
            extra={"feature_compilation": report, "challenger_fit": False},
        )
    if train is None:
        from ncaaf_candidate_training_runner import train_and_persist_candidate

        train = train_and_persist_candidate
    try:
        trained = dict(train(db, training_code_sha=training_code_sha))
    except Exception as exc:
        native = str(getattr(exc, "code", None) or type(exc).__name__)
        if native in {"NCAAF_TRAINING_DATA_EMPTY", "NCAAF_COMPLETE_TRAINING_ROWS_INSUFFICIENT"}:
            failure = "MODEL_INPUTS_INSUFFICIENT"
        elif native in {
            "NCAAF_TRAINING_CODE_SHA_REQUIRED",
            "NCAAF_TRAINING_DATA_READ_FAILED",
            "NCAAF_TRAINING_DATA_INVALID",
            "NCAAF_CANDIDATE_ARTIFACT_PERSIST_FAILED",
        }:
            failure = native
        else:
            failure = "NCAAF_CANDIDATE_TRAINING_FAILED"
        return _hold_from_code(
            failure,
            detail=native,
            model_invoked=False,
            extra={"feature_compilation": report, "challenger_fit": False},
        )
    if trained.get("probability_publishable") is not False or trained.get("can_execute") is not False:
        return _hold_from_code("NCAAF_CANDIDATE_AUTHORITY_INVALID", detail="publish_or_execute")
    if trained.get("promoted") is True or str(trained.get("lifecycle_state") or "") != "CANDIDATE":
        return _hold_from_code("NCAAF_CANDIDATE_AUTHORITY_INVALID", detail="promotion")
    return {
        "status": "CHALLENGER_EVALUATED_HELD",
        "code": "NCAAF_CANDIDATE_ARTIFACTS_PERSISTED",
        "native_code": "NCAAF_LOGISTIC_V1",
        "model_invoked": True,
        "model_artifact_version": trained.get("model_artifact_version"),
        "calibrator_version": trained.get("calibrator_version"),
        "training_rows": trained.get("training_rows"),
        "lifecycle_state": "CANDIDATE",
        "calibration_health_status": trained.get("calibration_health_status") or "BLOCKED",
        "calibrated_lower_bound": None,
        "feature_compilation": report,
        "probability_publishable": False,
        "rank_eligible": False,
        "promoted": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "can_execute": False,
    }


def score_pregame_moneyline(
    db: Any,
    *,
    official_event_id: str,
    event_start_time: str,
    home_team: str,
    away_team: str,
    expected_source_manifest_sha256: str,
    current_context: Mapping[str, Any] | None = None,
    now: Any | None = None,
    clock: Callable[[], Any] | None = None,
    scorer: Callable[..., Any] | None = None,
    calibrator: Callable[..., Any] | None = None,
    score_fn: Callable[..., Mapping[str, Any]] = score_current_event_snapshot,
) -> dict[str, Any]:
    """Return one scoring receipt. Publication stays held."""
    try:
        scored = dict(score_fn(
            db,
            official_event_id=official_event_id,
            event_start_time=event_start_time,
            home_team=home_team,
            away_team=away_team,
            expected_source_manifest_sha256=expected_source_manifest_sha256,
            current_context=current_context,
            now=now,
            clock=clock,
            scorer=scorer,
            calibrator=calibrator,
        ))
    except NCAAFEventFeatureHandoffUnavailable as exc:
        invoked = exc.code in {"MODEL_SCORER_FAILED", "MODEL_OUTPUT_INVALID", "MODEL_UNAVAILABLE"}
        if exc.code == "MODEL_UNAVAILABLE":
            invoked = False
        return _hold_from_code(exc.code, detail=str(exc), model_invoked=invoked)
    except Exception as exc:
        return _hold_from_code("MODEL_SCORER_FAILED", detail=type(exc).__name__, model_invoked=True)
    supplied_bound = scored.get("calibrated_lower_bound")
    scored["probability_publishable"] = False
    scored["rank_eligible"] = False
    scored["can_execute"] = False
    scored["promoted"] = False
    scored["automatic_certification"] = False
    scored["automatic_promotion"] = False
    # A callback lower bound is not a certified dynamic bound. Drop the value.
    scored["calibrated_lower_bound"] = None
    scored["uncertified_lower_bound_contained"] = supplied_bound is not None
    if supplied_bound is not None:
        blockers = list(scored.get("blockers") or [])
        blockers.append("NCAAF_UNCERTIFIED_LOWER_BOUND_CONTAINED")
        scored["blockers"] = list(dict.fromkeys(str(item) for item in blockers))
    scored.setdefault("code", "NCAAF_PREGAME_MONEYLINE_SCORED_HELD")
    scored.setdefault("native_code", "NCAAF_CERTIFIED_DYNAMIC_BOUND_NOT_AVAILABLE")
    return scored


__all__ = [
    "CAN_EXECUTE",
    "MINIMUM_COMPLETE_TRAINING_ROWS",
    "PROBABILITY_PUBLISHABLE",
    "classify_source_snapshot_for_pregame",
    "establish_official_event_identity",
    "evaluate_fitted_challenger",
    "score_pregame_moneyline",
    "verify_pregame_inputs",
]
