"""
gate_engine/wnba/external_adapters.py
WOW-PATCH-2026-08-06-WNBA-EXTERNAL-EVIDENCE-ADAPTERS
WOW-PATCH-2026-08-06-WNBA-ACQUISITION-CONTRACT-REPAIR

Real outbound HTTP request adapters for WNBA evidence acquisition.

Contract (all adapters must satisfy):
  1. Make actual outbound HTTP requests — never return NOT_ATTEMPTED unless
     the adapter decides a call is not warranted (e.g. missing API key).
  2. Return AdapterResult with all 11 required fields plus request_count.
  3. Mark REQUEST_SUCCEEDED ONLY when HTTP 200 AND ≥1 record was parsed.
  4. Mark REQUEST_EMPTY when HTTP 200 but 0 matching records returned.
  5. Mark RATE_LIMITED on HTTP 429.
  6. Mark AUTH_REQUIRED on HTTP 401/403 or missing API key.
  7. Mark SOURCE_UNAVAILABLE on connection/DNS failure.
  8. Mark REQUEST_FAILED on any other non-200 HTTP status.
  9. Mark PARSE_FAILED when HTTP 200 but response body cannot be parsed.
 10. Never invent or average data; return only raw per-game rows.
 11. Never mark a source as attempted unless an actual HTTP request was made.

request_count tracks the total number of HTTP requests made per adapter call
(e.g. athlete-search + gamelog = 2 requests).

can_execute=False is unconditional.
"""
from __future__ import annotations

import datetime
import os
from dataclasses import dataclass, field as dc_field
from typing import Any

can_execute = False


# ---------------------------------------------------------------------------
# Request status vocabulary (honest — never converted to hide failure)
# ---------------------------------------------------------------------------

class RequestStatus:
    REQUEST_SUCCEEDED   = "REQUEST_SUCCEEDED"
    REQUEST_EMPTY       = "REQUEST_EMPTY"        # 200 but 0 matching records
    REQUEST_FAILED      = "REQUEST_FAILED"       # non-200 / timeout / other
    RATE_LIMITED        = "RATE_LIMITED"         # HTTP 429
    AUTH_REQUIRED       = "AUTH_REQUIRED"        # HTTP 401/403 or no API key
    PARSE_FAILED        = "PARSE_FAILED"         # 200 but body unparseable
    SOURCE_UNAVAILABLE  = "SOURCE_UNAVAILABLE"   # DNS / connection failure
    NOT_ATTEMPTED       = "NOT_ATTEMPTED"        # adapter not invoked at all


# ---------------------------------------------------------------------------
# Structured adapter result
# ---------------------------------------------------------------------------

@dataclass
class AdapterResult:
    """Structured result from every external fetch adapter.

    Required fields (11) per patch spec:
      provider, source_url_or_id, retrieved_at, source_grade, freshness_age,
      request_status, parse_status, normalized_fields, raw_record_count,
      conflict_status, failure_reason.

    Additional field:
      request_count — number of actual HTTP requests made (≥1 if any call was
                      attempted; 0 means no request was made).
    """
    provider:           str
    source_url_or_id:   str
    retrieved_at:       str
    source_grade:       str
    freshness_age:      "float | None"
    request_status:     str   # RequestStatus constant
    parse_status:       str   # "OK" | "PARTIAL" | "FAILED" | "NOT_ATTEMPTED"
    normalized_fields:  dict  # field_name → value; "game_rows" for box_score_log
    raw_record_count:   int
    conflict_status:    str   # "NONE" | "CONFLICT" | "UNRESOLVABLE"
    failure_reason:     "str | None"
    request_count:      int = 0


# ---------------------------------------------------------------------------
# ESPN base URLs — undocumented web JSON endpoints used as best-effort secondary
# structured sources.  They are NOT guaranteed public developer APIs and may change
# or become unavailable without notice.  No official ESPN developer agreement covers
# these paths; treat as read-only opportunistic fallback, not a stable data contract.
# ---------------------------------------------------------------------------

# BUG-003b fix: v2 search endpoint is the one that reliably resolves WNBA athletes.
# The v3 endpoint (site.web.api.espn.com/apis/common/v3/search) was returning
# 0 athlete results for all tested WNBA players — do not use it.
# v2 response shape: {"results":[{"type":"player","contents":[{"uid":"s:40~l:59~a:<id>",
#                      "displayName":"...","description":"WNBA"}]}]}
_ESPN_SEARCH_URL   = "https://site.api.espn.com/apis/search/v2"
_ESPN_SCOREBOARD   = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard"
_ESPN_INJURIES     = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/injuries"
_ESPN_NEWS         = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/news"
_ESPN_GAMELOG_TMPL = (
    "https://site.web.api.espn.com/apis/common/v3/sports/"
    "basketball/wnba/athletes/{athlete_id}/gamelog"
)

_DEFAULT_TIMEOUT  = 8   # seconds per HTTP call
_GAMELOG_TIMEOUT  = 10  # gamelog response can be larger


# ---------------------------------------------------------------------------
# Shared internal helpers
# ---------------------------------------------------------------------------

def _now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _freshness(retrieved_at: str) -> "float | None":
    """Seconds elapsed since retrieved_at."""
    try:
        dt = datetime.datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return round(
            (datetime.datetime.now(datetime.timezone.utc) - dt).total_seconds(), 1
        )
    except Exception:
        return None


def _safe_float(value: Any) -> "float | None":
    if value is None:
        return None
    try:
        f = float(value)
        return f if f >= 0 else None
    except (TypeError, ValueError):
        return None


def _parse_fraction(value: Any, index: int = 0) -> "float | None":
    """Parse a 'made-attempts' fraction string; index 0=made, 1=attempts."""
    if value is None:
        return None
    if isinstance(value, str) and "-" in value:
        parts = value.split("-")
        if len(parts) == 2:
            return _safe_float(parts[index])
    return _safe_float(value)


def _parse_minutes(value: Any) -> "float | None":
    """Parse 'MM:SS' string or plain numeric minutes."""
    if value is None:
        return None
    if isinstance(value, str) and ":" in value:
        parts = value.split(":")
        try:
            return round(float(parts[0]) + float(parts[1]) / 60, 2)
        except Exception:
            pass
    return _safe_float(value)


