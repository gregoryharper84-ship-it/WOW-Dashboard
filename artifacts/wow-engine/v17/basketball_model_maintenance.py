"""Governed NBA/WNBA training refresh and shadow-fit maintenance.

This module advances only model-development evidence:

  source hydration -> provenance check -> deterministic feature replay
  -> league-isolated fit/calibration -> SHADOW evidence

Fresh provider acquisition and persisted-corpus replay are deliberately separate
stages. A temporary/missing acquisition provider must not prevent replay of an
already persisted, provenance-complete, fresh corpus. Acquisition degradation is
retained as typed metadata and never converted into model readiness.

Explicit-clock maintenance may hydrate but cannot fit historical shadow evidence
until persisted replay reads support the same cutoff. Default live maintenance
continues to replay the current persisted corpus.

It never promotes or activates an artifact, never publishes a betting
probability, and never enables execution.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Iterable

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from basketball_event_hydration_runtime import (
    BasketballHydrationError,
    BasketballHydrationPartialWriteError,
    hydrate,
)
from basketball_training_replay import run_training_replay
from github_actions_oidc import scout_route_auth_dependency
from v17.team_event_model_development_manifest import development_lane

CAN_EXECUTE = False
SUPPORTED_SPORTS = ("NBA", "WNBA")


def default_seasons(
    now: datetime | None = None, *, sport: str = "WNBA",
) -> tuple[int, ...]:
    """Choose league-specific ending-year releases for the validated clock."""
    clock = now or datetime.now(timezone.utc)
    if not isinstance(clock, datetime) or clock.utcoffset() is None:
        raise ValueError("BASKETBALL_SEASON_CLOCK_UNVERIFIED")
    current = clock.year
    # ESPN/NBA labels the 2026-27 season as 2027. The SportsDataverse
    # open-licensed asset is nba_schedule_2027.csv, whereas WNBA 2026 is
    # wnba_schedule_2026.csv. Previous maintenance omitted the current NBA
    # year for the entire Oct-Dec opening ramp.
    if sport.upper() == "NBA" and clock.month >= 10:
        current += 1
    return tuple(range(current - 3, current + 1))


def _blocked(
    sport: str,
    code: str,
    *,
    error_type: str | None = None,
    hydration_blocker: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a typed maintenance hold without granting model or execution authority."""
    lane = development_lane(sport)
    payload: dict[str, Any] = {
        "sport": sport,
        "status": "BLOCKED",
        "code": code,
        "model_development": lane.as_dict() if lane is not None else None,
        "promotion_attempted": False,
        "probability_publishable": False,
        "can_execute": False,
    }
    if error_type:
        payload["error_type"] = error_type
    if hydration_blocker:
        payload["hydration_blocker"] = hydration_blocker
    return payload


