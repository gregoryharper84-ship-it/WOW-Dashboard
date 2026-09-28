"""Deterministic persistence records for the JS research learning ledger.

The helpers build immutable records only; they do not mutate production data by
themselves. Database adoption is governed separately by the additive migration.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any, Mapping

from v17.js_style.contracts import BoardSnapshot, CandidateObservation

_FORBIDDEN_AUTHORITY_KEYS = frozenset(
    {
        "js_probability",
        "js_hit_probability",
        "js_win_probability",
        "js_lower_bound",
    }
)


def _digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_research_only(payload: Mapping[str, Any]) -> None:
    forbidden = sorted(_FORBIDDEN_AUTHORITY_KEYS.intersection(payload))
    if forbidden:
        raise ValueError("JS_FORBIDDEN_AUTHORITY_FIELD:" + ",".join(forbidden))
    for key in ("js_probability_authority", "probability_publishable", "rank_eligible", "can_execute"):
        if key in payload and payload[key] is not False:
            raise ValueError(f"JS_GOVERNANCE_FLAG_MUST_BE_FALSE:{key}")


def board_snapshot_record(snapshot: BoardSnapshot) -> dict[str, Any]:
    payload = asdict(snapshot)
    _validate_research_only(payload)
    expected = _digest(
        {
            "provider": snapshot.provider,
            "captured_at": snapshot.captured_at,
            "source_snapshot_digest": snapshot.source_snapshot_digest,
            "feature_schema_version": snapshot.feature_schema_version,
        }
    )
    if snapshot.board_snapshot_id != expected:
        raise ValueError("JS_BOARD_SNAPSHOT_ID_MISMATCH")
    return payload


def make_board_snapshot_id(
    *, provider: str, captured_at: str, source_snapshot_digest: str, feature_schema_version: str
) -> str:
    return _digest(
        {
            "provider": provider,
            "captured_at": captured_at,
            "source_snapshot_digest": source_snapshot_digest,
            "feature_schema_version": feature_schema_version,
        }
    )


def candidate_observation_record(observation: CandidateObservation) -> dict[str, Any]:
    payload = asdict(observation)
    _validate_research_only(payload)
    identity_payload = {
        "board_snapshot_id": observation.board_snapshot_id,
        "provider_event_alias": observation.provider_event_alias,
        "canonical_event_id": observation.canonical_event_id,
        "participant_id": observation.participant_id,
        "participant_name": observation.participant_name,
        "sport": observation.sport,
        "stat": observation.stat,
        "period": observation.period,
        "exact_settlement_threshold": observation.exact_settlement_threshold,
        "direction": observation.direction,
        "settlement_rule_version": observation.settlement_rule_version,
        "feature_schema_version": observation.feature_schema_version,
        "ruleset_version": observation.ruleset_version,
    }
    payload["observation_id"] = _digest(identity_payload)
    return payload


def selection_event_record(
    *, observation_id: str, selected_by_js: bool, selected_at: str, selection_source: str
) -> dict[str, Any]:
    payload = {
        "observation_id": observation_id,
        "selected_by_js": bool(selected_by_js),
        "selected_at": selected_at,
        "selection_source": selection_source,
        "js_probability_authority": False,
        "can_execute": False,
    }
    payload["selection_event_id"] = _digest(payload)
    _validate_research_only(payload)
    return payload


__all__ = [
    "board_snapshot_record",
    "candidate_observation_record",
    "make_board_snapshot_id",
    "selection_event_record",
]
