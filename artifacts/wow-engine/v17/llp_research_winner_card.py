"""Read-only pregame coverage audit for human/LLP research winner cards.

A user may request one winner for every NFL/NBA/NHL game even when the fitted
specialist is unavailable. This audits those *research* selections, never
creates a winner, changes a governed model/terminal, or confers certification.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

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
    if not official_slate_verified or not _text(official_slate_receipt_id):
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
        if pick_counts[(sport, event_id)] != 1:
            issues.append("MISSING_OR_DUPLICATE_WINNER")
        if side.casefold() not in {home.casefold(), away.casefold()} or side.upper() in NO_WINNER_TOKENS:
            issues.append("INVALID_OR_PASS_WINNER")
        if when is None or kickoff is None or when >= kickoff:
            issues.append("RESEARCH_PICK_NOT_PREGAME")
        if not _text(chosen.get("reasoning")):
            warnings.append("REASONING_MISSING")
        checks = chosen.get("pregame_checks")
        checks = checks if isinstance(checks, Mapping) else {}
        for name in CHECKS.get(sport, ()):
            state = _text(checks.get(name)).upper()
            if state not in {"KNOWN", "UNKNOWN", "CONTRADICTORY"}:
                warnings.append("CHECK_NOT_RECONCILED:" + name)
            elif state != "KNOWN":
                warnings.append("CHECK_" + state + ":" + name)
        if any("UNKNOWN:" in w or "CONTRADICTORY:" in w for w in warnings) and chosen.get("scenario_reviewed") is not True:
            warnings.append("SCENARIO_REVIEW_REQUIRED")
        evidence = chosen.get("research_sources")
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
        market = chosen.get("market_context")
        if market is not None:
            if not isinstance(market, Mapping) or _utc(market.get("as_of_utc")) is None or when is None or _utc(market.get("as_of_utc")) > when:
                warnings.append("MARKET_CONTEXT_UNVERIFIED_OR_FUTURE")
            elif _text(market.get("favorite")).casefold() not in {"", side.casefold()} and chosen.get("market_disagreement_reviewed") is not True:
                warnings.append("MARKET_CONFLICT_NOT_RECONCILED")
        if _text(chosen.get("research_confidence")).upper() == "HIGH" and warnings:
            warnings.append("HIGH_CONFIDENCE_UNSUPPORTED")
        rows.append({
            "sport": sport,
            "official_event_id": event_id,
            "home_team": home,
            "away_team": away,
            "selected_side": side if not issues else None,
            "selection_class": "RESEARCH_PROVISIONAL" if not issues else "RESEARCH_INVALID",
            "model_status": _text(chosen.get("model_status")) or "UNKNOWN",
            "research_confidence": _text(chosen.get("research_confidence")) or "UNSPECIFIED",
            "selection_blockers": list(dict.fromkeys(issues)),
            "evidence_warnings": list(dict.fromkeys(warnings)),
            "source_publishers_verified": len(publishers),
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
