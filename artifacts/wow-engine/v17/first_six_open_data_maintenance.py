"""Authenticated candidate-maintenance routes for first-six open-data lanes."""
from __future__ import annotations

from concurrent.futures import Future
import ctypes
from datetime import datetime, timezone
import gc
import os
import threading
from typing import Any, Callable

from fastapi import FastAPI, HTTPException

from github_actions_oidc import scout_route_auth_dependency
from v17.ncaab_sportsdataverse_candidate import NCAABCandidateUnavailable, train_and_persist as train_ncaab
from v17.soccer_openfootball_candidate import SoccerCandidateUnavailable, train_all as train_soccer
from v17.tennis_valuebet_candidate import TennisCandidateUnavailable, train_all as train_tennis
from v17.team_state_challenger_maintenance import run_all_team_state_challengers
from v17.team_state_scoped_maintenance import run_team_state_scope

CAN_EXECUTE = False

_TEAM_STATE_SOURCE_HEAVY_SPORTS = {"MLB", "NCAAB", "SOCCER"}
_TEAM_STATE_RUNNER_ONLY_SCOPES = {
    "MLB",
    "NCAAB",
    "SOCCER_EPL",
    "SOCCER_BUNDESLIGA",
    "SOCCER_LALIGA",
    "SOCCER_SERIE_A",
    "SOCCER_LIGUE_1",
}
_TEAM_STATE_PERSIST_CONTRACTS = {
    "wow_d1_training_rows": {
        "on_conflict": "sport,official_event_id,feature_schema_version,source_manifest_sha256",
        "max_rows": 250,
    },
    "wow_d1_candidate_artifacts": {
        "on_conflict": "model_artifact_version",
        "max_rows": 5,
    },
}

# Scoped team-state maintenance is deliberately single-flight inside the
# production Uvicorn process. GitHub callers may disconnect at their own timeout
# while the synchronous acquisition/fitting work continues server-side. A retry
# must join that in-flight computation rather than allocate a second copy of the
# training corpus. Different lightweight scopes are serialized as well so model
# maintenance cannot multiply memory pressure against interactive scoring.
_TEAM_STATE_INFLIGHT_LOCK = threading.Lock()
_TEAM_STATE_EXECUTION_LOCK = threading.Lock()
_TEAM_STATE_INFLIGHT: dict[tuple[str, str], Future[dict[str, Any]]] = {}


def _sha() -> str:
    return str(os.getenv("RENDER_GIT_COMMIT") or os.getenv("GIT_COMMIT_SHA") or "").strip().lower()


def _run(name: str, fn: Callable[..., dict[str, Any]], db: Any) -> dict[str, Any]:
    sha = _sha()
    if len(sha) < 7:
        return {"status":"BLOCKED","code":f"{name}_TRAINING_CODE_SHA_UNAVAILABLE",
                "automatic_certification":False,"automatic_promotion":False,"probability_publishable":False,"can_execute":False}
    try:
        result = fn(db, training_code_sha=sha)
    except (NCAABCandidateUnavailable, SoccerCandidateUnavailable, TennisCandidateUnavailable) as exc:
        return {"status":"BLOCKED","code":exc.code,"detail":str(exc),"automatic_certification":False,
                "automatic_promotion":False,"probability_publishable":False,"can_execute":False}
    except Exception as exc:
        return {"status":"BLOCKED","code":f"{name}_CANDIDATE_MAINTENANCE_FAILED","detail":{"error_type":type(exc).__name__},
                "automatic_certification":False,"automatic_promotion":False,"probability_publishable":False,"can_execute":False}
    return {"status":"CANDIDATE_EVIDENCE_UPDATED","generated_at":datetime.now(timezone.utc).isoformat(),**result,
            "automatic_certification":False,"automatic_promotion":False,"probability_publishable":False,"can_execute":False}


def _run_team_state(db: Any) -> dict[str, Any]:
    sha = _sha()
    if len(sha) < 7:
        return {"status":"BLOCKED","code":"TEAM_STATE_TRAINING_CODE_SHA_UNAVAILABLE","automatic_certification":False,
                "automatic_promotion":False,"probability_publishable":False,"can_execute":False}
    try:
        result = run_all_team_state_challengers(db, training_code_sha=sha)
    except Exception as exc:
        return {"status":"BLOCKED","code":"TEAM_STATE_CHALLENGER_MAINTENANCE_FAILED","detail":{"error_type":type(exc).__name__},
                "automatic_certification":False,"automatic_promotion":False,"probability_publishable":False,"can_execute":False}
    return {**result,"automatic_certification":False,"automatic_promotion":False,"probability_publishable":False,"can_execute":False}


