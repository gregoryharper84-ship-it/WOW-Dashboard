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

CRITICAL contract — routes_attempted audit semantics:
  routes_attempted / fallback_routes_attempted may contain ONLY sources for
  which an actual outbound HTTP request was initiated (request_made=True).

  Sources that were NOT called are classified into:
    routes_skipped_by_policy  — blocked by robots.txt / ToS (e.g. BBRef)
    routes_not_implemented    — configured in priority table but no handler
                                reaches them yet in this patch scope
    routes_unavailable        — adapter called; source was unreachable
    routes_auth_required      — adapter called (or skipped); credentials absent

  Configured route presence in FALLBACK_SOURCE_PRIORITY by itself NEVER
  implies an attempted request.

can_execute=False is unconditional.
"""
from __future__ import annotations

import datetime
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
# Enforced category dispatch order
# ---------------------------------------------------------------------------
# box_score_log MUST be dispatched before role_status so that
# _attempt_box_score_log can write game rows to enr["box_score_log"] before
# _attempt_role_status reads them for box-score reconstruction.
# This order is mandatory — do not reorder without updating _attempt_role_status.
CATEGORY_DISPATCH_ORDER: list[str] = [
    "box_score_log",        # 1. must run first — writes enr["box_score_log"]
    "event_status",         # 2. no dependency on other categories
    "role_status",          # 3. uses enr["box_score_log"] from step 1
    "matchup",              # 4.
    "market_comparison",    # 5.
    "news_contradiction",   # 6.
]


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
        {"source_id": "status_role_gate_output",            "source_grade": SourceGrade.B, "method": AcquisitionMethod.PRIMARY_API},
        {"source_id": "espn_wnba_injuries",                 "source_grade": SourceGrade.A, "method": AcquisitionMethod.WEB_FALLBACK},
        {"source_id": "reconstructed_from_box_scores",      "source_grade": SourceGrade.B, "method": AcquisitionMethod.RECONSTRUCTED},
        {"source_id": "official_wnba_injury_report",        "source_grade": SourceGrade.A, "method": AcquisitionMethod.WEB_FALLBACK},
        {"source_id": "official_team_game_notes",           "source_grade": SourceGrade.A, "method": AcquisitionMethod.WEB_FALLBACK},
        {"source_id": "reputable_beat_reporter",            "source_grade": SourceGrade.B, "method": AcquisitionMethod.WEB_FALLBACK},
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
    field_category:            str
    source_id:                 str
    source_grade:              str
    method:                    str
    status:                    str    # AcquisitionFieldStatus constant
    value_retrieved:           Any    = None
    note:                      str    = ""
    # INVARIANT: routes_attempted contains ONLY provider names where an actual
    # outbound HTTP request was initiated (request_made=True).  In-pipeline
    # reconstruction and not-called sources must NOT appear here.
    routes_attempted:          list   = dc_field(default_factory=list)
    # Extended fields (from real adapter calls)
    adapter_result:            "AdapterResult | None" = None
    request_count:             int    = 0
    # Normalized per-route records (WOW-PATCH audit-semantics correction)
    route_records:             list   = dc_field(default_factory=list)
    routes_skipped_by_policy:  list   = dc_field(default_factory=list)
    routes_not_implemented:    list   = dc_field(default_factory=list)


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
# Audit-semantics helpers — normalized route records and not-called source lists
# (WOW-PATCH audit-semantics correction)
# ---------------------------------------------------------------------------

# Sources in FALLBACK_SOURCE_PRIORITY that are configured but NEVER called by
# any handler.  Grouped by category so each handler emits the correct audit records.
_NOT_IMPLEMENTED_BY_CATEGORY: "dict[str, list[tuple[str, str]]]" = {
    "event_status": [
        ("official_wnba_injury_report",
         "Configured but not implemented in current patch scope."),
        ("official_team_game_notes",
         "Configured but not implemented in current patch scope."),
        ("reputable_beat_reporter",
         "Configured but not implemented in current patch scope."),
        ("aggregator_corroboration_only",
         "Configured but not implemented in current patch scope."),
    ],
    "role_status": [
        ("official_wnba_injury_report",
         "Configured but not implemented in current patch scope."),
        ("official_team_game_notes",
         "Configured but not implemented in current patch scope."),
        ("reputable_beat_reporter",
         "Configured but not implemented in current patch scope."),
    ],
    "box_score_log": [
        ("statmuse_reconstruction_query",
         "Reconstruction support only — no per-game proof source; not implemented."),
    ],
    "matchup": [
        ("official_wnba_team_stats",
         "Requires authenticated session; not implemented in current patch scope."),
        ("advanced_stats_database",
         "Configured but not implemented in current patch scope."),
        ("proxy_estimate_from_season_log",
         "Configured but not implemented in current patch scope."),
    ],
    "market_comparison": [
        ("consensus_sportsbook_line",
         "Configured but not implemented; Odds API is the only active source."),
    ],
    "news_contradiction": [
        ("dedicated_conflict_scan",
         "Configured but not implemented in current patch scope."),
    ],
}

# Sources skipped for policy reasons (robots.txt, ToS) — never called.
_POLICY_SKIPPED_BY_CATEGORY: "dict[str, list[tuple[str, str]]]" = {
    "box_score_log": [
        ("basketball_reference",
         "Skipped: robots.txt / ToS policy — not callable from Replit host."),
    ],
}


def _make_route_record(
    provider: str,
    *,
    request_made: bool,
    method: str = "HTTP_GET",
    request_status: str,
    retrieved_at: "str | None" = None,
    failure_reason: "str | None" = None,
    skip_category: "str | None" = None,
) -> dict:
    """Build a normalized route-level record for the acquisition audit.

    request_made=True  → actual outbound HTTP request was initiated.
    request_made=False → source was not called (policy, not-implemented,
                         auth-absent, or in-pipeline reconstruction).
    """
    record: dict = {
        "provider":       provider,
        "request_made":   request_made,
        "method":         method,
        "request_status": request_status,
        "retrieved_at":   retrieved_at,
        "failure_reason": failure_reason,
    }
    if not request_made and skip_category:
        record["skip_category"] = skip_category
    return record


def _adapter_route_record(ar: "AdapterResult") -> dict:
    """Build a normalized record from a completed AdapterResult.

    request_made reflects whether the adapter actually issued an HTTP request
    (request_count > 0).  AUTH_REQUIRED with no key skips the request entirely
    (request_count=0, request_made=False).
    """
    no_request = ar.request_count == 0
    return _make_route_record(
        ar.provider,
        request_made   = not no_request,
        method         = "HTTP_GET",
        request_status = ar.request_status,
        retrieved_at   = ar.retrieved_at,
        failure_reason = ar.failure_reason,
        skip_category  = ("AUTH_REQUIRED" if no_request and
                           ar.request_status == RequestStatus.AUTH_REQUIRED
                           else None),
    )


def _not_impl_records_for(category: str) -> "tuple[list[dict], list[str]]":
    """Return (route_records, provider_names) for NOT_IMPLEMENTED sources."""
    entries = _NOT_IMPLEMENTED_BY_CATEGORY.get(category, [])
    records = [
        _make_route_record(
            provider, request_made=False, method="NOT_ATTEMPTED",
            request_status="NOT_ATTEMPTED",
            skip_category="NOT_IMPLEMENTED",
            failure_reason=reason,
        )
        for provider, reason in entries
    ]
    return records, [e[0] for e in entries]


def _policy_skipped_records_for(category: str) -> "tuple[list[dict], list[str]]":
    """Return (route_records, provider_names) for SKIPPED_BY_POLICY sources."""
    entries = _POLICY_SKIPPED_BY_CATEGORY.get(category, [])
    records = [
        _make_route_record(
            provider, request_made=False, method="NOT_ATTEMPTED",
            request_status="NOT_ATTEMPTED",
            skip_category="SKIPPED_BY_POLICY",
            failure_reason=reason,
        )
        for provider, reason in entries
    ]
    return records, [e[0] for e in entries]


# ---------------------------------------------------------------------------
# Box-score role reconstruction helper
# ---------------------------------------------------------------------------

def _reconstruct_role_from_box_scores(
    game_rows: list[dict],
    as_of: "str | None" = None,
) -> "dict | None":
    """
    Derive a canonical role_status estimate from recent completed game rows.

    This function implements the box-score reconstruction tier of the role_status
    fallback ladder (Step 3 in _attempt_role_status).  It is the evidence-based
    alternative to ACTIVE_INFERRED: rather than guessing from an ESPN absence, it
    uses the player's actual appearance in completed WNBA games as direct evidence
    that they are active and participating.

    Algorithm
    ---------
    1. Filter rows to those with minutes > 0 (player actually took the court).
       ESPN gamelog rows have no game_status field; non-zero minutes is the proxy
       for a completed participation record.
    2. Require ≥ 3 qualifying rows.  Fewer rows is not sufficient evidence.
    3. Weighted mean of L5 minutes (weights [5,4,3,2,1][:n]).
    4. starter_rate from last 5 rows (ESPN sets starter=None; result is
       "PROJECTED_RESERVE" unless a future adapter supplies starter flags).
    5. role_timestamp = as_of (time of assessment, NOT game date — per document
       guidance: "role_timestamp must represent when the role assessment was
       created or refreshed").
    6. inference_basis = None — CRITICAL: this is NOT an inferred-absence result;
       it is backed by direct observation of completed game participation.  The
       None value allows the write-back rules in evidence_acquisition.run() to
       accept role_timestamp as a canonical observed timestamp.

    Returns None when insufficient qualifying data is available so the caller
    can fall through to DATA_UNOBTAINABLE_AFTER_EXHAUSTION.

    Values returned
    ---------------
    active_status     "ACTIVE" — in _CANONICAL_ACTIVE_STATUSES; passes
                      _validate_critical_field_value
    projected_minutes weighted mean float ≥ 0; passes projected_minutes check
    role_timestamp    as_of ISO string; passes role_timestamp non-blank check
    starter_status    "PROJECTED_STARTER" or "PROJECTED_RESERVE"
    source            "reconstructed_from_recent_box_scores"
    confidence        "MEDIUM"
    inference_basis   None (MUST remain None — canonical observation)
    games_used        int
    minutes_sample    list[float]
    """
    if not game_rows:
        return None

    # Filter: rows where the player actually played (minutes > 0)
    played: list[dict] = []
    for row in game_rows:
        try:
            mins = row.get("minutes")
            if mins is not None and float(mins) > 0:
                played.append(row)
        except (TypeError, ValueError):
            pass

    if len(played) < 3:
        # Fewer than 3 qualifying game appearances — not sufficient evidence
        return None

    # Weighted mean of L5 minutes (most-recent game gets weight 5)
    recent5 = played[:5]
    minutes_list = []
    for row in recent5:
        try:
            m = row.get("minutes")
            if m is not None:
                minutes_list.append(float(m))
        except (TypeError, ValueError):
            pass

    if not minutes_list:
        return None

    weights      = [5, 4, 3, 2, 1][: len(minutes_list)]
    proj_minutes = sum(m * w for m, w in zip(minutes_list, weights)) / sum(weights)

    # Starter rate (ESPN gamelog sets starter=None; treat None as False)
    starter_rate   = sum(1 for r in recent5 if r.get("starter")) / len(recent5)
    starter_status = (
        "PROJECTED_STARTER" if starter_rate >= 0.6 else "PROJECTED_RESERVE"
    )

    role_ts = as_of or datetime.datetime.now(datetime.timezone.utc).isoformat()

    return {
        "active_status":     "ACTIVE",           # canonical; in _CANONICAL_ACTIVE_STATUSES
        "projected_minutes": round(proj_minutes, 1),
        "role_timestamp":    role_ts,             # time of assessment, not game date
        "starter_status":    starter_status,
        "source":            "reconstructed_from_recent_box_scores",
        "confidence":        "MEDIUM",
        "inference_basis":   None,               # MUST be None — direct evidence, not inference
        "games_used":        len(recent5),
        "minutes_sample":    minutes_list,
    }


# ---------------------------------------------------------------------------
# Field-category fallback handlers
# ---------------------------------------------------------------------------

def _attempt_event_status(packet: dict, enr: dict) -> RouteAttemptResult:
    """
    Resolve event_status:
      1. Enrichment alternate keys (no HTTP)
      2. ESPN WNBA scoreboard (real HTTP call — undocumented web JSON endpoint;
         not a guaranteed public developer API and may change without notice)

    Not-implemented sources (official_wnba_injury_report, official_team_game_notes,
    reputable_beat_reporter, aggregator_corroboration_only) appear in
    routes_not_implemented in the audit output; they are NEVER called here.
    """
    ni_recs, ni_names = _not_impl_records_for("event_status")

    # Step 1 — in-pipeline: check enrichment alternate keys (no HTTP request)
    event_status_val = (
        enr.get("event_status")
        or enr.get("game_status")
        or enr.get("game_state")
        or enr.get("status")
    )
    if event_status_val:
        return RouteAttemptResult(
            field_category         = "event_status",
            source_id              = "enrichment_alternate_key",
            source_grade           = SourceGrade.C,
            method                 = AcquisitionMethod.RECONSTRUCTED,
            status                 = AcquisitionFieldStatus.FALLBACK_RETRIEVED,
            value_retrieved        = event_status_val,
            note                   = "resolved from enrichment alternate key (no HTTP request)",
            routes_attempted       = [],          # no HTTP request made
            request_count          = 0,
            route_records          = list(ni_recs),
            routes_not_implemented = ni_names,
        )

    # Step 2 — ESPN WNBA scoreboard (real HTTP call)
    # Extract slate_date from enrichment/packet so the scoreboard query targets
    # the correct local date rather than defaulting to UTC-now (which can miss
    # games scheduled for "tonight" when UTC has already rolled to the next day).
    _raw_slate_date = enr.get("slate_date") or packet.get("slate_date")
    date_str_for_scoreboard: "str | None" = None
    if _raw_slate_date:
        _cleaned = str(_raw_slate_date).replace("-", "")
        if len(_cleaned) >= 8:
            date_str_for_scoreboard = _cleaned[:8]

    game_str = (
        packet.get("game")
        or enr.get("game")
        or f"{packet.get('team', '')} vs {packet.get('opponent', '')}"
    )
    adapter     = fetch_event_status(game_str, date_str=date_str_for_scoreboard)
    adapter_rec = _adapter_route_record(adapter)
    http_attempted = [adapter.provider] if adapter.request_count > 0 else []
    all_recs = [adapter_rec] + list(ni_recs)

    if adapter.request_status == RequestStatus.REQUEST_SUCCEEDED:
        nf = adapter.normalized_fields
        return RouteAttemptResult(
            field_category         = "event_status",
            source_id              = "espn_wnba_scoreboard",
            source_grade           = SourceGrade.A,
            method                 = AcquisitionMethod.WEB_FALLBACK,
            status                 = AcquisitionFieldStatus.FALLBACK_RETRIEVED,
            value_retrieved        = nf.get("event_status"),
            note                   = (
                f"ESPN scoreboard: {nf.get('event_status')} "
                f"({nf.get('status_detail', '')}) "
                f"match_confidence={nf.get('match_confidence')} "
                f"event_id={nf.get('event_id', '')}"
            ),
            routes_attempted       = http_attempted,
            adapter_result         = adapter,
            request_count          = adapter.request_count,
            route_records          = all_recs,
            routes_not_implemented = ni_names,
        )

    # Exhausted — ESPN was the only HTTP route; not-implemented sources not called
    return RouteAttemptResult(
        field_category         = "event_status",
        source_id              = "all_configured_routes",
        source_grade           = SourceGrade.C,
        method                 = AcquisitionMethod.NOT_ATTEMPTED,
        status                 = AcquisitionFieldStatus.DATA_UNOBTAINABLE_AFTER_EXHAUSTION,
        note                   = (
            f"event_status unobtainable after 1 HTTP route. "
            f"ESPN scoreboard: {adapter.request_status}. "
            f"failure_reason={adapter.failure_reason}. "
            f"game_str='{game_str}' date_str={date_str_for_scoreboard}"
        ),
        routes_attempted       = http_attempted,
        adapter_result         = adapter,
        request_count          = adapter.request_count,
        route_records          = all_recs,
        routes_not_implemented = ni_names,
    )


def _attempt_role_status(packet: dict, enr: dict) -> RouteAttemptResult:
    """
    Resolve role_status fields:
      1. status_role gate output already on packet (no HTTP)
      2. ESPN WNBA injuries (real HTTP call — undocumented web JSON endpoint;
         not a guaranteed public developer API and may change without notice)

    Not-implemented sources (official_wnba_injury_report, official_team_game_notes,
    reputable_beat_reporter) appear in routes_not_implemented; never called here.
    """
    role_sec          = packet.get("role_status") or {}
    active_status     = role_sec.get("active_status")
    role_timestamp    = role_sec.get("role_timestamp") or enr.get("role_timestamp")
    projected_minutes = role_sec.get("projected_minutes") or enr.get("projected_minutes")

    present_count = sum(1 for v in [active_status, role_timestamp, projected_minutes] if v)
    ni_recs, ni_names = _not_impl_records_for("role_status")

    if present_count == 3:
        return RouteAttemptResult(
            field_category         = "role_status",
            source_id              = "status_role_gate_output",
            source_grade           = SourceGrade.B,
            method                 = AcquisitionMethod.PRIMARY_API,
            status                 = AcquisitionFieldStatus.PRIMARY_RETRIEVED,
            value_retrieved        = {"active_status": active_status,
                                      "role_timestamp": role_timestamp,
                                      "projected_minutes": projected_minutes},
            note                   = "all three core role fields present from status_role gate",
            routes_attempted       = [],      # no HTTP request
            request_count          = 0,
            route_records          = list(ni_recs),
            routes_not_implemented = ni_names,
        )

    if present_count > 0:
        # Partial — try ESPN injuries to fill gaps (real HTTP)
        player_name   = packet.get("player") or ""
        adapter       = fetch_role_status(player_name)
        adapter_rec   = _adapter_route_record(adapter)
        http_attempted = [adapter.provider] if adapter.request_count > 0 else []

        if adapter.request_status == RequestStatus.REQUEST_SUCCEEDED:
            nf = adapter.normalized_fields
            merged = {
                "active_status":     active_status or nf.get("active_status"),
                "role_timestamp":    role_timestamp or nf.get("role_timestamp"),
                "projected_minutes": projected_minutes or nf.get("projected_minutes"),
            }
            new_count = sum(1 for v in merged.values() if v)
            fs = (AcquisitionFieldStatus.MULTI_SOURCE_RECONSTRUCTED
                  if new_count == 3 else AcquisitionFieldStatus.FALLBACK_RETRIEVED)
            return RouteAttemptResult(
                field_category         = "role_status",
                source_id              = "espn_wnba_injuries",
                source_grade           = SourceGrade.A,
                method                 = AcquisitionMethod.WEB_FALLBACK,
                status                 = fs,
                value_retrieved        = merged,
                note                   = f"ESPN injuries supplemented {3 - present_count} missing field(s)",
                routes_attempted       = http_attempted,
                adapter_result         = adapter,
                request_count          = adapter.request_count,
                route_records          = [adapter_rec] + list(ni_recs),
                routes_not_implemented = ni_names,
            )

        # ESPN failed — return partial in-pipeline data
        return RouteAttemptResult(
            field_category         = "role_status",
            source_id              = "status_role_gate_partial",
            source_grade           = SourceGrade.C,
            method                 = AcquisitionMethod.RECONSTRUCTED,
            status                 = AcquisitionFieldStatus.MULTI_SOURCE_RECONSTRUCTED,
            value_retrieved        = {"active_status": active_status,
                                      "role_timestamp": role_timestamp,
                                      "projected_minutes": projected_minutes},
            note                   = f"partial role data ({present_count}/3); ESPN: {adapter.request_status}",
            routes_attempted       = http_attempted,
            adapter_result         = adapter,
            request_count          = adapter.request_count,
            route_records          = [adapter_rec] + list(ni_recs),
            routes_not_implemented = ni_names,
        )

    # No role fields at all.
    # ── Step 2a: box-score reconstruction (zero HTTP cost) ───────────────────
    # When box_score_log is already present in the enrichment (written by
    # _attempt_box_score_log which runs BEFORE _attempt_role_status per
    # CATEGORY_DISPATCH_ORDER, or from primary acquisition), try reconstruction
    # BEFORE making any external HTTP call.  This avoids a live ESPN request
    # when the data we need is already on hand.
    as_of_ts = enr.get("_acquisition_as_of") or datetime.datetime.now(
        datetime.timezone.utc
    ).isoformat()
    pre_game_rows: list = (
        enr.get("box_score_log")      # written by _attempt_box_score_log or GPT-supplied
        or packet.get("box_score_log")  # from primary acquisition
        or []
    )
    pre_reconstructed = _reconstruct_role_from_box_scores(pre_game_rows, as_of=as_of_ts)

    if pre_reconstructed:
        # Sufficient box-score evidence found — return canonical reconstruction
        # without making any ESPN HTTP request.
        bsr_record = _make_route_record(
            "reconstructed_from_box_scores",
            request_made   = False,
            method         = "RECONSTRUCTION",
            request_status = "RECONSTRUCTION_SUCCEEDED",
        )
        return RouteAttemptResult(
            field_category         = "role_status",
            source_id              = "reconstructed_from_box_scores",
            source_grade           = SourceGrade.B,
            method                 = AcquisitionMethod.RECONSTRUCTED,
            status                 = AcquisitionFieldStatus.MULTI_SOURCE_RECONSTRUCTED,
            value_retrieved        = pre_reconstructed,
            note                   = (
                f"box-score reconstruction from {pre_reconstructed['games_used']} "
                f"completed games (pre-ESPN path; box_score_log already in enrichment). "
                f"projected_minutes={pre_reconstructed['projected_minutes']}, "
                f"starter_status={pre_reconstructed['starter_status']}, "
                f"confidence={pre_reconstructed['confidence']}"
            ),
            routes_attempted       = [],    # no HTTP request made
            request_count          = 0,
            route_records          = [bsr_record] + list(ni_recs),
            routes_not_implemented = ni_names,
        )

    # ── Step 2b: ESPN WNBA injuries (real HTTP) ───────────────────────────────
    # Box-score reconstruction was insufficient (< 3 qualifying rows or empty).
    # Fall back to ESPN injuries endpoint.
    player_name   = packet.get("player") or ""
    adapter       = fetch_role_status(player_name)
    adapter_rec   = _adapter_route_record(adapter)
    http_attempted = [adapter.provider] if adapter.request_count > 0 else []

    if adapter.request_status == RequestStatus.REQUEST_SUCCEEDED:
        nf = adapter.normalized_fields
        is_inferred = nf.get("inference_basis") == "not_on_espn_injury_report"

        if not is_inferred:
            # ESPN returned a genuine injury status (ACTIVE, OUT, QUESTIONABLE, …)
            return RouteAttemptResult(
                field_category         = "role_status",
                source_id              = "espn_wnba_injuries",
                source_grade           = SourceGrade.A,
                method                 = AcquisitionMethod.WEB_FALLBACK,
                status                 = AcquisitionFieldStatus.FALLBACK_RETRIEVED,
                value_retrieved        = nf,
                note                   = f"ESPN injury status: {nf.get('active_status')}",
                routes_attempted       = http_attempted,
                adapter_result         = adapter,
                request_count          = adapter.request_count,
                route_records          = [adapter_rec] + list(ni_recs),
                routes_not_implemented = ni_names,
            )

        # ── Step 3: box-score reconstruction (post-ESPN PROXY_ONLY) ──────────
        # ESPN returned PROXY_ONLY (player absent from injury report →
        # ACTIVE_INFERRED).  Check again whether game rows were written to
        # enr["box_score_log"] by _attempt_box_score_log during this same
        # fallback pass (may have arrived after the pre-ESPN check above).
        post_game_rows: list = (
            enr.get("box_score_log")
            or packet.get("box_score_log")
            or []
        )
        reconstructed = _reconstruct_role_from_box_scores(post_game_rows, as_of=as_of_ts)

        if reconstructed:
            bsr_record = _make_route_record(
                "reconstructed_from_box_scores",
                request_made   = False,
                method         = "RECONSTRUCTION",
                request_status = "RECONSTRUCTION_SUCCEEDED",
            )
            return RouteAttemptResult(
                field_category         = "role_status",
                source_id              = "reconstructed_from_box_scores",
                source_grade           = SourceGrade.B,
                method                 = AcquisitionMethod.RECONSTRUCTED,
                status                 = AcquisitionFieldStatus.MULTI_SOURCE_RECONSTRUCTED,
                value_retrieved        = reconstructed,
                note                   = (
                    f"box-score reconstruction from {reconstructed['games_used']} "
                    f"completed games (ESPN injuries PROXY_ONLY; reconstruction used "
                    f"as canonical evidence). "
                    f"projected_minutes={reconstructed['projected_minutes']}, "
                    f"starter_status={reconstructed['starter_status']}, "
                    f"confidence={reconstructed['confidence']}"
                ),
                # ESPN adapter DID make a real HTTP request — record it in routes_attempted
                routes_attempted       = http_attempted,
                adapter_result         = adapter,
                request_count          = adapter.request_count,
                route_records          = [adapter_rec, bsr_record] + list(ni_recs),
                routes_not_implemented = ni_names,
            )

        # Reconstruction had insufficient game data — PROXY_ONLY
        bsr_fail_record = _make_route_record(
            "reconstructed_from_box_scores",
            request_made   = False,
            method         = "RECONSTRUCTION",
            request_status = "RECONSTRUCTION_INSUFFICIENT_DATA",
            failure_reason = (
                f"fewer than 3 qualifying game rows "
                f"(pre-ESPN: {len(pre_game_rows)}, post-ESPN: {len(post_game_rows)})"
            ),
        )
        return RouteAttemptResult(
            field_category         = "role_status",
            source_id              = "espn_wnba_injuries",
            source_grade           = SourceGrade.A,
            method                 = AcquisitionMethod.WEB_FALLBACK,
            status                 = AcquisitionFieldStatus.PROXY_ONLY,
            value_retrieved        = nf,
            note                   = (
                "ACTIVE_INFERRED from absence on ESPN injury report (PROXY_ONLY); "
                f"box-score reconstruction failed: "
                f"{len(post_game_rows)} rows available, need ≥ 3 with minutes > 0"
            ),
            routes_attempted       = http_attempted,
            adapter_result         = adapter,
            request_count          = adapter.request_count,
            route_records          = [adapter_rec, bsr_fail_record] + list(ni_recs),
            routes_not_implemented = ni_names,
        )

    return RouteAttemptResult(
        field_category         = "role_status",
        source_id              = "all_configured_routes",
        source_grade           = SourceGrade.C,
        method                 = AcquisitionMethod.NOT_ATTEMPTED,
        status                 = AcquisitionFieldStatus.DATA_UNOBTAINABLE_AFTER_EXHAUSTION,
        note                   = (
            f"role_status unobtainable: pre-ESPN reconstruction had "
            f"{len(pre_game_rows)} rows (need ≥ 3); "
            f"ESPN injuries: {adapter.request_status}. "
            f"failure_reason={adapter.failure_reason}"
        ),
        routes_attempted       = http_attempted,
        adapter_result         = adapter,
        request_count          = adapter.request_count,
        route_records          = [adapter_rec] + list(ni_recs),
        routes_not_implemented = ni_names,
    )


def _attempt_box_score_log(packet: dict, enr: dict) -> RouteAttemptResult:
    """
    Resolve box_score_log / ledger fields:
      1. box_score_log already in packet (no HTTP)
      2. game_log alternate key in enrichment (no HTTP, reconstruction)
      3. ESPN WNBA athlete gamelog (real HTTP call, 2 requests — undocumented
         web JSON endpoint; not a guaranteed public developer API and may
         change without notice)

    Basketball Reference is configured in FALLBACK_SOURCE_PRIORITY but is
    NOT called — skipped per robots.txt / ToS policy.  It appears in
    routes_skipped_by_policy in the audit output, NOT in routes_attempted.

    StatMuse is configured but NOT called — reconstruction support only,
    no per-game proof source.  Appears in routes_not_implemented, NOT
    in routes_attempted.
    """
    pi_recs, pi_names = _policy_skipped_records_for("box_score_log")
    ni_recs, ni_names = _not_impl_records_for("box_score_log")
    non_http_recs     = pi_recs + ni_recs

    # Step 1 — in-pipeline: box_score_log already populated from primary enrichment
    raw_log    = packet.get("box_score_log") or []
    l5_ledger  = packet.get("l5_ledger")  or []
    l10_ledger = packet.get("l10_ledger") or []

    if raw_log:
        n = len(raw_log)
        return RouteAttemptResult(
            field_category           = "box_score_log",
            source_id                = "enrichment_box_score_log",
            source_grade             = SourceGrade.B,
            method                   = AcquisitionMethod.RECONSTRUCTED,
            status                   = AcquisitionFieldStatus.MULTI_SOURCE_RECONSTRUCTED,
            value_retrieved          = {"raw_game_count": n, "l5_rows": len(l5_ledger),
                                        "l10_rows": len(l10_ledger)},
            note                     = f"{n} raw game rows from primary enrichment; l5/l10 built",
            routes_attempted         = [],    # no HTTP request
            request_count            = 0,
            route_records            = list(non_http_recs),
            routes_skipped_by_policy = pi_names,
            routes_not_implemented   = ni_names,
        )

    # Step 2 — game_log alternate key (no HTTP)
    # NOTE: with BUG-001 fixed in build_packet(), this step is rarely reached
    # (build_packet now consumes "game_log" directly before detect_missing runs).
    # Kept as a defensive fallback.  Pass market_type so single-stat rows map correctly.
    game_log_alt = enr.get("game_log") or []
    if game_log_alt and isinstance(game_log_alt, list):
        raw_rows   = reconstruct_raw_ledger_rows(game_log_alt, market_type=packet.get("market") or None)
        l5, l10, _ = _split_ledger(raw_rows)
        n = len(raw_rows)
        if n > 0:
            return RouteAttemptResult(
                field_category           = "box_score_log",
                source_id                = "enrichment_game_log_alternate_key",
                source_grade             = SourceGrade.B,
                method                   = AcquisitionMethod.RECONSTRUCTED,
                status                   = AcquisitionFieldStatus.FALLBACK_RETRIEVED,
                value_retrieved          = {"raw_game_count": n, "l5_rows": len(l5),
                                            "l10_rows": len(l10), "source_key": "game_log"},
                note                     = f"reconstructed from enrichment['game_log'] ({n} rows)",
                routes_attempted         = [],    # no HTTP request
                request_count            = 0,
                route_records            = list(non_http_recs),
                routes_skipped_by_policy = pi_names,
                routes_not_implemented   = ni_names,
            )

    # Step 3 — ESPN WNBA athlete gamelog (real HTTP, 2 calls)
    player_name = packet.get("player") or ""
    adapter     = fetch_box_score_log(player_name, n_games=10)
    adapter_rec = _adapter_route_record(adapter)
    http_attempted = [adapter.provider] if adapter.request_count > 0 else []
    all_recs = [adapter_rec] + non_http_recs

    if adapter.request_status == RequestStatus.REQUEST_SUCCEEDED:
        game_rows = adapter.normalized_fields.get("game_rows") or []
        n         = len(game_rows)
        # Write rows back into enrichment so the packet rebuilds correctly
        enr["box_score_log"] = game_rows
        l5, l10, _ = _split_ledger(reconstruct_raw_ledger_rows(game_rows, market_type=packet.get("market") or None))
        return RouteAttemptResult(
            field_category           = "box_score_log",
            source_id                = "espn_wnba_athlete_gamelog",
            source_grade             = SourceGrade.B,
            method                   = AcquisitionMethod.WEB_FALLBACK,
            status                   = AcquisitionFieldStatus.FALLBACK_RETRIEVED,
            value_retrieved          = {"raw_game_count": n, "l5_rows": len(l5),
                                        "l10_rows": len(l10),
                                        "athlete_id": adapter.normalized_fields.get("athlete_id")},
            note                     = f"ESPN gamelog: {n} game rows retrieved for '{player_name}'",
            routes_attempted         = http_attempted,
            adapter_result           = adapter,
            request_count            = adapter.request_count,
            route_records            = all_recs,
            routes_skipped_by_policy = pi_names,
            routes_not_implemented   = ni_names,
        )

    # ESPN failed — BBRef (policy) and StatMuse (not-implemented) are NOT called.
    # They are recorded in routes_skipped_by_policy / routes_not_implemented only.
    return RouteAttemptResult(
        field_category           = "box_score_log",
        source_id                = "all_configured_routes",
        source_grade             = SourceGrade.C,
        method                   = AcquisitionMethod.NOT_ATTEMPTED,
        status                   = AcquisitionFieldStatus.DATA_UNOBTAINABLE_AFTER_EXHAUSTION,
        note                     = (
            f"box_score_log unobtainable after {len(http_attempted)} HTTP route(s). "
            f"ESPN gamelog: {adapter.request_status}. "
            f"BBRef: skipped (robots.txt/ToS policy). "
            f"StatMuse: not implemented (no per-game proof source). "
            f"failure_reason={adapter.failure_reason}"
        ),
        routes_attempted         = http_attempted,
        adapter_result           = adapter,
        request_count            = adapter.request_count,
        route_records            = all_recs,
        routes_skipped_by_policy = pi_names,
        routes_not_implemented   = ni_names,
    )


def _attempt_matchup(packet: dict, enr: dict) -> RouteAttemptResult:
    """
    Resolve matchup context.
    No external HTTP calls currently — PROXY_ONLY when unobtainable.
    (Official WNBA team stats endpoint requires session auth; external
    advanced-stats APIs are not connected in this patch.)

    Not-implemented sources (official_wnba_team_stats, advanced_stats_database,
    proxy_estimate_from_season_log) appear in routes_not_implemented; never called.
    """
    ni_recs, ni_names = _not_impl_records_for("matchup")
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
            field_category         = "matchup",
            source_id              = "enrichment_matchup_dict",
            source_grade           = SourceGrade.C,
            method                 = AcquisitionMethod.PRIMARY_API,
            status                 = status,
            value_retrieved        = {"defined_fields": list(defined.keys())},
            note                   = (
                f"{len(defined)}/{total} matchup sub-fields present; "
                "null fields marked PROXY_ONLY (not fabricated)"
            ),
            routes_attempted       = [],    # no HTTP request
            request_count          = 0,
            route_records          = list(ni_recs),
            routes_not_implemented = ni_names,
        )

    return RouteAttemptResult(
        field_category         = "matchup",
        source_id              = "all_configured_routes",
        source_grade           = SourceGrade.C,
        method                 = AcquisitionMethod.NOT_ATTEMPTED,
        status                 = AcquisitionFieldStatus.PROXY_ONLY,
        note                   = (
            "No matchup data in enrichment; marked PROXY_ONLY (analytical gates "
            "assess matchup independently). External team-stats endpoints require "
            "authenticated session outside current patch scope."
        ),
        routes_attempted       = [],    # no HTTP request
        request_count          = 0,
        route_records          = list(ni_recs),
        routes_not_implemented = ni_names,
    )


def _attempt_market_comparison(packet: dict, enr: dict) -> RouteAttemptResult:
    """
    Resolve market comparison via Odds API (real HTTP when key present).
    Returns AUTH_REQUIRED with no HTTP request when ODDS_API_KEY is absent
    (request_count=0, request_made=False on the route record).

    consensus_sportsbook_line is configured in the priority table but not
    implemented; it appears in routes_not_implemented only.
    """
    ni_recs, ni_names = _not_impl_records_for("market_comparison")
    player_name   = packet.get("player") or ""
    prop_type     = packet.get("market") or ""
    line          = packet.get("line")

    adapter     = fetch_market_comparison(player_name, prop_type, line)
    adapter_rec = _adapter_route_record(adapter)
    # request_made=True only if an actual HTTP request was issued
    http_attempted = [adapter.provider] if adapter.request_count > 0 else []
    all_recs = [adapter_rec] + ni_recs

    if adapter.request_status == RequestStatus.REQUEST_SUCCEEDED:
        nf = adapter.normalized_fields
        return RouteAttemptResult(
            field_category         = "market_comparison",
            source_id              = "odds_api_player_props",
            source_grade           = SourceGrade.A,
            method                 = AcquisitionMethod.PRIMARY_API,
            status                 = AcquisitionFieldStatus.FALLBACK_RETRIEVED,
            value_retrieved        = nf,
            note                   = (
                f"Odds API: consensus={nf.get('consensus_line')}, "
                f"books={nf.get('books_sampled')}"
            ),
            routes_attempted       = http_attempted,
            adapter_result         = adapter,
            request_count          = adapter.request_count,
            route_records          = all_recs,
            routes_not_implemented = ni_names,
        )

    # AUTH_REQUIRED (no key) / RATE_LIMITED / empty → DATA_UNOBTAINABLE
    # QUALIFICATION_BLOCKING — row proceeds with PACKET_PARTIAL_HOLD, not rejected
    return RouteAttemptResult(
        field_category         = "market_comparison",
        source_id              = "all_configured_routes",
        source_grade           = SourceGrade.C,
        method                 = AcquisitionMethod.NOT_ATTEMPTED,
        status                 = AcquisitionFieldStatus.DATA_UNOBTAINABLE_AFTER_EXHAUSTION,
        note                   = (
            f"market_comparison unobtainable: Odds API {adapter.request_status}. "
            f"failure_reason={adapter.failure_reason}"
        ),
        routes_attempted       = http_attempted,
        adapter_result         = adapter,
        request_count          = adapter.request_count,
        route_records          = all_recs,
        routes_not_implemented = ni_names,
    )


def _attempt_news_contradiction(packet: dict, enr: dict) -> RouteAttemptResult:
    """
    Run news/contradiction check via ESPN athlete news (real HTTP, 2 calls —
    undocumented web JSON endpoint; not a guaranteed public developer API and
    may change without notice).

    dedicated_conflict_scan is configured in the priority table but not
    implemented; it appears in routes_not_implemented only.
    """
    ni_recs, ni_names = _not_impl_records_for("news_contradiction")
    player_name   = packet.get("player") or ""

    adapter     = fetch_news_contradiction(player_name)
    adapter_rec = _adapter_route_record(adapter)
    http_attempted = [adapter.provider] if adapter.request_count > 0 else []
    all_recs = [adapter_rec] + ni_recs

    if adapter.request_status == RequestStatus.REQUEST_SUCCEEDED:
        nf = adapter.normalized_fields
        conflict = (
            AcquisitionFieldStatus.SOURCE_CONFLICT
            if nf.get("contradiction_found")
            else AcquisitionFieldStatus.FALLBACK_RETRIEVED
        )
        return RouteAttemptResult(
            field_category         = "news_contradiction",
            source_id              = "espn_wnba_athlete_news",
            source_grade           = SourceGrade.B,
            method                 = AcquisitionMethod.WEB_FALLBACK,
            status                 = conflict,
            value_retrieved        = nf,
            note                   = (
                f"ESPN news: {nf.get('article_count')} articles; "
                f"contradiction={'YES' if nf.get('contradiction_found') else 'NO'}"
            ),
            routes_attempted       = http_attempted,
            adapter_result         = adapter,
            request_count          = adapter.request_count,
            route_records          = all_recs,
            routes_not_implemented = ni_names,
        )

    return RouteAttemptResult(
        field_category         = "news_contradiction",
        source_id              = "all_configured_routes",
        source_grade           = SourceGrade.C,
        method                 = AcquisitionMethod.NOT_ATTEMPTED,
        status                 = AcquisitionFieldStatus.DATA_UNOBTAINABLE_AFTER_EXHAUSTION,
        note                   = (
            f"news_contradiction unobtainable: ESPN {adapter.request_status}. "
            f"failure_reason={adapter.failure_reason}"
        ),
        routes_attempted       = http_attempted,
        adapter_result         = adapter,
        request_count          = adapter.request_count,
        route_records          = all_recs,
        routes_not_implemented = ni_names,
    )


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

# Use lambdas so that monkey-patching any of these module-level functions
# (e.g. `_fr._attempt_role_status = fake`) is picked up at call time.
# A static dict of direct function references captures the original reference
# at module load and ignores later reassignments.
_CATEGORY_HANDLERS: dict[str, Any] = {
    "event_status":       lambda p, e: _attempt_event_status(p, e),
    "role_status":        lambda p, e: _attempt_role_status(p, e),
    "box_score_log":      lambda p, e: _attempt_box_score_log(p, e),
    "matchup":            lambda p, e: _attempt_matchup(p, e),
    "market_comparison":  lambda p, e: _attempt_market_comparison(p, e),
    "news_contradiction": lambda p, e: _attempt_news_contradiction(p, e),
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

    Dispatch order is enforced by CATEGORY_DISPATCH_ORDER — box_score_log always
    runs before role_status so that _attempt_box_score_log can write game rows into
    enr["box_score_log"] before _attempt_role_status reads them for reconstruction.
    """
    results: dict[str, RouteAttemptResult] = {}

    # ── Pass 1: dispatch in fixed order ──────────────────────────────────────
    for category in CATEGORY_DISPATCH_ORDER:
        if category not in missing_categories:
            continue
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

    # ── Pass 2: any categories not covered by the fixed order ────────────────
    for category in missing_categories:
        if category in results:
            continue
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
