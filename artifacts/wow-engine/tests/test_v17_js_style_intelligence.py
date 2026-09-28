from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import pytest

from v17.js_style import CAN_EXECUTE, JS_PROBABILITY_AUTHORITY, JS_STYLE_FEATURE_SCHEMA_V1
from v17.js_style.archetypes import ArchetypeEvidence, classify_archetypes
from v17.js_style.burden import compute_threshold_burden
from v17.js_style.contracts import (
    BoardSnapshot,
    CandidateObservation,
    EVIDENCE_COMPLETE,
    EVIDENCE_INCOMPLETE,
    RESEARCH_COMPONENT_MAXIMA,
    ThresholdEvidence,
)
from v17.js_style.persistence import (
    board_snapshot_record,
    candidate_observation_record,
    make_board_snapshot_id,
    selection_event_record,
)
from v17.js_style.replay import build_selection_style_rows
from v17.js_style.research_priority import compute_research_priority
from v17.js_style.thesis_cluster import ThesisEvidence, classify_thesis


def _threshold(direction: str) -> ThresholdEvidence:
    return ThresholdEvidence(
        exact_settlement_threshold=17.5,
        direction=direction,
        period="FULL_GAME",
        comparable_values=(10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 18.0, 19.0),
        role_adjusted_median=14.0,
        source_provenance="IMMUTABLE_PREGAME_SNAPSHOT",
        as_of="2026-09-27T18:00:00Z",
        cohort_name="ROLE_MATCHED",
    )


def _observation(*, selected: bool, name: str) -> CandidateObservation:
    return CandidateObservation(
        board_snapshot_id="snapshot",
        provider_event_alias="provider-1",
        canonical_event_id="canonical-1",
        participant_id=name.lower(),
        participant_name=name,
        sport="NBA",
        stat="PRA",
        period="FULL_GAME",
        exact_settlement_threshold=31.5,
        direction="LESS",
        promo_type=None,
        settlement_rule_version="RULE_V1",
        archetypes=("JS_CEILING_LESS",),
        js_research_priority=72.0,
        controlling_specialist_identity="wow.existing-specialist",
        governed_scoring_status="SCORED",
        governed_typed_blocker=None,
        selected_by_js=selected,
        eventual_settlement="HIT" if selected else "MISS",
    )


def test_threshold_burden_is_descriptive_and_direction_oriented():
    less = compute_threshold_burden(_threshold("LESS"))
    more = compute_threshold_burden(_threshold("MORE"))
    assert less.evidence_status == EVIDENCE_COMPLETE
    assert less.threshold_burden_robust is not None
    assert less.threshold_burden_robust > 0
    assert more.threshold_burden_robust == pytest.approx(-less.threshold_burden_robust)
    assert 0.0 <= less.threshold_distribution_position <= 1.0
    assert less.js_probability_authority is False
    assert less.probability_publishable is False
    assert less.rank_eligible is False
    assert less.can_execute is False


def test_missing_threshold_evidence_fails_explicitly_without_defaults():
    result = compute_threshold_burden(
        ThresholdEvidence(
            exact_settlement_threshold=None,
            direction="LESS",
            period=None,
            comparable_values=(),
            role_adjusted_median=None,
        )
    )
    assert result.evidence_status == EVIDENCE_INCOMPLETE
    assert result.threshold_burden_robust is None
    assert result.threshold_distribution_position is None
    assert "exact_settlement_threshold" in result.missing_evidence
    assert "period" in result.missing_evidence
    assert "comparable_sample" in result.missing_evidence


def test_less_direction_alone_creates_no_archetype_or_priority_points():
    classified = classify_archetypes(ArchetypeEvidence(direction="LESS", evidence_complete=True))
    assert classified.evidence_status == EVIDENCE_COMPLETE
    assert classified.archetypes == ()

    zero_components = {key: 0.0 for key in RESEARCH_COMPONENT_MAXIMA}
    priority = compute_research_priority(zero_components)
    assert priority.js_research_priority == 0.0


