"""
gate_engine/fantasy_score_model
WOW v16 Clean Core — Fantasy Score Generative Model

IMPLEMENTATION_READY_FOR_SHADOW_TEST
Shadow/test probability-only capability.  Does not promote to any money or
execution lane.  can_execute = False is unconditional.

Public surface
--------------
  run(row, enr=None)          — pipeline gate entry point
  score_fantasy_row(...)      — programmatic entry point (testing / direct use)
  SUPPORTED_SPORTS            — frozenset of handled sport strings
  SHADOW_MODE                 — always True; never flip without governance review
"""
from __future__ import annotations

can_execute: bool  = False        # UNCONDITIONAL — do not change
SHADOW_MODE: bool  = True         # output is diagnostic; does not alter terminal_label

from .gate import run, score_fantasy_row, SUPPORTED_SPORTS

__all__ = ["run", "score_fantasy_row", "SUPPORTED_SPORTS", "can_execute", "SHADOW_MODE"]
