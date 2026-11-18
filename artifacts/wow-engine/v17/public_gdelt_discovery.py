"""Single-call, no-credential GDELT Project research receipt runner.

Runs without a Scout DB. Source errors are explicitly BLOCKED in the receipt
but never turn into betting/prediction authority or crash a structured refresh.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

try:
    from v17.news_feed_research_adapter import fetch_gdelt_news
except ModuleNotFoundError:
    from news_feed_research_adapter import fetch_gdelt_news

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
    return {
        "schema_version": "wow.v17.public_news_receipt.v1",
        "source_status": "OK" if result.ok else "BLOCKED",
        "source_code": result.code,
        "sport_key": sport_key,
        "source_observed_at": result.observed_at,
        "articles": result.data.get("articles", []) if isinstance(result.data, dict) else [],
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
    args = parser.parse_args()
    receipt = discover(sport=args.sport)
    dest = Path(args.output)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"source_status": receipt["source_status"],
                      "source_code": receipt["source_code"],
                      "sport_key": receipt["sport_key"],
                      "article_count": len(receipt["articles"]),
                      "prediction_authority": False, "can_execute": False}))
    # A single public news outage cannot block the structured Scout or models;
    # the receipt truthfully records its own blocked status.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
