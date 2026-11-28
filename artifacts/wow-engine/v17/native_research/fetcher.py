"""WOW Native Crawler: bounded, authorized public retrieval.

- https only; every host (including redirect targets) must be on the approved list
- robots.txt is honoured for web hosts; documented public APIs follow their API terms
- per-domain minimum interval, per-run request and byte budgets
- conditional GET with ETag / Last-Modified; content hashing; in-run cache
- never sends cookies or credentials, never retries around 401/403/paywalls
"""
from __future__ import annotations

import hashlib
import time
import urllib.robotparser
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

USER_AGENT = "WOW-NativeResearch/1.0 (+https://github.com/gregoryharper84-ship-it/WOW-Dashboard; evidence-only)"
# Documented public APIs: access is governed by their API terms, not robots.txt.
OFFICIAL_PUBLIC_APIS = frozenset({"api.github.com", "api.crossref.org", "api.gdeltproject.org"})
DEFAULT_APPROVED_DOMAINS = frozenset({"api.github.com", "raw.githubusercontent.com", "api.crossref.org",
                                      "api.gdeltproject.org"})
_TRACKING_PARAMS = ("utm_", "fbclid", "gclid")


class FetchBlocked(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}:{detail}" if detail else code)
        self.code = code


@dataclass(frozen=True)
class FetchResult:
    url: str
    canonical_url: str
    status: int
    content: bytes | None
    content_sha256: str | None
    etag: str | None
    last_modified: str | None
    not_modified: bool
    truncated: bool
    content_type: str | None
    retrieved_at: str
    access_basis: str

    def text(self) -> str:
        return (self.content or b"").decode("utf-8", errors="replace")


def canonical_url(url: str) -> str:
    parts = urlsplit(str(url).strip())
    if parts.scheme.lower() != "https" or not parts.hostname:
        raise FetchBlocked("NRE_URL_NOT_HTTPS", str(url)[:200])
    if parts.username or parts.password:
        raise FetchBlocked("NRE_URL_CREDENTIALS_FORBIDDEN")
    host = parts.hostname.lower().rstrip(".")
    netloc = host if parts.port in (None, 443) else f"{host}:{parts.port}"
    query = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                   if not k.lower().startswith(_TRACKING_PARAMS))
    return urlunsplit(("https", netloc, parts.path or "/", urlencode(query), ""))


def host_approved(host: str, approved: Iterable[str]) -> bool:
    host = host.lower()
    return any(host == d or host.endswith("." + d) for d in approved)


