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
import statistics as _stats
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.odds_api   import fetch_all_props, SPORT_KEYS
from services.rundown    import fetch_backup_props
from services.player_logs import get_player_log_stats
from services.status     import get_injuries, get_player_injury_flag, get_mlb_probable_pitchers
from storage.results     import save_scan_result, get_scan_summary

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
        }

    # Base anchor: L10 median preferred for stability
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

    return {
        "projection_status":      "INTERNAL",
        "projection_value":       projection_value,
        "projection_margin":      margin,
        "projection_source":      "internal_l10_model",
        "final_approval_blocker": blocker,
    }


# -------------------------------------------------------------------
# Classification logic (v14.9.1)
# -------------------------------------------------------------------

def classify_prop(
    wow_score, signal, log_status, inj_flag, sources,
    raw_l5=None, raw_l10=None,
    manual_fallback_used=False, environment="live",
    projection_data=None,
):
    """
    WOW v14.9.1 — returns (classification_label, final_approval_blocker | None).

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
    """
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

    # --- Hard reject: injury ---
    if not inj_ok:
        return "Reject", f"injury_flag={inj_flag} (>= 2)"

    # --- Final Approval tier ---
    if wow_score >= 75 and inj_ok and raw_logs_ok and not live_manual_block and proj_ok:
        if odds_ok and proj_status == "EXTERNAL":
            # Full external verification — highest label
            return "Market Verified Approved", None
        # Internal projection (may still have odds_ok = True)
        return "Final Approved — Internal Projection", None

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
        return "Model Qualified — PrizePicks", blocker
    if wow_score >= 55:
        return "Conditional", blocker
    if wow_score >= 45:
        return "Watch", blocker

    any_source = any("AVAILABLE" in str(v) for v in sources.values())
    if not any_source:
        return "Data Insufficient", blocker

    return "Reject", f"score={wow_score} < 45"


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
# Main scan function
# -------------------------------------------------------------------

def run_scan(sports=None, environment="live", limit_per_sport=50):
    """
    Run the WOW daily scan.

    Returns structured result dict including:
      requested_sports / scanned_sports / missing_sports / scan_valid
    """
    requested_sports = list(sports) if sports is not None else list(ALL_SPORTS)

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

    for sport in requested_sports:
        execution_notes.append(f"--- {sport} ---")

        # Step 1-3: Props (Odds API primary, Rundown backup)
        props, odds_status = fetch_all_props(sport)
        source_access[f"{sport}_odds"] = (
            odds_status.get("props", odds_status)
            if isinstance(odds_status, dict) else odds_status
        )

        if not props:
            rundown_props, rd_status = fetch_backup_props(sport)
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
                continue
        else:
            source_access[f"{sport}_rundown"] = "NOT_CALLED: primary succeeded"

        props = dedup_props(props)
        if len(props) > limit_per_sport:
            props = props[:limit_per_sport]
        execution_notes.append(f"{sport}: {len(props)} unique props to evaluate")
        scanned_sports.append(sport)

        injuries_cache, inj_source = get_injuries(sport)
        source_access[f"{sport}_status"] = inj_source

        mlb_pitchers = {}
        if sport == "MLB":
            mlb_pitchers, _ = get_mlb_probable_pitchers(run_date)

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
            classification, final_approval_blocker = classify_prop(
                wow_score, signal, log_status, inj_flag,
                {"odds": source_access.get(f"{sport}_odds", "NOT_CALLED")},
                raw_l5=raw_l5,
                raw_l10=raw_l10,
                manual_fallback_used=manual_fallback_used,
                environment=environment,
                projection_data=projection_data,
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

            result_row = {
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
            }
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
                "final_approval_blocker": final_approval_blocker,
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
