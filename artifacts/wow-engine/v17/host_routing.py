"""WOW V17 active-generation host/lane routing contract.

Pure deterministic routing and host-side Full Model Action receipt validation.
This module never scores probabilities, never sets the global terminal label,
and can never execute a wager.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CAN_EXECUTE = False

WOW_BETTING_ENGINE = "WOW_BETTING_ENGINE"
LLP_TEAM_BETTING_ENGINE = "LLP_TEAM_BETTING_ENGINE"
KALSHI_WEATHER_MARKET_EXPERT = "KALSHI_WEATHER_MARKET_EXPERT"
PROJECT_CHAT = "PROJECT_CHAT"

LIVE_GPT_ACTION_INVOCATION_BLOCKED = "LIVE_GPT_ACTION_INVOCATION_BLOCKED"
LIVE_GPT_ACTION_RESULT_INVALID = "LIVE_GPT_ACTION_RESULT_INVALID"

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
WEATHER_FAMILIES = frozenset(
    {
        "DAILY_HIGH_TEMPERATURE",
        "DAILY_LOW_TEMPERATURE",
        "HOURLY_TEMPERATURE",
        "PRECIPITATION_DAILY",
        "PRECIPITATION_MONTHLY",
        "HURRICANE_TROPICAL",
        "CLIMATE_RECORD",
        "HEATWAVE_EXTREME",
        "SNOW_SKI_RESORT",
        "NATURAL_DISASTER_WEATHER",
        "OTHER_WEATHER",
    }
)


@dataclass(frozen=True)
class HostRoute:
    requester_host_identity: str
    candidate_family: str
    controlling_engine_identity: str
    global_terminal_authority: bool = False
    can_execute: bool = False


@dataclass(frozen=True)
class FullModelActionReceipt:
    """Host-side proof that a governed Full Model Action was actually attempted.

    The receipt does not create or reinterpret a probability. It only prevents
    research/discovery output from being mislabeled as a completed Full Model
    pass. Backend terminal semantics remain authoritative.

    ``backend_specialist_scoring_attempted`` is the backend's own statement of
    whether the controlling specialist scorer ran. It is a different fact from
    whether the host invoked the Action, and the two are never merged.
    """

    candidate_family: str
    action_invoked: bool
    operation_id: str | None = None
    backend_terminal_status: str | None = None
    backend_model_capability: str | None = None
    request_id: str | None = None
    run_id: str | None = None
    http_result: int | None = None
    exact_error: Any = None
    backend_specialist_scoring_attempted: bool | None = None


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
        "WEATHER": "OTHER_WEATHER",
        "DAILY_HIGH": "DAILY_HIGH_TEMPERATURE",
        "DAILY_LOW": "DAILY_LOW_TEMPERATURE",
        "HOURLY_WEATHER": "HOURLY_TEMPERATURE",
        "HOURLY_WEATHER_INDEX": "HOURLY_TEMPERATURE",
    }
    return aliases.get(family, family)


def controlling_engine_for(candidate_family: str) -> str:
    family = normalize_candidate_family(candidate_family)
    if family in PROP_FAMILIES:
        return WOW_BETTING_ENGINE
    if family in TEAM_EVENT_FAMILIES:
        return LLP_TEAM_BETTING_ENGINE
    if family in WEATHER_FAMILIES:
        return KALSHI_WEATHER_MARKET_EXPERT
    raise ValueError("CANDIDATE_FAMILY_UNSUPPORTED")


def expected_full_model_operation_id(candidate_family: str) -> str:
    """Return the current live Custom GPT Action operation for a Full Model row."""
    family = normalize_candidate_family(candidate_family)
    if family in PROP_FAMILIES:
        return "scoreWowPickRequest"
    if family in TEAM_EVENT_FAMILIES:
        return "scoreWowTeamEventRequest"
    if family in WEATHER_FAMILIES:
        return "analyzeKalshiWeatherV17Contract"
    raise ValueError("CANDIDATE_FAMILY_UNSUPPORTED")


def accepted_full_model_operation_ids(candidate_family: str) -> frozenset[str]:
    """Accept the live operation ID plus the pre-sync V17 alias during cutover.

    This is naming compatibility only. Both names identify the same governed
    lane and neither changes probability, terminal, or execution semantics.
    """
    family = normalize_candidate_family(candidate_family)
    canonical = expected_full_model_operation_id(family)
    if family in PROP_FAMILIES:
        return frozenset({canonical, "scoreWowV17PickRequest"})
    if family in TEAM_EVENT_FAMILIES:
        return frozenset({canonical, "scoreWowV17TeamEventFromWowHost"})
    return frozenset({canonical})


def _specialist_scoring_attempted(receipt: FullModelActionReceipt) -> bool | str:
    """Report the backend scorer fact separately, never inferred from the host."""
    if receipt.backend_specialist_scoring_attempted is None:
        return "UNKNOWN"
    return bool(receipt.backend_specialist_scoring_attempted)


def validate_full_model_action_receipt(receipt: FullModelActionReceipt) -> dict[str, Any]:
    """Fail closed unless a Full Model request has a concrete Action receipt.

    Two distinct facts are reported under two distinct names, because conflating
    them made a receipt read as "no Action call occurred" when the Action had in
    fact been invoked and only the specialist scorer had not run:

    - ``action_invocation_attempted`` — the controlling host contract's fact:
      did a required Action call occur at all. ``scoring_attempted`` is retained
      as its contract-compatible alias and keeps exactly this meaning. Once an
      Action call is made it stays true even when auth, transport, schema,
      input, scorer, or backend validation fails.
    - ``specialist_scoring_attempted`` — the backend's fact: did the controlling
      specialist scorer run. ``UNKNOWN`` when the backend did not report it;
      it is never inferred from host-side invocation.

    Backend statuses are preserved verbatim.
    """
    family = normalize_candidate_family(receipt.candidate_family)
    expected_operation = expected_full_model_operation_id(family)
    accepted_operations = accepted_full_model_operation_ids(family)
    specialist_scoring_attempted = _specialist_scoring_attempted(receipt)

    if not receipt.action_invoked:
        return {
            "status": LIVE_GPT_ACTION_INVOCATION_BLOCKED,
            "candidate_family": family,
            "expected_operation_id": expected_operation,
            "operation_id": None,
            "action_invocation_attempted": False,
            "specialist_scoring_attempted": specialist_scoring_attempted,
            "scoring_attempted": False,
            "backend_model_capability": "UNKNOWN",
            "backend_terminal_status": None,
            "rank_eligible": False,
            "full_model_completed": False,
            "request_id": receipt.request_id,
            "run_id": receipt.run_id,
            "http_result": receipt.http_result,
            "exact_error": receipt.exact_error,
            "can_execute": False,
        }

    operation = str(receipt.operation_id or "").strip()
    if operation not in accepted_operations:
        return {
            "status": LIVE_GPT_ACTION_RESULT_INVALID,
            "candidate_family": family,
            "expected_operation_id": expected_operation,
            "operation_id": operation or None,
            "action_invocation_attempted": True,
            "specialist_scoring_attempted": specialist_scoring_attempted,
            "scoring_attempted": True,
            "backend_model_capability": receipt.backend_model_capability or "UNKNOWN",
            "backend_terminal_status": receipt.backend_terminal_status,
            "rank_eligible": False,
            "full_model_completed": False,
            "request_id": receipt.request_id,
            "run_id": receipt.run_id,
            "http_result": receipt.http_result,
            "exact_error": receipt.exact_error or "CANONICAL_OPERATION_ID_MISMATCH",
            "can_execute": False,
        }

    return {
        "status": receipt.backend_terminal_status or LIVE_GPT_ACTION_RESULT_INVALID,
        "candidate_family": family,
        "expected_operation_id": expected_operation,
        "operation_id": operation,
        "action_invocation_attempted": True,
        "specialist_scoring_attempted": specialist_scoring_attempted,
        "scoring_attempted": True,
        "backend_model_capability": receipt.backend_model_capability or "UNKNOWN",
        "backend_terminal_status": receipt.backend_terminal_status,
        "rank_eligible": False,
        "full_model_completed": receipt.backend_terminal_status is not None,
        "request_id": receipt.request_id,
        "run_id": receipt.run_id,
        "http_result": receipt.http_result,
        "exact_error": receipt.exact_error,
        "can_execute": False,
    }


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
