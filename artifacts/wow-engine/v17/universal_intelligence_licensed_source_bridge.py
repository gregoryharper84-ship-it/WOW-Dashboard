"""Offline registry-aware source evidence -> V17 research/shadow bridge.

Consumes caller-supplied observations ONLY. No fetch, credentials copied, storage,
official identity attestation, fitted scoring, source-rights attestation or deploy.
Registry readiness means *candidate source eligibility*, not a license opinion or
independent verification of any particular observation.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import math
import re
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

from v17.model_source_entitlements import SOURCES, source_readiness
from v17.universal_sports_intelligence_research import (
    SPORT_DOMAINS, UNIVERSAL_DOMAINS, compile_intelligence_research_snapshot,
)
from v17.universal_intelligence_shadow_handoff import (
    TARGETS, prepare_shadow_feature_handoff,
)

SCHEMA = "WOW_V17_LICENSED_SOURCE_SHADOW_BRIDGE_V1"
MAX_ROWS = 100
SHA256 = re.compile(r"[a-f0-9]{64}\Z")
FORBIDDEN = frozenset((
    "model_probability", "calibrated_probability", "calibrated_lower_bound",
    "market_implied_probability", "scenario_probability", "probability_publishable",
    "can_execute", "source_rights_attested", "official_identity_attested",
    "pregame_model_feature_eligible", "sporting_probability", "bookmaker_odds",
    "sportsbook", "odds", "price", "betting_line",
))


class SourceBridgeHold(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require(ok: bool, code: str) -> None:
    if not ok:
        raise SourceBridgeHold(code)


def _utc(value: Any) -> datetime:
    _require(isinstance(value, str), "SOURCE_BRIDGE_TIME_INVALID")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        raise SourceBridgeHold("SOURCE_BRIDGE_TIME_INVALID") from exc
    _require(dt.utcoffset() is not None, "SOURCE_BRIDGE_TIME_INVALID")
    return dt.astimezone(timezone.utc)


def _row_evidence(
    row: Mapping[str, Any], *, sport: str, event_id: str,
    cutoff: datetime, kickoff: datetime, seen: set[tuple[str, str]],
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    _require(isinstance(row, Mapping), "SOURCE_BRIDGE_ROW_INVALID")
    _require(not FORBIDDEN.intersection(row.keys()), "SOURCE_BRIDGE_AUTHORITY_INJECTION")
    sid = row.get("source_id")
    _require(isinstance(sid, str) and sid in SOURCES,
             "SOURCE_BRIDGE_SOURCE_UNCLASSIFIED")
    src = SOURCES[sid]
    _require(sport in src.sports, "SOURCE_BRIDGE_SOURCE_SPORT_MISMATCH")
    _require(src.use in {
        "TRAINING_OPEN_LICENSED", "TRAINING_WITH_ENTITLEMENT",
        "TRAINING_WITH_CREDENTIAL",
    }, "SOURCE_BRIDGE_SOURCE_NOT_TRAINING_ELIGIBLE")
    readiness = source_readiness(sid)
    _require(readiness.ready_for_candidate_training,
             "SOURCE_BRIDGE_SOURCE_PREFLIGHT_NOT_READY")
    _require(row.get("sport") == sport and row.get("event_id_claimed") == event_id,
             "SOURCE_BRIDGE_EVENT_IDENTITY_CONFLICT")
    url, rid = row.get("source_url"), row.get("source_record_id")
    _require(isinstance(url, str) and isinstance(rid, str) and bool(rid.strip()),
             "SOURCE_BRIDGE_PROVENANCE_INVALID")
    parsed = urlparse(url)
    _require(parsed.scheme == "https" and bool(parsed.hostname)
             and parsed.username is None and parsed.password is None
             and parsed.hostname.lower() not in {"propsmadness.com", "www.propsmadness.com"},
             "SOURCE_BRIDGE_PROVENANCE_INVALID")
    digest = row.get("payload_sha256")
    _require(isinstance(digest, str) and SHA256.fullmatch(digest) is not None,
             "SOURCE_BRIDGE_PAYLOAD_DIGEST_INVALID")
    observed, available, retrieved = (
        _utc(row.get("observed_at")), _utc(row.get("first_available_at")),
        _utc(row.get("retrieved_at")),
    )
    _require(observed <= available <= retrieved <= cutoff < kickoff,
             "SOURCE_BRIDGE_POST_CUTOFF_OR_LEAKAGE")
    domain, metric, entity = row.get("domain"), row.get("metric"), row.get("entity_id")
    _require(isinstance(domain, str)
             and domain in (set(UNIVERSAL_DOMAINS) | set(SPORT_DOMAINS[sport])),
             "SOURCE_BRIDGE_DOMAIN_UNSUPPORTED")
    _require(all(isinstance(x, str) and bool(x.strip()) for x in (metric, entity)),
             "SOURCE_BRIDGE_FEATURE_IDENTITY_INVALID")
    value = row.get("value")
    _require((isinstance(value, str) and bool(value.strip()))
             or (type(value) in (int, float) and math.isfinite(value)),
             "SOURCE_BRIDGE_VALUE_INVALID")
    kind = row.get("evidence_kind")
    _require(kind in {"SOURCE_OBSERVATION", "CONTEXT_HYPOTHESIS"},
             "SOURCE_BRIDGE_KIND_INVALID")
    target = row.get("target_lanes")
    _require(isinstance(target, (list, tuple))
             and len(set(target)) == len(target)
             and all(isinstance(lane, str) and lane in TARGETS for lane in target),
             "SOURCE_BRIDGE_TARGET_INVALID")
    source_key = (url, rid)
    _require(source_key not in seen, "SOURCE_BRIDGE_DUPLICATE_SOURCE_ROW")
    seen.add(source_key)
    ref = hashlib.sha256(f"{url}|{rid}".encode()).hexdigest()
    fact = {
        "domain": domain, "metric": metric, "entity_id": entity,
        "value": value, "evidence_kind": kind,
        "source_record_id": rid, "source_url": url,
        "observed_at": observed.isoformat(),
        "first_available_at": available.isoformat(),
        "retrieved_at": retrieved.isoformat(),
        "rights_receipt_id": None,
    }
    provenance = {
        "source_id": sid, "source_reference": ref,
        "source_record_id": rid, "payload_sha256_claimed": digest,
        "registry_use": src.use, "registry_license_id": src.license_id,
        "candidate_source_registry_ready": True,
        "independent_rights_review_pending": src.certification_source_review_required,
        "payload_independently_verified": False,
        "source_entitlement_independently_attested": False,
        "official_event_identity_attested": False,
    }
    return fact, provenance, list(target)


def compile_registry_source_shadow_bridge(
    *, sport: str, official_event_id_claimed: str, event_start_utc: str,
    as_of_utc: str, observations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Reconcile valid and held rows independently; publish no probabilities."""
    _require(sport in SPORT_DOMAINS, "SOURCE_BRIDGE_SPORT_UNSUPPORTED")
    _require(isinstance(official_event_id_claimed, str)
             and bool(official_event_id_claimed.strip()), "SOURCE_BRIDGE_EVENT_MISSING")
    kickoff, cutoff = _utc(event_start_utc), _utc(as_of_utc)
    _require(cutoff < kickoff, "SOURCE_BRIDGE_NOT_PREGAME")
    _require(isinstance(observations, (list, tuple)) and len(observations) <= MAX_ROWS,
             "SOURCE_BRIDGE_INPUT_INVALID")
    seen: set[tuple[str, str]] = set()
    accepted: list[dict[str, Any]] = []
    provenance: list[dict[str, Any]] = []
    target_map: dict[str, list[str]] = {}
    decisions: list[dict[str, Any]] = []
    for i, row in enumerate(observations):
        try:
            fact, source, lanes = _row_evidence(
                row, sport=sport, event_id=official_event_id_claimed,
                cutoff=cutoff, kickoff=kickoff, seen=seen,
            )
        except SourceBridgeHold as exc:
            decisions.append({"row_index": i, "status": exc.code,
                              "candidate_eligible": False, "can_execute": False})
            continue
        accepted.append(fact)
        provenance.append(source)
        target_map[source["source_reference"]] = lanes
        decisions.append({"row_index": i, "status": "SOURCE_CANDIDATE_UNVERIFIED",
                          "candidate_eligible": True, "can_execute": False})
    snapshot = compile_intelligence_research_snapshot(
        sport=sport, official_event_id=official_event_id_claimed,
        event_start_utc=event_start_utc, as_of_utc=as_of_utc, facts=accepted,
    )
    shadow = prepare_shadow_feature_handoff(snapshot, proposed_targets=target_map)
    _require(len(shadow["rows"]) == len(accepted) == len(provenance),
             "SOURCE_BRIDGE_RECONCILIATION_INVALID")
    return {
        "schema": SCHEMA,
        "status": "SOURCE_CANDIDATES_RESEARCH_ONLY",
        "sport": sport, "event_id_claimed": official_event_id_claimed,
        "received": len(observations), "candidate_rows": len(accepted),
        "held": len(observations) - len(accepted), "row_dispositions": decisions,
        "source_claims": provenance, "research_snapshot": snapshot,
        "shadow_handoff": shadow, "reconciled_exactly_once": True,
        "source_rights_independently_attested": False,
        "event_identity_independently_attested": False,
        "historical_training_authorized_by_bridge": False,
        "model_consumption_receipts": [],
        "calibrated_probability": None, "calibrated_lower_bound": None,
        "probability_publishable": False, "can_execute": False,
    }


__all__ = ["SCHEMA", "SourceBridgeHold", "compile_registry_source_shadow_bridge"]