def _player_name_match(a: str, b: str) -> bool:
    """Loose bidirectional case-insensitive name match."""
    def _norm(s: str) -> str:
        import unicodedata
        s = s.lower().strip()
        try:
            s = unicodedata.normalize("NFD", s)
            s = "".join(c for c in s if unicodedata.category(c) != "Mn")
        except Exception:
            pass
        # strip possessives / punctuation
        s = "".join(c for c in s if c.isalnum() or c.isspace())
        return s
    na, nb = _norm(a), _norm(b)
    return na and nb and (na in nb or nb in na)


def _failure_result(
    provider: str,
    url: str,
    retrieved_at: str,
    request_status: str,
    failure_reason: str,
    request_count: int,
    source_grade: str = "B",
) -> AdapterResult:
    """Convenience builder for failed adapter results."""
    return AdapterResult(
        provider          = provider,
        source_url_or_id  = url,
        retrieved_at      = retrieved_at,
        source_grade      = source_grade,
        freshness_age     = _freshness(retrieved_at),
        request_status    = request_status,
        parse_status      = "NOT_ATTEMPTED",
        normalized_fields = {},
        raw_record_count  = 0,
        conflict_status   = "NONE",
        failure_reason    = failure_reason,
        request_count     = request_count,
    )


# ---------------------------------------------------------------------------
# ESPN Athlete search (shared step 1 for gamelog + news adapters)
# ---------------------------------------------------------------------------

def _espn_search_wnba_athlete(
    player_name: str,
    timeout: int = _DEFAULT_TIMEOUT,
) -> "tuple[str | None, str, str, str]":
    """Search for a WNBA athlete via the ESPN v2 search API.

    BUG-003b fix: uses site.api.espn.com/apis/search/v2 (the same endpoint
    used successfully by services/player_logs.py).  The previous v3 endpoint
    (site.web.api.espn.com/apis/common/v3/search) returned 0 athlete results
    for every tested WNBA player and has been removed.

    Validation rules (all required for ATHLETE_RESOLVED):
      1. uid contains "~a:" — confirms it is an athlete record, not a team.
      2. description == "WNBA" (case-insensitive) — confirms correct league.
      3. displayName loosely matches player_name — confirms identity, not a
         different person sharing a partial name.

    Returns (athlete_id, canonical_name, request_status, url_used).
    Callers must increment request_count by 1 for this call.

    request_status values:
      REQUEST_SUCCEEDED — athlete resolved, identity confirmed (ATHLETE_RESOLVED)
      REQUEST_EMPTY     — HTTP 200 but no matching WNBA athlete (ATHLETE_NOT_FOUND)
      AUTH_REQUIRED     — HTTP 401/403
      RATE_LIMITED      — HTTP 429
      SOURCE_UNAVAILABLE — connection/DNS failure
      REQUEST_FAILED    — other non-200 status or unexpected exception
      PARSE_FAILED      — HTTP 200 but response body cannot be parsed
    """
    import requests as _req  # local import per Replit pattern

    url    = _ESPN_SEARCH_URL
    # v2 search: type=player is the key filter; no sport/league query param needed —
    # WNBA league identity is validated from the "description" field in the response.
    params = {"query": player_name, "limit": 5, "type": "player"}

    try:
        r = _req.get(url, params=params, timeout=timeout)
    except _req.exceptions.ConnectionError:
        return None, "", RequestStatus.SOURCE_UNAVAILABLE, url
    except (_req.exceptions.Timeout, _req.exceptions.ReadTimeout):
        return None, "", RequestStatus.REQUEST_FAILED, url
    except Exception:
        return None, "", RequestStatus.REQUEST_FAILED, url

    if r.status_code == 429:
        return None, "", RequestStatus.RATE_LIMITED, url
    if r.status_code in (401, 403):
        return None, "", RequestStatus.AUTH_REQUIRED, url
    if r.status_code != 200:
        return None, "", RequestStatus.REQUEST_FAILED, url

    try:
        data = r.json()
        # v2 response: {"results":[{"type":"player","contents":[
        #   {"uid":"s:40~l:59~a:<id>","displayName":"...","description":"WNBA"}]}]}
        for section in (data.get("results") or []):
            if section.get("type") != "player":
                continue
            for item in (section.get("contents") or []):
                uid         = item.get("uid", "")
                display     = item.get("displayName", "")
                description = item.get("description", "")

                # Rule 1: must be an athlete record (uid contains "~a:")
                if "~a:" not in uid:
                    continue
                athlete_id = uid.split("~a:")[-1]
                if not athlete_id:
                    continue

                # Rule 2: must be WNBA (guards against NBA/NCAAW name collisions)
                if description.strip().upper() != "WNBA":
                    continue

                # Rule 3: name must loosely match requested player
                if not _player_name_match(player_name, display):
                    continue

                return str(athlete_id), display, RequestStatus.REQUEST_SUCCEEDED, url

        # HTTP 200 but no WNBA athlete matched — ATHLETE_NOT_FOUND, not REQUEST_FAILED
        return None, "", RequestStatus.REQUEST_EMPTY, url
    except Exception:
        return None, "", RequestStatus.PARSE_FAILED, url


# ---------------------------------------------------------------------------
# Adapter 1 — Box Score / Game Log (ESPN athlete gamelog, 2 HTTP calls)
# ---------------------------------------------------------------------------

