"""
gate_engine/runtime_provenance.py
WOW Runtime Provenance & Routing Governance v1.1
WOW-PATCH-2026-08-19-RUNTIME-PROVENANCE

Host abstraction preserved (WOW v16 Clean Core):
  * WOW_BETTING_ENGINE (the Custom GPT) is the model host.
  * Replit is only the capability backend — never the model layer.
  * nested_custom_gpt_required = False, replit_is_model_layer = False.

This module is deliberately observational + downgrade-only.  It does NOT
compute probabilities, does NOT assign or invent terminal labels beyond the
existing MODEL_QUALIFIED_HOLD ceiling constant, and does NOT change any gate
pass/fail logic.  It answers exactly one question, fail-closed:

    "Was this run executed through the preferred production path
     (WOW_BETTING_ENGINE host + all REQUIRED_FOR_CURRENT_RUN capabilities
     verified through configured Replit production services/Actions)?"

v1.1 hardening (post code-review):
  1. ATTESTATION: every record built here carries an HMAC attestation over
     its enforcement-relevant fields, keyed by server-side secret material a
     caller cannot know.  Enforcement points (provenance_blocker) verify the
     attestation — a caller-forged record with production_probability_verified
     = true is rejected as ATTESTATION_INVALID.  Fail-closed, never fail-open.
  2. STRICT EVIDENCE SHAPE: only `verification_source` is accepted; the
     `source` alias is removed.
  3. SERVER-DERIVED EVIDENCE ONLY: capability evidence is a keyword-only
     argument to build_runtime_provenance().  Evidence embedded in the caller
     context dict is IGNORED.  Routes obtain evidence exclusively from
     gate_engine.runtime_capability_probe (in-process, server-side).

can_execute = False (unconditional).  DRY_RUN_ONLY (unconditional).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any

PROVENANCE_VERSION: str = "WOW-RUNTIME-PROVENANCE-v1.1"
PATCH_ID: str = "WOW-PATCH-2026-08-19-RUNTIME-PROVENANCE"

CAN_EXECUTE: bool = False
DRY_RUN_ONLY: bool = True
EXECUTION_RULE: str = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"

# Host abstraction invariants (preserved from FMCG v1.1)
NESTED_CUSTOM_GPT_REQUIRED: bool = False
REPLIT_IS_MODEL_LAYER: bool = False

# Hosts
PREFERRED_HOST: str = "WOW_BETTING_ENGINE"
PROJECT_CHAT: str = "PROJECT_CHAT"
_KNOWN_HOSTS: frozenset[str] = frozenset({PREFERRED_HOST, PROJECT_CHAT})

# Backend verification statuses
PRODUCTION_BACKEND_VERIFIED: str = "PRODUCTION_BACKEND_VERIFIED"
BACKEND_NOT_VERIFIED: str = "BACKEND_NOT_VERIFIED"

# Model run statuses
PREFERRED_PRODUCTION_RUN: str = "PREFERRED_PRODUCTION_RUN"
FALLBACK_RUN: str = "FALLBACK_RUN"
BACKEND_CAPABILITY_INCOMPLETE: str = "BACKEND_CAPABILITY_INCOMPLETE"

# The stricter ceiling any non-verified run must respect.  Reuses the
# existing FMCG label — no new terminal-label taxonomy is introduced.
FALLBACK_CEILING: str = "MODEL_QUALIFIED_HOLD"

# Evidence sources accepted as "configured Replit production services/Actions".
_PRODUCTION_CAPABILITY_SOURCES: frozenset[str] = frozenset({
    "REPLIT_PRODUCTION_SERVICE",
    "REPLIT_PRODUCTION_ACTION",
})

# Probability origins that must never be presented as production-verified.
_LOCAL_PROBABILITY_ORIGINS: frozenset[str] = frozenset({
    "LOCAL_RECONSTRUCTED",
    "LOCAL_SPECIALIST",
    "LOCALLY_RECONSTRUCTED_SPECIALIST",
    "PROJECT_CHAT_RECONSTRUCTED",
    "MANUAL_RECONSTRUCTED",
})

_BLOCKER_PREFIX: str = "RUNTIME_PROVENANCE:BACKEND_NOT_VERIFIED"

# ── Attestation key material ─────────────────────────────────────────────────
# Server-only secret a request payload can NEVER know.  Only a dedicated
# internal attestation secret (WOW_ATTESTATION_SECRET) or SESSION_SECRET is
# acceptable — an API authentication credential (e.g. GPT_ACTION_SECRET) is
# held by callers and must never be key material.  When neither is
# configured, attestation is impossible and every record built is forced
# unverified (fail closed) — see build_runtime_provenance.
def _attestation_key() -> bytes | None:
    for env_name in ("WOW_ATTESTATION_SECRET", "SESSION_SECRET"):
        value = (os.environ.get(env_name) or "").strip()
        if value:
            return value.encode("utf-8")
    return None

# Fields bound by the attestation — the enforcement-relevant surface.
_ATTESTED_FIELDS: tuple[str, ...] = (
    "provenance_version",
    "requested_host",
    "actual_host",
    "execution_path",
    "backend_verification_status",
    "required_capabilities",
    "required_capabilities_satisfied",
    "required_capabilities_unavailable",
    "production_probability_verified",
    "fallback_run",
    "fallback_reason",
    "model_run_status",
    "lowest_ceiling",
)


def _attestation_digest(record: dict[str, Any]) -> str | None:
    key = _attestation_key()
    if key is None:
        return None
    payload = json.dumps(
        {k: record.get(k) for k in _ATTESTED_FIELDS},
        sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def _normalise_capabilities(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return []
    return sorted({str(cap).strip() for cap in value if str(cap).strip()})


def _capability_verified(evidence: Any) -> bool:
    """
    Only structured evidence stamped by a configured Replit production
    service/Action counts as verified.  Strict shape: `status` == VERIFIED
    and `verification_source` (exact key — no aliases) in the accepted set.
    Anything else — booleans, bare strings, self-asserted flags — fails closed.
    """
    if not isinstance(evidence, dict):
        return False
    status = str(evidence.get("status") or "").upper().strip()
    source = str(evidence.get("verification_source") or "").upper().strip()
    return status == "VERIFIED" and source in _PRODUCTION_CAPABILITY_SOURCES


def build_runtime_provenance(
    context: dict[str, Any] | None = None,
    *,
    capability_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build the attested runtime provenance record for a WOW Daily / full-model
    run.

    Fail-closed derivation: `production_probability_verified` is computed
    exclusively from (a) host routing, (b) per-capability verified evidence
    supplied server-side via the keyword-only `capability_evidence` argument,
    and (c) probability origin.  Caller-supplied booleans cannot elevate it,
    and any `capability_evidence` key embedded in `context` is IGNORED —
    routes must pass evidence from gate_engine.runtime_capability_probe.

    Context keys (all optional; absence fails closed where relevant):
      requested_host                 — host the caller asked for
      actual_host                    — host actually executing the run
      preferred_host_available       — False → preferred path cannot be used
      required_capabilities          — capabilities REQUIRED_FOR_CURRENT_RUN
      probability_origin             — origin tag for the probability values
      locally_reconstructed_specialist_probability — explicit local flag
      fallback_reason                — caller-supplied reason (recorded only
                                       when the run is already a fallback)
      lowest_ceiling                 — upstream ceiling passthrough (verified
                                       runs only; fallbacks always get the
                                       stricter FALLBACK_CEILING)
    """
    ctx = dict(context or {})
    nested = ctx.get("runtime_context")
    if isinstance(nested, dict):
        ctx = {**ctx, **nested}

    requested_host = str(ctx.get("requested_host") or PREFERRED_HOST).upper().strip()
    if requested_host not in _KNOWN_HOSTS:
        requested_host = PROJECT_CHAT  # unknown host → treated as non-preferred

    preferred_available = ctx.get("preferred_host_available") is not False

    supplied_actual = ctx.get("actual_host")
    if supplied_actual:
        actual_host = str(supplied_actual).upper().strip()
        if actual_host not in _KNOWN_HOSTS:
            actual_host = PROJECT_CHAT
    elif requested_host == PROJECT_CHAT or not preferred_available:
        actual_host = PROJECT_CHAT
    else:
        actual_host = PREFERRED_HOST

    required = _normalise_capabilities(
        ctx.get("required_capabilities") or ctx.get("REQUIRED_FOR_CURRENT_RUN")
    )
    evidence = capability_evidence if isinstance(capability_evidence, dict) else {}
    satisfied = [c for c in required if _capability_verified(evidence.get(c))]
    unavailable = [c for c in required if c not in satisfied]

    probability_origin = str(
        ctx.get("probability_origin") or ctx.get("probability_source_origin") or ""
    ).upper().strip()
    local_probability = (
        ctx.get("locally_reconstructed_specialist_probability") is True
        or probability_origin in _LOCAL_PROBABILITY_ORIGINS
    )

    # Without server-only attestation key material a verified record cannot
    # be distinguished from a forgery — fail closed to unverified.
    attestation_unavailable = _attestation_key() is None

    on_preferred_path = actual_host == PREFERRED_HOST and preferred_available
    production_probability_verified = bool(
        on_preferred_path and not unavailable and not local_probability
        and not attestation_unavailable
    )

    # ── Fallback reason (recorded, never invented for verified runs) ────────
    fallback_reason: str | None = None
    if not production_probability_verified:
        supplied_reason = str(ctx.get("fallback_reason") or "").strip()
        if supplied_reason:
            fallback_reason = supplied_reason
        elif actual_host == PROJECT_CHAT:
            fallback_reason = (
                "PREFERRED_HOST_UNAVAILABLE"
                if not preferred_available
                else "PROJECT_CHAT_REQUESTED_OR_SELECTED"
            )
        elif unavailable:
            fallback_reason = "REQUIRED_REPLIT_CAPABILITIES_UNVERIFIED"
        elif local_probability:
            fallback_reason = "LOCAL_SPECIALIST_PROBABILITY_NOT_PRODUCTION_VERIFIED"
        elif attestation_unavailable:
            fallback_reason = "ATTESTATION_KEY_UNAVAILABLE"
        else:
            fallback_reason = "RUNTIME_PROVENANCE_UNVERIFIED"

    # ── Execution path / statuses / ceiling ─────────────────────────────────
    if production_probability_verified:
        execution_path = "WOW_BETTING_ENGINE->REPLIT_PRODUCTION_SERVICES_ACTIONS"
        model_run_status = PREFERRED_PRODUCTION_RUN
        backend_verification_status = PRODUCTION_BACKEND_VERIFIED
        lowest_ceiling = ctx.get("lowest_ceiling")  # passthrough; no upgrade implied
    elif actual_host == PROJECT_CHAT:
        execution_path = "PROJECT_CHAT->FALLBACK_RESEARCH"
        model_run_status = FALLBACK_RUN
        backend_verification_status = BACKEND_NOT_VERIFIED
        lowest_ceiling = FALLBACK_CEILING
    else:
        execution_path = "WOW_BETTING_ENGINE->REPLIT_CAPABILITY_VERIFICATION_INCOMPLETE"
        model_run_status = BACKEND_CAPABILITY_INCOMPLETE
        backend_verification_status = BACKEND_NOT_VERIFIED
        lowest_ceiling = FALLBACK_CEILING

    record = {
        "provenance_version": PROVENANCE_VERSION,
        "patch_id": PATCH_ID,
        # Host routing
        "requested_host": requested_host,
        "preferred_host": PREFERRED_HOST,
        "actual_host": actual_host,
        "execution_path": execution_path,
        # Backend verification
        "backend_verification_status": backend_verification_status,
        "required_capabilities": required,
        "required_capabilities_satisfied": satisfied,
        "required_capabilities_unavailable": unavailable,
        "production_probability_verified": production_probability_verified,
        # Fallback identification
        "fallback_run": not production_probability_verified,
        "fallback_reason": fallback_reason,
        "model_run_status": model_run_status,
        "lowest_ceiling": lowest_ceiling,
        # Host abstraction + governance invariants (unconditional)
        "nested_custom_gpt_required": NESTED_CUSTOM_GPT_REQUIRED,
        "replit_is_model_layer": REPLIT_IS_MODEL_LAYER,
        "can_execute": CAN_EXECUTE,
        "dry_run_only": DRY_RUN_ONLY,
        "execution_rule": EXECUTION_RULE,
    }
    record["attestation"] = _attestation_digest(record)
    return record