# Only source-owned failure codes may cross the authenticated API boundary.
# Exception messages can contain URLs, provider payloads or secrets; map known
# tokens to literal constants instead of returning any exception-derived text.
_PUBLIC_FAILURE_CODE_MAP = {
    code: code
    for code in (
        "BALLDONTLIE_API_KEY unavailable",
        "SUPABASE service credential unavailable",
        "BALLDONTLIE_RATE_LIMITED",
        "BALLDONTLIE_AUTH_FAILED",
        "BALLDONTLIE_INVALID_JSON",
        "BALLDONTLIE_INVALID_RESPONSE",
        "SPORTSDATAVERSE_ESPN_SOURCE_NOT_READY",
        "SPORTSDATAVERSE_ESPN_REQUEST_FAILED",
        "SPORTSDATAVERSE_ESPN_RATE_LIMITED",
        "SPORTSDATAVERSE_ESPN_SCHEMA_DRIFT",
        "ESPN_SCOREBOARD_REQUEST_FAILED",
        "ESPN_SCOREBOARD_INVALID_JSON",
        "ESPN_SCOREBOARD_INVALID_RESPONSE",
        "ESPN_RECENT_SPORT_UNSUPPORTED",
        "ESPN_RECENT_LOOKBACK_INVALID",
        "ESPN_RECENT_CLOCK_INVALID",
        "ESPN_RECENT_SOURCE_FETCH_FAILED",
        "ESPN_RECENT_SOURCE_INVALID_JSON",
        "ESPN_RECENT_SOURCE_INVALID_RESPONSE",
        "ESPN_RECENT_EVENT_IDENTITY_MISSING",
        "ESPN_RECENT_DUPLICATE_GAME_ID",
        "ESPN_RECENT_EVENT_TIME_INVALID",
        "ESPN_RECENT_SOURCE_EVENT_WRONG_DATE",
        "ESPN_RECENT_WINDOW_GAP_NOT_COVERED",
        "BASKETBALL_SETTLED_GAME_ID_INVALID",
        "BASKETBALL_EXISTING_GAME_IDS_UNVERIFIED",
        "BASKETBALL_HYDRATION_CLOCK_INVALID",
        "BALLDONTLIE_EMPTY_SEASON",
        "NBA_CANONICAL_SDV_SOURCE_UNAVAILABLE",
        "BASKETBALL_FRESH_SOURCE_UNAVAILABLE",
        "NBA_TEAM_PROVIDER_CROSSWALK_UNVERIFIED",
        "NBA_SOURCE_HTTP_503",
        "SOURCE_HTTP_503",
        "NBA_FAILURE",
        "NBA_TRAINING_CORPUS_EMPTY",
        "WNBA_TRAINING_CORPUS_EMPTY",
        "NBA_TRAINING_PROVENANCE_INCOMPLETE",
        "WNBA_TRAINING_PROVENANCE_INCOMPLETE",
        "NBA_TRAINING_CORPUS_FUTURE_DATED",
        "WNBA_TRAINING_CORPUS_FUTURE_DATED",
        "NBA_TRAINING_CORPUS_STALE",
        "WNBA_TRAINING_CORPUS_STALE",
        "NBA_FEATURE_REPLAY_DUPLICATE_GAME_IDS",
        "WNBA_FEATURE_REPLAY_DUPLICATE_GAME_IDS",
        *(f"{prefix}{status}" for prefix in (
            "BALLDONTLIE_HTTP_",
            "SPORTSDATAVERSE_ESPN_HTTP_",
            "ESPN_SCOREBOARD_HTTP_",
            "ESPN_RECENT_SOURCE_HTTP_",
            "NBA_SOURCE_HTTP_",
        ) for status in range(400, 600)),
    )
}


def _public_failure_code(exc: Exception, fallback: str) -> str:
    """Map known source errors to literals; never echo untrusted exception text."""
    if not isinstance(exc, (BasketballHydrationError, RuntimeError)):
        return fallback
    raw = str(exc).strip()[:256]
    token = raw.split(":", 1)[0].split(None, 1)[0]
    # Values come only from this module's literal allowlist. Unknown prefixes,
    # arbitrary RuntimeError messages and provider/DB details use the fallback.
    return _PUBLIC_FAILURE_CODE_MAP.get(
        raw, _PUBLIC_FAILURE_CODE_MAP.get(token, fallback)
    )


def _hydration_blocker(exc: Exception) -> dict[str, Any]:
    code = _public_failure_code(exc, "BASKETBALL_HYDRATION_FAILED")
    return {
        "status": "BLOCKED",
        "stage": "FRESH_SOURCE_HYDRATION",
        "code": code,
        "error_type": type(exc).__name__,
        "recoverable": True,
        "can_execute": False,
    }