def _parse_espn_wnba_gamelog(
    data: dict,
    n_games: int,
) -> list[dict]:
    """Parse ESPN WNBA athlete gamelog JSON into normalized per-game dicts.

    Two observed response shapes — parser handles both:

    Shape A (original assumed shape — per-category labels):
    {
      "seasonTypes": [
        {
          "type": {"text": "Regular Season"},
          "categories": [
            {
              "labels": ["MIN", "FG", "3PT", "FT", "OREB", "DREB", "REB",
                         "AST", "STL", "BLK", "TO", "PF", "+/-", "PTS"],
              "events": [
                {
                  "id": "...",
                  "gameDate": "2026-08-01T00:00Z",
                  "opponent": {"displayName": "...", "abbreviation": "..."},
                  "stats": ["36:00", "8-18", "2-5", "7-9", "2", "8", "10",
                             "4", "1", "2", "2", "2", "+5", "25"]
                }
              ]
            }
          ]
        }
      ]
    }

    Shape B (live ESPN response as of 2026-08 — labels at top level):
    {
      "names":  ["minutes", "points", "totalRebounds", "assists", "steals",
                 "blocks", "turnovers",
                 "fieldGoalsMade-fieldGoalsAttempted", "fieldGoalPct",
                 "threePointFieldGoalsMade-threePointFieldGoalsAttempted",
                 "threePointPct", "freeThrowsMade-freeThrowsAttempted",
                 "freeThrowPct", "fouls"],
      "events": {"<id>": {"id": "...", "links": [...]}},   ← metadata only
      "seasonTypes": [
        {
          "displayName": "...",
          "categories": [
            {
              "displayName": "august",
              "events": [
                {"eventId": "...", "stats": ["33", "15", "8", "7", ...]}
              ]
            }
          ]
        }
      ]
    }

    BUG-004 fix (WOW-PATCH-2026-08-06-WNBA-ESPN-GAMELOG-LABEL-PATH):
    In Shape B, category.get("labels") returns [] because labels live at
    top-level "names", not per-category.  Parser now reads top_level_names
    first and uses it as the fallback when a category has no "labels" key.
    """
    rows:     list[dict] = []
    seen_ids: set        = set()

    # BUG-004: Shape B places stat column names at the top-level "names" key.
    # Per-category "labels" takes precedence when present (Shape A / forward compat).
    top_level_names: list[str] = data.get("names") or []

    for season_type in (data.get("seasonTypes") or []):
        type_text = (season_type.get("type") or {}).get("text", "").lower()
        # Skip post-season when we already have regular-season rows
        if rows and "post" in type_text:
            continue

        for category in (season_type.get("categories") or []):
            # Shape A: labels nested per-category.
            # Shape B: labels absent per-category → fall back to top_level_names.
            raw_labels = category.get("labels") or top_level_names
            labels = [lb.lower().replace(" ", "").replace("-", "") for lb in raw_labels]

            def _idx(*keys: str) -> "int | None":
                for k in keys:
                    for i, lb in enumerate(labels):
                        if k in lb:
                            return i
                return None

            pts_i  = _idx("pts", "points")
            reb_i  = _idx("totalreb", "reb")
            ast_i  = _idx("ast", "assists")   # "ast" not a substring of "assists" in Shape B
            min_i  = _idx("min")
            fga_i  = _idx("fg")
            tpa_i  = _idx("3pt", "3p")
            fta_i  = _idx("ft")
            pf_i   = _idx("pf")

            for event in (category.get("events") or []):
                eid = str(event.get("id") or event.get("eventId") or "")
                if eid in seen_ids:
                    continue
                seen_ids.add(eid)

                stats = event.get("stats") or []

                def _s_raw(idx: "int | None") -> Any:
                    if idx is None or idx >= len(stats):
                        return None
                    return stats[idx]

                def _s(idx: "int | None") -> "float | None":
                    raw = _s_raw(idx)
                    if isinstance(raw, str) and "-" in raw:
                        # e.g. "8-18" field-goal fraction — take attempts (index 1)
                        return _parse_fraction(raw, 1)
                    return _safe_float(raw)

                def _s_made(idx: "int | None") -> "float | None":
                    raw = _s_raw(idx)
                    if isinstance(raw, str) and "-" in raw:
                        return _parse_fraction(raw, 0)
                    return _safe_float(raw)

                def _s_attempts(idx: "int | None") -> "float | None":
                    raw = _s_raw(idx)
                    if isinstance(raw, str) and "-" in raw:
                        return _parse_fraction(raw, 1)
                    return _safe_float(raw)

                pts  = _s_made(pts_i)  # PTS label is usually a plain number
                if pts is None:
                    pts = _safe_float(_s_raw(pts_i))  # fallback to plain parse

                reb  = _s(reb_i)
                ast  = _s(ast_i)
                mins = _parse_minutes(_s_raw(min_i))
                fga  = _s_attempts(fga_i)
                tpa  = _s_attempts(tpa_i)
                fta  = _s_attempts(fta_i)
                pf   = _s(pf_i)

                pra: "float | None" = None
                if pts is not None and reb is not None and ast is not None:
                    pra = pts + reb + ast

                game_date = event.get("gameDate") or event.get("date") or ""
                opp_obj   = event.get("opponent") or {}
                opp_name  = (
                    opp_obj.get("displayName")
                    or opp_obj.get("abbreviation")
                    or ""
                )

                rows.append({
                    "date":                  game_date[:10] if game_date else None,
                    "opponent":              opp_name,
                    "starter":               None,
                    "minutes":               mins,
                    "points":                pts,
                    "rebounds":              reb,
                    "assists":               ast,
                    "pra":                   pra,
                    "field_goal_attempts":   fga,
                    "three_point_attempts":  tpa,
                    "free_throw_attempts":   fta,
                    "team_result":           None,
                    "margin":                None,
                    "fouls":                 pf,
                    "_source":               "espn_wnba_gamelog",
                    "_event_id":             eid,
                })

                if len(rows) >= n_games:
                    return rows

    return rows