# ── Server-authoritative route provenance ────────────────────────────────────
# Required capabilities are declared HERE, per governed route — never taken
# from request JSON.  A caller may only make the run stricter (declare extra
# capabilities, declare fallback, declare local probability origin); it can
# never shrink the required set, assert host identity, or supply evidence.
ROUTE_CAPABILITY_REGISTRY: dict[str, tuple[str, ...]] = {
    "wow_daily_scan": ("engine_health", "odds_gateway", "database"),
    "wow_v16_run":    ("engine_health", "database"),
    "wow_cc_run":     ("engine_health", "database"),
}

# Caller context keys honoured by build_route_provenance — strictly the
# downgrade-only signals.  Everything else in the request is ignored.
_DOWNGRADE_ONLY_CONTEXT_KEYS: frozenset[str] = frozenset({
    "requested_host",
    "preferred_host_available",
    "probability_origin",
    "probability_source_origin",
    "locally_reconstructed_specialist_probability",
    "fallback_reason",
    "required_capabilities",
    "REQUIRED_FOR_CURRENT_RUN",
})


# The only credential principal treated as the WOW_BETTING_ENGINE Custom-GPT
# Action.  General API-key callers (e.g. SCORING_API) are never the preferred
# host — they are a fallback/unverified host by construction.
PREFERRED_ACTION_PRINCIPAL: str = "GPT_ACTION"


