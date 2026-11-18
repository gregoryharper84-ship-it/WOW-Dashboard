"""Governed pregame spread receipt producer (V17).

This is the production-side entrypoint that turns a pregame slate plus a
*certified* fitted margin artifact into admitted rows in
``public.wow_v17_spread_card_receipts``. It is the only module that combines the
three authorities a receipt needs:

* the append-only market ledger, for the exact home line and its quote provenance
* a certified fitted margin artifact, for the three-way cover distribution
* ``v17.spread_receipt_registry``, for the insert-only write boundary

Everything is fail-closed and typed. No certified artifact is a
``MODEL_UNAVAILABLE``, never a local fit. Missing ledger evidence is a held row
with the binder's own reason code, never a synthetic line. Missing features are
``MODEL_INPUTS_INSUFFICIENT``, never a default vector. A scorer that throws is
``MODEL_SCORER_FAILED``; malformed model output is ``MODEL_OUTPUT_INVALID``.

The producer never fits a model, never promotes an artifact, never publishes a
probability, and never executes anything. ``can_execute`` is false throughout.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import math
from typing import Any, Callable, Mapping, Sequence

from v17.spread_margin_challenger import (
    MarginDistributionArtifact,
    SpreadChallengerUnavailable,
    score_home_spread,
)
from v17.spread_receipt_registry import (
    MODEL_CLASS,
    TABLE as RECEIPT_TABLE,
    SpreadReceiptRejected,
    persist_certified_spread_receipt,
)

CAN_EXECUTE = False
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS = True
AUTOMATIC_CERTIFICATION = False
AUTOMATIC_PROMOTION = False
MARKET_PROBABILITY_SUBSTITUTION_ALLOWED = False
MONEYLINE_TO_SPREAD_CONVERSION_ALLOWED = False
RUNTIME_GENERATION = "V17_ACTIVE"

SUPPORTED_SPORTS = ("NFL", "NBA", "NCAAF", "NCAAB", "WNBA")
DEFAULT_EVENT_LIMIT = 6
MAX_EVENT_LIMIT = 12
LEDGER_LINE_SOURCE = "APPEND_ONLY_MARKET_LEDGER"
BOUND_METHOD = "EMPIRICAL_RESIDUAL_WILSON_COVER_INTERVAL_V1"
WRITE_SCOPE = "INSERT_ONLY_APPEND_ONLY_RECEIPT_REGISTRY"
REQUIRED_CERTIFIED_FIELDS = (
    "controlling_spread_specialist",
    "calibration_version",
)
ARTIFACT_FIELDS = (
    "sport",
    "model_family",
    "feature_schema_version",
    "feature_names",
    "scaler_mean",
    "scaler_scale",
    "coefficients",
    "intercept",
    "calibration_residuals",
    "train_rows",
    "calibration_rows",
    "test_rows",
    "training_dataset_hash",
    "ridge_alpha",
)


class SpreadProducerUnavailable(RuntimeError):
    """Narrow, typed producer failure. ``code`` is one of the V17 model codes."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def settlement_basis_for(sport: str) -> str:
    """One governed settlement identity per sport, shared with the Daily lane."""
    from v17.daily_snapshot_runtime import _settlement_basis

    return _settlement_basis(str(sport).upper())


def _wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    """Empirical Wilson cover interval from the artifact's own residual sample."""
    if total <= 0:
        raise SpreadProducerUnavailable(
            "MODEL_OUTPUT_INVALID", "distribution sample is empty; no calibrated bound exists"
        )
    p = successes / total
    denom = 1.0 + (z * z / total)
    center = p + (z * z / (2.0 * total))
    radius = z * math.sqrt((p * (1.0 - p) / total) + (z * z / (4.0 * total * total)))
    return max(0.0, (center - radius) / denom), min(1.0, (center + radius) / denom)


