"""NBA candidate forward shadow: score the registered SHADOW candidate pre-tipoff.

Research-only evidence lane (Class B). The certified NBA scorer can only run a
certified specialist, and certification needs prospective evidence, so the
current candidate could never accumulate any. This lane closes that gap the
same way the NFL challenger lane does: it scores the exact candidate named on
``wow_basketball_team_event_certification`` (digest-verified artifact plus its
Platt calibrator) for games tipping off within the lookahead window, using only
settled results already persisted at capture time, and appends one immutable
row per distinct input. The database clock stamps every row and rejects any row
at or after tipoff. Grades come from a fresh-join view over official finals.

Never publishes, ranks, certifies, promotes or executes. It never resolves or
requires a certification, and it never changes any probability, bound,
artifact or flag. can_execute=false.
"""
from __future__ import annotations

from dataclasses import fields
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from typing import Any, Callable, Iterable, Mapping, Sequence

from basketball_team_event_specialist import (
    FEATURE_NAMES,
    FEATURE_SCHEMA_VERSION,
    MODEL_FAMILY,
    LogisticArtifact,
    TrainingGame,
)
from v17.nba_forward_shadow_features import current_feature_values

CAN_EXECUTE = False
SCHEMA_VERSION = "NBA_CANDIDATE_FORWARD_SHADOW_V1"
SELECTION_POLICY = "LATEST_VALID_PRETIPOFF_V1"
TABLE = "wow_nba_candidate_forward_shadow"
VERIFIED_VIEW = "wow_nba_candidate_forward_verified_grades"
CERTIFICATION_TABLE = "wow_basketball_team_event_certification"
ARTIFACT_TABLE = "wow_basketball_team_event_model_artifacts"
CALIBRATOR_TABLE = "wow_basketball_team_event_calibrators"
TRAINING_TABLE = "wow_nba_training_games"
CANDIDATE_STATUSES = frozenset({"SHADOW", "CERTIFIED"})
CALIBRATION_METHOD = "PLATT_TIME_SPLIT_V1"
LOOKAHEAD = timedelta(hours=36)
_CHECKSUM_KEYS = (
    "sport", "model_family", "model_artifact_version", "feature_schema_version",
    "feature_names", "intercept", "coefficients", "means", "scales",
)
_PAGE = 1000
_MAX_PAGES = 64


class NBACandidateForwardShadowError(RuntimeError):
    def __init__(self, code: str, typed_failure: str):
        super().__init__(code)
        self.code = code
        self.typed_failure = typed_failure


def _sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _one(rows: Any, code: str) -> Mapping[str, Any]:
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], Mapping):
        raise NBACandidateForwardShadowError(code, "MODEL_UNAVAILABLE")
    return rows[0]


def _finite(value: Any, code: str) -> float:
    if isinstance(value, bool):
        raise NBACandidateForwardShadowError(code, "MODEL_SCORER_FAILED")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise NBACandidateForwardShadowError(code, "MODEL_SCORER_FAILED") from exc
    if not math.isfinite(parsed):
        raise NBACandidateForwardShadowError(code, "MODEL_SCORER_FAILED")
    return parsed


