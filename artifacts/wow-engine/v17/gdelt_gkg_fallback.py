"""GDELT 2.0 GKG file-dump fallback when DOC ArtList is rate-limited/timed out.

Same GDELT Project ToU and attribution as the DOC adapter. Downloads only
allowlisted ``data.gdeltproject.org`` lastupdate + recent ``*.gkg.csv.zip``
slices, extracts HTTPS article URLs and ``<PAGE_TITLE>`` metadata, and never
fetches publisher article bodies. Research discovery only; not official facts.
"""
from __future__ import annotations

import io
import re
import zipfile
from datetime import datetime, timedelta, timezone

import httpx

try:
    from v17.firecrawl_research_adapter import _safe_url
    from v17.news_feed_research_adapter import (
        MAX_NEWS_ITEMS,
        PROVIDER,
        SOURCE_CLASS,
        normalize_articles,
    )
    from v17.research_source_adapters import ResearchFetchResult
    from v17.web_intelligence_sport_profiles import WEIGHT_PROFILES, normalize_sport
except ModuleNotFoundError:
    from firecrawl_research_adapter import _safe_url
    from news_feed_research_adapter import (
        MAX_NEWS_ITEMS,
        PROVIDER,
        SOURCE_CLASS,
        normalize_articles,
    )
    from research_source_adapters import ResearchFetchResult
    from web_intelligence_sport_profiles import WEIGHT_PROFILES, normalize_sport

GDELT_DATA_HOST = "data.gdeltproject.org"
LASTUPDATE_URL = f"https://{GDELT_DATA_HOST}/gdeltv2/lastupdate.txt"
MAX_LASTUPDATE_BYTES = 8_192
MAX_GKG_ZIP_BYTES = 12_000_000
MAX_GKG_FILES = 2
MAX_LOOKBACK_STEPS = 8
GKG_INTERVAL = timedelta(minutes=15)
PAGE_TITLE_RE = re.compile(r"<PAGE_TITLE>(.*?)</PAGE_TITLE>", re.IGNORECASE | re.DOTALL)
PRECISE_TS_RE = re.compile(r"<PAGE_PRECISEPUBTIMESTAMP>(\d{14})</PAGE_PRECISEPUBTIMESTAMP>",
                           re.IGNORECASE)

# Whole-token title markers aligned to the existing DOC sport queries.
SPORT_TITLE_RE: dict[str, re.Pattern[str]] = {
    "americanfootball_nfl": re.compile(r"\bNFL\b"),
    "basketball_nba": re.compile(r"\bNBA\b"),
    "icehockey_nhl": re.compile(r"\bNHL\b"),
    "americanfootball_ncaaf": re.compile(
        r"\bNCAAF\b|\bFBS\b|college football|College Football", re.IGNORECASE),
    "basketball_ncaab": re.compile(
        r"\bNCAAB\b|college basketball|College Basketball|March Madness", re.IGNORECASE),
    "baseball_mlb": re.compile(r"\bMLB\b"),
    "basketball_wnba": re.compile(r"\bWNBA\b"),
    "soccer": re.compile(
        r"\bMLS\b|\bEPL\b|\bPremier League\b|\bUEFA\b|\bLa Liga\b|\bsoccer\b|\bfootball club\b",
        re.IGNORECASE),
    "tennis": re.compile(r"\bATP\b|\bWTA\b|\btennis\b", re.IGNORECASE),
    "golf": re.compile(r"\bPGA\b|\bLPGA\b|\bgolf\b", re.IGNORECASE),
    "mma": re.compile(r"\bUFC\b|\bMMA\b"),
    "boxing": re.compile(r"\bboxing\b|\bheavyweight\b", re.IGNORECASE),
    "cricket": re.compile(r"\bcricket\b|\bIPL\b|\bT20\b", re.IGNORECASE),
}

