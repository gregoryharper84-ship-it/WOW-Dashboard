#!/usr/bin/env python3
"""Gate-capability matrix (WOW Engineering V25 section 2).

Answers one question before CI is spent or a reviewer is dispatched: **can this gate
ever pass for this change?** If it cannot, name the typed reason and the authorized
route that resolves it instead of re-dispatching and waiting for a different answer.

This is a read-only capability model. It dispatches nothing, merges nothing, grants
no bypass and never sets execution authority: ``can_execute=false`` everywhere.
Recovery actions reuse the vocabulary already owned by
``v17/engineering_recovery_planner.py`` (``WAITING_ON_OWNER``,
``WAITING_ON_EVIDENCE``, ``DISPATCH_REPAIR``, ``INDEPENDENT_SOURCE_ADOPTION_*``), so
this module classifies reachability and does not introduce a second control plane.

V3 release classes are ``R`` (routine) and ``P`` (protected). The matrix also
accepts inherited project classification ``A`` (routine), ``D`` (diagnostic),
``B`` (probability-adjacent), ``S`` (protected semantics), and ``C``
(probability-producing). A protected trust-root path is always classified
protected even when the caller labels the task routine. ``C`` also requires
separate fitted-model validation before any probability publication.

The ``requirement`` field records pinned CI, not authorization: ``ruleset_live``
identifies the three checks observed in ruleset #24907422. Governance V3 permits
owner-authored protected source under deterministic QA; legacy certifiers can
remain advisory-unreachable and a historical REJECT cannot be rewritten.
Separately applicable project security/release authorization still governs.
The static model is not permission to merge and cannot prove source independence.
``ruleset_proposed`` gates are declared in ``ruleset-main.proposed.json`` but are not
live, and ``advisory`` gates publish a check without gating the merge. A gate that is
structurally impossible for a change class is therefore a fact to record, not a
reason to spin: it is ``POLICY_UNREACHABLE`` with a named actor, never a retry.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

CHANGE_CLASSES = ("A", "D", "S", "C", "R", "P", "B")

# Classification vocabulary requested by V25 section 1. Only TRANSIENT may be retried.
RECOVERY_CLASSES = (
    "TRANSIENT",
    "REPAIRABLE_CODE",
    "AUTH_OR_CONFIGURATION",
    "EVIDENCE_PENDING",
    "POLICY_UNREACHABLE",
    "PROVEN_REJECT",
)

REQUIREMENT_LEVELS = ("ruleset_live", "ruleset_proposed", "advisory")

# Structural conditions the matrix can evaluate from facts the caller already has.
CONDITIONS = (
    "owner_authored_trust_root",
    "provider_unavailable",
    "unchanged_head_after_reject",
)


@dataclass(frozen=True)
class ImpossibleState:
    """One reason a gate can never pass for a change, with its authorized route."""

    condition: str
    reason: str
    recovery_class: str
    actor: str
    action: str


@dataclass(frozen=True)
class Gate:
    id: str
    workflow: str
    check_context: str
    requirement: str
    required_by: str
    publisher: str
    impossible: tuple[ImpossibleState, ...] = ()


_ADOPTION = (
    "INDEPENDENT_ENGINEERING_AUTHOR",
    "INDEPENDENT_SOURCE_ADOPTION_WITH_VERIFIED_WRITER_LEASE",
)

GATES = (
    Gate(
        id="required_three_regression",
        workflow=".github/workflows/wow-verify.yml",
        check_context="WOW required-three regression",
        requirement="ruleset_live",
        required_by="ruleset #24907422, App 15368, strict_required_status_checks_policy=false",
        publisher=".github/workflows/wow-verify.yml job `required-three`",
    ),
    Gate(
        id="governed_probability_backend",
        workflow=".github/workflows/wow-engine-verify.yml",
        check_context="WOW governed probability backend",
        requirement="ruleset_live",
        required_by="ruleset #24907422, App 15368, strict_required_status_checks_policy=false",
        publisher=".github/workflows/wow-engine-verify.yml job `governed-probability-backend`",
    ),
    Gate(
        id="additional_required_regression",
        workflow=".github/workflows/wow-verify.yml",
        check_context="WOW additional required regression",
        requirement="ruleset_live",
        required_by="ruleset #24907422, App 15368, strict_required_status_checks_policy=false",
        publisher=".github/workflows/wow-verify.yml job `additional-required`",
    ),
    Gate(
        id="independent_qa",
        workflow=".github/workflows/wow-v17-independent-qa-check.yml",
        check_context="WOW Independent QA exact-head",
        requirement="ruleset_proposed",
        required_by=(
            "ruleset-main.proposed.json placeholder __WOW_QA_APP_ID__; not in the live "
            "ruleset, so never merge-blocking today (see Governance V3; owner-authored source is not an automatic QA failure)"
        ),
        publisher="QA App check_run API (no literal job name)",

    ),
    Gate(
        id="release_authority",
        workflow=".github/workflows/wow-v17-release-authority-check.yml",
        check_context="WOW Release Authority exact-head",
        requirement="ruleset_proposed",
        required_by=(
            "ruleset-main.proposed.json placeholder __WOW_RELEASE_APP_ID__; not in the live "
            "ruleset, so never merge-blocking today"
        ),
        publisher="Release App check_run API (no literal job name)",

    ),
    Gate(
        id="semantic_review",
        workflow=".github/workflows/wow-v17-simple-security-review.yml",
        check_context="WOW Independent Security Review",
        requirement="advisory",
        required_by="QA App 5245832 publishes the check; no ruleset pins it",
        publisher="job `Execute independent semantic security review`, QA App check_run API",
        impossible=(
            ImpossibleState(
                condition="provider_unavailable",
                reason="PROVIDER_MODEL_UNAVAILABLE",
                recovery_class="AUTH_OR_CONFIGURATION",
                actor="OWNER",
                action="REPAIR_PROVIDER_CREDENTIAL_OR_ROUTE",
            ),
            ImpossibleState(
                condition="unchanged_head_after_reject",
                reason="PREVIOUS_SEMANTIC_REJECT_SAME_SHA",
                recovery_class="PROVEN_REJECT",
                actor="OWNER",
                action="NEW_HEAD_OR_NEW_EVIDENCE_REQUIRED",
            ),
        ),
    ),
    Gate(
        id="owner_fallback",
        workflow=".github/workflows/wow-v17-simple-security-review.yml",
        check_context="(third stage; publishes no check)",
        requirement="advisory",
        required_by="stage 3 of the semantic reviewer; requires App author and non-owner source",
        publisher="stage 3 inside the semantic reviewer",
        impossible=(
            ImpossibleState(
                condition="owner_authored_trust_root",
                reason="OWNER_IS_SOURCE_AUTHOR_OR_IDENTITY_UNVERIFIED",
                recovery_class="POLICY_UNREACHABLE",
                actor=_ADOPTION[0],
                action=_ADOPTION[1],
            ),
        ),
    ),
    Gate(
        id="existing_pr_governance",
        workflow=".github/workflows/wow-v17-existing-pr-governance-review.yml",
        check_context="Existing PR trusted governance certification",
        requirement="advisory",
        required_by="advisory exact-head certification; no ruleset pins it",
        publisher=".github/workflows/wow-v17-existing-pr-governance-review.yml job `certify`",
        impossible=(
            ImpossibleState(
                condition="owner_authored_trust_root",
                reason="QA_TRUST_ROOT_OWNER_AUTHORED",
                recovery_class="POLICY_UNREACHABLE",
                actor=_ADOPTION[0],
                action=_ADOPTION[1],
            ),
        ),
    ),
)

GATE_IDS = tuple(gate.id for gate in GATES)


def live_gates() -> tuple[Gate, ...]:
    """Gates the live ruleset actually pins. Only these can block a merge."""
    return tuple(gate for gate in GATES if gate.requirement == "ruleset_live")


def assess(
    *,
    change_class: str,
    author_is_owner: bool,
    touches_trust_root: bool,
    provider_available: bool = True,
    head_unchanged_since_reject: bool = False,
) -> dict:
    """Return per-gate reachability for one change.

    Owner authorship does not make V3 deterministic QA or Release structurally
    unreachable. Legacy advisory certifiers and owner-source reviewer fallback
    can remain unreachable: preserve that evidence without conflating it with
    required CI or authorizing a merge.
    """
    if change_class not in CHANGE_CLASSES:
        raise ValueError(f"unknown change class: {change_class!r}")

    conditions = {
        "owner_authored_trust_root": bool(author_is_owner and touches_trust_root),
        "provider_unavailable": not provider_available,
        "unchanged_head_after_reject": bool(head_unchanged_since_reject),
    }

    unreachable: list[dict] = []
    blocking: list[dict] = []
    for gate in GATES:
        for state in gate.impossible:
            if not conditions.get(state.condition):
                continue
            entry = {
                "gate": gate.id,
                "check": gate.check_context,
                "requirement": gate.requirement,
                "condition": state.condition,
                "reason": state.reason,
                "recovery_class": state.recovery_class,
                "actor": state.actor,
                "action": state.action,
            }
            unreachable.append(entry)
            if gate.requirement == "ruleset_live":
                blocking.append(entry)

    retryable = [e for e in unreachable if e["recovery_class"] == "TRANSIENT"]
    return {
        "change_class": change_class,
        "normalized_release_class": (
            "P" if change_class in ("P", "B", "S", "C") or touches_trust_root
            else "R"
        ),
        "model_governance_required": change_class == "C",
        "unreachable": unreachable,
        "blocking": blocking,
        "merge_blocked": bool(blocking),
        "merge_blocked_scope": "STRUCTURAL_PINNED_CI_ONLY",
        "policy_review_required": change_class in ("P", "B", "S", "C") or touches_trust_root,
        "independent_review_status": "UNVERIFIED",
        "source_independence_status": "UNVERIFIED",
        "live_ruleset_status": "NOT_QUERIED_THIS_INVOCATION",
        "retryable": retryable,
        "next_action": blocking[0]["action"] if blocking else (
            unreachable[0]["action"] if unreachable else "NONE"
        ),
        "grants_merge": False,
        "can_execute": False,
    }


def render_markdown() -> str:
    lines = [
        "# WOW gate-capability matrix",
        "",
        "Generated from `artifacts/wow-engine/v17/gate_capability_matrix.py`. Do not edit",
        "this file by hand: `test_v17_gate_capability_matrix.py` fails when it drifts from",
        "the module.",
        "",
        "Answer one question before spending CI or dispatching a reviewer: **can this gate",
        "ever pass for this change?** When it cannot, the row names the typed reason and the",
        "authorized route that resolves it. A structurally impossible gate is",
        "`POLICY_UNREACHABLE` with a named actor, never a retry. Nothing here merges,",
        "dispatches or bypasses: `can_execute=false`.",
        "",
        "Only `ruleset_live` checks are tagged as pinned CI. This static source cannot",
        "revalidate live GitHub settings on each invocation or prove release authority.",
        "Protected semantic changes still require independent security evidence under policy.",
        "`merge_blocked=false` is never merge authorization, and Engineering App copying",
        "source bytes does not establish independent authorship or independent review.",
        "Proposed checks appear in `ruleset-main.proposed.json`; other checks",
        "are advisory unless separately established as policy obligations.",
        "",
        "| Gate | Published check | Requirement | Impossible for | Typed reason | Recovery class | Authorized route |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for gate in GATES:
        if not gate.impossible:
            lines.append(
                f"| `{gate.id}` | {gate.check_context} | {gate.requirement} | - | - | - | "
                "no structural impossibility declared |"
            )
            continue
        for state in gate.impossible:
            lines.append(
                f"| `{gate.id}` | {gate.check_context} | {gate.requirement} | "
                f"`{state.condition}` | `{state.reason}` | `{state.recovery_class}` | "
                f"{state.action} (`{state.actor}`) |"
            )
    lines += [
        "",
        "## Reachability by change class",
        "",
        "| Change class | Owner-authored trust root | Merge blocked by a structural impasse |",
        "| --- | --- | --- |",
    ]
    for change_class in CHANGE_CLASSES:
        for owner in (False, True):
            result = assess(
                change_class=change_class,
                author_is_owner=owner,
                touches_trust_root=owner,
            )
            lines.append(
                f"| `{change_class}` | {'yes' if owner else 'no'} | "
                f"{'yes' if result['merge_blocked'] else 'no'} |"
            )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--render", action="store_true", help="print the markdown table")
    parser.add_argument("--write-doc", type=Path, help="write the markdown table to a path")
    parser.add_argument("--change-class", choices=CHANGE_CLASSES)
    parser.add_argument("--owner-authored-trust-root", action="store_true")
    parser.add_argument("--provider-unavailable", action="store_true")
    parser.add_argument("--unchanged-head-after-reject", action="store_true")
    args = parser.parse_args(argv)

    if args.write_doc:
        args.write_doc.write_text(render_markdown(), encoding="utf-8")
        print(args.write_doc)
        return 0
    if args.render:
        print(render_markdown(), end="")
        return 0
    if args.change_class:
        owner = args.owner_authored_trust_root
        print(json.dumps(assess(
            change_class=args.change_class,
            author_is_owner=owner,
            touches_trust_root=owner,
            provider_available=not args.provider_unavailable,
            head_unchanged_since_reject=args.unchanged_head_after_reject,
        ), indent=2))
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
