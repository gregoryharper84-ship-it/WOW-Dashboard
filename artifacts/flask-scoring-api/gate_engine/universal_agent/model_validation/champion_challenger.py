"""
gate_engine/universal_agent/model_validation/champion_challenger.py
WOW-PATCH-2026-08-11-UNIVERSAL-AGENT-CORE-V1-B4-MODELVAL

Champion / Challenger Registry.

For each (sport, stat_key) key:
  - Tracks exactly one champion model_id.
  - Tracks up to MAX_CHALLENGERS challenger model_ids.
  - Challenger promotion to champion requires explicit governance_pin (an
    externally-provided approval string). NEVER automatic.
  - Challengers may be added/removed freely (advisory action, no governance).

Governance invariant (unconditional):
  NO_AUTO_PROMOTION = True
  Champions never switch without a governance_pin supplied by the caller.
  This registry never generates, approves, or infers governance_pins.

can_execute = False
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

can_execute      = False
NO_AUTO_PROMOTION = True
EXECUTION_RULE   = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"

MAX_CHALLENGERS = 3


@dataclass
class ModelSlot:
    """One (sport, stat_key) slot in the registry."""
    sport:        str
    stat_key:     str
    champion:     str | None
    challengers:  list[str]
    promotions:   list[dict]   # audit trail of champion changes
    challenger_log: list[dict] # audit trail of challenger add/remove


class ChampionChallengerRegistry:
    """
    Advisory-only champion/challenger registry.
    can_execute = False. NO_AUTO_PROMOTION = True.
    """

    def __init__(self) -> None:
        self._slots: dict[tuple[str, str], ModelSlot] = {}

    def _key(self, sport: str, stat_key: str) -> tuple[str, str]:
        return (sport.upper().strip(), stat_key.lower().strip())

    def _get_or_create(self, sport: str, stat_key: str) -> ModelSlot:
        k = self._key(sport, stat_key)
        if k not in self._slots:
            self._slots[k] = ModelSlot(
                sport=sport, stat_key=stat_key,
                champion=None, challengers=[],
                promotions=[], challenger_log=[],
            )
        return self._slots[k]

    def set_champion(
        self, sport: str, stat_key: str, model_id: str, *, governance_pin: str
    ) -> None:
        """
        Set the champion for (sport, stat_key).
        Requires a non-empty governance_pin. Raises ValueError without one.
        """
        if not governance_pin or not governance_pin.strip():
            raise ValueError(
                "ChampionChallengerRegistry: champion promotion requires a "
                "non-empty governance_pin. NO_AUTO_PROMOTION is unconditional."
            )
        slot = self._get_or_create(sport, stat_key)
        previous = slot.champion
        slot.champion = model_id
        slot.promotions.append({
            "previous_champion": previous,
            "new_champion": model_id,
            "governance_pin": governance_pin,
        })
        # Remove promoted model from challengers if present
        if model_id in slot.challengers:
            slot.challengers.remove(model_id)

    def add_challenger(self, sport: str, stat_key: str, model_id: str) -> None:
        """Add a challenger. Raises ValueError if slot is full or model already present."""
        slot = self._get_or_create(sport, stat_key)
        if model_id in slot.challengers:
            return  # idempotent
        if model_id == slot.champion:
            raise ValueError(f"{model_id!r} is already the champion — cannot add as challenger.")
        if len(slot.challengers) >= MAX_CHALLENGERS:
            raise ValueError(
                f"Challenger limit reached ({MAX_CHALLENGERS}). "
                "Remove one before adding another."
            )
        slot.challengers.append(model_id)
        slot.challenger_log.append({"action": "ADD", "model_id": model_id})

    def remove_challenger(self, sport: str, stat_key: str, model_id: str) -> None:
        """Remove a challenger. No-op if not present."""
        slot = self._get_or_create(sport, stat_key)
        if model_id in slot.challengers:
            slot.challengers.remove(model_id)
            slot.challenger_log.append({"action": "REMOVE", "model_id": model_id})

    def get_champion(self, sport: str, stat_key: str) -> str | None:
        k = self._key(sport, stat_key)
        return self._slots[k].champion if k in self._slots else None

    def get_challengers(self, sport: str, stat_key: str) -> list[str]:
        k = self._key(sport, stat_key)
        return list(self._slots[k].challengers) if k in self._slots else []

    def status(self, sport: str, stat_key: str) -> dict[str, Any]:
        k = self._key(sport, stat_key)
        if k not in self._slots:
            return {"champion": None, "challengers": [], "promotion_count": 0}
        slot = self._slots[k]
        return {
            "champion":        slot.champion,
            "challengers":     list(slot.challengers),
            "promotion_count": len(slot.promotions),
            "no_auto_promotion": True,
        }

    def all_slots(self) -> list[dict[str, Any]]:
        return [self.status(s.sport, s.stat_key) for s in self._slots.values()]