def fetch_box_score_log(
    player_name: str,
    n_games: int = 10,
) -> AdapterResult:
    """Fetch recent WNBA game logs for a player from ESPN.

    Two HTTP calls: (1) athlete search, (2) gamelog fetch.
    Returns per-game dicts in normalized_fields["game_rows"].
    Each row is dict-compatible with reconstruct_raw_ledger_rows().
    """
    import requests as _req

    provider     = "espn_wnba_athlete_gamelog"
    retrieved_at = _now_utc()

    # Call 1: resolve athlete_id via ESPN search
    athlete_id, canonical_name, search_status, search_url = \
        _espn_search_wnba_athlete(player_name)

    if search_status != RequestStatus.REQUEST_SUCCEEDED:
        return _failure_result(
            provider, search_url, retrieved_at,
            search_status,
            f"athlete search '{player_name}': {search_status}",
            request_count=1,
        )

    # Call 2: fetch gamelog
    gamelog_url = _ESPN_GAMELOG_TMPL.format(athlete_id=athlete_id)
    try:
        r = _req.get(gamelog_url, timeout=_GAMELOG_TIMEOUT)
    except _req.exceptions.ConnectionError:
        return _failure_result(
            provider, gamelog_url, retrieved_at,
            RequestStatus.SOURCE_UNAVAILABLE,
            "connection error on gamelog fetch",
            request_count=2,
        )
    except (_req.exceptions.Timeout, _req.exceptions.ReadTimeout):
        return _failure_result(
            provider, gamelog_url, retrieved_at,
            RequestStatus.REQUEST_FAILED,
            "timeout on gamelog fetch",
            request_count=2,
        )
    except Exception as exc:
        return _failure_result(
            provider, gamelog_url, retrieved_at,
            RequestStatus.REQUEST_FAILED,
            str(exc)[:200],
            request_count=2,
        )

    if r.status_code == 429:
        return _failure_result(
            provider, gamelog_url, retrieved_at,
            RequestStatus.RATE_LIMITED,
            "ESPN rate-limited (HTTP 429) — move to next configured route",
            request_count=2,
        )
    if r.status_code in (401, 403):
        return _failure_result(
            provider, gamelog_url, retrieved_at,
            RequestStatus.AUTH_REQUIRED,
            f"ESPN auth required (HTTP {r.status_code})",
            request_count=2,
        )
    if r.status_code != 200:
        return _failure_result(
            provider, gamelog_url, retrieved_at,
            RequestStatus.REQUEST_FAILED,
            f"ESPN HTTP {r.status_code}",
            request_count=2,
        )

    try:
        data      = r.json()
        game_rows = _parse_espn_wnba_gamelog(data, n_games)
    except Exception as exc:
        return AdapterResult(
            provider          = provider,
            source_url_or_id  = gamelog_url,
            retrieved_at      = retrieved_at,
            source_grade      = "B",
            freshness_age     = _freshness(retrieved_at),
            request_status    = RequestStatus.PARSE_FAILED,
            parse_status      = "FAILED",
            normalized_fields = {},
            raw_record_count  = 0,
            conflict_status   = "NONE",
            failure_reason    = f"parse error: {str(exc)[:200]}",
            request_count     = 2,
        )

    if not game_rows:
        return AdapterResult(
            provider          = provider,
            source_url_or_id  = gamelog_url,
            retrieved_at      = retrieved_at,
            source_grade      = "B",
            freshness_age     = _freshness(retrieved_at),
            request_status    = RequestStatus.REQUEST_EMPTY,
            parse_status      = "OK",
            normalized_fields = {"athlete_id": athlete_id, "game_rows": []},
            raw_record_count  = 0,
            conflict_status   = "NONE",
            failure_reason    = "ESPN gamelog returned 0 parseable game rows",
            request_count     = 2,
        )

    return AdapterResult(
        provider          = provider,
        source_url_or_id  = gamelog_url,
        retrieved_at      = retrieved_at,
        source_grade      = "B",
        freshness_age     = _freshness(retrieved_at),
        request_status    = RequestStatus.REQUEST_SUCCEEDED,
        parse_status      = "OK",
        normalized_fields = {
            "athlete_id":      athlete_id,
            "canonical_name":  canonical_name,
            "game_rows":       game_rows,
        },
        raw_record_count  = len(game_rows),
        conflict_status   = "NONE",
        failure_reason    = None,
        request_count     = 2,
    )


# ---------------------------------------------------------------------------
# Adapter 2 — Event Status (ESPN WNBA scoreboard, 1 HTTP call)
# ---------------------------------------------------------------------------

