"""G1 corpus certification readiness for NHL Design A Fix A.

Audits training-scope FULL_SEASON provenance (G1a) and current-season
hydration (G1b) without minting certification, promotion, publication, or
execution. Live rebuilds remain rights-gated; dry-run acquisition stays
available when rights are held.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from fastapi import FastAPI

from github_actions_oidc import scout_route_auth_dependency
from v17.nhl_provenance import SOURCE_PROVENANCE_VERSION
from v17.nhl_source_reingest import (
    CAN_EXECUTE,
    PROBABILITY_PUBLISHABLE,
    default_season_id,
    nhl_source_rights_approved,
    run_nhl_source_reingest,
)
from v17.nhl_fitted_specialist import (
    NHLFittedSpecialistError,
    load_nhl_season_events,
    load_nhl_season_timeline_receipt,
)

# Chronological start years that feed the frozen 3676/1226/1226 research corpus.
CERTIFICATION_TRAINING_START_YEARS: tuple[int, ...] = (2021, 2022, 2023, 2024, 2025)
REQUIRED_TRAINING_ROWS = 3676
REQUIRED_CALIBRATION_ROWS = 1226
REQUIRED_TEST_ROWS = 1226
REQUIRED_CORPUS_ROWS = (
    REQUIRED_TRAINING_ROWS + REQUIRED_CALIBRATION_ROWS + REQUIRED_TEST_ROWS
)


def _season_id_for_start_year(start_year: int) -> int:
    year = int(start_year)
    return int(f"{year}{year + 1}")


def certification_training_season_ids(
    start_years: Sequence[int] | None = None,
) -> tuple[int, ...]:
    years = (
        tuple(int(item) for item in start_years)
        if start_years is not None
        else CERTIFICATION_TRAINING_START_YEARS
    )
    return tuple(_season_id_for_start_year(year) for year in years)


def _blocked(code: str, **extra: Any) -> dict[str, Any]:
    payload = {
        "status": "BLOCKED",
        "code": code,
        "g1a_ready": False,
        "g1b_ready": False,
        "corpus_rebuild_ready": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": PROBABILITY_PUBLISHABLE,
        "can_execute": CAN_EXECUTE,
    }
    payload.update(extra)
    return payload


def _season_provenance_stats(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    distinct: set[str] = set()
    provenanced: set[str] = set()
    for row in rows:
        event_id = str(row.get("official_event_id") or "").strip()
        if not event_id:
            continue
        distinct.add(event_id)
        outcome = row.get("outcome_json")
        if not isinstance(outcome, Mapping):
            continue
        if (
            outcome.get("provenance_version") == SOURCE_PROVENANCE_VERSION
            and type(outcome.get("game_type")) is int
            and outcome.get("game_type") == 2
            and type(outcome.get("neutral_site")) is bool
            and str(outcome.get("game_state") or "").upper() in {"FINAL", "OFF"}
        ):
            provenanced.add(event_id)
    return {
        "distinct_event_n": len(distinct),
        "provenanced_event_n": len(provenanced),
        "provenance_complete": bool(distinct)
        and distinct == provenanced
        and len(provenanced) > 0,
    }


def assess_season_g1a(db: Any, *, season: int | str) -> dict[str, Any]:
    """Assess one training season for V2 provenance + FULL_SEASON receipt."""
    season_token = str(season).strip()
    blockers: list[str] = []
    try:
        rows = load_nhl_season_events(db, season=int(season_token))
    except NHLFittedSpecialistError as exc:
        return {
            "season": season_token,
            "status": "BLOCKED",
            "blockers": list(exc.blockers) or ["NHL_SOURCE_SEASON_UNREADABLE"],
            "provenance_complete": False,
            "timeline_receipt_present": False,
            "g1a_ready": False,
            "can_execute": CAN_EXECUTE,
        }
    except Exception:  # noqa: BLE001 - readiness must fail closed
        return {
            "season": season_token,
            "status": "BLOCKED",
            "blockers": ["NHL_SOURCE_SEASON_UNREADABLE"],
            "provenance_complete": False,
            "timeline_receipt_present": False,
            "g1a_ready": False,
            "can_execute": CAN_EXECUTE,
        }

    stats = _season_provenance_stats(rows)
    if stats["distinct_event_n"] <= 0:
        blockers.append("NHL_SOURCE_SEASON_EMPTY")
    if not stats["provenance_complete"]:
        blockers.append("NHL_SOURCE_PROVENANCE_INCOMPLETE")

    receipt = None
    try:
        receipt = load_nhl_season_timeline_receipt(db, season=season_token)
    except NHLFittedSpecialistError as exc:
        blockers.extend(list(exc.blockers) or ["NHL_SOURCE_TIMELINE_RECEIPT_INVALID"])
        receipt = None
    except Exception:  # noqa: BLE001
        blockers.append("NHL_SOURCE_TIMELINE_RECEIPT_UNREADABLE")
        receipt = None

    timeline_ok = False
    if isinstance(receipt, Mapping):
        timeline_ok = (
            receipt.get("provenance_complete") is True
            and str(receipt.get("season_scope") or "") == "FULL_SEASON"
            and str(receipt.get("season") or "") == season_token
        )
        if not timeline_ok:
            blockers.append("NHL_SOURCE_TIMELINE_RECEIPT_INVALID")
    else:
        blockers.append("NHL_SOURCE_TIMELINE_RECEIPT_MISSING")

    ready = not blockers
    return {
        "season": season_token,
        "status": "G1A_READY" if ready else "BLOCKED",
        "blockers": blockers,
        "distinct_event_n": stats["distinct_event_n"],
        "provenanced_event_n": stats["provenanced_event_n"],
        "provenance_complete": stats["provenance_complete"],
        "timeline_receipt_present": timeline_ok,
        "g1a_ready": ready,
        "can_execute": CAN_EXECUTE,
        "probability_publishable": PROBABILITY_PUBLISHABLE,
    }


def assess_season_g1b(db: Any, *, season: int | str) -> dict[str, Any]:
    """Assess current-season hydration independently of training receipts."""
    season_token = str(season).strip()
    blockers: list[str] = []
    try:
        rows = load_nhl_season_events(db, season=int(season_token))
    except NHLFittedSpecialistError as exc:
        return {
            "season": season_token,
            "status": "BLOCKED",
            "blockers": list(exc.blockers) or ["NHL_SOURCE_SEASON_UNREADABLE"],
            "hydration_present": False,
            "g1b_ready": False,
            "can_execute": CAN_EXECUTE,
        }
    except Exception:  # noqa: BLE001
        return {
            "season": season_token,
            "status": "BLOCKED",
            "blockers": ["NHL_SOURCE_SEASON_UNREADABLE"],
            "hydration_present": False,
            "g1b_ready": False,
            "can_execute": CAN_EXECUTE,
        }

    stats = _season_provenance_stats(rows)
    if stats["distinct_event_n"] <= 0:
        blockers.append("NHL_CURRENT_SEASON_EVENTS_MISSING")
    if not stats["provenance_complete"]:
        blockers.append("NHL_CURRENT_SEASON_PROVENANCE_INCOMPLETE")

    receipt = None
    try:
        receipt = load_nhl_season_timeline_receipt(db, season=season_token)
    except NHLFittedSpecialistError:
        receipt = None
    except Exception:  # noqa: BLE001
        receipt = None
        blockers.append("NHL_SOURCE_TIMELINE_RECEIPT_UNREADABLE")

    timeline_ok = (
        isinstance(receipt, Mapping)
        and receipt.get("provenance_complete") is True
        and str(receipt.get("season_scope") or "") == "FULL_SEASON"
        and str(receipt.get("season") or "") == season_token
    )
    if not timeline_ok:
        blockers.append("NHL_CURRENT_SEASON_TIMELINE_RECEIPT_MISSING")

    ready = not blockers
    return {
        "season": season_token,
        "status": "G1B_READY" if ready else "BLOCKED",
        "blockers": blockers,
        "distinct_event_n": stats["distinct_event_n"],
        "provenanced_event_n": stats["provenanced_event_n"],
        "hydration_present": stats["distinct_event_n"] > 0,
        "provenance_complete": stats["provenance_complete"],
        "timeline_receipt_present": timeline_ok,
        "g1b_ready": ready,
        "can_execute": CAN_EXECUTE,
        "probability_publishable": PROBABILITY_PUBLISHABLE,
    }


def assess_nhl_corpus_certification_readiness(
    db: Any,
    *,
    training_start_years: Sequence[int] | None = None,
    current_season: int | str | None = None,
    rights_approved: bool | None = None,
) -> dict[str, Any]:
    """Fail-closed G1 readiness report for Fix A; never certifies or publishes."""
    training_seasons = certification_training_season_ids(training_start_years)
    current = (
        int(current_season)
        if current_season is not None
        else default_season_id()
    )
    if current in training_seasons:
        return _blocked(
            "NHL_CORPUS_CURRENT_SEASON_COLLIDES_WITH_TRAINING",
            detail={
                "current_season": str(current),
                "training_seasons": [str(item) for item in training_seasons],
            },
        )

    g1a_reports = [
        assess_season_g1a(db, season=season) for season in training_seasons
    ]
    g1b_report = assess_season_g1b(db, season=current)
    g1a_ready = all(item.get("g1a_ready") is True for item in g1a_reports)
    g1b_ready = g1b_report.get("g1b_ready") is True
    rights_ok = nhl_source_rights_approved(rights_approved)

    blockers: list[str] = []
    for report in g1a_reports:
        blockers.extend(str(code) for code in (report.get("blockers") or []))
    blockers.extend(str(code) for code in (g1b_report.get("blockers") or []))
    if not rights_ok:
        blockers.append("NHL_SOURCE_RIGHTS_HOLD")

    # Deduplicate while preserving order.
    seen: set[str] = set()
    ordered_blockers: list[str] = []
    for code in blockers:
        if code in seen:
            continue
        seen.add(code)
        ordered_blockers.append(code)

    ready = g1a_ready and g1b_ready and rights_ok and not ordered_blockers
    return {
        "status": "G1_CORPUS_READY" if ready else "BLOCKED",
        "blockers": ordered_blockers,
        "training_seasons": [str(item) for item in training_seasons],
        "current_season": str(current),
        "required_corpus_rows": REQUIRED_CORPUS_ROWS,
        "required_split": {
            "training_rows": REQUIRED_TRAINING_ROWS,
            "calibration_rows": REQUIRED_CALIBRATION_ROWS,
            "test_rows": REQUIRED_TEST_ROWS,
        },
        "g1a": g1a_reports,
        "g1b": g1b_report,
        "g1a_ready": g1a_ready,
        "g1b_ready": g1b_ready,
        "source_rights_approved": rights_ok,
        "corpus_rebuild_ready": ready,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": PROBABILITY_PUBLISHABLE,
        "can_execute": CAN_EXECUTE,
        "disposition": "WORKING_NOT_COMPLETE",
    }


def run_nhl_g1_corpus_rebuild(
    db: Any,
    *,
    training_start_years: Sequence[int] | None = None,
    current_season: int | str | None = None,
    dry_run: bool = True,
    rights_approved: bool | None = None,
    ingest_code_sha: str | None = None,
    session: Any = None,
) -> dict[str, Any]:
    """Rebuild G1a training seasons then G1b current season, rights-fail-closed.

    Default dry_run=True so engineering validation never writes without an
    explicit live rebuild request plus source-rights approval.
    """
    training_seasons = certification_training_season_ids(training_start_years)
    current = (
        int(current_season)
        if current_season is not None
        else default_season_id()
    )
    seasons: list[int] = list(training_seasons) + [current]
    if len(set(seasons)) != len(seasons):
        return _blocked(
            "NHL_CORPUS_CURRENT_SEASON_COLLIDES_WITH_TRAINING",
            detail={
                "current_season": str(current),
                "training_seasons": [str(item) for item in training_seasons],
            },
        )

    if not dry_run and not nhl_source_rights_approved(rights_approved):
        return _blocked(
            "NHL_SOURCE_RIGHTS_HOLD",
            detail={
                "reason": "NHL_SOURCE_RIGHTS_NOT_APPROVED",
                "dry_run_allowed": True,
                "seasons": [str(item) for item in seasons],
            },
        )

    season_results: list[dict[str, Any]] = []
    for season in seasons:
        result = run_nhl_source_reingest(
            db,
            season=season,
            dry_run=dry_run,
            rights_approved=rights_approved,
            ingest_code_sha=ingest_code_sha,
            session=session,
        )
        season_results.append(dict(result, season=str(result.get("season") or season)))

    ok_statuses = {
        "NHL_SOURCE_REINGEST_DRY_RUN_READY",
        "NHL_SOURCE_REINGEST_PERSISTED",
    }
    failed = [
        item
        for item in season_results
        if str(item.get("status") or "") not in ok_statuses
        or item.get("can_execute") is not False
        or item.get("probability_publishable") is not False
    ]
    readiness = assess_nhl_corpus_certification_readiness(
        db,
        training_start_years=training_start_years,
        current_season=current,
        rights_approved=rights_approved,
    )
    return {
        "status": (
            "NHL_G1_CORPUS_REBUILD_DRY_RUN_READY"
            if dry_run and not failed
            else "NHL_G1_CORPUS_REBUILD_PERSISTED"
            if (not dry_run and not failed and readiness.get("corpus_rebuild_ready") is True)
            else "BLOCKED"
        ),
        "dry_run": bool(dry_run),
        "seasons": [str(item) for item in seasons],
        "season_results": season_results,
        "failed_season_n": len(failed),
        "readiness": readiness,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": PROBABILITY_PUBLISHABLE,
        "can_execute": CAN_EXECUTE,
        "disposition": "WORKING_NOT_COMPLETE",
    }


def install_nhl_corpus_certification_readiness_routes(
    app: FastAPI,
    *,
    auth_dependency: Any,
    db_client_fn: Any,
) -> None:
    """Install read-only readiness and rights-gated G1 rebuild routes."""
    readiness_path = "/internal/v17/nhl-corpus-certification-readiness"
    rebuild_path = "/internal/v17/nhl-g1-corpus-rebuild"
    existing = {getattr(route, "path", None) for route in app.router.routes}

    if readiness_path not in existing:

        @app.get(
            readiness_path,
            dependencies=[scout_route_auth_dependency(auth_dependency)],
            operation_id="getWowV17NhlCorpusCertificationReadiness",
        )
        def get_readiness() -> dict[str, Any]:
            return assess_nhl_corpus_certification_readiness(db_client_fn())

    if rebuild_path not in existing:

        @app.post(
            rebuild_path,
            dependencies=[scout_route_auth_dependency(auth_dependency)],
            operation_id="runWowV17NhlG1CorpusRebuild",
        )
        def run_rebuild(dry_run: bool = True) -> dict[str, Any]:
            # Live writes still require WOW_NHL_SOURCE_RIGHTS_APPROVED in-process.
            return run_nhl_g1_corpus_rebuild(
                db_client_fn(),
                dry_run=dry_run,
            )


__all__ = [
    "CAN_EXECUTE",
    "CERTIFICATION_TRAINING_START_YEARS",
    "PROBABILITY_PUBLISHABLE",
    "REQUIRED_CALIBRATION_ROWS",
    "REQUIRED_CORPUS_ROWS",
    "REQUIRED_TEST_ROWS",
    "REQUIRED_TRAINING_ROWS",
    "assess_nhl_corpus_certification_readiness",
    "assess_season_g1a",
    "assess_season_g1b",
    "certification_training_season_ids",
    "install_nhl_corpus_certification_readiness_routes",
    "run_nhl_g1_corpus_rebuild",
]
