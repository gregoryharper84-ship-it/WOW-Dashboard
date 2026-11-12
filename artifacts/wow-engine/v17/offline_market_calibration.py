"""Research-only, point-in-time binary model-vs-market calibration audit.

This module consumes *supplied* immutable-forecast claims, a same-time sportsbook
pair and settled outcomes. It does not download data, reconstruct source rights,
fit sporting probabilities, verify source signatures, promote, persist or execute.
All metrics are descriptive and UNATTESTED until independent provenance review.
A closing quote after forecast time is not a same-time baseline.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from itertools import islice
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

CAN_EXECUTE = False
SCHEMA = "WOW_V17_OFFLINE_MARKET_CALIBRATION_V1"
MIN_EVIDENCE = 30
MAX_ROWS = 100000
KEYS = ("sport", "event_id", "market", "period", "selection", "line", "direction")
SHA_DIGITS = set("0123456789abcdef")


def _timestamp(value: Any) -> datetime | None:
    """Require a fully specified offset-aware instant, never guess dates."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.utcoffset() is not None else None
    except (ValueError, OverflowError):
        return None


def _decimal_odds(value: Any) -> float | None:
    """Reject malformed, Boolean, infinite or nonpositive decimal prices."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) and 1.0 < parsed <= 1000000 else None


def _probability(value: Any) -> float | None:
    """A model probability may not be manufactured from the market quote."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) and 0 < parsed < 1 else None


def _digest(value: Any) -> bool:
    """Validate syntax only; caller-supplied hashes are not source attestation."""
    return isinstance(value, str) and len(value) == 64 and set(value) <= SHA_DIGITS


def _identity(row: Mapping[str, Any]) -> tuple[str, ...] | None:
    """Refuse blank identity tokens or guessed name/date matching."""
    parts = tuple(row.get(field) for field in KEYS)
    if any(not isinstance(v, str) or not v.strip() or v != v.strip() for v in parts):
        return None
    if not all(isinstance(row.get(key), str) and row[key].strip()
               and row[key] == row[key].strip() for key in
               ("home_team_id", "away_team_id", "quote_home_team_id",
                "quote_away_team_id", "settled_home_team_id", "settled_away_team_id")):
        return None
    if row["home_team_id"] == row["away_team_id"]:
        return None
    # Each evidence stream must independently name the identical event and
    # selection; alias/date joins are not a substitute for a canonical ID.
    if (row.get("quote_event_id") != row["event_id"]
            or row.get("settled_event_id") != row["event_id"]
            or row.get("settled_market") != row["market"]
            or row.get("settled_period") != row["period"]
            or row.get("settled_selection") != row["selection"]
            or row.get("settled_line") != row["line"]
            or row.get("settled_direction") != row["direction"]
            or row.get("quote_market") != row["market"]
            or row.get("quote_period") != row["period"]
            or row.get("quote_direction") != row["direction"]
            or row.get("settled_home_team_id") != row["home_team_id"]
            or row.get("settled_away_team_id") != row["away_team_id"]):
        return None
    if (row["home_team_id"] != row["quote_home_team_id"]
            or row["away_team_id"] != row["quote_away_team_id"]):
        return None
    if row.get("quote_selection") != row["selection"] or row.get("quote_line") != row["line"]:
        return None
    return parts


def _binary_loss(probability: float, outcome: int) -> tuple[float, float]:
    """Brier and finite binary log loss for an observed outcome."""
    # The source package already requires strict 0 < p < 1. Clipping here
    # would understate loss for a valid but extremely incorrect probability.
    return ((probability - outcome) ** 2,
            -(outcome * math.log(probability) + (1 - outcome) * math.log1p(-probability)))


def _reliability(values: Sequence[tuple[float, int]]) -> list[dict[str, Any]]:
    """Fixed ten-bin descriptive reliability for supplied probabilities."""
    buckets: list[list[tuple[float, int]]] = [[] for _ in range(10)]
    for probability, outcome in values:
        buckets[min(9, int(probability * 10))].append((probability, outcome))
    return [{
        "bin_lower": i / 10, "bin_upper": (i + 1) / 10,
        "n": len(bucket),
        "mean_probability": sum(p for p, _ in bucket) / len(bucket) if bucket else None,
        "observed_rate": sum(y for _, y in bucket) / len(bucket) if bucket else None,
    } for i, bucket in enumerate(buckets)]


