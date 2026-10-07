"""Governed current-pregame feature hydration for NBA/WNBA team-event models.

This module builds the exact BASKETBALL_TEAM_EVENT_FEATURES_V2 vector from
settled sporting history strictly before the target event date. It does not
score, calibrate, rank, publish, or execute. Market data is never consumed.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

from basketball_specialist_pipeline import load_games
from basketball_team_event_specialist import (
    FEATURE_NAMES,
    FEATURE_SCHEMA_VERSION,
    MIN_PRIOR_GAMES,
)
from v17.nba_event_identity import resolve_nba_current_event_identity
from v17.wnba_spread_event_identity import resolve_wnba_current_event_identity

CAN_EXECUTE = False
SUPPORTED_SPORTS = ("NBA", "WNBA")


class BasketballForwardFeatureError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail or code


def _aware(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise BasketballForwardFeatureError("BASKETBALL_EVENT_TIME_INVALID") from exc
    if parsed.utcoffset() is None:
        raise BasketballForwardFeatureError("BASKETBALL_EVENT_TIME_INVALID")
    return parsed.astimezone(timezone.utc)


def build_basketball_forward_features(
    games: Sequence[Any],
    *,
    target_date: date,
    home_team_id: str,
    away_team_id: str,
) -> tuple[dict[str, float], dict[str, Any]]:
    """Build the exact fitted basketball feature vector from prior settled games."""
    home_id = str(home_team_id or "").strip()
    away_id = str(away_team_id or "").strip()
    if not home_id or not away_id or home_id == away_id:
        raise BasketballForwardFeatureError("BASKETBALL_TEAM_IDENTITY_INVALID")

    history: dict[str, list[tuple[date, int, int, bool]]] = {}
    for game in sorted(games, key=lambda g: (g.game_date, g.game_id)):
        if game.game_date >= target_date:
            continue
        history.setdefault(str(game.home_team_id), []).append(
            (game.game_date, int(game.home_score), int(game.away_score), bool(game.home_win))
        )
        history.setdefault(str(game.away_team_id), []).append(
            (game.game_date, int(game.away_score), int(game.home_score), not bool(game.home_win))
        )

    hh = history.get(home_id, [])
    ah = history.get(away_id, [])
    if len(hh) < MIN_PRIOR_GAMES or len(ah) < MIN_PRIOR_GAMES:
        raise BasketballForwardFeatureError(
            "BASKETBALL_FORWARD_HISTORY_INSUFFICIENT",
            f"need at least {MIN_PRIOR_GAMES} prior games per team; home={len(hh)} away={len(ah)}",
        )

    hrest = max(0, (target_date - hh[-1][0]).days - 1)
    arest = max(0, (target_date - ah[-1][0]).days - 1)
    values = {
        "home_win_rate_prior": sum(x[3] for x in hh) / len(hh),
        "away_win_rate_prior": sum(x[3] for x in ah) / len(ah),
        "home_point_diff_prior": sum(x[1] - x[2] for x in hh) / len(hh),
        "away_point_diff_prior": sum(x[1] - x[2] for x in ah) / len(ah),
        "home_rest_days_capped": float(min(hrest, 7)),
        "away_rest_days_capped": float(min(arest, 7)),
        "home_back_to_back": float(hrest == 0),
        "away_back_to_back": float(arest == 0),
    }
    if tuple(values) != tuple(FEATURE_NAMES):
        raise BasketballForwardFeatureError("BASKETBALL_FORWARD_FEATURE_SCHEMA_MISMATCH")

    return (
        {name: float(values[name]) for name in FEATURE_NAMES},
        {
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "feature_as_of": target_date.isoformat(),
            "home_prior_games": len(hh),
            "away_prior_games": len(ah),
            "home_latest_prior_game_date": hh[-1][0].isoformat(),
            "away_latest_prior_game_date": ah[-1][0].isoformat(),
            "market_features_used": False,
            "market_probability_used_as_model": False,
            "generic_reasoning_used_as_model": False,
            "can_execute": False,
        },
    )


def hydrate_current_basketball_features(
    db: Any,
    *,
    sport: str,
    event_start_time: str,
    home_team_alias: str,
    away_team_alias: str,
    event_id: str | None = None,
    nba_identity_resolver: Callable[..., Mapping[str, Any]] = resolve_nba_current_event_identity,
    wnba_identity_resolver: Callable[..., Mapping[str, Any]] = resolve_wnba_current_event_identity,
) -> dict[str, Any]:
    """Resolve exact current identity and build a leakage-safe fitted feature package."""
    normalized = str(sport or "").strip().upper()
    if normalized not in SUPPORTED_SPORTS:
        raise BasketballForwardFeatureError("BASKETBALL_SPORT_UNSUPPORTED", normalized)

    target = _aware(event_start_time)
    if normalized == "NBA":
        identity = dict(
            nba_identity_resolver(
                event_start_time=target.isoformat(),
                home_team_alias=str(home_team_alias),
                away_team_alias=str(away_team_alias),
            )
        )
    else:
        if not str(event_id or "").strip():
            raise BasketballForwardFeatureError("WNBA_EVENT_ID_REQUIRED")
        identity = dict(
            wnba_identity_resolver(
                event_id=str(event_id),
                event_start_time=target.isoformat(),
                home_team_id=str(home_team_alias),
                away_team_id=str(away_team_alias),
            )
        )

    home_team_id = str(identity.get("home_team_id") or "").strip()
    away_team_id = str(identity.get("away_team_id") or "").strip()
    canonical_event_id = str(identity.get("event_id") or "").strip()
    if not canonical_event_id or not home_team_id or not away_team_id:
        raise BasketballForwardFeatureError("BASKETBALL_CANONICAL_IDENTITY_INCOMPLETE")

    games = load_games(db, normalized)
    target_date = target.astimezone(ZoneInfo("America/New_York")).date()
    features, audit = build_basketball_forward_features(
        games,
        target_date=target_date,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
    )
    audit.update(
        {
            "sport": normalized,
            "official_event_id": canonical_event_id,
            "event_start_time_utc": target.isoformat(),
            "home_team_id": home_team_id,
            "away_team_id": away_team_id,
            "identity_provider": identity.get("identity_provider"),
            "identity_resolution": identity.get("identity_resolution"),
            "provider_event_alias": identity.get("provider_event_alias"),
        }
    )
    return {
        "status": "PASS",
        "sport": normalized,
        "official_event_id": canonical_event_id,
        "event_start_time_utc": target.isoformat(),
        "home_team_id": home_team_id,
        "away_team_id": away_team_id,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "features": features,
        "feature_audit": audit,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


__all__ = [
    "BasketballForwardFeatureError",
    "CAN_EXECUTE",
    "SUPPORTED_SPORTS",
    "build_basketball_forward_features",
    "hydrate_current_basketball_features",
]
