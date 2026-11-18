"""Single-call, no-credential GDELT Project research receipt runner.

Runs without a Scout DB. Source errors are explicitly BLOCKED in the receipt
but never turn into betting/prediction authority or crash a structured refresh.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

try:
    from v17.news_feed_research_adapter import fetch_gdelt_news
    from v17.news_active_slate import select_active_slate
except ModuleNotFoundError:
    from news_feed_research_adapter import fetch_gdelt_news
    from news_active_slate import select_active_slate

# Exact, stable 13-sport schedule. One sport per 6-hour discovery slot avoids
# free API burst calls. This is a discovery sampling cadence, NOT full coverage.
SPORT_QUERIES = (
    ("americanfootball_nfl", "NFL injury"),
    ("basketball_nba", "NBA injury"),
    ("icehockey_nhl", "NHL injury"),
    ("americanfootball_ncaaf", "college football injury"),
    ("basketball_ncaab", "college basketball injury"),
    ("baseball_mlb", "MLB injury"),
    ("basketball_wnba", "WNBA injury"),
    ("soccer", "soccer injury"),
    ("tennis", "tennis injury"),
    ("golf", "golf tournament injury"),
    ("mma", "MMA UFC injury"),
    ("boxing", "boxing bout injury"),
    ("cricket", "cricket injury"),
)

# Measured 2026-11-18 from this runtime against the DOC 2.0 endpoint: HTTP 429
# was returned at 0s, 45s, 90s, 135s and 735s of complete silence, with no
# Retry-After header. GDELT therefore applies a sustained penalty, not the
# advertised five-second interval. A retry below that floor (the old 6s sleep,
# or any sub-minute step) is near-certain wasted load, so there is exactly one
# paced retry, just past the measured floor. It is bounded and still fails
# closed: a final 429 stays BLOCKED.
RETRY_DELAYS_SECONDS = (900,)
MAX_TOTAL_WAIT_SECONDS = 1200
RETRY_POLICY = {
    "ladder_seconds": list(RETRY_DELAYS_SECONDS),
    "honours_retry_after": True,
    "max_total_wait_seconds": MAX_TOTAL_WAIT_SECONDS,
    "measured_block_floor_seconds": 735,
    "measured_at_utc": "2026-11-18T21:12:54Z",
    "measured_status": "SOURCE_RATE_LIMITED",
}


def selection(at: datetime) -> tuple[str, str]:
    if at.tzinfo is None:
        raise ValueError("GDELT_SLOT_REQUIRES_UTC_AWARE_TIME")
    index = int(at.timestamp() // (6 * 3600)) % len(SPORT_QUERIES)
    return SPORT_QUERIES[index]


def discover(at: datetime | None = None, *, sport: str | None = None,
             client=None) -> dict:
    at = at or datetime.now(timezone.utc)
    selected = next(((key, query) for key, query in SPORT_QUERIES if key == sport), None) if sport else selection(at)
    if selected is None:
        return {
            "schema_version": "wow.v17.public_news_receipt.v1",
            "source_status": "BLOCKED",
            "source_code": "SOURCE_TARGET_INVALID",
            "sport_key": sport, "articles": [],
            "prediction_authority": False, "can_execute": False,
        }
    sport_key, query = selected
    url = ("https://api.gdeltproject.org/api/v2/doc/doc?" +
           urlencode({"query": query, "mode": "ArtList", "format": "json",
                      "maxrecords": 20, "timespan": "24h", "sort": "DateDesc"}))
    result = fetch_gdelt_news(sport_key, url, client=client)
    attempts = [{
        "attempt": 1,
        "delay_before_seconds": 0,
        "http_status": getattr(result, "status", None),
        "source_code": result.code,
        "observed_at": result.observed_at,
    }]
    waited = 0.0
    # Real HTTP sessions only: an injected client keeps offline tests and
    # callers deterministic and keeps zero-pacing behaviour. Each step paces
    # from the measured ladder, lengthened to any Retry-After the source sends.
    for scheduled_wait in RETRY_DELAYS_SECONDS:
        if client is not None or result.code != "SOURCE_RATE_LIMITED":
            break
        hinted = getattr(result, "retry_after_seconds", None)
        wait = max(float(scheduled_wait), float(hinted or 0))
        if waited + wait > MAX_TOTAL_WAIT_SECONDS:
            break
        time.sleep(wait)
        waited += wait
        result = fetch_gdelt_news(sport_key, url, client=client)
        attempts.append({
            "attempt": len(attempts) + 1,
            "delay_before_seconds": wait,
            "http_status": getattr(result, "status", None),
            "source_code": result.code,
            "observed_at": result.observed_at,
        })
    articles = result.data.get("articles", []) if isinstance(result.data, dict) else []
    return {
        "schema_version": "wow.v17.public_news_receipt.v1",
        "source_status": "OK" if result.ok else "BLOCKED",
        "source_code": result.code,
        "sport_key": sport_key,
        "source_observed_at": result.observed_at,
        "articles": articles,
        "article_count": len(articles),
        "attempt_count": len(attempts),
        "attempts": attempts,
        "total_wait_seconds": round(waited, 1),
        "retry_policy": RETRY_POLICY,
        "model_rescore_verified": False,
        "attribution": "The GDELT Project (https://www.gdeltproject.org/)",
        "query": query,
        "cadence": "one sport per 6-hour slot; not a full 13-sport feed",
        "model_feature_eligible": False,
        "historical_pregame_eligible": False,
        "prediction_authority": False,
        "can_execute": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sport", choices=[s for s, _ in SPORT_QUERIES])
    parser.add_argument("--output", required=True)
    parser.add_argument("--candidate-manifest",
                        help="Same-run Scout handoff for active-sport research selection")
    args = parser.parse_args()
    at = datetime.now(timezone.utc)
    sport = args.sport
    policy = "EXPLICIT_SPORT" if sport else "FIXED_ROTATION"
    counts = {}
    if args.candidate_manifest and not sport:
        policy = "SLATE_UNAVAILABLE_FIXED_ROTATION"
        try:
            manifest = Path(args.candidate_manifest)
            if manifest.stat().st_size > 64_000_000:
                raise ValueError("NEWS_MANIFEST_OVERSIZED")
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            selected, counts = select_active_slate(
                payload, at, tuple(key for key, _ in SPORT_QUERIES))
            if selected:
                sport = selected
                policy = "ACTIVE_SLATE_ROTATION"
        except (OSError, ValueError, UnicodeDecodeError):
            pass
    receipt = discover(at, sport=sport)
    receipt["selection_policy"] = policy
    receipt["slate_active_event_counts"] = counts
    dest = Path(args.output)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"source_status": receipt["source_status"],
                      "source_code": receipt["source_code"],
                      "sport_key": receipt["sport_key"],
                      "article_count": len(receipt["articles"]),
                      "attempt_count": receipt["attempt_count"],
                      "selection_policy": policy,
                      "prediction_authority": False, "can_execute": False}))
    # A single public news outage cannot block the structured Scout or models;
    # the receipt truthfully records its own blocked status.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
