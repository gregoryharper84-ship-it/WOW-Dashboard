from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Mapping

import httpx


@dataclass(frozen=True)
class HttpPolicy:
    timeout_seconds: float = 8.0
    max_attempts: int = 3
    backoff_seconds: float = 0.25


class JsonHttpClient:
    """Small bounded GET-only client for public/official weather evidence.

    It intentionally exposes no POST/PUT/PATCH/DELETE methods. This keeps the
    Kalshi Weather analytical runtime structurally incapable of order actions.
    """

    def __init__(self, policy: HttpPolicy | None = None, client: httpx.Client | None = None):
        self.policy = policy or HttpPolicy()
        self.client = client or httpx.Client(timeout=self.policy.timeout_seconds, follow_redirects=True)

    def get_json(self, url: str, headers: Mapping[str, str] | None = None) -> Mapping[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, self.policy.max_attempts + 1):
            try:
                response = self.client.get(url, headers=dict(headers or {}))
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, Mapping):
                    raise ValueError("JSON_ROOT_NOT_OBJECT")
                return data
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt >= self.policy.max_attempts:
                    break
                time.sleep(self.policy.backoff_seconds * attempt)
        raise RuntimeError(f"HTTP_JSON_GET_FAILED:{type(last_error).__name__ if last_error else 'UNKNOWN'}") from last_error
