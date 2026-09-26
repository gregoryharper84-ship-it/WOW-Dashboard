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


def _blocked_optional_rating_snapshot(
    *,
    family: str,
    season: int,
    week: Optional[int],
    requested_at: datetime,
    code: str,
) -> SourceSnapshot:
    normalized = str(family or "").strip().lower()
    params: dict[str, Any] = {"year": int(season)}
    if normalized == "elo" and week is not None:
        params["week"] = int(week)
    return SourceSnapshot(
        provider="CFBD",
        endpoint=f"/ratings/{normalized}",
        season=int(season),
        week=week,
        requested_at=requested_at.astimezone(timezone.utc).isoformat(),
        retrieved_at=_utc_now().isoformat(),
        request_params=params,
        response_rows=[],
        response_row_count=0,
        payload_sha256=_canonical_hash([]),
        acquisition_status="BLOCKED",
        blocker_codes=[str(code or "CFBD_RATING_ACQUISITION_FAILED")],
        can_execute=False,
    )


def hydrate_cfbd_season(
    client: CFBDClient,
    *,
    season: int,
    weeks: Iterable[int],
    rating_families: Iterable[str] = ("elo",),
    classification: Optional[str] = "fbs",
    allow_optional_rating_failures: bool = False,
) -> list[SourceSnapshot]:
    """Fetch one season into auditable raw source snapshots.

    ``/games`` is always mandatory. Rating families are optional only when the
    caller explicitly enables ``allow_optional_rating_failures``. In that mode,
    a rating-provider failure is preserved as a blocked source snapshot while
    valid game snapshots continue through persistence. This does not fill or
    synthesize rating evidence and cannot make a model publishable.
    """
    requested_at = _utc_now()
    snapshots: list[SourceSnapshot] = []
    normalized_weeks = sorted({int(w) for w in weeks})
    if not normalized_weeks or any(w < 0 or w > 30 for w in normalized_weeks):
        raise ValueError("weeks must contain valid NCAAF week numbers")

    for week in normalized_weeks:
        # Settled game identity/result history is required. Any /games provider
        # failure remains fail-closed even when optional rating degradation is
        # permitted by the caller.
        games = client.games(year=season, week=week, classification=classification)
        snapshots.append(_snapshot(games, season=season, week=week, requested_at=requested_at))

        for family in rating_families:
            normalized = str(family).strip().lower()
            # Only Elo is week-addressable in the current narrow client.
            # Other families are acquired once per season below so we do not
            # pretend a full-season retrospective value was known earlier.
            if normalized != "elo":
                continue
            try:
                rating = client.ratings(normalized, year=season, week=week)
            except CFBDUnavailable as exc:
                if not allow_optional_rating_failures:
                    raise
                snapshots.append(
                    _blocked_optional_rating_snapshot(
                        family=normalized,
                        season=season,
                        week=week,
                        requested_at=requested_at,
                        code=exc.code,
                    )
                )
            else:
                snapshots.append(_snapshot(rating, season=season, week=week, requested_at=requested_at))

    for family in rating_families:
        normalized = str(family).strip().lower()
        if normalized == "elo":
            continue
        try:
            rating = client.ratings(normalized, year=season)
        except CFBDUnavailable as exc:
            if not allow_optional_rating_failures:
                raise
            snapshots.append(
                _blocked_optional_rating_snapshot(
                    family=normalized,
                    season=season,
                    week=None,
                    requested_at=requested_at,
                    code=exc.code,
                )
            )
            continue
        snap = _snapshot(rating, season=season, week=None, requested_at=requested_at)
        snapshots.append(
            SourceSnapshot(
                **{
                    **asdict(snap),
                    "blocker_codes": [*snap.blocker_codes, "RETROSPECTIVE_RATING_NOT_PREGAME_FEATURE"],
                }
            )
        )

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
