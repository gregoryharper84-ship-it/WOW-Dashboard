"""Hourly WOW_PROP_LANE paper-trade slip stage: build -> persist -> grade.

Before this module nothing in the repository called the slip builder, so the
hourly priority audit could never produce a shape-valid paper snapshot even
when certified prop predictions and settled outcomes existed. This stage
closes that gap without changing any probability behaviour:

* candidates are read from persisted ``wow_predictions`` rows only;
* a row is admitted only when its own active fitted artifact is
  ``probability_publishable`` and the player's persisted evidence snapshot
  confirms official availability;
* uncertified, ruled-out, unverified or incomplete rows are held, never
  replaced with qualitative or market-implied probability;
* snapshots are written to the governed ``wow_slip_snapshots`` ledger through
  the service-role client with ``can_execute = false`` (no local file in
  production);
* pending snapshots are graded only when every leg has an official settled
  outcome, so missing evidence is never recorded as a push.

``can_execute`` is false unconditionally. Nothing here fits, calibrates,
certifies, promotes, publishes, prices, ranks or executes a sporting
probability.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from wow_slip_builder import (
    ARCHETYPES,
    ArchetypeId,
    Direction,
    PowerProduct,
    SlipSnapshot,
    ValidationError,
)
from wow_slip_grader import grade_slip
from wow_slip_intelligence import assess_slip_exposure, record_slip_learning
from wow_slip_prop_lane import build_paper_slip_from_prop_lane
from wow_slip_store import DEFAULT_DATA_DIR, SlipLedgerStore

from v17.player_availability_gate import (
    PLAYER_AVAILABILITY_RECHECK_REQUIRED,
    PLAYER_AVAILABILITY_UNVERIFIED,
    PLAYER_RULED_OUT,
    evaluate_player_availability,
)

CAN_EXECUTE = False
PROVIDER = "WOW_PROP_FITTED_MODEL_V1"
PREDICTION_TABLE = "wow_predictions"
SNAPSHOT_EVIDENCE_TABLE = "wow_prop_evidence_snapshots"
ARTIFACT_TABLE = "wow_prop_fitted_model_artifacts"
SLIP_SNAPSHOT_TABLE = "wow_slip_snapshots"
SLIP_GRADE_TABLE = "wow_slip_grades"
OUTCOME_TABLE = "wow_outcomes"

PAGE_SIZE = 1000
# Bounded per hourly cycle so the stage cannot dominate the audit's deadline.
MAX_PREDICTION_ROWS = 4000
MAX_SNAPSHOTS_PER_CYCLE = 12
MAX_PENDING_GRADES = 50
PREGAME_HORIZON_HOURS = 48
ID_CHUNK_SIZE = 150
PERSISTENCE_PATH = "SUPABASE_SERVICE_ROLE"

# Hold codes. Each is terminal for one archetype/candidate in this cycle.
HELD_NO_ACTIVE_ARTIFACT = "HELD_NO_ACTIVE_ARTIFACT"
HELD_ARTIFACT_NOT_PUBLISHABLE = "HELD_ARTIFACT_NOT_PUBLISHABLE"
HELD_CALIBRATION_INCOMPLETE = "HELD_CALIBRATION_INCOMPLETE"
HELD_EVIDENCE_SNAPSHOT_MISSING = "HELD_EVIDENCE_SNAPSHOT_MISSING"
HELD_AVAILABILITY_NOT_CONFIRMED = "HELD_AVAILABILITY_NOT_CONFIRMED"
HELD_CT_WINDOW = "HELD_CT_WINDOW"
HELD_INSUFFICIENT_ELIGIBLE_LEGS = "HELD_INSUFFICIENT_ELIGIBLE_LEGS"
HELD_EXACT_GAME_STACK = "HELD_EXACT_GAME_STACK"
HELD_DISTINCT_GAMES = "HELD_DISTINCT_GAMES"
HELD_BUILD_REJECTED = "HELD_BUILD_REJECTED"

# The persisted snapshot stores the availability verdict in ``blockers``
# (pick_request_runtime_core._snapshot_payload). Only these codes mean the
# participant is not cleared; an unrelated caller-supplied blocker code must
# not silently hold an otherwise cleared production row.
AVAILABILITY_BLOCKER_CODES = frozenset({
    PLAYER_RULED_OUT,
    PLAYER_AVAILABILITY_UNVERIFIED,
    PLAYER_AVAILABILITY_RECHECK_REQUIRED,
})


def _aware(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _paginate(build: Callable[[], Any], *, max_rows: int = MAX_PREDICTION_ROWS) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    start = 0
    while True:
        page = build().range(start, start + PAGE_SIZE - 1).execute().data or []
        batch = [dict(row) for row in page]
        output.extend(batch)
        if len(batch) < PAGE_SIZE or len(output) >= max_rows:
            return output[:max_rows]
        start += PAGE_SIZE


def _player_key(row: Mapping[str, Any]) -> str:
    """Stable per-player key for duplicate detection (no invented athlete id)."""
    team = str(row.get("team") or "").strip().upper()
    player = " ".join(str(row.get("player") or "").split()).casefold()
    return f"{team}:{player}" if team else player


def load_active_artifacts(db: Any) -> dict[tuple[str, str], dict[str, dict[str, Any]]]:
    """Active fitted artifacts keyed by (SPORT, STAT) then artifact version."""
    rows = _paginate(
        lambda: db.table(ARTIFACT_TABLE)
        .select("sport,stat_type,model_artifact_version,probability_publishable,active")
        .eq("active", True)
    )
    output: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for row in rows:
        key = (str(row.get("sport") or "").strip().upper(), str(row.get("stat_type") or "").strip().upper())
        version = str(row.get("model_artifact_version") or "").strip()
        if not all(key) or not version:
            continue
        output.setdefault(key, {})[version] = row
    return output


def _snapshot_evidence(db: Any, snapshot_ids: list[str]) -> dict[str, dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = {}
    for offset in range(0, len(snapshot_ids), ID_CHUNK_SIZE):
        chunk = snapshot_ids[offset:offset + ID_CHUNK_SIZE]
        rows = _paginate(
            lambda chunk=chunk: db.table(SNAPSHOT_EVIDENCE_TABLE)
            .select("source_snapshot_id,hydration_status,blockers,role_status")
            .in_("source_snapshot_id", chunk)
        )
        for row in rows:
            snapshot_id = str(row.get("source_snapshot_id") or "")
            if snapshot_id:
                evidence[snapshot_id] = row
    return evidence


def _availability_confirmed(evidence: Mapping[str, Any] | None) -> bool:
    """Official availability is required; hydration PASS alone is not enough."""
    if not evidence:
        return False
    if str(evidence.get("hydration_status") or "").strip().upper() != "PASS":
        return False
    blockers = {
        str(code).strip().upper()
        for code in (evidence.get("blockers") or [])
    }
    if blockers & AVAILABILITY_BLOCKER_CODES:
        return False
    role = evidence.get("role_status")
    decision = evaluate_player_availability(role if isinstance(role, Mapping) else {})
    return decision.available is True


def load_slip_candidates(
    db: Any,
    *,
    now: datetime,
    horizon_hours: int = PREGAME_HORIZON_HOURS,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Read persisted pregame predictions and map them to eligible Prop Lane rows.

    Returns (rows, holds). Holds are counted per fail-closed reason; they are
    never silently dropped as if they had been considered eligible.
    """
    horizon = now + timedelta(hours=horizon_hours)
    predictions: list[dict[str, Any]] = []
    for row in _paginate(
        lambda: db.table(PREDICTION_TABLE).select("*").eq("model_provider_identity", PROVIDER)
    ):
        start = _aware(row.get("event_start_time"))
        if start is None or start <= now or start > horizon:
            continue
        predictions.append(row)

    artifacts = load_active_artifacts(db)
    snapshot_ids = [str(row.get("source_snapshot_id")) for row in predictions if row.get("source_snapshot_id")]
    evidence = _snapshot_evidence(db, snapshot_ids) if snapshot_ids else {}

    holds: dict[str, int] = {}
    candidates: list[dict[str, Any]] = []

    def hold(code: str) -> None:
        holds[code] = holds.get(code, 0) + 1

    for row in predictions:
        key = (str(row.get("sport") or "").strip().upper(), str(row.get("stat_type") or "").strip().upper())
        version = str(row.get("model_artifact_version") or "").strip()
        artifact = artifacts.get(key, {}).get(version)
        if artifact is None:
            hold(HELD_NO_ACTIVE_ARTIFACT)
            continue
        if artifact.get("probability_publishable") is not True:
            hold(HELD_ARTIFACT_NOT_PUBLISHABLE)
            continue
        if not (row.get("calibrated_probability") is not None
                and row.get("calibrated_probability_lower_bound") is not None
                and str(row.get("calibration_version") or "").strip()):
            hold(HELD_CALIBRATION_INCOMPLETE)
            continue
        snapshot_id = str(row.get("source_snapshot_id") or "")
        if not snapshot_id:
            hold(HELD_EVIDENCE_SNAPSHOT_MISSING)
            continue
        if not _availability_confirmed(evidence.get(snapshot_id)):
            hold(HELD_AVAILABILITY_NOT_CONFIRMED)
            continue
        try:
            probability = float(row["calibrated_probability"])
            lower = float(row["calibrated_probability_lower_bound"])
            line = float(row["line"])
        except (KeyError, TypeError, ValueError):
            hold(HELD_CALIBRATION_INCOMPLETE)
            continue
        direction = str(row.get("direction") or "").strip().upper()
        if direction not in {"MORE", "LESS"} or not 0.0 < probability < 1.0 or not 0.0 <= lower <= probability:
            hold(HELD_CALIBRATION_INCOMPLETE)
            continue
        player_key = _player_key(row)
        event_id = str(row.get("event_id") or "").strip()
        prediction_id = str(row.get("prediction_id") or "").strip()
        stat_type = str(row.get("stat_type") or "").strip()
        if not (player_key and event_id and prediction_id and stat_type):
            hold(HELD_BUILD_REJECTED)
            continue
        candidates.append({
            "leg_id": prediction_id,
            "row_id": prediction_id,
            "player_id": player_key,
            "player_name": str(row.get("player") or player_key),
            "game_id": event_id,
            "market": stat_type.lower(),
            "direction": direction,
            "line": line,
            "modifier": "NORMAL",
            "calibrated_probability": probability,
            "calibrated_lower_bound": lower,
            "event_start_time": _aware(row.get("event_start_time")),
            "is_confirmed_active": True,
            "is_promo_square": False,
            # Governance evidence consumed by wow_slip_prop_lane.is_certified_eligible.
            "probability_publishable": True,
            "model_package_valid": True,
        })
    return candidates, holds


