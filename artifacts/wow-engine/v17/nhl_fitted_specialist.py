"""Server-owned fitted NHL game-winner specialist.

This module restores the *fitted* NHL lane without inventing a new model. It
loads the exact ``NHL_REGULAR_SEASON_LOGISTIC_V1`` candidate artifact from the
shared governed D1 registry tables (``wow_d1_candidate_artifacts``) and applies
the chronological calibrator that was selected by the candidate lifecycle.

Two properties matter and are intentionally separate:

* The artifact is resolved **server-side**. A request can never supply its own
  coefficients, calibrator, model version, or calibrated bounds.
* Candidate status is not certification. Every package keeps
  ``probability_publishable=False`` and ``rank_eligible=False``. Publication is
  only possible after the existing certification gate is satisfied elsewhere.

Current-event features are rebuilt from prior settled first-party NHL rows in
``wow_d1_source_events`` using the same feature semantics as training. If the
prior-game timeline is unavailable, incomplete, or contains a conflicting
identity, the lane returns a typed hold instead of a probability.

No function here can execute a wager.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re
from typing import Any, Iterable, Mapping

from v17.nhl_event_identity import team_abbreviation
from v17.nhl_provenance import (
    SOURCE_PAYLOAD_HASH_SCOPE,
    SOURCE_PROVENANCE_VERSION,
    provenance_envelope_hash,
)

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False

MODEL_FAMILY = "NHL_REGULAR_SEASON_LOGISTIC_V1"
FEATURE_SCHEMA_VERSION = "NHL_REGULAR_SEASON_FEATURES_V1"
SOURCE_POLICY_ID = "NHL_PUBLIC_WEB_API"
CANDIDATE_TABLE = "wow_d1_candidate_artifacts"
SOURCE_TABLE = "wow_d1_source_events"

# Exact training constants/feature order. A regression test asserts these stay
# identical to nhl_candidate_pipeline so the registry artifact cannot be fed a
# drifted feature schema.
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
ELO_K = 20.0
ELO_INITIAL = 1500.0
RECENT_WINDOW = 10
MIN_PRIOR_GAMES = 5
SPLIT_POLICY = "CHRONOLOGICAL_60_20_20"

IDENTITY_CALIBRATION_METHOD = "IDENTITY_RAW_PROBABILITY_V1"
EMPIRICAL_CALIBRATION_METHOD = "EMPIRICAL_WILSON_BINS_V1"

FULL_GAME_SETTLEMENT = "HOME_AWAY_INCLUDING_OT_SO"
SETTLEMENT_CONTRACT = "FULL_GAME_MONEYLINE_INCLUDING_OVERTIME_SHOOTOUT"
ACCEPTED_SETTLEMENT_BASES = frozenset({FULL_GAME_SETTLEMENT, SETTLEMENT_CONTRACT})
# Request/host labels that mean the same full-game OT/SO moneyline settlement.
REQUEST_FULL_GAME_OT_SO_BASES = frozenset(
    {
        FULL_GAME_SETTLEMENT,
        SETTLEMENT_CONTRACT,
        "FULL_GAME_INCLUDING_OVERTIME_AND_SHOOTOUT",
        "FULL_GAME_OUTRIGHT",
    }
)
CONFIRMED_GOALIE_STATUSES = frozenset({"CONFIRMED", "CONFIRMED_STARTER", "OFFICIAL_STARTER"})
MAX_EVIDENCE_AGE = timedelta(minutes=60)

_PAGE_SIZE = 1000
_MAX_SEASON_EVENTS = 5000

# Test/rollback seam. Production installs the trusted db-client provider once.
_REGISTRY_DB_PROVIDER: Any = None


class NHLFittedSpecialistError(RuntimeError):
    """Typed fail-closed error carrying the exact blocker vocabulary."""

    def __init__(self, code: str, blockers: Iterable[str], detail: Any = None):
        """Store a typed NHL specialist failure code and blocker list."""
        self.code = str(code)
        self.blockers = tuple(dict.fromkeys(str(v) for v in blockers if v))
        self.detail = detail
        super().__init__(f"{self.code}:{','.join(self.blockers)}")


def _sha256(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _aware(value: Any) -> datetime | None:
    """Parse a timestamp into an aware UTC datetime, or None if invalid."""
    token = str(value or "").strip()
    if not token:
        return None
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _number(value: Any) -> float | None:
    """Coerce a finite numeric value, or return None when malformed."""
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _probability(value: Any) -> float | None:
    """Clamp a probability into the open unit interval used by scoring."""
    parsed = _number(value)
    return parsed if parsed is not None and 0.0 < parsed < 1.0 else None


def _sigmoid(value: float) -> float:
    """Map a real-valued score through the logistic sigmoid."""
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exp_value = math.exp(value)
    return exp_value / (1.0 + exp_value)


def _hash64(value: Any) -> bool:
    """Hash ordered bytes into a stable 64-bit digest for provenance."""
    token = str(value or "").strip().lower()
    return len(token) == 64 and all(ch in "0123456789abcdef" for ch in token)


def install_nhl_fitted_registry_provider(provider: Any) -> None:
    global _REGISTRY_DB_PROVIDER
    _REGISTRY_DB_PROVIDER = provider


def reset_nhl_fitted_registry_provider() -> None:
    global _REGISTRY_DB_PROVIDER
    _REGISTRY_DB_PROVIDER = None


@dataclass(frozen=True)
class NHLFittedCandidate:
    candidate_id: str
    model_version: str
    model_family: str
    dataset_hash: str
    training_code_sha: str
    artifact_checksum: str
    created_at: datetime
    artifact: Mapping[str, Any]
    calibrator: Mapping[str, Any]
    metrics: Mapping[str, Any]
    training_rows: int
    calibration_rows: int
    test_rows: int
    research_screen_pass: bool
    source_review_status: str

    @property
    def publication_capable(self) -> bool:
        return False

    @property
    def selected_calibrator_method(self) -> str:
        """Return the selected calibrator method for the fitted candidate."""
        return str(self.calibrator.get("method") or "")

    @property
    def has_calibrated_bounds(self) -> bool:
        return self.selected_calibrator_method == EMPIRICAL_CALIBRATION_METHOD


@dataclass(frozen=True)
class NHLFeatureReconstruction:
    season: int
    official_event_id: str
    event_start_time: datetime
    feature_as_of: datetime
    home_team: str
    away_team: str
    features: Mapping[str, float]
    home_prior_games: int
    away_prior_games: int
    prior_event_count: int
    timeline_hash: str


def _verify_candidate(row: Mapping[str, Any]) -> NHLFittedCandidate:
    """Verify fitted-candidate registry fields fail-closed before scoring."""
    blockers: list[str] = []
    if str(row.get("sport") or "").upper() != "NHL":
        blockers.append("NHL_FITTED_ARTIFACT_SPORT_MISMATCH")
    if str(row.get("league") or "").upper() != "NHL":
        blockers.append("NHL_FITTED_ARTIFACT_LEAGUE_MISMATCH")
    if str(row.get("model_family") or "") != MODEL_FAMILY:
        blockers.append("NHL_FITTED_ARTIFACT_MODEL_FAMILY_MISMATCH")
    if str(row.get("feature_schema_version") or "") != FEATURE_SCHEMA_VERSION:
        blockers.append("NHL_FITTED_ARTIFACT_FEATURE_SCHEMA_MISMATCH")
    if str(row.get("source_policy_id") or "") != SOURCE_POLICY_ID:
        blockers.append("NHL_FITTED_ARTIFACT_SOURCE_POLICY_MISMATCH")
    if str(row.get("lifecycle_state") or "") != "CANDIDATE":
        blockers.append("NHL_FITTED_ARTIFACT_NOT_CANDIDATE_LIFECYCLE")
    if row.get("promoted") is not False or row.get("active") is not False:
        blockers.append("NHL_FITTED_ARTIFACT_ILLEGAL_PROMOTION_STATE")
    if row.get("probability_publishable") is not False or row.get("can_execute") is not False:
        blockers.append("NHL_FITTED_ARTIFACT_GOVERNANCE_FLAGS_INVALID")
    if row.get("research_screen_pass") is not True:
        blockers.append("NHL_FITTED_ARTIFACT_RESEARCH_SCREEN_NOT_PASS")
    if str(row.get("source_review_status") or "").upper() not in {"REQUIRED", "PENDING", "PASS"}:
        blockers.append("NHL_FITTED_ARTIFACT_SOURCE_REVIEW_STATUS_INVALID")

    artifact = row.get("artifact_payload")
    calibrator = row.get("calibrator_payload")
    metrics = row.get("validation_metrics")
    if not isinstance(artifact, Mapping):
        blockers.append("NHL_FITTED_ARTIFACT_PAYLOAD_MISSING")
        artifact = {}
    if not isinstance(calibrator, Mapping):
        blockers.append("NHL_FITTED_CALIBRATOR_PAYLOAD_MISSING")
        calibrator = {}
    if not isinstance(metrics, Mapping):
        blockers.append("NHL_FITTED_VALIDATION_METRICS_MISSING")
        metrics = {}

    checksum = str(row.get("artifact_checksum") or "").strip().lower()
    if not _hash64(checksum) or checksum != _sha256(dict(artifact)):
        blockers.append("NHL_FITTED_ARTIFACT_CHECKSUM_MISMATCH")
    dataset_hash = str(row.get("training_dataset_hash") or "").strip().lower()
    if not _hash64(dataset_hash):
        blockers.append("NHL_FITTED_DATASET_HASH_INVALID")
    training_code_sha = str(row.get("training_code_sha") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{7,64}", training_code_sha):
        blockers.append("NHL_FITTED_TRAINING_CODE_SHA_INVALID")

    if str(artifact.get("model_family") or "") != MODEL_FAMILY:
        blockers.append("NHL_FITTED_ARTIFACT_EMBEDDED_IDENTITY_MISMATCH")
    if str(artifact.get("artifact_format") or "") != "STANDARDIZED_LOGISTIC_JSON_V1":
        blockers.append("NHL_FITTED_ARTIFACT_FORMAT_UNSUPPORTED")
    if tuple(artifact.get("feature_names") or ()) != FEATURE_NAMES:
        blockers.append("NHL_FITTED_ARTIFACT_FEATURE_ORDER_MISMATCH")
    coefficients = artifact.get("coefficients")
    means = artifact.get("scaler_mean")
    scales = artifact.get("scaler_scale")
    if (
        not isinstance(coefficients, list)
        or not isinstance(means, list)
        or not isinstance(scales, list)
        or len(coefficients) != len(FEATURE_NAMES)
        or len(means) != len(FEATURE_NAMES)
        or len(scales) != len(FEATURE_NAMES)
    ):
        blockers.append("NHL_FITTED_ARTIFACT_COEFFICIENTS_INVALID")
    else:
        for value in [*coefficients, *means, *scales, artifact.get("intercept")]:
            if _number(value) is None:
                blockers.append("NHL_FITTED_ARTIFACT_COEFFICIENTS_INVALID")
                break
        if any((_number(scale) or 0.0) == 0.0 for scale in scales):
            blockers.append("NHL_FITTED_ARTIFACT_SCALER_INVALID")
    if str(artifact.get("split_policy") or "") != SPLIT_POLICY:
        blockers.append("NHL_FITTED_ARTIFACT_SPLIT_POLICY_INVALID")

    stored_counts = (
        row.get("training_rows"),
        row.get("calibration_rows"),
        row.get("test_rows"),
    )
    fitted_counts = (
        metrics.get("train_n"),
        metrics.get("calibration_n"),
        metrics.get("test_n"),
    )
    counts_valid = (
        all(type(value) is int and value > 0 for value in stored_counts)
        and all(type(value) is int and value > 0 for value in fitted_counts)
        and stored_counts == fitted_counts
    )
    if not counts_valid:
        blockers.append("NHL_FITTED_ARTIFACT_PARTITION_MISMATCH")
    # Never index or add unvalidated registry values. A malformed newest
    # candidate must yield a typed blocker and allow an older valid fallback.
    if not counts_valid or (
        type(artifact.get("train_end_index")) is not int
        or artifact.get("train_end_index") != stored_counts[0]
        or type(artifact.get("calibration_end_index")) is not int
        or artifact.get("calibration_end_index") != stored_counts[0] + stored_counts[1]
    ):
        blockers.append("NHL_FITTED_ARTIFACT_SPLIT_INDEX_MISMATCH")
    if metrics.get("market_features_used") is not False:
        blockers.append("NHL_FITTED_MARKET_FEATURES_FORBIDDEN")
    if metrics.get("historical_reconstruction") is not True:
        blockers.append("NHL_FITTED_HISTORICAL_RECONSTRUCTION_NOT_PROVEN")

    method = str(calibrator.get("method") or "")
    if method not in {IDENTITY_CALIBRATION_METHOD, EMPIRICAL_CALIBRATION_METHOD}:
        blockers.append("NHL_FITTED_CALIBRATOR_METHOD_UNSUPPORTED")
    calibration_n = calibrator.get("training_n")
    if type(calibration_n) is not int or calibration_n < 30:
        blockers.append("NHL_FITTED_CALIBRATOR_SAMPLE_INSUFFICIENT")
    if method == EMPIRICAL_CALIBRATION_METHOD:
        bins = calibrator.get("bins")
        if not isinstance(bins, list) or not bins:
            blockers.append("NHL_FITTED_CALIBRATOR_BINS_MISSING")
        else:
            for entry in bins:
                if not isinstance(entry, Mapping):
                    blockers.append("NHL_FITTED_CALIBRATOR_BINS_INVALID")
                    break
                if (
                    _probability(entry.get("calibrated_probability")) is None
                    or _probability(entry.get("wilson_lower")) is None
                    or _probability(entry.get("wilson_upper")) is None
                    or (_number(entry.get("raw_mean")) is None)
                ):
                    blockers.append("NHL_FITTED_CALIBRATOR_BINS_INVALID")
                    break

    created_at = _aware(row.get("created_at"))
    if created_at is None:
        blockers.append("NHL_FITTED_ARTIFACT_CREATED_AT_INVALID")

    if blockers:
        raise NHLFittedSpecialistError(
            "MODEL_INPUTS_INSUFFICIENT",
            sorted(set(blockers)),
            {"candidate_id": row.get("candidate_id")},
        )
    return NHLFittedCandidate(
        candidate_id=str(row.get("candidate_id") or ""),
        model_version=str(row.get("model_artifact_version") or ""),
        model_family=str(row.get("model_family") or ""),
        dataset_hash=dataset_hash,
        training_code_sha=training_code_sha,
        artifact_checksum=checksum,
        created_at=created_at,
        artifact=dict(artifact),
        calibrator=dict(calibrator),
        metrics=dict(metrics),
        training_rows=int(stored_counts[0]),
        calibration_rows=int(stored_counts[1]),
        test_rows=int(stored_counts[2]),
        research_screen_pass=True,
        source_review_status=str(row.get("source_review_status") or "REQUIRED").upper(),
    )


def load_nhl_fitted_candidate(
    db: Any,
    *,
    as_of_utc: datetime | None = None,
) -> NHLFittedCandidate:
    """Resolve the newest eligible fitted NHL candidate from the governed registry.

    This is the only artifact-resolution path. Request evidence cannot override
    it, and a malformed row is skipped with its typed blockers reported when no
    eligible row remains.
    """
    if db is None:
        raise NHLFittedSpecialistError(
            "MODEL_UNAVAILABLE", ["NHL_FITTED_REGISTRY_UNAVAILABLE"]
        )
    as_of = as_of_utc or datetime.now(timezone.utc)
    if as_of.tzinfo is None:
        raise ValueError("as_of_utc must be timezone-aware")
    result = (
        db.table(CANDIDATE_TABLE)
        .select(
            "candidate_id,created_at,sport,league,model_family,model_artifact_version,"
            "feature_schema_version,source_policy_id,training_dataset_hash,"
            "training_code_sha,artifact_checksum,artifact_payload,calibrator_payload,"
            "validation_metrics,training_rows,calibration_rows,test_rows,"
            "research_screen_pass,source_review_status,lifecycle_state,promoted,"
            "active,probability_publishable,can_execute"
        )
        .eq("sport", "NHL")
        .eq("model_family", MODEL_FAMILY)
        .eq("lifecycle_state", "CANDIDATE")
        .eq("promoted", False)
        .eq("active", False)
        .eq("can_execute", False)
        .order("created_at", desc=True)
        .limit(25)
        .execute()
    )
    rows = [dict(item) for item in (getattr(result, "data", None) or []) if isinstance(item, Mapping)]
    rejected: list[str] = []
    for row in rows:
        created_at = _aware(row.get("created_at"))
        if created_at is None or created_at > as_of + timedelta(minutes=5):
            rejected.append("NHL_FITTED_ARTIFACT_NOT_AVAILABLE_AS_OF")
            continue
        try:
            return _verify_candidate(row)
        except NHLFittedSpecialistError as exc:
            rejected.extend(exc.blockers)
    raise NHLFittedSpecialistError(
        "MODEL_INPUTS_INSUFFICIENT",
        rejected or ["NHL_FITTED_ARTIFACT_UNAVAILABLE"],
        {"rows_examined": len(rows)},
    )


def _project_source_event(row: Mapping[str, Any]) -> dict[str, Any]:
    """Project one source-event row into the reconstruction identity."""
    blockers: list[str] = []
    event_id = str(row.get("official_event_id") or "").strip()
    start = _aware(row.get("event_start_time"))
    home = str(row.get("home_participant") or "").strip().upper()
    away = str(row.get("away_participant") or "").strip().upper()
    outcome = row.get("outcome_json")
    if not isinstance(outcome, Mapping):
        blockers.append("NHL_SOURCE_OUTCOME_MISSING")
        outcome = {}
    home_score = outcome.get("home_score")
    away_score = outcome.get("away_score")
    season_value = str(row.get("season") or "").strip()
    if not season_value.isdigit():
        blockers.append("NHL_SOURCE_SEASON_UNVERIFIED")
    if not event_id or start is None or not home or not away or home == away:
        blockers.append("NHL_SOURCE_EVENT_IDENTITY_INVALID")
    if type(home_score) is not int or type(away_score) is not int or home_score < 0 or away_score < 0:
        blockers.append("NHL_SOURCE_SCORE_INVALID")
    if type(home_score) is int and type(away_score) is int and home_score == away_score:
        blockers.append("NHL_SOURCE_TIE_UNSUPPORTED")
    if outcome.get("positive_outcome") is not None and type(outcome.get("positive_outcome")) is not bool:
        blockers.append("NHL_SOURCE_OUTCOME_INVALID")
    game_type = outcome.get("game_type")
    neutral_site = outcome.get("neutral_site")
    game_state = str(outcome.get("game_state") or "").upper()
    provenance_version = str(outcome.get("provenance_version") or "")
    if type(game_type) is not int or game_type != 2:
        blockers.append("NHL_SOURCE_GAME_TYPE_UNVERIFIED")
    if type(neutral_site) is not bool:
        blockers.append("NHL_SOURCE_NEUTRAL_SITE_UNVERIFIED")
    if game_state not in {"FINAL", "OFF"}:
        blockers.append("NHL_SOURCE_GAME_STATE_UNVERIFIED")
    commitment = str(outcome.get("source_payload_commitment") or "").strip().lower()
    hash_scope = str(outcome.get("payload_hash_scope") or "")
    if provenance_version != SOURCE_PROVENANCE_VERSION:
        blockers.append("NHL_SOURCE_PROVENANCE_UNVERIFIED")
    if not _hash64(commitment):
        blockers.append("NHL_SOURCE_PAYLOAD_COMMITMENT_INVALID")
    if hash_scope != SOURCE_PAYLOAD_HASH_SCOPE:
        blockers.append("NHL_SOURCE_PAYLOAD_HASH_SCOPE_UNVERIFIED")
    if not _hash64(row.get("source_payload_sha256")):
        blockers.append("NHL_SOURCE_PAYLOAD_HASH_INVALID")
    if str(row.get("source_provider") or "") != SOURCE_POLICY_ID:
        blockers.append("NHL_SOURCE_PROVIDER_UNVERIFIED")
    if not str(row.get("source_uri") or "").startswith(
        "https://api-web.nhle.com/v1/club-schedule-season/"
    ):
        blockers.append("NHL_SOURCE_URI_UNVERIFIED")
    if not blockers:
        expected_hash = provenance_envelope_hash(
            official_event_id=event_id,
            season=season_value,
            event_start_time=start.isoformat(),
            home_team=home,
            away_team=away,
            home_score=int(home_score),
            away_score=int(away_score),
            game_type=game_type,
            game_state=game_state,
            neutral_site=neutral_site,
        )
        if expected_hash != str(row.get("source_payload_sha256") or "").strip().lower():
            blockers.append("NHL_SOURCE_PAYLOAD_HASH_MISMATCH")
    if blockers:
        raise NHLFittedSpecialistError("MODEL_INPUTS_INSUFFICIENT", blockers, {"event_id": event_id})
    return {
        "event_id": event_id,
        "start": start,
        "home": home,
        "away": away,
        "home_score": int(home_score),
        "away_score": int(away_score),
        "neutral_site": neutral_site,
        "game_type": game_type,
        "game_state": game_state,
        "provenance_version": provenance_version,
        "source_payload_sha256": str(row["source_payload_sha256"]).lower(),
    }


def load_nhl_season_events(db: Any, *, season: int) -> list[dict[str, Any]]:
    """Load the settled first-party NHL timeline for one season, paged."""
    if db is None:
        raise NHLFittedSpecialistError(
            "MODEL_UNAVAILABLE", ["NHL_FITTED_REGISTRY_UNAVAILABLE"]
        )
    columns = (
        "official_event_id,season,event_start_time,home_participant,away_participant,"
        "outcome_json,source_provider,source_uri,source_payload_sha256"
    )
    collected: list[dict[str, Any]] = []
    offset = 0
    while True:
        query = (
            db.table(SOURCE_TABLE)
            .select(columns)
            .eq("sport", "NHL")
            .eq("season", str(season))
            .order("event_start_time")
            .order("official_event_id")
            .order("source_payload_sha256")
        )
        page_method = getattr(query, "range", None)
        if callable(page_method):
            result = page_method(offset, offset + _PAGE_SIZE - 1).execute()
        else:  # narrow test doubles
            result = query.limit(_PAGE_SIZE).execute()
        batch = [dict(item) for item in (getattr(result, "data", None) or []) if isinstance(item, Mapping)]
        collected.extend(batch)
        if len(batch) < _PAGE_SIZE:
            break
        offset += _PAGE_SIZE
        if offset > _MAX_SEASON_EVENTS:
            raise NHLFittedSpecialistError(
                "MODEL_INPUTS_INSUFFICIENT", ["NHL_SOURCE_SEASON_TIMELINE_TOO_LARGE"]
            )
    return collected


def _recent_metrics(recent: deque[tuple[int, int, int]]) -> tuple[float, float, float, float]:
    """Aggregate recent win and goal differentials for form features."""
    n = len(recent)
    wins = sum(item[0] for item in recent)
    goals_for = sum(item[1] for item in recent)
    goals_against = sum(item[2] for item in recent)
    return wins / n, (goals_for - goals_against) / n, goals_for / n, goals_against / n


def _season_start_year(event_time: datetime) -> int:
    """Derive the NHL season start year from an event timestamp."""
    return event_time.year if event_time.month >= 7 else event_time.year - 1


def _season_id(start_year: int) -> int:
    return int(f"{int(start_year)}{int(start_year) + 1}")


def _supplied_season(value: Any) -> int | None:
    """Return an exact numeric season id, or None for malformed evidence."""
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


def _validated_target_season(value: Any, event_start: datetime) -> int:
    """Require caller season evidence to match the event-derived season."""
    target = _season_id(_season_start_year(event_start))
    if value in (None, ""):
        return target
    if _supplied_season(value) != target:
        raise NHLFittedSpecialistError(
            "MODEL_INPUTS_INSUFFICIENT",
            ["NHL_SEASON_IDENTITY_INVALID"],
        )
    return target


def reconstruct_nhl_pregame_features(
    events: Iterable[Mapping[str, Any]],
    *,
    official_event_id: str,
    event_start_time: Any,
    home_team: str,
    away_team: str,
    season: int | str | None = None,
    neutral_site: bool = False,
) -> NHLFeatureReconstruction:
    """Replay prior settled games to the exact training feature vector.

    The state machine mirrors ``nhl_candidate_pipeline.reconstruct_training_rows``:
    per-season team state, Elo K=20, a 10-game recent window, and rest computed
    from the previous game start. The target event must not already be settled.
    """
    event_id = str(official_event_id or "").strip()
    target_start = _aware(event_start_time)
    home = team_abbreviation(home_team)
    away = team_abbreviation(away_team)
    blockers: list[str] = []
    if not re.fullmatch(r"\d{10}", event_id):
        blockers.append("NHL_EVENT_IDENTITY_UNVERIFIED")
    if target_start is None:
        blockers.append("NHL_EVENT_TIME_INVALID")
        target_start = datetime.now(timezone.utc)
    if not home or not away or home == away:
        blockers.append("NHL_TEAM_IDENTITY_UNVERIFIED")
    expected_season = _season_id(_season_start_year(target_start))
    if season not in (None, "") and _supplied_season(season) != expected_season:
        blockers.append("NHL_SEASON_IDENTITY_INVALID")
    # Source-row persistence does not retain the raw neutral-site flag. Only an
    # explicit boolean may define the target feature; it is never inferred.
    if type(neutral_site) is not bool:
        blockers.append("NHL_NEUTRAL_SITE_UNVERIFIED")
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for raw in events:
        if not isinstance(raw, Mapping):
            blockers.append("NHL_SOURCE_EVENT_INVALID")
            continue
        grouped.setdefault(str(raw.get("official_event_id") or ""), []).append(raw)
    projected: dict[str, dict[str, Any]] = {}
    for raw_rows in grouped.values():
        provenanced = [
            raw for raw in raw_rows
            if isinstance(raw.get("outcome_json"), Mapping)
            and raw["outcome_json"].get("provenance_version") == SOURCE_PROVENANCE_VERSION
        ]
        # One authoritative V2 row supersedes a legacy sibling for the same
        # event. Without a V2 sibling the legacy row is still projected so its
        # typed provenance blocker is preserved rather than silently ignored.
        for raw in (provenanced or raw_rows):
            event = _project_source_event(raw)
            existing = projected.get(event["event_id"])
            if existing is not None:
                identity = (existing["start"], existing["home"], existing["away"],
                            existing["home_score"], existing["away_score"])
                candidate_identity = (event["start"], event["home"], event["away"],
                                      event["home_score"], event["away_score"])
                if identity != candidate_identity:
                    raise NHLFittedSpecialistError(
                        "MODEL_INPUTS_INSUFFICIENT",
                        ["NHL_SOURCE_EVENT_IDENTITY_CONFLICT"],
                        {"event_id": event["event_id"]},
                    )
                # A game appears in both clubs' schedules. Pick the smallest
                # payload hash so the timeline hash is reproducible across DB
                # row order.
                if event["source_payload_sha256"] < existing["source_payload_sha256"]:
                    projected[event["event_id"]] = event
                continue
            projected[event["event_id"]] = event
    if event_id in projected:
        raise NHLFittedSpecialistError(
            "MODEL_OUTPUT_INVALID",
            ["NHL_EVENT_ALREADY_SETTLED"],
            {"event_id": event_id},
        )
    if blockers:
        raise NHLFittedSpecialistError("MODEL_INPUTS_INSUFFICIENT", blockers, {"event_id": event_id})

    ordered = sorted(projected.values(), key=lambda item: (item["start"], item["event_id"]))
    states: dict[str, dict[str, Any]] = {}

    def state(team: str) -> dict[str, Any]:
        return states.setdefault(
            team,
            {
                "elo": ELO_INITIAL,
                "wins": 0,
                "games": 0,
                "recent": deque(maxlen=RECENT_WINDOW),
                "last_start": None,
            },
        )

    prior_count = 0
    timeline: list[str] = []
    for game in ordered:
        if game["start"] >= target_start:
            continue
        home_state = state(game["home"])
        away_state = state(game["away"])
        home_win = game["home_score"] > game["away_score"]
        expected_home = 1.0 / (1.0 + math.pow(10.0, (away_state["elo"] - home_state["elo"]) / 400.0))
        delta = ELO_K * ((1.0 if home_win else 0.0) - expected_home)
        home_state["elo"] += delta
        away_state["elo"] -= delta
        home_state["wins"] += 1 if home_win else 0
        away_state["wins"] += 0 if home_win else 1
        home_state["games"] += 1
        away_state["games"] += 1
        home_state["recent"].append((1 if home_win else 0, game["home_score"], game["away_score"]))
        away_state["recent"].append((0 if home_win else 1, game["away_score"], game["home_score"]))
        home_state["last_start"] = game["start"]
        away_state["last_start"] = game["start"]
        prior_count += 1
        timeline.append(
            "|".join(
                [
                    game["event_id"],
                    game["start"].isoformat(),
                    game["home"],
                    game["away"],
                    str(game["home_score"]),
                    str(game["away_score"]),
                    game["source_payload_sha256"],
                ]
            )
        )

    home_state = state(str(home))
    away_state = state(str(away))
    if home_state["games"] < MIN_PRIOR_GAMES or away_state["games"] < MIN_PRIOR_GAMES:
        raise NHLFittedSpecialistError(
            "MODEL_INPUTS_INSUFFICIENT",
            ["NHL_PRIOR_GAMES_INSUFFICIENT"],
            {
                "home_prior_games": home_state["games"],
                "away_prior_games": away_state["games"],
                "minimum": MIN_PRIOR_GAMES,
            },
        )
    home_rest = (target_start - home_state["last_start"]).total_seconds() / 86400.0
    away_rest = (target_start - away_state["last_start"]).total_seconds() / 86400.0
    if home_rest <= 0 or away_rest <= 0:
        raise NHLFittedSpecialistError(
            "MODEL_INPUTS_INSUFFICIENT", ["NHL_CHRONOLOGY_INVALID"]
        )
    h_win10, h_gd10, h_gf10, h_ga10 = _recent_metrics(home_state["recent"])
    a_win10, a_gd10, a_gf10, a_ga10 = _recent_metrics(away_state["recent"])
    features = {
        "elo_delta": home_state["elo"] - away_state["elo"],
        "win_pct_l10_delta": h_win10 - a_win10,
        "goal_diff_l10_delta": h_gd10 - a_gd10,
        "goals_for_l10_delta": h_gf10 - a_gf10,
        "goals_against_l10_delta": h_ga10 - a_ga10,
        "season_win_pct_delta": (home_state["wins"] / home_state["games"])
        - (away_state["wins"] / away_state["games"]),
        "rest_days_delta": min(home_rest, 14.0) - min(away_rest, 14.0),
        "home_back_to_back": 1.0 if home_rest <= 1.5 else 0.0,
        "away_back_to_back": 1.0 if away_rest <= 1.5 else 0.0,
        "neutral_site": 1.0 if neutral_site else 0.0,
    }
    return NHLFeatureReconstruction(
        season=int(expected_season),
        official_event_id=event_id,
        event_start_time=target_start,
        feature_as_of=target_start - timedelta(seconds=1),
        home_team=str(home),
        away_team=str(away),
        features=features,
        home_prior_games=int(home_state["games"]),
        away_prior_games=int(away_state["games"]),
        prior_event_count=prior_count,
        timeline_hash=_sha256(timeline),
    )


def _apply_calibrator(raw: float, calibrator: Mapping[str, Any]) -> tuple[float, float | None, float | None]:
    """Apply a candidate calibrator to a raw model probability."""
    method = str(calibrator.get("method") or "")
    if method == IDENTITY_CALIBRATION_METHOD:
        return raw, None, None
    if method != EMPIRICAL_CALIBRATION_METHOD:
        raise NHLFittedSpecialistError(
            "MODEL_INPUTS_INSUFFICIENT", ["NHL_FITTED_CALIBRATOR_METHOD_UNSUPPORTED"]
        )
    bins = [entry for entry in (calibrator.get("bins") or []) if isinstance(entry, Mapping)]
    if not bins:
        raise NHLFittedSpecialistError(
            "MODEL_INPUTS_INSUFFICIENT", ["NHL_FITTED_CALIBRATOR_BINS_MISSING"]
        )
    chosen = min(bins, key=lambda entry: abs(float(entry["raw_mean"]) - raw))
    point = _probability(chosen.get("calibrated_probability"))
    lower = _probability(chosen.get("wilson_lower"))
    upper = _probability(chosen.get("wilson_upper"))
    if point is None or lower is None or upper is None:
        raise NHLFittedSpecialistError(
            "MODEL_OUTPUT_INVALID", ["NHL_FITTED_CALIBRATOR_BINS_INVALID"]
        )
    return point, min(lower, point), max(upper, point)


def score_nhl_fitted_candidate(
    candidate: NHLFittedCandidate,
    reconstruction: NHLFeatureReconstruction,
    *,
    event_key: str,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Score one pregame event with the exact fitted artifact and calibrator."""
    for name in FEATURE_NAMES:
        if _number(reconstruction.features.get(name)) is None:
            raise NHLFittedSpecialistError(
                "MODEL_INPUTS_INSUFFICIENT", [f"NHL_FITTED_FEATURE_INVALID:{name}"]
            )
    artifact = candidate.artifact
    z = float(artifact["intercept"])
    for index, name in enumerate(FEATURE_NAMES):
        z += float(artifact["coefficients"][index]) * (
            (float(reconstruction.features[name]) - float(artifact["scaler_mean"][index]))
            / float(artifact["scaler_scale"][index])
        )
    raw_home = min(max(_sigmoid(z), 1e-6), 1.0 - 1e-6)
    calibrated_home, home_lower, home_upper = _apply_calibrator(raw_home, candidate.calibrator)
    calibrated_home = min(max(calibrated_home, 1e-6), 1.0 - 1e-6)
    calibrated_away = 1.0 - calibrated_home
    blockers: list[str] = []
    if home_lower is None or home_upper is None:
        away_lower = away_upper = None
        blockers.append("NHL_CALIBRATED_LOWER_BOUND_UNAVAILABLE")
        code = "MODEL_INPUTS_INSUFFICIENT"
    else:
        away_lower = min(max(1.0 - home_upper, 1e-6), 1.0 - 1e-6)
        away_upper = min(max(1.0 - home_lower, 1e-6), 1.0 - 1e-6)
        code = "SPORTING_PROBABILITY_SCORED_CANDIDATE"
    if home_lower is None:
        side = "home" if calibrated_home >= calibrated_away else "away"
    else:
        side = "home" if home_lower >= (away_lower or 0.0) else "away"
    selected = reconstruction.home_team if side == "home" else reconstruction.away_team
    selected_probability = calibrated_home if side == "home" else calibrated_away
    selected_lower = home_lower if side == "home" else away_lower
    selected_upper = home_upper if side == "home" else away_upper
    package = {
        "sport": "NHL",
        "league": "NHL",
        "candidate_family": "TEAM_EVENT",
        "controlling_specialist": "NHL_GAME_WIN_PROBABILITY_EXPERT_V1",
        "model_family": candidate.model_family,
        "model_version": candidate.model_version,
        "model_artifact_version": candidate.model_version,
        "model_artifact_checksum": candidate.artifact_checksum,
        "training_dataset_hash": candidate.dataset_hash,
        "training_code_sha": candidate.training_code_sha,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "source_policy_id": SOURCE_POLICY_ID,
        "official_event_id": reconstruction.official_event_id,
        "event_key": event_key,
        "event_start_time_utc": reconstruction.event_start_time.isoformat(),
        "home_team": reconstruction.home_team,
        "away_team": reconstruction.away_team,
        "selected_participant": selected,
        "selected_calibrated_probability": round(selected_probability, 6),
        "selected_calibrated_lower_bound": None if selected_lower is None else round(selected_lower, 6),
        "selected_calibrated_upper_bound": None if selected_upper is None else round(selected_upper, 6),
        "raw_home_probability": round(raw_home, 6),
        "raw_away_probability": round(1.0 - raw_home, 6),
        "calibrated_probability": round(selected_probability, 6),
        "calibrated_lower_bound": None if selected_lower is None else round(selected_lower, 6),
        "calibrated_upper_bound": None if selected_upper is None else round(selected_upper, 6),
        "calibrated_home_probability": round(calibrated_home, 6),
        "calibrated_home_lower_bound": None if home_lower is None else round(home_lower, 6),
        "calibrated_home_upper_bound": None if home_upper is None else round(home_upper, 6),
        "calibrated_away_probability": round(calibrated_away, 6),
        "calibrated_away_lower_bound": None if away_lower is None else round(away_lower, 6),
        "calibrated_away_upper_bound": None if away_upper is None else round(away_upper, 6),
        "calibration_method": candidate.selected_calibrator_method,
        "calibration_version": str(candidate.calibrator.get("selection_version") or ""),
        "calibration_status": "CANDIDATE",
        "calibration_health_status": (
            "RESEARCH_SCREEN_PASS" if candidate.research_screen_pass else "NOT_PASS"
        ),
        "calibration_history_present": True,
        "calibration_artifact_certified": False,
        "calibration_training_n": int(candidate.calibrator.get("training_n") or 0),
        "calibration_fit_end": candidate.created_at.isoformat(),
        "calibration_brier_score": _number(candidate.metrics.get("calibrated_brier")),
        "calibration_error": _number(candidate.metrics.get("ece")),
        "calibration_artifact_fingerprint": candidate.artifact_checksum,
        "calibration_source_data_hash": candidate.dataset_hash,
        "calibration_split_hash": _sha256(
            {
                "train_end": candidate.training_rows,
                "calibration_end": candidate.training_rows + candidate.calibration_rows,
                "dataset": candidate.dataset_hash,
            }
        ),
        "calibration_sample_scope": "NHL_HISTORICAL_TIME_SPLIT",
        "effective_sample_n": candidate.training_rows + candidate.calibration_rows,
        "immutable_model_timestamp": candidate.created_at.isoformat(),
        "source_snapshot_id": f"NHL_FITTED:{candidate.candidate_id}:{candidate.dataset_hash[:16]}",
        "feature_reconstruction": {
            "season": reconstruction.season,
            "feature_as_of": reconstruction.feature_as_of.isoformat(),
            "home_prior_games": reconstruction.home_prior_games,
            "away_prior_games": reconstruction.away_prior_games,
            "prior_event_count": reconstruction.prior_event_count,
            "timeline_hash": reconstruction.timeline_hash,
            "market_features_used": False,
        },
        "evidence_consumed": {
            key: evidence.get(key)
            for key in (
                "official_event_id",
                "goalie_status",
                "goalie_status_observed_at",
                "goalie_identity_snapshot_id",
                "injury_report",
                "rest_travel",
                "settlement_basis",
            )
        },
        "fitted_registry_scoring": True,
        "code": code,
        "blockers": blockers,
        "probability_publishable": False,
        "rank_eligible": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "event_prediction_id": (
            f"NHL:{candidate.model_version}:{reconstruction.official_event_id}"
        ),
        "can_execute": False,
    }
    return package


