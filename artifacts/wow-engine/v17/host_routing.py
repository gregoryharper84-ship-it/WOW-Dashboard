"""WOW v17 candidate host/lane routing contract.

Pure deterministic routing only. This module never scores probabilities, never
sets the global terminal label, and can never execute a wager.
"""
from __future__ import annotations

from dataclasses import dataclass

CAN_EXECUTE = False

WOW_BETTING_ENGINE = "WOW_BETTING_ENGINE"
LLP_TEAM_BETTING_ENGINE = "LLP_TEAM_BETTING_ENGINE"
PROJECT_CHAT = "PROJECT_CHAT"

AUTHORIZED_REQUESTER_HOSTS = frozenset(
    {WOW_BETTING_ENGINE, LLP_TEAM_BETTING_ENGINE, PROJECT_CHAT}
)

PROP_FAMILIES = frozenset(
    {
        "PLAYER_PROP",
        "PLAYER_SCALAR",
        "PITCHER_PROP",
        "BATTER_PROP",
        "WORKLOAD_PROP",
    }
)
TEAM_EVENT_FAMILIES = frozenset(
    {
        "TEAM_EVENT",
        "OUTRIGHT_WINNER",
        "MONEYLINE",
        "FAVORITE",
        "UNDERDOG",
        "UPSET",
        "MATCH_WINNER",
        "FIGHT_WINNER",
    }
)


@dataclass(frozen=True)
class HostRoute:
    requester_host_identity: str
    candidate_family: str
    controlling_engine_identity: str
    global_terminal_authority: bool = False
    can_execute: bool = False


def normalize_host_identity(value: str) -> str:
    identity = str(value or "").strip().upper()
    aliases = {
        "WOW": WOW_BETTING_ENGINE,
        "WOW BETTING ENGINE": WOW_BETTING_ENGINE,
        "LLP": LLP_TEAM_BETTING_ENGINE,
        "LLP TEAM BETTING ENGINE": LLP_TEAM_BETTING_ENGINE,
        "LLP_TEAM_BETTING_MODEL": LLP_TEAM_BETTING_ENGINE,
        "WOW_LLP_TEAM_BETTING_MODEL": LLP_TEAM_BETTING_ENGINE,
        "PROJECT": PROJECT_CHAT,
    }
    identity = aliases.get(identity, identity)
    if identity not in AUTHORIZED_REQUESTER_HOSTS:
        raise ValueError("UNAUTHORIZED_WOW_REQUESTER_HOST")
    return identity


def normalize_candidate_family(value: str) -> str:
    family = str(value or "").strip().upper()
    aliases = {
        "PROP": "PLAYER_PROP",
        "PLAYER": "PLAYER_PROP",
        "ML": "MONEYLINE",
        "GAME_WINNER": "OUTRIGHT_WINNER",
        "EVENT_WINNER": "OUTRIGHT_WINNER",
    }
    return aliases.get(family, family)


def controlling_engine_for(candidate_family: str) -> str:
    family = normalize_candidate_family(candidate_family)
    if family in PROP_FAMILIES:
        return WOW_BETTING_ENGINE
    if family in TEAM_EVENT_FAMILIES:
        return LLP_TEAM_BETTING_ENGINE
    raise ValueError("CANDIDATE_FAMILY_UNSUPPORTED")


def resolve_host_route(requester_host_identity: str, candidate_family: str) -> HostRoute:
    requester = normalize_host_identity(requester_host_identity)
    family = normalize_candidate_family(candidate_family)
    controller = controlling_engine_for(family)
    return HostRoute(
        requester_host_identity=requester,
        candidate_family=family,
        controlling_engine_identity=controller,
        global_terminal_authority=False,
        can_execute=False,
    )


def host_decision_audit_fields(*, host_label: str | None, host_blockers: list[str] | None = None) -> dict:
    """Preserve host-local decisions as audit evidence, never global authority."""
    return {
        "host_terminal_label": host_label,
        "host_blockers": list(host_blockers or []),
        "host_terminal_authority": False,
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": False,
    }
