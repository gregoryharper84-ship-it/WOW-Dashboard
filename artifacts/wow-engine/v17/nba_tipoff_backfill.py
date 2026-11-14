"""NBA-only, provenance-checked scheduled-start backfill for settled research rows.

Default DRY RUN. Only explicit --apply may fill a NULL start; never insert games,
rewrite original source evidence, or alter pregame features/model artifacts.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from typing import Any, Iterable
from zoneinfo import ZoneInfo

TABLE = "wow_nba_training_games"
PAGE_SIZE = 500
MATCH_FIELDS = (
    "game_id", "season", "game_date", "home_team_id", "away_team_id",
    "home_score", "away_score", "home_win", "source_provider", "source_endpoint",
    "source_payload_sha256",
)
READ_FIELDS = ",".join((*MATCH_FIELDS, "scheduled_start_utc", "source_retrieved_at"))
CAN_EXECUTE = False


class BackfillIntegrityError(RuntimeError):
    pass


def _instant(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("missing instant")
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.utcoffset() is None:
        raise ValueError("naive instant")
    return instant.astimezone(timezone.utc)


def classify(stored: dict[str, Any], source: dict[str, Any] | None) -> str:
    """Pure match gate: never infer a tip-off from date, timezones or scores."""
    if stored.get("scheduled_start_utc") is not None:
        return "ALREADY_PRESENT"
    if stored.get("source_provider") != "SPORTSDATAVERSE_ESPN":
        return "OTHER_PROVIDER"
    if not isinstance(source, dict):
        return "CANONICAL_GAME_NOT_FOUND"
    if any(stored.get(key) != source.get(key) for key in MATCH_FIELDS):
        return "SOURCE_IDENTITY_OR_HASH_CONFLICT"
    start = source.get("scheduled_start_utc")
    if not start:
        return "SOURCE_TIME_UNVERIFIED"
    try:
        start_at = _instant(start)
        observed_at = _instant(stored.get("source_retrieved_at"))
        game_day = str(stored["game_date"])
        if (
            start_at.astimezone(ZoneInfo("America/New_York")).date().isoformat() != game_day
            or start_at >= observed_at
        ):
            return "CHRONOLOGY_CONFLICT"
    except (ValueError, TypeError, OverflowError, KeyError):
        return "CHRONOLOGY_UNVERIFIED"
    return "VERIFIED_BACKFILL_ELIGIBLE"


def _load_existing(client: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for start in range(0, 1000000, PAGE_SIZE):
        response = (
            client.table(TABLE).select(READ_FIELDS).order("game_id")
            .range(start, start + PAGE_SIZE - 1).execute()
        )
        chunk = getattr(response, "data", None)
        if not isinstance(chunk, list) or len(chunk) > PAGE_SIZE:
            raise BackfillIntegrityError("NBA_BACKFILL_PAGE_READ_INVALID")
        for row in chunk:
            if not isinstance(row, dict) or not isinstance(row.get("game_id"), str):
                raise BackfillIntegrityError("NBA_BACKFILL_STORED_ROW_INVALID")
            if row["game_id"] in seen:
                raise BackfillIntegrityError("NBA_BACKFILL_DUPLICATE_STORED_ID")
            seen.add(row["game_id"])
            records.append(row)
        if len(chunk) < PAGE_SIZE:
            return records
    raise BackfillIntegrityError("NBA_BACKFILL_CORPUS_TOO_LARGE")


def _fetch_normalized_sources(years: Iterable[int]) -> dict[str, dict[str, Any]]:
    # Import the existing source-entitled downloader and normalizer, not an
    # alternate provider/crosswalk. Acquisition is completed before any write.
    from basketball_event_hydration_runtime import (
        fetch_sportsdataverse_games, normalize_sportsdataverse_game,
    )
    all_rows: dict[str, dict[str, Any]] = {}
    for year in sorted(set(years)):
        raw = fetch_sportsdataverse_games("NBA", year)
        retrieved_at = datetime.now(timezone.utc).isoformat()
        for event in raw:
            row = normalize_sportsdataverse_game(
                event, "NBA", retrieved_at, source_release_year=year,
            )
            if row is None:
                continue
            key = row["game_id"]
            if key in all_rows:
                raise BackfillIntegrityError("NBA_BACKFILL_DUPLICATE_CANONICAL_ID")
            all_rows[key] = row
    return all_rows


def _read_one(client: Any, game_id: str) -> dict[str, Any]:
    response = client.table(TABLE).select(READ_FIELDS).eq("game_id", game_id).limit(2).execute()
    rows = getattr(response, "data", None)
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise BackfillIntegrityError("NBA_BACKFILL_WRITE_READBACK_INVALID")
    return rows[0]


def _apply_one(client: Any, old: dict[str, Any], source: dict[str, Any]) -> bool:
    # Check the full old identity immediately before writing. The UPDATE also
    # guards these identity fields in SQL; a concurrent non-null start wins.
    before = _read_one(client, old["game_id"])
    if classify(before, source) == "ALREADY_PRESENT":
        return False
    if classify(before, source) != "VERIFIED_BACKFILL_ELIGIBLE":
        raise BackfillIntegrityError("NBA_BACKFILL_RACE_OR_SOURCE_CONFLICT")
    query = client.table(TABLE).update({"scheduled_start_utc": source["scheduled_start_utc"]})
    for key in MATCH_FIELDS:
        query = query.eq(key, before[key])
    query = query.is_("scheduled_start_utc", "null")
    response = query.execute()
    if not isinstance(getattr(response, "data", None), list):
        raise BackfillIntegrityError("NBA_BACKFILL_UPDATE_RESPONSE_INVALID")
    after = _read_one(client, old["game_id"])
    if any(after.get(k) != before.get(k) for k in MATCH_FIELDS):
        raise BackfillIntegrityError("NBA_BACKFILL_SOURCE_IDENTITY_CHANGED")
    if _instant(str(after.get("scheduled_start_utc"))) != _instant(source["scheduled_start_utc"]):
        raise BackfillIntegrityError("NBA_BACKFILL_START_READBACK_MISMATCH")
    return bool(response.data)


def run(client: Any, *, years: Iterable[int] | None = None,
        apply: bool = False, max_updates: int = 250,
        source_rows: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    if not isinstance(max_updates, int) or isinstance(max_updates, bool) or max_updates < 1:
        raise ValueError("max_updates must be positive")
    stored_rows = _load_existing(client)
    years_requested = sorted(set(int(y) for y in years)) if years is not None else sorted({
        row["season"] for row in stored_rows if type(row.get("season")) is int
    })
    if not years_requested:
        raise BackfillIntegrityError("NBA_BACKFILL_NO_SEASONS")
    # Pre-fetch and verify the full source set before a single write is attempted.
    sources = source_rows if source_rows is not None else _fetch_normalized_sources(years_requested)
    counters: Counter[str] = Counter()
    eligible: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for stored in stored_rows:
        if stored.get("season") not in years_requested:
            continue
        source = sources.get(stored["game_id"])
        verdict = classify(stored, source)
        counters[verdict] += 1
        if verdict == "VERIFIED_BACKFILL_ELIGIBLE":
            eligible.append((stored, source))
    result: dict[str, Any] = {
        "mode": "APPLY" if apply else "DRY_RUN", "can_execute": False,
        "years": years_requested, "rows_read": len(stored_rows),
        "rows_in_scope": sum(counters.values()), "eligible": len(eligible),
        "reason_counts": dict(sorted(counters.items())),
        "updated": 0, "limit": max_updates,
        "unattempted_eligible": len(eligible) if not apply else max(0, len(eligible)-max_updates),
        "probability_publishable": False, "model_certification_changed": False,
    }
    if apply:
        # The only data mutation, deliberately bounded and idempotent.
        for stored, source in eligible[:max_updates]:
            if _apply_one(client, stored, source):
                result["updated"] += 1
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seasons", help="NBA ending/release years; default all existing")
    parser.add_argument("--apply", action="store_true", help="requires governed authorization")
    parser.add_argument("--max-updates", type=int, default=250)
    args = parser.parse_args()
    from basketball_event_hydration_runtime import _client
    years = [int(s.strip()) for s in args.seasons.split(",")] if args.seasons else None
    print(json.dumps(run(_client(), years=years, apply=args.apply,
                         max_updates=args.max_updates), sort_keys=True))


if __name__ == "__main__":
    main()