SPORT_URL_RE: dict[str, re.Pattern[str]] = {
    "americanfootball_nfl": re.compile(r"(?:/nfl(?:/|$)|nfl\.com)", re.IGNORECASE),
    "basketball_nba": re.compile(r"(?:/nba(?:/|$)|nba\.com)", re.IGNORECASE),
    "icehockey_nhl": re.compile(r"(?:/nhl(?:/|$)|nhl\.com|tsn\.ca/nhl)", re.IGNORECASE),
    "americanfootball_ncaaf": re.compile(
        r"(?:/college-football|/ncaaf|espn\.com/college-football)", re.IGNORECASE),
    "basketball_ncaab": re.compile(
        r"(?:/mens-college-basketball|/ncaab|espn\.com/mens-college-basketball)",
        re.IGNORECASE),
    "baseball_mlb": re.compile(r"(?:/mlb(?:/|$)|mlb\.com)", re.IGNORECASE),
    "basketball_wnba": re.compile(r"(?:/wnba(?:/|$)|wnba\.com)", re.IGNORECASE),
    "soccer": re.compile(
        r"(?:/soccer|/football|/mls|/premier-league|espn\.com/soccer)", re.IGNORECASE),
    "tennis": re.compile(r"(?:/tennis|atptour\.com|wtatennis\.com)", re.IGNORECASE),
    "golf": re.compile(r"(?:/golf|pgatour\.com)", re.IGNORECASE),
    "mma": re.compile(r"(?:/mma|/ufc|ufc\.com)", re.IGNORECASE),
    "boxing": re.compile(r"(?:/boxing)", re.IGNORECASE),
    "cricket": re.compile(r"(?:/cricket|espncricinfo\.com)", re.IGNORECASE),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _blocked(sport_key: str, code: str, status: int | None = None,
             **extra: object) -> ResearchFetchResult:
    seen = _now().isoformat().replace("+00:00", "Z")
    data = {
        "source_observed_at": seen,
        "source_code": code,
        "evidence_only": True,
        "acquisition_path": "GDELT_GKG_DUMP_FALLBACK",
        "attribution": "The GDELT Project (https://www.gdeltproject.org/)",
        **extra,
    }
    return ResearchFetchResult(
        False, PROVIDER, sport_key, "news", SOURCE_CLASS,
        data=data, observed_at=seen, code=code, status=status,
        prediction_authority=False, can_execute=False,
    )


def _gkg_stamp(value: str) -> str | None:
    if not isinstance(value, str) or len(value) != 14 or not value.isdigit():
        return None
    return f"{value[:8]}T{value[8:]}Z"


def _row_matches_sport(sport_key: str, url: str, title: str) -> bool:
    title_re = SPORT_TITLE_RE.get(sport_key)
    url_re = SPORT_URL_RE.get(sport_key)
    if title_re is None or url_re is None:
        return False
    return bool(title_re.search(title) or url_re.search(url))


def parse_gkg_articles(raw_csv: bytes, *, sport_key: str,
                       observed_at: datetime) -> list[dict]:
    """Parse GKG TSV bytes into DOC-compatible article dicts for one sport."""
    if not isinstance(raw_csv, (bytes, bytearray)) or not raw_csv:
        return []
    title_re = SPORT_TITLE_RE.get(sport_key)
    if title_re is None:
        return []
    records: list[dict] = []
    seen_urls: set[str] = set()
    text = raw_csv.decode("utf-8", "replace")
    for line in text.splitlines():
        if not line or "\t" not in line:
            continue
        cols = line.split("\t")
        if len(cols) < 5:
            continue
        url = cols[4].strip()
        extras = cols[-1] if cols else ""
        title_match = PAGE_TITLE_RE.search(extras)
        if not title_match:
            continue
        title = " ".join(title_match.group(1).split())
        if not title or not _row_matches_sport(sport_key, url, title):
            continue
        if url in seen_urls:
            continue
        precise = PRECISE_TS_RE.search(extras)
        stamp = _gkg_stamp(precise.group(1) if precise else cols[1])
        if not stamp:
            continue
        seen_urls.add(url)
        records.append({"url": url, "title": title, "seendate": stamp})
        if len(records) >= MAX_NEWS_ITEMS * 3:
            break
    return normalize_articles({"articles": records}, observed_at=observed_at)


def _read_bounded(response: httpx.Response, *, limit: int) -> bytes | None:
    declared = 0
    try:
        declared = int(response.headers.get("Content-Length", "0"))
    except ValueError:
        declared = 0
    if declared > limit:
        return None
    data = bytearray()
    for chunk in response.iter_raw(chunk_size=65536):
        data.extend(chunk)
        if len(data) > limit:
            return None
    return bytes(data)


def _candidate_gkg_urls(lastupdate_text: str, *, at: datetime) -> list[str]:
    """Prefer lastupdate listing, then walk back 15-minute stamps if stale/404."""
    urls: list[str] = []
    for line in lastupdate_text.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        raw = parts[2].strip()
        if not raw.endswith(".gkg.csv.zip"):
            continue
        https = raw.replace("http://", "https://", 1)
        safe = _safe_url(https, (GDELT_DATA_HOST,))
        if safe:
            urls.append(safe)
    # Always append lookback candidates so a stale lastupdate pointer can recover.
    base = at.replace(second=0, microsecond=0)
    minute = (base.minute // 15) * 15
    base = base.replace(minute=minute)
    for step in range(MAX_LOOKBACK_STEPS):
        stamp = (base - GKG_INTERVAL * step).strftime("%Y%m%d%H%M%S")
        candidate = _safe_url(
            f"https://{GDELT_DATA_HOST}/gdeltv2/{stamp}.gkg.csv.zip",
            (GDELT_DATA_HOST,),
        )
        if candidate and candidate not in urls:
            urls.append(candidate)
    return urls


def fetch_gdelt_gkg_news(sport_key: str, *, client: httpx.Client | None = None,
                         at: datetime | None = None) -> ResearchFetchResult:
    """Fetch up to two recent GKG dumps and extract sport-matching headlines."""
    if normalize_sport(sport_key) not in WEIGHT_PROFILES:
        return _blocked(sport_key, "SOURCE_TARGET_INVALID")
    if sport_key not in SPORT_TITLE_RE:
        return _blocked(sport_key, "SOURCE_TARGET_INVALID")

    owned = client is None
    http = client or httpx.Client(timeout=30, follow_redirects=False)
    files_tried: list[dict] = []
    try:
        with http.stream(
            "GET", LASTUPDATE_URL,
            headers={"Accept": "text/plain", "Accept-Encoding": "identity"},
            follow_redirects=False, timeout=20,
        ) as response:
            status = response.status_code
            if 300 <= status < 400:
                return _blocked(sport_key, "SOURCE_REDIRECT_NOT_ALLOWED", status)
            if status >= 400:
                reason = ("SOURCE_RATE_LIMITED" if status == 429 else
                          "SOURCE_UPSTREAM_UNAVAILABLE" if status >= 500 else
                          "SOURCE_UPSTREAM_REJECTED")
                return _blocked(sport_key, reason, status)
            body = _read_bounded(response, limit=MAX_LASTUPDATE_BYTES)
            if body is None:
                return _blocked(sport_key, "SOURCE_BODY_TOO_LARGE", status)
        lastupdate_text = body.decode("utf-8", "replace")
        observed = at or _now()
        if observed.tzinfo is None:
            return _blocked(sport_key, "SOURCE_TARGET_INVALID")
        candidates = _candidate_gkg_urls(lastupdate_text, at=observed.astimezone(timezone.utc))
        if not candidates:
            return _blocked(sport_key, "SOURCE_GKG_INDEX_EMPTY", status)

        articles: list[dict] = []
        downloaded = 0
        for url in candidates:
            if downloaded >= MAX_GKG_FILES or len(articles) >= MAX_NEWS_ITEMS:
                break
            with http.stream(
                "GET", url,
                headers={"Accept": "application/zip", "Accept-Encoding": "identity"},
                follow_redirects=False, timeout=60,
            ) as zip_response:
                zstatus = zip_response.status_code
                attempt = {"url_host": GDELT_DATA_HOST, "http_status": zstatus,
                           "source_code": None}
                if zstatus == 404:
                    attempt["source_code"] = "SOURCE_GKG_SLICE_MISSING"
                    files_tried.append(attempt)
                    continue
                if 300 <= zstatus < 400:
                    return _blocked(sport_key, "SOURCE_REDIRECT_NOT_ALLOWED", zstatus,
                                    gkg_files_tried=files_tried)
                if zstatus >= 400:
                    reason = ("SOURCE_RATE_LIMITED" if zstatus == 429 else
                              "SOURCE_UPSTREAM_UNAVAILABLE" if zstatus >= 500 else
                              "SOURCE_UPSTREAM_REJECTED")
                    attempt["source_code"] = reason
                    files_tried.append(attempt)
                    if reason == "SOURCE_RATE_LIMITED":
                        return _blocked(sport_key, reason, zstatus,
                                        gkg_files_tried=files_tried)
                    continue
                raw_zip = _read_bounded(zip_response, limit=MAX_GKG_ZIP_BYTES)
                if raw_zip is None:
                    attempt["source_code"] = "SOURCE_BODY_TOO_LARGE"
                    files_tried.append(attempt)
                    continue
            try:
                with zipfile.ZipFile(io.BytesIO(raw_zip)) as zf:
                    names = [n for n in zf.namelist() if n.endswith(".gkg.csv")]
                    if not names:
                        attempt["source_code"] = "SOURCE_GKG_MEMBER_MISSING"
                        files_tried.append(attempt)
                        continue
                    with zf.open(names[0]) as member:
                        # Bound uncompressed scan; GKG slices are typically <25MB.
                        raw_csv = member.read(25_000_000)
            except zipfile.BadZipFile:
                attempt["source_code"] = "SOURCE_GKG_ZIP_INVALID"
                files_tried.append(attempt)
                continue
            downloaded += 1
            parsed = parse_gkg_articles(raw_csv, sport_key=sport_key, observed_at=observed)
            attempt["source_code"] = "SOURCE_GKG_SLICE_OK"
            attempt["article_count"] = len(parsed)
            files_tried.append(attempt)
            # Merge unique URLs in arrival order.
            seen = {a["source_url"] for a in articles}
            for item in parsed:
                if item["source_url"] not in seen:
                    articles.append(item)
                    seen.add(item["source_url"])
                if len(articles) >= MAX_NEWS_ITEMS:
                    break

        if not articles:
            return _blocked(sport_key, "SOURCE_NO_VALID_NEWS", status,
                            gkg_files_tried=files_tried, gkg_files_downloaded=downloaded)
        stamp = observed.isoformat().replace("+00:00", "Z")
        return ResearchFetchResult(
            True, PROVIDER, sport_key, "news", SOURCE_CLASS,
            data={
                "source_observed_at": stamp,
                "articles": articles[:MAX_NEWS_ITEMS],
                "attribution": "The GDELT Project (https://www.gdeltproject.org/)",
                "verified_event_count": 0,
                "evidence_only": True,
                "acquisition_path": "GDELT_GKG_DUMP_FALLBACK",
                "gkg_files_tried": files_tried,
                "gkg_files_downloaded": downloaded,
            },
            code="SOURCE_FETCH_OK", status=200, observed_at=stamp,
            endpoint=LASTUPDATE_URL, prediction_authority=False, can_execute=False,
        )
    except httpx.TimeoutException:
        return _blocked(sport_key, "SOURCE_TIMEOUT", gkg_files_tried=files_tried)
    except httpx.HTTPError:
        return _blocked(sport_key, "SOURCE_HTTP_ERROR", gkg_files_tried=files_tried)
    except httpx.StreamError:
        return _blocked(sport_key, "SOURCE_STREAM_ERROR", gkg_files_tried=files_tried)
    finally:
        if owned:
            http.close()


__all__ = [
    "GDELT_DATA_HOST",
    "LASTUPDATE_URL",
    "MAX_GKG_FILES",
    "MAX_GKG_ZIP_BYTES",
    "fetch_gdelt_gkg_news",
    "parse_gkg_articles",
]
