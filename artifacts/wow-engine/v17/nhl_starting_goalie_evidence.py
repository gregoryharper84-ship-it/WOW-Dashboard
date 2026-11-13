"""Research-only NHL verified starting-goalie and full-game modeling contract.

The existing pregame evidence boundary requires a CONFIRMED goalie status and a
goalie identity snapshot, but nothing in the repository derives or verifies that
receipt from a first-party source. This module closes that gap:

- verifies a server-owned starting-goalie confirmation receipt whose source is
  the official NHL public web API gamecenter endpoint for the exact canonical
  event, with bounded freshness and no market/model authority;
- computes explicit starter and replacement (backup) save-percentage scenarios
  with the same pre-registered shrinkage prior used by the existing
  research-only goalie/shot challenger;
- enforces that a full-game NHL model includes five-on-five, special-teams,
  overtime and shootout segments and never reuses a regulation-only model for a
  full-game (including OT/SO) settlement.

It does not fit, calibrate, rank, publish, persist or execute anything.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import re
from typing import Any, Mapping

from v17.nhl_event_identity import team_abbreviation

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
RESEARCH_ONLY = True
SOURCE_PROVIDER = "NHL_PUBLIC_WEB_API"
GAMECENTER_PREFIX = "https://api-web.nhle.com/v1/gamecenter/"
START_TOLERANCE_MINUTES = 30.0
MAX_CONFIRMATION_AGE_MINUTES = 60
MAX_IDENTITY_AGE_HOURS = 24
GOALIE_PRIOR_SHOTS = 300
GOALIE_PRIOR_SV = 0.900
FULL_GAME_SETTLEMENT = "HOME_AWAY_INCLUDING_OT_SO"
CONFIRMATION_METHODS = frozenset(
    {"OFFICIAL_GAMECENTER_STARTER_ANNOUNCEMENT", "OFFICIAL_CLUB_STARTER_ANNOUNCEMENT"}
)
REPLACEMENT_STATUSES = frozenset({"BACKUP_ROSTERED", "PROJECTED_BACKUP"})
MODELED = "MODELED"
FULL_GAME_SEGMENTS = (
    "five_on_five_expected_goals",
    "special_teams_interaction",
    "overtime_segment",
    "shootout_segment",
)
_EVENT_ID = re.compile(r"[0-9]{10}")
_GOALIE_ID = re.compile(r"[1-9][0-9]{0,9}")
_SIDES = ("home", "away")


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


def _digest(value: Any) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _receipt(
    status: str,
    blockers: list[str],
    *,
    evidence: Mapping[str, Any] | None,
    as_of: datetime,
) -> dict[str, Any]:
    evidence = evidence if isinstance(evidence, Mapping) else {}
    return {
        "status": status,
        "blockers": sorted(set(blockers)),
        "sport": "NHL",
        "official_event_id": evidence.get("official_event_id"),
        "event_start_time_utc": evidence.get("event_start_time_utc"),
        "source_snapshot_id": evidence.get("source_snapshot_id"),
        "feature_timestamp": as_of.isoformat(),
        "starter_confirmed": status == "GOALIE_EVIDENCE_PASS_NOT_CERTIFICATION",
        "research_only": RESEARCH_ONLY,
        "probability_publishable": PROBABILITY_PUBLISHABLE,
        "rank_eligible": False,
        "can_execute": False,
    }


def _verify_identity(
    resolved_identity: Any,
    *,
    official_id: str,
    home: str | None,
    away: str | None,
    start_at: datetime | None,
    now: datetime,
    blockers: list[str],
) -> None:
    receipt = _mapping(resolved_identity)
    if receipt is None:
        blockers.append("NHL_GOALIE_CANONICAL_IDENTITY_RECEIPT_MISSING")
        return
    source = receipt.get("identity_source")
    verified_at = _aware(receipt.get("identity_verified_at"))
    resolved_start = _aware(receipt.get("event_start_time"))
    receipt_home = team_abbreviation(receipt.get("home_team"))
    receipt_away = team_abbreviation(receipt.get("away_team"))
    if (
        receipt.get("identity_provider") != SOURCE_PROVIDER
        or not isinstance(source, str)
        or not source.startswith("https://api-web.nhle.com/v1/club-schedule-season/")
        or not _EVENT_ID.fullmatch(official_id)
        or str(receipt.get("event_id") or "") != official_id
        or not home
        or not away
        or receipt_home != home
        or receipt_away != away
        or resolved_start is None
        or start_at is None
        or abs((resolved_start - start_at).total_seconds()) > START_TOLERANCE_MINUTES * 60
        or verified_at is None
        or (verified_at - now).total_seconds() > 5 * 60
        or (now - verified_at).total_seconds() > MAX_IDENTITY_AGE_HOURS * 3600
        or receipt.get("market_features_used") is not False
        or receipt.get("prediction_authority") is not False
        or receipt.get("can_execute") is not False
    ):
        blockers.append("NHL_GOALIE_CANONICAL_IDENTITY_RECEIPT_MISMATCH")


def verify_nhl_starting_goalie_evidence(
    evidence: Mapping[str, Any] | None,
    *,
    as_of_utc: datetime,
    resolved_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify first-party starting-goalie confirmation; never certifies a model."""
    if not isinstance(as_of_utc, datetime) or as_of_utc.utcoffset() is None:
        raise ValueError("as_of_utc must be timezone-aware")
    now = as_of_utc.astimezone(timezone.utc)
    package = _mapping(evidence)
    if package is None:
        return _receipt("HELD", ["NHL_GOALIE_EVIDENCE_MISSING"], evidence=None, as_of=now)

    blockers: list[str] = []
    if str(package.get("sport") or "").strip().upper() != "NHL":
        blockers.append("NHL_GOALIE_SPORT_MISMATCH")
    official_id = str(package.get("official_event_id") or "").strip()
    if not _EVENT_ID.fullmatch(official_id):
        blockers.append("NHL_GOALIE_EVENT_IDENTITY_INVALID")
    home = team_abbreviation(package.get("home_team"))
    away = team_abbreviation(package.get("away_team"))
    if not home or not away:
        blockers.append("NHL_GOALIE_TEAM_IDENTITY_UNVERIFIED")
    elif home == away:
        blockers.append("NHL_GOALIE_DUPLICATE_OPPONENT_IDENTITY")
    start_at = _aware(package.get("event_start_time_utc"))
    if start_at is None:
        blockers.append("NHL_GOALIE_EVENT_TIME_INVALID")
    elif start_at <= now:
        blockers.append("NHL_GOALIE_EVENT_NOT_PREGAME")
    snapshot_id = str(package.get("source_snapshot_id") or "").strip()
    if not snapshot_id:
        blockers.append("NHL_GOALIE_SOURCE_SNAPSHOT_MISSING")
    source_at = _aware(package.get("source_snapshot_timestamp"))
    if source_at is None:
        blockers.append("NHL_GOALIE_SOURCE_TIMESTAMP_INVALID")
    else:
        if source_at > now:
            blockers.append("NHL_GOALIE_SOURCE_SNAPSHOT_IN_FUTURE")
        if start_at is not None and source_at >= start_at:
            blockers.append("NHL_GOALIE_SOURCE_SNAPSHOT_AFTER_KICKOFF")

    _verify_identity(
        resolved_identity,
        official_id=official_id,
        home=home,
        away=away,
        start_at=start_at,
        now=now,
        blockers=blockers,
    )

    confirmation = _mapping(package.get("confirmation"))
    if confirmation is None:
        blockers.append("NHL_GOALIE_CONFIRMATION_MISSING")
    else:
        if confirmation.get("source_provider") != SOURCE_PROVIDER:
            blockers.append("NHL_GOALIE_CONFIRMATION_PROVIDER_UNVERIFIED")
        source_url = confirmation.get("source_url")
        if (
            not isinstance(source_url, str)
            or not source_url.startswith(GAMECENTER_PREFIX)
            or official_id not in source_url
        ):
            blockers.append("NHL_GOALIE_CONFIRMATION_SOURCE_UNVERIFIED")
        if str(confirmation.get("method") or "").strip().upper() not in CONFIRMATION_METHODS:
            blockers.append("NHL_GOALIE_CONFIRMATION_METHOD_UNVERIFIED")
        confirmation_snapshot = str(confirmation.get("snapshot_id") or "").strip()
        if not confirmation_snapshot:
            blockers.append("NHL_GOALIE_CONFIRMATION_SNAPSHOT_MISSING")
        captured_at = _aware(confirmation.get("captured_at"))
        if (
            captured_at is None
            or captured_at > now
            or (now - captured_at).total_seconds() > MAX_CONFIRMATION_AGE_MINUTES * 60
        ):
            blockers.append("NHL_GOALIE_CONFIRMATION_STALE")
        if (
            confirmation.get("market_features_used") is not False
            or confirmation.get("prediction_authority") is not False
            or confirmation.get("can_execute") is not False
        ):
            blockers.append("NHL_GOALIE_CONFIRMATION_AUTHORITY_CLAIMED")

        starters = _mapping(confirmation.get("starters"))
        replacements = _mapping(confirmation.get("replacements"))
        if starters is None:
            blockers.append("NHL_GOALIE_STARTERS_MISSING")
        if replacements is None:
            blockers.append("NHL_GOALIE_REPLACEMENT_SCENARIO_MISSING")
        goalie_ids: dict[str, str] = {}
        for side in _SIDES:
            expected_team = home if side == "home" else away
            starter = _mapping(starters.get(side)) if starters is not None else None
            if starter is None:
                blockers.append(f"NHL_GOALIE_{side.upper()}_STARTER_MISSING")
            else:
                goalie_id = str(starter.get("goalie_id") or "").strip()
                if not _GOALIE_ID.fullmatch(goalie_id):
                    blockers.append(f"NHL_GOALIE_{side.upper()}_STARTER_ID_INVALID")
                else:
                    goalie_ids[side] = goalie_id
                if team_abbreviation(starter.get("team")) != expected_team:
                    blockers.append(f"NHL_GOALIE_{side.upper()}_STARTER_TEAM_MISMATCH")
                if str(starter.get("status") or "").strip().upper() != "CONFIRMED":
                    blockers.append(f"NHL_GOALIE_{side.upper()}_STARTER_NOT_CONFIRMED")
                observed_at = _aware(starter.get("observed_at"))
                if (
                    observed_at is None
                    or observed_at > now
                    or (now - observed_at).total_seconds() > MAX_CONFIRMATION_AGE_MINUTES * 60
                ):
                    blockers.append(f"NHL_GOALIE_{side.upper()}_STARTER_STALE")
            replacement = _mapping(replacements.get(side)) if replacements is not None else None
            if replacement is None:
                blockers.append(f"NHL_GOALIE_{side.upper()}_REPLACEMENT_MISSING")
                continue
            replacement_id = str(replacement.get("goalie_id") or "").strip()
            shots = replacement.get("prior_shots_against")
            saves = replacement.get("prior_saves")
            if not _GOALIE_ID.fullmatch(replacement_id):
                blockers.append(f"NHL_GOALIE_{side.upper()}_REPLACEMENT_ID_INVALID")
            elif goalie_ids.get(side) == replacement_id:
                blockers.append(f"NHL_GOALIE_{side.upper()}_REPLACEMENT_DUPLICATES_STARTER")
            if team_abbreviation(replacement.get("team")) != expected_team:
                blockers.append(f"NHL_GOALIE_{side.upper()}_REPLACEMENT_TEAM_MISMATCH")
            if str(replacement.get("status") or "").strip().upper() not in REPLACEMENT_STATUSES:
                blockers.append(f"NHL_GOALIE_{side.upper()}_REPLACEMENT_STATUS_UNVERIFIED")
            if (
                isinstance(shots, bool)
                or not isinstance(shots, int)
                or shots <= 0
                or isinstance(saves, bool)
                or not isinstance(saves, int)
                or saves < 0
                or saves > shots
            ):
                blockers.append(f"NHL_GOALIE_{side.upper()}_REPLACEMENT_WORKLOAD_INVALID")
        if len(set(goalie_ids.values())) != len(goalie_ids):
            blockers.append("NHL_GOALIE_DUPLICATE_STARTER_IDENTITY")

    status = "GOALIE_EVIDENCE_PASS_NOT_CERTIFICATION" if not blockers else "HELD"
    return _receipt(status, blockers, evidence=package, as_of=now)