def build_route_provenance(
    route: str,
    *,
    action_principal: str | None,
    caller_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Server-authoritative provenance builder for governed routes.

    * Required capabilities come from ROUTE_CAPABILITY_REGISTRY, UNIONED with
      any caller-declared extras (caller can add, never remove).
    * Capability evidence comes exclusively from the in-process probe
      registry (gate_engine.runtime_capability_probe).
    * Host identity is derived from the authenticated credential PRINCIPAL
      recorded by require_api_key (request-local state), not request JSON:
      only the designated Custom-GPT Action credential
      (PREFERRED_ACTION_PRINCIPAL) is the preferred host.  Unauthenticated
      calls and general API-key principals can never be the preferred host.
      A caller may still explicitly downgrade to PROJECT_CHAT /
      preferred_host_available=False.
    * An unregistered route fails closed (UNGOVERNED_ROUTE fallback).
    """
    from gate_engine.runtime_capability_probe import probe_required_capabilities

    ctx_in = dict(caller_context or {})
    nested = ctx_in.get("runtime_context")
    if isinstance(nested, dict):
        ctx_in = {**ctx_in, **nested}
    # Strip everything except downgrade-only signals.
    ctx = {k: v for k, v in ctx_in.items() if k in _DOWNGRADE_ONLY_CONTEXT_KEYS}

    route_caps = ROUTE_CAPABILITY_REGISTRY.get(str(route))
    caller_extra = _normalise_capabilities(
        ctx.get("required_capabilities") or ctx.get("REQUIRED_FOR_CURRENT_RUN")
    )
    if route_caps is None:
        # Fail closed: an ungoverned route can never verify.
        ctx["preferred_host_available"] = False
        ctx.setdefault("fallback_reason", f"UNGOVERNED_ROUTE:{str(route)[:60]}")
        required = caller_extra
    else:
        required = sorted(set(route_caps) | set(caller_extra))
    ctx["required_capabilities"] = required
    ctx.pop("REQUIRED_FOR_CURRENT_RUN", None)

    # Host identity from the authenticated credential principal, never JSON.
    if action_principal != PREFERRED_ACTION_PRINCIPAL:
        ctx["requested_host"] = PROJECT_CHAT
        ctx.setdefault(
            "fallback_reason",
            "UNAUTHENTICATED_ACTION" if not action_principal
            else f"NON_ACTION_CREDENTIAL:{str(action_principal)[:40]}",
        )
    elif str(ctx.get("requested_host") or "").upper().strip() != PROJECT_CHAT:
        ctx["requested_host"] = PREFERRED_HOST
    ctx.pop("actual_host", None)  # derived, never caller-asserted

    return build_runtime_provenance(
        ctx,
        capability_evidence=probe_required_capabilities(required),
    )


def _record_attested(provenance: dict[str, Any]) -> bool:
    supplied = provenance.get("attestation")
    if not isinstance(supplied, str) or not supplied:
        return False
    expected = _attestation_digest(provenance)
    if expected is None:   # no server key material → cannot attest anything
        return False
    return hmac.compare_digest(supplied, expected)


def provenance_blocker(provenance: dict[str, Any] | None) -> str | None:
    """
    Return a stable blocker string for an unverified runtime, or None ONLY
    for a fully verified, server-attested preferred production run.

    Fail-closed at every branch:
      * missing record                       → PROVENANCE_RECORD_MISSING
      * verified=True but bad/absent HMAC    → ATTESTATION_INVALID
        (a caller cannot forge attestation without server key material)
      * verified=True, attested, but the record's own fields contradict the
        verified claim                       → RECORD_INCONSISTENT
      * anything else                        → recorded fallback reason
    """
    if not isinstance(provenance, dict):
        return f"{_BLOCKER_PREFIX}:PROVENANCE_RECORD_MISSING"

    if provenance.get("production_probability_verified") is not True:
        reason = str(provenance.get("fallback_reason") or "UNSPECIFIED")[:120]
        return f"{_BLOCKER_PREFIX}:{reason}"

    # Claimed verified — never trust the boolean alone.
    if not _record_attested(provenance):
        return f"{_BLOCKER_PREFIX}:ATTESTATION_INVALID"

    consistent = (
        provenance.get("actual_host") == PREFERRED_HOST
        and provenance.get("backend_verification_status") == PRODUCTION_BACKEND_VERIFIED
        and provenance.get("model_run_status") == PREFERRED_PRODUCTION_RUN
        and not provenance.get("required_capabilities_unavailable")
        and provenance.get("fallback_run") is not True
    )
    if not consistent:
        return f"{_BLOCKER_PREFIX}:RECORD_INCONSISTENT"

    return None


def is_provenance_blocker(blocker: Any) -> bool:
    """True when a blocker string originates from this runtime contract."""
    return isinstance(blocker, str) and blocker.startswith(_BLOCKER_PREFIX)


def enforce_no_upgrade(result: dict[str, Any], provenance: dict[str, Any] | None) -> None:
    """
    Downgrade-only guard for run-level result envelopes (in-place).

    When the runtime is not production-backend-verified (including forged or
    inconsistent records), any final_label of FINAL_APPROVED or
    MONEY_QUALIFIED is capped to the fallback ceiling via the existing
    canonical resolver, and the blocker is recorded.  Labels at or below the
    ceiling are never touched; nothing is ever upgraded.
    """
    blocker = provenance_blocker(provenance)
    if blocker is None:
        return

    result.setdefault("blockers", [])
    if not any(is_provenance_blocker(b) for b in result["blockers"]):
        result["blockers"].append(blocker)

    final = result.get("final_label") or ""
    if final in ("FINAL_APPROVED", "MONEY_QUALIFIED"):
        try:
            from gate_engine.full_model_gatekeeper import canonical_ceiling_resolve
            result["final_label"] = canonical_ceiling_resolve(FALLBACK_CEILING, final)
        except Exception:
            result["final_label"] = FALLBACK_CEILING
        result["runtime_provenance_enforcement"] = {
            "action": "DOWNGRADE_TO_FALLBACK_CEILING",
            "reason": blocker,
            "can_execute": CAN_EXECUTE,
        }
