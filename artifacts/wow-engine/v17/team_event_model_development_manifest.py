"""All-sports V17 team/event model-development coverage.

This is a development/maintenance map, not production capability. It records the
strongest repository lane that can advance each sport toward a certified fitted
team/event model while preserving fail-closed production routing. Exact runtime
installers may promote an entry only after its scorer/bridge/certification chain
activates successfully.
"""
from __future__ import annotations

from dataclasses import dataclass

from v17.team_event_capability_manifest import EXPECTED_TEAM_EVENT_SPORTS

CAN_EXECUTE = False


@dataclass(frozen=True)
class ModelDevelopmentLane:
    sport: str
    status: str
    maintenance_lane: str | None
    next_gate: str
    can_execute: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "sport": self.sport,
            "status": self.status,
            "maintenance_lane": self.maintenance_lane,
            "next_gate": self.next_gate,
            "can_execute": False,
        }


TEAM_EVENT_MODEL_DEVELOPMENT: dict[str, ModelDevelopmentLane] = {
    "MLB": ModelDevelopmentLane("MLB", "PRODUCTION_MODEL_PRESENT", "MLB_EVENT_PROBABILITY", "LIVE_GOVERNANCE_ACCEPTANCE"),
    "NFL": ModelDevelopmentLane("NFL", "PRODUCTION_MODEL_PRESENT", "NFL_EVENT_PROBABILITY", "LIVE_GOVERNANCE_ACCEPTANCE"),
    "NBA": ModelDevelopmentLane("NBA", "CANDIDATE_PIPELINE_PRESENT", "BASKETBALL_MODEL_MAINTENANCE", "CERTIFICATION_REPLAY_AND_PROMOTION"),
    "WNBA": ModelDevelopmentLane("WNBA", "CANDIDATE_PIPELINE_PRESENT", "BASKETBALL_MODEL_MAINTENANCE", "PREGAME_FEATURE_REPLAY_THEN_CERTIFICATION"),
    "NCAAF": ModelDevelopmentLane("NCAAF", "CANDIDATE_PIPELINE_PRESENT", "NCAAF_MODEL_MAINTENANCE", "FITTED_ARTIFACT_AND_CALIBRATOR_PROMOTION"),
    "NCAAB": ModelDevelopmentLane("NCAAB", "CANDIDATE_PIPELINE_PRESENT", "NCAAB_OPEN_DATA_MAINTENANCE", "CERTIFICATION_REPLAY_AND_PROMOTION"),
    "NHL": ModelDevelopmentLane("NHL", "CANDIDATE_PIPELINE_PRESENT", "NHL_MODEL_MAINTENANCE", "CERTIFICATION_REPLAY_AND_PROMOTION"),
    "SOCCER": ModelDevelopmentLane("SOCCER", "CANDIDATE_PIPELINE_PRESENT", "SOCCER_OPEN_DATA_MAINTENANCE", "THREE_WAY_CERTIFICATION_AND_PROMOTION"),
    "TENNIS": ModelDevelopmentLane("TENNIS", "CANDIDATE_PIPELINE_PRESENT", "TENNIS_OPEN_DATA_MAINTENANCE", "RETIREMENT_AWARE_CERTIFICATION_AND_PROMOTION"),
    "PGA": ModelDevelopmentLane("PGA", "BUILD_REQUIRED", None, "HISTORICAL_FIELD_DATA_AND_CALIBRATED_FIELD_MODEL"),
    "MMA": ModelDevelopmentLane("MMA", "BUILD_REQUIRED", None, "HISTORICAL_FIGHT_DATA_AND_CALIBRATED_WIN_MODEL"),
    "BOXING": ModelDevelopmentLane("BOXING", "BUILD_REQUIRED", None, "HISTORICAL_FIGHT_DATA_AND_CALIBRATED_WIN_MODEL"),
    "CRICKET": ModelDevelopmentLane("CRICKET", "BUILD_REQUIRED", None, "T20_HISTORICAL_MATCH_DATA_HYDRATOR_FITTED_WIN_MODEL_AND_CALIBRATION"),
}

if set(TEAM_EVENT_MODEL_DEVELOPMENT) != set(EXPECTED_TEAM_EVENT_SPORTS):
    raise RuntimeError("TEAM_EVENT_MODEL_DEVELOPMENT_COVERAGE_MISMATCH")


def development_lane(sport: str) -> ModelDevelopmentLane | None:
    return TEAM_EVENT_MODEL_DEVELOPMENT.get(str(sport or "").strip().upper())


__all__ = [
    "CAN_EXECUTE",
    "ModelDevelopmentLane",
    "TEAM_EVENT_MODEL_DEVELOPMENT",
    "development_lane",
]
