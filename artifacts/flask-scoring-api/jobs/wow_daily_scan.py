"""
WOW Daily Scanner — jobs/wow_daily_scan.py

Pipeline:
  1. Pull today's events (Odds API primary, TheRundown backup)
  2. Pull player prop markets per event
  3. Pull ESPN player game logs → raw_l5 / raw_l10 rows
  4. Calculate L5/L10 hit rate, median, average
  5. Pull injury / status / lineup data
  6. Score each prop via compute_wow_score()
  7. Compute internal model projection (WOW v14.9.1)
  8. Classify: Final Approved — Internal Projection /
               Market Verified Approved / Model Qualified — PrizePicks /
               Conditional / Watch / Reject / Data Insufficient / No Play
  9. Save all results to scan_results table
 10. Return structured JSON including requested/scanned/missing sports

Classification gates (v14.9.1):
  Final Approved — Internal Projection:
    score >= 75 + injury OK + raw_l5 + raw_l10 + internal projection
    margin >= 5% + no live manual fallback
  Market Verified Approved:
    same as above + odds AVAILABLE + external projection (future)
  Never 0 Final Approved solely because external projection API is missing.
"""

import sys
import os
import json
import re
import statistics as _stats
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.odds_api   import fetch_all_props, SPORT_KEYS
from services.rundown    import fetch_backup_props
from services.player_logs import get_player_log_stats
from services.status     import get_injuries, get_player_injury_flag, get_mlb_probable_pitchers
from storage.results     import save_scan_result, get_scan_summary
from jobs.market_math import (
    no_vig_pair, pp_cash_threshold, compute_threshold_hit_rate,
    compute_drift_grade, classify_market_cause,
)

import importlib.util
_app_path = os.path.join(os.path.dirname(__file__), "..", "app.py")
_spec = importlib.util.spec_from_file_location("app_module", _app_path)
_app_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_app_module)
compute_wow_score = _app_module.compute_wow_score

# -------------------------------------------------------------------
# Sports to scan
# -------------------------------------------------------------------
ALL_SPORTS = ["NBA", "WNBA", "MLB", "NFL", "NHL", "NCAAB", "NCAAF", "Soccer", "Tennis"]

# Minimum projection margin (%) to clear the approval gate
PROJECTION_MARGIN_THRESHOLD = 5.0


# -------------------------------------------------------------------
# WOW v14.9.1 — Internal model projection
# -------------------------------------------------------------------

def compute_internal_projection(log_stats, line, side):
    """
    Compute an internal model projection from verified L5/L10 data.

    Formula:
      base     = L10 median (most stable anchor; fallback: L10 avg)
      trend    = (L5 avg − L10 avg) / L10 avg  →  recent momentum
      proj     = base × (1 + trend × 0.30)     →  30 % trend weight
      margin   = (proj − line) / line × 100     →  positive = favours MORE
                 (line − proj) / line × 100     →  positive = favours LESS

    Gate: margin must be >= PROJECTION_MARGIN_THRESHOLD (5 %) to approve.

    Returns dict:
      projection_status      — "INTERNAL" | "MISSING"
      projection_value       — rounded projected stat value
      projection_margin      — % clearance (positive = favourable for side)
      projection_source      — "internal_l10_model" | None
      final_approval_blocker — None (pass) or reason string (fail)
      used_average_only      — True when L10 median was unavailable and the
                                projection fell back to L10 average as its
                                base anchor (WOW-PATCH-2026-07-05-DATA-QUALITY-HOLD).
                                Average-only support is a real signal for
                                ranking/tracking/review, but is not sufficient
                                data quality for Power/Flex slip construction.
      live_cushion_margin     — raw (unsigned-to-side) live scoring margin:
                                projection_or_median − line. This is the LIVE
                                scoring signal, distinct from retro QA margins
                                (see `compute_retro_result_margin` below) —
                                never mix the two. None when no base anchor.
    """
    l10_median      = log_stats.get("l10_median")
    l10_avg         = log_stats.get("l10_avg")
    games_available = log_stats.get("games_available", 0)
    raw_l5          = log_stats.get("raw_l5",  [])
    raw_l10         = log_stats.get("raw_l10", [])

    # Need >= 5 games plus at least one summary stat
    if games_available < 5 or (l10_median is None and l10_avg is None):
        return {
            "projection_status":       "MISSING",
            "projection_value":        None,
            "projection_margin":       None,
            "projection_source":       None,
            "final_approval_blocker":  (
                "internal projection requires >= 5 verified games with L10 median/avg; "
                f"got {games_available} games"
            ),
            "used_average_only":       False,
            "live_cushion_margin":     None,
        }

    # Base anchor: L10 median preferred for stability. Falling back to the
    # average alone (no true median) is flagged so downstream classification
    # can apply the DATA_QUALITY_HOLD sub-tag — average-only support is not
    # enough for Power/Flex slip eligibility (WOW-PATCH-2026-07-05).
    used_average_only = l10_median is None and l10_avg is not None
    base = l10_median if l10_median is not None else l10_avg

    # L5 and L10 raw values for trend calculation
    l5_vals  = [r["stat"] for r in raw_l5]  if raw_l5  else []
    l10_vals = [r["stat"] for r in raw_l10] if raw_l10 else []

    l5_avg_val   = (sum(l5_vals)  / len(l5_vals))  if l5_vals  else (l10_avg or base)
    l10_avg_val  = (sum(l10_vals) / len(l10_vals)) if l10_vals else (l10_avg or base)

    # Trend factor: positive = recent form above baseline
    trend_factor = 0.0
    if l10_avg_val and l10_avg_val > 0:
        trend_factor = (l5_avg_val - l10_avg_val) / l10_avg_val

    # Blend: 70 % base + 30 % trend influence
    projection_value = round(base * (1.0 + trend_factor * 0.30), 2)

    # Margin in favour of the side
    if line > 0:
        margin = (
            (projection_value - line) / line * 100 if side == "MORE"
            else (line - projection_value) / line * 100
        )
        margin = round(margin, 2)
    else:
        margin = 0.0

    # Gate check
    if margin < PROJECTION_MARGIN_THRESHOLD:
        blocker = (
            f"internal projection margin {margin:.1f}% < required "
            f"{PROJECTION_MARGIN_THRESHOLD:.0f}% "
            f"(proj={projection_value}, line={line}, side={side})"
        )
    else:
        blocker = None

    # LIVE cushion margin: projection_or_median − line, raw (not side-signed,
    # not a %). This is the live-scoring field; it must never be confused
    # with `compute_retro_result_margin` below, which is retro QA-only and
    # uses the settled final_result instead of the projection/median.
    live_cushion_margin = round(projection_value - line, 4)

    return {
        "projection_status":      "INTERNAL",
        "projection_value":       projection_value,
        "projection_margin":      margin,
        "projection_source":      "internal_l10_model",
        "final_approval_blocker": blocker,
        "used_average_only":      used_average_only,
        "live_cushion_margin":    live_cushion_margin,
    }


