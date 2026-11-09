"""Read-only NFL/NBA/NHL research-card retro; never a fitted-model grader.

Research winners and upset *watches* are separate cohorts. Only independently
verified immutable pre-kickoff picks and official finals can enter these counts.
The incumbent fitted-model grade remains exclusively in LLP's governed package
scorer. This module cannot recalibrate, amend a pick, or promote an upset.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

SPORTS = frozenset({"NFL", "NBA", "NHL"})
ROLES = frozenset({"PREFERRED", "UPSET_WATCH"})
HYPOTHESES = {
    "NFL": ("QB_EFFICIENCY_TURNOVER_SENSITIVITY", "PASS_PROTECTION_MATCHUP"),
    "NBA": ("AVAILABILITY_REPLACEMENT", "OPPONENT_OFFENSIVE_VOLATILITY"),
    "NHL": ("GOALIE_REST_TRAVEL_SPECIAL_TEAMS", "OVERTIME_AND_LATE_GAME_VARIANCE"),
}


def _name(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _key(row: Mapping[str, Any]) -> tuple[str, str]:
    return (_name(row.get("sport")).upper(), _name(row.get("official_event_id")))


def _moment(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return result.astimezone(timezone.utc) if result.tzinfo is not None else None


def _same_event(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    return (
        _key(a) == _key(b)
        and _moment(a.get("event_start_time_utc")) is not None
        and _moment(a.get("event_start_time_utc")) == _moment(b.get("event_start_time_utc"))
        and all(_name(a.get(p)).casefold() == _name(b.get(p)).casefold()
                for p in ("home_team", "away_team"))
    )


def _verify_pick(row: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    kickoff = _moment(row.get("event_start_time_utc"))
    frozen = _moment(row.get("frozen_at_utc"))
    visible = _moment(row.get("available_to_user_at_utc"))
    as_of = _moment(row.get("source_as_of_utc"))
    home, away, side = (_name(row.get(k)) for k in ("home_team", "away_team", "selected_side"))
    if not home or not away or home.casefold() == away.casefold() or side.casefold() not in (home.casefold(), away.casefold()):
        issues.append("PICK_SIDE_OR_TEAMS_INVALID")
    if row.get("immutable_pregame") is not True or row.get("pregame_receipt_verified") is not True or not _name(row.get("pregame_receipt_id")):
        issues.append("PREGAME_RECEIPT_UNVERIFIED")
    if kickoff is None or frozen is None or frozen >= kickoff:
        issues.append("PICK_NOT_FROZEN_PREGAME")
    if visible is None or frozen is None or visible > frozen:
        issues.append("PREGAME_VISIBILITY_UNVERIFIED")
    if as_of is None or frozen is None or as_of > frozen:
        issues.append("RESEARCH_AS_OF_NOT_PREGAME_FROZEN")
    inputs = row.get("research_inputs", [])
    if not isinstance(inputs, (list, tuple)):
        issues.append("RESEARCH_INPUTS_INVALID")
    else:
        for item in inputs:
            if not isinstance(item, Mapping):
                issues.append("RESEARCH_INPUTS_INVALID")
                continue
            when = _moment(item.get("available_to_user_at_utc"))
            if when is None or frozen is None or when > frozen:
                issues.append("HINDSIGHT_EVIDENCE_IN_FROZEN_PICK")
    return issues


def _verify_final(pick: Mapping[str, Any], final: Mapping[str, Any] | None) -> list[str]:
    if final is None:
        return ["OFFICIAL_FINAL_MISSING"]
    issues: list[str] = []
    if not _same_event(pick, final):
        issues.append("OFFICIAL_EVENT_IDENTITY_CONFLICT")
    if final.get("final_status") != "FINAL":
        issues.append("OFFICIAL_FINAL_NOT_FINAL")
    if final.get("independently_verified") is not True or not _name(final.get("settlement_receipt_id")) or not _name(final.get("settlement_source")):
        issues.append("OFFICIAL_FINAL_UNVERIFIED")
    winner = _name(final.get("winning_side"))
    if not winner or winner.casefold() not in {
        _name(final.get("home_team")).casefold(),
        _name(final.get("away_team")).casefold(),
    }:
        issues.append("OFFICIAL_WINNER_INVALID")
    event_time = _moment(final.get("event_start_time_utc"))
    settled_at = _moment(final.get("settled_at_utc"))
    if event_time is None or settled_at is None or settled_at <= event_time:
        issues.append("OFFICIAL_SETTLEMENT_TIME_INVALID")
    return issues


def _grade_counts(rows: Sequence[Mapping[str, Any]], role: str) -> dict[str, int]:
    selected = [r for r in rows if r["selection_role"] == role]
    wins = sum(r["grade_status"] == "WIN" for r in selected)
    losses = sum(r["grade_status"] == "LOSS" for r in selected)
    return {"correct": wins, "incorrect": losses, "graded": wins + losses}


def audit_research_card(
    picks: Sequence[Mapping[str, Any]],
    finals: Sequence[Mapping[str, Any]],
    *,
    official_slate: Sequence[Mapping[str, Any]] | None = None,
    official_slate_verified: bool = False,
    official_slate_receipt_id: str | None = None,
) -> dict[str, Any]:
    """Grade separate frozen research cohorts without any sporting-model credit.

    A verified official manifest is required for headline accuracy. Partial
    rows are diagnostic only; missing, conflicting, or late receipts are HELD.
    Attestation booleans are *inputs* that must come from independent checks;
    this module does not fetch league results or validate source authenticity.
    """
    source_picks, source_finals = list(picks), list(finals)
    if any(not isinstance(r, Mapping) for r in (*source_picks, *source_finals)):
        raise ValueError("RESEARCH_CARD_RECORD_NOT_OBJECT")
    board_blockers: list[str] = []
    final_keys = Counter(_key(r) for r in source_finals)
    final_lookup = {_key(r): r for r in source_finals}
    pick_keys = Counter((*_key(r), _name(r.get("selection_role")).upper()) for r in source_picks)
    primary_keys = {_key(r) for r in source_picks if _name(r.get("selection_role")).upper() == "PREFERRED"}
    preferred_sides = {_key(r): _name(r.get("selected_side")).casefold() for r in source_picks if _name(r.get("selection_role")).upper() == "PREFERRED"}
    manifest_keys: set[tuple[str, str]] = set()
    manifest_lookup: dict[tuple[str, str], Mapping[str, Any]] = {}
    if official_slate is None or official_slate_verified is not True or not _name(official_slate_receipt_id):
        board_blockers.append("OFFICIAL_SLATE_NOT_VERIFIED")
    else:
        for item in official_slate:
            if not isinstance(item, Mapping):
                board_blockers.append("OFFICIAL_SLATE_ROW_INVALID")
                continue
            key = _key(item)
            if key in manifest_keys or key[0] not in SPORTS or not key[1]:
                board_blockers.append("OFFICIAL_SLATE_DUPLICATE_OR_INVALID")
            manifest_keys.add(key)
            manifest_lookup[key] = item
        if not manifest_keys:
            board_blockers.append("OFFICIAL_SLATE_EMPTY")
        if manifest_keys - primary_keys:
            board_blockers.append("OFFICIAL_SLATE_PREFERRED_PICK_MISSING")
        if primary_keys - manifest_keys:
            board_blockers.append("PREFERRED_PICK_NOT_IN_OFFICIAL_SLATE")
    rows: list[dict[str, Any]] = []
    for forecast in source_picks:
        sport, event_id = _key(forecast)
        role = _name(forecast.get("selection_role")).upper()
        blocked: list[str] = []
        if sport not in SPORTS or not event_id:
            blocked.append("RESEARCH_CARD_IDENTITY_INVALID")
        if role not in ROLES:
            blocked.append("RESEARCH_CARD_ROLE_INVALID")
        if pick_keys[(sport, event_id, role)] > 1:
            blocked.append("DUPLICATE_CARD_ROLE_FOR_EVENT")
            board_blockers.append("DUPLICATE_CARD_ROLE_FOR_EVENT")
        if role == "UPSET_WATCH" and _name(forecast.get("selected_side")).casefold() == preferred_sides.get((sport, event_id)):
            blocked.append("UPSET_WATCH_NOT_ALTERNATIVE")
        blocked.extend(_verify_pick(forecast))
        final = final_lookup.get((sport, event_id))
        if final_keys[(sport, event_id)] > 1:
            blocked.append("DUPLICATE_OFFICIAL_FINAL")
            board_blockers.append("DUPLICATE_OFFICIAL_FINAL")
        blocked.extend(_verify_final(forecast, final))
        if manifest_lookup and (sport, event_id) in manifest_lookup and not _same_event(forecast, manifest_lookup[(sport, event_id)]):
            blocked.append("OFFICIAL_SLATE_IDENTITY_CONFLICT")
            board_blockers.append("OFFICIAL_SLATE_IDENTITY_CONFLICT")
        if manifest_keys and (sport, event_id) not in manifest_keys:
            blocked.append("PICK_NOT_IN_OFFICIAL_SLATE")
        grade = "HELD" if blocked else (
            "WIN" if _name(forecast.get("selected_side")).casefold() == _name(final.get("winning_side")).casefold() else "LOSS"
        )
        rows.append({
            "sport": sport,
            "official_event_id": event_id,
            "selection_role": role,
            "selected_side": _name(forecast.get("selected_side")),
            "grade_status": grade,
            "blockers": sorted(set(blocked)),
            "pregame_receipt_id": _name(forecast.get("pregame_receipt_id")) or None,
            "settlement_receipt_id": _name(final.get("settlement_receipt_id")) if final is not None else None,
            "source_class": "EXTERNAL_RESEARCH_ONLY",
            "fitted_model_grade": False,
            "automatic_model_mutation": False,
            "can_execute": False,
        })
    primary = _grade_counts(rows, "PREFERRED")
    watch = _grade_counts(rows, "UPSET_WATCH")
    preferred_grades = {(r["sport"], r["official_event_id"]): r["grade_status"] for r in rows if r["selection_role"] == "PREFERRED" and r["grade_status"] in {"WIN", "LOSS"}}
    paired = [r for r in rows if r["selection_role"] == "UPSET_WATCH" and r["grade_status"] in {"WIN", "LOSS"} and (r["sport"],r["official_event_id"]) in preferred_grades]
    pair_summary = {
        "preferred_correct": sum(preferred_grades[(r["sport"], r["official_event_id"])] == "WIN" for r in paired),
        "watch_correct": sum(r["grade_status"] == "WIN" for r in paired),
        "matched_events": len(paired),
    }
    by_sport = []
    for sport in sorted(SPORTS):
        sport_rows = [r for r in rows if r["sport"] == sport]
        if sport_rows:
            by_sport.append({"sport": sport, "preferred": _grade_counts(sport_rows, "PREFERRED"), "upset_watch": _grade_counts(sport_rows, "UPSET_WATCH")})
    hypotheses = [
        {"sport": r["sport"], "official_event_id": r["official_event_id"],
         "questions": list(HYPOTHESES[r["sport"]]),
         "finding_status": "HYPOTHESIS_NOT_VALIDATED",
         "requires_frozen_pregame_inputs": True,
         "requires_out_of_time_challenger_validation": True,
         "automatic_probability_change": False}
        for r in rows if r["selection_role"] == "PREFERRED" and r["grade_status"] == "LOSS" and r["sport"] in SPORTS
    ]
    if any(r["grade_status"] == "HELD" for r in rows):
        board_blockers.append("RESEARCH_OR_SETTLEMENT_ROWS_HELD")
    if manifest_keys and primary["graded"] != len(manifest_keys):
        board_blockers.append("OFFICIAL_SLATE_GRADED_COUNT_MISMATCH")
    board_complete = not board_blockers
    return {
        "serving_mode": "RETROSPECTIVE_RESEARCH_DIAGNOSTIC_ONLY",
        "board_complete": board_complete,
        "board_blockers": sorted(set(board_blockers)),
        "preferred": primary,
        "upset_watch": watch,
        "upset_pair_comparison": pair_summary,
        "by_sport": by_sport,
        "rows": rows,
        "learning_hypotheses": hypotheses,
        "headline_accuracy": primary["correct"] / primary["graded"] if board_complete and primary["graded"] else None,
        "fitted_model_samples_added": 0,
        "model_certification_impact": False,
        "gh_submitted_card_graded": False,
        "automatic_upset_flip": False,
        "automatic_parameter_mutation": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": False,
    }


__all__ = ["audit_research_card"]
