"""Bounded, opt-in Firecrawl public-web evidence for V17 Scout.

Never a scoring, calibration, approval or execution capability. Every URL must
match a configured public-domain allowlist before contacting Firecrawl.
"""
from __future__ import annotations

import hashlib
import ipaddress
import os
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import httpx

try:
    from v17.research_source_adapters import ResearchFetchResult
except ModuleNotFoundError:
    from research_source_adapters import ResearchFetchResult

FIRECRAWL_SCRAPE_ENDPOINT = "https://api.firecrawl.dev/v2/scrape"
MAX_TARGETS_PER_REFRESH = 5
MAX_MARKDOWN_CHARS = 30000
SOURCE_CLASS = "PUBLIC_WEB_RESEARCH"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def configured_domains() -> tuple[str, ...]:
    return tuple(x.strip().lower().lstrip(".") for x in
                 os.environ.get("WOW_FIRECRAWL_ALLOWED_DOMAINS", "").split(",") if x.strip())


def _safe_url(value: str, domains: tuple[str, ...]) -> str | None:
    if not isinstance(value, str) or len(value) > 2048:
        return None
    try:
        parts = urlsplit(value.strip())
        host = (parts.hostname or "").lower().rstrip(".")
        port = parts.port
    except ValueError:
        return None
    if (parts.scheme != "https" or not host or parts.username is not None or
            parts.password is not None or port not in (None, 443) or
            host == "localhost" or host.endswith((".local", ".internal")) or
            "." not in host):
        return None
    try:
        ipaddress.ip_address(host)
        return None  # No IP literals, including public ones.
    except ValueError:
        pass
    if not any(host == d or host.endswith("." + d) for d in domains if "." in d):
        return None
    # URLs are administrator-configured evidence targets, never credential containers.
    if any(any(s in k.lower() for s in ("token", "secret", "password", "api_key", "apikey"))
           for k, _ in parse_qsl(parts.query, keep_blank_values=True)):
        return None
    return urlunsplit(("https", host, parts.path or "/", parts.query, ""))


def fetch_firecrawl_page(
    sport_key: str,
    capability: str,
    url: str,
    *,
    client: httpx.Client | None = None,
    allowed_domains: tuple[str, ...] | None = None,
) -> ResearchFetchResult:
    """Fetch one bounded page. Failure only blocks THIS optional research source."""
    now = _now_iso()
    source_url: str | None = None

    def result(ok: bool, code: str, *, data=None, status=None) -> ResearchFetchResult:
        return ResearchFetchResult(
            ok, "FIRECRAWL", str(sport_key), str(capability), SOURCE_CLASS,
            data=data if data is not None else ({"source_url": source_url, "evidence_only": True} if source_url else None),
            status=status, code=code, observed_at=now,
            endpoint=FIRECRAWL_SCRAPE_ENDPOINT,
            prediction_authority=False, can_execute=False,
        )

    if not isinstance(sport_key, str) or not sport_key or not isinstance(capability, str) or not capability:
        return result(False, "SOURCE_TARGET_INVALID")
    source_url = _safe_url(url, configured_domains() if allowed_domains is None else allowed_domains)
    if source_url is None:
        return result(False, "SOURCE_TARGET_NOT_ALLOWLISTED")
    key = os.environ.get("FIRECRAWL_API_KEY") or os.environ.get("WOW_FIRECRAWL_API_KEY")
    if not key:
        return result(False, "SOURCE_CREDENTIAL_UNCONFIGURED")

    owns_client = client is None
    http = client or httpx.Client(timeout=15.0)
    try:
        response = http.post(
            FIRECRAWL_SCRAPE_ENDPOINT,
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            json={"url": source_url, "formats": ["markdown"], "onlyMainContent": True},
        )
        if response.status_code >= 400:
            code = ("SOURCE_RATE_LIMITED" if response.status_code == 429 else
                    "SOURCE_AUTH_FAILED" if response.status_code in (401, 403) else
                    "SOURCE_UPSTREAM_UNAVAILABLE" if response.status_code >= 500 else
                    "SOURCE_UPSTREAM_REJECTED")
            return result(False, code, status=response.status_code)
        try:
            body = response.json()
        except ValueError:
            return result(False, "SOURCE_INVALID_JSON", status=response.status_code)
        if not isinstance(body, dict) or body.get("success") is not True:
            return result(False, "SOURCE_UPSTREAM_REJECTED", status=response.status_code)
        page = body.get("data")
        if not isinstance(page, dict):
            return result(False, "SOURCE_OUTPUT_INVALID", status=response.status_code)
        markdown = page.get("markdown")
        metadata = page.get("metadata") or {}
        if not isinstance(markdown, str) or not markdown.strip() or not isinstance(metadata, dict):
            return result(False, "SOURCE_EMPTY_CONTENT", status=response.status_code)
        resolved_url = metadata.get("sourceURL") or metadata.get("url") or source_url
        checked_resolved_url = _safe_url(resolved_url, configured_domains() if allowed_domains is None else allowed_domains)
        if checked_resolved_url is None:
            return result(False, "SOURCE_REDIRECT_NOT_ALLOWLISTED", status=response.status_code)
        payload = {
            "source_url": source_url,
            "resolved_url": checked_resolved_url,
            "title": str(metadata.get("title") or "")[:300],
            "markdown": markdown[:MAX_MARKDOWN_CHARS],
            "content_sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
            "content_truncated": len(markdown) > MAX_MARKDOWN_CHARS,
            "page_claimed_published_at": metadata.get("publishedTime"),
            "evidence_only": True,
        }
        return result(True, "SOURCE_FETCH_OK", data=payload, status=response.status_code)
    except httpx.TimeoutException:
        return result(False, "SOURCE_TIMEOUT")
    except httpx.HTTPError:
        return result(False, "SOURCE_HTTP_ERROR")
    finally:
        if owns_client:
            http.close()


__all__ = ["fetch_firecrawl_page", "configured_domains", "MAX_TARGETS_PER_REFRESH"]
