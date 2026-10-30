"""Bounded public HTML extraction of NBA Explore listings.

This module reads *only* four explicit public pages. It never logs in, follows
private endpoints, bypasses paywalls/robot restrictions, persists data, trains
a model, or produces an official sporting/market probability.

A page listing is for candidate DISCOVERY only: public rows do not authenticate
their sportsbook, start time, player identity, or a historical pretip snapshot.
Website terms grant personal non-commercial access. Operators are responsible
for determining their permissible use before invoking the fetch function.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlparse
from urllib.request import Request, build_opener
from urllib.error import HTTPError, URLError
from urllib.robotparser import RobotFileParser
import hashlib
import json
import re
from typing import Any, Callable

CAN_EXECUTE = False
ROOT = "https://propsmadness.com"
PUBLIC_MARKETS = {
    "points": ("POINTS", "player-points"),
    "assists": ("ASSISTS", "player-assists"),
    "rebounds": ("REBOUNDS", "player-rebounds"),
    "threes": ("THREE_POINTERS_MADE", "player-threes"),
}
# Keep the human-facing market name separate from the actual public-page slug.
# The current NBA threes page is /made-threes, not /threes.
PUBLIC_PAGE_SLUGS = {"threes": "made-threes"}
USER_AGENT = "WOW-V17-Public-Research-Probe/1.0 (no-login; one-shot)"
MAX_HTML_BYTES = 2_000_000
POINT_IN_TIME_SOURCE_AUTHORITY = False
MARKET_PRICE_AUTHORITY = False
MODEL_AUTHORITY = False

GAME_RE = re.compile(
    r"\b(Points|Assists|Rebounds|Threes)\s+(\d+(?:\.\d+)?)\s*[·•]\s*"
    r"([A-Z]{2,3})\s*(@|vs\.?)\s*([A-Z]{2,3})\b", re.I,
)
ODDS_RE = re.compile(r"\bO\s+([+-]\d{3,5})\s+U\s+([+-]\d{3,5})\b")
AVG_RE = re.compile(r"\bL10\s+AVG\s+(\d+(?:\.\d+)?)\b", re.I)


def _source_url(market: str) -> str:
    if market not in PUBLIC_MARKETS:
        raise PublicPageHold("PM_PUBLIC_MARKET_NOT_ALLOWLISTED")
    return ROOT + "/nba/props/" + PUBLIC_PAGE_SLUGS.get(market, market)


class PublicPageHold(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class _AnchorsAndTokens(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tokens: list[tuple[str, str, str]] = []
        self._anchor: dict[str, Any] | None = None
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "noscript", "svg", "template"):
            self._skip += 1
            return
        if self._skip:
            return
        if tag == "a":
            self._anchor = {"href": dict(attrs).get("href", ""), "text": []}

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        val = " ".join(data.split())
        if not val:
            return
        if self._anchor is not None:
            self._anchor["text"].append(val)
        else:
            self.tokens.append(("text", val, ""))

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript", "svg", "template"):
            self._skip = max(self._skip - 1, 0)
            return
        if self._skip:
            return
        if tag == "a" and self._anchor is not None:
            value = self._anchor
            self.tokens.append(("link", " ".join(value["text"]), value["href"]))
            self._anchor = None


def _player_link(url: str, market_key: str) -> tuple[str, str] | None:
    absolute = urljoin(ROOT, url)
    target = urlparse(absolute)
    if target.scheme != "https" or target.hostname not in ("propsmadness.com", "www.propsmadness.com"):
        return None
    if target.path.rstrip("/") != "/nba":
        return None
    params = parse_qs(target.query, strict_parsing=False)
    expected_market = PUBLIC_MARKETS[market_key][1]
    permitted_markets = {expected_market}
    if market_key == "threes":
        permitted_markets.add("player-made-threes")
    market_values = params.get("market")
    if not market_values or len(market_values) != 1 or market_values[0] not in permitted_markets:
        return None
    player, match = params.get("player"), params.get("match")
    if not player or not match or len(player) != 1 or len(match) != 1:
        return None
    if not re.fullmatch(r"[0-9]{1,12}", player[0]) or not re.fullmatch(r"[0-9]{1,12}", match[0]):
        return None
    return player[0], match[0]


class _VisibleTableReader(HTMLParser):
    """Only visible td/anchor text from an ordinary public HTML table.

    Does not inspect Next.js state, scripts, hidden API payloads or image URLs.
    """
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[dict[str, Any]]] = []
        self._row: list[dict[str, Any]] | None = None
        self._cell: dict[str, Any] | None = None
        self._anchor: dict[str, Any] | None = None
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "noscript", "svg", "template"):
            self._skip += 1
            return
        if self._skip:
            return
        if tag == "tr":
            self._row = []
        elif tag == "td" and self._row is not None:
            self._cell = {"text": [], "anchors": []}
        elif tag == "a" and self._cell is not None:
            self._anchor = {"href": dict(attrs).get("href") or "", "text": []}

    def handle_data(self, data: str) -> None:
        if self._skip or self._cell is None:
            return
        value = " ".join(data.split())
        if not value:
            return
        self._cell["text"].append(value)
        if self._anchor is not None:
            self._anchor["text"].append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript", "svg", "template"):
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag == "a" and self._anchor is not None and self._cell is not None:
            self._cell["anchors"].append({
                "href": self._anchor["href"],
                "text": " ".join(self._anchor["text"]).strip(),
            })
            self._anchor = None
        elif tag == "td" and self._cell is not None and self._row is not None:
            self._row.append(self._cell)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def _visible_nba_table_rows(html: bytes, market: str) -> list[dict[str, Any]]:
    """Extract only table rows with a visible linked player and a real line.

    The source's October 2026 table separates matchup and line into td cells;
    the older legacy parser expected a combined 'Points 23.5 · TEAM vs TEAM'
    string that the current site no longer renders.
    """
    if market not in PUBLIC_MARKETS:
        raise PublicPageHold("PM_PUBLIC_MARKET_NOT_ALLOWLISTED")
    parser = _VisibleTableReader()
    try:
        parser.feed(html.decode("utf-8", errors="strict"))
        parser.close()
    except (UnicodeError, ValueError) as exc:
        raise PublicPageHold("PM_PUBLIC_HTML_DECODE_INVALID") from exc
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str, float]] = set()
    for cells in parser.rows:
        # #, Player, Line, L10 graph, L10 HIT, L10 AVG, ...
        if len(cells) < 6:
            continue
        players = [
            (ids, a["text"])
            for a in cells[1]["anchors"]
            if a["text"] and (ids := _player_link(a["href"], market))
        ]
        if len(players) != 1:
            continue
        (player, match), player_name = players[0]
        matchup = re.search(
            r"\b([A-Z]{2,3})\s*(@|vs\.?)\s*([A-Z]{2,3})\b",
            " ".join(cells[1]["text"]),
        )
        if not matchup:
            continue
        line_cell = " ".join(cells[2]["text"])
        if "UNLOCK" in line_cell.upper() or "LOCKED" in line_cell.upper():
            continue
        # A valid NBA board line is decimal; odds and ranking figures are
        # different fields. Never infer the line from L10/other cells.
        line_match = re.search(r"(?<![A-Za-z0-9+-])(\d{1,3}\.5)\b", line_cell)
        if not line_match:
            continue
        line = float(line_match[1])
        if not 0 < line <= 1000:
            raise PublicPageHold("PM_PUBLIC_EXACT_LINE_INVALID")
        key = (player, match, line)
        if key in seen:
            raise PublicPageHold("PM_PUBLIC_DUPLICATE_PLAYER_EVENT_LINE")
        seen.add(key)
        odds_match = re.search(r"\bO\s*([+-]\d{3,5})\s*U\s*([+-]\d{3,5})\b",
                               line_cell, re.I)
        avg_cell = " ".join(cells[5]["text"])
        average = re.fullmatch(r"\d+(?:\.\d+)?", avg_cell)
        output.append({
            "provider_player_id": player,
            "provider_match_id": match,
            "display_name_unverified": player_name,
            "line": line,
            "fixture_text": f"{matchup[1]} {matchup[2]} {matchup[3]}",
            "over_odds_display": int(odds_match[1]) if odds_match else None,
            "under_odds_display": int(odds_match[2]) if odds_match else None,
            "l10_avg_display": float(avg_cell) if average else None,
        })
    return output


def parse_public_explore_html(html: bytes, *, market: str, fetched_at: str) -> dict[str, Any]:
    """Only parse actual visible page rows; fail typed when HTML schema changes."""
    if market not in PUBLIC_MARKETS:
        raise PublicPageHold("PM_PUBLIC_MARKET_NOT_ALLOWLISTED")
    if not isinstance(html, bytes) or not html or len(html) > MAX_HTML_BYTES:
        raise PublicPageHold("PM_PUBLIC_HTML_SIZE_INVALID")
    try:
        fetched = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        raise PublicPageHold("PM_PUBLIC_CAPTURE_TIME_INVALID") from exc
    if fetched.utcoffset() is None:
        raise PublicPageHold("PM_PUBLIC_CAPTURE_TIME_INVALID")
    parser = _AnchorsAndTokens()
    try:
        parser.feed(html.decode("utf-8", errors="strict"))
        parser.close()
    except (UnicodeError, ValueError) as exc:
        raise PublicPageHold("PM_PUBLIC_HTML_DECODE_INVALID") from exc
    anchors: list[tuple[int, str, str, str]] = []
    for idx, (kind, text, href) in enumerate(parser.tokens):
        if kind == "link":
            link = _player_link(href, market)
            if link:
                anchors.append((idx, text, link[0], link[1]))
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    expected_name = PUBLIC_MARKETS[market][0]
    for j, (idx, display_name, player, match) in enumerate(anchors):
        end = anchors[j + 1][0] if j + 1 < len(anchors) else min(idx + 60, len(parser.tokens))
        text = " ".join(item[1] for item in parser.tokens[idx + 1:end])
        line = GAME_RE.search(text)
        if not line:
            continue
        stat_name = "THREE_POINTERS_MADE" if line[1].lower() == "threes" else line[1].upper()
        if stat_name != expected_name:
            continue
        odds = ODDS_RE.search(text)
        avg = AVG_RE.search(text)
        key = (player, match, str(line[2]))
        if key in seen:
            raise PublicPageHold("PM_PUBLIC_DUPLICATE_PLAYER_EVENT_LINE")
        seen.add(key)
        # No canonical event identity, known book or verified historical pretip time.
        result.append({
            "source": "PROPSMADNESS_PUBLIC_EXPLORE",
            "source_market": market,
            "stat_type": expected_name,
            "provider_player_id": player,
            "provider_match_id": match,
            "display_name_unverified": display_name,
            "line": float(line[2]),
            "fixture_text": f"{line[3]} {line[4]} {line[5]}",
            "over_odds_display": int(odds[1]) if odds else None,
            "under_odds_display": int(odds[2]) if odds else None,
            "l10_avg_display": float(avg[1]) if avg else None,
            "capture_utc": fetched.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "source_page": _source_url(market),
            "canonical_player_id": None,
            "canonical_event_id": None,
            "sportsbook": None,
            "event_start_utc": None,
            "pregame_verified": False,
            "exact_line_market_authority": False,
            "historical_line_evidence": False,
            "model_probability": None,
            "calibrated_probability": None,
            "can_execute": False,
        })
    # Current public pages expose players, matchup, line and L10 averages in
    # separate table cells. Prefer these strict verified cells over legacy
    # adjacent-token extraction when real table records are available.
    table_rows = _visible_nba_table_rows(html, market)
    if table_rows:
        result = [
            {
                "source": "PROPSMADNESS_PUBLIC_EXPLORE",
                "source_market": market,
                "stat_type": expected_name,
                **row,
                "capture_utc": fetched.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "source_page": _source_url(market),
                "canonical_player_id": None, "canonical_event_id": None,
                "sportsbook": None, "event_start_utc": None,
                "pregame_verified": False, "exact_line_market_authority": False,
                "historical_line_evidence": False, "model_probability": None,
                "calibrated_probability": None, "can_execute": False,
            }
            for row in table_rows
        ]
    return {
        "schema": "WOW_PM_NBA_PUBLIC_EXPLORE_PROBE_V1",
        "status": "PUBLIC_ROWS_PARSED_DISCOVERY_ONLY" if result else "PUBLIC_HTML_NO_VERIFIED_ROWS",
        "source_url": _source_url(market),
        "html_sha256": hashlib.sha256(html).hexdigest(),
        "rows": result,
        "row_count": len(result),
        "source_access_rights": "PERSONAL_NONCOMMERCIAL_TERMS_REVIEW_REQUIRED",
        "history_materialized": False,
        "historical_training_authorized": False,
        "market_evidence_certified": False,
        "model_certified": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def fetch_public_explore_once(
    market: str, *, personal_noncommercial_use_confirmed: bool,
    urlopen: Callable[..., Any] | None = None,
    robots_text: str | None = None,
) -> dict[str, Any]:
    """One explicit market, no sessions and no retries. Robots unknown => stop.

    For testing, robots_text may be supplied with an injected urlopen, but
    not in the normal live path: no caller-supplied bypass of robots policy.
    """
    if market not in PUBLIC_MARKETS:
        raise PublicPageHold("PM_PUBLIC_MARKET_NOT_ALLOWLISTED")
    if personal_noncommercial_use_confirmed is not True:
        raise PublicPageHold("PM_PUBLIC_USE_NOT_CONFIRMED")
    if robots_text is not None and urlopen is None:
        raise PublicPageHold("PM_PUBLIC_ROBOTS_OVERRIDE_FORBIDDEN")
    opener = urlopen or build_opener().open
    def read(url: str, limit: int) -> bytes:
        try:
            with opener(Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html, text/plain;q=0.9"}), timeout=12) as resp:
                if resp.geturl() != url:
                    raise PublicPageHold("PM_PUBLIC_REDIRECT_NOT_ALLOWED")
                if resp.status in (403, 429):
                    raise PublicPageHold("PM_PUBLIC_SOURCE_ACCESS_DENIED")
                if resp.status != 200:
                    raise PublicPageHold("PM_PUBLIC_SOURCE_HTTP_FAILURE")
                data = resp.read(limit + 1)
                if not data or len(data) > limit:
                    raise PublicPageHold("PM_PUBLIC_SOURCE_PAYLOAD_INVALID")
                return data
        except PublicPageHold:
            raise
        except HTTPError as exc:
            if exc.code in (403, 429):
                raise PublicPageHold("PM_PUBLIC_SOURCE_ACCESS_DENIED") from exc
            raise PublicPageHold("PM_PUBLIC_SOURCE_HTTP_FAILURE") from exc
        except URLError as exc:
            raise PublicPageHold("PM_PUBLIC_SOURCE_NETWORK_UNAVAILABLE") from exc

    text = robots_text
    if text is None:
        text = read(ROOT + "/robots.txt", 50000).decode("utf-8", errors="strict")
    if not text.strip() or not re.search(r"(?im)^\s*User-agent\s*:", text):
        raise PublicPageHold("PM_PUBLIC_ROBOTS_UNVERIFIED")
    robots = RobotFileParser()
    robots.parse(text.splitlines())
    url = _source_url(market)
    if not robots.can_fetch(USER_AGENT, url):
        raise PublicPageHold("PM_PUBLIC_ROBOTS_DISALLOW")
    html = read(url, MAX_HTML_BYTES)
    return parse_public_explore_html(
        html, market=market,
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )


def main() -> int:
    """A read-only operator command, off by default, never writes raw HTML."""
    import argparse
    parser = argparse.ArgumentParser(description="Public-page one-shot NBA discovery only")
    parser.add_argument("--market", choices=tuple(PUBLIC_MARKETS), required=True)
    parser.add_argument("--confirm-personal-noncommercial-use", action="store_true")
    args = parser.parse_args()
    try:
        result = fetch_public_explore_once(
            args.market, personal_noncommercial_use_confirmed=args.confirm_personal_noncommercial_use,
        )
        print(json.dumps(result, sort_keys=True))
        return 0 if result["row_count"] else 2
    except PublicPageHold as exc:
        print(json.dumps({
            "status": "PUBLIC_SCRAPE_HOLD", "reason": exc.code,
            "historical_training_authorized": False,
            "can_execute": False,
        }, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
