"""Append-only NHL source-provenance re-ingest.

Existing ``wow_d1_source_events`` rows deliberately are not updated or deleted.
This module appends settled regular-season games with an explicit V2 provenance
block (game type, neutral site, settled state, ingest code SHA). Re-running is
idempotent through the shared unique source identity. Before any write the lane
re-reads the persisted season and fails closed when a planned event would append
a second V2 identity: rows are immutable, so that state could never be repaired
by retrying.

Only first-party official NHL results are accepted. The module cannot train,
certify, promote, publish a probability, rank an event, or execute a wager.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
from typing import TYPE_CHECKING, Any, Iterable, Mapping

from fastapi import FastAPI
import requests

from github_actions_oidc import scout_route_auth_dependency
from v17.nhl_provenance import (
    SOURCE_PAYLOAD_HASH_SCOPE,
    SOURCE_PROVENANCE_VERSION,
    provenance_envelope_hash,
)
from v17.d1_bulk_candidate_registry import persist_source_events_bulk
from v17.d1_candidate_registry import D1RegistryError
from v17.nhl_fitted_specialist import (
    NHLFittedSpecialistError,
    build_reingest_timeline_receipt,
    load_nhl_season_events,
    persist_nhl_season_timeline_receipt,
)

if TYPE_CHECKING:
    from nhl_candidate_pipeline import NHLGame

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
DEFAULT_MAX_GAMES = 1600
SETTLED_GAME_STATES = frozenset({"FINAL", "OFF"})


class NHLSourceReingestError(RuntimeError):
    def __init__(self, code: str, detail: Any = None):
        """Store a typed NHL specialist failure code and blocker list."""
        self.code = str(code)
        self.detail = detail
        super().__init__(f"{self.code}:{detail}" if detail is not None else self.code)


def default_season_id(now: datetime | None = None) -> int:
    # nhl_candidate_pipeline owns sklearn-backed training imports. Keep it off
    # the production route-construction path; re-ingest only needs it on call.
    """Compute the current NHL season id from wall-clock UTC."""
    from nhl_candidate_pipeline import season_id

    current = now or datetime.now(timezone.utc)
    start_year = current.year if current.month >= 7 else current.year - 1
    return season_id(start_year)


def _season_start_year(value: Any) -> int:
    """Derive the NHL season start year from an event timestamp."""
    from nhl_candidate_pipeline import season_id

    token = str(value or "").strip()
    if len(token) != 8 or not token.isdigit():
        raise NHLSourceReingestError("NHL_SEASON_ID_INVALID", token)
    start_year = int(token[:4])
    if season_id(start_year) != int(token):
        raise NHLSourceReingestError("NHL_SEASON_ID_INVALID", token)
    return start_year


def _exact_season_id(value: Any) -> int | None:
    """Return an exact season id; floats and booleans are never coerced."""
    if type(value) is int:
        return value
    if isinstance(value, str):
        text = value.strip()
        # Require ASCII digits: Unicode Nd can pass isdigit() and convert via int().
        if text and text.isascii() and text.isdigit():
            try:
                return int(text)
            except ValueError:
                return None
    return None


def _blocked(code: str, *, stage: str, detail: Any = None) -> dict[str, Any]:
    return {
        "status": "BLOCKED",
        "code": code,
        "blocked_stage": stage,
        "detail": detail,
        "provenance_complete": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def build_reingest_rows(
    games: Iterable[NHLGame],
    *,
    ingest_code_sha: str,
    provenance_version: str = SOURCE_PROVENANCE_VERSION,
) -> list[dict[str, Any]]:
    """Build deterministic append-only rows from settled regular-season games."""
    from nhl_candidate_pipeline import NHLGame, game_record

    sha = str(ingest_code_sha or "").strip()
    if len(sha) < 7:
        raise NHLSourceReingestError("NHL_INGEST_CODE_SHA_UNAVAILABLE")
    if str(provenance_version or "").strip() != SOURCE_PROVENANCE_VERSION:
        raise NHLSourceReingestError("NHL_INGEST_PROVENANCE_VERSION_INVALID", provenance_version)

    by_id: dict[str, dict[str, Any]] = {}
    for game in games:
        if not isinstance(game, NHLGame):
            raise NHLSourceReingestError("NHL_INGEST_GAME_TYPE_INVALID", type(game).__name__)
        if game.can_execute is not False or game.historical_reconstruction is not True:
            raise NHLSourceReingestError("NHL_INGEST_GOVERNANCE_INVALID", game.game_id)
        if game.game_type != 2:
            raise NHLSourceReingestError("NHL_SOURCE_GAME_TYPE_UNVERIFIED", game.game_id)
        if game.game_state not in SETTLED_GAME_STATES:
            raise NHLSourceReingestError("NHL_SOURCE_GAME_STATE_UNVERIFIED", game.game_id)
        if type(game.neutral_site) is not bool:
            raise NHLSourceReingestError("NHL_SOURCE_NEUTRAL_SITE_UNVERIFIED", game.game_id)
        # normalize_game historically coerced ``neutralSite`` with bool(), so a
        # missing key, string, or numeric value became a persisted boolean. The
        # raw evidence must be an explicit boolean before this lane may write it.
        if type(getattr(game, "neutral_site_evidence", None)) is not bool:
            raise NHLSourceReingestError("NHL_SOURCE_NEUTRAL_SITE_UNVERIFIED", game.game_id)
        raw_commitment = str(game.source_payload_sha256 or "").strip().lower()
        if len(raw_commitment) != 64 or any(
            char not in "0123456789abcdef" for char in raw_commitment
        ):
            raise NHLSourceReingestError("NHL_SOURCE_PAYLOAD_HASH_INVALID", game.game_id)
        row = game_record(game)
        row["game_type"] = game.game_type
        row["neutral_site"] = bool(game.neutral_site)
        row["game_state"] = game.game_state
        row["provenance_version"] = SOURCE_PROVENANCE_VERSION
        row["ingest_code_sha"] = sha
        row["source_payload_commitment"] = raw_commitment
        row["payload_hash_scope"] = SOURCE_PAYLOAD_HASH_SCOPE
        # Stable per event regardless of which club's schedule supplied the
        # payload. This avoids the legacy unique-index collision and duplicate
        # V2 rows across partial/full club runs.
        row["source_payload_sha256"] = provenance_envelope_hash(
            official_event_id=game.game_id,
            season=game.season_id,
            event_start_time=game.event_start_time,
            home_team=game.home_team,
            away_team=game.away_team,
            home_score=game.home_score,
            away_score=game.away_score,
            game_type=game.game_type,
            game_state=game.game_state,
            neutral_site=game.neutral_site,
        )
        existing = by_id.get(game.game_id)
        if existing is not None:
            identity = (
                existing["event_start_time"],
                existing["home_team"],
                existing["away_team"],
                existing["home_score"],
                existing["away_score"],
            )
            incoming = (
                row["event_start_time"],
                row["home_team"],
                row["away_team"],
                row["home_score"],
                row["away_score"],
            )
            if identity != incoming:
                raise NHLSourceReingestError("NHL_INGEST_EVENT_IDENTITY_CONFLICT", game.game_id)
            # The same game appears in both clubs' schedule payloads. Choosing the
            # smaller hash makes the persisted source identity reproducible.
            if str(row["source_payload_sha256"]) < str(existing["source_payload_sha256"]):
                by_id[game.game_id] = row
            continue
        by_id[game.game_id] = row
    return [by_id[key] for key in sorted(by_id, key=lambda item: (by_id[item]["event_start_time"], item))]


def _v2_identities_from_rows(
    rows: Iterable[Mapping[str, Any]], expected: Iterable[str]
) -> dict[str, set[str]]:
    """Group persisted V2 envelope identities by official event id."""
    wanted = set(expected)
    identities: dict[str, set[str]] = {}
    for row in rows:
        event_id = str(row.get("official_event_id") or "").strip()
        if event_id not in wanted:
            continue
        outcome = row.get("outcome_json")
        if not isinstance(outcome, Mapping):
            continue
        if outcome.get("provenance_version") != SOURCE_PROVENANCE_VERSION:
            continue
        identities.setdefault(event_id, set()).add(
            str(row.get("source_payload_sha256") or "").strip().lower()
        )
    return identities


def _persisted_v2_identities(
    db: Any, *, season: int, expected: Iterable[str]
) -> dict[str, set[str]]:
    """Read persisted V2 identities before writing to an append-only ledger.

    Existing rows are never mutated, so appending a second identity for one
    official event would leave the season unusable for fitted replay with no
    retry path. This read-only preflight lets the lane fail closed instead.
    """
    return _v2_identities_from_rows(
        load_nhl_season_events(db, season=season), expected
    )


def _identity_conflicts(
    rows: Iterable[dict[str, Any]], persisted: dict[str, set[str]]
) -> list[str]:
    """Return planned events whose persisted V2 identity would not converge."""
    conflicted: list[str] = []
    for row in rows:
        event_id = str(row.get("official_event_id") or "").strip()
        observed = persisted.get(event_id) or set()
        planned = str(row.get("source_payload_sha256") or "").strip().lower()
        if len(observed) > 1 or (observed and planned not in observed):
            conflicted.append(event_id)
    return sorted(conflicted)


def run_nhl_source_reingest(
    db: Any,
    *,
    season: int | None = None,
    teams: Iterable[str] | None = None,
    session: Any = None,
    ingest_code_sha: str | None = None,
    dry_run: bool = False,
    max_games: int = DEFAULT_MAX_GAMES,
) -> dict[str, Any]:
    """Acquire settled official games and append V2 provenance rows.

    All games are acquired and validated before the first write. A source or
    identity failure therefore cannot leave a partially appended timeline.
    """
    from nhl_candidate_pipeline import NHLCandidateError, fetch_historical_games

    try:
        if season is None:
            effective_season = default_season_id()
        else:
            exact_season = _exact_season_id(season)
            if exact_season is None:
                raise NHLSourceReingestError(
                    "NHL_SEASON_ID_INVALID", type(season).__name__
                )
            effective_season = exact_season
        start_year = _season_start_year(effective_season)
    except (TypeError, ValueError, NHLSourceReingestError) as exc:
        return _blocked(
            "NHL_SEASON_ID_INVALID",
            stage="CONFIGURATION",
            detail={"error_type": type(exc).__name__},
        )

    sha = str(ingest_code_sha or os.getenv("RENDER_GIT_COMMIT") or "").strip()
    if len(sha) < 7:
        return _blocked("NHL_INGEST_CODE_SHA_UNAVAILABLE", stage="PROVENANCE")

    try:
        games = fetch_historical_games(
            [start_year],
            teams=tuple(teams) if teams is not None else None,
            session=session or requests,
            strict=True,
        )
    except NHLCandidateError as exc:
        return _blocked(
            exc.code,
            stage="SOURCE_ACQUISITION",
            detail={"error_type": type(exc).__name__},
        )
    except Exception as exc:  # noqa: BLE001 - acquisition is a typed boundary
        return _blocked(
            "NHL_SOURCE_ACQUISITION_FAILED",
            stage="SOURCE_ACQUISITION",
            detail={"error_type": type(exc).__name__},
        )

    selected = [game for game in games if game.season_id == effective_season]
    if not selected:
        return _blocked(
            "NHL_REINGEST_NO_SETTLED_GAMES",
            stage="SOURCE_ACQUISITION",
            detail={"season": effective_season},
        )
    if len(selected) > max_games:
        return _blocked(
            "NHL_REINGEST_GAME_LIMIT_EXCEEDED",
            stage="SOURCE_ACQUISITION",
            detail={"games": len(selected), "max_games": max_games},
        )

    try:
        rows = build_reingest_rows(selected, ingest_code_sha=sha)
    except NHLSourceReingestError as exc:
        return _blocked(
            exc.code,
            stage="PROVENANCE",
            detail={"error_type": type(exc).__name__},
        )

    expected_events = {row["official_event_id"] for row in rows}
    try:
        persisted = _persisted_v2_identities(
            db, season=effective_season, expected=expected_events
        )
    except NHLFittedSpecialistError as exc:
        return _blocked(
            "NHL_SOURCE_IDENTITY_UNVERIFIABLE",
            stage="SOURCE_IDENTITY",
            detail={"blockers": list(exc.blockers)},
        )
    except Exception as exc:  # noqa: BLE001 - identity preflight is a typed boundary
        return _blocked(
            "NHL_SOURCE_IDENTITY_UNVERIFIABLE",
            stage="SOURCE_IDENTITY",
            detail={"error_type": type(exc).__name__, "retryable": True},
        )
    conflicted = _identity_conflicts(rows, persisted)
    if conflicted:
        return _blocked(
            "NHL_SOURCE_IDENTITY_CONFLICT",
            stage="SOURCE_IDENTITY",
            detail={
                "conflicting_event_n": len(conflicted),
                "conflicting_event_ids": conflicted[:20],
                "retryable": False,
                "requires_append_only_correction_protocol": True,
            },
        )
    if dry_run:
        return {
            "status": "NHL_SOURCE_REINGEST_DRY_RUN_READY",
            "season": str(effective_season),
            "source_event_n": len(rows),
            "event_ids": sorted(expected_events),
            "provenance_version": SOURCE_PROVENANCE_VERSION,
            "ingest_code_sha": sha,
            "automatic_certification": False,
            "automatic_promotion": False,
            "probability_publishable": False,
            "can_execute": False,
        }

    try:
        persistence = persist_source_events_bulk(db, rows)
    except D1RegistryError as exc:
        return _blocked(
            exc.code,
            stage="SOURCE_PERSISTENCE",
            detail={"error_type": type(exc).__name__},
        )
    except Exception as exc:  # noqa: BLE001 - persistence is a typed boundary
        return _blocked(
            "NHL_SOURCE_PERSISTENCE_FAILED",
            stage="SOURCE_PERSISTENCE",
            detail={
                "error_type": type(exc).__name__,
                "retryable": True,
                "partial_write_possible": True,
            },
        )

    try:
        verified = load_nhl_season_events(db, season=effective_season)
    except NHLFittedSpecialistError as exc:
        return {
            "status": "NHL_SOURCE_REINGEST_PROVENANCE_UNVERIFIED",
            "season": str(effective_season),
            "source_event_n": len(rows),
            "persistence": persistence,
            "blockers": list(exc.blockers),
            "provenance_complete": False,
            "automatic_certification": False,
            "automatic_promotion": False,
            "probability_publishable": False,
            "can_execute": False,
        }
    except Exception as exc:  # noqa: BLE001 - verification is a typed boundary
        return _blocked(
            "NHL_SOURCE_VERIFICATION_FAILED",
            stage="SOURCE_VERIFICATION",
            detail={
                "error_type": type(exc).__name__,
                "retryable": True,
                "persistence": persistence,
            },
        )

    # The preflight is check-then-write, so a concurrent writer or a correction
    # that surfaced mid-run can still land a second identity. Verify the
    # post-write season with the same invariant the reader enforces; a
    # conflicted season must never be reported complete.
    post_conflicts = _identity_conflicts(
        rows, _v2_identities_from_rows(verified, expected_events)
    )
    if post_conflicts:
        return _blocked(
            "NHL_SOURCE_IDENTITY_CONFLICT",
            stage="SOURCE_VERIFICATION",
            detail={
                "conflicting_event_n": len(post_conflicts),
                "conflicting_event_ids": post_conflicts[:20],
                "retryable": False,
                "requires_append_only_correction_protocol": True,
            },
        )

    provenanced: set[str] = set()
    distinct: set[str] = set()
    for row in verified:
        event_id = str(row.get("official_event_id") or "")
        if not event_id:
            continue
        distinct.add(event_id)
        outcome = row.get("outcome_json")
        if not isinstance(outcome, dict):
            continue
        if (
            outcome.get("provenance_version") == SOURCE_PROVENANCE_VERSION
            and type(outcome.get("game_type")) is int
            and outcome.get("game_type") == 2
            and type(outcome.get("neutral_site")) is bool
            and str(outcome.get("game_state") or "").upper() in SETTLED_GAME_STATES
        ):
            provenanced.add(event_id)
    complete = expected_events <= provenanced and distinct == expected_events
    timeline_receipt: dict[str, Any] | None = None
    # Full-season scope only: partial team filters must never mint a scoring receipt.
    if complete and teams is None:
        try:
            receipt = build_reingest_timeline_receipt(
                verified,
                season=effective_season,
                ingest_code_sha=sha,
            )
            timeline_receipt = persist_nhl_season_timeline_receipt(db, receipt)
        except Exception as exc:  # noqa: BLE001 - receipt persist is a typed boundary
            return _blocked(
                "NHL_SOURCE_TIMELINE_RECEIPT_PERSIST_FAILED",
                stage="SOURCE_VERIFICATION",
                detail={
                    "error_type": type(exc).__name__,
                    "retryable": True,
                    "persistence": persistence,
                    "provenance_complete": complete,
                },
            )
    return {
        "status": (
            "NHL_SOURCE_REINGEST_PERSISTED"
            if complete
            else "NHL_SOURCE_REINGEST_PROVENANCE_INCOMPLETE"
        ),
        "season": str(effective_season),
        "source_event_n": len(rows),
        "distinct_source_event_n": len(distinct),
        "provenanced_event_n": len(provenanced),
        "persistence": persistence,
        "timeline_receipt": timeline_receipt,
        "timeline_receipt_persisted": timeline_receipt is not None,
        "provenance_version": SOURCE_PROVENANCE_VERSION,
        "ingest_code_sha": sha,
        "provenance_complete": complete,
        "blockers": [] if complete else ["NHL_SOURCE_PROVENANCE_INCOMPLETE"],
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def install_nhl_source_reingest_route(
    app: FastAPI,
    *,
    auth_dependency: Any,
    db_client_fn: Any,
) -> None:
    """Install the governed NHL source re-ingest HTTP route."""
    path = "/internal/v17/nhl-source-reingest"
    if any(getattr(route, "path", None) == path for route in app.router.routes):
        return

    @app.post(
        path,
        dependencies=[scout_route_auth_dependency(auth_dependency)],
        operation_id="runWowV17NhlSourceReingest",
    )
    def run_reingest(season: int | None = None, dry_run: bool = False) -> dict[str, Any]:
        return run_nhl_source_reingest(db_client_fn(), season=season, dry_run=dry_run)


__all__ = [
    "CAN_EXECUTE",
    "DEFAULT_MAX_GAMES",
    "NHLSourceReingestError",
    "SOURCE_PROVENANCE_VERSION",
    "build_reingest_rows",
    "default_season_id",
    "install_nhl_source_reingest_route",
    "run_nhl_source_reingest",
]
