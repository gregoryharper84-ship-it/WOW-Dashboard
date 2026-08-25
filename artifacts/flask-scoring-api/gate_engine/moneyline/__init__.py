"""
gate_engine/moneyline
WOW v16 Moneyline Architecture — upgraded package.

Separates independent sporting probability, calibrated probability,
calibrated lower bound, and market edge into distinct, non-interchangeable
outputs.  Preserves all WOW v16 governance, fail-closed behavior, and
can_execute=False constraints unconditionally.

Public API
──────────
  from gate_engine.moneyline import run_moneyline_pipeline, MoneylineResult
"""
from __future__ import annotations

from gate_engine.moneyline.pipeline import run_moneyline_pipeline, MoneylineResult

can_execute: bool = False  # UNCONDITIONAL

__all__ = ["run_moneyline_pipeline", "MoneylineResult", "can_execute"]