def _evidence(req: Any) -> dict[str, Any]:
    """Copy sport-specific evidence from the request into a plain dict."""
    value = getattr(req, "sport_specific_evidence", None)
    return dict(value) if isinstance(value, Mapping) else {}


def score_current_nhl_event(req: Any, *, db: Any = None, now_utc: datetime | None = None) -> dict[str, Any]:
    """Controlling fitted scoring entrypoint for one current pregame NHL event."""
    evidence = _evidence(req)
    now = now_utc or datetime.now(timezone.utc)
    blockers: list[str] = []
    official_id = str(evidence.get("official_event_id") or getattr(req, "official_event_id", "") or "").strip()
    if not re.fullmatch(r"\d{10}", official_id):
        blockers.append("NHL_EVENT_IDENTITY_UNVERIFIED")
    target_start = _aware(getattr(req, "event_start_time_utc", None))
    if target_start is None:
        blockers.append("NHL_EVENT_TIME_INVALID")
    elif target_start <= now:
        blockers.append("NHL_EVENT_NOT_PREGAME")
    evidence_basis = evidence.get("settlement_basis")
    if not isinstance(evidence_basis, str) or evidence_basis not in ACCEPTED_SETTLEMENT_BASES:
        blockers.append("NHL_SETTLEMENT_BASIS_UNVERIFIED")
    request_basis = getattr(req, "settlement_basis", None)
    if request_basis not in (None, ""):
        if (
            not isinstance(request_basis, str)
            or request_basis.strip() not in REQUEST_FULL_GAME_OT_SO_BASES
        ):
            blockers.append("NHL_SETTLEMENT_BASIS_MISMATCH")
    goalie_status = str(evidence.get("goalie_status") or "").strip().upper()
    if goalie_status not in CONFIRMED_GOALIE_STATUSES:
        blockers.append("NHL_STARTING_GOALIE_NOT_CONFIRMED")
    observed_at = _aware(evidence.get("goalie_status_observed_at"))
    if observed_at is None or observed_at > now or (now - observed_at) > MAX_EVIDENCE_AGE:
        blockers.append("NHL_GOALIE_STATUS_STALE")
    if not str(evidence.get("goalie_identity_snapshot_id") or "").strip():
        blockers.append("NHL_GOALIE_IDENTITY_UNVERIFIED")
    for key in ("injury_report", "rest_travel"):
        value = evidence.get(key)
        if not isinstance(value, Mapping) or not value:
            blockers.append(f"NHL_{key.upper()}_UNAVAILABLE")
    if not str(evidence.get("source_snapshot_id") or "").strip():
        blockers.append("NHL_SOURCE_SNAPSHOT_MISSING")
    if blockers:
        raise NHLFittedSpecialistError("MODEL_INPUTS_INSUFFICIENT", sorted(set(blockers)))

    neutral_site = evidence.get("neutral_site")
    if type(neutral_site) is not bool:
        raise NHLFittedSpecialistError(
            "MODEL_INPUTS_INSUFFICIENT", ["NHL_NEUTRAL_SITE_UNVERIFIED"]
        )
    season = _validated_target_season(evidence.get("season"), target_start)
    provider = db if db is not None else _REGISTRY_DB_PROVIDER
    if provider is None:
        raise NHLFittedSpecialistError(
            "MODEL_UNAVAILABLE", ["NHL_FITTED_REGISTRY_UNAVAILABLE"]
        )
    client = provider() if callable(provider) else provider
    candidate = load_nhl_fitted_candidate(client, as_of_utc=now)
    events = load_nhl_season_events(client, season=season)
    reconstruction = reconstruct_nhl_pregame_features(
        events,
        official_event_id=official_id,
        event_start_time=target_start.isoformat(),
        home_team=str(getattr(req, "home_team", "") or ""),
        away_team=str(getattr(req, "away_team", "") or ""),
        season=season,
        neutral_site=neutral_site,
    )
    return score_nhl_fitted_candidate(
        candidate,
        reconstruction,
        event_key=str(getattr(req, "event_key", "") or f"NHL:{official_id}"),
        evidence=evidence,
    )


