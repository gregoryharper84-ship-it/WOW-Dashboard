"""
gate_engine/universal_agent/lanes/mlb_props/event_tree/one_ip_gate.py
WOW-PATCH-2026-08-16-UNIVERSAL-AGENT-CORE-V1-B5

1IP Pitches Event-Tree Gate.

The 1IP pitches market MUST route exclusively through the dedicated event-tree.
It is structurally incompatible with generic prop models (binomial, Poisson)
because the outcome space is bounded to a single inning of work, producing a
non-stationary distribution highly sensitive to game context (starter vs long
relief, pitch count state, manager leash).

This gate enforces that constraint at the adapter layer:
  - Accepts a combined evidence dict (row + enrichment).
  - Returns OneIpGateResult with routing_required=True and the canonical
    event_tree_id for all 1IP markets.
  - Returns routing_required=False with block_reason for non-1IP markets
    (gate is a no-op for those; caller must not apply the 1IP event-tree
    to other stat types).

Invariants
----------
- can_execute = False
- No live LLM, API, or network calls.
- Never fabricates probability values.
- Returns deterministic, reproducible results for identical inputs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"

ONE_IP_EVENT_TREE_ID = "MLB_1IP_PITCHES_EVENT_TREE_V1"

# Canonical stat_key values that identify the 1IP pitches market.
_ONE_IP_STAT_KEYS: frozenset[str] = frozenset({
    "pitcher_1ip_pitches",
    "1ip_pitches",
    "pitches_first_inning",
    "first_inning_pitches",
})

# Generic prop models that MUST NOT be applied to 1IP markets.
BLOCKED_GENERIC_MODELS: frozenset[str] = frozenset({
    "MLB_BINOMIAL_V1",
    "MLB_POISSON_V1",
    "GENERIC_PROP_MODEL",
})


@dataclass(frozen=True)
class OneIpGateResult:
    """
    Immutable result of a OneIpGate.evaluate() call.

    Fields
    ------
    routing_required
        True when the stat_key identifies a 1IP pitches market and the
        caller MUST use the ONE_IP_EVENT_TREE_ID routing. False when the
        market is not 1IP (gate is advisory and callers should not apply
        the event-tree).
    event_tree_id
        ONE_IP_EVENT_TREE_ID when routing_required=True; None otherwise.
    stat_key_detected
        The stat_key string extracted from the combined dict (lower-cased
        and stripped). Always present; may be "UNKNOWN" if absent from row.
    block_reason
        Non-None only when routing_required=False due to an explicit
        non-1IP market. Provides a human-readable gate skip reason.
    generic_model_blocked
        True when routing_required=True (generic models are unconditionally
        blocked for 1IP markets). Always False when routing_required=False.
    """
    routing_required:      bool
    event_tree_id:         Optional[str]
    stat_key_detected:     str
    block_reason:          Optional[str]
    generic_model_blocked: bool


class OneIpGate:
    """
    Stateless 1IP pitches event-tree routing gate.

    Usage
    -----
        gate   = OneIpGate()
        result = gate.evaluate(combined)

        if result.routing_required:
            # must use ONE_IP_EVENT_TREE_ID, generic models blocked
            routing = result.event_tree_id   # "MLB_1IP_PITCHES_EVENT_TREE_V1"
        else:
            # not a 1IP market — gate does not apply
            pass
    """

    def evaluate(self, combined: dict) -> OneIpGateResult:
        """
        Evaluate whether the combined evidence dict describes a 1IP pitches market.

        Parameters
        ----------
        combined
            Merged evidence dict ({**enrichment, **row}, row wins on collision).
            Must be a dict; non-dict input treated as empty.

        Returns
        -------
        OneIpGateResult (frozen dataclass).
        """
        if not isinstance(combined, dict):
            combined = {}

        raw_stat_key = (
            combined.get("stat_key")
            or combined.get("prop_type")
            or combined.get("market_subtype")
            or ""
        )
        stat_key = str(raw_stat_key).lower().strip() if raw_stat_key else "UNKNOWN"

        is_one_ip = stat_key in _ONE_IP_STAT_KEYS

        if is_one_ip:
            return OneIpGateResult(
                routing_required=True,
                event_tree_id=ONE_IP_EVENT_TREE_ID,
                stat_key_detected=stat_key,
                block_reason=None,
                generic_model_blocked=True,
            )
        else:
            reason = (
                f"stat_key={stat_key!r} is not a 1IP pitches market; "
                "event-tree routing does not apply"
            )
            return OneIpGateResult(
                routing_required=False,
                event_tree_id=None,
                stat_key_detected=stat_key,
                block_reason=reason,
                generic_model_blocked=False,
            )
