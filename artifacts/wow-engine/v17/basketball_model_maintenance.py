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


def _hydration_blocker(exc: Exception) -> dict[str, Any]:
    code = str(exc) or "BASKETBALL_HYDRATION_FAILED"
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


def _nba_current_season_data_gap(hydration: dict[str, Any] | None, clock: datetime) -> dict[str, Any] | None:
    """Keep a stale historical NBA replay from masquerading as a live-season refresh.

    NBA official seasons begin in autumn and use the ending year for release
    assets. In Nov-Jun at least one settled game from the *current* season
    must be persisted; Oct is not gated before tipoff, and Jul-Sep is offseason.
    An absent/invalid persisted readback is unverified, never a successful fit.
    This is a source-data guard, not a fitted-model probability threshold.
    """
    if clock.month in (7, 8, 9, 10):
        return None
    season_start_year = clock.year if clock.month >= 11 else clock.year - 1
    season_start = date(season_start_year, 10, 1)
    latest_value = hydration.get("latest_game_date_after") if isinstance(hydration, dict) else None
    try:
        latest = date.fromisoformat(str(latest_value)[:10]) if latest_value else None
    except ValueError:
        latest = None
    if latest is not None and latest >= season_start and latest <= clock.date():
        return None
    return {
        "status": "BLOCKED",
        "stage": "FRESH_SOURCE_HYDRATION",
        "code": "NBA_CURRENT_SEASON_SETTLED_DATA_MISSING",
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
        # Explicit historical seasons remain a separate research-only route.
        if sport == "NBA" and explicit_seasons is None:
            gap = _nba_current_season_data_gap(hydration, clock)
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
                    str(exc) or "BASKETBALL_TRAINING_REPLAY_BLOCKED",
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
    def run_maintenance() -> dict[str, Any]:
        return run_basketball_model_maintenance(db_client_fn())


__all__ = [
    "CAN_EXECUTE",
    "SUPPORTED_SPORTS",
    "default_seasons",
    "install_basketball_model_maintenance_route",
    "run_basketball_model_maintenance",
]
