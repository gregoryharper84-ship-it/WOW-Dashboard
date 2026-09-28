"""Candidate-only NCAAF player-prop replay runner.

Reads governed CFBD snapshots, builds the seven-route Class-C challenger package,
and can persist only inert CANDIDATE artifacts. It has no production scoring route
and no certification, activation, promotion, publication, ranking, or execution path.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ncaaf_cfbd_hydrator import SourceSnapshot
from v17.ncaaf_prop_challenger import build_candidate_package

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_CERTIFICATION = False
AUTOMATIC_PROMOTION = False
SOURCE_SEASONS = (2023, 2024, 2025, 2026)


class NCAAFPropChallengerRunnerError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}:{detail}" if detail else code)
        self.code = code
        self.detail = detail


def _snapshot_from_row(row: Mapping[str, Any]) -> SourceSnapshot:
    response_rows = row.get("response_rows")
    blocker_codes = row.get("blocker_codes")
    if not isinstance(response_rows, list):
        raise NCAAFPropChallengerRunnerError("NCAAF_PROP_SOURCE_ROWS_INVALID")
    if not isinstance(blocker_codes, list):
        blocker_codes = []
    can_execute = row.get("can_execute")
    if can_execute is not False:
        raise NCAAFPropChallengerRunnerError("NCAAF_PROP_SOURCE_GOVERNANCE_INVALID")
    return SourceSnapshot(
        provider=str(row.get("provider") or ""),
        endpoint=str(row.get("endpoint") or ""),
        season=int(row.get("season")),
        week=None if row.get("week") is None else int(row.get("week")),
        requested_at=str(row.get("requested_at") or ""),
        retrieved_at=str(row.get("retrieved_at") or ""),
        request_params=dict(row.get("request_params") or {}),
        response_rows=response_rows,
        response_row_count=int(row.get("response_row_count") or 0),
        payload_sha256=str(row.get("payload_sha256") or ""),
        acquisition_status=str(row.get("acquisition_status") or ""),
        blocker_codes=[str(v) for v in blocker_codes],
        can_execute=False,
    )


def load_source_snapshots(
    db: Any,
    *,
    endpoint: str,
    seasons: Sequence[int] = SOURCE_SEASONS,
    page_size: int = 500,
) -> list[SourceSnapshot]:
    fields = (
        "provider,endpoint,season,week,requested_at,retrieved_at,request_params,"
        "response_rows,response_row_count,payload_sha256,acquisition_status,blocker_codes,can_execute"
    )
    snapshots: list[SourceSnapshot] = []
    offset = 0
    while True:
        try:
            result = (
                db.table("wow_ncaaf_source_snapshots")
                .select(fields)
                .eq("provider", "CFBD")
                .eq("endpoint", endpoint)
                .in_("season", [int(v) for v in seasons])
                .range(offset, offset + page_size - 1)
                .execute()
            )
        except Exception as exc:  # noqa: BLE001
            raise NCAAFPropChallengerRunnerError(
                "NCAAF_PROP_SOURCE_READ_FAILED", endpoint
            ) from exc
        rows = getattr(result, "data", None)
        if not isinstance(rows, list):
            raise NCAAFPropChallengerRunnerError("NCAAF_PROP_SOURCE_READ_INVALID", endpoint)
        snapshots.extend(_snapshot_from_row(row) for row in rows if isinstance(row, Mapping))
        if len(rows) < page_size:
            break
        offset += page_size
    return snapshots


def run_candidate_replay(db: Any, *, training_code_sha: str) -> dict[str, Any]:
    games = load_source_snapshots(db, endpoint="/games")
    players = load_source_snapshots(db, endpoint="/games/players")
    if not games:
        raise NCAAFPropChallengerRunnerError("NCAAF_PROP_GAMES_CORPUS_EMPTY")
    if not players:
        raise NCAAFPropChallengerRunnerError("NCAAF_PROP_PLAYER_CORPUS_EMPTY")
    package = build_candidate_package(
        players,
        games,
        training_code_sha=training_code_sha,
    )
    return {
        **package,
        "game_snapshot_n": len(games),
        "player_snapshot_n": len(players),
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def _assert_inert_candidate(row: Mapping[str, Any]) -> None:
    if row.get("lifecycle_state") != "CANDIDATE":
        raise NCAAFPropChallengerRunnerError("NCAAF_PROP_ARTIFACT_NOT_CANDIDATE")
    for field in ("active", "promoted", "probability_publishable", "can_execute"):
        if row.get(field) is not False:
            raise NCAAFPropChallengerRunnerError(
                "NCAAF_PROP_ARTIFACT_GOVERNANCE_INVALID", field
            )
    if row.get("candidate_research_active") is not True:
        raise NCAAFPropChallengerRunnerError("NCAAF_PROP_RESEARCH_FLAG_INVALID")
    if str(row.get("sport") or "").upper() != "NCAAF":
        raise NCAAFPropChallengerRunnerError("NCAAF_PROP_SPORT_IDENTITY_INVALID")


def persist_inert_candidates(db: Any, package: Mapping[str, Any]) -> dict[str, Any]:
    if package.get("can_execute") is not False or package.get("probability_publishable") is not False:
        raise NCAAFPropChallengerRunnerError("NCAAF_PROP_PACKAGE_GOVERNANCE_INVALID")
    persisted: list[dict[str, Any]] = []
    for raw in package.get("candidates") or []:
        if not isinstance(raw, Mapping):
            continue
        _assert_inert_candidate(raw)
        row = dict(raw)
        try:
            result = (
                db.table("wow_prop_fitted_model_artifacts")
                .upsert(row, on_conflict="provider_identity,model_artifact_version")
                .execute()
            )
        except Exception as exc:  # noqa: BLE001
            raise NCAAFPropChallengerRunnerError(
                "NCAAF_PROP_CANDIDATE_PERSIST_FAILED", str(row.get("stat_type") or "")
            ) from exc
        data = getattr(result, "data", None)
        persisted.append({
            "stat_type": row.get("stat_type"),
            "model_artifact_version": row.get("model_artifact_version"),
            "research_screen_pass": bool((row.get("validation_metrics") or {}).get("research_screen_pass")),
            "persisted_row_n": len(data) if isinstance(data, list) else 0,
            "lifecycle_state": "CANDIDATE",
            "probability_publishable": False,
            "can_execute": False,
        })
    return {
        "status": "NCAAF_PROP_INERT_CANDIDATES_PERSISTED",
        "candidate_n": len(persisted),
        "candidates": persisted,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


__all__ = [
    "AUTOMATIC_CERTIFICATION", "AUTOMATIC_PROMOTION", "CAN_EXECUTE",
    "NCAAFPropChallengerRunnerError", "PROBABILITY_PUBLISHABLE",
    "load_source_snapshots", "persist_inert_candidates", "run_candidate_replay",
]
