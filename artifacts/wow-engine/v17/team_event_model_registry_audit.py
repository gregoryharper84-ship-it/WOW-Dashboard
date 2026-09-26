"""Explicit resolver states and repository audit for team/event model coverage.

The audit separates numerical implementation, bridge registration, fitted-artifact
presence, and governed certification. A sport can have useful model code without
being registered; it can be registered without being fitted-artifact certified;
and neither state may be promoted merely because an exact scorer imports.
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
    """Return repository/runtime certification without self-certifying bridges.

    Static certification comes from the governed catalog. A live bridge may be
    registered for research, hydration, shadow validation, or a runtime-gated
    champion path, but registration/importability alone is not fitted-model
    certification. Newly promoted sports must receive an explicit immutable
    certification receipt and then be admitted to the governed catalog/runtime
    proof path in a separate reviewed change.
    """
    from v17.team_event_capability_manifest import CERTIFIED_TEAM_EVENT_SPORTS

    normalized = normalize_team_event_sport(sport)
    static_certification_id = CERTIFIED_TEAM_EVENT_SPORTS.get(normalized)
    if static_certification_id:
        return CERTIFIED, static_certification_id
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


_DECLARED_CHAINS: dict[str, dict[str, Any]] = {
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
    # These implementations remain useful research/shadow scorers, but they are
    # deliberately not advertised as fitted production artifacts. Their fitted
    # candidate pipelines and certification work are tracked separately below.
    "WNBA": {
        "adapter_module": "v17.multisport_team_event_bridges",
        "scorer_symbol": "score_wnba_team_event_request",
        "notes": (
            "Numerical WNBA bridge is importable, but its Bradley-Terry raw path is not "
            "the fitted basketball artifact pipeline and cannot self-certify."
        ),
    },
    "NHL": {
        "adapter_module": "v17.multisport_team_event_bridges",
        "scorer_symbol": "score_nhl_team_event_request",
        "notes": (
            "Numerical NHL Elo/goalie/special-teams bridge is importable; fitted candidate "
            "artifacts exist separately and require replay/promotion before publication."
        ),
    },
    "SOCCER": {
        "adapter_module": "v17.multisport_team_event_bridges",
        "scorer_symbol": "score_soccer_team_event_request",
        "notes": (
            "Numerical soccer 1X2 bridge is importable; competition-scoped fitted multinomial "
            "candidate artifacts require three-way replay/promotion before publication."
        ),
    },
    "TENNIS": {
        "adapter_module": "v17.multisport_team_event_bridges",
        "scorer_symbol": "score_tennis_team_event_request",
        "notes": (
            "Numerical tennis specialist hierarchy is importable; fitted tour/surface candidates "
            "require retirement-aware replay/promotion before publication."
        ),
    },
    "MMA": {
        "adapter_module": "v17.multisport_team_event_bridges",
        "scorer_symbol": "score_mma_team_event_request",
        "notes": (
            "MMA chronological fight-ledger Elo is an event-time fitted calculation, not a "
            "persisted certified artifact. Historical fitted-artifact lifecycle remains required."
        ),
    },
}

_KNOWN_PARTIAL_WORK: dict[str, str] = {
    "NCAAF": (
        "Fitted-artifact provider (ncaaf_fitted_provider), logistic model-family adapter, live "
        "feature snapshots and static calibrator registry exist, but the team/event publication "
        "bridge still lacks the complete failure-path/dynamic-bound/governor chain."
    ),
    "NBA": (
        "NBA has an independent fitted basketball candidate/training/calibration pipeline and a "
        "certified-artifact RPC, but no complete current-pregame feature hydration + publication bridge."
    ),
    "NCAAB": (
        "NCAAB has a fitted SportsDataverse prior-form logistic candidate with chronological "
        "calibration/test partitions, but it remains immutable CANDIDATE evidence pending replay, "
        "promotion, current-event feature hydration and a governed publication bridge."
    ),
    "PGA": "No calibrated field-distribution or head-to-head fitted model, adapter or scorer.",
    "BOXING": "No fitted fight-winner specialist, adapter or scorer in the repository.",
}


def _probe_chain(sport: str, chain: dict[str, Any]) -> CapabilityProbe:
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
                # scorer_resolvable is a live-routing field. An importable but
                # unregistered implementation remains unavailable to this GPT;
                # implementation_scorer_resolvable preserves the engineering
                # signal without falsely advertising a callable production lane.
                "scorer_resolvable": bool(probe.scorer_resolvable and registered),
                "implementation_scorer_resolvable": bool(probe.scorer_resolvable),
                "registry_state": state,
                "registered_capability": registered,
                "certification_status": certification,
                "certification_id": certification_id,
                "fitted_artifact_backed": bool(probe.fitted_module and probe.model_artifact_loader),
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
