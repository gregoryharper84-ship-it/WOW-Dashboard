"""Read-only, fail-closed certification evidence evaluator.

No database connections, mutations, release actions, or wagering operations.
Input is an exported JSON snapshot with immutable identifiers and source evidence.
This module NEVER grants production certification; independent authorities do.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path

STATES = {"NO_CAPABILITY", "RESEARCH", "SHADOW", "CERTIFICATION_HOLD", "PRODUCTION_VERIFIED"}
MARKETS = {"ML", "SPREAD", "PROP"}

def _digest(record):
    return hashlib.sha256(json.dumps(record, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def _valid_probability(p):
    return isinstance(p, (int, float)) and not isinstance(p, bool) and math.isfinite(p) and 0 <= p <= 1

def evaluate(snapshot):
    """Evaluate supplied evidence only; never turn absent evidence into PASS."""
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("routes"), list):
        raise ValueError("routes array required")
    output = []
    for route in snapshot["routes"]:
        sport = str(route.get("sport", "")).upper()
        market = str(route.get("market", "")).upper()
        if not sport or market not in MARKETS:
            raise ValueError("sport and market (ML/SPREAD/PROP) required")
        rows = route.get("predictions", [])
        if not isinstance(rows, list):
            raise ValueError("predictions must be an array")
        seen = set()
        graded = []
        errors = []
        for row in rows:
            identity = (row.get("prediction_id"), row.get("event_id"), row.get("market_key"))
            if not all(identity) or identity in seen:
                errors.append("MISSING_OR_DUPLICATE_PREDICTION_IDENTITY")
                continue
            seen.add(identity)
            required = ("model_artifact_sha", "calibrator_sha", "predicted_at", "event_start_at",
                        "feature_cutoff_at", "source_manifest_sha", "outcome_source_sha")
            if any(not row.get(k) for k in required):
                errors.append("MISSING_IMMUTABLE_PROVENANCE")
                continue
            if not (row["feature_cutoff_at"] <= row["predicted_at"] < row["event_start_at"]):
                errors.append("PREGAME_TIME_ORDER_VIOLATION")
                continue
            p, outcome = row.get("probability"), row.get("outcome")
            if not _valid_probability(p) or outcome not in (0, 1) or isinstance(outcome, bool):
                errors.append("INVALID_PROBABILITY_OR_OUTCOME")
                continue
            if market == "SPREAD" and (row.get("line") is None or row.get("settlement") not in ("WIN", "LOSS")):
                errors.append("SPREAD_LINE_OR_SETTLEMENT_MISSING")
                continue
            if market == "PROP" and any(row.get(k) is None for k in ("player_id", "stat_type", "line", "side", "settlement")):
                errors.append("PROP_CONTRACT_MISSING")
                continue
            graded.append((p, outcome))
        n = len(graded)
        brier = sum((p-y)**2 for p,y in graded)/n if n else None
        logloss = sum(-(y*math.log(max(p,1e-15))+(1-y)*math.log(max(1-p,1e-15))) for p,y in graded)/n if n else None
        buckets = defaultdict(list)
        for p,y in graded:
            buckets[min(9,int(p*10))].append((p,y))
        ece = sum(len(v)/n*abs(sum(p for p,_ in v)/len(v)-sum(y for _,y in v)/len(v)) for v in buckets.values()) if n else None
        # Caller-supplied gate flags are assertions, not independently verified receipts.
        # Fail closed until a separate trusted verifier supplies source-pinned evidence.
        gate1 = gate2 = gate3 = False
        claimed = route.get("state", "NO_CAPABILITY")
        if claimed not in STATES:
            errors.append("UNKNOWN_STATE")
        if claimed == "PRODUCTION_VERIFIED":
            errors.append("UNSUPPORTED_PRODUCTION_CLAIM")
        output.append({"sport": sport, "market": market, "claimed_state": claimed,
                       "verified_state": "CERTIFICATION_HOLD" if errors else claimed,
                       "eligible_count": len(rows), "graded_count": n, "brier": brier,
                       "log_loss": logloss, "ece_10_bins": ece, "gate_evidence": [gate1,gate2,gate3],
                       "errors": sorted(set(errors)), "evidence_sha256": _digest(route),
                       "note": "Advisory evidence only; never a publication authorization."})
    return {"schema": "wow-certification-readonly-v1", "routes": output, "can_execute": False,
            "terminal_authority": "V17_TERMINAL_REDUCER"}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=Path)
    args = parser.parse_args()
    print(json.dumps(evaluate(json.loads(args.snapshot.read_text())), sort_keys=True, indent=2))

if __name__ == "__main__":
    main()
