"""Explicit resolver states and repository audit for team/event model coverage.

``team_event_bridge_health`` could previously only say registered or not, so a
sport with a real adapter that simply was not wired looked identical to a sport
with no model at all — and both looked identical to a sport whose model exists
but has no promoted artifact.  Those are three different engineering problems.

This module answers one question per sport, from the repository itself rather
than from documentation:

    Is there a fitted implementation, an adapter, and a resolvable scorer?

It never creates capability.  Everything here is read-only inspection; a sport
with no importable adapter stays ``ADAPTER_MISSING`` and resolves, externally, to
``MODEL_UNAVAILABLE``.  Governance decides what that means downstream.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, Callable

from v17.team_event_capability_manifest import (
    EXPECTED_TEAM_EVENT_SPORTS,
    normalize_team_event_sport,
)

CAN_EXECUTE = False

# Resolver states. Only REGISTERED may score.
REGISTERED = "REGISTERED"
UNREGISTERED = "UNREGISTERED"
DISABLED = "DISABLED"
MODEL_ARTIFACT_MISSING = "MODEL_ARTIFACT_MISSING"
ADAPTER_MISSING = "ADAPTER_MISSING"

RESOLVER_STATES = (REGISTERED, UNREGISTERED, DISABLED, MODEL_ARTIFACT_MISSING, ADAPTER_MISSING)


@dataclass(frozen=True)
class CapabilityProbe:
    """What the repository actually contains for one sport."""

    sport: str
    fitted_module: str | None
    adapter_module: str | None
    scorer_symbol: str | None
    adapter_importable: bool
    scorer_resolvable: bool
    model_artifact_loader: str | None
    notes: str
    can_execute: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "sport": self.sport,
            "fitted_module": self.fitted_module,
            "adapter_module": self.adapter_module,
            "scorer_symbol": self.scorer_symbol,
            "adapter_importable": self.adapter_importable,
            "scorer_resolvable": self.scorer_resolvable,
            "model_artifact_loader": self.model_artifact_loader,
            "notes": self.notes,
            "can_execute": False,
        }


# Declared implementation chains. A sport appears here only when a concrete
# module path is claimed; the probe then verifies that claim by import. Absence
# from this table is itself an audit answer: no fitted team/event implementation
# was found in the repository for that sport.
_DECLARED_CHAINS: dict[str, dict[str, str]] = {
    "MLB": {
        "fitted_module": "v17.mlb_event_bridge_repair",
        "adapter_module": "v17.team_event_request_runtime",
        "scorer_symbol": "score_team_event_request",
        "model_artifact_loader": "v17.mlb_event_bridge_repair:score_event_v17_bridge",
        "notes": "Certified MLB game-win specialist with its own canonical hydration contract.",
    },
    "NFL": {
        "fitted_module": "nfl_event_model_v17",
        "adapter_module": "v17.nfl_team_event_specialist",
        "scorer_symbol": "score_nfl_team_event",
        "model_artifact_loader": "nfl_event_model_v17:load_champion_model",
        "notes": (
            "Fitted NFL outright-win bundle with Platt calibration and governed publication "
            "bridge. Champion promotion is a runtime/database condition: with no promoted "
            "artifact the scorer fails closed as MODEL_UNAVAILABLE at score time."
        ),
    },
}

# Sports whose evidence/trust or training material exists but which have no
# team/event winner scorer entry point. Recorded so the audit answer is a
# reason, not a silence. These never become registered capability.
_KNOWN_PARTIAL_WORK: dict[str, str] = {
    "NCAAF": (
        "Fitted-artifact provider (ncaaf_fitted_provider), logistic model-family adapter and "
        "trust layer exist, but there is no team/event winner scorer entry point, no governed "
        "probability-package mapping and no event-governor binding."
    ),
    "WNBA": (
        "WNBA history/runtime and prop-lane work exist; no team/event winner model, adapter or "
        "scorer."
    ),
    "NBA": "No fitted team/event winner model, adapter or scorer in the repository.",
    "NCAAB": "No fitted team/event winner model, adapter or scorer in the repository.",
    "NHL": "No fitted team/event winner model, adapter or scorer in the repository.",
    "SOCCER": (
        "No fitted three-way (home/draw/away) winner model, adapter or scorer in the repository."
    ),
    "TENNIS": "No fitted match-winner model, adapter or scorer in the repository.",
    "PGA": "No calibrated field-distribution or head-to-head model, adapter or scorer.",
    "MMA": "No fitted fight-winner specialist, adapter or scorer in the repository.",
    "BOXING": "No fitted fight-winner specialist, adapter or scorer in the repository.",
}


def _probe_chain(sport: str, chain: dict[str, str]) -> CapabilityProbe:
    adapter_module = chain.get("adapter_module")
    scorer_symbol = chain.get("scorer_symbol")
    adapter_importable = False
    scorer_resolvable = False
    try:
        module = importlib.import_module(str(adapter_module))
        adapter_importable = True
        scorer_resolvable = callable(getattr(module, str(scorer_symbol), None))
    except Exception:  # noqa: BLE001 - a broken import is an audit answer
        adapter_importable = False
        scorer_resolvable = False
    return CapabilityProbe(
        sport=sport,
        fitted_module=chain.get("fitted_module"),
        adapter_module=adapter_module,
        scorer_symbol=scorer_symbol,
        adapter_importable=adapter_importable,
        scorer_resolvable=scorer_resolvable,
        model_artifact_loader=chain.get("model_artifact_loader"),
        notes=str(chain.get("notes") or ""),
    )


def probe_sport(sport: str) -> CapabilityProbe:
    """Inspect one sport's implementation chain by import, never by documentation."""
    normalized = normalize_team_event_sport(sport)
    chain = _DECLARED_CHAINS.get(normalized)
    if chain is not None:
        return _probe_chain(normalized, chain)
    return CapabilityProbe(
        sport=normalized,
        fitted_module=None,
        adapter_module=None,
        scorer_symbol=None,
        adapter_importable=False,
        scorer_resolvable=False,
        model_artifact_loader=None,
        notes=_KNOWN_PARTIAL_WORK.get(
            normalized, "No fitted team/event winner model, adapter or scorer in the repository."
        ),
    )