def test_research_priority_honors_component_budget_and_visible_penalties():
    full = {key: maximum for key, maximum in RESEARCH_COMPONENT_MAXIMA.items()}
    result = compute_research_priority(
        full,
        penalties={"uncertain_role_minutes": 7.0, "contradictory_evidence": 11.0},
    )
    assert sum(result.components.values()) == 100.0
    assert result.js_research_priority == 82.0
    assert result.penalties == {"uncertain_role_minutes": 7.0, "contradictory_evidence": 11.0}
    assert result.js_probability_authority is False
    assert result.rank_eligible is False
    assert result.probability_publishable is False


def test_thesis_cluster_never_multiplies_marginal_probabilities():
    result = classify_thesis(
        ThesisEvidence(
            cluster_thesis_id="game-1-suppression",
            cluster_thesis_version="V1",
            cluster_direction="LESS",
            shared_driver="LOW_PACE",
            joint_benefit_paths=("fewer_possessions", "lower_counting_volume"),
            joint_failure_paths=(),
            dependence_type="SHARED_GAME_ENVIRONMENT",
            dependence_evidence_complete=True,
        )
    )
    assert result.classification == "THESIS_COHERENT"
    assert result.joint_probability_computed is False
    assert result.js_probability_authority is False
    assert result.probability_publishable is False
    assert result.can_execute is False


def test_learning_ledger_ids_are_deterministic_and_research_only():
    snapshot_id = make_board_snapshot_id(
        provider="PRIZEPICKS",
        captured_at="2026-09-27T18:00:00Z",
        source_snapshot_digest="abc123",
        feature_schema_version=JS_STYLE_FEATURE_SCHEMA_V1,
    )
    snapshot = BoardSnapshot(
        board_snapshot_id=snapshot_id,
        provider="PRIZEPICKS",
        captured_at="2026-09-27T18:00:00Z",
        as_of="2026-09-27T18:00:00Z",
        source_snapshot_digest="abc123",
    )
    first = board_snapshot_record(snapshot)
    second = board_snapshot_record(snapshot)
    assert first == second
    assert first["js_probability_authority"] is False
    assert first["can_execute"] is False

    observation = _observation(selected=True, name="Player A")
    obs_record = candidate_observation_record(observation)
    assert obs_record["probability_publishable"] is False
    assert obs_record["rank_eligible"] is False
    assert obs_record["can_execute"] is False
    event = selection_event_record(
        observation_id=obs_record["observation_id"],
        selected_by_js=True,
        selected_at="2026-09-27T18:01:00Z",
        selection_source="HISTORICAL_SCREENSHOT",
    )
    assert event["js_probability_authority"] is False
    assert event["can_execute"] is False


def test_selection_imitation_replay_excludes_outcome_and_requires_negative_examples():
    with pytest.raises(ValueError, match="JS_IMITATION_CLASS_SUPPORT_INSUFFICIENT"):
        build_selection_style_rows([_observation(selected=True, name="Only Positive")])

    rows = build_selection_style_rows(
        [
            _observation(selected=True, name="Selected"),
            _observation(selected=False, name="Rejected"),
        ]
    )
    assert {row["target_selected_by_js"] for row in rows} == {True, False}
    for row in rows:
        assert "eventual_settlement" not in row
        assert "governed_scoring_status" not in row
        assert "governed_typed_blocker" not in row
        assert row["research_only"] is True
        assert row["js_probability_authority"] is False
        assert row["probability_publishable"] is False
        assert row["rank_eligible"] is False
        assert row["can_execute"] is False


def test_package_exposes_no_authority_and_sql_locks_false_flags():
    assert JS_PROBABILITY_AUTHORITY is False
    assert CAN_EXECUTE is False
    sql = Path("v17/sql/20260928_js_style_learning_ledger.sql").read_text()
    lowered = sql.lower()
    assert "enable row level security" in lowered
    assert "from anon, authenticated" in lowered
    assert "to service_role" in lowered
    assert "check (js_probability_authority = false)" in lowered
    assert "check (probability_publishable = false)" in lowered
    assert "check (rank_eligible = false)" in lowered
    assert "check (can_execute = false)" in lowered


def test_contract_serialization_has_no_js_probability_aliases():
    payload = asdict(_observation(selected=True, name="Player A"))
    forbidden = {"js_probability", "js_hit_probability", "js_win_probability", "js_lower_bound"}
    assert forbidden.isdisjoint(payload)
    assert payload["js_probability_authority"] is False