def _fresh_acquisition_status(
    hydration: dict[str, Any] | None,
    hydration_blocker: dict[str, Any] | None,
) -> str:
    """Report actual new source evidence separately from successful shadow replay."""
    if hydration_blocker is not None:
        return "FRESH_ACQUISITION_BLOCKED_USING_PERSISTED_CORPUS"
    if not isinstance(hydration, dict):
        return "FRESH_ACQUISITION_NOT_VERIFIED"
    supplement = hydration.get("recent_supplement_status")
    if isinstance(supplement, dict) and any(
        str(code).startswith("SOURCE_BLOCKED:") for code in supplement.values()
    ):
        return "FRESH_ACQUISITION_PARTIAL_SOURCE_BLOCKED_USING_PERSISTED_CORPUS"
    if (
        isinstance(supplement, dict) and supplement
        and all(code == "NO_NEW_SETTLEMENT" for code in supplement.values())
    ):
        return "FRESH_ACQUISITION_NO_NEW_SETTLEMENTS_USING_PERSISTED_CORPUS"
    # Historic tests/callers may not yet expose this count; use the
    # established settled_rows field in that case, but never invent a count.
    new_rows = hydration.get("fresh_acquisition_new_settled_games", hydration.get("settled_rows"))
    if type(new_rows) is int and new_rows == 0:
        return "FRESH_ACQUISITION_NO_NEW_SETTLEMENTS_USING_PERSISTED_CORPUS"
    if type(new_rows) is int and new_rows > 0:
        return "FRESH_ACQUISITION_UPDATED"
    return "FRESH_ACQUISITION_NOT_VERIFIED"


