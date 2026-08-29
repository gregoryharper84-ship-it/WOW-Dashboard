"""Fail-closed historical acquisition for NCAAF fitted-model research.

The hydrator stages raw read-only provider responses with deterministic hashes.
It does not transform raw provider rows into model-ready features, certify a
model, publish a probability, or enable execution.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

from ncaaf_cfbd_client import CFBDClient, CFBDResponse, CFBDUnavailable

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False


@dataclass(frozen=True)
class SourceSnapshot:
    provider: str
    endpoint: str
    season: int
    week: Optional[int]
    requested_at: str
    retrieved_at: str
    request_params: Mapping[str, Any]
    response_rows: list[Mapping[str, Any]]
    response_row_count: int
    payload_sha256: str
    acquisition_status: str
    blocker_codes: list[str]
    can_execute: bool = False


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_hash(rows: Iterable[Mapping[str, Any]]) -> str:
    payload = json.dumps(list(rows), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _snapshot(response: CFBDResponse, *, season: int, week: Optional[int], requested_at: datetime) -> SourceSnapshot:
    rows = list(response.rows)
    status = "AVAILABLE" if rows else "EMPTY"
    blockers = [] if rows else ["NCAAF_SOURCE_EMPTY"]
    return SourceSnapshot(
        provider="CFBD",
        endpoint=response.endpoint,
        season=int(season),
        week=week,
        requested_at=requested_at.astimezone(timezone.utc).isoformat(),
        retrieved_at=_utc_now().isoformat(),
        request_params=dict(response.params),
        response_rows=rows,
        response_row_count=len(rows),
        payload_sha256=_canonical_hash(rows),
        acquisition_status=status,
        blocker_codes=blockers,
    )


def hydrate_cfbd_season(
    client: CFBDClient,
    *,
    season: int,
    weeks: Iterable[int],
    rating_families: Iterable[str] = ("elo",),
    classification: Optional[str] = "fbs",
) -> list[SourceSnapshot]:
    """Fetch one season into auditable raw source snapshots.

    Games are fetched per week. Rating families are fetched only through the
    client's allowlist. Provider failure is explicit and aborts the run rather
    than returning partial model-ready evidence.
    """
    requested_at = _utc_now()
    snapshots: list[SourceSnapshot] = []
    normalized_weeks = sorted({int(w) for w in weeks})
    if not normalized_weeks or any(w < 0 or w > 30 for w in normalized_weeks):
        raise ValueError("weeks must contain valid NCAAF week numbers")

    try:
        for week in normalized_weeks:
            games = client.games(year=season, week=week, classification=classification)
            snapshots.append(_snapshot(games, season=season, week=week, requested_at=requested_at))

            for family in rating_families:
                normalized = str(family).strip().lower()
                # Only Elo is week-addressable in the current narrow client.
                # Other families are acquired once per season below so we do
                # not pretend a full-season retrospective value was known in an
                # earlier week.
                if normalized == "elo":
                    rating = client.ratings(normalized, year=season, week=week)
                    snapshots.append(_snapshot(rating, season=season, week=week, requested_at=requested_at))

        for family in rating_families:
            normalized = str(family).strip().lower()
            if normalized != "elo":
                rating = client.ratings(normalized, year=season)
                snap = _snapshot(rating, season=season, week=None, requested_at=requested_at)
                snapshots.append(
                    SourceSnapshot(
                        **{**asdict(snap), "blocker_codes": [*snap.blocker_codes, "RETROSPECTIVE_RATING_NOT_PREGAME_FEATURE"]}
                    )
                )
    except CFBDUnavailable:
        raise

    return snapshots


def persist_source_snapshots(supabase_client: Any, snapshots: Iterable[SourceSnapshot]) -> int:
    """Persist staged raw observations using a service-role Supabase client.

    The target table is RLS-protected and not exposed to anon/authenticated.
    This function returns inserted/upserted row count only; it has no scoring
    side effects.
    """
    rows = [asdict(snapshot) for snapshot in snapshots]
    if not rows:
        return 0
    result = supabase_client.table("wow_ncaaf_source_snapshots").upsert(
        rows,
        on_conflict="provider,endpoint,season,week,payload_sha256",
    ).execute()
    data = getattr(result, "data", None)
    return len(data) if isinstance(data, list) else 0
