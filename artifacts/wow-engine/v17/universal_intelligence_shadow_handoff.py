"""Sport-agnostic WOW V17 research -> shadow challenger feature proposal.

Purely offline; it does NOT accept a fitted model receipt or activate a runtime
hydrator. Both LLP and the prop lane can study the same source observation
without confusing a research feature with certified numerical consumption.
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Mapping, Sequence

from v17.universal_sports_intelligence_research import (
    SCHEMA as SNAPSHOT_SCHEMA, SPORT_DOMAINS, _time,
)

SCHEMA = "WOW_V17_INTELLIGENCE_SHADOW_FEATURE_HANDOFF_V1"
TARGETS = {"LLP_TEAM_EVENT", "WOW_PROP_LANE"}
HEX64 = re.compile(r"[a-f0-9]{64}\Z")


class ShadowFeatureHold(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require(ok: bool, code: str) -> None:
    if not ok:
        raise ShadowFeatureHold(code)


def prepare_shadow_feature_handoff(
    snapshot: Mapping[str, Any],
    *, proposed_targets: Mapping[str, Sequence[str]],
) -> dict[str, Any]:
    """Compile candidate features, explicit non-consumption and missingness.

    Caller-proposed model targets determine research queues ONLY, not route
    authority or numerical scoring. All source and event identities remain
    unverified until independent backend hydration/certification.
    """
    _require(isinstance(snapshot, Mapping) and snapshot.get("schema") == SNAPSHOT_SCHEMA
             and snapshot.get("sport") in SPORT_DOMAINS,
             "INTEL_SHADOW_SNAPSHOT_INVALID")
    _require(snapshot.get("prediction_authority") is False
             and snapshot.get("probability_publishable") is False
             and snapshot.get("can_execute") is False
             and snapshot.get("event_identity_attested") is False
             and snapshot.get("verified_model_features") == []
             and snapshot.get("model_consumption_receipts") == []
             and snapshot.get("calibrated_probability") is None
             and snapshot.get("calibrated_lower_bound") is None,
             "INTEL_SHADOW_AUTHORITY_CONFLICT")
    _require(isinstance(snapshot.get("official_event_id_claimed"), str)
             and bool(snapshot["official_event_id_claimed"].strip()),
             "INTEL_SHADOW_EVENT_INVALID")
    cutoff = _time(snapshot.get("as_of_utc"))
    start = _time(snapshot.get("event_start_utc"))
    _require(cutoff < start, "INTEL_SHADOW_NOT_PREGAME")
    facts = snapshot.get("research_facts")
    _require(isinstance(facts, list) and type(snapshot.get("fact_count")) is int
             and len(facts) == snapshot["fact_count"] and len(facts) <= 500,
             "INTEL_SHADOW_ROWS_INVALID")
    _require(isinstance(proposed_targets, Mapping),
             "INTEL_SHADOW_TARGETS_INVALID")
    seen: set[str] = set()
    allowed = set()
    for f in facts:
        _require(isinstance(f, Mapping), "INTEL_SHADOW_ROW_INVALID")
        ref = f.get("source_reference")
        _require(isinstance(ref, str) and HEX64.fullmatch(ref) is not None
                 and ref not in seen, "INTEL_SHADOW_SOURCE_REFERENCE_INVALID")
        seen.add(ref)
        allowed.add(ref)
    _require(set(proposed_targets).issubset(allowed), "INTEL_SHADOW_UNKNOWN_TARGET_REF")
    results: list[dict[str, Any]] = []
    for f in facts:
        ref = f["source_reference"]
        lanes = proposed_targets.get(ref, [])
        _require(isinstance(lanes, (tuple, list)) and len(lanes) == len(set(lanes))
                 and all(isinstance(t, str) and t in TARGETS for t in lanes),
                 "INTEL_SHADOW_TARGETS_INVALID")
        _require(f.get("pregame_model_feature_eligible") is False
                 and f.get("source_attestation") == "UNVERIFIED_RESEARCH"
                 and f.get("evidence_kind") in {"SOURCE_OBSERVATION", "CONTEXT_HYPOTHESIS"}
                 and isinstance(f.get("metric"), str) and bool(f["metric"])
                 and isinstance(f.get("entity_id"), str) and bool(f["entity_id"]),
                 "INTEL_SHADOW_ROW_AUTHORITY_CONFLICT")
        _require(_time(f.get("observed_at")) <= _time(f.get("first_available_at"))
                 <= _time(f.get("retrieved_at")) <= cutoff,
                 "INTEL_SHADOW_CHRONOLOGY_INVALID")
        value = f.get("value")
        numeric = type(value) in (int, float) and math.isfinite(value)
        if f["evidence_kind"] == "CONTEXT_HYPOTHESIS":
            status = "HYPOTHESIS_ONLY_NO_NUMERIC_FEATURE"
        elif not numeric:
            status = "CATEGORICAL_RESEARCH_ONLY"
        elif not lanes:
            status = "NO_EXPLICIT_RESEARCH_TARGET"
        else:
            status = "SHADOW_FEATURE_CANDIDATE_UNVERIFIED"
        identifier = hashlib.sha256(
            f"{snapshot['sport']}|{snapshot['official_event_id_claimed']}|"
            f"{ref}|{f['domain']}|{f['metric']}".encode()
        ).hexdigest()
        results.append({
            "feature_research_id": identifier,
            "source_reference": ref, "domain": f.get("domain"),
            "metric": f["metric"], "entity_id_claimed": f["entity_id"],
            "research_value": value, "research_target_lanes": sorted(lanes),
            "status": status, "official_identity_attested": False,
            "source_rights_attested": False, "fitted_feature_consumed": False,
            "model_probability": None, "calibrated_lower_bound": None,
            "can_execute": False,
        })
    return {
        "schema": SCHEMA, "sport": snapshot["sport"],
        "official_event_id_claimed": snapshot["official_event_id_claimed"],
        "received": len(facts), "rows": results,
        "shadow_candidates": sum(
            row["status"] == "SHADOW_FEATURE_CANDIDATE_UNVERIFIED"
            for row in results
        ), "held_or_unassigned": sum(
            row["status"] != "SHADOW_FEATURE_CANDIDATE_UNVERIFIED"
            for row in results
        ), "reconciled_exactly_once": len(results) == len(facts),
        "verified_model_features": [], "model_consumption_receipts": [],
        "source_rights_attested": False, "probability_publishable": False,
        "can_execute": False,
    }