def compute_retro_result_margin(final_result, line):
    """
    RETRO QA ONLY — never used for live scoring/classification.

    retro_result_margin = final_result - line

    This measures how a prop actually settled relative to its line, for
    post-hoc grading/calibration review. It is distinct from
    `live_cushion_margin` (projection_or_median − line), which is the
    live-scoring signal computed BEFORE the game and used for gating.
    Mixing the two would silently leak retro/settlement information into
    the live approval pipeline — keep them separate fields end-to-end.

    Returns None if either input is missing/invalid.
    """
    if final_result is None or line is None:
        return None
    try:
        return round(float(final_result) - float(line), 4)
    except (TypeError, ValueError):
        return None


# -------------------------------------------------------------------
# Line normalization (PATCH-BINARY-EVENT-POSTSCAN-INVARIANT)
# -------------------------------------------------------------------

_LINE_NUMBER_RE = re.compile(r"-?(?:\d+\.\d+|\.\d+|\d+)")


def normalize_line(line):
    """
    Best-effort extraction of the numeric line value from either a plain
    number or an OCR/string-style row, e.g.:
      0.5, "0.5", "0.50", ".5", "0.5 Hits", "LESS 0.5", "More Than 0.5"

    Returns a float, or None if no numeric token could be found. This is
    intentionally permissive (first numeric token wins) since it is only
    used to detect the binary-event 0.5 line shape, never to price/settle
    anything.
    """
    if line is None:
        return None
    if isinstance(line, (int, float)):
        try:
            return float(line)
        except (TypeError, ValueError):
            return None
    match = _LINE_NUMBER_RE.search(str(line))
    if not match:
        return None
    try:
        return float(match.group())
    except ValueError:
        return None


def is_binary_event_line(line):
    """True when the normalized line is exactly 0.5 (single-occurrence
    "did it happen at all" threshold)."""
    val = normalize_line(line)
    return val is not None and val == 0.5


# -------------------------------------------------------------------
# Classification logic (v14.9.1)
# -------------------------------------------------------------------

