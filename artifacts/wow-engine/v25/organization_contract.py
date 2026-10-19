"""Read-only V25 organization contract checks.

This module is NOT registered as a worker, release gate, scheduler, model or
production evaluator. Until governed review and adoption it cannot confer any
agent authority. It preserves all existing V17 ownership and execution limits.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

CONTRACT_PATH = Path(__file__).with_name("team-organization-contract.json")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REQUIRED_TEAMS = frozenset({
    "PRODUCTION_ENGINEERING", "MODEL_EXCELLENCE", "RESEARCH_INNOVATION",
    "AMRT", "SYSTEMS_INTELLIGENCE_RELIABILITY", "BETTING_INTELLIGENCE",
    "INDEPENDENT_VERIFICATION",
})
ALLOWED_DISPOSITIONS = frozenset({
    "FIXED_AND_VERIFIED", "PR_CREATED", "EXPERIMENT_CREATED", "DUPLICATE",
    "NOT_REPRODUCIBLE", "BLOCKED_WITH_EXACT_REASON",
    "DEFERRED_WITH_JUSTIFICATION",
})


def load_contract() -> dict[str, Any]:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def validate_contract(spec: dict[str, Any]) -> list[str]:
    """Reject authority expansion, competing controllers and malformed roles."""
    errors: list[str] = []
    fixed = {
        "schema_version": 1,
        "status": "PROPOSED_NOT_DEPLOYED",
        "custom_gpt_identity": "WOW_BETTING_ENGINE",
        "runtime_generation": "V17_ACTIVE",
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": False,
        "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS": True,
        "shared_control_plane": "WOW_ECOSYSTEM_CONDUCTOR",
        "shared_queue": "EXISTING_CANONICAL_INCIDENT_AND_WORK_LEDGER",
        "shared_dispatcher": "EXISTING_GOVERNED_WORKER_ONLY",
        "shared_release_path": "EXISTING_PROTECTED_GITHUB_AND_RENDER_RELEASE",
    }
    for key, expected in fixed.items():
        if spec.get(key) != expected:
            errors.append(f"{key}: must remain {expected!r}")
    teams = spec.get("teams")
    agents = spec.get("agent_authorities")
    if not isinstance(teams, dict) or set(teams) != REQUIRED_TEAMS:
        return errors + ["teams: missing or unexpected team"]
    if not isinstance(agents, dict):
        return errors + ["agent_authorities: must be a mapping"]
    if teams["AMRT"].get("parent") != "PRODUCTION_ENGINEERING":
        errors.append("AMRT must remain a Production Engineering unit")
    if teams["SYSTEMS_INTELLIGENCE_RELIABILITY"].get("parent") != "SYSTEMS_INTELLIGENCE_RELIABILITY":
        errors.append("SIRT must remain independently owned")
    if teams["INDEPENDENT_VERIFICATION"].get("parent") != "INDEPENDENT_VERIFICATION":
        errors.append("Independent Verification must remain separate from implementation")
    seen: set[str] = set()
    for team, team_spec in teams.items():
        roles = team_spec.get("roles")
        if not isinstance(roles, list) or not roles:
            errors.append(f"{team}: roles must be nonempty")
            continue
        if team_spec.get("operational_status") != "PROPOSED_NOT_DEPLOYED":
            errors.append(f"{team}: cannot claim deployed status")
        for role in roles:
            if not isinstance(role, str):
                errors.append(f"{team}: nonstring role")
                continue
            if role in seen:
                errors.append(f"{role}: duplicate team membership")
            seen.add(role)
            entry = agents.get(role)
            if not isinstance(entry, dict):
                errors.append(f"{role}: missing authority entry")
                continue
            if entry.get("team") != team:
                errors.append(f"{role}: team/authority mismatch")
            for right in (
                "probability_authority", "terminal_authority",
                "safety_hold_authority", "release_approval_authority",
                "production_write_authority", "can_execute",
            ):
                if entry.get(right) is not False:
                    errors.append(f"{role}: forbidden right {right}")
            if entry.get("activation") != "DESIGN_ONLY":
                errors.append(f"{role}: activation requires governed adoption")
    if set(agents) != seen:
        errors.append("agent catalog must match exactly the team role inventory")
    route_scopes = spec.get("route_scopes", {})
    if set(route_scopes.values()) != REQUIRED_TEAMS:
        errors.append("route_scopes must cover the seven unique team domains")
    if spec.get("final_closure") != "FIXED_AND_VERIFIED":
        errors.append("final closure must require production verification")
    required = spec.get("handoff_required_fields", [])
    for field in ("work_item_id", "current_owner", "next_owner",
                  "lease_id", "lease_epoch", "lease_expires_at",
                  "accepted_by_next_owner", "next_action", "evidence_refs"):
        if field not in required:
            errors.append(f"handoff missing required field: {field}")
    if set(spec.get("nonfinal_dispositions", [])) - ALLOWED_DISPOSITIONS:
        errors.append("unknown incident disposition")
    return errors


def route_objective(spec: dict[str, Any], scope: str) -> dict[str, str]:
    """Classify scope only; does not dispatch, claim a lease or change ownership."""
    errors = validate_contract(spec)
    if errors:
        return {"state": "BLOCKED", "reason": "ORGANIZATION_CONTRACT_INVALID"}
    owner = spec["route_scopes"].get(scope)
    if owner is None:
        return {"state": "BLOCKED", "reason": "UNREGISTERED_WORK_SCOPE"}
    return {"state": "ADVISORY_ROUTE_ONLY", "team": owner}


def assess_handoff(spec: dict[str, Any], item: dict[str, Any],
                   now: datetime | None = None) -> list[str]:
    """Never assume an expired lease grants takeover; require explicit acceptance."""
    errors = validate_contract(spec)
    if errors:
        return ["ORGANIZATION_CONTRACT_INVALID"]
    for field in spec["handoff_required_fields"]:
        if field not in item:
            errors.append(f"MISSING_{field.upper()}")
    if errors:
        return errors
    agents = spec["agent_authorities"]
    if item["current_owner"] not in agents or item["next_owner"] not in agents:
        errors.append("UNKNOWN_ROLE_IDENTITY")
    if item["current_owner"] == item["next_owner"]:
        errors.append("HANDOFF_REQUIRES_DISTINCT_ROLE")
    if item["accepted_by_next_owner"] != item["next_owner"]:
        errors.append("NEXT_OWNER_NOT_ACKNOWLEDGED")
    if not isinstance(item["lease_id"], str) or not item["lease_id"].strip():
        errors.append("LEASE_ID_MISSING")
    if type(item["lease_epoch"]) is not int or item["lease_epoch"] < 1:
        errors.append("INVALID_LEASE_EPOCH")
    if not isinstance(item["evidence_refs"], list) or not item["evidence_refs"]:
        errors.append("EVIDENCE_MISSING")
    if not isinstance(item["next_action"], str) or not item["next_action"].strip():
        errors.append("NEXT_ACTION_MISSING")
    try:
        expiry = datetime.fromisoformat(item["lease_expires_at"])
        if expiry.tzinfo is None:
            errors.append("LEASE_TIMEZONE_MISSING")
        else:
            reference = now or datetime.now(timezone.utc)
            if reference.tzinfo is None or expiry <= reference:
                errors.append("LEASE_EXPIRED_FAIL_CLOSED")
    except (ValueError, TypeError):
        errors.append("LEASE_EXPIRY_INVALID")
    return errors


def assess_production_closure(spec: dict[str, Any],
                              receipt: dict[str, Any]) -> list[str]:
    """Evidence sufficiency only. NEVER grants a merge, deployment or approval."""
    errors = validate_contract(spec)
    if errors:
        return ["ORGANIZATION_CONTRACT_INVALID"]
    for field in spec["production_closure_required_fields"]:
        if not receipt.get(field):
            errors.append(f"MISSING_{field.upper()}")
    if errors:
        return errors
    sha = receipt["target_deploy_sha"]
    if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
        errors.append("TARGET_SHA_INVALID")
    if receipt["merged_sha"] != sha or receipt["live_sha"] != sha:
        errors.append("DEPLOYED_SHA_MISMATCH")
    implementer = receipt.get("implementer")
    for key in ("independent_review", "independent_qa"):
        evidence = receipt.get(key)
        if not isinstance(evidence, dict):
            errors.append(f"{key.upper()}_MALFORMED")
        elif (not evidence.get("verified") or
              evidence.get("sha") != sha or
              not evidence.get("actor") or evidence.get("actor") == implementer):
            errors.append(f"{key.upper()}_IDENTITY_OR_SHA_UNVERIFIED")
    approvals = receipt.get("required_approvals")
    if not isinstance(approvals, dict):
        errors.append("APPROVALS_MALFORMED")
    else:
        for gate in ("codeowner", "security", "qa", "release"):
            if approvals.get(gate) != sha:
                errors.append(f"APPROVAL_{gate.upper()}_MISSING_OR_STALE")
    for flag in ("production_replay", "persistence_reconciliation",
                 "security_governance_clear"):
        if receipt.get(flag) is not True:
            errors.append(f"{flag.upper()}_NOT_VERIFIED")
    if (isinstance(receipt.get("independent_review"), dict) and
        isinstance(receipt.get("independent_qa"), dict) and
        receipt["independent_review"].get("actor") == receipt["independent_qa"].get("actor")):
        errors.append("REVIEW_AND_QA_MUST_BE_INDEPENDENT")
    return errors


def assess_model_promotion(spec: dict[str, Any],
                           proposal: dict[str, Any]) -> list[str]:
    """A complete proposal still is not authorization to promote a model."""
    if validate_contract(spec):
        return ["ORGANIZATION_CONTRACT_INVALID"]
    errors = [
        f"MISSING_{field.upper()}"
        for field in spec["model_promotion_required_fields"]
        if not proposal.get(field)
    ]
    if proposal.get("submitter") == proposal.get("independent_model_review"):
        errors.append("MODEL_SELF_REVIEW_FORBIDDEN")
    return errors
