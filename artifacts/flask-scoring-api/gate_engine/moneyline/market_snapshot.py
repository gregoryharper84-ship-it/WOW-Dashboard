"""
gate_engine/moneyline/market_snapshot.py
WOW-PATCH-2026-08-17-MONEYLINE-MARKET-SNAPSHOT

Shared ingestion contract between the odds endpoint and the moneyline scorer.

Root causes addressed (external verifier finding):
  1. Event ID stripped at board scan — the snapshot carries the Odds API
     ``event_id`` end-to-end so downstream dedup never has to fall back to
     fuzzy team-name/date composites.
  2. Three incompatible schemas — this module is the SINGLE adapter that
     converts the raw Odds API nested ``bookmakers[].markets[].outcomes[]``
     shape into the flat ``sportsbook_odds: [{odds, team, ...}]`` shape the
     scorer's ``extract_no_vig_probability`` / ``_build_market_comparison``
     consume.  No stage re-maps the data independently.
  3. No shared contract / silent zero-book handoff — every stage increments
     a named counter (books_fetched → books_sent_to_scorer) and a hard
     invariant blocks scoring with MARKET_PIPELINE_CONTRACT_BREACH when
     books were fetched but none reached the scorer.

can_execute = False (unconditional).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

can_execute: bool = False   # UNCONDITIONAL

# Terminal label for the pipeline-handoff invariant breach.
# Module-level string constant per protected-file policy — NOT a PropLabel
# member and never added to labels.py.
MARKET_PIPELINE_CONTRACT_BREACH: str = "MARKET_PIPELINE_CONTRACT_BREACH"

# Only these Odds API market keys may be ingested as moneyline markets.
_MONEYLINE_MARKET_KEYS: frozenset[str] = frozenset({"h2h"})

# Quotes older than this are rejected as stale.
DEFAULT_MAX_QUOTE_AGE_HOURS: float = 24.0

# Counter names, in pipeline order.
PIPELINE_COUNTERS: tuple[str, ...] = (
    "books_fetched",
    "books_normalized",
    "books_event_matched",
    "books_market_matched",
    "books_fresh",
    "books_sent_to_scorer",
)


def _norm_team(name: str | None) -> str:
    """Alnum-lowercase normalization (mirror of app.py `_llp_norm_team`)."""
    if not name:
        return ""
    return "".join(c for c in str(name).lower() if c.isalnum())


def _american_to_implied(american: float) -> float:
    if american > 0:
        return 100.0 / (american + 100.0)
    return abs(american) / (abs(american) + 100.0)


# ---------------------------------------------------------------------------
# Contract dataclasses
# ---------------------------------------------------------------------------

@dataclass
class BookQuote:
    """One bookmaker's price for one participant of a moneyline market."""
    bookmaker:     str
    team:          str            # canonical participant name (home_team/away_team)
    american_odds: float
    retrieved_at:  str | None = None   # ISO timestamp (bookmaker last_update)
    market_key:    str = "h2h"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MoneylineMarketSnapshot:
    """
    Normalized market snapshot handed from the odds endpoint to the scorer.

    Carries the Odds API event id, canonical home/away participants, and one
    BookQuote per (bookmaker, participant).  The `counters` dict records how
    many bookmakers survived each pipeline stage, and `dropped` records why
    each excluded book was dropped.
    """
    event_id:      str | None
    sport:         str
    home_team:     str
    away_team:     str
    commence_time: str | None = None
    market_key:    str = "h2h"
    books:         list[BookQuote] = field(default_factory=list)
    counters:      dict[str, int] = field(
        default_factory=lambda: {k: 0 for k in PIPELINE_COUNTERS})
    dropped:       list[dict[str, Any]] = field(default_factory=list)
    status:        str = "OK"
    # Authoritative alias table (canonical name → alternate display names)
    # captured at acquisition time so every downstream consumer resolves
    # participants against the SAME mapping — no consumer re-supplies it.
    aliases:       dict[str, list[str]] = field(default_factory=dict)
    can_execute:   bool = False   # unconditional

    # -- participants ---------------------------------------------------
    def resolve_participant(self, name: str, aliases: dict | None = None) -> str | None:
        """
        Resolve any display/alias team name to the snapshot's canonical
        participant (home_team or away_team).  `aliases` maps canonical name
        → list of alternate display names (same shape as app.py's
        _LLP_TEAM_ALIASES).  Returns None when the name matches neither
        participant.
        """
        n = _norm_team(name)
        if not n:
            return None
        if aliases is None:
            aliases = self.aliases
        forms = {n}
        for canonical, alist in (aliases or {}).items():
            all_norms = {_norm_team(canonical)} | {_norm_team(a) for a in alist}
            if n in all_norms:
                forms |= all_norms
                break
        # Tier 1: exact / containment match on normalized forms.
        tier1 = []
        for canon in (self.home_team, self.away_team):
            cn = _norm_team(canon)
            if any(f and (f == cn or f in cn or cn in f) for f in forms):
                if canon not in tier1:
                    tier1.append(canon)
        if len(tier1) == 1:
            return tier1[0]
        if len(tier1) == 2:
            return None  # ambiguous — fail honest

        # Tier 2: token overlap on DISTINGUISHING tokens only (tokens shared
        # by both participants, e.g. "Team" or a common city word, are
        # ignored).  Supports abbreviated display names like "MIN Lynx".
        def _tokens(s):
            return {t for t in str(s).lower().split() if t.isalnum()}

        name_tokens = _tokens(name)
        home_tokens = _tokens(self.home_team)
        away_tokens = _tokens(self.away_team)
        shared = home_tokens & away_tokens
        matches = []
        for canon, toks in ((self.home_team, home_tokens - shared),
                            (self.away_team, away_tokens - shared)):
            if name_tokens & toks and canon not in matches:
                matches.append(canon)
        if len(matches) == 1:
            return matches[0]
        # Ambiguous (matches both participants) or no match — fail honest.
        return None

    # -- serialization ----------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id":      self.event_id,
            "sport":         self.sport,
            "home_team":     self.home_team,
            "away_team":     self.away_team,
            "commence_time": self.commence_time,
            "market_key":    self.market_key,
            "books":         [b.to_dict() for b in self.books],
            "counters":      dict(self.counters),
            "dropped":       list(self.dropped),
            "status":        self.status,
            "aliases":       {k: list(v) for k, v in (self.aliases or {}).items()},
            "can_execute":   False,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MoneylineMarketSnapshot":
        snap = cls(
            event_id      = d.get("event_id"),
            sport         = str(d.get("sport") or ""),
            home_team     = str(d.get("home_team") or ""),
            away_team     = str(d.get("away_team") or ""),
            commence_time = d.get("commence_time"),
            market_key    = str(d.get("market_key") or "h2h"),
        )
        for b in (d.get("books") or []):
            if not isinstance(b, dict):
                continue
            try:
                snap.books.append(BookQuote(
                    bookmaker     = str(b.get("bookmaker") or "unknown"),
                    team          = str(b.get("team") or ""),
                    american_odds = float(b.get("american_odds")),
                    retrieved_at  = b.get("retrieved_at"),
                    market_key    = str(b.get("market_key") or "h2h"),
                ))
            except (TypeError, ValueError):
                continue
        counters = d.get("counters") or {}
        for k in PIPELINE_COUNTERS:
            if k in counters:
                try:
                    snap.counters[k] = int(counters[k])
                except (TypeError, ValueError):
                    pass
        snap.dropped = list(d.get("dropped") or [])
        snap.status  = str(d.get("status") or "OK")
        raw_aliases = d.get("aliases") or {}
        if isinstance(raw_aliases, dict):
            snap.aliases = {
                str(k): [str(a) for a in (v or [])]
                for k, v in raw_aliases.items() if isinstance(v, (list, tuple))
            }
        return snap