def classify_prop(
    wow_score, signal, log_status, inj_flag, sources,
    raw_l5=None, raw_l10=None,
    manual_fallback_used=False, environment="live",
    projection_data=None, line=None,
):
    """
    WOW v14.9.1 — returns
    (classification_label, final_approval_blocker | None,
     data_quality_tag | None, block_power_flex: bool).

    Tier 1: Market Verified Approved
      score >= 75, injury OK, odds AVAILABLE, raw L5+L10, external projection,
      projection margin >= 5%, no live manual fallback.

    Tier 1b: Final Approved — Internal Projection
      Same as above but uses internal model projection instead of external.
      Does NOT require odds_ok label ("Market Verified") — only verified data.

    Tier 2: Model Qualified — PrizePicks   (score >= 65, logs AVAILABLE)
    Tier 3: Conditional                    (score >= 55)
    Tier 4: Watch                          (score >= 45)
    Tier 5: Reject                         (score < 45 or injury >= 2)
    Tier 6: Data Insufficient              (no sources available)

    PATCH-BINARY-EVENT-PURGE: a 0.5 line is a single-occurrence "did it
    happen at all" threshold (e.g. MLB Hitter Hits LESS 0.5). This is a
    structural trait, not a statistical one — wow_score/proj_margin can
    still look strong on these — so it is capped at "Watch" (never Model
    Qualified or above) regardless of score, ahead of every other tier.

    WOW-PATCH-2026-07-05-DATA-QUALITY-HOLD (Section 32 ruling):
    DATA_QUALITY_HOLD is a SUB-TAG, never a terminal label. It is applied
    when the internal projection fell back to L10-average-only support
    (L10 median missing). Rules:
      - Default parent label: "Watch".
      - Ceiling: "Model Qualified — PrizePicks", and only when there is
        independent market/projection support (odds_ok AND proj_ok AND
        score/log thresholds are otherwise met). Never Final Approved or
        Market Verified.
      - Never a terminal label by itself — it rides alongside whatever
        parent label it caps, surfaced via data_quality_tag.
      - Never overrides a harder cap that already ran (injury reject,
        binary-event structural cap) and never overrides THIN_MARGIN_RISK
        if/when that tag is implemented elsewhere in the pipeline.
      - block_power_flex=True whenever the tag is set — average-only
        support is not sufficient data quality for Power/Flex slip
        construction, even at the Model Qualified ceiling.
    """
    _binary_event = is_binary_event_line(line)

    odds_ok   = "AVAILABLE" in (sources.get("odds", "") or "")
    logs_ok   = "AVAILABLE" in (log_status or "")
    inj_ok    = inj_flag < 2
    raw_l5_ok  = bool(raw_l5)
    raw_l10_ok = bool(raw_l10)
    raw_logs_ok = raw_l5_ok and raw_l10_ok
    live_manual_block = manual_fallback_used and (environment == "live")

    # Projection gate
    proj = projection_data or {}
    proj_status  = proj.get("projection_status", "MISSING")
    proj_margin  = proj.get("projection_margin") or 0.0
    proj_blocker = proj.get("final_approval_blocker")
    proj_ok = (
        proj_status in ("EXTERNAL", "INTERNAL")
        and proj_margin >= PROJECTION_MARGIN_THRESHOLD
        and not proj_blocker
    )
    used_average_only = bool(proj.get("used_average_only"))

    # --- Hard reject: injury ---
    if not inj_ok:
        return "Reject", f"injury_flag={inj_flag} (>= 2)", None, False

    # --- Binary-event structural cap (PATCH-BINARY-EVENT-PURGE) ---
    # Ahead of every scoring tier: a 0.5-line prop is structurally near-binary
    # (single occurrence decides the whole outcome) and must never reach
    # Model Qualified, Final Approved, or Market Verified, no matter how
    # strong wow_score/projection_margin look.
    if _binary_event:
        if wow_score >= 45:
            return (
                "Watch",
                "binary_event_structural_cap: 0.5 line is a single-occurrence binary outcome, capped below Model Qualified",
                None, False,
            )
        any_source = any("AVAILABLE" in str(v) for v in sources.values())
        if not any_source:
            return (
                "Data Insufficient",
                "binary_event_structural_cap: 0.5 line is a single-occurrence binary outcome",
                None, False,
            )
        return "Reject", f"binary_event_structural_cap (score={wow_score} < 45)", None, False

    # --- DATA_QUALITY_HOLD sub-tag (WOW-PATCH-2026-07-05, Section 32) ---
    # Average-only internal projection support (L10 median missing) is a
    # real signal worth ranking/tracking/reviewing, but it is not enough
    # data quality for Power/Flex slip eligibility. Default to "Watch";
    # only rise to "Model Qualified — PrizePicks" with independent
    # market/projection support, and never above that.
    if used_average_only:
        data_quality_tag = "DATA_QUALITY_HOLD"
        if wow_score >= 65 and logs_ok and odds_ok and proj_ok and not live_manual_block:
            return (
                "Model Qualified — PrizePicks",
                "DATA_QUALITY_HOLD: average-only support (L10 median missing) — "
                "independent market/projection support present, capped below Final Approved",
                data_quality_tag, True,
            )
        return (
            "Watch",
            "DATA_QUALITY_HOLD: average-only support (L10 median missing) — "
            "not eligible for Power/Flex until a true projection or L10 median is retrieved",
            data_quality_tag, True,
        )

    # --- Final Approval tier ---
    if wow_score >= 75 and inj_ok and raw_logs_ok and not live_manual_block and proj_ok:
        if odds_ok and proj_status == "EXTERNAL":
            # Full external verification — highest label
            return "Market Verified Approved", None, None, False
        # Internal projection (may still have odds_ok = True)
        return "Final Approved — Internal Projection", None, None, False

    # Build blocker reason for lower tiers
    blocker_parts = []
    if wow_score < 75:
        blocker_parts.append(f"score={wow_score} < 75")
    if not raw_l5_ok:
        blocker_parts.append("raw_l5 missing")
    if not raw_l10_ok:
        blocker_parts.append("raw_l10 missing")
    if live_manual_block:
        blocker_parts.append("manual_fallback in live environment")
    if not proj_ok:
        if proj_status == "MISSING":
            blocker_parts.append("internal projection: insufficient data")
        elif proj_margin < PROJECTION_MARGIN_THRESHOLD:
            blocker_parts.append(
                f"projection margin {proj_margin:.1f}% < required "
                f"{PROJECTION_MARGIN_THRESHOLD:.0f}%"
            )
    blocker = "; ".join(blocker_parts) if blocker_parts else None

    if wow_score >= 65 and logs_ok:
        return "Model Qualified — PrizePicks", blocker, None, False
    if wow_score >= 55:
        return "Conditional", blocker, None, False
    if wow_score >= 45:
        return "Watch", blocker, None, False

    any_source = any("AVAILABLE" in str(v) for v in sources.values())
    if not any_source:
        return "Data Insufficient", blocker, None, False

    return "Reject", f"score={wow_score} < 45", None, False


# -------------------------------------------------------------------
# Dedup props
# -------------------------------------------------------------------

def dedup_props(props):
    seen = {}
    for p in props:
        key = (p["player"], p["prop"], p["side"], p["line"], p["sport"])
        if key not in seen:
            seen[key] = p
    return list(seen.values())


# -------------------------------------------------------------------
# WOW-PATCH-2026-07-06 — cross-book consensus + mutex grouping
# -------------------------------------------------------------------