class NativeFetcher:
    def __init__(self, *, approved_domains: Iterable[str] = DEFAULT_APPROVED_DOMAINS,
                 max_requests: int = 40, max_bytes: int = 24_000_000, per_file_bytes: int = 6_000_000,
                 min_interval_s: float = 1.0, timeout_s: float = 20.0, session: Any = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 deadline_s: float | None = None):
        if session is None:
            import requests
            session = requests.Session()
        self.approved = frozenset(d.lower() for d in approved_domains)
        self.max_requests = max_requests
        self.max_bytes = max_bytes
        self.per_file_bytes = per_file_bytes
        self.min_interval_s = min_interval_s
        self.timeout_s = timeout_s
        self.session = session
        self.clock = clock
        self.sleep = sleep
        self.deadline = (clock() + deadline_s) if deadline_s else None
        self.requests_used = 0
        self.bytes_used = 0
        self._last_hit: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._cache: dict[tuple[str, str | None], FetchResult] = {}
        self.log: list[dict[str, Any]] = []

    def budget(self) -> dict[str, Any]:
        return {"requests_used": self.requests_used, "max_requests": self.max_requests,
                "bytes_used": self.bytes_used, "max_bytes": self.max_bytes}

    # -- internals -----------------------------------------------------------
    def _spend(self, host: str) -> None:
        if self.deadline is not None and self.clock() > self.deadline:
            raise FetchBlocked("NRE_RETRIEVAL_DEADLINE_EXCEEDED")
        if self.requests_used >= self.max_requests:
            raise FetchBlocked("NRE_RETRIEVAL_BUDGET_EXHAUSTED", "requests")
        if self.bytes_used >= self.max_bytes:
            raise FetchBlocked("NRE_RETRIEVAL_BUDGET_EXHAUSTED", "bytes")
        last = self._last_hit.get(host)
        if last is not None:
            wait = self.min_interval_s - (self.clock() - last)
            if wait > 0:
                self.sleep(wait)
        self._last_hit[host] = self.clock()
        self.requests_used += 1

    def _raw_get(self, url: str, headers: dict[str, str], limit: int) -> tuple[Any, bytes, bool]:
        response = self.session.get(url, headers=headers, timeout=self.timeout_s,
                                    allow_redirects=False, stream=True)
        body = bytearray()
        truncated = False
        if response.status_code == 200:
            for chunk in response.iter_content(65536):
                body.extend(chunk)
                if len(body) > limit:
                    del body[limit:]
                    truncated = True
                    break
        response.close()
        self.bytes_used += len(body)
        return response, bytes(body), truncated

    def _robots_allows(self, url: str) -> bool:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if host not in self._robots:
            robots_url = f"https://{host}/robots.txt"
            self._spend(host)
            try:
                response, body, _ = self._raw_get(robots_url, {"User-Agent": USER_AGENT}, 512_000)
            except Exception:  # noqa: BLE001 - unreachable robots fails closed
                self._robots[host] = None
                return False
            status = response.status_code
            if status in (404, 410):
                parser = urllib.robotparser.RobotFileParser()
                parser.parse([])
            elif status == 200:
                parser = urllib.robotparser.RobotFileParser()
                parser.parse(body.decode("utf-8", errors="replace").splitlines())
            else:  # 401/403/429/5xx: no permission evidence, fail closed
                parser = None
            self._robots[host] = parser
        parser = self._robots[host]
        return bool(parser and parser.can_fetch(USER_AGENT, url))

    # -- public --------------------------------------------------------------
    def get(self, url: str, *, etag: str | None = None, last_modified: str | None = None,
            accept: str | None = None, max_bytes: int | None = None) -> FetchResult:
        current = canonical_url(url)
        key = (current, accept)
        if key in self._cache:
            return self._cache[key]
        for _hop in range(4):
            host = urlsplit(current).hostname or ""
            if not host_approved(host, self.approved):
                raise FetchBlocked("NRE_DOMAIN_NOT_APPROVED", host)
            basis = "OFFICIAL_PUBLIC_API" if host in OFFICIAL_PUBLIC_APIS else "ROBOTS_PERMITTED"
            if basis == "ROBOTS_PERMITTED" and not self._robots_allows(current):
                raise FetchBlocked("NRE_ROBOTS_DISALLOWED", current)
            headers = {"User-Agent": USER_AGENT}
            if accept:
                headers["Accept"] = accept
            if etag:
                headers["If-None-Match"] = etag
            if last_modified:
                headers["If-Modified-Since"] = last_modified
            self._spend(host)
            try:
                response, body, truncated = self._raw_get(current, headers, max_bytes or self.per_file_bytes)
            except FetchBlocked:
                raise
            except Exception as exc:  # noqa: BLE001
                raise FetchBlocked("NRE_SOURCE_UNREACHABLE", type(exc).__name__) from exc
            status = response.status_code
            self.log.append({"url": current, "status": status, "bytes": len(body)})
            if status in (301, 302, 303, 307, 308):
                location = response.headers.get("Location")
                if not location:
                    raise FetchBlocked("NRE_REDIRECT_INVALID", current)
                current = canonical_url(urljoin(current, location))
                continue
            if status in (401, 402, 403, 407, 451):
                # Access control, paywall or legal block: never worked around.
                if status == 403 and response.headers.get("X-RateLimit-Remaining") == "0":
                    raise FetchBlocked("NRE_SOURCE_RATE_LIMITED", current)
                raise FetchBlocked("NRE_ACCESS_RESTRICTED", f"{status}:{current}")
            if status == 429:
                raise FetchBlocked("NRE_SOURCE_RATE_LIMITED", current)
            if status == 404:
                raise FetchBlocked("NRE_SOURCE_NOT_FOUND", current)
            if status not in (200, 304):
                raise FetchBlocked("NRE_SOURCE_HTTP_FAILED", f"{status}:{current}")
            result = FetchResult(
                url=url, canonical_url=current, status=status,
                content=body if status == 200 else None,
                content_sha256=hashlib.sha256(body).hexdigest() if status == 200 else None,
                etag=response.headers.get("ETag"), last_modified=response.headers.get("Last-Modified"),
                not_modified=status == 304, truncated=truncated,
                content_type=response.headers.get("Content-Type"),
                retrieved_at=datetime.now(timezone.utc).isoformat(), access_basis=basis)
            self._cache[key] = result
            return result
        raise FetchBlocked("NRE_REDIRECT_LIMIT", current)

    def get_json(self, url: str, **kwargs: Any) -> Any:
        import json

        result = self.get(url, accept="application/json", **kwargs)
        if result.truncated:
            raise FetchBlocked("NRE_DOCUMENT_TRUNCATED", result.canonical_url)
        try:
            return json.loads(result.text())
        except ValueError as exc:
            raise FetchBlocked("NRE_SOURCE_SCHEMA_INVALID", result.canonical_url) from exc
