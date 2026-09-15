"""Typed TheRundown rate-limit handling.

A bare ``RUNDOWN_HTTP_429`` cannot tell short-lived burst throttling (wait and
retry) from account data-point exhaustion (retrying is pure waste and makes the
quota problem worse).  This module reads the provider's own rate-limit headers,
classifies the refusal, and states the single bounded retry policy the rest of
the runtime is allowed to use.

Retry policy invariants:
- ``Retry-After`` is honoured when the provider sends one;
- retries are bounded and jittered, never a tight loop;
- a quota-exhaustion response is never immediately retried;
- classification is evidence only — it can never change a model status.
"""
from __future__ import annotations

import os
import random
from dataclasses import dataclass
from typing import Any, Mapping

CAN_EXECUTE = False

BURST_THROTTLED = "RUNDOWN_BURST_THROTTLED"
QUOTA_EXHAUSTED = "RUNDOWN_QUOTA_EXHAUSTED"
HTTP_429_UNKNOWN = "RUNDOWN_HTTP_429_UNKNOWN"

RATE_LIMIT_CLASSIFICATIONS = frozenset({BURST_THROTTLED, QUOTA_EXHAUSTED, HTTP_429_UNKNOWN})

_RETRY_AFTER_HEADERS = ("retry-after", "x-retry-after")
_DATAPOINT_REMAINING_HEADERS = (
    "x-datapoints-remaining",
    "x-data-points-remaining",
    "x-ratelimit-datapoints-remaining",
)
_DATAPOINT_RESET_HEADERS = (
    "x-datapoints-reset",
    "x-data-points-reset",
    "x-ratelimit-datapoints-reset",
)
_QUOTA_SCOPE_HEADERS = ("x-ratelimit-scope", "x-quota-scope", "x-ratelimit-period")

# Provider wording that names an exhausted allowance rather than a burst.
_QUOTA_TOKENS = (
    "quota",
    "data point",
    "datapoint",
    "monthly",
    "daily limit",
    "plan limit",
    "subscription",
    "upgrade",
    "exceeded your",
)


@dataclass(frozen=True)
class RundownRateLimit:
    """Everything a 429 told us, preserved instead of collapsed to one code."""

    classification: str
    retry_after_seconds: float | None
    rate_limit: str | None
    rate_limit_remaining: str | None
    rate_limit_reset: str | None
    datapoints_remaining: int | None
    datapoints_reset: str | None
    quota_scope: str | None
    retry_allowed: bool

    @classmethod
    def from_dict(cls, payload: Any) -> "RundownRateLimit":
        """Rebuild from a previously emitted ``as_dict`` payload."""
        if not isinstance(payload, Mapping):
            return classify_rate_limit(None)
        return cls(
            classification=str(payload.get("classification") or HTTP_429_UNKNOWN),
            retry_after_seconds=payload.get("retry_after_seconds"),
            rate_limit=payload.get("rate_limit"),
            rate_limit_remaining=payload.get("rate_limit_remaining"),
            rate_limit_reset=payload.get("rate_limit_reset"),
            datapoints_remaining=payload.get("datapoints_remaining"),
            datapoints_reset=payload.get("datapoints_reset"),
            quota_scope=payload.get("quota_scope"),
            retry_allowed=bool(payload.get("retry_allowed")),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification,
            "retry_after_seconds": self.retry_after_seconds,
            "rate_limit": self.rate_limit,
            "rate_limit_remaining": self.rate_limit_remaining,
            "rate_limit_reset": self.rate_limit_reset,
            "datapoints_remaining": self.datapoints_remaining,
            "datapoints_reset": self.datapoints_reset,
            "quota_scope": self.quota_scope,
            "retry_allowed": self.retry_allowed,
            "prediction_authority": False,
            "can_execute": False,
        }


def _headers_map(headers: Any) -> dict[str, str]:
    """Normalise any header container (dict, email.Message, list) to lowercase."""
    if headers is None:
        return {}
    items: Any
    if isinstance(headers, Mapping):
        items = headers.items()
    elif hasattr(headers, "items"):
        items = headers.items()
    elif isinstance(headers, (list, tuple)):
        items = headers
    else:
        return {}
    out: dict[str, str] = {}
    for entry in items:
        try:
            key, value = entry
        except (TypeError, ValueError):
            continue
        if value is None:
            continue
        out[str(key).strip().lower()] = str(value).strip()
    return out


