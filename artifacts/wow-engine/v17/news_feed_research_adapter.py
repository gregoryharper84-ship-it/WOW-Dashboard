"""Optional licensed JSON news discovery for existing WOW V17 Scout snapshots.

This adapter captures bounded, untrusted publisher claims for research only.
No article is an independently verified injury, lineup, official event or model
feature. Model probabilities and terminal/execution authority are untouched.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import httpx

try:
    from v17.firecrawl_research_adapter import _safe_url
    from v17.research_source_adapters import ResearchFetchResult
    from v17.web_intelligence_sport_profiles import normalize_sport, WEIGHT_PROFILES
except ModuleNotFoundError:
    from firecrawl_research_adapter import _safe_url
    from research_source_adapters import ResearchFetchResult
    from web_intelligence_sport_profiles import normalize_sport, WEIGHT_PROFILES

MAX_NEWS_TARGETS = 5
MAX_NEWS_BODY_BYTES = 512_000
MAX_NEWS_ITEMS = 40
SOURCE_CLASS = "PUBLIC_WEB_RESEARCH"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _timestamp(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        value = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return value.astimezone(timezone.utc) if value.utcoffset() is not None else None


def _article_url(raw: object) -> str | None:
    """Validate article URL for evidence storage; never fetch it."""
    if not isinstance(raw, str):
        return None
    try:
        hostname = (urlsplit(raw).hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    return _safe_url(raw, (hostname,)) if hostname else None


def normalize_articles(
    payload: object,
    *,
    observed_at: datetime,
    provider: str,
) -> list[dict]:
    """Normalize canonical feed JSON; never trust publisher identity/status."""
    if not isinstance(payload, dict) or not isinstance(payload.get("articles"), list):
        return []
    unique: set[str] = set()
    results: list[dict] = []
    for article in payload["articles"][:MAX_NEWS_ITEMS]:
        if not isinstance(article, dict):
            continue
        url = _article_url(article.get("url"))
        published = _timestamp(article.get("published_at"))
        title = article.get("title")
        if not url or not published or not isinstance(title, str) or not title.strip():
            continue
        # Publisher timestamps are claims and cannot establish prelock availability.
        if published > observed_at + timedelta(minutes=1) or url in unique:
            continue
        unique.add(url)
        summary = article.get("summary")
        provider_event_id = article.get("event_id")
        results.append({
            "article_id": hashlib.sha256((provider + "|" + url).encode()).hexdigest()[:32],
            "source_url": url,
            "source_provider": provider,
            "source_published_at_claim": published.isoformat().replace("+00:00", "Z"),
            "title": title.strip()[:300],
            "summary_excerpt": summary.strip()[:1000] if isinstance(summary, str) else "",
            "provider_event_id": str(provider_event_id)[:120] if provider_event_id is not None else None,
            "official_event_identity_verified": False,
            "publisher_claim_verified": False,
            "historical_pregame_eligible": False,
            "evidence_only": True,
        })
    return results


def fetch_news_json(
    sport_key: str,
    provider: str,
    url: str,
    *,
    client: httpx.Client | None = None,
    allowed_domains: tuple[str, ...] | None = None,
    license_ack: bool | None = None,
) -> ResearchFetchResult:
    """Fetch a specifically authorized JSON source, never arbitrary web content."""
    now = _now()
    normalized = normalize_sport(sport_key)
    provider = provider.strip().upper() if isinstance(provider, str) else ""
    domains = (allowed_domains if allowed_domains is not None else
               tuple(x.strip().lower() for x in os.environ.get(
                   "WOW_NEWS_FEED_ALLOWED_DOMAINS", "").split(",") if x.strip()))
    endpoint = _safe_url(url, domains)
    def failure(code: str, status: int | None = None) -> ResearchFetchResult:
        return ResearchFetchResult(
            False, provider or "NEWS_FEED", str(sport_key), "news",
            SOURCE_CLASS, status=status, code=code,
            observed_at=now.isoformat().replace("+00:00", "Z"),
            prediction_authority=False, can_execute=False,
        )

    if normalized not in WEIGHT_PROFILES or not re.fullmatch(r"[A-Z0-9_-]{2,40}", provider):
        return failure("SOURCE_TARGET_INVALID")
    if endpoint is None:
        return failure("SOURCE_TARGET_NOT_ALLOWLISTED")
    acknowledged = license_ack if license_ack is not None else (
        os.environ.get("WOW_NEWS_FEED_LICENSE_ACK", "").lower() == "true"
    )
    if not acknowledged:
        return failure("SOURCE_LICENSE_ACK_REQUIRED")

    key = os.environ.get("WOW_NEWS_FEED_API_KEY", "")
    # No query-string API credentials; HTTPS header only.
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    own_client = client is None
    http = client or httpx.Client(timeout=12.0, follow_redirects=False)
    try:
        with http.stream("GET", endpoint, headers=headers) as response:
            status = response.status_code
            if 300 <= status < 400:
                return failure("SOURCE_REDIRECT_NOT_ALLOWED", status)
            if status >= 400:
                code = ("SOURCE_RATE_LIMITED" if status == 429 else
                        "SOURCE_AUTH_FAILED" if status in (401, 403) else
                        "SOURCE_UPSTREAM_UNAVAILABLE" if status >= 500 else
                        "SOURCE_UPSTREAM_REJECTED")
                return failure(code, status)
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > MAX_NEWS_BODY_BYTES:
                    return failure("SOURCE_BODY_TOO_LARGE", status)
        try:
            parsed = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            return failure("SOURCE_INVALID_JSON", status)
        if not isinstance(parsed, dict) or not isinstance(parsed.get("articles"), list):
            return failure("SOURCE_OUTPUT_INVALID", status)
        articles = normalize_articles(parsed, observed_at=now, provider=provider)
        if not articles:
            return failure("SOURCE_NO_VALID_NEWS", status)
        return ResearchFetchResult(
            True, provider, str(sport_key), "news", SOURCE_CLASS,
            data={"articles": articles, "feed_endpoint": endpoint,
                  "news_schema": "wow.v17.news_discovery.v1",
                  "verified_event_count": 0,
                  "evidence_only": True},
            status=status, code="SOURCE_FETCH_OK",
            observed_at=now.isoformat().replace("+00:00", "Z"),
            endpoint=endpoint, prediction_authority=False, can_execute=False,
        )
    except httpx.TimeoutException:
        return failure("SOURCE_TIMEOUT")
    except httpx.HTTPError:
        return failure("SOURCE_HTTP_ERROR")
    finally:
        if own_client:
            http.close()


__all__ = ["fetch_news_json", "normalize_articles", "MAX_NEWS_TARGETS",
           "MAX_NEWS_BODY_BYTES", "MAX_NEWS_ITEMS"]
