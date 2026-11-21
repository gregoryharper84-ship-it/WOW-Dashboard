"""Research-only chronological, unique-event probability evaluation.

Purpose: prevent multiple pregame predictions about the same event from being
misrepresented as statistically independent outcomes. No model training,
publication, promotion, price inference, or execution is permitted.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from math import isfinite, log
from decimal import Decimal
from typing import Any, Iterable, Mapping

CAN_EXECUTE = False
AUTHORITY = "ADVISORY_ONLY"


def _moment(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None and dt.utcoffset() is not None else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    result = float(value)
    return result if isfinite(result) else None


def evaluate_independent_events(
    rows: Iterable[Mapping[str, Any]],
    *,
    model_version: str,
    holdout_start: str,
    holdout_end: str,
    min_independent_events: int = 100,
) -> dict[str, Any]:
    """One latest frozen pregame HOME-side forecast per official event.

    The caller must supply previously frozen, matched event prediction/outcome
    facts; no source data is altered. Repeated rows are retained in counts but
    cannot inflate the statistical sample. The holdout interval is explicitly
    bounded so the caller cannot silently train/evaluate on the same period.
    """
    start = _moment(holdout_start)
    end = _moment(holdout_end)
    if not model_version.strip() or not start or not end or start >= end:
        raise ValueError("CHRONOLOGICAL_HOLDOUT_REQUIRED")
    if isinstance(min_independent_events, bool) or not isinstance(min_independent_events, int) or min_independent_events < 1:
        raise ValueError("MIN_INDEPENDENT_EVENTS_INVALID")

    reasons: Counter[str] = Counter()
    candidates: dict[tuple[str, str], list[tuple[datetime, str, float, float, int, str, str]]] = defaultdict(list)
    input_n = 0
    for row in rows:
        input_n += 1
        if not isinstance(row, Mapping):
            reasons["ROW_NOT_MAPPING"] += 1
            continue
        if str(row.get("model_version") or "").strip() != model_version:
            reasons["OTHER_MODEL_VERSION"] += 1
            continue
        event_id = str(row.get("official_event_id") or "").strip()
        sport = str(row.get("sport") or "").strip()
        prediction_id = str(row.get("event_prediction_id") or "").strip()
        model_time = _moment(row.get("model_timestamp"))
        event_time = _moment(row.get("event_start_time"))
        if not (event_id and sport and prediction_id and model_time and event_time):
            reasons["IDENTITY_OR_TIME_UNRESOLVED"] += 1
            continue
        if not (start <= event_time < end):
            reasons["OUTSIDE_HOLDOUT"] += 1
            continue
        if not model_time < event_time:
            reasons["NOT_PREGAME_FROZEN"] += 1
            continue
        if row.get("void") is True:
            reasons["VOID_EXCLUDED"] += 1
            continue
        home = row.get("home_score")
        away = row.get("away_score")
        if isinstance(home, bool) or isinstance(away, bool) or not isinstance(home, int) or not isinstance(away, int) or home == away:
            reasons["SETTLEMENT_NOT_BINARY"] += 1
            continue
        p = _number(row.get("calibrated_home_probability"))
        lower = _number(row.get("calibrated_home_lower_bound"))
        if p is None or lower is None or not 0.0 < p < 1.0 or not 0.0 <= lower <= p:
            reasons["CALIBRATION_INVALID"] += 1
            continue
        candidates[(sport, event_id)].append(
            (model_time, prediction_id, p, lower, int(home > away), event_time.astimezone(timezone.utc).isoformat(), f"{home}:{away}")
        )

    selected = []
    conflict_events = 0
    conflict_rows = 0
    for _event, options in candidates.items():
        if len({(row[5], row[6]) for row in options}) != 1:
            # Never quietly choose a winner when two settlement records disagree.
            conflict_events += 1
            conflict_rows += len(options)
            continue
        selected.append(max(options, key=lambda r: (r[0], r[1])))

    n = len(selected)
    duplicate_rows = sum(max(0, len(options) - 1) for options in candidates.values() if len({(r[5], r[6]) for r in options}) == 1)
    accounted_rows = n + duplicate_rows + conflict_rows + sum(reasons.values())
    if accounted_rows != input_n:
        raise ValueError("INDEPENDENT_EVENT_ROW_RECONCILIATION_FAILED")
    if n:
        brier = sum((p - y) ** 2 for _, _, p, _, y, _, _ in selected) / n
        ll = -sum(y * log(p) + (1 - y) * log(1 - p) for _, _, p, _, y, _, _ in selected) / n
        mean_probability = sum(row[2] for row in selected) / n
        home_rate = sum(row[4] for row in selected) / n
        mean_lower_bound = sum(row[3] for row in selected) / n
    else:
        brier = ll = mean_probability = home_rate = mean_lower_bound = None

    return {
        "schema_version": "WOW17_INDEPENDENT_EVENT_ACCURACY_V1",
        "model_version": model_version,
        "holdout_start": start.isoformat(),
        "holdout_end": end.isoformat(),
        "input_rows": input_n,
        "independent_events": n,
        "duplicate_snapshot_rows_excluded": duplicate_rows,
        "conflicting_events_excluded": conflict_events,
        "conflicting_snapshot_rows_excluded": conflict_rows,
        "row_reconciliation_pass": accounted_rows == input_n,
        "exclusion_counts": dict(sorted(reasons.items())),
        "brier": brier,
        "log_loss": ll,
        "mean_calibrated_home_probability": mean_probability,
        "observed_home_win_rate": home_rate,
        "mean_calibrated_lower_bound": mean_lower_bound,
        "evidence_status": "REVIEW_COHORT" if n >= min_independent_events else "WATCH_ONLY_THIN_COHORT",
        "research_only": True,
        "automatic_promotion_allowed": False,
        "authority": AUTHORITY,
        "can_execute": False,
    }


__all__ = ["evaluate_independent_events", "AUTHORITY", "CAN_EXECUTE"]
