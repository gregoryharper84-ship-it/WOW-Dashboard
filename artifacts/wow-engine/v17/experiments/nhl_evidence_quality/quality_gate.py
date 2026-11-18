"""NHL moneyline collapse and player-prop source preflight; research only.

No network, source authentication, model fitting, probability publication, or execution.
Inputs must be obtained from the existing NHL event/official-result readers and frozen
pregame receipts; an input that claims to be official is NOT independently verified here.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import re
from typing import Any, Mapping, Sequence

CAN_EXECUTE = False
AUTOMATIC_PROMOTION = False
PROBABILITY_PUBLISHABLE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"

# Freshness limits are preflight defaults to test, not validated optimal cutoffs.
ROLE_MAX_AGE_MINUTES = {
    "OFFICIAL_FIXTURE": 24 * 60,
    "TEAM_SHOT_QUALITY": 36 * 60,
    "OPPONENT_DEFENSE_5V5": 36 * 60,
    "SPECIAL_TEAMS": 36 * 60,
    "REST_AND_AVAILABILITY": 3 * 60,
    "GOALIE_STARTERS": 90,
    "GOALIE_FORM": 36 * 60,
    "PLAYER_TOI_PP": 36 * 60,
    "PLAYER_OPPORTUNITY": 36 * 60,
    "OPPONENT_PROP_SUPPRESSION": 36 * 60,
    "LINE_ASSIGNMENT": 3 * 60,
    "PLAYER_AVAILABILITY": 3 * 60,
    "MARKET_OFFER": 60,
    "GOALIE_WORKLOAD": 36 * 60,
    "OPPONENT_SHOT_VOLUME": 36 * 60,
}
REQUIRED_ROLES = {
    "FULL_GAME_ML": frozenset({
        "OFFICIAL_FIXTURE", "TEAM_SHOT_QUALITY", "OPPONENT_DEFENSE_5V5",
        "SPECIAL_TEAMS", "REST_AND_AVAILABILITY", "GOALIE_STARTERS", "GOALIE_FORM",
    }),
    "PLAYER_PROP": frozenset({
        "OFFICIAL_FIXTURE", "PLAYER_TOI_PP", "PLAYER_OPPORTUNITY",
        "OPPONENT_PROP_SUPPRESSION", "LINE_ASSIGNMENT",
        "PLAYER_AVAILABILITY", "GOALIE_STARTERS", "GOALIE_FORM",
        "SPECIAL_TEAMS", "REST_AND_AVAILABILITY", "MARKET_OFFER",
    }),
}
GOALIE_SAVES_ROLES = frozenset({
    "OFFICIAL_FIXTURE", "GOALIE_STARTERS", "GOALIE_FORM", "GOALIE_WORKLOAD",
    "OPPONENT_SHOT_VOLUME", "OPPONENT_DEFENSE_5V5", "REST_AND_AVAILABILITY",
    "MARKET_OFFER",
})
# Typed source-content checks. Source labels and hashes alone cannot establish
# a measurement exists. These are candidate research-contract units, not fitted
# coefficients or approved source entitlements.
ROLE_REQUIRED_METRICS = {
    "TEAM_SHOT_QUALITY": ("home_xgf_per60", "away_xgf_per60", "home_high_danger_for_per60", "away_high_danger_for_per60"),
    "OPPONENT_DEFENSE_5V5": ("home_xga_per60", "away_xga_per60", "home_high_danger_against_per60", "away_high_danger_against_per60"),
    "SPECIAL_TEAMS": ("home_pp_rate", "away_pp_rate", "home_pk_rate", "away_pk_rate"),
    "REST_AND_AVAILABILITY": ("home_rest_days", "away_rest_days"),
    "GOALIE_FORM": ("home_goalie_save_rate", "away_goalie_save_rate", "home_goalie_gsax_per60", "away_goalie_gsax_per60"),
    "OPPONENT_PROP_SUPPRESSION": ("opponent_allowed_stat_per60",),
}
RATE_METRICS = frozenset({"home_pp_rate", "away_pp_rate", "home_pk_rate", "away_pk_rate", "home_goalie_save_rate", "away_goalie_save_rate"})
PLAYER_ROLE_IDS = frozenset({"PLAYER_TOI_PP", "PLAYER_OPPORTUNITY", "LINE_ASSIGNMENT", "PLAYER_AVAILABILITY", "GOALIE_WORKLOAD", "MARKET_OFFER"})
PROP_TYPES = frozenset({"GOALS", "ASSISTS", "POINTS", "SHOTS_ON_GOAL", "GOALIE_SAVES"})
OPPORTUNITY_METRICS = {
    "GOALS": ("ixg_per60", "shot_attempts_per60"),
    "ASSISTS": ("assists_per60", "potential_assists_per60"),
    "POINTS": ("ixg_per60", "potential_assists_per60"),
    "SHOTS_ON_GOAL": ("sog_per60", "shot_attempts_per60"),
}


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None or dt.utcoffset() is None:
        return None
    return dt.astimezone(timezone.utc)


def _valid_id(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9]{4}02[0-9]{4}", value) is not None


def _hash(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-fA-F0-9]{64}", value) is not None


def _number(value: Any, *, minimum: float = 0) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        v = float(value)
    except (ValueError, OverflowError):
        return False
    return math.isfinite(v) and v >= minimum


def _result(status: str, **details: Any) -> dict[str, Any]:
    return {
        "status": status,
        "research_only": True,
        "can_execute": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "terminal_authority": TERMINAL_AUTHORITY,
        "source_authentication": "REQUIRES_INDEPENDENT_SERVER_RECEIPTS",
        **details,
    }


def screen_nhl_pregame_evidence(card: Mapping[str, Any]) -> dict[str, Any]:
    """Fail-closed source-coverage check before ranking NHL ML or exact-line props.

    Passing proves only that the caller supplied internally consistent claims.
    It cannot independently verify goalie confirmation, licensed reuse, provenance,
    line prices, or production model certification.
    """
    blockers: set[str] = set()
    if not isinstance(card, Mapping):
        return _result("NHL_EVIDENCE_HOLD", blockers=["NHL_CARD_INVALID"])
    family = card.get("market_family")
    if not isinstance(family, str) or family not in REQUIRED_ROLES:
        blockers.add("NHL_MARKET_FAMILY_INVALID")
    if not _valid_id(card.get("official_event_id")):
        blockers.add("NHL_OFFICIAL_EVENT_ID_INVALID")
    start, snapshot = _utc(card.get("event_start_utc")), _utc(card.get("snapshot_at_utc"))
    if start is None or snapshot is None or snapshot >= start:
        blockers.add("NHL_PREGAME_CHRONOLOGY_INVALID")
    home, away = card.get("home"), card.get("away")
    if (not isinstance(home, str) or not re.fullmatch(r"[A-Z]{3}", home)
            or not isinstance(away, str) or not re.fullmatch(r"[A-Z]{3}", away)
            or home == away):
        blockers.add("NHL_CARD_OPPONENT_IDENTITY_INVALID")
    if not isinstance(card.get("pregame_receipt_id"), str) or not card["pregame_receipt_id"].strip():
        blockers.add("NHL_PREGAME_RECEIPT_MISSING")
    if not _hash(card.get("pregame_source_hash")):
        blockers.add("NHL_PREGAME_SOURCE_HASH_INVALID")
    if family == "FULL_GAME_ML":
        if card.get("predicted_winner") not in (home, away) or not isinstance(card.get("predicted_winner"), str):
            blockers.add("NHL_ML_SELECTED_SIDE_INVALID")
        if card.get("settlement_basis") != "FULL_GAME_INCLUDING_OT_SO":
            blockers.add("NHL_ML_SETTLEMENT_BASIS_INVALID")
    if family == "PLAYER_PROP":
        if (not isinstance(card.get("team"), str) or not isinstance(card.get("opponent"), str)
                or card.get("team") not in (home, away)
                or card.get("opponent") not in (home, away)
                or card.get("team") == card.get("opponent")):
            blockers.add("NHL_PROP_OPPONENT_IDENTITY_INVALID")
        if not isinstance(card.get("stat_type"), str) or card.get("stat_type") not in PROP_TYPES or not (
            isinstance(card.get("player_id"), str) and card["player_id"].isdigit()
        ):
            blockers.add("NHL_PROP_IDENTITY_INVALID")
        if (not _number(card.get("exact_line")) or
                card.get("side") not in {"OVER", "UNDER"} or
                not isinstance(card.get("market_id"), str) or
                not card["market_id"].strip()):
            blockers.add("NHL_PROP_EXACT_MARKET_INVALID")

    if family == "PLAYER_PROP" and card.get("stat_type") == "GOALIE_SAVES":
        required = GOALIE_SAVES_ROLES
    elif isinstance(family, str):
        required = REQUIRED_ROLES.get(family, frozenset())
    else:
        required = frozenset()
    observed: set[str] = set()
    evidence = card.get("source_evidence")
    if not isinstance(evidence, (list, tuple)):
        blockers.add("NHL_EVIDENCE_LIST_INVALID")
        evidence = ()
    for item in evidence:
        if not isinstance(item, Mapping):
            blockers.add("NHL_EVIDENCE_ROW_INVALID")
            continue
        role = item.get("role")
        if not isinstance(role, str) or role not in ROLE_MAX_AGE_MINUTES:
            blockers.add("NHL_EVIDENCE_ROLE_INVALID")
            continue
        if role in observed:
            blockers.add("NHL_EVIDENCE_DUPLICATE_ROLE")
        observed.add(role)
        if (item.get("home") != home or item.get("away") != away):
            blockers.add("NHL_EVIDENCE_OPPONENT_IDENTITY_MISMATCH")
        if (item.get("official_event_id") != card.get("official_event_id")
                or not isinstance(item.get("provider"), str)
                or not item["provider"].strip()
                or not _hash(item.get("source_hash"))):
            blockers.add("NHL_EVIDENCE_SOURCE_IDENTITY_INVALID")
        as_of = _utc(item.get("as_of_utc"))
        captured = _utc(item.get("captured_at_utc"))
        if (as_of is None or captured is None or snapshot is None or start is None
                or as_of > captured or captured > snapshot or as_of >= start):
            blockers.add("NHL_EVIDENCE_TIME_INVALID")
        elif snapshot - as_of > timedelta(minutes=ROLE_MAX_AGE_MINUTES[role]):
            blockers.add("NHL_EVIDENCE_STALE_" + role)
        if family == "PLAYER_PROP" and role in PLAYER_ROLE_IDS:
            if item.get("player_id") != card.get("player_id"):
                blockers.add("NHL_EVIDENCE_PLAYER_IDENTITY_MISMATCH")
            if item.get("team") != card.get("team") or item.get("opponent") != card.get("opponent"):
                blockers.add("NHL_EVIDENCE_PLAYER_OPPONENT_MISMATCH")
        if role in ROLE_REQUIRED_METRICS:
            metrics = item.get("metrics")
            keys = ROLE_REQUIRED_METRICS[role]
            if (not isinstance(metrics, Mapping) or any(
                not _number(metrics.get(k), minimum=-10 if k.endswith("gsax_per60") else 0) or
                (k in RATE_METRICS and float(metrics[k]) > 1)
                for k in keys
            )):
                blockers.add("NHL_EVIDENCE_ROLE_METRICS_MISSING_OR_INVALID_" + role)
        if role == "GOALIE_STARTERS":
            if not all(isinstance(item.get(k), str) and item[k].isdigit() and int(item[k]) > 0
                       for k in ("home_goalie_id", "away_goalie_id")):
                blockers.add("NHL_EVIDENCE_GOALIE_IDS_MISSING")
        if role == "LINE_ASSIGNMENT" and family == "PLAYER_PROP":
            if not (isinstance(item.get("line_id"), str) and item["line_id"].strip()
                    and type(item.get("power_play_unit")) is int and 0 <= item["power_play_unit"] <= 2):
                blockers.add("NHL_EVIDENCE_LINE_ASSIGNMENT_INVALID")
        if role == "PLAYER_AVAILABILITY" and family == "PLAYER_PROP":
            if item.get("availability") != "CONFIRMED_ACTIVE":
                blockers.add("NHL_EVIDENCE_PLAYER_NOT_CONFIRMED_ACTIVE")
        if role == "PLAYER_OPPORTUNITY" and family == "PLAYER_PROP":
            fields = OPPORTUNITY_METRICS.get(card.get("stat_type")) if isinstance(card.get("stat_type"), str) else None
            metrics = item.get("metrics")
            if not isinstance(metrics, Mapping) or not fields or any(not _number(metrics.get(k)) for k in fields):
                blockers.add("NHL_PLAYER_OPPORTUNITY_METRICS_MISSING")
        if role == "PLAYER_TOI_PP" and family == "PLAYER_PROP":
            metrics = item.get("metrics")
            if not isinstance(metrics, Mapping) or any(
                not _number(metrics.get(k)) for k in ("toi_minutes_per_game", "pp_toi_minutes_per_game")
            ):
                blockers.add("NHL_PLAYER_USAGE_METRICS_MISSING")
        if role == "GOALIE_WORKLOAD" and family == "PLAYER_PROP":
            metrics = item.get("metrics")
            if not isinstance(metrics, Mapping) or any(
                not _number(metrics.get(k)) for k in ("shots_against_per60", "minutes_per_start")
            ):
                blockers.add("NHL_GOALIE_WORKLOAD_METRICS_MISSING")
        if role == "OPPONENT_SHOT_VOLUME" and family == "PLAYER_PROP":
            metrics = item.get("metrics")
            if not isinstance(metrics, Mapping) or not _number(metrics.get("opponent_sog_for_per60")):
                blockers.add("NHL_OPPONENT_SHOT_VOLUME_MISSING")
        if role == "MARKET_OFFER" and family == "PLAYER_PROP":
            if (not _number(item.get("price_decimal"), minimum=1.00001)
                    or item.get("offer_status") != "OPEN"
                    or item.get("player_id") != card.get("player_id")
                    or item.get("market_id") != card.get("market_id")
                    or item.get("exact_line") != card.get("exact_line")
                    or item.get("side") != card.get("side")
                    or not isinstance(item.get("book"), str) or not item["book"].strip()):
                blockers.add("NHL_EXACT_OFFER_SOURCE_MISMATCH")

    missing = sorted(required - observed)
    if missing:
        blockers.add("NHL_EVIDENCE_REQUIRED_ROLES_MISSING")
    # Official starter confirmation is controlled by the existing trusted provider
    # registry. Never infer 'confirmed' from a user-supplied source-provider label.
    if card.get("goalie_status") != "CONFIRMED" or not card.get("trusted_goalie_receipt_id"):
        blockers.add("NHL_TRUSTED_GOALIE_CONFIRMATION_NOT_PROVEN")
    return _result(
        "NHL_EVIDENCE_HOLD" if blockers else "NHL_RESEARCH_EVIDENCE_PRECHECK_PASSED",
        blockers=sorted(blockers),
        missing_roles=missing,
        observed_roles=sorted(observed),
        model_probability=None,
        permitted_action="RESEARCH_INPUT_REVIEW_ONLY",
    )


def audit_nhl_ml_loss_tails(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Grade already-frozen full-game winner picks against independently settled games.

    Research scorecard, not model calibration; 3+ goal losses are an observed
    severity measure, not a fitted pregame probability or a causal diagnosis.
    Refuse to compute a partial aggregate if any row has invalid provenance.
    """
    if not isinstance(rows, (list, tuple)) or not rows:
        return _result("NHL_ML_AUDIT_HOLD", blockers=["NHL_AUDIT_EMPTY"])
    problems: set[str] = set()
    seen: set[str] = set()
    games: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping) or not _valid_id(row.get("official_event_id")):
            problems.add("NHL_AUDIT_IDENTITY_INVALID")
            continue
        game_id = row["official_event_id"]
        if game_id in seen:
            problems.add("NHL_AUDIT_DUPLICATE_EVENT")
        seen.add(game_id)
        home, away, pick = row.get("home"), row.get("away"), row.get("predicted_winner")
        if not all(isinstance(v, str) and re.fullmatch(r"[A-Z]{3}", v) for v in (home, away, pick)) or home == away or pick not in (home, away):
            problems.add("NHL_AUDIT_TEAMS_INVALID")
            continue
        if (row.get("settlement_basis") != "FULL_GAME_INCLUDING_OT_SO"
                or row.get("settlement_source") != "INDEPENDENT_OFFICIAL"
                or not isinstance(row.get("settlement_receipt_id"), str)
                or not row["settlement_receipt_id"].strip()
                or not isinstance(row.get("forecast_receipt_id"), str)
                or not row["forecast_receipt_id"].strip()
                or not _hash(row.get("forecast_source_hash"))):
            problems.add("NHL_AUDIT_RECEIPT_INVALID")
        start, forecast, finalized = (_utc(row.get(k)) for k in (
            "event_start_utc", "forecast_at_utc", "finalized_at_utc"))
        if start is None or forecast is None or finalized is None or not (forecast < start <= finalized):
            problems.add("NHL_AUDIT_CHRONOLOGY_INVALID")
        hg, ag = row.get("home_goals"), row.get("away_goals")
        if (type(hg) is not int or type(ag) is not int or
                not (0 <= hg <= 50 and 0 <= ag <= 50) or hg == ag):
            problems.add("NHL_AUDIT_SCORE_INVALID")
            continue
        chosen, opponent = (hg, ag) if pick == home else (ag, hg)
        margin = chosen - opponent
        games.append({
            "official_event_id": game_id,
            "predicted_winner": pick,
            "won": margin > 0,
            "margin": margin,
            "loss_by_3plus": margin <= -3,
        })
    if problems:
        return _result("NHL_ML_AUDIT_HOLD", blockers=sorted(problems), games_graded=0)
    losses = [g for g in games if not g["won"]]
    severe = [g for g in losses if g["loss_by_3plus"]]
    return _result(
        "NHL_ML_LOSS_MARGIN_AUDIT_COMPLETE",
        games_graded=len(games),
        wins=len(games) - len(losses),
        losses=len(losses),
        losses_by_3plus=len(severe),
        severe_loss_share_of_losses=(len(severe) / len(losses) if losses else None),
        loss_margins=[-g["margin"] for g in losses],
        games=games,
        fitted_tail_probability=None,
        causal_attribution="NOT_ESTABLISHED",
        model_certification_change=False,
    )
