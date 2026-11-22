"""Default, non-predictive sport-specific matchup review for every V17 event.

This is an evidence-coverage/audit layer, not a numerical model. Only sourced,
time-correct observations are marked screened; a check being requested is NOT a
verified fact. Do not pass these outputs as fitted-model features without the
controlling specialist's own source, fit, calibration and receipt validation.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from math import isfinite
from typing import Any

try:
    from v17.scout_source_policy import COMMON_SOURCE_RULES, evidence_quality
except ModuleNotFoundError:
    from scout_source_policy import COMMON_SOURCE_RULES, evidence_quality

SCHEMA_VERSION = "wow.v17.matchup_discovery.v1"
UNIVERSAL = {
    "BASELINE": ("opponent_adjusted_strength", "recent_form_vs_baseline", "pace_or_event_volume"),
    "PERSONNEL": ("availability_and_lineups", "usage_or_opportunity_shift", "depth_and_rotation"),
    "SITUATION": ("rest_travel_fatigue", "venue_home_away", "environmental_effects"),
    "GAME_SCRIPT": ("leading_trailing_behaviors", "blowout_or_early_finish", "correlation_and_tail_risk"),
    "MARKET": ("exact_current_quote", "opening_to_current_movement", "cross_book_comparison"),
}
PROFILES = {
    "FOOTBALL": ("ol_vs_dl_pressure", "neutral_pass_rate_and_tempo", "coverage_vs_receivers",
                 "red_zone_and_explosives", "wind_precipitation_surface"),
    "BASKETBALL": ("opponent_adjusted_possessions", "shot_zones_vs_allowed", "rebound_turnover_matchup",
                   "minutes_and_foul_rotation", "officials_foul_rates"),
    "BASEBALL": ("starting_pitcher_xfip_kbb_barrels", "handedness_and_platoon",
                 "bullpen_leverage_workload", "park_wind_and_roof", "umpire_strike_zone"),
    "HOCKEY": ("confirmed_starting_goalies", "goalie_gsax_and_workload", "five_on_five_xg_and_shots",
               "power_play_vs_penalty_kill", "score_effects_and_empty_net"),
    "SOCCER": ("xg_and_post_shot_xg", "press_vs_low_block_transitions",
               "starting_eleven_and_fixture_congestion", "referee_card_rates", "goalkeeper_form"),
    "COMBAT": ("striking_and_defense_matchup", "grappling_takedown_matchup",
               "cardio_by_round", "weigh_in_weight_cut", "short_notice_and_camp_changes"),
    "TENNIS": ("surface_adjusted_serve_return", "style_matchup", "fatigue_and_medical",
               "weather_and_court_conditions", "tiebreak_and_set_variance"),
    "GOLF": ("course_fit_strokes_gained", "approach_and_short_game",
             "weather_wave_and_tee_time", "course_setup_and_field_strength", "round_variance"),
    "CRICKET": ("format_and_overs", "batting_vs_bowling_style", "pitch_ground_boundary",
                "toss_and_dew_conditions", "bowling_workload_and_lineup"),
}
CRITICAL_BY_PROFILE = {
    "FOOTBALL": ("ol_vs_dl_pressure",),
    "BASKETBALL": ("opponent_adjusted_possessions", "minutes_and_foul_rotation"),
    "BASEBALL": ("starting_pitcher_xfip_kbb_barrels", "bullpen_leverage_workload"),
    "HOCKEY": ("confirmed_starting_goalies", "five_on_five_xg_and_shots"),
    "SOCCER": ("starting_eleven_and_fixture_congestion",),
    "COMBAT": ("weigh_in_weight_cut", "short_notice_and_camp_changes"),
    "TENNIS": ("surface_adjusted_serve_return", "fatigue_and_medical"),
    "GOLF": ("course_fit_strokes_gained", "weather_wave_and_tee_time"),
    "CRICKET": ("format_and_overs", "pitch_ground_boundary"),
}
CRITICAL_UNIVERSAL = ("opponent_adjusted_strength", "availability_and_lineups",
                      "rest_travel_fatigue", "leading_trailing_behaviors", "exact_current_quote")

SPORT_TO_PROFILE = {
    "NFL": "FOOTBALL", "NCAAF": "FOOTBALL", "CFB": "FOOTBALL",
    "NBA": "BASKETBALL", "WNBA": "BASKETBALL", "NCAAB": "BASKETBALL", "NCAAMB": "BASKETBALL",
    "MLB": "BASEBALL", "NHL": "HOCKEY", "SOCCER": "SOCCER", "MMA": "COMBAT",
    "UFC": "COMBAT", "BOXING": "COMBAT", "TENNIS": "TENNIS", "GOLF": "GOLF",
    "PGA": "GOLF", "CRICKET": "CRICKET",
}
FAMILY_TO_PROFILE = {
    "americanfootball": "FOOTBALL", "basketball": "BASKETBALL", "baseball": "BASEBALL",
    "icehockey": "HOCKEY", "soccer": "SOCCER", "mixedmartialarts": "COMBAT",
    "mma": "COMBAT", "boxing": "COMBAT", "tennis": "TENNIS", "golf": "GOLF",
    "cricket": "CRICKET",
}


def _aware(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.utcoffset() is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def profile_for(sport_key: str) -> str | None:
    key = str(sport_key or "").strip().upper()
    if key in SPORT_TO_PROFILE:
        return SPORT_TO_PROFILE[key]
    return FAMILY_TO_PROFILE.get(key.lower().split("_", 1)[0])


def feature_requirements(sport_key: str) -> dict[str, tuple[str, ...]]:
    profile = profile_for(sport_key)
    return {**UNIVERSAL, "SPORT_SPECIFIC": PROFILES.get(profile, ())}


def _metric_rows(candidate: dict[str, Any]) -> dict[str, Any]:
    """Reuse existing sourced research and same-event market rows when possible.

    Raw stats, odds and implied market probabilities never leave this screen.
    The metric names must already be supplied by a provider/research adapter:
    the audit does not fabricate metrics from team names or a bookmaker line.
    """
    raw = candidate.get("matchup_metric_evidence")
    if not isinstance(raw, dict):
        specific = candidate.get("sport_specific_evidence")
        raw = specific.get("matchup_metric_evidence") if isinstance(specific, dict) else None
    metrics = dict(raw) if isinstance(raw, dict) else {}

    # Existing research-domain evidence can carry typed feature observations.
    domains = candidate.get("research_domain_evidence")
    if isinstance(domains, dict):
        for domain_rows in domains.values():
            rows = domain_rows if isinstance(domain_rows, list) else [domain_rows]
            for evidence in rows:
                if not isinstance(evidence, dict):
                    continue
                named = evidence.get("matchup_metrics")
                if not isinstance(named, dict):
                    continue
                for name, value in named.items():
                    if name in metrics or value is None or value == {}:
                        continue
                    metrics[name] = {
                        "official_event_id": evidence.get("official_event_id"),
                        "home_team": evidence.get("home_team"),
                        "away_team": evidence.get("away_team"),
                        "source_id": evidence.get("source_id") or evidence.get("source_provider"),
                        "source_class": evidence.get("source_class"),
                        "captured_at": evidence.get("captured_at"),
                        "confirmed": evidence.get("confirmed") is True,
                        "value": value,
                    }

    # Reuse the existing event-scoped bookmaker feed only for a timestamped
    # *exact* quote. No opener/movement, price comparison or market probability
    # is inferred from one quote or from a line without its market identity.
    if "exact_current_quote" not in metrics:
        # Scout discovery may attach multiple bookmaker rows, while the
        # canonical snapshot adapter also supports one quote as a dict.
        # Treat both shapes identically; never iterate dict keys as quotes.
        raw_quotes = candidate.get("market_evidence")
        quotes = ([raw_quotes] if isinstance(raw_quotes, dict)
                  else raw_quotes if isinstance(raw_quotes, list) else [])
        for quote in quotes:
            if not isinstance(quote, dict):
                continue
            # An explicit candidate/quote identity mismatch cannot be
            # laundered into evidence under the candidate's event ID.
            quote_event = quote.get("official_event_id")
            if quote_event is not None and str(quote_event) != str(candidate.get("official_event_id") or ""):
                continue
            key = str(quote.get("market_key") or "").lower()
            if key not in ("h2h", "spreads", "totals") and not key.startswith(("player_", "pitcher_", "batter_")):
                continue
            if not (quote.get("bookmaker") and quote.get("outcome_name")
                    and quote.get("price") is not None):
                continue
            try:
                if not isfinite(float(quote["price"])):
                    continue
                if key != "h2h" and not isfinite(float(quote["point"])):
                    continue
            except (ValueError, TypeError, OverflowError, KeyError):
                continue
            if not (quote.get("market_last_update") or quote.get("bookmaker_last_update")):
                continue
            metrics.setdefault("exact_current_quote", []).append({
                "official_event_id": candidate.get("official_event_id"),
                "source_id": quote["bookmaker"],
                "source_class": "SPORTSBOOK_FEED",
                "captured_at": quote.get("market_last_update") or quote.get("bookmaker_last_update"),
                "confirmed": True,
                "value": {"market_key": key, "side": str(quote["outcome_name"])},
            })
    return metrics


HIGH_IMPACT_METRIC_TYPES = {
    "availability_and_lineups": "LINEUP_STATUS",
    "usage_or_opportunity_shift": "ROTATION_STATUS",
    "depth_and_rotation": "ROTATION_STATUS",
    "minutes_and_foul_rotation": "MINUTES_LIMIT",
    "confirmed_starting_goalies": "STARTING_GOALIE_STATUS",
    "starting_eleven_and_fixture_congestion": "LINEUP_STATUS",
    "short_notice_and_camp_changes": "STARTER_STATUS",
}


def _has_measurement(value: Any) -> bool:
    """An observed zero or False is valid; empty placeholders are not."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (dict, list, tuple, set)):
        return bool(value) and any(_has_measurement(v) for v in (
            value.values() if isinstance(value, dict) else value
        ))
    if isinstance(value, float):
        return isfinite(value)
    return True


