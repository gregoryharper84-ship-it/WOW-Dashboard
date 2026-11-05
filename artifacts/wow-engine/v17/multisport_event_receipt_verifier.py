"""Versioned, opt-in LLP multisport receipt readback, not a prediction producer.

Nothing can qualify without an exact immutable event receipt and a separate
trusted reducer verification RPC. This module never touches the MLB ledger,
models, or wager execution. Absence of the RPC is an infrastructure HOLD.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping
from uuid import UUID

CAN_EXECUTE = False
TABLE = "wow_v17_multisport_event_prediction_receipts"
RPC = "wow_v17_verify_multisport_event_receipt"
VERSION = "WOW_TEAM_EVENT_RECEIPT_V1"
BASIS = {
    "NFL": "FULL_GAME_INCLUDING_OVERTIME",
    "NBA": "FULL_GAME_INCLUDING_OVERTIME",
    "NHL": "FULL_GAME_MONEYLINE_INCLUDING_OVERTIME_SHOOTOUT",
}
ID_HOLD = "LLP_EVENT_PREDICTION_ID_UNVERIFIED"
LOOKUP_HOLD = "LLP_EVENT_PREDICTION_LOOKUP_UNAVAILABLE"
MISSING_HOLD = "LLP_EVENT_PREDICTION_LOOKUP_NOT_PERFORMED"
ROW_HOLD = "LLP_EVENT_PREDICTION_NOT_EXACTLY_ONE"
MATCH_HOLD = "LLP_EVENT_PREDICTION_RECEIPT_MISMATCH"
NUM_HOLD = "LLP_EVENT_PREDICTION_PROBABILITY_MISMATCH"
SHA = re.compile(r"^[a-f0-9]{64}$")
FIELDS = (
    "event_prediction_id created_at schema_version sport official_event_id "
    "event_start_time home_team away_team selected_participant market_family "
    "settlement_basis source_snapshot_id source_snapshot_timestamp "
    "score_snapshot_id model_artifact_id model_version model_artifact_sha256 "
    "calibration_artifact_id calibration_version calibration_artifact_sha256 "
    "model_timestamp locked_at terminal_reduced_at reducer_receipt_id "
    "reducer_receipt_sha256 terminal_authority terminal_status terminal_label "
    "calibrated_home_probability calibrated_home_lower_bound calibrated_home_upper_bound "
    "calibrated_away_probability calibrated_away_lower_bound calibrated_away_upper_bound "
    "rank_eligible probability_publishable can_execute"
).split()


def _uid(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        canonical = str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        return None
    return canonical if canonical == value.lower() else None


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, (str, datetime)):
        return None
    try:
        d = value if isinstance(value, datetime) else datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
        return d.astimezone(timezone.utc) if d.utcoffset() is not None else None
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _numeric(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        n = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return n if n.is_finite() else None


def verify_multisport_event_receipt(
    db: Any, *, event: Mapping[str, Any], winner: Mapping[str, Any]
) -> str | None:
    """Return a typed hold; None requires both ledger and trusted reducer RPC.

    A unit-test double returning PASS is not sporting certification. Actual
    producer/terminal admission, database roles, and real PG integration need
    independent acceptance before this verifier is activated in production.
    """
    rid = _uid(winner.get("source_receipt_id"))
    score_id = _uid(winner.get("score_snapshot_id"))
    terminal_id = _uid(winner.get("reducer_receipt_id"))
    if rid is None or score_id is None or terminal_id is None:
        return ID_HOLD
    sport = _text(event.get("sport")).upper()
    if sport not in BASIS:
        return MATCH_HOLD
    if db is None:
        return MISSING_HOLD
    try:
        res = (db.table(TABLE).select(",".join(FIELDS))
               .eq("event_prediction_id", rid).limit(2).execute())
        rows = getattr(res, "data", None)
    except Exception:
        return LOOKUP_HOLD
    if not isinstance(rows, list):
        return LOOKUP_HOLD
    if len(rows) != 1 or not isinstance(rows[0], Mapping):
        return ROW_HOLD
    saved = rows[0]
    home = _text(event.get("home_team")).upper()
    away = _text(event.get("away_team")).upper()
    selected = _text(winner.get("side")).upper()
    if not home or not away or home == away or selected not in (home, away):
        return MATCH_HOLD
    stamps = [_utc(saved.get(k)) for k in (
        "source_snapshot_timestamp", "model_timestamp", "locked_at",
        "terminal_reduced_at", "created_at", "event_start_time")]
    if any(t is None for t in stamps):
        return MATCH_HOLD
    source, model, locked, reduced, inserted, start = stamps
    if not source <= model <= locked <= reduced <= inserted < start:
        return MATCH_HOLD
    if (start != _utc(event.get("event_start_time_utc"))
        or source != _utc(winner.get("source_snapshot_timestamp"))
        or model != _utc(winner.get("model_timestamp"))):
        return MATCH_HOLD

    matched = (
        ("official_event_id", "official_event_id"),
        ("source_snapshot_id", "source_snapshot_id"),
        ("model_artifact_id", "model_artifact_id"),
        ("model_version", "model_version"),
        ("model_artifact_sha256", "model_artifact_sha256"),
        ("calibration_artifact_id", "calibration_artifact_id"),
        ("calibration_version", "calibration_version"),
        ("calibration_artifact_sha256", "calibration_artifact_sha256"),
        ("reducer_receipt_sha256", "reducer_receipt_sha256"),
    )
    if any(not _text(saved.get(k)) or _text(saved.get(k)) != _text(winner.get(w))
           for k, w in matched):
        return MATCH_HOLD
    if any(not SHA.fullmatch(_text(saved.get(k))) for k in (
        "model_artifact_sha256", "calibration_artifact_sha256", "reducer_receipt_sha256")):
        return MATCH_HOLD
    if any((
        _uid(saved.get("event_prediction_id")) != rid,
        _uid(saved.get("score_snapshot_id")) != score_id,
        _uid(saved.get("reducer_receipt_id")) != terminal_id,
        _text(saved.get("schema_version")) != VERSION,
        _text(saved.get("sport")).upper() != sport,
        _text(saved.get("market_family")) != "OUTRIGHT_WINNER",
        _text(saved.get("settlement_basis")) != BASIS[sport],
        _text(saved.get("home_team")).upper() != home,
        _text(saved.get("away_team")).upper() != away,
        _text(saved.get("selected_participant")).upper() != selected,
        _text(saved.get("terminal_authority")) != "V17_TERMINAL_REDUCER",
        _text(winner.get("terminal_authority")) != "V17_TERMINAL_REDUCER",
        _text(saved.get("terminal_status")) != "PASS",
        _text(saved.get("terminal_label")) != "FINAL_APPROVED",
        saved.get("rank_eligible") is not True,
        saved.get("probability_publishable") is not True,
        saved.get("can_execute") is not False,
        winner.get("rank_eligible") is not True,
        winner.get("probability_publishable") is not True,
        winner.get("can_execute") is not False,
    )):
        return MATCH_HOLD

    probabilities = {}
    for side in ("home", "away"):
        triple = [_numeric(saved.get(f"calibrated_{side}_{suffix}"))
                  for suffix in ("lower_bound", "probability", "upper_bound")]
        if any(n is None for n in triple):
            return NUM_HOLD
        lower, point, upper = triple
        if not Decimal(0) < lower <= point <= upper < Decimal(1):
            return NUM_HOLD
        probabilities[side] = point
    if abs(sum(probabilities.values()) - Decimal(1)) > Decimal("0.000001"):
        return NUM_HOLD
    side = "home" if selected == home else "away"
    for suffix, key in (
        ("probability", "calibrated_probability"),
        ("lower_bound", "calibrated_probability_lower_bound"),
        ("upper_bound", "calibrated_probability_upper_bound"),
    ):
        stored = _numeric(saved.get(f"calibrated_{side}_{suffix}"))
        claimed = _numeric(winner.get(key))
        if stored is None or claimed is None or stored != claimed:
            return NUM_HOLD

    # The ledger row is not its own reduction proof. In production this RPC
    # must be independently implemented against the authentic reducer receipt.
    try:
        reply = getattr(
            db.rpc(RPC, {"p_event_prediction_id": rid}).execute(), "data", None)
    except Exception:
        return LOOKUP_HOLD
    if isinstance(reply, list):
        reply = reply[0] if len(reply) == 1 else None
    if not isinstance(reply, Mapping):
        return LOOKUP_HOLD
    if any((
        reply.get("status") != "PASS",
        reply.get("terminal_label") != "FINAL_APPROVED",
        reply.get("terminal_authority") != "V17_TERMINAL_REDUCER",
        reply.get("can_execute") is not False,
        reply.get("rank_eligible") is not True,
        reply.get("probability_publishable") is not True,
        _uid(reply.get("event_prediction_id")) != rid,
        _uid(reply.get("score_snapshot_id")) != score_id,
        _uid(reply.get("reducer_receipt_id")) != terminal_id,
        _text(reply.get("reducer_receipt_sha256")) !=
            _text(saved.get("reducer_receipt_sha256")),
    )):
        return MATCH_HOLD
    return None