# ---------------------------------------------------------------------------
# Endpoint-side adapter: raw Odds API event → snapshot
# ---------------------------------------------------------------------------

def build_snapshot_from_odds_event(
    event: dict[str, Any],
    sport: str,
    *,
    market_key: str = "h2h",
    now: datetime | None = None,
    max_quote_age_hours: float = DEFAULT_MAX_QUOTE_AGE_HOURS,
    aliases: dict | None = None,
) -> MoneylineMarketSnapshot:
    """
    Normalize a raw Odds API event (nested bookmakers[].markets[].outcomes[])
    into a MoneylineMarketSnapshot with per-stage counters.

    Stage semantics (per bookmaker):
      books_fetched        — bookmaker entries present on the raw event
      books_event_matched  — fetched entries (event already matched by caller;
                             counted explicitly so a 0 here is visible)
      books_market_matched — bookmakers carrying the requested moneyline
                             market key (unsupported markets excluded here)
      books_normalized     — bookmakers whose outcomes parsed into valid
                             participant-matched American odds
      books_fresh          — normalized bookmakers whose last_update is within
                             max_quote_age_hours of `now`
    """
    snap = MoneylineMarketSnapshot(
        event_id      = str(event.get("id")) if event.get("id") else None,
        sport         = (sport or "").upper().strip(),
        home_team     = (event.get("home_team") or "").strip(),
        away_team     = (event.get("away_team") or "").strip(),
        commence_time = event.get("commence_time"),
        market_key    = market_key,
        aliases       = {str(k): [str(a) for a in (v or [])]
                         for k, v in (aliases or {}).items()},
    )
    now = now or datetime.now(timezone.utc)

    bookmakers = event.get("bookmakers") or []
    snap.counters["books_fetched"]       = len(bookmakers)
    snap.counters["books_event_matched"] = len(bookmakers)

    if market_key not in _MONEYLINE_MARKET_KEYS:
        snap.status = "UNSUPPORTED_MARKET_KEY"
        snap.dropped.append({
            "reason": "UNSUPPORTED_MARKET_KEY",
            "market_key": market_key,
        })
        _log_counters(snap)
        return snap

    for bm in bookmakers:
        if not isinstance(bm, dict):
            snap.dropped.append({"reason": "BOOKMAKER_NOT_DICT"})
            continue
        book_name = bm.get("key") or bm.get("title") or "unknown"
        ml_markets = [
            m for m in (bm.get("markets") or [])
            if isinstance(m, dict) and m.get("key") == market_key
        ]
        if not ml_markets:
            snap.dropped.append({
                "bookmaker": book_name, "reason": "MARKET_KEY_NOT_OFFERED",
            })
            continue
        snap.counters["books_market_matched"] += 1

        quotes: list[BookQuote] = []
        last_update = None
        for m in ml_markets:
            last_update = m.get("last_update") or bm.get("last_update")
            for o in (m.get("outcomes") or []):
                if not isinstance(o, dict):
                    continue
                team = snap.resolve_participant(o.get("name") or "", aliases)
                if team is None:
                    snap.dropped.append({
                        "bookmaker": book_name,
                        "reason": "OUTCOME_PARTICIPANT_UNRESOLVED",
                        "name": o.get("name"),
                    })
                    continue
                try:
                    price = float(o.get("price"))
                except (TypeError, ValueError):
                    snap.dropped.append({
                        "bookmaker": book_name,
                        "reason": "OUTCOME_PRICE_INVALID",
                        "price": o.get("price"),
                    })
                    continue
                quotes.append(BookQuote(
                    bookmaker     = str(book_name),
                    team          = team,
                    american_odds = price,
                    retrieved_at  = last_update,
                    market_key    = market_key,
                ))
        if not quotes:
            snap.dropped.append({
                "bookmaker": book_name, "reason": "NO_VALID_OUTCOMES",
            })
            continue
        snap.counters["books_normalized"] += 1

        # Freshness check — per bookmaker (all quotes share last_update)
        if _is_stale(last_update, now, max_quote_age_hours):
            snap.dropped.append({
                "bookmaker":    book_name,
                "reason":       "STALE_QUOTE",
                "retrieved_at": last_update,
                "max_age_hours": max_quote_age_hours,
            })
            continue
        snap.counters["books_fresh"] += 1
        snap.books.extend(quotes)

    _log_counters(snap)
    return snap