def load_certified_spread_artifact(
    payload: Mapping[str, Any] | None, *, sport: str
) -> tuple[MarginDistributionArtifact, dict[str, Any]]:
    """Reconstruct a *certified* artifact from its governed registry payload.

    A challenger or research artifact is refused as ``MODEL_UNAVAILABLE``: the
    required fitted capability (a certified artifact for this sport) does not
    exist, and this module will not certify one. A structurally broken payload is
    ``MODEL_OUTPUT_INVALID`` — a different failure, kept separate.
    """
    normalized = str(sport or "").strip().upper()
    if normalized not in SUPPORTED_SPORTS:
        raise SpreadProducerUnavailable(
            "MODEL_UNAVAILABLE", f"no governed spread market is configured for {normalized or 'UNKNOWN'}"
        )
    if not isinstance(payload, Mapping) or not payload:
        raise SpreadProducerUnavailable(
            "MODEL_UNAVAILABLE",
            "CERTIFIED_SPREAD_ARTIFACT_UNAVAILABLE: no certified fitted spread artifact is registered",
        )

    declared_sport = str(payload.get("sport") or "").strip().upper()
    if declared_sport != normalized:
        raise SpreadProducerUnavailable(
            "MODEL_UNAVAILABLE",
            f"CERTIFIED_SPREAD_ARTIFACT_SPORT_MISMATCH: artifact declares {declared_sport or 'UNKNOWN'}",
        )
    lifecycle = str(payload.get("lifecycle_state") or "").strip().upper()
    if lifecycle != "CERTIFIED" or payload.get("probability_publishable") is not True:
        raise SpreadProducerUnavailable(
            "MODEL_UNAVAILABLE",
            "CERTIFIED_SPREAD_ARTIFACT_NOT_CERTIFIED: "
            f"lifecycle_state={lifecycle or 'UNKNOWN'} probability_publishable={payload.get('probability_publishable')}",
        )
    for name in REQUIRED_CERTIFIED_FIELDS:
        if not str(payload.get(name) or "").strip():
            raise SpreadProducerUnavailable(
                "MODEL_OUTPUT_INVALID", f"certified artifact is missing {name}"
            )
    missing = [name for name in ARTIFACT_FIELDS if payload.get(name) in (None, (), "")]
    if missing:
        raise SpreadProducerUnavailable(
            "MODEL_OUTPUT_INVALID", f"certified artifact payload is missing {sorted(missing)}"
        )

    feature_names = tuple(str(name) for name in payload["feature_names"])
    scaler_mean = tuple(float(v) for v in payload["scaler_mean"])
    scaler_scale = tuple(float(v) for v in payload["scaler_scale"])
    coefficients = tuple(float(v) for v in payload["coefficients"])
    residuals = tuple(float(v) for v in payload["calibration_residuals"])
    width = len(feature_names)
    if width == 0 or not all(len(part) == width for part in (scaler_mean, scaler_scale, coefficients)):
        raise SpreadProducerUnavailable(
            "MODEL_OUTPUT_INVALID", "artifact feature/scaler/coefficient widths disagree"
        )
    if len(residuals) < 2:
        raise SpreadProducerUnavailable(
            "MODEL_OUTPUT_INVALID", "artifact residual distribution is too small to bound"
        )
    if not all(math.isfinite(v) for v in (*scaler_mean, *scaler_scale, *coefficients, *residuals)):
        raise SpreadProducerUnavailable("MODEL_OUTPUT_INVALID", "artifact contains non-finite parameters")

    artifact = MarginDistributionArtifact(
        sport=normalized,
        model_family=str(payload["model_family"]),
        feature_schema_version=str(payload["feature_schema_version"]),
        feature_names=feature_names,
        scaler_mean=scaler_mean,
        scaler_scale=scaler_scale,
        coefficients=coefficients,
        intercept=float(payload["intercept"]),
        calibration_residuals=residuals,
        train_rows=int(payload["train_rows"]),
        calibration_rows=int(payload["calibration_rows"]),
        test_rows=int(payload["test_rows"]),
        training_dataset_hash=str(payload["training_dataset_hash"]),
        ridge_alpha=float(payload["ridge_alpha"]),
    )
    meta = {
        "sport": normalized,
        "model_family": artifact.model_family,
        "model_artifact_version": str(payload.get("model_artifact_version") or artifact.model_family),
        "feature_schema_version": artifact.feature_schema_version,
        "training_dataset_hash": artifact.training_dataset_hash,
        "controlling_spread_specialist": str(payload["controlling_spread_specialist"]),
        "calibration_version": str(payload["calibration_version"]),
        "lifecycle_state": lifecycle,
        "bound_method": BOUND_METHOD,
    }
    return artifact, meta


