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

from typing import Any

from services import odds_api, status as status_service


# Conservative, explicit prop_type -> odds_api market-suffix mapping.
# Only unambiguous, commonly-seen WOW prop types are listed. Anything not
# listed here is left unmapped on purpose — the row gets no auto-filled
# market line, identical to today's behavior.
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
    "passing yards": "pass_yds",
    "passing tds": "pass_tds",
    "rushing yards": "rush_yds",
    "receiving yards": "reception_yds",
    "receptions": "receptions",
}

_PITCHER_PROP_TYPES = {"pitcher strikeouts"}


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

    for row in rows:
        player = row.get("player")
        prop_type = row.get("prop_type")
        sport = (row.get("sport") or "").upper()
        direction = (row.get("direction") or "").upper()
        if not player or not prop_type:
            continue

        rid = row.get("row_id", "")
        key = f"{player.lower()}:{prop_type.lower()}"
        # Preserve whichever key the caller already used, matching
        # pipeline._get_enrichment's lookup order (row_id, then key).
        write_key = rid if rid and rid in enrichment else key
        entry = dict(enrichment.get(write_key) or {})

        # --- Market lines ---
        market_key = _market_key_for(sport, prop_type)
        if market_key:
            entries = market_indexes.get(sport, {}).get((player.lower(), market_key), [])
            side = "MORE" if direction in ("MORE", "OVER") else (
                "LESS" if direction in ("LESS", "UNDER") else None
            )
            matching = _lines_for_side(entries, side)
            lines = [e["line"] for e in matching if e.get("line") is not None]
            if lines:
                if entry.get("sportsbook_line") is None:
                    entry["sportsbook_line"] = lines[0]
                if entry.get("best_available") is None:
                    # MORE/OVER: lowest line is easiest to clear.
                    # LESS/UNDER: highest line is easiest to stay under.
                    entry["best_available"] = min(lines) if side == "MORE" else max(lines)
                if entry.get("consensus_line") is None:
                    entry["consensus_line"] = round(sum(lines) / len(lines), 3)

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

        if entry:
            enrichment[write_key] = entry

    return enrichment, source_status
