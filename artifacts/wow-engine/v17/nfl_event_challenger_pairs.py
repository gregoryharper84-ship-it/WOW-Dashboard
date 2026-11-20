"""NFL event-winner adapter for the shared challenger evaluator.

Maps rows already persisted by the NFL lanes into evaluator rows. Pure and
read-only: callers supply the rows (from ``wow_nfl_forward_verified_grades`` and
``wow_nfl_ml_challenger_forward_shadow``); nothing is written, fitted, or
published.

- ``incumbent_rows``: the incumbent's verified forward grades, for an
  incumbent-only baseline.
- ``paired_rows``: incumbent grades joined to one challenger's forward-shadow
  rows on the official event. The challenger probability is oriented to the
  incumbent's selected participant; its lower bound is kept only when both
  selected the same side, because a lower bound does not transfer to the
  complement.

Every dropped row is counted under a typed exclusion code, so no candidate
disappears silently. Both cohorts here are PROSPECTIVE_HOLDOUT: each forecast
was made before kickoff. ``settled_at`` is the event start, a conservative
stand-in that is always earlier than settlement. can_execute is always false.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from v17.challenger_evaluator import PROSPECTIVE
from v17.nfl_atd_gamebook_evidence import TEAM_NAMES

MARKET_FAMILY = "NFL_EVENT_WINNER"


def _ts(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


_ABBR_BY_NAME = {name.upper(): abbr for abbr, name in TEAM_NAMES.items()}


def _participant(value: Any) -> str:
    """Canonical team identity: lanes store either 'HOU' or 'Houston Texans'."""
    raw = str(value or "").strip().upper()
    if raw in TEAM_NAMES:
        return raw
    return _ABBR_BY_NAME.get(raw, raw)


def _incumbent_row(grade: Mapping[str, Any], excluded: Counter) -> dict[str, Any] | None:
    pred_id = str(grade.get("event_prediction_id") or "").strip()
    event_id = str(grade.get("official_event_id") or "").strip()
    if not pred_id or not event_id or not _participant(grade.get("selected_participant")):
        excluded["INCUMBENT_IDENTITY_MISSING"] += 1
        return None
    outcome = grade.get("outcome")
    if isinstance(outcome, bool) or outcome not in (0, 1):
        excluded["OUTCOME_NOT_BINARY"] += 1  # ties, voids and unsettled rows are not coerced
        return None
    created, start = _ts(grade.get("prediction_created_at")), _ts(grade.get("event_start_time_utc"))
    if created is None or start is None:
        excluded["TIMESTAMP_INVALID"] += 1
        return None
    if created >= start:
        excluded["FORECAST_NOT_BEFORE_START"] += 1
        return None
    return {
        "row_id": pred_id,
        "official_event_id": event_id,
        "market_family": MARKET_FAMILY,
        "cohort": PROSPECTIVE,
        "incumbent_p": grade.get("calibrated_probability"),
        "incumbent_lower": grade.get("calibrated_lower_bound"),
        "outcome": int(outcome),
        "forecast_at": created.isoformat(),
        "settled_at": start.isoformat(),
        "_selected": _participant(grade.get("selected_participant")),
    }


def incumbent_rows(grades: Iterable[Mapping[str, Any]], *, model_version: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    excluded: Counter = Counter()
    rows, seen = [], set()
    for grade in grades:
        if grade.get("model_version") != model_version:
            excluded["OTHER_MODEL_VERSION"] += 1
            continue
        row = _incumbent_row(grade, excluded)
        if row is None:
            continue
        if row["official_event_id"] in seen:
            excluded["DUPLICATE_INCUMBENT_EVENT"] += 1
            continue
        seen.add(row["official_event_id"])
        rows.append(row)
    return [_public(r) for r in rows], dict(excluded)


def paired_rows(
    grades: Iterable[Mapping[str, Any]],
    challenger: Iterable[Mapping[str, Any]],
    *,
    model_version: str,
    challenger_id: str,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    excluded: Counter = Counter()
    by_event: dict[str, list[Mapping[str, Any]]] = {}
    for row in challenger:
        if row.get("challenger_id") != challenger_id:
            continue
        by_event.setdefault(str(row.get("official_event_id") or "").strip(), []).append(row)

    incumbents: dict[str, dict[str, Any]] = {}
    for grade in grades:
        if grade.get("model_version") != model_version:
            continue
        row = _incumbent_row(grade, excluded)
        if row is None:
            continue
        if row["official_event_id"] in incumbents:
            excluded["DUPLICATE_INCUMBENT_EVENT"] += 1
            continue
        incumbents[row["official_event_id"]] = row

    out = []
    for event_id, inc in sorted(incumbents.items()):
        candidates = by_event.get(event_id, [])
        if not candidates:
            excluded["NO_CHALLENGER_ROW"] += 1
            continue
        if len(candidates) > 1:
            excluded["AMBIGUOUS_CHALLENGER_ROWS"] += 1
            continue
        ch = candidates[0]
        ch_created, start = _ts(ch.get("prediction_created_at")), _ts(inc["settled_at"])
        if ch_created is None or start is None or ch_created >= start:
            excluded["CHALLENGER_FORECAST_NOT_BEFORE_START"] += 1
            continue
        p = ch.get("calibrated_probability")
        if isinstance(p, bool) or not isinstance(p, (int, float)):
            excluded["CHALLENGER_PROBABILITY_INVALID"] += 1
            continue
        ch_side = _participant(ch.get("selected_participant"))
        teams = {_participant(ch.get("home_team")), _participant(ch.get("away_team"))}
        if ch_side == inc["_selected"]:
            ch_p, ch_lower = float(p), ch.get("composite_lower_bound")
        elif inc["_selected"] in teams and ch_side in teams and len(teams) == 2:
            ch_p, ch_lower = 1.0 - float(p), None
        else:
            excluded["PARTICIPANT_MISMATCH"] += 1
            continue
        ch_outcome = ch.get("outcome")
        if ch_outcome in (0, 1) and not isinstance(ch_outcome, bool):
            oriented = ch_outcome if ch_side == inc["_selected"] else 1 - ch_outcome
            if oriented != inc["outcome"]:
                excluded["OUTCOME_CONFLICT"] += 1
                continue
        out.append({**_public(inc), "challenger_p": ch_p, "challenger_lower": ch_lower})
    return out, dict(excluded)


def _public(row: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if not k.startswith("_") and k != "official_event_id"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build challenger-evaluator rows for NFL event winner.")
    parser.add_argument("--grades-json", required=True, help="JSON list of wow_nfl_forward_verified_grades rows")
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--challenger-json", help="JSON list of wow_nfl_ml_challenger_forward_shadow rows")
    parser.add_argument("--challenger-id")
    parser.add_argument("--out", required=True, help="evaluator rows as JSONL")
    args = parser.parse_args(argv)
    try:
        grades = json.loads(Path(args.grades_json).read_text(encoding="utf-8"))
        if args.challenger_json:
            if not args.challenger_id:
                raise ValueError("--challenger-id is required with --challenger-json")
            challenger = json.loads(Path(args.challenger_json).read_text(encoding="utf-8"))
            rows, excluded = paired_rows(grades, challenger, model_version=args.model_version, challenger_id=args.challenger_id)
        else:
            rows, excluded = incumbent_rows(grades, model_version=args.model_version)
    except (OSError, ValueError, json.JSONDecodeError, TypeError, AttributeError) as exc:
        print(json.dumps({"error": "ADAPTER_INPUT_INVALID", "detail": str(exc), "can_execute": False}))
        return 2
    Path(args.out).write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
    print(json.dumps({"rows": len(rows), "excluded": excluded, "market_family": MARKET_FAMILY, "can_execute": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