def _first(headers: dict[str, str], names: tuple[str, ...]) -> str | None:
    for name in names:
        value = headers.get(name)
        if value:
            return value
    return None


def _as_float(value: Any) -> float | None:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _as_int(value: Any) -> int | None:
    parsed = _as_float(value)
    return int(parsed) if parsed is not None else None


def classify_rate_limit(headers: Any = None, body: Any = None) -> RundownRateLimit:
    """Classify one HTTP 429 from its headers and (optionally) its body text.

    Quota exhaustion is asserted only on positive evidence — an explicitly
    zero/absent data-point allowance or provider wording that names a quota.
    Ambiguous refusals stay ``RUNDOWN_HTTP_429_UNKNOWN`` and are treated as
    non-retryable, because guessing "burst" on an exhausted account is the
    failure mode that turns one 429 into a retry storm.
    """
    mapped = _headers_map(headers)
    retry_after = _as_float(_first(mapped, _RETRY_AFTER_HEADERS))
    datapoints_remaining = _as_int(_first(mapped, _DATAPOINT_REMAINING_HEADERS))
    rate_remaining = _first(mapped, ("x-ratelimit-remaining", "ratelimit-remaining"))
    text = str(body or "").lower()

    quota_evidence = (
        (datapoints_remaining is not None and datapoints_remaining <= 0)
        or any(token in text for token in _QUOTA_TOKENS)
    )

    if quota_evidence:
        classification = QUOTA_EXHAUSTED
    elif retry_after is not None or (rate_remaining is not None and _as_int(rate_remaining) == 0):
        classification = BURST_THROTTLED
    else:
        classification = HTTP_429_UNKNOWN

    return RundownRateLimit(
        classification=classification,
        retry_after_seconds=retry_after,
        rate_limit=_first(mapped, ("x-ratelimit-limit", "ratelimit-limit")),
        rate_limit_remaining=rate_remaining,
        rate_limit_reset=_first(mapped, ("x-ratelimit-reset", "ratelimit-reset")),
        datapoints_remaining=datapoints_remaining,
        datapoints_reset=_first(mapped, _DATAPOINT_RESET_HEADERS),
        quota_scope=_first(mapped, _QUOTA_SCOPE_HEADERS),
        retry_allowed=classification == BURST_THROTTLED,
    )


def max_attempts() -> int:
    """Total attempts (first try included).  Bounded to 3 by contract."""
    try:
        configured = int(os.environ.get("WOW_RUNDOWN_429_MAX_ATTEMPTS", "2"))
    except ValueError:
        configured = 2
    return max(1, min(configured, 3))


def _fallback_backoff() -> float:
    try:
        configured = float(os.environ.get("WOW_RUNDOWN_429_BACKOFF_SECONDS", "0.5"))
    except ValueError:
        configured = 0.5
    return max(0.0, min(configured, 5.0))


def _max_sleep() -> float:
    try:
        configured = float(os.environ.get("WOW_RUNDOWN_429_MAX_SLEEP_SECONDS", "5"))
    except ValueError:
        configured = 5.0
    return max(0.0, min(configured, 30.0))


def retry_delay_seconds(
    rate_limit: RundownRateLimit,
    attempt: int,
    *,
    jitter: Any = None,
) -> float:
    """Bounded delay before the next attempt.  Honours ``Retry-After`` first."""
    if not rate_limit.retry_allowed:
        return 0.0
    ceiling = _max_sleep()
    if rate_limit.retry_after_seconds is not None:
        return max(0.0, min(rate_limit.retry_after_seconds, ceiling))
    base = _fallback_backoff() * (2 ** max(0, attempt - 1))
    spread = (jitter if jitter is not None else random.random()) * _fallback_backoff()
    return max(0.0, min(base + spread, ceiling))


def should_retry(rate_limit: RundownRateLimit, attempt: int) -> bool:
    """Retry only a burst throttle, and only inside the bounded attempt budget."""
    return bool(rate_limit.retry_allowed) and attempt < max_attempts()


__all__ = [
    "BURST_THROTTLED",
    "CAN_EXECUTE",
    "HTTP_429_UNKNOWN",
    "QUOTA_EXHAUSTED",
    "RATE_LIMIT_CLASSIFICATIONS",
    "RundownRateLimit",
    "classify_rate_limit",
    "max_attempts",
    "retry_delay_seconds",
    "should_retry",
]
