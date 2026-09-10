from __future__ import annotations

from dataclasses import dataclass
from time import monotonic, sleep
from typing import Any, Callable, Mapping

import httpx


@dataclass(frozen=True)
class HttpFetchResult:
    url: str
    status_code: int
    payload: Mapping[str, Any]
    attempts: int
    from_cache: bool


class SafeJsonGetClient:
    """Small GET-only client with bounded retries and in-memory TTL cache.

    It intentionally exposes no POST/PUT/PATCH/DELETE helpers. External
    analytical acquisition therefore cannot accidentally become an execution
    surface. Callers provide a clock/sleeper in tests when deterministic timing
    is needed.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = 10.0,
        max_attempts: int = 3,
        backoff_seconds: float = 0.25,
        cache_ttl_seconds: float = 15.0,
        min_interval_seconds: float = 0.0,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = monotonic,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self.timeout_seconds = float(timeout_seconds)
        self.max_attempts = int(max_attempts)
        self.backoff_seconds = max(0.0, float(backoff_seconds))
        self.cache_ttl_seconds = max(0.0, float(cache_ttl_seconds))
        self.min_interval_seconds = max(0.0, float(min_interval_seconds))
        self.clock = clock
        self.sleeper = sleeper
        self._client = httpx.Client(timeout=self.timeout_seconds, transport=transport, follow_redirects=True)
        self._cache: dict[tuple[str, tuple[tuple[str, str], ...]], tuple[float, Mapping[str, Any]]] = {}
        self._last_request_at: float | None = None

    def get_json(self, url: str, headers: Mapping[str, str] | None = None) -> Mapping[str, Any]:
        return self.fetch(url, headers=headers).payload

    def fetch(self, url: str, *, headers: Mapping[str, str] | None = None, use_cache: bool = True) -> HttpFetchResult:
        if not isinstance(url, str) or not url.startswith("https://"):
            raise ValueError("only absolute https GET URLs are permitted")
        normalized_headers = tuple(sorted((str(k), str(v)) for k, v in (headers or {}).items()))
        cache_key = (url, normalized_headers)
        now = self.clock()
        cached = self._cache.get(cache_key)
        if use_cache and cached and (now - cached[0]) <= self.cache_ttl_seconds:
            return HttpFetchResult(url=url, status_code=200, payload=cached[1], attempts=0, from_cache=True)

        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            self._respect_min_interval()
            try:
                response = self._client.get(url, headers=dict(normalized_headers))
                self._last_request_at = self.clock()
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, Mapping):
                    raise ValueError("expected JSON object response")
                frozen_payload = dict(payload)
                self._cache[cache_key] = (self.clock(), frozen_payload)
                return HttpFetchResult(
                    url=url,
                    status_code=response.status_code,
                    payload=frozen_payload,
                    attempts=attempt,
                    from_cache=False,
                )
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt < self.max_attempts:
                    self.sleeper(self.backoff_seconds * (2 ** (attempt - 1)))

        assert last_error is not None
        raise RuntimeError(f"GET_JSON_FAILED after {self.max_attempts} attempts: {url}") from last_error

    def _respect_min_interval(self) -> None:
        if self._last_request_at is None or self.min_interval_seconds <= 0:
            return
        wait = self.min_interval_seconds - (self.clock() - self._last_request_at)
        if wait > 0:
            self.sleeper(wait)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "SafeJsonGetClient":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