def _validated_features(
    features: Any, *, artifact: MarginDistributionArtifact, sport: str
) -> dict[str, float]:
    if not isinstance(features, Mapping):
        raise SpreadProducerUnavailable(
            "MODEL_INPUTS_INSUFFICIENT", "no pregame feature vector is available for this event"
        )
    try:
        normalized = {str(name): float(value) for name, value in features.items()}
    except (TypeError, ValueError) as exc:
        raise SpreadProducerUnavailable(
            "MODEL_INPUTS_INSUFFICIENT", "pregame feature vector is not numeric"
        ) from exc
    # The schema is a set of names; artifact order is a fitted detail, not an input contract.
    if tuple(sorted(normalized)) != tuple(sorted(artifact.feature_names)):
        raise SpreadProducerUnavailable(
            "MODEL_INPUTS_INSUFFICIENT",
            f"feature schema does not match the certified {sport} artifact",
        )
    if not all(math.isfinite(value) for value in normalized.values()):
        raise SpreadProducerUnavailable(
            "MODEL_INPUTS_INSUFFICIENT", "pregame feature vector contains non-finite values"
        )
    return normalized


def _validated_score(scored: Any) -> dict[str, Any]:
    if not isinstance(scored, Mapping):
        raise SpreadProducerUnavailable("MODEL_OUTPUT_INVALID", "scorer returned no mapping")
    try:
        p_cover = float(scored["p_cover"])
        p_push = float(scored["p_push"])
        p_not_cover = float(scored["p_not_cover"])
        cover_count = int(scored["cover_count"])
        total = int(scored["distribution_sample_n"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SpreadProducerUnavailable(
            "MODEL_OUTPUT_INVALID", "scorer output is missing a usable cover distribution"
        ) from exc
    legs = (p_cover, p_push, p_not_cover)
    if not all(math.isfinite(leg) and 0.0 <= leg <= 1.0 for leg in legs):
        raise SpreadProducerUnavailable("MODEL_OUTPUT_INVALID", "cover distribution is not a probability")
    if abs(sum(legs) - 1.0) > 1e-9:
        raise SpreadProducerUnavailable("MODEL_OUTPUT_INVALID", "cover distribution does not normalize")
    if total <= 0 or not 0 <= cover_count <= total:
        raise SpreadProducerUnavailable("MODEL_OUTPUT_INVALID", "cover counts are inconsistent")
    return {
        "p_cover": p_cover,
        "p_push": p_push,
        "p_not_cover": p_not_cover,
        "cover_count": cover_count,
        "distribution_sample_n": total,
    }


def _source_receipt_id(*, sport: str, event_id: str, line: float, model_timestamp: str) -> str:
    payload = "|".join([sport, event_id, repr(float(line)), model_timestamp])
    return "spread-receipt-" + sha256(payload.encode("utf-8")).hexdigest()[:32]


def build_receipt_candidate(
    *,
    event: Mapping[str, Any],
    evidence: Any,
    artifact: MarginDistributionArtifact,
    meta: Mapping[str, Any],
    features: Mapping[str, float],
    scored: Mapping[str, Any],
    observed_at: datetime,
) -> dict[str, Any]:
    """Assemble one governed receipt candidate from bound evidence and a score."""
    sport = artifact.sport
    event_id = str(event.get("event_id") or "").strip()
    line = float(evidence.home_spread)
    model_timestamp = observed_at.isoformat()
    lower, upper = _wilson_interval(scored["cover_count"], scored["distribution_sample_n"])
    quote_timestamp = str(evidence.quote_timestamp)
    quote_source_id = str(evidence.market_id or "") or f"{sport}:{evidence.provider_event_id}:{quote_timestamp}"
    return {
        "sport": sport,
        "official_event_id": event_id,
        "event_start_time_utc": str(evidence.event_start_time),
        "home_team": str(event.get("home_team") or evidence.home_team),
        "away_team": str(event.get("away_team") or evidence.away_team),
        "market_family": "POINT_SPREAD",
        "exact_line_side": "HOME",
        "exact_signed_spread": line,
        "settlement_basis": settlement_basis_for(sport),
        "line_source": LEDGER_LINE_SOURCE,
        "quote_source_id": quote_source_id,
        "quote_sportsbook": str(evidence.sportsbook),
        "quote_observed_at_utc": quote_timestamp,
        "provider_event_id": str(evidence.provider_event_id),
        "controlling_spread_specialist": meta["controlling_spread_specialist"],
        "model_artifact_version": meta["model_artifact_version"],
        "model_class": MODEL_CLASS,
        "model_version": artifact.model_family,
        "calibration_version": meta["calibration_version"],
        "p_cover": scored["p_cover"],
        "p_push": scored["p_push"],
        "p_not_cover": scored["p_not_cover"],
        "calibrated_lower_bound": lower,
        "calibrated_upper_bound": upper,
        "model_timestamp": model_timestamp,
        "source_snapshot_timestamp": model_timestamp,
        "event_state": "SCHEDULED",
        "score_snapshot_status": "PASS",
        "source_receipt_id": _source_receipt_id(
            sport=sport, event_id=event_id, line=line, model_timestamp=model_timestamp
        ),
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "controlling_engine_identity": "LLP_TEAM_BETTING_ENGINE",
        "can_execute": CAN_EXECUTE,
        "bound_method": BOUND_METHOD,
        "predicted_home_margin_center": scored.get("predicted_home_margin_center"),
    }


def _held_row(
    event: Mapping[str, Any], *, sport: str, codes: Sequence[str], **details: Any
) -> dict[str, Any]:
    return {
        "event_id": str(event.get("event_id") or ""),
        "sport": sport,
        "status": "HELD",
        "reason_codes": [str(code) for code in codes] or ["SPREAD_RECEIPT_NOT_PRODUCED"],
        "probability_publishable": False,
        "can_execute": False,
        **details,
    }


def produce_pregame_receipt(
    db: Any,
    *,
    event: Mapping[str, Any],
    artifact: MarginDistributionArtifact,
    meta: Mapping[str, Any],
    feature_provider: Callable[..., Any] | None,
    observed_at: datetime,
    writer: Callable[..., dict[str, Any]] = persist_certified_spread_receipt,
    scorer: Callable[..., Mapping[str, Any]] = score_home_spread,
    line_binder: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Produce at most one receipt for one pregame event."""
    sport = artifact.sport
    event_id = str(event.get("event_id") or "").strip()
    if not event_id:
        return _held_row(event, sport=sport, codes=["SPREAD_EVENT_IDENTITY_INCOMPLETE"])
    try:
        start = _dt(event.get("event_start_time"))
    except (TypeError, ValueError):
        return _held_row(event, sport=sport, codes=["SPREAD_EVENT_START_INVALID"])
    if observed_at >= start:
        # No model work is spent on a started event: a late receipt is not a
        # pregame receipt, and admission would hold it anyway.
        return _held_row(event, sport=sport, codes=["SPREAD_EVENT_ALREADY_STARTED"])

    if line_binder is None:
        from v17.spread_forward_auto_canary import try_bind_exact_line

        line_binder = try_bind_exact_line
    lookup = line_binder(
        db,
        sport=sport,
        event={
            "raw_event_id": event_id,
            "event_start_time": start.isoformat(),
            "home_team": event.get("home_team"),
            "away_team": event.get("away_team"),
        },
    )
    evidence = lookup.get("evidence")
    if evidence is None:
        return _held_row(
            event,
            sport=sport,
            codes=list(lookup.get("reason_codes") or ["SPREAD_EXACT_LINE_UNAVAILABLE"]),
            market_evidence_observed_rows=lookup.get("observed_rows", 0),
            blocker_counts=dict(lookup.get("blocker_counts") or {}),
        )

    if feature_provider is None:
        return _held_row(
            event,
            sport=sport,
            codes=["MODEL_INPUTS_INSUFFICIENT", "CERTIFIED_SPREAD_FEATURE_PROVIDER_UNAVAILABLE"],
            exact_line=float(evidence.home_spread),
        )
    try:
        raw_features = feature_provider(db, sport=sport, event=event, artifact=artifact)
    except SpreadProducerUnavailable as exc:
        return _held_row(
            event, sport=sport, codes=[exc.code], exact_line=float(evidence.home_spread)
        )
    except SpreadChallengerUnavailable as exc:
        return _held_row(
            event, sport=sport, codes=["MODEL_INPUTS_INSUFFICIENT", exc.code],
            exact_line=float(evidence.home_spread),
        )
    except Exception as exc:  # noqa: BLE001 - typed input boundary
        return _held_row(
            event,
            sport=sport,
            codes=["MODEL_INPUTS_INSUFFICIENT", f"SPREAD_FEATURE_PROVIDER_FAILED:{type(exc).__name__}"],
            exact_line=float(evidence.home_spread),
        )
    try:
        features = _validated_features(raw_features, artifact=artifact, sport=sport)
    except SpreadProducerUnavailable as exc:
        return _held_row(
            event, sport=sport, codes=[exc.code], exact_line=float(evidence.home_spread)
        )

    try:
        raw_scored = scorer(artifact, features, home_spread=float(evidence.home_spread))
    except SpreadChallengerUnavailable as exc:
        return _held_row(
            event, sport=sport, codes=["MODEL_SCORER_FAILED", exc.code],
            exact_line=float(evidence.home_spread),
        )
    except Exception as exc:  # noqa: BLE001 - typed scorer boundary
        return _held_row(
            event,
            sport=sport,
            codes=["MODEL_SCORER_FAILED", f"SPREAD_SCORER_FAILED:{type(exc).__name__}"],
            exact_line=float(evidence.home_spread),
        )
    try:
        scored = _validated_score(raw_scored)
    except SpreadProducerUnavailable as exc:
        return _held_row(
            event, sport=sport, codes=[exc.code], exact_line=float(evidence.home_spread)
        )

    try:
        candidate = build_receipt_candidate(
            event=event,
            evidence=evidence,
            artifact=artifact,
            meta=meta,
            features=features,
            scored=scored,
            observed_at=observed_at,
        )
    except SpreadProducerUnavailable as exc:
        return _held_row(
            event, sport=sport, codes=[exc.code], exact_line=float(evidence.home_spread)
        )

    try:
        persisted = writer(db, candidate)
    except SpreadReceiptRejected as exc:
        return {
            "event_id": event_id,
            "sport": sport,
            "status": "REJECTED",
            "reason_codes": [exc.code],
            "exact_line": float(evidence.home_spread),
            "probability_publishable": False,
            "can_execute": False,
        }
    if persisted.get("status") != "PERSISTED":
        return _held_row(
            event,
            sport=sport,
            codes=[str(persisted.get("code") or "SPREAD_RECEIPT_PERSISTENCE_UNAVAILABLE")],
            exact_line=float(evidence.home_spread),
        )
    return {
        "event_id": event_id,
        "sport": sport,
        "status": "PERSISTED",
        "reason_codes": [],
        "receipt_key": persisted["receipt_key"],
        "content_sha256": persisted.get("content_sha256"),
        "exact_line_side": "HOME",
        "exact_line": float(evidence.home_spread),
        "p_cover": candidate["p_cover"],
        "p_push": candidate["p_push"],
        "p_not_cover": candidate["p_not_cover"],
        "calibrated_probability_lower_bound": candidate["calibrated_lower_bound"],
        "calibrated_probability_upper_bound": candidate["calibrated_upper_bound"],
        "model_timestamp": candidate["model_timestamp"],
        "probability_publishable": True,
        "can_execute": False,
    }


def run_pregame_spread_production(
    db: Any,
    *,
    sport: str,
    events: Sequence[Mapping[str, Any]],
    artifact_payload: Mapping[str, Any] | None = None,
    feature_provider: Callable[..., Any] | None = None,
    requested_limit: int | None = None,
    observed_at: datetime | None = None,
    writer: Callable[..., dict[str, Any]] = persist_certified_spread_receipt,
    scorer: Callable[..., Mapping[str, Any]] = score_home_spread,
    line_binder: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Score a pregame slate and persist admitted receipts.

    Returns one row per event plus a reconciled summary. Nothing here writes
    anything but admitted receipts, and no row exists without an artifact.
    """
    normalized = str(sport or "").strip().upper()
    limit = DEFAULT_EVENT_LIMIT if requested_limit is None else max(1, min(MAX_EVENT_LIMIT, int(requested_limit)))
    observe = observed_at or _now()
    if observe.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")

    selected: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for event in events:
        event_id = str(event.get("event_id") or "").strip()
        if not event_id or event_id in seen or len(selected) >= limit:
            continue
        seen.add(event_id)
        selected.append(event)

    summary_base = {
        "sport": normalized,
        "rows_in": len(selected),
        "requested_limit": limit,
        "write_scope": WRITE_SCOPE,
        "receipt_table": RECEIPT_TABLE,
        "model_class": MODEL_CLASS,
        "bound_method": BOUND_METHOD,
        "line_source": LEDGER_LINE_SOURCE,
        "can_execute": CAN_EXECUTE,
        "dry_run_only": DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS,
        "automatic_certification": AUTOMATIC_CERTIFICATION,
        "automatic_promotion": AUTOMATIC_PROMOTION,
        "market_probability_substitution_allowed": MARKET_PROBABILITY_SUBSTITUTION_ALLOWED,
        "moneyline_to_spread_conversion_allowed": MONEYLINE_TO_SPREAD_CONVERSION_ALLOWED,
        "observed_at": observe.isoformat(),
    }

    if not selected:
        return {
            "status": "NO_EVENTS",
            "run_summary": {
                **summary_base,
                "receipts_persisted": 0,
                "held": 0,
                "rejected": 0,
            },
            "artifact": None,
            "rows": [],
            "can_execute": False,
        }

    try:
        artifact, meta = load_certified_spread_artifact(artifact_payload, sport=normalized)
    except SpreadProducerUnavailable as exc:
        rows = [
            _held_row(event, sport=normalized, codes=[exc.code], detail=str(exc))
            for event in selected
        ]
        return {
            "status": exc.code,
            "run_summary": {
                **summary_base,
                "receipts_persisted": 0,
                "held": len(rows),
                "rejected": 0,
                "artifact_reason": str(exc),
            },
            "artifact": None,
            "rows": rows,
            "can_execute": False,
        }

    rows = [
        produce_pregame_receipt(
            db,
            event=event,
            artifact=artifact,
            meta=meta,
            feature_provider=feature_provider,
            observed_at=observe,
            writer=writer,
            scorer=scorer,
            line_binder=line_binder,
        )
        for event in selected
    ]
    persisted = sum(1 for row in rows if row["status"] == "PERSISTED")
    rejected = sum(1 for row in rows if row["status"] == "REJECTED")
    held = sum(1 for row in rows if row["status"] == "HELD")
    return {
        "status": "COMPLETE" if persisted == len(rows) else "PARTIAL",
        "run_summary": {
            **summary_base,
            "receipts_persisted": persisted,
            "held": held,
            "rejected": rejected,
            "reconciliation_balanced": len(rows) == persisted + held + rejected,
            "model_family": meta["model_family"],
            "model_artifact_version": meta["model_artifact_version"],
            "training_dataset_hash": meta["training_dataset_hash"],
        },
        "artifact": dict(meta),
        "rows": rows,
        "can_execute": False,
    }


def events_from_moneyline_rows(
    moneyline_rows: Sequence[Mapping[str, Any]], *, sport: str
) -> list[dict[str, Any]]:
    """Project the Daily slate's MONEYLINE identities into producer events."""
    normalized = str(sport or "").strip().upper()
    events: list[dict[str, Any]] = []
    for source in moneyline_rows:
        if source.get("lane") != "MONEYLINE":
            continue
        identity = source.get("identity") or {}
        result = source.get("result") or {}
        row_sport = str(
            identity.get("sport") or result.get("sport") or result.get("league") or ""
        ).strip().upper()
        if row_sport != normalized:
            continue
        event_id = str(identity.get("official_event_id") or "").strip()
        if not event_id:
            continue
        events.append(
            {
                "event_id": event_id,
                "event_start_time": identity.get("event_start_time")
                or identity.get("commence_time_utc"),
                "home_team": identity.get("home_team"),
                "away_team": identity.get("away_team"),
            }
        )
    return events


def run_pregame_spread_production_from_moneyline(
    db: Any, *, moneyline_rows: Sequence[Mapping[str, Any]], sport: str, **kwargs: Any
) -> dict[str, Any]:
    """Slate adapter: Daily MONEYLINE rows in, admitted spread receipts out."""
    return run_pregame_spread_production(
        db,
        sport=sport,
        events=events_from_moneyline_rows(moneyline_rows, sport=sport),
        **kwargs,
    )


__all__ = [
    "AUTOMATIC_CERTIFICATION",
    "AUTOMATIC_PROMOTION",
    "BOUND_METHOD",
    "CAN_EXECUTE",
    "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS",
    "LEDGER_LINE_SOURCE",
    "MAX_EVENT_LIMIT",
    "SpreadProducerUnavailable",
    "build_receipt_candidate",
    "events_from_moneyline_rows",
    "load_certified_spread_artifact",
    "produce_pregame_receipt",
    "run_pregame_spread_production",
    "run_pregame_spread_production_from_moneyline",
    "settlement_basis_for",
]
