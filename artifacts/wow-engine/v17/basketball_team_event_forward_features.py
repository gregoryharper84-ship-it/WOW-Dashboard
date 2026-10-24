"""Governed current-pregame feature hydration for NBA/WNBA team-event models.

This module builds the exact BASKETBALL_TEAM_EVENT_FEATURES_V2 vector from
settled sporting history strictly before the target event date. It does not
score, calibrate, rank, publish, or execute. Market data is never consumed.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from hashlib import sha256
import json
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
    relevant_history: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for game in sorted(games, key=lambda g: (g.game_date, g.game_id)):
        if game.game_date >= target_date:
            continue
        game_home = str(game.home_team_id)
        game_away = str(game.away_team_id)
        # An unrelated club's result cannot affect this game's feature audit.
        if home_id not in (game_home, game_away) and away_id not in (game_home, game_away):
            continue
        gid = str(game.game_id or "").strip()
        if not gid or gid in seen_ids:
            raise BasketballForwardFeatureError("BASKETBALL_FORWARD_HISTORY_IDENTITY_DUPLICATE_OR_MISSING")
        seen_ids.add(gid)
        hscore, ascore = int(game.home_score), int(game.away_score)
        winner = bool(game.home_win)
        relevant_history.append({
            "game_id": gid,
            "game_date": game.game_date.isoformat(),
            "home_team_id": game_home,
            "away_team_id": game_away,
            "home_score": hscore,
            "away_score": ascore,
        })
        history.setdefault(game_home, []).append(
            (game.game_date, hscore, ascore, winner)
        )
        history.setdefault(game_away, []).append(
            (game.game_date, ascore, hscore, not winner)
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
            # Provenance/audit only: never a model input or approval receipt.
            "prior_history_sha256": sha256(
                json.dumps(relevant_history, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "prior_history_events": len(relevant_history),
            "home_history_age_days": (target_date - hh[-1][0]).days,
            "away_history_age_days": (target_date - ah[-1][0]).days,
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


def bind_nba_frozen_source_to_features(
    feature_package: Mapping[str, Any],
    *,
    capture_manifest: Mapping[str, Any],
    replay_receipt: Mapping[str, Any],
    feature_freeze_timestamp: str,
) -> dict[str, Any]:
    """Bind the *actual* NBA raw capture to a pregame historical feature vector.

    This is a structural source/feature join, not independent time attestation,
    certification, calibration, or authorization to publish a probability.
    Caller-provided freeze timestamps remain provisional until independently
    verified and durably persisted; market odds never enter the model vector.
    """
    if feature_package.get("status") != "PASS" or feature_package.get("sport") != "NBA":
        raise BasketballForwardFeatureError("NBA_FROZEN_FEATURE_PACKAGE_INVALID")
    if capture_manifest.get("sport") != "NBA" or capture_manifest.get("disposition") != "RAW_SOURCE_CAPTURE_ONLY":
        raise BasketballForwardFeatureError("NBA_RAW_SOURCE_CAPTURE_INVALID")
    digest = str(capture_manifest.get("raw_sha256") or "").strip().lower()
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise BasketballForwardFeatureError("NBA_RAW_SOURCE_SHA256_INVALID")
    if (
        replay_receipt.get("status") != "RAW_CAPTURE_HASH_AND_EVENT_REPLAY_PASS"
        or str(replay_receipt.get("raw_sha256") or "").lower() != digest
    ):
        raise BasketballForwardFeatureError("NBA_SOURCE_EVENT_REPLAY_NOT_VERIFIED")

    frozen_at = _aware(feature_freeze_timestamp)
    source_at = _aware(capture_manifest.get("source_http_date"))
    capture_at = _aware(capture_manifest.get("capture_host_clock_utc"))
    event_at = _aware(feature_package.get("event_start_time_utc"))
    if source_at > capture_at or capture_at > frozen_at or frozen_at >= event_at:
        raise BasketballForwardFeatureError("NBA_FROZEN_SOURCE_NOT_PREGAME")
    if abs((capture_at - source_at).total_seconds()) > 300:
        raise BasketballForwardFeatureError("NBA_SOURCE_CLOCK_SKEW_EXCEEDED")

    official_id = str(feature_package.get("official_event_id") or "").strip()
    home_alias = str(feature_package.get("home_team_id") or "").removeprefix("espn-")
    away_alias = str(feature_package.get("away_team_id") or "").removeprefix("espn-")
    matches = []
    eligible = capture_manifest.get("events_pregame_eligible")
    if not isinstance(eligible, list):
        raise BasketballForwardFeatureError("NBA_CAPTURE_EVENT_SET_INVALID")
    for item in eligible:
        if not isinstance(item, Mapping):
            raise BasketballForwardFeatureError("NBA_CAPTURE_EVENT_SET_INVALID")
        event_id = str(item.get("event_id") or "").strip()
        if event_id and f"espn-{event_id.removeprefix('espn-')}" == official_id:
            matches.append(item)
    if len(matches) != 1:
        raise BasketballForwardFeatureError("NBA_CAPTURE_CANONICAL_EVENT_NOT_UNIQUE")
    event = matches[0]
    sides = (event.get("home"), event.get("away"))
    if (
        any(not isinstance(side, Mapping) for side in sides)
        or str(sides[0].get("team_id") or "") != home_alias
        or str(sides[1].get("team_id") or "") != away_alias
        or _aware(event.get("event_start_utc")) != event_at
        or event.get("state") != "pre"
    ):
        raise BasketballForwardFeatureError("NBA_CAPTURE_EVENT_IDENTITY_MISMATCH")

    audit = feature_package.get("feature_audit")
    if not isinstance(audit, Mapping):
        raise BasketballForwardFeatureError("NBA_FEATURE_LINEAGE_MISSING")
    history_digest = str(audit.get("prior_history_sha256") or "")
    if len(history_digest) != 64 or any(ch not in "0123456789abcdef" for ch in history_digest):
        raise BasketballForwardFeatureError("NBA_FEATURE_LINEAGE_MISSING")
    bound_audit = dict(audit)
    bound_audit.update({
        "feature_timestamp": frozen_at.isoformat(),
        "dataset_id": "sha256:" + history_digest,
        "source_raw_sha256": digest,
        "source_event_id": official_id,
        "source_provenance": "ESPN_PUBLIC_SCOREBOARD_HTTP",
        "source_replay_status": "RAW_CAPTURE_HASH_AND_EVENT_REPLAY_PASS",
        "timestamp_attestation": "NOT_ESTABLISHED",
    })
    result = dict(feature_package)
    result.update({
        "source_snapshot_id": "sha256:" + digest,
        "source_snapshot_timestamp": source_at.isoformat(),
        "feature_audit": bound_audit,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    })
    return result


__all__ = [
    "BasketballForwardFeatureError",
    "CAN_EXECUTE",
    "SUPPORTED_SPORTS",
    "build_basketball_forward_features",
    "bind_nba_frozen_source_to_features",
    "hydrate_current_basketball_features",
]