def _nba_verified_current_season_date(db: Any, clock: datetime) -> str | None:
    """Read back the season-identified, canonical NBA settlement from storage.

    A recent game date alone never proves that the persisted row belongs to
    the current NBA ending-year corpus. This separate DB query is authoritative
    over source-return and caller-provided hydration metadata.
    """
    season_start_year = clock.year if clock.month >= 10 else clock.year - 1
    label = season_start_year + 1
    table = "wow_nba_training_games"
    fields = "game_date,season,game_id,source_provider"
    # Read the latest settled date from the entire persisted cohort, not
    # just the season-labelled subset. A recent row from a different season
    # cannot be hidden by a valid NBA 2027 row on the same day.
    top = (
        db.table(table).select(fields)
        .eq("settled", True).order("game_date", desc=True)
        .limit(1).execute()
    )
    top_rows = getattr(top, "data", None)
    if not isinstance(top_rows, list) or len(top_rows) > 1:
        raise BasketballHydrationError("NBA_CURRENT_SEASON_READBACK_INVALID")
    if not top_rows:
        return None
    top_row = top_rows[0]
    if not isinstance(top_row, dict) or not isinstance(top_row.get("game_date"), str):
        raise BasketballHydrationError("NBA_CURRENT_SEASON_READBACK_INVALID")
    try:
        latest_day = date.fromisoformat(top_row["game_date"])
    except ValueError as exc:
        raise BasketballHydrationError("NBA_CURRENT_SEASON_READBACK_INVALID") from exc
    if not date(season_start_year, 10, 1) <= latest_day <= clock.date():
        return None

    # A date can contain multiple settled games. Inspect *every* latest-day
    # identity; comparing a season-filtered date alone admits same-day
    # wrong-season records. 101 rows is far beyond an NBA day's slate; if
    # reached, fail closed rather than silently trust a truncated response.
    response = (
        db.table(table).select(fields)
        .eq("settled", True).eq("game_date", latest_day.isoformat())
        .limit(101).execute()
    )
    rows = getattr(response, "data", None)
    if not isinstance(rows, list) or not rows or len(rows) >= 101:
        raise BasketballHydrationError("NBA_CURRENT_SEASON_READBACK_INCOMPLETE")
    seen_ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise BasketballHydrationError("NBA_CURRENT_SEASON_READBACK_INVALID")
        event = row.get("game_id")
        if (
            type(row.get("season")) is not int
            or row["season"] != label
            or row.get("game_date") != latest_day.isoformat()
            or row.get("source_provider") != "SPORTSDATAVERSE_ESPN"
            or not isinstance(event, str)
            or not event.startswith("espn-")
            or not event[5:].isascii()
            or not event[5:].isdigit()
            or event in seen_ids
        ):
            raise BasketballHydrationError("NBA_CURRENT_SEASON_PERSISTED_PROVENANCE_INVALID")
        seen_ids.add(event)
    if top_row.get("game_id") not in seen_ids:
        raise BasketballHydrationError("NBA_CURRENT_SEASON_PERSISTED_IDENTITY_MISMATCH")

    # The latest day can be clean while earlier games in this season's
    # persisted training window have a wrong season/provider. Validate ALL
    # settled rows before replay. An exact PostgREST count makes a server-side
    # row cap observable: a short page must not silently end validation.
    scanned_ids: set[str] = set()
    season_open = date(season_start_year, 10, 1)
    page_size = 100
    max_pages = 20
    expected_count: int | None = None
    for page in range(max_pages + 1):
        result = (
            db.table(table).select(fields, count="exact")
            .eq("settled", True)
            .gte("game_date", season_open.isoformat())
            .lte("game_date", clock.date().isoformat())
            .order("game_date", desc=True).order("game_id", desc=True)
            .range(page * page_size, (page + 1) * page_size - 1)
            .execute()
        )
        batch = getattr(result, "data", None)
        total_count = getattr(result, "count", None)
        if (
            not isinstance(batch, list) or len(batch) > page_size
            or type(total_count) is not int
            or total_count < 0 or total_count > max_pages * page_size
            or (expected_count is not None and total_count != expected_count)
        ):
            raise BasketballHydrationError("NBA_CURRENT_SEASON_READBACK_INCOMPLETE")
        if expected_count is None:
            expected_count = total_count
        offset = page * page_size
        if offset + len(batch) > expected_count:
            raise BasketballHydrationError("NBA_CURRENT_SEASON_READBACK_INCOMPLETE")
        if page == max_pages:
            # This sentinel page must be empty because the maximum accepted
            # count is max_pages * page_size.
            if batch:
                raise BasketballHydrationError("NBA_CURRENT_SEASON_READBACK_INCOMPLETE")
            break
        for row in batch:
            if not isinstance(row, dict) or not isinstance(row.get("game_date"), str):
                raise BasketballHydrationError("NBA_CURRENT_SEASON_READBACK_INVALID")
            try:
                actual_date = date.fromisoformat(row["game_date"])
            except ValueError as exc:
                raise BasketballHydrationError("NBA_CURRENT_SEASON_READBACK_INVALID") from exc
            event = row.get("game_id")
            if (
                not season_open <= actual_date <= clock.date()
                or type(row.get("season")) is not int
                or row["season"] != label
                or row.get("source_provider") != "SPORTSDATAVERSE_ESPN"
                or not isinstance(event, str)
                or not event.startswith("espn-")
                or not event[5:].isascii()
                or not event[5:].isdigit()
                or event in scanned_ids
            ):
                raise BasketballHydrationError("NBA_CURRENT_SEASON_PERSISTED_PROVENANCE_INVALID")
            scanned_ids.add(event)
        if offset + len(batch) == expected_count:
            break
        if len(batch) != page_size:
            # PostgREST may cap rows below the requested range. Without this
            # check, an earlier wrong-provider settlement can escape review.
            raise BasketballHydrationError("NBA_CURRENT_SEASON_READBACK_INCOMPLETE")
    if not seen_ids.issubset(scanned_ids):
        raise BasketballHydrationError("NBA_CURRENT_SEASON_PERSISTED_IDENTITY_MISMATCH")
    return latest_day.isoformat()


