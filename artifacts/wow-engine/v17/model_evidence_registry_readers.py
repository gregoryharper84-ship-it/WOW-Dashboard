"""Read-only registry readers feeding the V17 three-part model evidence triad.

The pure contract in :mod:`v17.model_evidence_bundle_contract` can only assess
components it is handed. This module is the connection layer to governed
persistence, and it is deliberately narrow:

* it reads existing tables; it never writes, fits, calibrates, certifies,
  promotes, routes a probability or executes anything;
* a component is marked verified only when an immutable specialist
  verification receipt contains an explicit ``True`` for that exact field --
  a column named like a verification flag is never trusted;
* columns a physical table cannot supply stay absent, so the contract reports
  the gap instead of receiving a substituted hash, timestamp or identity;
* unconfigured, unreadable and ambiguous components become distinct typed
  blockers rather than empty results that could read as "nothing wrong".

Reading a complete and verified triad still yields only
``READY_FOR_INDEPENDENT_REVIEW``; publication, certification, ranking and
execution remain outside this layer.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from v17.model_evidence_bundle_contract import (
    COMPONENTS,
    ModelBundleScope,
    assess_model_bundle,
    missing_component_fields,
)

CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"

FOUND = "FOUND"
MISSING = "MISSING"
NOT_CONFIGURED = "NOT_CONFIGURED"
AMBIGUOUS = "AMBIGUOUS"
READ_FAILED = "READ_FAILED"
READ_STATES = (FOUND, MISSING, NOT_CONFIGURED, AMBIGUOUS, READ_FAILED)

VERIFICATION_TABLE = "wow_team_event_specialist_certifications"

# Explicit receipt keys per component. Absence of a key is not verification.
_VERIFICATION_BINDINGS: dict[str, dict[str, str]] = {
    "artifact": {
        "payload_checksum_verified": "artifact_payload_checksum_verified",
        "training_provenance_verified": "training_provenance_verified",
    },
    "calibrator": {
        "chronological_replay_verified": "calibrator_chronological_replay_verified",
        "calibrated_lower_bound_verified": "calibrated_lower_bound_verified",
    },
    "pregame_corpus": {
        "source_payload_independently_verified": "pregame_source_payload_verified",
        "feature_replay_verified": "pregame_feature_replay_verified",
        "official_event_identity_verified": "pregame_official_event_identity_verified",
        "settlement_basis_verified": "pregame_settlement_basis_verified",
    },
}


@dataclass(frozen=True)
class TableSpec:
    """A physical table plus the exact contract-field mapping it can support."""

    table: str
    field_columns: Mapping[str, str]
    filters: tuple[tuple[str, str], ...] = ()
    order_by: str | None = None
    # Literal predicates that belong to the declared reader rather than to the
    # assessed scope (for example the single current pregame snapshot row).
    static_filters: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class LaneReader:
    model_kind: str
    sport: str
    artifact: TableSpec | None
    calibrator: TableSpec | None
    pregame_corpus: TableSpec | None
    # Calibrator rows that carry no model linkage are bound by the artifact's
    # immutable foreign key; the pair is (calibrator column, artifact field).
    calibrator_link: tuple[str, str] | None = None


@dataclass(frozen=True)
class ComponentRead:
    component: str
    status: str
    row: Mapping[str, Any] | None
    table: str | None
    blockers: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "table": self.table,
            "blockers": list(self.blockers),
        }


@dataclass(frozen=True)
class RegistryEvidence:
    artifact: Mapping[str, Any] | None
    calibrator: Mapping[str, Any] | None
    pregame_corpus: Mapping[str, Any] | None
    components: Mapping[str, ComponentRead]
    missing_fields: Mapping[str, tuple[str, ...]]
    read_blockers: tuple[str, ...]

    def component_status(self) -> dict[str, dict[str, Any]]:
        return {name: self.components[name].as_dict() for name in COMPONENTS}


_NFL_ARTIFACT = TableSpec(
    table="wow_nfl_event_fitted_model_artifacts",
    field_columns={
        "artifact_id": "artifact_id",
        "model_family": "model_family",
        "model_artifact_version": "model_artifact_version",
        "feature_schema_version": "feature_schema_version",
        "artifact_checksum": "artifact_checksum",
        "training_dataset_hash": "training_dataset_hash",
        "training_code_sha": "training_code_sha",
        "training_rows": "training_rows",
        # Read only to bind the calibrator foreign key; it is not evidence.
        "calibrator_id": "calibrator_id",
    },
    filters=(
        ("model_artifact_version", "model_artifact_version"),
        ("feature_schema_version", "feature_schema_version"),
    ),
    order_by="created_at",
)

_MLB_ARTIFACT = TableSpec(
    table="wow_mlb_event_fitted_model_artifacts",
    field_columns=dict(_NFL_ARTIFACT.field_columns),
    filters=_NFL_ARTIFACT.filters,
    order_by="created_at",
)

_BASKETBALL_ARTIFACT_COLUMNS = {
    "artifact_id": "artifact_id",
    "sport": "sport",
    "model_family": "model_family",
    "model_artifact_version": "model_artifact_version",
    "feature_schema_version": "feature_schema_version",
    "artifact_checksum": "artifact_checksum",
    "training_rows": "training_row_count",
}

_BASKETBALL_CALIBRATOR = TableSpec(
    table="wow_basketball_team_event_calibrators",
    field_columns={
        "calibrator_id": "calibrator_id",
        "model_artifact_version": "model_artifact_version",
        "calibration_method": "calibration_method",
        "calibration_training_n": "calibration_row_count",
        "brier": "brier",
        "log_loss": "log_loss",
        "calibration_bias": "calibration_bias",
    },
    filters=(("sport", "sport"), ("model_artifact_version", "model_artifact_version")),
    order_by="created_at",
)

_BASKETBALL_PREGAME_COLUMNS = {
    "official_event_id": "game_id",
    "feature_schema_version": "feature_schema_version",
    "feature_snapshot_sha256": "feature_payload_sha256",
    "feature_snapshot_timestamp": "as_of",
}

_PROP_ARTIFACT = TableSpec(
    table="wow_prop_fitted_model_artifacts",
    field_columns={
        "artifact_id": "artifact_id",
        "sport": "sport",
        "model_family": "model_family",
        "model_artifact_version": "model_artifact_version",
        "feature_schema_version": "feature_schema_version",
        "artifact_checksum": "artifact_checksum",
        "training_dataset_hash": "training_dataset_hash",
        "training_code_sha": "training_code_sha",
        "training_rows": "training_rows",
    },
    filters=(
        ("sport", "sport"),
        ("stat_type", "stat_type"),
        ("model_artifact_version", "model_artifact_version"),
        ("feature_schema_version", "feature_schema_version"),
    ),
    order_by="created_at",
)

# MLB declares its own event-specific prelock corpus and exact-version
# calibrator evidence. The generic wow_calibrators table cannot supply a
# calibrator checksum, cohort hash, holdout evidence or overlap count, and the
# NFL pregame matrix has no per-event tipoff/source identity, so neither may
# stand in for these tables.
_MLB_CALIBRATOR_EVIDENCE = TableSpec(
    table="wow_mlb_event_calibrator_evidence",
    field_columns={
        "calibrator_id": "calibrator_id",
        "model_family": "model_family",
        "model_artifact_version": "model_artifact_version",
        "feature_schema_version": "feature_schema_version",
        "model_artifact_checksum": "model_artifact_checksum",
        "calibrator_checksum": "calibrator_checksum",
        "calibration_method": "calibration_method",
        "bounds_method_version": "bounds_method_version",
        "calibration_coefficients_sha256": "calibration_coefficients_sha256",
        "calibration_receipt_manifest": "calibration_receipt_manifest",
        "calibration_receipt_manifest_sha256": "calibration_receipt_manifest_sha256",
        "calibration_cohort_sha256": "calibration_cohort_sha256",
        "holdout_evidence_sha256": "holdout_evidence_sha256",
        "calibration_training_n": "calibration_training_n",
        "holdout_n": "holdout_n",
        "holdout_overlap_rows": "holdout_overlap_rows",
        "brier": "brier",
        "log_loss": "log_loss",
        "calibration_bias": "calibration_bias",
        "holdout_incumbent_brier": "holdout_incumbent_brier",
        "holdout_incumbent_log_loss": "holdout_incumbent_log_loss",
        "holdout_brier_delta": "holdout_brier_delta",
        "holdout_log_loss_delta": "holdout_log_loss_delta",
        "holdout_calibrated_worse_n": "holdout_calibrated_worse_n",
        "holdout_bounds_lower_mean": "holdout_bounds_lower_mean",
        "holdout_bounds_upper_mean": "holdout_bounds_upper_mean",
        "holdout_lower_bound_reliability_margin": (
            "holdout_lower_bound_reliability_margin"
        ),
        "calibration_fit_end": "calibration_fit_end",
    },
    filters=(
        ("model_artifact_version", "model_artifact_version"),
        ("feature_schema_version", "feature_schema_version"),
    ),
    order_by="created_at",
)

_MLB_PREGAME = TableSpec(
    table="wow_mlb_event_pregame_feature_rows",
    field_columns={
        "model_family": "model_family",
        "model_artifact_version": "model_artifact_version",
        "feature_schema_version": "feature_schema_version",
        "model_artifact_checksum": "model_artifact_checksum",
        "official_event_id": "official_event_id",
        "event_start_time": "event_start_time",
        "event_status": "event_status",
        "source_snapshot_id": "source_snapshot_id",
        "source_snapshot_timestamp": "source_snapshot_timestamp",
        "source_payload_sha256": "source_payload_sha256",
        "feature_snapshot_id": "feature_snapshot_id",
        "feature_snapshot_timestamp": "feature_snapshot_timestamp",
        "feature_snapshot_sha256": "feature_snapshot_sha256",
    },
    filters=(
        ("official_event_id", "official_event_id"),
        ("model_artifact_version", "model_artifact_version"),
        ("feature_schema_version", "feature_schema_version"),
    ),
    order_by="created_at",
    # Only the single current snapshot is servable evidence; superseded rows are
    # retained for audit. Two current rows remain AMBIGUOUS and fail closed.
    static_filters=(("is_current", "true"),),
)

_NFL_CALIBRATOR_EVIDENCE = TableSpec(
    table="wow_nfl_event_calibrator_evidence",
    field_columns={
        "calibrator_id": "calibrator_id",
        "model_family": "model_family",
        "model_artifact_version": "model_artifact_version",
        "feature_schema_version": "feature_schema_version",
        "model_artifact_checksum": "model_artifact_checksum",
        "calibrator_checksum": "calibrator_checksum",
        "calibration_method": "calibration_method",
        "bounds_method_version": "bounds_method_version",
        "calibration_cohort_sha256": "calibration_cohort_sha256",
        "holdout_evidence_sha256": "holdout_evidence_sha256",
        "calibration_training_n": "calibration_training_n",
        "holdout_overlap_rows": "holdout_overlap_rows",
        "brier": "brier",
        "log_loss": "log_loss",
        "calibration_bias": "calibration_bias",
        "calibration_fit_end": "calibration_fit_end",
    },
    filters=(
        ("model_artifact_version", "model_artifact_version"),
        ("feature_schema_version", "feature_schema_version"),
    ),
    order_by="created_at",
)

_NFL_PREGAME_EVIDENCE = TableSpec(
    table="wow_nfl_event_pregame_feature_rows",
    field_columns={
        "model_family": "model_family",
        "model_artifact_version": "model_artifact_version",
        "feature_schema_version": "feature_schema_version",
        "model_artifact_checksum": "model_artifact_checksum",
        "official_event_id": "official_event_id",
        "event_start_time": "event_start_time",
        "event_status": "event_status",
        "source_snapshot_id": "source_snapshot_id",
        "source_snapshot_timestamp": "source_snapshot_timestamp",
        "source_payload_sha256": "source_payload_sha256",
        "feature_snapshot_id": "feature_snapshot_id",
        "feature_snapshot_timestamp": "feature_snapshot_timestamp",
        "feature_snapshot_sha256": "feature_snapshot_sha256",
    },
    filters=(
        ("official_event_id", "official_event_id"),
        ("model_artifact_version", "model_artifact_version"),
        ("feature_schema_version", "feature_schema_version"),
    ),
    order_by="created_at",
    # Only the single current snapshot is servable evidence; superseded rows are
    # retained for audit. Two current rows remain AMBIGUOUS and fail closed.
    static_filters=(("is_current", "true"),),
)

# Only lanes whose artifact, calibrator and pregame tables can be named with
# confidence are configured. Every other declared sport reports an explicit
# unconfigured blocker: the D1 candidate registry is research evidence, not a
# dedicated per-event pregame corpus and not a verification receipt source.
_LANES: dict[tuple[str, str], LaneReader] = {
    ("TEAM_EVENT", "NFL"): LaneReader(
        "TEAM_EVENT", "NFL", _NFL_ARTIFACT, _NFL_CALIBRATOR_EVIDENCE, _NFL_PREGAME_EVIDENCE,
        calibrator_link=("calibrator_id", "calibrator_id"),
    ),
    ("TEAM_EVENT", "MLB"): LaneReader(
        "TEAM_EVENT", "MLB", _MLB_ARTIFACT, _MLB_CALIBRATOR_EVIDENCE, _MLB_PREGAME,
        calibrator_link=("calibrator_id", "calibrator_id"),
    ),
    ("TEAM_EVENT", "NBA"): LaneReader(
        "TEAM_EVENT", "NBA",
        TableSpec(
            table="wow_basketball_team_event_model_artifacts",
            field_columns=_BASKETBALL_ARTIFACT_COLUMNS,
            filters=(
                ("sport", "sport"),
                ("model_artifact_version", "model_artifact_version"),
                ("feature_schema_version", "feature_schema_version"),
            ),
            order_by="created_at",
        ),
        _BASKETBALL_CALIBRATOR,
        TableSpec(
            table="wow_nba_pregame_feature_rows",
            field_columns=_BASKETBALL_PREGAME_COLUMNS,
            filters=(("game_id", "official_event_id"),),
        ),
    ),
    ("TEAM_EVENT", "WNBA"): LaneReader(
        "TEAM_EVENT", "WNBA",
        TableSpec(
            table="wow_basketball_team_event_model_artifacts",
            field_columns=_BASKETBALL_ARTIFACT_COLUMNS,
            filters=(
                ("sport", "sport"),
                ("model_artifact_version", "model_artifact_version"),
                ("feature_schema_version", "feature_schema_version"),
            ),
            order_by="created_at",
        ),
        _BASKETBALL_CALIBRATOR,
        TableSpec(
            table="wow_wnba_pregame_feature_rows",
            field_columns=_BASKETBALL_PREGAME_COLUMNS,
            filters=(("game_id", "official_event_id"),),
        ),
    ),
    ("PLAYER_PROP", "*"): LaneReader(
        "PLAYER_PROP", "*", _PROP_ARTIFACT, None, None,
    ),
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _resolve(row: Mapping[str, Any], path: str) -> Any:
    value: Any = row
    for part in path.split("."):
        if not isinstance(value, Mapping):
            return None
        value = value.get(part)
    return value


def lane_configured(model_kind: Any, sport: Any) -> bool:
    """Does a declared reader lane exist for this exact model kind and sport?

    Used by serving paths whose identity is not a `ModelBundleScope` yet. A
    transient lane can never be invented here: an undeclared lane is reported as
    unconfigured downstream instead of being read from a look-alike table.
    """
    kind = _text(model_kind)
    name = _text(sport).upper()
    if not kind:
        return False
    return (kind, name) in _LANES or (kind, "*") in _LANES


def lane_for(scope: ModelBundleScope) -> LaneReader | None:
    """Return the declared reader lane for this exact model/sport, or None."""
    if not isinstance(scope, ModelBundleScope):
        return None
    kind = _text(scope.model_kind)
    sport = _text(scope.sport).upper()
    return _LANES.get((kind, sport)) or _LANES.get((kind, "*"))


def _map_row(spec: TableSpec, row: Mapping[str, Any], scope: ModelBundleScope) -> dict[str, Any]:
    mapped = {field: _resolve(row, path) for field, path in spec.field_columns.items()}
    # These three are constrained by the query filters themselves, so recording
    # them is not an identity claim about any other lane's row.
    mapped["model_kind"] = scope.model_kind
    mapped["sport"] = scope.sport
    if _text(scope.market_family):
        mapped["market_family"] = scope.market_family
    return mapped


def _read_component(
    db: Any,
    component: str,
    spec: TableSpec | None,
    scope: ModelBundleScope,
    extra_filters: tuple[tuple[str, str], ...] = (),
) -> ComponentRead:
    code = f"MODEL_BUNDLE_{component.upper()}"
    if spec is None:
        return ComponentRead(component, NOT_CONFIGURED, None, None, (f"{code}_SOURCE_NOT_CONFIGURED",))
    if db is None:
        return ComponentRead(component, READ_FAILED, None, spec.table, (f"{code}_READ_FAILED",))
    try:
        query = db.table(spec.table).select("*")
        for column, attribute in spec.filters:
            value = _text(getattr(scope, attribute, None))
            if not value:
                return ComponentRead(component, READ_FAILED, None, spec.table, (f"{code}_READ_FAILED",))
            query = query.eq(column, value)
        for column, value in spec.static_filters:
            query = query.eq(column, value)
        for column, value in extra_filters:
            if not value:
                return ComponentRead(component, READ_FAILED, None, spec.table, (f"{code}_READ_FAILED",))
            query = query.eq(column, value)
        if spec.order_by:
            query = query.order(spec.order_by, desc=True)
        result = query.limit(2).execute()
        rows = [dict(row) for row in (getattr(result, "data", None) or [])]
    except Exception:  # noqa: BLE001 - any read failure must stay fail-closed
        return ComponentRead(component, READ_FAILED, None, spec.table, (f"{code}_READ_FAILED",))
    if not rows:
        return ComponentRead(component, MISSING, None, spec.table)
    if len(rows) > 1:
        return ComponentRead(component, AMBIGUOUS, None, spec.table, (f"{code}_AMBIGUOUS",))
    return ComponentRead(component, FOUND, _map_row(spec, rows[0], scope), spec.table)


def _read_verification(
    db: Any,
    scope: ModelBundleScope,
    artifact: Mapping[str, Any] | None,
    calibrator: Mapping[str, Any] | None,
    pregame_corpus: Mapping[str, Any] | None,
) -> tuple[dict[str, dict[str, bool]], str | None]:
    """Verify component-specific attestation, not merely an artifact receipt.

    A team/event artifact certification never attests to *all* future games.
    Calibrator flags require the exact immutable calibrator ID/checksum; event
    pregame flags require this event and its snapshot IDs/checksums. Missing
    bindings remain False even when the artifact receipt's booleans are True.
    """
    checksum = _text((artifact or {}).get("artifact_checksum")).lower()
    if not checksum:
        return {}, "MODEL_BUNDLE_VERIFICATION_RECEIPT_UNBINDABLE"
    if db is None:
        return {}, "MODEL_BUNDLE_VERIFICATION_RECEIPT_READ_FAILED"
    try:
        result = (
            db.table(VERIFICATION_TABLE)
            .select("*")
            .eq("sport", _text(scope.sport).upper())
            .eq("model_version", _text(scope.model_artifact_version))
            .eq("artifact_sha256", checksum)
            .eq("feature_schema_version", _text(scope.feature_schema_version))
            .eq("certification_status", "CERTIFIED")
            .eq("independent_verification_status", "PASS")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = [dict(row) for row in (getattr(result, "data", None) or [])]
    except Exception:  # noqa: BLE001 - a receipt read failure is never "verified"
        return {}, "MODEL_BUNDLE_VERIFICATION_RECEIPT_READ_FAILED"
    if not rows:
        return {}, "MODEL_BUNDLE_VERIFICATION_RECEIPT_MISSING"
    receipt_row = rows[0]
    payload = receipt_row.get("verification_receipt")
    if not isinstance(payload, Mapping):
        payload = {}

    # These bindings are intentionally strict: the current generic calibrator
    # registry does not store a calibrator checksum, and the current game feature
    # tables do not store all source snapshot identities. Those models therefore
    # remain held until the missing independent evidence is persisted.
    calibrator_checksum = _text((calibrator or {}).get("calibrator_checksum")).lower()
    calibration_row_checksum = _text(receipt_row.get("calibration_sha256")).lower()
    calibrator_bound = bool(
        calibrator_checksum
        and len(calibrator_checksum) == 64
        and calibrator_checksum == calibration_row_checksum
        and calibrator_checksum == _text(payload.get("calibrator_sha256")).lower()
        and _text((calibrator or {}).get("calibrator_id"))
            == _text(receipt_row.get("calibration_artifact_id"))
        and _text((calibrator or {}).get("calibrator_id"))
            == _text(payload.get("calibrator_id"))
    )

    feature_checksum = _text((pregame_corpus or {}).get("feature_snapshot_sha256")).lower()
    source_checksum = _text((pregame_corpus or {}).get("source_payload_sha256")).lower()
    event_bound = bool(
        scope.official_event_id
        and _text(payload.get("official_event_id")) == scope.official_event_id
        and _text((pregame_corpus or {}).get("official_event_id")) == scope.official_event_id
        and feature_checksum
        and feature_checksum == _text(payload.get("feature_snapshot_sha256")).lower()
        and source_checksum
        and source_checksum == _text(payload.get("source_payload_sha256")).lower()
        and _text((pregame_corpus or {}).get("feature_snapshot_id"))
            == _text(payload.get("feature_snapshot_id"))
        and _text((pregame_corpus or {}).get("source_snapshot_id"))
            == _text(payload.get("source_snapshot_id"))
        and _text(payload.get("event_start_time")) == scope.event_start_time
    )
    return {
        "artifact": {
            field: payload.get(key) is True
            for field, key in _VERIFICATION_BINDINGS["artifact"].items()
        },
        "calibrator": {
            field: calibrator_bound and payload.get(key) is True
            for field, key in _VERIFICATION_BINDINGS["calibrator"].items()
        },
        "pregame_corpus": {
            field: event_bound and payload.get(key) is True
            for field, key in _VERIFICATION_BINDINGS["pregame_corpus"].items()
        },
    }, None


def read_model_evidence_bundle(db: Any, scope: ModelBundleScope) -> RegistryEvidence:
    """Read the three components for one exact scope without granting trust."""
    lane = lane_for(scope)
    artifact_read = _read_component(
        db, "artifact", lane.artifact if lane else None, scope
    )

    extra: tuple[tuple[str, str], ...] = ()
    calibrator_override: ComponentRead | None = None
    if lane is not None and lane.calibrator is not None and lane.calibrator_link is not None:
        column, field = lane.calibrator_link
        linked = _text((artifact_read.row or {}).get(field))
        if linked:
            extra = ((column, linked),)
        else:
            # No artifact row, or an artifact row with no calibrator foreign
            # key: the calibrator cannot be bound, so it is not looked up by
            # model family, cohort name or any other substitutable identity.
            calibrator_override = ComponentRead(
                "calibrator", MISSING, None, lane.calibrator.table,
                ("MODEL_BUNDLE_CALIBRATOR_LINK_UNAVAILABLE",),
            )

    calibrator_read = calibrator_override or _read_component(
        db, "calibrator", lane.calibrator if lane else None, scope, extra
    )
    pregame_read = _read_component(
        db, "pregame_corpus", lane.pregame_corpus if lane else None, scope
    )

    reads = {
        "artifact": artifact_read,
        "calibrator": calibrator_read,
        "pregame_corpus": pregame_read,
    }
    verification, verification_blocker = _read_verification(
        db, scope, artifact_read.row, calibrator_read.row, pregame_read.row
    )
    for component, read in reads.items():
        if read.row is None:
            continue
        flags = verification.get(component, {})
        for field in _VERIFICATION_BINDINGS[component]:
            # Override, never setdefault: a registry column with this name is
            # not a governed verification receipt.
            read.row[field] = flags.get(field, False)

    blockers: list[str] = []
    for component in COMPONENTS:
        blockers.extend(reads[component].blockers)
    if verification_blocker:
        blockers.append(verification_blocker)
    for component in COMPONENTS:
        if reads[component].status == FOUND:
            blockers.extend(
                f"MODEL_BUNDLE_{component.upper()}_FIELD_UNAVAILABLE:{field}"
                for field in missing_component_fields(component, reads[component].row)
            )

    return RegistryEvidence(
        artifact=reads["artifact"].row,
        calibrator=reads["calibrator"].row,
        pregame_corpus=reads["pregame_corpus"].row,
        components=reads,
        missing_fields={
            component: missing_component_fields(component, reads[component].row)
            for component in COMPONENTS
        },
        read_blockers=tuple(dict.fromkeys(blockers)),
    )


def assess_registry_model_bundle(db: Any, scope: ModelBundleScope) -> dict[str, Any]:
    """Assess the triad from governed registries and return the merged verdict.

    The returned row is an evidence report, never a publication decision:
    ``probability_publishable``, ``rank_eligible``, ``automatic_certification``,
    ``automatic_promotion`` and ``can_execute`` are always false.
    """
    evidence = read_model_evidence_bundle(db, scope)
    assessed = assess_model_bundle(
        scope,
        artifact=evidence.artifact,
        calibrator=evidence.calibrator,
        pregame_corpus=evidence.pregame_corpus,
    )
    blockers = tuple(dict.fromkeys((*assessed.blockers, *evidence.read_blockers)))
    ready = assessed.bundle_complete and not evidence.read_blockers
    return {
        **assessed.as_dict(),
        "status": "READY_FOR_INDEPENDENT_REVIEW" if ready else "BLOCKED",
        "blockers": list(blockers),
        "bundle_complete": ready,
        "component_sources": evidence.component_status(),
        "missing_component_fields": {
            component: list(fields)
            for component, fields in evidence.missing_fields.items()
        },
        "probability_publishable": False,
        "rank_eligible": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
    }


__all__ = [
    "AMBIGUOUS",
    "CAN_EXECUTE",
    "FOUND",
    "MISSING",
    "NOT_CONFIGURED",
    "READ_FAILED",
    "READ_STATES",
    "TERMINAL_AUTHORITY",
    "VERIFICATION_TABLE",
    "ComponentRead",
    "LaneReader",
    "RegistryEvidence",
    "TableSpec",
    "assess_registry_model_bundle",
    "lane_configured",
    "lane_for",
    "read_model_evidence_bundle",
]