def load_candidate(db: Any) -> dict[str, Any]:
    """Resolve the exact registered candidate; verify artifact and calibrator."""
    cert = _one(
        db.table(CERTIFICATION_TABLE)
        .select("sport,capability_status,model_artifact_version,calibrator_version")
        .eq("sport", "NBA").execute().data,
        "NBA_CANDIDATE_NOT_REGISTERED",
    )
    status = str(cert.get("capability_status") or "")
    version = str(cert.get("model_artifact_version") or "").strip()
    cal_version = str(cert.get("calibrator_version") or "").strip()
    if status not in CANDIDATE_STATUSES or not version or not cal_version:
        raise NBACandidateForwardShadowError("NBA_CANDIDATE_NOT_REGISTERED", "MODEL_UNAVAILABLE")

    art = _one(
        db.table(ARTIFACT_TABLE)
        .select("model_artifact_version,model_family,feature_schema_version,artifact_payload,artifact_checksum")
        .eq("sport", "NBA").eq("model_artifact_version", version).execute().data,
        "NBA_CANDIDATE_ARTIFACT_NOT_FOUND",
    )
    payload = art.get("artifact_payload")
    checksum = str(art.get("artifact_checksum") or "").strip().lower()
    if (
        not isinstance(payload, Mapping)
        or art.get("model_family") != MODEL_FAMILY
        or art.get("feature_schema_version") != FEATURE_SCHEMA_VERSION
        or payload.get("model_artifact_version") != version
        or tuple(payload.get("feature_names") or ()) != tuple(FEATURE_NAMES)
        or any(key not in payload for key in _CHECKSUM_KEYS)
        or _sha256({key: payload[key] for key in _CHECKSUM_KEYS}) != checksum
    ):
        raise NBACandidateForwardShadowError("NBA_CANDIDATE_ARTIFACT_INTEGRITY_FAILED", "MODEL_SCORER_FAILED")
    try:
        values = {f.name: payload[f.name] for f in fields(LogisticArtifact)}
    except KeyError as exc:
        raise NBACandidateForwardShadowError("NBA_CANDIDATE_ARTIFACT_INTEGRITY_FAILED", "MODEL_SCORER_FAILED") from exc
    for key in ("feature_names", "coefficients", "means", "scales"):
        values[key] = tuple(values[key])
    artifact = LogisticArtifact(**values)

    cal = _one(
        db.table(CALIBRATOR_TABLE)
        .select("calibrator_version,model_artifact_version,calibration_method,coefficients")
        .eq("sport", "NBA").eq("calibrator_version", cal_version)
        .eq("model_artifact_version", version).execute().data,
        "NBA_CANDIDATE_CALIBRATOR_NOT_FOUND",
    )
    coefficients = cal.get("coefficients")
    if cal.get("calibration_method") != CALIBRATION_METHOD or not isinstance(coefficients, Mapping):
        raise NBACandidateForwardShadowError("NBA_CANDIDATE_CALIBRATOR_INVALID", "MODEL_SCORER_FAILED")
    return {
        "capability_status": status,
        "artifact": artifact,
        "model_artifact_version": version,
        "model_artifact_checksum": checksum,
        "calibrator_version": cal_version,
        "calibration_method": CALIBRATION_METHOD,
        "platt_a": _finite(coefficients.get("a"), "NBA_CANDIDATE_CALIBRATOR_INVALID"),
        "platt_b": _finite(coefficients.get("b"), "NBA_CANDIDATE_CALIBRATOR_INVALID"),
    }


def score(candidate: Mapping[str, Any], values: Sequence[float]) -> tuple[float, float]:
    """Raw and Platt-calibrated home-win probability (same math as the holdout)."""
    raw = float(candidate["artifact"].raw_probability(values))
    if not math.isfinite(raw) or not 0.0 < raw < 1.0:
        raise NBACandidateForwardShadowError("NBA_CANDIDATE_RAW_PROBABILITY_INVALID", "MODEL_OUTPUT_INVALID")
    clipped = min(max(raw, 1e-12), 1.0 - 1e-12)
    z = candidate["platt_a"] + candidate["platt_b"] * math.log(clipped / (1.0 - clipped))
    p = 1.0 / (1.0 + math.exp(-z))
    if not math.isfinite(p) or not 0.0 < p < 1.0:
        raise NBACandidateForwardShadowError("NBA_CANDIDATE_CALIBRATED_PROBABILITY_INVALID", "MODEL_OUTPUT_INVALID")
    return raw, p