def _hold_code(exc: ValidationError) -> str:
    text = str(exc).lower()
    if "ct window" in text:
        return HELD_CT_WINDOW
    if "insufficient eligible legs" in text:
        return HELD_INSUFFICIENT_ELIGIBLE_LEGS
    if "distinct games" in text:
        return HELD_DISTINCT_GAMES
    if "exact" in text and "game stack" in text:
        return HELD_EXACT_GAME_STACK
    return HELD_BUILD_REJECTED


def _existing_leg_sets(db: Any) -> frozenset[frozenset[str]]:
    """Leg-id sets already persisted, so hourly reruns cannot duplicate a slip."""
    rows = _paginate(lambda: db.table(SLIP_SNAPSHOT_TABLE).select("legs_json"))
    output: set[frozenset[str]] = set()
    for row in rows:
        legs = row.get("legs_json")
        if not isinstance(legs, list):
            continue
        ids = frozenset(str(leg.get("leg_id")) for leg in legs if isinstance(leg, Mapping))
        if ids:
            output.add(ids)
    return frozenset(output)


def build_slips(
    candidates: list[dict[str, Any]],
    *,
    now: datetime,
    limit: int = MAX_SNAPSHOTS_PER_CYCLE,
    existing_leg_sets: frozenset[frozenset[str]] = frozenset(),
) -> tuple[list[SlipSnapshot], list[dict[str, Any]], dict[str, int]]:
    """Attempt every declared archetype once; holds are terminal, not fatal."""
    snapshots: list[SlipSnapshot] = []
    dispositions: list[dict[str, Any]] = []
    holds: dict[str, int] = {}
    for archetype in ArchetypeId:
        if len(snapshots) >= limit:
            dispositions.append({
                "archetype": archetype.value,
                "status": "DEFERRED_CYCLE_LIMIT",
                "can_execute": False,
            })
            continue
        try:
            snapshot = build_paper_slip_from_prop_lane(archetype, candidates, now)
        except ValidationError as exc:
            code = _hold_code(exc)
            holds[code] = holds.get(code, 0) + 1
            dispositions.append({
                "archetype": archetype.value,
                "status": code,
                "product": ARCHETYPES[archetype].product.value,
                "can_execute": False,
            })
            continue
        if frozenset(leg.leg_id for leg in snapshot.legs) in existing_leg_sets:
            dispositions.append({
                "archetype": archetype.value,
                "status": "SKIPPED_DUPLICATE_LEG_SET",
                "can_execute": False,
            })
            continue
        snapshots.append(snapshot)
        dispositions.append({
            "archetype": archetype.value,
            "status": "BUILT",
            "slip_id": str(snapshot.slip_id),
            "legs": len(snapshot.legs),
            "exposure": assess_slip_exposure(snapshot).model_dump(mode="json"),
            "can_execute": False,
        })
    return snapshots, dispositions, holds


