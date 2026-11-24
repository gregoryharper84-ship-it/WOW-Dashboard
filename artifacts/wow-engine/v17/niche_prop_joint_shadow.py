"""Aligned multi-leg scenario/payout study, not a certified joint model.

Requires complete *joint* scenario outcome vectors and a signed whole-lineup
payout receipt; never estimates P(all win) by multiplying leg hit rates.
External scenario provenance and provider authorization remain unverified.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Mapping, Sequence

from v17.niche_prop_quote_integrity import verify_lineup_payout

CAN_EXECUTE = False
MIN_JOINT_DRAWS = 1000
MAX_JOINT_DRAWS = 1000000


class JointShadowHold(ValueError):
    """No payout or probability claim when joint evidence is inadequate."""


def _hold(code: str) -> None:
    raise JointShadowHold(code)


def _utc(value: Any) -> datetime:
    if not isinstance(value, str):
        _hold("JOINT_FROZEN_CHRONOLOGY_INVALID")
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _hold("JOINT_FROZEN_CHRONOLOGY_INVALID")
    if d.tzinfo is None or d.utcoffset() is None:
        _hold("JOINT_FROZEN_CHRONOLOGY_INVALID")
    return d.astimezone(timezone.utc)


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _is_sha(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(ch in "0123456789abcdef" for ch in value.lower()))


def evaluate_joint_shadow(
    signed_offers: Sequence[Mapping[str, Any]],
    signed_payout: Mapping[str, Any],
    scenario_bundle: Mapping[str, Any],
    *,
    secret: bytes, approved_sources: Mapping[str, str], as_of_utc: str,
) -> dict[str, Any]:
    """Descriptive Monte Carlo diagnostic from aligned, caller-claimed draws.

    No calibrated joint probability or certified positive EV. A signed internal
    lineup is not proof that its PrizePicks price or source has been authorized.
    """
    payout = verify_lineup_payout(
        signed_payout, secret, signed_offers,
        as_of_utc=as_of_utc, approved_sources=approved_sources,
    )
    nlegs = len(signed_offers)
    if not isinstance(scenario_bundle, Mapping):
        _hold("JOINT_SCENARIOS_MISSING")
    binding = [x["payload_digest"] for x in signed_offers]
    if scenario_bundle.get("ordered_offer_binding_digests") != binding:
        _hold("JOINT_LINEUP_BINDING_MISMATCH")
    digests = scenario_bundle.get("model_artifact_digests")
    if (not isinstance(digests, list) or len(digests) != nlegs
            or any(not _is_sha(d) for d in digests)
            or not _is_sha(scenario_bundle.get("source_snapshot_sha256"))
            or not isinstance(scenario_bundle.get("joint_source_receipt_id"), str)
            or not scenario_bundle["joint_source_receipt_id"].strip()):
        _hold("JOINT_MODEL_LINEAGE_MISSING")
    freeze = _utc(scenario_bundle.get("frozen_at_utc"))
    as_of = _utc(as_of_utc)
    starts = [_utc(x["payload"]["event_start_utc"]) for x in signed_offers]
    last_offer = max(_utc(x["payload"]["captured_at_utc"]) for x in signed_offers)
    payout_time = _utc(signed_payout["payload"]["captured_at_utc"])
    if freeze < max(last_offer, payout_time) or freeze > as_of or as_of >= min(starts):
        _hold("JOINT_FROZEN_CHRONOLOGY_INVALID")
    draws = scenario_bundle.get("draws")
    if not isinstance(draws, list) or not MIN_JOINT_DRAWS <= len(draws) <= MAX_JOINT_DRAWS:
        _hold("MINIMUM_JOINT_DRAWS_NOT_MET")
    counts: Counter = Counter()
    seen: set[str] = set()
    for draw in draws:
        if not isinstance(draw, Mapping):
            _hold("SCENARIO_OUTCOME_SHAPE_INVALID")
        ident = draw.get("scenario_id")
        if not isinstance(ident, str) or not ident or ident in seen:
            _hold("DUPLICATE_SCENARIO_ID_OR_MISSING")
        seen.add(ident)
        outcomes = draw.get("outcomes")
        if not isinstance(outcomes, list) or len(outcomes) != nlegs:
            _hold("SCENARIO_OUTCOME_SHAPE_INVALID")
        if any(x in ("PUSH", "VOID") for x in outcomes):
            _hold("SCENARIO_UNQUOTED_PUSH_VOID_RULES")
        if any(x not in ("WIN", "LOSS") for x in outcomes):
            _hold("SCENARIO_OUTCOME_SHAPE_INVALID")
        counts[outcomes.count("WIN")] += 1
    n = len(draws)
    by_hits = {str(k): counts[k] / n for k in range(nlegs + 1)}
    schedule = payout["payout_by_hits"]
    gross = sum(by_hits[str(k)] * schedule[str(k)] for k in range(nlegs + 1))
    return {
        "schema_version": "wow.v17.niche-joint-shadow.v1",
        "status": "SHADOW_UNCERTIFIED",
        "scenario_count": n,
        "research_all_clear_fraction": by_hits[str(nlegs)],
        "hit_count_distribution": by_hits,
        "research_gross_payout_multiple": gross,
        "research_net_return_per_unit": gross - 1,
        "joint_scenario_payload_digest": _hash(dict(scenario_bundle)),
        "lineup_binding_digest": payout["lineup_binding_digest"],
        "source_rights_independently_verified": False,
        "scenario_provenance_independently_authenticated": False,
        "calibrated_joint_probability": None,
        "positive_ev_certified": False,
        "model_certified": False,
        "can_execute": False,
    }
