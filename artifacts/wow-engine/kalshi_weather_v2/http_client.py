from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Mapping

import httpx


class HttpAcquisitionError(RuntimeError):
    def __init__(self, code: str, url: str, detail: str):
        self.code = code
        self.url = url
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class HttpPolicy:
    timeout_seconds: float = 12.0
    max_attempts: int = 3
    backoff_seconds: float = 0.4


class ReadOnlyJsonClient:
    """Small deterministic GET-only client for public weather/market evidence.

    There are intentionally no POST/PUT/PATCH/DELETE helpers here. The Kalshi
    Weather V2 analytical runtime must not acquire order-placement capability.
    """

    def __init__(self, *, policy: HttpPolicy | None = None, client: httpx.Client | None = None):
        self.policy = policy or HttpPolicy()
        self.client = client or httpx.Client(follow_redirects=True)

    def get_json(self, url: str, headers: Mapping[str, str] | None = None) -> Mapping[str, Any]:
        if not url.startswith("https://"):
            raise HttpAcquisitionError("INSECURE_URL_PROHIBITED", url, "HTTPS is required")

        last_detail = "request not attempted"
        for attempt in range(1, self.policy.max_attempts + 1):
            try:
                response = self.client.get(url, headers=dict(headers or {}), timeout=self.policy.timeout_seconds)
            except httpx.TimeoutException as exc:
                last_detail = f"timeout: {exc.__class__.__name__}"
                retryable = True
            except httpx.RequestError as exc:
                last_detail = f"request_error: {exc.__class__.__name__}"
                retryable = True
            else:
                if response.status_code == 200:
                    try:
                        data = response.json()
                    except ValueError as exc:
                        raise HttpAcquisitionError("INVALID_JSON_RESPONSE", url, str(exc)) from exc
                    if not isinstance(data, Mapping):
                        raise HttpAcquisitionError("JSON_OBJECT_REQUIRED", url, "top-level JSON must be an object")
                    return data

                last_detail = f"http_status={response.status_code}"
                retryable = response.status_code == 429 or 500 <= response.status_code <= 599
                if not retryable:
                    raise HttpAcquisitionError("HTTP_NONRETRYABLE", url, last_detail)

            if not retryable or attempt >= self.policy.max_attempts:
                break
            time.sleep(self.policy.backoff_seconds * attempt)

        raise HttpAcquisitionError("HTTP_RETRY_EXHAUSTED", url, last_detail)

    def close(self) -> None:
        self.client.close()
