"""Governed pregame NFL feature provider for certified spread artifacts.

The producer needs one feature vector per pregame event that is *identical in
schema and provenance* to the rows the spread artifact was fitted on. For NFL the
fitted family is the P2 pregame feature row stored in
``wow_nfl_pregame_feature_rows``, so this provider reuses the same two governed
functions the forward shadow path already uses:

* ``resolve_nfl_team_event_evidence`` — canonical schedule identity per event
* ``_prediction_feature_row`` — the leakage-safe prior-only P2 row

Nothing here fits a model, scores a line, or touches the market. The spread line,
moneyline price, implied probability, and any market-derived quantity are absent
by construction, and the only inputs are completed prior games, prior team
summaries, and the canonical schedule snapshot.

Everything fails closed with a typed ``SpreadChallengerUnavailable`` code, which
the producer surfaces as ``MODEL_INPUTS_INSUFFICIENT`` plus that code: an
unresolved event, thin prior sample, missing rate feature, incomplete vector, or
detected temporal leakage never becomes a substitute feature vector.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from nfl_event_features_p2 import FEATURE_ORDER
from v17.nfl_team_event_specialist import (
    _prediction_feature_row,
    resolve_nfl_team_event_evidence,
)
from v17.spread_margin_challenger import SpreadChallengerUnavailable

CAN_EXECUTE = False
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS = True
AUTOMATIC_CERTIFICATION = False
AUTOMATIC_PROMOTION = False
MARKET_FEATURES_ALLOWED = False
SPREAD_LINE_AS_FEATURE_ALLOWED = False
MONEYLINE_PROBABILITY_ALLOWED = False
PROVIDER_VERSION = "NFL_PREGAME_SPREAD_FEATURE_PROVIDER_V1"
SETTLEMENT_BASIS = "FULL_GAME_INCLUDING_OVERTIME"
REQUESTED_TIMEZONE = "America/New_York"
SUPPORTED_SPORTS = ("NFL",)
LEAKAGE_EXCLUSION = "TEMPORAL_LEAKAGE_DETECTED"


def _dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _day(value: Any) -> date:
    text = str(value or "").strip()
    if not text:
        raise ValueError("empty gameday")
    return date.fromisoformat(text[:10])


def _is_finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value == value  # NaN-safe
        and abs(float(value)) != float("inf")
    )


def _event_request(event: Mapping[str, Any], start: datetime) -> SimpleNamespace:
    return SimpleNamespace(
        official_event_id=str(event.get("event_id") or "").strip(),
        home_team=str(event.get("home_team") or "").strip(),
        away_team=str(event.get("away_team") or "").strip(),
        requested_slate_date=start.astimezone(ZoneInfo(REQUESTED_TIMEZONE)).date().isoformat(),
        event_start_time_utc=start.isoformat(),
        requested_timezone=REQUESTED_TIMEZONE,
        settlement_basis=SETTLEMENT_BASIS,
    )


def build_nfl_pregame_features_with_audit(
    db: Any,
    *,
    event: Mapping[str, Any],
    artifact: Any,
) -> tuple[dict[str, float], dict[str, Any]]:
    """Return the artifact-schema feature vector plus its provenance audit."""
    event_id = str(event.get("event_id") or "").strip()
    home = str(event.get("home_team") or "").strip()
    away = str(event.get("away_team") or "").strip()
    if not event_id:
        raise SpreadChallengerUnavailable(
            "NFL_PREGAME_FEATURE_EVENT_IDENTITY_INCOMPLETE", "event_id is required"
        )
    if not home or not away:
        raise SpreadChallengerUnavailable(
            "NFL_PREGAME_FEATURE_EVENT_IDENTITY_INCOMPLETE", "home_team and away_team are required"
        )
    if home.upper() == away.upper():
        raise SpreadChallengerUnavailable(
            "NFL_PREGAME_FEATURE_EVENT_IDENTITY_INVALID", "home_team and away_team must differ"
        )
    try:
        start = _dt(event.get("event_start_time"))
    except (TypeError, ValueError) as exc:
        raise SpreadChallengerUnavailable(
            "NFL_PREGAME_FEATURE_EVENT_START_INVALID",
            "event_start_time must be timezone-aware",
        ) from exc

    request = _event_request(event, start)
    try:
        identity = resolve_nfl_team_event_evidence(request, db=db)
    except Exception as exc:  # noqa: BLE001 - typed identity boundary
        raise SpreadChallengerUnavailable(
            f"NFL_PREGAME_FEATURE_EVIDENCE_UNAVAILABLE:{type(exc).__name__}",
            "canonical NFL event identity could not be read",
        ) from exc
    if not identity.get("ok"):
        raise SpreadChallengerUnavailable(
            str(identity.get("code") or "NFL_PREGAME_EVENT_IDENTITY_UNAVAILABLE"),
            "canonical NFL event identity could not be proven",
        )

    try:
        row = _prediction_feature_row(
            db=db,
            evidence=dict(identity["evidence"]),
            canonical_game_id=str(identity["canonical_event_id"]),
            canonical_home_team=str(identity["canonical_home_team"]),
            canonical_away_team=str(identity["canonical_away_team"]),
        )
    except Exception as exc:  # noqa: BLE001 - typed feature-row boundary
        code = str(getattr(exc, "code", "") or type(exc).__name__)
        raise SpreadChallengerUnavailable(
            f"NFL_PREGAME_FEATURE_ROW_UNAVAILABLE:{code}",
            "leakage-safe pregame feature row could not be built",
        ) from exc

    exclusions = sorted({str(reason) for reason in row.get("exclusion_reasons") or []})
    if LEAKAGE_EXCLUSION in exclusions:
        raise SpreadChallengerUnavailable(
            "NFL_PREGAME_FEATURES_TEMPORAL_LEAKAGE",
            "a prior gameday is not strictly before the target gameday",
        )
    if exclusions:
        raise SpreadChallengerUnavailable(
            "NFL_PREGAME_FEATURES_INSUFFICIENT:" + ",".join(exclusions),
            "pregame feature row is not usable: " + ",".join(exclusions),
        )
    if row.get("training_eligible") is not True:
        raise SpreadChallengerUnavailable(
            "NFL_PREGAME_FEATURES_NOT_TRAINING_ELIGIBLE",
            "pregame feature row is marked ineligible by the feature builder",
        )

    # Belt-and-braces leakage check: the newest consumed game must precede the
    # target gameday even if the builder's own exclusion list were to change.
    max_prior = row.get("max_prior_gameday")
    target_gameday = row.get("gameday") or request.requested_slate_date
    if max_prior:
        try:
            if _day(max_prior) >= _day(target_gameday):
                raise SpreadChallengerUnavailable(
                    "NFL_PREGAME_FEATURES_TEMPORAL_LEAKAGE",
                    "newest prior gameday is not before the target gameday",
                )
        except ValueError as exc:
            raise SpreadChallengerUnavailable(
                "NFL_PREGAME_FEATURE_CUTOFF_INVALID", "prior gameday is not a valid date"
            ) from exc

    row_features = row.get("features")
    if not isinstance(row_features, Mapping):
        raise SpreadChallengerUnavailable(
            "NFL_PREGAME_FEATURES_INSUFFICIENT", "feature row exposes no feature mapping"
        )
    names = tuple(str(name) for name in getattr(artifact, "feature_names", ()))
    if not names:
        raise SpreadChallengerUnavailable(
            "NFL_PREGAME_FEATURE_ARTIFACT_SCHEMA_MISSING", "certified artifact declares no feature schema"
        )
    missing = [name for name in names if name not in row_features]
    if missing:
        raise SpreadChallengerUnavailable(
            "NFL_PREGAME_FEATURE_SCHEMA_MISMATCH:" + ",".join(sorted(missing)),
            f"artifact requires features the pregame row does not produce: {sorted(missing)}",
        )
    non_finite = [name for name in names if not _is_finite_number(row_features[name])]
    if non_finite:
        raise SpreadChallengerUnavailable(
            "NFL_PREGAME_FEATURES_NON_FINITE:" + ",".join(sorted(non_finite)),
            f"pregame feature vector is not finite: {sorted(non_finite)}",
        )

    features = {name: float(row_features[name]) for name in names}
    audit = {
        "provider_version": PROVIDER_VERSION,
        "sport": "NFL",
        "canonical_event_id": str(identity["canonical_event_id"]),
        "canonical_home_team": str(identity["canonical_home_team"]),
        "canonical_away_team": str(identity["canonical_away_team"]),
        "identity_resolution": identity.get("identity_resolution"),
        "canonical_source_snapshot_id": identity.get("canonical_source_snapshot_id"),
        "feature_schema_version": row.get("feature_schema_version"),
        "feature_row_hash": row.get("row_inputs_hash"),
        "feature_as_of": row.get("feature_cutoff_date") or max_prior,
        "max_prior_gameday": max_prior,
        "target_gameday": target_gameday,
        "home_prior_games": row.get("home_prior_games"),
        "away_prior_games": row.get("away_prior_games"),
        "feature_names": list(names),
        "market_features_used": MARKET_FEATURES_ALLOWED,
        "moneyline_probability_used": MONEYLINE_PROBABILITY_ALLOWED,
        "spread_line_used_as_feature": SPREAD_LINE_AS_FEATURE_ALLOWED,
        "manual_probability_adjustments": False,
        "probability_publishable": False,
        "can_execute": CAN_EXECUTE,
    }
    return features, audit


def build_nfl_pregame_features(
    db: Any, *, event: Mapping[str, Any], artifact: Any
) -> dict[str, float]:
    """Producer-compatible feature builder: artifact schema in, floats out."""
    features, _audit = build_nfl_pregame_features_with_audit(
        db, event=event, artifact=artifact
    )
    return features


def nfl_pregame_feature_provider(
    db: Any, *, sport: str, event: Mapping[str, Any], artifact: Any
) -> dict[str, float]:
    """Drop-in ``feature_provider`` for ``run_pregame_spread_production``."""
    normalized = str(sport or "").strip().upper()
    if normalized not in SUPPORTED_SPORTS:
        raise SpreadChallengerUnavailable(
            "NFL_PREGAME_FEATURE_PROVIDER_SPORT_MISMATCH",
            f"this provider builds NFL features only; got {normalized or 'UNKNOWN'}",
        )
    return build_nfl_pregame_features(db, event=event, artifact=artifact)


__all__ = [
    "AUTOMATIC_CERTIFICATION",
    "AUTOMATIC_PROMOTION",
    "CAN_EXECUTE",
    "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS",
    "FEATURE_ORDER",
    "MARKET_FEATURES_ALLOWED",
    "MONEYLINE_PROBABILITY_ALLOWED",
    "PROVIDER_VERSION",
    "SETTLEMENT_BASIS",
    "SPREAD_LINE_AS_FEATURE_ALLOWED",
    "SUPPORTED_SPORTS",
    "build_nfl_pregame_features",
    "build_nfl_pregame_features_with_audit",
    "nfl_pregame_feature_provider",
]
