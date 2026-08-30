"""Persist raw first-party WNBA Stats player-game rows without role inference.

This is a bronze/source ingestion boundary only. Official LeagueGameLog rows are
stored exactly enough for audit and later enrichment. Starter/role state is never
inferred from minutes or production; training remains blocked until independent
role evidence is resolved. can_execute is always false.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Iterable


class WNBAHistoryIngestionError(RuntimeError):
    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class WNBAHistoryIngestionResult:
    fetched_n: int
    accepted_n: int
    persisted_n: int
    rejected_n: int
    rejected_codes: tuple[str, ...]
    role_evidence_status: str = "UNRESOLVED"
    training_materialization_status: str = "BLOCKED_ROLE_EVIDENCE"
    runtime_model_status: str = "MODEL_UNAVAILABLE"
    probability_publishable: bool = False
    can_execute: bool = False


def _text(row: dict[str, Any], key: str) -> str:
    value = " ".join(str(row.get(key) or "").strip().split())
    if not value:
        raise WNBAHistoryIngestionError("WNBA_HISTORY_REQUIRED_FIELD_MISSING", key)
    return value


def _int_stat(row: dict[str, Any], key: str) -> int:
    value = row.get(key)
    if isinstance(value, bool):
        raise WNBAHistoryIngestionError("WNBA_HISTORY_STAT_INVALID", key)
    try:
        parsed = int(value)
        original = float(value)
    except (TypeError, ValueError) as exc:
        raise WNBAHistoryIngestionError("WNBA_HISTORY_STAT_INVALID", key) from exc
    if parsed < 0 or original != float(parsed):
        raise WNBAHistoryIngestionError("WNBA_HISTORY_STAT_INVALID", key)
    return parsed


def _minutes(row: dict[str, Any]) -> float:
    try:
        value = float(row.get("MIN"))
    except (TypeError, ValueError) as exc:
        raise WNBAHistoryIngestionError("WNBA_HISTORY_MINUTES_INVALID") from exc
    if not 0 <= value <= 60:
        raise WNBAHistoryIngestionError("WNBA_HISTORY_MINUTES_INVALID")
    return value


def _game_date(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise WNBAHistoryIngestionError("WNBA_HISTORY_GAME_DATE_INVALID")
    # LeagueGameLog commonly emits values such as MAY 16, 2026. Accept ISO too.
    for fmt in ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    raise WNBAHistoryIngestionError("WNBA_HISTORY_GAME_DATE_INVALID")


def normalize_raw_game_log_row(
    row: dict[str, Any], *, season: int, season_type: str, source_retrieved_at: str
) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise WNBAHistoryIngestionError("WNBA_HISTORY_ROW_NOT_OBJECT")
    player_id = _text(row, "PLAYER_ID")
    game_id = _text(row, "GAME_ID")
    canonical_raw = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    payload_hash = sha256(canonical_raw.encode("utf-8")).hexdigest()
    return {
        "season": int(season),
        "season_type": season_type,
        "game_id": game_id,
        "game_date": _game_date(row.get("GAME_DATE")),
        "player_id": player_id,
        "player_name": _text(row, "PLAYER_NAME"),
        "team_abbreviation": _text(row, "TEAM_ABBREVIATION").upper(),
        "matchup": _text(row, "MATCHUP"),
        "minutes": _minutes(row),
        "pts": _int_stat(row, "PTS"),
        "reb": _int_stat(row, "REB"),
        "ast": _int_stat(row, "AST"),
        "fg3m": _int_stat(row, "FG3M"),
        "source_identity": "WNBA_STATS_LEAGUE_GAME_LOG",
        "source_retrieved_at": source_retrieved_at,
        "source_payload_hash": payload_hash,
        "raw_row": row,
        "role_evidence_status": "UNRESOLVED",
        "training_materialization_status": "BLOCKED_ROLE_EVIDENCE",
        "can_execute": False,
    }


def persist_raw_game_logs(
    client: Any,
    rows: Iterable[dict[str, Any]],
    *,
    season: int,
    season_type: str,
    source_retrieved_at: str,
) -> WNBAHistoryIngestionResult:
    fetched = list(rows)
    accepted: list[dict[str, Any]] = []
    rejected_codes: list[str] = []
    for row in fetched:
        try:
            accepted.append(normalize_raw_game_log_row(
                row,
                season=season,
                season_type=season_type,
                source_retrieved_at=source_retrieved_at,
            ))
        except WNBAHistoryIngestionError as exc:
            rejected_codes.append(exc.code)

    if rejected_codes:
        # Do not persist a partial official season pull as if complete. Caller may
        # retry after source/parser repair; accepted rows are returned only via counts.
        return WNBAHistoryIngestionResult(
            fetched_n=len(fetched),
            accepted_n=len(accepted),
            persisted_n=0,
            rejected_n=len(rejected_codes),
            rejected_codes=tuple(sorted(set(rejected_codes))),
        )

    persisted_n = 0
    for payload in accepted:
        try:
            result = (
                client.table("wow_wnba_player_game_logs")
                .upsert(
                    payload,
                    on_conflict="season,season_type,game_id,player_id,source_identity",
                )
                .execute()
            )
        except Exception as exc:
            raise WNBAHistoryIngestionError("WNBA_HISTORY_PERSISTENCE_UNAVAILABLE") from exc
        data = getattr(result, "data", None)
        persisted_n += len(data) if isinstance(data, list) and data else 1

    return WNBAHistoryIngestionResult(
        fetched_n=len(fetched),
        accepted_n=len(accepted),
        persisted_n=persisted_n,
        rejected_n=0,
        rejected_codes=(),
    )
