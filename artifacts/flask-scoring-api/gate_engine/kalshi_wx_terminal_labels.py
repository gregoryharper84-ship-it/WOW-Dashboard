"""
gate_engine/kalshi_wx_terminal_labels.py
WOW-PATCH-2026-08-08-KALSHI-WX-TERMINAL-LABEL-FAIL-CLOSED
WOW-PATCH-2026-08-08-MULTI-AGENT-KALSHI-WX-SHADOW

Single source of truth for confirmed-reachable Kalshi Weather terminal labels.

Extracted from app.py so that both the route-level fail-closed guard
(app.py: _validate_wx_terminal_label) and the shadow-pilot taxonomy registry
(gate_engine/kalshi_wx_shadow_registry.py) can reference the same frozenset
without duplication.

ADDING LABELS:
  Do NOT add a label here until its code path in _weather_terminal_label_v2()
  is confirmed reachable via a live return statement, not merely mentioned in
  a docstring or comment.

INTENTIONALLY EXCLUDED (docstring-only, no reachable return path today):
  KALSHI_REJECT_THIN_BOOK
  KALSHI_REJECT_FEE_DRAG

ISOLATION INVARIANT:
  This module must NOT be imported by or referenced from:
    gate_engine/wow_runtime_manifest.py
    gate_engine/command_center/cc_labels.py
    gate_engine/command_center/ceiling_resolver.py
"""
from __future__ import annotations

KALSHI_WX_TERMINAL_LABEL_REGISTRY: frozenset[str] = frozenset({
    "KALSHI_PLAYABLE_LIMIT_ONLY",
    "KALSHI_WATCH",
    "KALSHI_REJECT_NO_EDGE",
    "KALSHI_REJECT_BAD_RULES",
    "KALSHI_REJECT_UNCALIBRATED",
    "KALSHI_DATA_UNOBTAINABLE",
})
