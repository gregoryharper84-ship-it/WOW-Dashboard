"""NBA historical prop coverage-gap audit (synthetic only) for WOW #2018.

Compares synthetic PropsMadness fixture records against *synthetic* independently
sourced settled NBA labels. This is a method for measuring potential data value,
NOT a real-data import, source entitlement, fit, or output probability.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Mapping, Sequence

from v17.propsmadness_nba_offline_adapter import (
    ALLOWED_STATS, PropsMadnessFixtureHold as Hold,
)

SCHEMA = "WOW_NBA_HISTORICAL_SOURCE_GAP_AUDIT_V1"
SUPPORTED_BASELINE = "SPORTSDATAVERSE_ESPN"
SYNTHETIC_ORIGIN = "SYNTHETIC_TEST_DATA"
MAX_ROWS = 500
CAN_EXECUTE = False


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise Hold(reason)


def _str(value: Any, code: str) -> str:
    _require(isinstance(value, str) and bool(value.strip()), code)
    return value.strip()


def _time(value: Any, code: str) -> datetime:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        raise Hold(code) from exc
    _require(dt.utcoffset() is not None, code)
    return dt


def _count(value: Any) -> int:
    _require(type(value) is int and value >= 0, "NBA_BASELINE_NONINTEGER_LABEL")
    return value


def evaluate_synthetic_historical_gap(
    propsmadness_receipt: Mapping[str, Any],
    baseline_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """No I/O, no network, and no claim of independently authorized NBA data."""
    _require(isinstance(propsmadness_receipt, dict),
             "NBA_GAP_PM_RECEIPT_INVALID")
    _require(propsmadness_receipt.get("status") == "OFFLINE_SYNTHETIC_FIXTURE_VERIFIED"
             and propsmadness_receipt.get("source_provenance_attested") is False
             and propsmadness_receipt.get("crosswalk_attested") is False
             and propsmadness_receipt.get("training_ready") is False
             and propsmadness_receipt.get("probability_publishable") is False
             and propsmadness_receipt.get("can_execute") is False,
             "NBA_GAP_PM_REAL_SOURCE_NOT_APPROVED")
    p_rows = propsmadness_receipt.get("rows")
    _require(isinstance(p_rows, list) and 0 < len(p_rows) <= MAX_ROWS,
             "NBA_GAP_PM_ROWS_INVALID")
    _require(isinstance(baseline_rows, (list, tuple))
             and 0 < len(baseline_rows) <= MAX_ROWS, "NBA_GAP_BASELINE_ROWS_INVALID")

    baseline: dict[tuple[str, str, str], int] = {}
    for record in baseline_rows:
        _require(isinstance(record, dict), "NBA_GAP_BASELINE_SCHEMA_INVALID")
        _require(record.get("origin") == SYNTHETIC_ORIGIN
                 and record.get("source_provider") == SUPPORTED_BASELINE
                 and record.get("sport") == "NBA"
                 and record.get("period") == "FULL_GAME",
                 "NBA_GAP_UNAPPROVED_BASELINE_SOURCE")
        player = _str(record.get("canonical_player_id"),
                      "NBA_GAP_BASELINE_PLAYER_MISSING")
        event = _str(record.get("canonical_event_id"),
                     "NBA_GAP_BASELINE_EVENT_MISSING")
        stat = _str(record.get("stat_type"), "NBA_GAP_BASELINE_STAT_MISSING")
        _require(stat in ALLOWED_STATS, "NBA_GAP_UNSUPPORTED_STAT")
        final = _time(record.get("game_final_at"), "NBA_GAP_FINAL_TIME_INVALID")
        observed = _time(record.get("observed_at"), "NBA_GAP_OBSERVED_TIME_INVALID")
        _require(observed >= final, "NBA_GAP_LABEL_NOT_FINAL")
        key = (player, event, stat)
        _require(key not in baseline, "NBA_GAP_DUPLICATE_BASELINE_LABEL")
        baseline[key] = _count(record.get("actual_value"))

    p_labels: dict[tuple[str, str, str], int] = {}
    context_names = Counter()
    market_keys = set()
    for item in p_rows:
        _require(isinstance(item, dict)
                 and item.get("source_origin") == SYNTHETIC_ORIGIN
                 and item.get("provider") == "PROPSMADNESS"
                 and item.get("sport") == "NBA"
                 and item.get("period") == "FULL_GAME"
                 and item.get("can_execute") is False
                 and item.get("crosswalk_attested") is False,
                 "NBA_GAP_PM_ROWS_NOT_SYNTHETIC")
        key = (str(item.get("canonical_player_id") or ""),
               str(item.get("canonical_event_id") or ""),
               str(item.get("stat_type") or ""))
        _require(all(key) and key[2] in ALLOWED_STATS,
                 "NBA_GAP_PM_IDENTITY_UNSUPPORTED")
        kind = item.get("record_type")
        if kind == "FINAL_OUTCOME":
            _require(item.get("evidence_domain") == "SPORTING_SETTLED_LABEL"
                     and item.get("training_label_authorized") is False,
                     "NBA_GAP_PM_LABEL_PROMOTED")
            _require(key not in p_labels, "NBA_GAP_DUPLICATE_PM_LABEL")
            p_labels[key] = _count(item.get("actual_value"))
        elif kind == "PREGAME_CONTEXT":
            _require(item.get("evidence_domain") == "SPORTING_RESEARCH"
                     and item.get("pregame_feature_eligible") is False,
                     "NBA_GAP_PM_CONTEXT_PROMOTED")
            context_names[str(item.get("metric_name") or "UNKNOWN")] += 1
        elif kind == "MARKET_DISCOVERY":
            _require(item.get("evidence_domain") == "MARKET"
                     and item.get("pregame_feature_eligible") is False,
                     "NBA_GAP_PM_MARKET_PROMOTED")
            market_keys.add(key + (str(item.get("sportsbook") or ""),
                                   item.get("line"), item.get("direction")))
        else:
            raise Hold("NBA_GAP_PM_KIND_UNSUPPORTED")

    common = set(baseline).intersection(p_labels)
    exact_matches = sum(p_labels[k] == baseline[k] for k in common)
    conflicts = len(common) - exact_matches
    pm_only = set(p_labels) - set(baseline)
    baseline_only = set(baseline) - set(p_labels)
    return {
        "schema": SCHEMA, "status": "SYNTHETIC_COMPARISON_COMPLETE",
        "baseline": SUPPORTED_BASELINE,
        "baseline_origin": SYNTHETIC_ORIGIN,
        "propsmadness_origin": SYNTHETIC_ORIGIN,
        "baseline_label_count": len(baseline),
        "propsmadness_label_count": len(p_labels),
        "shared_labels": len(common),
        "agreed_labels": exact_matches,
        "conflicted_labels": conflicts,
        "pm_only_labels": len(pm_only),
        "baseline_only_labels": len(baseline_only),
        "context_field_counts": dict(sorted(context_names.items())),
        "market_offers_seen": len(market_keys),
        "historical_line_archive_verified": False,
        "historical_pregame_availability_verified": False,
        "commercial_data_rights_verified": False,
        "incremental_predictive_value_verified": False,
        "independent_provider_parity_verified": False,
        "training_ready": False,
        "staging_write_authorized": False,
        "model_certified": False,
        "probability_publishable": False,
        "can_execute": False,
    }
