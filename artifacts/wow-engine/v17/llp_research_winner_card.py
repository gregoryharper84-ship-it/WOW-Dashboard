"""Read-only pregame coverage audit for human/LLP research winner cards.

A user may request one winner for every NFL/NBA/NHL game even when the fitted
specialist is unavailable. This audits those *research* selections, never
creates a winner, changes a governed model/terminal, or confers certification.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from v17.llp_matchup_media_script_audit import audit_script_robustness_and_media

SPORTS = frozenset({"NFL", "NBA", "NHL"})
CHECKS = {
    "NFL": ("starting_qb", "key_injuries", "turnover_volatility"),
    "NBA": ("star_availability", "replacement_rotation", "rest_travel", "shooting_volatility"),
    "NHL": ("starting_goalie", "key_injuries", "rest_travel", "special_teams", "ot_shootout"),
}
NO_WINNER_TOKENS = frozenset({"", "PASS", "HOLD", "NO_PICK", "TBD", "UNKNOWN"})
CAN_EXECUTE = False


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo is not None else None


def _key(row: Mapping[str, Any]) -> tuple[str, str]:
    return _text(row.get("sport")).upper(), _text(row.get("official_event_id"))


def _unsupported_script_numeric_key(key: Any) -> bool:
    """Reject numeric-likelihood aliases, not ordinary research metadata."""
    if not isinstance(key, str):
        return True
    token = key.casefold().replace("-", "_")
    return any(word in token for word in ("prob", "odds", "weight", "likelihood", "chance", "win_pct"))


def audit_research_winner_card(
    official_slate: Sequence[Mapping[str, Any]],
    selections: Sequence[Mapping[str, Any]],
    *,
    official_slate_verified: bool = False,
    official_slate_receipt_id: str | None = None,
) -> dict[str, Any]:
    """Audit one research pick per official scheduled game without making picks.

    Missing/conflicting information creates *research evidence warnings*, not
    an automatic pass. A missing selection remains incomplete instead of
    silently inventing a winner. The qualified fitted model board is separate.
    """
    if any(not isinstance(x, Mapping) for x in (*official_slate, *selections)):
        raise ValueError("WINNER_CARD_ROW_NOT_OBJECT")
    manifest = list(official_slate)
    picks = list(selections)
    source_counts = Counter(_key(row) for row in manifest)
    pick_counts = Counter(_key(row) for row in picks)
    pick_lookup = {_key(row): row for row in picks}
    manifest_keys = set(source_counts)
    problems: list[str] = []
    if official_slate_verified is not True or not _text(official_slate_receipt_id):
        problems.append("OFFICIAL_SLATE_UNVERIFIED")
    if not manifest:
        problems.append("EMPTY_OFFICIAL_SLATE")
    if any(count > 1 for count in source_counts.values()):
        problems.append("DUPLICATE_OFFICIAL_EVENT")
    if any(count > 1 for count in pick_counts.values()):
        problems.append("DUPLICATE_RESEARCH_SELECTION")
    if any(k not in manifest_keys for k in pick_counts):
        problems.append("EXTRA_RESEARCH_SELECTION_NOT_ON_SLATE")

    rows: list[dict[str, Any]] = []
    for event in manifest:
        sport, event_id = _key(event)
        home, away = _text(event.get("home_team")), _text(event.get("away_team"))
        kickoff = _utc(event.get("event_start_time_utc"))
        # Never attribute evidence to whichever duplicate happened to be last.
        chosen = pick_lookup.get((sport, event_id), {}) if pick_counts[(sport, event_id)] == 1 else {}
        side = _text(chosen.get("selected_side"))
        when = _utc(chosen.get("selected_at_utc"))
        issues: list[str] = []
        warnings: list[str] = []
        if sport not in SPORTS or not event_id or not home or not away or home.casefold() == away.casefold() or kickoff is None:
            issues.append("OFFICIAL_EVENT_IDENTITY_INVALID")
        if source_counts[(sport, event_id)] != 1:
            # A conflicting duplicate official-event record is not a valid
            # source for ANY pregame pick, media or scenario attribution.
            issues.append("OFFICIAL_EVENT_IDENTITY_CONFLICT")
        if pick_counts[(sport, event_id)] != 1:
            issues.append("MISSING_OR_DUPLICATE_WINNER")
        if chosen and (
            _text(chosen.get("home_team")).casefold() != home.casefold()
            or _text(chosen.get("away_team")).casefold() != away.casefold()
            or _utc(chosen.get("event_start_time_utc")) != kickoff
        ):
            # No evidence from an unmatched or rescheduled event may be
            # treated as pregame evidence for the verified manifest event.
            issues.append("RESEARCH_SELECTION_EVENT_IDENTITY_CONFLICT")
        if side.casefold() not in {home.casefold(), away.casefold()} or side.upper() in NO_WINNER_TOKENS:
            issues.append("INVALID_OR_PASS_WINNER")
        if when is None or kickoff is None or when >= kickoff:
            issues.append("RESEARCH_PICK_NOT_PREGAME")
        # Any invalid/late/duplicate selection has NO admissible pregame evidence.
        # Do not expose post-kickoff press or script material as pregame context.
        admissible = chosen if not issues else {}
        if not _text(admissible.get("reasoning")):
            warnings.append("REASONING_MISSING")
        checks = admissible.get("pregame_checks")
        checks = checks if isinstance(checks, Mapping) else {}
        for name in CHECKS.get(sport, ()):
            state = _text(checks.get(name)).upper()
            if state not in {"KNOWN", "UNKNOWN", "CONTRADICTORY"}:
                warnings.append("CHECK_NOT_RECONCILED:" + name)
            elif state != "KNOWN":
                warnings.append("CHECK_" + state + ":" + name)
        if any("UNKNOWN:" in w or "CONTRADICTORY:" in w for w in warnings) and admissible.get("scenario_reviewed") is not True:
            warnings.append("SCENARIO_REVIEW_REQUIRED")
        evidence = admissible.get("research_sources")
        evidence = evidence if isinstance(evidence, (list, tuple)) else []
        publishers: set[str] = set()
        for source in evidence:
            if not isinstance(source, Mapping):
                warnings.append("SOURCE_MALFORMED")
                continue
            publisher = _text(source.get("publisher")).casefold()
            available = _utc(source.get("available_to_user_at_utc"))
            if not publisher or available is None or when is None or available > when:
                warnings.append("SOURCE_UNVERIFIED_OR_FUTURE")
            elif _text(source.get("source_type")).upper() == "NON_MARKET":
                publishers.add(publisher)
        if len(publishers) < 2:
            warnings.append("TWO_INDEPENDENT_NONMARKET_SOURCES_NOT_MET")
        if publishers:
            # This helper checks caller-supplied structure and time, not the
            # publisher's site or an independent source-receipt ledger.
            warnings.append("NONMARKET_PUBLISHER_PROVENANCE_NOT_INDEPENDENTLY_VERIFIED")
        market = admissible.get("market_context")
        if market is not None:
            market_as_of = _utc(market.get("as_of_utc")) if isinstance(market, Mapping) else None
            favorite = _text(market.get("favorite")).casefold() if isinstance(market, Mapping) else ""
            if market_as_of is None or when is None or market_as_of > when:
                warnings.append("MARKET_CONTEXT_UNVERIFIED_OR_FUTURE")
            elif when - market_as_of > timedelta(minutes=60):
                warnings.append("MARKET_CONTEXT_STALE_FOR_RECONCILIATION")
            elif favorite and favorite not in {home.casefold(), away.casefold()}:
                warnings.append("MARKET_FAVORITE_OUTSIDE_MATCHUP")
            elif favorite and favorite != side.casefold() and admissible.get("market_disagreement_reviewed") is not True:
                warnings.append("MARKET_CONFLICT_NOT_RECONCILED")
        # Research game scripts are narrative scenarios, not fitted distributions.
        # Never accept post-selection scripts as original pregame evidence.
        game_script = admissible.get("game_script")
        script_primary = None
        script_adverse = None
        script_swings = None
        script_verified = False
        if not isinstance(game_script, Mapping):
            warnings.append("GAME_SCRIPT_NOT_REVIEWED")
        else:
            primary = _text(game_script.get("primary_win_path"))
            adverse = _text(game_script.get("opponent_win_path"))
            swings = game_script.get("swing_factors")
            script_time = _utc(game_script.get("as_of_utc"))
            if not primary or not adverse or primary.casefold() == adverse.casefold():
                warnings.append("GAME_SCRIPT_SCENARIOS_INCOMPLETE")
            expected_favored = _text(game_script.get("primary_favored_side")).casefold()
            opposing_favored = _text(game_script.get("opponent_favored_side")).casefold()
            other_sides = {home.casefold(), away.casefold()} - {side.casefold()}
            if (expected_favored != side.casefold() or len(other_sides) != 1
                or opposing_favored not in other_sides):
                warnings.append("GAME_SCRIPT_OPPOSING_WIN_PATH_UNVERIFIED")
            if game_script.get("contradictions_reviewed") is not True:
                warnings.append("GAME_SCRIPT_CONTRADICTIONS_REVIEW_REQUIRED")
            if not isinstance(swings, (list, tuple)) or not swings or any(not _text(v) for v in swings):
                warnings.append("GAME_SCRIPT_SWING_FACTORS_MISSING")
            if script_time is None or when is None or script_time > when:
                warnings.append("GAME_SCRIPT_TIME_UNVERIFIED_OR_FUTURE")
            if any(_unsupported_script_numeric_key(field) for field in game_script):
                warnings.append("UNVERIFIED_GAME_SCRIPT_PROBABILITY")
            script_verified = not any(w.startswith(("GAME_SCRIPT_", "UNVERIFIED_GAME_SCRIPT_")) for w in warnings)
            if script_verified:
                script_primary = primary
                script_adverse = adverse
                script_swings = list(swings)
        media_script_audit = audit_script_robustness_and_media(
            admissible,
            selected_side=side,
            home_team=home,
            away_team=away,
            selected_at_utc=_text(admissible.get("selected_at_utc")),
        )
        if issues:
            media_script_audit["media_evidence_status"] = "NOT_ADMISSIBLE_INVALID_SELECTION"
        warnings.extend(media_script_audit["research_warnings"])
        if _text(admissible.get("research_confidence")).upper() == "HIGH" and warnings:
            warnings.append("HIGH_CONFIDENCE_UNSUPPORTED")
        rows.append({
            "sport": sport,
            "official_event_id": event_id,
            "home_team": home,
            "away_team": away,
            "selected_side": side if not issues else None,
            "selection_class": "RESEARCH_PROVISIONAL" if not issues else "RESEARCH_INVALID",
            "model_status": "UNVERIFIED_CALLER_DECLARATION" if not issues else "UNKNOWN",
            "model_status_declared": _text(admissible.get("model_status")) or "UNKNOWN",
            "model_status_provenance": "CALLER_DECLARED_UNVERIFIED",
            "research_confidence": _text(admissible.get("research_confidence")) or "UNSPECIFIED",
            "selection_blockers": list(dict.fromkeys(issues)),
            "evidence_warnings": list(dict.fromkeys(warnings)),
            "source_publishers_verified": 0,
            "source_publishers_declared": len(publishers),
            "game_script_verified_pregame": script_verified,
            "game_script_primary_win_path": script_primary,
            "game_script_opponent_win_path": script_adverse,
            "game_script_swing_factors": script_swings,
            "script_robustness": media_script_audit["script_robustness"],
            "media_headlines": media_script_audit["media_headlines"],
            "media_storylines": media_script_audit["media_storylines"],
            "media_counter_narrative": media_script_audit["media_counter_narrative"],
            "media_evidence_status": media_script_audit["media_evidence_status"],
            "governed_probability": None,
            "governed_lower_bound": None,
        })
    selected = sum(r["selection_class"] == "RESEARCH_PROVISIONAL" for r in rows)
    complete = selected == len(rows) and not problems
    return {
        "official_games": len(rows),
        "winner_picks": selected,
        "all_sides_selected": selected == len(rows) and bool(rows) and not any("DUPLICATE" in p or "EXTRA" in p for p in problems),
        "board_complete": complete,
        "research_evidence_fully_reconciled": complete and all(not r["evidence_warnings"] for r in rows),
        "board_blockers": problems,
        "rows": rows,
        "fitted_model_samples_added": 0,
        "governed_model_modified": False,
        "terminal_authority_modified": False,
        "can_execute": CAN_EXECUTE,
    }
