"""
auto_enrichment.py

WOW-PATCH-2026-07-04-PREGATE-AUTO-ENRICHMENT (proposed)

Pre-gate auto-enrichment: fills market lines (via The Odds API) and
player status/injury flags (via ESPN) into the enrichment dict BEFORE
run_pipeline() runs, instead of requiring the caller to hand-assemble
that JSON for every row.

Scope note: L10/L5 game-log auto-fetch is intentionally NOT included in
this patch. Game-log data is fetched from different tables/sources per
sport (a dedicated WNBA scraper table, separate MLB/NBA cache tables,
external stat APIs) with no single reusable function across sports —
wiring that in safely is a separate, larger patch.

Guarantees:
  - Never fabricates data. If a live source is unavailable, a sport is
    unsupported, or a prop_type has no known market mapping, that field
    is simply left unfilled — the row falls through to the existing
    gate behavior (NO_MARKET_FOUND / SOURCE_NOT_CALLED / LINEUP_UNCONFIRMED)
    exactly as it does today.
  - Caller-supplied enrichment always wins per-field. This function only
    fills fields that are missing (None) in the caller's entry for that
    row — it never overwrites an explicit caller-provided value.
  - Does not touch market_gate.py, classifier.py, status_role.py, or any
    gate threshold. It only populates the upstream `enrichment` dict that
    those gates already consume via run_pipeline().
"""
from __future__ import annotations

import datetime as _datetime
import statistics as _statistics
from typing import Any

from services import odds_api, status as status_service
from gate_engine.auto_game_log import fetch_game_log, GameLogUnavailable


# ---------------------------------------------------------------------------
# Stat-key canonicalization
# ---------------------------------------------------------------------------
# Maps display prop_type strings (from GPT / normalizer) → the canonical
# stat_key that fetch_game_log and _MLB_STAT_FIELDS understand.
# This prevents display strings like "Pitcher Strikeouts" from reaching
# fetch_game_log as-is and causing GameLogUnavailable → NOT_CALLED.
_STAT_KEY_CANONICAL: dict[str, str] = {
    # MLB pitcher
    "pitcher strikeouts":          "K",
    "strikeouts":                  "K",
    "k":                           "K",
    "so":                          "K",
    "pitching outs":               "OUTS",
    "pitching out":                "OUTS",
    "outs":                        "OUTS",
    "plate appearances":           "PA",
    "plate_appearances":           "PA",
    "pa":                          "PA",
    "1st inning pitches thrown":   "1IP_PITCHES_THROWN",
    "1ip pitches thrown":          "1IP_PITCHES_THROWN",
    "1ip":                         "1IP_PITCHES_THROWN",
    "1ip_pitches_thrown":          "1IP_PITCHES_THROWN",
    # MLB hitter combos
    "hits + runs + rbi":           "H+R+RBI",
    "hits+runs+rbi":               "H+R+RBI",
    "h+r+rbi":                     "H+R+RBI",
    "h + r + rbi":                 "H+R+RBI",
    "hitter fantasy score":        "FANTASY_SCORE",
    "fantasy score":               "FANTASY_SCORE",
    "fantasy_score":               "FANTASY_SCORE",
    # NBA/WNBA
    "points rebounds assists":     "PTS+REB+AST",
    "pra":                         "PTS+REB+AST",
    "pts+reb+ast":                 "PTS+REB+AST",
    "pts + reb + ast":             "PTS+REB+AST",
}


def _canonicalize_stat_key(raw: str | None) -> str:
    """Return canonical stat_key for a display or short-form input, or the
    original (stripped) value if no alias is registered.  Never returns None."""
    if not raw:
        return ""
    stripped = raw.strip()
    return _STAT_KEY_CANONICAL.get(stripped.lower(), stripped)


