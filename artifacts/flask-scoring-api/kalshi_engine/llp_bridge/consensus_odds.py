"""
consensus_odds.py — sportsbook consensus no-vig fair-probability gate
for the Kalshi Sports ML Edge Rule (WNBA/MLB only), per user-approved
2026-07-05 amendment ("Kalshi Sports ML Edge Rule — WNBA/MLB Only").

HARD GATE: ml-evaluate must never compute a Kalshi money edge from
model_probability alone. A no-vig sportsbook consensus fair probability
for the exact team on the Kalshi YES side must be AVAILABLE, fresh
(< STALE_SECONDS), and non-contradictory across books, or the row is
capped downstream (see ml_evaluate.py's ODDS_CONSENSUS_* gate).

Source priority:
  1. The Odds API (services/odds_api.py) — primary. A true "consensus"
     needs >=2 independently-reporting bookmakers.
  2. TheRundown (services/rundown.py) — fallback/corroboration only,
     tried only when The Odds API has no matching event or zero usable
     (fresh) books.

A result built from exactly one bookmaker (from either source) is
tagged single_book_fallback=True. Per the approved rule, a single raw
sportsbook price is NEVER treated as fair probability outright — the
caller (ml_evaluate.py) must cap the row (LLP_WATCH) when this flag is
set, never approve off it.

This module is read-only. It never calls an authenticated sportsbook
write/order endpoint, and it never fabricates a price when a real
lookup fails — FAILED/NOT_CALLED/STALE/CONTRADICTORY are always
returned honestly with no synthetic fallback value.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from services import odds_api as _odds_api
from services import rundown as _rundown

# Book-level no-vig fair probabilities that disagree by more than this are
# flagged CONTRADICTORY rather than blindly averaged into a consensus.
CONTRADICTION_SPREAD = 0.08  # 8 percentage points

# An odds snapshot older than this is considered stale and excluded.
STALE_SECONDS = 30 * 60  # 30 minutes


def _american_to_implied(price: Any) -> Optional[float]:
    if price is None:
        return None
    try:
        p = float(price)
    except (TypeError, ValueError):
        return None
    if p > 0:
        return 100.0 / (p + 100.0)
    if p < 0:
        return -p / (-p + 100.0)
    return None


def _normalize_name(name: Optional[str]) -> str:
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


def _team_matches(a: Optional[str], b: Optional[str]) -> bool:
    na, nb = _normalize_name(a), _normalize_name(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def _parse_timestamp(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _age_seconds(ts: Optional[str], now: Optional[datetime] = None) -> Optional[float]:
    dt = _parse_timestamp(ts)
    if dt is None:
        return None
    now = now or datetime.now(timezone.utc)
    return (now - dt).total_seconds()


def _books_from_odds_api_event(
    event: dict[str, Any], team_a: str, team_b: str, target_team: str,
) -> list[dict[str, Any]]:
    """
    Extract per-bookmaker no-vig fair probability for `target_team` from
    one Odds-API event payload. Only exact 2-outcome h2h markets are used
    — WNBA/MLB moneyline only, no draws.
    """
    books: list[dict[str, Any]] = []
    for bm in (event.get("bookmakers") or []):
        for market in (bm.get("markets") or []):
            if market.get("key") != "h2h":
                continue
            outcomes = market.get("outcomes") or []
            if len(outcomes) != 2:
                continue
            target_price = other_price = None
            for o in outcomes:
                name = o.get("name", "")
                if _team_matches(name, target_team):
                    target_price = o.get("price")
                elif _team_matches(name, team_a) or _team_matches(name, team_b):
                    other_price = o.get("price")
            if target_price is None or other_price is None:
                continue
            ti = _american_to_implied(target_price)
            oi = _american_to_implied(other_price)
            if ti is None or oi is None or (ti + oi) <= 0:
                continue
            books.append({
                "bookmaker":              bm.get("key", "") or "unknown_book",
                "target_fair_probability": round(ti / (ti + oi), 6),
                "last_update":            market.get("last_update") or bm.get("last_update"),
                "source":                 "the_odds_api",
            })
    return books


def _find_odds_api_event(sport_key: str, team_a: str, team_b: str):
    events, status = _odds_api.get_h2h_odds(sport_key)
    if not events:
        return None, status
    for ev in events:
        h, a = ev.get("home_team", ""), ev.get("away_team", "")
        if (_team_matches(h, team_a) and _team_matches(a, team_b)) or \
           (_team_matches(h, team_b) and _team_matches(a, team_a)):
            return ev, status
    return None, status


def _books_from_rundown(
    sport: str, team_a: str, team_b: str, target_team: str,
) -> tuple[list[dict[str, Any]], str]:
    """
    Fallback/corroboration only. TheRundown's per-affiliate moneyline shape
    is `event["lines"][affiliate_id]["moneyline"]` with `moneyline_home` /
    `moneyline_away` / `date_updated` keys (per TheRundown public API
    reference). This is best-effort parsing: any shape mismatch degrades
    to zero books (never a fabricated price), so the caller correctly
    falls through to NOT_CALLED/FAILED rather than raising.
    """
    events, status = _rundown.get_moneyline_events_for_sport(sport)
    books: list[dict[str, Any]] = []
    for ev in (events or []):
        teams = ev.get("teams_normalized") or []
        names = [t.get("name", "") for t in teams]
        if len(names) < 2:
            continue
        home_name, away_name = names[0], names[-1]
        if not ((_team_matches(home_name, team_a) and _team_matches(away_name, team_b)) or
                (_team_matches(home_name, team_b) and _team_matches(away_name, team_a))):
            continue
        for affiliate_id, line in (ev.get("lines") or {}).items():
            ml = (line or {}).get("moneyline") or {}
            home_price = ml.get("moneyline_home")
            away_price = ml.get("moneyline_away")
            if home_price is None or away_price is None:
                continue
            if _team_matches(home_name, target_team):
                target_price, other_price = home_price, away_price
            elif _team_matches(away_name, target_team):
                target_price, other_price = away_price, home_price
            else:
                continue
            ti = _american_to_implied(target_price)
            oi = _american_to_implied(other_price)
            if ti is None or oi is None or (ti + oi) <= 0:
                continue
            books.append({
                "bookmaker":              f"rundown:{affiliate_id}",
                "target_fair_probability": round(ti / (ti + oi), 6),
                "last_update":            ml.get("date_updated"),
                "source":                 "rundown",
            })
        if books:
            break  # matched event found, stop scanning further events
    return books, status


def get_consensus_no_vig_probability(
    sport: str,
    team_a: str,
    team_b: str,
    target_team: str,
) -> dict[str, Any]:
    """
    Fetch a no-vig sportsbook consensus fair probability for `target_team`
    winning the `team_a` vs `team_b` game. `target_team` must be the exact
    Kalshi YES-side team (i.e. mapping["yes_sub_title"]) — this function
    has no concept of home/away, only "the team the YES contract pays on".

    Returns:
      {
        status: "AVAILABLE" | "NOT_CALLED" | "FAILED" | "STALE" | "CONTRADICTORY",
        consensus_fair_probability: float | None,
        books_used:            [str, ...],
        book_count:            int,
        single_book_fallback:  bool,
        max_book_spread:       float | None,
        oldest_book_age_seconds: float | None,
        source:                str | None,
        blocker_tags:          [str, ...],
        detail:                str,
      }
    """
    sport_key = _odds_api.SPORT_KEYS.get((sport or "").upper())
    fresh_books: list[dict[str, Any]] = []
    stale_books: list[dict[str, Any]] = []
    detail_parts: list[str] = []
    any_hard_failure = False

    if sport_key:
        event, api_status = _find_odds_api_event(sport_key, team_a, team_b)
        detail_parts.append(f"the_odds_api:{api_status}")
        if isinstance(api_status, str) and api_status.startswith("FAILED"):
            any_hard_failure = True
        if event:
            for b in _books_from_odds_api_event(event, team_a, team_b, target_team):
                age = _age_seconds(b.get("last_update"))
                (stale_books if (age is not None and age > STALE_SECONDS) else fresh_books).append(b)
    else:
        detail_parts.append(f"the_odds_api:NOT_CALLED unknown sport {sport!r}")

    # Fallback/corroboration only when Odds API produced no usable fresh books.
    if not fresh_books:
        rd_books, rd_status = _books_from_rundown(sport, team_a, team_b, target_team)
        detail_parts.append(f"rundown:{rd_status}")
        if isinstance(rd_status, str) and rd_status.startswith("FAILED"):
            any_hard_failure = True
        for b in rd_books:
            age = _age_seconds(b.get("last_update"))
            (stale_books if (age is not None and age > STALE_SECONDS) else fresh_books).append(b)

    detail = "; ".join(detail_parts)

    if not fresh_books and not stale_books:
        status = "FAILED" if any_hard_failure else "NOT_CALLED"
        return {
            "status":                   status,
            "consensus_fair_probability": None,
            "books_used":               [],
            "book_count":               0,
            "single_book_fallback":     False,
            "max_book_spread":          None,
            "oldest_book_age_seconds":  None,
            "source":                   None,
            "blocker_tags":             ["ODDS_CONSENSUS_UNAVAILABLE"],
            "detail":                   detail or "no matching event found on any odds source",
        }

    if not fresh_books and stale_books:
        oldest = max((_age_seconds(b.get("last_update")) or 0.0) for b in stale_books)
        return {
            "status":                   "STALE",
            "consensus_fair_probability": None,
            "books_used":               [b["bookmaker"] for b in stale_books],
            "book_count":               len(stale_books),
            "single_book_fallback":     len(stale_books) == 1,
            "max_book_spread":          None,
            "oldest_book_age_seconds":  oldest,
            "source":                   stale_books[0]["source"],
            "blocker_tags":             ["ODDS_CONSENSUS_STALE"],
            "detail":                   f"{detail}; newest usable quote is {oldest:.0f}s old (limit {STALE_SECONDS}s)",
        }

    probs = [b["target_fair_probability"] for b in fresh_books]
    spread = round(max(probs) - min(probs), 6) if len(probs) > 1 else 0.0
    sources = sorted(set(b["source"] for b in fresh_books))
    oldest = max((_age_seconds(b.get("last_update")) or 0.0) for b in fresh_books)

    if len(fresh_books) > 1 and spread > CONTRADICTION_SPREAD:
        return {
            "status":                   "CONTRADICTORY",
            "consensus_fair_probability": round(sum(probs) / len(probs), 6),
            "books_used":               [b["bookmaker"] for b in fresh_books],
            "book_count":               len(fresh_books),
            "single_book_fallback":     False,
            "max_book_spread":          spread,
            "oldest_book_age_seconds":  oldest,
            "source":                   "+".join(sources),
            "blocker_tags":             ["ODDS_CONSENSUS_CONTRADICTORY"],
            "detail":                   f"{detail}; book spread {spread} exceeds {CONTRADICTION_SPREAD}",
        }

    single_book = len(fresh_books) == 1
    return {
        "status":                   "AVAILABLE",
        "consensus_fair_probability": round(sum(probs) / len(probs), 6),
        "books_used":               [b["bookmaker"] for b in fresh_books],
        "book_count":               len(fresh_books),
        "single_book_fallback":     single_book,
        "max_book_spread":          spread,
        "oldest_book_age_seconds":  oldest,
        "source":                   "+".join(sources),
        "blocker_tags":             ["ODDS_CONSENSUS_SINGLE_BOOK"] if single_book else [],
        "detail":                   detail,
    }
