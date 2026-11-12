"""Immutable-evidence cross-sport *research* spread card and settlement diagnostic.

This is an LLP_TEAM_BETTING_ENGINE downstream presentation/retrospective helper,
not a sporting model, source authenticator, certification gate or terminal
publisher. Caller-supplied verification must be independently read back.
Never convert a winner, market quote or research narrative to cover probability.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Any, Mapping, Sequence

CAN_EXECUTE = False
CONTROLLING_ENGINE = "LLP_TEAM_BETTING_ENGINE"
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_CERTIFICATION = False
MAX_QUOTE_AGE = timedelta(minutes=60)
MAX_ABS_LINE = 200
RULES = {
    "NFL": {"POINT_SPREAD": ("FULL_GAME_INCLUDING_OVERTIME", "home_score", "away_score")},
    "NBA": {"POINT_SPREAD": ("FULL_GAME_INCLUDING_OVERTIME", "home_score", "away_score")},
    "WNBA": {"POINT_SPREAD": ("FULL_GAME_INCLUDING_OVERTIME", "home_score", "away_score")},
    "NCAAF": {"POINT_SPREAD": ("FULL_GAME_INCLUDING_OVERTIME", "home_score", "away_score")},
    "NCAAB": {"POINT_SPREAD": ("FULL_GAME_INCLUDING_OVERTIME", "home_score", "away_score")},
    "NHL": {"PUCK_LINE": ("FULL_GAME_INCLUDING_OVERTIME_SHOOTOUT", "home_score", "away_score")},
    "MLB": {"RUN_LINE": ("FULL_GAME_INCLUDING_EXTRA_INNINGS", "home_score", "away_score")},
    "SOCCER": {"ASIAN_HANDICAP": ("REGULATION", "home_score", "away_score")},
    "TENNIS": {
        "GAME_HANDICAP": ("COMPLETED_MATCH_NO_RETIREMENT", "home_games", "away_games"),
        "SET_HANDICAP": ("COMPLETED_MATCH_NO_RETIREMENT", "home_sets", "away_sets"),
    },
}
_NONPLAYABLE = frozenset({"CANCELLED", "POSTPONED"})
_PLAYABLE = frozenset({"SCHEDULED", "LIVE", "FINAL"})
_GRADED = frozenset({"WIN", "LOSS", "PUSH", "HALF_WIN", "HALF_LOSS"})


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo else None
    except (ValueError, OverflowError):
        return None


def _line(value: Any, *, soccer: bool) -> float | None:
    # Bound before float conversion to avoid overflow from arbitrary-size ints.
    if type(value) not in (int, float) or not -MAX_ABS_LINE <= value <= MAX_ABS_LINE:
        return None
    number = float(value)
    scale = 4 if soccer else 2
    if not isfinite(number) or abs(number * scale - round(number * scale)) > 1e-9:
        return None
    return number


def _key(row: Mapping[str, Any]) -> tuple[str, str]:
    return (_text(row.get("sport")).upper(), _text(row.get("official_event_id")))


def _identity(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    return (
        _key(a) == _key(b)
        and _utc(a.get("event_start_time_utc")) is not None
        and _utc(a.get("event_start_time_utc")) == _utc(b.get("event_start_time_utc"))
        and all(_text(a.get(k)).casefold() == _text(b.get(k)).casefold()
                for k in ("home_team", "away_team"))
        and _text(a.get("market_type")).upper() == _text(b.get("market_type")).upper()
        and _text(a.get("settlement_basis")).upper() == _text(b.get("settlement_basis")).upper()
    )


def _valid_manifest(event: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    sport, event_id = _key(event)
    home, away = _text(event.get("home_team")), _text(event.get("away_team"))
    typ = _text(event.get("market_type")).upper()
    cfg = RULES.get(sport, {}).get(typ)
    if not sport or not event_id or not home or not away or home.casefold() == away.casefold() or _utc(event.get("event_start_time_utc")) is None:
        errors.append("OFFICIAL_EVENT_IDENTITY_INVALID")
    if cfg is None or _text(event.get("settlement_basis")).upper() != cfg[0]:
        errors.append("SPORT_MARKET_FORMAT_UNSUPPORTED")
    if _text(event.get("event_state")).upper() not in (_PLAYABLE | _NONPLAYABLE):
        errors.append("OFFICIAL_EVENT_STATE_INVALID")
    return errors


def _check_pick(event: Mapping[str, Any], row: Mapping[str, Any],
                *, receipt_counts: Counter[str], quote_counts: Counter[str]) -> list[str]:
    errors: list[str] = []
    kickoff = _utc(event.get("event_start_time_utc"))
    frozen = _utc(row.get("frozen_at_utc"))
    visible = _utc(row.get("available_to_user_at_utc"))
    if not _identity(event, row) or _text(row.get("selected_side")).upper() not in {"HOME", "AWAY"}:
        errors.append("PREGAME_EVENT_OR_SIDE_MISMATCH")
    if frozen is None or kickoff is None or frozen >= kickoff:
        errors.append("PREGAME_FREEZE_INVALID")
    if visible is None or frozen is None or visible > frozen:
        errors.append("PREGAME_VISIBILITY_INVALID")
    receipt = _text(row.get("pregame_receipt_id"))
    if (row.get("immutable_pregame") is not True or row.get("pregame_receipt_verified") is not True
            or not receipt or receipt_counts[receipt] != 1):
        errors.append("PREGAME_RECEIPT_UNVERIFIED_OR_REUSED")
    line = _line(row.get("signed_home_line"), soccer=_key(event)[0] == "SOCCER")
    if line is None:
        errors.append("EXACT_LINE_INVALID")
    if row.get("can_execute") is True:
        errors.append("EXECUTION_INVARIANT_VIOLATION")
    quote = row.get("quote")
    if not isinstance(quote, Mapping):
        errors.append("EXACT_QUOTE_MISSING")
    else:
        quote_line = _line(quote.get("signed_home_line"), soccer=_key(event)[0] == "SOCCER")
        if not _identity(event, quote) or line is None or quote_line != line:
            errors.append("EXACT_QUOTE_IDENTITY_OR_LINE_MISMATCH")
        qreceipt = _text(quote.get("receipt_id"))
        if qreceipt and quote_counts[qreceipt] != 1:
            errors.append("QUOTE_RECEIPT_REUSED")
        if quote.get("independently_verified") is not True or not qreceipt:
            errors.append("EXACT_QUOTE_UNVERIFIED")
        at = _utc(quote.get("observed_at_utc"))
        qvisible = _utc(quote.get("available_to_user_at_utc"))
        if (frozen is None or kickoff is None or at is None or qvisible is None
                or at > frozen or at >= kickoff or qvisible > frozen or qvisible < at
                or frozen - at > MAX_QUOTE_AGE):
            errors.append("EXACT_QUOTE_NOT_FRESH_PREGAME")
    return errors


def _final_grade(event: Mapping[str, Any], row: Mapping[str, Any], final: Mapping[str, Any] | None,
                 *, receipt_counts: Counter[str]) -> tuple[str, list[str]]:
    if final is None or _text(final.get("final_status")).upper() in {"", "SCHEDULED", "LIVE", "IN_PROGRESS"}:
        return "PENDING", []
    if _key(event)[0] == "TENNIS" and _text(final.get("final_status")).upper() == "RETIRED":
        return "HELD", ["TENNIS_RETIREMENT_RULE_UNVERIFIED"]
    errors: list[str] = []
    receipt = _text(final.get("settlement_receipt_id"))
    if not _identity(event, final) or _text(final.get("final_status")).upper() != "FINAL":
        errors.append("OFFICIAL_FINAL_IDENTITY_OR_STATE_INVALID")
    if final.get("independently_verified") is not True or not receipt or not _text(final.get("settlement_source")) or receipt_counts[receipt] != 1:
        errors.append("OFFICIAL_FINAL_RECEIPT_UNVERIFIED_OR_REUSED")
    when = _utc(final.get("settled_at_utc"))
    kickoff = _utc(event.get("event_start_time_utc"))
    if when is None or kickoff is None or when <= kickoff:
        errors.append("OFFICIAL_FINAL_TIMESTAMP_INVALID")
    sport, _ = _key(event)
    market_type = _text(event.get("market_type")).upper()
    rule = RULES.get(sport, {}).get(market_type)
    if rule is None:
        errors.append("SPORT_MARKET_FORMAT_UNSUPPORTED")
    if errors:
        return "HELD", errors
    if (sport == "TENNIS" and final.get("deciding_match_tiebreak") is True
            and final.get("provider_handicap_rule_verified") is not True):
        return "HELD", ["TENNIS_MATCH_TIEBREAK_SETTLEMENT_UNVERIFIED"]
    if (sport == "MLB" and final.get("shortened_game") is True
            and final.get("provider_settlement_rule_verified") is not True):
        return "HELD", ["SHORTENED_GAME_SETTLEMENT_UNVERIFIED"]
    assert rule is not None
    home = final.get(rule[1])
    away = final.get(rule[2])
    if (type(home) is not int or type(away) is not int or not 0 <= home <= 10_000
            or not 0 <= away <= 10_000):
        return "HELD", ["OFFICIAL_SPREAD_SETTLEMENT_UNITS_INVALID"]
    line = _line(row.get("signed_home_line"), soccer=sport == "SOCCER")
    if line is None:
        return "HELD", ["EXACT_LINE_INVALID"]
    winner = _text(final.get("winning_side")).upper()
    if winner and sport == "TENNIS" and market_type == "GAME_HANDICAP":
        # Match winner follows sets, not accumulated games. Never compare a
        # match-winner label to game-handicap settlement units.
        home_sets, away_sets = final.get("home_sets"), final.get("away_sets")
        if type(home_sets) is int and type(away_sets) is int and home_sets >= 0 and away_sets >= 0:
            winner_home, winner_away = home_sets, away_sets
        else:
            winner_home = winner_away = None
    else:
        winner_home, winner_away = home, away
    if winner and winner_home is not None and winner_away is not None:
        home_name = _text(event.get("home_team")).upper()
        away_name = _text(event.get("away_team")).upper()
        expected = "HOME" if winner_home > winner_away else "AWAY" if winner_away > winner_home else "DRAW"
        aliases = {"HOME": "HOME", home_name: "HOME", "AWAY": "AWAY",
                   away_name: "AWAY", "DRAW": "DRAW", "TIE": "DRAW"}
        if aliases.get(winner) != expected:
            return "HELD", ["OFFICIAL_SCORE_WINNER_CONFLICT"]
    margin = home - away
    # Quarter Asian handicaps split evenly across the surrounding half-lines.
    quarters = round(line * 4)
    lines = (line - 0.25, line + 0.25) if sport == "SOCCER" and quarters % 2 else (line,)
    selected = _text(row.get("selected_side")).upper()
    units = []
    for threshold in lines:
        adjusted = margin + threshold
        if abs(adjusted) < 1e-9:
            units.append("PUSH")
        elif (adjusted > 0) == (selected == "HOME"):
            units.append("WIN")
        else:
            units.append("LOSS")
    if len(units) == 1 or units[0] == units[1]:
        return units[0], []
    if "PUSH" in units and "WIN" in units:
        return "HALF_WIN", []
    if "PUSH" in units and "LOSS" in units:
        return "HALF_LOSS", []
    # If the score/line pair ever produces incompatible half results, hold.
    return "HELD", ["QUARTER_HANDICAP_SETTLEMENT_AMBIGUOUS"]


def audit_spread_research_card(
    official_slate: Sequence[Mapping[str, Any]],
    picks: Sequence[Mapping[str, Any]],
    finals: Sequence[Mapping[str, Any]] = (),
    *,
    official_slate_verified: bool = False,
    official_slate_receipt_id: str | None = None,
) -> dict[str, Any]:
    """Return visible full-slate exact-line research rows, never fitted scores.

    Attestations are caller claims and MUST be independently authenticated
    upstream; no source lookup or automatic promotion occurs in this function.
    """
    if any(not isinstance(x, Mapping) for x in (*official_slate, *picks, *finals)):
        raise ValueError("SPREAD_RESEARCH_ROW_NOT_OBJECT")
    manifest_keys = Counter(_key(x) for x in official_slate)
    pick_keys = Counter(_key(x) for x in picks)
    final_keys = Counter(_key(x) for x in finals)
    pick_receipts = Counter(_text(x.get("pregame_receipt_id")) for x in picks if _text(x.get("pregame_receipt_id")))
    quote_receipts = Counter(_text(x.get("quote", {}).get("receipt_id")) for x in picks
                             if isinstance(x.get("quote"), Mapping) and _text(x["quote"].get("receipt_id")))
    final_receipts = Counter(_text(x.get("settlement_receipt_id")) for x in finals if _text(x.get("settlement_receipt_id")))
    pick_map = {_key(x): x for x in picks}
    final_map = {_key(x): x for x in finals}
    board_blockers: list[str] = []
    if not official_slate or official_slate_verified is not True or not _text(official_slate_receipt_id):
        board_blockers.append("OFFICIAL_SLATE_NOT_VERIFIED")
    rows: list[dict[str, Any]] = []
    for event in official_slate:
        sport, key = _key(event)
        market = _text(event.get("market_type")).upper()
        basis = _text(event.get("settlement_basis")).upper()
        state = _text(event.get("event_state")).upper()
        playable = state not in _NONPLAYABLE
        problems = _valid_manifest(event)
        if manifest_keys[(sport, key)] != 1:
            problems.append("DUPLICATE_OFFICIAL_EVENT")
        if pick_keys[(sport, key)] > 1:
            problems.append("DUPLICATE_PREGAME_PICK")
        if final_keys[(sport, key)] > 1:
            problems.append("DUPLICATE_OFFICIAL_FINAL")
        p = pick_map.get((sport, key))
        if playable and p is None:
            problems.append("PREGAME_PICK_MISSING")
        if playable and p is not None:
            problems.extend(_check_pick(event, p, receipt_counts=pick_receipts,
                                       quote_counts=quote_receipts))
        selected = _text(p.get("selected_side")).upper() if p is not None else None
        parsed_line = _line(p.get("signed_home_line"), soccer=sport == "SOCCER") if p is not None else None
        status = "NOT_PLAYABLE" if not playable else "HELD" if problems else "VALID"
        grade, grading_problems = ("NOT_PLAYABLE", []) if not playable else (
            ("HELD", []) if status == "HELD" else _final_grade(
                event, p, final_map.get((sport, key)), receipt_counts=final_receipts)
        )
        problems.extend(grading_problems)
        rows.append({
            "sport": sport, "official_event_id": key,
            "home_team": _text(event.get("home_team")), "away_team": _text(event.get("away_team")),
            "event_start_time_utc": _text(event.get("event_start_time_utc")),
            "event_state": state, "market_type": market, "settlement_basis": basis,
            "in_official_slate": True,
            "playable": playable, "selected_side": selected or None,
            "signed_home_line": parsed_line, "selected_signed_line": (
                parsed_line if selected == "HOME" else -parsed_line if selected == "AWAY" and parsed_line is not None else None
            ),
            "pregame_status": status, "grade_status": grade,
            "research_status": "PROVISIONAL_RESEARCH_ONLY" if status == "VALID" else status,
            "blockers": sorted(set(problems)),
            "quote_receipt_id": _text(p.get("quote", {}).get("receipt_id")) if p and isinstance(p.get("quote"), Mapping) else None,
            "pregame_receipt_id": _text(p.get("pregame_receipt_id")) if p else None,
            "settlement_receipt_id": _text(final_map.get((sport, key), {}).get("settlement_receipt_id")),
            "model_probability": None, "calibrated_lower_bound": None,
            "rank_eligible": False, "probability_publishable": False, "can_execute": False,
        })
    # Extra picks/finals must not disappear silently from the audit denominator.
    slate_set = set(manifest_keys)
    unmatched_picks = [p for p in picks if _key(p) not in slate_set]
    unmatched_finals = [f for f in finals if _key(f) not in slate_set]
    if unmatched_picks:
        board_blockers.append("PICK_NOT_IN_OFFICIAL_SLATE")
    if unmatched_finals:
        board_blockers.append("FINAL_NOT_IN_OFFICIAL_SLATE")
    for p in unmatched_picks:
        sport, key = _key(p)
        rows.append({
            "sport": sport, "official_event_id": key,
            "home_team": _text(p.get("home_team")), "away_team": _text(p.get("away_team")),
            "event_start_time_utc": _text(p.get("event_start_time_utc")),
            "event_state": None, "market_type": _text(p.get("market_type")).upper(),
            "settlement_basis": _text(p.get("settlement_basis")).upper(),
            "in_official_slate": False, "playable": False,
            "selected_side": _text(p.get("selected_side")).upper() or None,
            "signed_home_line": None, "selected_signed_line": None,
            "pregame_status": "HELD", "grade_status": "HELD", "research_status": "HELD",
            "blockers": ["PICK_NOT_IN_OFFICIAL_SLATE"],
            "quote_receipt_id": None,
            "pregame_receipt_id": _text(p.get("pregame_receipt_id")) or None,
            "settlement_receipt_id": None, "model_probability": None,
            "calibrated_lower_bound": None, "rank_eligible": False,
            "probability_publishable": False, "can_execute": False,
        })
    if any(row["pregame_status"] == "HELD" for row in rows):
        board_blockers.append("PREGAME_RESEARCH_ROWS_HELD")
    playable_rows = [r for r in rows if r["playable"]]
    settled = [r for r in playable_rows if r["grade_status"] in _GRADED]
    held = [r for r in playable_rows if r["grade_status"] == "HELD"]
    pending = [r for r in playable_rows if r["grade_status"] == "PENDING"]
    card_complete = bool(playable_rows) and not board_blockers
    settlement_complete = card_complete and not pending and not held
    full_win = sum(r["grade_status"] == "WIN" for r in settled)
    full_loss = sum(r["grade_status"] == "LOSS" for r in settled)
    half_count = sum(r["grade_status"] in {"HALF_WIN", "HALF_LOSS"} for r in settled)
    by_sport = []
    for sport in sorted({r["sport"] for r in rows}):
        group = [r for r in rows if r["sport"] == sport]
        by_sport.append({"sport": sport, "scheduled": sum(r["in_official_slate"] for r in group),
                         "playable": sum(r["playable"] for r in group),
                         "pregame_valid": sum(r["pregame_status"] == "VALID" for r in group),
                         "verified_settled": sum(r["grade_status"] in _GRADED for r in group),
                         "pending": sum(r["grade_status"] == "PENDING" for r in group),
                         "held": sum(r["grade_status"] == "HELD" for r in group)})
    return {
        "contract": "LLP_SPREAD_RESEARCH_FULL_SLATE_V1",
        "controlling_engine_identity": CONTROLLING_ENGINE,
        "terminal_authority": TERMINAL_AUTHORITY,
        "scheduled_count": len(official_slate), "playable_count": len(playable_rows),
        "unmatched_pick_count": len(unmatched_picks),
        "unmatched_final_count": len(unmatched_finals),
        "card_complete": card_complete, "settlement_complete": settlement_complete,
        "board_blockers": sorted(set(board_blockers)),
        "pregame_valid_count": sum(r["pregame_status"] == "VALID" for r in playable_rows),
        "verified_settled_count": len(settled), "pending_count": len(pending),
        "held_count": len(held), "push_count": sum(r["grade_status"] == "PUSH" for r in settled),
        "half_win_count": sum(r["grade_status"] == "HALF_WIN" for r in settled),
        "half_loss_count": sum(r["grade_status"] == "HALF_LOSS" for r in settled),
        "full_win_count": full_win, "full_loss_count": full_loss,
        "headline_full_win_rate": (
            full_win / (full_win + full_loss) if settlement_complete and not half_count and full_win + full_loss else None
        ),
        "market_edge_established": False, "fitted_model_samples_added": 0,
        "probability_publishable": False, "automatic_certification": False,
        "automatic_promotion": False, "source_authentication_requires_independent_readback": True,
        "by_sport": by_sport, "rows": rows, "can_execute": False,
    }


__all__ = ["audit_spread_research_card"]
