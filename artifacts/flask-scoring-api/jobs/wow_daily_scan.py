"""
WOW Daily Scanner — jobs/wow_daily_scan.py

Pipeline:
  1. Pull today's events (Odds API primary, TheRundown backup)
  2. Pull player prop markets per event
  3. Pull ESPN player game logs
  4. Calculate L5/L10 hit rate, median, average
  5. Pull injury / status / lineup data
  6. Score each prop via compute_wow_score()
  7. Classify: Market Verified Approved / Model Qualified / Conditional /
               Watch / Reject / Data Insufficient / No Play
  8. Save all results to scan_results table
  9. Return structured JSON output

Can be run standalone:  python jobs/wow_daily_scan.py
Or called via API:      POST /wow-daily-scan
"""

import sys
import os
import json
from datetime import date, datetime, timezone

# Allow running from repo root or from within jobs/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.odds_api  import fetch_all_props, SPORT_KEYS
from services.rundown   import fetch_backup_props
from services.player_logs import get_player_log_stats
from services.status    import get_injuries, get_player_injury_flag, get_mlb_probable_pitchers
from storage.results    import save_scan_result, get_scan_summary

# Import scoring from parent app.py path
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

# -------------------------------------------------------------------
# Classification logic
# -------------------------------------------------------------------

def classify_prop(wow_score, signal, log_status, inj_flag, sources):
    """
    WOW v14.8+ classification rules.

    Market Verified Approved : score >= 75, injury OK, odds source AVAILABLE
    Model Qualified          : score >= 65, logs available
    Conditional              : score >= 55, partial data
    Watch                    : score >= 45
    Reject                   : score < 45 OR injury_flag >= 2
    Data Insufficient        : required sources failed / missing logs
    No Play                  : full pathway attempted, nothing passed
    """
    odds_ok  = "AVAILABLE" in (sources.get("odds",  "") or "")
    logs_ok  = "AVAILABLE" in (log_status or "")
    inj_ok   = inj_flag < 2

    if not inj_ok:
        return "Reject"

    if wow_score >= 75 and odds_ok and inj_ok:
        return "Market Verified Approved"

    if wow_score >= 65 and logs_ok:
        return "Model Qualified — PrizePicks"

    if wow_score >= 55:
        return "Conditional"

    if wow_score >= 45:
        return "Watch"

    # Check if we truly tried everything
    any_source_available = any(
        "AVAILABLE" in str(v) for v in sources.values()
    )
    if not any_source_available:
        return "Data Insufficient"

    if wow_score < 45:
        return "Reject"

    return "No Play"


