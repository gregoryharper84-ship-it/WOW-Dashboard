from __future__ import annotations

from v17.model_source_entitlements import SOURCES, assert_source_registry, source_readiness, sport_source_readiness


def test_source_registry_never_grants_probability_authority_or_execution():
    assert_source_registry()
    assert SOURCES
    for source in SOURCES.values():
        assert source.probability_source is False
        assert source.market_feature_allowed is False
        assert source.can_execute is False


def test_therundown_is_event_market_evidence_not_fitted_training(monkeypatch):
    monkeypatch.setenv("THERUNDOWN_API_KEY", "present")
    readiness = source_readiness("THERUNDOWN")
    assert readiness.ready_for_candidate_training is False
    assert "MODEL_SOURCE_NOT_AUTHORIZED_FOR_FITTED_TRAINING" in readiness.blockers


def test_sportsdataverse_espn_is_open_licensed_candidate_training_but_still_requires_certification_review():
    readiness = source_readiness("SPORTSDATAVERSE_ESPN")
    source = SOURCES["SPORTSDATAVERSE_ESPN"]
    assert readiness.ready_for_candidate_training is True
    assert readiness.blockers == ()
    assert source.use == "TRAINING_OPEN_LICENSED"
    assert source.license_id == "CC-BY-4.0"
    assert source.license_url
    assert source.attribution_required is True
    assert source.certification_source_review_required is True
    assert source.probability_source is False
    assert source.can_execute is False


def test_sportsdataverse_source_is_registered_for_basketball_candidate_lanes():
    for sport in ("NBA", "WNBA", "NCAAB"):
        readiness = sport_source_readiness(sport)
        assert "SPORTSDATAVERSE_ESPN" in readiness
        assert readiness["SPORTSDATAVERSE_ESPN"].ready_for_candidate_training is True


def test_openfootball_cc0_is_candidate_training_only():
    readiness = source_readiness("OPENFOOTBALL_CC0")
    source = SOURCES["OPENFOOTBALL_CC0"]
    assert readiness.ready_for_candidate_training is True
    assert source.use == "TRAINING_OPEN_LICENSED"
    assert source.license_id == "CC0-1.0"
    assert source.attribution_required is False
    assert source.certification_source_review_required is True
    assert source.probability_source is False
    assert source.can_execute is False


def test_valuebetennis_cc_by_4_is_candidate_training_only_and_requires_attribution():
    readiness = source_readiness("VALUEBETENNIS_CC_BY_4")
    source = SOURCES["VALUEBETENNIS_CC_BY_4"]
    assert readiness.ready_for_candidate_training is True
    assert source.use == "TRAINING_OPEN_LICENSED"
    assert source.license_id == "CC-BY-4.0"
    assert source.attribution_required is True
    assert source.certification_source_review_required is True
    assert source.probability_source is False
    assert source.can_execute is False


def test_nhl_first_party_public_source_can_support_candidate_training_but_not_certification_by_itself():
    readiness = source_readiness("NHL_PUBLIC_WEB_API")
    assert readiness.ready_for_candidate_training is True
    assert SOURCES["NHL_PUBLIC_WEB_API"].certification_source_review_required is True


def test_ufcstats_candidate_source_still_requires_source_review_before_certification():
    readiness = source_readiness("UFCSTATS_PUBLIC")
    assert readiness.ready_for_candidate_training is True
    assert SOURCES["UFCSTATS_PUBLIC"].certification_source_review_required is True
    assert readiness.can_execute is False


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


def test_ncaab_legacy_approved_stats_alias_remains_fail_closed():
    readiness = source_readiness("NCAAB_APPROVED_STATS")
    assert readiness.ready_for_candidate_training is False
    assert "MODEL_SOURCE_NOT_AUTHORIZED_FOR_FITTED_TRAINING" in readiness.blockers


def test_sport_source_readiness_keeps_event_market_sources_separate():
    nhl = sport_source_readiness("NHL")
    assert "THERUNDOWN" in nhl
    assert "NHL_PUBLIC_WEB_API" in nhl
    assert nhl["THERUNDOWN"].ready_for_candidate_training is False
    assert nhl["NHL_PUBLIC_WEB_API"].ready_for_candidate_training is True


def test_open_sources_are_visible_to_correct_sport_lanes():
    assert sport_source_readiness("SOCCER")["OPENFOOTBALL_CC0"].ready_for_candidate_training is True
    assert sport_source_readiness("TENNIS")["VALUEBETENNIS_CC_BY_4"].ready_for_candidate_training is True


def test_unclassified_source_fails_closed():
    readiness = source_readiness("some-new-feed")
    assert readiness.ready_for_candidate_training is False
    assert readiness.blockers == ("MODEL_SOURCE_UNCLASSIFIED",)