def _conflicting_observations(rows: list[Any], checks: list[str], metric: str) -> bool:
    """Quarantine unreconciled same-tier disagreement about the same subject.

    Rows with explicitly distinct subjects (e.g., home and away goalie) are
    compared separately. Numeric disagreement is flagged for research review;
    this layer does not decide materiality or invent reconciliation.
    """
    if metric == "exact_current_quote":
        # Opposite book sides and differing prices are competing quotes,
        # not a contradictory player/lineup fact.
        return False
    observations: dict[tuple[str, str, str], str] = {}
    for row, check in zip(rows, checks):
        if check != "RESEARCH_SOURCE_SCREENED" or not isinstance(row, dict):
            continue
        tier = COMMON_SOURCE_RULES[str(row["source_class"]).upper()].trust_tier
        subject = str(row.get("subject_id") or row.get("participant_id")
                      or row.get("side") or "EVENT")
        measurement = row.get("value") if _has_measurement(row.get("value")) else row.get("values")
        items = measurement.items() if isinstance(measurement, dict) else (("", measurement),)
        for key, value in items:
            signature = (tier, subject, str(key))
            serialized = json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)
            prior = observations.setdefault(signature, serialized)
            if prior != serialized:
                return True
    return False


def _confirmed_goalie_pair(row: dict[str, Any]) -> bool:
    """Each official source must identify *both* starters by side."""
    values = row.get("values") or row.get("value")
    return (isinstance(values, dict)
            and _has_measurement(values.get("home"))
            and _has_measurement(values.get("away")))


