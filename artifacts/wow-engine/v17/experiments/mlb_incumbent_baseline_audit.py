"""Read-only WOW MLB immutable-prediction baseline audit (NOT a challenger).

Consumes JSON rows returned by the companion SELECT-only SQL. Every rejected
row is accounted for. Independent game sample counts never treat repeated
snapshots of one game as separate sporting outcomes.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
from typing import Any, Mapping, Sequence

from free_forecasting_shadow import metric_packet

CAN_EXECUTE = False
AUTOMATIC_PROMOTION_ALLOWED = False
PRODUCTION_MUTATION_ALLOWED = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"


class BaselineCohortError(ValueError):
    pass


def _time(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise BaselineCohortError("REQUIRED_TIMESTAMP_MISSING")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BaselineCohortError("TIMESTAMP_INVALID") from exc
    if result.utcoffset() is None:
        raise BaselineCohortError("TIMESTAMP_NOT_AWARE")
    return result.astimezone(timezone.utc)


def _admit(row: Mapping[str, Any], cutoff: datetime):
    if row.get("sport") != "MLB" or row.get("market_family") != "OUTRIGHT_WINNER":
        raise BaselineCohortError("WRONG_SPECIALIST_COHORT")
    if row.get("can_execute") is not False:
        raise BaselineCohortError("EXECUTION_LOCK_INVALID")
    if any(not isinstance(row.get(k), str) or not row[k].strip()
           for k in ("prediction_id", "official_event_id", "home_team", "away_team")):
        raise BaselineCohortError("CANONICAL_IDENTITY_MISSING")
    if row["home_team"] == row["away_team"]:
        raise BaselineCohortError("SAME_HOME_AWAY_IDENTITY")
    event_start = _time(row.get("event_start_time"))
    if event_start >= cutoff:
        raise BaselineCohortError("EVENT_NOT_SETTLED_AT_AUDIT_CUTOFF")
    created, model = _time(row.get("created_at")), _time(row.get("model_timestamp"))
    if not (created < event_start and model < event_start):
        raise BaselineCohortError("PREGAME_TIMING_FAILED")
    latest = _time(row.get("latest_material_update_timestamp"))
    if model < latest or row.get("model_valid_after_latest_update") is not True:
        raise BaselineCohortError("STALE_MODEL_AFTER_MATERIAL_UPDATE")
    if row.get("rank_eligible") is not True or row.get("probability_publishable") is not True:
        raise BaselineCohortError("NOT_GOVERNED_RANK_ELIGIBLE")
    if row.get("void") is True:
        raise BaselineCohortError("VOID_OUTCOME")
    if not isinstance(row.get("settlement_source"), str) or not row["settlement_source"].strip():
        raise BaselineCohortError("OFFICIAL_SETTLEMENT_SOURCE_MISSING")
    settlement = _time(row.get("settlement_timestamp"))
    if settlement <= event_start:
        raise BaselineCohortError("SETTLEMENT_BEFORE_EVENT")
    winner = row.get("official_winner")
    if winner not in (row["home_team"], row["away_team"]):
        raise BaselineCohortError("OFFICIAL_WINNER_IDENTITY_MISMATCH")
    try:
        p = float(row.get("calibrated_home_probability"))
    except (TypeError, ValueError) as exc:
        raise BaselineCohortError("INVALID_GOVERNED_PROBABILITY") from exc
    if isinstance(row.get("calibrated_home_probability"), bool) or not isfinite(p) or not 0 < p < 1:
        raise BaselineCohortError("INVALID_GOVERNED_PROBABILITY")
    event_key = (row["official_event_id"], event_start)
    y = int(winner == row["home_team"])
    return event_key, (model, created, row["prediction_id"]), p, y


def audit_baseline(rows: Sequence[Mapping[str, Any]], *, audited_at: str) -> dict[str, Any]:
    """Audit one immutable MLB OUTRIGHT_WINNER cohort, no writes or predictions."""
    if not isinstance(rows, list):
        raise BaselineCohortError("SOURCE_ROWS_NOT_LIST")
    cutoff = _time(audited_at)
    excluded: Counter[str] = Counter()
    accepted: dict[tuple[str, datetime], tuple[tuple, float, int]] = {}
    seen_predictions: set[str] = set()
    admitted = 0
    for row in rows:
        if not isinstance(row, dict):
            excluded["SOURCE_ROW_NOT_OBJECT"] += 1
            continue
        pid = row.get("prediction_id")
        if isinstance(pid, str) and pid in seen_predictions:
            excluded["DUPLICATE_PREDICTION_ID"] += 1
            continue
        if isinstance(pid, str):
            seen_predictions.add(pid)
        try:
            event_key, tie, p, y = _admit(row, cutoff)
        except BaselineCohortError as exc:
            excluded[str(exc)] += 1
            continue
        admitted += 1
        prior = accepted.get(event_key)
        if prior is None or tie > prior[0]:
            if prior is not None and prior[2] != y:
                raise BaselineCohortError("CONTRADICTORY_OFFICIAL_OUTCOME_FOR_EVENT")
            accepted[event_key] = (tie, p, y)
        elif prior[2] != y:
            raise BaselineCohortError("CONTRADICTORY_OFFICIAL_OUTCOME_FOR_EVENT")
    rows_selected = list(accepted.values())
    metrics = metric_packet([v[1] for v in rows_selected], [v[2] for v in rows_selected]) if rows_selected else None
    raw_hash = sha256(json.dumps(rows, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    # One event = one independent outcome for descriptive metrics. A 32-event
    # snapshot is insufficient for a new model's Class C calibration approval.
    return {"status": "BASELINE_DESCRIPTIVE_ONLY" if metrics else "NO_ELIGIBLE_OUTCOME_COHORT",
            "sport": "MLB", "market": "OUTRIGHT_WINNER",
            "source_row_count": len(rows), "admitted_prediction_rows": admitted,
            "unique_events": len(rows_selected),
            "repeated_valid_predictions": admitted - len(rows_selected),
            "excluded_by_reason": dict(sorted(excluded.items())),
            "accounted_rows": len(rows) == admitted + sum(excluded.values()),
            "selection_policy": "LATEST_VALID_PREGAME_PREDICTION_PER_OFFICIAL_EVENT_AND_START",
            "metrics": metrics, "source_sha256": raw_hash,
            "training_or_model_comparison_performed": False,
            "historical_d1_reconstruction_is_certified_pregame": False,
            "automatic_promotion_allowed": AUTOMATIC_PROMOTION_ALLOWED,
            "production_mutation_allowed": PRODUCTION_MUTATION_ALLOWED,
            "can_execute": CAN_EXECUTE,
            "terminal_authority": TERMINAL_AUTHORITY}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path,
                        help="Trusted JSON list obtained from the companion read-only SQL")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--audited-at", required=True,
                        help="ISO timestamp; freeze audit cutoff, do not accept future games")
    args = parser.parse_args()
    data = json.loads(args.input.read_text(encoding="utf-8"))
    result = audit_baseline(data, audited_at=args.audited_at)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True)+"\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