def _aware(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.utcoffset() is not None else None


def upcoming_events(
    schedule_rows: Iterable[Mapping[str, Any]], *, now: datetime, lookahead: timedelta = LOOKAHEAD,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Scheduled, not-completed games with tipoff in (now, now + lookahead]."""
    events: dict[str, dict[str, Any]] = {}
    holds: dict[str, int] = {}
    conflicted: set[str] = set()

    def hold(code: str) -> None:
        holds[code] = holds.get(code, 0) + 1

    for row in schedule_rows:
        if not isinstance(row, Mapping):
            continue
        start = _aware(row.get("game_date_time"))
        utc_start = _aware(row.get("start_date")) if row.get("start_date") else start
        if start is None or utc_start is None:
            hold("SCHEDULE_START_INVALID")
            continue
        if not (now < start <= now + lookahead):
            continue
        game_id = str(row.get("game_id") or row.get("id") or "").strip()
        home = str(row.get("home_id") or "").strip()
        away = str(row.get("away_id") or "").strip()
        local_date = str(row.get("game_date") or "")[:10]
        if utc_start != start:
            hold("SCHEDULE_START_CONFLICT")
            conflicted.add(f"espn-{game_id}")
            continue
        if str(row.get("status_type_completed") or "").strip().lower() == "true":
            continue
        try:
            game_date = date.fromisoformat(local_date)
        except ValueError:
            hold("SCHEDULE_GAME_DATE_INVALID")
            continue
        if not (game_id.isdigit() and home.isdigit() and away.isdigit()) or home == away:
            hold("SCHEDULE_IDENTITY_INVALID")
            continue
        event = {
            "official_event_id": f"espn-{game_id}",
            "event_game_date": game_date,
            "event_start_time_utc": start,
            "home_team_id": f"espn-{home}",
            "away_team_id": f"espn-{away}",
            "source_snapshot_id": _sha256({
                "game_id": game_id, "game_date": local_date, "start": start.isoformat(),
                "home": home, "away": away, "status": row.get("status_type_name"),
                "observed_at": row.get("_wow_source_observed_at"),
            }),
            "source_observed_at": _aware(row.get("_wow_source_observed_at")) or now,
        }
        previous = events.get(event["official_event_id"])
        if previous is not None and previous["source_snapshot_id"] != event["source_snapshot_id"]:
            conflicted.add(event["official_event_id"])
        events[event["official_event_id"]] = event
    for event_id in sorted(conflicted):
        events.pop(event_id, None)
        hold("SCHEDULE_EVENT_AMBIGUOUS")
    return sorted(events.values(), key=lambda e: (e["event_start_time_utc"], e["official_event_id"])), holds


def load_settled_games(db: Any) -> list[tuple[TrainingGame, datetime]]:
    """Every persisted settled NBA final with the instant WOW first stored it."""
    out: list[tuple[TrainingGame, datetime]] = []
    seen: set[str] = set()
    for page in range(_MAX_PAGES):
        batch = list(
            db.table(TRAINING_TABLE)
            .select("game_id,season,game_date,home_team_id,away_team_id,home_score,away_score,source_provider,created_at")
            .eq("settled", True).order("game_id")
            .range(page * _PAGE, page * _PAGE + _PAGE - 1).execute().data or []
        )
        for r in batch:
            gid = str(r.get("game_id") or "")
            stored = _aware(r.get("created_at"))
            if not gid or gid in seen or stored is None:
                raise NBACandidateForwardShadowError("NBA_TRAINING_HISTORY_INVALID", "MODEL_INPUTS_INSUFFICIENT")
            seen.add(gid)
            out.append((TrainingGame(
                game_id=gid, sport="NBA", season=int(r["season"]),
                game_date=date.fromisoformat(str(r["game_date"])[:10]),
                home_team_id=str(r["home_team_id"]), away_team_id=str(r["away_team_id"]),
                home_score=int(r["home_score"]), away_score=int(r["away_score"]),
                source_provider=str(r.get("source_provider") or "").strip().upper(),
            ), stored))
        if len(batch) < _PAGE:
            return out
    raise NBACandidateForwardShadowError("NBA_TRAINING_HISTORY_PAGE_LIMIT", "MODEL_INPUTS_INSUFFICIENT")


def build_capture_rows(
    candidate: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    settled: Sequence[tuple[TrainingGame, datetime]],
    *,
    knowledge_cutoff: datetime,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Feature + score each event from finals stored at or before the cutoff.

    Features use the provider's local game date, exactly as training does, so
    same-night games and rest days match the fitted definition.
    """
    known = [game for game, stored in settled if stored <= knowledge_cutoff]
    rows: list[dict[str, Any]] = []
    holds: dict[str, int] = {}
    for event in events:
        if knowledge_cutoff >= event["event_start_time_utc"]:
            holds["NOT_PRE_TIPOFF"] = holds.get("NOT_PRE_TIPOFF", 0) + 1
            continue
        teams = {event["home_team_id"], event["away_team_id"]}
        used = sorted(
            (g.game_id, g.game_date.isoformat(), g.home_team_id, g.away_team_id, g.home_score, g.away_score)
            for g in known if g.game_date < event["event_game_date"] and teams & {g.home_team_id, g.away_team_id}
        )
        try:
            values = current_feature_values(known, event["home_team_id"], event["away_team_id"], event["event_game_date"])
        except Exception:  # noqa: BLE001 - tied/invalid settled history
            values = None
        if values is None:
            holds["MODEL_INPUTS_INSUFFICIENT"] = holds.get("MODEL_INPUTS_INSUFFICIENT", 0) + 1
            continue
        features = {name: float(v) for name, v in zip(FEATURE_NAMES, values)}
        raw, p = score(candidate, values)
        rows.append({
            "schema_version": SCHEMA_VERSION,
            "official_event_id": event["official_event_id"],
            "event_game_date": event["event_game_date"].isoformat(),
            "event_start_time_utc": event["event_start_time_utc"].isoformat(),
            "home_team_id": event["home_team_id"],
            "away_team_id": event["away_team_id"],
            "model_artifact_version": candidate["model_artifact_version"],
            "model_artifact_checksum": candidate["model_artifact_checksum"],
            "calibrator_version": candidate["calibrator_version"],
            "calibration_method": candidate["calibration_method"],
            "candidate_capability_status": candidate["capability_status"],
            "raw_home_win_probability": raw,
            "home_win_probability": p,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "features": features,
            "feature_payload_sha256": _sha256({
                "id": event["official_event_id"], "home": event["home_team_id"],
                "away": event["away_team_id"], "features": features,
            }),
            "dataset_id": _sha256(used),
            "history_max_game_date": max((u[1] for u in used), default=None),
            "source_snapshot_id": event["source_snapshot_id"],
            "source_observed_at": min(event["source_observed_at"], knowledge_cutoff).isoformat(),
            "knowledge_cutoff": knowledge_cutoff.isoformat(),
            "probability_publishable": False,
            "rank_eligible": False,
            "can_execute": False,
        })
    return rows, holds


def _existing_keys(db: Any, event_ids: Sequence[str]) -> set[tuple[str, str, str, str]]:
    if not event_ids:
        return set()
    data = (
        db.table(TABLE)
        .select("official_event_id,model_artifact_version,feature_payload_sha256,dataset_id")
        .in_("official_event_id", list(event_ids)).execute().data or []
    )
    return {
        (r["official_event_id"], r["model_artifact_version"], r["feature_payload_sha256"], r["dataset_id"])
        for r in data
    }


def capture(
    db: Any,
    *,
    schedule_rows_fn: Callable[[int], Iterable[Mapping[str, Any]]] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    now_fn = clock or (lambda: datetime.now(timezone.utc))
    now = now_fn()
    candidate = load_candidate(db)
    if schedule_rows_fn is None:
        from v17.nba_event_identity import _schedule_rows
        schedule_rows_fn = _schedule_rows
    from v17.nba_event_identity import schedule_season_year
    years = sorted({schedule_season_year(now), schedule_season_year(now + LOOKAHEAD)})
    schedule = [row for year in years for row in schedule_rows_fn(year)]
    events, holds = upcoming_events(schedule, now=now)
    settled = load_settled_games(db) if events else []
    # The knowledge cutoff is taken after history is read, so every included
    # final was stored before it and nothing stored later can be used.
    cutoff = now_fn()
    rows, feature_holds = build_capture_rows(candidate, events, settled, knowledge_cutoff=cutoff)
    for code, n in feature_holds.items():
        holds[code] = holds.get(code, 0) + n
    existing = _existing_keys(db, [r["official_event_id"] for r in rows])
    new = [
        r for r in rows
        if (r["official_event_id"], r["model_artifact_version"], r["feature_payload_sha256"], r["dataset_id"])
        not in existing
    ]
    if new:
        db.table(TABLE).insert(new).execute()
    return {
        "status": "COMPLETED",
        "model_artifact_version": candidate["model_artifact_version"],
        "candidate_capability_status": candidate["capability_status"],
        "events_in_window": len(events),
        "rows_scored": len(rows),
        "rows_inserted": len(new),
        "rows_already_captured": len(rows) - len(new),
        "holds": dict(sorted(holds.items())),
        "knowledge_cutoff": cutoff.isoformat(),
    }


def scorecard(db: Any) -> dict[str, Any]:
    """Unique-event scorecard over the fresh-join verified view (diagnostic)."""
    rows: list[Mapping[str, Any]] = []
    for page in range(_MAX_PAGES):
        batch = list(
            db.table(VERIFIED_VIEW)
            .select("official_event_id,home_win_probability,outcome")
            .order("official_event_id")
            .range(page * _PAGE, page * _PAGE + _PAGE - 1).execute().data or []
        )
        rows.extend(batch)
        if len(batch) < _PAGE:
            break
    ids = [r.get("official_event_id") for r in rows]
    if len(ids) != len(set(ids)):
        raise NBACandidateForwardShadowError("NBA_CANDIDATE_GRADE_EVENT_DUPLICATED", "MODEL_OUTPUT_INVALID")
    n = len(rows)
    if n == 0:
        return {"selection_policy": SELECTION_POLICY, "graded_n": 0}
    ps = [float(r["home_win_probability"]) for r in rows]
    ys = [int(r["outcome"]) for r in rows]
    ece = 0.0
    for b in range(10):
        bucket = [(p, y) for p, y in zip(ps, ys) if b / 10 <= p < (b + 1) / 10]
        if bucket:
            ece += len(bucket) / n * abs(sum(p for p, _ in bucket) / len(bucket) - sum(y for _, y in bucket) / len(bucket))
    return {
        "selection_policy": SELECTION_POLICY,
        "graded_n": n,
        "brier": sum((p - y) ** 2 for p, y in zip(ps, ys)) / n,
        "log_loss": sum(-math.log(p if y else 1 - p) for p, y in zip(ps, ys)) / n,
        "mean_home_win_probability": sum(ps) / n,
        "observed_home_win_rate": sum(ys) / n,
        "ece_10_bin": ece,
    }


def run_nba_candidate_forward_shadow(db: Any, **kwargs: Any) -> dict[str, Any]:
    """Capture then score; each stage fails typed and in isolation."""
    out: dict[str, Any] = {
        "lane": "NBA_CANDIDATE_FORWARD_SHADOW",
        "research_only": True,
        "certification_admission_source": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": CAN_EXECUTE,
    }
    for stage, fn in (("capture", lambda: capture(db, **kwargs)), ("scorecard", lambda: scorecard(db))):
        try:
            out[stage] = fn()
        except NBACandidateForwardShadowError as exc:
            out[stage] = {"status": "FAILED", "reason_code": exc.code, "typed_failure": exc.typed_failure}
        except Exception as exc:  # noqa: BLE001 - infrastructure failure, typed and isolated
            out[stage] = {"status": "FAILED", "reason_code": f"NBA_CANDIDATE_{stage.upper()}_{type(exc).__name__.upper()}",
                          "typed_failure": "MODEL_SCORER_FAILED"}
    return out
