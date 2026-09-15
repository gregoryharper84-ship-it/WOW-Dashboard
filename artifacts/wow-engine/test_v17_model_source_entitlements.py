from __future__ import annotations

import pytest

from v17.model_source_entitlements import SOURCES, assert_source_registry, source_readiness


def test_source_registry_never_grants_probability_authority_or_execution():
    assert_source_registry()
    assert SOURCES
    for source in SOURCES.values():
        assert source.probability_source is False
        assert source.market_feature_allowed is False
        assert source.can_execute is False


def test_therundown_is_event_market_evidence_not_fitted_training(monkeypatch):
    monkeypatch.setenv("RUNDOWN_API_KEY", "present")
    readiness = source_readiness("THERUNDOWN")
    assert readiness.ready_for_candidate_training is False
    assert "MODEL_SOURCE_NOT_AUTHORIZED_FOR_FITTED_TRAINING" in readiness.blockers


def test_nhl_first_party_public_source_can_support_candidate_training_but_not_certification_by_itself():
    readiness = source_readiness("NHL_PUBLIC_WEB_API")
    assert readiness.ready_for_candidate_training is True
    assert SOURCES["NHL_PUBLIC_WEB_API"].certification_source_review_required is True


def test_licensed_tennis_source_requires_both_credential_and_entitlement(monkeypatch):
    monkeypatch.delenv("LIVE_TENNIS_API_KEY", raising=False)
    monkeypatch.delenv("WOW_LIVE_TENNIS_COMMERCIAL_TRAINING_ENTITLED", raising=False)
    blocked = source_readiness("LIVE_TENNIS_API")
    assert blocked.ready_for_candidate_training is False
    assert "MODEL_SOURCE_CREDENTIAL_MISSING" in blocked.blockers
    assert "MODEL_SOURCE_TRAINING_ENTITLEMENT_NOT_CONFIRMED" in blocked.blockers

    monkeypatch.setenv("LIVE_TENNIS_API_KEY", "configured")
    monkeypatch.setenv("WOW_LIVE_TENNIS_COMMERCIAL_TRAINING_ENTITLED", "true")
    assert source_readiness("LIVE_TENNIS_API").ready_for_candidate_training is True


def test_research_tennis_archive_is_never_candidate_training_source():
    readiness = source_readiness("JEFF_SACKMANN_TENNIS_ARCHIVE")
    assert readiness.ready_for_candidate_training is False
    assert "MODEL_SOURCE_NOT_AUTHORIZED_FOR_FITTED_TRAINING" in readiness.blockers


def test_data_golf_requires_explicit_commercial_training_entitlement(monkeypatch):
    monkeypatch.setenv("DATA_GOLF_API_KEY", "configured")
    monkeypatch.delenv("WOW_DATA_GOLF_COMMERCIAL_TRAINING_ENTITLED", raising=False)
    assert source_readiness("DATA_GOLF").ready_for_candidate_training is False
    monkeypatch.setenv("WOW_DATA_GOLF_COMMERCIAL_TRAINING_ENTITLED", "approved")
    assert source_readiness("DATA_GOLF").ready_for_candidate_training is True


def test_missing_approved_ncaab_and_mma_sources_fail_closed():
    assert source_readiness("NCAAB_APPROVED_STATS").ready_for_candidate_training is False
    assert source_readiness("MMA_APPROVED_STATS").ready_for_candidate_training is False


def test_unclassified_source_fails_closed():
    readiness = source_readiness("some-new-feed")
    assert readiness.ready_for_candidate_training is False
    assert readiness.blockers == ("MODEL_SOURCE_UNCLASSIFIED",)
