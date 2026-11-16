"""Cross-lane regression suite and objective production-acceptance fixtures for independent SIRT use.

Covers V17 Triple D restoration requirements:
- NCAAF pregame replay clock authority and pre-inference cutoff
- Spread receipt admission invariants (exact line, 3-way distribution, interior bounds, can_execute=false)
- Model factory research-safety and source-review invariants
- 13-sport inventory capability truthfulness and explicit stage blockers
- Cross-lane governance invariants (terminal reducer authority, dry-run only)

Identity: SIRT_INDEPENDENT_ASSURANCE / GRAVITY_ACCEPTANCE_FIXTURES
can_execute = False (immutable)
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

# Setup paths
ENGINE_DIR = Path(__file__).resolve().parents[1]
V17_DIR = ENGINE_DIR / "v17"
sys.path.insert(0, str(ENGINE_DIR))
sys.path.insert(0, str(V17_DIR))

from v17.cross_sport_certification_inventory import (
    CERTIFICATION_SPORTS,
    SURFACES,
    CertificationAssessment,
    CertificationEvidence,
    assess,
    build_inventory,
)
from v17.team_event_capability_manifest import (
    CERTIFIED_TEAM_EVENT_SPORTS,
    EXPECTED_TEAM_EVENT_SPORTS,
    TEAM_EVENT_INPUT_CONTRACTS,
    normalize_team_event_sport,
)
from v17.team_event_model_registry_audit import (
    CERTIFIED,
    NOT_CERTIFIED,
    audit_table,
    probe_sport,
    resolve_state,
)
from v17.prop_capability_manifest import (
    PUBLICATION_ALLOWED_LANES,
    declared_prop_lane_manifest,
)
from ncaaf_fitted_provider import RawNCAAFWinProbability
from ncaaf_event_feature_handoff import (
    NCAAFEventFeatureHandoffUnavailable,
    score_current_event_snapshot,
)
from test_ncaaf_event_feature_handoff import (
    _stored_snapshot,
    _score_context,
    _Client,
    EVENT_ID,
    KICKOFF,
)


# ============================================================================
# 1. NCAAF PREGAME CLOCK & REPLAY TIME INVARIANTS
# ============================================================================

def test_sirt_ncaaf_replay_clock_honors_explicit_replay_instant():
    """SIRT Acceptance: Replay now instant must survive pre-inference check without clock."""
    snapshot = _stored_snapshot()
    client = _Client(snapshot)

    observed = []
    raw = RawNCAAFWinProbability(
        home_probability=0.62,
        away_probability=0.38,
        model_artifact_version="ncaaf-replay-v1",
        artifact_id="ncaaf-replay-v1",
    )
    calibrated = SimpleNamespace(
        calibrated_probability=0.60,
        calibration_lower_diagnostic=0.42,
        calibration_upper_diagnostic=0.76,
    )

    result = score_current_event_snapshot(
        client,
        official_event_id=EVENT_ID,
        event_start_time=KICKOFF.isoformat(),
        home_team="Louisville",
        away_team="Florida State",
        expected_source_manifest_sha256=snapshot["source_manifest"]["snapshot_sha256"],
        scorer=lambda *args, **kwargs: observed.append("scored") or raw,
        calibrator=lambda *args, **kwargs: calibrated,
        **_score_context(snapshot),
    )

    assert observed == ["scored"]
    assert result["status"] == "SCORED_HELD"
    assert result["can_execute"] is False
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False


def test_sirt_ncaaf_post_kickoff_clock_denies_scoring():
    """SIRT Acceptance: Authoritative clock advancing past kickoff must raise NCAAF_EVENT_NOT_PREGAME."""
    snapshot = _stored_snapshot()
    client = _Client(snapshot)

    # Clock indicates game has started
    post_kickoff_clock = lambda: KICKOFF + timedelta(minutes=5)

    with pytest.raises(NCAAFEventFeatureHandoffUnavailable) as exc_info:
        score_current_event_snapshot(
            client,
            official_event_id=EVENT_ID,
            event_start_time=KICKOFF.isoformat(),
            home_team="Louisville",
            away_team="Florida State",
            expected_source_manifest_sha256=snapshot["source_manifest"]["snapshot_sha256"],
            clock=post_kickoff_clock,
            scorer=lambda *args, **kwargs: None,
            calibrator=lambda *args, **kwargs: None,
            **_score_context(snapshot),
        )
    assert exc_info.value.code == "NCAAF_EVENT_NOT_PREGAME"


# ============================================================================
# 2. SPREAD RECEIPT ADMISSION & LINE DISTRIBUTION INVARIANTS
# ============================================================================
# These tests call the actually deployed card admission boundary. They do not
# pretend to exercise the as-yet-unmerged spread-receipt SQL relation (#2633).
from v17.spread_card_admission import spread_card_admission


def _spread_admission_receipt(**overrides):
    row = {
        "sport": "NFL",
        "event_id": "TEST_SPREAD_EVENT",
        "exact_line_side": "HOME",
        "exact_signed_spread": -3.5,
        "settlement_basis": "FULL_GAME_INCLUDING_OVERTIME",
        "controlling_spread_specialist": "TEST_ONLY_SPREAD_SPECIALIST",
        "model_artifact_version": "TEST_ONLY_ARTIFACT",
        "model_timestamp": "2026-11-16T17:00:00+00:00",
        "p_cover": 0.52,
        "p_push": 0.05,
        "p_not_cover": 0.43,
        "calibrated_lower_bound": 0.45,
        "lower_bound_required": True,
        "probability_publishable": True,
        "rank_eligible": True,
        "can_execute": False,
    }
    row.update(overrides)
    return row


def _check_spread_admission(**overrides):
    return spread_card_admission(
        _spread_admission_receipt(**overrides),
        requested_event_id="TEST_SPREAD_EVENT",
        requested_side="HOME",
        requested_signed_spread=-3.5,
    )


def test_sirt_spread_distribution_triplet_must_be_strictly_normalized():
    """A real admission call must reject a nonnormalized cover/push/noncover triplet."""
    valid = _check_spread_admission()
    assert valid["status"] == "PASS"
    assert valid["card_admission_eligible"] is True
    assert valid["can_execute"] is False

    invalid = _check_spread_admission(p_not_cover=0.40)
    assert invalid["status"] == "BLOCKED"
    assert invalid["card_admission_eligible"] is False
    assert "SPREAD_ADMISSION:PROBABILITY_DISTRIBUTION_NOT_NORMALIZED" in invalid["blockers"]


def test_sirt_spread_required_lower_bound_fails_closed_at_card_admission():
    """The existing real admission boundary rejects missing/nonfinite lower bounds.

    Full interior upper/lower coherence is a #2633 receipt-registry/SQL test,
    not a claim made by this independent standalone fixture.
    """
    for missing in (None, float("inf"), float("nan")):
        blocked = _check_spread_admission(calibrated_lower_bound=missing)
        assert blocked["card_admission_eligible"] is False
        assert "SPREAD_ADMISSION:LOWER_BOUND_REQUIRED" in blocked["blockers"]


def test_sirt_spread_can_execute_false_at_actual_card_admission_boundary():
    """Real card admission refuses an execution-enabled purported receipt."""
    blocked = _check_spread_admission(can_execute=True)
    assert blocked["status"] == "BLOCKED"
    assert blocked["can_execute"] is False
    assert "SPREAD_ADMISSION:CAN_EXECUTE_INVARIANT_VIOLATION" in blocked["blockers"]


# ============================================================================
# 3. 13-SPORT CAPABILITY INVENTORY TRUTHFULNESS
# ============================================================================

def test_sirt_all_13_sports_accounted_for_in_certification_inventory():
    """SIRT Acceptance: All 13 named sports must be accounted for across PROP and TEAM_EVENT."""
    expected_13 = {
        "NFL", "NBA", "NHL", "NCAAF", "NCAAB", "MLB", "WNBA",
        "SOCCER", "TENNIS", "PGA", "MMA", "BOXING", "CRICKET"
    }
    assert set(CERTIFICATION_SPORTS) == expected_13
    assert set(SURFACES) == {"PROP", "TEAM_EVENT"}

    inventory = build_inventory()
    # 13 sports * 2 surfaces = 26 assessments
    assert len(inventory) == 26

    # Every single row must have can_execute = False
    assert all(row["can_execute"] is False for row in inventory)


def test_sirt_no_unregistered_sport_claims_numerical_authority():
    """SIRT Acceptance: Only statically certified fitted sports may claim numerical authority."""
    audit_rows = audit_table()
    assert len(audit_rows) == 13

    for row in audit_rows:
        sport = row["sport"]
        cert = row["certification_status"]
        if sport == "MLB":
            assert cert == CERTIFIED
            assert row["fitted_artifact_backed"] is True
        else:
            assert cert != CERTIFIED, f"Sport {sport} must not be CERTIFIED without independent approval"
            assert row["probability_publishable"] is False
            assert row["can_execute"] is False


def test_sirt_declared_prop_lanes_truthfulness():
    """SIRT Acceptance: 73 declared prop lanes must preserve typed blockers; 0 uncertified lanes publish."""
    manifest = declared_prop_lane_manifest()
    assert manifest["can_execute"] is False
    assert manifest["declared_lane_count"] == 73
    assert manifest["publication_allowed_lane_count"] == 9  # 5 MLB + 4 NFL declared production
    assert set(manifest["sports_declared"]) == {
        "NFL", "NBA", "NHL", "NCAAF", "NCAAB", "MLB", "WNBA",
        "SOCCER", "TENNIS", "PGA", "MMA", "BOXING", "CRICKET"
    }

    # All build_required lanes must have explicit typed blocker
    for lane in manifest["lanes"]:
        if lane["lane_status"] == "BUILD_REQUIRED":
            assert lane["publication_allowed"] is False
            assert lane["blocker"] is not None
            assert "BUILD_REQUIRED" in lane["blocker"] or "CORPUS_REQUIRED" in lane["blocker"]


# ============================================================================
# 4. CROSS-LANE GOVERNANCE INVARIANTS
# ============================================================================

def test_sirt_terminal_reducer_authority_invariant():
    """SIRT Acceptance: V17_TERMINAL_REDUCER is the sole global terminal authority."""
    from v17.all_sport_capability_readiness import TERMINAL_AUTHORITY as READINESS_AUTH
    from v17.sirt_assurance import TERMINAL_AUTHORITY as SIRT_AUTH

    assert READINESS_AUTH == "V17_TERMINAL_REDUCER"
    assert SIRT_AUTH == "V17_TERMINAL_REDUCER"


def test_sirt_immutable_wager_execution_denial():
    """SIRT Acceptance: can_execute is universally False across all modules."""
    import v17.all_sport_capability_readiness as readiness
    import v17.cross_sport_certification_inventory as cert_inv
    import v17.team_event_capability_manifest as manifest
    import v17.team_event_model_registry_audit as audit
    import v17.prop_capability_manifest as prop_manifest
    import v17.sirt_assurance as sirt

    assert readiness.CAN_EXECUTE is False
    assert cert_inv.CAN_EXECUTE is False
    assert manifest.CAN_EXECUTE is False
    assert audit.CAN_EXECUTE is False
    assert prop_manifest.CAN_EXECUTE is False
    assert sirt.CAN_EXECUTE is False
