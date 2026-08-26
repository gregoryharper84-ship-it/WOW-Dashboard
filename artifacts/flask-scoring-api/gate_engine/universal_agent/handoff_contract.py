"""
gate_engine/universal_agent/handoff_contract.py
WOW-PATCH-2026-08-09-UNIVERSAL-AGENT-CORE-V1 / Phase B0

Canonical Handoff Contract — data schema for evidence/findings handoff
between agent gates. This is a DATA CONTRACT only, not a messaging system.
No routing, pub/sub, queuing, or network transport is implemented here.

Fields capture what was found, what failures exist, what ruling is requested,
and what the next allowed action is. Execution authority fields (EXECUTE,
TRADE, CAPITAL, APPROVE) are structurally forbidden — HandoffContract.__post_init__
rejects them before the object can be constructed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


# ── Authority request constants ───────────────────────────────────────────────
# What an agent may request from the next gate.
# Execution/capital/trade authority is intentionally absent.

class AuthorityRequest:
    NONE            = "NONE"            # No authority requested
    ADVISORY_REVIEW = "ADVISORY_REVIEW" # Request human review of findings
    ESCALATE        = "ESCALATE"        # Escalate to next gate for review
    # EXECUTE, APPROVE, TRADE, CAPITAL intentionally omitted.
    # Attempting to set authority_requested to one of these values raises ValueError.

    _FORBIDDEN: frozenset[str] = frozenset({
        "EXECUTE", "APPROVE", "TRADE", "CAPITAL", "DEPLOY",
        "AUTHORIZE", "ALLOCATE",
    })

    @classmethod
    def validate(cls, value: str) -> None:
        if value.upper() in cls._FORBIDDEN:
            raise ValueError(
                f"HandoffContract.authority_requested='{value}' implies execution "
                f"or capital authority, which is not permitted in this contract. "
                f"Permitted values: NONE, ADVISORY_REVIEW, ESCALATE."
            )


# ── Next-action constants ─────────────────────────────────────────────────────

class NextAction:
    CONTINUE_PIPELINE  = "CONTINUE_PIPELINE"
    AWAIT_HUMAN_REVIEW = "AWAIT_HUMAN_REVIEW"
    ABORT              = "ABORT"
    RETRY              = "RETRY"


# ── Handoff contract ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class HandoffContract:
    """
    Immutable handoff record passed between agent gates.

    DATA CONTRACT ONLY — does not implement message routing, pub/sub, or queuing.
    Carries findings, known failures, and advisory requests — never execution
    permissions or capital-allocation fields.

    Fields:
      current_gate              Which gate produced this handoff
      sender                    Agent ID of sender
      recipient                 Agent ID or gate name of recipient
      claim                     Brief claim being made (one sentence)
      evidence                  Structured evidence dict (opaque to this module)
      tests_or_evidence_produced  Test names or evidence IDs produced by sender
      known_failures            Open failures at handoff time (for recipient context)
      requested_ruling          What the sender asks the recipient to decide
      authority_requested       One of AuthorityRequest.* (NONE/ADVISORY_REVIEW/ESCALATE)
      next_allowed_action       One of NextAction.*
      cost_so_far               {"usd": float, "input_tokens": int, "output_tokens": int}
    """
    current_gate:               str
    sender:                     str
    recipient:                  str
    claim:                      str
    evidence:                   dict
    tests_or_evidence_produced: tuple   # immutable sequence of strings
    known_failures:             tuple   # immutable sequence of strings
    requested_ruling:           str
    authority_requested:        str     # Must be one of AuthorityRequest.*
    next_allowed_action:        str     # Must be one of NextAction.*
    cost_so_far:                dict

    def __post_init__(self) -> None:
        # Non-empty string checks
        for fname in (
            "current_gate", "sender", "recipient", "claim",
            "requested_ruling", "authority_requested", "next_allowed_action",
        ):
            val: Any = object.__getattribute__(self, fname)
            if not isinstance(val, str) or not val.strip():
                raise ValueError(
                    f"HandoffContract.{fname} must be a non-empty string, got {val!r}"
                )

        # Execution authority is forbidden
        AuthorityRequest.validate(self.authority_requested)

        # evidence and cost_so_far must be dicts
        for fname in ("evidence", "cost_so_far"):
            val = object.__getattribute__(self, fname)
            if not isinstance(val, dict):
                raise TypeError(
                    f"HandoffContract.{fname} must be a dict, got {type(val).__name__}"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_gate":               self.current_gate,
            "sender":                     self.sender,
            "recipient":                  self.recipient,
            "claim":                      self.claim,
            "evidence":                   self.evidence,
            "tests_or_evidence_produced": list(self.tests_or_evidence_produced),
            "known_failures":             list(self.known_failures),
            "requested_ruling":           self.requested_ruling,
            "authority_requested":        self.authority_requested,
            "next_allowed_action":        self.next_allowed_action,
            "cost_so_far":                self.cost_so_far,
        }


# ── Constructor ───────────────────────────────────────────────────────────────

def build_handoff_contract(
    *,
    current_gate: str,
    sender: str,
    recipient: str,
    claim: str,
    evidence: Optional[dict[str, Any]] = None,
    tests_or_evidence_produced: Optional[list[str]] = None,
    known_failures: Optional[list[str]] = None,
    requested_ruling: str,
    authority_requested: str = AuthorityRequest.NONE,
    next_allowed_action: str = NextAction.CONTINUE_PIPELINE,
    cost_so_far: Optional[dict[str, Any]] = None,
) -> HandoffContract:
    """
    Primary constructor. Lists are converted to tuples for immutability.
    Forbidden authority values raise ValueError before the object is created.
    Non-dict evidence or cost_so_far raise TypeError before construction.
    """
    if evidence is not None and not isinstance(evidence, dict):
        raise TypeError(
            f"evidence must be a dict or None, got {type(evidence).__name__}"
        )
    if cost_so_far is not None and not isinstance(cost_so_far, dict):
        raise TypeError(
            f"cost_so_far must be a dict or None, got {type(cost_so_far).__name__}"
        )
    return HandoffContract(
        current_gate=current_gate,
        sender=sender,
        recipient=recipient,
        claim=claim,
        evidence=dict(evidence) if evidence is not None else {},
        tests_or_evidence_produced=tuple(tests_or_evidence_produced or []),
        known_failures=tuple(known_failures or []),
        requested_ruling=requested_ruling,
        authority_requested=authority_requested,
        next_allowed_action=next_allowed_action,
        cost_so_far=dict(cost_so_far) if cost_so_far is not None else {},
    )
