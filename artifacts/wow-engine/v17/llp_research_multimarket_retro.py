"""Read-only, evidence-gated research grading for mixed-sport market cards.

This is NOT a fitted-model scorer, a settlement authority, or a source verifier.
It grades only independently attested frozen selections against authenticated
official results. Callers must establish authenticity outside this module.
No aggregate across different market families is reported as model accuracy.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from math import isfinite
from typing import Any, Mapping, Sequence

CAN_EXECUTE = False
MARKETS = frozenset({"MONEYLINE", "SPREAD", "TOTAL"})
BASIS = {
    "NFL": "FULL_GAME_INCLUDING_OVERTIME",
    "NBA": "FULL_GAME_INCLUDING_OVERTIME",
    "WNBA": "FULL_GAME_INCLUDING_OVERTIME",
    "NCAAF": "FULL_GAME_INCLUDING_OVERTIME",
    "NCAAB": "FULL_GAME_INCLUDING_OVERTIME",
    "NHL": "FULL_GAME_INCLUDING_OVERTIME_SHOOTOUT",
    "MLB": "FULL_GAME_INCLUDING_EXTRA_INNINGS",
    "SOCCER": "REGULATION",
    "TENNIS": "MATCH_COMPLETED",
}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def _number(value: Any, *, maximum: float, allow_negative: bool) -> float | None:
    # Bool, NaN, infinity, and enormous integers must not enter arithmetic.
    if type(value) not in (int, float) or not -maximum <= value <= maximum:
        return None
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    if not isfinite(number) or (not allow_negative and number < 0):
        return None
    # Quarter-point/Asian lines require different settlement semantics.
    return number if number * 2 == int(number * 2) else None


def _key(row: Mapping[str, Any]) -> tuple[str, str]:
    return _text(row.get("sport")).upper(), _text(row.get("official_event_id"))


def _result_valid(pick: Mapping[str, Any], result: Mapping[str, Any] | None) -> list[str]:
    if result is None:
        return ["OFFICIAL_FINAL_MISSING"]
    errors = []
    if (_key(pick) != _key(result)
            or any(not _text(pick.get(k)) or
                   _text(pick.get(k)).casefold() != _text(result.get(k)).casefold()
                   for k in ("home_team", "away_team"))
            or _utc(pick.get("event_start_time_utc")) is None
            or _utc(pick.get("event_start_time_utc")) != _utc(result.get("event_start_time_utc"))):
        errors.append("OFFICIAL_EVENT_IDENTITY_CONFLICT")
    sport = _key(pick)[0]
    if pick.get("settlement_basis") != BASIS.get(sport) or result.get("settlement_basis") != BASIS.get(sport):
        errors.append("MARKET_SETTLEMENT_BASIS_INVALID")
    if (result.get("independently_verified") is not True
            or not _text(result.get("settlement_receipt_id"))
            or not _text(result.get("settlement_source"))):
        errors.append("OFFICIAL_FINAL_UNVERIFIED")
    started, settled = _utc(result.get("event_start_time_utc")), _utc(result.get("settled_at_utc"))
    is_void = sport == "TENNIS" and result.get("final_status") == "VOID_PREMATCH_WITHDRAWAL"
    freeze = _utc(pick.get("frozen_at_utc"))
    if (started is None or settled is None
            or (is_void and (freeze is None or settled <= freeze))
            or (not is_void and settled <= started)):
        errors.append("OFFICIAL_SETTLEMENT_TIME_INVALID")
    if is_void:
        if result.get("void_reason") != "PREMATCH_WITHDRAWAL":
            errors.append("VOID_REASON_INVALID")
    elif result.get("final_status") != "FINAL":
        errors.append("OFFICIAL_FINAL_NOT_FINAL")
    if sport != "TENNIS":
        for score_key in ("home_score", "away_score"):
            score = result.get(score_key)
            if type(score) is not int or score < 0 or score > 1000:
                errors.append("OFFICIAL_SCORE_INVALID")
                break
    return errors


def audit_multimarket_retro(
    picks: Sequence[Mapping[str, Any]],
    finals: Sequence[Mapping[str, Any]],
    *,
    cohort_manifest: Mapping[str, Sequence[str]] | None = None,
    cohort_manifest_verified: bool = False,
    cohort_manifest_receipt_id: str | None = None,
) -> dict[str, Any]:
    """Grade exact sides and signed lines; no universal win-rate headline.

    cohort_manifest maps each frozen selection cohort to its complete set of
    immutable selection IDs. It is not itself independently authenticated here.
    Missing/held entries suppress only that cohort's headline, not other cohorts.
    """
    if any(not isinstance(x, Mapping) for x in (*picks, *finals)):
        raise ValueError("RESEARCH_RETRO_RECORD_INVALID")
    if cohort_manifest is not None and not isinstance(cohort_manifest, Mapping):
        raise ValueError("COHORT_MANIFEST_NOT_OBJECT")
    final_count = Counter(_key(r) for r in finals)
    final_by_event = {_key(r): r for r in finals}
    settlement_receipts = Counter(_text(r.get("settlement_receipt_id")) for r in finals
                                  if _text(r.get("settlement_receipt_id")))
    cohort_market_counts = Counter(
        (_text(p.get("cohort")), *_key(p), _text(p.get("market_family")).upper())
        for p in picks
    )
    cohort_markets: dict[str, set[str]] = {}
    for p in picks:
        cohort_markets.setdefault(_text(p.get("cohort")), set()).add(
            _text(p.get("market_family")).upper()
        )
    receipt_count = Counter(_text(p.get("pregame_receipt_id")) for p in picks
                            if _text(p.get("pregame_receipt_id")))
    pick_ids = Counter(_text(p.get("selection_id")) for p in picks if _text(p.get("selection_id")))
    groups: dict[str, list[dict[str, Any]]] = {}
    rows = []
    for p in picks:
        sport, event = _key(p)
        market, cohort = _text(p.get("market_family")).upper(), _text(p.get("cohort"))
        selection_id = _text(p.get("selection_id"))
        side = _text(p.get("selected_side")).upper()
        line = p.get("selected_side_line")
        issues = []
        if len(cohort_markets.get(cohort, set())) != 1:
            issues.append("MIXED_MARKET_COHORT_INVALID")
        if sport not in BASIS or not event or not cohort or market not in MARKETS:
            issues.append("RESEARCH_SELECTION_IDENTITY_OR_MARKET_INVALID")
        if cohort_market_counts[(cohort, sport, event, market)] != 1:
            issues.append("DUPLICATE_COHORT_EVENT_MARKET")
        if (not selection_id or pick_ids[selection_id] != 1
                or not _text(p.get("pregame_receipt_id"))
                or receipt_count[_text(p.get("pregame_receipt_id"))] != 1
                or p.get("immutable_pregame") is not True
                or p.get("pregame_receipt_verified") is not True):
            issues.append("PREGAME_SELECTION_RECEIPT_INVALID")
        start, freeze = _utc(p.get("event_start_time_utc")), _utc(p.get("frozen_at_utc"))
        visible = _utc(p.get("available_to_user_at_utc"))
        as_of = _utc(p.get("source_as_of_utc"))
        if (start is None or freeze is None or visible is None or as_of is None
                or not as_of <= visible <= freeze < start):
            issues.append("PREGAME_CHRONOLOGY_INVALID")
        if market == "MONEYLINE":
            expected = {"HOME", "AWAY", "DRAW"} if sport == "SOCCER" else {"HOME", "AWAY"}
            if side not in expected or line is not None:
                issues.append("MONEYLINE_SIDE_OR_LINE_INVALID")
        elif market == "SPREAD":
            if sport == "TENNIS" or side not in {"HOME", "AWAY"} or _number(line, maximum=100, allow_negative=True) is None:
                issues.append("SPREAD_SIDE_OR_LINE_INVALID")
        elif market == "TOTAL":
            if sport == "TENNIS" or side not in {"OVER", "UNDER"} or _number(line, maximum=500, allow_negative=False) is None:
                issues.append("TOTAL_SIDE_OR_LINE_INVALID")
        if sport in {"PGA", "MMA", "BOXING", "CRICKET"}:
            issues.append("SPECIALIST_SETTLEMENT_NOT_IMPLEMENTED")
        result = final_by_event.get((sport, event))
        if final_count[(sport, event)] != 1:
            issues.append("OFFICIAL_FINAL_MISSING_OR_DUPLICATE")
        if (result is not None and
                settlement_receipts[_text(result.get("settlement_receipt_id"))] > 1):
            issues.append("DUPLICATE_OFFICIAL_SETTLEMENT_RECEIPT")
        issues.extend(_result_valid(p, result))
        grade = "HELD"
        if not issues and result is not None:
            if sport == "TENNIS" and result.get("final_status") == "VOID_PREMATCH_WITHDRAWAL":
                grade = "VOID"
            elif market == "MONEYLINE":
                if sport == "TENNIS":
                    winner = _text(result.get("winner_side")).upper()
                    if winner not in {"HOME", "AWAY"}:
                        issues.append("OFFICIAL_WINNER_INVALID")
                    else:
                        grade = "WIN" if side == winner else "LOSS"
                else:
                    home, away = result["home_score"], result["away_score"]
                    winner = "HOME" if home > away else ("AWAY" if away > home else "DRAW")
                    if winner == "DRAW" and sport != "SOCCER":
                        issues.append("NON_DRAW_MARKET_TIED")
                    else:
                        grade = "WIN" if side == winner else "LOSS"
            elif market == "SPREAD":
                margin = result["home_score"] - result["away_score"]
                signed_margin = margin if side == "HOME" else -margin
                value = signed_margin + float(line)
                grade = "WIN" if value > 0 else ("LOSS" if value < 0 else "PUSH")
            elif market == "TOTAL":
                value = result["home_score"] + result["away_score"] - float(line)
                grade = "PUSH" if value == 0 else ("WIN" if (value > 0) == (side == "OVER") else "LOSS")
        if issues:
            grade = "HELD"
        row = {
            "selection_id": selection_id, "cohort": cohort, "sport": sport,
            "official_event_id": event, "market_family": market, "selected_side": side,
            "grade_status": grade, "blockers": sorted(set(issues)),
            "source_class": "EXTERNAL_RESEARCH_ONLY",
            "fitted_model_grade": False, "can_execute": False,
        }
        rows.append(row)
        groups.setdefault(cohort, []).append(row)
    summaries = []
    for cohort in sorted(set(groups) | set(cohort_manifest or {})):
        group = groups.get(cohort, [])
        counts = Counter(r["grade_status"] for r in group)
        provided_ids = [r["selection_id"] for r in group]
        expected_ids = (cohort_manifest or {}).get(cohort)
        manifest_ok = (cohort_manifest_verified is True and bool(_text(cohort_manifest_receipt_id))
                       and isinstance(expected_ids, (list, tuple)) and len(expected_ids) > 0
                       and all(isinstance(i, str) and i for i in expected_ids)
                       and len(expected_ids) == len(set(expected_ids))
                       and Counter(provided_ids) == Counter(expected_ids))
        safe_ids = (isinstance(expected_ids, (list, tuple))
                    and all(isinstance(i, str) and i for i in expected_ids))
        missing = (list((Counter(expected_ids) - Counter(provided_ids)).elements())
                   if safe_ids else [])
        extra = (list((Counter(provided_ids) - Counter(expected_ids)).elements())
                 if safe_ids else [])
        eligible = bool(manifest_ok and counts["HELD"] == 0)
        graded = counts["WIN"] + counts["LOSS"]
        summaries.append({
            "cohort": cohort, "win": counts["WIN"], "loss": counts["LOSS"],
            "push": counts["PUSH"], "void": counts["VOID"], "held": counts["HELD"],
            "graded": graded, "manifest_missing_selection_ids": sorted(missing),
            "manifest_extra_selection_ids": sorted(extra),
            "headline_accuracy": (counts["WIN"] / graded if eligible and graded else None),
            "headline_eligible": eligible,
        })
    return {
        "rows": rows, "by_cohort": summaries, "cross_market_accuracy": None,
        "fitted_model_samples_added": 0, "model_certification_impact": False,
        "automatic_parameter_mutation": False, "can_execute": False,
    }