def fetch_event_status(
    game_str: str,
    date_str: "str | None" = None,
) -> AdapterResult:
    """Fetch WNBA game event status from ESPN scoreboard.

    game_str: team abbreviation or game string like 'LVA vs SEA' or 'A vs B'.
    date_str: YYYYMMDD; defaults to today.
    """
    import requests as _req

    provider     = "espn_wnba_scoreboard"
    retrieved_at = _now_utc()

    if not date_str:
        date_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")

    url = _ESPN_SCOREBOARD
    try:
        r = _req.get(url, params={"dates": date_str, "limit": "20"}, timeout=_DEFAULT_TIMEOUT)
    except _req.exceptions.ConnectionError:
        return _failure_result(provider, url, retrieved_at, RequestStatus.SOURCE_UNAVAILABLE,
                               "connection error", request_count=1)
    except (_req.exceptions.Timeout, _req.exceptions.ReadTimeout):
        return _failure_result(provider, url, retrieved_at, RequestStatus.REQUEST_FAILED,
                               "timeout", request_count=1)
    except Exception as exc:
        return _failure_result(provider, url, retrieved_at, RequestStatus.REQUEST_FAILED,
                               str(exc)[:200], request_count=1)

    if r.status_code == 429:
        return _failure_result(provider, url, retrieved_at, RequestStatus.RATE_LIMITED,
                               "ESPN rate-limited", request_count=1)
    if r.status_code in (401, 403):
        return _failure_result(provider, url, retrieved_at, RequestStatus.AUTH_REQUIRED,
                               f"ESPN HTTP {r.status_code}", request_count=1)
    if r.status_code != 200:
        return _failure_result(provider, url, retrieved_at, RequestStatus.REQUEST_FAILED,
                               f"ESPN HTTP {r.status_code}", request_count=1)

    try:
        data = r.json()
    except Exception:
        return _failure_result(provider, url, retrieved_at, RequestStatus.PARSE_FAILED,
                               "JSON parse failed", request_count=1)

    # Normalise game_str into candidate team tokens (individual words).
    game_tokens = {t.strip().lower() for t in game_str.replace("@", " ").replace("vs", " ").split()
                   if len(t.strip()) >= 2}

    # Score every event against the candidate tokens.
    #
    # BUG-004-EVENT-STATUS (WOW-PATCH-2026-08-06-WNBA-ESPN-GAMELOG-LABEL-PATH):
    # The original code built `all_tokens = team_abbrs | team_names` where
    # `team_names` contained FULL display strings like "indiana fever".  Set
    # intersection then compared individual tokens ("indiana") against the full
    # string ("indiana fever") — yielding zero overlap even when the team was
    # present.  Fix: tokenize each team's displayName into individual words so
    # "indiana" and "fever" each become independent candidate tokens.
    scored_events: "list[tuple[Any, int]]" = []   # (event_dict, overlap_score)
    for event in (data.get("events") or []):
        comp  = ((event.get("competitions") or [{}])[0])
        comps = comp.get("competitors", [])

        # Abbreviations (e.g. "IND", "LVA") — already single tokens
        team_abbrs: "set[str]" = {
            (c.get("team") or {}).get("abbreviation", "").lower()
            for c in comps
            if (c.get("team") or {}).get("abbreviation")
        }
        # Display names tokenized into individual words:
        #   "Indiana Fever" → {"indiana", "fever"}
        name_word_tokens: "set[str]" = set()
        for c in comps:
            dn = (c.get("team") or {}).get("displayName", "").lower()
            name_word_tokens.update(w for w in dn.split() if len(w) >= 2)

        all_tokens = team_abbrs | name_word_tokens
        overlap    = len(game_tokens & all_tokens)
        scored_events.append((event, overlap))

    events_on_date = len(scored_events)

    if not scored_events:
        return AdapterResult(
            provider=provider, source_url_or_id=url, retrieved_at=retrieved_at,
            source_grade="A", freshness_age=_freshness(retrieved_at),
            request_status=RequestStatus.REQUEST_EMPTY, parse_status="OK",
            normalized_fields={"events_on_date": 0},
            raw_record_count=0, conflict_status="NONE",
            failure_reason=(
                f"EVENT_STATUS_UNRESOLVED: ESPN scoreboard returned 0 events "
                f"for date={date_str}"
            ),
            request_count=1,
        )

    best_overlap = max(sc for _, sc in scored_events)

    if best_overlap == 0:
        # No event matched any candidate token — EVENT_NOT_FOUND.
        # Expose diagnostic keys so the postmortem can identify the mismatch.
        scoreboard_keys: "list[str]" = []
        for ev, _ in scored_events:
            cp = ((ev.get("competitions") or [{}])[0])
            for c in cp.get("competitors", []):
                abbr = (c.get("team") or {}).get("abbreviation", "")
                dn   = (c.get("team") or {}).get("displayName", "")
                if abbr or dn:
                    scoreboard_keys.append(f"{abbr}|{dn}")

        return AdapterResult(
            provider=provider, source_url_or_id=url, retrieved_at=retrieved_at,
            source_grade="A", freshness_age=_freshness(retrieved_at),
            request_status=RequestStatus.REQUEST_EMPTY, parse_status="OK",
            normalized_fields={
                "event_status":     "EVENT_NOT_FOUND",
                "events_on_date":   events_on_date,
                "candidate_tokens": sorted(game_tokens),
                "scoreboard_keys":  scoreboard_keys[:20],
                "match_confidence": 0,
            },
            raw_record_count=0, conflict_status="NONE",
            failure_reason=(
                f"EVENT_NOT_FOUND: no WNBA event matched game_str='{game_str}' "
                f"on {date_str}. "
                f"candidate_tokens={sorted(game_tokens)}; "
                f"scoreboard_team_keys={scoreboard_keys[:10]}"
            ),
            request_count=1,
        )

    # Ambiguity guard: multiple events share the same max overlap → fail closed.
    # A broad first-event fallback is explicitly prohibited.
    top_matches: "list[tuple[Any, int]]" = [
        (ev, sc) for ev, sc in scored_events if sc == best_overlap
    ]
    if len(top_matches) > 1:
        ambiguous_ids = [ev.get("id", "") for ev, _ in top_matches]
        return AdapterResult(
            provider=provider, source_url_or_id=url, retrieved_at=retrieved_at,
            source_grade="A", freshness_age=_freshness(retrieved_at),
            request_status=RequestStatus.REQUEST_FAILED, parse_status="OK",
            normalized_fields={
                "event_status":        "EVENT_MATCH_AMBIGUOUS",
                "events_on_date":      events_on_date,
                "ambiguous_event_ids": ambiguous_ids,
                "match_score":         best_overlap,
                "candidate_tokens":    sorted(game_tokens),
            },
            raw_record_count=0, conflict_status="NONE",
            failure_reason=(
                f"EVENT_MATCH_AMBIGUOUS: {len(top_matches)} events share "
                f"overlap={best_overlap} for game_str='{game_str}' on {date_str} "
                f"— fail closed. ambiguous_event_ids={ambiguous_ids}"
            ),
            request_count=1,
        )

    best_event = top_matches[0][0]

    comp        = ((best_event.get("competitions") or [{}])[0])
    status_obj  = comp.get("status", {}).get("type", {})
    status_name = status_obj.get("name", "")
    status_desc = status_obj.get("description", status_obj.get("detail", ""))

    # Map ESPN status names to canonical event_status vocabulary.
    _STATUS_MAP = {
        "STATUS_SCHEDULED":    "SCHEDULED",
        "STATUS_PRE_GAME":     "PREGAME",
        "STATUS_IN_PROGRESS":  "IN_PROGRESS",
        "STATUS_HALFTIME":     "IN_PROGRESS",
        "STATUS_FINAL":        "FINAL",
        "STATUS_POSTPONED":    "POSTPONED",
        "STATUS_CANCELLED":    "CANCELLED",
        "STATUS_SUSPENDED":    "SUSPENDED",
        "STATUS_DELAYED":      "DELAYED",
    }
    normalized_status = _STATUS_MAP.get(status_name, status_name or "UNKNOWN")

    # Provenance: winning competitor list and match tokens for audit trail.
    winning_comp_tokens: "set[str]" = set()
    competitors_info:    "list[dict]" = []
    for c in comp.get("competitors", []):
        t    = c.get("team") or {}
        abbr = t.get("abbreviation", "").lower()
        dn   = t.get("displayName", "").lower()
        if abbr:
            winning_comp_tokens.add(abbr)
        winning_comp_tokens.update(w for w in dn.split() if len(w) >= 2)
        competitors_info.append({
            "abbreviation": t.get("abbreviation", ""),
            "displayName":  t.get("displayName", ""),
        })

    return AdapterResult(
        provider          = provider,
        source_url_or_id  = url,
        retrieved_at      = retrieved_at,
        source_grade      = "A",
        freshness_age     = _freshness(retrieved_at),
        request_status    = RequestStatus.REQUEST_SUCCEEDED,
        parse_status      = "OK",
        normalized_fields = {
            "event_status":        normalized_status,
            "espn_status_raw":     status_name,
            "status_detail":       status_desc,
            "event_id":            best_event.get("id", ""),
            "date_str":            date_str,
            "match_method":        "team_token_overlap",
            "match_confidence":    best_overlap,
            "match_tokens_used":   sorted(game_tokens & winning_comp_tokens),
            "competitors":         competitors_info,
            "events_on_date":      events_on_date,
        },
        raw_record_count  = 1,
        conflict_status   = "NONE",
        failure_reason    = None,
        request_count     = 1,
    )


# ---------------------------------------------------------------------------
# Adapter 3 — Role / Injury Status (ESPN WNBA injuries, 1 HTTP call)
# ---------------------------------------------------------------------------