def build_consensus_map(props):
    """
    Group raw props (pre-dedup, all bookmakers) by (player, prop, side) to
    build a cross-book consensus view: average line, average price, and a
    conflict flag when bookmakers disagree on the line by more than a small
    tolerance. Returns {(player, prop): {"MORE": {...}, "LESS": {...},
    "conflict": bool}}.

    This is the cross-market consensus used for board_consensus_delta and
    no_vig_probability — a scoped stand-in for a dedicated PrizePicks board
    feed, which this scanner does not currently ingest separately from the
    sportsbook odds it pulls (see WOW-PATCH-2026-07-06 doc, "Deferred").
    """
    by_key = {}
    for p in props:
        key = (p.get("player", ""), p.get("prop", ""))
        side = (p.get("side") or "MORE").upper()
        line = p.get("line")
        price = p.get("price")
        if line is None:
            continue
        entry = by_key.setdefault(key, {"MORE": [], "LESS": []})
        if side in ("MORE", "OVER"):
            entry["MORE"].append((float(line), price))
        elif side in ("LESS", "UNDER"):
            entry["LESS"].append((float(line), price))

    consensus = {}
    for key, sides in by_key.items():
        lines_seen = [ln for ln, _ in sides["MORE"]] + [ln for ln, _ in sides["LESS"]]
        conflict = bool(lines_seen) and (max(lines_seen) - min(lines_seen) > 1.0)

        def _avg(entries):
            if not entries:
                return None, None
            avg_line = sum(ln for ln, _ in entries) / len(entries)
            prices = [pr for _, pr in entries if pr is not None]
            avg_price = sum(prices) / len(prices) if prices else None
            return round(avg_line, 4), (round(avg_price, 2) if avg_price is not None else None)

        more_line, more_price = _avg(sides["MORE"])
        less_line, less_price = _avg(sides["LESS"])
        consensus[key] = {
            "consensus_line": more_line if more_line is not None else less_line,
            "consensus_price_more": more_price,
            "consensus_price_less": less_price,
            "conflict": conflict,
        }
    return consensus


def assign_mutex_groups(cards):
    """
    WOW-PATCH-2026-07-06 item 8 (scoped) — same-player mutex grouping.

    Groups playable-tier cards by (sport, player, game_date). When a group
    has more than one candidate, tags all of them with a shared
    mutex_group_id and marks exactly one (highest wow_score) as the
    preferred_candidate; the rest get preferred_candidate=False.

    Full stat-family / same-pitcher / same-game-script correlation (as
    implemented for slip construction in gate_engine/correlation_gate.py) is
    deferred — see WOW-PATCH-2026-07-06 doc, "Deferred".
    """
    groups = {}
    for card in cards:
        key = (card.get("sport"), card.get("player"), card.get("game_date"))
        groups.setdefault(key, []).append(card)

    for key, group in groups.items():
        if len(group) < 2:
            for card in group:
                card["mutex_group_id"] = None
                card["preferred_candidate"] = True
            continue
        mutex_id = f"MUTEX_{key[0]}_{key[1]}_{key[2]}".replace(" ", "_")
        best = max(group, key=lambda c: c.get("wow_score") or 0)
        for card in group:
            card["mutex_group_id"] = mutex_id
            card["preferred_candidate"] = (card is best)


# -------------------------------------------------------------------
# Main scan function
# -------------------------------------------------------------------

