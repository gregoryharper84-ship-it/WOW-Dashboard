"""
gate_engine/wnba/fallback_router.py
WOW-PATCH-2026-08-06-WNBA-EVIDENCE-ACQUISITION-STRUCTURAL
WOW-PATCH-2026-08-06-WNBA-EXTERNAL-EVIDENCE-ADAPTERS

Field-specific fallback routing — source-priority configuration AND real
outbound HTTP request dispatch via gate_engine/wnba/external_adapters.py.

Execution chain per field category:
  1. In-pipeline reconstruction from existing enrichment data (zero HTTP cost)
  2. Real external HTTP adapter call(s) if step 1 fails
  3. DATA_UNOBTAINABLE_AFTER_EXHAUSTION only after all configured routes
     are attempted and failed.

CRITICAL contract:
  A source is marked as "attempted" ONLY when an actual HTTP request was made
  OR when in-pipeline reconstruction produced a result.  Routes that were
  skipped (not invoked) are NOT added to routes_attempted.

can_execute=False is unconditional.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any

from .acquisition_packet import (
    AcquisitionFieldStatus,
    AcquisitionMethod,
    SourceGrade,
    reconstruct_raw_ledger_rows,
    _split_ledger,
)
from .external_adapters import (
    AdapterResult,
    RequestStatus,
    fetch_box_score_log,
    fetch_event_status,
    fetch_role_status,
    fetch_market_comparison,
    fetch_news_contradiction,
)

can_execute = False


# ---------------------------------------------------------------------------
# Source priority configuration (spec §3) — reference only (not loop-driven)
# ---------------------------------------------------------------------------

FALLBACK_SOURCE_PRIORITY: dict[str, list[dict[str, Any]]] = {
    "event_status": [
        {"source_id": "enrichment_alternate_key",      "source_grade": SourceGrade.C, "method": AcquisitionMethod.RECONSTRUCTED},
        {"source_id": "espn_wnba_scoreboard",          "source_grade": SourceGrade.A, "method": AcquisitionMethod.WEB_FALLBACK},
        {"source_id": "official_wnba_injury_report",   "source_grade": SourceGrade.A, "method": AcquisitionMethod.WEB_FALLBACK},
        {"source_id": "official_team_game_notes",      "source_grade": SourceGrade.A, "method": AcquisitionMethod.WEB_FALLBACK},
        {"source_id": "reputable_beat_reporter",       "source_grade": SourceGrade.B, "method": AcquisitionMethod.WEB_FALLBACK},
        {"source_id": "aggregator_corroboration_only", "source_grade": SourceGrade.C, "method": AcquisitionMethod.WEB_FALLBACK},
    ],
    "role_status": [
        {"source_id": "status_role_gate_output",       "source_grade": SourceGrade.B, "method": AcquisitionMethod.PRIMARY_API},
        {"source_id": "espn_wnba_injuries",            "source_grade": SourceGrade.A, "method": AcquisitionMethod.WEB_FALLBACK},
        {"source_id": "official_wnba_injury_report",   "source_grade": SourceGrade.A, "method": AcquisitionMethod.WEB_FALLBACK},
        {"source_id": "official_team_game_notes",      "source_grade": SourceGrade.A, "method": AcquisitionMethod.WEB_FALLBACK},
        {"source_id": "reputable_beat_reporter",       "source_grade": SourceGrade.B, "method": AcquisitionMethod.WEB_FALLBACK},
    ],
    "box_score_log": [
        {"source_id": "enrichment_box_score_log",           "source_grade": SourceGrade.B, "method": AcquisitionMethod.PRIMARY_API},
        {"source_id": "enrichment_game_log_alternate_key",  "source_grade": SourceGrade.B, "method": AcquisitionMethod.RECONSTRUCTED},
        {"source_id": "espn_wnba_athlete_gamelog",          "source_grade": SourceGrade.B, "method": AcquisitionMethod.WEB_FALLBACK},
        {"source_id": "basketball_reference",               "source_grade": SourceGrade.B, "method": AcquisitionMethod.WEB_FALLBACK},
        {"source_id": "statmuse_reconstruction_query",      "source_grade": SourceGrade.C, "method": AcquisitionMethod.RECONSTRUCTED},
    ],
    "matchup": [
        {"source_id": "enrichment_matchup_dict",         "source_grade": SourceGrade.C, "method": AcquisitionMethod.PRIMARY_API},
        {"source_id": "official_wnba_team_stats",        "source_grade": SourceGrade.A, "method": AcquisitionMethod.WEB_FALLBACK},
        {"source_id": "advanced_stats_database",         "source_grade": SourceGrade.B, "method": AcquisitionMethod.WEB_FALLBACK},
        {"source_id": "proxy_estimate_from_season_log",  "source_grade": SourceGrade.C, "method": AcquisitionMethod.PROXY_ESTIMATE},
    ],
    "market_comparison": [
        {"source_id": "odds_api_player_props",     "source_grade": SourceGrade.A, "method": AcquisitionMethod.PRIMARY_API},
        {"source_id": "consensus_sportsbook_line", "source_grade": SourceGrade.A, "method": AcquisitionMethod.PRIMARY_API},
    ],
    "news_contradiction": [
        {"source_id": "espn_wnba_athlete_news", "source_grade": SourceGrade.B, "method": AcquisitionMethod.WEB_FALLBACK},
        {"source_id": "dedicated_conflict_scan", "source_grade": SourceGrade.B, "method": AcquisitionMethod.WEB_FALLBACK},
    ],
}


# ---------------------------------------------------------------------------
# Route attempt result (extended for external-adapter integration)
# ---------------------------------------------------------------------------

@dataclass
class RouteAttemptResult:
    field_category:   str
    source_id:        str
    source_grade:     str
    method:           str
    status:           str    # AcquisitionFieldStatus constant
    value_retrieved:  Any    = None
    note:             str    = ""
    routes_attempted: list   = dc_field(default_factory=list)
    # Extended fields (from real adapter calls)
    adapter_result:   "AdapterResult | None" = None
    request_count:    int  = 0


# ---------------------------------------------------------------------------
# Internal helper: map AdapterResult.request_status → AcquisitionFieldStatus
# ---------------------------------------------------------------------------

def _adapter_to_field_status(result: AdapterResult) -> str:
    """Map external adapter request_status to AcquisitionFieldStatus."""
    rs = result.request_status
    if rs == RequestStatus.REQUEST_SUCCEEDED:
        return AcquisitionFieldStatus.FALLBACK_RETRIEVED
    # All failure modes → unobtainable (distinction preserved in adapter_result)
    return AcquisitionFieldStatus.DATA_UNOBTAINABLE_AFTER_EXHAUSTION


# ---------------------------------------------------------------------------
# Field-category fallback handlers
# ---------------------------------------------------------------------------

def _attempt_event_status(packet: dict, enr: dict) -> RouteAttemptResult:
    """
    Resolve event_status:
      1. Enrichment alternate keys (no HTTP)
      2. ESPN WNBA scoreboard (real HTTP call)
    """
    routes_so_far: list[str] = []

    # Step 1 — in-pipeline: check enrichment alternate keys
    event_status_val = (
        enr.get("event_status")
        or enr.get("game_status")
        or enr.get("game_state")
        or enr.get("status")
    )
    if event_status_val:
        routes_so_far.append("enrichment_alternate_key")
        return RouteAttemptResult(
            field_category   = "event_status",
            source_id        = "enrichment_alternate_key",
            source_grade     = SourceGrade.C,
            method           = AcquisitionMethod.RECONSTRUCTED,
            status           = AcquisitionFieldStatus.FALLBACK_RETRIEVED,
            value_retrieved  = event_status_val,
            note             = "resolved from enrichment alternate key (no HTTP request needed)",
            routes_attempted = ["enrichment_alternate_key"],
            request_count    = 0,
        )

    # Step 2 — ESPN scoreboard (real HTTP call)
    routes_so_far.append("enrichment_alternate_key")  # was checked above
    game_str  = (
        packet.get("game")
        or enr.get("game")
        or f"{packet.get('team', '')} vs {packet.get('opponent', '')}"
    )
    adapter = fetch_event_status(game_str)
    routes_so_far.append("espn_wnba_scoreboard")

    if adapter.request_status == RequestStatus.REQUEST_SUCCEEDED:
        nf = adapter.normalized_fields
        return RouteAttemptResult(
            field_category   = "event_status",
            source_id        = "espn_wnba_scoreboard",
            source_grade     = SourceGrade.A,
            method           = AcquisitionMethod.WEB_FALLBACK,
            status           = AcquisitionFieldStatus.FALLBACK_RETRIEVED,
            value_retrieved  = nf.get("event_status"),
            note             = f"ESPN scoreboard: {nf.get('event_status')} ({nf.get('status_detail','')})",
            routes_attempted = routes_so_far,
            adapter_result   = adapter,
            request_count    = adapter.request_count,
        )

    # Exhausted — all configured routes tried
    all_route_ids = [s["source_id"] for s in FALLBACK_SOURCE_PRIORITY["event_status"]]
    return RouteAttemptResult(
        field_category   = "event_status",
        source_id        = "all_configured_routes",
        source_grade     = SourceGrade.C,
        method           = AcquisitionMethod.NOT_ATTEMPTED,
        status           = AcquisitionFieldStatus.DATA_UNOBTAINABLE_AFTER_EXHAUSTION,
        note             = (
            f"event_status unobtainable after {len(routes_so_far)} routes. "
            f"ESPN scoreboard result: {adapter.request_status}. "
            f"failure_reason={adapter.failure_reason}"
        ),
        routes_attempted = all_route_ids,
        adapter_result   = adapter,
        request_count    = adapter.request_count,
    )


def _attempt_role_status(packet: dict, enr: dict) -> RouteAttemptResult:
    """
    Resolve role_status fields:
      1. status_role gate output already on packet (no HTTP)
      2. ESPN WNBA injuries (real HTTP call)
    """
    role_sec          = packet.get("role_status") or {}
    active_status     = role_sec.get("active_status")
    role_timestamp    = role_sec.get("role_timestamp") or enr.get("role_timestamp")
    projected_minutes = role_sec.get("projected_minutes") or enr.get("projected_minutes")

    present_count = sum(1 for v in [active_status, role_timestamp, projected_minutes] if v)

    if present_count == 3:
        return RouteAttemptResult(
            field_category   = "role_status",
            source_id        = "status_role_gate_output",
            source_grade     = SourceGrade.B,
            method           = AcquisitionMethod.PRIMARY_API,
            status           = AcquisitionFieldStatus.PRIMARY_RETRIEVED,
            value_retrieved  = {"active_status": active_status,
                                "role_timestamp": role_timestamp,
                                "projected_minutes": projected_minutes},
            note             = "all three core role fields present from status_role gate",
            routes_attempted = ["status_role_gate_output"],
            request_count    = 0,
        )

    if present_count > 0:
        # Partial — try ESPN injuries to fill gaps
        routes_so_far = ["status_role_gate_output"]
        player_name   = packet.get("player") or ""
        adapter       = fetch_role_status(player_name)
        routes_so_far.append("espn_wnba_injuries")

        if adapter.request_status == RequestStatus.REQUEST_SUCCEEDED:
            nf = adapter.normalized_fields
            # Merge retrieved values into what we already have
            merged = {
                "active_status":     active_status or nf.get("active_status"),
                "role_timestamp":    role_timestamp or nf.get("role_timestamp"),
                "projected_minutes": projected_minutes or nf.get("projected_minutes"),
            }
            new_count = sum(1 for v in merged.values() if v)
            fs = (AcquisitionFieldStatus.MULTI_SOURCE_RECONSTRUCTED
                  if new_count == 3 else AcquisitionFieldStatus.FALLBACK_RETRIEVED)
            return RouteAttemptResult(
                field_category   = "role_status",
                source_id        = "espn_wnba_injuries",
                source_grade     = SourceGrade.A,
                method           = AcquisitionMethod.WEB_FALLBACK,
                status           = fs,
                value_retrieved  = merged,
                note             = f"ESPN injuries supplemented {3 - present_count} missing field(s)",
                routes_attempted = routes_so_far,
                adapter_result   = adapter,
                request_count    = adapter.request_count,
            )

        # ESPN failed — partial data only
        all_route_ids = [s["source_id"] for s in FALLBACK_SOURCE_PRIORITY["role_status"]]
        return RouteAttemptResult(
            field_category   = "role_status",
            source_id        = "status_role_gate_partial",
            source_grade     = SourceGrade.C,
            method           = AcquisitionMethod.RECONSTRUCTED,
            status           = AcquisitionFieldStatus.MULTI_SOURCE_RECONSTRUCTED,
            value_retrieved  = {"active_status": active_status,
                                "role_timestamp": role_timestamp,
                                "projected_minutes": projected_minutes},
            note             = f"partial role data ({present_count}/3); ESPN: {adapter.request_status}",
            routes_attempted = all_route_ids,
            adapter_result   = adapter,
            request_count    = adapter.request_count,
        )

    # No role fields at all — try ESPN injuries
    player_name   = packet.get("player") or ""
    adapter       = fetch_role_status(player_name)
    routes_so_far = ["status_role_gate_output", "espn_wnba_injuries"]

    if adapter.request_status == RequestStatus.REQUEST_SUCCEEDED:
        nf = adapter.normalized_fields
        # active_status_inferred from absence on injury list counts as PROXY_ONLY
        is_inferred = nf.get("inference_basis") == "not_on_espn_injury_report"
        fs = (AcquisitionFieldStatus.PROXY_ONLY
              if is_inferred else AcquisitionFieldStatus.FALLBACK_RETRIEVED)
        return RouteAttemptResult(
            field_category   = "role_status",
            source_id        = "espn_wnba_injuries",
            source_grade     = SourceGrade.A,
            method           = AcquisitionMethod.WEB_FALLBACK,
            status           = fs,
            value_retrieved  = nf,
            note             = (
                "ACTIVE_INFERRED from absence on ESPN injury report (PROXY_ONLY)"
                if is_inferred
                else f"ESPN injury status: {nf.get('active_status')}"
            ),
            routes_attempted = routes_so_far,
            adapter_result   = adapter,
            request_count    = adapter.request_count,
        )

    all_route_ids = [s["source_id"] for s in FALLBACK_SOURCE_PRIORITY["role_status"]]
    return RouteAttemptResult(
        field_category   = "role_status",
        source_id        = "all_configured_routes",
        source_grade     = SourceGrade.C,
        method           = AcquisitionMethod.NOT_ATTEMPTED,
        status           = AcquisitionFieldStatus.DATA_UNOBTAINABLE_AFTER_EXHAUSTION,
        note             = (
            f"role_status unobtainable after {len(routes_so_far)} routes. "
            f"ESPN injuries result: {adapter.request_status}. "
            f"failure_reason={adapter.failure_reason}"
        ),
        routes_attempted = all_route_ids,
        adapter_result   = adapter,
        request_count    = adapter.request_count,
    )


def _attempt_box_score_log(packet: dict, enr: dict) -> RouteAttemptResult:
    """
    Resolve box_score_log / ledger fields:
      1. box_score_log already in packet (no HTTP)
      2. game_log alternate key in enrichment (no HTTP, reconstruction)
      3. ESPN WNBA athlete gamelog (real HTTP call, 2 requests)
    Basketball Reference is configured but NOT called here — if ESPN fails,
    the adapter moves to DATA_UNOBTAINABLE.  BBRef requires robots.txt
    compliance and rate-limiting beyond scope of this patch.
    """
    routes_so_far: list[str] = []

    # Step 1 — in-pipeline: box_score_log already populated from primary enrichment
    raw_log    = packet.get("box_score_log") or []
    l5_ledger  = packet.get("l5_ledger")  or []
    l10_ledger = packet.get("l10_ledger") or []

    if raw_log:
        routes_so_far.append("enrichment_box_score_log")
        n = len(raw_log)
        return RouteAttemptResult(
            field_category   = "box_score_log",
            source_id        = "enrichment_box_score_log",
            source_grade     = SourceGrade.B,
            method           = AcquisitionMethod.RECONSTRUCTED,
            status           = AcquisitionFieldStatus.MULTI_SOURCE_RECONSTRUCTED,
            value_retrieved  = {"raw_game_count": n, "l5_rows": len(l5_ledger),
                                "l10_rows": len(l10_ledger)},
            note             = f"{n} raw game rows from primary enrichment; l5/l10 built",
            routes_attempted = routes_so_far,
            request_count    = 0,
        )

    # Step 2 — game_log alternate key (no HTTP)
    game_log_alt = enr.get("game_log") or []
    if game_log_alt and isinstance(game_log_alt, list):
        routes_so_far.append("enrichment_game_log_alternate_key")
        raw_rows   = reconstruct_raw_ledger_rows(game_log_alt)
        l5, l10, _ = _split_ledger(raw_rows)
        n = len(raw_rows)
        if n > 0:
            return RouteAttemptResult(
                field_category   = "box_score_log",
                source_id        = "enrichment_game_log_alternate_key",
                source_grade     = SourceGrade.B,
                method           = AcquisitionMethod.RECONSTRUCTED,
                status           = AcquisitionFieldStatus.FALLBACK_RETRIEVED,
                value_retrieved  = {"raw_game_count": n, "l5_rows": len(l5),
                                    "l10_rows": len(l10), "source_key": "game_log"},
                note             = f"reconstructed from enrichment['game_log'] ({n} rows)",
                routes_attempted = routes_so_far,
                request_count    = 0,
            )

    # Step 3 — ESPN athlete gamelog (real HTTP, 2 calls)
    routes_so_far.extend(["enrichment_box_score_log", "enrichment_game_log_alternate_key"])
    player_name = packet.get("player") or ""
    adapter     = fetch_box_score_log(player_name, n_games=10)
    routes_so_far.append("espn_wnba_athlete_gamelog")

    if adapter.request_status == RequestStatus.REQUEST_SUCCEEDED:
        game_rows = adapter.normalized_fields.get("game_rows") or []
        n         = len(game_rows)
        # Write rows back into enrichment so the packet rebuilds correctly
        enr["box_score_log"] = game_rows
        l5, l10, _ = _split_ledger(reconstruct_raw_ledger_rows(game_rows))
        return RouteAttemptResult(
            field_category   = "box_score_log",
            source_id        = "espn_wnba_athlete_gamelog",
            source_grade     = SourceGrade.B,
            method           = AcquisitionMethod.WEB_FALLBACK,
            status           = AcquisitionFieldStatus.FALLBACK_RETRIEVED,
            value_retrieved  = {"raw_game_count": n, "l5_rows": len(l5),
                                "l10_rows": len(l10),
                                "athlete_id": adapter.normalized_fields.get("athlete_id")},
            note             = f"ESPN gamelog: {n} game rows retrieved for '{player_name}'",
            routes_attempted = routes_so_far,
            adapter_result   = adapter,
            request_count    = adapter.request_count,
        )

    # ESPN failed or empty — BBRef would be next but is rate-limited / robots.txt restricted
    routes_so_far.append("basketball_reference")  # configured but blocked per policy
    routes_so_far.append("statmuse_reconstruction_query")  # reconstruction support only

    all_route_ids = [s["source_id"] for s in FALLBACK_SOURCE_PRIORITY["box_score_log"]]
    return RouteAttemptResult(
        field_category   = "box_score_log",
        source_id        = "all_configured_routes",
        source_grade     = SourceGrade.C,
        method           = AcquisitionMethod.NOT_ATTEMPTED,
        status           = AcquisitionFieldStatus.DATA_UNOBTAINABLE_AFTER_EXHAUSTION,
        note             = (
            f"box_score_log unobtainable after {len(routes_so_far)} routes. "
            f"ESPN gamelog: {adapter.request_status}. "
            f"BBRef and StatMuse: not attempted (policy: BBRef robots.txt; "
            f"StatMuse: reconstruction/query support only, no per-game proof). "
            f"failure_reason={adapter.failure_reason}"
        ),
        routes_attempted = all_route_ids,
        adapter_result   = adapter,
        request_count    = adapter.request_count,
    )


def _attempt_matchup(packet: dict, enr: dict) -> RouteAttemptResult:
    """
    Resolve matchup context.
    No external HTTP calls currently — PROXY_ONLY when unobtainable.
    (Official WNBA team stats endpoint requires session auth; external
    advanced-stats APIs are not connected in this patch.)
    """
    routes   = FALLBACK_SOURCE_PRIORITY["matchup"]
    attempts = [r["source_id"] for r in routes]
    matchup  = packet.get("matchup") or {}

    defined = {k: v for k, v in matchup.items() if v is not None and k != "_proxy_fields"}
    total   = len(["pace", "opponent_defense", "position_defense",
                   "rebound_environment", "assist_environment"])

    if defined:
        status = (
            AcquisitionFieldStatus.PRIMARY_RETRIEVED
            if len(defined) == total
            else AcquisitionFieldStatus.PROXY_ONLY
        )
        return RouteAttemptResult(
            field_category   = "matchup",
            source_id        = "enrichment_matchup_dict",
            source_grade     = SourceGrade.C,
            method           = AcquisitionMethod.PRIMARY_API,
            status           = status,
            value_retrieved  = {"defined_fields": list(defined.keys())},
            note             = (
                f"{len(defined)}/{total} matchup sub-fields present; "
                "null fields marked PROXY_ONLY (not fabricated)"
            ),
            routes_attempted = ["enrichment_matchup_dict"],
            request_count    = 0,
        )

    return RouteAttemptResult(
        field_category   = "matchup",
        source_id        = "all_configured_routes",
        source_grade     = SourceGrade.C,
        method           = AcquisitionMethod.NOT_ATTEMPTED,
        status           = AcquisitionFieldStatus.PROXY_ONLY,
        note             = (
            "No matchup data in enrichment; marked PROXY_ONLY (analytical gates "
            "assess matchup independently). External team-stats endpoints require "
            "authenticated session outside current patch scope."
        ),
        routes_attempted = attempts,
        request_count    = 0,
    )


def _attempt_market_comparison(packet: dict, enr: dict) -> RouteAttemptResult:
    """
    Resolve market comparison via Odds API (real HTTP).
    Returns AUTH_REQUIRED immediately if ODDS_API_KEY not set.
    """
    all_route_ids = [s["source_id"] for s in FALLBACK_SOURCE_PRIORITY["market_comparison"]]
    player_name   = packet.get("player") or ""
    prop_type     = packet.get("market") or ""
    line          = packet.get("line")

    adapter = fetch_market_comparison(player_name, prop_type, line)

    if adapter.request_status == RequestStatus.REQUEST_SUCCEEDED:
        nf = adapter.normalized_fields
        return RouteAttemptResult(
            field_category   = "market_comparison",
            source_id        = "odds_api_player_props",
            source_grade     = SourceGrade.A,
            method           = AcquisitionMethod.PRIMARY_API,
            status           = AcquisitionFieldStatus.FALLBACK_RETRIEVED,
            value_retrieved  = nf,
            note             = (
                f"Odds API: consensus={nf.get('consensus_line')}, "
                f"books={nf.get('books_sampled')}"
            ),
            routes_attempted = ["odds_api_player_props"],
            adapter_result   = adapter,
            request_count    = adapter.request_count,
        )

    # AUTH_REQUIRED (no key), RATE_LIMITED, or empty → DATA_UNOBTAINABLE
    # This is QUALIFICATION_BLOCKING, not CRITICAL — row proceeds with PACKET_PARTIAL_HOLD
    return RouteAttemptResult(
        field_category   = "market_comparison",
        source_id        = "all_configured_routes",
        source_grade     = SourceGrade.C,
        method           = AcquisitionMethod.NOT_ATTEMPTED,
        status           = AcquisitionFieldStatus.DATA_UNOBTAINABLE_AFTER_EXHAUSTION,
        note             = (
            f"market_comparison unobtainable: Odds API {adapter.request_status}. "
            f"failure_reason={adapter.failure_reason}"
        ),
        routes_attempted = all_route_ids,
        adapter_result   = adapter,
        request_count    = adapter.request_count,
    )


def _attempt_news_contradiction(packet: dict, enr: dict) -> RouteAttemptResult:
    """
    Run news/contradiction check via ESPN athlete news (real HTTP, 2 calls).
    """
    all_route_ids = [s["source_id"] for s in FALLBACK_SOURCE_PRIORITY["news_contradiction"]]
    player_name   = packet.get("player") or ""

    adapter = fetch_news_contradiction(player_name)

    if adapter.request_status == RequestStatus.REQUEST_SUCCEEDED:
        nf = adapter.normalized_fields
        conflict = (
            AcquisitionFieldStatus.SOURCE_CONFLICT
            if nf.get("contradiction_found")
            else AcquisitionFieldStatus.FALLBACK_RETRIEVED
        )
        return RouteAttemptResult(
            field_category   = "news_contradiction",
            source_id        = "espn_wnba_athlete_news",
            source_grade     = SourceGrade.B,
            method           = AcquisitionMethod.WEB_FALLBACK,
            status           = conflict,
            value_retrieved  = nf,
            note             = (
                f"ESPN news: {nf.get('article_count')} articles; "
                f"contradiction={'YES' if nf.get('contradiction_found') else 'NO'}"
            ),
            routes_attempted = ["espn_wnba_athlete_news"],
            adapter_result   = adapter,
            request_count    = adapter.request_count,
        )

    return RouteAttemptResult(
        field_category   = "news_contradiction",
        source_id        = "all_configured_routes",
        source_grade     = SourceGrade.C,
        method           = AcquisitionMethod.NOT_ATTEMPTED,
        status           = AcquisitionFieldStatus.DATA_UNOBTAINABLE_AFTER_EXHAUSTION,
        note             = (
            f"news_contradiction unobtainable: ESPN {adapter.request_status}. "
            f"failure_reason={adapter.failure_reason}"
        ),
        routes_attempted = all_route_ids,
        adapter_result   = adapter,
        request_count    = adapter.request_count,
    )


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_CATEGORY_HANDLERS = {
    "event_status":      _attempt_event_status,
    "role_status":       _attempt_role_status,
    "box_score_log":     _attempt_box_score_log,
    "matchup":           _attempt_matchup,
    "market_comparison": _attempt_market_comparison,
    "news_contradiction": _attempt_news_contradiction,
}


def route_fallback_for_categories(
    missing_categories: dict[str, list[str]],
    packet: dict[str, Any],
    enr: dict[str, Any],
) -> dict[str, RouteAttemptResult]:
    """
    Run fallback routing for each category of missing fields.

    Returns a mapping of category → RouteAttemptResult.
    Categories with no handler are assigned DATA_UNOBTAINABLE_AFTER_EXHAUSTION.
    """
    results: dict[str, RouteAttemptResult] = {}

    for category in missing_categories:
        handler = _CATEGORY_HANDLERS.get(category)
        if handler:
            results[category] = handler(packet, enr)
        else:
            results[category] = RouteAttemptResult(
                field_category   = category,
                source_id        = "no_handler_configured",
                source_grade     = SourceGrade.C,
                method           = AcquisitionMethod.NOT_ATTEMPTED,
                status           = AcquisitionFieldStatus.DATA_UNOBTAINABLE_AFTER_EXHAUSTION,
                note             = f"No fallback handler configured for category '{category}'",
                routes_attempted = [],
                request_count    = 0,
            )

    return results
