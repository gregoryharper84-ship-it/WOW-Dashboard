"""Deterministic, read-only feedback for WOW's zero-credit research Swarm.

Prior receipts come only from the existing GitHub Actions artifact path.
This cannot fetch or license source data, fit a probability, self-modify code,
certify a model, publish a pick, or execute a wager.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

SCHEMA = "wow.v17.native_swarm_learning.v1"
RECEIPT_SCHEMA = "wow.v17.native_swarm_production_readback.v1"
MAX_HISTORY = 12
MAX_FILE_BYTES = 65536
_ENGINE_ROOT = str(Path(__file__).resolve().parents[1])
if _ENGINE_ROOT not in sys.path:
    sys.path.insert(0, _ENGINE_ROOT)
_SHA = re.compile(r"^[0-9a-f]{64}$")
_SAFE_FEATURE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")

def _safe_feature(name: Any) -> bool:
    if not isinstance(name, str) or not _SAFE_FEATURE.fullmatch(name):
        return False
    prohibited = (
        "api_key", "password", "secret", "token", "probability",
        "confidence", "predicted_", "model_prediction",
        "stake", "wager", "order", "estimated_edge",
    )
    return not any(fragment in name for fragment in prohibited)



def _load(path: Path) -> dict[str, Any]:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("research receipt exceeds bounded size")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("research receipt must be an object")
    return value


def _receipt(value: Any) -> dict[str, Any]:
    """Validate an untrusted research envelope; do not attest data truth."""
    if not isinstance(value, dict) or value.get("schema_version") != RECEIPT_SCHEMA:
        raise ValueError("unexpected native research receipt schema")
    pilot, summary = value.get("pilot"), value.get("summary")
    if not isinstance(pilot, dict) or not isinstance(summary, dict):
        raise ValueError("native pilot and summary are required")
    if (value.get("can_execute") is not False
            or value.get("may_publish_or_qualify") is not False
            or summary.get("can_execute") is not False
            or summary.get("may_publish_or_qualify") is not False
            or summary.get("prediction_authority") is not False
            or summary.get("specialist_rescore_verified") is not False
            or summary.get("independent_source_verification") is not False
            or summary.get("external_llm_calls") != 0
            or summary.get("paid_provider_calls") != 0):
        raise ValueError("native research receipt claims unverified authority")
    if (pilot.get("status") != "VERIFIED_EVENT_OPTED_IN"
            or pilot.get("requested_count") != 1
            or pilot.get("can_execute") is not False
            or pilot.get("may_publish_or_qualify") is not False):
        raise ValueError("native pilot must be exactly one verified event")
    sport, event = pilot.get("sport_key"), pilot.get("official_event_id")
    if (not isinstance(sport, str) or not sport.strip()
            or not isinstance(event, str) or not 0 < len(event) <= 160):
        raise ValueError("canonical event identity missing")
    if (summary.get("status") not in ("RESEARCH_ONLY_HOLD", "RESEARCH_ONLY_SCREENED")
            or summary.get("workers_completed") != 5
            or not isinstance(summary.get("receipt_sha256"), str)
            or not _SHA.fullmatch(summary["receipt_sha256"])):
        raise ValueError("invalid five-worker receipt")
    fields = ("research_facts_screened", "missing_evidence_count", "conflicts_count")
    if any(type(summary.get(k)) is not int or not 0 <= summary[k] <= 10000 for k in fields):
        raise ValueError("bounded evidence counts required")
    run_id = value.get("github_run_id")
    if not isinstance(run_id, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", run_id):
        raise ValueError("invalid GitHub run identity")
    return {
        "run_id": run_id, "sport": sport, "event_id": event,
        "digest": summary["receipt_sha256"], "status": summary["status"],
        "facts": summary["research_facts_screened"],
        "missing": summary["missing_evidence_count"],
        "conflicts": summary["conflicts_count"],
    }


def _features(receipt: dict[str, Any], sport: str) -> list[str]:
    """Use only current canonical gap names, never source measurements."""
    from v17.matchup_discovery import feature_requirements
    canonical = {feature for fields in feature_requirements(sport).values()
                 for feature in fields}
    raw = receipt.get("canonical_missing_features", [])
    if not isinstance(raw, list):
        return []
    return sorted({feature for feature in raw[:64]
                   if _safe_feature(feature) and feature in canonical})[:32]


def _history(paths: list[Path], current_run: str) -> tuple[list[dict[str, Any]], int]:
    valid: dict[str, dict[str, Any]] = {}
    quarantined: set[str] = set()
    rejected = 0
    for path in paths[:MAX_HISTORY * 3]:
        try:
            prior = _receipt(_load(path))
            if prior["run_id"] == current_run:
                continue
            hint_path = path.with_name("swarm-learning.json")
            prior["missing_features"] = []
            if hint_path.exists():
                hint = _load(hint_path)
                if (hint.get("schema_version") == SCHEMA
                        and hint.get("source_run_id") == prior["run_id"]
                        and hint.get("source_receipt_sha256") == prior["digest"]):
                    features = hint.get("missing_features")
                    if isinstance(features, list):
                        prior["missing_features"] = [
                            x for x in features[:32]
                            if _safe_feature(x)
                        ]
            run = prior["run_id"]
            if run in quarantined:
                rejected += 1
                continue
            if run in valid and valid[run]["digest"] != prior["digest"]:
                valid.pop(run)
                quarantined.add(run)
                rejected += 1
                continue
            valid[run] = prior
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            rejected += 1
    return sorted(valid.values(), key=lambda row: int(row["run_id"]))[-MAX_HISTORY:], rejected


def learn(current: dict[str, Any], history: list[dict[str, Any]],
          *, missing_features: list[str] | None = None,
          rejected_history: int = 0) -> dict[str, Any]:
    sample = _receipt(current)
    unique: dict[str, dict[str, Any]] = {}
    quarantined: set[str] = set()
    for row in history:
        try:
            checked = _receipt(row)
        except (ValueError, TypeError, KeyError):
            rejected_history += 1
            continue
        run = checked["run_id"]
        if run == sample["run_id"] or run in quarantined:
            continue
        if run in unique and unique[run]["digest"] != checked["digest"]:
            unique.pop(run)
            quarantined.add(run)
            rejected_history += 1
            continue
        unique[run] = {**checked, "missing_features": row.get("missing_features", [])}
    past = sorted(unique.values(), key=lambda row: int(row["run_id"]))[-MAX_HISTORY:]
    same = [row for row in past if (row["sport"], row["event_id"]) ==
            (sample["sport"], sample["event_id"])]
    latest = same[-1] if same else None
    # Each independent event contributes at most one observation to source-gap
    # priorities. Re-running one matchup cannot simulate additional evidence.
    recent_by_event: dict[tuple[str, str], dict[str, Any]] = {}
    for row in past:
        recent_by_event[(row["sport"], row["event_id"])] = row
    feature_counts = Counter()
    for row in recent_by_event.values():
        feature_counts.update(set(row.get("missing_features", [])))
    canonical = sorted({x for x in (missing_features or []) if isinstance(x, str)
                        and _safe_feature(x)})[:32]
    features_ranked = sorted(canonical, key=lambda key: (-feature_counts[key], key))
    if latest is None:
        status = "BASELINE_ESTABLISHED"
    elif sample["facts"] > latest["facts"] and sample["missing"] <= latest["missing"]:
        status = "RESEARCH_EVIDENCE_COVERAGE_IMPROVED"
    elif sample["facts"] < latest["facts"] or sample["missing"] > latest["missing"]:
        status = "RESEARCH_EVIDENCE_COVERAGE_REGRESSED"
    elif sample["status"] == "RESEARCH_ONLY_HOLD" and sample["facts"] == latest["facts"]:
        status = "REPEATED_RESEARCH_HOLD"
    else:
        status = "RESEARCH_EVIDENCE_STABLE"
    if sample["status"] == "RESEARCH_ONLY_HOLD":
        next_action = ("ACQUIRE_OR_VERIFY_AUTHORIZED_INPUT:" + features_ranked[0]
                       if features_ranked else "REVIEW_AUTHORIZED_SOURCE_EVIDENCE_GAPS")
    else:
        next_action = "INDEPENDENTLY_REVIEW_SCREENED_SOURCE_FACTS"
    fingerprint = hashlib.sha256(json.dumps(
        [(r["run_id"], r["digest"]) for r in past], sort_keys=True).encode()).hexdigest()
    return {
        "schema_version": SCHEMA,
        "status": status,
        "source_run_id": sample["run_id"],
        "source_receipt_sha256": sample["digest"],
        "sport_key": sample["sport"],
        "official_event_id": sample["event_id"],
        "prior_unique_runs": len(past),
        "prior_distinct_events": len(recent_by_event),
        "same_event_prior_runs": len(same),
        "history_rejected": rejected_history,
        "history_sha256": fingerprint,
        "previous_screened_facts": latest["facts"] if latest else None,
        "current_screened_facts": sample["facts"],
        "previous_missing_evidence": latest["missing"] if latest else None,
        "current_missing_evidence": sample["missing"],
        "missing_features": features_ranked,
        "next_research_action": next_action,
        "learning_scope": "ADVISORY_RESEARCH_SOURCE_COVERAGE_ONLY",
        "predictive_accuracy_improvement_verified": False,
        "may_fit_probability": False,
        "may_certify_or_qualify": False,
        "may_modify_model_or_release": False,
        "can_execute": False,
        "paid_provider_calls": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded zero-credit Swarm research feedback")
    parser.add_argument("--current", required=True)
    parser.add_argument("--history-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        current = _load(Path(args.current))
        checked = _receipt(current)
        history, rejected = _history(
            sorted(Path(args.history_dir).glob("**/native-swarm-receipt.json")),
            checked["run_id"])
        # Past envelopes are revalidated by learn; never trust history as data.
        raw = [{**{
            "schema_version": RECEIPT_SCHEMA,
            "github_run_id": row["run_id"],
            "pilot": {"status": "VERIFIED_EVENT_OPTED_IN", "requested_count": 1,
                      "sport_key": row["sport"], "official_event_id": row["event_id"],
                      "can_execute": False, "may_publish_or_qualify": False},
            "summary": {"status": row["status"], "workers_completed": 5,
                        "receipt_sha256": row["digest"], "research_facts_screened": row["facts"],
                        "missing_evidence_count": row["missing"], "conflicts_count": row["conflicts"],
                        "can_execute": False, "may_publish_or_qualify": False,
                        "prediction_authority": False, "specialist_rescore_verified": False,
                        "independent_source_verification": False, "external_llm_calls": 0,
                        "paid_provider_calls": 0},
            "can_execute": False, "may_publish_or_qualify": False,
        }, "missing_features": row.get("missing_features", [])} for row in history]
        report = learn(current, raw,
                       missing_features=_features(current, checked["sport"]),
                       rejected_history=rejected)
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({k: report[k] for k in (
            "status", "source_run_id", "prior_unique_runs", "prior_distinct_events",
            "same_event_prior_runs", "next_research_action", "can_execute")}))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        parser.exit(2, f"Swarm learning evidence HOLD: {type(exc).__name__}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
