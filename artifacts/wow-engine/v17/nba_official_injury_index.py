"""Research-only discovery of official NBA injury-report PDF links.

Accepts HTML already retrieved through a separately authorized source reader.
No network, PDF content retrieval, credentials, storage, sportsbook price,
model inference, or promotion. A filename timestamp is NOT independent
source-clock attestation. A discovered report URL is NOT a player status.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
import re
from typing import Any
from zoneinfo import ZoneInfo

SCHEMA = "WOW_NBA_OFFICIAL_INJURY_INDEX_DISCOVERY_V1"
SOURCE_INDEX_URL = "https://official.nba.com/nba-injury-report-2026-27-season/"
REPORT_ROOT = "https://ak-static.cms.nba.com/referee/injury/"
MAX_HTML_BYTES = 600_000
REPORT_RE = re.compile(
    r"^https://ak-static\.cms\.nba\.com/referee/injury/"
    r"Injury-Report_(\d{4}-\d{2}-\d{2})_(\d{2})_(\d{2})(AM|PM)\.pdf$"
)
ET = ZoneInfo("America/New_York")
CAN_EXECUTE = False


class NBAInjuryIndexHold(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            href = dict(attrs).get("href")
            if isinstance(href, str):
                self.hrefs.append(href)


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise NBAInjuryIndexHold("NBA_WEB_INJURY_INDEX_CLOCK_INVALID")
    return value.astimezone(timezone.utc)


def select_injury_report_link(
    html: bytes,
    *,
    target_date: date,
    captured_at: datetime,
    event_start_utc: datetime | None = None,
) -> dict[str, Any]:
    """Pick the latest official link in captured HTML with strictly earlier time.

    The report timestamp is inferred from its immutable-looking filename,
    not independently authenticated. Nothing here validates the report body.
    """
    captured = _utc(captured_at)
    start = _utc(event_start_utc) if event_start_utc is not None else None
    if (not isinstance(target_date, date) or isinstance(target_date, datetime)
            or (start is not None and start.astimezone(ET).date() != target_date)):
        raise NBAInjuryIndexHold("NBA_WEB_INJURY_INDEX_CLOCK_INVALID")
    if not isinstance(html, bytes) or not html or len(html) > MAX_HTML_BYTES:
        raise NBAInjuryIndexHold("NBA_WEB_INJURY_INDEX_CAPTURE_INVALID")
    try:
        source_text = html.decode("utf-8", errors="strict")
        parser = _Links()
        parser.feed(source_text)
        parser.close()
    except (ValueError, UnicodeError) as exc:
        raise NBAInjuryIndexHold("NBA_WEB_INJURY_INDEX_CAPTURE_INVALID") from exc
    candidates: dict[str, datetime] = {}
    for href in parser.hrefs:
        if "Injury-Report_" not in href:
            continue
        match = REPORT_RE.fullmatch(href)
        if match is None:
            raise NBAInjuryIndexHold("NBA_WEB_INJURY_INDEX_URL_UNTRUSTED")
        raw_date, hh, minute, meridiem = match.groups()
        try:
            report_date = date.fromisoformat(raw_date)
            hour, mm = int(hh), int(minute)
            if not (1 <= hour <= 12 and 0 <= mm <= 59):
                raise ValueError("clock out of range")
            hour = (hour % 12) + (12 if meridiem == "PM" else 0)
            report_time = datetime(report_date.year, report_date.month,
                                   report_date.day, hour, mm, tzinfo=ET)
        except ValueError as exc:
            raise NBAInjuryIndexHold("NBA_WEB_INJURY_INDEX_CLOCK_INVALID") from exc
        if report_date != target_date:
            continue
        candidates[href] = report_time.astimezone(timezone.utc)
    if not candidates:
        raise NBAInjuryIndexHold("NBA_WEB_INJURY_REPORT_LINK_MISSING")
    eligible = [(timestamp, url) for url, timestamp in candidates.items()
                if timestamp <= captured and (start is None or timestamp < start)]
    if not eligible:
        if all(ts > captured for ts in candidates.values()):
            raise NBAInjuryIndexHold("NBA_WEB_INJURY_REPORT_NOT_CAPTURED_YET")
        raise NBAInjuryIndexHold("NBA_WEB_INJURY_REPORT_NOT_PREGAME")
    chosen_time, chosen_url = max(eligible)
    # Preserve uncertainty about the report body and independently attested time.
    return {
        "schema": SCHEMA,
        "status": "NBA_OFFICIAL_REPORT_LINK_DISCOVERED_RESEARCH_ONLY",
        "source_index_url": SOURCE_INDEX_URL,
        "report_pdf_url": chosen_url,
        "report_time_inferred_utc": chosen_time.isoformat().replace("+00:00", "Z"),
        "captured_at": captured.isoformat().replace("+00:00", "Z"),
        "index_html_sha256": sha256(html).hexdigest(),
        "discovered_report_count": len(candidates),
        "timestamp_attestation": "NOT_ESTABLISHED",
        "source_access_rights": "REVIEW_REQUIRED",
        "report_content_verified": False,
        "player_status_verified": False,
        "lineup_verified": False,
        "pregame_timestamp_attested": False,
        "training_authorized": False,
        "probability_publishable": False,
        "production_write_authorized": False,
        "can_execute": False,
    }
