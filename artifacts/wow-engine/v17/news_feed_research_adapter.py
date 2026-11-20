"""Public GDELT Project news discovery; never an official sport or model source.

Single endpoint, no credentials, no provider registry, no paid-source path.
Articles are unverified discovery metadata, not historical pregame features.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import httpx

try:
    from v17.firecrawl_research_adapter import _safe_url
    from v17.research_source_adapters import ResearchFetchResult
    from v17.web_intelligence_sport_profiles import WEIGHT_PROFILES, normalize_sport
except ModuleNotFoundError:
    from firecrawl_research_adapter import _safe_url
    from research_source_adapters import ResearchFetchResult
    from web_intelligence_sport_profiles import WEIGHT_PROFILES, normalize_sport

PROVIDER = "GDELT"
SOURCE_CLASS = "PUBLIC_WEB_RESEARCH"
GDELT_HOST = "api.gdeltproject.org"
GDELT_PATH = "/api/v2/doc/doc"
MAX_NEWS_BODY_BYTES = 256_000
MAX_NEWS_ITEMS = 30
ALLOWED_QUERY_KEYS = {"query", "mode", "format", "timespan", "maxrecords", "sort"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _seen_at(value: object) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc) if isinstance(value, str) else None
    except ValueError:
        return None


def normalize_articles(payload: object, *, observed_at: datetime) -> list[dict]:
    """Keep just GDELT article metadata, never download the underlying story."""
    if not isinstance(payload, dict) or not isinstance(payload.get("articles"), list):
        return []
    output = []
    seen_urls: set[str] = set()
    for record in payload["articles"][:MAX_NEWS_ITEMS]:
        if not isinstance(record, dict):
            continue
        raw_url, title = record.get("url"), record.get("title")
        if not isinstance(raw_url, str) or not isinstance(title, str) or not title.strip():
            continue
        try:
            host = (urlsplit(raw_url).hostname or "").lower().rstrip(".")
            url = _safe_url(raw_url, (host,)) if host else None
        except ValueError:
            url = None
        seen_at = _seen_at(record.get("seendate"))
        if not url or not seen_at or seen_at > observed_at + timedelta(minutes=1) or url in seen_urls:
            continue
        seen_urls.add(url)
        output.append({
            "article_id": hashlib.sha256(("GDELT|" + url).encode()).hexdigest()[:32],
            "source_url": url,
            "source_provider": PROVIDER,
            "source_discovered_at_claim": seen_at.isoformat().replace("+00:00", "Z"),
            "source_published_at_claim": None,
            "title": title.strip()[:300],
            "official_event_identity_verified": False,
            "publisher_claim_verified": False,
            "historical_pregame_eligible": False,
            "evidence_only": True,
        })
    return output


def fetch_gdelt_news(sport_key: str, url: str, *, client: httpx.Client | None = None) -> ResearchFetchResult:
    """One bounded read of official GDELT Project DOC 2.0 ArtList JSON."""
    def blocked(code: str, status: int | None = None) -> ResearchFetchResult:
        seen = _now().isoformat().replace("+00:00", "Z")
        return ResearchFetchResult(
            False, PROVIDER, sport_key, "news", SOURCE_CLASS,
            data={"source_observed_at": seen, "source_code": code, "evidence_only": True,
                  "attribution": "The GDELT Project (https://www.gdeltproject.org/)"},
            observed_at=seen, code=code, status=status,
            prediction_authority=False, can_execute=False,
        )

    if normalize_sport(sport_key) not in WEIGHT_PROFILES:
        return blocked("SOURCE_TARGET_INVALID")
    endpoint = _safe_url(url, (GDELT_HOST,))
    if endpoint is None:
        return blocked("SOURCE_TARGET_NOT_ALLOWLISTED")
    parsed = urlsplit(endpoint)
    params = parse_qs(parsed.query, keep_blank_values=True)
    if (parsed.hostname != GDELT_HOST or parsed.path != GDELT_PATH or
        set(params) - ALLOWED_QUERY_KEYS or
        [x.lower() for x in params.get("mode", [])] != ["artlist"] or
        [x.lower() for x in params.get("format", [])] != ["json"] or
        len(params.get("query", [])) != 1 or
        not params["query"][0].strip() or len(params["query"][0]) > 300 or
        any(len(v) != 1 for v in params.values())):
        return blocked("SOURCE_GDELT_QUERY_INVALID")
    if "maxrecords" in params:
        try:
            count = int(params["maxrecords"][0])
        except ValueError:
            return blocked("SOURCE_GDELT_QUERY_INVALID")
        if not 1 <= count <= MAX_NEWS_ITEMS:
            return blocked("SOURCE_GDELT_QUERY_INVALID")

    owned = client is None
    http = client or httpx.Client(timeout=12, follow_redirects=False)
    try:
        # No Authorization, cookies, commercial tokens or redirects.
        with http.stream("GET", endpoint,
                         headers={"Accept": "application/json", "Accept-Encoding": "identity"},
                         follow_redirects=False, timeout=12) as response:
            status = response.status_code
            if 300 <= status < 400:
                return blocked("SOURCE_REDIRECT_NOT_ALLOWED", status)
            if status >= 400:
                reason = ("SOURCE_RATE_LIMITED" if status == 429 else
                          "SOURCE_UPSTREAM_UNAVAILABLE" if status >= 500 else
                          "SOURCE_UPSTREAM_REJECTED")
                return blocked(reason, status)
            if response.headers.get("Content-Encoding", "identity").strip().lower() not in ("", "identity"):
                return blocked("SOURCE_CONTENT_ENCODING_UNSUPPORTED", status)
            try:
                declared = int(response.headers.get("Content-Length", "0"))
            except ValueError:
                declared = 0
            if declared > MAX_NEWS_BODY_BYTES:
                return blocked("SOURCE_BODY_TOO_LARGE", status)
            data = bytearray()
            for chunk in response.iter_raw(chunk_size=65536):
                data.extend(chunk)
                if len(data) > MAX_NEWS_BODY_BYTES:
                    return blocked("SOURCE_BODY_TOO_LARGE", status)
        try:
            payload = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            return blocked("SOURCE_INVALID_JSON", status)
        if not isinstance(payload, dict) or not isinstance(payload.get("articles"), list):
            return blocked("SOURCE_OUTPUT_INVALID", status)
        observed = _now()
        articles = normalize_articles(payload, observed_at=observed)
        if not articles:
            return blocked("SOURCE_NO_VALID_NEWS", status)
        stamp = observed.isoformat().replace("+00:00", "Z")
        return ResearchFetchResult(
            True, PROVIDER, sport_key, "news", SOURCE_CLASS,
            data={"source_observed_at": stamp, "articles": articles,
                  "attribution": "The GDELT Project (https://www.gdeltproject.org/)",
                  "verified_event_count": 0, "evidence_only": True},
            code="SOURCE_FETCH_OK", status=status, observed_at=stamp,
            endpoint=endpoint, prediction_authority=False, can_execute=False,
        )
    except httpx.TimeoutException:
        return blocked("SOURCE_TIMEOUT")
    except httpx.HTTPError:
        return blocked("SOURCE_HTTP_ERROR")
    except httpx.StreamError:
        return blocked("SOURCE_STREAM_ERROR")
    finally:
        if owned:
            http.close()


__all__ = ["fetch_gdelt_news", "normalize_articles", "MAX_NEWS_BODY_BYTES", "MAX_NEWS_ITEMS"]
