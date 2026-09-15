from __future__ import annotations

import pytest

from v17.legacy_probability_quarantine import (
    LEGACY_COMPONENTS,
    assert_legacy_probability_quarantine,
    legacy_component_disposition,
)


def test_all_legacy_probability_components_are_non_authoritative():
    assert LEGACY_COMPONENTS
    assert_legacy_probability_quarantine()
    for row in LEGACY_COMPONENTS:
        assert row.probability_reuse_allowed is False
        assert row.certification_reuse_allowed is False
        assert row.rank_reuse_allowed is False
        assert row.can_execute is False


def test_legacy_acquisition_may_be_reused_only_as_evidence():
    row = legacy_component_disposition("gate_engine.moneyline.team_acquisition")
    assert row.evidence_reuse_allowed is True
    assert row.probability_reuse_allowed is False
    assert row.disposition == "EVIDENCE_ONLY_REVALIDATE_PROVENANCE_AND_TIMESTAMP"


def test_legacy_sport_model_is_research_baseline_not_v17_model():
    row = legacy_component_disposition("gate_engine.moneyline.sport_model")
    assert row.evidence_reuse_allowed is False
    assert row.disposition == "RESEARCH_BASELINE_ONLY_NOT_GOVERNED_MODEL"


def test_unknown_legacy_component_cannot_be_silently_reused():
    with pytest.raises(KeyError, match="V17_LEGACY_COMPONENT_UNCLASSIFIED"):
        legacy_component_disposition("gate_engine.some_future_probability")
