"""NHL regular-season historical reconstruction and D1 candidate fitting.

This lane uses first-party NHL schedule/result data only.  Features are rebuilt
chronologically from prior settled games; sportsbook prices are never model
features.  Reconstructed rows are explicitly marked historical reconstruction,
not immutable archived pregame observations, so the output can be CANDIDATE
research evidence only and can never certify or publish by itself.
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from typing import Any, Iterable, Mapping, Sequence

import requests

from v17.binary_candidate_lifecycle import BinaryCandidate, BinaryTrainingRow, train_binary_candidate
from v17.model_source_entitlements import source_readiness

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
SOURCE_ID = "NHL_PUBLIC_WEB_API"
MODEL_FAMILY = "NHL_REGULAR_SEASON_LOGISTIC_V1"
FEATURE_SCHEMA_VERSION = "NHL_REGULAR_SEASON_FEATURES_V1"
FEATURE_RECONSTRUCTION_VERSION = "NHL_PRIOR_SETTLED_RECONSTRUCTION_V1"
BASE_URL = "https://api-web.nhle.com/v1/club-schedule-season/{team}/{season_id}"
MIN_PRIOR_GAMES = 5
ELO_K = 20.0
ELO_INITIAL = 1500.0
RECENT_WINDOW = 10

# Modern NHL plus Arizona for historical seasons preceding Utah's relocation.
TEAM_ABBREVIATIONS = (
    "ANA", "ARI", "BOS", "BUF", "CAR", "CBJ", "CGY", "CHI", "COL", "DAL",
    "DET", "EDM", "FLA", "LAK", "MIN", "MTL", "NJD", "NSH", "NYI", "NYR",
    "OTT", "PHI", "PIT", "SEA", "SJS", "STL", "TBL", "TOR", "UTA", "VAN",
    "VGK", "WPG", "WSH",
)

FEATURE_NAMES = (
    "elo_delta",
    "win_pct_l10_delta",
    "goal_diff_l10_delta",
    "goals_for_l10_delta",
    "goals_against_l10_delta",
    "season_win_pct_delta",
    "rest_days_delta",
    "home_back_to_back",
    "away_back_to_back",
    "neutral_site",
)


class NHLCandidateError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class NHLGame:
    game_id: str
    season_id: int
    event_start_time: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    neutral_site: bool
    source_uri: str
    source_retrieved_at: str
    source_payload_sha256: str
    game_type: int = 2
    historical_reconstruction: bool = True
    can_execute: bool = False


@dataclass
class _TeamState:
    elo: float
    wins: int
    games: int
    recent: deque[tuple[int, int, int]]
    last_start: datetime | None
    history_hash: str


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise NHLCandidateError("NHL_EVENT_TIME_INVALID", str(value))
    return parsed.astimezone(timezone.utc)


def _hash_payload(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()


def season_id(start_year: int) -> int:
    year = int(start_year)
    if year < 2000 or year > 2100:
        raise ValueError("invalid NHL season start year")
    return int(f"{year}{year + 1}")


def normalize_game(raw: Mapping[str, Any], *, source_uri: str, retrieved_at: str) -> NHLGame | None:
    if int(raw.get("gameType") or 0) != 2:
        return None
    if str(raw.get("gameState") or "").upper() not in {"FINAL", "OFF"}:
        return None
    home = raw.get("homeTeam")
    away = raw.get("awayTeam")
    if not isinstance(home, Mapping) or not isinstance(away, Mapping):
        return None
    game_id = str(raw.get("id") or "").strip()
    start = str(raw.get("startTimeUTC") or "").strip()
    home_abbrev = str(home.get("abbrev") or "").strip().upper()
    away_abbrev = str(away.get("abbrev") or "").strip().upper()
    hs = home.get("score")
    aws = away.get("score")
    raw_season = raw.get("season")
    if not game_id or not start or not home_abbrev or not away_abbrev or hs is None or aws is None or raw_season is None:
        return None
    try:
        home_score, away_score = int(hs), int(aws)
        parsed_season = int(raw_season)
        _aware(start)
    except (TypeError, ValueError, NHLCandidateError):
        return None
    if home_score == away_score:
        return None
    return NHLGame(
        game_id=game_id,
        season_id=parsed_season,
        event_start_time=_aware(start).isoformat(),
        home_team=home_abbrev,
        away_team=away_abbrev,
        home_score=home_score,
        away_score=away_score,
        neutral_site=bool(raw.get("neutralSite", False)),
        source_uri=source_uri,
        source_retrieved_at=retrieved_at,
        source_payload_sha256=_hash_payload(raw),
    )


def fetch_team_season_games(
    team: str,
    start_year: int,
    *,
    session: Any = requests,
    timeout: int = 20,
) -> list[NHLGame]:
    readiness = source_readiness(SOURCE_ID)
    if not readiness.ready_for_candidate_training:
        raise NHLCandidateError("NHL_SOURCE_NOT_READY", ",".join(readiness.blockers))
    sid = season_id(start_year)
    abbr = str(team or "").strip().upper()
    if abbr not in TEAM_ABBREVIATIONS:
        raise NHLCandidateError("NHL_TEAM_UNSUPPORTED", abbr)
    url = BASE_URL.format(team=abbr, season_id=sid)
    try:
        response = session.get(url, timeout=timeout, headers={"User-Agent": "WOW-V17-Model-Research/1.0"})
    except Exception as exc:  # noqa: BLE001
        raise NHLCandidateError("NHL_SOURCE_TRANSPORT_FAILED", type(exc).__name__) from exc
    if response.status_code == 429:
        raise NHLCandidateError("NHL_SOURCE_RATE_LIMITED", abbr)
    if response.status_code != 200:
        raise NHLCandidateError(f"NHL_SOURCE_HTTP_{response.status_code}", abbr)
    try:
        body = response.json()
    except Exception as exc:  # noqa: BLE001
        raise NHLCandidateError("NHL_SOURCE_RESPONSE_INVALID", type(exc).__name__) from exc
    games = body.get("games") if isinstance(body, Mapping) else None
    if not isinstance(games, list):
        raise NHLCandidateError("NHL_SOURCE_SCHEMA_DRIFT", "games array missing")
    retrieved_at = _utcnow().isoformat()
    normalized = [
        game
        for raw in games
        if isinstance(raw, Mapping)
        if (game := normalize_game(raw, source_uri=url, retrieved_at=retrieved_at)) is not None
    ]
    return normalized


def fetch_historical_games(
    start_years: Iterable[int],
    *,
    teams: Sequence[str] = TEAM_ABBREVIATIONS,
    session: Any = requests,
) -> list[NHLGame]:
    by_id: dict[str, NHLGame] = {}
    for year in sorted({int(v) for v in start_years}):
        for team in teams:
            for game in fetch_team_season_games(team, year, session=session):
                current = by_id.get(game.game_id)
                if current is not None:
                    # The same official game appears on both club schedules.
                    # Identity/result must agree exactly; provenance URI may differ.
                    identity = (game.season_id, game.event_start_time, game.home_team, game.away_team, game.home_score, game.away_score)
                    other = (current.season_id, current.event_start_time, current.home_team, current.away_team, current.home_score, current.away_score)
                    if identity != other:
                        raise NHLCandidateError("NHL_DUPLICATE_GAME_CONFLICT", game.game_id)
                    continue
                by_id[game.game_id] = game
    return sorted(by_id.values(), key=lambda row: (row.event_start_time, row.game_id))


def _state(team: str, season: int) -> _TeamState:
    seed = _hash_payload({"team": team, "season": season, "version": FEATURE_RECONSTRUCTION_VERSION})
    return _TeamState(
        elo=ELO_INITIAL,
        wins=0,
        games=0,
        recent=deque(maxlen=RECENT_WINDOW),
        last_start=None,
        history_hash=seed,
    )


def _recent_metrics(state: _TeamState) -> tuple[float, float, float, float]:
    if not state.recent:
        raise NHLCandidateError("NHL_RECENT_HISTORY_EMPTY", "prior games required")
    n = len(state.recent)
    wins = sum(item[0] for item in state.recent)
    goals_for = sum(item[1] for item in state.recent)
    goals_against = sum(item[2] for item in state.recent)
    return wins / n, (goals_for - goals_against) / n, goals_for / n, goals_against / n


def _rest_days(state: _TeamState, start: datetime) -> float:
    if state.last_start is None:
        raise NHLCandidateError("NHL_REST_HISTORY_EMPTY", "prior game required")
    value = (start - state.last_start).total_seconds() / 86400.0
    if value <= 0:
        raise NHLCandidateError("NHL_CHRONOLOGY_INVALID", "nonpositive rest interval")
    return min(value, 14.0)


def reconstruct_training_rows(games: Sequence[NHLGame]) -> tuple[list[BinaryTrainingRow], list[dict[str, Any]]]:
    ordered = sorted(games, key=lambda row: (row.event_start_time, row.game_id))
    if list(games) != ordered:
        raise NHLCandidateError("NHL_GAMES_NOT_CHRONOLOGICAL", "games must be sorted")
    states: dict[tuple[int, str], _TeamState] = {}
    rows: list[BinaryTrainingRow] = []
    feature_records: list[dict[str, Any]] = []

    for game in ordered:
        if game.game_type != 2 or game.can_execute is not False:
            continue
        start = _aware(game.event_start_time)
        home_key = (game.season_id, game.home_team)
        away_key = (game.season_id, game.away_team)
        home = states.setdefault(home_key, _state(game.home_team, game.season_id))
        away = states.setdefault(away_key, _state(game.away_team, game.season_id))

        if home.games >= MIN_PRIOR_GAMES and away.games >= MIN_PRIOR_GAMES:
            h_win10, h_gd10, h_gf10, h_ga10 = _recent_metrics(home)
            a_win10, a_gd10, a_gf10, a_ga10 = _recent_metrics(away)
            home_rest = _rest_days(home, start)
            away_rest = _rest_days(away, start)
            features = {
                "elo_delta": home.elo - away.elo,
                "win_pct_l10_delta": h_win10 - a_win10,
                "goal_diff_l10_delta": h_gd10 - a_gd10,
                "goals_for_l10_delta": h_gf10 - a_gf10,
                "goals_against_l10_delta": h_ga10 - a_ga10,
                "season_win_pct_delta": (home.wins / home.games) - (away.wins / away.games),
                "rest_days_delta": home_rest - away_rest,
                "home_back_to_back": 1.0 if home_rest <= 1.5 else 0.0,
                "away_back_to_back": 1.0 if away_rest <= 1.5 else 0.0,
                "neutral_site": 1.0 if game.neutral_site else 0.0,
            }
            manifest = {
                "source_id": SOURCE_ID,
                "feature_schema_version": FEATURE_SCHEMA_VERSION,
                "reconstruction_version": FEATURE_RECONSTRUCTION_VERSION,
                "historical_reconstruction": True,
                "reconstruction_basis": "PRIOR_SETTLED_GAMES_ONLY",
                "archive_pregame_snapshot_claimed": False,
                "market_features_used": False,
                "home_prior_history_hash": home.history_hash,
                "away_prior_history_hash": away.history_hash,
                "game_source_payload_sha256": game.source_payload_sha256,
                "can_execute": False,
            }
            manifest_hash = _hash_payload(manifest)
            feature_as_of = (start - timedelta(seconds=1)).isoformat()
            row = BinaryTrainingRow(
                event_id=game.game_id,
                event_start_time=start.isoformat(),
                feature_as_of=feature_as_of,
                positive_outcome=game.home_score > game.away_score,
                features=features,
                source_manifest_sha256=manifest_hash,
            )
            rows.append(row)
            feature_records.append({
                "sport": "NHL",
                "league": "NHL",
                "official_event_id": game.game_id,
                "season": str(game.season_id),
                "event_start_time": start.isoformat(),
                "feature_as_of": feature_as_of,
                "feature_schema_version": FEATURE_SCHEMA_VERSION,
                "features": features,
                "source_manifest": manifest,
                "source_manifest_sha256": manifest_hash,
                "historical_reconstruction": True,
                "archived_pregame_snapshot": False,
                "market_features_used": False,
                "can_execute": False,
            })

        # Only after feature creation/outcome freeze do we update state with the
        # current settled game, preventing target leakage into its own features.
        home_win = game.home_score > game.away_score
        elo_expected_home = 1.0 / (1.0 + math.pow(10.0, (away.elo - home.elo) / 400.0))
        delta = ELO_K * ((1.0 if home_win else 0.0) - elo_expected_home)
        home.elo += delta
        away.elo -= delta
        home.wins += 1 if home_win else 0
        away.wins += 0 if home_win else 1
        home.games += 1
        away.games += 1
        home.recent.append((1 if home_win else 0, game.home_score, game.away_score))
        away.recent.append((0 if home_win else 1, game.away_score, game.home_score))
        home.last_start = start
        away.last_start = start
        game_chain = _hash_payload({
            "game_id": game.game_id,
            "start": start.isoformat(),
            "home": game.home_team,
            "away": game.away_team,
            "home_score": game.home_score,
            "away_score": game.away_score,
            "source_payload_sha256": game.source_payload_sha256,
        })
        home.history_hash = _hash_payload({"previous": home.history_hash, "game": game_chain})
        away.history_hash = _hash_payload({"previous": away.history_hash, "game": game_chain})

    return rows, feature_records


def fit_nhl_candidate(games: Sequence[NHLGame]) -> tuple[BinaryCandidate, list[dict[str, Any]]]:
    rows, feature_records = reconstruct_training_rows(games)
    try:
        candidate = train_binary_candidate(
            rows,
            model_family=MODEL_FAMILY,
            feature_names=FEATURE_NAMES,
            min_rows=300,
        )
    except Exception as exc:
        if isinstance(exc, NHLCandidateError):
            raise
        code = getattr(exc, "code", "NHL_CANDIDATE_TRAINING_FAILED")
        raise NHLCandidateError(str(code), str(exc)) from exc
    return candidate, feature_records


def game_record(game: NHLGame) -> dict[str, Any]:
    return {
        "sport": "NHL",
        "league": "NHL",
        "official_event_id": game.game_id,
        "season": str(game.season_id),
        "event_start_time": game.event_start_time,
        "home_team": game.home_team,
        "away_team": game.away_team,
        "home_score": game.home_score,
        "away_score": game.away_score,
        "positive_outcome": game.home_score > game.away_score,
        "source_provider": SOURCE_ID,
        "source_uri": game.source_uri,
        "source_retrieved_at": game.source_retrieved_at,
        "source_payload_sha256": game.source_payload_sha256,
        "historical_reconstruction": True,
        "can_execute": False,
    }


def candidate_record(candidate: BinaryCandidate, *, training_code_sha: str) -> dict[str, Any]:
    if len(str(training_code_sha or "").strip()) < 7:
        raise NHLCandidateError("NHL_TRAINING_CODE_SHA_REQUIRED", "auditable code SHA required")
    payload = dict(candidate.artifact_payload)
    calibrator = dict(candidate.calibrator_payload)
    artifact_checksum = _hash_payload(payload)
    version = f"NHL_RS_LOGISTIC_V1-{candidate.dataset_hash[:16]}-{training_code_sha[:12]}"
    metrics = asdict(candidate.metrics)
    metrics.update({
        "research_screen_pass": candidate.research_screen_pass,
        "historical_reconstruction": True,
        "archive_pregame_snapshot_claimed": False,
        "market_features_used": False,
        "probability_publishable": False,
        "can_execute": False,
    })
    return {
        "sport": "NHL",
        "league": "NHL",
        "model_family": MODEL_FAMILY,
        "model_artifact_version": version,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "source_policy_id": SOURCE_ID,
        "historical_reconstruction": True,
        "training_dataset_hash": candidate.dataset_hash,
        "training_code_sha": str(training_code_sha),
        "artifact_checksum": artifact_checksum,
        "artifact_payload": payload,
        "calibrator_payload": calibrator,
        "validation_metrics": metrics,
        "training_rows": candidate.metrics.train_n + candidate.metrics.calibration_n + candidate.metrics.test_n,
        "calibration_rows": candidate.metrics.calibration_n,
        "test_rows": candidate.metrics.test_n,
        "research_screen_pass": candidate.research_screen_pass,
        "source_review_status": "REQUIRED",
        "lifecycle_state": "CANDIDATE",
        "promoted": False,
        "active": False,
        "probability_publishable": False,
        "can_execute": False,
    }


__all__ = [
    "BASE_URL",
    "CAN_EXECUTE",
    "FEATURE_NAMES",
    "FEATURE_RECONSTRUCTION_VERSION",
    "FEATURE_SCHEMA_VERSION",
    "MODEL_FAMILY",
    "NHLCandidateError",
    "NHLGame",
    "SOURCE_ID",
    "TEAM_ABBREVIATIONS",
    "candidate_record",
    "fetch_historical_games",
    "fetch_team_season_games",
    "fit_nhl_candidate",
    "game_record",
    "normalize_game",
    "reconstruct_training_rows",
    "season_id",
]
