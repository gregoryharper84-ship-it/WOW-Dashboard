"""Machine-readable contract for the WOW V17 nightly engineering team.

This module contains engineering workflow metadata only. It does not call a
sporting model, market, betting, credential, database, or deployment API.
"""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

TEAM_CONTRACT_VERSION = "1.0"

ROLES = {
    "REPORTER_AGENT",
    "RESEARCH_TRIAGE_AGENT",
    "ENGINEERING_AGENT",
    "INDEPENDENT_REVIEW_AGENT",
    "QA_VERIFICATION_AGENT",
    "RELEASE_OBSERVABILITY_AGENT",
    "SYSTEM_ARCHITECT_AGENT",
}

WORKFLOW_STAGES = {
    "REPORTER_INTAKE",
    "RESEARCH_TRIAGE",
    "ENGINEERING",
    "INDEPENDENT_REVIEW",
    "QA_VERIFICATION",
    "RELEASE_OBSERVABILITY",
    "REPORTER_CLOSURE",
}

STAGE_OWNER = {
    "REPORTER_INTAKE": "REPORTER_AGENT",
    "RESEARCH_TRIAGE": "RESEARCH_TRIAGE_AGENT",
    "ENGINEERING": "ENGINEERING_AGENT",
    "INDEPENDENT_REVIEW": "INDEPENDENT_REVIEW_AGENT",
    "QA_VERIFICATION": "QA_VERIFICATION_AGENT",
    "RELEASE_OBSERVABILITY": "RELEASE_OBSERVABILITY_AGENT",
    "REPORTER_CLOSURE": "REPORTER_AGENT",
}

LEGAL_STAGE_TRANSITIONS = {
    "REPORTER_INTAKE": {"RESEARCH_TRIAGE", "REPORTER_CLOSURE"},
    "RESEARCH_TRIAGE": {"ENGINEERING", "REPORTER_CLOSURE"},
    "ENGINEERING": {"INDEPENDENT_REVIEW", "REPORTER_CLOSURE"},
    "INDEPENDENT_REVIEW": {"ENGINEERING", "QA_VERIFICATION", "REPORTER_CLOSURE"},
    "QA_VERIFICATION": {"ENGINEERING", "RELEASE_OBSERVABILITY", "REPORTER_CLOSURE"},
    "RELEASE_OBSERVABILITY": {"ENGINEERING", "REPORTER_CLOSURE"},
    "REPORTER_CLOSURE": set(),
}

SUBSYSTEMS = {
    "WOW_HOST_ORCHESTRATION",
    "WOW_PROP_ENGINE",
    "LLP_TEAM_EVENT_ENGINE",
    "KALSHI_WEATHER_ENGINE",
    "SLATE_IDENTITY",
    "FAILURE_PATH",
    "DYNAMIC_CALIBRATION",
    "EXACT_LINE_MARKET_ECONOMICS",
    "SLIP_CARD_EXPOSURE",
    "KALSHI_PORTFOLIO",
    "PERSISTENCE_POSTMORTEM",
    "FINAL_REFRESH",
    "V17_TERMINAL_REDUCER",
    "DEPLOYMENT_RUNTIME",
    "SECURITY_CREDENTIAL",
    "UNKNOWN",
}

PROTECTED_SUBSYSTEMS = {
    "WOW_HOST_ORCHESTRATION",
    "DYNAMIC_CALIBRATION",
    "PERSISTENCE_POSTMORTEM",
    "V17_TERMINAL_REDUCER",
    "SECURITY_CREDENTIAL",
}

PROTECTED_CONTRACT_KEYS = {
    "routing",
    "specialist_ownership",
    "probability_package_schema",
    "calibration_semantics",
    "publication_eligibility",
    "rank_eligibility",
    "typed_failure_semantics",
    "action_contract_meaning",
    "terminal_reducer_precedence",
    "prediction_outcome_schema",
    "persistence_grading_semantics",
    "cross_lane_interface",
    "governance_safety",
}

