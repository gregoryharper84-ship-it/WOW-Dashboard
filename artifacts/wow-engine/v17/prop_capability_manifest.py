"""Machine-readable V17 player-prop lane classification.

The governed prop capability ledger advertises one aggregate
``PROP_PROBABILITY`` status, which cannot express that a prop route exists and
is actively reachable while its controlling artifact is not certified for
publication. MLB 1IP is exactly that case: ``/score-pick-request`` carries an
active ``MLB_STATS_API_OFFICIAL_1IP_V1`` hydration route and returns 1IP-native
terminals, while the advertised manifest named only ``MLB / PITCHER_STRIKEOUTS``.
Silent partial coverage is the drift this module removes: every reachable prop
lane is declared with its true status.

Declaration is not capability. A declared lane says the governed backend knows
the lane's shape and which specialist controls it; publication still requires a
certified, promoted artifact plus calibration. A lane that is declared but not
certified terminates against a known contract instead of an unknown stat type,
and it may never borrow another lane's model or a market-implied probability.

This module never scores, never promotes an artifact, and never authorizes
execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CAN_EXECUTE = False

MLB_STRIKEOUT_EXPERT = "wow.mlb-strikeout-expert"
MLB_FIRST_INNING_PITCH_COUNT_EXPERT = "wow.mlb-first-inning-pitch-count-expert"

# Lane classifications. Ordered from most to least production authority.
CERTIFIED_PRODUCTION = "CERTIFIED_PRODUCTION"
SUPPORTED_HOLD_ONLY = "SUPPORTED_HOLD_ONLY"
TEST_ONLY = "TEST_ONLY"
NOT_DECLARED = "NOT_DECLARED"

PUBLICATION_ALLOWED_LANES = frozenset({CERTIFIED_PRODUCTION})

# Exact-line policies. Adjacent-line substitution is never permitted: an
# unsupported exact line rejects as REJECT_OOD so a row is never scored against
# a line the artifact was not certified for.
EXACT_CERTIFIED_LINES_ONLY = "EXACT_CERTIFIED_LINES_ONLY_REJECT_OOD"
CONTINUOUS_LINE_SUPPORT = "CONTINUOUS_LINE_SUPPORT"

# Canonical stat types, matching PROP_STAT_ALIASES in pick_request_runtime_core.
MLB_PITCHER_STRIKEOUTS = "PITCHER_STRIKEOUTS"
MLB_1IP_STAT_TYPE = "1ST_INNING_PITCHES_THROWN"


@dataclass(frozen=True)
class PropCapability:
    sport: str
    stat_type: str
    lane_status: str
    controlling_specialist: str | None
    route_active: bool
    declared_skill_status: str | None
    exact_line_support_policy: str | None
    certified_line_support_source: str | None
    publication_allowed: bool
    blocker: str | None
    notes: str | None = None
    can_execute: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "sport": self.sport,
            "stat_type": self.stat_type,
            "lane_status": self.lane_status,
            "controlling_specialist": self.controlling_specialist,
            "route_active": self.route_active,
            "declared_skill_status": self.declared_skill_status,
            "exact_line_support_policy": self.exact_line_support_policy,
            "certified_line_support_source": self.certified_line_support_source,
            "publication_allowed": self.publication_allowed,
            "blocker": self.blocker,
            "notes": self.notes,
            "can_execute": False,
        }


DECLARED_PROP_LANES: dict[tuple[str, str], PropCapability] = {
    ("MLB", MLB_PITCHER_STRIKEOUTS): PropCapability(
        sport="MLB",
        stat_type=MLB_PITCHER_STRIKEOUTS,
        lane_status=CERTIFIED_PRODUCTION,
        controlling_specialist=MLB_STRIKEOUT_EXPERT,
        route_active=True,
        declared_skill_status="PRODUCTION",
        exact_line_support_policy=CONTINUOUS_LINE_SUPPORT,
        certified_line_support_source=None,
        publication_allowed=True,
        blocker=None,
        notes="Aggregate PROP_PROBABILITY capability status still gates publication at runtime.",
    ),
    ("MLB", MLB_1IP_STAT_TYPE): PropCapability(
        sport="MLB",
        stat_type=MLB_1IP_STAT_TYPE,
        lane_status=SUPPORTED_HOLD_ONLY,
        controlling_specialist=MLB_FIRST_INNING_PITCH_COUNT_EXPERT,
        route_active=True,
        # The immutable v3 skill stays TEST_ONLY under the contract registry's
        # own promotion rules; the live artifact is PROSPECTIVE_CERTIFIED, not
        # promoted. The lane is therefore reachable and scorable, but held.
        declared_skill_status=TEST_ONLY,
        exact_line_support_policy=EXACT_CERTIFIED_LINES_ONLY,
        # Never hardcoded here: the certified line set is read from the governed
        # registry artifact's validation_metrics.validated_lines at score time.
        certified_line_support_source="wow_prop_fitted_model_artifacts.validation_metrics.validated_lines",
        publication_allowed=False,
        blocker="MLB_1IP_ARTIFACT_PROSPECTIVE_CERTIFIED_NOT_PROMOTED",
        notes=(
            "Active MLB_STATS_API_OFFICIAL_1IP_V1 hydration route. An exact line "
            "outside certified support terminates REJECT_OOD; adjacent-line "
            "substitution is prohibited."
        ),
    ),
}


def normalize_prop_sport(value: str) -> str:
    sport = str(value or "").strip().upper()
    aliases = {
        "BASEBALL": "MLB",
        "BASEBALL_MLB": "MLB",
        "MAJOR LEAGUE BASEBALL": "MLB",
        "MAJOR_LEAGUE_BASEBALL": "MLB",
    }
    return aliases.get(sport, sport)


def prop_capability(sport: str, stat_type: str) -> PropCapability:
    """Classify one prop lane. An undeclared lane fails closed, never guesses."""
    normalized_sport = normalize_prop_sport(sport)
    normalized_stat = str(stat_type or "").strip().upper()
    declared = DECLARED_PROP_LANES.get((normalized_sport, normalized_stat))
    if declared is not None:
        return declared
    return PropCapability(
        sport=normalized_sport,
        stat_type=normalized_stat,
        lane_status=NOT_DECLARED,
        controlling_specialist=None,
        route_active=False,
        declared_skill_status=None,
        exact_line_support_policy=None,
        certified_line_support_source=None,
        publication_allowed=False,
        blocker="PROP_LANE_NOT_DECLARED",
        notes="Undeclared prop lane terminates MODEL_UNAVAILABLE against a known contract.",
    )


def declared_prop_lane_manifest() -> dict[str, Any]:
    """Advertise every declared prop lane and its true classification."""
    lanes = [capability.as_dict() for capability in DECLARED_PROP_LANES.values()]
    return {
        "manifest_version": "WOW_V17_PROP_LANE_MANIFEST_V1",
        "lanes": lanes,
        "declared_lane_count": len(lanes),
        "publication_allowed_lane_count": sum(1 for lane in lanes if lane["publication_allowed"]),
        "route_active_lane_count": sum(1 for lane in lanes if lane["route_active"]),
        "declaration_is_not_capability": True,
        "adjacent_line_substitution_permitted": False,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "CERTIFIED_PRODUCTION",
    "CONTINUOUS_LINE_SUPPORT",
    "DECLARED_PROP_LANES",
    "EXACT_CERTIFIED_LINES_ONLY",
    "MLB_1IP_STAT_TYPE",
    "MLB_FIRST_INNING_PITCH_COUNT_EXPERT",
    "MLB_PITCHER_STRIKEOUTS",
    "MLB_STRIKEOUT_EXPERT",
    "NOT_DECLARED",
    "PUBLICATION_ALLOWED_LANES",
    "SUPPORTED_HOLD_ONLY",
    "TEST_ONLY",
    "PropCapability",
    "declared_prop_lane_manifest",
    "normalize_prop_sport",
    "prop_capability",
]
