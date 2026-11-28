"""Merge-starvation prevention for the existing V17 integration controller.

A pull request that is merely *behind main* is not blocked work: it needs one
reserved integration window in which its branch is refreshed, its required
checks rerun on the refreshed head, and fresh acceptance is obtained before
authorized integration.  This module makes only that reservation decision.

It never merges, pushes, refreshes a branch, reruns a workflow, deploys, or
waives a gate.  It is pure: the caller supplies authenticated repository
evidence and receives a deterministic plan.  Required-check success recorded
against an older head is not evidence about the current head, so a stale green
check can never select a pull request for integration.

An unreadable, unattested or ambiguous inventory fails closed instead of
guessing: the controller must escalate, not select a different pull request.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from math import isfinite
from pathlib import Path
from typing import Any, Mapping, Sequence

try:  # imported as v17.pr_integration_reservation
    from .engineering_pr_keep_current import ENGINEERING_BOT_LOGIN
except ImportError:  # direct `python artifacts/wow-engine/v17/pr_integration_reservation.py`
    from engineering_pr_keep_current import ENGINEERING_BOT_LOGIN

CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"

DEFAULT_RESERVATION_SECONDS = 1800
MIN_RESERVATION_SECONDS = 300
MAX_RESERVATION_SECONDS = 7200
STARVATION_WARNING_HOURS = 72.0

READY = "READY_FOR_AUTHORIZED_INTEGRATION"
BEHIND_BASE = "BEHIND_BASE"
CLOSED_OR_MERGED = "CLOSED_OR_MERGED"
CONFLICT_REPAIR = "MERGE_CONFLICT_REPAIR_REQUIRED"
CHECKS_REPAIR = "REQUIRED_CHECKS_FAILED_REPAIR_REQUIRED"
# Mechanical branch currency already has an owner: wow-v17-engineering-pr-keep-current
# updates behind-main wow-engineering[bot] PRs with an App token, which triggers the
# normal PR checks. The selector records that ownership instead of paying a provider
# worker to run `update-branch`.
REFRESH_OWNER = "WOW_V17_ENGINEERING_PR_KEEP_CURRENT"
# keep-current resolves candidates with its exact login, ENGINEERING_BOT_LOGIN. It is
# imported from that module rather than re-declared here so the two identities cannot
# drift; any other author is never seen by that sweep.
REFRESH_OWNER_KEEP_CURRENT = "KEEP_CURRENT_SELECTS_THIS_PULL"
REFRESH_OWNER_NO_OWNER = "NO_AUTOMATIC_REFRESH_OWNER_FOR_AUTHOR"
REFRESH_OWNER_AUTHOR_UNATTESTED = "AUTHOR_UNATTESTED"
REFRESH_OWNER_MERGE_STATE_UNATTESTED = "MERGE_STATE_NOT_ATTESTED_AS_BEHIND"

SELECTABLE_STATES = frozenset({READY, BEHIND_BASE, CONFLICT_REPAIR, CHECKS_REPAIR})
CODE_REPAIR_STATES = frozenset({CONFLICT_REPAIR, CHECKS_REPAIR})
MERGE_STATES = frozenset(
    {"behind", "clean", "blocked", "dirty", "draft", "has_hooks", "unstable", "unknown"}
)
DISPATCH_PROVIDERS = frozenset({"openai", "anthropic"})
FAILING_CONCLUSIONS = frozenset(
    {"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"}
)
PASSING_CONCLUSIONS = frozenset({"SUCCESS", "NEUTRAL", "SKIPPED"})
CHECK_STATES = frozenset({"SUCCESS", "FAILURE", "PENDING", "MISSING"})
REVIEW_STATES = frozenset({"APPROVED", "CHANGES_REQUESTED", "PENDING"})
REPOSITORY_STATES = frozenset({"open", "closed", "merged"})

_STATE_BLOCKERS = {
    CLOSED_OR_MERGED: "PULL_CLOSED_OR_MERGED",
    "DRAFT": "PULL_IS_DRAFT",
    "REVIEW_NOT_APPROVED": "PULL_REVIEW_NOT_APPROVED",
    "CHECKS_NOT_ON_CURRENT_HEAD": "REQUIRED_CHECKS_NOT_RUN_ON_CURRENT_HEAD",
    "CHECKS_FAILED": "REQUIRED_CHECKS_FAILED",
    "CHECKS_PENDING": "REQUIRED_CHECKS_PENDING",
    "CHECKS_MISSING": "REQUIRED_CHECKS_MISSING",
    "FRESH_ACCEPTANCE_REQUIRED": "FRESH_ACCEPTANCE_REQUIRED",
}

_HEX = frozenset("0123456789abcdef")


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _sha(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 40 and all(c in _HEX for c in value.lower())


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _hours(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and isfinite(value)
        and value >= 0
    )


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _assess_pull(pull: Any, index: int) -> dict[str, Any]:
    """Return one attested pull assessment, or an invalid record with blockers."""
    if not isinstance(pull, Mapping):
        return {"index": index, "invalid": True, "blockers": [f"PULL_{index}_NOT_A_MAPPING"]}

    blockers: list[str] = []
    number = pull.get("number")
    if not (_count(number) and number > 0):
        blockers.append(f"PULL_{index}_NUMBER_INVALID")
    head = pull.get("head_sha")
    if not _sha(head):
        blockers.append(f"PULL_{index}_HEAD_SHA_INVALID")

    repository_state = str(pull.get("state") or "open").strip().lower()
    if repository_state not in REPOSITORY_STATES:
        blockers.append(f"PULL_{index}_STATE_UNKNOWN")

    draft = pull.get("draft", False)
    if not isinstance(draft, bool):
        blockers.append(f"PULL_{index}_DRAFT_FLAG_INVALID")

    review = str(pull.get("review_state") or "").strip().upper()
    if review not in REVIEW_STATES:
        blockers.append(f"PULL_{index}_REVIEW_STATE_UNKNOWN")

    behind = pull.get("behind_by")
    if not _count(behind):
        blockers.append(f"PULL_{index}_BEHIND_BASE_UNATTESTED")

    author_login = pull.get("author_login")
    if author_login is not None:
        author_login = str(author_login).strip() or None
        if author_login is None:
            blockers.append(f"PULL_{index}_AUTHOR_INVALID")

    merge_state = pull.get("merge_state")
    if merge_state is not None:
        merge_state = str(merge_state).strip().lower() or None
        if merge_state is not None and merge_state not in MERGE_STATES:
            blockers.append(f"PULL_{index}_MERGE_STATE_UNKNOWN")

    age_hours = pull.get("age_hours")
    if not _hours(age_hours):
        blockers.append(f"PULL_{index}_AGE_UNATTESTED")

    checks = pull.get("checks")
    check_status: str | None = None
    check_sha: Any = None
    if not isinstance(checks, Mapping):
        blockers.append(f"PULL_{index}_REQUIRED_CHECKS_UNATTESTED")
    else:
        check_status = str(checks.get("status") or "").strip().upper() or None
        check_sha = checks.get("sha")
        if check_status not in CHECK_STATES:
            blockers.append(f"PULL_{index}_REQUIRED_CHECKS_STATUS_UNKNOWN")
        if not _sha(check_sha):
            blockers.append(f"PULL_{index}_REQUIRED_CHECKS_SHA_INVALID")

    accepted = pull.get("accepted_head_sha")
    if accepted is not None and not _sha(accepted):
        blockers.append(f"PULL_{index}_ACCEPTED_HEAD_SHA_INVALID")

    if blockers:
        return {"index": index, "invalid": True, "blockers": blockers}

    if repository_state in {"closed", "merged"}:
        state = CLOSED_OR_MERGED
    elif draft:
        state = "DRAFT"
    elif review != "APPROVED":
        state = "REVIEW_NOT_APPROVED"
    elif merge_state == "dirty":
        # A real conflict cannot be fixed by a mechanical update-branch.
        state = CONFLICT_REPAIR
    elif check_sha != head:
        state = "CHECKS_NOT_ON_CURRENT_HEAD"
    elif check_status == "FAILURE":
        state = CHECKS_REPAIR
    elif check_status == "PENDING":
        state = "CHECKS_PENDING"
    elif check_status == "MISSING":
        state = "CHECKS_MISSING"
    elif behind > 0:
        state = BEHIND_BASE
    elif accepted != head:
        state = "FRESH_ACCEPTANCE_REQUIRED"
    else:
        state = READY

    requires_code_repair = state in CODE_REPAIR_STATES
    requires_refresh = state == BEHIND_BASE
    requires_fresh_acceptance = requires_refresh or requires_code_repair or accepted != head
    return {
        "index": index,
        "invalid": False,
        "number": number,
        "head_sha": head,
        "state": state,
        "selectable": state in SELECTABLE_STATES,
        "requires_code_repair": requires_code_repair,
        "requires_refresh": requires_refresh,
        "requires_fresh_acceptance": requires_fresh_acceptance,
        "merge_state": merge_state,
        "author_login": author_login,
        "behind_by": behind,
        "age_hours": float(age_hours),
        "blockers": [] if state in SELECTABLE_STATES else [_STATE_BLOCKERS.get(state, "PULL_NOT_SELECTABLE")],
    }


def _blocked(reason: str, **extra: Any) -> dict[str, Any]:
    result = {
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
        "decision": "BLOCKED_EVIDENCE",
        "blockers": [reason],
        "reservation": None,
        "selected_pull_number": None,
        "requires_code_repair": False,
        "requires_refresh": False,
        "requires_fresh_acceptance": False,
        "refresh_owner": None,
        "refresh_owner_reason": None,
        "pulls": [],
        "waiting": [],
        "starvation_risk": [],
    }
    result.update(extra)
    return result


def _validated_reservation(active_reservation: Any, now: datetime) -> dict[str, Any]:
    """Return parsed reservation state or a single blocking reason."""
    if not isinstance(active_reservation, Mapping):
        return {"problem": "INTEGRATION_RESERVATION_NOT_A_MAPPING"}
    number = active_reservation.get("pull_number")
    if not (_count(number) and number > 0):
        return {"problem": "INTEGRATION_RESERVATION_PULL_NUMBER_INVALID"}
    if not _sha(active_reservation.get("head_sha")):
        return {"problem": "INTEGRATION_RESERVATION_HEAD_SHA_INVALID"}
    expires = _parse_ts(active_reservation.get("expires_at"))
    if expires is None:
        return {"problem": "INTEGRATION_RESERVATION_EXPIRY_INVALID"}
    return {
        "problem": None,
        "pull_number": number,
        "head_sha": active_reservation["head_sha"],
        "expires_at": expires,
        "expired": expires <= now,
    }


def plan_integration(
    pulls: Sequence[Mapping[str, Any]],
    *,
    now: datetime,
    active_reservation: Mapping[str, Any] | None = None,
    reservation_seconds: int = DEFAULT_RESERVATION_SECONDS,
) -> dict[str, Any]:
    """Decide which single pull request owns the next integration window."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("NOW_MUST_BE_TIMEZONE_AWARE")
    if not _count(reservation_seconds) or not (
        MIN_RESERVATION_SECONDS <= reservation_seconds <= MAX_RESERVATION_SECONDS
    ):
        raise ValueError("RESERVATION_SECONDS_OUT_OF_BOUNDS")
    if not isinstance(pulls, Sequence) or isinstance(pulls, (str, bytes)):
        return _blocked("PULL_INVENTORY_NOT_A_SEQUENCE")

    reservation: dict[str, Any] | None = None
    release_reason: str | None = None
    if active_reservation is not None:
        reservation = _validated_reservation(active_reservation, now)
        if reservation["problem"]:
            return _blocked(str(reservation["problem"]))

    assessed = [_assess_pull(pull, index) for index, pull in enumerate(pulls)]
    invalid = [item for item in assessed if item["invalid"]]
    if invalid:
        blockers = [code for item in invalid for code in item["blockers"]]
        return _blocked(blockers[0], blockers=blockers)

    numbers = [item["number"] for item in assessed]
    if len(set(numbers)) != len(numbers):
        return _blocked("DUPLICATE_PULL_NUMBER")

    by_number = {item["number"]: item for item in assessed}
    active: dict[str, Any] | None = None
    if reservation is not None:
        holder = by_number.get(reservation["pull_number"])
        if reservation["expired"]:
            release_reason = "INTEGRATION_RESERVATION_EXPIRED"
        elif holder is None:
            release_reason = "INTEGRATION_RESERVATION_HOLDER_ABSENT"
        elif holder["head_sha"] != reservation["head_sha"]:
            release_reason = "INTEGRATION_RESERVATION_HEAD_MOVED"
        elif not holder["selectable"]:
            release_reason = "INTEGRATION_RESERVATION_HOLDER_NO_LONGER_ELIGIBLE"
        else:
            active = {
                "pull_number": holder["number"],
                "head_sha": holder["head_sha"],
                "expires_at": _iso(reservation["expires_at"]),
                "reason": "ACTIVE_RESERVATION_HONORED",
            }

    selected: dict[str, Any] | None = None
    if active is None:
        candidates = sorted(
            (item for item in assessed if item["selectable"]),
            key=lambda item: (-item["age_hours"], item["number"]),
        )
        if candidates:
            selected = candidates[0]
            starved = selected["age_hours"] >= STARVATION_WARNING_HOURS
            active = {
                "pull_number": selected["number"],
                "head_sha": selected["head_sha"],
                "expires_at": _iso(now + timedelta(seconds=reservation_seconds)),
                "reason": (
                    "MERGE_STARVATION_PREVENTION"
                    if starved
                    else "OLDEST_ELIGIBLE_PULL"
                ),
            }

    if active is None:
        decision = "NO_ELIGIBLE_PULL"
    elif selected is not None:
        decision = "RELEASE_AND_REASSIGN" if release_reason else "RESERVATION_GRANTED"
    else:
        decision = "CONTINUE_ACTIVE_RESERVATION"

    selected_number = active["pull_number"] if active else None
    selected_pull = by_number.get(selected_number) if selected_number is not None else None
    waiting = [
        item["number"]
        for item in assessed
        if item["number"] != selected_number and item["state"] != CLOSED_OR_MERGED
    ]
    starvation_risk = [
        item["number"]
        for item in assessed
        if item["state"] != CLOSED_OR_MERGED
        and item["number"] != selected_number
        and item["age_hours"] >= STARVATION_WARNING_HOURS
    ]

    # Only wow-engineering[bot] authored, open, non-draft PRs reported as `behind`
    # are candidates for wow-v17-engineering-pr-keep-current. Crediting that sweep
    # for any other author would name an owner that will never see the pull.
    refresh_owner: str | None = None
    refresh_owner_reason: str | None = None
    if selected_pull and selected_pull["requires_refresh"] and not selected_pull["requires_code_repair"]:
        if selected_pull["merge_state"] != "behind":
            refresh_owner_reason = REFRESH_OWNER_MERGE_STATE_UNATTESTED
        elif selected_pull["author_login"] is None:
            refresh_owner_reason = REFRESH_OWNER_AUTHOR_UNATTESTED
        elif selected_pull["author_login"] == ENGINEERING_BOT_LOGIN:
            refresh_owner = REFRESH_OWNER
            refresh_owner_reason = REFRESH_OWNER_KEEP_CURRENT
        else:
            refresh_owner_reason = REFRESH_OWNER_NO_OWNER

    return {
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
        "decision": decision,
        "blockers": [release_reason] if release_reason and decision == "NO_ELIGIBLE_PULL" else [],
        "reservation": active,
        "selected_pull_number": selected_number,
        "requires_code_repair": bool(selected_pull and selected_pull["requires_code_repair"]),
        "requires_refresh": bool(selected_pull and selected_pull["requires_refresh"]),
        "requires_fresh_acceptance": bool(
            selected_pull and selected_pull["requires_fresh_acceptance"]
        ),
        "refresh_owner": refresh_owner,
        "refresh_owner_reason": refresh_owner_reason,
        "pulls": [
            {
                "number": item["number"],
                "state": item["state"],
                "status": (
                    "RESERVED_ACTIVE"
                    if item["number"] == selected_number
                    else (
                        "CLOSED_OR_MERGED"
                        if item["state"] == CLOSED_OR_MERGED
                        else "WAITING_FOR_INTEGRATION_WINDOW"
                    )
                ),
                "selectable": item["selectable"],
                "requires_code_repair": item["requires_code_repair"],
                "requires_refresh": item["requires_refresh"],
                "requires_fresh_acceptance": item["requires_fresh_acceptance"],
                "blockers": item["blockers"],
            }
            for item in assessed
        ],
        "waiting": waiting,
        "starvation_risk": starvation_risk,
    }