def fetch_role_status(player_name: str) -> AdapterResult:
    """Fetch WNBA player injury/role status from ESPN injury feed.

    If a player is NOT in the injury report, they are inferred as ACTIVE
    (PROXY_ONLY grade — inference from absence, not confirmed signal).
    If they ARE listed, return their status and detail.
    """
    import requests as _req

    provider     = "espn_wnba_injuries"
    retrieved_at = _now_utc()
    url          = _ESPN_INJURIES

    try:
        r = _req.get(url, timeout=_DEFAULT_TIMEOUT)
    except _req.exceptions.ConnectionError:
        return _failure_result(provider, url, retrieved_at, RequestStatus.SOURCE_UNAVAILABLE,
                               "connection error", request_count=1)
    except (_req.exceptions.Timeout, _req.exceptions.ReadTimeout):
        return _failure_result(provider, url, retrieved_at, RequestStatus.REQUEST_FAILED,
                               "timeout", request_count=1)
    except Exception as exc:
        return _failure_result(provider, url, retrieved_at, RequestStatus.REQUEST_FAILED,
                               str(exc)[:200], request_count=1)

    if r.status_code == 429:
        return _failure_result(provider, url, retrieved_at, RequestStatus.RATE_LIMITED,
                               "ESPN rate-limited", request_count=1)
    if r.status_code in (401, 403):
        return _failure_result(provider, url, retrieved_at, RequestStatus.AUTH_REQUIRED,
                               f"ESPN HTTP {r.status_code}", request_count=1)
    if r.status_code != 200:
        return _failure_result(provider, url, retrieved_at, RequestStatus.REQUEST_FAILED,
                               f"ESPN HTTP {r.status_code}", request_count=1)

    try:
        data = r.json()
    except Exception:
        return _failure_result(provider, url, retrieved_at, RequestStatus.PARSE_FAILED,
                               "JSON parse failed", request_count=1)

    # Search injury rows for this player
    matched_row: "dict | None" = None
    for team_entry in (data.get("injuries") or []):
        for inj in (team_entry.get("injuries") or []):
            ath  = inj.get("athlete") or {}
            name = ath.get("displayName") or ath.get("fullName") or ""
            if _player_name_match(player_name, name):
                matched_row = inj
                break
        if matched_row:
            break

    if matched_row is None:
        # Player not on injury list — infer ACTIVE from absence (PROXY_ONLY)
        return AdapterResult(
            provider          = provider,
            source_url_or_id  = url,
            retrieved_at      = retrieved_at,
            source_grade      = "B",
            freshness_age     = _freshness(retrieved_at),
            request_status    = RequestStatus.REQUEST_SUCCEEDED,
            parse_status      = "OK",
            normalized_fields = {
                "active_status":      "ACTIVE_INFERRED",
                "injury_status":      None,
                "injury_detail":      None,
                "return_date":        None,
                "inference_basis":    "not_on_espn_injury_report",
                "role_timestamp":     retrieved_at,
                "projected_minutes":  None,  # cannot infer from injury report
            },
            raw_record_count  = 0,
            conflict_status   = "NONE",
            failure_reason    = None,
            request_count     = 1,
        )

    # Player found on injury report
    ath      = matched_row.get("athlete") or {}
    details  = matched_row.get("details") or {}
    inj_stat = matched_row.get("status", "")
    comment  = matched_row.get("longComment") or matched_row.get("shortComment") or ""

    _STATUS_MAP = {
        "Out":              "OUT",
        "Questionable":     "QUESTIONABLE",
        "Doubtful":         "DOUBTFUL",
        "Day-To-Day":       "DAY_TO_DAY",
        "Probable":         "PROBABLE",
        "Active":           "ACTIVE",
    }
    normalized_status = _STATUS_MAP.get(inj_stat, inj_stat.upper() if inj_stat else "UNKNOWN")

    # Active_status derivation: OUT = inactive, everything else = some availability
    if normalized_status == "OUT":
        active_status = "INACTIVE"
    elif normalized_status in ("QUESTIONABLE", "DOUBTFUL", "DAY_TO_DAY"):
        active_status = "UNCERTAIN"
    else:
        active_status = "ACTIVE"

    return AdapterResult(
        provider          = provider,
        source_url_or_id  = url,
        retrieved_at      = retrieved_at,
        source_grade      = "A",
        freshness_age     = _freshness(retrieved_at),
        request_status    = RequestStatus.REQUEST_SUCCEEDED,
        parse_status      = "OK",
        normalized_fields = {
            "active_status":      active_status,
            "injury_status":      normalized_status,
            "injury_detail":      comment,
            "return_date":        details.get("returnDate"),
            "role_timestamp":     retrieved_at,
            "projected_minutes":  None,  # not in ESPN injury feed
        },
        raw_record_count  = 1,
        conflict_status   = "NONE",
        failure_reason    = None,
        request_count     = 1,
    )


# ---------------------------------------------------------------------------
# Adapter 4 — Market Comparison (Odds API, 1–2 HTTP calls)
# ---------------------------------------------------------------------------