def _run_team_state_scope(db: Any, scope: str) -> dict[str, Any]:
    sha = _sha()
    if len(sha) < 7:
        return {"status":"BLOCKED","program":"LLP_DYNAMIC_TEAM_STATE_CHALLENGER_V1","scope":str(scope or "").upper(),
                "code":"TEAM_STATE_TRAINING_CODE_SHA_UNAVAILABLE","rows":[],"candidate_rows_updated":0,
                "candidate_rows_blocked":1,"automatic_certification":False,"automatic_promotion":False,
                "probability_publishable":False,"can_execute":False}
    try:
        result = run_team_state_scope(db, scope=scope, training_code_sha=sha)
    except Exception as exc:
        return {"status":"BLOCKED","program":"LLP_DYNAMIC_TEAM_STATE_CHALLENGER_V1","scope":str(scope or "").upper(),
                "code":"TEAM_STATE_SCOPED_MAINTENANCE_FAILED","detail":{"error_type":type(exc).__name__},"rows":[],
                "candidate_rows_updated":0,"candidate_rows_blocked":1,"automatic_certification":False,
                "automatic_promotion":False,"probability_publishable":False,"can_execute":False}
    return {**result,"automatic_certification":False,"automatic_promotion":False,"probability_publishable":False,"can_execute":False}