def summarize_status_rollup(rollup: Any) -> str:
    """Reduce GitHub's per-PR check rollup to one attested status.

    ``UNATTESTED`` is returned for an unrecognised shape so the pull record is
    rejected downstream instead of being treated as green.
    """
    if not isinstance(rollup, list) or not rollup:
        return "MISSING"
    failed = False
    pending = False
    for entry in rollup:
        if not isinstance(entry, Mapping):
            return "UNATTESTED"
        state = str(entry.get("state") or "").strip().upper()
        if state:
            # Legacy commit-status context (no status/conclusion pair).
            if state in {"FAILURE", "ERROR"}:
                failed = True
            elif state != "SUCCESS":
                pending = True
            continue
        status = str(entry.get("status") or "").strip().upper()
        conclusion = str(entry.get("conclusion") or "").strip().upper()
        if not status and not conclusion:
            return "UNATTESTED"
        if status and status != "COMPLETED":
            pending = True
        elif conclusion in FAILING_CONCLUSIONS:
            failed = True
        elif conclusion not in PASSING_CONCLUSIONS:
            pending = True
    if failed:
        return "FAILURE"
    if pending:
        return "PENDING"
    return "SUCCESS"


def _author_login(pull: Mapping[str, Any]) -> str | None:
    """Extract the PR author login from either `gh pr list` shape or a flat record."""
    author = pull.get("author")
    if isinstance(author, Mapping):
        return str(author.get("login") or "").strip() or None
    return str(pull.get("author_login") or "").strip() or None


