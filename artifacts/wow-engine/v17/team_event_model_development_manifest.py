"""All-sports V17 team/event model-development coverage.

This is a development/maintenance map, not execution authority. It records the
strongest repository lane that owns each sport and the next engineering gate.
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
    "WNBA": ModelDevelopmentLane("WNBA", "PRODUCTION_MODEL_PRESENT", "WNBA_TEAM_EVENT_PROBABILITY", "LIVE_GOVERNANCE_ACCEPTANCE"),
    "NCAAF": ModelDevelopmentLane("NCAAF", "CANDIDATE_PIPELINE_PRESENT", "NCAAF_MODEL_MAINTENANCE", "FITTED_ARTIFACT_AND_CALIBRATOR_PROMOTION"),
    "NCAAB": ModelDevelopmentLane("NCAAB", "CANDIDATE_PIPELINE_PRESENT", "NCAAB_OPEN_DATA_MAINTENANCE", "CERTIFICATION_REPLAY_AND_PROMOTION"),
    "NHL": ModelDevelopmentLane("NHL", "PRODUCTION_MODEL_PRESENT", "NHL_TEAM_EVENT_PROBABILITY", "LIVE_GOVERNANCE_ACCEPTANCE"),
    "SOCCER": ModelDevelopmentLane("SOCCER", "PRODUCTION_MODEL_PRESENT", "SOCCER_TEAM_EVENT_PROBABILITY", "LIVE_GOVERNANCE_ACCEPTANCE"),
    "TENNIS": ModelDevelopmentLane("TENNIS", "PRODUCTION_MODEL_PRESENT", "TENNIS_TEAM_EVENT_PROBABILITY", "LIVE_GOVERNANCE_ACCEPTANCE"),
    "PGA": ModelDevelopmentLane("PGA", "BUILD_REQUIRED", None, "HISTORICAL_FIELD_DATA_AND_CALIBRATED_FIELD_MODEL"),
    "MMA": ModelDevelopmentLane("MMA", "PRODUCTION_MODEL_PRESENT", "MMA_TEAM_EVENT_PROBABILITY", "LIVE_GOVERNANCE_ACCEPTANCE_AND_HISTORY_HYDRATION"),
    "BOXING": ModelDevelopmentLane("BOXING", "BUILD_REQUIRED", None, "HISTORICAL_FIGHT_DATA_AND_CALIBRATED_WIN_MODEL"),
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
