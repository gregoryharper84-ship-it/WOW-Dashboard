from __future__ import annotations

from dataclasses import replace

import pytest

from historical_data_backbone import (
    EvidenceDomain,
    HistoricalDataContractError,
    SourceManifestEntry,
    SourceRightsState,
    load_source_manifest,
)
from historical_ingestion_readiness import (
    HISTORICAL_ADAPTER_UNAVAILABLE,
    HISTORICAL_CORPUS_NOT_HYDRATED,
    HISTORICAL_SOURCE_CONTRACT_REQUIRED,
    HISTORICAL_SOURCE_CREDENTIAL_MISSING,
    HISTORICAL_SOURCE_LICENSE_REVIEW_REQUIRED,
    HISTORICAL_SOURCE_RESEARCH_ONLY,
    HISTORICAL_SOURCE_UNREGISTERED,
    MARKET_EVIDENCE_NOT_ALLOWED_IN_SPORTING_MODEL,
    READY_FOR_OFFLINE_TRAINING,
    default_manifest_path,
    evaluate_historical_ingestion_readiness,
)


def _entries():
    return load_source_manifest(default_manifest_path())


def _evaluate(sport: str, provider: str, **overrides):
    params = {
        "sport": sport,
        "provider": provider,
        "adapter_available": True,
        "credential_configured": True,
        "corpus_row_count": 100,
        "manifest_entries": _entries(),
    }
    params.update(overrides)
    return evaluate_historical_ingestion_readiness(**params)


def test_unknown_provider_is_typed_unregistered() -> None:
    result = _evaluate("WNBA", "NOT_A_PROVIDER")
    assert result.status_code == HISTORICAL_SOURCE_UNREGISTERED
    assert result.production_training_ready is False
    assert result.blocker_code == HISTORICAL_SOURCE_UNREGISTERED


def test_market_provider_cannot_enter_sporting_training() -> None:
    result = _evaluate("MULTISPORT", "SPORTSDATAIO_VAULT")
    assert result.status_code == MARKET_EVIDENCE_NOT_ALLOWED_IN_SPORTING_MODEL
    assert result.production_training_ready is False


def test_research_only_provider_is_blocked_before_technical_readiness() -> None:
    result = _evaluate(
        "TENNIS",
        "SACKMANN_TENNIS",
        adapter_available=True,
        credential_configured=True,
        corpus_row_count=999999,
    )
    assert result.status_code == HISTORICAL_SOURCE_RESEARCH_ONLY


def test_retired_wnba_derived_alias_is_unregistered() -> None:
    """The vague validation-only alias was replaced by an explicit governed source."""
    result = _evaluate("WNBA", "WNBA_EXISTING_STATS_DERIVED")
    assert result.status_code == HISTORICAL_SOURCE_UNREGISTERED
    assert result.production_training_ready is False
    assert result.grants_model_capability is False
    assert result.can_execute is False


def test_governed_sportsdataverse_wnba_source_is_ready_for_offline_training_only() -> None:
    result = _evaluate(
        "WNBA",
        "SPORTSDATAVERSE_WNBA_STATS",
        credential_configured=False,
        adapter_available=True,
        corpus_row_count=4000,
    )
    assert result.status_code == READY_FOR_OFFLINE_TRAINING
    assert result.production_training_ready is True
    assert result.blocker_code is None
    assert result.grants_model_capability is False
    assert result.can_execute is False


def test_contract_required_is_not_bypassed_by_hypothetical_key() -> None:
    result = _evaluate(
        "WNBA",
        "SPORTRADAR_WNBA",
        credential_configured=True,
        adapter_available=True,
        corpus_row_count=5000,
    )
    assert result.status_code == HISTORICAL_SOURCE_CONTRACT_REQUIRED


def test_license_review_required_is_typed() -> None:
    result = _evaluate("NFL", "NFLVERSE")
    assert result.status_code == HISTORICAL_SOURCE_LICENSE_REVIEW_REQUIRED


def test_approved_credentialed_source_requires_key() -> None:
    result = _evaluate(
        "NCAAF",
        "CFBD",
        credential_configured=False,
        adapter_available=True,
        corpus_row_count=0,
    )
    assert result.status_code == HISTORICAL_SOURCE_CREDENTIAL_MISSING


def test_adapter_blocker_is_after_rights_and_credentials() -> None:
    result = _evaluate(
        "NCAAF",
        "CFBD",
        credential_configured=True,
        adapter_available=False,
        corpus_row_count=0,
    )
    assert result.status_code == HISTORICAL_ADAPTER_UNAVAILABLE


def test_empty_corpus_is_typed_after_source_and_adapter_are_ready() -> None:
    result = _evaluate(
        "NCAAF",
        "CFBD",
        credential_configured=True,
        adapter_available=True,
        corpus_row_count=0,
    )
    assert result.status_code == HISTORICAL_CORPUS_NOT_HYDRATED


def test_ready_means_offline_training_ready_not_model_capable() -> None:
    result = _evaluate(
        "NCAAF",
        "CFBD",
        credential_configured=True,
        adapter_available=True,
        corpus_row_count=25000,
    )
    assert result.status_code == READY_FOR_OFFLINE_TRAINING
    assert result.production_training_ready is True
    assert result.blocker_code is None
    assert result.grants_model_capability is False
    assert result.can_execute is False


def test_mlb_approved_no_credential_source_can_be_ready() -> None:
    result = _evaluate(
        "MLB",
        "MLB_STATS_API",
        credential_configured=False,
        adapter_available=True,
        corpus_row_count=1000,
    )
    assert result.status_code == READY_FOR_OFFLINE_TRAINING


def test_negative_corpus_count_is_invalid() -> None:
    with pytest.raises(HistoricalDataContractError) as exc:
        _evaluate("NCAAF", "CFBD", corpus_row_count=-1)
    assert exc.value.code == "HISTORICAL_CORPUS_ROW_COUNT_INVALID"


def test_empty_source_identity_is_invalid() -> None:
    with pytest.raises(HistoricalDataContractError) as exc:
        _evaluate("", "CFBD")
    assert exc.value.code == "HISTORICAL_SOURCE_IDENTITY_INVALID"


def test_ambiguous_manifest_rows_fail_closed() -> None:
    entries = list(_entries())
    cfbd = next(entry for entry in entries if entry.sport == "NCAAF" and entry.provider == "CFBD")
    entries.append(replace(cfbd))
    with pytest.raises(HistoricalDataContractError) as exc:
        evaluate_historical_ingestion_readiness(
            sport="NCAAF",
            provider="CFBD",
            adapter_available=True,
            credential_configured=True,
            corpus_row_count=100,
            manifest_entries=entries,
        )
    assert exc.value.code == "HISTORICAL_SOURCE_MANIFEST_AMBIGUOUS"


def test_custom_approved_market_row_still_fails_market_domain_first() -> None:
    market = SourceManifestEntry(
        sport="TEST",
        provider="MARKET_TEST",
        evidence_domain=EvidenceDomain.MARKET,
        rights_state=SourceRightsState.V17_APPROVED,
        credential_required=False,
    )
    result = evaluate_historical_ingestion_readiness(
        sport="TEST",
        provider="MARKET_TEST",
        adapter_available=True,
        credential_configured=True,
        corpus_row_count=100,
        manifest_entries=[market],
    )
    assert result.status_code == MARKET_EVIDENCE_NOT_ALLOWED_IN_SPORTING_MODEL
