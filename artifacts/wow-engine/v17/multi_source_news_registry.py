"""Read-only provider inventory and editorial-metadata normalization for WOW V17.

This intentionally does NOT fetch third-party sources or create model features.
A source listing, key, trial, or headline cannot certify a production feed.
Actual acquisition requires a separate provider-specific, licensed adapter and
scheduled receipt-based acceptance. No probability, order, or execution path.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

MAX_EDITORIAL_ITEMS = 30
MAX_RSS_BYTES = 256_000


@dataclass(frozen=True)
class NewsSource:
    provider: str
    kind: str
    product_url: str
    route: str
    activation: str
    rationale: str


SOURCES = (
    NewsSource("GDELT", "HEADLINES", "https://www.gdeltproject.org/",
               "EXISTING_PUBLIC_DISCOVERY", "EXISTING_NEEDS_RUNTIME_RECEIPT",
               "Existing collector; 429s and actual articles must be audited per run."),
    NewsSource("ESPN_RSS", "HEADLINES", "https://www.espn.com/espn/rss/news",
               "OPTIONAL_EDITORIAL_METADATA", "RIGHTS_AND_ACCESS_REVIEW",
               "RSS endpoint access and automated-use rights are unverified."),
    NewsSource("NEWSAPI", "HEADLINES", "https://newsapi.org/s/us-sports-news-api",
               "OPTIONAL_EDITORIAL_METADATA", "PRODUCTION_LICENSE_REQUIRED",
               "Developer key is not authorized for production."),
    NewsSource("HIGHLIGHTLY", "STRUCTURED_SPORTS", "https://highlightly.net/sport-api/",
               "OPTIONAL_STRUCTURED_SOURCE", "KEY_COVERAGE_RIGHTS_REVIEW",
               "Free quota is limited; individual leagues/fields require verification."),
    NewsSource("SPORTSDATAIO_GLOBAL", "STRUCTURED_SPORTS", "https://sportsdata.io/global-api",
               "EXISTING_PROVIDER_NEW_PRODUCT", "GLOBAL_ENTITLEMENT_REQUIRED",
               "Existing league adapters do not establish Global API entitlement."),
    NewsSource("ALLSPORTSAPI", "STRUCTURED_SPORTS", "https://allsportsapi.com/",
               "OPTIONAL_STRUCTURED_SOURCE", "TRIAL_AND_CONTRACT_REVIEW",
               "Advertised trial is not a continuing production grant."),
    NewsSource("RSS_APP", "AGGREGATION_ONLY", "https://rss.app/en/docs/api/feeds",
               "OPTIONAL_RSS_CONVERTER", "KEY_AND_RIGHTS_REVIEW",
               "RSS conversion does not grant rights to underlying articles."),
)


def source_inventory() -> list[dict[str, Any]]:
    """Expose every requested source with a conservative current design status."""
    return [
        {**asdict(item), "operational_status": "NOT_VERIFIED",
         "prediction_authority": False, "model_feature_eligible": False,
         "model_rescore_verified": False, "can_execute": False}
        for item in SOURCES
    ]


def _iso_datetime(value: object, *, rfc822: bool = False) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = (parsedate_to_datetime(value) if rfc822 else
                  datetime.fromisoformat(value.replace("Z", "+00:00")))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _article_url(value: object, provider: str) -> str | None:
    if not isinstance(value, str) or len(value) > 2000:
        return None
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
        if (parts.scheme != "https" or not host or parts.username or
                parts.password or parts.port not in (None, 443) or
                host in ("localhost",) or host.endswith(".local")):
            return None
        # ESPN RSS must not be used to launder an untrusted external link.
        if provider == "ESPN_RSS" and host not in ("espn.com", "www.espn.com"):
            return None
        return value
    except ValueError:
        return None


def _normalize_rows(provider: str, rows: list[dict[str, Any]],
                    observed_at: datetime) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows[:MAX_EDITORIAL_ITEMS * 3]:
        if not isinstance(row, dict):
            continue
        url = _article_url(row.get("url"), provider)
        title = row.get("title")
        published = _iso_datetime(row.get("published_at"), rfc822=provider == "ESPN_RSS")
        if (not url or not isinstance(title, str) or not title.strip()
                or not published or published > observed_at + timedelta(minutes=1)
                or url in seen):
            continue
        seen.add(url)
        output.append({
            "article_id": hashlib.sha256((provider + "|" + url).encode()).hexdigest()[:32],
            "source_url": url,
            "title": title.strip()[:300],
            "source_provider": provider,
            "source_discovered_at_claim": observed_at.isoformat().replace("+00:00", "Z"),
            "source_published_at_claim": published.isoformat().replace("+00:00", "Z"),
            "official_event_identity_verified": False,
            "publisher_claim_verified": False,
            "historical_pregame_eligible": False,
            "model_feature_eligible": False,
            "model_rescore_verified": False,
            "prediction_authority": False,
            "evidence_only": True,
            "can_execute": False,
        })
        if len(output) >= MAX_EDITORIAL_ITEMS:
            break
    return output


def normalize_newsapi_headlines(payload: object, *, observed_at: datetime) -> list[dict[str, Any]]:
    """Development/test-only transformation. Does not authorize a NewsAPI request."""
    if (observed_at.tzinfo is None or not isinstance(payload, dict)
            or payload.get("status") != "ok" or not isinstance(payload.get("articles"), list)):
        return []
    rows = [
        {"url": article.get("url"), "title": article.get("title"),
         "published_at": article.get("publishedAt")}
        for article in payload["articles"][:MAX_EDITORIAL_ITEMS * 3]
        if isinstance(article, dict)
    ]
    return _normalize_rows("NEWSAPI", rows, observed_at.astimezone(timezone.utc))


def normalize_espn_rss(raw: bytes, *, observed_at: datetime) -> list[dict[str, Any]]:
    """Parse a provided sample/feed only; makes no request to ESPN."""
    if (observed_at.tzinfo is None or not isinstance(raw, bytes)
            or len(raw) > MAX_RSS_BYTES or b"<!DOCTYPE" in raw.upper()
            or b"<!ENTITY" in raw.upper()):
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    if root.tag != "rss":
        return []
    channel = root.find("channel")
    if channel is None:
        return []
    rows = [
        {"url": (item.findtext("link") or "").strip(),
         "title": (item.findtext("title") or "").strip(),
         "published_at": (item.findtext("pubDate") or "").strip()}
        for item in channel.findall("item")[:MAX_EDITORIAL_ITEMS * 3]
    ]
    return _normalize_rows("ESPN_RSS", rows, observed_at.astimezone(timezone.utc))


def source_receipt(provider: str, articles: list[dict[str, Any]],
                   observed_at: datetime) -> dict[str, Any]:
    """Only editorial discovery receipts. Never silently promote to sports truth."""
    if provider not in ("NEWSAPI", "ESPN_RSS"):
        raise ValueError("NEWS_SOURCE_NOT_EDITORIAL")
    if observed_at.tzinfo is None:
        raise ValueError("NEWS_SOURCE_TIMESTAMP_INVALID")
    return {
        "schema_version": "wow.v17.optional_news_discovery.v1",
        "provider": provider,
        "source_status": "RESEARCH_METADATA_ONLY" if articles else "NO_VALID_ARTICLES",
        "article_count": len(articles),
        "articles": articles[:MAX_EDITORIAL_ITEMS],
        "source_observed_at": observed_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "publisher_claim_verified": False, "official_event_identity_verified": False,
        "model_feature_eligible": False, "model_rescore_verified": False,
        "prediction_authority": False, "can_execute": False,
    }


__all__ = ["SOURCES", "source_inventory", "normalize_newsapi_headlines",
           "normalize_espn_rss", "source_receipt"]