def github_pull_record(
    pull: Any, *, now: datetime, behind_by: Any
) -> dict[str, Any]:
    """Normalise one ``gh pr list`` row into the attested pull record.

    ``behind_by`` must come from an explicit base comparison; when it is unknown
    the record stays unattested and the plan fails closed.  No acceptance is
    inferred from a green rollup, so ``accepted_head_sha`` is only ever supplied
    by a caller that holds independent QA/Release evidence for the exact head.
    """
    if not isinstance(pull, Mapping):
        return {
            "number": None,
            "head_sha": None,
            "state": "unknown",
            "draft": False,
            "review_state": "PENDING",
            "checks": {"status": "UNATTESTED", "sha": None},
            "accepted_head_sha": None,
            "behind_by": behind_by,
            "merge_state": None,
            "author_login": None,
            "age_hours": None,
        }
    head = pull.get("headRefOid")
    review = str(pull.get("reviewDecision") or "").strip().upper()
    review_state = {
        "APPROVED": "APPROVED",
        "CHANGES_REQUESTED": "CHANGES_REQUESTED",
    }.get(review, "PENDING")
    created = _parse_ts(pull.get("createdAt"))
    return {
        "number": pull.get("number"),
        "head_sha": head,
        "state": str(pull.get("state") or "open").strip().lower(),
        "draft": pull.get("isDraft") is True,
        "review_state": review_state,
        "checks": {
            "status": summarize_status_rollup(pull.get("statusCheckRollup")),
            "sha": head,
        },
        "accepted_head_sha": None,
        "behind_by": behind_by,
        "merge_state": str(pull.get("mergeStateStatus") or "").strip().lower() or None,
        "author_login": _author_login(pull),
        "age_hours": None if created is None else max(0.0, (now - created).total_seconds() / 3600.0),
    }