def _nba_current_season_data_gap(
    hydration: dict[str, Any] | None, clock: datetime, *,
    require_verified_current_season: bool = True,
) -> dict[str, Any] | None:
    """Keep a stale historical NBA replay from masquerading as a live-season refresh.

    NBA official seasons begin in autumn and use the ending year for release
    assets. October may replay prior history only before a verified opening in
    the acquired canonical schedule. After opening, or with unknown opening
    evidence, current-season persistence is mandatory; Jul-Sep is offseason.
    An absent/invalid persisted readback is unverified, never a successful fit.
    This is a source-data guard, not a fitted-model probability threshold.
    """
    if clock.month in (7, 8, 9):
        return None
    latest_value = hydration.get("latest_game_date_after") if isinstance(hydration, dict) else None
    try:
        latest = date.fromisoformat(str(latest_value)[:10]) if latest_value else None
    except ValueError:
        latest = None
    season_start_year = clock.year if clock.month >= 10 else clock.year - 1
    if clock.month == 10 and isinstance(hydration, dict):
        label = season_start_year + 1
        starts = hydration.get("regular_season_start_by_season")
        sources = hydration.get("source_by_season")
        if isinstance(starts, dict) and isinstance(sources, dict):
            opening_value = starts.get(label, starts.get(str(label)))
            provider = sources.get(label, sources.get(str(label)))
            try:
                opening = datetime.fromisoformat(str(opening_value).replace("Z", "+00:00"))
                if (
                    provider == "SPORTSDATAVERSE_ESPN"
                    and opening.utcoffset() is not None
                    and opening.year == season_start_year and opening.month == 10
                    and clock < opening
                    and (latest_value is None or (latest is not None and latest <= clock.date()))
                ):
                    return None
            except (ValueError, TypeError, OverflowError):
                pass
    season_start = date(season_start_year, 10, 1)
    candidate_current = latest is not None and season_start <= latest <= clock.date()
    verified_value = (
        hydration.get("latest_current_season_game_date_after")
        if isinstance(hydration, dict) else None
    )
    try:
        verified = date.fromisoformat(verified_value) if isinstance(verified_value, str) else None
    except ValueError:
        verified = None
    if candidate_current and (
        not require_verified_current_season
        or (verified is not None and season_start <= verified <= clock.date()
            and verified == latest)
    ):
        return None
    return {
        "status": "BLOCKED",
        "stage": "FRESH_SOURCE_HYDRATION",
        "code": "NBA_CURRENT_SEASON_SETTLED_DATA_MISSING",
        "current_season_readback_code": (
            hydration.get("current_season_readback_code")
            if isinstance(hydration, dict) else None
        ) or ("NBA_CURRENT_SEASON_PERSISTED_PROOF_MISSING"
              if candidate_current and require_verified_current_season else None),
        "latest_verified_current_season_game_date": (
            verified.isoformat() if verified else None
        ),
        "required_season_start": season_start.isoformat(),
        "latest_persisted_game_date": latest.isoformat() if latest else None,
        "recovery_class": "SOURCE_COVERAGE_REPAIR",
        "required_season_label": season_start_year + 1,
        "next_action": (
            f"REACQUIRE_CANONICAL_{season_start_year + 1}_SDV_SEASON_"
            "AND_VERIFY_PERSISTED_SETTLEMENTS"
        ),
        "recoverable": True,
        "can_execute": False,
    }


