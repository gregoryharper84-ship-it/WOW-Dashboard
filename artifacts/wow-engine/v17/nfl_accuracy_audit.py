"""NFL V17 read-only accuracy/settlement reconciliation. Issue #1939.

Never fits, modifies, certifies or publishes sporting probabilities; never writes
an outcome or alters a prediction. This is diagnostic evidence only.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import math
import os
from typing import Any, Mapping, Sequence

CAN_EXECUTE = False
AUDIT_SCHEMA = "NFL_V17_ACCURACY_AUDIT_V1"
MIN_ML_FORWARD_GRADED = 100


def _utc(value: Any) -> datetime:
    value = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if value.utcoffset() is None:
        raise ValueError("NAIVE_TIMESTAMP")
    return value.astimezone(timezone.utc)


def _p(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("INVALID_PROBABILITY")
    p = float(value)
    if not math.isfinite(p) or not 0 < p < 1:
        raise ValueError("INVALID_PROBABILITY")
    return p


def _metrics(pairs: Sequence[tuple[float, int]]) -> dict[str, Any]:
    n = len(pairs)
    if not n:
        return {"n": 0, "mean_probability": None, "hit_rate": None, "brier": None, "log_loss": None}
    mean_p = sum(p for p, _ in pairs) / n
    hits = sum(y for _, y in pairs) / n
    return {
        "n": n,
        "mean_probability": mean_p,
        "hit_rate": hits,
        "calibration_bias": mean_p - hits,
        "brier": sum((p - y) ** 2 for p, y in pairs) / n,
        "log_loss": sum(-(y * math.log(p) + (1-y) * math.log(1-p)) for p, y in pairs) / n,
    }


def _line(value: Any) -> str:
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            raise ValueError("INVALID_LINE")
        return format(number.normalize(), "f")
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("INVALID_LINE") from exc


def audit_moneyline(predictions: Sequence[Mapping[str, Any]], games: Sequence[Mapping[str, Any]],
                    grades: Sequence[Mapping[str, Any]], *, as_of: datetime) -> dict[str, Any]:
    now = _utc(as_of)
    excluded = Counter()
    candidates: dict[str, tuple[datetime, str, Mapping[str, Any], float]] = {}
    seen_events = set()
    for row in predictions:
        event = str(row.get("official_event_id") or "").strip()
        if event:
            seen_events.add(event)
        try:
            ident = str(row.get("event_prediction_id") or "").strip()
            if not event or not ident:
                raise ValueError("IDENTITY_MISSING")
            if row.get("can_execute") is True:
                raise ValueError("CAN_EXECUTE_TRUE")
            start, created, modeled = (_utc(row.get(k)) for k in ("event_start_time_utc", "created_at", "model_timestamp"))
            if created >= start or modeled >= start:
                raise ValueError("NOT_PREGAME")
            if not all(row.get(k) is True for k in ("model_package_valid", "provenance_complete", "model_probability_publishable")):
                raise ValueError("MODEL_PACKAGE_NOT_VALID")
            if str(row.get("selected_participant")) not in {str(row.get("home_team")), str(row.get("away_team"))}:
                raise ValueError("SELECTED_TEAM_INVALID")
            p = _p(row.get("calibrated_selection_probability"))
            lo, hi = _p(row.get("calibrated_selection_lower_bound")), _p(row.get("calibrated_selection_upper_bound"))
            if not lo <= p <= hi:
                raise ValueError("BOUNDS_INVALID")
        except (ValueError, TypeError, OverflowError) as exc:
            excluded[str(exc)] += 1
            continue
        ordering = (created, ident)
        if event not in candidates or ordering > candidates[event][:2]:
            candidates[event] = (created, ident, row, p)

    game_index: dict[str, Mapping[str, Any]] = {}
    bad_games = set()
    for game in games:
        ident = str(game.get("game_id") or "")
        if not ident:
            continue
        if ident in game_index:
            bad_games.add(ident)
        game_index[ident] = game

    grade_index: dict[str, Mapping[str, Any]] = {}
    bad_grades = set()
    for grade in grades:
        ident = str(grade.get("official_event_id") or "")
        if not ident:
            continue
        if ident in grade_index:
            bad_grades.add(ident)
        grade_index[ident] = grade

    issues, expected = Counter(), Counter()
    scored = []
    for event, (_, prediction_id, row, p) in sorted(candidates.items()):
        if event in bad_games or event in bad_grades:
            issues["DUPLICATE_OFFICIAL_ROW"] += 1
            continue
        game = game_index.get(event)
        if not game or game.get("home_score") is None or game.get("away_score") is None:
            key = "PREGAME_PENDING" if _utc(row["event_start_time_utc"]) > now else "OFFICIAL_OUTCOME_MISSING"
            (expected if key == "PREGAME_PENDING" else issues)[key] += 1
            continue
        home, away = int(game["home_score"]), int(game["away_score"])
        if game.get("tie") is True or home == away:
            if home != away:
                issues["TIE_FLAG_CONFLICT"] += 1
            else:
                expected["TIE_EXCLUDED"] += 1
            continue
        if game.get("home_win") is not (home > away):
            issues["HOME_WIN_SCORE_CONFLICT"] += 1
            continue
        selected_home = str(row["selected_participant"]) == str(row["home_team"])
        y = int((home > away) if selected_home else (away > home))
        grade = grade_index.get(event)
        if grade is None:
            issues["GRADE_MISSING"] += 1
            continue
        try:
            matches = (
                str(grade.get("event_prediction_id")) == prediction_id
                and str(grade.get("selected_participant")) == str(row["selected_participant"])
                and int(grade.get("outcome")) == y
                and abs(_p(grade.get("calibrated_probability")) - p) <= 1e-10
            )
        except (TypeError, ValueError):
            matches = False
        if not matches:
            issues["IMMUTABLE_GRADE_MISMATCH"] += 1
            continue
        scored.append((p, y))

    orphan_count = len(set(grade_index) - set(candidates))
    out = {
        "snapshot_rows": len(predictions),
        "distinct_events": len(seen_events),
        "canonical_pregame_events": len(candidates),
        "duplicate_snapshots": len(predictions) - len(candidates) - sum(excluded.values()),
        "excluded_snapshots": dict(sorted(excluded.items())),
        "persisted_grade_rows": len(grades),
        "orphan_grade_events": orphan_count,
        "expected_ungraded": dict(sorted(expected.items())),
        "reconciliation_issues": dict(sorted(issues.items())),
        "metrics_for_reconciled_events_only": _metrics(scored),
        "minimum_forward_required": MIN_ML_FORWARD_GRADED,
    }
    out["audit_status"] = "RECONCILIATION_GAPS" if issues or orphan_count else "RECONCILED_WITH_EXCLUSIONS" if excluded or expected else "RECONCILED"
    return out


def audit_anytime_td(predictions: Sequence[Mapping[str, Any]], outcomes: Sequence[Mapping[str, Any]],
                     *, as_of: datetime) -> dict[str, Any]:
    now = _utc(as_of)
    filtered = [r for r in predictions if str(r.get("sport") or "").upper() == "NFL"
                and str(r.get("stat_type") or "").upper() == "ANYTIME_TD"]
    excluded = Counter()
    canonical: dict[tuple[str, str, str, str, str], tuple[datetime, str, Mapping[str, Any]]] = {}
    for row in filtered:
        try:
            event = str(row.get("event_id") or "").strip()
            player = " ".join(str(row.get("player") or "").casefold().split())
            side = str(row.get("direction") or "").upper()
            if not event or not player or side not in {"MORE", "LESS"}:
                raise ValueError("THESIS_IDENTITY_INVALID")
            if row.get("can_execute") is True:
                raise ValueError("CAN_EXECUTE_TRUE")
            created, start, modeled = (_utc(row.get(k)) for k in ("created_at", "event_start_time", "model_timestamp"))
            if created >= start or modeled >= start:
                raise ValueError("NOT_PREGAME")
            _p(row.get("calibrated_probability"))
            ident = str(row.get("prediction_id") or "").strip()
            if not ident:
                raise ValueError("PREDICTION_ID_MISSING")
            key = (event, player, "ANYTIME_TD", _line(row.get("line")), side)
        except (ValueError, TypeError, OverflowError) as exc:
            excluded[str(exc)] += 1
            continue
        if key not in canonical or (created, ident) > canonical[key][:2]:
            canonical[key] = (created, ident, row)

    outcomes_by_id = {}
    duplicate_outcomes = set()
    for row in outcomes:
        ident = str(row.get("prediction_id") or "")
        if ident in outcomes_by_id:
            duplicate_outcomes.add(ident)
        elif ident:
            outcomes_by_id[ident] = row

    base_markets = defaultdict(list)
    gaps = Counter()
    matched = []
    for key, (created, ident, row) in canonical.items():
        base = key[:4]
        base_markets[base].append(key[-1])
        if ident in duplicate_outcomes:
            gaps["DUPLICATE_SETTLEMENT"] += 1
            continue
        result = outcomes_by_id.get(ident)
        if result is None:
            gaps["UNSETTLED_AFTER_KICKOFF" if _utc(row["event_start_time"]) <= now else "PREGAME_PENDING"] += 1
            continue
        if result.get("void") is True or result.get("push") is True:
            gaps["VOID_OR_PUSH"] += 1
            continue
        if not isinstance(result.get("hit"), bool):
            gaps["INVALID_SETTLEMENT"] += 1
            continue
        matched.append((base, created, ident, _p(row["calibrated_probability"]), int(result["hit"])))

    # Opposing MORE/LESS sides of the same player/threshold share an outcome.
    # Pre-committed latest timestamp, never outcome-dependent selection.
    unique_markets = {}
    for base, created, ident, p, y in matched:
        if base not in unique_markets or (created, ident) > unique_markets[base][:2]:
            unique_markets[base] = (created, ident, p, y)
    independent = [(p, y) for _, _, p, y in unique_markets.values()]
    return {
        "prediction_rows": len(filtered),
        "directional_theses": len(canonical),
        "underlying_player_markets": len(base_markets),
        "paired_more_less_markets": sum(len(sides) > 1 for sides in base_markets.values()),
        "duplicate_snapshots": len(filtered) - len(canonical) - sum(excluded.values()),
        "excluded_snapshots": dict(sorted(excluded.items())),
        "settlement_dispositions": dict(sorted(gaps.items())),
        "settled_directional_theses": len(matched),
        "independent_graded_markets": _metrics(independent),
        "ungraded_underlying_markets": len(base_markets) - len(unique_markets),
        "period_identity_limit": "wow_predictions has no period column; no period-level certification inferred",
        "audit_status": "SETTLEMENT_GAPS" if gaps.get("UNSETTLED_AFTER_KICKOFF") else "READ_ONLY_AUDIT",
    }


def audit_nfl(ml: Sequence[Mapping[str, Any]], games: Sequence[Mapping[str, Any]],
              grades: Sequence[Mapping[str, Any]], td: Sequence[Mapping[str, Any]],
              td_outcomes: Sequence[Mapping[str, Any]], *, as_of: datetime) -> dict[str, Any]:
    return {
        "schema_version": AUDIT_SCHEMA, "can_execute": False, "read_only": True,
        "moneyline": audit_moneyline(ml, games, grades, as_of=as_of),
        "anytime_td": audit_anytime_td(td, td_outcomes, as_of=as_of),
    }


def _select_all(db: Any, table: str, cols: str) -> list[dict[str, Any]]:
    rows = []
    offset = 0
    while True:
        # This is deliberately SELECT-only; no mutations or scorer invocations.
        page = db.table(table).select(cols).range(offset, offset + 499).execute()
        batch = list(page.data or [])
        rows.extend(dict(r) for r in batch)
        if len(batch) < 500:
            return rows
        offset += 500


def run_read_only_audit(db: Any, *, as_of: datetime) -> dict[str, Any]:
    ml = _select_all(db, "wow_nfl_event_predictions", "event_prediction_id,official_event_id,event_start_time_utc,created_at,model_timestamp,home_team,away_team,selected_participant,calibrated_selection_probability,calibrated_selection_lower_bound,calibrated_selection_upper_bound,model_package_valid,provenance_complete,model_probability_publishable,can_execute")
    games = _select_all(db, "wow_nfl_training_games", "game_id,home_score,away_score,home_win,tie")
    grades = _select_all(db, "wow_nfl_forward_shadow_grades", "official_event_id,event_prediction_id,selected_participant,calibrated_probability,outcome")
    td = _select_all(db, "wow_predictions", "prediction_id,event_id,event_start_time,created_at,model_timestamp,player,sport,stat_type,line,direction,calibrated_probability,can_execute")
    settlements = _select_all(db, "wow_outcomes", "prediction_id,hit,push,void")
    return audit_nfl(ml, games, grades, td, settlements, as_of=as_of)


def main() -> None:
    from supabase import create_client
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.environ["SUPABASE_SERVICE_KEY"]
    report = run_read_only_audit(create_client(os.environ["SUPABASE_URL"], key),
                                 as_of=datetime.now(timezone.utc))
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