def resolve_state(
    sport: str,
    *,
    registered: bool,
    probe: CapabilityProbe | None = None,
    disabled: bool = False,
) -> str:
    """Map a sport onto exactly one resolver state.

    The states are ordered by what an engineer would have to do next: a
    registered bridge is done, a disabled one needs an operator, an importable
    adapter that is not registered needs wiring, and everything else needs a
    model-development project.
    """
    if disabled:
        return DISABLED
    if registered:
        return REGISTERED
    probe = probe or probe_sport(sport)
    if probe.scorer_resolvable:
        return UNREGISTERED
    if probe.adapter_module and not probe.adapter_importable:
        return ADAPTER_MISSING
    if probe.fitted_module and not probe.scorer_resolvable:
        return MODEL_ARTIFACT_MISSING
    return ADAPTER_MISSING


def audit_table(is_registered: Callable[[str], bool] | None = None) -> list[dict[str, Any]]:
    """Repository audit across the declared discovery universe.

    Returns one row per sport describing what exists, what is wired, and — when
    a sport is not registered — why not. This is the evidence behind every
    ``MODEL_UNAVAILABLE`` the cross-sport lane emits.
    """
    rows: list[dict[str, Any]] = []
    for sport in EXPECTED_TEAM_EVENT_SPORTS:
        probe = probe_sport(sport)
        registered = bool(is_registered(sport)) if is_registered is not None else False
        state = resolve_state(sport, registered=registered, probe=probe)
        rows.append(
            {
                **probe.as_dict(),
                "registry_state": state,
                "registered_capability": registered,
                "safe_to_register": bool(probe.scorer_resolvable),
                "reason_if_not_registered": None if registered else probe.notes,
                "probability_publishable": False,
                "can_execute": False,
            }
        )
    return rows


__all__ = [
    "ADAPTER_MISSING",
    "CAN_EXECUTE",
    "CapabilityProbe",
    "DISABLED",
    "MODEL_ARTIFACT_MISSING",
    "REGISTERED",
    "RESOLVER_STATES",
    "UNREGISTERED",
    "audit_table",
    "probe_sport",
    "resolve_state",
]