def _pending_snapshot_rows(db: Any, limit: int = MAX_PENDING_GRADES) -> list[dict[str, Any]]:
    graded = {
        str(row.get("slip_id"))
        for row in _paginate(lambda: db.table(SLIP_GRADE_TABLE).select("slip_id"))
        if row.get("slip_id")
    }
    rows = _paginate(
        lambda: db.table(SLIP_SNAPSHOT_TABLE)
        .select("slip_id,archetype,product,multiplier,primary_direction,captured_at,built_at,stake_units,lane,legs_json")
        .eq("can_execute", False)
    )
    pending = [row for row in rows if str(row.get("slip_id")) not in graded]
    return pending[:limit]


def _snapshot_from_row(row: Mapping[str, Any]) -> SlipSnapshot:
    return SlipSnapshot(
        slip_id=row["slip_id"],
        archetype=ArchetypeId(str(row["archetype"])),
        product=PowerProduct(str(row["product"])),
        multiplier=float(row["multiplier"]),
        primary_direction=Direction(str(row["primary_direction"])),
        legs=list(row["legs_json"]),
        captured_at=_aware(row["captured_at"]),
        built_at=_aware(row.get("built_at")) or _aware(row["captured_at"]),
        stake_units=float(row.get("stake_units") or 1.0),
        lane=str(row.get("lane") or "WOW_PROP_LANE"),
        can_execute=False,
    )


