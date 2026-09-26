"""Run source-heavy team-state challenger acquisition/fitting off the Render request path.

Public-source acquisition and model fitting execute on the protected GitHub runner.
Database authority remains server-side: this module captures only the existing
governed upsert calls so the workflow can forward bounded evidence batches to
the authenticated Render persistence endpoint.

No serving artifact is activated here. No probability is published. Execution
authority remains permanently false.
"""
from __future__ import annotations

import json
from typing import Any

from v17.team_state_scoped_maintenance import run_team_state_scope

CAN_EXECUTE = False

SOURCE_HEAVY_SCOPES = (
    "MLB",
    "NCAAB",
    "SOCCER_EPL",
    "SOCCER_BUNDESLIGA",
    "SOCCER_LALIGA",
    "SOCCER_SERIE_A",
    "SOCCER_LIGUE_1",
)

PERSIST_CONTRACTS = {
    "wow_d1_training_rows": {
        "on_conflict": "sport,official_event_id,feature_schema_version,source_manifest_sha256",
        "max_rows": 250,
    },
    "wow_d1_candidate_artifacts": {
        "on_conflict": "model_artifact_version",
        "max_rows": 5,
    },
}


def _json_default(value: Any) -> Any:
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        return tolist()
    item = getattr(value, "item", None)
    if callable(item):
        return item()
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True, default=_json_default))


class _CaptureExecute:
    def __init__(
        self,
        client: "CaptureClient",
        table: str,
        rows: list[dict[str, Any]],
        *,
        on_conflict: str,
        ignore_duplicates: bool,
    ) -> None:
        self._client = client
        self._table = table
        self._rows = rows
        self._on_conflict = on_conflict
        self._ignore_duplicates = ignore_duplicates

    def execute(self) -> Any:
        self._client._record(
            self._table,
            self._rows,
            on_conflict=self._on_conflict,
            ignore_duplicates=self._ignore_duplicates,
        )
        return type("CaptureResult", (), {"data": []})()


class _CaptureTable:
    def __init__(self, client: "CaptureClient", table: str) -> None:
        self._client = client
        self._table = table

    def upsert(
        self,
        rows: Any,
        *,
        on_conflict: str,
        ignore_duplicates: bool = False,
        **_: Any,
    ) -> _CaptureExecute:
        normalized = rows if isinstance(rows, list) else [rows]
        if not normalized or not all(isinstance(row, dict) for row in normalized):
            raise RuntimeError("TEAM_STATE_RUNNER_CAPTURE_ROWS_INVALID")
        return _CaptureExecute(
            self._client,
            self._table,
            normalized,
            on_conflict=on_conflict,
            ignore_duplicates=ignore_duplicates,
        )

    def __getattr__(self, name: str) -> Any:
        raise RuntimeError(f"TEAM_STATE_RUNNER_DB_READ_NOT_ALLOWED:{self._table}:{name}")


class CaptureClient:
    """Minimal Supabase-compatible write capture for source-heavy lanes only."""

    def __init__(self) -> None:
        self.batches: list[dict[str, Any]] = []

    def table(self, table: str) -> _CaptureTable:
        if table not in PERSIST_CONTRACTS:
            raise RuntimeError(f"TEAM_STATE_RUNNER_TABLE_NOT_ALLOWED:{table}")
        return _CaptureTable(self, table)

    def _record(
        self,
        table: str,
        rows: list[dict[str, Any]],
        *,
        on_conflict: str,
        ignore_duplicates: bool,
    ) -> None:
        contract = PERSIST_CONTRACTS[table]
        if on_conflict != contract["on_conflict"] or ignore_duplicates is not True:
            raise RuntimeError(f"TEAM_STATE_RUNNER_UPSERT_CONTRACT_INVALID:{table}")
        if len(rows) > int(contract["max_rows"]):
            raise RuntimeError(f"TEAM_STATE_RUNNER_BATCH_TOO_LARGE:{table}:{len(rows)}")
        canonical_rows = _jsonable(rows)
        self.batches.append(
            {
                "table": table,
                "rows": canonical_rows,
                "on_conflict": on_conflict,
                "ignore_duplicates": True,
            }
        )


def prepare_source_heavy_scope(*, scope: str, training_code_sha: str) -> dict[str, Any]:
    normalized = str(scope or "").strip().upper()
    if normalized not in SOURCE_HEAVY_SCOPES:
        raise RuntimeError(f"TEAM_STATE_RUNNER_SCOPE_NOT_ALLOWED:{normalized}")

    code = str(training_code_sha or "").strip().lower()
    if len(code) < 7:
        raise RuntimeError("TEAM_STATE_RUNNER_TRAINING_CODE_SHA_UNAVAILABLE")

    client = CaptureClient()
    result = _jsonable(
        run_team_state_scope(
            client,
            scope=normalized,
            training_code_sha=code,
        )
    )

    updated = int(result.get("candidate_rows_updated") or 0)
    if updated:
        if not client.batches:
            raise RuntimeError(f"TEAM_STATE_RUNNER_PERSISTENCE_MISSING:{normalized}")
        tables = {batch["table"] for batch in client.batches}
        if "wow_d1_training_rows" not in tables or "wow_d1_candidate_artifacts" not in tables:
            raise RuntimeError(f"TEAM_STATE_RUNNER_PERSISTENCE_INCOMPLETE:{normalized}:{sorted(tables)}")
    elif client.batches:
        raise RuntimeError(f"TEAM_STATE_RUNNER_BLOCKED_WITH_WRITES:{normalized}")

    return {
        "scope": normalized,
        "result": result,
        "batches": client.batches,
        "batch_count": len(client.batches),
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "CaptureClient",
    "PERSIST_CONTRACTS",
    "SOURCE_HEAVY_SCOPES",
    "prepare_source_heavy_scope",
]