"""Single-call, no-credential GDELT Project research receipt runner.

Runs without a Scout DB. Source errors are explicitly BLOCKED in the receipt
but never turn into betting/prediction authority or crash a structured refresh.

When DOC ArtList returns rate-limit or timeout, one bounded GKG dump fallback
(same GDELT Project ToU) may recover headlines without a second DOC request.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

try:
    from v17.gdelt_gkg_fallback import fetch_gdelt_gkg_news
    from v17.news_feed_research_adapter import fetch_gdelt_news
    from v17.news_active_slate import select_active_slate
    from v17.news_material_lead import material_lead_count, material_query_for, prefer_material_leads
except ModuleNotFoundError:
    from gdelt_gkg_fallback import fetch_gdelt_gkg_news
    from news_feed_research_adapter import fetch_gdelt_news
    from news_active_slate import select_active_slate
    from news_material_lead import material_lead_count, material_query_for, prefer_material_leads

# Exact, stable 13-sport inventory. Values are league labels; material topic
# clauses rotate per 6-hour slot so DOC queries cover injury/lineup/availability.
SPORT_QUERIES = (
    ("americanfootball_nfl", "NFL"),
    ("basketball_nba", "NBA"),
    ("icehockey_nhl", "NHL"),
    ("americanfootball_ncaaf", "college football"),
    ("basketball_ncaab", "college basketball"),
    ("baseball_mlb", "MLB"),
    ("basketball_wnba", "WNBA"),
    ("soccer", "soccer"),
    ("tennis", "tennis"),
    ("golf", "golf tournament"),
    ("mma", "MMA UFC"),
    ("boxing", "boxing bout"),
    ("cricket", "cricket"),
)
SPORT_QUERY_MAP = dict(SPORT_QUERIES)

# Calendar approximation for research sampling priority (not a league calendar
# authority). Off-season sports remain inventoriable but are not rotated daily.
ACTIVE_SPORTS_BY_MONTH: dict[int, tuple[str, ...]] = {
    1: ("americanfootball_nfl", "basketball_nba", "icehockey_nhl",
        "basketball_ncaab", "soccer", "tennis", "mma", "boxing", "cricket"),
    2: ("basketball_nba", "icehockey_nhl", "basketball_ncaab",
        "soccer", "tennis", "mma", "boxing", "golf", "cricket"),
    3: ("basketball_nba", "icehockey_nhl", "basketball_ncaab",
        "soccer", "tennis", "mma", "boxing", "golf", "cricket", "baseball_mlb"),
    4: ("basketball_nba", "icehockey_nhl", "baseball_mlb",
        "soccer", "tennis", "mma", "boxing", "golf", "cricket"),
    5: ("basketball_nba", "icehockey_nhl", "baseball_mlb", "basketball_wnba",
        "soccer", "tennis", "mma", "boxing", "golf", "cricket"),
    6: ("baseball_mlb", "basketball_wnba", "soccer", "tennis",
        "mma", "boxing", "golf", "cricket"),
    7: ("baseball_mlb", "basketball_wnba", "soccer", "tennis",
        "mma", "boxing", "golf", "cricket"),
    8: ("baseball_mlb", "basketball_wnba", "americanfootball_nfl",
        "americanfootball_ncaaf", "soccer", "tennis", "mma", "boxing", "golf"),
    9: ("americanfootball_nfl", "americanfootball_ncaaf", "baseball_mlb",
        "soccer", "tennis", "mma", "boxing", "golf", "cricket"),
    10: ("americanfootball_nfl", "americanfootball_ncaaf", "basketball_nba",
         "icehockey_nhl", "baseball_mlb", "soccer", "mma", "boxing", "golf"),
    11: ("americanfootball_nfl", "basketball_nba", "icehockey_nhl",
         "americanfootball_ncaaf", "basketball_ncaab", "soccer",
         "mma", "boxing", "tennis", "golf", "cricket"),
    12: ("americanfootball_nfl", "basketball_nba", "icehockey_nhl",
         "americanfootball_ncaaf", "basketball_ncaab", "soccer",
         "mma", "boxing", "tennis", "cricket"),
}

DOC_FALLBACK_CODES = frozenset({"SOURCE_RATE_LIMITED", "SOURCE_TIMEOUT"})

# DOC 429/timeout is common on shared Actions egress. Same-run DOC retry is
# still disabled; recovery uses the already-permitted GKG dump channel instead.
RETRY_POLICY = {
    "same_run_doc_retry": False,
    "gkg_dump_fallback_on": sorted(DOC_FALLBACK_CODES),
    "defer_to": "GKG_DUMP_THEN_NEXT_SCHEDULED_SOURCE_REFRESH",
    "nominal_refresh_interval_seconds": 21600,
    "last_probed_utc": "2026-11-24T16:52:00Z",
    "rate_limit_recovery_not_established": True,
    "gkg_fallback_recovery_verified": False,
    "recovery_verified": False,
}


def active_sport_keys(at: datetime) -> tuple[str, ...]:
    if at.tzinfo is None:
        raise ValueError("GDELT_SLOT_REQUIRES_UTC_AWARE_TIME")
    keys = ACTIVE_SPORTS_BY_MONTH.get(at.month) or tuple(k for k, _ in SPORT_QUERIES)
    return tuple(key for key in keys if key in SPORT_QUERY_MAP)


def _slot_index(at: datetime) -> int:
    return int(at.timestamp() // (6 * 3600))


def query_for(sport_key: str, at: datetime) -> str:
    label = SPORT_QUERY_MAP.get(sport_key)
    if not label:
        raise ValueError("GDELT_SPORT_QUERY_UNKNOWN")
    return material_query_for(label, slot_index=_slot_index(at))


def selection(at: datetime) -> tuple[str, str]:
    """Prefer active-season sports for the fixed four-daily source refresh."""
    if at.tzinfo is None:
        raise ValueError("GDELT_SLOT_REQUIRES_UTC_AWARE_TIME")
    active = active_sport_keys(at)
    index = _slot_index(at) % len(active)
    sport_key = active[index]
    return sport_key, query_for(sport_key, at)


def discover(at: datetime | None = None, *, sport: str | None = None,
             client=None) -> dict:
    at = at or datetime.now(timezone.utc)
    if sport:
        if sport not in SPORT_QUERY_MAP:
            selected = None
        else:
            selected = (sport, query_for(sport, at))
    else:
        selected = selection(at)
    if selected is None:
        return {
            "schema_version": "wow.v17.public_news_receipt.v1",
            "source_status": "BLOCKED",
            "source_code": "SOURCE_TARGET_INVALID",
            "sport_key": sport, "articles": [], "article_count": 0,
            "attempt_count": 0, "attempts": [],
            "acquisition_path": "NONE",
            "fallback_status": "NOT_ATTEMPTED",
            "total_wait_seconds": 0,
            "retry_after_seconds": None,
            "retry_deferred": False,
            "retry_policy": RETRY_POLICY,
            "model_rescore_verified": False,
            "model_feature_eligible": False,
            "historical_pregame_eligible": False,
            "prediction_authority": False, "can_execute": False,
        }
    sport_key, query = selected
    url = ("https://api.gdeltproject.org/api/v2/doc/doc?" +
           urlencode({"query": query, "mode": "ArtList", "format": "json",
                      "maxrecords": 20, "timespan": "24h", "sort": "DateDesc"}))
    result = fetch_gdelt_news(sport_key, url, client=client)
    # Exactly one DOC request. On native 429/timeout, try the same-provider
    # GKG dump path (data.gdeltproject.org) once; never sleep or rotate IPs.
    retry_after = getattr(result, "retry_after_seconds", None)
    attempts = [{
        "attempt": 1,
        "channel": "GDELT_DOC",
        "delay_before_seconds": 0,
        "http_status": getattr(result, "status", None),
        "source_code": result.code,
        "observed_at": result.observed_at,
        "retry_after_seconds": retry_after,
    }]
    acquisition_path = "GDELT_DOC"
    fallback_status = "NOT_ATTEMPTED"
    if (not result.ok) and result.code in DOC_FALLBACK_CODES:
        fallback = fetch_gdelt_gkg_news(sport_key, client=client, at=at)
        attempts.append({
            "attempt": 2,
            "channel": "GDELT_GKG_DUMP",
            "delay_before_seconds": 0,
            "http_status": getattr(fallback, "status", None),
            "source_code": fallback.code,
            "observed_at": fallback.observed_at,
            "retry_after_seconds": getattr(fallback, "retry_after_seconds", None),
        })
        if fallback.ok:
            result = fallback
            acquisition_path = "GDELT_GKG_DUMP_FALLBACK"
            fallback_status = "RECOVERED"
        else:
            fallback_status = fallback.code or "SOURCE_FALLBACK_FAILED"
    raw_articles = result.data.get("articles", []) if isinstance(result.data, dict) else []
    articles = prefer_material_leads(
        raw_articles if isinstance(raw_articles, list) else [],
        limit=20,
    )
    active_keys = list(active_sport_keys(at))
    return {
        "schema_version": "wow.v17.public_news_receipt.v1",
        "source_status": "OK" if result.ok else "BLOCKED",
        "source_code": result.code,
        "sport_key": sport_key,
        "source_observed_at": result.observed_at,
        "articles": articles,
        "article_count": len(articles),
        "material_lead_count": material_lead_count(articles),
        "attempt_count": len(attempts),
        "attempts": attempts,
        "acquisition_path": acquisition_path,
        "fallback_status": fallback_status,
        "total_wait_seconds": 0,
        "retry_after_seconds": retry_after,
        "retry_deferred": (
            result.code == "SOURCE_RATE_LIMITED"
            and acquisition_path == "GDELT_DOC"
        ),
        "retry_policy": RETRY_POLICY,
        "model_rescore_verified": False,
        "attribution": "The GDELT Project (https://www.gdeltproject.org/)",
        "query": query,
        "cadence": (
            "active-season sport per 6-hour slot with rotating material "
            "injury/lineup/availability topics; GKG dump fallback on DOC "
            "429/timeout; not a full 13-sport feed"
        ),
        "active_season_sports": active_keys,
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
    policy = "EXPLICIT_SPORT" if sport else "ACTIVE_SEASON_ROTATION"
    counts = {}
    manifest_source = "NOT_REQUESTED"
    manifest_status = "NOT_REQUESTED"
    if args.candidate_manifest and not sport:
        policy = "SLATE_UNAVAILABLE_ACTIVE_SEASON_ROTATION"
        manifest_source = "UNAVAILABLE"
        manifest_status = "NEWS_MANIFEST_INVALID"
        try:
            manifest = Path(args.candidate_manifest)
            if manifest.stat().st_size > 64_000_000:
                # Nightly Multi-Scout emits the compact event-only sibling in
                # its existing acquisition step, before the 500+ MB quote
                # artifact. We never parse the oversized quote manifest.
                manifest = manifest.with_name("news-active-slate.json")
                manifest_source = "COMPACT_SIBLING"
                if not manifest.exists():
                    raise ValueError("NEWS_COMPACT_MANIFEST_MISSING")
                if manifest.stat().st_size > 2_000_000:
                    raise ValueError("NEWS_COMPACT_MANIFEST_OVERSIZED")
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                if (not isinstance(payload, dict)
                        or payload.get("schema_version") != "wow.v17.news_slate_selection.v1"
                        or payload.get("can_execute") is not False):
                    raise ValueError("NEWS_COMPACT_MANIFEST_INVALID")
            else:
                manifest_source = "FULL_SMALL"
                payload = json.loads(manifest.read_text(encoding="utf-8"))
            selected, counts = select_active_slate(
                payload, at, tuple(key for key, _ in SPORT_QUERIES))
            if selected:
                sport = selected
                policy = "ACTIVE_SLATE_ROTATION"
                manifest_status = "ACTIVE_SPORT_SELECTED"
            else:
                manifest_status = "NEWS_ACTIVE_SLATE_EMPTY"
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            # Expose a *typed* reason, not filesystem paths or raw JSON.
            code = str(exc)
            manifest_status = (code if code in {
                "NEWS_COMPACT_MANIFEST_MISSING",
                "NEWS_COMPACT_MANIFEST_OVERSIZED",
                "NEWS_COMPACT_MANIFEST_INVALID",
            } else "NEWS_MANIFEST_INVALID")
    receipt = discover(at, sport=sport)
    receipt["selection_policy"] = policy
    receipt["slate_active_event_counts"] = counts
    receipt["slate_manifest_source"] = manifest_source
    receipt["slate_manifest_status"] = manifest_status
    dest = Path(args.output)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"source_status": receipt["source_status"],
                      "source_code": receipt["source_code"],
                      "sport_key": receipt["sport_key"],
                      "article_count": len(receipt["articles"]),
                      "attempt_count": receipt["attempt_count"],
                      "acquisition_path": receipt["acquisition_path"],
                      "fallback_status": receipt["fallback_status"],
                      "retry_deferred": receipt["retry_deferred"],
                      "selection_policy": policy,
                      "prediction_authority": False, "can_execute": False}))
    # A single public news outage cannot block the structured Scout or models;
    # the receipt truthfully records its own blocked status.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
