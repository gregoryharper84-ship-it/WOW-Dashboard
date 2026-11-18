"""Governed exact-line spread receipt writer and reader for V17.

This module is the only sanctioned write boundary for
``public.wow_v17_spread_card_receipts``. A receipt enters that relation when a
certified fitted spread model produced it, its three-way distribution and
calibrated bounds are internally coherent, its quote provenance is real, and
``v17.spread_card_admission`` admits it. The writer derives every approval label
itself; a caller cannot talk a receipt past the gate by setting flags.

The writer is insert-only. There is deliberately no update or delete helper, and
the relation rejects both by trigger.

Read-time binding is a separate, later decision: this module stores the quote the
model was scored against, while the Daily SPREAD lane re-binds that quote to the
append-only market ledger and re-runs admission before showing anything.

can_execute=false is invariant. Nothing here places, routes, approves, modifies,
or cancels a wager or market order.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from typing import Any, Mapping

from v17.spread_card_admission import spread_card_admission

CAN_EXECUTE = False
AUTOMATIC_CERTIFICATION = False
AUTOMATIC_PROMOTION = False
GLOBAL_TERMINAL_REDUCER = "V17_TERMINAL_REDUCER"
CONTROLLING_ENGINE = "LLP_TEAM_BETTING_ENGINE"
MARKET_FAMILY = "POINT_SPREAD"
MODEL_CLASS = "CERTIFIED_FITTED"
TABLE = "wow_v17_spread_card_receipts"
SCHEMA_VERSION = "WOW_V17_SPREAD_CARD_RECEIPT_V1"
SUPPORTED_SPORTS = frozenset({"NFL", "NBA", "WNBA", "NCAAF", "NCAAB"})
SUPPORTED_EVENT_STATES = frozenset({"SCHEDULED", "PREGAME"})


class SpreadReceiptRejected(RuntimeError):
    """A candidate receipt failed a governed write-side invariant."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _text(value: Any) -> str:
    return str(value or "").strip()


