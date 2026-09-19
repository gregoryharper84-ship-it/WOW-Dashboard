"""Explicit resolver states and repository audit for team/event model coverage.

The audit separates implementation, registration, and certification. A sport can
have numerical code without being registered, and registration alone never
certifies it. Only exact importable scorer chains are reported as implementation
coverage.
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

REGISTERED = "REGISTERED"
UNREGISTERED = "UNREGISTERED"
DISABLED = "DISABLED"
MODEL_ARTIFACT_MISSING = "MODEL_ARTIFACT_MISSING"
ADAPTER_MISSING = "ADAPTER_MISSING"
RESOLVER_STATES = (
    REGISTERED,
    UNREGISTERED,
    DISABLED,
    MODEL_ARTIFACT_MISSING,
    ADAPTER_MISSING,
)

CERTIFIED = "CERTIFIED"
CANDIDATE_REGISTERED_UNCERTIFIED = "CANDIDATE_REGISTERED_UNCERTIFIED"
NOT_CERTIFIED = "NOT_CERTIFIED"
CERTIFICATION_STATES = (
    CERTIFIED,
    CANDIDATE_REGISTERED_UNCERTIFIED,
    NOT_CERTIFIED,
)


def certification_state(
    sport: str,
    *,
    registered: bool,
) -> tuple[str, str | None]:
    """Certification status/id independent of bridge registration."""
    from v17.team_event_capability_manifest import CERTIFIED_TEAM_EVENT_SPORTS

    normalized = normalize_team_event_sport(sport)
    certification_id = CERTIFIED_TEAM_EVENT_SPORTS.get(normalized)
    if certification_id:
        return CERTIFIED, certification_id
    if registered:
        return CANDIDATE_REGISTERED_UNCERTIFIED, None
    return NOT_CERTIFIED, None


@dataclass(frozen=True)
class CapabilityProbe:
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
            "Fitted NFL outright-win bundle with Platt calibration and governed "
            "publication bridge. Champion promotion remains a runtime/database condition."
        ),
    },
    "WNBA": {
        "fitted_module": "v17.multisport_team_event_models",
        "adapter_module": "v17.multisport_team_event_bridges",
        "scorer_symbol": "score_wnba_team_event_request",
        "model_artifact_loader": "v17.multisport_team_event_models:score_wnba_team_event",
        "notes": "WNBA Bradley-Terry specialist with V17 dynamic uncertainty and terminal governance.",
    },
    "NHL": {
        "fitted_module": "v17.multisport_team_event_models",
        "adapter_module": "v17.multisport_team_event_bridges",
        "scorer_symbol": "score_nhl_team_event_request",
        "model_artifact_loader": "v17.multisport_team_event_models:score_nhl_team_event",
        "notes": "NHL Elo + goalie/special-teams/OT simulation with governed bounds.",
    },
    "SOCCER": {
        "fitted_module": "v17.multisport_team_event_models",
        "adapter_module": "v17.multisport_team_event_bridges",
        "scorer_symbol": "score_soccer_team_event_request",
        "model_artifact_loader": "v17.multisport_team_event_models:score_soccer_team_event",
        "notes": "Soccer independent-Poisson three-state 1X2 specialist; draw is never collapsed into binary.",
    },
    "TENNIS": {
        "fitted_module": "v17.multisport_team_event_models",
        "adapter_module": "v17.multisport_team_event_bridges",
        "scorer_symbol": "score_tennis_team_event_request",
        "model_artifact_loader": "v17.multisport_team_event_models:score_tennis_team_event",
        "notes": "Tennis surface/form, Elo, hold-rate, H2H specialist hierarchy with retirement-aware input contract.",
    },
    "MMA": {
        "fitted_module": "v17.multisport_team_event_models",
        "adapter_module": "v17.multisport_team_event_bridges",
        "scorer_symbol": "score_mma_team_event_request",
        "model_artifact_loader": "v17.multisport_team_event_models:score_mma_team_event",
        "notes": "MMA internally fitted chronological fight-ledger Elo specialist; no sportsbook or generic-probability substitution.",
    },
}

_KNOWN_PARTIAL_WORK: dict[str, str] = {
    "NCAAF": (
        "Fitted-artifact provider (ncaaf_fitted_provider), logistic model-family adapter and "
        "trust layer exist, but there is no team/event winner scorer entry point, no governed "
        "probability-package mapping and no event-governor binding."
    ),
    "NBA": "No certified V17 team/event winner bridge in the repository.",
    "NCAAB": "No fitted team/event winner model, adapter or scorer in the repository.",
    "PGA": "No calibrated field-distribution or head-to-head model, adapter or scorer.",
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
            normalized,
            "No fitted team/event winner model, adapter or scorer in the repository.",
        ),
    )


def resolve_state(
    sport: str,
    *,
    registered: bool,
    probe: CapabilityProbe | None = None,
    disabled: bool = False,
) -> str:
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


def audit_table(
    is_registered: Callable[[str], bool] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sport in EXPECTED_TEAM_EVENT_SPORTS:
        probe = probe_sport(sport)
        registered = (
            bool(is_registered(sport)) if is_registered is not None else False
        )
        state = resolve_state(sport, registered=registered, probe=probe)
        certification, certification_id = certification_state(
            sport, registered=registered
        )
        rows.append(
            {
                **probe.as_dict(),
                "registry_state": state,
                "registered_capability": registered,
                "certification_status": certification,
                "certification_id": certification_id,
                "safe_to_register": bool(probe.scorer_resolvable),
                "reason_if_not_registered": None if registered else probe.notes,
                "probability_publishable": False,
                "can_execute": False,
            }
        )
    return rows


__all__ = [
    "ADAPTER_MISSING",
    "CANDIDATE_REGISTERED_UNCERTIFIED",
    "CAN_EXECUTE",
    "CERTIFICATION_STATES",
    "CERTIFIED",
    "NOT_CERTIFIED",
    "certification_state",
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
