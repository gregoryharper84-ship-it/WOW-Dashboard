"""WOW V17 read-only accuracy experiment controller: never scores a live pick.

Consumes an exact original-board manifest and immutable pregame/settlement
evidence supplied by upstream adapters. Source assertions in local JSON are NOT
authenticated production receipts or certified fitted-model provenance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Mapping

CAN_EXECUTE = False
AUTOMATIC_PROMOTION = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
MIN_HOLDOUT_EVENTS = 30
SPORTS = {"NFL", "NBA", "MLB", "NHL", "WNBA", "NCAAF", "NCAAB", "SOCCER"}
DIRECTIONS = {"MORE", "LESS", "OVER", "UNDER", "YES", "NO", "HOME", "AWAY"}
BACKEND_FAILURES = {
    "MODEL_UNAVAILABLE", "MODEL_SCORER_FAILED", "MODEL_SCORE_TIMED_OUT",
    "MODEL_INPUTS_INSUFFICIENT", "MODEL_OUTPUT_INVALID", "CALIBRATION_FAILED",
    "INGESTION_FAILED", "TRANSPORT_FAILED",
}
RESEARCH_ACTIONS = {
    "MODEL_UNAVAILABLE": ("MODEL_CAPABILITY", "Review certified specialist and artifact registration"),
    "MODEL_SCORER_FAILED": ("SCORING", "Replay the scorer exception with original inputs"),
    "MODEL_SCORE_TIMED_OUT": ("SCORING", "Profile exact receipt and timeout"),
    "MODEL_INPUTS_INSUFFICIENT": ("EVIDENCE", "Recover official pregame feature snapshots"),
    "MODEL_OUTPUT_INVALID": ("SCORING", "Check fitted package/calibration/bound contract"),
    "CALIBRATION_FAILED": ("CALIBRATION", "Inspect chronological calibrator provenance"),
    "INGESTION_FAILED": ("INGESTION", "Restore exact original-offer acquisition"),
    "TRANSPORT_FAILED": ("TRANSPORT", "Diagnose authenticated action transport"),
    "NOT_SCORED": ("COVERAGE", "Trace missing row-level scorer and receipts"),
    "IDENTITY_NOT_OFFICIALLY_VERIFIED": ("IDENTITY", "Verify player, team, opponent and official event"),
    "PREGAME_ELIGIBILITY_UNVERIFIED": ("ELIGIBILITY", "Verify official injury and active-status snapshot"),
    "OFFICIAL_SETTLEMENT_MISSING": ("SETTLEMENT", "Wire exact independent official settlement"),
}

class ExperimentInputError(ValueError):
    pass

def _str(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExperimentInputError(code)
    return value.strip()

def _time(value: Any, code: str) -> datetime:
    try:
        dt = datetime.fromisoformat(_str(value, code).replace("Z", "+00:00"))
        if dt.tzinfo is not None and dt.utcoffset() is not None:
            return dt.astimezone(timezone.utc)
    except ValueError:
        pass
    raise ExperimentInputError(code)

def _p(value: Any, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ExperimentInputError(code)
    value = float(value)
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ExperimentInputError(code)
    return value

def _offer(raw: Any) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        raise ExperimentInputError("INVALID_ORIGINAL_OFFER")
    keys = (
        "offer_id", "original_label", "event_id", "entity_id", "stat",
        "threshold_label", "direction", "offer_type", "sport", "route",
        "event_start_utc",
    )
    row = {key: _str(raw.get(key), "MISSING_OFFER_" + key.upper()) for key in keys}
    row["offer_type"] = row["offer_type"].upper()
    row["direction"] = row["direction"].upper()
    row["sport"] = row["sport"].upper()
    if row["sport"] not in SPORTS or row["direction"] not in DIRECTIONS:
        raise ExperimentInputError("UNSUPPORTED_SPORT_OR_DIRECTION")
    threshold = row["threshold_label"]
    kind = row["offer_type"]
    if kind == "AT_LEAST":
        if not re.fullmatch(r"\d+\+", threshold):
            raise ExperimentInputError("INVALID_LITERAL_AT_LEAST")
    elif kind == "SCALAR":
        if not re.fullmatch(r"-?\d+(?:\.\d+)?", threshold):
            raise ExperimentInputError("INVALID_EXACT_SCALAR_THRESHOLD")
    elif kind == "COMPOUND":
        if (row["stat"].upper() != "DOUBLE_DOUBLE" or threshold != "DOUBLE_DOUBLE"
                or "DOUBLE_DOUBLE" not in row["route"].upper()):
            raise ExperimentInputError("COMPOUND_REQUIRES_SEPARATE_ROUTE")
    else:
        raise ExperimentInputError("UNSUPPORTED_OFFER_TYPE")
    _time(row["event_start_utc"], "INVALID_EVENT_START_UTC")
    return row

def _package(value: Any, offer: Mapping[str, str], start: datetime,
             name: str) -> tuple[float, float]:
    if not isinstance(value, Mapping):
        raise ExperimentInputError(name + "_MODEL_PACKAGE_MISSING")
    for key in ("prediction_id", "model_artifact_id", "calibrator_id", "specialist_id"):
        _str(value.get(key), name + "_" + key.upper() + "_REQUIRED")
    if value.get("offer_id") != offer["offer_id"] or value.get("route") != offer["route"]:
        raise ExperimentInputError(name + "_EXACT_OFFER_MISMATCH")
    if value.get("origin") != "FITTED_SPECIALIST":
        raise ExperimentInputError(name + "_NOT_FITTED_SPECIALIST")
    created = _time(value.get("created_at_utc"), name + "_SNAPSHOT_TIMESTAMP_INVALID")
    features = _time(value.get("feature_asof_utc"), name + "_FEATURE_TIMESTAMP_INVALID")
    if not features <= created < start:
        raise ExperimentInputError(name + "_PREGAME_LEAKAGE")
    probability = _p(value.get("calibrated_probability"), name + "_CALIBRATED_PROBABILITY_INVALID")
    lower = _p(value.get("calibrated_lower_bound"), name + "_LOWER_BOUND_INVALID")
    if lower > probability:
        raise ExperimentInputError(name + "_LOWER_BOUND_EXCEEDS_PROBABILITY")
    return probability, lower

def _grade(offer: Mapping[str, str], evidence: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "offer_id": offer["offer_id"], "original_label": offer["original_label"],
        "status": "HELD", "code": "NOT_SCORED",
    }
    if evidence is None:
        return result
    try:
        if not isinstance(evidence, Mapping):
            raise ExperimentInputError("INVALID_EVIDENCE_OBJECT")
        if evidence.get("offer_id") != offer["offer_id"]:
            raise ExperimentInputError("EVIDENCE_OFFER_ID_MISMATCH")
        if evidence.get("identity_verification") != "OFFICIAL_VERIFIED":
            raise ExperimentInputError("IDENTITY_NOT_OFFICIALLY_VERIFIED")
        if evidence.get("pregame_eligibility") != "CONFIRMED_ELIGIBLE":
            raise ExperimentInputError("PREGAME_ELIGIBILITY_UNVERIFIED")
        start = _time(offer["event_start_utc"], "INVALID_EVENT_START_UTC")
        if not _time(evidence.get("evidence_asof_utc"), "INVALID_EVIDENCE_TIMESTAMP") < start:
            raise ExperimentInputError("PREGAME_EVIDENCE_LEAKAGE")
        if evidence.get("backend_code") is not None:
            code = evidence["backend_code"]
            if code not in BACKEND_FAILURES:
                raise ExperimentInputError("UNRECOGNIZED_TYPED_BACKEND_FAILURE")
            _str(evidence.get("backend_receipt_id"), "BACKEND_FAILURE_RECEIPT_REQUIRED")
            raise ExperimentInputError(code)
        settlement = evidence.get("settlement")
        if not isinstance(settlement, Mapping):
            raise ExperimentInputError("OFFICIAL_SETTLEMENT_MISSING")
        for key in ("source_id", "verification_id", "receipt_id"):
            _str(settlement.get(key), "SETTLEMENT_" + key.upper() + "_REQUIRED")
        if _time(settlement.get("settled_at_utc"), "INVALID_SETTLEMENT_TIMESTAMP") < start:
            raise ExperimentInputError("SETTLEMENT_BEFORE_EVENT_START")
        outcome = settlement.get("outcome")
        if type(outcome) is not int or outcome not in (0, 1):
            raise ExperimentInputError("OFFICIAL_BINARY_OUTCOME_REQUIRED")
        split = evidence.get("split")
        if split not in ("CALIBRATION", "HOLDOUT"):
            raise ExperimentInputError("EXACT_SPLIT_REQUIRED")
        champion = _package(evidence.get("champion"), offer, start, "CHAMPION")
        challenger = _package(evidence.get("challenger"), offer, start, "CHALLENGER")
        if evidence["champion"]["prediction_id"] == evidence["challenger"]["prediction_id"]:
            raise ExperimentInputError("DUPLICATE_MODEL_PREDICTION_RECEIPT")
        if evidence["champion"]["model_artifact_id"] == evidence["challenger"]["model_artifact_id"]:
            raise ExperimentInputError("SAME_ARTIFACT_FAKE_CHALLENGER")
        result.update(
            status="PAIRED_RESEARCH_GRADE", code="OFFLINE_ATTESTED_NOT_PRODUCTION_CERTIFIED",
            split=split, sport=offer["sport"], route=offer["route"],
            event_id=offer["event_id"], event_start_utc=start.isoformat(),
            outcome=outcome, champion_probability=champion[0], challenger_probability=challenger[0],
            champion_lower_bound=champion[1], challenger_lower_bound=challenger[1],
            champion_prediction_id=evidence["champion"]["prediction_id"],
            challenger_prediction_id=evidence["challenger"]["prediction_id"],
            settlement_receipt_id=settlement["receipt_id"],
        )
    except ExperimentInputError as exc:
        result["code"] = str(exc)
    return result

def _metrics(rows: list[dict[str, Any]], model: str) -> dict[str, float]:
    if not rows:
        return {}
    n = len(rows)
    ps = [r[model + "_probability"] for r in rows]
    ys = [r["outcome"] for r in rows]
    brier = sum((p-y)**2 for p, y in zip(ps, ys)) / n
    loss = -sum(y*math.log(max(p, 1e-15)) +
                (1-y)*math.log(max(1-p, 1e-15)) for p, y in zip(ps, ys)) / n
    bins: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for p, y in zip(ps, ys):
        bins[min(9, int(p*10))].append((p, y))
    ece = sum(len(g)/n * abs(sum(p for p, _ in g)/len(g) -
                             sum(y for _, y in g)/len(g)) for g in bins.values())
    return {"n": n, "brier": round(brier, 8), "log_loss": round(loss, 8),
            "ece_10_bin": round(ece, 8),
            "signed_calibration_bias": round(sum(p-y for p, y in zip(ps, ys))/n, 8)}

def _ci(rows: list[dict[str, Any]]) -> list[float] | None:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["event_id"]].append(row)
    if len(groups) < 2:
        return None
    events = list(groups.values())
    rng = random.Random(170026)
    draws = []
    for _ in range(1000):
        sample = [r for _j in events for r in events[rng.randrange(len(events))]]
        draws.append(_metrics(sample, "champion")["brier"] -
                     _metrics(sample, "challenger")["brier"])
    draws.sort()
    return [round(draws[24], 8), round(draws[974], 8)]

def evaluate_experiment(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Reconcile all source offers even when some have no usable model evidence."""
    if not isinstance(payload, Mapping):
        raise ExperimentInputError("EXPERIMENT_OBJECT_REQUIRED")
    experiment_id = _str(payload.get("experiment_id"), "EXPERIMENT_ID_REQUIRED")
    raw = payload.get("offers")
    if not isinstance(raw, list) or not raw:
        raise ExperimentInputError("NONEMPTY_ORIGINAL_OFFERS_REQUIRED")
    offers = [_offer(x) for x in raw]
    ids = [o["offer_id"] for o in offers]
    if len(ids) != len(set(ids)):
        raise ExperimentInputError("DUPLICATE_ORIGINAL_OFFER_ID")
    expected = payload.get("expected_original_offer_count")
    if type(expected) is not int or expected != len(offers):
        raise ExperimentInputError("SOURCE_BOARD_COUNT_MISMATCH")
    evidence = payload.get("evidence_by_offer_id", {})
    if not isinstance(evidence, Mapping):
        raise ExperimentInputError("EVIDENCE_MAP_REQUIRED")
    if set(evidence) - set(ids):
        raise ExperimentInputError("EXTRANEOUS_EVIDENCE_OFFER_ID")
    manifest_hash = hashlib.sha256(json.dumps(offers, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    rows = [_grade(offer, evidence.get(offer["offer_id"])) for offer in offers]
    paired = [r for r in rows if r["status"] == "PAIRED_RESEARCH_GRADE"]
    for field in ("champion_prediction_id", "challenger_prediction_id", "settlement_receipt_id"):
        counts = Counter(r[field] for r in paired)
        reused = {key for key, n in counts.items() if n > 1}
        for row in paired:
            if row[field] in reused:
                row.update(status="HELD", code="REUSED_IMMUTABLE_RECEIPT")
    paired = [r for r in rows if r["status"] == "PAIRED_RESEARCH_GRADE"]
    group_splits: dict[str, set[str]] = defaultdict(set)
    for row in paired:
        group_splits[row["event_id"]].add(row["split"])
    for row in paired:
        if len(group_splits[row["event_id"]]) > 1:
            row.update(status="HELD", code="CROSS_SPLIT_EVENT_LEAKAGE")
    paired = [r for r in rows if r["status"] == "PAIRED_RESEARCH_GRADE"]
    cal = [_time(r["event_start_utc"], "INVALID_EVENT_START_UTC") for r in paired if r["split"] == "CALIBRATION"]
    hold = [_time(r["event_start_utc"], "INVALID_EVENT_START_UTC") for r in paired if r["split"] == "HOLDOUT"]
    if cal and hold and max(cal) >= min(hold):
        for row in paired:
            row.update(status="HELD", code="CHRONOLOGICAL_SPLIT_LEAKAGE")
    paired = [r for r in rows if r["status"] == "PAIRED_RESEARCH_GRADE"]
    holdout = [r for r in paired if r["split"] == "HOLDOUT"]
    event_count = len({r["event_id"] for r in holdout})
    champion = _metrics(holdout, "champion")
    challenger = _metrics(holdout, "challenger")
    interval = _ci(holdout)
    difference = round(champion["brier"] - challenger["brier"], 8) if holdout else None
    if event_count < MIN_HOLDOUT_EVENTS:
        disposition = "INSUFFICIENT_UNSEEN_EVENTS"
    elif interval is None or interval[0] <= 0:
        disposition = "NO_STATISTICALLY_CONFIRMED_IMPROVEMENT"
    else:
        disposition = "CHALLENGER_REQUIRES_INDEPENDENT_CLASS_C_CERTIFICATION"
    failures = Counter(r["code"] for r in rows if r["status"] == "HELD")
    hypotheses = []
    for code, frequency in sorted(failures.items(), key=lambda item: (-item[1], item[0])):
        category, proposal = RESEARCH_ACTIONS.get(code, ("DATA_INTEGRITY", "Reproduce exact-row discrepancy"))
        hypotheses.append({"failure_code": code, "frequency": frequency,
                           "category": category, "next_experiment": proposal,
                           "auto_dispatch": False})
    return {
        "experiment_id": experiment_id, "status": "RESEARCH_ONLY",
        "can_execute": False, "automatic_promotion": False,
        "probability_publishable": False, "terminal_authority": TERMINAL_AUTHORITY,
        "source_board_sha256": manifest_hash,
        "original_offer_count": len(offers), "reconciled_offer_count": len(rows),
        "paired_research_rows": len(paired), "holdout_event_count": event_count,
        "held_codes": dict(sorted(failures.items())), "recovery_experiment_candidates": hypotheses,
        "holdout_champion_metrics": champion, "holdout_challenger_metrics": challenger,
        "brier_improvement_champion_minus_challenger": difference,
        "brier_improvement_event_bootstrap_95_ci": interval,
        "certification_disposition": disposition, "rows": rows,
    }

def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only WOW V17 accuracy experiment")
    parser.add_argument("--evidence-json", required=True)
    args = parser.parse_args()
    with open(args.evidence_json, encoding="utf-8") as stream:
        payload = json.load(stream)
    print(json.dumps(evaluate_experiment(payload), indent=2, sort_keys=True))

if __name__ == "__main__":
    main()
