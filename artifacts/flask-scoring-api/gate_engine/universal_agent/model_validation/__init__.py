"""
gate_engine/universal_agent/model_validation/__init__.py
WOW-PATCH-2026-08-11-UNIVERSAL-AGENT-CORE-V1-B4-MODELVAL

Shared Model Consistency & Validation Layer — public API.

Provides advisory-only infrastructure for:
  - Point-in-time data locking           (PointInTimeFeatureStore)
  - Immutable model-run provenance       (ModelManifest)
  - Champion/challenger registry         (ChampionChallengerRegistry)
  - Family-specific calibration          (CalibrationScoreboard)
  - Model/data drift detection           (DriftMonitor)
  - Model health state machine           (ModelHealthStateMachine)
  - Two-speed learning schedule          (TwoSpeedLearningSchedule)
  - Promotion / rollback gates           (PromotionGate)
  - Walk-forward historical replay       (WalkForwardReplayEngine)
  - B4 result validation wrapper         (ModelValidationWrapper)

Governance invariants (unconditional)
--------------------------------------
  can_execute             = False
  PRODUCTION_AUTHORITY    = False
  USER_OUTPUT_AUTHORITY   = False
  CAPITAL_AUTHORITY       = False
  NO_AUTO_PROMOTION       = True   # champions never switch automatically
  NO_FORMULA_MUTATION     = True   # production probability formulas unchanged

FOLLOWUP_193 must be resolved before authoritative decision integration.
FOLLOWUP_195 must be resolved before B4 closure.
Explicit place_bet/settlement governance resolution required before any
live external-model canary.
"""
can_execute           = False
PRODUCTION_AUTHORITY  = False
USER_OUTPUT_AUTHORITY = False
CAPITAL_AUTHORITY     = False
NO_AUTO_PROMOTION     = True
NO_FORMULA_MUTATION   = True
PATCH_ID              = "WOW-PATCH-2026-08-11-MODELVAL"

from gate_engine.universal_agent.model_validation.feature_store import (
    PointInTimeFeatureStore, FeatureSnapshot,
)
from gate_engine.universal_agent.model_validation.model_manifest import (
    ModelManifest, ManifestEntry,
)
from gate_engine.universal_agent.model_validation.champion_challenger import (
    ChampionChallengerRegistry,
)
from gate_engine.universal_agent.model_validation.calibration_scoreboard import (
    CalibrationScoreboard,
)
from gate_engine.universal_agent.model_validation.drift_monitor import (
    DriftMonitor, DriftStatus,
)
from gate_engine.universal_agent.model_validation.health_state import (
    ModelHealthStateMachine, HealthState,
)
from gate_engine.universal_agent.model_validation.promotion_gate import (
    PromotionGate, PromotionStatus,
)
from gate_engine.universal_agent.model_validation.walk_forward import (
    WalkForwardReplayEngine,
)
from gate_engine.universal_agent.model_validation.validation_wrapper import (
    ModelValidationWrapper, ValidatedAdapterResult,
)

__all__ = [
    "PointInTimeFeatureStore", "FeatureSnapshot",
    "ModelManifest", "ManifestEntry",
    "ChampionChallengerRegistry",
    "CalibrationScoreboard",
    "DriftMonitor", "DriftStatus",
    "ModelHealthStateMachine", "HealthState",
    "PromotionGate", "PromotionStatus",
    "WalkForwardReplayEngine",
    "ModelValidationWrapper", "ValidatedAdapterResult",
]
