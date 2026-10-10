"""Read-only current-event NCAAF result/form feature diagnostics.

The fitted candidate's exact FEATURES_V1 inputs can be reconstructed from
settled prior games. This bridge does not score, calibrate, certify, activate,
or publish that candidate. It requires an upstream CFBD canonical identity
proof and never silently fills unavailable evidence or consumes market data.

A retrospective reconstruction is not an archived pregame feature snapshot.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from typing import Any, Mapping, Sequence

from v17.ncaaf_event_identity import SOURCE_PROVIDER, START_TOLERANCE_MINUTES, _name_match
from v17.ncaaf_result_form_candidate import (
    FEATURE_NAMES,
    FEATURE_SCHEMA_VERSION,
    MODEL_FAMILY,
    SOURCE_POLICY_ID,
    _summary,
)

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
CURRENT_FEATURE_BRIDGE_VERSION = "NCAAF_RESULT_FORM_FORWARD_FEATURES_DIAGNOSTIC_V1"


class NCAAFForwardFeatureUnavailable(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code


def _aware(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise NCAAFForwardFeatureUnavailable("NCAAF_FORWARD_EVENT_TIME_INVALID") from exc
    if parsed.utcoffset() is None:
        raise NCAAFForwardFeatureUnavailable("NCAAF_FORWARD_EVENT_TIME_INVALID")
    return parsed.astimezone(timezone.utc)


def current_event_feature_package(
    games: Sequence[Mapping[str, Any]],
    *,
    official_event_id: str,
    event_start_time: str,
    home_team: str,
    away_team: str,
    neutral_site: bool,
    canonical_identity_verified: bool,
    canonical_resolution: Mapping[str, Any],
) -> dict[str, Any]:
    """Build exact candidate features, not a governed probability package.

    Only source-verified, settled games known before the requested event time
    may enter either team's history; a source acquired after the target start
    is inadmissible. Return a typed hold rather than inventing a team baseline.
    """
    event_id = str(official_event_id or "").strip()
    home = str(home_team or "").strip()
    away = str(away_team or "").strip()
    if not canonical_identity_verified:
        raise NCAAFForwardFeatureUnavailable("NCAAF_FORWARD_CANONICAL_IDENTITY_NOT_PROVEN")
    if not event_id or not home or not away or home == away:
        raise NCAAFForwardFeatureUnavailable("NCAAF_FORWARD_EVENT_IDENTITY_INVALID")
    if not isinstance(neutral_site, bool):
        raise NCAAFForwardFeatureUnavailable("NCAAF_FORWARD_NEUTRAL_SITE_INVALID")
    start = _aware(event_start_time)
    # Reconcile the caller's claim to an actual resolver-shaped CFBD proof.
    # A bare true flag can never establish canonical identity.
    if not isinstance(canonical_resolution, Mapping):
        raise NCAAFForwardFeatureUnavailable("NCAAF_FORWARD_CANONICAL_PROOF_INVALID")
    if (str(canonical_resolution.get("event_id") or "").strip() != event_id
            or canonical_resolution.get("identity_provider") != SOURCE_PROVIDER
            or canonical_resolution.get("identity_resolution") != "CFBD_EXACT_PARTICIPANTS_START_MATCH"
            or canonical_resolution.get("market_features_used") is not False
            or canonical_resolution.get("prediction_authority") is not False
            or canonical_resolution.get("can_execute") is not False
            or not _name_match(home, canonical_resolution.get("home_team"))
            or not _name_match(away, canonical_resolution.get("away_team"))):
        raise NCAAFForwardFeatureUnavailable("NCAAF_FORWARD_CANONICAL_PROOF_INVALID")
    resolved_start = _aware(canonical_resolution.get("event_start_time"))
    if abs((start - resolved_start).total_seconds()) > 60 * START_TOLERANCE_MINUTES:
        raise NCAAFForwardFeatureUnavailable("NCAAF_FORWARD_CANONICAL_START_MISMATCH")

    history: dict[str, list[dict[str, Any]]] = {home: [], away: []}
    source_times: list[datetime] = []
    accepted_ids: set[str] = set()
    for raw in games:
        row_id = str(raw.get("official_event_id") or "").strip()
        if row_id == event_id:
            raise NCAAFForwardFeatureUnavailable("NCAAF_FORWARD_TARGET_IN_HISTORY")
        h, a = str(raw.get("home_team") or "").strip(), str(raw.get("away_team") or "").strip()
        if home not in (h, a) and away not in (h, a):
            continue
        if not row_id:
            raise NCAAFForwardFeatureUnavailable("NCAAF_FORWARD_PRIOR_EVENT_ID_MISSING")
        row_start = _aware(raw.get("event_start_time"))
        if row_start >= start:
            continue
        if row_id in accepted_ids:
            raise NCAAFForwardFeatureUnavailable("NCAAF_FORWARD_PRIOR_EVENT_DUPLICATE")
        if raw.get("home_won") is None or raw.get("home_points") is None or raw.get("away_points") is None:
            continue
        if not str(raw.get("result_source") or "").strip():
            continue
        source_value = raw.get("result_source_timestamp")
        if source_value in (None, ""):
            continue
        source_time = _aware(source_value)
        if source_time <= row_start:
            raise NCAAFForwardFeatureUnavailable("NCAAF_FORWARD_PRIOR_EVIDENCE_TIME_CONTRADICTION")
        if source_time >= start:
            continue
        for score in (raw["home_points"], raw["away_points"]):
            if isinstance(score, bool) or (isinstance(score, float) and not score.is_integer()):
                raise NCAAFForwardFeatureUnavailable("NCAAF_FORWARD_PRIOR_RESULT_INVALID")
            if isinstance(score, str) and not score.isdigit():
                raise NCAAFForwardFeatureUnavailable("NCAAF_FORWARD_PRIOR_RESULT_INVALID")
        try:
            hp, ap = int(raw["home_points"]), int(raw["away_points"])
        except (ValueError, TypeError, OverflowError) as exc:
            raise NCAAFForwardFeatureUnavailable("NCAAF_FORWARD_PRIOR_RESULT_INVALID") from exc
        if hp < 0 or ap < 0 or hp == ap or not isinstance(raw["home_won"], bool):
            raise NCAAFForwardFeatureUnavailable("NCAAF_FORWARD_PRIOR_RESULT_INVALID")
        if bool(raw["home_won"]) != (hp > ap):
            raise NCAAFForwardFeatureUnavailable("NCAAF_FORWARD_PRIOR_RESULT_CONTRADICTION")
        accepted_ids.add(row_id)
        source_times.append(source_time)
        if h in history:
            history[h].append({
                "event_id": row_id, "event_start": row_start,
                "won": hp > ap, "point_diff": hp - ap,
            })
        if a in history:
            history[a].append({
                "event_id": row_id, "event_start": row_start,
                "won": ap > hp, "point_diff": ap - hp,
            })

    for participant in (home, away):
        history[participant].sort(key=lambda row: (row["event_start"], row["event_id"]))
    hs, aws = _summary(history[home], start), _summary(history[away], start)
    if not hs or not aws:
        raise NCAAFForwardFeatureUnavailable(
            "NCAAF_FORWARD_PRIOR_FORM_INSUFFICIENT",
            f"home_prior={len(history[home])};away_prior={len(history[away])};minimum=3",
        )
    h, home_ids = hs
    a, away_ids = aws
    features = {
        "home_win_rate_prior": h["win_rate"],
        "away_win_rate_prior": a["win_rate"],
        "home_point_diff_prior": h["point_diff"],
        "away_point_diff_prior": a["point_diff"],
        "home_games_prior_log": h["games_prior_log"],
        "away_games_prior_log": a["games_prior_log"],
        "home_rest_days_capped": h["rest_days_capped"],
        "away_rest_days_capped": a["rest_days_capped"],
        "neutral_site": float(neutral_site),
    }
    if tuple(features) != FEATURE_NAMES or any(not math.isfinite(x) for x in features.values()):
        raise NCAAFForwardFeatureUnavailable("NCAAF_FORWARD_FEATURE_SCHEMA_INVALID")
    as_of = max(source_times)
    if as_of >= start:
        raise NCAAFForwardFeatureUnavailable("NCAAF_FORWARD_FEATURE_NOT_PREGAME")
    # Bind the exact numeric candidate-compatible vector to source provenance.
    # This is an integrity digest, not an external-source signature.
    features_sha256 = sha256(
        json.dumps(features, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    manifest = {
        "features_sha256": features_sha256,
        "feature_bridge_version": CURRENT_FEATURE_BRIDGE_VERSION,
        "source_policy_id": SOURCE_POLICY_ID,
        "official_event_id": event_id,
        "event_start_time": start.isoformat(),
        "home_team": home, "away_team": away,
        "home_prior_event_ids": home_ids, "away_prior_event_ids": away_ids,
        "feature_as_of": as_of.isoformat(),
        "market_features_used": False,
        "canonical_identity_verified_by_caller": True,
        "canonical_identity_source": SOURCE_PROVIDER,
        "canonical_identity_resolution": str(canonical_resolution["identity_resolution"]),
        "canonical_event_start_time": resolved_start.isoformat(),
        "historical_reconstruction": True,
        "archived_pregame_snapshot": False,
        "can_execute": False,
    }
    digest = sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        "status": "RESEARCH_FORWARD_FEATURES_ONLY",
        "official_event_id": event_id,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "candidate_model_family": MODEL_FAMILY,
        "features": features,
        "features_sha256": features_sha256,
        "feature_as_of": as_of.isoformat(),
        "source_manifest": manifest,
        "source_manifest_sha256": digest,
        "certification_status": "NOT_CERTIFIED_BY_FEATURE_BRIDGE",
        "model_probability": None,
        "calibrated_probability": None,
        "calibrated_lower_bound": None,
        "probability_publishable": False,
        "can_execute": False,
    }


def current_event_feature_package_from_cfbd(
    games: Sequence[Mapping[str, Any]],
    *,
    event_start_time: str,
    home_team: str,
    away_team: str,
    neutral_site: bool,
    client: Any = None,
) -> dict[str, Any]:
    """Preferred source-bound entrypoint: resolve canonical identity at CFBD.

    A caller-supplied boolean or fabricated identity dictionary must not be
    regarded as CFBD attestation. This function invokes the canonical resolver
    itself (or its injectable CFBD client in deterministic tests) and passes
    that result directly into the research-only feature builder.
    """
    from v17.ncaaf_event_identity import (
        NCAAFEventIdentityError,
        resolve_ncaaf_current_event_identity,
    )

    try:
        resolved = resolve_ncaaf_current_event_identity(
            event_start_time=event_start_time,
            home_team=home_team,
            away_team=away_team,
            client=client,
        )
    except NCAAFEventIdentityError as exc:
        raise NCAAFForwardFeatureUnavailable(exc.code, str(exc)) from exc

    return current_event_feature_package(
        games,
        official_event_id=str(resolved["event_id"]),
        event_start_time=event_start_time,
        home_team=home_team,
        away_team=away_team,
        neutral_site=neutral_site,
        canonical_identity_verified=True,
        canonical_resolution=resolved,
    )


__all__ = [
    "CAN_EXECUTE",
    "PROBABILITY_PUBLISHABLE",
    "CURRENT_FEATURE_BRIDGE_VERSION",
    "NCAAFForwardFeatureUnavailable",
    "current_event_feature_package",
    "current_event_feature_package_from_cfbd",
]