def _is_stale(retrieved_at: str | None, now: datetime, max_age_hours: float) -> bool:
    """A missing timestamp is NOT treated as stale (Odds API omits it rarely);
    an unparseable one IS (fail-honest on corrupt provenance)."""
    if not retrieved_at:
        return False
    try:
        ts = datetime.fromisoformat(str(retrieved_at).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return (now - ts).total_seconds() > max_age_hours * 3600.0


def _log_counters(snap: MoneylineMarketSnapshot) -> None:
    c = snap.counters
    print(
        "MONEYLINE_PIPELINE_COUNTS "
        f"event_id={snap.event_id} sport={snap.sport} "
        + " ".join(f"{k}={c.get(k, 0)}" for k in PIPELINE_COUNTERS)
        + f" status={snap.status}",
        flush=True,
    )


# ---------------------------------------------------------------------------
# Scorer-side adapter: snapshot → flat enrichment shape
# ---------------------------------------------------------------------------

def snapshot_to_scorer_enrichment(snap: MoneylineMarketSnapshot) -> dict[str, Any]:
    """
    Produce the flat ``sportsbook_odds`` list consumed by
    extract_no_vig_probability() and _build_market_comparison().

    This is the ONLY place the snapshot→scorer mapping lives.  Marks
    books_sent_to_scorer and enforces the hard handoff invariant.
    """
    books = [
        {
            "name":         q.bookmaker,
            "bookmaker":    q.bookmaker,
            "team":         q.team,
            "odds":         q.american_odds,
            "retrieved_at": q.retrieved_at,
        }
        for q in snap.books
    ]
    sent_bookmakers = {q.bookmaker for q in snap.books}
    snap.counters["books_sent_to_scorer"] = len(sent_bookmakers)
    check_pipeline_invariant(snap)   # may set status + raise via caller policy
    _log_counters(snap)
    return {"sportsbook_odds": books}


def check_pipeline_invariant(snap: MoneylineMarketSnapshot) -> bool:
    """
    Hard invariant: books were fetched but none reached the scorer.

    Returns True when the invariant is BREACHED.  Sets snap.status to
    MARKET_PIPELINE_CONTRACT_BREACH and emits an alert log line; callers must
    block scoring when this returns True.
    """
    fetched = snap.counters.get("books_fetched", 0)
    sent    = snap.counters.get("books_sent_to_scorer", 0)
    if fetched > 0 and sent == 0:
        snap.status = MARKET_PIPELINE_CONTRACT_BREACH
        print(
            f"ALERT {MARKET_PIPELINE_CONTRACT_BREACH} "
            f"event_id={snap.event_id} sport={snap.sport} "
            f"books_fetched={fetched} books_sent_to_scorer=0 "
            f"dropped={len(snap.dropped)} scoring_blocked=True",
            flush=True,
        )
        return True
    return False


# ---------------------------------------------------------------------------
# Consensus no-vig (endpoint-grade, two-sided)
# ---------------------------------------------------------------------------

def snapshot_two_sided_gap(snap: MoneylineMarketSnapshot) -> list[str]:
    """Shared two-sided-market validity check used by EVERY snapshot consumer.

    Returns the list of snapshot participants that have NO quote (empty list
    means the snapshot carries a valid two-sided market).  A snapshot with no
    quotes at all returns both participants."""
    quoted = {q.team for q in snap.books}
    return [p for p in (snap.home_team, snap.away_team) if p not in quoted]


def consensus_no_vig(
    snap: MoneylineMarketSnapshot,
    side: str,
    *,
    aliases: dict | None = None,
) -> float | None:
    """
    Two-sided consensus no-vig probability for `side` across all fresh books.

    Averages implied probabilities per participant across books, then removes
    the overround: p_side / (p_side + p_opponent).  Returns None when the side
    cannot be resolved or no quotes exist for it.
    """
    canon = snap.resolve_participant(side, aliases)
    if canon is None:
        return None
    opponent = snap.away_team if canon == snap.home_team else snap.home_team
    side_probs = [_american_to_implied(q.american_odds)
                  for q in snap.books if q.team == canon]
    opp_probs  = [_american_to_implied(q.american_odds)
                  for q in snap.books if q.team == opponent]
    if not side_probs:
        return None
    if not opp_probs:
        # One-sided snapshot: a vigged single-side implied probability is NOT
        # a no-vig consensus — return unavailable, never a biased fallback.
        return None
    avg_side = sum(side_probs) / len(side_probs)
    avg_opp = sum(opp_probs) / len(opp_probs)
    denom = avg_side + avg_opp
    if denom <= 0:
        return None
    return max(0.01, min(0.99, avg_side / denom))


# ---------------------------------------------------------------------------
# Snapshot → LLP analyzer adapters (single source: no re-fetch, no re-map)
# ---------------------------------------------------------------------------

def snapshot_to_odds_event(snap: MoneylineMarketSnapshot) -> dict[str, Any]:
    """Canonical inverse mapping: rebuild the Odds API event shape from the
    snapshot's normalized quotes.  Lives HERE (the shared adapter) so no
    consumer re-implements the mapping.  Used by consumers whose helpers
    (consensus panels, book comparison) read the nested event shape."""
    by_book: dict[str, list[BookQuote]] = {}
    for q in snap.books:
        by_book.setdefault(q.bookmaker, []).append(q)
    return {
        "id":            snap.event_id,
        "home_team":     snap.home_team,
        "away_team":     snap.away_team,
        "commence_time": snap.commence_time,
        "bookmakers": [
            {
                "key":   book,
                "title": book,
                "markets": [{
                    "key":         snap.market_key,
                    "last_update": quotes[0].retrieved_at,
                    "outcomes": [
                        {"name": q.team, "price": q.american_odds}
                        for q in quotes
                    ],
                }],
            }
            for book, quotes in by_book.items()
        ],
    }


def select_market_from_snapshot(
    snap: MoneylineMarketSnapshot,
    side: str,
    *,
    aliases: dict | None = None,
) -> dict[str, Any] | None:
    """Select the best-priced quote for `side` plus the two-sided consensus
    no-vig, in the flat `sel` shape LLP analysis consumes
    ({book, american, point, implied_prob, novig_prob})."""
    canon = snap.resolve_participant(side, aliases)
    if canon is None:
        return None
    quotes = [q for q in snap.books if q.team == canon]
    if not quotes:
        return None
    best = max(quotes, key=lambda q: q.american_odds)
    nv = consensus_no_vig(snap, side, aliases=aliases)
    return {
        "book":         best.bookmaker,
        "american":     best.american_odds,
        "point":        None,
        "implied_prob": _american_to_implied(best.american_odds),
        "novig_prob":   nv,
    }


# ---------------------------------------------------------------------------
# Pipeline attachment helper (used by run_moneyline_pipeline)
# ---------------------------------------------------------------------------

def attach_snapshot_to_enrichment(
    enrichment: dict[str, Any],
    snapshot: "MoneylineMarketSnapshot | dict[str, Any]",
    row: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], MoneylineMarketSnapshot, bool]:
    """
    Merge a market snapshot into scorer enrichment.

    Returns (new_enrichment, snapshot_obj, breached).  When breached is True
    the caller MUST block scoring with MARKET_PIPELINE_CONTRACT_BREACH.
    The snapshot-derived sportsbook_odds REPLACE any caller-supplied list so
    exactly one mapping path exists.

    When `row` is provided, quote participants are ALIGNED to the row's own
    team/opponent strings (the scorer matches by exact string).  If quotes
    exist but the row's team cannot be resolved against the snapshot
    participants — or team/opponent collapse onto the same participant — the
    handoff fails CLOSED with MARKET_PIPELINE_CONTRACT_BREACH instead of
    letting the scorer silently see zero matching books.
    """
    if isinstance(snapshot, MoneylineMarketSnapshot):
        snap = snapshot
    elif isinstance(snapshot, dict):
        snap = MoneylineMarketSnapshot.from_dict(snapshot)
    else:
        # Malformed supplied snapshot — fail closed, never ignore.
        snap = MoneylineMarketSnapshot.from_dict({})
    out = dict(enrichment)
    scorer_enr = snapshot_to_scorer_enrichment(snap)

    def _breach(reason: str, **detail: Any) -> tuple[dict, MoneylineMarketSnapshot, bool]:
        snap.status = MARKET_PIPELINE_CONTRACT_BREACH
        snap.dropped.append({"reason": reason, **detail})
        snap.counters["books_sent_to_scorer"] = 0
        print(
            f"ALERT {MARKET_PIPELINE_CONTRACT_BREACH} "
            f"event_id={snap.event_id} reason={reason} scoring_blocked=True",
            flush=True,
        )
        out["sportsbook_odds"] = []
        return out, snap, True

    # A supplied snapshot must carry a valid two-sided market: quotes for
    # BOTH participants.  A one-sided/partial snapshot must never reach the
    # scorer (extract_no_vig_probability would silently fall back to the
    # single side's vigged implied probability).
    if not snap.books:
        # An explicitly supplied snapshot with no usable quotes (including a
        # bare {}) can never feed the scorer — breach, don't silently score
        # or re-fetch.
        return _breach(
            "EMPTY_SNAPSHOT_SUPPLIED",
            books_fetched=snap.counters.get("books_fetched", 0),
        )
    if snap.books:
        missing = snapshot_two_sided_gap(snap)
        if missing:
            return _breach(
                "ONE_SIDED_MARKET",
                missing_participants=missing,
                home_team=snap.home_team,
                away_team=snap.away_team,
            )

    if row is not None and snap.books:
        row_team = str(row.get("team") or row.get("player") or "").strip()
        row_opp  = str(row.get("opponent") or row.get("opponent_team") or "").strip()
        canon_team = snap.resolve_participant(row_team) if row_team else None
        canon_opp  = snap.resolve_participant(row_opp)  if row_opp  else None
        if canon_team is None or (canon_opp is not None and canon_opp == canon_team):
            snap.status = MARKET_PIPELINE_CONTRACT_BREACH
            snap.dropped.append({
                "reason":     "PARTICIPANT_ALIGNMENT_FAILED",
                "row_team":   row_team,
                "row_opponent": row_opp,
                "home_team":  snap.home_team,
                "away_team":  snap.away_team,
            })
            snap.counters["books_sent_to_scorer"] = 0
            print(
                f"ALERT {MARKET_PIPELINE_CONTRACT_BREACH} "
                f"event_id={snap.event_id} reason=PARTICIPANT_ALIGNMENT_FAILED "
                f"row_team={row_team!r} row_opponent={row_opp!r} "
                f"scoring_blocked=True",
                flush=True,
            )
            out["sportsbook_odds"] = []
            return out, snap, True
        # Rewrite quote team names to the row's own strings so the scorer's
        # exact-string matching (extract_no_vig_probability /
        # _build_market_comparison) sees every book.
        rename = {canon_team: row_team}
        if canon_opp is not None:
            rename[canon_opp] = row_opp
        for b in scorer_enr["sportsbook_odds"]:
            b["team"] = rename.get(b["team"], b["team"])

    out.update(scorer_enr)
    breached = snap.status == MARKET_PIPELINE_CONTRACT_BREACH
    return out, snap, breached