def evaluate_binary_market_baseline(rows: Sequence[Mapping[str, Any]], *,
                                    minimum_n: int = MIN_EVIDENCE,
                                    max_quote_age_minutes: int = 60) -> dict[str, Any]:
    """Compare externally supplied fitted predictions and same-time de-vig odds.

    Two outcomes at the *same bookmaker, timestamp, event and line* are required.
    Three-way soccer or binary ties/pushes/voids are held, never silently graded.
    Snapshot staleness is research-only, not a production qualification threshold.
    """
    if not isinstance(rows, (list, tuple)) or len(rows) > MAX_ROWS:
        raise ValueError("PAIR_REPLAY_INPUT_INVALID")
    if type(minimum_n) is not int or minimum_n < MIN_EVIDENCE:
        raise ValueError("PAIR_REPLAY_MINIMUM_N_INVALID")
    if type(max_quote_age_minutes) is not int or not 1 <= max_quote_age_minutes <= 1440:
        raise ValueError("PAIR_REPLAY_INPUT_INVALID")

    identity_counts = Counter(key for row in rows if isinstance(row, Mapping)
                              if (key := _identity(row)) is not None)
    prediction_receipt_counts = Counter(
        row.get("immutable_prediction_id") for row in rows
        if isinstance(row, Mapping)
        and isinstance(row.get("immutable_prediction_id"), str)
        and row["immutable_prediction_id"].strip()
    )
    # Read each primary event/team assertion *before* per-row quote and
    # settlement checks. A conflicting row with a bad quote must not hide its
    # contradictory event identity and allow another side into the metrics.
    # Missing/blank assertions do not create fabricated conflicts.
    event_contexts: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        sport, event_id, home, away = (
            row.get("sport"), row.get("event_id"),
            row.get("home_team_id"), row.get("away_team_id"),
        )
        if not all(isinstance(v, str) and v.strip() and v == v.strip() for v in
                   (sport, event_id, home, away)):
            continue
        if home == away:
            continue
        event_contexts.setdefault((sport, event_id), set()).add((home, away))
    statuses: list[dict[str, Any]] = []
    pairs: list[tuple[float, float, int]] = []
    for index, row in enumerate(rows):
        reason = "REPLAY_RECORD_INVALID"
        key = _identity(row) if isinstance(row, Mapping) else None
        if key is not None:
            reason = "REPLAY_DUPLICATE_THESIS" if identity_counts[key] > 1 else "REPLAY_CANDIDATE_UNVERIFIED"
            if reason == "REPLAY_DUPLICATE_THESIS":
                pass
            elif len(event_contexts[(row["sport"], row["event_id"])]) > 1:
                reason = "REPLAY_EVENT_CONTEXT_CONFLICT"
            elif row.get("partition") != "HOLDOUT":
                reason = "REPLAY_NOT_HOLDOUT"
            elif not isinstance(row.get("immutable_prediction_id"), str) or not row["immutable_prediction_id"].strip():
                reason = "REPLAY_MISSING_IMMUTABLE_RECEIPT"
            elif prediction_receipt_counts[row["immutable_prediction_id"]] > 1:
                reason = "REPLAY_PREDICTION_RECEIPT_REUSED"
            elif not _digest(row.get("model_artifact_checksum")):
                reason = "REPLAY_FITTED_CHECKSUM_MISSING_OR_INVALID"
            elif (not _digest(row.get("quote_source_sha256"))
                  or not isinstance(row.get("quote_book"), str)
                  or not row["quote_book"].strip()
                  or row["quote_book"] != row["quote_book"].strip()):
                reason = "REPLAY_MARKET_QUOTE_INVALID"
            elif (not _digest(row.get("opposite_quote_source_sha256"))
                  or not isinstance(row.get("opposite_quote_book"), str)
                  or not row["opposite_quote_book"].strip()
                  or row["opposite_quote_book"] != row["opposite_quote_book"].strip()
                  or row.get("opposite_quote_book") != row.get("quote_book")
                  or row.get("opposite_quote_event_id") != row["event_id"]
                  or row.get("opposite_quote_market") != row["market"]
                  or row.get("opposite_quote_period") != row["period"]
                  or row.get("opposite_quote_line") != row["line"]
                  or not isinstance(row.get("opposite_quote_selection"), str)
                  or not isinstance(row.get("opposite_quote_direction"), str)
                  or not row["opposite_quote_selection"].strip()
                  or not row["opposite_quote_direction"].strip()
                  # Conservative first pilot: only HOME-WIN vs AWAY-WIN
                  # with no line can be proven complementary by this schema.
                  # Spreads, totals, props and same-outcome aliases HOLD until
                  # their explicit market-specific settlement contracts exist.
                  or row["market"] != "MONEYLINE"
                  or row["line"] != "NO_LINE"
                  or row["selection"] not in ("HOME", "AWAY")
                  or row["quote_selection"] != row["selection"]
                  or row["direction"] != "WIN"
                  or row["quote_direction"] != "WIN"
                  or row["opposite_quote_direction"] != "WIN"
                  or row["opposite_quote_selection"] != (
                      "AWAY" if row["selection"] == "HOME" else "HOME")
                  or _timestamp(row.get("opposite_quote_asof_utc")) is None
                  or _timestamp(row.get("opposite_quote_asof_utc"))
                     != _timestamp(row.get("quote_asof_utc"))):
                reason = "REPLAY_MARKET_QUOTE_PAIR_MISMATCH"
            elif row.get("quote_outcome_count") != "2" or row.get("market_settlement_type") != "BINARY":
                reason = "REPLAY_MARKET_QUOTE_INVALID"
            elif not (p := _probability(row.get("model_probability"))):
                reason = "REPLAY_PROBABILITY_PACKAGE_INVALID"
            elif (side := _decimal_odds(row.get("selection_decimal_odds"))) is None or (
                    opposite := _decimal_odds(row.get("opposite_decimal_odds"))) is None:
                reason = "REPLAY_MARKET_QUOTE_INVALID"
            elif (not all((start := _timestamp(row.get("event_start_utc")),
                           forecast := _timestamp(row.get("forecast_asof_utc")),
                           feature := _timestamp(row.get("latest_feature_available_utc")),
                           training := _timestamp(row.get("training_cutoff_utc")),
                           quote := _timestamp(row.get("quote_asof_utc")),
                           settled := _timestamp(row.get("settled_at_utc"))))):
                reason = "REPLAY_TIME_LEAKAGE_OR_MISMATCH"
            elif not (training <= forecast and feature <= forecast < start < settled and quote <= forecast):
                reason = "REPLAY_TIME_LEAKAGE_OR_MISMATCH"
            elif (forecast - quote).total_seconds() > max_quote_age_minutes * 60:
                reason = "REPLAY_MARKET_CAPTURE_STALE"
            elif row.get("result_status") != "FINAL" or row.get("void_or_push") != "false" or row.get("tie") != "false":
                reason = "REPLAY_UNSETTLED_OR_VOID"
            elif row.get("outcome") not in ("0", "1"):
                reason = "REPLAY_SETTLEMENT_INVALID"
            else:
                model_p = p
                market_p = (1.0 / side) / (1.0 / side + 1.0 / opposite)
                if not 0 < market_p < 1:
                    reason = "REPLAY_MARKET_QUOTE_INVALID"
                else:
                    pairs.append((model_p, market_p, int(row["outcome"])))
                    reason = "REPLAY_CANDIDATE_UNVERIFIED"
        else:
            if isinstance(row, Mapping):
                reason = "REPLAY_EXACT_IDENTITY_MISMATCH"
        statuses.append({"row_index": index, "status": reason,
                         "exact_thesis": list(key) if key is not None else None,
                         "input_provenance_attested": False, "can_execute": False})
    n = len(pairs)
    model_losses = [_binary_loss(p, y) for p, _, y in pairs]
    market_losses = [_binary_loss(p, y) for _, p, y in pairs]
    avg = lambda values, at: sum(row[at] for row in values) / n if n else None
    model_brier = avg(model_losses, 0)
    market_brier = avg(market_losses, 0)
    model_log = avg(model_losses, 1)
    market_log = avg(market_losses, 1)
    return {
        "schema": SCHEMA,
        "status": "DESCRIPTIVE_MARKET_BASELINE_NOT_ATTESTED" if n >= minimum_n else "NOT_ENOUGH_EVIDENCE",
        "received": len(rows), "eligible_unattested": n, "held": len(rows) - n,
        "rows": statuses,
        "model_brier_unattested": model_brier, "market_brier_unattested": market_brier,
        "model_log_loss_unattested": model_log, "market_log_loss_unattested": market_log,
        "brier_delta_model_minus_market": model_brier - market_brier if n else None,
        "log_loss_delta_model_minus_market": model_log - market_log if n else None,
        "model_reliability_10_bins_unattested": _reliability([(p, y) for p, _, y in pairs]),
        "market_reliability_10_bins_unattested": _reliability([(p, y) for _, p, y in pairs]),
        "market_quote_rule": f"ASOF_FORECAST_WITHIN_{max_quote_age_minutes}_MINUTES_TWO_WAY_SAME_BOOK",
        "source_rights_approved": False, "input_provenance_attested": False,
        "holdout_independently_attested": False, "independent_calibration_certified": False,
        "statistically_significant_superiority": None,
        "model_probability_publishable": False, "registration_attempted": False,
        "can_execute": False,
    }


def main() -> int:
    """Audit a caller-supplied CSV; do not fetch, fit or write to production."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    options = parser.parse_args()
    with options.csv.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not set(KEYS).issubset(reader.fieldnames):
            raise ValueError("REPLAY_EXACT_IDENTITY_MISMATCH")
        rows = list(islice(reader, MAX_ROWS + 1))
    receipt = evaluate_binary_market_baseline(rows)
    if options.out.resolve() == options.csv.resolve():
        raise ValueError("PAIR_REPLAY_INPUT_INVALID")
    options.out.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive create also rejects preexisting hardlink aliases to the input.
    with options.out.open("x", encoding="utf-8") as output:
        output.write(json.dumps(receipt, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(json.dumps({"schema": SCHEMA, "status": receipt["status"],
                      "eligible_unattested": receipt["eligible_unattested"],
                      "held": receipt["held"], "can_execute": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
