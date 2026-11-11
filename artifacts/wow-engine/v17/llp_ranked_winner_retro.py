"""Read-only ranked cross-sport winner retro against a frozen market favorite.

This is an EXTERNAL_RESEARCH_ONLY diagnostic, not an LLP fitted-model grader.
It can audit a deliberately ranked subset without presenting it as a full slate.
Market odds are only a same-time comparison, never specialist probabilities.
Independent source/receipt authentication happens upstream, not in this helper.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from math import isfinite
from typing import Any, Mapping, Sequence

SPORTS = frozenset({"NFL", "NBA", "NHL", "NCAAF", "NCAAB", "UWCL"})
THREE_WAY = frozenset({"UWCL"})
CONTEXT = {
    "NCAAB": ("opponent_quality", "half_court_defense", "three_point_variance", "venue"),
    "NCAAF": ("rushing_matchup", "turnovers", "quarterback_availability"),
    "UWCL": ("lineup_rotation", "draw_path", "finishing_variance"),
    "NBA": ("injury_rotation", "pace", "three_point_variance"),
    "NHL": ("starting_goalie", "rest", "special_teams", "ot_so"),
    "NFL": ("starting_qb", "injuries", "turnovers"),
}
CAN_EXECUTE = False


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return d.astimezone(timezone.utc) if d.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def _key(row: Mapping[str, Any]) -> tuple[str, str]:
    return (_text(row.get("sport")).upper(), _text(row.get("official_event_id")))


def _identity(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    return (_key(a) == _key(b)
            and _utc(a.get("event_start_time_utc")) is not None
            and _utc(a.get("event_start_time_utc")) == _utc(b.get("event_start_time_utc"))
            and all(_text(a.get(k)).casefold() == _text(b.get(k)).casefold()
                    for k in ("home_team", "away_team")))


def _market(row: Mapping[str, Any], frozen: datetime | None) -> tuple[str | None, list[str]]:
    """Return unique highest implied-odds outcome; no modeled probabilities."""
    if not isinstance(row, Mapping):
        return None, ["MARKET_SNAPSHOT_MISSING"]
    problems = []
    when = _utc(row.get("as_of_utc"))
    if row.get("independently_verified") is not True or not _text(row.get("receipt_id")):
        problems.append("MARKET_RECEIPT_UNVERIFIED")
    if when is None or frozen is None or when > frozen:
        problems.append("MARKET_NOT_VISIBLE_AT_FREEZE")
    sport = _text(row.get("sport")).upper()
    expected = {"HOME", "AWAY", "DRAW"} if sport in THREE_WAY else {"HOME", "AWAY"}
    if row.get("market_type") != ("REGULATION_3WAY" if sport in THREE_WAY else "FULL_GAME_2WAY"):
        problems.append("MARKET_SETTLEMENT_BASIS_INVALID")
    quotes = row.get("decimal_odds")
    if not isinstance(quotes, Mapping) or set(quotes) != expected:
        problems.append("MARKET_OUTCOME_SET_INVALID")
        return None, problems
    if any(type(v) not in (float, int) or not isfinite(v) or v <= 1 for v in quotes.values()):
        problems.append("MARKET_PRICE_INVALID")
        return None, problems
    if problems:
        return None, problems
    smallest = min(quotes.values())
    favorites = [side for side, price in quotes.items() if price == smallest]
    return (favorites[0], []) if len(favorites) == 1 else (None, ["MARKET_FAVORITE_TIED"])


def audit_ranked_winner_retro(
    picks: Sequence[Mapping[str, Any]],
    finals: Sequence[Mapping[str, Any]],
    *,
    ranked_cohort: Sequence[Mapping[str, Any]] | None = None,
    cohort_verified: bool = False,
    cohort_receipt_id: str | None = None,
    cohort_kind: str = "RANKED_SUBSET",
) -> dict[str, Any]:
    """Audit frozen picks without conflating a top-N subset with a full slate.

    Input attestation is caller-supplied and MUST be independently checked;
    this helper does not independently obtain odds, results or receipts.
    For UWCL, team picks lose on regulation draws: no two-way simplification.
    Separate signed spread diagnostics are never graded as winner predictions.
    """
    if any(not isinstance(r, Mapping) for r in (*picks, *finals, *(ranked_cohort or []))):
        raise ValueError("RANKED_RETRO_ROW_NOT_OBJECT")
    counts = Counter(_key(p) for p in picks)
    final_counts = Counter(_key(f) for f in finals)
    result_map = {_key(f): f for f in finals}
    cohort = list(ranked_cohort or [])
    cohort_counts = Counter(_key(c) for c in cohort)
    cohort_map = {_key(c): c for c in cohort}
    receipt_counts = Counter(_text(p.get("pregame_receipt_id")) for p in picks
                             if _text(p.get("pregame_receipt_id")))
    final_receipts = Counter(_text(f.get("settlement_receipt_id")) for f in finals
                             if _text(f.get("settlement_receipt_id")))
    market_receipts = Counter(_text(p.get("market", {}).get("receipt_id"))
                              for p in picks if isinstance(p.get("market"), Mapping)
                              and _text(p["market"].get("receipt_id")))
    cohort_ok = (cohort_verified is True and bool(_text(cohort_receipt_id))
                 and cohort_kind in {"RANKED_SUBSET", "VERIFIED_FULL_SLATE"}
                 and bool(cohort) and all(c == 1 for c in cohort_counts.values())
                 and set(cohort_counts) == set(counts)
                 and all(v == 1 for v in counts.values()))
    rows = []
    for p in picks:
        sport, event_id = _key(p)
        chosen = _text(p.get("selected_side")).upper()
        kickoff = _utc(p.get("event_start_time_utc"))
        frozen = _utc(p.get("frozen_at_utc"))
        final = result_map.get((sport, event_id))
        errors = []
        if (sport not in SPORTS or not event_id
            or not _text(p.get("home_team")) or not _text(p.get("away_team"))
            or _text(p.get("home_team")).casefold() == _text(p.get("away_team")).casefold()
            or chosen not in {"HOME", "AWAY"}):
            errors.append("EVENT_OR_WINNER_IDENTITY_INVALID")
        if counts[(sport, event_id)] != 1:
            errors.append("DUPLICATE_RANKED_SELECTION")
        if kickoff is None or frozen is None or frozen >= kickoff:
            errors.append("PREGAME_FREEZE_INVALID")
        if (p.get("immutable_pregame") is not True
            or p.get("pregame_receipt_verified") is not True
            or not _text(p.get("pregame_receipt_id"))
            or receipt_counts[_text(p.get("pregame_receipt_id"))] != 1):
            errors.append("PREGAME_RECEIPT_UNVERIFIED")
        visible = _utc(p.get("available_to_user_at_utc"))
        if visible is None or frozen is None or visible > frozen:
            errors.append("PREGAME_VISIBILITY_INVALID")
        if not cohort_ok or (sport, event_id) not in cohort_map or not _identity(p, cohort_map.get((sport, event_id), {})):
            errors.append("RANKED_COHORT_NOT_INDEPENDENTLY_RECONCILED")
        if final is None or final_counts[(sport, event_id)] != 1:
            errors.append("OFFICIAL_FINAL_MISSING_OR_DUPLICATE")
        elif (not _identity(p, final) or final.get("final_status") != "FINAL"
              or final.get("independently_verified") is not True
              or not _text(final.get("settlement_receipt_id"))
              or final_receipts[_text(final.get("settlement_receipt_id"))] != 1
              or not _text(final.get("settlement_source"))
              or _utc(final.get("settled_at_utc")) is None
              or kickoff is None or _utc(final.get("settled_at_utc")) <= kickoff):
            errors.append("OFFICIAL_SETTLEMENT_UNVERIFIED")
        expected_basis = "REGULATION" if sport in THREE_WAY else "FULL_GAME"
        outcome = _text(final.get("outcome")).upper() if final else ""
        if final is not None and (final.get("settlement_basis") != expected_basis
                                  or outcome not in ({"HOME", "AWAY", "DRAW"} if sport in THREE_WAY else {"HOME", "AWAY"})):
            errors.append("OFFICIAL_OUTCOME_OR_BASIS_INVALID")
        if final is not None and sport not in THREE_WAY and outcome == "DRAW":
            errors.append("NON_SOCCER_DRAW_NEEDS_SEPARATE_TIE_RULE")
        scores = None
        if final is not None:
            home_score, away_score = final.get("home_score"), final.get("away_score")
            if (type(home_score) is not int or type(away_score) is not int
                or home_score < 0 or away_score < 0):
                errors.append("OFFICIAL_SCORE_INVALID")
            else:
                scores = (home_score, away_score)
                score_outcome = "HOME" if home_score > away_score else "AWAY" if away_score > home_score else "DRAW"
                if score_outcome != outcome:
                    errors.append("OFFICIAL_SCORE_OUTCOME_CONFLICT")
        # Market research remains separate; a missing price does not make a
        # verified frozen winner wrong, but prevents market-edge claims.
        market = p.get("market")
        favorite, market_problems = _market(market, frozen)
        if isinstance(market, Mapping):
            if (_text(market.get("sport")).upper() != sport or
                market_receipts[_text(market.get("receipt_id"))] != 1):
                market_problems.append("MARKET_IDENTITY_OR_RECEIPT_REUSED")
        valid = not errors
        pick_grade = ("WIN" if chosen == outcome else "LOSS") if valid else "HELD"
        market_grade = ("WIN" if favorite == outcome else "LOSS") if valid and favorite else "UNAVAILABLE"
        # Signed home-line cover is an isolated diagnostic only.
        spread_grade = "NOT_EVALUATED"
        spread = p.get("spread")
        if (valid and scores is not None and isinstance(spread, Mapping)
            and spread.get("independently_verified") is True
            and _text(spread.get("receipt_id"))
            and (_utc(spread.get("as_of_utc")) is not None)
            and frozen is not None and _utc(spread.get("as_of_utc")) <= frozen
            and spread.get("settlement_basis") == expected_basis
            and type(spread.get("home_line")) in (int, float)
            and isfinite(spread["home_line"])):
            adjusted = scores[0] - scores[1] + spread["home_line"]
            spread_grade = "HOME_COVER" if adjusted > 0 else "AWAY_COVER" if adjusted < 0 else "PUSH"
        checks = p.get("context_checks")
        checks = checks if isinstance(checks, Mapping) else {}
        context_warnings = [f"PREGAME_CONTEXT_UNRECONCILED:{k}" for k in CONTEXT.get(sport, ())
                            if _text(checks.get(k)).upper() != "KNOWN"]
        rows.append({
            "sport": sport, "official_event_id": event_id,
            "selection_grade": pick_grade, "market_favorite_grade": market_grade,
            "market_favorite": favorite if valid else None,
            "different_from_market_favorite": (chosen != favorite if valid and favorite else None),
            "official_outcome": outcome if valid else None,
            "selection_blockers": sorted(set(errors)),
            "market_blockers": sorted(set(market_problems)),
            "context_warnings": context_warnings,
            "spread_outcome_diagnostic_only": spread_grade,
            "spread_was_selected_as_pick": False,
            "narrative_causality_proven": False,
            "source_class": "EXTERNAL_RESEARCH_ONLY",
            "fitted_model_grade": False, "can_execute": False,
        })
    complete = bool(picks) and cohort_ok and all(r["selection_grade"] != "HELD" for r in rows)
    market_complete = complete and all(r["market_favorite_grade"] != "UNAVAILABLE" for r in rows)
    sport_rows = []
    for sport in sorted({r["sport"] for r in rows}):
        group = [r for r in rows if r["sport"] == sport]
        sport_rows.append({"sport": sport, "graded": sum(r["selection_grade"] != "HELD" for r in group),
                           "correct": sum(r["selection_grade"] == "WIN" for r in group)})
    return {
        "research_scope": cohort_kind,
        "cohort_complete": complete,
        "market_baseline_complete": market_complete,
        "graded": len(rows) if complete else None,
        "correct": sum(r["selection_grade"] == "WIN" for r in rows) if complete else None,
        "subset_accuracy": (sum(r["selection_grade"] == "WIN" for r in rows) / len(rows)) if complete else None,
        "market_favorite_correct": (sum(r["market_favorite_grade"] == "WIN" for r in rows)
                                    if market_complete else None),
        "incremental_correct_vs_market": (sum(r["selection_grade"] == "WIN" for r in rows)
                                          - sum(r["market_favorite_grade"] == "WIN" for r in rows)
                                          if market_complete else None),
        "market_edge_established": False,
        "fitted_model_samples_added": 0,
        "gh_submitted_card_graded": False,
        "automatic_probability_change": False,
        "automatic_winner_change": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": False,
        "by_sport": sport_rows, "rows": rows,
        "input_attestation_requires_independent_readback": True,
    }


__all__ = ["audit_ranked_winner_retro"]
