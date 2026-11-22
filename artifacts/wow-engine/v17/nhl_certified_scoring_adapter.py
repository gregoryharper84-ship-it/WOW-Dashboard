"""Registry-backed NHL certified scoring adapter (Fix D).

Resolves the immutable team-event certification receipt and emits the V17
certified probability envelope. Candidate research rows stay immutable; request
or catalog self-assertion cannot mint certification. can_execute stays false.

Starting-goalie readiness is never taken from caller-asserted
``goalie_status`` / ``goalie_identity_snapshot_id`` strings. The adapter requires
two server-attested OFFICIAL provider receipts, reconciled through
``nhl_starter_goalie_confirmation.confirm_starting_goalies`` under a
server-owned trusted-provider registry and attestation key.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any, Mapping

from v17.candidate_certification_evidence import _hash
from v17.fitted_team_event_certification import (
    FittedTeamEventCertification,
    TeamEventCertificationError,
    resolve_certification,
    validate_prediction_package,
)
from v17.nhl_candidate_preflight import NHLCandidatePreflightError
from v17.nhl_starter_goalie_confirmation import (
    START_TIME_TOLERANCE,
    TrustedProvider,
    confirm_starting_goalies,
)

CAN_EXECUTE = False
CONTROLLING_SPECIALIST = "NHL_GAME_WIN_PROBABILITY_EXPERT_V1"
IDENTITY_CALIBRATION_METHOD = "IDENTITY_RAW_PROBABILITY_V1"
FULL_GAME_SETTLEMENT = "HOME_AWAY_INCLUDING_OT_SO"
SETTLEMENT_CONTRACT = "FULL_GAME_MONEYLINE_INCLUDING_OVERTIME_SHOOTOUT"
ACCEPTED_SETTLEMENT_BASES = frozenset(
    {
        FULL_GAME_SETTLEMENT,
        SETTLEMENT_CONTRACT,
        "FULL_GAME_INCLUDING_OVERTIME_AND_SHOOTOUT",
        "FULL_GAME_OUTRIGHT",
    }
)
STARTER_PROVIDER_RECEIPTS_FIELD = "starter_provider_receipts"
CANDIDATE_TABLE = "wow_d1_candidate_artifacts"


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


def _require_settlement_basis(evidence: Mapping[str, Any], req: Any | None = None) -> str:
    evidence_basis = evidence.get("settlement_basis")
    if not isinstance(evidence_basis, str) or evidence_basis.strip() not in ACCEPTED_SETTLEMENT_BASES:
        raise NHLCertifiedScoringError(
            "MODEL_INPUTS_INSUFFICIENT", ["NHL_SETTLEMENT_BASIS_UNVERIFIED"]
        )
    if req is not None:
        request_basis = getattr(req, "settlement_basis", None)
        if request_basis not in (None, ""):
            if (
                not isinstance(request_basis, str)
                or request_basis.strip() not in ACCEPTED_SETTLEMENT_BASES
            ):
                raise NHLCertifiedScoringError(
                    "MODEL_INPUTS_INSUFFICIENT", ["NHL_SETTLEMENT_BASIS_MISMATCH"]
                )
    return evidence_basis.strip()


def _text_upper(value: Any) -> str:
    return str(value or "").strip().upper()


def _attested_receipt_matchup(receipts: Any) -> dict[str, Any]:
    """Read event identity from attested receipts after confirmation agreement."""
    if not isinstance(receipts, (list, tuple)) or not receipts:
        raise NHLCertifiedScoringError(
            "MODEL_INPUTS_INSUFFICIENT", ["NHL_STARTER_RECEIPTS_INCOMPLETE"]
        )
    first = receipts[0]
    if not isinstance(first, Mapping):
        raise NHLCertifiedScoringError(
            "MODEL_INPUTS_INSUFFICIENT", ["NHL_STARTER_RECEIPTS_INCOMPLETE"]
        )
    start = _aware(first.get("event_start_time_utc"))
    if start is None:
        raise NHLCertifiedScoringError(
            "MODEL_INPUTS_INSUFFICIENT", ["NHL_STARTER_RECEIPTS_INCOMPLETE"]
        )
    return {
        "official_event_id": str(first.get("official_event_id") or "").strip(),
        "home_team_id": _text_upper(first.get("home_team_id")),
        "away_team_id": _text_upper(first.get("away_team_id")),
        "event_start_time_utc": start,
    }


def _bind_request_matchup_to_attested_receipts(
    *,
    receipts: Any,
    confirmed: Mapping[str, Any],
    evidence: Mapping[str, Any],
    official_event_id: str | None,
    home_team: str | None,
    away_team: str | None,
    event_start_time_utc: Any,
) -> None:
    """Require the scored request matchup to equal the attested receipt identity.

    Valid receipts for one event must not authorize scoring a different
    caller-selected home/away or start time. Missing request identity fails closed
    (never skipped).
    """
    matchup = _attested_receipt_matchup(receipts)
    expected_event = str(
        official_event_id or evidence.get("official_event_id") or ""
    ).strip()
    expected_home = _text_upper(home_team or evidence.get("home_team"))
    expected_away = _text_upper(away_team or evidence.get("away_team"))
    expected_start = _aware(event_start_time_utc) or _aware(
        evidence.get("event_start_time_utc")
    )
    if not expected_event or not expected_home or not expected_away or expected_start is None:
        raise NHLCertifiedScoringError(
            "MODEL_INPUTS_INSUFFICIENT",
            ["NHL_STARTER_REQUEST_MATCHUP_INCOMPLETE"],
            detail={
                "official_event_id": expected_event or None,
                "home_team": expected_home or None,
                "away_team": expected_away or None,
                "event_start_time_utc": None if expected_start is None else expected_start.isoformat(),
            },
        )
    derived_event = str(confirmed.get("official_event_id") or matchup["official_event_id"]).strip()
    if expected_event != derived_event:
        raise NHLCertifiedScoringError(
            "MODEL_INPUTS_INSUFFICIENT",
            ["NHL_STARTER_EVENT_IDENTITY_MISMATCH"],
            detail={"expected": expected_event, "derived": derived_event},
        )
    if expected_home != matchup["home_team_id"] or expected_away != matchup["away_team_id"]:
        raise NHLCertifiedScoringError(
            "MODEL_INPUTS_INSUFFICIENT",
            ["NHL_STARTER_EVENT_IDENTITY_MISMATCH"],
            detail={
                "expected_home": expected_home,
                "expected_away": expected_away,
                "receipt_home": matchup["home_team_id"],
                "receipt_away": matchup["away_team_id"],
            },
        )
    if abs(expected_start - matchup["event_start_time_utc"]) > START_TIME_TOLERANCE:
        raise NHLCertifiedScoringError(
            "MODEL_INPUTS_INSUFFICIENT",
            ["NHL_STARTER_START_TIME_DISAGREEMENT"],
            detail={
                "expected_start": expected_start.isoformat(),
                "receipt_start": matchup["event_start_time_utc"].isoformat(),
            },
        )


def resolve_server_confirmed_goalies(
    evidence: Mapping[str, Any],
    *,
    now_utc: datetime,
    trusted_providers: Mapping[str, TrustedProvider] | None,
    attestation_key: bytes | None,
    official_event_id: str | None = None,
    home_team: str | None = None,
    away_team: str | None = None,
    event_start_time_utc: Any = None,
) -> dict[str, Any]:
    """Derive CONFIRMED goalies from two attested OFFICIAL receipts only.

    Caller-supplied ``goalie_status``, ``goalie_identity_snapshot_id``, or any
    ``derived_server_side`` / ``server_owned`` flag is never authority. Missing
    registry, key, or receipts fail closed through the starter confirmation
    contract codes. The request event id, home/away teams, and start time must
    bind exactly to the attested receipt matchup before scoring may proceed.
    """
    receipts = evidence.get(STARTER_PROVIDER_RECEIPTS_FIELD)
    providers = trusted_providers if isinstance(trusted_providers, Mapping) else {}
    key = attestation_key if isinstance(attestation_key, (bytes, bytearray)) else b""
    try:
        confirmed = confirm_starting_goalies(
            receipts,
            now=now_utc,
            trusted_providers=providers,
            attestation_key=bytes(key),
        )
    except NHLCandidatePreflightError as exc:
        raise NHLCertifiedScoringError(
            "MODEL_INPUTS_INSUFFICIENT",
            [exc.code],
            detail=str(exc),
        ) from exc

    _bind_request_matchup_to_attested_receipts(
        receipts=receipts,
        confirmed=confirmed,
        evidence=evidence,
        official_event_id=official_event_id,
        home_team=home_team,
        away_team=away_team,
        event_start_time_utc=event_start_time_utc,
    )
    return confirmed


def rehydrate_evidence_with_server_goalies(
    evidence: Mapping[str, Any],
    confirmed: Mapping[str, Any],
) -> dict[str, Any]:
    """Replace caller goalie scalars with the derived server confirmation boundary."""
    merged = dict(evidence)
    # Strip any caller-asserted promotion flags; only confirmation output is trusted.
    merged.pop("derived_server_side", None)
    merged.pop("server_owned", None)
    merged["goalie_status"] = confirmed["goalie_status"]
    merged["goalie_identity_snapshot_id"] = confirmed["goalie_identity_snapshot_id"]
    merged["goalie_status_observed_at"] = confirmed["verified_at"]
    merged["home_goalie_id"] = confirmed["home_goalie_id"]
    merged["away_goalie_id"] = confirmed["away_goalie_id"]
    merged["official_event_id"] = confirmed["official_event_id"]
    merged["starter_confirmation"] = {
        "derived_server_side": True,
        "provider_receipts": confirmed.get("provider_receipts"),
        "verified_at": confirmed["verified_at"],
    }
    return merged


def _require_goalie_ready(
    evidence: Mapping[str, Any],
    *,
    now_utc: datetime,
    trusted_providers: Mapping[str, TrustedProvider] | None = None,
    attestation_key: bytes | None = None,
    official_event_id: str | None = None,
    home_team: str | None = None,
    away_team: str | None = None,
    event_start_time_utc: Any = None,
) -> dict[str, Any]:
    return resolve_server_confirmed_goalies(
        evidence,
        now_utc=now_utc,
        trusted_providers=trusted_providers,
        attestation_key=attestation_key,
        official_event_id=official_event_id,
        home_team=home_team,
        away_team=away_team,
        event_start_time_utc=event_start_time_utc,
    )


def load_candidate_matching_receipt(db: Any, receipt: FittedTeamEventCertification) -> Mapping[str, Any]:
    """Load the exact candidate row bound to the certification receipt identities."""
    result = (
        db.table(CANDIDATE_TABLE)
        .select("*")
        .eq("sport", "NHL")
        .eq("model_family", receipt.model_family)
        .eq("model_artifact_version", receipt.model_version)
        .eq("artifact_checksum", receipt.artifact_sha256)
        .limit(2)
        .execute()
    )
    rows = [dict(row) for row in (getattr(result, "data", None) or [])]
    if len(rows) != 1:
        raise NHLCertifiedScoringError(
            "MODEL_UNAVAILABLE",
            ["NHL_CERTIFIED_CANDIDATE_NOT_FOUND"],
            detail={"matches": len(rows)},
        )
    row = rows[0]
    calibrator = row.get("calibrator_payload") if isinstance(row.get("calibrator_payload"), Mapping) else {}
    if str(calibrator.get("method") or "") == IDENTITY_CALIBRATION_METHOD:
        raise NHLCertifiedScoringError(
            "MODEL_OUTPUT_INVALID", ["NHL_IDENTITY_CALIBRATOR_FORBIDDEN"]
        )
    if _hash(calibrator) != receipt.calibration_sha256:
        raise NHLCertifiedScoringError(
            "TEAM_EVENT_CERTIFICATION_CALIBRATION_SHA256_MISMATCH",
            ["NHL_CERTIFIED_CALIBRATOR_FINGERPRINT_MISMATCH"],
        )
    if str(row.get("artifact_checksum") or "").lower() != receipt.artifact_sha256:
        raise NHLCertifiedScoringError(
            "TEAM_EVENT_CERTIFICATION_ARTIFACT_MISMATCH",
            ["NHL_CERTIFIED_ARTIFACT_CHECKSUM_MISMATCH"],
        )
    return row


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
    req: Any | None = None,
    trusted_providers: Mapping[str, TrustedProvider] | None = None,
    attestation_key: bytes | None = None,
) -> dict[str, Any]:
    """Build the certified V17 envelope from a resolved receipt and scored sides."""
    if receipt.sport != "NHL":
        raise NHLCertifiedScoringError("TEAM_EVENT_CERTIFICATION_SPORT_MISMATCH", [receipt.sport])
    if receipt.calibration_method == IDENTITY_CALIBRATION_METHOD:
        raise NHLCertifiedScoringError(
            "MODEL_OUTPUT_INVALID", ["NHL_IDENTITY_CALIBRATOR_FORBIDDEN"]
        )
    settlement_basis = _require_settlement_basis(evidence, req)
    request_home = home_team or (getattr(req, "home_team", None) if req is not None else None)
    request_away = away_team or (getattr(req, "away_team", None) if req is not None else None)
    request_start = (
        getattr(req, "event_start_time_utc", None) if req is not None else None
    )
    _require_goalie_ready(
        evidence,
        now_utc=now_utc,
        trusted_providers=trusted_providers,
        attestation_key=attestation_key,
        official_event_id=official_event_id,
        home_team=str(request_home or "") or None,
        away_team=str(request_away or "") or None,
        event_start_time_utc=request_start or evidence.get("event_start_time_utc"),
    )

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
        "settlement_basis": settlement_basis,
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


def _score_matching_certified_artifacts(
    *,
    db: Any,
    req: Any,
    receipt: FittedTeamEventCertification,
    evidence: Mapping[str, Any],
    now_utc: datetime,
) -> dict[str, Any]:
    """Load receipt-bound artifacts and score one pregame event."""
    import v17.nhl_fitted_specialist as nhl

    row = load_candidate_matching_receipt(db, receipt)
    try:
        candidate = nhl._verify_candidate(row)
    except nhl.NHLFittedSpecialistError as exc:
        raise NHLCertifiedScoringError(exc.code, exc.blockers) from exc

    # Pass the exact registry-verified candidate only for this request.
    # Module-global loader replacement is unsafe across concurrent scoring
    # calls and can mix independently certified artifacts between events.
    try:
        package = nhl.score_current_nhl_event(
            req, db=db, now_utc=now_utc, certified_candidate=candidate,
        )
    except nhl.NHLFittedSpecialistError as exc:
        raise NHLCertifiedScoringError(exc.code, exc.blockers, detail=exc.detail) from exc

    lower = package.get("calibrated_home_lower_bound")
    upper = package.get("calibrated_home_upper_bound")
    if lower is None or upper is None:
        raise NHLCertifiedScoringError(
            "MODEL_OUTPUT_INVALID", ["NHL_CALIBRATED_LOWER_BOUND_UNAVAILABLE"]
        )
    material_candidates = [
        _aware(evidence.get("goalie_status_observed_at")),
        _aware(evidence.get("source_snapshot_timestamp")),
        _aware(package.get("immutable_model_timestamp")),
        candidate.created_at,
    ]
    material_times = [ts for ts in material_candidates if ts is not None and ts <= now_utc]
    latest_material = max(material_times) if material_times else now_utc
    # Model timestamp is the scoring instant and must not predate material inputs.
    model_timestamp = max(now_utc, latest_material)
    return {
        "raw_home": float(package["raw_home_probability"]),
        "calibrated_home": float(package["calibrated_home_probability"]),
        "lower": float(lower),
        "upper": float(upper),
        "model_timestamp": model_timestamp.isoformat(),
        "latest_material_update_timestamp": latest_material.isoformat(),
        "home_team": str(package.get("home_team") or getattr(req, "home_team", "") or ""),
        "away_team": str(package.get("away_team") or getattr(req, "away_team", "") or ""),
        "official_event_id": str(package.get("official_event_id") or evidence.get("official_event_id") or ""),
        "model_artifact_checksum": str(package.get("model_artifact_checksum") or receipt.artifact_sha256),
    }


def score_nhl_certified_event(
    req: Any,
    *,
    db: Any,
    now_utc: datetime | None = None,
    trusted_providers: Mapping[str, TrustedProvider] | None = None,
    attestation_key: bytes | None = None,
) -> dict[str, Any]:
    """Score one NHL event under a resolved certification receipt.

    ``trusted_providers`` and ``attestation_key`` must be supplied by the
    authenticated server-owned acquisition path. They are never read from the
    request body. Without them, starter confirmation fails closed.
    """
    now = now_utc or datetime.now(timezone.utc)
    raw_evidence = _evidence(req)
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

    # Canonicalize goalies before scoring so specialist scalar checks cannot be
    # satisfied by forged caller status/ID strings. Attested receipts must also
    # bind to the request matchup (event id, home/away, start) — not only goalies.
    confirmed = resolve_server_confirmed_goalies(
        raw_evidence,
        now_utc=now,
        trusted_providers=trusted_providers,
        attestation_key=attestation_key,
        official_event_id=str(
            getattr(req, "official_event_id", None)
            or raw_evidence.get("official_event_id")
            or ""
        ),
        home_team=str(getattr(req, "home_team", None) or raw_evidence.get("home_team") or ""),
        away_team=str(getattr(req, "away_team", None) or raw_evidence.get("away_team") or ""),
        event_start_time_utc=(
            getattr(req, "event_start_time_utc", None)
            or raw_evidence.get("event_start_time_utc")
        ),
    )
    evidence = rehydrate_evidence_with_server_goalies(raw_evidence, confirmed)
    # Mirror rehydrated goalie evidence onto the request for the specialist path.
    if hasattr(req, "sport_specific_evidence"):
        try:
            req.sport_specific_evidence = evidence
        except Exception:  # noqa: BLE001 - immutable request objects stay fail-closed via evidence arg
            pass

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
            or confirmed.get("official_event_id")
            or ""
        ),
        now_utc=now,
        model_artifact_checksum=str(scored.get("model_artifact_checksum") or ""),
        req=req,
        trusted_providers=trusted_providers,
        attestation_key=attestation_key,
    )


__all__ = [
    "CAN_EXECUTE",
    "CONTROLLING_SPECIALIST",
    "ACCEPTED_SETTLEMENT_BASES",
    "STARTER_PROVIDER_RECEIPTS_FIELD",
    "NHLCertifiedScoringError",
    "TrustedProvider",
    "load_candidate_matching_receipt",
    "resolve_server_confirmed_goalies",
    "rehydrate_evidence_with_server_goalies",
    "build_nhl_certified_envelope",
    "score_nhl_certified_event",
]
