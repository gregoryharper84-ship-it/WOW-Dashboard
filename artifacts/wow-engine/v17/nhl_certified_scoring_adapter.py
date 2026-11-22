"""Registry-backed NHL certified scoring adapter (Fix D).

Resolves the immutable team-event certification receipt and emits the V17
certified probability envelope. Candidate research rows stay immutable; request
or catalog self-assertion cannot mint certification. can_execute stays false.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Any, Mapping

from v17.fitted_team_event_certification import (
    FittedTeamEventCertification,
    TeamEventCertificationError,
    resolve_certification,
    validate_prediction_package,
)

CAN_EXECUTE = False
CONTROLLING_SPECIALIST = "NHL_GAME_WIN_PROBABILITY_EXPERT_V1"
IDENTITY_CALIBRATION_METHOD = "IDENTITY_RAW_PROBABILITY_V1"
FULL_GAME_SETTLEMENT = "HOME_AWAY_INCLUDING_OT_SO"
CONFIRMED_GOALIE_STATUSES = frozenset({"CONFIRMED", "CONFIRMED_STARTER", "OFFICIAL_STARTER"})
MAX_EVIDENCE_AGE = timedelta(minutes=60)


class NHLCertifiedScoringError(RuntimeError):
    def __init__(self, code: str, blockers: list[str] | tuple[str, ...] = (), *, detail: Any = None):
        self.code = str(code)
        self.blockers = tuple(dict.fromkeys(str(b) for b in blockers if b))
        self.detail = detail
        self.can_execute = False
        self.probability_publishable = False
        super().__init__(f"{self.code}:{','.join(self.blockers)}")


def _evidence(req: Any) -> dict[str, Any]:
    value = getattr(req, "sport_specific_evidence", None)
    return dict(value) if isinstance(value, Mapping) else {}


def _aware(value: Any) -> datetime | None:
    token = str(value or "").strip()
    if not token:
        return None
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _probability(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    if not isfinite(parsed) or not 0.0 < parsed < 1.0:
        return None
    return parsed


def _require_goalie_ready(evidence: Mapping[str, Any], *, now_utc: datetime) -> None:
    blockers: list[str] = []
    status = str(evidence.get("goalie_status") or "").strip().upper()
    if status not in CONFIRMED_GOALIE_STATUSES:
        blockers.append("NHL_STARTING_GOALIE_NOT_CONFIRMED")
    observed_at = _aware(evidence.get("goalie_status_observed_at"))
    if observed_at is None or observed_at > now_utc or (now_utc - observed_at) > MAX_EVIDENCE_AGE:
        blockers.append("NHL_GOALIE_STATUS_STALE")
    if not str(evidence.get("goalie_identity_snapshot_id") or "").strip():
        blockers.append("NHL_GOALIE_IDENTITY_UNVERIFIED")
    if blockers:
        raise NHLCertifiedScoringError("MODEL_INPUTS_INSUFFICIENT", blockers)


def build_nhl_certified_envelope(
    *,
    receipt: FittedTeamEventCertification,
    raw_home: float,
    calibrated_home: float,
    lower: float | None,
    upper: float | None,
    evidence: Mapping[str, Any],
    model_timestamp: str,
    latest_material_update_timestamp: str,
    home_team: str,
    away_team: str,
    official_event_id: str,
    now_utc: datetime,
    model_artifact_checksum: str | None = None,
) -> dict[str, Any]:
    """Build the certified V17 envelope from a resolved receipt and scored sides."""
    if receipt.sport != "NHL":
        raise NHLCertifiedScoringError("TEAM_EVENT_CERTIFICATION_SPORT_MISMATCH", [receipt.sport])
    if receipt.calibration_method == IDENTITY_CALIBRATION_METHOD:
        raise NHLCertifiedScoringError(
            "MODEL_OUTPUT_INVALID", ["NHL_IDENTITY_CALIBRATOR_FORBIDDEN"]
        )
    _require_goalie_ready(evidence, now_utc=now_utc)

    raw = _probability(raw_home)
    calibrated = _probability(calibrated_home)
    low = _probability(lower)
    high = _probability(upper)
    blockers: list[str] = []
    if raw is None:
        blockers.append("RAW_PROBABILITY_INVALID")
    if calibrated is None:
        blockers.append("CALIBRATED_PROBABILITY_INVALID")
    if low is None:
        blockers.append("CALIBRATED_LOWER_BOUND_INVALID")
    if high is None:
        blockers.append("CALIBRATED_UPPER_BOUND_INVALID")
    if None not in (low, calibrated, high) and not (low <= calibrated <= high):
        blockers.append("CALIBRATED_BOUNDS_ORDER_INVALID")
    if blockers:
        raise NHLCertifiedScoringError("MODEL_OUTPUT_INVALID", blockers)

    assert raw is not None and calibrated is not None and low is not None and high is not None
    away = 1.0 - calibrated
    away_lower = min(max(1.0 - high, 1e-6), 1.0 - 1e-6)
    away_upper = min(max(1.0 - low, 1e-6), 1.0 - 1e-6)
    if abs((calibrated + away) - 1.0) > 1e-5:
        raise NHLCertifiedScoringError("MODEL_OUTPUT_INVALID", ["NHL_BINARY_PROBABILITY_SUM_INVALID"])

    package = {
        "sport": "NHL",
        "league_scope": receipt.league_scope,
        "controlling_specialist": receipt.controlling_specialist,
        "model_family": receipt.model_family,
        "model_version": receipt.model_version,
        "artifact_id": receipt.artifact_id,
        "model_artifact_checksum": model_artifact_checksum or receipt.artifact_sha256,
        "feature_schema_version": receipt.feature_schema_version,
        "input_contract_version": receipt.input_contract_version,
        "calibration_method": receipt.calibration_method,
        "calibration_artifact_id": receipt.calibration_artifact_id,
        "calibration_artifact_fingerprint": receipt.calibration_sha256,
        "independent_verification_status": receipt.independent_verification_status,
        "certification_id": receipt.certification_id,
        "certification_status": receipt.certification_status,
        "official_event_id": official_event_id,
        "home_team": home_team,
        "away_team": away_team,
        "raw_probability": round(raw, 6),
        "calibrated_probability": round(calibrated, 6),
        "lower_bound": round(low, 6),
        "upper_bound": round(high, 6),
        "raw_home_probability": round(raw, 6),
        "raw_away_probability": round(1.0 - raw, 6),
        "calibrated_home_probability": round(calibrated, 6),
        "calibrated_away_probability": round(away, 6),
        "calibrated_home_lower_bound": round(low, 6),
        "calibrated_home_upper_bound": round(high, 6),
        "calibrated_away_lower_bound": round(away_lower, 6),
        "calibrated_away_upper_bound": round(away_upper, 6),
        "model_timestamp": model_timestamp,
        "latest_material_update_timestamp": latest_material_update_timestamp,
        "immutable_model_timestamp": model_timestamp,
        "probability_package_valid": True,
        "calibration_status": "PASS",
        "calibration_artifact_certified": True,
        "calibration_health_status": "PASS",
        "calibration_history_present": True,
        "outcome_space": FULL_GAME_SETTLEMENT,
        "settlement_basis": str(evidence.get("settlement_basis") or FULL_GAME_SETTLEMENT),
        "fitted_registry_scoring": True,
        "certified_runtime_adapter": True,
        "code": "SPORTING_PROBABILITY_SCORED_CERTIFIED",
        "blockers": [],
        "probability_publishable": True,
        "rank_eligible": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "can_execute": False,
    }
    ok, package_blockers = validate_prediction_package(package, receipt)
    if not ok:
        raise NHLCertifiedScoringError("TEAM_EVENT_PROBABILITY_PACKAGE_INVALID", package_blockers)
    return package


def _score_matching_certified_artifacts(**kwargs: Any) -> dict[str, Any]:
    """Load receipt-matching artifacts and score one event.

    Production wiring fills this seam. Unit tests monkeypatch it so adapter
    envelope/HOLD behavior can be proven without hydrating a full season corpus.
    """
    raise NHLCertifiedScoringError(
        "MODEL_UNAVAILABLE",
        ["NHL_CERTIFIED_ARTIFACT_LOADER_NOT_WIRED"],
        detail=sorted(kwargs.keys()),
    )


def score_nhl_certified_event(
    req: Any,
    *,
    db: Any,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Score one NHL event under a resolved certification receipt."""
    now = now_utc or datetime.now(timezone.utc)
    evidence = _evidence(req)
    # Request-asserted certification is ignored; only the registry resolve counts.
    try:
        receipt = resolve_certification(db, sport="NHL", league_scope="NHL")
    except TeamEventCertificationError as exc:
        raise NHLCertifiedScoringError(exc.code, [str(exc)]) from exc

    if receipt.controlling_specialist != CONTROLLING_SPECIALIST:
        raise NHLCertifiedScoringError(
            "TEAM_EVENT_CERTIFICATION_SPECIALIST_MISMATCH",
            [receipt.controlling_specialist],
        )
    if receipt.calibration_method == IDENTITY_CALIBRATION_METHOD:
        raise NHLCertifiedScoringError(
            "MODEL_OUTPUT_INVALID", ["NHL_IDENTITY_CALIBRATOR_FORBIDDEN"]
        )

    scored = _score_matching_certified_artifacts(
        db=db,
        req=req,
        receipt=receipt,
        evidence=evidence,
        now_utc=now,
    )
    return build_nhl_certified_envelope(
        receipt=receipt,
        raw_home=float(scored["raw_home"]),
        calibrated_home=float(scored["calibrated_home"]),
        lower=scored.get("lower"),
        upper=scored.get("upper"),
        evidence=evidence,
        model_timestamp=str(scored["model_timestamp"]),
        latest_material_update_timestamp=str(scored["latest_material_update_timestamp"]),
        home_team=str(scored.get("home_team") or getattr(req, "home_team", "") or ""),
        away_team=str(scored.get("away_team") or getattr(req, "away_team", "") or ""),
        official_event_id=str(
            scored.get("official_event_id")
            or evidence.get("official_event_id")
            or ""
        ),
        now_utc=now,
        model_artifact_checksum=str(scored.get("model_artifact_checksum") or ""),
    )


__all__ = [
    "CAN_EXECUTE",
    "CONTROLLING_SPECIALIST",
    "NHLCertifiedScoringError",
    "build_nhl_certified_envelope",
    "score_nhl_certified_event",
]
