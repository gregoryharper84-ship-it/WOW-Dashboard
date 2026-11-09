"""Independent replay of the 2026-11-09 pregame-availability incident.

No stored payload for run ``v17-daily-a2b7f50e-5ebd-4cab-a6c9-20e39edea2cc``
exists in this repository, so the incident is reproduced from its inputs rather
than from a captured body: an NFL participant the club had already ruled out,
whose roster/identity record still said ``ACTIVE``, reaching the canonical pick
request boundary and being scored as if available.

Three historical states are replayed:

1. ``ROSTER_ONLY`` — what the pre-repair hydration produced. It is no longer
   publishable: validation reports ``UNVERIFIED`` and the ranked outcome is
   ``HELD``.
2. ``OUT`` — an official NFL injury-report designation. It terminates before
   any probability is computed.
3. ``DOUBTFUL`` — an official designation that does *not* remove the player
   from the game. It must stay playable and scored, proving the repair did not
   become a blanket refusal.

The replay asserts the governed invariant directly: no row without official
participation evidence may be rank eligible, and Daily may not classify it as
completed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import pick_request_runtime_core as runtime
from v17.daily_terminal_reduction import classify_stage_status
from v17.player_availability_gate import (
    PLAYER_AVAILABILITY_UNVERIFIED,
    PLAYER_RULED_OUT,
)
from qualification_policy_v2 import classify_prop_probability
from prop_terminal_reducer_v2 import reduce_prop_terminal

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

STAT = "RECEIVING_YARDS"


def _evidence(*, availability: dict | None) -> dict:
    """One hydrated NFL prop row; ``availability`` mirrors the injury report."""
    captured = datetime.now(timezone.utc) - timedelta(minutes=6)
    role_status: dict = {
        "status": "ACTIVE_CURRENT_ESPN_ROSTER",
        "identity_binding_status": "PASS",
        "canonical_event_id": "WOW:NFL:2026-W10-JENNINGS",
        "verified_canonical_event_id": "WOW:NFL:2026-W10-JENNINGS",
    }
    if availability is not None:
        role_status["availability"] = availability
    return {
        "captured_at": captured.isoformat(),
        "game_log": [16.0, 21.0, 18.0, 9.0, 24.0, 13.0, 17.0, 20.0, 11.0, 19.0],
        "box_score_log": [{"week": week, "targets": 6 + week} for week in range(1, 11)],
        "role_status": role_status,
        "role_timestamp": captured.isoformat(),
        "opportunity_ledger": {"status": "PASS", "route_participation": 0.82},
        "source_timestamps": {"official_box_scores": captured.isoformat()},
        "evidence_version": "PROP_EVIDENCE_V1",
        "rate_provenance": "OFFICIAL_BOX_SCORE_L10_V1",
    }


def _row(availability: dict | None):
    return runtime.PickRequestRow(
        event_id="WOW:NFL:2026-W10-JENNINGS",
        event_start_time=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        sport="NFL",
        player="Jauan Jennings",
        stat_type=STAT,
        line=17.5,
        direction="LESS",
        evidence=runtime.RawPropEvidence.model_validate(_evidence(availability=availability)),
    )


def _official(designation: str | None, *, status: str = "AVAILABLE") -> dict:
    return {
        "status": status,
        "designation": designation,
        "evidence_status": "RETRIEVED",
        "source": "ESPN_NFL_ATHLETE_INJURY_REPORT",
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


def _scored_payload() -> dict:
    """A model package that would otherwise publish (control for the block)."""
    return {
        "ok": True,
        "probability_publishable": True,
        "prediction": {
            "calibrated_probability": 0.64,
            "calibrated_probability_lower_bound": 0.58,
            "calibrated_probability_upper_bound": 0.70,
            "calibration_status": "CALIBRATED_FORWARD",
        },
        "probability_qualification": {
            "terminal_label": "MODEL_QUALIFIED_HOLD",
            "confidence_tier": "HIGH",
            "rank_eligible": True,
            "model_supported": True,
            "downstream_money_evaluation_allowed": True,
            "blockers": [],
        },
        "objective_lanes": {"MARKET": {"status": "PASS"}, "MONEY": {"status": "PASS"}},
    }


# --------------------------------------------------------------------------
# State 1: the historical roster-only row (the defect as it reached production)
# --------------------------------------------------------------------------
def test_roster_only_row_is_unverified_and_cannot_be_published():
    """Pre-repair hydration supplied no participation evidence at all."""
    row = _row(None)
    normalized = runtime._validate_evidence(row, STAT)
    assert normalized["availability_status"] == "UNVERIFIED"

    # The scorer still runs, so the row is visible for research...
    outcome = runtime._completed_scored_outcome(
        row_key="nov9-roster-only",
        scored=_scored_payload(),
        snapshot_id="snapshot-nov9",
        fingerprint="fingerprint-nov9",
        acquisition={"mode": "PRE_REPAIR_ROSTER_ONLY"},
        premodel_blockers=[normalized["availability"]["blocker"]],
    )
    # ...but the score can never reach the Verified Pool.
    assert outcome["terminal_label"] == "PLAYER_AVAILABILITY_UNVERIFIED"
    assert outcome["terminal_status"] == "HELD"
    assert outcome["rank_eligible"] is False
    assert outcome["downstream_money_evaluation_allowed"] is False
    assert outcome["pick_rejected"] is False  # held for evidence, not refused
    assert classify_stage_status(
        {
            "terminal_label": outcome["terminal_label"],
            "probability_publishable": outcome["probability_publishable"],
            "rank_eligible": outcome["rank_eligible"],
        }
    ) == "HELD"


def test_roster_only_control_payload_would_have_published_without_the_gate():
    """Isolates the gate: the identical model package still scores when cleared."""
    outcome = runtime._completed_scored_outcome(
        row_key="nov9-control",
        scored=_scored_payload(),
        snapshot_id="snapshot-nov9",
        fingerprint="fingerprint-nov9",
        acquisition={"mode": "TEST"},
        premodel_blockers=[],
    )
    assert outcome["terminal_status"] == "COMPLETED"
    assert outcome["rank_eligible"] is True


# --------------------------------------------------------------------------
# State 2: the ruled-out participant
# --------------------------------------------------------------------------
def test_ruled_out_participant_terminates_before_probability():
    with pytest.raises(ValueError, match=f"^{PLAYER_RULED_OUT}:OUT$"):
        runtime._validate_evidence(_row(_official("OUT", status="OUT")), STAT)


def test_participation_terminal_outranks_a_qualified_model_package():
    assert reduce_prop_terminal(
        proposed_label="MODEL_QUALIFIED_HOLD",
        blockers=[PLAYER_RULED_OUT],
        model_evaluated=True,
    ).terminal_label == PLAYER_RULED_OUT

    decision = classify_prop_probability(
        calibrated_probability=0.99,
        calibrated_lower_bound=0.97,
        calibrated_upper_bound=0.999,
        calibration_status="CALIBRATED_FORWARD",
        blockers=[PLAYER_RULED_OUT],
        probability_publishable=True,
    )
    assert decision.rank_eligible is False
    assert decision.downstream_money_evaluation_allowed is False
    assert classify_stage_status(
        {"terminal_label": PLAYER_RULED_OUT, "pick_rejected": True}
    ) == "PURGED"


# --------------------------------------------------------------------------
# State 3: control — playable official designations are not over-blocked
# --------------------------------------------------------------------------
def test_doubtful_designation_stays_playable_but_flagged():
    normalized = runtime._validate_evidence(
        _row(_official("DOUBTFUL", status="DOUBTFUL")), STAT
    )
    assert normalized["availability_status"] == "AVAILABLE"
    assert normalized["availability"]["risk_designation"] is True


def test_unverified_availability_cannot_be_upgraded_by_a_scorer_claim():
    """A downstream package claiming eligibility must not lift the blocker."""
    decision = classify_prop_probability(
        calibrated_probability=0.99,
        calibrated_lower_bound=0.99,
        calibrated_upper_bound=0.999,
        calibration_status="CALIBRATED_FORWARD",
        blockers=[PLAYER_AVAILABILITY_UNVERIFIED],
        probability_publishable=True,
    )
    assert decision.rank_eligible is False
    assert decision.model_qualified is False