def _fitted_http_failure(req: Any, exc: NHLFittedSpecialistError):
    """Translate a native fitted-specialist hold into the shared bridge shape."""
    from v17.team_event_bridge_runtime import _model_failure_detail

    status_code = {
        "MODEL_UNAVAILABLE": 409,
        "MODEL_INPUTS_INSUFFICIENT": 422,
        "MODEL_OUTPUT_INVALID": 500,
    }.get(exc.code, 500)
    blocker = exc.blockers[0] if exc.blockers else "NHL_FITTED_SPECIALIST_BLOCKED"
    return _model_failure_detail(
        req,
        code=exc.code,
        blocker=blocker,
        status_code=status_code,
        extra={
            "blockers": list(exc.blockers),
            "model_invoked": exc.code != "MODEL_UNAVAILABLE",
            "detail": exc.detail,
        },
    )


def score_nhl_fitted_team_event(req: Any, **_kwargs: Any) -> dict[str, Any]:
    """Bridge-compatible scorer; extra bridge kwargs are intentionally ignored."""
    try:
        return score_current_nhl_event(req)
    except NHLFittedSpecialistError as exc:
        # The registered bridge calls this function directly rather than going
        # through _score_one, so preserve the native typed blocker here.
        raise _fitted_http_failure(req, exc) from exc


__all__ = [
    "ACCEPTED_SETTLEMENT_BASES",
    "REQUEST_FULL_GAME_OT_SO_BASES",
    "CAN_EXECUTE",
    "CANDIDATE_TABLE",
    "CONFIRMED_GOALIE_STATUSES",
    "FEATURE_NAMES",
    "FEATURE_SCHEMA_VERSION",
    "MODEL_FAMILY",
    "NHLFeatureReconstruction",
    "NHLFittedCandidate",
    "NHLFittedSpecialistError",
    "PROBABILITY_PUBLISHABLE",
    "SOURCE_POLICY_ID",
    "SOURCE_TABLE",
    "install_nhl_fitted_registry_provider",
    "load_nhl_fitted_candidate",
    "load_nhl_season_events",
    "reconstruct_nhl_pregame_features",
    "reset_nhl_fitted_registry_provider",
    "score_current_nhl_event",
    "score_nhl_fitted_candidate",
    "score_nhl_fitted_team_event",
]
