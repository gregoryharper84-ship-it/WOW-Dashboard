"""V17 Full Model card-admission/capability preflight.

Prevents discovery research, sportsbook probabilities, third-party projections,
or manually reconstructed probabilities from entering governed Full Model cards.
Only a controlling backend scorer may populate governed probability fields.
This module never emits FINAL_APPROVED and never authorizes execution.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite
from typing import Any, Iterable, Mapping

CAN_EXECUTE = False
PROP_LANE = "PROP"
TEAM_EVENT_LANE = "TEAM_EVENT_ML"
SUPPORTED_LANES = {PROP_LANE, TEAM_EVENT_LANE}
TEST_ONLY_STATES = {"TEST_ONLY", "NCAAF_TEST_ONLY", "RESEARCH_ONLY", "UNSUPPORTED", "MODEL_UNAVAILABLE"}
TYPED_SCORER_FAILURES = {
    "MODEL_UNAVAILABLE", "MODEL_INPUTS_INSUFFICIENT", "MODEL_SCORER_FAILED",
    "MODEL_OUTPUT_INVALID", "CERTIFIED_ADAPTER_UNAVAILABLE",
    "COMPUTATION_VERIFICATION_FAILED", "COMPUTATION_VERIFICATION_CONFLICT",
}
REJECTING_TERMINALS = {
    "MODEL_UNAVAILABLE", "MODEL_INPUTS_INSUFFICIENT", "MODEL_SCORER_FAILED",
    "MODEL_OUTPUT_INVALID", "NO_PLAY", "REJECT_DATA_QUALITY", "REJECT_BAD_STRUCTURE",
    "SLATE_PURGE", "DUPLICATE_EXPOSURE_BLOCK",
}
EXTERNAL_RESEARCH_KEYS = {
    "external_probability", "external_projection", "external_lower_bound",
    "market_probability", "implied_probability", "no_vig_probability",
    "recent_hit_rate", "research_probability",
}


def _norm(value: Any) -> str:
    return str(value or "").strip().upper()


def _candidate_id(candidate: Mapping[str, Any]) -> str:
    return str(candidate.get("candidate_id") or candidate.get("row_id") or candidate.get("prediction_id") or "").strip()


def _lane(candidate: Mapping[str, Any]) -> str:
    raw = _norm(candidate.get("lane") or candidate.get("route") or candidate.get("market_lane"))
    aliases = {
        "PROP": PROP_LANE, "PROPS": PROP_LANE, "PLAYER_PROP": PROP_LANE, "PLAYER_PROPS": PROP_LANE,
        "TEAM_EVENT": TEAM_EVENT_LANE, "TEAM_EVENT_ML": TEAM_EVENT_LANE, "MONEYLINE": TEAM_EVENT_LANE,
        "ML": TEAM_EVENT_LANE, "OUTRIGHT_WINNER": TEAM_EVENT_LANE,
    }
    return aliases.get(raw, raw)


def _prob(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    if not isfinite(value) or not 0.0 <= value <= 1.0:
        return None
    return value


def _bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _blockers(payload: Mapping[str, Any] | None) -> list[str]:
    if not payload:
        return []
    values: list[str] = []
    for key in ("blockers", "readiness_reasons", "reasons", "failure_reasons"):
        raw = payload.get(key)
        if isinstance(raw, str) and raw.strip():
            values.append(raw.strip())
        elif isinstance(raw, Iterable) and not isinstance(raw, (str, bytes, Mapping)):
            values.extend(str(item).strip() for item in raw if str(item).strip())
    return list(dict.fromkeys(values))


def _typed_failure(payload: Mapping[str, Any] | None) -> str | None:
    if not payload:
        return None
    for item in (
        payload.get("typed_failure"), payload.get("failure"), payload.get("failure_class"),
        payload.get("probability_claim_status"), payload.get("terminal_status"),
        payload.get("terminal_label"), payload.get("status"),
    ):
        value = _norm(item)
        if value in TYPED_SCORER_FAILURES:
            return value
    return None


def _trust_state(readiness: Mapping[str, Any]) -> str:
    for key in ("lane_status", "trust_state", "ncaaf_trust_state", "model_status"):
        value = _norm(readiness.get(key))
        if value:
            return value
    return "UNKNOWN"


def _route_supported(readiness: Mapping[str, Any]) -> bool | None:
    for key in ("route_supported", "model_capability_available", "capability_available"):
        value = _bool(readiness.get(key))
        if value is not None:
            return value
    capability = _norm(readiness.get("ncaaf_controlling_model") or readiness.get("model_capability") or readiness.get("capability_status"))
    if capability in {"AVAILABLE", "MODEL_CAPABILITY_AVAILABLE", "READY"}:
        return True
    if capability in {"UNAVAILABLE", "MODEL_UNAVAILABLE", "UNSUPPORTED"}:
        return False
    return None


def _governed_package(scorer: Mapping[str, Any] | None) -> dict[str, Any]:
    if not scorer:
        return {}
    raw = _prob(scorer.get("model_probability"))
    if raw is None:
        raw = _prob(scorer.get("raw_model_probability"))
    if raw is None:
        raw = _prob(scorer.get("raw_probability"))
    unconditional = _prob(scorer.get("unconditional_probability"))
    if unconditional is None:
        unconditional = raw
    return {
        "model_probability": raw,
        "unconditional_probability": unconditional,
        "calibrated_probability": _prob(scorer.get("calibrated_probability")),
        "calibrated_lower_bound": _prob(scorer.get("calibrated_lower_bound")),
        "calibrated_upper_bound": _prob(scorer.get("calibrated_upper_bound")),
        "rank_eligible": _bool(scorer.get("rank_eligible")),
        "model_qualified": _bool(scorer.get("model_qualified")),
        "probability_publishable": _bool(scorer.get("probability_publishable")),
        "terminal_label": str(scorer.get("terminal_label") or scorer.get("terminal_status") or "").strip(),
        "market_status": str(scorer.get("market_status") or scorer.get("market_gate") or "").strip(),
    }


def _valid_rank_package(package: Mapping[str, Any]) -> bool:
    calibrated = _prob(package.get("calibrated_probability"))
    lower = _prob(package.get("calibrated_lower_bound"))
    upper = _prob(package.get("calibrated_upper_bound"))
    if calibrated is None or lower is None or lower > calibrated:
        return False
    if upper is not None and calibrated > upper:
        return False
    if package.get("rank_eligible") is not True:
        return False
    return _norm(package.get("terminal_label")) not in REJECTING_TERMINALS


def _sporting_probability_preserved(package: Mapping[str, Any]) -> bool:
    return any(_prob(package.get(key)) is not None for key in ("model_probability", "unconditional_probability", "calibrated_probability"))


@dataclass(frozen=True)
class CardAdmissionDecision:
    candidate_id: str
    lane: str
    sport: str
    controlling_specialist: str
    eligible_for_governed_scoring: bool
    admitted_to_probability_card: bool
    sporting_probability_preserved: bool
    governed_probability_package: dict[str, Any]
    terminal_label: str
    typed_failure: str | None
    blockers: tuple[str, ...]
    market_status: str | None
    external_research_present: bool
    can_execute: bool = CAN_EXECUTE

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CardAdmissionBatch:
    decisions: tuple[CardAdmissionDecision, ...]
    admitted_candidate_ids: tuple[str, ...]
    excluded_candidate_ids: tuple[str, ...]
    rows_in: int
    rows_admitted: int
    rows_excluded: int
    can_execute: bool = CAN_EXECUTE

    def as_dict(self) -> dict[str, Any]:
        return {
            "decisions": [decision.as_dict() for decision in self.decisions],
            "admitted_candidate_ids": list(self.admitted_candidate_ids),
            "excluded_candidate_ids": list(self.excluded_candidate_ids),
            "rows_in": self.rows_in,
            "rows_admitted": self.rows_admitted,
            "rows_excluded": self.rows_excluded,
            "can_execute": False,
        }


def admit_full_model_candidate(candidate: Mapping[str, Any], *, readiness: Mapping[str, Any] | None, scorer_result: Mapping[str, Any] | None) -> CardAdmissionDecision:
    readiness = readiness or {}
    candidate_id = _candidate_id(candidate)
    lane = _lane(candidate)
    sport = _norm(candidate.get("sport"))
    specialist = str(candidate.get("controlling_specialist") or readiness.get("controlling_specialist") or "").strip()
    blockers = _blockers(readiness)
    external_present = any(candidate.get(key) is not None for key in EXTERNAL_RESEARCH_KEYS)

    if not candidate_id:
        blockers.append("CANDIDATE_ID_UNRESOLVED")
    if lane not in SUPPORTED_LANES:
        blockers.append("NO_CONTROLLING_SPECIALIST_ROUTE")
    if not specialist:
        blockers.append("CONTROLLING_SPECIALIST_UNRESOLVED")

    trust_state = _trust_state(readiness)
    route_supported = _route_supported(readiness)
    readiness_failure = _typed_failure(readiness)
    if trust_state in TEST_ONLY_STATES:
        blockers.append(trust_state)
    if route_supported is False:
        blockers.append("ROUTE_CAPABILITY_UNAVAILABLE")
    if readiness.get("probability_publishable") is False:
        blockers.append("PROBABILITY_PUBLICATION_BLOCKED")

    eligible_for_scoring = bool(candidate_id) and lane in SUPPORTED_LANES and bool(specialist) and trust_state not in TEST_ONLY_STATES and route_supported is not False
    package = _governed_package(scorer_result)
    scorer_failure = _typed_failure(scorer_result)
    typed_failure = scorer_failure or readiness_failure

    if not scorer_result:
        blockers.append("LIVE_GPT_ACTION_INVOCATION_BLOCKED")
        typed_failure = typed_failure or "LIVE_GPT_ACTION_INVOCATION_BLOCKED"
    elif scorer_failure:
        blockers.append(scorer_failure)

    sporting_preserved = _sporting_probability_preserved(package)
    valid_rank_package = _valid_rank_package(package)
    if package and not valid_rank_package:
        if package.get("calibrated_lower_bound") is None:
            blockers.append("CALIBRATED_LOWER_BOUND_UNAVAILABLE")
        if package.get("rank_eligible") is not True:
            blockers.append("RANK_ELIGIBILITY_BLOCKED")

    admitted = eligible_for_scoring and scorer_failure is None and valid_rank_package
    terminal = str(package.get("terminal_label") or "").strip()
    if admitted:
        terminal = terminal or "MODEL_QUALIFIED_HOLD"
    elif scorer_failure:
        terminal = scorer_failure
    elif readiness_failure:
        terminal = readiness_failure
    elif trust_state in TEST_ONLY_STATES or route_supported is False:
        terminal = "MODEL_UNAVAILABLE"
    elif not scorer_result:
        terminal = "LIVE_GPT_ACTION_INVOCATION_BLOCKED"
    else:
        terminal = terminal or "MODEL_QUALIFIED_HOLD"

    market_status = str(package.get("market_status") or readiness.get("market_status") or "").strip() or None
    return CardAdmissionDecision(
        candidate_id=candidate_id, lane=lane, sport=sport, controlling_specialist=specialist,
        eligible_for_governed_scoring=eligible_for_scoring, admitted_to_probability_card=admitted,
        sporting_probability_preserved=sporting_preserved, governed_probability_package=package,
        terminal_label=terminal, typed_failure=typed_failure, blockers=tuple(dict.fromkeys(blockers)),
        market_status=market_status, external_research_present=external_present, can_execute=False,
    )


def admit_full_model_card(candidates: Iterable[Mapping[str, Any]], *, readiness_by_candidate: Mapping[str, Mapping[str, Any]], scorer_results_by_candidate: Mapping[str, Mapping[str, Any]]) -> CardAdmissionBatch:
    decisions: list[CardAdmissionDecision] = []
    seen: set[str] = set()
    for candidate in candidates:
        candidate_id = _candidate_id(candidate)
        if candidate_id in seen and candidate_id:
            raise ValueError(f"DUPLICATE_CANDIDATE_ID:{candidate_id}")
        if candidate_id:
            seen.add(candidate_id)
        decisions.append(admit_full_model_candidate(
            candidate,
            readiness=readiness_by_candidate.get(candidate_id, {}),
            scorer_result=scorer_results_by_candidate.get(candidate_id),
        ))
    admitted = tuple(d.candidate_id for d in decisions if d.admitted_to_probability_card)
    excluded = tuple(d.candidate_id for d in decisions if not d.admitted_to_probability_card)
    rows_in = len(decisions)
    if rows_in != len(admitted) + len(excluded):
        raise ValueError("FULL_MODEL_CARD_RECONCILIATION_FAILED")
    return CardAdmissionBatch(tuple(decisions), admitted, excluded, rows_in, len(admitted), len(excluded), False)


__all__ = [
    "CAN_EXECUTE", "CardAdmissionBatch", "CardAdmissionDecision", "EXTERNAL_RESEARCH_KEYS",
    "PROP_LANE", "TEAM_EVENT_LANE", "admit_full_model_candidate", "admit_full_model_card",
]
