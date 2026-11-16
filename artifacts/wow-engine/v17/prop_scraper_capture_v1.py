"""Fail-closed public-web scraper intake for WOW V17 prop research.

This is an acquisition boundary, not a new scoring or publishing pathway.
No source is enabled by default. A separately reviewed code change must add
an explicit source-specific URL and automated-reuse permission to the live
registry. A license in a historical-source catalog is not scraper permission.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from http.client import HTTPException
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.robotparser import RobotFileParser
import math
import re

SCHEMA = "WOW_V17_PROP_SCRAPER_CAPTURE_V1"
USER_AGENT = "WOW-V17-SourceResearch/1.0 (bounded; no login)"
MAX_HTML_BYTES = 1_000_000
MAX_ROBOTS_BYTES = 50_000
MAX_ROWS = 500
CAN_EXECUTE = False


class PropScrapeHold(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class SourceScrapePermit:
    source_id: str
    allowed_urls: tuple[str, ...]
    rights_receipt_id: str
    automated_fetch_authorized: bool = False

    def __post_init__(self) -> None:
        if (not self.source_id or not self.rights_receipt_id
                or not self.allowed_urls or len(self.allowed_urls) > 20
                or len(set(self.allowed_urls)) != len(self.allowed_urls)):
            raise PropScrapeHold("PROP_SCRAPE_POLICY_INVALID")
        for url in self.allowed_urls:
            _safe_url(url)


# Intentionally empty: no currently attested permission for automated
# PropsMadness extraction, retained historical web copies or model training.
# A source is added only through independent source-rights review and code review.
APPROVED_LIVE_SCRAPE_SOURCES: dict[str, SourceScrapePermit] = {}


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request: Any, fp: Any, code: int,
                         msg: str, headers: Any, newurl: str) -> None:
        raise PropScrapeHold("PROP_SCRAPE_REDIRECT_BLOCKED")


def _safe_url(url: str) -> str:
    if not isinstance(url, str) or any(c.isspace() for c in url):
        raise PropScrapeHold("PROP_SCRAPE_URL_NOT_APPROVED")
    parsed = urlsplit(url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise PropScrapeHold("PROP_SCRAPE_URL_NOT_APPROVED") from exc
    if (parsed.scheme != "https" or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or port not in (None, 443) or parsed.fragment
            or not re.fullmatch(r"[a-zA-Z0-9.-]+", parsed.hostname)
            or parsed.hostname.lower() in {"localhost", "metadata.google.internal"}
            or parsed.hostname.startswith(("127.", "10.", "192.168.", "169.254."))
            or parsed.hostname.endswith((".local", ".internal"))
            or re.fullmatch(r"\d+(?:\.\d+){3}", parsed.hostname)):
        raise PropScrapeHold("PROP_SCRAPE_URL_NOT_APPROVED")
    return f"https://{parsed.hostname}"


def _utc(value: datetime) -> str:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise PropScrapeHold("PROP_SCRAPE_CAPTURE_TIME_INVALID")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _read(url: str, *, limit: int, content_type: str,
          opener: Callable[..., Any]) -> bytes:
    try:
        with opener(Request(url, headers={"User-Agent": USER_AGENT}),
                    timeout=8) as response:
            if response.geturl() != url:
                raise PropScrapeHold("PROP_SCRAPE_REDIRECT_BLOCKED")
            if response.status != 200:
                raise PropScrapeHold("PROP_SCRAPE_HTTP_REJECTED")
            mime = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if mime != content_type:
                raise PropScrapeHold("PROP_SCRAPE_CONTENT_TYPE_INVALID")
            declared_length = response.headers.get("Content-Length")
            if declared_length is not None:
                if (not isinstance(declared_length, str)
                        or re.fullmatch(r"[0-9]{1,15}", declared_length) is None):
                    raise PropScrapeHold("PROP_SCRAPE_SOURCE_UNREACHABLE")
                if int(declared_length) > limit:
                    raise PropScrapeHold("PROP_SCRAPE_PAYLOAD_INVALID")
            data = response.read(limit + 1)
            if declared_length is not None and len(data) != int(declared_length):
                raise PropScrapeHold("PROP_SCRAPE_SOURCE_UNREACHABLE")
    except PropScrapeHold:
        raise
    except HTTPError as exc:
        if exc.code in (401, 403):
            code = "PROP_SCRAPE_ACCESS_DENIED"
        elif exc.code == 429:
            code = "PROP_SCRAPE_RATE_LIMITED"
        else:
            code = "PROP_SCRAPE_HTTP_REJECTED"
        raise PropScrapeHold(code) from exc
    except (URLError, OSError, TimeoutError, HTTPException) as exc:
        raise PropScrapeHold("PROP_SCRAPE_SOURCE_UNREACHABLE") from exc
    if not isinstance(data, bytes) or not data or len(data) > limit:
        raise PropScrapeHold("PROP_SCRAPE_PAYLOAD_INVALID")
    return data


_ALLOWED_ROW_FIELDS = frozenset({
    "participant_alias", "stat_type", "period", "display_line", "fixture_text",
})


def _research_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping):
        raise PropScrapeHold("PROP_SCRAPE_PARSER_INVALID")
    raw = payload.get("rows")
    if not isinstance(raw, list) or len(raw) > MAX_ROWS:
        raise PropScrapeHold("PROP_SCRAPE_PARSER_INVALID")
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, float, str]] = set()
    for row in raw:
        if not isinstance(row, Mapping) or not set(row) <= _ALLOWED_ROW_FIELDS:
            raise PropScrapeHold("PROP_SCRAPE_AUTHORITY_INJECTION")
        name, stat, period = (row.get("participant_alias"),
                              row.get("stat_type"), row.get("period"))
        line = row.get("display_line")
        if (not all(isinstance(x, str) and 0 < len(x.strip()) <= 100
                    for x in (name, stat, period))
                or type(line) not in (float, int)
                or not 0 < line <= 10000 or not math.isfinite(line)):
            raise PropScrapeHold("PROP_SCRAPE_ROW_INVALID")
        fixture = row.get("fixture_text")
        if fixture is not None and not isinstance(fixture, str):
            raise PropScrapeHold("PROP_SCRAPE_ROW_INVALID")
        fixture_label = (fixture or "").strip()[:150]
        key = (name.strip(), stat.strip(), period.strip(),
               float(line), fixture_label)
        if key in seen:
            raise PropScrapeHold("PROP_SCRAPE_DUPLICATE_ROW")
        seen.add(key)
        result.append({
            "participant_alias_unverified": key[0],
            "stat_type_display": key[1],
            "period_display": key[2],
            "line_display": key[3],
            "fixture_text_unverified": key[4],
            "canonical_player_id": None,
            "canonical_event_id": None,
            "book": None,
            "model_probability": None,
            "can_execute": False,
        })
    return result



def _validated_robots(robots_text: str, url: str) -> None:
    """Require an applicable policy instead of relying on default-allow.

    The standard robot parser treats '*' and '$' in paths literally, so
    unsupported wildcard directives are held rather than silently ignored.
    """
    directives = {
        "user-agent", "allow", "disallow", "crawl-delay", "request-rate",
        "sitemap", "host",
    }
    clean: list[str] = []
    for raw in robots_text.splitlines():
        line = raw.partition("#")[0].strip()
        if not line:
            continue
        if ":" not in line:
            raise PropScrapeHold("PROP_SCRAPE_ROBOTS_UNVERIFIED")
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key not in directives or (key == "user-agent" and not value):
            raise PropScrapeHold("PROP_SCRAPE_ROBOTS_UNVERIFIED")
        if key in {"allow", "disallow"} and (
                "*" in value or "$" in value
                or "%2a" in value.lower() or "%24" in value.lower()):
            raise PropScrapeHold("PROP_SCRAPE_ROBOTS_UNVERIFIED")
        clean.append(line)
    robots = RobotFileParser()
    robots.parse(clean)
    selected = next(
        (entry for entry in robots.entries if entry.applies_to(USER_AGENT)),
        robots.default_entry,
    )
    if selected is None or not selected.rulelines:
        raise PropScrapeHold("PROP_SCRAPE_ROBOTS_UNVERIFIED")
    if not robots.can_fetch(USER_AGENT, url):
        raise PropScrapeHold("PROP_SCRAPE_ROBOTS_DISALLOW")

def _capture_with_permit(*, permit: SourceScrapePermit, url: str,
                         parse_html: Callable[[bytes], Mapping[str, Any]],
                         captured_at: datetime,
                         opener: Callable[..., Any]) -> dict[str, Any]:
    """Internal tested worker; caller supplies an independently approved permit."""
    if permit.automated_fetch_authorized is not True:
        raise PropScrapeHold("PROP_SCRAPE_RIGHTS_NOT_APPROVED")
    origin = _safe_url(url)
    if url not in permit.allowed_urls:
        raise PropScrapeHold("PROP_SCRAPE_URL_NOT_APPROVED")
    robots_url = origin + "/robots.txt"
    robots_bytes = _read(robots_url, limit=MAX_ROBOTS_BYTES,
                         content_type="text/plain", opener=opener)
    try:
        robots_text = robots_bytes.decode("utf-8")
    except UnicodeError as exc:
        raise PropScrapeHold("PROP_SCRAPE_ROBOTS_UNVERIFIED") from exc
    _validated_robots(robots_text, url)
    html = _read(url, limit=MAX_HTML_BYTES,
                 content_type="text/html", opener=opener)
    try:
        parsed = parse_html(html)
    except PropScrapeHold:
        raise
    except Exception as exc:
        raise PropScrapeHold("PROP_SCRAPE_PARSER_FAILED") from exc
    rows = _research_rows(parsed)
    return {
        "schema": SCHEMA,
        "status": "RESEARCH_ROWS_PARSED" if rows else "RESEARCH_EMPTY_NOT_NO_SLATE",
        "source_id": permit.source_id,
        "source_url": url,
        "rights_receipt_id": permit.rights_receipt_id,
        "captured_at": _utc(captured_at),
        "html_sha256": sha256(html).hexdigest(),
        "request_count": 2,
        "row_count": len(rows),
        "rows": rows,
        "source_identity_verified": False,
        "pregame_snapshot_verified": False,
        "historical_training_authorized": False,
        "market_quote_verified": False,
        "probability_publishable": False,
        "production_write_authorized": False,
        "can_execute": False,
    }


def scrape_approved_once(source_id: str, url: str, *,
                         parse_html: Callable[[bytes], Mapping[str, Any]],
                         captured_at: datetime | None = None) -> dict[str, Any]:
    """Only code-reviewed sources can fetch. No retries, storage or scheduling."""
    permit = APPROVED_LIVE_SCRAPE_SOURCES.get(source_id)
    if permit is None:
        raise PropScrapeHold("PROP_SCRAPE_SOURCE_NOT_APPROVED")
    opener = build_opener(_NoRedirects()).open
    return _capture_with_permit(
        permit=permit, url=url, parse_html=parse_html,
        captured_at=captured_at or datetime.now(timezone.utc), opener=opener,
    )
