"""Read-only, fail-closed bridge for V17 prediction-receipts/lookup responses.

Accepts an already-returned response from the V17 authenticated lookup endpoint.
It cannot establish whether the caller authenticated the response; it never
constructs a missing model artifact, opponent, player, period or exact-line adapter.
The existing endpoint does not return every required Class-C provenance field.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Mapping

from v17.accuracy_improvement_controller import (
    _offer, _p, _str, _time, ExperimentInputError, TERMINAL_AUTHORITY,
)

CAN_EXECUTE = False
AUTOMATIC_PROMOTION = False
PROBABILITY_PUBLISHABLE = False


class ReceiptReadinessError(ValueError):
    pass


def _check_prediction(offer: Mapping[str, str], match: Mapping[str, Any]) -> str:
    if match.get("can_execute") is not False:
        return "RECEIPT_EXECUTION_FENCE_NOT_PROVEN"
    if match.get("governed_prediction_table") != "wow_predictions":
        return "RECEIPT_LEDGER_PROVENANCE_UNEXPECTED"
    if match.get("is_immutable_pregame") is not True:
        return "IMMUTABLE_PREGAME_NOT_PROVEN"
    row = match.get("prediction")
    if not isinstance(row, Mapping):
        return "PREDICTION_OBJECT_MISSING"
    if not match.get("governed_prediction_id") or (
        match["governed_prediction_id"] != row.get("prediction_id")
    ):
        return "PREDICTION_IDENTITY_CONFLICT"
    # The lookup uses display-player names, not certified canonical athlete IDs.
    # Do not infer player identity from a label or substring of the offer.
    if not offer.get("canonical_player_name"):
        return "CANONICAL_PLAYER_LOOKUP_BINDING_MISSING"
    for actual, expected in (
        (row.get("event_id"), offer["event_id"]),
        (row.get("sport"), offer["sport"]),
        (row.get("player"), offer["canonical_player_name"]),
        (row.get("team"), offer["team_id"]),
        (row.get("opponent"), offer["opponent_id"]),
        (row.get("stat_type"), offer["stat"]),
        (row.get("direction"), offer["direction"]),
    ):
        if actual != expected:
            return "PREDICTION_EXACT_IDENTITY_CONFLICT"
    if offer["offer_type"] == "AT_LEAST":
        return "AT_LEAST_CERTIFIED_LINE_ADAPTER_REQUIRED"
    if offer["offer_type"] == "COMPOUND":
        return "COMPOUND_CERTIFIED_RECEIPT_ADAPTER_REQUIRED"
    try:
        if float(row.get("line")) != float(offer["threshold_label"]):
            return "PREDICTION_EXACT_LINE_CONFLICT"
    except (ValueError, TypeError):
        return "PREDICTION_EXACT_LINE_NOT_AVAILABLE"
    # The current immutable lookup projection does not expose a period binding.
    # An absent period must block; never default silently to full-game.
    if row.get("period") != offer["period"]:
        return "PREDICTION_PERIOD_BINDING_MISSING_OR_CONFLICT"
    try:
        event_start = _time(offer["event_start_utc"], "INVALID_OFFER_START")
        if _time(row.get("event_start_time"), "INVALID_LEDGER_START") != event_start:
            return "PREDICTION_EVENT_START_CONFLICT"
        created = _time(row.get("created_at"), "PREDICTION_CREATED_AT_MISSING")
        model_ts = _time(row.get("model_timestamp"), "PREDICTION_MODEL_TIME_MISSING")
        locked = _time(row.get("locked_at"), "PREDICTION_LOCK_MISSING")
        if not created < event_start or not model_ts < event_start or not locked <= event_start:
            return "PREDICTION_PREGAME_CHRONOLOGY_INVALID"
        _str(row.get("source_snapshot_id"), "PREDICTION_SNAPSHOT_MISSING")
        _p(match.get("calibrated_probability"), "PREDICTION_CALIBRATION_INVALID")
        bound = _p(match.get("calibrated_lower_bound"), "PREDICTION_BOUND_INVALID")
        if bound > match["calibrated_probability"]:
            return "PREDICTION_BOUND_EXCEEDS_CALIBRATED"
    except ExperimentInputError as exc:
        return str(exc)
    # These links are not present in the current lookup projection; surface the
    # evidence gap rather than deriving them from model names or probabilities.
    if not all(row.get(key) for key in (
        "model_artifact_id", "calibrator_id", "specialist_id"
    )):
        return "FITTED_SPECIALIST_ARTIFACT_LINKAGE_MISSING"
    return "OFFLINE_RECEIPT_FIELDS_RECONCILED_SOURCE_UNAUTHENTICATED"


def inspect_lookup_readiness(
    original_offers: list[Mapping[str, Any]],
    lookup_response: Mapping[str, Any],
) -> dict[str, Any]:
    """Preserve every source market and classify exact receipt readiness.

    This is an *offline* import boundary, not a source of authenticated receipts.
    Never feed its output to the governed probability publisher.
    """
    if not isinstance(original_offers, list) or not original_offers:
        raise ReceiptReadinessError("ORIGINAL_OFFERS_REQUIRED")
    try:
        offers = [_offer(raw) for raw in original_offers]
    except ExperimentInputError as exc:
        raise ReceiptReadinessError(str(exc)) from exc
    keys = [o["offer_id"] for o in offers]
    if len(keys) != len(set(keys)):
        raise ReceiptReadinessError("DUPLICATE_ORIGINAL_OFFER_ID")
    if not isinstance(lookup_response, Mapping):
        raise ReceiptReadinessError("LOOKUP_RESPONSE_REQUIRED")
    if lookup_response.get("can_execute") is not False:
        raise ReceiptReadinessError("LOOKUP_EXECUTION_FENCE_NOT_PROVEN")
    raw_rows = lookup_response.get("rows")
    if not isinstance(raw_rows, list):
        raise ReceiptReadinessError("LOOKUP_ROWS_REQUIRED")
    if lookup_response.get("rows_in") != len(keys) or lookup_response.get("reconciliation_pass") is not True:
        raise ReceiptReadinessError("LOOKUP_BATCH_RECONCILIATION_FAILED")
    by_key: dict[str, Any] = {}
    for r in raw_rows:
        if not isinstance(r, Mapping) or r.get("row_key") not in keys:
            raise ReceiptReadinessError("LOOKUP_UNEXPECTED_ROW_KEY")
        key = r["row_key"]
        if key in by_key:
            raise ReceiptReadinessError("LOOKUP_DUPLICATE_ROW_KEY")
        by_key[key] = r
    outcomes = []
    for offer in offers:
        key = offer["offer_id"]
        found = by_key.get(key)
        if found is None:
            code = "LOOKUP_ROW_NOT_RETURNED"
        elif found.get("can_execute") is not False:
            code = "LOOKUP_ROW_EXECUTION_FENCE_NOT_PROVEN"
        elif found.get("status") != "MATCHED":
            code = _str(found.get("code"), "LOOKUP_NONMATCHED_UNKNOWN_STATUS")
        elif found.get("match_count") != 1 or not isinstance(found.get("matches"), list) or len(found["matches"]) != 1:
            code = "LOOKUP_MATCH_COUNT_INCONSISTENT"
        else:
            code = _check_prediction(offer, found["matches"][0])
        outcomes.append({
            "offer_id": key, "original_label": offer["original_label"],
            "code": code, "status": "HELD",
        })
    return {
        "status": "OFFLINE_RECEIPT_READINESS_ONLY",
        "can_execute": False, "automatic_promotion": False,
        "probability_publishable": False, "terminal_authority": TERMINAL_AUTHORITY,
        "source_authentication_proven": False,
        "original_offer_count": len(keys), "reconciled_offer_count": len(outcomes),
        "codes": dict(sorted(Counter(o["code"] for o in outcomes).items())),
        "rows": outcomes,
    }