def _settled_actuals(db: Any, leg_ids: list[str]) -> dict[str, float | None] | None:
    """Return leg_id -> actual (None when voided) only when every leg settled."""
    actuals: dict[str, float | None] = {}
    for offset in range(0, len(leg_ids), ID_CHUNK_SIZE):
        chunk = leg_ids[offset:offset + ID_CHUNK_SIZE]
        rows = _paginate(
            lambda chunk=chunk: db.table(OUTCOME_TABLE)
            .select("prediction_id,actual_stat,void,settlement_timestamp")
            .in_("prediction_id", chunk)
        )
        for row in rows:
            prediction_id = str(row.get("prediction_id") or "")
            if not prediction_id or row.get("settlement_timestamp") is None:
                continue
            if row.get("void") is True:
                actuals[prediction_id] = None
            elif row.get("actual_stat") is not None:
                actuals[prediction_id] = float(row["actual_stat"])
    return actuals if all(leg_id in actuals for leg_id in leg_ids) else None


def grade_pending_slips(
    db: Any,
    store: SlipLedgerStore,
    *,
    limit: int = MAX_PENDING_GRADES,
) -> dict[str, Any]:
    """Grade only fully settled slips; unsettled evidence stays pending."""
    graded = 0
    pending = 0
    failed = 0
    grades: list[dict[str, Any]] = []
    for row in _pending_snapshot_rows(db, limit):
        try:
            snapshot = _snapshot_from_row(row)
        except Exception:  # noqa: BLE001 - malformed stored row is reported, not graded
            failed += 1
            continue
        leg_ids = [leg.leg_id for leg in snapshot.legs]
        actuals = _settled_actuals(db, leg_ids) if leg_ids else None
        if actuals is None:
            pending += 1
            continue
        try:
            grade = grade_slip(snapshot, actuals)
            store.write_grade(grade)
        except Exception:  # noqa: BLE001 - one bad row never blocks the lane
            failed += 1
            continue
        graded += 1
        grades.append({
            "slip_id": str(grade.slip_id),
            "archetype": grade.archetype,
            "net_units": grade.net_units,
            "effective_multiplier": grade.effective_multiplier,
            "learning": record_slip_learning(grade).model_dump(mode="json"),
            "can_execute": False,
        })
    return {
        "grades_written": graded,
        "pending_outcomes": pending,
        "grade_failures": failed,
        "grades": grades,
        "can_execute": False,
    }