def render_integration_receipt(
    plan: Mapping[str, Any], *, mode: str, generated_at: datetime, provider: str = "none"
) -> dict[str, Any]:
    """Render the auditable selector receipt; never authorises a merge."""
    decision = plan.get("decision")
    if decision == "BLOCKED_EVIDENCE":
        status = "BLOCKED_EVIDENCE"
    elif decision == "NO_ELIGIBLE_PULL":
        status = "NO_ELIGIBLE_PULL"
    else:
        status = "RESERVED"
    # Branch currency is not a code repair: wow-v17-engineering-pr-keep-current
    # already updates behind-main wow-engineering[bot] PRs with an App token that
    # triggers normal CI. Paying a provider worker for a mechanical update-branch
    # would duplicate that lane, so dispatch is limited to real repairs (a merge
    # conflict or failing required checks on the current head).
    requires_code_repair = bool(plan.get("requires_code_repair"))
    requires_refresh = bool(plan.get("requires_refresh"))
    dispatch_repair = bool(
        mode == "dispatch"
        and status == "RESERVED"
        and requires_code_repair
        and plan.get("reservation")
        and plan.get("selected_pull_number") is not None
        and str(provider).strip().lower() in DISPATCH_PROVIDERS
    )
    # The plan already decided who, if anyone, owns a mechanical refresh; the
    # receipt must not re-derive an owner from rules the plan did not apply.
    refresh_owner = plan.get("refresh_owner") if status == "RESERVED" else None
    refresh_owner_reason = plan.get("refresh_owner_reason") if status == "RESERVED" else None
    reservation = plan.get("reservation") or None
    return {
        "status": status,
        "mode": mode,
        "provider": str(provider).strip().lower() or "none",
        "plan_decision": decision,
        "selected_pull_number": plan.get("selected_pull_number"),
        "reserved_head_sha": (reservation or {}).get("head_sha"),
        "requires_code_repair": requires_code_repair,
        "requires_refresh": requires_refresh,
        "requires_fresh_acceptance": bool(plan.get("requires_fresh_acceptance")),
        "refresh_owner": refresh_owner,
        "refresh_owner_reason": refresh_owner_reason,
        "dispatch_repair": dispatch_repair,
        "starvation_risk": list(plan.get("starvation_risk") or []),
        "waiting": list(plan.get("waiting") or []),
        "blockers": list(plan.get("blockers") or []),
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
        "generated_at": _iso(generated_at),
    }