def _interval(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SpreadReceiptRejected(f"RECEIPT_{name.upper()}_INVALID", f"{name} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed) or not 0.0 <= parsed <= 1.0:
        raise SpreadReceiptRejected(
            f"RECEIPT_{name.upper()}_INVALID", f"{name} must be finite and within [0, 1]"
        )
    return parsed


def _stamp(name: str, value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise SpreadReceiptRejected(
            f"RECEIPT_{name.upper()}_INVALID", f"{name} must be an ISO-8601 timestamp"
        ) from exc
    if parsed.utcoffset() is None:
        raise SpreadReceiptRejected(
            f"RECEIPT_{name.upper()}_INVALID", f"{name} must be timezone-aware"
        )
    return parsed.astimezone(timezone.utc)


def _require_text(candidate: Mapping[str, Any], name: str) -> str:
    value = _text(candidate.get(name))
    if not value:
        raise SpreadReceiptRejected(
            f"RECEIPT_{name.upper()}_REQUIRED", f"{name} is required for a governed receipt"
        )
    return value


def _normalized_spread_receipt(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one candidate receipt and return its canonical write row."""
    sport = _text(candidate.get("sport")).upper()
    if sport not in SUPPORTED_SPORTS:
        raise SpreadReceiptRejected(
            "RECEIPT_SPORT_UNSUPPORTED", f"no governed spread market for sport={sport or 'UNKNOWN'}"
        )
    event_id = _require_text(candidate, "official_event_id")
    home = _require_text(candidate, "home_team")
    away = _require_text(candidate, "away_team")
    if home.upper() == away.upper():
        raise SpreadReceiptRejected("RECEIPT_TEAMS_IDENTICAL", "home and away teams must differ")

    market_family = _text(candidate.get("market_family") or MARKET_FAMILY).upper()
    if market_family != MARKET_FAMILY:
        raise SpreadReceiptRejected(
            "RECEIPT_MARKET_FAMILY_UNSUPPORTED",
            f"this relation stores {MARKET_FAMILY} only; got {market_family}",
        )

    side = _text(candidate.get("exact_line_side") or candidate.get("spread_side")).upper()
    if side not in {"HOME", "AWAY"}:
        raise SpreadReceiptRejected(
            "RECEIPT_SIDE_REQUIRED", "exact_line_side must be HOME or AWAY; never inferred"
        )
    try:
        line = float(candidate.get("exact_signed_spread"))
    except (TypeError, ValueError) as exc:
        raise SpreadReceiptRejected(
            "RECEIPT_EXACT_LINE_REQUIRED", "an exact signed spread is required"
        ) from exc
    if not math.isfinite(line) or not -100.0 < line < 100.0:
        raise SpreadReceiptRejected("RECEIPT_EXACT_LINE_INVALID", "exact signed spread is out of range")
    settlement_basis = _require_text(candidate, "settlement_basis")

    # A receipt without real observed quote provenance cannot be stored: this is
    # what stops a synthetic or reconstructed line from entering the relation.
    line_source = _require_text(candidate, "line_source")
    quote_source_id = _require_text(candidate, "quote_source_id")
    quote_sportsbook = _require_text(candidate, "quote_sportsbook")
    provider_event_id = _require_text(candidate, "provider_event_id")
    quote_observed_at = _stamp("quote_observed_at_utc", candidate.get("quote_observed_at_utc"))

    specialist = _require_text(candidate, "controlling_spread_specialist")
    artifact_version = _require_text(candidate, "model_artifact_version")
    model_version = _require_text(candidate, "model_version")
    calibration_version = _require_text(candidate, "calibration_version")
    model_class = _text(candidate.get("model_class")).upper()
    if model_class != MODEL_CLASS:
        raise SpreadReceiptRejected(
            "RECEIPT_MODEL_CLASS_NOT_CERTIFIED",
            f"model_class must be {MODEL_CLASS}; research/shadow receipts stay out of this relation",
        )
    source_receipt_id = _require_text(candidate, "source_receipt_id")

    event_start = _stamp("event_start_time_utc", candidate.get("event_start_time_utc"))
    model_at = _stamp("model_timestamp", candidate.get("model_timestamp"))
    source_at = _stamp("source_snapshot_timestamp", candidate.get("source_snapshot_timestamp"))
    if not model_at < event_start:
        raise SpreadReceiptRejected(
            "RECEIPT_MODEL_TIMESTAMP_NOT_PREGAME", "model_timestamp must precede the event start"
        )
    if not quote_observed_at < event_start:
        raise SpreadReceiptRejected(
            "RECEIPT_QUOTE_NOT_PREGAME", "quote must have been observed before the event start"
        )
    if source_at > model_at:
        raise SpreadReceiptRejected(
            "RECEIPT_SOURCE_AFTER_MODEL", "source snapshot cannot postdate the model timestamp"
        )

    p_cover = _interval("p_cover", candidate.get("p_cover"))
    p_push = _interval("p_push", candidate.get("p_push"))
    p_not_cover = _interval("p_not_cover", candidate.get("p_not_cover"))
    if abs(p_cover + p_push + p_not_cover - 1.0) > 1e-9:
        raise SpreadReceiptRejected(
            "RECEIPT_DISTRIBUTION_NOT_NORMALIZED", "p_cover + p_push + p_not_cover must equal 1"
        )
    lower = _interval(
        "calibrated_lower_bound",
        candidate.get("calibrated_lower_bound", candidate.get("calibrated_probability_lower_bound")),
    )
    upper = _interval(
        "calibrated_upper_bound",
        candidate.get("calibrated_upper_bound", candidate.get("calibrated_probability_upper_bound")),
    )
    if not 0.0 < lower <= p_cover <= upper < 1.0:
        raise SpreadReceiptRejected(
            "RECEIPT_BOUNDS_INCOHERENT",
            "calibrated bounds must satisfy 0 < lower <= p_cover <= upper < 1",
        )

    event_state = _text(candidate.get("event_state") or "SCHEDULED").upper()
    if event_state not in SUPPORTED_EVENT_STATES:
        raise SpreadReceiptRejected(
            "RECEIPT_EVENT_NOT_PREGAME", f"event_state must be pregame; got {event_state}"
        )
    if _text(candidate.get("score_snapshot_status") or "PASS").upper() != "PASS":
        raise SpreadReceiptRejected(
            "RECEIPT_SCORE_SNAPSHOT_NOT_PREGAME", "score_snapshot_status must be PASS"
        )
    if _text(candidate.get("terminal_authority") or GLOBAL_TERMINAL_REDUCER).upper() != GLOBAL_TERMINAL_REDUCER:
        raise SpreadReceiptRejected(
            "RECEIPT_TERMINAL_AUTHORITY_INVALID",
            f"terminal_authority must be {GLOBAL_TERMINAL_REDUCER}",
        )
    if _text(candidate.get("controlling_engine_identity") or CONTROLLING_ENGINE).upper() != CONTROLLING_ENGINE:
        raise SpreadReceiptRejected(
            "RECEIPT_CONTROLLING_ENGINE_INVALID",
            f"controlling_engine_identity must be {CONTROLLING_ENGINE}",
        )
    # Execution capability may never be asserted by a caller.
    if candidate.get("can_execute") is not False:
        raise SpreadReceiptRejected(
            "RECEIPT_EXECUTION_INVARIANT_UNPROVEN", "can_execute must be explicitly false"
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "sport": sport,
        "official_event_id": event_id,
        "event_start_time_utc": event_start.isoformat(),
        "home_team": home,
        "away_team": away,
        "market_family": MARKET_FAMILY,
        "exact_line_side": side,
        "exact_signed_spread": line,
        "settlement_basis": settlement_basis,
        "line_source": line_source,
        "quote_source_id": quote_source_id,
        "quote_sportsbook": quote_sportsbook,
        "quote_observed_at_utc": quote_observed_at.isoformat(),
        "provider_event_id": provider_event_id,
        "controlling_spread_specialist": specialist,
        "model_artifact_version": artifact_version,
        "model_class": MODEL_CLASS,
        "model_version": model_version,
        "calibration_version": calibration_version,
        "p_cover": p_cover,
        "p_push": p_push,
        "p_not_cover": p_not_cover,
        "calibrated_lower_bound": lower,
        "calibrated_upper_bound": upper,
        "model_timestamp": model_at.isoformat(),
        "source_snapshot_timestamp": source_at.isoformat(),
        "event_state": event_state,
        "score_snapshot_status": "PASS",
        "source_receipt_id": source_receipt_id,
        "terminal_authority": GLOBAL_TERMINAL_REDUCER,
        "controlling_engine_identity": CONTROLLING_ENGINE,
    }


def receipt_key(normalized: Mapping[str, Any]) -> str:
    """Stable identity for one produced receipt (line moves create new keys)."""
    identity = [
        _text(normalized.get("sport")),
        _text(normalized.get("official_event_id")),
        _text(normalized.get("exact_line_side")),
        repr(float(normalized.get("exact_signed_spread"))),
        _text(normalized.get("model_timestamp")),
        _text(normalized.get("model_artifact_version")),
    ]
    return sha256("|".join(identity).encode("utf-8")).hexdigest()


def content_sha256(normalized: Mapping[str, Any]) -> str:
    payload = json.dumps(dict(normalized), sort_keys=True, default=str)
    return sha256(payload.encode("utf-8")).hexdigest()


def admit_spread_receipt(candidate: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate a candidate and require exact-line admission to pass.

    Returns the canonical write row and the admission receipt. Raises
    ``SpreadReceiptRejected`` when admission is blocked, so no caller-supplied
    publishability claim can reach the relation.
    """
    if not isinstance(candidate, Mapping):
        raise SpreadReceiptRejected("RECEIPT_NOT_A_MAPPING", "candidate must be a mapping")
    normalized = _normalized_spread_receipt(candidate)
    admission_candidate = {
        **normalized,
        "lower_bound_required": True,
        "calibrated_lower_bound": normalized["calibrated_lower_bound"],
        "calibrated_probability_lower_bound": normalized["calibrated_lower_bound"],
        "calibrated_probability_upper_bound": normalized["calibrated_upper_bound"],
        # Derived here, never taken from the caller.
        "probability_publishable": True,
        "rank_eligible": True,
        "can_execute": False,
    }
    admission = spread_card_admission(
        admission_candidate,
        requested_event_id=normalized["official_event_id"],
        requested_side=normalized["exact_line_side"],
        requested_signed_spread=normalized["exact_signed_spread"],
    )
    if admission["card_admission_eligible"] is not True:
        raise SpreadReceiptRejected(
            "RECEIPT_ADMISSION_BLOCKED",
            "exact-line admission blocked the receipt: " + ",".join(admission["blockers"]),
        )
    row = {
        **normalized,
        "admission_status": "PASS",
        "probability_publishable": True,
        "rank_eligible": True,
        "can_execute": False,
        "receipt_key": receipt_key(normalized),
        "content_sha256": content_sha256(normalized),
    }
    return row, admission


def persist_certified_spread_receipt(client: Any, candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Insert one admitted receipt. Insert-only and idempotent on receipt_key."""
    row, admission = admit_spread_receipt(candidate)
    try:
        client.table(TABLE).upsert(
            [row], on_conflict="receipt_key", ignore_duplicates=True
        ).execute()
    except Exception as exc:  # noqa: BLE001 - typed persistence boundary
        return {
            "status": "PERSISTENCE_UNAVAILABLE",
            "code": f"SPREAD_RECEIPT_PERSISTENCE_UNAVAILABLE:{type(exc).__name__}",
            "receipt_key": row["receipt_key"],
            "admission": admission,
            "updates_allowed": False,
            "can_execute": False,
        }
    return {
        "status": "PERSISTED",
        "code": "SPREAD_RECEIPT_PERSISTED",
        "receipt_key": row["receipt_key"],
        "content_sha256": row["content_sha256"],
        "admission": admission,
        "updates_allowed": False,
        "can_execute": False,
    }


def read_certified_spread_receipt(
    client: Any, *, sport: str, event_id: str
) -> tuple[dict[str, Any] | None, str | None]:
    """Read the newest admitted receipt for one event, or a typed reason code."""
    normalized_sport = _text(sport).upper()
    normalized_event = _text(event_id)
    if not normalized_sport or not normalized_event:
        return None, "SPREAD_RECEIPT_EVENT_IDENTITY_INCOMPLETE"
    try:
        response = (
            client.table(TABLE)
            .select("*")
            .eq("sport", normalized_sport)
            .eq("official_event_id", normalized_event)
            .order("model_timestamp", desc=True)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 - typed registry boundary
        return None, f"SPREAD_RECEIPT_REGISTRY_UNAVAILABLE:{type(exc).__name__}"
    rows = getattr(response, "data", None) or []
    first = rows[0] if rows else None
    if not isinstance(first, dict):
        return None, "SPREAD_RECEIPT_NOT_PUBLISHED"
    if _text(first.get("admission_status")).upper() != "PASS":
        return None, "SPREAD_RECEIPT_NOT_ADMITTED"
    return dict(first), None


__all__ = [
    "AUTOMATIC_CERTIFICATION",
    "AUTOMATIC_PROMOTION",
    "CAN_EXECUTE",
    "CONTROLLING_ENGINE",
    "GLOBAL_TERMINAL_REDUCER",
    "MARKET_FAMILY",
    "MODEL_CLASS",
    "SCHEMA_VERSION",
    "SUPPORTED_SPORTS",
    "TABLE",
    "SpreadReceiptRejected",
    "admit_spread_receipt",
    "content_sha256",
    "persist_certified_spread_receipt",
    "read_certified_spread_receipt",
    "receipt_key",
]