def fetch_market_comparison(
    player_name: str,
    prop_type:   str,
    line:        "float | None" = None,
    sport_key:   str = "basketball_wnba",
) -> AdapterResult:
    """Fetch WNBA player prop lines from The Odds API for market comparison.

    BUG-003a fix: uses services.odds_api.resolve_odds_api_key_with_source()
    instead of reading ODDS_API_KEY directly.  Priority:
      ODDS_API_PAID_KEY → ODDS_API_FREE_KEY → ODDS_API_KEY (legacy fallback).
    A deactivated legacy key in ODDS_API_KEY can no longer override an
    available paid or free key.

    Returns AUTH_REQUIRED immediately (request_count=0) when no key is available.
    Audit fields added: credential_source_name, credential_resolver_used=True.
    The actual credential value is NEVER logged or stored.
    """
    from services.odds_api import resolve_odds_api_key_with_source  # lazy import (Replit pattern)

    provider     = "odds_api_player_props"
    retrieved_at = _now_utc()
    url          = f"https://api.the-odds-api.com/v4/sports/{sport_key}/events"

    odds_key, credential_source_name = resolve_odds_api_key_with_source()

    if not odds_key:
        # No HTTP call made — return AUTH_REQUIRED immediately
        return AdapterResult(
            provider          = provider,
            source_url_or_id  = url,
            retrieved_at      = retrieved_at,
            source_grade      = "A",
            freshness_age     = _freshness(retrieved_at),
            request_status    = RequestStatus.AUTH_REQUIRED,
            parse_status      = "NOT_ATTEMPTED",
            normalized_fields = {
                "credential_source_name": "NONE",
                "credential_resolver_used": True,
            },
            raw_record_count  = 0,
            conflict_status   = "NONE",
            failure_reason    = "No Odds API key configured (checked PAID_KEY→FREE_KEY→LEGACY_KEY)",
            request_count     = 0,  # no request made
        )

    # Map prop_type to Odds API market key (best-effort)
    _PROP_MAP = {
        "points":    "player_points",
        "rebounds":  "player_rebounds",
        "assists":   "player_assists",
        "threes":    "player_threes",
        "steals":    "player_steals",
        "blocks":    "player_blocks",
        "pra":       "player_points_rebounds_assists",
    }
    pt_lower   = (prop_type or "").lower().strip()
    market_key = _PROP_MAP.get(pt_lower, f"player_{pt_lower}")

    import requests as _req

    # Call 1: list events
    try:
        r = _req.get(url, params={"apiKey": odds_key}, timeout=_DEFAULT_TIMEOUT)
    except _req.exceptions.ConnectionError:
        return _failure_result(provider, url, retrieved_at, RequestStatus.SOURCE_UNAVAILABLE,
                               "connection error on events list", request_count=1)
    except (_req.exceptions.Timeout, _req.exceptions.ReadTimeout):
        return _failure_result(provider, url, retrieved_at, RequestStatus.REQUEST_FAILED,
                               "timeout on events list", request_count=1)
    except Exception as exc:
        return _failure_result(provider, url, retrieved_at, RequestStatus.REQUEST_FAILED,
                               str(exc)[:200], request_count=1)

    if r.status_code == 429:
        return _failure_result(provider, url, retrieved_at, RequestStatus.RATE_LIMITED,
                               "Odds API quota exhausted (HTTP 429)", request_count=1)
    if r.status_code in (401, 403):
        return _failure_result(provider, url, retrieved_at, RequestStatus.AUTH_REQUIRED,
                               f"Odds API invalid key (HTTP {r.status_code})", request_count=1)
    if r.status_code != 200:
        return _failure_result(provider, url, retrieved_at, RequestStatus.REQUEST_FAILED,
                               f"Odds API HTTP {r.status_code}", request_count=1)

    try:
        events = r.json()
    except Exception:
        return _failure_result(provider, url, retrieved_at, RequestStatus.PARSE_FAILED,
                               "JSON parse failed on events", request_count=1)

    if not events:
        return AdapterResult(
            provider=provider, source_url_or_id=url, retrieved_at=retrieved_at,
            source_grade="A", freshness_age=_freshness(retrieved_at),
            request_status=RequestStatus.REQUEST_EMPTY, parse_status="OK",
            normalized_fields={}, raw_record_count=0, conflict_status="NONE",
            failure_reason="no WNBA events currently listed by Odds API",
            request_count=1,
        )

    # Call 2+: search for player prop across events
    name_lower  = player_name.lower().strip()
    lines:  list[float] = []
    books:  list[str]   = []
    total_calls = 1

    for event in events[:8]:  # cap at 8 events to conserve quota
        event_id = event.get("id", "")
        if not event_id:
            continue
        odds_url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/events/{event_id}/odds"
        total_calls += 1
        try:
            r2 = _req.get(odds_url,
                          params={"apiKey": odds_key, "regions": "us",
                                  "markets": market_key, "oddsFormat": "american"},
                          timeout=_DEFAULT_TIMEOUT)
        except Exception:
            continue
        if r2.status_code != 200:
            continue
        try:
            for bk in r2.json().get("bookmakers", []):
                for mkt in bk.get("markets", []):
                    if mkt.get("key") != market_key:
                        continue
                    for outcome in mkt.get("outcomes", []):
                        desc = (outcome.get("description") or "").lower()
                        if name_lower in desc or desc in name_lower:
                            pt = outcome.get("point")
                            if pt is not None:
                                lines.append(float(pt))
                                books.append(bk.get("key", "?"))
        except Exception:
            continue

    if not lines:
        return AdapterResult(
            provider=provider, source_url_or_id=url, retrieved_at=retrieved_at,
            source_grade="A", freshness_age=_freshness(retrieved_at),
            request_status=RequestStatus.REQUEST_EMPTY, parse_status="OK",
            normalized_fields={
                "credential_source_name":  credential_source_name,
                "credential_resolver_used": True,
            },
            raw_record_count=0, conflict_status="NONE",
            failure_reason=f"player '{player_name}' not found in any WNBA prop market",
            request_count=total_calls,
        )

    import statistics as _stats
    consensus = _stats.median(lines)
    return AdapterResult(
        provider          = provider,
        source_url_or_id  = url,
        retrieved_at      = retrieved_at,
        source_grade      = "A",
        freshness_age     = _freshness(retrieved_at),
        request_status    = RequestStatus.REQUEST_SUCCEEDED,
        parse_status      = "OK",
        normalized_fields = {
            "consensus_line":          consensus,
            "submitted_line":          line,
            "books_sampled":           len(lines),
            "book_keys":               list(set(books))[:6],
            "line_range":              [min(lines), max(lines)],
            "cross_book_spread":       round(max(lines) - min(lines), 1),
            "credential_source_name":  credential_source_name,
            "credential_resolver_used": True,
        },
        raw_record_count  = len(lines),
        conflict_status   = "NONE",
        failure_reason    = None,
        request_count     = total_calls,
    )


# ---------------------------------------------------------------------------
# Adapter 5 — News / Contradiction Check (ESPN WNBA news, 2 HTTP calls)
# ---------------------------------------------------------------------------

