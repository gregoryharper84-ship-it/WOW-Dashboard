"""Wave 3 FB-NFL-FIVE: five missing count-prop family evidence registry (§B).

Research-only inventory. No model is fitted here, no zero-imputation is
performed, and no family is promoted. Each family carries a typed HOLD or
MODEL_BUILD_REQUIRED until genuine per-family fitted evidence exists.

Preserved source facts (from #2605 research, restated as blockers not data):
- 32,220 positive-offensive-snap player-games; 28,178 present weekly;
  3,994 snap-positive mapped with no weekly stats (UNKNOWN stays UNKNOWN);
  48 PFR↔GSIS mapping gaps.
- Two documented residuals stay visible: Rodgers 2025 week 2 PIT passing
  attempts; Ogunbowale 2024 week 15 HOU rushing attempts.
- Poisson is a challenger only; NB / zero-inflated / mixed-regime
  distributions must be compared where supported.
- Chronology freeze: 2021–2023 train, 2024 calibration, untouched 2025
  holdout (or stricter ratified partition). No future-week windows, postgame
  status, current odds, future injury features or leakage.
- Old-four lanes (ANYTIME_TD, PASSING_YARDS, RECEIVING_YARDS, RUSHING_YARDS)
  must keep passing nonregression; candidate coverage stays +0 published.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

CAN_EXECUTE = False

FIVE_FAMILIES = (
    "COMPLETIONS",
    "PASSING_TOUCHDOWNS",
    "PASS_ATTEMPTS",
    "RECEPTIONS",
    "RUSH_ATTEMPTS",
)

OLD_FOUR_FAMILIES = (
    "ANYTIME_TD",
    "PASSING_YARDS",
    "RECEIVING_YARDS",
    "RUSHING_YARDS",
)

CANDIDATE_ONLY_FAMILIES = ("FANTASY_SCORE",)

CHRONOLOGY_PARTITION = {
    "train": "2021-2023",
    "calibration": "2024",
    "holdout": "2025",
}

RESIDUAL_DISAGREEMENTS = (
    {
        "id": "RODGERS_2025_W02_PIT_PASS_ATTEMPTS",
        "status": "UNRESOLVED_SOURCE_DISAGREEMENT",
    },
    {
        "id": "OGUNBOWALE_2024_W15_HOU_RUSH_ATTEMPTS",
        "status": "UNRESOLVED_SOURCE_DISAGREEMENT",
    },
)


@dataclass(frozen=True)
class FamilyEvidence:
    stat_type: str
    status: str
    blockers: tuple
    fitted: bool = False
    probability_publishable: bool = False
    can_execute: bool = False


def five_family_inventory() -> Mapping[str, FamilyEvidence]:
    """Return the per-family typed HOLD registry. No family is fitted here."""
    inventory: dict[str, FamilyEvidence] = {}
    for family in FIVE_FAMILIES:
        inventory[family] = FamilyEvidence(
            stat_type=family,
            status="MODEL_BUILD_REQUIRED",
            blockers=(
                "FIVE_PROP_PER_FAMILY_FIT_MISSING",
                "FIVE_PROP_UNKNOWN_ROWS_QUARANTINED_3994",
                "FIVE_PROP_IDENTITY_GAPS_48",
                "FIVE_PROP_SOURCE_SEMANTICS_UNPROVEN",
                "FIVE_PROP_2025_HOLDOUT_REPLAY_MISSING",
            ),
        )
    return inventory


def old_four_nonregression_gate(old_four_pass: bool) -> Mapping[str, object]:
    """Guard: candidate work must not regress the four live lanes."""
    if not old_four_pass:
        return {
            "decision": "HOLD",
            "blockers": ["OLD_FOUR_NONREGRESSION_FAILED"],
            "candidate_published_delta": 0,
            "can_execute": False,
        }
    return {
        "decision": "PROCEED_RESEARCH_ONLY",
        "blockers": [],
        "candidate_published_delta": 0,
        "can_execute": False,
    }


def family_decision(family: str, *, fitted: bool, holdout_pass: bool) -> Mapping[str, object]:
    """Per-family READY vs HOLD. FANTASY_SCORE is CANDIDATE_ONLY."""
    name = str(family or "").strip().upper()
    if name in CANDIDATE_ONLY_FAMILIES:
        return {
            "stat_type": name,
            "decision": "CANDIDATE_ONLY",
            "probability_publishable": False,
            "can_execute": False,
        }
    if name not in FIVE_FAMILIES:
        return {
            "stat_type": name,
            "decision": "HOLD",
            "blockers": ["UNKNOWN_STAT_FAMILY"],
            "probability_publishable": False,
            "can_execute": False,
        }
    if fitted and holdout_pass:
        return {
            "stat_type": name,
            "decision": "READY_FOR_INDEPENDENT_MODEL_REVIEW",
            "probability_publishable": False,
            "can_execute": False,
        }
    return {
        "stat_type": name,
        "decision": "HOLD",
        "blockers": ["PER_FAMILY_FIT_OR_HOLDOUT_MISSING"],
        "probability_publishable": False,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "CANDIDATE_ONLY_FAMILIES",
    "CHRONOLOGY_PARTITION",
    "FIVE_FAMILIES",
    "OLD_FOUR_FAMILIES",
    "RESIDUAL_DISAGREEMENTS",
    "FamilyEvidence",
    "family_decision",
    "five_family_inventory",
    "old_four_nonregression_gate",
]
