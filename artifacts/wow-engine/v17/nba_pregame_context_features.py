"""Research-only NBA pregame context/availability feature derivation.

The fitted basketball team-event vector carries only team win rate, point
differential and rest. This module derives the additional pre-registered
pregame factors from *server-owned* evidence so a future fitted model can be
trained and calibrated on them:

- minutes-weighted player value and availability minutes share
- replacement-quality delta for unavailable/limited rotation players
- lineup continuity against the last verified starting five
- opponent-adjusted shot quality
- pace/profile tactical matchup distances
- travel/rest fatigue components
- scoring (margin) variance

It emits an immutable evidence receipt with typed blockers when any required
pregame input is missing, stale, malformed or chronologically impossible.

This module never scores, calibrates, ranks, publishes, persists or executes.
Its output is NOT a member of the fitted BASKETBALL_TEAM_EVENT_FEATURES_V2
vector; extending that vector requires a separate governed model release.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import re
from typing import Any, Mapping

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
RESEARCH_ONLY = True
SUPPORTED_SPORT = "NBA"
REGULATION_ROTATION_MINUTES = 240.0
MIN_MARGIN_OBSERVATIONS = 5
PLAYER_STATUSES = frozenset({"AVAILABLE", "PROBABLE", "QUESTIONABLE", "OUT"})
CONTEXT_FEATURE_NAMES = (
    "minutes_weighted_player_value",
    "availability_minutes_share",
    "replacement_quality_delta",
    "lineup_continuity",
    "opponent_adjusted_shot_quality",
    "pace",
    "pace_style_mismatch",
    "rest_days",
    "travel_miles_7d",
    "time_zone_shift_hours",
    "back_to_back",
    "recent_margin_variance",
)
_SIDES = ("home", "away")
_EVENT_ID = re.compile(r"espn-[0-9]{6,12}")
_TEAM_ID = re.compile(r"espn-[0-9]+")
_PLAYER_ID = re.compile(r"[1-9][0-9]{0,9}")
_MINUTES_EPSILON = 1e-6


def _aware(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    converted = float(value)
    return converted if math.isfinite(converted) else None


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _held(
    blockers: list[str],
    *,
    identity: Mapping[str, Any] | None = None,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    identity = identity if isinstance(identity, Mapping) else {}
    receipt = {
        "status": "HELD",
        "code": "MODEL_INPUTS_INSUFFICIENT",
        "typed_failure": "MODEL_INPUTS_INSUFFICIENT",
        "blockers": sorted(set(blockers)),
        "sport": SUPPORTED_SPORT,
        "official_event_id": identity.get("official_event_id"),
        "home_team_id": identity.get("home_team_id"),
        "away_team_id": identity.get("away_team_id"),
        "source_snapshot_id": identity.get("source_snapshot_id"),
        "source_snapshot_timestamp": identity.get("source_snapshot_timestamp"),
        "feature_timestamp": as_of.isoformat() if as_of is not None else None,
        "features": None,
        "evidence_sha256": None,
        "source_evidence": None,
        "research_only": RESEARCH_ONLY,
        "fitted_feature_vector_extension": False,
        "calibration_status": "NOT_CALIBRATED_RESEARCH_ONLY",
        "probability_publishable": PROBABILITY_PUBLISHABLE,
        "rank_eligible": False,
        "can_execute": False,
    }
    return receipt


def _rotation_features(
    players: Any,
    side: str,
    blockers: list[str],
) -> dict[str, float] | None:
    entries = players if isinstance(players, list) else None
    if not entries:
        blockers.append(f"NBA_CONTEXT_{side.upper()}_ROTATION_MISSING")
        return None

    rotation_ids = {
        str(_mapping(entry).get("player_id") or "").strip()
        for entry in entries
        if _mapping(entry) is not None
    }
    seen: set[str] = set()
    planned_total = 0.0
    expected_total = 0.0
    weighted_value = 0.0
    replacement_delta_numerator = 0.0
    projected_starters = 0
    retained_starters = 0
    valid = True

    for entry in entries:
        player = _mapping(entry)
        if player is None:
            blockers.append(f"NBA_CONTEXT_{side.upper()}_ROTATION_ENTRY_INVALID")
            valid = False
            continue
        player_id = str(player.get("player_id") or "").strip()
        if not _PLAYER_ID.fullmatch(player_id):
            blockers.append(f"NBA_CONTEXT_{side.upper()}_PLAYER_ID_INVALID")
            valid = False
            continue
        if player_id in seen:
            blockers.append(f"NBA_CONTEXT_{side.upper()}_PLAYER_ID_DUPLICATE")
            valid = False
            continue
        seen.add(player_id)

        value = _num(player.get("player_value"))
        planned = _num(player.get("planned_minutes"))
        expected = _num(player.get("expected_minutes"))
        status = str(player.get("status") or "").strip().upper()
        projected_starter = player.get("projected_starter")
        prior_game_starter = player.get("prior_game_starter")
        if (
            value is None
            or planned is None
            or expected is None
            or planned <= 0
            or expected < 0
            or expected > planned + _MINUTES_EPSILON
            or status not in PLAYER_STATUSES
            or not isinstance(projected_starter, bool)
            or not isinstance(prior_game_starter, bool)
        ):
            blockers.append(f"NBA_CONTEXT_{side.upper()}_ROTATION_ENTRY_INVALID")
            valid = False
            continue
        if status == "OUT" and expected > 0:
            blockers.append(f"NBA_CONTEXT_{side.upper()}_OUT_PLAYER_HAS_MINUTES")
            valid = False

        planned_total += planned
        expected_total += expected
        weighted_value += expected * value
        if projected_starter:
            projected_starters += 1
            if prior_game_starter:
                retained_starters += 1

        missing_minutes = planned - expected
        if missing_minutes <= _MINUTES_EPSILON:
            continue
        replacement = _mapping(player.get("replacement"))
        if replacement is None:
            blockers.append(f"NBA_CONTEXT_{side.upper()}_REPLACEMENT_MISSING")
            valid = False
            continue
        replacement_id = str(replacement.get("player_id") or "").strip()
        replacement_value = _num(replacement.get("player_value"))
        replacement_minutes = _num(replacement.get("expected_minutes"))
        if (
            not _PLAYER_ID.fullmatch(replacement_id)
            or replacement_id in rotation_ids
            or replacement_value is None
            or replacement_minutes is None
            or replacement_minutes < 0
        ):
            blockers.append(f"NBA_CONTEXT_{side.upper()}_REPLACEMENT_INVALID")
            valid = False
            continue
        covered = min(missing_minutes, replacement_minutes)
        replacement_delta_numerator += covered * (replacement_value - value)

    if not valid:
        return None
    if abs(planned_total - REGULATION_ROTATION_MINUTES) > _MINUTES_EPSILON:
        blockers.append(f"NBA_CONTEXT_{side.upper()}_ROTATION_MINUTES_INVALID")
        return None
    if projected_starters == 0:
        blockers.append(f"NBA_CONTEXT_{side.upper()}_STARTERS_MISSING")
        return None

    return {
        "minutes_weighted_player_value": weighted_value / planned_total,
        "availability_minutes_share": expected_total / planned_total,
        "replacement_quality_delta": replacement_delta_numerator / planned_total,
        "lineup_continuity": retained_starters / projected_starters,
    }


def _margin_variance(margins: Any, side: str, blockers: list[str]) -> float | None:
    values = margins if isinstance(margins, list) else None
    if values is None or len(values) < MIN_MARGIN_OBSERVATIONS:
        blockers.append(f"NBA_CONTEXT_{side.upper()}_MARGIN_HISTORY_INSUFFICIENT")
        return None
    parsed: list[float] = []
    for value in values:
        margin = _num(value)
        if margin is None or abs(margin) > 100:
            blockers.append(f"NBA_CONTEXT_{side.upper()}_MARGIN_HISTORY_INVALID")
            return None
        parsed.append(margin)
    mean = sum(parsed) / len(parsed)
    return sum((value - mean) ** 2 for value in parsed) / (len(parsed) - 1)


def _shot_profile(profile: Any, side: str, blockers: list[str]) -> dict[str, float] | None:
    values = _mapping(profile)
    if values is None:
        blockers.append(f"NBA_CONTEXT_{side.upper()}_SHOT_PROFILE_MISSING")
        return None
    parsed: dict[str, float] = {}
    for key in ("three_point_rate", "rim_rate", "midrange_rate"):
        rate = _num(values.get(key))
        if rate is None or not 0 <= rate <= 1:
            blockers.append(f"NBA_CONTEXT_{side.upper()}_SHOT_PROFILE_INVALID")
            return None
        parsed[key] = rate
    if abs(sum(parsed.values()) - 1.0) > _MINUTES_EPSILON:
        blockers.append(f"NBA_CONTEXT_{side.upper()}_SHOT_PROFILE_INVALID")
        return None
    return parsed


def derive_nba_pregame_context_features(
    evidence: Mapping[str, Any] | None,
    *,
    as_of_utc: datetime,
) -> dict[str, Any]:
    """Derive pre-registered NBA context factors or return a typed hold.

    ``evidence`` must be a server-owned pregame snapshot captured strictly
    before kickoff. No caller-supplied probability, market price or projection
    is accepted as an input.
    """
    if not isinstance(as_of_utc, datetime) or as_of_utc.utcoffset() is None:
        raise ValueError("as_of_utc must be timezone-aware")
    as_of = as_of_utc.astimezone(timezone.utc)

    blockers: list[str] = []
    package = _mapping(evidence)
    if package is None:
        return _held(["NBA_CONTEXT_EVIDENCE_MISSING"], as_of=as_of)

    identity = {
        "official_event_id": package.get("official_event_id"),
        "home_team_id": package.get("home_team_id"),
        "away_team_id": package.get("away_team_id"),
        "source_snapshot_id": package.get("source_snapshot_id"),
        "source_snapshot_timestamp": package.get("source_snapshot_timestamp"),
    }
    if str(package.get("sport") or "").strip().upper() != SUPPORTED_SPORT:
        blockers.append("NBA_CONTEXT_SPORT_MISMATCH")

    official_id = str(package.get("official_event_id") or "").strip()
    if not _EVENT_ID.fullmatch(official_id):
        blockers.append("NBA_CONTEXT_EVENT_IDENTITY_INVALID")
    home_team = str(package.get("home_team_id") or "").strip()
    away_team = str(package.get("away_team_id") or "").strip()
    if not _TEAM_ID.fullmatch(home_team) or not _TEAM_ID.fullmatch(away_team):
        blockers.append("NBA_CONTEXT_TEAM_IDENTITY_INVALID")
    elif home_team == away_team:
        blockers.append("NBA_CONTEXT_DUPLICATE_OPPONENT_IDENTITY")

    event_at = _aware(package.get("event_start_time_utc"))
    source_at = _aware(package.get("source_snapshot_timestamp"))
    if event_at is None:
        blockers.append("NBA_CONTEXT_EVENT_TIME_INVALID")
    elif as_of >= event_at:
        blockers.append("NBA_CONTEXT_NOT_PREGAME")
    if source_at is None:
        blockers.append("NBA_CONTEXT_SOURCE_TIMESTAMP_INVALID")
    else:
        if source_at > as_of:
            blockers.append("NBA_CONTEXT_SOURCE_SNAPSHOT_IN_FUTURE")
        if event_at is not None and source_at >= event_at:
            blockers.append("NBA_CONTEXT_SOURCE_SNAPSHOT_AFTER_KICKOFF")

    snapshot_id = str(package.get("source_snapshot_id") or "").strip()
    if not snapshot_id:
        blockers.append("NBA_CONTEXT_SOURCE_SNAPSHOT_MISSING")

    players = _mapping(package.get("players"))
    if players is None:
        blockers.append("NBA_CONTEXT_ROTATION_MISSING")
    shot_quality = _mapping(package.get("shot_quality"))
    if shot_quality is None:
        blockers.append("NBA_CONTEXT_SHOT_QUALITY_MISSING")
    pace = _mapping(package.get("pace"))
    if pace is None:
        blockers.append("NBA_CONTEXT_PACE_MISSING")
    profiles = _mapping(package.get("shot_profile"))
    if profiles is None:
        blockers.append("NBA_CONTEXT_SHOT_PROFILE_MISSING")
    travel = _mapping(package.get("travel"))
    if travel is None:
        blockers.append("NBA_CONTEXT_TRAVEL_MISSING")
    margins = _mapping(package.get("recent_margins"))
    if margins is None:
        blockers.append("NBA_CONTEXT_MARGIN_HISTORY_MISSING")

    rotation: dict[str, dict[str, float]] = {}
    if players is not None:
        for side in _SIDES:
            side_features = _rotation_features(players.get(side), side, blockers)
            if side_features is not None:
                rotation[side] = side_features

    opponent_adjusted: dict[str, float] = {}
    if shot_quality is not None:
        own: dict[str, float] = {}
        allowed: dict[str, float] = {}
        for side in _SIDES:
            block = _mapping(shot_quality.get(side))
            offense = _num(block.get("own_offense_index")) if block is not None else None
            defense = _num(block.get("defense_allowed_index")) if block is not None else None
            if offense is None or defense is None:
                blockers.append(f"NBA_CONTEXT_{side.upper()}_SHOT_QUALITY_INVALID")
                continue
            own[side] = offense
            allowed[side] = defense
        if set(own) == set(_SIDES) and set(allowed) == set(_SIDES):
            opponent_adjusted = {
                "home": (own["home"] + allowed["away"]) / 2.0,
                "away": (own["away"] + allowed["home"]) / 2.0,
            }

    pace_values: dict[str, float] = {}
    if pace is not None:
        for side in _SIDES:
            value = _num(pace.get(side))
            if value is None or value <= 0:
                blockers.append(f"NBA_CONTEXT_{side.upper()}_PACE_INVALID")
                continue
            pace_values[side] = value

    profile_values: dict[str, dict[str, float]] = {}
    if profiles is not None:
        for side in _SIDES:
            parsed = _shot_profile(profiles.get(side), side, blockers)
            if parsed is not None:
                profile_values[side] = parsed

    travel_features: dict[str, dict[str, float]] = {}
    if travel is not None:
        for side in _SIDES:
            block = _mapping(travel.get(side))
            rest = _int(block.get("rest_days")) if block is not None else None
            miles = _num(block.get("travel_miles_7d")) if block is not None else None
            shift = _num(block.get("time_zone_shift_hours")) if block is not None else None
            if (
                rest is None
                or not 0 <= rest <= 30
                or miles is None
                or miles < 0
                or shift is None
                or abs(shift) > 15
            ):
                blockers.append(f"NBA_CONTEXT_{side.upper()}_TRAVEL_INVALID")
                continue
            travel_features[side] = {
                "rest_days": float(rest),
                "travel_miles_7d": miles,
                "time_zone_shift_hours": shift,
                "back_to_back": float(rest == 0),
            }

    variance: dict[str, float] = {}
    if margins is not None:
        for side in _SIDES:
            value = _margin_variance(margins.get(side), side, blockers)
            if value is not None:
                variance[side] = value

    if blockers:
        return _held(blockers, identity=identity, as_of=as_of)

    pace_gap = abs(pace_values["home"] - pace_values["away"])
    home_profile = profile_values["home"]
    away_profile = profile_values["away"]
    matchup = 0.5 * sum(
        abs(home_profile[key] - away_profile[key])
        for key in ("three_point_rate", "rim_rate", "midrange_rate")
    )
    features = {
        "home": {
            **rotation["home"],
            "opponent_adjusted_shot_quality": opponent_adjusted["home"],
            "pace": pace_values["home"],
            "pace_style_mismatch": matchup,
            **travel_features["home"],
            "recent_margin_variance": variance["home"],
        },
        "away": {
            **rotation["away"],
            "opponent_adjusted_shot_quality": opponent_adjusted["away"],
            "pace": pace_values["away"],
            "pace_style_mismatch": matchup,
            **travel_features["away"],
            "recent_margin_variance": variance["away"],
        },
    }
    for side in _SIDES:
        if tuple(sorted(features[side])) != tuple(sorted(CONTEXT_FEATURE_NAMES)):
            raise AssertionError("NBA_CONTEXT_FEATURE_SCHEMA_DRIFT")

    normalized = json.loads(
        json.dumps(package, sort_keys=True, separators=(",", ":"), allow_nan=False)
    )
    evidence_digest = sha256(
        json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    vector_digest = sha256(
        json.dumps(
            {
                "event": official_id,
                "home_team_id": home_team,
                "away_team_id": away_team,
                "event_start_time_utc": event_at.isoformat(),
                "source_snapshot_id": snapshot_id,
                "source_snapshot_timestamp": source_at.isoformat(),
                "features": features,
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()

    return {
        "status": "PASS",
        "code": None,
        "typed_failure": None,
        "blockers": [],
        "sport": SUPPORTED_SPORT,
        "official_event_id": official_id,
        "home_team_id": home_team,
        "away_team_id": away_team,
        "event_start_time_utc": event_at.isoformat(),
        "feature_timestamp": as_of.isoformat(),
        "source_snapshot_id": snapshot_id,
        "source_snapshot_timestamp": source_at.isoformat(),
        "features": features,
        "feature_audit": {
            "context_feature_names": list(CONTEXT_FEATURE_NAMES),
            "home_rotation_size": len(normalized["players"]["home"]),
            "away_rotation_size": len(normalized["players"]["away"]),
            "home_prior_starters_retained": sum(
                1
                for p in normalized["players"]["home"]
                if p["projected_starter"] and p["prior_game_starter"]
            ),
            "away_prior_starters_retained": sum(
                1
                for p in normalized["players"]["away"]
                if p["projected_starter"] and p["prior_game_starter"]
            ),
            "opponent_adjustment": "ADDITIVE_TWO_SIDED_INDEX_MEAN_V1",
            "pace_style_mismatch": "HALF_L1_SHOT_PROFILE_DISTANCE_V1",
            "scoring_variance": "SAMPLE_VARIANCE_PRIOR_MARGINS_V1",
            "market_features_used": False,
            "market_probability_used_as_model": False,
            "caller_verified_override_used": False,
        },
        "evidence_sha256": evidence_digest,
        "factor_vector_sha256": vector_digest,
        "source_evidence": normalized,
        "research_only": RESEARCH_ONLY,
        "fitted_feature_vector_extension": False,
        "calibration_status": "NOT_CALIBRATED_RESEARCH_ONLY",
        "probability_publishable": PROBABILITY_PUBLISHABLE,
        "rank_eligible": False,
        "can_execute": False,
    }


def verify_nba_pregame_context_receipt(receipt: Mapping[str, Any] | None) -> dict[str, Any]:
    """Re-derive and verify a stored receipt's immutable evidence digest.

    Returns a verification result only; it never upgrades a research receipt
    into a publishable probability or a fitted-model input.
    """
    if not isinstance(receipt, Mapping):
        return {"status": "HELD", "blockers": ["NBA_CONTEXT_RECEIPT_MISSING"], "can_execute": False}
    blockers: list[str] = []
    if receipt.get("status") != "PASS":
        blockers.append("NBA_CONTEXT_RECEIPT_NOT_PASS")
    evidence = deepcopy(receipt.get("source_evidence"))
    if not isinstance(evidence, Mapping):
        blockers.append("NBA_CONTEXT_RECEIPT_EVIDENCE_MISSING")
        return {
            "status": "HELD",
            "blockers": sorted(set(blockers)),
            "research_only": RESEARCH_ONLY,
            "probability_publishable": False,
            "can_execute": False,
        }
    digest = sha256(
        json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if digest != receipt.get("evidence_sha256"):
        blockers.append("NBA_CONTEXT_RECEIPT_EVIDENCE_DIGEST_MISMATCH")
    as_of = _aware(receipt.get("feature_timestamp"))
    if as_of is None:
        blockers.append("NBA_CONTEXT_RECEIPT_FEATURE_TIMESTAMP_INVALID")
        return {
            "status": "HELD",
            "blockers": sorted(set(blockers)),
            "research_only": RESEARCH_ONLY,
            "probability_publishable": False,
            "can_execute": False,
        }
    rederived = derive_nba_pregame_context_features(evidence, as_of_utc=as_of)
    if rederived.get("status") != "PASS":
        blockers.extend(rederived.get("blockers") or ["NBA_CONTEXT_RECEIPT_REDERIVATION_HELD"])
        return {
            "status": "HELD",
            "blockers": sorted(set(blockers)),
            "research_only": RESEARCH_ONLY,
            "probability_publishable": False,
            "can_execute": False,
        }
    if (
        rederived.get("features") != receipt.get("features")
        or rederived.get("factor_vector_sha256") != receipt.get("factor_vector_sha256")
    ):
        blockers.append("NBA_CONTEXT_RECEIPT_FEATURE_VECTOR_MISMATCH")
    return {
        "status": "VERIFIED_RESEARCH_RECEIPT" if not blockers else "HELD",
        "blockers": sorted(set(blockers)),
        "evidence_sha256": digest,
        "factor_vector_sha256": rederived.get("factor_vector_sha256"),
        "research_only": RESEARCH_ONLY,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "CONTEXT_FEATURE_NAMES",
    "MIN_MARGIN_OBSERVATIONS",
    "PROBABILITY_PUBLISHABLE",
    "REGULATION_ROTATION_MINUTES",
    "RESEARCH_ONLY",
    "SUPPORTED_SPORT",
    "derive_nba_pregame_context_features",
    "verify_nba_pregame_context_receipt",
]