# -------------------------------------------------------------------
# Dedup props (same player/prop/side/line from multiple bookmakers)
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
    Run the WOW daily scan for the given sports list.
    Returns the full structured result dict.
    """
    if sports is None:
        sports = ALL_SPORTS

    run_date = date.today().isoformat()
    run_at   = datetime.now(timezone.utc).isoformat()

    source_access = {}
    market_verified  = []
    model_qualified  = []
    conditional      = []
    watch_list       = []
    reject_list      = []
    data_insufficient = []
    no_play_list     = []
    execution_notes  = []

    for sport in sports:
        execution_notes.append(f"--- {sport} ---")

        # -----------------------------------------------------------
        # Step 1-3: Pull props (Odds API primary, Rundown backup)
        # -----------------------------------------------------------
        props, odds_status = fetch_all_props(sport)
        source_access[f"{sport}_odds"] = odds_status.get("props", odds_status) if isinstance(odds_status, dict) else odds_status

        if not props:
            rundown_props, rd_status = fetch_backup_props(sport)
            source_access[f"{sport}_rundown"] = rd_status
            if rundown_props:
                props = rundown_props
                execution_notes.append(f"{sport}: using TheRundown backup ({len(props)} props)")
            else:
                execution_notes.append(f"{sport}: no props available from either source")
                continue
        else:
            source_access[f"{sport}_rundown"] = "NOT_CALLED: primary succeeded"

        props = dedup_props(props)
        if len(props) > limit_per_sport:
            props = props[:limit_per_sport]
        execution_notes.append(f"{sport}: {len(props)} unique props to evaluate")

        # -----------------------------------------------------------
        # Step 7: Pull injury/status data once per sport
        # -----------------------------------------------------------
        injuries_cache, inj_source = get_injuries(sport)
        source_access[f"{sport}_status"] = inj_source

        # MLB probable pitchers
        mlb_pitchers = {}
        if sport == "MLB":
            mlb_pitchers, _ = get_mlb_probable_pitchers(run_date)

        # -----------------------------------------------------------
        # Step 5-8: For each prop — logs, score, classify, save
        # -----------------------------------------------------------
        for p in props:
            player    = p.get("player", "")
            prop_key  = p.get("prop", "")
            side      = p.get("side", "MORE")
            line      = float(p.get("line", 0))
            game_date = p.get("game_date", run_date)

            if not player or not prop_key or line <= 0:
                continue

            # Step 5-6: Game logs + hit stats
            log_stats, log_status = get_player_log_stats(sport, player, prop_key, line, side)
            source_access_log_key = f"{sport}_logs"
            prev = source_access.get(source_access_log_key, "")
            # Promote to AVAILABLE if any player succeeded; keep best status
            if "AVAILABLE" in log_status:
                source_access[source_access_log_key] = log_status
            elif "AVAILABLE" not in prev:
                source_access[source_access_log_key] = log_status

            # Step 7: Injury flag
            inj_flag, inj_raw, _ = get_player_injury_flag(
                sport, player, injuries_cache=injuries_cache
            )

            # MLB: skip if not a probable pitcher for pitching props
            if sport == "MLB" and "pitcher" in prop_key:
                if mlb_pitchers and player.lower() not in mlb_pitchers:
                    data_insufficient.append({
                        "player": player, "sport": sport, "prop": prop_key,
                        "side": side, "line": line,
                        "reason": "not listed as probable pitcher",
                    })
                    continue

            # Step 8: Compute WOW score
            features = {
                "l5_hit_rate":    log_stats.get("l5_hit_rate"),
                "l10_hit_rate":   log_stats.get("l10_hit_rate"),
                "recent_avg":     log_stats.get("l10_avg"),
                "median_edge":    _median_edge(log_stats.get("l10_median"), line),
                "injury_flag":    inj_flag,
            }
            features = {k: v for k, v in features.items() if v is not None}

            wow_score, signal, message = compute_wow_score(features, player, prop_key, side, line)

            # Classify
            classification = classify_prop(
                wow_score, signal, log_status, inj_flag,
                {"odds": source_access.get(f"{sport}_odds", "NOT_CALLED")}
            )

            # Build result record
            result_row = {
                "run_date":       run_date,
                "sport":          sport,
                "player":         player,
                "prop":           prop_key,
                "line":           line,
                "side":           side,
                "game_date":      game_date,
                "wow_score":      wow_score,
                "signal":         signal,
                "message":        message,
                "classification": classification,
                "environment":    environment,
                "source_odds":    source_access.get(f"{sport}_odds",    "NOT_CALLED"),
                "source_rundown": source_access.get(f"{sport}_rundown", "NOT_CALLED"),
                "source_logs":    log_status,
                "source_status":  inj_source,
                "l5_hit_rate":    log_stats.get("l5_hit_rate"),
                "l10_hit_rate":   log_stats.get("l10_hit_rate"),
                "l10_median":     log_stats.get("l10_median"),
                "l10_avg":        log_stats.get("l10_avg"),
                "raw_features":   features,
                "notes":          f"injury={inj_raw}; games_found={log_stats.get('games_found', 0)}",
            }
            save_scan_result(result_row)

            # Summary card (lightweight)
            card = {
                "player": player, "sport": sport, "prop": prop_key,
                "side": side, "line": line, "game_date": game_date,
                "wow_score": wow_score, "signal": signal,
            }

            if classification == "Market Verified Approved":
                market_verified.append(card)
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

    # -----------------------------------------------------------
    # Build source access status summary
    # -----------------------------------------------------------
    def _summarize_sources(prefix_map):
        out = {}
        for key, val in prefix_map.items():
            label = "AVAILABLE" if "AVAILABLE" in str(val) else \
                    "PARTIAL"   if "PARTIAL"   in str(val) else \
                    "FAILED"    if "FAILED"    in str(val) else \
                    "MISSING"   if "MISSING"   in str(val) else \
                    "NOT_CALLED"
            out[key] = label
        return out

    return {
        "run_date":  run_date,
        "run_at":    run_at,
        "sports_scanned": sports,
        "source_access_status": _summarize_sources(source_access),
        "source_access_detail": source_access,
        "market_verified":    market_verified,
        "model_qualified":    model_qualified,
        "conditional":        conditional,
        "watch":              watch_list,
        "reject":             reject_list,
        "data_insufficient":  data_insufficient,
        "no_play":            no_play_list,
        "counts": {
            "market_verified":    len(market_verified),
            "model_qualified":    len(model_qualified),
            "conditional":        len(conditional),
            "watch":              len(watch_list),
            "reject":             len(reject_list),
            "data_insufficient":  len(data_insufficient),
            "no_play":            len(no_play_list),
        },
        "execution_notes": execution_notes,
    }


def _median_edge(median, line):
    """Convert median vs line into a 0-1 normalised edge score."""
    if median is None or line is None or line == 0:
        return None
    return round((median - line) / line, 4)


# -------------------------------------------------------------------
# Standalone entry point
# -------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="WOW Daily Scanner")
    parser.add_argument("--sports",  nargs="+", default=None,
                        help="Sports to scan (default: all)")
    parser.add_argument("--env",     default="live",
                        choices=["test", "live"])
    parser.add_argument("--limit",   type=int, default=50,
                        help="Max props per sport")
    args = parser.parse_args()

    print(f"[WOW Scanner] Starting scan at {datetime.now(timezone.utc).isoformat()}")
    result = run_scan(
        sports=args.sports,
        environment=args.env,
        limit_per_sport=args.limit,
    )
    print(json.dumps(result, indent=2, default=str))