def _screen_row(row: Any, candidate: dict[str, Any], event_start: datetime,
                now: datetime, metric: str) -> str:
    if not isinstance(row, dict):
        return "INVALID_EVIDENCE_SHAPE"
    official_id = str(candidate.get("official_event_id") or "")
    if not official_id or str(row.get("official_event_id") or "") != official_id:
        return "EVENT_ID_MISMATCH"
    for side in ("home_team", "away_team"):
        if row.get(side) is not None and str(row[side]) != str(candidate.get(side)):
            return "PARTICIPANT_MISMATCH"
    captured = _aware(row.get("captured_at"))
    if captured is None or captured > event_start or captured > now:
        return "TIMESTAMP_NOT_VALID_PREGAME"
    if row.get("conflict") is True:
        return "CONFLICT_QUARANTINED"
    if not str(row.get("source_id") or "").strip():
        return "SOURCE_ID_MISSING"
    source_class = str(row.get("source_class") or "").upper()
    if source_class not in COMMON_SOURCE_RULES:
        return "SOURCE_CLASS_INVALID"
    if not (_has_measurement(row.get("value")) or _has_measurement(row.get("values"))):
        return "MEASUREMENT_MISSING"
    age = (now - captured).total_seconds() / 60
    quality = evidence_quality(source_class, age_minutes=age,
                               confirmed=row.get("confirmed") is True,
                               evidence_type=HIGH_IMPACT_METRIC_TYPES.get(metric) or row.get("evidence_type"))
    if not quality["research_usable"]:
        return "STALE_OR_UNCONFIRMED_SOURCE"
    return "RESEARCH_SOURCE_SCREENED"


