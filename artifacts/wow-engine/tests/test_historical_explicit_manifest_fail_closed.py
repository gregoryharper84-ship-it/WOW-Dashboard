"""Regression: explicit empty source grants cannot fall back to a global registry.

A caller narrowing authorization to an empty manifest must not accidentally
inherit default source rights, credentials or training readiness.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from historical_data_backbone import (
    EvidenceDomain,
    HistoricalDataContractError,
    SourceManifestEntry,
    SourceRightsState,
)

from historical_ingestion_readiness import (
    HISTORICAL_SOURCE_UNREGISTERED,
    READY_FOR_OFFLINE_TRAINING,
    evaluate_historical_ingestion_readiness,
)


@pytest.mark.parametrize("manifest_entries", [[], ()])
@pytest.mark.parametrize("sport,provider", [
    ("WNBA", "SPORTSDATAVERSE_WNBA_STATS"),
    ("MLB", "MLB_STATS_API"),
    ("NCAAF", "CFBD"),
])
def test_explicit_empty_source_manifest_never_inherits_default_rights(
    sport, provider, manifest_entries,
) -> None:
    outcome = evaluate_historical_ingestion_readiness(
        sport=sport,
        provider=provider,
        adapter_available=True,
        credential_configured=True,
        corpus_row_count=100,
        manifest_entries=manifest_entries,
    )
    assert outcome.status_code == HISTORICAL_SOURCE_UNREGISTERED
    assert outcome.production_training_ready is False
    assert outcome.grants_model_capability is False
    assert outcome.can_execute is False


def test_omitted_manifest_still_loads_default_catalog() -> None:
    outcome = evaluate_historical_ingestion_readiness(
        sport="WNBA",
        provider="SPORTSDATAVERSE_WNBA_STATS",
        adapter_available=True,
        credential_configured=True,
        corpus_row_count=100,
    )
    assert outcome.status_code == READY_FOR_OFFLINE_TRAINING
    assert outcome.grants_model_capability is False
    assert outcome.can_execute is False


@pytest.mark.parametrize("domain,rights,grants_capability", [
    ("MARKET", SourceRightsState.V17_APPROVED, False),
    ("SPORTING", SourceRightsState.V17_APPROVED, False),
    (EvidenceDomain.SPORTING, "UNRATIFIED", False),
    (EvidenceDomain.SPORTING, "V17_APPROVED", False),
    (EvidenceDomain.SPORTING, SourceRightsState.V17_APPROVED, True),
])
def test_untyped_explicit_source_manifest_can_never_grant_offline_readiness(
    domain, rights, grants_capability,
) -> None:
    """Runtime-invalid or capability-granting manifest entries fail closed."""
    entry = SourceManifestEntry(
        sport="TEST", provider="UNTRUSTED_EXPLICIT_SOURCE",
        evidence_domain=domain, rights_state=rights,
        credential_required=False,
        grants_model_capability=grants_capability,
    )
    with pytest.raises(HistoricalDataContractError) as exc:
        evaluate_historical_ingestion_readiness(
            sport="TEST", provider="UNTRUSTED_EXPLICIT_SOURCE",
            adapter_available=True, credential_configured=True,
            corpus_row_count=100, manifest_entries=[entry],
        )
    assert exc.value.code == "HISTORICAL_SOURCE_MANIFEST_ENTRY_INVALID"
