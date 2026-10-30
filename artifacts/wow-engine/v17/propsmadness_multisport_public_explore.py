"""WOW #2018 multi-sport PropsMadness public Explore discovery (read-only).

Supports NBA, NFL, MLB, WNBA fixture parsing. A live operator invocation is
limited to one URL plus robots.txt, requires a truthful personal/noncommercial
use confirmation, and is never run in CI. No login, private endpoints, rotating
identities, raw-data persistence, model training, or betting execution.

The display data are NONAUTHORITATIVE: no validated official player/event ID,
sportsbook, market snapshot time, original game start, or historical archive.
"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, parse_qs
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError
from urllib.robotparser import RobotFileParser
import hashlib
import json
import re
from typing import Any, Callable

from v17.propsmadness_nba_public_explore import (
    _AnchorsAndTokens, PublicPageHold, ODDS_RE, AVG_RE,
)

ROOT = "https://propsmadness.com"
USER_AGENT = "WOW-V17-Public-Research-Probe/1.0 (no-login; one-shot)"
MAX_HTML = 2_000_000
MAX_TOKENS = 35000
CAN_EXECUTE = False

# Public URL paths verified against PropsMadness' own market navigation/sitemap.
# market label is exactly the one visible on a public Explore page.
MARKETS: dict[str, dict[str, tuple[str, str]]] = {
    "NBA": {
        "points": ("POINTS", "Points"),
        "rebounds": ("REBOUNDS", "Rebounds"),
        "assists": ("ASSISTS", "Assists"),
        "threes": ("THREE_POINTERS_MADE", "Threes"),
    },
    "NFL": {
        "passing-yards": ("PASSING_YARDS", "Passing Yards"),
        "receiving-yards": ("RECEIVING_YARDS", "Receiving Yards"),
        "rushing-yards": ("RUSHING_YARDS", "Rushing Yards"),
        "receptions": ("RECEPTIONS", "Receptions"),
    },
    "MLB": {
        "strikeouts": ("PITCHER_STRIKEOUTS", "Strikeouts"),
        "pitcher-outs": ("PITCHING_OUTS", "Pitcher Outs"),
        "hits": ("HITS", "Hits"),
        "total-bases": ("TOTAL_BASES", "Total Bases"),
    },
    "WNBA": {
        "points": ("POINTS", "Points"),
        "rebounds": ("REBOUNDS", "Rebounds"),
        "assists": ("ASSISTS", "Assists"),
        "threes": ("THREE_POINTERS_MADE", "Threes"),
    },
}


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, request: Any, fp: Any, code: int,
                         msg: str, headers: Any, newurl: str) -> None:
        raise PublicPageHold("PM_PUBLIC_REDIRECT_NOT_ALLOWED")


def _config(sport: str, market: str) -> tuple[str, str, str]:
    if sport not in MARKETS:
        raise PublicPageHold("PM_PUBLIC_SPORT_NOT_ALLOWLISTED")
    if market not in MARKETS[sport]:
        raise PublicPageHold("PM_PUBLIC_MARKET_NOT_ALLOWLISTED")
    stat, label = MARKETS[sport][market]
    # This is a public-page URL, NOT a new market/stat identity or a license.
    # NBA's currently published threes slug differs from the UI market key.
    page_slug = "made-threes" if (sport, market) == ("NBA", "threes") else market
    return stat, label, f"{ROOT}/{sport.lower()}/props/{page_slug}"


def _candidate(href: str, sport: str, market: str) -> tuple[str, str] | None:
    target = urlparse(urljoin(ROOT, href))
    if target.scheme != "https" or target.hostname not in (
        "propsmadness.com", "www.propsmadness.com",
    ):
        return None
    if target.path.rstrip("/") != f"/{sport.lower()}":
        return None
    query = parse_qs(target.query)
    accepted = {f"player-{market}"}
    if (sport, market) == ("NBA", "threes"):
        accepted.add("player-made-threes")
    if tuple(query.get("market", [])) not in {(name,) for name in accepted}:
        return None
    ids = (query.get("player"), query.get("match"))
    if any(not isinstance(v, list) or len(v) != 1 or
           not re.fullmatch(r"[0-9]{1,12}", v[0]) for v in ids):
        return None
    return ids[0][0], ids[1][0]


def _timestamp(value: str) -> str:
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise PublicPageHold("PM_PUBLIC_CAPTURE_TIME_INVALID") from exc
    if t.utcoffset() is None:
        raise PublicPageHold("PM_PUBLIC_CAPTURE_TIME_INVALID")
    return t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_multisport_public_html(
    html: bytes, *, sport: str, market: str, fetched_at: str,
) -> dict[str, Any]:
    """Pure HTML fixture reader; never claims entitlement or certified evidence."""
    stat, label, source_url = _config(sport, market)
    if not isinstance(html, bytes) or not html or len(html) > MAX_HTML:
        raise PublicPageHold("PM_PUBLIC_HTML_SIZE_INVALID")
    timestamp = _timestamp(fetched_at)
    parser = _AnchorsAndTokens()
    try:
        parser.feed(html.decode("utf-8", errors="strict"))
        parser.close()
    except (UnicodeError, ValueError) as exc:
        raise PublicPageHold("PM_PUBLIC_HTML_DECODE_INVALID") from exc
    if len(parser.tokens) > MAX_TOKENS:
        raise PublicPageHold("PM_PUBLIC_HTML_TOKENS_EXCEEDED")
    anchors: list[tuple[int, str, str, str]] = []
    for i, (kind, name, href) in enumerate(parser.tokens):
        if kind != "link":
            continue
        ids = _candidate(href, sport, market)
        if ids:
            anchors.append((i, name, ids[0], ids[1]))
    regex = re.compile(
        rf"\b{re.escape(label)}\s+(\d+(?:\.\d+)?)\s*[·•]\s*"
        rf"([A-Z]{{2,3}})\s*(@|vs\.?)\s*([A-Z]{{2,3}})\b", re.I,
    )
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, float]] = set()
    for idx, (position, name, player, match) in enumerate(anchors):
        end = anchors[idx + 1][0] if idx + 1 < len(anchors) else min(position + 65, len(parser.tokens))
        window = " ".join(token[1] for token in parser.tokens[position + 1:end])
        hit = regex.search(window)
        if not hit:
            continue  # Locked, unavailable, stale or unsupported line: no invented value
        try:
            line = float(hit[1])
        except (TypeError, ValueError) as exc:
            raise PublicPageHold("PM_PUBLIC_EXACT_LINE_INVALID") from exc
        if not 0 < line <= 1000:
            raise PublicPageHold("PM_PUBLIC_EXACT_LINE_INVALID")
        key = (player, match, line)
        if key in seen:
            raise PublicPageHold("PM_PUBLIC_DUPLICATE_PLAYER_EVENT_LINE")
        seen.add(key)
        odds = ODDS_RE.search(window)
        avg = AVG_RE.search(window)
        rows.append({
            "sport": sport, "stat_type": stat, "period": "FULL_GAME",
            "source_provider": "PROPSMADNESS_PUBLIC_EXPLORE",
            "source_page": source_url, "provider_player_id": player,
            "provider_match_id": match, "display_name_unverified": name,
            "display_line": line,
            "fixture_text": f"{hit[2]} {hit[3]} {hit[4]}",
            "over_odds_display": int(odds[1]) if odds else None,
            "under_odds_display": int(odds[2]) if odds else None,
            "l10_avg_display": float(avg[1]) if avg else None,
            "capture_utc": timestamp,
            "canonical_player_id": None, "canonical_event_id": None,
            "sportsbook": None, "event_start_utc": None,
            "pregame_verified": False, "historical_line_evidence": False,
            "exact_line_market_authority": False,
            "model_probability": None, "calibrated_lower_bound": None,
            "can_execute": False,
        })
    return {
        "schema": "WOW_PM_MULTISPORT_PUBLIC_DISCOVERY_V1",
        "status": "PUBLIC_ROWS_PARSED_DISCOVERY_ONLY" if rows else "PUBLIC_HTML_NO_VERIFIED_ROWS",
        "sport": sport, "market": market, "source_url": source_url,
        "html_sha256": hashlib.sha256(html).hexdigest(),
        "row_count": len(rows), "rows": rows,
        "source_rights_state": "PERSONAL_NONCOMMERCIAL_TERMS_REVIEW_REQUIRED",
        "historical_dataset_materialized": False,
        "historical_training_authorized": False,
        "market_evidence_certified": False,
        "prediction_authority": False,
        "probability_publishable": False, "can_execute": False,
    }


def fetch_public_one_shot(
    sport: str, market: str, *,
    personal_noncommercial_use_confirmed: bool,
    urlopen: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """One human-initiated page; unknown robots or denied access stop.
    
    An injected opener is for local fixture tests, not an alternate live route.
    """
    _stat, _label, url = _config(sport, market)
    if personal_noncommercial_use_confirmed is not True:
        raise PublicPageHold("PM_PUBLIC_USE_NOT_CONFIRMED")
    opener = urlopen or build_opener(_RejectRedirects()).open

    def read(url_to_fetch: str, limit: int) -> bytes:
        try:
            with opener(Request(url_to_fetch, headers={
                "User-Agent": USER_AGENT, "Accept": "text/html,text/plain;q=0.9",
            }), timeout=12) as response:
                if response.geturl() != url_to_fetch:
                    raise PublicPageHold("PM_PUBLIC_REDIRECT_NOT_ALLOWED")
                if response.status in (403, 429):
                    raise PublicPageHold("PM_PUBLIC_SOURCE_ACCESS_DENIED")
                if response.status != 200:
                    raise PublicPageHold("PM_PUBLIC_SOURCE_HTTP_FAILURE")
                raw = response.read(limit + 1)
        except PublicPageHold:
            raise
        except HTTPError as exc:
            if exc.code in (403, 429):
                raise PublicPageHold("PM_PUBLIC_SOURCE_ACCESS_DENIED") from exc
            raise PublicPageHold("PM_PUBLIC_SOURCE_HTTP_FAILURE") from exc
        except URLError as exc:
            raise PublicPageHold("PM_PUBLIC_SOURCE_NETWORK_UNAVAILABLE") from exc
        if not raw or len(raw) > limit:
            raise PublicPageHold("PM_PUBLIC_SOURCE_PAYLOAD_INVALID")
        return raw

    robots_bytes = read(ROOT + "/robots.txt", 50_000)
    try:
        robots_text = robots_bytes.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise PublicPageHold("PM_PUBLIC_ROBOTS_UNVERIFIED") from exc
    if not re.search(r"(?im)^\s*User-agent\s*:", robots_text):
        raise PublicPageHold("PM_PUBLIC_ROBOTS_UNVERIFIED")
    policy = RobotFileParser()
    policy.parse(robots_text.splitlines())
    if not policy.can_fetch(USER_AGENT, url):
        raise PublicPageHold("PM_PUBLIC_ROBOTS_DISALLOW")
    return parse_multisport_public_html(
        read(url, MAX_HTML), sport=sport, market=market,
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="One public PropsMadness Explore page, no persistent dataset")
    parser.add_argument("--sport", required=True, choices=list(MARKETS))
    parser.add_argument("--market", required=True)
    parser.add_argument("--confirm-personal-noncommercial-use", action="store_true")
    args = parser.parse_args()
    try:
        receipt = fetch_public_one_shot(
            args.sport, args.market,
            personal_noncommercial_use_confirmed=args.confirm_personal_noncommercial_use,
        )
        print(json.dumps(receipt, sort_keys=True))
        return 0 if receipt["row_count"] else 2
    except PublicPageHold as exc:
        print(json.dumps({
            "status": "PUBLIC_SCRAPE_HOLD", "typed_blocker": exc.code,
            "historical_training_authorized": False, "can_execute": False,
        }, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