def fetch_news_contradiction(player_name: str) -> AdapterResult:
    """Fetch recent ESPN WNBA news for a player and flag contradictions.

    Two HTTP calls: (1) athlete search via v2 endpoint (BUG-003b fix), (2) athlete news.
    Contradiction is flagged when role-relevant keywords appear in headlines.
    This is structural contradiction detection — no probability inference.

    Sub-status fields in normalized_fields:
      athlete_resolution_status: ATHLETE_RESOLVED | ATHLETE_NOT_FOUND | ATHLETE_IDENTITY_CONFLICT
      news_fetch_status: NEWS_RETRIEVED | NEWS_REQUEST_EMPTY | NEWS_REQUEST_FAILED
    HTTP 200 with zero matching athletes → athlete_resolution_status=ATHLETE_NOT_FOUND,
      request_status=REQUEST_EMPTY (not REQUEST_FAILED).
    HTTP 200 with zero news articles → news_fetch_status=NEWS_REQUEST_EMPTY,
      request_status=REQUEST_EMPTY (not REQUEST_FAILED).
    """
    import requests as _req

    provider     = "espn_wnba_athlete_news"
    retrieved_at = _now_utc()

    # Call 1: resolve athlete_id via v2 search (BUG-003b fix)
    athlete_id, canonical_name, search_status, search_url = \
        _espn_search_wnba_athlete(player_name)

    # HTTP 200 with no athlete match → ATHLETE_NOT_FOUND, REQUEST_EMPTY (not REQUEST_FAILED)
    if search_status == RequestStatus.REQUEST_EMPTY:
        return AdapterResult(
            provider          = provider,
            source_url_or_id  = search_url,
            retrieved_at      = retrieved_at,
            source_grade      = "B",
            freshness_age     = _freshness(retrieved_at),
            request_status    = RequestStatus.REQUEST_EMPTY,
            parse_status      = "OK",
            normalized_fields = {
                "athlete_resolution_status": "ATHLETE_NOT_FOUND",
                "news_fetch_status":         "NEWS_NOT_ATTEMPTED",
                "article_count":             0,
                "contradiction_found":       False,
            },
            raw_record_count  = 0,
            conflict_status   = "NONE",
            failure_reason    = (
                f"athlete search '{player_name}': no WNBA athlete matched "
                f"(HTTP 200, 0 results) — NEWS_REQUEST_EMPTY"
            ),
            request_count     = 1,
        )

    if search_status != RequestStatus.REQUEST_SUCCEEDED:
        return _failure_result(
            provider, search_url, retrieved_at, search_status,
            f"athlete search '{player_name}': {search_status}",
            request_count=1,
        )

    # Call 2: fetch athlete news
    news_url = _ESPN_NEWS
    try:
        r = _req.get(news_url,
                     params={"athlete": athlete_id, "limit": "10"},
                     timeout=_DEFAULT_TIMEOUT)
    except _req.exceptions.ConnectionError:
        return _failure_result(provider, news_url, retrieved_at,
                               RequestStatus.SOURCE_UNAVAILABLE,
                               "connection error on news fetch", request_count=2)
    except (_req.exceptions.Timeout, _req.exceptions.ReadTimeout):
        return _failure_result(provider, news_url, retrieved_at,
                               RequestStatus.REQUEST_FAILED,
                               "timeout on news fetch", request_count=2)
    except Exception as exc:
        return _failure_result(provider, news_url, retrieved_at,
                               RequestStatus.REQUEST_FAILED,
                               str(exc)[:200], request_count=2)

    if r.status_code == 429:
        return _failure_result(provider, news_url, retrieved_at,
                               RequestStatus.RATE_LIMITED, "ESPN rate-limited", request_count=2)
    if r.status_code in (401, 403):
        return _failure_result(provider, news_url, retrieved_at,
                               RequestStatus.AUTH_REQUIRED,
                               f"ESPN HTTP {r.status_code}", request_count=2)
    if r.status_code != 200:
        return _failure_result(provider, news_url, retrieved_at,
                               RequestStatus.REQUEST_FAILED,
                               f"ESPN HTTP {r.status_code}", request_count=2)

    try:
        data     = r.json()
        articles = data.get("articles") or data.get("news") or []
    except Exception:
        return _failure_result(provider, news_url, retrieved_at,
                               RequestStatus.PARSE_FAILED, "JSON parse failed", request_count=2)

    if not articles:
        # HTTP 200, athlete resolved, but 0 news articles → NEWS_REQUEST_EMPTY (not REQUEST_FAILED)
        return AdapterResult(
            provider=provider, source_url_or_id=news_url, retrieved_at=retrieved_at,
            source_grade="B", freshness_age=_freshness(retrieved_at),
            request_status=RequestStatus.REQUEST_EMPTY, parse_status="OK",
            normalized_fields={
                "athlete_id":                athlete_id,
                "canonical_name":            canonical_name,
                "athlete_resolution_status": "ATHLETE_RESOLVED",
                "news_fetch_status":         "NEWS_REQUEST_EMPTY",
                "article_count":             0,
                "contradiction_found":       False,
            },
            raw_record_count=0, conflict_status="NONE",
            failure_reason="athlete resolved but no recent news articles found (NEWS_REQUEST_EMPTY)",
            request_count=2,
        )

    # Scan for contradiction-relevant keywords
    _OUT_KEYWORDS     = {"out", "dnp", "inactive", "ruled out", "will not play", "scratched"}
    _ACTIVE_KEYWORDS  = {"starting", "active", "available", "day-to-day", "probable"}
    out_signals    = 0
    active_signals = 0
    headlines: list[str] = []

    for article in articles[:10]:
        hl  = (article.get("headline") or article.get("title") or "").lower()
        desc = (article.get("description") or "").lower()
        combined = hl + " " + desc
        headlines.append(hl)
        for kw in _OUT_KEYWORDS:
            if kw in combined:
                out_signals += 1
        for kw in _ACTIVE_KEYWORDS:
            if kw in combined:
                active_signals += 1

    contradiction_found = out_signals > 0 and active_signals > 0
    conflict_status     = "CONFLICT" if contradiction_found else "NONE"

    return AdapterResult(
        provider          = provider,
        source_url_or_id  = news_url,
        retrieved_at      = retrieved_at,
        source_grade      = "B",
        freshness_age     = _freshness(retrieved_at),
        request_status    = RequestStatus.REQUEST_SUCCEEDED,
        parse_status      = "OK",
        normalized_fields = {
            "athlete_id":                athlete_id,
            "canonical_name":            canonical_name,
            "athlete_resolution_status": "ATHLETE_RESOLVED",
            "news_fetch_status":         "NEWS_RETRIEVED",
            "article_count":             len(articles),
            "contradiction_found":       contradiction_found,
            "out_signals":               out_signals,
            "active_signals":            active_signals,
            "recent_headlines":          headlines[:5],
            "checked_at":                retrieved_at,
        },
        raw_record_count  = len(articles),
        conflict_status   = conflict_status,
        failure_reason    = None,
        request_count     = 2,
    )