def run_slip_lane(
    db: Any,
    *,
    now: datetime | None = None,
    data_dir: Any = None,
    build_limit: int = MAX_SNAPSHOTS_PER_CYCLE,
    grade_limit: int = MAX_PENDING_GRADES,
) -> dict[str, Any]:
    """One bounded hourly paper-trade cycle: build, persist, grade."""
    captured_at = now or datetime.now(timezone.utc)
    candidates, candidate_holds = load_slip_candidates(db, now=captured_at)
    snapshots, dispositions, build_holds = build_slips(
        candidates,
        now=captured_at,
        limit=build_limit,
        existing_leg_sets=_existing_leg_sets(db),
    )

    store = SlipLedgerStore(
        data_dir=data_dir or DEFAULT_DATA_DIR,
        supabase_client=db,
        append_jsonl=False,
    )
    stored = 0
    store_failures = 0
    for snapshot in snapshots:
        try:
            store.write_snapshot(snapshot)
        except Exception:  # noqa: BLE001 - a failed insert is counted, never retried blind
            store_failures += 1
            continue
        stored += 1

    if stored == 0 and store_failures:
        # A governed ledger that rejects every write (for example an unapplied
        # migration) is a stage blocker, not a quiet zero-snapshot cycle.
        raise RuntimeError(
            f"slip persistence failed for {store_failures} snapshot(s)"
        )

    grading = grade_pending_slips(db, store, limit=grade_limit)

    holds: dict[str, int] = {}
    for source in (candidate_holds, build_holds):
        for code, count in source.items():
            holds[code] = holds.get(code, 0) + count

    return {
        "captured_at": captured_at.isoformat(),
        "candidates_considered": len(candidates) + sum(candidate_holds.values()),
        "candidates_eligible": len(candidates),
        "candidate_holds": dict(sorted(candidate_holds.items())),
        "archetypes_attempted": len(ArchetypeId),
        "snapshots_built": len(snapshots),
        "snapshots_stored": stored,
        "snapshot_store_failures": store_failures,
        "build_dispositions": dispositions,
        "holds_by_code": dict(sorted(holds.items())),
        "grading": grading,
        "persistence_path": PERSISTENCE_PATH,
        "local_file_written": False,
        "acquisition_performed": False,
        "probability_modified": False,
        "certification_or_promotion_performed": False,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "MAX_SNAPSHOTS_PER_CYCLE",
    "PERSISTENCE_PATH",
    "build_slips",
    "grade_pending_slips",
    "load_active_artifacts",
    "load_slip_candidates",
    "run_slip_lane",
]
