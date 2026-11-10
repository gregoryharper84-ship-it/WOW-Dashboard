"""Fail-closed V17 evidence-triad requirement for every fitted model lane.

A model has a complete research/acceptance bundle only when the exact fitted
artifact, its exact-version calibrator and its event-specific, pre-lock corpus
are independently checkable. This is a pure validator: it does not query
registries, certify candidates, route probabilities or execute anything.

MODEL_BUNDLE_COMPLETE != probability_publishable. A complete bundle still
requires the sport specialist's governed certification, fresh Action receipt,
calibrated lower bound, independent acceptance and V17_TERMINAL_REDUCER.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import Any, Mapping

from v17.team_event_capability_manifest import EXPECTED_TEAM_EVENT_SPORTS

CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"

COMPONENTS = ("artifact", "calibrator", "pregame_corpus")
MODEL_KINDS = frozenset(("TEAM_EVENT", "PLAYER_PROP", "WEATHER_CONTRACT"))

# Fields a component must supply before the validator can even inspect values.
# Registry readers use this to report exactly which evidence a physical table
# cannot supply, instead of inventing a value or calling the component present.
COMPONENT_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "artifact": (
        "artifact_id",
        "artifact_checksum",
        "training_dataset_hash",
        "training_code_sha",
        "training_rows",
        "payload_checksum_verified",
        "training_provenance_verified",
    ),
    "calibrator": (
        "model_artifact_checksum",
        "calibrator_id",
        "calibrator_checksum",
        "calibration_method",
        "bounds_method_version",
        "calibration_cohort_sha256",
        "holdout_evidence_sha256",
        "calibration_training_n",
        "holdout_overlap_rows",
        "brier",
        "log_loss",
        "calibration_bias",
        "calibration_fit_end",
        "chronological_replay_verified",
        "calibrated_lower_bound_verified",
    ),
    "pregame_corpus": (
        "model_artifact_checksum",
        "official_event_id",
        "event_start_time",
        "source_snapshot_id",
        "source_snapshot_timestamp",
        "source_payload_sha256",
        "feature_snapshot_id",
        "feature_snapshot_timestamp",
        "feature_snapshot_sha256",
        "event_status",
        "source_payload_independently_verified",
        "feature_replay_verified",
        "official_event_identity_verified",
        "settlement_basis_verified",
    ),
}

# A single contract for every declared team/event sport; PLAYER_PROP and
# WEATHER_CONTRACT are defined for exact sport/market-family rows discovered
# from their own specialist registries, not inferred from a team/event model.
TEAM_EVENT_BUNDLE_REQUIREMENTS = {
    sport: COMPONENTS for sport in EXPECTED_TEAM_EVENT_SPORTS
}


@dataclass(frozen=True)
class ModelBundleScope:
    model_kind: str
    sport: str
    model_family: str
    model_artifact_version: str
    feature_schema_version: str
    official_event_id: str
    event_start_time: str
    prediction_timestamp: str
    market_family: str | None = None
    latest_material_update_timestamp: str | None = None
    # Exact selectable prop: the line is a literal string (e.g. "7+") and
    # must never be silently normalized into an adjacent 6.5/7.5 line.
    player_id: str | None = None
    stat_type: str | None = None
    period: str | None = None
    line: str | None = None
    direction: str | None = None
    # Weather settlement identity cannot borrow a nearby station/contract.
    contract_id: str | None = None
    station_id: str | None = None


@dataclass(frozen=True)
class ModelBundleAssessment:
    model_kind: str
    sport: str
    market_family: str | None
    model_family: str
    model_artifact_version: str
    status: str
    blockers: tuple[str, ...]
    artifact_valid: bool
    calibrator_valid: bool
    pregame_corpus_valid: bool
    bundle_complete: bool
    probability_publishable: bool = False
    rank_eligible: bool = False
    automatic_certification: bool = False
    automatic_promotion: bool = False
    can_execute: bool = False
    terminal_authority: str = TERMINAL_AUTHORITY

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_kind": self.model_kind,
            "sport": self.sport,
            "market_family": self.market_family,
            "model_family": self.model_family,
            "model_artifact_version": self.model_artifact_version,
            "required_components": list(COMPONENTS),
            "status": self.status,
            "blockers": list(self.blockers),
            "artifact_valid": self.artifact_valid,
            "calibrator_valid": self.calibrator_valid,
            "pregame_corpus_valid": self.pregame_corpus_valid,
            "bundle_complete": self.bundle_complete,
            "probability_publishable": False,
            "rank_eligible": False,
            "automatic_certification": False,
            "automatic_promotion": False,
            "can_execute": False,
            "terminal_authority": TERMINAL_AUTHORITY,
        }


def _text(value: Any) -> str:
    return str(value or "").strip()


def _sha256(value: Any) -> bool:
    value = _text(value).lower()
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _full_code_sha(value: Any) -> bool:
    token = _text(value).lower()
    return len(token) in (40, 64) and all(ch in "0123456789abcdef" for ch in token)


def _when(value: Any) -> datetime | None:
    raw = _text(value)
    if not raw:
        return None
    try:
        result = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, OverflowError, TypeError):
        return None
    if result.utcoffset() is None:
        return None
    return result.astimezone(timezone.utc)


def _finite_metric(row: Mapping[str, Any], key: str, lo: float, hi: float) -> bool:
    value = row.get(key)
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and isfinite(float(value))
        and lo <= float(value) <= hi
    )


def _matches_identity(row: Mapping[str, Any], scope: ModelBundleScope) -> bool:
    return (
        _text(row.get("model_kind")) == scope.model_kind
        and _text(row.get("sport")).upper() == _text(scope.sport).upper()
        and _text(row.get("model_family")) == scope.model_family
        and _text(row.get("model_artifact_version")) == scope.model_artifact_version
        and _text(row.get("feature_schema_version")) == scope.feature_schema_version
        and _text(row.get("market_family")) == _text(scope.market_family)
    )


def _artifact_valid(artifact: Mapping[str, Any], scope: ModelBundleScope) -> bool:
    return (
        _matches_identity(artifact, scope)
        and bool(_text(artifact.get("artifact_id")))
        and _sha256(artifact.get("artifact_checksum"))
        and _sha256(artifact.get("training_dataset_hash"))
        and _full_code_sha(artifact.get("training_code_sha"))
        and artifact.get("payload_checksum_verified") is True
        and artifact.get("training_provenance_verified") is True
        and type(artifact.get("training_rows")) is int
        and artifact["training_rows"] > 0
    )


def _calibrator_valid(
    calibrator: Mapping[str, Any],
    artifact: Mapping[str, Any],
    scope: ModelBundleScope,
    prediction_at: datetime | None,
) -> bool:
    fit_end = _when(calibrator.get("calibration_fit_end"))
    return (
        _matches_identity(calibrator, scope)
        and _sha256(calibrator.get("model_artifact_checksum"))
        and _text(calibrator.get("model_artifact_checksum")).lower()
            == _text(artifact.get("artifact_checksum")).lower()
        and bool(_text(calibrator.get("calibrator_id")))
        and _sha256(calibrator.get("calibrator_checksum"))
        and bool(_text(calibrator.get("calibration_method")))
        and bool(_text(calibrator.get("bounds_method_version")))
        and _sha256(calibrator.get("calibration_cohort_sha256"))
        and _sha256(calibrator.get("holdout_evidence_sha256"))
        # Distinct custody hashes are necessary, though independent overlap
        # verification remains mandatory for actual cohort disjointness.
        and len({
            _text(artifact.get("training_dataset_hash")).lower(),
            _text(calibrator.get("calibration_cohort_sha256")).lower(),
            _text(calibrator.get("holdout_evidence_sha256")).lower(),
        }) == 3
        and calibrator.get("chronological_replay_verified") is True
        and calibrator.get("calibrated_lower_bound_verified") is True
        and type(calibrator.get("calibration_training_n")) is int
        and calibrator["calibration_training_n"] > 0
        and calibrator.get("holdout_overlap_rows") == 0
        and type(calibrator.get("holdout_overlap_rows")) is int
        and _finite_metric(calibrator, "brier", 0.0, 1.0)
        and _finite_metric(calibrator, "log_loss", 0.0, float("inf"))
        and _finite_metric(calibrator, "calibration_bias", -1.0, 1.0)
        and fit_end is not None
        and prediction_at is not None
        and fit_end <= prediction_at
    )


def _pregame_valid(
    corpus: Mapping[str, Any],
    artifact: Mapping[str, Any],
    scope: ModelBundleScope,
    event_at: datetime | None,
    prediction_at: datetime | None,
    latest_material_at: datetime | None,
) -> bool:
    captured = _when(corpus.get("source_snapshot_timestamp"))
    feature_at = _when(corpus.get("feature_snapshot_timestamp"))
    game_at = _when(corpus.get("event_start_time"))
    return (
        _matches_identity(corpus, scope)
        and _sha256(corpus.get("model_artifact_checksum"))
        and _text(corpus.get("model_artifact_checksum")).lower()
            == _text(artifact.get("artifact_checksum")).lower()
        and bool(_text(corpus.get("source_snapshot_id")))
        and bool(_text(corpus.get("feature_snapshot_id")))
        and _text(corpus.get("official_event_id")) == scope.official_event_id
        and (
            scope.model_kind != "PLAYER_PROP"
            or all(
                _text(corpus.get(field)) == _text(getattr(scope, field))
                for field in ("player_id", "stat_type", "period", "line", "direction")
            )
        )
        and (
            scope.model_kind != "WEATHER_CONTRACT"
            or all(
                _text(corpus.get(field)) == _text(getattr(scope, field))
                for field in ("contract_id", "station_id")
            )
        )
        and _sha256(corpus.get("source_payload_sha256"))
        and _sha256(corpus.get("feature_snapshot_sha256"))
        and corpus.get("source_payload_independently_verified") is True
        and corpus.get("feature_replay_verified") is True
        and corpus.get("official_event_identity_verified") is True
        and corpus.get("settlement_basis_verified") is True
        and corpus.get("event_status") == "SCHEDULED"
        and event_at is not None
        and prediction_at is not None
        and captured is not None
        and feature_at is not None
        and game_at == event_at
        and captured <= feature_at <= prediction_at < event_at
        and (latest_material_at is None or latest_material_at <= feature_at)
    )


def missing_component_fields(
    component: str, row: Mapping[str, Any] | None
) -> tuple[str, ...]:
    """Return the required fields this row does not supply.

    Reporting is intentionally literal: a field is missing when it is absent,
    ``None`` or blank/whitespace-only text. This function never infers a value from an
    adjacent column, a sibling component or a model-family name.
    """
    if component not in COMPONENT_REQUIRED_FIELDS:
        raise KeyError(component)
    required = COMPONENT_REQUIRED_FIELDS[component]
    if not isinstance(row, Mapping):
        return required
    return tuple(
        field for field in required
        if row.get(field) is None
        or (isinstance(row.get(field), str) and not row[field].strip())
    )


def assess_model_bundle(
    scope: ModelBundleScope,
    *,
    artifact: Mapping[str, Any] | None,
    calibrator: Mapping[str, Any] | None,
    pregame_corpus: Mapping[str, Any] | None,
) -> ModelBundleAssessment:
    """Assess exact three-component evidence, without granting publication.

    A separate typed blocker is emitted for missing and invalid components;
    a valid artifact cannot stand in for its calibrator or pregame corpus.
    This function deliberately has no database access, mutation or model fit.
    """
    if not isinstance(scope, ModelBundleScope):
        return ModelBundleAssessment(
            model_kind="UNKNOWN",
            sport="",
            market_family=None,
            model_family="",
            model_artifact_version="",
            status="BLOCKED",
            blockers=("MODEL_BUNDLE_ROUTE_UNSUPPORTED",),
            artifact_valid=False,
            calibrator_valid=False,
            pregame_corpus_valid=False,
            bundle_complete=False,
        )

    blockers: list[str] = []
    supported = (
        isinstance(scope, ModelBundleScope)
        and scope.model_kind in MODEL_KINDS
        and bool(_text(scope.sport))
        and bool(_text(scope.model_family))
        and bool(_text(scope.model_artifact_version))
        and bool(_text(scope.feature_schema_version))
        and bool(_text(scope.official_event_id))
        and (
            scope.model_kind != "TEAM_EVENT"
            or scope.sport in TEAM_EVENT_BUNDLE_REQUIREMENTS
        )
        and (
            scope.model_kind == "TEAM_EVENT"
            or bool(_text(scope.market_family))
        )
        and (
            scope.model_kind != "PLAYER_PROP"
            or all(_text(value) for value in (
                scope.player_id, scope.stat_type, scope.period,
                scope.line, scope.direction,
            ))
        )
        and (
            scope.model_kind != "WEATHER_CONTRACT"
            or all(_text(value) for value in (
                scope.contract_id, scope.station_id,
            ))
        )
    )
    if not supported:
        blockers.append("MODEL_BUNDLE_ROUTE_UNSUPPORTED")

    event_at = _when(scope.event_start_time)
    prediction_at = _when(scope.prediction_timestamp)
    latest_at = _when(scope.latest_material_update_timestamp) if scope.latest_material_update_timestamp else None
    if (
        event_at is None
        or prediction_at is None
        or prediction_at >= event_at
        or (scope.latest_material_update_timestamp and latest_at is None)
    ):
        blockers.append("MODEL_BUNDLE_EVENT_OR_PREDICTION_TIME_INVALID")

    if not isinstance(artifact, Mapping):
        blockers.append("MODEL_BUNDLE_ARTIFACT_MISSING")
    elif not _artifact_valid(artifact, scope):
        blockers.append("MODEL_BUNDLE_ARTIFACT_INVALID_OR_UNVERIFIED")

    if not isinstance(calibrator, Mapping):
        blockers.append("MODEL_BUNDLE_CALIBRATOR_MISSING")
    elif not isinstance(artifact, Mapping) or not _calibrator_valid(calibrator, artifact, scope, prediction_at):
        blockers.append("MODEL_BUNDLE_CALIBRATOR_INVALID_OR_UNVERIFIED")

    if not isinstance(pregame_corpus, Mapping):
        blockers.append("MODEL_BUNDLE_PREGAME_CORPUS_MISSING")
    elif not isinstance(artifact, Mapping) or not _pregame_valid(
        pregame_corpus, artifact, scope, event_at, prediction_at, latest_at
    ):
        blockers.append("MODEL_BUNDLE_PREGAME_CORPUS_INVALID_OR_UNVERIFIED")

    av = not any(code.startswith("MODEL_BUNDLE_ARTIFACT_") for code in blockers)
    cv = not any(code.startswith("MODEL_BUNDLE_CALIBRATOR_") for code in blockers)
    pv = not any(code.startswith("MODEL_BUNDLE_PREGAME_CORPUS_") for code in blockers)
    ready = not blockers
    return ModelBundleAssessment(
        model_kind=scope.model_kind if isinstance(scope, ModelBundleScope) else "UNKNOWN",
        sport=scope.sport if isinstance(scope, ModelBundleScope) else "",
        market_family=scope.market_family if isinstance(scope, ModelBundleScope) else None,
        model_family=scope.model_family if isinstance(scope, ModelBundleScope) else "",
        model_artifact_version=scope.model_artifact_version if isinstance(scope, ModelBundleScope) else "",
        status="READY_FOR_INDEPENDENT_REVIEW" if ready else "BLOCKED",
        blockers=tuple(dict.fromkeys(blockers)),
        artifact_valid=av,
        calibrator_valid=cv,
        pregame_corpus_valid=pv,
        bundle_complete=ready,
    )


__all__ = [
    "CAN_EXECUTE",
    "COMPONENTS",
    "COMPONENT_REQUIRED_FIELDS",
    "MODEL_KINDS",
    "TEAM_EVENT_BUNDLE_REQUIREMENTS",
    "TERMINAL_AUTHORITY",
    "ModelBundleAssessment",
    "ModelBundleScope",
    "assess_model_bundle",
    "missing_component_fields",
]