def _load_json(path: str | None) -> Any:
    if not path:
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_receipt(path: str, receipt: Mapping[str, Any]) -> None:
    Path(path).write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _disabled_receipt(generated_at: datetime) -> dict[str, Any]:
    return {
        "status": "DISABLED",
        "mode": "off",
        "provider": "none",
        "plan_decision": "NOT_EVALUATED",
        "selected_pull_number": None,
        "reserved_head_sha": None,
        "requires_code_repair": False,
        "requires_refresh": False,
        "requires_fresh_acceptance": False,
        "refresh_owner": None,
        "refresh_owner_reason": None,
        "dispatch_repair": False,
        "starvation_risk": [],
        "waiting": [],
        "blockers": [],
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
        "generated_at": _iso(generated_at),
    }


def main(argv: Sequence[str] | None = None) -> int:
    """CLI used by the provider dispatcher.  Read-only; never merges."""
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    select = subcommands.add_parser("select", help="select the next integration reservation")
    select.add_argument("--mode", choices=("off", "report", "dispatch"), default="off")
    select.add_argument("--output", required=True)
    select.add_argument("--prs-file", default=None)
    select.add_argument("--provider", default="none")
    select.add_argument("--now", default=None)
    select.add_argument("--active-reservation-file", default=None)
    select.add_argument("--reservation-seconds", type=int, default=DEFAULT_RESERVATION_SECONDS)
    args = parser.parse_args(argv)

    now = _parse_ts(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        raise SystemExit("NOW_INVALID")
    if args.mode == "off":
        receipt = _disabled_receipt(now)
        _write_receipt(args.output, receipt)
        print(json.dumps(receipt, sort_keys=True))
        return 0

    payload = _load_json(args.prs_file)
    reservation = _load_json(args.active_reservation_file)
    if not isinstance(payload, list):
        receipt = render_integration_receipt(
            {"decision": "BLOCKED_EVIDENCE", "blockers": ["PULL_EVIDENCE_UNREADABLE"]},
            mode=args.mode,
            generated_at=now,
            provider=args.provider,
        )
    else:
        pulls = [
            github_pull_record(
                pull,
                now=now,
                behind_by=(pull.get("behind_by") if isinstance(pull, Mapping) else None),
            )
            for pull in payload
        ]
        plan = plan_integration(
            pulls,
            now=now,
            active_reservation=reservation if isinstance(reservation, Mapping) else None,
            reservation_seconds=args.reservation_seconds,
        )
        receipt = render_integration_receipt(
            plan, mode=args.mode, generated_at=now, provider=args.provider
        )
    _write_receipt(args.output, receipt)
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())


__all__ = [
    "BEHIND_BASE",
    "CHECKS_REPAIR",
    "CONFLICT_REPAIR",
    "ENGINEERING_BOT_LOGIN",
    "REFRESH_OWNER",
    "REFRESH_OWNER_AUTHOR_UNATTESTED",
    "REFRESH_OWNER_NO_OWNER",
    "CAN_EXECUTE",
    "DEFAULT_RESERVATION_SECONDS",
    "MAX_RESERVATION_SECONDS",
    "MIN_RESERVATION_SECONDS",
    "READY",
    "STARVATION_WARNING_HOURS",
    "TERMINAL_AUTHORITY",
    "github_pull_record",
    "main",
    "plan_integration",
    "render_integration_receipt",
    "summarize_status_rollup",
]
