"""Provider-failover classifier and deterministic survival receipts for WOW V17.

This module is control-plane only. It never produces sporting probabilities and
never grants wager or market execution authority.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .engineering_agent_team import load_ledger, select_priority_incident
except ImportError:  # direct-script execution
    from engineering_agent_team import load_ledger, select_priority_incident

OPENAI_FAILURE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "OPENAI_API_QUOTA_EXCEEDED",
        (
            "quota exceeded",
            "insufficient_quota",
            "check your plan and billing details",
        ),
    ),
    (
        "OPENAI_API_KEY_MISSING",
        (
            "openai_api_key is required",
            "openai api key is required",
        ),
    ),
    (
        "OPENAI_API_AUTH_FAILED",
        (
            "invalid_api_key",
            "incorrect api key",
            "invalid authentication",
            "authentication failed",
        ),
    ),
    (
        "OPENAI_API_RATE_LIMITED",
        (
            "rate limit exceeded",
            "rate_limit_exceeded",
            "too many requests",
            "http 429",
            "status 429",
        ),
    ),
    (
        "OPENAI_API_UPSTREAM_UNAVAILABLE",
        (
            "openai upstream unavailable",
            "responses api unavailable",
            "http 500",
            "http 502",
            "http 503",
            "http 504",
        ),
    ),
)

ANTHROPIC_FAILURE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "ANTHROPIC_API_CREDIT_EXHAUSTED",
        ("credit balance", "billing error", "spend limit reached"),
    ),
    (
        "ANTHROPIC_API_AUTH_FAILED",
        ("authentication_error", "invalid x-api-key", "invalid api key"),
    ),
    (
        "ANTHROPIC_API_RATE_LIMITED",
        ("rate_limit_error", "rate limit"),
    ),
    (
        "ANTHROPIC_API_UPSTREAM_UNAVAILABLE",
        ("overloaded_error", "api_error", "service unavailable"),
    ),
)

OPENAI_FAILOVER_ELIGIBLE = {
    "OPENAI_API_QUOTA_EXCEEDED",
    "OPENAI_API_KEY_MISSING",
    "OPENAI_API_AUTH_FAILED",
    "OPENAI_API_RATE_LIMITED",
    "OPENAI_API_UPSTREAM_UNAVAILABLE",
}

# These are account/credential problems, not short-lived throughput spikes.
# No automatic OpenAI re-dispatch can clear a billing or invalid-key error.
# An explicit authorized provider probe, with a fresh successful worker run,
# is the only supported way to clear the automatic circuit breaker.
OPENAI_RECOVERY_REQUIRED = frozenset({
    "OPENAI_API_QUOTA_EXCEEDED",
    "OPENAI_API_KEY_MISSING",
    "OPENAI_API_AUTH_FAILED",
})

# Typed worker delivery outcomes (incidents #1021/#1527). Matched only on the emitted
# workflow error command, never on the step's own script text echoed into the log.
DELIVERY_ERROR_RE = re.compile(r"(?:::error::|##\[error\])(ACTIONABLE_REPAIR_[A-Z_]+):")
GOVERNANCE_BLOCKED_CODES = frozenset({"ACTIONABLE_REPAIR_POLICY_BOUNDARY"})

NON_PROVIDER_MARKERS = (
    "eacces:",
    "permission denied",
    "no such file or directory",
    "syntaxerror",
    "assertionerror",
    "pytest",
    "yaml",
)

# Actions --log-failed includes echoed shell source; a harmless quoted
# "quota exceeded" string is not an actual exhausted account. Only a
# runtime error line, structured API error, or standalone provider code
# can trip the persistent quota breaker.
RUNTIME_QUOTA_RE = re.compile(
    r"(?im)^(?:[0-9]{4}-[0-9]{2}-[0-9]{2}T[^\s]+\s+)?"
    r"(?:ERROR(?: code)?:|##\[error\]|::error::)"
    r"[^\n]*(?:quota exceeded|insufficient_quota|check your plan and billing details)"
)


def _quota_evidence(log_text: str) -> bool:
    value = str(log_text or "")
    if RUNTIME_QUOTA_RE.search(value):
        return True
    if re.search(r"(?im)^\s*(?:insufficient_quota|quota exceeded)\s*$", value):
        return True
    return bool(re.search(
        r'(?im)^(?:[0-9]{4}-[0-9]{2}-[0-9]{2}T[^\s]+\s+)?\{[^\n]*'
        r'"error"[^\n]*"insufficient_quota"', value
    ))


@dataclass(frozen=True)
class ProviderFailure:
    provider: str
    code: str
    failover_eligible: bool
    circuit_breaker_minutes: int
    can_execute: bool = False
    disposition: str | None = None
    provider_signal: str | None = None
    manual_recovery_required: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "code": self.code,
            "failover_eligible": self.failover_eligible,
            "circuit_breaker_minutes": self.circuit_breaker_minutes,
            "disposition": self.disposition,
            "provider_signal": self.provider_signal,
            "manual_recovery_required": self.manual_recovery_required,
            "can_execute": False,
            "terminal_authority": "V17_TERMINAL_REDUCER",
        }


def classify_provider_failure(provider: str, log_text: str) -> ProviderFailure:
    provider_key = str(provider or "").strip().lower()
    text = str(log_text or "").lower()

    patterns = (
        OPENAI_FAILURE_PATTERNS
        if provider_key == "openai"
        else ANTHROPIC_FAILURE_PATTERNS
        if provider_key in {"anthropic", "claude"}
        else ()
    )
    provider_code = next(
        (
            code for code, needles in patterns
            if any(needle in text for needle in needles)
            and (code != "OPENAI_API_QUOTA_EXCEEDED" or _quota_evidence(log_text))
        ),
        None,
    )
    delivery = DELIVERY_ERROR_RE.search(str(log_text or ""))
    if delivery:
        # A typed delivery outcome is never relabelled as a provider failure. A governance
        # policy boundary never fails over or retries, whatever else the log contains.
        delivery_code = delivery.group(1)
        blocked = delivery_code in GOVERNANCE_BLOCKED_CODES
        eligible = not blocked and provider_code in OPENAI_FAILOVER_ELIGIBLE
        return ProviderFailure(
            provider=provider_key or "unknown",
            code=delivery_code,
            failover_eligible=eligible,
            # Explicit governance HOLD is authoritative even if the same
            # log also includes a provider quota symptom.
            circuit_breaker_minutes=(
                0 if blocked else -1 if provider_code in OPENAI_RECOVERY_REQUIRED
                else 120 if eligible else 0
            ),
            disposition="BLOCKED_WITH_EXACT_REASON" if blocked else "UNRESOLVED_TYPED_FAILURE",
            provider_signal=provider_code,
            manual_recovery_required=(
                not blocked and provider_code in OPENAI_RECOVERY_REQUIRED
            ),
        )

    for code, needles in patterns:
        if (any(needle in text for needle in needles)
                and (code != "OPENAI_API_QUOTA_EXCEEDED" or _quota_evidence(log_text))):
            eligible = code in OPENAI_FAILOVER_ELIGIBLE
            persistent = code in OPENAI_RECOVERY_REQUIRED or code == "ANTHROPIC_API_CREDIT_EXHAUSTED"
            cooldown = -1 if persistent else 120 if eligible else 0
            return ProviderFailure(
                provider=provider_key or "unknown",
                code=code,
                failover_eligible=eligible,
                circuit_breaker_minutes=cooldown,
                manual_recovery_required=persistent,
            )

    if any(marker in text for marker in NON_PROVIDER_MARKERS):
        return ProviderFailure(
            provider=provider_key or "unknown",
            code="NON_PROVIDER_FAILURE",
            failover_eligible=False,
            circuit_breaker_minutes=0,
        )

    return ProviderFailure(
        provider=provider_key or "unknown",
        code="UNCLASSIFIED_PROVIDER_OR_WORKFLOW_FAILURE",
        failover_eligible=False,
        circuit_breaker_minutes=0,
    )


def circuit_breaker_active(
    failure_code: str,
    *,
    age_minutes: float,
    cooldown_minutes: int = 120,
) -> bool:
    # Billing exhaustion/invalid credentials do not recover on a timer.
    # A successful, separately authorized provider probe resets the last-failed
    # run observed by the dispatcher; never recycle an expired billing HOLD.
    if failure_code in OPENAI_RECOVERY_REQUIRED:
        return age_minutes >= 0
    return (
        failure_code in OPENAI_FAILOVER_ELIGIBLE
        and age_minutes >= 0
        and age_minutes < cooldown_minutes
    )


def survival_receipt(
    *,
    reason: str,
    parent_run_id: str,
    ledger_path: str | Path,
) -> dict[str, Any]:
    records = load_ledger(ledger_path)
    priority = select_priority_incident(records).as_dict()
    return {
        "status": "DEGRADED_DETERMINISTIC_SURVIVAL",
        "reason": reason or "UNSPECIFIED_PROVIDER_FAILURE",
        "parent_run_id": str(parent_run_id or ""),
        "priority": priority,
        "provider_generation_allowed": False,
        "deterministic_checks_allowed": True,
        "probability_authority": False,
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "generated_utc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    classify = sub.add_parser("classify")
    classify.add_argument("--provider", required=True)
    classify.add_argument("--log-file", required=True)

    breaker = sub.add_parser("circuit-breaker")
    breaker.add_argument("--failure-code", required=True)
    breaker.add_argument("--age-minutes", type=float, required=True)
    breaker.add_argument("--cooldown-minutes", type=int, default=120)

    survival = sub.add_parser("survival-receipt")
    survival.add_argument("--reason", required=True)
    survival.add_argument("--parent-run-id", default="")
    survival.add_argument(
        "--ledger",
        default=str(Path(__file__).with_name("incident-ledger.json")),
    )
    survival.add_argument("--output")

    args = parser.parse_args()

    if args.command == "classify":
        result = classify_provider_failure(
            args.provider,
            Path(args.log_file).read_text(errors="replace"),
        ).as_dict()
        print(json.dumps(result, sort_keys=True))
        return

    if args.command == "circuit-breaker":
        active = circuit_breaker_active(
            args.failure_code,
            age_minutes=args.age_minutes,
            cooldown_minutes=args.cooldown_minutes,
        )
        print(json.dumps({"active": active}, sort_keys=True))
        return

    receipt = survival_receipt(
        reason=args.reason,
        parent_run_id=args.parent_run_id,
        ledger_path=args.ledger,
    )
    text = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(text)
    print(text, end="")


if __name__ == "__main__":
    main()
