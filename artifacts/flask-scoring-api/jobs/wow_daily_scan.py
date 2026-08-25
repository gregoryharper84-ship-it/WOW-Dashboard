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
from collections import Counter
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.odds_api   import fetch_all_props, get_h2h_odds, SPORT_KEYS
from services.rundown    import fetch_backup_props
from services.player_logs import get_player_log_stats
from services.status     import get_injuries, get_player_injury_flag, get_mlb_probable_pitchers
from storage.results     import save_scan_result, get_scan_summary
from jobs.market_math import (
    no_vig_pair, pp_cash_threshold, compute_threshold_hit_rate,
    compute_drift_grade, classify_market_cause,
)
from gate_engine.scan_integrity import (
    MLB_1IP,
    MLB_UPSET_DISCOVERY,
    OUTRIGHT_WINNER,
    PLAYER_PROP,
    WNBA_PRA,
    build_objective_separation,
    build_scan_integrity_report,
    correlate_board_delta,
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


def _prop_scan_family(sport, row):
    raw = str(
        row.get("stat_key") or row.get("prop_type") or row.get("prop") or ""
    ).lower()
    if sport == "MLB" and (
        "1ip" in raw or "first_inning" in raw or "first inning" in raw
    ):
        return MLB_1IP
    if sport == "WNBA" and (
        "pra" in raw
        or "points_rebounds_assists" in raw
        or "points rebounds assists" in raw
    ):
        return WNBA_PRA
    return PLAYER_PROP


def _board_special_family_signals(board_rows):
    signals = {}
    for row in board_rows or []:
        if not isinstance(row, dict):
            continue
        sport = str(row.get("sport") or "").strip().upper()
        family = _prop_scan_family(sport, row)
        if family in (MLB_1IP, WNBA_PRA):
            signals.setdefault(sport, set()).add(family)
    return signals


def _moneyline_value(snapshot, primary, fallback):
    value = snapshot.get(primary)
    return value if value is not None else snapshot.get(fallback)


def _mlb_moneyline_discovery(events, upstream_status, run_date, expected_active_events=0):
    """
    Normalize live MLB h2h events and score both sides with the existing
    OUTRIGHT_WINNER specialist.  The market favorite and underdog are placed
    in separate, disjoint scan lanes; no generic model or approval path exists.
    """
    from gate_engine.moneyline.market_snapshot import (
        build_snapshot_from_odds_event,
        consensus_no_vig,
        snapshot_to_scorer_enrichment,
        snapshot_two_sided_gap,
    )
    from gate_engine.moneyline.team_acquisition import acquire_team_data
    from gate_engine.moneyline_probability import score_outright_winner_row

    from gate_engine.moneyline.pregame import pregame_exclusion_reason

    raw_events = [event for event in (events or []) if isinstance(event, dict)]
    inventory_by_family = {
        OUTRIGHT_WINNER: [],
        MLB_UPSET_DISCOVERY: [],
    }
    evaluated_by_family = Counter()
    terminal_by_family = Counter()
    qualifiers_by_family = Counter()
    provisional_by_family = Counter()
    candidates_by_family = {
        OUTRIGHT_WINNER: [],
        MLB_UPSET_DISCOVERY: [],
    }
    normalization_failures = []

    for event in raw_events:
        pregame_failure = pregame_exclusion_reason(event)
        if pregame_failure:
            normalization_failures.append({
                "event_id": str(event.get("id") or "").strip() or None,
                "reason": pregame_failure,
            })
            continue
        event_id = str(event.get("id") or "").strip()
        home = str(event.get("home_team") or "").strip()
        away = str(event.get("away_team") or "").strip()
        if not event_id or not home or not away:
            normalization_failures.append({
                "event_id": event_id or None,
                "reason": "H2H_EVENT_IDENTITY_INCOMPLETE",
            })
            continue

        snapshot = build_snapshot_from_odds_event(event, "MLB", market_key="h2h")
        missing_sides = snapshot_two_sided_gap(snapshot)
        home_market = consensus_no_vig(snapshot, home)
        away_market = consensus_no_vig(snapshot, away)
        if missing_sides or home_market is None or away_market is None:
            normalization_failures.append({
                "event_id": event_id,
                "reason": "H2H_TWO_SIDED_MARKET_UNAVAILABLE",
                "missing_participants": missing_sides,
            })
            continue

        # Ties are resolved deterministically so every usable event contributes
        # exactly one winner lane row and one upset lane row.
        favorite = home if home_market >= away_market else away
        participants = (
            (home, away, home_market),
            (away, home, away_market),
        )
        for team, opponent, market_probability in participants:
            family = OUTRIGHT_WINNER if team == favorite else MLB_UPSET_DISCOVERY
            slate_date = str(event.get("commence_time") or run_date)[:10] or run_date
            row = {
                "row_id": f"daily-scan:MLB:{event_id}:{team}",
                "sport": "MLB",
                "team": team,
                "opponent": opponent,
                "market_type": "h2h",
                "event_id": event_id,
                "slate_date": slate_date,
                "board_source": "odds_api_daily_scan",
                "market_family": OUTRIGHT_WINNER,
                "objective": "OUTRIGHT_WIN_PROBABILITY_ONLY",
                "input_contract_version": "MONEYLINE_V1",
                "scan_lane": family,
                "market_role": "FAVORITE" if family == OUTRIGHT_WINNER else "UNDERDOG",
                "market_probability": market_probability,
                "commence_time": event.get("commence_time"),
                "can_execute": False,
            }
            inventory_by_family[family].append(dict(row))

            # The canonical snapshot adapter is the only raw-market handoff
            # allowed into the specialist.  Preserve its normalized snapshot
            # alongside the scorer-ready odds for audit visibility.
            enrichment = snapshot_to_scorer_enrichment(snapshot)
            enrichment["market_snapshot"] = snapshot.to_dict()
            try:
                team_data = acquire_team_data(row, "MLB")
            except Exception as exc:
                team_data = None
                enrichment["team_acquisition_error"] = str(exc)
            if team_data:
                enrichment.update(team_data)

            evaluated_by_family[family] += 1
            try:
                scored = score_outright_winner_row(row, enrichment=enrichment)
            except Exception as exc:
                scored = {
                    "terminal_label": "DATA_CONTRACT_FAIL",
                    "blockers": [f"MONEYLINE_SPECIALIST_EXCEPTION:{exc}"],
                    "probability_snapshot": None,
                    "model_id": None,
                    "model_status": "UNAVAILABLE",
                    "can_execute": False,
                }
            terminal_by_family[family] += 1

            probability_snapshot = scored.get("probability_snapshot") or {}
            layers = probability_snapshot.get("moneyline_architecture_layers") or {}
            specialist_classification = layers.get("classification") or {}
            lower_bound = _moneyline_value(
                probability_snapshot,
                "calibrated_probability_lower_bound",
                "lower_bound",
            )
            candidate = {
                **row,
                "terminal_label": scored.get("terminal_label"),
                "blockers": list(scored.get("blockers") or []),
                "model_id": scored.get("model_id"),
                "model_status": scored.get("model_status"),
                "raw_probability": probability_snapshot.get("raw_probability"),
                "calibrated_probability": probability_snapshot.get("calibrated_probability"),
                "calibrated_probability_lower_bound": lower_bound,
                "lower_bound": lower_bound,
                "upper_bound": _moneyline_value(
                    probability_snapshot,
                    "calibrated_probability_upper_bound",
                    "upper_bound",
                ),
                "pure_edge": probability_snapshot.get("net_edge"),
                "probability_audit": probability_snapshot.get("probability_audit"),
                "specialist_classification": specialist_classification,
                "probability_snapshot": probability_snapshot or None,
                "can_execute": False,
                "can_approve_bets": False,
            }
            candidates_by_family[family].append(candidate)

            audit = probability_snapshot.get("probability_audit") or {}
            qualification_gate = specialist_classification.get("qualification_gate")
            if (
                lower_bound is not None
                and audit.get("passed") is True
                and qualification_gate != "TAIL_ONLY_REJECTED"
            ):
                qualifiers_by_family[family] += 1
            if lower_bound is None:
                provisional_by_family[family] += 1

    upstream_upper = str(upstream_status or "").upper()
    usable_events = sum(len(rows) for rows in inventory_by_family.values()) // 2
    active_events = max(len(raw_events), int(expected_active_events or 0))
    if "FAILED" in upstream_upper or "PARTIAL" in upstream_upper:
        acquisition_status = str(upstream_status)
    elif active_events > 0 and usable_events == 0:
        acquisition_status = (
            f"FAILED: 0/{active_events} MLB h2h events produced two-sided inventory"
        )
    elif usable_events < active_events:
        acquisition_status = (
            f"PARTIAL: {usable_events}/{active_events} MLB h2h events produced "
            "two-sided inventory"
        )
    else:
        acquisition_status = (
            f"AVAILABLE: {usable_events}/{active_events} MLB h2h events produced "
            "two-sided inventory"
        )

    source_by_family = {}
    for family in (OUTRIGHT_WINNER, MLB_UPSET_DISCOVERY):
        inventory_count = len(inventory_by_family[family])
        completed_count = sum(
            row.get("calibrated_probability_lower_bound") is not None
            for row in candidates_by_family[family]
        )
        if inventory_count == 0:
            scoring_status = "FAILED: no independently acquired h2h candidates"
        elif completed_count == 0:
            scoring_status = (
                f"FAILED: 0/{inventory_count} h2h candidates produced a probability"
            )
        elif completed_count < inventory_count:
            scoring_status = (
                f"PARTIAL: {completed_count}/{inventory_count} h2h candidates "
                "produced a probability"
            )
        else:
            scoring_status = (
                f"AVAILABLE: {completed_count}/{inventory_count} h2h candidates "
                "produced a probability"
            )
        source_by_family[family] = {
            "events": acquisition_status,
            "props": scoring_status,
            "backup": None,
        }

    return {
        "active_events": active_events,
        "upstream_status": upstream_status,
        "acquisition_status": acquisition_status,
        "inventory_by_family": inventory_by_family,
        "evaluated_by_family": dict(evaluated_by_family),
        "terminal_by_family": dict(terminal_by_family),
        "qualifiers_by_family": dict(qualifiers_by_family),
        "provisional_by_family": dict(provisional_by_family),
        "source_by_family": source_by_family,
        "candidates_by_family": candidates_by_family,
        "normalization_failures": normalization_failures,
        "can_execute": False,
        "dry_run_only": True,
    }


# -------------------------------------------------------------------
# Main scan function
# -------------------------------------------------------------------

def run_scan(
    sports=None,
    environment="live",
    limit_per_sport=50,
    runtime_provenance=None,
    _props_by_sport=None,
    _source_status_by_sport=None,
    _persist_results=True,
    board_rows=None,
    previous_board_rows=None,
    prior_evidence=None,
):
    """
    Run the WOW daily scan.

    Returns structured result dict including:
      requested_sports / scanned_sports / missing_sports / scan_valid
    """
    requested_sports = list(sports) if sports is not None else list(ALL_SPORTS)

    # An unverified runtime can only downgrade classifications.  This is kept
    # separate from coverage: integrity reports discovery completeness while
    # provenance governs whether a completed score may remain in a playable
    # presentation bucket.
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
    # Observational facts consumed after discovery by scan_integrity.  This
    # never controls the classifier or changes a probability calculation.
    sport_observations       = {}
    board_special_signals    = _board_special_family_signals(board_rows)
    moneyline_discovery      = {
        "candidates": [],
        "normalization_failures": [],
        "source_status": None,
        "can_execute": False,
        "dry_run_only": True,
    }

    for sport in requested_sports:
        execution_notes.append(f"--- {sport} ---")

        # Step 1-3: Props (Odds API primary, Rundown backup)
        # WOW-PATCH-2026-07-06 item 9: a raw fetch exception here previously
        # propagated out of run_scan uncaught (surfacing as a bare 500 to the
        # caller, e.g. a ClientResponseError from the upstream odds/rundown
        # HTTP client) instead of being recorded and labeled. Catch and
        # record it as a failed module so the run can be marked
        # DEGRADED_ENGINE_RUN instead of silently erroring out mid-scan.
        if _props_by_sport is not None:
            props = list(_props_by_sport.get(sport) or [])
            injected_source_status = (
                (_source_status_by_sport or {}).get(sport) or {}
            )
            primary_status = injected_source_status.get(
                f"{sport}_odds",
                "ORCHESTRATOR_CANONICAL_BOARD",
            )
            backup_status = injected_source_status.get(
                f"{sport}_rundown",
                "ORCHESTRATOR_CANONICAL_BOARD",
            )
            odds_status = {
                "events": "ORCHESTRATOR_CANONICAL_BOARD",
                "props": primary_status,
                "coverage_props": primary_status,
                "event_count": 0,
            }
            source_access[f"{sport}_odds"] = primary_status
            source_access[f"{sport}_rundown"] = backup_status
            for source_key, source_status in injected_source_status.items():
                source_status_text = str(source_status).upper()
                if (
                    source_status_text.startswith(
                        ("FAILED", "ERROR", "UNAVAILABLE", "PARTIAL")
                    )
                    or " FAILED" in source_status_text
                    or " PARTIAL" in source_status_text
                ):
                    failed_modules.append(
                        f"{sport}:orchestrator_source:{source_key}:{source_status}"
                    )
        else:
            try:
                props, odds_status = fetch_all_props(sport)
            except Exception as exc:
                failed_modules.append(f"{sport}:fetch_all_props:{exc}")
                source_access[f"{sport}_odds"] = f"FAILED: {exc}"
                props, odds_status = [], f"FAILED: {exc}"

        source_access[f"{sport}_odds"] = (
            odds_status.get("props", odds_status)
            if isinstance(odds_status, dict) else odds_status
        )
        events_status = odds_status.get("events") if isinstance(odds_status, dict) else None
        props_status = odds_status.get("props") if isinstance(odds_status, dict) else odds_status
        coverage_props_status = (
            odds_status.get("coverage_props", props_status)
            if isinstance(odds_status, dict) else props_status
        )
        event_count = (
            int(odds_status.get("event_count", 0) or 0)
            if isinstance(odds_status, dict) else 0
        )
        evaluated_rows = 0
        evaluated_by_family = Counter()
        terminal_by_family = Counter()
        qualifiers_by_family = Counter()
        provisional_by_family = Counter()

        if not props and _props_by_sport is None:
            try:
                rundown_props, rd_status = fetch_backup_props(sport)
            except Exception as exc:
                failed_modules.append(f"{sport}:fetch_backup_props:{exc}")
                rundown_props, rd_status = [], f"FAILED: {exc}"
            source_access[f"{sport}_rundown"] = rd_status
            if rundown_props:
                props = rundown_props
                execution_notes.append(
                    f"{sport}: using TheRundown backup ({len(props)} props)"
                )
            else:
                execution_notes.append(
                    f"{sport}: no props available from either source — MISSING"
                )
        elif _props_by_sport is None:
            source_access[f"{sport}_rundown"] = "NOT_CALLED: primary succeeded"
        else:
            source_access.setdefault(
                f"{sport}_rundown",
                "ORCHESTRATOR_CANONICAL_BOARD",
            )

        # WOW-PATCH-2026-07-06 item 4 (scoped) — cross-book consensus, used
        # for board_consensus_delta / no_vig_probability, and a SOURCE_CONFLICT
        # flag when bookmakers disagree on the same player/prop line by more
        # than 1.0. Full Layer-0 event reconciliation (team/opponent/game_id/
        # start_time identity matching across independent sources) is
        # deferred — see WOW-PATCH-2026-07-06 doc, "Deferred".
        consensus_map = build_consensus_map(props) if props else {}

        props = dedup_props(props) if props else []
        inventory_props = list(props)
        if event_count <= 0 and inventory_props:
            event_keys = {
                (
                    row.get("event_id"),
                    row.get("home_team"),
                    row.get("away_team"),
                    row.get("game_date"),
                )
                for row in inventory_props
            }
            event_count = max(1, len(event_keys))
        if (
            _props_by_sport is None
            and limit_per_sport is not None
            and len(props) > limit_per_sport
        ):
            props = props[:limit_per_sport]
        execution_notes.append(f"{sport}: {len(props)} unique props to evaluate")

        moneyline = None
        if sport == "MLB":
            try:
                h2h_events, h2h_status = get_h2h_odds(SPORT_KEYS["MLB"])
            except Exception as exc:
                failed_modules.append(f"MLB:get_h2h_odds:{exc}")
                h2h_events, h2h_status = [], f"FAILED: {exc}"
            source_access["MLB_h2h"] = h2h_status
            moneyline = _mlb_moneyline_discovery(
                h2h_events,
                h2h_status,
                run_date,
                expected_active_events=event_count,
            )
            moneyline_discovery = {
                "candidates": (
                    moneyline["candidates_by_family"][OUTRIGHT_WINNER]
                    + moneyline["candidates_by_family"][MLB_UPSET_DISCOVERY]
                ),
                "normalization_failures": moneyline["normalization_failures"],
                "source_status": moneyline["acquisition_status"],
                "event_count": moneyline["active_events"],
                "can_execute": False,
                "dry_run_only": True,
            }
            event_count = max(event_count, moneyline["active_events"])

        if not props:
            source_by_family = {}
            required_special_families = set(board_special_signals.get(sport, set()))
            if sport == "MLB" and event_count > 0:
                required_special_families.add(MLB_1IP)
            if sport == "WNBA" and event_count > 0:
                required_special_families.add(WNBA_PRA)
            for family in required_special_families:
                source_by_family[family] = {
                    "events": events_status,
                    "props": (
                        "FAILED: required active lane has no independent "
                        "source inventory or evaluation"
                    ),
                    "backup": source_access.get(f"{sport}_rundown"),
                }
            if moneyline:
                source_by_family.update(moneyline["source_by_family"])
            sport_observations[sport] = {
                "active_events": event_count,
                "events_status": events_status,
                # The aggregate status is diagnostic-only; legacy source_access
                # above intentionally retains the last-event scoring status.
                "props_status": coverage_props_status,
                "backup_status": source_access.get(f"{sport}_rundown"),
                "expected_families": sorted(required_special_families),
                "inventory": [],
                "inventory_by_family": (
                    moneyline["inventory_by_family"] if moneyline else {}
                ),
                "active_events_by_family": (
                    {
                        OUTRIGHT_WINNER: moneyline["active_events"],
                        MLB_UPSET_DISCOVERY: moneyline["active_events"],
                    } if moneyline else {}
                ),
                "source_by_family": source_by_family,
                "evaluated_rows": 0,
                "evaluated_by_family": (
                    moneyline["evaluated_by_family"] if moneyline else {}
                ),
                "terminal_outcomes": 0,
                "terminal_by_family": (
                    moneyline["terminal_by_family"] if moneyline else {}
                ),
                "qualifier_count": 0,
                "qualifiers_by_family": (
                    moneyline["qualifiers_by_family"] if moneyline else {}
                ),
                "provisional_refreshes": 0,
                "provisional_by_family": (
                    moneyline["provisional_by_family"] if moneyline else {}
                ),
            }
            scanned_sports.append(sport)
            continue

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
            evaluated_rows += 1
            scan_family = _prop_scan_family(sport, p)
            # A normalized prop belongs to exactly one coverage family.  This
            # leaves the legacy scorer untouched while preventing special
            # lanes from being counted again as generic player props.
            evaluated_by_family[scan_family] += 1
            terminal_by_family[scan_family] += 1

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
                        "scan_family": scan_family,
                        "can_execute": False,
                    })
                    provisional_by_family[scan_family] += 1
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

            runtime_provenance_hold = False
            if _prov_blocker is not None and classification in (
                "Market Verified Approved",
                "Final Approved — Internal Projection",
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
                "scan_family":            scan_family,
                "can_execute":            False,
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
                "scan_family":           scan_family,
                "can_execute":           False,
            }

            if classification == "Market Verified Approved":
                market_verified.append(card)
                qualifiers_by_family[scan_family] += 1
            elif classification == "Final Approved — Internal Projection":
                final_approved_internal.append(card)
                qualifiers_by_family[scan_family] += 1
            elif classification == "Model Qualified — PrizePicks":
                model_qualified.append(card)
                qualifiers_by_family[scan_family] += 1
            elif classification == "Conditional":
                conditional.append(card)
            elif classification == "Watch":
                watch_list.append(card)
                provisional_by_family[scan_family] += 1
            elif classification == "Data Insufficient":
                data_insufficient.append({**card, "reason": log_status})
                provisional_by_family[scan_family] += 1
            elif classification == "No Play":
                no_play_list.append(card)
            else:
                reject_list.append(card)
        inventory_by_family = {
            PLAYER_PROP: [
                row for row in inventory_props
                if _prop_scan_family(sport, row) == PLAYER_PROP
            ],
            MLB_1IP: [
                row for row in inventory_props
                if _prop_scan_family(sport, row) == MLB_1IP
            ],
            WNBA_PRA: [
                row for row in inventory_props
                if _prop_scan_family(sport, row) == WNBA_PRA
            ],
        }
        source_by_family = {
            PLAYER_PROP: {
                "events": events_status,
                # Preserve the complete event-level source observation for
                # coverage without changing legacy scoring's `props` status.
                "props": coverage_props_status,
                "backup": source_access.get(f"{sport}_rundown"),
            },
        }
        special_families = set(board_special_signals.get(sport, set()))
        if sport == "MLB" and event_count > 0:
            special_families.add(MLB_1IP)
        if sport == "WNBA" and event_count > 0:
            special_families.add(WNBA_PRA)
        for family in special_families:
            independently_observed = bool(inventory_by_family.get(family))
            source_by_family[family] = {
                "events": events_status,
                "props": (
                    coverage_props_status if independently_observed else
                    "FAILED: board-signaled lane has no independent source "
                    "inventory or evaluation"
                ),
                "backup": source_access.get(f"{sport}_rundown"),
            }

        active_events_by_family = {PLAYER_PROP: event_count}
        for family in special_families:
            active_events_by_family[family] = event_count
        if moneyline:
            inventory_by_family.update(moneyline["inventory_by_family"])
            source_by_family.update(moneyline["source_by_family"])
            active_events_by_family.update({
                OUTRIGHT_WINNER: moneyline["active_events"],
                MLB_UPSET_DISCOVERY: moneyline["active_events"],
            })
            evaluated_by_family.update(moneyline["evaluated_by_family"])
            terminal_by_family.update(moneyline["terminal_by_family"])
            qualifiers_by_family.update(moneyline["qualifiers_by_family"])
            provisional_by_family.update(moneyline["provisional_by_family"])

        sport_observations[sport] = {
            "active_events": event_count,
            "events_status": events_status,
            "props_status": coverage_props_status,
            "backup_status": source_access.get(f"{sport}_rundown"),
            "expected_families": sorted(special_families),
            "inventory": inventory_props,
            "inventory_by_family": inventory_by_family,
            "active_events_by_family": active_events_by_family,
            "source_by_family": source_by_family,
            "evaluated_rows": evaluated_by_family[PLAYER_PROP],
            "evaluated_by_family": dict(evaluated_by_family),
            "terminal_outcomes": terminal_by_family[PLAYER_PROP],
            "terminal_by_family": dict(terminal_by_family),
            "qualifier_count": qualifiers_by_family[PLAYER_PROP],
            "qualifiers_by_family": dict(qualifiers_by_family),
            "provisional_refreshes": provisional_by_family[PLAYER_PROP],
            "provisional_by_family": dict(provisional_by_family),
        }

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

    # Coverage is calculated from acquisition facts after all source and
    # normalization paths have finished.  It is intentionally independent of
    # row quality/classification and cannot promote a card.
    integrity_report = build_scan_integrity_report(requested_sports, sport_observations)
    reconciliation = integrity_report["reconciliation"]
    if not reconciliation["integrity_valid"]:
        execution_notes.append(
            "RUN_INTEGRITY_FAILURE — "
            f"unavailable sources: {', '.join(reconciliation['unavailable_source_lanes']) or 'none'}; "
            f"lane mismatch: {', '.join(reconciliation['duplicate_or_mismatched_lanes']) or 'none'}"
        )

    # ── WOW-PATCH-2026-07-06 item 9 — DEGRADED_ENGINE_RUN gate ──────────────
    # Any backend fetch failure recorded in failed_modules means this run
    # cannot be trusted as a full Final WOW check: playable buckets are
    # cleared (moved to watch_list with an explicit degraded marker) and the
    # run is labeled so callers never mistake a partial/degraded run for a
    # complete one.
    run_status = (
        "DEGRADED_ENGINE_RUN"
        if failed_modules or not reconciliation["integrity_valid"]
        else "COMPLETE"
    )
    if failed_modules or not reconciliation["integrity_valid"]:
        execution_notes.append(
            f"DEGRADED_ENGINE_RUN — {len(failed_modules)} module(s) failed; "
            f"integrity_valid={reconciliation['integrity_valid']}: "
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
    board_correlation = correlate_board_delta(
        board_rows, previous_board_rows, prior_evidence
    )
    all_candidate_rows = (
        market_verified + final_approved_internal + model_qualified + conditional
        + watch_list + reject_list + data_insufficient + no_play_list
        + moneyline_discovery["candidates"]
    )
    ranking_separation = build_objective_separation(all_candidate_rows)

    return {
        "run_date":  run_date,
        "run_at":    run_at,
        "runtime_provenance":       runtime_provenance,
        "run_status":               run_status,
        "can_execute":              False,
        "dry_run_only":             True,
        "failed_modules":           failed_modules,
        "requested_sports":         requested_sports,
        "scanned_sports":           scanned_sports,
        "missing_sports":           missing_sports,
        "scan_valid":               scan_valid,
        "scan_integrity":           integrity_report,
        "board_correlation":        board_correlation,
        "ranking_separation":       ranking_separation,
        # Probability-only h2h research stays visible independently of prop
        # approval buckets and remains dry-run only.
        "moneyline_discovery":      moneyline_discovery,
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
