"""Governed V17 historical-data contracts shared across prop specialists.

This module is deliberately infrastructure-only. It does not grant model capability,
register fitted artifacts, publish probabilities, or enable execution.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


class EvidenceDomain(str, Enum):
    SPORTING = "SPORTING"
    MARKET = "MARKET"


class SourceRightsState(str, Enum):
    V17_APPROVED = "V17_APPROVED"
    CONTRACT_REQUIRED = "CONTRACT_REQUIRED"
    LICENSE_REVIEW_REQUIRED = "LICENSE_REVIEW_REQUIRED"
    RESEARCH_ONLY = "RESEARCH_ONLY"
    VALIDATION_ONLY = "VALIDATION_ONLY"


class HistoricalDataContractError(ValueError):
    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def _require_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise HistoricalDataContractError(
            "HISTORICAL_TIMESTAMP_NAIVE", f"{field_name} must be timezone-aware"
        )
    return value


def canonical_json_bytes(payload: Any) -> bytes:
    """Stable JSON encoding used for immutable payload hashing."""
    try:
        text = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise HistoricalDataContractError(
            "HISTORICAL_PAYLOAD_NOT_CANONICALIZABLE", str(exc)
        ) from exc
    return text.encode("utf-8")


def payload_sha256(payload: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


@dataclass(frozen=True)
class CanonicalIdentity:
    sport: str
    event_id: str
    participant_id: str
    team_id: str
    opponent_id: str
    provider_ids: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        required = {
            "sport": self.sport,
            "event_id": self.event_id,
            "participant_id": self.participant_id,
            "team_id": self.team_id,
            "opponent_id": self.opponent_id,
        }
        missing = [name for name, value in required.items() if not str(value).strip()]
        if missing:
            raise HistoricalDataContractError(
                "HISTORICAL_IDENTITY_UNRESOLVED",
                f"missing canonical identity fields: {','.join(missing)}",
            )
        for provider, provider_id in self.provider_ids.items():
            if not str(provider).strip() or not str(provider_id).strip():
                raise HistoricalDataContractError(
                    "HISTORICAL_PROVIDER_IDENTITY_INVALID",
                    "provider identity mappings require non-empty provider and id",
                )


@dataclass(frozen=True)
class RawSourceSnapshot:
    sport: str
    provider: str
    source_record_id: str
    retrieved_at: datetime
    payload_hash: str
    evidence_domain: EvidenceDomain = EvidenceDomain.SPORTING
    can_execute: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        _require_aware(self.retrieved_at, "retrieved_at")
        for name, value in {
            "sport": self.sport,
            "provider": self.provider,
            "source_record_id": self.source_record_id,
            "payload_hash": self.payload_hash,
        }.items():
            if not str(value).strip():
                raise HistoricalDataContractError(
                    "HISTORICAL_SOURCE_SNAPSHOT_INVALID", f"{name} is required"
                )

    @classmethod
    def from_payload(
        cls,
        *,
        sport: str,
        provider: str,
        source_record_id: str,
        retrieved_at: datetime,
        payload: Any,
        evidence_domain: EvidenceDomain = EvidenceDomain.SPORTING,
    ) -> "RawSourceSnapshot":
        return cls(
            sport=sport,
            provider=provider,
            source_record_id=source_record_id,
            retrieved_at=retrieved_at,
            payload_hash=payload_sha256(payload),
            evidence_domain=evidence_domain,
        )


@dataclass(frozen=True)
class NormalizedPlayerGameOutcome:
    identity: CanonicalIdentity
    event_start_time: datetime
    outcome_as_of: datetime
    stat_type: str
    actual_value: float
    source_provider: str
    source_payload_hash: str
    can_execute: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        _require_aware(self.event_start_time, "event_start_time")
        _require_aware(self.outcome_as_of, "outcome_as_of")
        if self.outcome_as_of < self.event_start_time:
            raise HistoricalDataContractError(
                "HISTORICAL_OUTCOME_PREMATURE",
                "settled outcome timestamp cannot precede event start",
            )
        if not self.stat_type.strip():
            raise HistoricalDataContractError(
                "HISTORICAL_STAT_TYPE_MISSING", "stat_type is required"
            )


@dataclass(frozen=True)
class PointInTimeFeature:
    identity: CanonicalIdentity
    event_start_time: datetime
    feature_as_of: datetime
    feature_name: str
    value: float | int | str | bool | None
    source_provider: str
    source_payload_hash: str
    evidence_domain: EvidenceDomain = EvidenceDomain.SPORTING
    retrieved_at: datetime | None = None
    can_execute: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        _require_aware(self.event_start_time, "event_start_time")
        _require_aware(self.feature_as_of, "feature_as_of")
        if self.retrieved_at is not None:
            _require_aware(self.retrieved_at, "retrieved_at")
        if self.feature_as_of >= self.event_start_time:
            raise HistoricalDataContractError(
                "HISTORICAL_FEATURE_LEAKAGE",
                "feature_as_of must be strictly earlier than event_start_time",
            )
        if not self.feature_name.strip():
            raise HistoricalDataContractError(
                "HISTORICAL_FEATURE_NAME_MISSING", "feature_name is required"
            )


def build_sporting_feature_matrix(
    records: Iterable[PointInTimeFeature],
) -> list[dict[str, Any]]:
    """Normalize sporting features; market evidence is rejected fail-closed."""
    output: list[dict[str, Any]] = []
    for record in records:
        if record.evidence_domain is not EvidenceDomain.SPORTING:
            raise HistoricalDataContractError(
                "MARKET_EVIDENCE_NOT_ALLOWED_IN_SPORTING_MODEL",
                f"{record.feature_name} is typed {record.evidence_domain.value}",
            )
        output.append(
            {
                "sport": record.identity.sport,
                "event_id": record.identity.event_id,
                "participant_id": record.identity.participant_id,
                "feature_name": record.feature_name,
                "value": record.value,
                "feature_as_of": record.feature_as_of,
                "source_provider": record.source_provider,
                "source_payload_hash": record.source_payload_hash,
            }
        )
    return output


@dataclass(frozen=True)
class ChronologicalSplit:
    train: tuple[Any, ...]
    calibrate: tuple[Any, ...]
    test: tuple[Any, ...]


def chronological_train_calibrate_test(
    rows: Sequence[Any],
    *,
    timestamp_attr: str = "event_start_time",
    train_fraction: float = 0.60,
    calibration_fraction: float = 0.20,
) -> ChronologicalSplit:
    """Split by unique event timestamps so equal-time events never cross folds."""
    if not rows:
        raise HistoricalDataContractError(
            "HISTORICAL_SPLIT_EMPTY", "at least three time groups are required"
        )
    if not (0 < train_fraction < 1 and 0 < calibration_fraction < 1):
        raise HistoricalDataContractError(
            "HISTORICAL_SPLIT_INVALID", "fractions must be between zero and one"
        )
    if train_fraction + calibration_fraction >= 1:
        raise HistoricalDataContractError(
            "HISTORICAL_SPLIT_INVALID", "train + calibration fraction must be < 1"
        )

    decorated: list[tuple[datetime, Any]] = []
    for row in rows:
        timestamp = getattr(row, timestamp_attr, None)
        if not isinstance(timestamp, datetime):
            raise HistoricalDataContractError(
                "HISTORICAL_SPLIT_TIMESTAMP_MISSING", timestamp_attr
            )
        decorated.append((_require_aware(timestamp, timestamp_attr), row))
    decorated.sort(key=lambda item: item[0])

    timestamps = sorted({timestamp for timestamp, _ in decorated})
    if len(timestamps) < 3:
        raise HistoricalDataContractError(
            "HISTORICAL_SPLIT_INSUFFICIENT_TIME_GROUPS",
            "at least three unique event timestamps are required",
        )

    train_groups = max(1, int(len(timestamps) * train_fraction))
    calibration_groups = max(1, int(len(timestamps) * calibration_fraction))
    if train_groups + calibration_groups >= len(timestamps):
        calibration_groups = 1
        train_groups = len(timestamps) - 2

    train_end = timestamps[train_groups - 1]
    calibration_end = timestamps[train_groups + calibration_groups - 1]

    train = tuple(row for timestamp, row in decorated if timestamp <= train_end)
    calibrate = tuple(
        row
        for timestamp, row in decorated
        if train_end < timestamp <= calibration_end
    )
    test = tuple(row for timestamp, row in decorated if timestamp > calibration_end)

    if not train or not calibrate or not test:
        raise HistoricalDataContractError(
            "HISTORICAL_SPLIT_EMPTY_FOLD", "all chronological folds must be non-empty"
        )

    max_train = max(getattr(row, timestamp_attr) for row in train)
    min_calibrate = min(getattr(row, timestamp_attr) for row in calibrate)
    max_calibrate = max(getattr(row, timestamp_attr) for row in calibrate)
    min_test = min(getattr(row, timestamp_attr) for row in test)
    if not (max_train < min_calibrate and max_calibrate < min_test):
        raise HistoricalDataContractError(
            "HISTORICAL_SPLIT_TEMPORAL_OVERLAP",
            "train, calibration, and test boundaries must be strictly chronological",
        )
    return ChronologicalSplit(train=train, calibrate=calibrate, test=test)


@dataclass(frozen=True)
class SourceManifestEntry:
    sport: str
    provider: str
    evidence_domain: EvidenceDomain
    rights_state: SourceRightsState
    credential_required: bool
    coverage_start: str | None = None
    coverage_end: str | None = None
    notes: str = ""
    grants_model_capability: bool = False
    can_execute: bool = field(default=False, init=False)

    @property
    def production_training_eligible(self) -> bool:
        return (
            self.evidence_domain is EvidenceDomain.SPORTING
            and self.rights_state is SourceRightsState.V17_APPROVED
        )


def load_source_manifest(path: str | Path) -> tuple[SourceManifestEntry, ...]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if raw.get("schema_version") != "WOW_HISTORICAL_SOURCE_MANIFEST_V1":
        raise HistoricalDataContractError(
            "HISTORICAL_SOURCE_MANIFEST_VERSION_INVALID",
            "expected WOW_HISTORICAL_SOURCE_MANIFEST_V1",
        )
    entries: list[SourceManifestEntry] = []
    for item in raw.get("sources", []):
        entry = SourceManifestEntry(
            sport=item["sport"],
            provider=item["provider"],
            evidence_domain=EvidenceDomain(item["evidence_domain"]),
            rights_state=SourceRightsState(item["rights_state"]),
            credential_required=bool(item.get("credential_required", False)),
            coverage_start=item.get("coverage_start"),
            coverage_end=item.get("coverage_end"),
            notes=item.get("notes", ""),
            grants_model_capability=bool(item.get("grants_model_capability", False)),
        )
        if entry.grants_model_capability:
            raise HistoricalDataContractError(
                "SOURCE_MANIFEST_CANNOT_GRANT_MODEL_CAPABILITY",
                f"{entry.sport}/{entry.provider} attempted to grant model capability",
            )
        entries.append(entry)
    if not entries:
        raise HistoricalDataContractError(
            "HISTORICAL_SOURCE_MANIFEST_EMPTY", "source manifest cannot be empty"
        )
    return tuple(entries)
