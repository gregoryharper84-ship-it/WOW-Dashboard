from __future__ import annotations

from v17.cross_sport_certification_inventory import (
    CERTIFICATION_SPORTS,
    SURFACES,
    CertificationEvidence,
    assess,
    build_inventory,
)


def test_inventory_never_silently_omits_required_sports_or_surfaces():
    rows = build_inventory()
    assert len(rows) == len(CERTIFICATION_SPORTS) * len(SURFACES)
    assert {row["sport"] for row in rows} == set(CERTIFICATION_SPORTS)
    assert {row["surface"] for row in rows} == set(SURFACES)
    assert all(row["can_execute"] is False for row in rows)


def test_skill_or_route_presence_alone_cannot_create_numerical_authority():
    result = assess(CertificationEvidence(
        sport="TENNIS",
        surface="TEAM_EVENT",
        controlling_specialist_ready=True,
        model_build_exists=True,
        data_current=True,
        source_provenance_ready=True,
    ))
    assert result.status == "MODEL_BUILD_REQUIRED"
    assert result.numerical_authority is False
    assert "FITTED_MODEL_ARTIFACT_REQUIRED" in result.blockers


def test_candidate_with_replay_and_calibrator_still_requires_explicit_promotion():
    result = assess(CertificationEvidence(
        sport="WNBA",
        surface="PROP",
        controlling_specialist_ready=True,
        fitted_model_present=True,
        candidate_ready=True,
        deterministic_replay_ready=True,
        calibrator_ready=True,
        data_current=True,
        source_provenance_ready=True,
    ))
    assert result.status == "READY_FOR_LIFECYCLE_REVIEW"
    assert result.numerical_authority is False
    assert result.blockers == ("EXACT_CERTIFIED_ARTIFACT_NOT_PROMOTED",)


def test_exact_certified_artifact_plus_calibrator_grants_route_scoped_authority_only():
    result = assess(CertificationEvidence(
        sport="NFL",
        surface="TEAM_EVENT",
        controlling_specialist_ready=True,
        fitted_model_present=True,
        exact_certified_artifact_ready=True,
        calibrator_ready=True,
        data_current=True,
        source_provenance_ready=True,
    ))
    assert result.status == "CERTIFIED_ROUTE"
    assert result.numerical_authority is True
    assert result.blockers == ()
    assert result.can_execute is False


def test_stale_corpus_blocks_certification_even_when_model_code_exists():
    result = assess(CertificationEvidence(
        sport="NBA",
        surface="TEAM_EVENT",
        controlling_specialist_ready=True,
        fitted_model_present=False,
        calibrator_ready=False,
        model_build_exists=True,
        data_current=False,
        source_provenance_ready=True,
    ))
    assert result.status == "DATA_REFRESH_REQUIRED"
    assert result.numerical_authority is False
    assert "CURRENT_TRAINING_CORPUS_REQUIRED" in result.blockers


def test_execution_authority_is_never_accepted_as_certification_evidence():
    try:
        assess(CertificationEvidence(
            sport="MLB",
            surface="PROP",
            controlling_specialist_ready=True,
            fitted_model_present=True,
            exact_certified_artifact_ready=True,
            calibrator_ready=True,
            data_current=True,
            source_provenance_ready=True,
            can_execute=True,
        ))
    except ValueError as exc:
        assert "cannot grant execution authority" in str(exc)
    else:
        raise AssertionError("can_execute=true must fail closed")
