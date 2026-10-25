"""Research-only admission of point-in-time NFL personnel and matchup evidence.

Do not feed the output to the active NFL fitted scorer. A complete packet is NOT
a fitted/calibrated probability, a verified provider source or an approved model.
Unproven historical availability must stay missing rather than reconstructed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from math import isfinite
import re
from typing import Any, Mapping, Sequence

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
PROMOTION_AUTHORIZED = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
SCHEMA_VERSION = "NFL_PREGAME_CONTEXT_RESEARCH_V1"
FAMILIES = ("QB_STARTER", "ROSTER_AVAILABILITY", "RUSH_PASS_MATCHUP")
SIDES = ("HOME", "AWAY")
_ALLOWED_SOURCES = frozenset(
    {"OFFICIAL_TEAM_REPORT", "OFFICIAL_INACTIVES", "LICENSED_STATS_FEED", "AUTHORIZED_TEAM_DATA"}
)
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")


class NFLContextAdmissionError(ValueError):
    """Typed fail-closed source/identity/provenance error."""


def _require(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NFLContextAdmissionError(code)
    return value.strip()


def _time(value: Any, code: str) -> datetime:
    if not isinstance(value, (str, datetime)):
        raise NFLContextAdmissionError(code)
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        raise NFLContextAdmissionError(code) from exc
    if parsed.utcoffset() is None:
        raise NFLContextAdmissionError(code)
    return parsed.astimezone(timezone.utc)


def _boolean(value: Any, code: str) -> int:
    if type(value) is not bool:
        raise NFLContextAdmissionError(code)
    return int(value)


def _int(value: Any, minimum: int, maximum: int, code: str) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise NFLContextAdmissionError(code)
    return value


def _number(value: Any, lo: float, hi: float, code: str) -> float:
    if type(value) not in (int, float) or not isfinite(float(value)) or not lo <= value <= hi:
        raise NFLContextAdmissionError(code)
    return float(value)


def _metrics(family: str, fields: Any, kickoff: datetime, observed: datetime) -> dict[str, float]:
    if not isinstance(fields, Mapping):
        raise NFLContextAdmissionError("NFL_CONTEXT_METRICS_MISSING")
    allowed = {
        "QB_STARTER": {"starter_id", "starter_confirmed", "starter_changed_from_previous_game"},
        "ROSTER_AVAILABILITY": {
            "ol_starters_expected", "skill_starters_expected", "defense_starters_expected",
        },
        "RUSH_PASS_MATCHUP": {
            "rush_off_epa_pp", "pass_off_epa_pp", "rush_def_epa_allowed_pp",
            "pass_def_epa_allowed_pp", "sample_games", "last_input_game_end_at",
        },
    }[family]
    if set(fields) != allowed:
        raise NFLContextAdmissionError("NFL_CONTEXT_METRIC_CONTRACT_INVALID")
    if family == "QB_STARTER":
        _require(fields.get("starter_id"), "NFL_CONTEXT_STARTER_ID_MISSING")
        if fields.get("starter_confirmed") is not True:
            raise NFLContextAdmissionError("NFL_CONTEXT_STARTER_NOT_CONFIRMED")
        return {
            "starter_changed": float(_boolean(
                fields.get("starter_changed_from_previous_game"), "NFL_CONTEXT_QB_CHANGE_INVALID"
            )),
        }
    if family == "ROSTER_AVAILABILITY":
        return {
            "ol_available": float(_int(fields.get("ol_starters_expected"), 0, 5, "NFL_CONTEXT_OL_COUNT_INVALID")),
            "skill_available": float(_int(fields.get("skill_starters_expected"), 0, 5, "NFL_CONTEXT_SKILL_COUNT_INVALID")),
            "defense_available": float(_int(fields.get("defense_starters_expected"), 0, 11, "NFL_CONTEXT_DEF_COUNT_INVALID")),
        }
    prior_end = _time(fields.get("last_input_game_end_at"), "NFL_CONTEXT_PRIOR_GAME_TIME_INVALID")
    if prior_end >= kickoff or prior_end > observed:
        raise NFLContextAdmissionError("NFL_CONTEXT_PRIOR_GAME_LEAKAGE")
    sample = _int(fields.get("sample_games"), 1, 32, "NFL_CONTEXT_MATCHUP_SAMPLE_INVALID")
    return {
        "rush_off_epa_pp": _number(fields.get("rush_off_epa_pp"), -2, 2, "NFL_CONTEXT_MATCHUP_EPA_INVALID"),
        "pass_off_epa_pp": _number(fields.get("pass_off_epa_pp"), -2, 2, "NFL_CONTEXT_MATCHUP_EPA_INVALID"),
        "rush_def_epa_allowed_pp": _number(fields.get("rush_def_epa_allowed_pp"), -2, 2, "NFL_CONTEXT_MATCHUP_EPA_INVALID"),
        "pass_def_epa_allowed_pp": _number(fields.get("pass_def_epa_allowed_pp"), -2, 2, "NFL_CONTEXT_MATCHUP_EPA_INVALID"),
        "sample_games": float(sample),
    }


def build_pregame_context_packet(
    event: Mapping[str, Any],
    evidence: Sequence[Mapping[str, Any]],
    *,
    decision_as_of: str,
) -> dict[str, Any]:
    """Build a *non-serving* research feature packet with no fabricated missing values.

    All source timestamps must be timezone-aware; source observation <= public
    availability <= authorized capture <= decision < kickoff. Provider identity
    and licensing remain independently auditable prerequisites for real training.
    """
    if not isinstance(event, Mapping):
        raise NFLContextAdmissionError("NFL_CONTEXT_EVENT_REQUIRED")
    event_id = _require(event.get("official_event_id"), "NFL_CONTEXT_EVENT_ID_MISSING")
    home = _require(event.get("home_team"), "NFL_CONTEXT_TEAM_ID_MISSING")
    away = _require(event.get("away_team"), "NFL_CONTEXT_TEAM_ID_MISSING")
    if home == away:
        raise NFLContextAdmissionError("NFL_CONTEXT_TEAM_ID_INVALID")
    kickoff = _time(event.get("event_start_time_utc"), "NFL_CONTEXT_KICKOFF_INVALID")
    cutoff = _time(decision_as_of, "NFL_CONTEXT_DECISION_TIME_INVALID")
    if cutoff >= kickoff:
        raise NFLContextAdmissionError("NFL_CONTEXT_DECISION_NOT_PREGAME")
    if not isinstance(evidence, (list, tuple)) or len(evidence) > 24:
        raise NFLContextAdmissionError("NFL_CONTEXT_SOURCE_COUNT_INVALID")

    accepted: dict[tuple[str, str], dict[str, float]] = {}
    receipts: list[dict[str, str]] = []
    for source in evidence:
        if not isinstance(source, Mapping):
            raise NFLContextAdmissionError("NFL_CONTEXT_SOURCE_INVALID")
        if source.get("official_event_id") != event_id:
            raise NFLContextAdmissionError("NFL_CONTEXT_EVENT_MISMATCH")
        side = source.get("side")
        family = source.get("family")
        if side not in SIDES or family not in FAMILIES:
            raise NFLContextAdmissionError("NFL_CONTEXT_FAMILY_OR_SIDE_INVALID")
        expected_team = home if side == "HOME" else away
        if source.get("team") != expected_team:
            raise NFLContextAdmissionError("NFL_CONTEXT_SIDE_TEAM_MISMATCH")
        key = (side, family)
        if key in accepted:
            raise NFLContextAdmissionError("NFL_CONTEXT_DUPLICATE_SOURCE_FAMILY")
        if source.get("data_domain") != "SPORTING" or source.get("source_kind") not in _ALLOWED_SOURCES:
            raise NFLContextAdmissionError("NFL_CONTEXT_SOURCE_DOMAIN_INVALID")
        if source.get("rights_status") != "RESEARCH_APPROVED":
            raise NFLContextAdmissionError("NFL_CONTEXT_SOURCE_RIGHTS_NOT_APPROVED")
        receipt_id = _require(source.get("source_receipt_id"), "NFL_CONTEXT_RECEIPT_ID_MISSING")
        publisher = _require(source.get("source_publisher"), "NFL_CONTEXT_PUBLISHER_MISSING")
        license_id = _require(source.get("rights_receipt_id"), "NFL_CONTEXT_RIGHTS_RECEIPT_MISSING")
        content_hash = _require(source.get("content_sha256"), "NFL_CONTEXT_CONTENT_HASH_MISSING")
        if not _SHA256.fullmatch(content_hash):
            raise NFLContextAdmissionError("NFL_CONTEXT_CONTENT_HASH_INVALID")
        observed = _time(source.get("observed_at"), "NFL_CONTEXT_SOURCE_TIME_INVALID")
        published = _time(source.get("published_at"), "NFL_CONTEXT_SOURCE_TIME_INVALID")
        captured = _time(source.get("captured_at"), "NFL_CONTEXT_SOURCE_TIME_INVALID")
        if not observed <= published <= captured <= cutoff:
            raise NFLContextAdmissionError("NFL_CONTEXT_SOURCE_TIME_LEAKAGE")
        fields = _metrics(family, source.get("measurements"), kickoff, observed)
        accepted[key] = fields
        receipts.append({
            "side": side, "family": family,
            "source_receipt_id": receipt_id, "source_publisher": publisher,
            "rights_receipt_id": license_id, "content_sha256": content_hash,
            "captured_at": captured.isoformat(),
        })

    missing = sorted(f"{side}:{family}" for side in SIDES for family in FAMILIES if (side, family) not in accepted)
    # No partial vector or arbitrary substitution; downstream candidate trains only
    # when every required research family is present.
    features = None
    if not missing:
        hq, aq = accepted[("HOME", "QB_STARTER")], accepted[("AWAY", "QB_STARTER")]
        hr, ar = accepted[("HOME", "ROSTER_AVAILABILITY")], accepted[("AWAY", "ROSTER_AVAILABILITY")]
        hm, am = accepted[("HOME", "RUSH_PASS_MATCHUP")], accepted[("AWAY", "RUSH_PASS_MATCHUP")]
        features = {
            "qb_change_edge": aq["starter_changed"] - hq["starter_changed"],
            "home_qb_starter_change": hq["starter_changed"],
            "away_qb_starter_change": aq["starter_changed"],
            "ol_available_edge": hr["ol_available"] - ar["ol_available"],
            "skill_available_edge": hr["skill_available"] - ar["skill_available"],
            "defense_available_edge": hr["defense_available"] - ar["defense_available"],
            "home_rush_matchup_epa_edge": hm["rush_off_epa_pp"] - am["rush_def_epa_allowed_pp"],
            "away_rush_matchup_epa_edge": am["rush_off_epa_pp"] - hm["rush_def_epa_allowed_pp"],
            "home_pass_matchup_epa_edge": hm["pass_off_epa_pp"] - am["pass_def_epa_allowed_pp"],
            "away_pass_matchup_epa_edge": am["pass_off_epa_pp"] - hm["pass_def_epa_allowed_pp"],
            "home_matchup_sample_games": hm["sample_games"],
            "away_matchup_sample_games": am["sample_games"],
        }
    sorted_receipts = sorted(receipts, key=lambda r: (r["side"], r["family"]))
    payload = {
        "official_event_id": event_id,
        "home_team": home, "away_team": away,
        "decision_as_of": cutoff.isoformat(),
        "event_start_time_utc": kickoff.isoformat(),
        "research_feature_schema": SCHEMA_VERSION,
        "source_receipts": sorted_receipts,
        "features": features, "missing_families": missing,
    }
    digest = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        **payload,
        "status": ("RESEARCH_CONTEXT_PACKET_COMPLETE_UNCERTIFIED" if not missing
                   else "PREGAME_CONTEXT_EVIDENCE_INCOMPLETE"),
        "packet_sha256": digest,
        "provider_authenticity_independently_verified": False,
        "model_fit_or_validation_complete": False,
        "active_champion_features_changed": False,
        "probability_publishable": False,
        "promotion_authorized": False,
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
    }



def audit_historical_snapshot_admissibility(
    snapshot: Mapping[str, Any],
    *,
    event_start_time_utc: str,
    decision_as_of: str,
    rights_state_from_manifest: str,
    row_available_at: str | None = None,
) -> dict[str, Any]:
    """Explain why bulk historical archives cannot establish pregame visibility.

    Archive HTTP Last-Modified and season file labels are NOT substitutes for
    contemporaneous public availability of each exact row. An archive whose
    source rules pass is only a candidate, never independently authenticated
    game-day evidence or a fitted model training feature.
    """
    kickoff = _time(event_start_time_utc, "NFL_CONTEXT_KICKOFF_INVALID")
    cutoff = _time(decision_as_of, "NFL_CONTEXT_DECISION_TIME_INVALID")
    if cutoff >= kickoff:
        raise NFLContextAdmissionError("NFL_CONTEXT_DECISION_NOT_PREGAME")
    if not isinstance(snapshot, Mapping):
        raise NFLContextAdmissionError("NFL_CONTEXT_SOURCE_INVALID")
    dataset = _require(snapshot.get("dataset_name"), "NFL_CONTEXT_DATASET_MISSING")
    if dataset not in {"INJURIES", "WEEKLY_ROSTERS", "PLAY_BY_PLAY"}:
        raise NFLContextAdmissionError("NFL_CONTEXT_DATASET_INVALID")
    fetched = _time(snapshot.get("fetched_at"), "NFL_CONTEXT_SOURCE_TIME_INVALID")
    blockers = []
    if rights_state_from_manifest != "RESEARCH_APPROVED":
        blockers.append("NFL_CONTEXT_MANIFEST_RIGHTS_NOT_APPROVED")
    if snapshot.get("source_status") != "CAPTURED":
        blockers.append("NFL_CONTEXT_SOURCE_CAPTURE_NOT_VERIFIED")
    if fetched > cutoff:
        blockers.append("NFL_CONTEXT_ARCHIVE_CAPTURE_AFTER_DECISION")
    if row_available_at is None:
        blockers.append("NFL_CONTEXT_HISTORICAL_ROW_AVAILABILITY_UNVERIFIED")
    else:
        available = _time(row_available_at, "NFL_CONTEXT_SOURCE_TIME_INVALID")
        if available > cutoff:
            blockers.append("NFL_CONTEXT_HISTORICAL_ROW_NOT_PREGAME_AVAILABLE")
        if available > fetched:
            blockers.append("NFL_CONTEXT_ROW_AVAILABILITY_AFTER_CAPTURE")
    return {
        "dataset_name": dataset,
        "source_snapshot_id": str(snapshot.get("snapshot_id") or ""),
        "admission_status": "RESEARCH_CANDIDATE_ONLY" if not blockers else "HOLD",
        "blockers": sorted(set(blockers)),
        "archive_capture_at": fetched.isoformat(),
        "row_availability_independently_verified": False,
        "source_license_independently_verified": False,
        "historical_feature_admitted": False,
        "model_probability_publishable": False,
        "can_execute": False,
    }


__all__ = ["SCHEMA_VERSION", "FAMILIES", "NFLContextAdmissionError", "build_pregame_context_packet", "audit_historical_snapshot_admissibility"]
