"""Prospective, append-only DAFT/LLP research evaluation.

No postgame field enters a pre-event prediction snapshot. Historical Discord
capture is never upgraded into a prospective model observation.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping

from v17.daft_research_market import (
    CAN_EXECUTE, comparable_clv, expected_return, finite_number,
    same_contract, timestamp,
)


def prospective_snapshot(observation: Mapping[str, Any]) -> dict[str, Any]:
    captured = timestamp(observation.get("captured_at"))
    published = timestamp(observation.get("published_at"))
    start = timestamp(observation.get("event_start_at"))
    if (captured is None or published is None or start is None or
        published > captured or captured >= start or
        observation.get("research_eligibility") != "PROSPECTIVE"):
        raise ValueError("Retrospective or untimed capture cannot enter prospective shadow ledger")
    if not isinstance(observation.get("contract"), Mapping):
        raise ValueError("Exact immutable contract required")
    from v17.daft_research_market import contract_key
    if contract_key(observation["contract"]) is None:
        raise ValueError("Incomplete contract cannot enter prospective ledger")
    if observation.get("official_result") is not None or observation.get("independent_result") is not None:
        raise ValueError("Pregame snapshot cannot contain future outcomes")
    fields = {
        "schema_version": "WOW_DAFT_SHADOW_PREGAME_V1",
        "captured_at": observation["captured_at"],
        "published_at": observation["published_at"],
        "event_start_at": observation["event_start_at"],
        "contract": dict(observation["contract"]),
        "candidate_id": observation.get("candidate_id"),
        "prediction_id": observation.get("prediction_id"),
        "source_message_id": observation.get("message_id"),
        "offer_decimal_odds": observation.get("offer_decimal_odds"),
        "offer_quote_at": observation.get("offer_quote_at"),
        "source_estimated_probability": observation.get("source_estimated_probability"),
        "governed_calibrated_probability": observation.get("governed_calibrated_probability"),
        "governed_calibrated_lower_bound": observation.get("governed_calibrated_lower_bound"),
        "terminal_status_at_capture": observation.get("terminal_status_at_capture"),
        "can_execute": False,
    }
    serialized = json.dumps(fields, sort_keys=True, separators=(",", ":"), default=str)
    fields["immutable_snapshot_id"] = hashlib.sha256(serialized.encode()).hexdigest()
    return fields


def append_private_jsonl(path: str | Path, record: Mapping[str, Any]) -> None:
    """Append-only private storage, explicitly outside checkout; no network/database write."""
    target = Path(path)
    if (not target.is_absolute() or target.is_symlink() or
            target.resolve().is_relative_to(Path.cwd().resolve())):
        raise ValueError("Private ledgers must live outside the public checkout")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(target), flags, 0o600)
    try:
        data = (json.dumps(dict(record), sort_keys=True, default=str) + "\n").encode("utf-8")
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def settle_shadow_snapshot(snapshot: Mapping[str, Any],
                           settlement: Mapping[str, Any]) -> dict[str, Any]:
    """Append a separate outcome record; never overwrite the pregame snapshot."""
    if not same_contract(snapshot.get("contract"), settlement.get("contract")):
        return {"status": "EXACT_CONTRACT_UNRESOLVED", "graded": False,
                "can_execute": False}
    if not settlement.get("independently_verified") or not settlement.get("settlement_source"):
        return {"status": "INDEPENDENT_RESULT_UNVERIFIED", "graded": False,
                "source_reported_result": settlement.get("source_reported_result"),
                "can_execute": False}
    result = settlement.get("official_result")
    if result not in ("WIN", "LOSS", "PUSH", "VOID"):
        return {"status": "SETTLEMENT_UNRESOLVED", "graded": False, "can_execute": False}
    price = finite_number(snapshot.get("offer_decimal_odds"))
    quote = timestamp(snapshot.get("offer_quote_at"))
    entry = ({"contract": snapshot.get("contract"), "decimal_odds": price,
              "quote_at": snapshot.get("offer_quote_at"),
              "event_start_at": snapshot.get("event_start_at")} if price is not None else {})
    close = settlement.get("closing_offer")
    clv = comparable_clv(entry, close) if isinstance(close, Mapping) else None
    actual_return = (price - 1.0 if result == "WIN" and price is not None and price > 1.0
                     else -1.0 if result == "LOSS" and price is not None and price > 1.0
                     else 0.0 if result in ("PUSH", "VOID") else None)
    # Missing/invalid contemporaneous quote forbids executable-price ROI claims.
    if quote is None or timestamp(snapshot.get("captured_at")) is None or quote > timestamp(snapshot["captured_at"]):
        actual_return = None
    p = finite_number(snapshot.get("governed_calibrated_probability"))
    brier = (p - (1.0 if result == "WIN" else 0.0)) ** 2 if (
        p is not None and 0 < p < 1 and result in ("WIN", "LOSS")
        and snapshot.get("prediction_id")) else None
    return {
        "schema_version": "WOW_DAFT_SHADOW_SETTLEMENT_V1",
        "immutable_snapshot_id": snapshot.get("immutable_snapshot_id"),
        "status": "INDEPENDENT_SETTLEMENT_RECORDED", "graded": True,
        "official_result": result,
        "source_reported_result": settlement.get("source_reported_result"),
        "source_result_agrees": settlement.get("source_reported_result") == result
        if settlement.get("source_reported_result") in ("WIN", "LOSS", "PUSH", "VOID") else None,
        "settlement_source": settlement["settlement_source"],
        "recorded_at": settlement.get("recorded_at"),
        "available_price_return_per_unit": actual_return,
        "comparable_clv_decimal": clv,
        "governed_brier_if_immutable_model": brier,
        "can_execute": CAN_EXECUTE,
    }


def evaluation_summary(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Report scope/uncertainty; never infer an incremental LLP edge from a thin ledger."""
    settled = [r for r in rows if r.get("graded") is True and
               r.get("status") == "INDEPENDENT_SETTLEMENT_RECORDED"]
    priced = [finite_number(r.get("available_price_return_per_unit")) for r in settled]
    priced = [value for value in priced if value is not None]
    clv = [finite_number(r.get("comparable_clv_decimal")) for r in settled]
    clv = [value for value in clv if value is not None]
    matched = [r for r in settled if r.get("source_result_agrees") is not None]
    return {
        "settled_exact_records": len(settled), "priced_records": len(priced),
        "unit_flat_return_at_available_price": sum(priced) if priced else None,
        "unit_flat_roi_at_available_price": sum(priced) / len(priced) if priced else None,
        "comparable_clv_n": len(clv),
        "mean_comparable_clv_decimal": sum(clv) / len(clv) if clv else None,
        "source_official_agreement_n": sum(r["source_result_agrees"] is True for r in matched),
        "source_official_comparison_n": len(matched),
        "incremental_value_over_llp": None,
        "incremental_value_reason": "REQUIRES_PAIRED_CHRONOLOGICAL_HOLDOUT_VS_LLP_ALONE",
        "can_execute": False,
    }
