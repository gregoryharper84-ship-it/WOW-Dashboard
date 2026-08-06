"""
gate_engine/command_center
WOW Sports Intelligence Command Center — Phase 1

Federated orchestration and governance layer above the WOW Prop, LLP,
Kalshi Sports, and Kalshi Weather engines.

Public API:
  run_intake(raw_candidates, ...)           → Phase A: routing manifest
  run_command_center(raw_candidates, ...)   → Phase A+B: full orchestration

Governance constants (all unconditional):
  CAN_EXECUTE = False
  DRY_RUN_ONLY = True
  KALSHI_RECOVERY_MODE = "ACTIVE"
"""
from .orchestrator import run_intake, run_command_center
from .cc_labels import (
    CAN_EXECUTE, DRY_RUN_ONLY, KALSHI_RECOVERY_MODE,
    FAMILY_PROP, FAMILY_LLP, FAMILY_KALSHI_SPORTS, FAMILY_KALSHI_WEATHER,
    ALL_FAMILIES, CEILING_ORDER,
    ceiling_rank,
)
from .ceiling_resolver import resolve_ceiling

__all__ = [
    "run_intake",
    "run_command_center",
    "CAN_EXECUTE",
    "DRY_RUN_ONLY",
    "KALSHI_RECOVERY_MODE",
    "FAMILY_PROP",
    "FAMILY_LLP",
    "FAMILY_KALSHI_SPORTS",
    "FAMILY_KALSHI_WEATHER",
    "ALL_FAMILIES",
    "CEILING_ORDER",
    "ceiling_rank",
    "resolve_ceiling",
]