def run_basketball_model_maintenance(
    db: Any,
    *,
    sports: Iterable[str] = SUPPORTED_SPORTS,
    seasons: Iterable[int] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Hydrate and replay governed current evidence while preserving native holds."""
    clock = now or datetime.now(timezone.utc)
    if not isinstance(clock, datetime) or clock.utcoffset() is None:
        raise ValueError("BASKETBALL_SEASON_CLOCK_UNVERIFIED")
    explicit_seasons = tuple(int(v) for v in seasons) if seasons is not None else None
    reported_base_seasons = explicit_seasons or default_seasons(now=clock)
    seasons_by_sport: dict[str, list[int]] = {}
    results: list[dict[str, Any]] = []

    for raw_sport in sports:
        sport = str(raw_sport or "").strip().upper()
        if sport not in SUPPORTED_SPORTS:
            results.append(_blocked(sport or "UNKNOWN", "BASKETBALL_SPORT_UNSUPPORTED"))
            continue

        season_values = explicit_seasons if explicit_seasons is not None else default_seasons(now=clock, sport=sport)
        seasons_by_sport[sport] = list(season_values)
        hydration: dict[str, Any] | None = None
        hydration_blocker: dict[str, Any] | None = None
        try:
            hydration = hydrate(sport, season_values, client=db, now=now)
        except BasketballHydrationPartialWriteError as exc:
            # A prior upsert may have committed; do not replay or fit this
            # incompletely refreshed corpus until it is reconciled.
            blocker = {**_hydration_blocker(exc), "recoverable": False,
                       "requires_corpus_reconciliation": True}
            results.append(_blocked(
                sport, "BASKETBALL_HYDRATION_PARTIAL_WRITE_REPLAY_FORBIDDEN",
                error_type=type(exc).__name__,
                hydration_blocker=blocker,
            ))
            continue
        except BasketballHydrationError as exc:
            hydration_blocker = _hydration_blocker(exc)
        except Exception as exc:  # noqa: BLE001 - preserve typed acquisition degradation
            hydration_blocker = _hydration_blocker(exc)

        # The scheduled/default NBA run is a current-season refresh, not a
        # historical backtest. The old June corpus was repeatedly replayed
        # with a green workflow during the November 2026 season. Fail closed
        # before another stale-corpus refit even when hydration itself succeeded.
        # Explicit season selection remains separate from current-season
        # refresh; explicit clocks are held below until replay reads honor them.
        if sport == "NBA" and explicit_seasons is None:
            # The live candidate must be proven by a separate season-filtered
            # readback; do not trust a hydration-supplied verification flag.
            # Explicit-clock replay is separately held before fitting.
            current_evidence: dict[str, Any] = {}
            if isinstance(hydration, dict):
                current_evidence = {
                    k: v for k, v in hydration.items()
                    if k not in {
                        "latest_current_season_game_date_after",
                        "current_season_readback_code",
                    }
                }
            raw_latest = current_evidence.get("latest_game_date_after")
            try:
                latest_date = date.fromisoformat(str(raw_latest)[:10]) if raw_latest else None
            except ValueError:
                latest_date = None
            start_year = clock.year if clock.month >= 10 else clock.year - 1
            if now is None and (
                hydration is None or (
                    latest_date is not None
                    and date(start_year, 10, 1) <= latest_date <= clock.date()
                )
            ):
                try:
                    verified = _nba_verified_current_season_date(db, clock)
                    current_evidence["latest_current_season_game_date_after"] = verified
                    if hydration is None:
                        # This is independent persisted evidence, never a
                        # successful fresh acquisition. Keep hydration=None and
                        # its native provider blocker in the replay response.
                        current_evidence["latest_game_date_after"] = verified
                except BasketballHydrationError as exc:
                    current_evidence["current_season_readback_code"] = _public_failure_code(
                        exc, "NBA_CURRENT_SEASON_PERSISTED_READBACK_FAILED"
                    )
                except Exception:  # noqa BLE001 - no unsafe replay after DB failure
                    current_evidence["current_season_readback_code"] = (
                        "NBA_CURRENT_SEASON_PERSISTED_READBACK_FAILED"
                    )
            if isinstance(hydration, dict):
                hydration = current_evidence
            gap = _nba_current_season_data_gap(
                current_evidence, clock, require_verified_current_season=now is None,
            )
            if gap is not None:
                # Preserve the original acquisition failure underneath the
                # current-season data gap; callers must not mistake HTTP 503
                # for a confirmed absence of settled training history.
                if hydration_blocker is not None:
                    gap = {**gap, "upstream_hydration_blocker": hydration_blocker}
                results.append(_blocked(
                    sport, gap["code"],
                    hydration_blocker=gap,
                ))
                continue

        # Hydration alone honors an explicit replay clock. The persisted
        # provenance/freshness/feature/fit readers currently load the whole
        # corpus, including evidence acquired after that clock. Do not create
        # historical SHADOW evidence until every replay read honors the cutoff.
        if now is not None:
            held = _blocked(
                sport, "BASKETBALL_HISTORICAL_REPLAY_CUTOFF_UNSUPPORTED",
                hydration_blocker=hydration_blocker,
            )
            held.update({
                "hydration": hydration,
                "replay_cutoff": clock.isoformat(),
                "replay_attempted": False,
            })
            results.append(held)
            continue

        # A blocked fresh-source fetch is not proof that the persisted corpus is
        # unusable. Replay performs its own provenance + freshness preflights and
        # therefore remains the authority on whether existing history is safe to
        # use for candidate/shadow development.
        try:
            replay = run_training_replay(sport, client=db)
            lane = development_lane(sport)
            row: dict[str, Any] = {
                "sport": sport,
                "status": "SHADOW_EVIDENCE_UPDATED",
                "hydration": hydration,
                "hydration_status": _fresh_acquisition_status(hydration, hydration_blocker),
                "hydration_blocker": hydration_blocker,
                "training_replay": replay,
                "model_development": lane.as_dict() if lane is not None else None,
                "promotion_attempted": False,
                "probability_publishable": False,
                "can_execute": False,
            }
            results.append(row)
        except RuntimeError as exc:
            results.append(
                _blocked(
                    sport,
                    _public_failure_code(exc, "BASKETBALL_TRAINING_REPLAY_BLOCKED"),
                    error_type=type(exc).__name__,
                    hydration_blocker=hydration_blocker,
                )
            )
        except Exception as exc:  # noqa: BLE001 - typed fail-closed maintenance report
            results.append(
                _blocked(
                    sport,
                    "BASKETBALL_MODEL_MAINTENANCE_FAILED",
                    error_type=type(exc).__name__,
                    hydration_blocker=hydration_blocker,
                )
            )

    updated = sum(row["status"] == "SHADOW_EVIDENCE_UPDATED" for row in results)
    degraded = sum(
        row.get("hydration_status") in {
            "FRESH_ACQUISITION_BLOCKED_USING_PERSISTED_CORPUS",
            "FRESH_ACQUISITION_PARTIAL_SOURCE_BLOCKED_USING_PERSISTED_CORPUS",
        }
        for row in results
    )
    no_new = sum(
        row.get("hydration_status") == "FRESH_ACQUISITION_NO_NEW_SETTLEMENTS_USING_PERSISTED_CORPUS"
        for row in results
    )
    unverified = sum(
        row.get("hydration_status") == "FRESH_ACQUISITION_NOT_VERIFIED"
        for row in results
    )
    return {
        "status": "COMPLETE" if updated == len(results) else ("PARTIAL" if updated else "BLOCKED"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seasons": list(reported_base_seasons),
        "seasons_by_sport": seasons_by_sport,
        "rows": results,
        "rows_updated": updated,
        "rows_blocked": len(results) - updated,
        "rows_fresh_acquisition_degraded": degraded,
        "rows_no_new_settlements": no_new,
        "rows_fresh_acquisition_unverified": unverified,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def install_basketball_model_maintenance_route(
    app: FastAPI,
    *,
    auth_dependency: Any,
    db_client_fn: Any,
) -> None:
    path = "/internal/v17/basketball-model-maintenance"
    if any(getattr(route, "path", None) == path for route in app.router.routes):
        return

    @app.post(
        path,
        dependencies=[scout_route_auth_dependency(auth_dependency)],
        operation_id="runWowV17BasketballModelMaintenance",
    )
    def run_maintenance() -> Any:
        """Run authenticated maintenance using the configured database client."""
        try:
            return run_basketball_model_maintenance(db_client_fn())
        except Exception:  # noqa: BLE001 - sanitize the authenticated HTTP boundary
            # Never expose database, provider, or stack-trace detail to the
            # caller. A fixed typed hold keeps the route fail-closed.
            return JSONResponse(
                status_code=500,
                content={
                    "status": "BLOCKED",
                    "stage": "BASKETBALL_MODEL_MAINTENANCE",
                    "code": "BASKETBALL_MODEL_MAINTENANCE_FAILED",
                    "automatic_certification": False,
                    "automatic_promotion": False,
                    "probability_publishable": False,
                    "can_execute": False,
                },
            )


__all__ = [
    "CAN_EXECUTE",
    "SUPPORTED_SPORTS",
    "default_seasons",
    "install_basketball_model_maintenance_route",
    "run_basketball_model_maintenance",
]
