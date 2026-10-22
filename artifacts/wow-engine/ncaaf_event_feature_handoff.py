"""NCAAF-only immutable pregame feature-to-fitted-scorer handoff.

This research-stage boundary materializes the existing NCAAF_FEATURES_V1 schema
and can invoke only the governed fitted provider. Static Wilson bounds remain
diagnostics; publication and ranking require independent certified dynamic bounds.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any, Callable, Iterable, Mapping

from ncaaf_feature_compiler import (
    FEATURE_SCHEMA_VERSION,
    REQUIRED_SCOPED_KEYS,
    NCAAFFeatureCompileBlocked,
    compile_official_event_feature_row,
)
from ncaaf_fitted_provider import (
    NCAAFFittedProviderUnavailable,
    NCAAFInferenceRequest,
)
from ncaaf_static_calibrator import NCAAFStaticCalibrationUnavailable

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
CURRENT_CONTEXT_RECEIPT_PURPOSES = (
    "QB_STATUS_HOME",
    "QB_STATUS_AWAY",
    "DEPTH_CHART",
    "INJURY_REPORT",
    "LINEUP",
)
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SNAPSHOT_METADATA_FIELDS = frozenset(
    {"feature_snapshot_id", "created_at", "ok", "code", "probability_publishable"}
)


class NCAAFEventFeatureHandoffUnavailable(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code


def _aware(value: Any, field: str) -> datetime:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(
            str(value or "").replace("Z", "+00:00")
        )
    except (TypeError, ValueError) as exc:
        raise NCAAFEventFeatureHandoffUnavailable(
            "NCAAF_EVENT_TIMESTAMP_INVALID", field
        ) from exc
    if parsed.utcoffset() is None:
        raise NCAAFEventFeatureHandoffUnavailable(
            "NCAAF_EVENT_TIMESTAMP_INVALID", field
        )
    return parsed.astimezone(timezone.utc)


def _hash(value: Any) -> str:
    return sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
        ).encode("utf-8")
    ).hexdigest()


def _receipt(
    value: Any,
    *,
    purpose: str,
    event_id: str,
    kickoff: datetime,
    current_time: datetime,
) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise NCAAFEventFeatureHandoffUnavailable(
            "NCAAF_CURRENT_SOURCE_RECEIPT_MISSING", purpose
        )
    provider = str(value.get("source_provider") or value.get("provider") or "").strip()
    record_id = str(value.get("source_record_id") or value.get("receipt_id") or "").strip()
    receipt_event = str(value.get("official_event_id") or "").strip()
    payload_hash = str(value.get("payload_sha256") or "").lower()
    timestamp = _aware(value.get("evidence_timestamp") or value.get("timestamp"), purpose)
    if not provider or not record_id or receipt_event != event_id or not _HEX_SHA256.fullmatch(payload_hash):
        raise NCAAFEventFeatureHandoffUnavailable(
            "NCAAF_CURRENT_SOURCE_RECEIPT_INVALID", purpose
        )
    if timestamp >= kickoff:
        raise NCAAFEventFeatureHandoffUnavailable(
            "NCAAF_EVENT_FEATURE_TEMPORAL_LEAKAGE", purpose
        )
    if timestamp > current_time:
        raise NCAAFEventFeatureHandoffUnavailable(
            "NCAAF_EVENT_FEATURE_TEMPORAL_LEAKAGE", f"{purpose}:AFTER_SNAPSHOT_AS_OF"
        )
    return {
        "purpose": purpose,
        "source_provider": provider,
        "source_record_id": record_id,
        "evidence_timestamp": timestamp.isoformat(),
        "payload_sha256": payload_hash,
    }


def current_event_receipt_dependencies() -> dict[str, Any]:
    """List the exact receipts needed to build a current NCAAF fitted snapshot."""
    return {
        "official_event_identity": [
            "official_event_id",
            "event_start_time",
            "home_team",
            "away_team",
            "venue",
            "neutral_site",
            "source_provider",
            "source_record_id",
            "payload_sha256",
            "evidence_timestamp",
        ],
        "model_feature_evidence": [f"{kind}:{scope}" for kind, scope in REQUIRED_SCOPED_KEYS],
        "current_context_receipts": list(CURRENT_CONTEXT_RECEIPT_PURPOSES),
        "market_features": "PROHIBITED",
        "receipt_fields": [
            "source_provider",
            "source_record_id",
            "official_event_id",
            "evidence_timestamp",
            "payload_sha256",
        ],
        "can_execute": False,
    }


def _content_digest(row: Mapping[str, Any], base_manifest: Mapping[str, Any]) -> str:
    values = {
        key: value
        for key, value in row.items()
        if key not in _SNAPSHOT_METADATA_FIELDS | {"source_manifest"}
    }
    return _hash({"snapshot": values, "source_manifest": dict(base_manifest)})


def _verify_snapshot_manifest(row: Mapping[str, Any]) -> str:
    manifest = row.get("source_manifest")
    if not isinstance(manifest, Mapping):
        raise NCAAFEventFeatureHandoffUnavailable("NCAAF_SOURCE_MANIFEST_INVALID")
    expected = str(manifest.get("snapshot_sha256") or "").lower()
    base = {key: value for key, value in manifest.items() if key != "snapshot_sha256"}
    if (
        not _HEX_SHA256.fullmatch(expected)
        or manifest.get("market_features_used") is not False
        or _content_digest(row, base) != expected
    ):
        raise NCAAFEventFeatureHandoffUnavailable("NCAAF_SOURCE_MANIFEST_MISMATCH")
    return expected


def build_current_event_feature_snapshot(
    event: Mapping[str, Any],
    evidence_rows: Iterable[Mapping[str, Any]],
    *,
    current_context: Mapping[str, Any],
    now: Any | None = None,
) -> dict[str, Any]:
    """Build one exact-schema snapshot from timestamped, event-bound receipts."""
    event_id = str(event.get("official_event_id") or "").strip()
    home_team = str(event.get("home_team") or "").strip()
    away_team = str(event.get("away_team") or "").strip()
    kickoff = _aware(event.get("event_start_time"), "event_start_time")
    current_time = _aware(now or datetime.now(timezone.utc), "now")
    if (
        not event_id
        or not home_team
        or not away_team
        or home_team.casefold() == away_team.casefold()
        or not str(event.get("venue") or "").strip()
        or not isinstance(event.get("neutral_site"), bool)
        or event.get("can_execute", False) is not False
    ):
        raise NCAAFEventFeatureHandoffUnavailable("NCAAF_EVENT_IDENTITY_INCOMPLETE")
    if kickoff <= current_time:
        raise NCAAFEventFeatureHandoffUnavailable("NCAAF_EVENT_NOT_PREGAME")

    event_receipt = _receipt(
        event.get("source_receipt"),
        purpose="OFFICIAL_EVENT",
        event_id=event_id,
        kickoff=kickoff,
        current_time=current_time,
    )
    context = dict(current_context or {})
    context_receipts = context.get("receipts")
    if not isinstance(context_receipts, Iterable) or isinstance(context_receipts, (str, bytes, Mapping)):
        raise NCAAFEventFeatureHandoffUnavailable("NCAAF_CURRENT_EVENT_CONTEXT_INCOMPLETE")
    receipts_by_purpose: dict[str, dict[str, str]] = {}
    for raw in context_receipts:
        purpose = str(raw.get("purpose") or "").strip().upper() if isinstance(raw, Mapping) else ""
        if purpose in receipts_by_purpose:
            raise NCAAFEventFeatureHandoffUnavailable("NCAAF_CURRENT_SOURCE_RECEIPT_AMBIGUOUS", purpose)
        if purpose not in CURRENT_CONTEXT_RECEIPT_PURPOSES:
            raise NCAAFEventFeatureHandoffUnavailable("NCAAF_CURRENT_SOURCE_RECEIPT_UNEXPECTED", purpose)
        receipts_by_purpose[purpose] = _receipt(
            raw,
            purpose=purpose,
            event_id=event_id,
            kickoff=kickoff,
            current_time=current_time,
        )
    if set(receipts_by_purpose) != set(CURRENT_CONTEXT_RECEIPT_PURPOSES):
        missing = sorted(set(CURRENT_CONTEXT_RECEIPT_PURPOSES) - set(receipts_by_purpose))
        raise NCAAFEventFeatureHandoffUnavailable(
            "NCAAF_CURRENT_EVENT_CONTEXT_INCOMPLETE", ",".join(missing)
        )
    for field in (
        "starting_qb_status_home",
        "starting_qb_status_away",
        "depth_chart_status",
        "lineup_status",
    ):
        if not str(context.get(field) or "").strip():
            raise NCAAFEventFeatureHandoffUnavailable(
                "NCAAF_CURRENT_EVENT_CONTEXT_INCOMPLETE", field
            )
    injury_time = _aware(context.get("injury_evidence_timestamp"), "injury_evidence_timestamp")
    if injury_time >= kickoff or injury_time > current_time:
        raise NCAAFEventFeatureHandoffUnavailable(
            "NCAAF_EVENT_FEATURE_TEMPORAL_LEAKAGE", "INJURY_REPORT"
        )
    if injury_time.isoformat() != receipts_by_purpose["INJURY_REPORT"]["evidence_timestamp"]:
        raise NCAAFEventFeatureHandoffUnavailable("NCAAF_EVENT_FEATURE_TEMPORAL_LEAKAGE", "INJURY_REPORT")

    rows = [dict(row) for row in evidence_rows]
    for row in rows:
        if str(row.get("official_event_id") or "") != event_id:
            continue
        try:
            evidence_time = _aware(row.get("evidence_timestamp"), "evidence_timestamp")
        except NCAAFEventFeatureHandoffUnavailable:
            continue
        if evidence_time >= kickoff:
            raise NCAAFEventFeatureHandoffUnavailable(
                "NCAAF_EVENT_FEATURE_TEMPORAL_LEAKAGE",
                str(row.get("evidence_kind") or "EVIDENCE"),
            )
        if evidence_time > current_time:
            raise NCAAFEventFeatureHandoffUnavailable(
                "NCAAF_EVENT_FEATURE_TEMPORAL_LEAKAGE",
                f"{row.get('evidence_kind') or 'EVIDENCE'}:AFTER_SNAPSHOT_AS_OF",
            )
    game = {
        "official_event_id": event_id,
        "event_start_time": kickoff.isoformat(),
        "home_team": home_team,
        "away_team": away_team,
    }
    try:
        compiled = compile_official_event_feature_row(game, rows)
    except NCAAFFeatureCompileBlocked as exc:
        raise NCAAFEventFeatureHandoffUnavailable(exc.code, ",".join(exc.blockers)) from exc
    if compiled.get("market_home_no_vig") is not None or compiled.get("market_away_no_vig") is not None:
        raise NCAAFEventFeatureHandoffUnavailable("NCAAF_MARKET_FEATURES_PROHIBITED")

    row = {
        "official_event_id": event_id,
        "event_start_time": kickoff.isoformat(),
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_as_of": compiled["feature_as_of"],
        "home_team": home_team,
        "away_team": away_team,
        "venue": str(event["venue"]).strip(),
        "neutral_site": event["neutral_site"],
        "starting_qb_status_home": str(context["starting_qb_status_home"]).strip(),
        "starting_qb_status_away": str(context["starting_qb_status_away"]).strip(),
        "depth_chart_status": str(context["depth_chart_status"]).strip(),
        "injury_evidence_timestamp": injury_time.isoformat(),
        "lineup_status": str(context["lineup_status"]).strip(),
        "market_timestamp": None,
        "market_home_no_vig": None,
        "market_away_no_vig": None,
        "can_execute": False,
        **{
            key: value
            for key, value in compiled.items()
            if key not in {
                "feature_schema_version",
                "feature_source_manifest",
                "weather_temperature_f",
                "market_home_no_vig",
                "market_away_no_vig",
                "market_timestamp",
                "can_execute",
            }
        },
    }
    base_manifest = {
        "compiler_version": compiled["feature_source_manifest"]["compiler_version"],
        "feature_source_manifest": compiled["feature_source_manifest"],
        "official_event_receipt": event_receipt,
        "current_context_receipts": [
            receipts_by_purpose[purpose] for purpose in CURRENT_CONTEXT_RECEIPT_PURPOSES
        ],
        "market_features_used": False,
        "can_execute": False,
    }
    row["source_manifest"] = {
        **base_manifest,
        "snapshot_sha256": _content_digest(row, base_manifest),
    }
    return row


def persist_current_event_feature_snapshot(db: Any, row: Mapping[str, Any]) -> dict[str, Any]:
    """Insert once; never upsert or rewrite an immutable event feature snapshot."""
    digest = _verify_snapshot_manifest(row)
    if row.get("can_execute") is not False or row.get("feature_schema_version") != FEATURE_SCHEMA_VERSION:
        raise NCAAFEventFeatureHandoffUnavailable("NCAAF_EVENT_FEATURE_SNAPSHOT_INVALID")
    try:
        result = db.table("wow_ncaaf_event_feature_snapshots").insert(dict(row)).execute()
    except Exception as exc:
        raise NCAAFEventFeatureHandoffUnavailable(
            "NCAAF_EVENT_FEATURE_SNAPSHOT_IMMUTABLE_INSERT_FAILED", type(exc).__name__
        ) from exc
    return {
        "status": "PERSISTED",
        "official_event_id": str(row["official_event_id"]),
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "source_manifest_sha256": digest,
        "probability_publishable": False,
        "can_execute": False,
    }


def _validate_refreshed_context(
    row: Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    event_id: str,
    kickoff: datetime,
    current_time: datetime,
) -> None:
    if not isinstance(context, Mapping):
        raise NCAAFEventFeatureHandoffUnavailable(
            "NCAAF_CURRENT_EVENT_CONTEXT_REFRESH_REQUIRED"
        )
    raw_receipts = context.get("receipts")
    if not isinstance(raw_receipts, Iterable) or isinstance(
        raw_receipts, (str, bytes, Mapping)
    ):
        raise NCAAFEventFeatureHandoffUnavailable(
            "NCAAF_CURRENT_EVENT_CONTEXT_REFRESH_REQUIRED"
        )
    fresh_receipts: dict[str, dict[str, str]] = {}
    for raw in raw_receipts:
        purpose = str(raw.get("purpose") or "").strip().upper() if isinstance(raw, Mapping) else ""
        if purpose not in CURRENT_CONTEXT_RECEIPT_PURPOSES or purpose in fresh_receipts:
            raise NCAAFEventFeatureHandoffUnavailable(
                "NCAAF_CURRENT_EVENT_CONTEXT_REFRESH_INVALID", purpose
            )
        fresh_receipts[purpose] = _receipt(
            raw,
            purpose=purpose,
            event_id=event_id,
            kickoff=kickoff,
            current_time=current_time,
        )
    if set(fresh_receipts) != set(CURRENT_CONTEXT_RECEIPT_PURPOSES):
        raise NCAAFEventFeatureHandoffUnavailable(
            "NCAAF_CURRENT_EVENT_CONTEXT_REFRESH_REQUIRED"
        )

    manifest = row.get("source_manifest")
    stored_receipts = (
        manifest.get("current_context_receipts") if isinstance(manifest, Mapping) else None
    )
    if not isinstance(stored_receipts, list):
        raise NCAAFEventFeatureHandoffUnavailable("NCAAF_SOURCE_MANIFEST_INVALID")
    stored_by_purpose = {
        str(receipt.get("purpose") or ""): receipt
        for receipt in stored_receipts
        if isinstance(receipt, Mapping)
    }
    for purpose in CURRENT_CONTEXT_RECEIPT_PURPOSES:
        prior = stored_by_purpose.get(purpose)
        if prior is None:
            raise NCAAFEventFeatureHandoffUnavailable("NCAAF_SOURCE_MANIFEST_INVALID")
        prior_time = _aware(prior.get("evidence_timestamp"), purpose)
        fresh_time = _aware(fresh_receipts[purpose]["evidence_timestamp"], purpose)
        if prior_time > current_time or fresh_time < prior_time:
            raise NCAAFEventFeatureHandoffUnavailable(
                "NCAAF_CURRENT_EVENT_CONTEXT_STALE", purpose
            )

    for field in (
        "starting_qb_status_home",
        "starting_qb_status_away",
        "depth_chart_status",
        "lineup_status",
    ):
        value = str(context.get(field) or "").strip()
        if not value:
            raise NCAAFEventFeatureHandoffUnavailable(
                "NCAAF_CURRENT_EVENT_CONTEXT_REFRESH_REQUIRED", field
            )
        if value != str(row.get(field) or "").strip():
            raise NCAAFEventFeatureHandoffUnavailable(
                "NCAAF_CURRENT_EVENT_CONTEXT_CHANGED", field
            )
    injury_time = _aware(context.get("injury_evidence_timestamp"), "injury_evidence_timestamp")
    if injury_time > current_time or injury_time >= kickoff:
        raise NCAAFEventFeatureHandoffUnavailable(
            "NCAAF_EVENT_FEATURE_TEMPORAL_LEAKAGE", "INJURY_REPORT"
        )
    if injury_time.isoformat() != fresh_receipts["INJURY_REPORT"]["evidence_timestamp"]:
        raise NCAAFEventFeatureHandoffUnavailable(
            "NCAAF_CURRENT_EVENT_CONTEXT_REFRESH_INVALID", "INJURY_REPORT"
        )
    if injury_time.isoformat() != str(row.get("injury_evidence_timestamp") or ""):
        raise NCAAFEventFeatureHandoffUnavailable(
            "NCAAF_CURRENT_EVENT_CONTEXT_CHANGED", "injury_evidence_timestamp"
        )


def score_current_event_snapshot(
    db: Any,
    *,
    official_event_id: str,
    event_start_time: str,
    home_team: str,
    away_team: str,
    expected_source_manifest_sha256: str,
    current_context: Mapping[str, Any] | None = None,
    now: Any | None = None,
    scorer: Callable[..., Any] | None = None,
    calibrator: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Score the exact immutable row, withholding publication absent dynamic bounds."""
    kickoff = _aware(event_start_time, "event_start_time")
    current_time = _aware(now or datetime.now(timezone.utc), "now")
    if kickoff <= current_time:
        raise NCAAFEventFeatureHandoffUnavailable("NCAAF_EVENT_NOT_PREGAME")
    if current_context is None:
        raise NCAAFEventFeatureHandoffUnavailable(
            "NCAAF_CURRENT_EVENT_CONTEXT_REFRESH_REQUIRED"
        )
    if not _HEX_SHA256.fullmatch(str(expected_source_manifest_sha256 or "").lower()):
        raise NCAAFEventFeatureHandoffUnavailable("NCAAF_SOURCE_MANIFEST_MISMATCH")
    try:
        result = db.rpc(
            "wow_ncaaf_latest_event_features",
            {
                "p_official_event_id": official_event_id,
                "p_feature_schema_version": FEATURE_SCHEMA_VERSION,
                "p_home_team": home_team,
                "p_away_team": away_team,
            },
        ).execute()
    except Exception as exc:
        raise NCAAFEventFeatureHandoffUnavailable(
            "NCAAF_EVENT_FEATURE_SNAPSHOT_READ_FAILED", type(exc).__name__
        ) from exc
    row = getattr(result, "data", None)
    if not isinstance(row, Mapping) or row.get("ok") is not True:
        code = str(row.get("code") or "") if isinstance(row, Mapping) else ""
        if code == "NCAAF_EVENT_FEATURE_SNAPSHOT_NOT_FOUND":
            raise NCAAFEventFeatureHandoffUnavailable(code)
        raise NCAAFEventFeatureHandoffUnavailable(
            code or "NCAAF_EVENT_FEATURE_SNAPSHOT_INVALID"
        )
    if (
        str(row.get("official_event_id") or "") != official_event_id
        or str(row.get("home_team") or "") != home_team
        or str(row.get("away_team") or "") != away_team
        or str(row.get("feature_schema_version") or "") != FEATURE_SCHEMA_VERSION
        or _aware(row.get("event_start_time"), "event_start_time")
        != _aware(event_start_time, "event_start_time")
    ):
        raise NCAAFEventFeatureHandoffUnavailable("NCAAF_EVENT_FEATURE_COHORT_MISMATCH")
    feature_as_of = _aware(row.get("feature_as_of"), "feature_as_of")
    if feature_as_of >= kickoff:
        raise NCAAFEventFeatureHandoffUnavailable(
            "NCAAF_EVENT_FEATURE_TEMPORAL_LEAKAGE"
        )
    actual_digest = _verify_snapshot_manifest(row)
    if actual_digest != str(expected_source_manifest_sha256).lower():
        raise NCAAFEventFeatureHandoffUnavailable("NCAAF_SOURCE_MANIFEST_MISMATCH")
    _validate_refreshed_context(
        row,
        current_context,
        event_id=official_event_id,
        kickoff=kickoff,
        current_time=current_time,
    )

    inference_request = NCAAFInferenceRequest(
        official_event_id=official_event_id,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        feature_as_of=str(row["feature_as_of"]),
        home_team=home_team,
        away_team=away_team,
    )
    if scorer is None:
        import ncaaf_logistic_adapter  # noqa: F401
        from ncaaf_fitted_provider import infer_raw_probability

        scorer = infer_raw_probability
    try:
        raw = scorer(db, request=inference_request, features=row)
    except NCAAFFittedProviderUnavailable as exc:
        if exc.code in {
            "NCAAF_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND",
            "NCAAF_MODEL_FAMILY_ADAPTER_UNAVAILABLE",
        }:
            raise NCAAFEventFeatureHandoffUnavailable("MODEL_UNAVAILABLE", exc.code) from exc
        if exc.code in {
            "NCAAF_MODEL_ADAPTER_INVALID_OUTPUT",
            "NCAAF_MODEL_OUTPUT_INVALID",
            "NCAAF_MODEL_OUTPUT_NOT_NORMALIZED",
        }:
            raise NCAAFEventFeatureHandoffUnavailable("MODEL_OUTPUT_INVALID", exc.code) from exc
        raise NCAAFEventFeatureHandoffUnavailable(exc.code, str(exc)) from exc
    except Exception as exc:
        raise NCAAFEventFeatureHandoffUnavailable(
            "MODEL_SCORER_FAILED", type(exc).__name__
        ) from exc
    from ncaaf_fitted_provider import RawNCAAFWinProbability

    if not isinstance(raw, RawNCAAFWinProbability):
        raise NCAAFEventFeatureHandoffUnavailable("MODEL_OUTPUT_INVALID")
    try:
        raw.validate()
    except NCAAFFittedProviderUnavailable as exc:
        raise NCAAFEventFeatureHandoffUnavailable(exc.code, str(exc)) from exc

    if calibrator is None:
        from ncaaf_static_calibrator import calibrate_from_registry

        calibrator = calibrate_from_registry
    try:
        static = calibrator(
            db,
            model_artifact_version=raw.model_artifact_version,
            raw_probability=raw.home_probability,
        )
    except NCAAFStaticCalibrationUnavailable as exc:
        raise NCAAFEventFeatureHandoffUnavailable(exc.code, str(exc)) from exc
    except Exception as exc:
        raise NCAAFEventFeatureHandoffUnavailable(
            "NCAAF_STATIC_CALIBRATION_FAILED", type(exc).__name__
        ) from exc

    return {
        "status": "SCORED_HELD",
        "official_event_id": official_event_id,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "source_manifest_sha256": actual_digest,
        "raw_home_probability": raw.home_probability,
        "raw_away_probability": raw.away_probability,
        "static_calibrated_home_probability": static.calibrated_probability,
        "static_calibration_lower_diagnostic": static.calibration_lower_diagnostic,
        "static_calibration_upper_diagnostic": static.calibration_upper_diagnostic,
        "calibrated_lower_bound": None,
        "rank_eligible": False,
        "probability_publishable": False,
        "blockers": ["NCAAF_CERTIFIED_DYNAMIC_BOUND_NOT_AVAILABLE"],
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "CURRENT_CONTEXT_RECEIPT_PURPOSES",
    "NCAAFEventFeatureHandoffUnavailable",
    "PROBABILITY_PUBLISHABLE",
    "build_current_event_feature_snapshot",
    "current_event_receipt_dependencies",
    "persist_current_event_feature_snapshot",
    "score_current_event_snapshot",
]
