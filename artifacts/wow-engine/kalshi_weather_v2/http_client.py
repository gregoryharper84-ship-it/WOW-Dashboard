from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

import httpx


@dataclass(frozen=True)
class HttpPolicy:
    timeout_seconds: float = 8.0
    max_attempts: int = 3
    backoff_seconds: float = 0.25
    cache_ttl_seconds: float = 15.0
    min_interval_seconds: float = 0.0


class JsonHttpClient:
    """Bounded HTTPS GET-only client for weather and Kalshi evidence.

    No mutation helpers exist. Successful JSON-object responses are cached for
    a short TTL, and callers can configure a minimum request interval for
    provider-specific pacing. This keeps acquisition deterministic enough for
    replay while structurally preventing order actions.
    """

    def __init__(
        self,
        policy: HttpPolicy | None = None,
        client: httpx.Client | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.policy = policy or HttpPolicy()
        if self.policy.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self.client = client or httpx.Client(timeout=self.policy.timeout_seconds, follow_redirects=True)
        self.clock = clock
        self.sleeper = sleeper
        self._cache: dict[tuple[str, tuple[tuple[str, str], ...]], tuple[float, Mapping[str, Any]]] = {}
        self._last_request_at: float | None = None

    def get_json(self, url: str, headers: Mapping[str, str] | None = None) -> Mapping[str, Any]:
        if not isinstance(url, str) or not url.startswith("https://"):
            raise ValueError("ONLY_HTTPS_GET_ALLOWED")

        normalized_headers = tuple(sorted((str(k), str(v)) for k, v in (headers or {}).items()))
        key = (url, normalized_headers)
        now = self.clock()
        cached = self._cache.get(key)
        if cached and (now - cached[0]) <= max(0.0, self.policy.cache_ttl_seconds):
            return cached[1]

        last_error: Exception | None = None
        for attempt in range(1, self.policy.max_attempts + 1):
            self._pace()
            try:
                response = self.client.get(url, headers=dict(normalized_headers))
                self._last_request_at = self.clock()
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, Mapping):
                    raise ValueError("JSON_ROOT_NOT_OBJECT")
                frozen = dict(data)
                self._cache[key] = (self.clock(), frozen)
                return frozen
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt >= self.policy.max_attempts:
                    break
                self.sleeper(max(0.0, self.policy.backoff_seconds) * (2 ** (attempt - 1)))

        raise RuntimeError(f"HTTP_JSON_GET_FAILED:{type(last_error).__name__ if last_error else 'UNKNOWN'}") from last_error

    def _pace(self) -> None:
        interval = max(0.0, self.policy.min_interval_seconds)
        if interval <= 0 or self._last_request_at is None:
            return
        wait = interval - (self.clock() - self._last_request_at)
        if wait > 0:
            self.sleeper(wait)