REPRODUCTION_STATUSES = {
    "PENDING",
    "REPRODUCED",
    "INTERMITTENT",
    "ENVIRONMENT_SPECIFIC",
    "NOT_REPRODUCED",
    "INSUFFICIENT_EVIDENCE",
    "DUPLICATE",
    "EXPECTED_BEHAVIOR",
}

CLOSURE_RELEASE_STATUSES = {"PRODUCTION_VERIFIED", "NOT_APPLICABLE"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def route_subsystem(domain: str, *, explicit: str | None = None) -> str:
    """Return a deterministic primary engineering subsystem.

    Explicit valid subsystem input always wins. Heuristics are deliberately
    engineering-oriented and never choose a sporting probability/result.
    """

    if explicit:
        value = explicit.strip().upper()
        if value not in SUBSYSTEMS:
            raise ValueError(f"invalid subsystem: {explicit}")
        return value

    text = (domain or "").strip().lower()

    if "kalshi" in text and any(k in text for k in ("portfolio", "combo", "recovery", "concentration")):
        return "KALSHI_PORTFOLIO"
    if "kalshi" in text and any(k in text for k in ("weather", "temperature", "station", "bracket")):
        return "KALSHI_WEATHER_ENGINE"
    if any(k in text for k in ("llp", "team event", "team/event", "moneyline", "winner", "upset", "match winner")):
        return "LLP_TEAM_EVENT_ENGINE"
    if any(k in text for k in ("terminal reducer", "terminal label", "terminal precedence", "terminal semantics")):
        return "V17_TERMINAL_REDUCER"
    if any(k in text for k in ("calibration", "lower bound", "rank eligibility", "publication eligibility")):
        return "DYNAMIC_CALIBRATION"
    if any(k in text for k in ("failure path", "failure-path", "unconditional probability", "regime")):
        return "FAILURE_PATH"
    if any(k in text for k in ("final refresh", "last-mile refresh")):
        return "FINAL_REFRESH"
    if any(k in text for k in ("portfolio", "combo", "concentration")) and "kalshi" in text:
        return "KALSHI_PORTFOLIO"
    if any(k in text for k in ("slip", "card", "duplicate thesis", "correlation", "session exposure", "weakest leg")):
        return "SLIP_CARD_EXPOSURE"
    if any(k in text for k in ("no-vig", "no vig", "payout", "exact line", "market economics", "push", "fee friction")):
        return "EXACT_LINE_MARKET_ECONOMICS"
    if any(k in text for k in ("slate", "wrong date", "wrong year", "event identity", "settlement identity", "timezone")):
        return "SLATE_IDENTITY"
    if any(k in text for k in ("prediction ledger", "outcome ledger", "postmortem", "persistence", "brier", "log loss", "grading")):
        return "PERSISTENCE_POSTMORTEM"
    if any(k in text for k in ("action", "host orchestration", "action receipt", "scoring receipt", "live gpt", "requester host")):
        return "WOW_HOST_ORCHESTRATION"
    if any(k in text for k in ("render", "deployment", "deploy", "runtime", "ci", "workflow", "health probe")):
        return "DEPLOYMENT_RUNTIME"
    if any(k in text for k in ("secret", "credential", "auth", "rls", "api key")):
        return "SECURITY_CREDENTIAL"
    if any(k in text for k in ("prop", "1ip", "strikeout", "pitcher", "player scalar", "player market")):
        return "WOW_PROP_ENGINE"
    return "UNKNOWN"


def architect_required(*, subsystem: str, protected_contracts: list[str] | None = None) -> bool:
    if subsystem in PROTECTED_SUBSYSTEMS:
        return True
    return bool(PROTECTED_CONTRACT_KEYS.intersection(set(protected_contracts or [])))


def initial_team_fields(*, domain: str, reported_by: str, explicit_subsystem: str | None = None) -> dict[str, Any]:
    subsystem = route_subsystem(domain, explicit=explicit_subsystem)
    return {
        "team_contract_version": TEAM_CONTRACT_VERSION,
        "primary_subsystem": subsystem,
        "secondary_subsystems": [],
        "reported_by": reported_by,
        "workflow_stage": "REPORTER_INTAKE",
        "current_owner": "REPORTER_AGENT",
        "reproduction_status": "PENDING",
        "root_cause_status": "PENDING",
        "acceptance_criteria": [],
        "handoff_history": [],
        "review": {"status": "PENDING", "system_architect_status": "PENDING"},
        "qa": {"status": "PENDING"},
        "release": {"status": "PENDING", "production_verified": False},
        "reporter_closure": {"status": "PENDING"},
        "learning": {
            "failure_category": None,
            "why_existing_tests_missed_it": None,
            "regression_test_added": None,
            "architecture_lesson": None,
            "preventive_control": None,
            "similar_code_paths_reviewed": [],
            "recurrence_of": None,
        },
    }


def transition_record(record: dict[str, Any], *, to_stage: str, evidence: str, at: str | None = None) -> dict[str, Any]:
    current = record.get("workflow_stage")
    if current not in WORKFLOW_STAGES:
        raise ValueError(f"invalid current workflow stage: {current!r}")
    if to_stage not in WORKFLOW_STAGES:
        raise ValueError(f"invalid target workflow stage: {to_stage!r}")
    if to_stage not in LEGAL_STAGE_TRANSITIONS[current]:
        raise ValueError(f"illegal workflow transition: {current} -> {to_stage}")
    if not evidence.strip():
        raise ValueError("handoff evidence is required")

    out = deepcopy(record)
    out.setdefault("handoff_history", []).append(
        {
            "from_stage": current,
            "from_role": STAGE_OWNER[current],
            "to_stage": to_stage,
            "to_role": STAGE_OWNER[to_stage],
            "timestamp_utc": at or utc_now(),
            "evidence": evidence.strip(),
        }
    )
    out["workflow_stage"] = to_stage
    out["current_owner"] = STAGE_OWNER[to_stage]
    return out


def validate_handoff_history(record: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    history = record.get("handoff_history")
    if not isinstance(history, list):
        return ["handoff_history must be a list"]

    for index, item in enumerate(history):
        if not isinstance(item, dict):
            errors.append(f"handoff_history[{index}] must be an object")
            continue
        src = item.get("from_stage")
        dst = item.get("to_stage")
        if src not in WORKFLOW_STAGES or dst not in WORKFLOW_STAGES:
            errors.append(f"handoff_history[{index}] has invalid stage")
            continue
        if dst not in LEGAL_STAGE_TRANSITIONS[src]:
            errors.append(f"handoff_history[{index}] illegal transition {src}->{dst}")
        if item.get("from_role") != STAGE_OWNER[src]:
            errors.append(f"handoff_history[{index}] wrong from_role")
        if item.get("to_role") != STAGE_OWNER[dst]:
            errors.append(f"handoff_history[{index}] wrong to_role")
        if not str(item.get("evidence") or "").strip():
            errors.append(f"handoff_history[{index}] missing evidence")
        if not str(item.get("timestamp_utc") or "").strip():
            errors.append(f"handoff_history[{index}] missing timestamp_utc")
    return errors


def validate_team_record(record: dict[str, Any], *, enforce_closure: bool = True) -> list[str]:
    """Validate a team-governed record and return all errors."""

    if record.get("legacy_imported") is True:
        return []
    if record.get("team_contract_version") != TEAM_CONTRACT_VERSION:
        return ["missing/invalid team_contract_version"]

    errors: list[str] = []
    stage = record.get("workflow_stage")
    owner = record.get("current_owner")
    subsystem = record.get("primary_subsystem")

    if stage not in WORKFLOW_STAGES:
        errors.append(f"invalid workflow_stage: {stage!r}")
    elif owner != STAGE_OWNER[stage]:
        errors.append(f"current_owner {owner!r} does not own {stage}")
    if owner not in ROLES:
        errors.append(f"invalid current_owner: {owner!r}")
    if subsystem not in SUBSYSTEMS:
        errors.append(f"invalid primary_subsystem: {subsystem!r}")
    if record.get("reproduction_status") not in REPRODUCTION_STATUSES:
        errors.append(f"invalid reproduction_status: {record.get('reproduction_status')!r}")
    if not isinstance(record.get("acceptance_criteria"), list):
        errors.append("acceptance_criteria must be a list")

    errors.extend(validate_handoff_history(record))

    if architect_required(
        subsystem=subsystem if subsystem in SUBSYSTEMS else "UNKNOWN",
        protected_contracts=record.get("protected_contracts_touched", []),
    ):
        architect_status = (record.get("review") or {}).get("system_architect_status")
        if stage in {"QA_VERIFICATION", "RELEASE_OBSERVABILITY", "REPORTER_CLOSURE"} and architect_status != "PASS":
            errors.append("system architect PASS required before QA/release/closure")

    if enforce_closure and record.get("state") == "VERIFIED_CLOSED":
        if stage != "REPORTER_CLOSURE":
            errors.append("VERIFIED_CLOSED requires REPORTER_CLOSURE stage")
        if not record.get("engineering_fix_ids"):
            errors.append("VERIFIED_CLOSED requires linked engineering fix")
        if record.get("root_cause_status") != "CONFIRMED":
            errors.append("VERIFIED_CLOSED requires root_cause_status=CONFIRMED")
        if record.get("reproduction_status") not in {"REPRODUCED", "INTERMITTENT", "ENVIRONMENT_SPECIFIC"}:
            errors.append("VERIFIED_CLOSED requires a qualifying reproduction status")
        if not record.get("acceptance_criteria"):
            errors.append("VERIFIED_CLOSED requires acceptance criteria")
        if (record.get("review") or {}).get("status") != "PASS":
            errors.append("VERIFIED_CLOSED requires independent review PASS")
        if (record.get("qa") or {}).get("status") != "PASS":
            errors.append("VERIFIED_CLOSED requires QA PASS")
        release_status = (record.get("release") or {}).get("status")
        if release_status not in CLOSURE_RELEASE_STATUSES:
            errors.append("VERIFIED_CLOSED requires production verified or explicitly not-applicable release")
        if release_status == "PRODUCTION_VERIFIED" and (record.get("release") or {}).get("production_verified") is not True:
            errors.append("PRODUCTION_VERIFIED requires production_verified=true")
        if (record.get("reporter_closure") or {}).get("status") != "FIXED_VERIFIED":
            errors.append("VERIFIED_CLOSED requires reporter FIXED_VERIFIED closure")

    return errors


def self_check() -> dict[str, Any]:
    for stage, owner in STAGE_OWNER.items():
        assert stage in WORKFLOW_STAGES
        assert owner in ROLES
    assert "V17_TERMINAL_REDUCER" in PROTECTED_SUBSYSTEMS
    assert route_subsystem("LLP moneyline scorer failure") == "LLP_TEAM_EVENT_ENGINE"
    assert route_subsystem("Kalshi weather station mapping") == "KALSHI_WEATHER_ENGINE"
    assert route_subsystem("1IP prop adapter") == "WOW_PROP_ENGINE"
    assert architect_required(subsystem="V17_TERMINAL_REDUCER")
    return {
        "team_contract_version": TEAM_CONTRACT_VERSION,
        "roles": sorted(ROLES),
        "workflow_stages": sorted(WORKFLOW_STAGES),
        "subsystems": sorted(SUBSYSTEMS),
        "can_execute": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["self-check"])
    args = parser.parse_args()
    if args.command == "self-check":
        print(json.dumps(self_check(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