def _shrunk_save_pct(shots: int, saves: int) -> float:
    return (saves + GOALIE_PRIOR_SHOTS * GOALIE_PRIOR_SV) / (shots + GOALIE_PRIOR_SHOTS)


def goalie_replacement_scenarios(
    evidence: Mapping[str, Any] | None,
    *,
    as_of_utc: datetime,
    resolved_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute starter/replacement save-% deltas for verified goalie evidence.

    The shrinkage prior and window are pre-registered constants already used by
    the research-only goalie/shot challenger. Outputs are descriptive research
    quantities, not fitted model probabilities.
    """
    verification = verify_nhl_starting_goalie_evidence(
        evidence, as_of_utc=as_of_utc, resolved_identity=resolved_identity
    )
    if verification["status"] != "GOALIE_EVIDENCE_PASS_NOT_CERTIFICATION":
        return {
            **verification,
            "scenarios": None,
            "goalie_feature_consumable": False,
            "calibration_status": "NOT_CALIBRATED_RESEARCH_ONLY",
        }

    as_of = as_of_utc.astimezone(timezone.utc)
    package = _mapping(evidence) or {}
    confirmation = _mapping(package.get("confirmation")) or {}
    starters = _mapping(confirmation.get("starters")) or {}
    replacements = _mapping(confirmation.get("replacements")) or {}
    scenarios: dict[str, dict[str, Any]] = {}
    for side in _SIDES:
        starter = _mapping(starters.get(side)) or {}
        replacement = _mapping(replacements.get(side)) or {}
        starter_shots = starter.get("prior_shots_against")
        starter_saves = starter.get("prior_saves")
        replacement_shots = replacement.get("prior_shots_against")
        replacement_saves = replacement.get("prior_saves")
        # Validation above guarantees these are positive-integer workloads.
        if (
            isinstance(starter_shots, bool)
            or not isinstance(starter_shots, int)
            or starter_shots <= 0
            or isinstance(starter_saves, bool)
            or not isinstance(starter_saves, int)
            or not 0 <= starter_saves <= starter_shots
        ):
            return {
                **verification,
                "status": "HELD",
                "starter_confirmed": False,
                "blockers": sorted(
                    set(verification["blockers"])
                    | {f"NHL_GOALIE_{side.upper()}_STARTER_WORKLOAD_INVALID"}
                ),
                "scenarios": None,
                "goalie_feature_consumable": False,
                "calibration_status": "NOT_CALIBRATED_RESEARCH_ONLY",
            }
        starter_sv = _shrunk_save_pct(starter_shots, starter_saves)
        replacement_sv = _shrunk_save_pct(replacement_shots, replacement_saves)
        scenarios[side] = {
            "starter_goalie_id": str(starter["goalie_id"]),
            "replacement_goalie_id": str(replacement["goalie_id"]),
            "starter_shrunk_save_pct": starter_sv,
            "replacement_shrunk_save_pct": replacement_sv,
            "replacement_save_pct_delta": starter_sv - replacement_sv,
            "replacement_basis": str(replacement["status"]).upper(),
        }

    normalized = json.loads(
        json.dumps(package, sort_keys=True, separators=(",", ":"), allow_nan=False)
    )
    try:
        identity_receipt = json.loads(
            json.dumps(
                resolved_identity, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
        )
    except (TypeError, ValueError):
        return {
            **verification,
            "status": "HELD",
            "starter_confirmed": False,
            "blockers": sorted(
                set(verification["blockers"])
                | {"NHL_GOALIE_IDENTITY_RECEIPT_NOT_SERIALIZABLE"}
            ),
            "scenarios": None,
            "goalie_feature_consumable": False,
            "calibration_status": "NOT_CALIBRATED_RESEARCH_ONLY",
        }
    return {
        "status": "GOALIE_SCENARIOS_RESEARCH_ONLY",
        "blockers": [],
        "sport": "NHL",
        "official_event_id": package.get("official_event_id"),
        "source_snapshot_id": package.get("source_snapshot_id"),
        "feature_timestamp": as_of.isoformat(),
        "goalie_identity_snapshot_id": str(confirmation.get("snapshot_id") or "").strip() or None,
        "scenarios": scenarios,
        "shrinkage_prior_shots": GOALIE_PRIOR_SHOTS,
        "shrinkage_prior_save_pct": GOALIE_PRIOR_SV,
        "source_evidence": normalized,
        "identity_receipt": identity_receipt,
        "identity_receipt_sha256": _digest(identity_receipt),
        "evidence_sha256": _digest(normalized),
        "goalie_feature_consumable": False,
        "calibration_status": "NOT_CALIBRATED_RESEARCH_ONLY",
        "research_only": RESEARCH_ONLY,
        "probability_publishable": PROBABILITY_PUBLISHABLE,
        "rank_eligible": False,
        "can_execute": False,
    }


def validate_nhl_full_game_model_contract(modeling: Mapping[str, Any] | None) -> dict[str, Any]:
    """Require full-game (including OT/SO) coverage from a modeled NHL artifact.

    A regulation-only or three-way regulation model may not be presented as a
    full-game model. This validates documented coverage claims only; it never
    produces or approves a probability.
    """
    package = _mapping(modeling)
    if package is None:
        return {
            "status": "HELD",
            "blockers": ["NHL_FULL_GAME_MODELING_EVIDENCE_MISSING"],
            "modeled_segments": [],
            "probability_publishable": PROBABILITY_PUBLISHABLE,
            "can_execute": False,
        }

    blockers: list[str] = []
    if package.get("settlement_basis") != FULL_GAME_SETTLEMENT:
        blockers.append("NHL_FULL_GAME_SETTLEMENT_BASIS_MISMATCH")
    if package.get("regulation_only_probability_reused_as_full_game") is not False:
        blockers.append("NHL_REGULATION_ONLY_MODEL_REUSE")
    if package.get("overtime_included_in_final_score") is not True:
        blockers.append("NHL_OVERTIME_NOT_INCLUDED_IN_FINAL_SCORE")
    if package.get("shootout_included_in_final_score") is not True:
        blockers.append("NHL_SHOOTOUT_NOT_INCLUDED_IN_FINAL_SCORE")
    modeled: list[str] = []
    for segment in FULL_GAME_SEGMENTS:
        block = _mapping(package.get(segment))
        artifact_id = str((block or {}).get("artifact_id") or "").strip()
        if (
            block is None
            or str(block.get("status") or "").strip().upper() != MODELED
            or not artifact_id
        ):
            blockers.append(f"NHL_{segment.upper()}_NOT_MODELED")
        else:
            modeled.append(segment)
    return {
        "status": "FULL_GAME_MODEL_CONTRACT_PASS_NOT_CERTIFICATION"
        if not blockers
        else "HELD",
        "blockers": sorted(set(blockers)),
        "settlement_basis": package.get("settlement_basis"),
        "modeled_segments": modeled,
        "research_only": RESEARCH_ONLY,
        "probability_publishable": PROBABILITY_PUBLISHABLE,
        "rank_eligible": False,
        "can_execute": False,
    }


def verify_nhl_goalie_receipt(receipt: Mapping[str, Any] | None) -> dict[str, Any]:
    """Re-derive a stored goalie scenario receipt from its frozen evidence."""
    if not isinstance(receipt, Mapping):
        return {"status": "HELD", "blockers": ["NHL_GOALIE_RECEIPT_MISSING"], "can_execute": False}
    blockers: list[str] = []
    if receipt.get("status") != "GOALIE_SCENARIOS_RESEARCH_ONLY":
        blockers.append("NHL_GOALIE_RECEIPT_NOT_PASS")
    evidence = deepcopy(receipt.get("source_evidence"))
    if not isinstance(evidence, Mapping):
        blockers.append("NHL_GOALIE_RECEIPT_EVIDENCE_MISSING")
        return {
            "status": "HELD",
            "blockers": sorted(set(blockers)),
            "research_only": RESEARCH_ONLY,
            "probability_publishable": False,
            "can_execute": False,
        }
    digest = _digest(evidence)
    if digest != receipt.get("evidence_sha256"):
        blockers.append("NHL_GOALIE_RECEIPT_EVIDENCE_DIGEST_MISMATCH")
    identity_receipt = deepcopy(receipt.get("identity_receipt"))
    if not isinstance(identity_receipt, Mapping):
        blockers.append("NHL_GOALIE_RECEIPT_IDENTITY_MISSING")
        return {
            "status": "HELD",
            "blockers": sorted(set(blockers)),
            "research_only": RESEARCH_ONLY,
            "probability_publishable": False,
            "can_execute": False,
        }
    if _digest(identity_receipt) != receipt.get("identity_receipt_sha256"):
        blockers.append("NHL_GOALIE_RECEIPT_IDENTITY_DIGEST_MISMATCH")
    as_of = _aware(receipt.get("feature_timestamp"))
    if as_of is None:
        blockers.append("NHL_GOALIE_RECEIPT_TIMESTAMP_INVALID")
        return {
            "status": "HELD",
            "blockers": sorted(set(blockers)),
            "research_only": RESEARCH_ONLY,
            "probability_publishable": False,
            "can_execute": False,
        }
    rederived = goalie_replacement_scenarios(
        evidence, as_of_utc=as_of, resolved_identity=identity_receipt
    )
    if rederived.get("status") != "GOALIE_SCENARIOS_RESEARCH_ONLY":
        blockers.extend(rederived.get("blockers") or ["NHL_GOALIE_RECEIPT_REDERIVATION_HELD"])
    elif rederived.get("scenarios") != receipt.get("scenarios"):
        blockers.append("NHL_GOALIE_RECEIPT_SCENARIO_MISMATCH")
    return {
        "status": "VERIFIED_RESEARCH_RECEIPT" if not blockers else "HELD",
        "blockers": sorted(set(blockers)),
        "evidence_sha256": digest,
        "research_only": RESEARCH_ONLY,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "CONFIRMATION_METHODS",
    "FULL_GAME_SEGMENTS",
    "FULL_GAME_SETTLEMENT",
    "GAMECENTER_PREFIX",
    "GOALIE_PRIOR_SHOTS",
    "GOALIE_PRIOR_SV",
    "MAX_CONFIRMATION_AGE_MINUTES",
    "PROBABILITY_PUBLISHABLE",
    "REPLACEMENT_STATUSES",
    "RESEARCH_ONLY",
    "SOURCE_PROVIDER",
    "goalie_replacement_scenarios",
    "validate_nhl_full_game_model_contract",
    "verify_nhl_goalie_receipt",
    "verify_nhl_starting_goalie_evidence",
]
