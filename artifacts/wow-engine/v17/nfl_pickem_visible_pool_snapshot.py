"""Research-only, privacy-minimized capture of visible NFL Pick'em pool selections.

Consumes only aggregated counts, immutable Google Drive copy metadata, and
certified event identities supplied by the caller. Does NOT infer historical
individual submissions, the user's own row, complete pool size, a true
opponent-only distribution, sporting probabilities, or first-place equity.
"""
from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Mapping, Sequence

from v17.nfl_pickem_full_pool_equity import (
    MAX_GAMES, PoolEquityInputError, _required, _time,
)

MAX_VISIBLE_ROWS = 1000


def _count(value: Any, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PoolEquityInputError(code)
    return value


def summarize_visible_pool_snapshot(
    snapshot: Mapping[str, Any],
    *,
    events: Sequence[Mapping[str, Any]],
    earliest_kickoff_at: str,
) -> dict[str, Any]:
    """Summarize observed sheet selections; never publish or change a pick.

    `earliest_kickoff_at` is a conservative pregame visibility cutoff, **not**
    a claim that the contest uses a full-week lock. The caller provides only
    team counts; participant names, user identities and picks are not retained.
    """
    cutoff = _time(earliest_kickoff_at, "PICKEM_VISIBLE_CUTOFF_INVALID")
    if not isinstance(snapshot, Mapping):
        raise PoolEquityInputError("PICKEM_VISIBLE_SNAPSHOT_REQUIRED")
    if not isinstance(events, (tuple, list)) or not 1 <= len(events) <= MAX_GAMES:
        raise PoolEquityInputError("PICKEM_VISIBLE_EVENT_SET_INVALID")
    if snapshot.get("audience") != "ALL_VISIBLE_ENTRIES":
        raise PoolEquityInputError("PICKEM_VISIBLE_AUDIENCE_INVALID")
    if snapshot.get("source_kind") != "GOOGLE_DRIVE_COPY":
        raise PoolEquityInputError("PICKEM_VISIBLE_SOURCE_KIND_INVALID")
    if snapshot.get("private_owner_only") is not True:
        raise PoolEquityInputError("PICKEM_VISIBLE_PRIVACY_UNVERIFIED")

    source_id = _required(snapshot.get("source_file_id"), "PICKEM_VISIBLE_SOURCE_ID_REQUIRED")
    copy_id = _required(snapshot.get("copy_file_id"), "PICKEM_VISIBLE_COPY_ID_REQUIRED")
    if source_id == copy_id:
        raise PoolEquityInputError("PICKEM_VISIBLE_COPY_NOT_INDEPENDENT")
    created = _time(snapshot.get("provider_created_at"), "PICKEM_VISIBLE_COPY_TIMESTAMP_INVALID")
    modified = _time(snapshot.get("provider_modified_at"), "PICKEM_VISIBLE_COPY_TIMESTAMP_INVALID")
    available = _time(snapshot.get("available_to_user_at"), "PICKEM_VISIBLE_AVAILABILITY_INVALID")
    if modified != created:
        raise PoolEquityInputError("PICKEM_VISIBLE_COPY_MODIFIED")
    if created > available or available >= cutoff:
        raise PoolEquityInputError("PICKEM_VISIBLE_NOT_BEFORE_KICKOFF")

    count = _count(snapshot.get("visible_row_count"), "PICKEM_VISIBLE_ROW_COUNT_INVALID")
    if count < 1 or count > MAX_VISIBLE_ROWS:
        raise PoolEquityInputError("PICKEM_VISIBLE_ROW_COUNT_INVALID")
    aggregate = snapshot.get("event_counts")
    if not isinstance(aggregate, Mapping):
        raise PoolEquityInputError("PICKEM_VISIBLE_EVENT_SET_INVALID")

    identities: dict[str, tuple[str, str]] = {}
    for row in events:
        if not isinstance(row, Mapping):
            raise PoolEquityInputError("PICKEM_VISIBLE_EVENT_ID_INVALID")
        event = _required(row.get("official_event_id"), "PICKEM_VISIBLE_EVENT_ID_INVALID")
        home = _required(row.get("home_team"), "PICKEM_VISIBLE_TEAM_ID_INVALID")
        away = _required(row.get("away_team"), "PICKEM_VISIBLE_TEAM_ID_INVALID")
        if home == away or event in identities:
            raise PoolEquityInputError("PICKEM_VISIBLE_EVENT_ID_INVALID")
        identities[event] = (home, away)
    if set(aggregate) != set(identities):
        raise PoolEquityInputError("PICKEM_VISIBLE_EVENT_SET_INVALID")

    rows = []
    for event, (home, away) in identities.items():
        votes = aggregate[event]
        if not isinstance(votes, Mapping) or set(votes) != {home, away}:
            raise PoolEquityInputError("PICKEM_VISIBLE_TEAM_COUNTS_INVALID")
        h = _count(votes[home], "PICKEM_VISIBLE_TEAM_COUNTS_INVALID")
        a = _count(votes[away], "PICKEM_VISIBLE_TEAM_COUNTS_INVALID")
        if h + a != count:
            raise PoolEquityInputError("PICKEM_VISIBLE_PARTIAL_OR_DUPLICATE_VOTES")
        rows.append({
            "official_event_id": event,
            "home_team": home,
            "away_team": away,
            "home_count": h,
            "away_count": a,
            "visible_rows": count,
            "observed_home_fraction": h / count,
        })

    # A digest over these observed aggregates is for change detection only;
    # it does NOT authenticate the original spreadsheet or individual entries.
    receipt_payload = {
        "source_file_id": source_id,
        "copy_file_id": copy_id,
        "provider_created_at": created.isoformat(),
        "provider_modified_at": modified.isoformat(),
        "available_to_user_at": available.isoformat(),
        "earliest_kickoff_at": cutoff.isoformat(),
        "visible_row_count": count,
        "event_counts": sorted(rows, key=lambda row: row["official_event_id"]),
    }
    digest = sha256(json.dumps(receipt_payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        "status": "PICKEM_VISIBLE_SAMPLE_RECORDED",
        "receipt_digest_sha256": digest,
        "digest_is_provider_signature": False,
        "source_kind": "GOOGLE_DRIVE_COPY",
        "copy_file_id": copy_id,
        "provider_created_at": created.isoformat(),
        "provider_modified_at": modified.isoformat(),
        "available_to_user_at": available.isoformat(),
        "earliest_kickoff_at": cutoff.isoformat(),
        "cutoff_semantics": "EARLIEST_GAME_KICKOFF_NOT_LEAGUE_LOCK",
        "sample_audience": "ALL_VISIBLE_ENTRIES_NOT_OPPONENTS",
        "visible_row_count": count,
        "event_count": len(rows),
        "rows": rows,
        "pool_size_verified": False,
        "user_entry_excluded": False,
        "individual_submission_times_verified": False,
        "certified_prelock_opponent_forecast": False,
        "certified_first_place_equity": False,
        "sporting_probability_modified": False,
        "automatic_promotion": False,
        "production_pick_mutation_allowed": False,
        "can_execute": False,
    }


__all__ = ["MAX_VISIBLE_ROWS", "summarize_visible_pool_snapshot"]