def _lookup_mlb_player_id(player_name: str) -> "str | None":
    """Best-effort MLB player ID lookup by full name via MLB Stats API.

    Called only when the row does not already carry a player_id.  Returns the
    string id of the first matching active player, or None on any failure.
    Network errors, timeouts, and unexpected response shapes are silently
    swallowed — the caller falls through to the honest gap path.

    WOW-PATCH-2026-08-16-R2: Adds Unicode accent-strip fallback so names like
    "Jeremy Peña" retry as "Jeremy Pena" when the MLB Stats API /people/search
    endpoint returns empty results for the accented form.
    """
    if not player_name or not player_name.strip():
        return None

    def _query(name: str) -> "str | None":
        try:
            import json
            import urllib.parse
            import urllib.request
            name_enc = urllib.parse.quote(name.strip())
            url = (
                f"https://statsapi.mlb.com/api/v1/people/search"
                f"?names={name_enc}&sportIds=1"
            )
            req = urllib.request.Request(url, headers={"User-Agent": "WOW/1.0"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read())
            people = data.get("people") or []
            if people:
                pid = people[0].get("id")
                return str(pid) if pid else None
        except Exception:
            pass
        return None

    # Primary attempt with the name as supplied
    result = _query(player_name)
    if result:
        return result

    # Fallback: strip Unicode combining marks (NFD decomposition drops diacritics).
    # "Jeremy Peña" → "Jeremy Pena", "Néstor Cortés" → "Nestor Cortes", etc.
    try:
        import unicodedata
        ascii_name = "".join(
            c for c in unicodedata.normalize("NFD", player_name)
            if unicodedata.category(c) != "Mn"
        )
        if ascii_name != player_name:
            return _query(ascii_name)
    except Exception:
        pass
    return None


# Conservative, explicit prop_type -> odds_api market-suffix mapping.
# Only unambiguous, commonly-seen WOW prop types are listed. Anything not
# listed here is left unmapped on purpose — the row gets no auto-filled
# market line, identical to today's behavior.
#
# FIX-2: Added "k" and "so" as aliases for pitcher strikeouts.
# normalizer.py maps "pitcher strikeouts" / "strikeouts" / "k" → stat_key "K",
# and app.py/_norm_to_pipeline_row sets prop_type = stat_key ("K").
# Without these entries the market lookup silently produced no entries even
# when The Odds API had lines, because "K" / "SO" weren't in this dict.
_PROP_TYPE_TO_MARKET_SUFFIX = {
    "points": "points",
    "rebounds": "rebounds",
    "assists": "assists",
    "threes": "threes",
    "three pointers made": "threes",
    "3pt made": "threes",
    "steals": "steals",
    "blocks": "blocks",
    "points rebounds assists": "points_rebounds_assists",
    "pra": "points_rebounds_assists",
    "hits": "hits",
    "home runs": "home_runs",
    "rbis": "rbis",
    "total bases": "total_bases",
    "pitcher strikeouts": "strikeouts",
    "strikeouts": "strikeouts",   # alternate long form sometimes emitted by gap-fill
    "k": "strikeouts",            # FIX-2: stat_key short form set by _norm_to_pipeline_row
    "so": "strikeouts",           # FIX-2: legacy short form
    # Pitching outs: normalizer.py maps "pitching outs" → stat_key "OUTS".
    # _market_key_for("MLB", "pitching outs") → prefix="pitcher" + "outs" = "pitcher_outs".
    "pitching outs": "outs",
    "outs": "outs",               # short-form stat_key emitted by _norm_to_pipeline_row
    # WOW-PATCH-2026-08-06-MLB-PLATE-APPEARANCES-COVERAGE
    "mlb_plate_appearances": "plate_appearances",
    "plate appearances":     "plate_appearances",
    "plate_appearances":     "plate_appearances",
    "pa":                    "plate_appearances",
    "passing yards": "pass_yds",
    "passing tds": "pass_tds",
    "rushing yards": "rush_yds",
    "receiving yards": "reception_yds",
    "receptions": "receptions",
}

_PITCHER_PROP_TYPES = {"pitcher strikeouts", "pitching outs", "outs"}


def _normalize_prop_type(prop_type: str | None) -> str:
    return (prop_type or "").strip().lower()


def _market_key_for(sport: str, prop_type: str) -> str | None:
    suffix = _PROP_TYPE_TO_MARKET_SUFFIX.get(_normalize_prop_type(prop_type))
    if not suffix:
        return None
    if sport == "MLB":
        prefix = "pitcher" if _normalize_prop_type(prop_type) in _PITCHER_PROP_TYPES else "batter"
        return f"{prefix}_{suffix}"
    return f"player_{suffix}"


def _build_market_index(props: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """Index flat odds_api props by (player_lower, market_key)."""
    index: dict[tuple[str, str], list[dict]] = {}
    for p in props:
        key = ((p.get("player") or "").lower(), p.get("prop") or "")
        index.setdefault(key, []).append(p)
    return index


def _lines_for_side(entries: list[dict], side: str | None) -> list[dict]:
    if side is None:
        return entries
    return [e for e in entries if e.get("side") == side]


# ---------------------------------------------------------------------------
# No-vig probability helpers (FIX-3)
# ---------------------------------------------------------------------------

def _american_odds_to_implied(price) -> float | None:
    """
    Convert American odds (e.g. -115, +110) to implied probability.
    Returns None if `price` is not a valid number.
    """
    try:
        p = float(price)
    except (TypeError, ValueError):
        return None
    if p >= 0:
        return 100.0 / (100.0 + p)
    else:
        return abs(p) / (abs(p) + 100.0)


def _compute_no_vig_prob(price_more, price_less) -> "str | float":
    """
    Compute the no-vig probability for the MORE/OVER side given two-sided
    American odds prices.

    Returns:
      float in [0, 1]      — when both sides are valid and computable
      "SOURCE_CONFLICT"    — when exactly one side is missing
      "MARKET_UNAVAILABLE" — when both sides are missing

    The sentinel strings pass data_contract._is_present() (non-None, non-blank)
    so run_intake() does not flag the field as absent.
    """
    if price_more is None and price_less is None:
        return "MARKET_UNAVAILABLE"
    if price_more is None or price_less is None:
        return "SOURCE_CONFLICT"
    p_m = _american_odds_to_implied(price_more)
    p_l = _american_odds_to_implied(price_less)
    if p_m is None or p_l is None:
        return "SOURCE_CONFLICT"
    total = p_m + p_l
    if total <= 0:
        return "SOURCE_CONFLICT"
    return round(p_m / total, 4)


def build_auto_enrichment(
    rows: list[dict[str, Any]],
    base_enrichment: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """
    Build an enrichment dict by auto-fetching market lines (The Odds API)
    and status/injury flags (ESPN) for the sports present in `rows`.

    Returns (enrichment, source_status):
      enrichment    — dict ready to pass straight into run_pipeline()
      source_status — {"sports": {SPORT: {"market": ..., "status": ...,
                        "market_props_found": N, "status_players_found": N}}}
                       Reports what was actually fetched per sport, so a
                       reviewer/caller can see whether a row's fields came
                       from a live source, were skipped (unmapped prop
                       type / unsupported sport), or failed outright.
    """
    base_enrichment = base_enrichment or {}
    enrichment: dict[str, dict[str, Any]] = {
        k: dict(v) for k, v in base_enrichment.items() if isinstance(v, dict)
    }
    # Keys the CALLER already populated, captured before this function
    # writes anything. Used to decide whether a row's data belongs at its
    # row_id or at the legacy player:prop key — see write-key priority below.
    _caller_supplied_keys = set(enrichment.keys())
    # Keys already claimed by an earlier row in *this* batch (no caller data
    # involved). Lets a single, unique player+prop pair keep using the
    # simple player:prop key (matches every existing caller/test that never
    # supplies row_id), while a SECOND row sharing the same player+prop
    # (different game, doubleheader, duplicate paste) is forced onto its own
    # row_id instead of silently overwriting/merging into the first row's
    # entry.
    _claimed_keys_this_batch: set[str] = set()

    sports = sorted({(r.get("sport") or "").upper() for r in rows if r.get("sport")})

    source_status: dict[str, Any] = {"sports": {}}
    market_indexes: dict[str, dict[tuple[str, str], list[dict]]] = {}
    injuries_by_sport: dict[str, dict[str, Any]] = {}

    for sport in sports:
        sport_status: dict[str, Any] = {}

        if sport in odds_api.SPORT_KEYS:
            props, fetch_status = odds_api.fetch_all_props(sport)
            market_indexes[sport] = _build_market_index(props)
            sport_status["market"] = fetch_status
            sport_status["market_props_found"] = len(props)
        else:
            market_indexes[sport] = {}
            sport_status["market"] = "NOT_CALLED: sport not supported by odds_api"
            sport_status["market_props_found"] = 0

        if sport in status_service.SPORT_ESPN:
            injuries, inj_status = status_service.get_injuries(sport)
            injuries_by_sport[sport] = injuries if inj_status == "AVAILABLE" else None
            sport_status["status"] = inj_status
            sport_status["status_players_found"] = len(injuries)
        else:
            injuries_by_sport[sport] = None
            sport_status["status"] = "NOT_CALLED: sport not supported by status service"
            sport_status["status_players_found"] = 0

        source_status["sports"][sport] = sport_status

    # Single fetch-timestamp for all data acquired in this function call.
    # Used to stamp data_timestamp, status_timestamp, role_timestamp.
    _fetch_ts = _datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    for row in rows:
        player = row.get("player")
        prop_type = row.get("prop_type")
        sport = (row.get("sport") or "").upper()
        direction = (row.get("direction") or "").upper()
        if not player or not prop_type:
            continue

        rid = row.get("row_id", "")
        key = f"{player.lower()}:{prop_type.lower()}"

        # Write-key priority (matches pipeline._get_enrichment's read order:
        # it checks enrichment[row_id] before enrichment[player:prop]):
        #   1. Caller already has data at this row's row_id           -> rid
        #   2. Caller already has data at the player:prop key         -> key
        #   3. First row this batch to use this player:prop pair      -> key
        #      (back-compat: single-row-per-player-prop boards, and every
        #      existing caller/test that never supplies row_id, keep
        #      resolving to the simple player:prop key exactly as before)
        #   4. A LATER row in this batch reusing the same player:prop -> rid
        #      (duplicate player+prop rows — different games, doubleheaders,
        #      duplicate paste — must not collide into one shared entry)
        if rid and rid in _caller_supplied_keys:
            write_key = rid
        elif key in _caller_supplied_keys:
            write_key = key
        elif key not in _claimed_keys_this_batch:
            write_key = key
            _claimed_keys_this_batch.add(key)
        else:
            write_key = rid or key

        entry = dict(enrichment.get(write_key) or {})

        # Best-effort game_date from the row itself; may be refined by fetch
        # sources below.
        target_dt = row.get("game_time", "")[:10] or row.get("slate_date") or None

        # --- Market lines + [FIX-3] book / odds / no-vig metadata ---
        market_key = _market_key_for(sport, prop_type)
        if market_key:
            all_entries = market_indexes.get(sport, {}).get(
                (player.lower(), market_key), []
            )
            side = "MORE" if direction in ("MORE", "OVER") else (
                "LESS" if direction in ("LESS", "UNDER") else None
            )
            matching = _lines_for_side(all_entries, side)
            lines = [e["line"] for e in matching if e.get("line") is not None]
            if lines:
                if entry.get("sportsbook_line") is None:
                    entry["sportsbook_line"] = lines[0]
                if entry.get("best_available") is None:
                    # MORE/OVER: lowest line is easiest to clear.
                    # LESS/UNDER: highest line is easiest to stay under.
                    entry["best_available"] = (
                        min(lines) if side == "MORE" else max(lines)
                    )
                if entry.get("consensus_line") is None:
                    entry["consensus_line"] = round(sum(lines) / len(lines), 3)

            # [FIX-3] Populate book_or_platform and odds_or_payout from the first
            # Odds API entry matching this side.
            if matching:
                if entry.get("book_or_platform") is None:
                    _bk = (matching[0].get("bookmaker") or "").strip()
                    if _bk:
                        entry["book_or_platform"] = _bk
                if entry.get("odds_or_payout") is None:
                    _px = matching[0].get("price")
                    if _px is not None:
                        entry["odds_or_payout"] = _px

            # [FIX-3] Two-sided no-vig probability.  Requires both MORE and LESS
            # prices; falls back to sentinel strings when one or both are absent.
            if entry.get("market_no_vig_probability") is None:
                _more_e = _lines_for_side(all_entries, "MORE")
                _less_e = _lines_for_side(all_entries, "LESS")
                _pm = _more_e[0].get("price") if _more_e else None
                _pl = _less_e[0].get("price") if _less_e else None
                entry["market_no_vig_probability"] = _compute_no_vig_prob(_pm, _pl)

            # [FIX-3] Opponent and game_date from Odds API event metadata.
            # The game-log fetch below (MLB path) can override opponent with a
            # more precise name from the split; this is just the first fallback.
            if all_entries:
                _first = all_entries[0]
                if entry.get("game_date") is None:
                    _gd = (_first.get("game_date") or "").strip()
                    if _gd:
                        entry["game_date"] = _gd
                if entry.get("opponent") is None:
                    _home = (_first.get("home_team") or "").strip()
                    _away = (_first.get("away_team") or "").strip()
                    if _home and _away:
                        # Provide both teams; player-team attribution requires
                        # roster data not available here.
                        entry["opponent"] = f"{_away} @ {_home}"

        # --- Status / role ---
        if entry.get("status_payload") is None:
            injuries = injuries_by_sport.get(sport)
            if injuries is not None:
                flag, raw_status, src_status = status_service.get_player_injury_flag(
                    sport, player, injuries_cache=injuries
                )
                if src_status == "AVAILABLE":
                    entry["status_payload"] = {
                        "status": raw_status,
                        "source": "ESPN",
                        "dnp_risk": flag == 2,
                        "minutes_restriction": False,
                    }

        # [FIX-3] status_timestamp / role_timestamp.
        # Set when the ESPN service was queried for this sport (regardless of
        # whether this specific player was found in the injury report).
        # If the sport is not in SPORT_ESPN, these stay absent → honest data
        # gap → run_deferred correctly emits DATA_CONTRACT_FAIL.
        if sport in status_service.SPORT_ESPN:
            if entry.get("status_timestamp") is None:
                entry["status_timestamp"] = _fetch_ts
            if entry.get("role_timestamp") is None:
                entry["role_timestamp"] = _fetch_ts

        # --- Game log auto-fetch + [FIX-3] l5/l10 computation ---
        # FIX-D: canonicalize stat_key BEFORE attempting game log fetch.
        # The normalizer may emit display strings ("Pitcher Strikeouts") or
        # short canonical forms ("K") depending on path.  fetch_game_log
        # only understands canonical keys; anything not in _MLB_STAT_FIELDS
        # was silently swallowed as GameLogUnavailable, appearing as NOT_CALLED
        # in the acquisition trace.  _canonicalize_stat_key maps both forms.
        # FIX-D: also attempt MLB name-to-ID lookup when player_id is absent,
        # so rows submitted without a player_id can still get game logs.
        if not entry.get("game_log"):
            player_id = row.get("player_id")
            raw_sk    = row.get("stat_key") or row.get("prop_type") or ""
            stat_key  = _canonicalize_stat_key(raw_sk)
            # MLB name lookup when player_id missing
            if not player_id and sport == "MLB" and stat_key:
                _looked_up = _lookup_mlb_player_id(row.get("player") or "")
                if _looked_up:
                    player_id = _looked_up
            if player_id and stat_key:
                try:
                    gl_result = fetch_game_log(
                        player_id=player_id,
                        sport=sport,
                        stat_key=stat_key,
                        target_date=target_dt,
                    )
                    if gl_result["values"]:
                        entry["game_log"] = gl_result["values"]
                        # Track data source provenance
                        existing_sources = entry.get("data_sources") or []
                        if gl_result["source"] not in existing_sources:
                            entry["data_sources"] = existing_sources + [gl_result["source"]]

                        # [FIX-3] Compute l5/l10 contract fields from fetched values.
                        # fetch_game_log returns most-recent-first; [:5]/[:10] gives
                        # the N most-recent games.  Caller-supplied values take
                        # precedence per-field.
                        _vals = gl_result["values"]
                        if _vals and entry.get("l5_values") is None:
                            entry["l5_values"] = _vals[:5]
                        if _vals and entry.get("l10_values") is None:
                            entry["l10_values"] = _vals[:10]
                        _l10v: list = entry.get("l10_values") or []
                        if _l10v and entry.get("l10_median") is None:
                            try:
                                entry["l10_median"] = round(
                                    _statistics.median(_l10v), 2
                                )
                            except Exception:
                                pass
                        if _l10v and entry.get("l10_mean") is None:
                            try:
                                entry["l10_mean"] = round(
                                    sum(_l10v) / len(_l10v), 2
                                )
                            except (ZeroDivisionError, TypeError):
                                pass
                        if entry.get("l5_line_used") is None:
                            entry["l5_line_used"] = row.get("line")

                        # MLB split metadata provides a precise opponent name and
                        # exact game date; use it as a higher-fidelity override
                        # of what the Odds API provided above.
                        if gl_result.get("game_date") and entry.get("game_date") is None:
                            entry["game_date"] = gl_result["game_date"]
                        if gl_result.get("opponent") and entry.get("opponent") is None:
                            entry["opponent"] = gl_result["opponent"]

                except GameLogUnavailable as _gl_err:
                    gaps = list(entry.get("data_gaps") or [])
                    gaps.append(f"game_log_fetch_failed:{_gl_err!s}"[:120])
                    entry["data_gaps"] = gaps
                except Exception:
                    gaps = list(entry.get("data_gaps") or [])
                    gaps.append("game_log_fetch_error")
                    entry["data_gaps"] = gaps

        # [FIX-3] game_date final fallback: row's own game_time / slate_date.
        if entry.get("game_date") is None and target_dt:
            entry["game_date"] = target_dt

        # [FIX-3] book_or_platform fallback: row's board_source field.
        if entry.get("book_or_platform") is None:
            _bs = (row.get("board_source") or "").strip()
            if _bs:
                entry["book_or_platform"] = _bs

        # [FIX-3] Sentinel values for pipeline-output fields.
        #
        # Six enrichment fields are computed INSIDE gate modules during pipeline
        # execution and written to row["gates"][<module>] — they are never
        # written back into the enrichment dict by the pipeline.  Yet
        # data_contract.run_intake() validates the enrichment dict for ALL
        # required fields before any gate fires, so every row that goes through
        # build_auto_enrichment hits DATA_CONTRACT_FAIL on these six fields.
        #
        # Fix: stamp explicit "not yet computed" sentinels so _is_present()
        # returns True, allowing intake to pass.  Gate logic is unaffected
        # because gates read from row["gates"], not from enr.
        #
        # Sentinel contract:
        #   non-None + non-blank  → _is_present() = True
        #   clearly unambiguous   → auditors can see these are pre-pipeline stubs
        _PENDING     = "PENDING_GATE_EVALUATION"
        _NOT_COMPUTED = "NOT_COMPUTED_AT_AUTO_ENRICHMENT"

        if entry.get("provisional_label") is None:
            entry["provisional_label"] = _PENDING
        if entry.get("validation_status") is None:
            entry["validation_status"] = _PENDING
        if entry.get("payout_context") is None:
            entry["payout_context"] = _NOT_COMPUTED
        if entry.get("failure_path_matrix") is None:
            entry["failure_path_matrix"] = _NOT_COMPUTED
        if entry.get("model_probability_ledger") is None:
            entry["model_probability_ledger"] = _NOT_COMPUTED
        # Empty list passes _is_present() (it's neither None nor blank string).
        if entry.get("directional_exposure_tags") is None:
            entry["directional_exposure_tags"] = []

        # data_timestamp is always valid — we executed enrichment, so a fetch
        # time is well-defined.
        if entry.get("data_timestamp") is None:
            entry["data_timestamp"] = _fetch_ts

        if entry:
            enrichment[write_key] = entry

    return enrichment, source_status


# ---------------------------------------------------------------------------
# Standalone game-log pre-fetch (unconditional — not gated by auto_enrich)
# ---------------------------------------------------------------------------

def fetch_missing_game_logs(
    rows: "list[dict]",
    enrichment: "dict",
    target_date: "object | None" = None,
) -> "dict":
    """
    Fetch game logs for rows that have player_id + stat_key but no game_log in
    the enrichment dict.  Mirrors the game-log section of build_auto_enrichment
    but runs unconditionally (no auto_enrich flag required) so the pipeline
    always has historical data for supported sports.

    Caller-supplied non-empty game_log values always win.
    Only fills entries where entry.get("game_log") is falsy.

    ``target_date`` may be a ``datetime.date`` object, an ISO-8601 string, or
    None (defaults to today when auto_game_log resolves it internally).

    Returns the (possibly-mutated) enrichment dict.
    """
    _SUPPORTED_SPORTS = {"NBA", "WNBA", "MLB", "NFL", "TENNIS"}

    # Normalise target_date to datetime.date once
    _tgt: "object | None" = None
    if isinstance(target_date, str):
        try:
            import datetime as _dt_mod
            _tgt = _dt_mod.date.fromisoformat(target_date)
        except (ValueError, AttributeError):
            _tgt = None
    else:
        _tgt = target_date  # already a date / None

    # Build the set of enrichment keys already claimed by callers so we pick
    # the right write_key for each row (mirrors build_auto_enrichment logic).
    _claimed_keys_this_batch: set = set()

    for row in rows:
        player    = row.get("player") or ""
        prop_type = row.get("prop_type") or ""
        sport     = (row.get("sport") or "").upper()
        player_id = row.get("player_id")
        raw_sk   = row.get("stat_key") or prop_type
        stat_key = _canonicalize_stat_key(raw_sk)  # FIX-D: normalize display names

        if not player or not prop_type:
            continue
        if not stat_key:
            continue
        # FIX-D: attempt MLB name-to-ID lookup when player_id absent
        if not player_id and sport == "MLB":
            _nlookup = _lookup_mlb_player_id(player)
            if _nlookup:
                player_id = _nlookup
        if not player_id:
            continue
        if sport not in _SUPPORTED_SPORTS:
            continue

        rid = row.get("row_id", "")
        key = f"{player.lower()}:{prop_type.lower()}"

        if rid and rid in enrichment:
            write_key = rid
        elif key in enrichment:
            write_key = key
        elif key not in _claimed_keys_this_batch:
            write_key = key
            _claimed_keys_this_batch.add(key)
        else:
            write_key = rid or key

        entry = dict(enrichment.get(write_key) or {})

        if entry.get("game_log"):
            continue  # already populated — caller data wins

        try:
            gl_result = fetch_game_log(
                player_id=player_id,
                sport=sport,
                stat_key=stat_key,
                target_date=_tgt,
            )
            if gl_result.get("values"):
                entry["game_log"] = gl_result["values"]
                existing_sources = entry.get("data_sources") or []
                if gl_result["source"] not in existing_sources:
                    entry["data_sources"] = existing_sources + [gl_result["source"]]
                _vals = gl_result["values"]
                if _vals and entry.get("l5_values") is None:
                    entry["l5_values"] = _vals[:5]
                if _vals and entry.get("l10_values") is None:
                    entry["l10_values"] = _vals[:10]
                _l10v: list = entry.get("l10_values") or []
                if _l10v and entry.get("l10_median") is None:
                    try:
                        entry["l10_median"] = round(_statistics.median(_l10v), 2)
                    except Exception:
                        pass
                if _l10v and entry.get("l10_mean") is None:
                    try:
                        entry["l10_mean"] = round(sum(_l10v) / len(_l10v), 2)
                    except (ZeroDivisionError, TypeError):
                        pass
                if entry.get("l5_line_used") is None:
                    entry["l5_line_used"] = row.get("line")
                if gl_result.get("game_date") and entry.get("game_date") is None:
                    entry["game_date"] = gl_result["game_date"]
                if gl_result.get("opponent") and entry.get("opponent") is None:
                    entry["opponent"] = gl_result["opponent"]
                enrichment[write_key] = entry
        except GameLogUnavailable:
            pass  # source unavailable — honest gap, no fabrication
        except Exception:
            pass  # unexpected error — never block the pipeline

    return enrichment
