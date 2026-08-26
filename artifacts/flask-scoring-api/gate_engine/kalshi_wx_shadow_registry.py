"""
gate_engine/kalshi_wx_shadow_registry.py
WOW-PATCH-2026-08-08-MULTI-AGENT-KALSHI-WX-SHADOW — Step 7: taxonomy registry

Canonical namespaced label taxonomy for the Kalshi Weather shadow pilot.
Three semantically distinct namespaces are maintained separately; they must
NOT be flattened into a single combined enum.

NAMESPACES
──────────
  OperationalState   — control-plane signals describing pilot execution policy
  ModelReadiness     — internal confidence-tier values (WEATHER_* prefix) used
                       by the weather route handlers; never appear in external output
  TerminalProjection — public-facing output labels (KALSHI_* prefix) returned by
                       _weather_terminal_label_v2() and validated by the fail-closed guard

CEILING-CAPABLE SUBSET
───────────────────────
  CEILING_CAPABLE_LABELS  exactly equals TerminalProjection.kalshi_weather.MEMBERS.
  A future agent-advisory ceiling field MUST be validated against this set only.
  OperationalState and ModelReadiness values are NOT ceiling-capable and must
  never pass ceiling validation even though they live in this registry module.

SINGLE SOURCE OF TRUTH
───────────────────────
  TerminalProjection.kalshi_weather.MEMBERS is NOT a second copy of the 6 labels.
  It references KALSHI_WX_TERMINAL_LABEL_REGISTRY from
  gate_engine/kalshi_wx_terminal_labels.py — the authoritative definition
  established by WOW-PATCH-2026-08-08-KALSHI-WX-TERMINAL-LABEL-FAIL-CLOSED.
  Changing the 6 labels in that module automatically propagates here.

SCOPE
─────
  This registry covers only what the Kalshi Weather shadow pilot requires.
  Do NOT add labels from other WOW lanes (LLP, Kalshi Sports, NBA/MLB props).
  Do NOT add KALSHI_REJECT_THIN_BOOK, KALSHI_REJECT_FEE_DRAG, or any
  WEATHER_REJECT_* value — those are out of scope for this registry.

ISOLATION INVARIANT
───────────────────
  This module must NOT be imported by or referenced from:
    gate_engine/wow_runtime_manifest.py   (CEILING_RANK / resolve_lowest_ceiling)
    gate_engine/command_center/cc_labels.py
    gate_engine/command_center/ceiling_resolver.py
  Those ceiling resolvers are independent systems with their own registries.
"""
from __future__ import annotations

from gate_engine.kalshi_wx_terminal_labels import KALSHI_WX_TERMINAL_LABEL_REGISTRY


# ---------------------------------------------------------------------------
# Namespace 1 — Operational State
#
# Control-plane signals describing the pilot's execution policy.
# These must NEVER appear as valid ceiling labels: they are mode descriptors,
# not outcome labels. A caller checking an agent-advisory ceiling field against
# these values should always receive False from is_ceiling_capable().
# ---------------------------------------------------------------------------
class OperationalState:
    """Pilot execution policy signals."""

    SHADOW_ONLY:  str = "SHADOW_ONLY"
    DRY_RUN_ONLY: str = "DRY_RUN_ONLY"

    MEMBERS: frozenset[str] = frozenset({SHADOW_ONLY, DRY_RUN_ONLY})


# ---------------------------------------------------------------------------
# Namespace 2 — Model Readiness
#
# Internal confidence-tier values produced by the weather route handlers
# (GET /kalshi/evaluate/weather/<city> and POST /wow/kalshi/weather/evaluate)
# and consumed by _weather_terminal_label_v2().  These are WEATHER_*-prefixed
# values already present as live string-literal assignments in app.py.
# They never appear in external API output or agent-facing responses — they are
# an intermediate computation step, not public projection labels.
# Must NEVER appear as valid ceiling labels.
# ---------------------------------------------------------------------------
class ModelReadiness:
    """Internal weather confidence-tier labels (WEATHER_* prefix)."""

    WEATHER_SCOUT:       str = "WEATHER_SCOUT"
    WEATHER_WATCH:       str = "WEATHER_WATCH"
    WEATHER_MODEL_READY: str = "WEATHER_MODEL_READY"

    MEMBERS: frozenset[str] = frozenset({
        WEATHER_SCOUT,
        WEATHER_WATCH,
        WEATHER_MODEL_READY,
    })


# ---------------------------------------------------------------------------
# Namespace 3 — Terminal Projection: Kalshi Weather sub-branch
#
# Public-facing output labels returned by _weather_terminal_label_v2() and
# validated by the fail-closed guard in the route handlers.  These are the
# labels that reach the API response, the weather_scout_log calibration
# ledger, and (in the shadow pilot) the agent-advisory ceiling field.
#
# The MEMBERS set is NOT a redefinition — it is the same frozenset object
# imported from gate_engine/kalshi_wx_terminal_labels.py.  There is exactly
# one definition of these 6 labels in the codebase.
# ---------------------------------------------------------------------------
class _KalshiWeatherProjection:
    """Kalshi Weather terminal-label sub-namespace."""

    MEMBERS: frozenset[str] = KALSHI_WX_TERMINAL_LABEL_REGISTRY


class TerminalProjection:
    """Public-facing output label namespaces."""

    kalshi_weather: type[_KalshiWeatherProjection] = _KalshiWeatherProjection


# ---------------------------------------------------------------------------
# Ceiling-capable subset
#
# Exactly equals TerminalProjection.kalshi_weather.MEMBERS (6 labels).
# This is the authoritative set for validating any agent-advisory ceiling field.
# OperationalState.MEMBERS and ModelReadiness.MEMBERS are disjoint from this set.
# ---------------------------------------------------------------------------
CEILING_CAPABLE_LABELS: frozenset[str] = TerminalProjection.kalshi_weather.MEMBERS


def is_ceiling_capable(label: str) -> bool:
    """
    Return True iff `label` is valid for an agent-advisory ceiling field.

    Returns True  for every member of TerminalProjection.kalshi_weather.MEMBERS.
    Returns False for OperationalState values (SHADOW_ONLY, DRY_RUN_ONLY).
    Returns False for ModelReadiness values (WEATHER_SCOUT, WEATHER_WATCH,
                   WEATHER_MODEL_READY).
    Returns False for any other string.

    Callers MUST use this function — not a direct `in _KALSHI_WX_TERMINAL_LABEL_REGISTRY`
    check — so that the ceiling-capable semantics remain centrally enforced here.
    """
    return label in CEILING_CAPABLE_LABELS