def discover_matchup(candidate: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Audit default review coverage; never compute edges, odds or probabilities."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    sport = str(candidate.get("sport_key") or candidate.get("sport") or "").strip()
    profile = profile_for(sport)
    event_start = _aware(candidate.get("commence_time") or candidate.get("event_start_utc")
                         or candidate.get("event_start_time_utc"))
    feature_groups = feature_requirements(sport)
    requested = tuple(dict.fromkeys(x for group in feature_groups.values() for x in group))
    required = tuple(dict.fromkeys((*CRITICAL_UNIVERSAL, *CRITICAL_BY_PROFILE.get(profile, ()))))
    evidence = _metric_rows(candidate)
    screened = []
    failures = {}
    for metric in requested:
        raw = evidence.get(metric)
        rows = raw if isinstance(raw, list) else [raw] if raw is not None else []
        if not rows:
            continue
        checks = [_screen_row(row, candidate, event_start, now, metric) for row in rows] if event_start else ["EVENT_TIME_MISSING"]
        if "CONFLICT_QUARANTINED" in checks or _conflicting_observations(rows, checks, metric):
            failures[metric] = "CONFLICT_QUARANTINED"
        elif metric == "confirmed_starting_goalies" and not any(
            check == "RESEARCH_SOURCE_SCREENED" for check in checks
        ):
            # Preserve the primary identity/freshness failure instead of
            # masking it behind a missing two-source goalie confirmation.
            failures[metric] = checks[0]
        elif metric == "confirmed_starting_goalies":
            # NHL research screening requires two distinct official providers.
            # A solitary beat report or projected lineup is not confirmation.
            official_sources = {
                str(row["source_id"]) for row, check in zip(rows, checks)
                if check == "RESEARCH_SOURCE_SCREENED"
                and str(row.get("source_class") or "").upper() in ("LEAGUE_OFFICIAL", "TEAM_OFFICIAL")
                and _confirmed_goalie_pair(row)
            }
            if len(official_sources) >= 2:
                screened.append(metric)
            else:
                failures[metric] = "TWO_INDEPENDENT_OFFICIAL_GOALIE_SOURCES_REQUIRED"
        elif "RESEARCH_SOURCE_SCREENED" in checks:
            screened.append(metric)
        else:
            failures[metric] = checks[0]
    missing = [m for m in requested if m not in screened]
    critical_missing = [m for m in required if m not in screened]
    if not candidate.get("official_event_id") or event_start is None:
        status = "EVENT_IDENTITY_OR_START_UNRESOLVED"
    elif event_start <= now:
        status = "PREGAME_WINDOW_CLOSED"
    elif any(v == "CONFLICT_QUARANTINED" for v in failures.values()):
        status = "CONFLICT_QUARANTINED"
    elif not screened:
        status = "MATCHUP_EVIDENCE_NOT_OBSERVED"
    elif missing:
        status = "PARTIAL_MATCHUP_REVIEW"
    else:
        status = "MATCHUP_RESEARCH_COVERED"
    return {
        "schema_version": SCHEMA_VERSION,
        "sport_key": sport,
        "profile": profile or "GENERIC_UNSUPPORTED",
        "official_event_id": candidate.get("official_event_id"),
        "review_status": status,
        "review_requested": True,
        "critical_feature_names": list(required),
        "feature_requirements": {k: list(v) for k, v in feature_groups.items()},
        "screened_features": screened,
        "missing_features": missing,
        "missing_critical_features": critical_missing,
        "invalid_or_stale_features": failures,
        "screened_count": len(screened),
        "requested_count": len(requested),
        "source_screening_is_not_independent_model_verification": True,
        "confidence_or_model_probability_created": False,
        "specialist_rescore_verified": False,
        "research_ceiling": "RESEARCH_INTEREST",
        "prediction_authority": False,
        "can_execute": False,
    }


__all__ = ["discover_matchup", "feature_requirements", "profile_for"]