def _runner_only_team_state_block(scope: str) -> dict[str, Any]:
    normalized = str(scope or "").strip().upper()
    code = "TEAM_STATE_SOURCE_HEAVY_RUNNER_REQUIRED"
    return {
        "status": "BLOCKED",
        "program": "LLP_DYNAMIC_TEAM_STATE_CHALLENGER_V1",
        "scope": normalized,
        "code": code,
        "rows": [{
            "sport": normalized,
            "status": "BLOCKED",
            "code": code,
            "runner_isolation_required": True,
            "automatic_certification": False,
            "automatic_promotion": False,
            "probability_publishable": False,
            "can_execute": False,
        }],
        "candidate_rows_updated": 0,
        "candidate_rows_blocked": 1,
        "runner_isolation_required": True,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def _release_process_memory() -> None:
    """Best-effort reclamation after in-process candidate maintenance.

    Python GC releases unreachable training objects. On glibc hosts malloc_trim
    additionally returns free arenas to the OS so Render's RSS budget reflects
    the completed workload. Failure to trim is intentionally non-fatal.
    """
    gc.collect()
    try:
        malloc_trim = getattr(ctypes.CDLL(None), "malloc_trim", None)
        if malloc_trim is not None:
            malloc_trim.argtypes = [ctypes.c_size_t]
            malloc_trim.restype = ctypes.c_int
            malloc_trim(0)
    except Exception:
        pass


def _run_team_state_scope_singleflight(db_client_fn: Callable[[], Any], scope: str) -> dict[str, Any]:
    normalized = str(scope or "").strip().upper()
    if normalized in _TEAM_STATE_RUNNER_ONLY_SCOPES:
        return _runner_only_team_state_block(normalized)

    sha = _sha()
    key = (sha, normalized)
    with _TEAM_STATE_INFLIGHT_LOCK:
        future = _TEAM_STATE_INFLIGHT.get(key)
        owner = future is None
        if owner:
            future = Future()
            _TEAM_STATE_INFLIGHT[key] = future

    assert future is not None
    if not owner:
        return future.result()

    try:
        with _TEAM_STATE_EXECUTION_LOCK:
            result = _run_team_state_scope(db_client_fn(), normalized)
            _release_process_memory()
        future.set_result(result)
        return result
    except BaseException as exc:
        future.set_exception(exc)
        raise
    finally:
        with _TEAM_STATE_INFLIGHT_LOCK:
            if _TEAM_STATE_INFLIGHT.get(key) is future:
                del _TEAM_STATE_INFLIGHT[key]


def _persist_team_state_batch(db: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Persist only governed team-state training/artifact upsert batches.

    Source-heavy public acquisition and fitting may run on a protected GitHub
    runner, but database authority stays on the backend.  This endpoint is not a
    generic table writer: it permits only the two team-state evidence tables,
    exact conflict contracts, bounded batches, and fail-closed candidate flags.
    """
    table = str(payload.get("table") or "").strip()
    contract = _TEAM_STATE_PERSIST_CONTRACTS.get(table)
    if contract is None:
        raise HTTPException(status_code=400, detail={"code":"TEAM_STATE_PERSIST_TABLE_NOT_ALLOWED","can_execute":False})

    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows or len(rows) > int(contract["max_rows"]):
        raise HTTPException(status_code=400, detail={"code":"TEAM_STATE_PERSIST_BATCH_INVALID","can_execute":False})
    if not all(isinstance(row, dict) for row in rows):
        raise HTTPException(status_code=400, detail={"code":"TEAM_STATE_PERSIST_ROW_INVALID","can_execute":False})

    on_conflict = str(payload.get("on_conflict") or "")
    if on_conflict != contract["on_conflict"] or payload.get("ignore_duplicates") is not True:
        raise HTTPException(status_code=400, detail={"code":"TEAM_STATE_PERSIST_UPSERT_CONTRACT_INVALID","can_execute":False})

    for row in rows:
        if row.get("can_execute") is not False:
            raise HTTPException(status_code=400, detail={"code":"TEAM_STATE_PERSIST_CAN_EXECUTE_MUST_BE_FALSE","can_execute":False})

        sport = str(row.get("sport") or "").strip().upper()
        model_family = str(row.get("model_family") or "").strip().upper()
        feature_schema = str(row.get("feature_schema_version") or "").strip().upper()
        if sport not in _TEAM_STATE_SOURCE_HEAVY_SPORTS:
            raise HTTPException(status_code=400, detail={"code":"TEAM_STATE_PERSIST_SPORT_NOT_ALLOWED","can_execute":False})
        if "DYNAMIC_TEAM_STATE" not in model_family or "DYNAMIC_TEAM_STATE" not in feature_schema:
            raise HTTPException(status_code=400, detail={"code":"TEAM_STATE_PERSIST_MODEL_FAMILY_INVALID","can_execute":False})

        if table == "wow_d1_training_rows":
            source_manifest = row.get("source_manifest")
            if (
                row.get("market_features_used") is not False
                or row.get("historical_reconstruction") is not True
                or not isinstance(source_manifest, dict)
                or source_manifest.get("program") != "LLP_DYNAMIC_TEAM_STATE_CHALLENGER_V1"
            ):
                raise HTTPException(status_code=400, detail={"code":"TEAM_STATE_TRAINING_ROW_GOVERNANCE_INVALID","can_execute":False})
        else:
            required_false = (
                "promoted",
                "active",
                "automatic_certification",
                "automatic_promotion",
                "probability_publishable",
            )
            if (
                row.get("lifecycle_state") != "CANDIDATE"
                or row.get("market_family") != "OUTRIGHT_WINNER"
                or row.get("source_policy_id") != "TEAM_STATE_DYNAMIC_PRIOR_ONLY_V1"
                or any(row.get(field) is not False for field in required_false)
            ):
                raise HTTPException(status_code=400, detail={"code":"TEAM_STATE_ARTIFACT_GOVERNANCE_INVALID","can_execute":False})

    db.table(table).upsert(
        rows,
        on_conflict=on_conflict,
        ignore_duplicates=True,
    ).execute()
    return {
        "status":"PERSISTED",
        "table":table,
        "row_count":len(rows),
        "automatic_certification":False,
        "automatic_promotion":False,
        "probability_publishable":False,
        "can_execute":False,
    }


def install_first_six_open_data_maintenance_routes(app: FastAPI, *, auth_dependency: Any, db_client_fn: Any) -> None:
    dependency = scout_route_auth_dependency(auth_dependency)
    routes = {getattr(route,"path",None) for route in app.router.routes}

    if "/internal/v17/ncaab-model-maintenance" not in routes:
        @app.post("/internal/v17/ncaab-model-maintenance",dependencies=[dependency],operation_id="runWowV17NcaabModelMaintenance")
        def run_ncaab_maintenance() -> dict[str, Any]:
            return _run("NCAAB",train_ncaab,db_client_fn())

    if "/internal/v17/soccer-model-maintenance" not in routes:
        @app.post("/internal/v17/soccer-model-maintenance",dependencies=[dependency],operation_id="runWowV17SoccerModelMaintenance")
        def run_soccer_maintenance() -> dict[str, Any]:
            return _run("SOCCER",train_soccer,db_client_fn())

    if "/internal/v17/tennis-model-maintenance" not in routes:
        @app.post("/internal/v17/tennis-model-maintenance",dependencies=[dependency],operation_id="runWowV17TennisModelMaintenance")
        def run_tennis_maintenance() -> dict[str, Any]:
            return _run("TENNIS",train_tennis,db_client_fn())

    if "/internal/v17/team-state-challenger-maintenance" not in routes:
        @app.post("/internal/v17/team-state-challenger-maintenance",dependencies=[dependency],operation_id="runWowV17TeamStateChallengerMaintenance")
        def run_team_state_challenger_maintenance() -> dict[str, Any]:
            return _run_team_state(db_client_fn())

    if "/internal/v17/team-state-challenger-maintenance/{scope}" not in routes:
        @app.post("/internal/v17/team-state-challenger-maintenance/{scope}",dependencies=[dependency],operation_id="runWowV17TeamStateChallengerMaintenanceScope")
        def run_team_state_challenger_maintenance_scope(scope: str) -> dict[str, Any]:
            return _run_team_state_scope_singleflight(db_client_fn, scope)

    if "/internal/v17/team-state-challenger-persist-batch" not in routes:
        @app.post("/internal/v17/team-state-challenger-persist-batch",dependencies=[dependency],operation_id="persistWowV17TeamStateChallengerBatch")
        def persist_team_state_challenger_batch(payload: dict[str, Any]) -> dict[str, Any]:
            return _persist_team_state_batch(db_client_fn(), payload)


__all__ = ["CAN_EXECUTE","install_first_six_open_data_maintenance_routes"]