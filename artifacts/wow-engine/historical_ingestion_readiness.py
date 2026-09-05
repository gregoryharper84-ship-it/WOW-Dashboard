"""Typed readiness classification for V17 historical-data ingestion.

This module reports source/legal/credential/adapter/corpus readiness only. It never
returns a sporting probability, never grants model capability, and preserves
``can_execute=false``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from historical_data_backbone import (
    EvidenceDomain,
    HistoricalDataContractError,
    SourceManifestEntry,
    SourceRightsState,
    load_source_manifest,
)


READY_FOR_OFFLINE_TRAINING = "READY_FOR_OFFLINE_TRAINING"
HISTORICAL_SOURCE_UNREGISTERED = "HISTORICAL_SOURCE_UNREGISTERED"
MARKET_EVIDENCE_NOT_ALLOWED_IN_SPORTING_MODEL = (
    "MARKET_EVIDENCE_NOT_ALLOWED_IN_SPORTING_MODEL"
)
HISTORICAL_SOURCE_RESEARCH_ONLY = "HISTORICAL_SOURCE_RESEARCH_ONLY"
HISTORICAL_SOURCE_VALIDATION_ONLY = "HISTORICAL_SOURCE_VALIDATION_ONLY"
HISTORICAL_SOURCE_CONTRACT_REQUIRED = "HISTORICAL_SOURCE_CONTRACT_REQUIRED"
HISTORICAL_SOURCE_LICENSE_REVIEW_REQUIRED = (
    "HISTORICAL_SOURCE_LICENSE_REVIEW_REQUIRED"
)
HISTORICAL_SOURCE_CREDENTIAL_MISSING = "HISTORICAL_SOURCE_CREDENTIAL_MISSING"
HISTORICAL_ADAPTER_UNAVAILABLE = "HISTORICAL_ADAPTER_UNAVAILABLE"
HISTORICAL_CORPUS_NOT_HYDRATED = "HISTORICAL_CORPUS_NOT_HYDRATED"


@dataclass(frozen=True)
class HistoricalIngestionReadiness:
    sport: str
    provider: str
    adapter_available: bool
    credential_configured: bool
    corpus_row_count: int
    production_training_ready: bool
    status_code: str
    blocker_detail: str | None = None
    grants_model_capability: bool = field(default=False, init=False)
    can_execute: bool = field(default=False, init=False)

    @property
    def blocker_code(self) -> str | None:
        return None if self.production_training_ready else self.status_code


def default_manifest_path() -> Path:
    return Path(__file__).with_name("historical_source_manifest_v1.json")


def _find_entry(
    *, sport: str, provider: str, entries: Sequence[SourceManifestEntry]
) -> SourceManifestEntry | None:
    sport_key = sport.strip().upper()
    provider_key = provider.strip().upper()
    matches = [
        entry
        for entry in entries
        if entry.sport.strip().upper() == sport_key
        and entry.provider.strip().upper() == provider_key
    ]
    if len(matches) > 1:
        raise HistoricalDataContractError(
            "HISTORICAL_SOURCE_MANIFEST_AMBIGUOUS",
            f"multiple manifest rows for {sport}/{provider}",
        )
    return matches[0] if matches else None


def _result(
    *,
    sport: str,
    provider: str,
    adapter_available: bool,
    credential_configured: bool,
    corpus_row_count: int,
    status_code: str,
    detail: str | None,
    ready: bool = False,
) -> HistoricalIngestionReadiness:
    return HistoricalIngestionReadiness(
        sport=sport,
        provider=provider,
        adapter_available=adapter_available,
        credential_configured=credential_configured,
        corpus_row_count=corpus_row_count,
        production_training_ready=ready,
        status_code=status_code,
        blocker_detail=detail,
    )


def evaluate_historical_ingestion_readiness(
    *,
    sport: str,
    provider: str,
    adapter_available: bool,
    credential_configured: bool,
    corpus_row_count: int,
    manifest_entries: Sequence[SourceManifestEntry] | None = None,
) -> HistoricalIngestionReadiness:
    """Classify historical-ingestion readiness in deterministic fail-closed order.

    Credential state is supplied by the caller. This module intentionally does not
    inspect environment variables or secret stores.
    """
    if corpus_row_count < 0:
        raise HistoricalDataContractError(
            "HISTORICAL_CORPUS_ROW_COUNT_INVALID",
            "corpus_row_count cannot be negative",
        )
    if not sport.strip() or not provider.strip():
        raise HistoricalDataContractError(
            "HISTORICAL_SOURCE_IDENTITY_INVALID",
            "sport and provider are required",
        )

    entries = tuple(manifest_entries or load_source_manifest(default_manifest_path()))
    entry = _find_entry(sport=sport, provider=provider, entries=entries)
    if entry is None:
        return _result(
            sport=sport,
            provider=provider,
            adapter_available=adapter_available,
            credential_configured=credential_configured,
            corpus_row_count=corpus_row_count,
            status_code=HISTORICAL_SOURCE_UNREGISTERED,
            detail="provider is not registered in the governed historical source manifest",
        )

    if entry.evidence_domain is EvidenceDomain.MARKET:
        return _result(
            sport=sport,
            provider=provider,
            adapter_available=adapter_available,
            credential_configured=credential_configured,
            corpus_row_count=corpus_row_count,
            status_code=MARKET_EVIDENCE_NOT_ALLOWED_IN_SPORTING_MODEL,
            detail="market evidence is a separate contract from sporting model training",
        )

    rights_blockers = {
        SourceRightsState.RESEARCH_ONLY: (
            HISTORICAL_SOURCE_RESEARCH_ONLY,
            "source is approved for research/prototyping only",
        ),
        SourceRightsState.VALIDATION_ONLY: (
            HISTORICAL_SOURCE_VALIDATION_ONLY,
            "source is approved for validation only, not production training",
        ),
        SourceRightsState.CONTRACT_REQUIRED: (
            HISTORICAL_SOURCE_CONTRACT_REQUIRED,
            "production use requires a separately ratified provider contract/license",
        ),
        SourceRightsState.LICENSE_REVIEW_REQUIRED: (
            HISTORICAL_SOURCE_LICENSE_REVIEW_REQUIRED,
            "production-use/license review must complete before training",
        ),
    }
    if entry.rights_state in rights_blockers:
        code, detail = rights_blockers[entry.rights_state]
        return _result(
            sport=sport,
            provider=provider,
            adapter_available=adapter_available,
            credential_configured=credential_configured,
            corpus_row_count=corpus_row_count,
            status_code=code,
            detail=detail,
        )

    if entry.credential_required and not credential_configured:
        return _result(
            sport=sport,
            provider=provider,
            adapter_available=adapter_available,
            credential_configured=credential_configured,
            corpus_row_count=corpus_row_count,
            status_code=HISTORICAL_SOURCE_CREDENTIAL_MISSING,
            detail="approved source requires a configured credential",
        )

    if not adapter_available:
        return _result(
            sport=sport,
            provider=provider,
            adapter_available=adapter_available,
            credential_configured=credential_configured,
            corpus_row_count=corpus_row_count,
            status_code=HISTORICAL_ADAPTER_UNAVAILABLE,
            detail="governed historical adapter is not available for this source",
        )

    if corpus_row_count == 0:
        return _result(
            sport=sport,
            provider=provider,
            adapter_available=adapter_available,
            credential_configured=credential_configured,
            corpus_row_count=corpus_row_count,
            status_code=HISTORICAL_CORPUS_NOT_HYDRATED,
            detail="source is approved/configured but no historical corpus rows are hydrated",
        )

    return _result(
        sport=sport,
        provider=provider,
        adapter_available=adapter_available,
        credential_configured=credential_configured,
        corpus_row_count=corpus_row_count,
        status_code=READY_FOR_OFFLINE_TRAINING,
        detail=None,
        ready=True,
    )