def run_scan(sports=None, environment="live", limit_per_sport=50,
             runtime_provenance=None,
             _props_by_sport=None,
             _persist_results=True):
    """
    Run the WOW daily scan.

    Parameters
    ----------
    sports             : list[str] | None — sports to evaluate (default: ALL_SPORTS)
    environment        : str — "live" or "test"
    limit_per_sport    : int | None — max props per sport after dedup.
                         Pass None to disable truncation (used by canonical orchestrator).
    runtime_provenance : dict | None — server-authoritative provenance record
    _props_by_sport    : dict[str, list] | None — pre-fetched canonical board from the
                         orchestrator.  When supplied, source fetches are skipped entirely
                         and the union is already done; limit_per_sport is not applied.
    _persist_results   : bool — when False, save_scan_result() is NOT called for
                         individual rows (orchestrator owns persistence via manifest).

    Returns structured result dict including:
      requested_sports / scanned_sports / missing_sports / scan_valid
    """
    requested_sports = list(sports) if sports is not None else list(ALL_SPORTS)

    # WOW-PATCH-2026-08-19 — runtime provenance (fail-closed, downgrade-only).
    # The one attested run record is stamped onto every candidate row; when
    # the run is not production-backend-verified, playable classifications
    # are capped to Watch at scoring time so persisted rows (and therefore
    # /scan-results/summary for async runs) can never expose playable
    # buckets from an unverified run.
    _prov_blocker = None
    if runtime_provenance is not None:
        try:
            from gate_engine.runtime_provenance import provenance_blocker
            _prov_blocker = provenance_blocker(runtime_provenance)
        except Exception:
            _prov_blocker = "RUNTIME_PROVENANCE:BACKEND_NOT_VERIFIED:UNSPECIFIED"

    run_date = date.today().isoformat()
    run_at   = datetime.now(timezone.utc).isoformat()

    source_access = {}
    market_verified          = []
    final_approved_internal  = []
    model_qualified          = []
    conditional              = []
    watch_list               = []
    reject_list              = []
    data_insufficient        = []
    no_play_list             = []
    execution_notes          = []
    scanned_sports           = []
    failed_modules           = []  # WOW-PATCH-2026-07-06 item 9 — DEGRADED_ENGINE_RUN

    for sport in requested_sports:
        execution_notes.append(f"--- {sport} ---")

        # Step 1-3: Props
        # When the canonical orchestrator supplies a pre-fetched, unioned board
        # via _props_by_sport, skip all HTTP fetches — source union was already
        # done upstream and no truncation applies.
        if _props_by_sport is not None:
            props = list(_props_by_sport.get(sport) or [])
            source_access[f"{sport}_odds"]    = "ORCHESTRATOR_CANONICAL_BOARD"
            source_access[f"{sport}_rundown"] = "ORCHESTRATOR_CANONICAL_BOARD"
            if not props:
                execution_notes.append(
                    f"{sport}: no props in orchestrator board — MISSING"
                )
                continue
        else:
            # WOW-PATCH-2026-08-19-DAILY-CANONICAL: always union primary AND
            # backup instead of replacing primary with backup on empty.
            # WOW-PATCH-2026-07-06 item 9: exceptions are recorded, not raised.
            primary_props = []
            odds_status   = "NOT_CALLED"
            try:
                primary_props, odds_status = fetch_all_props(sport)
                source_access[f"{sport}_odds"] = (
                    odds_status.get("props", odds_status)
                    if isinstance(odds_status, dict) else str(odds_status)
                )
            except Exception as exc:
                failed_modules.append(f"{sport}:fetch_all_props:{exc}")
                source_access[f"{sport}_odds"] = f"FAILED: {exc}"

            try:
                rundown_props, rd_status = fetch_backup_props(sport)
            except Exception as exc:
                failed_modules.append(f"{sport}:fetch_backup_props:{exc}")
                rundown_props, rd_status = [], f"FAILED: {exc}"
            source_access[f"{sport}_rundown"] = rd_status

            # Union both sources (never replace)
            props = list(primary_props or []) + list(rundown_props or [])
            if props:
                if primary_props and rundown_props:
                    execution_notes.append(
                        f"{sport}: unioned primary+backup ({len(primary_props)}+{len(rundown_props)} props)"
                    )
                elif rundown_props:
                    execution_notes.append(
                        f"{sport}: primary empty, using TheRundown backup ({len(rundown_props)} props)"
                    )
            else:
                execution_notes.append(
                    f"{sport}: no props from any source — MISSING"
                )
                continue

        # WOW-PATCH-2026-07-06 item 4 (scoped) — cross-book consensus, used
        # for board_consensus_delta / no_vig_probability, and a SOURCE_CONFLICT
        # flag when bookmakers disagree on the same player/prop line by more
        # than 1.0. Full Layer-0 event reconciliation (team/opponent/game_id/
        # start_time identity matching across independent sources) is
        # deferred — see WOW-PATCH-2026-07-06 doc, "Deferred".
        consensus_map = build_consensus_map(props)

        props = dedup_props(props)
        # Truncation only applies to direct legacy callers.
        # The canonical orchestrator passes _props_by_sport and limit_per_sport=None.
        if (
            _props_by_sport is None
            and limit_per_sport is not None
            and len(props) > limit_per_sport
        ):
            props = props[:limit_per_sport]
        execution_notes.append(f"{sport}: {len(props)} unique props to evaluate")
        scanned_sports.append(sport)

        try:
            injuries_cache, inj_source = get_injuries(sport)
        except Exception as exc:
            failed_modules.append(f"{sport}:get_injuries:{exc}")
            injuries_cache, inj_source = {}, f"FAILED: {exc}"
        source_access[f"{sport}_status"] = inj_source

        mlb_pitchers = {}
        if sport == "MLB":
            try:
                mlb_pitchers, _ = get_mlb_probable_pitchers(run_date)
            except Exception as exc:
                failed_modules.append(f"MLB:get_mlb_probable_pitchers:{exc}")
                mlb_pitchers = {}

        for p in props:
            player    = p.get("player", "")
            prop_key  = p.get("prop", "")
            side      = p.get("side", "MORE")
            line      = float(p.get("line", 0))
            game_date = p.get("game_date", run_date)

            if not player or not prop_key or line <= 0:
                continue

            # Game logs + raw rows
            log_stats, log_status = get_player_log_stats(
                sport, player, prop_key, line, side
            )
            log_key = f"{sport}_logs"
            prev = source_access.get(log_key, "")
            if "AVAILABLE" in log_status:
                source_access[log_key] = log_status
            elif "AVAILABLE" not in prev:
                source_access[log_key] = log_status

            inj_flag, inj_raw, _ = get_player_injury_flag(
                sport, player, injuries_cache=injuries_cache
            )

            if sport == "MLB" and "pitcher" in prop_key:
                if mlb_pitchers and player.lower() not in mlb_pitchers:
                    data_insufficient.append({
                        "player": player, "sport": sport, "prop": prop_key,
                        "side": side, "line": line,
                        "reason": "not listed as probable pitcher",
                    })
                    continue

            raw_l5               = log_stats.get("raw_l5",  [])
            raw_l10              = log_stats.get("raw_l10", [])
            games_available      = log_stats.get("games_available", 0)
            sample_scope         = log_stats.get("sample_scope", "insufficient")
            cross_season_used    = log_stats.get("cross_season_used", False)
            manual_fallback_used = log_stats.get("manual_fallback_used", False)
            stat_log_status      = log_stats.get("log_status", "RAW_LOG_MISSING")

            # WOW score
            features = {
                "l5_hit_rate":  log_stats.get("l5_hit_rate"),
                "l10_hit_rate": log_stats.get("l10_hit_rate"),
                "recent_avg":   log_stats.get("l10_avg"),
                "median_edge":  _median_edge(log_stats.get("l10_median"), line),
                "injury_flag":  inj_flag,
            }
            features = {k: v for k, v in features.items() if v is not None}
            wow_score, signal, message = compute_wow_score(
                features, player, prop_key, side, line
            )

            # v14.9.1 — internal model projection
            projection_data = compute_internal_projection(log_stats, line, side)

            # Classify (returns tuple)
            classification, final_approval_blocker, data_quality_tag, block_power_flex = classify_prop(
                wow_score, signal, log_status, inj_flag,
                {"odds": source_access.get(f"{sport}_odds", "NOT_CALLED")},
                raw_l5=raw_l5,
                raw_l10=raw_l10,
                manual_fallback_used=manual_fallback_used,
                environment=environment,
                projection_data=projection_data,
                line=line,
            )

            # Audit validity
            live_manual_block = manual_fallback_used and environment == "live"
            audit_valid = bool(raw_l5) and bool(raw_l10) and not live_manual_block
            if not audit_valid:
                if not raw_l5:
                    invalid_reason = "L5 raw rows not returned"
                elif not raw_l10:
                    invalid_reason = "L10 raw rows not returned"
                elif live_manual_block:
                    invalid_reason = "manual_fallback_used=true in live environment"
                else:
                    invalid_reason = "unknown audit failure"
            else:
                invalid_reason = None

            # ── WOW-PATCH-2026-07-06 — full output-row contract math ────────
            odds_ok_flag = "AVAILABLE" in (source_access.get(f"{sport}_odds", "") or "")
            logs_ok_flag = "AVAILABLE" in (log_status or "")

            cmap = consensus_map.get((player, prop_key), {})
            consensus_line        = cmap.get("consensus_line")
            consensus_price_more  = cmap.get("consensus_price_more")
            consensus_price_less  = cmap.get("consensus_price_less")
            source_conflict       = bool(cmap.get("conflict"))

            no_vig_more, no_vig_less = no_vig_pair(consensus_price_more, consensus_price_less)
            no_vig_probability = no_vig_more if side == "MORE" else no_vig_less

            pp_threshold = pp_cash_threshold(side, line)
            threshold_value = pp_threshold.get("cash_requires") if side == "MORE" else pp_threshold.get("cash_at_or_below")
            threshold_hit_rate = compute_threshold_hit_rate(raw_l10 or raw_l5, side, threshold_value)

            # model_probability: prefer the threshold-adjusted hit rate (the
            # empirical rate against the real PrizePicks cash threshold, item 7)
            # falling back to the line-based L10/L5 hit rate when unavailable.
            model_probability = (
                threshold_hit_rate
                if threshold_hit_rate is not None
                else (log_stats.get("l10_hit_rate") if log_stats.get("l10_hit_rate") is not None
                      else log_stats.get("l5_hit_rate"))
            )

            adjusted_edge = (
                round(model_probability - no_vig_probability, 4)
                if model_probability is not None and no_vig_probability is not None
                else None
            )
            edge_math = (
                f"{model_probability} - {no_vig_probability} = {adjusted_edge}"
                if adjusted_edge is not None else None
            )

            board_consensus_delta = (
                round(line - consensus_line, 4)
                if consensus_line is not None else None
            )
            drift_grade = compute_drift_grade(adjusted_edge)

            role_deployment_uncertain = bool(
                sport == "MLB" and "pitcher" in prop_key and not mlb_pitchers
            )
            payout_ev_fail = bool(
                threshold_hit_rate is not None
                and log_stats.get("l10_hit_rate") is not None
                and threshold_hit_rate < log_stats.get("l10_hit_rate") - 0.05
            )
            stale_board = bool(
                "AVAILABLE" not in (source_access.get(f"{sport}_odds", "") or "")
                and "AVAILABLE" in (source_access.get(f"{sport}_rundown", "") or "")
            )

            market_cause = classify_market_cause(
                classification=classification,
                odds_ok=odds_ok_flag,
                logs_ok=logs_ok_flag,
                adjusted_edge=adjusted_edge,
                used_average_only=bool(projection_data.get("used_average_only")),
                manual_fallback_used=manual_fallback_used,
                source_conflict=source_conflict,
                role_deployment_uncertain=role_deployment_uncertain,
                payout_ev_fail=payout_ev_fail,
                stale_board=stale_board,
            )

            # SOURCE_CONFLICT caps classification until resolved (item 4)
            if source_conflict and classification in (
                "Market Verified Approved", "Final Approved — Internal Projection",
                "Model Qualified — PrizePicks", "Conditional",
            ):
                final_approval_blocker = (
                    (final_approval_blocker + "; " if final_approval_blocker else "")
                    + "SOURCE_CONFLICT: bookmakers disagree on this player/prop line by > 1.0 — capped until resolved"
                )
                classification = "Watch"

            # WOW-PATCH-2026-07-06 item 2 — REJECT_NO_EDGE explicit no-vig math
            # Whenever the terminal bucket is "Reject" purely on score/edge
            # grounds (not injury or the binary-event structural cap, which
            # already set their own explicit blocker text above), replace the
            # generic "score=X < 45" blocker with the literal no-vig
            # calculation so a rejected row always shows its math.
            if classification == "Reject" and edge_math is not None and (
                final_approval_blocker or ""
            ).startswith("score="):
                final_approval_blocker = f"REJECT_NO_EDGE: {edge_math} (no verified positive edge vs. no-vig consensus)"

            # WOW-PATCH-2026-08-19 — runtime provenance cap (downgrade-only):
            # an unverified run can never persist a playable classification.
            runtime_provenance_hold = False
            if _prov_blocker is not None and classification in (
                "Market Verified Approved", "Final Approved — Internal Projection",
                "Model Qualified — PrizePicks",
            ):
                final_approval_blocker = (
                    (final_approval_blocker + "; " if final_approval_blocker else "")
                    + _prov_blocker
                )
                classification = "Watch"
                runtime_provenance_hold = True

            result_row = {
                "runtime_provenance":  runtime_provenance,
                "runtime_provenance_hold": runtime_provenance_hold,
                "run_date":            run_date,
                "sport":               sport,
                "player":              player,
                "prop":                prop_key,
                "line":                line,
                "side":                side,
                "game_date":           game_date,
                "wow_score":           wow_score,
                "signal":              signal,
                "message":             message,
                "classification":      classification,
                "environment":         environment,
                "source_odds":         source_access.get(f"{sport}_odds",    "NOT_CALLED"),
                "source_rundown":      source_access.get(f"{sport}_rundown", "NOT_CALLED"),
                "source_logs":         log_status,
                "source_status":       inj_source,
                "l5_hit_rate":         log_stats.get("l5_hit_rate"),
                "l10_hit_rate":        log_stats.get("l10_hit_rate"),
                "l10_median":          log_stats.get("l10_median"),
                "l10_avg":             log_stats.get("l10_avg"),
                "raw_features":        features,
                "notes":               f"injury={inj_raw}; games_found={games_available}",
                "raw_l5":              raw_l5,
                "raw_l10":             raw_l10,
                "games_available":     games_available,
                "sample_scope":        sample_scope,
                "cross_season_used":   cross_season_used,
                "manual_fallback_used": manual_fallback_used,
                "audit_valid":         audit_valid,
                "invalid_reason":      invalid_reason,
                # v14.9.1 projection fields
                "projection_status":      projection_data.get("projection_status"),
                "projection_value":       projection_data.get("projection_value"),
                "projection_margin":      projection_data.get("projection_margin"),
                "projection_source":      projection_data.get("projection_source"),
                "final_approval_blocker": final_approval_blocker,
                # WOW-PATCH-2026-07-05 — DATA_QUALITY_HOLD sub-tag + margin split
                "used_average_only":      projection_data.get("used_average_only", False),
                "data_quality_tag":       data_quality_tag,
                "block_power_flex":       block_power_flex,
                "live_cushion_margin":    projection_data.get("live_cushion_margin"),
                "retro_result_margin":    None,  # populated later by retro/settlement QA, never at scan time
                "final_result":           None,
                # WOW-PATCH-2026-07-06 — full output-row contract (items 1-3, 7, 9)
                "board_line":             line,
                "pp_cash_threshold":      json.dumps(pp_threshold),
                "consensus_line":         consensus_line,
                "consensus_price_more":   consensus_price_more,
                "consensus_price_less":   consensus_price_less,
                "no_vig_probability":     no_vig_probability,
                "model_probability":      model_probability,
                "adjusted_edge":          adjusted_edge,
                "edge_math":              edge_math,
                "board_consensus_delta":  board_consensus_delta,
                "drift_grade":            drift_grade,
                "market_cause":           market_cause,
                "terminal_bucket":        classification,
                "threshold_hit_rate":     threshold_hit_rate,
                "source_conflict":        source_conflict,
                "mutex_group_id":         None,       # filled in post-scan by assign_mutex_groups
                "preferred_candidate":    None,
            }
            if _persist_results:
                save_scan_result(result_row)

            card = {
                "player": player, "sport": sport, "prop": prop_key,
                "side": side, "line": line, "game_date": game_date,
                "wow_score": wow_score, "signal": signal,
                "games_available": games_available,
                "sample_scope": sample_scope,
                "audit_valid": audit_valid,
                "stat_log_status": stat_log_status,
                "projection_status":  projection_data.get("projection_status"),
                "projection_value":   projection_data.get("projection_value"),
                "projection_margin":  projection_data.get("projection_margin"),
                "projection_source":  projection_data.get("projection_source"),
                "used_average_only":  projection_data.get("used_average_only", False),
                "data_quality_tag":   data_quality_tag,
                "block_power_flex":   block_power_flex,
                "live_cushion_margin": projection_data.get("live_cushion_margin"),
                "final_approval_blocker": final_approval_blocker,
                # WOW-PATCH-2026-07-06
                "board_line":            line,
                "pp_cash_threshold":     pp_threshold,
                "consensus_line":        consensus_line,
                "consensus_price_more":  consensus_price_more,
                "consensus_price_less":  consensus_price_less,
                "no_vig_probability":    no_vig_probability,
                "model_probability":     model_probability,
                "adjusted_edge":         adjusted_edge,
                "edge_math":             edge_math,
                "board_consensus_delta": board_consensus_delta,
                "drift_grade":           drift_grade,
                "market_cause":          market_cause,
                "terminal_bucket":       classification,
                "threshold_hit_rate":    threshold_hit_rate,
                "source_conflict":       source_conflict,
            }

            if classification == "Market Verified Approved":
                market_verified.append(card)
            elif classification == "Final Approved — Internal Projection":
                final_approved_internal.append(card)
            elif classification == "Model Qualified — PrizePicks":
                model_qualified.append(card)
            elif classification == "Conditional":
                conditional.append(card)
            elif classification == "Watch":
                watch_list.append(card)
            elif classification == "Data Insufficient":
                data_insufficient.append({**card, "reason": log_status})
            elif classification == "No Play":
                no_play_list.append(card)
            else:
                reject_list.append(card)

    # ── PATCH-BINARY-EVENT-POSTSCAN-INVARIANT ────────────────────────────────
    # Final safety net, independent of classify_prop(): guarantee no output
    # list can ever contain a 0.5-line row above WATCH, even if some future
    # code path bypasses classify_prop() entirely. Downgrades any offending
    # card out of its current bucket and into watch_list.
    _INVARIANT_GUARDED_BUCKETS = (
        ("market_verified",         market_verified),
        ("final_approved_internal", final_approved_internal),
        ("model_qualified",         model_qualified),
        ("conditional",             conditional),
    )
    for _bucket_name, _bucket in _INVARIANT_GUARDED_BUCKETS:
        _keep = []
        for _card in _bucket:
            if is_binary_event_line(_card.get("line")):
                _downgraded = dict(_card)
                _downgraded["final_approval_blocker"] = "BE1_BINARY_LINE_0PT5"
                _downgraded["binary_event_cap"] = True
                _downgraded["can_execute"] = False
                _downgraded["postscan_invariant_downgraded_from"] = _bucket_name
                watch_list.append(_downgraded)
                execution_notes.append(
                    f"POSTSCAN INVARIANT: downgraded {_card.get('player')} / "
                    f"{_card.get('prop')} (line=0.5) out of {_bucket_name} to watch "
                    f"[BE1_BINARY_LINE_0PT5]"
                )
            else:
                _keep.append(_card)
        _bucket[:] = _keep

    # ── WOW-PATCH-2026-07-06 item 8 (scoped) — same-player mutex grouping ──
    assign_mutex_groups(
        market_verified + final_approved_internal + model_qualified + conditional
    )

    # ── WOW-PATCH-2026-07-06 item 9 — DEGRADED_ENGINE_RUN gate ──────────────
    # Any backend fetch failure recorded in failed_modules means this run
    # cannot be trusted as a full Final WOW check: playable buckets are
    # cleared (moved to watch_list with an explicit degraded marker) and the
    # run is labeled so callers never mistake a partial/degraded run for a
    # complete one.
    run_status = "DEGRADED_ENGINE_RUN" if failed_modules else "COMPLETE"
    if failed_modules:
        execution_notes.append(
            f"DEGRADED_ENGINE_RUN — {len(failed_modules)} module(s) failed: "
            f"{'; '.join(failed_modules)}"
        )
        for _bucket_name, _bucket in (
            ("market_verified",         market_verified),
            ("final_approved_internal", final_approved_internal),
            ("model_qualified",         model_qualified),
        ):
            for _card in _bucket:
                _degraded = dict(_card)
                _degraded["final_approval_blocker"] = (
                    "DEGRADED_ENGINE_RUN: backend fetch failure during this run — "
                    "not eligible for FINAL_APPROVED/MONEY_QUALIFIED until rerun"
                )
                _degraded["degraded_run_hold"] = True
                _degraded["postscan_invariant_downgraded_from"] = _bucket_name
                watch_list.append(_degraded)
            _bucket[:] = []

    # Sport coverage validation
    missing_sports = [s for s in requested_sports if s not in scanned_sports]
    scan_valid     = len(missing_sports) == 0

    if missing_sports:
        execution_notes.append(
            f"SCAN INCOMPLETE — missing sports: {', '.join(missing_sports)}"
        )
    else:
        execution_notes.append("SCAN COMPLETE — all requested sports had props")

    def _summarize_sources(prefix_map):
        out = {}
        for key, val in prefix_map.items():
            label = (
                "AVAILABLE"  if "AVAILABLE" in str(val) else
                "PARTIAL"    if "PARTIAL"   in str(val) else
                "FAILED"     if "FAILED"    in str(val) else
                "MISSING"    if "MISSING"   in str(val) else
                "NOT_CALLED"
            )
            out[key] = label
        return out

    total_final = len(market_verified) + len(final_approved_internal)

    return {
        "run_date":  run_date,
        "run_at":    run_at,
        "runtime_provenance":       runtime_provenance,
        "run_status":               run_status,
        "failed_modules":           failed_modules,
        "requested_sports":         requested_sports,
        "scanned_sports":           scanned_sports,
        "missing_sports":           missing_sports,
        "scan_valid":               scan_valid,
        "source_access_status":     _summarize_sources(source_access),
        "source_access_detail":     source_access,
        "market_verified":          market_verified,
        "final_approved_internal":  final_approved_internal,
        "model_qualified":          model_qualified,
        "conditional":              conditional,
        "watch":                    watch_list,
        "reject":                   reject_list,
        "data_insufficient":        data_insufficient,
        "no_play":                  no_play_list,
        "counts": {
            "market_verified":         len(market_verified),
            "final_approved_internal": len(final_approved_internal),
            "total_final_approved":    total_final,
            "model_qualified":         len(model_qualified),
            "playable_count":          total_final + len(model_qualified),
            "conditional":             len(conditional),
            "watch":                   len(watch_list),
            "reject":                  len(reject_list),
            "data_insufficient":       len(data_insufficient),
            "no_play":                 len(no_play_list),
        },
        "playable_card":   market_verified + final_approved_internal + model_qualified,
        "execution_notes": execution_notes,
    }


def _median_edge(median, line):
    if median is None or line is None or line == 0:
        return None
    return round((median - line) / line, 4)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="WOW Daily Scanner")
    parser.add_argument("--sports",  nargs="+", default=None)
    parser.add_argument("--env",     default="live", choices=["test", "live"])
    parser.add_argument("--limit",   type=int, default=50)
    args = parser.parse_args()
    print(f"[WOW Scanner] Starting at {datetime.now(timezone.utc).isoformat()}")
    result = run_scan(sports=args.sports, environment=args.env, limit_per_sport=args.limit)
    print(json.dumps(result, indent=2, default=str))
