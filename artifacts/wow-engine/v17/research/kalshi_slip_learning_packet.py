"""Anonymized, non-authoritative Kalshi construction findings for both research teams.

This is a neutral, in-memory handoff packet for model researchers. It is NOT
an LLP/WOW source-host observation, Action payload, database writer, pregame
model input, recommendation, trading trigger, or proof of source rights.

Never copy private Kalshi account/order data into the public GitHub repository.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Mapping

SCHEMA = "WOW_KALSHI_SLIP_METHODS_RESEARCH_PACKET_V1"
CONSUMERS = ("LLP_TEAM_BETTING_ENGINE", "WOW_BETTING_ENGINE")
PATTERN_CODES: dict[str, tuple[str, str]] = {
    "SINGLE_PLAYER_SAME_GAME_MULTI_MARKET": (
        "DEPENDENCY_CORRELATION", "SINGLE_PLAYER_COMMON_CAUSE"
    ),
    "MULTI_PLAYER_SAME_GAME": (
        "SLIP_FRAGILITY", "MULTI_PLAYER_GAME_SCRIPT_FRAGILITY"
    ),
    "MULTI_EVENT_COMBINATION": (
        "DEPENDENCY_CORRELATION", "MULTI_EVENT_OVERLAP_RESEARCH"
    ),
    "SAME_TEAM_RECEIVING_OPPORTUNITY_COMPETITION": (
        "DEPENDENCY_CORRELATION", "SAME_TEAM_TARGET_SHARE_COMPETITION"
    ),
    "VERIFIED_NESTED_THRESHOLD_REDUNDANCY": (
        "DEPENDENCY_CORRELATION", "VERIFIED_NESTED_THRESHOLD_RESEARCH"
    ),
    "POTENTIAL_SAME_STAT_THRESHOLD_NESTING": (
        "RESEARCH_HYPOTHESIS", "NESTED_STAT_CONTRACT_NOT_YET_VERIFIED"
    ),
    "COMPLEMENTARY_STATS_SHARED_PLAYER_ROLE": (
        "DEPENDENCY_CORRELATION", "COMPLEMENTARY_PLAYER_STATS_DEPENDENCE"
    ),
    "REPEATED_EVENT_EXPOSURE": (
        "SLIP_FRAGILITY", "MULTI_EVENT_HIDDEN_GAME_OVERLAP"
    ),
    "REPEATED_PLAYER_EXPOSURE": (
        "SLIP_FRAGILITY", "MULTI_EVENT_HIDDEN_PLAYER_OVERLAP"
    ),
    "PAYOUT_MINUS_ENTRY_BEFORE_UNOBSERVED_FEES": (
        "QUOTE_MARKET_SANITY", "FEE_BASIS_UNVERIFIED"
    ),
    "TOTAL_VS_SETTLEMENT_PAYOUT_MISMATCH": (
        "SETTLEMENT_DIAGNOSTIC", "TOTAL_PAYOUT_NOT_SETTLEMENT_OUTCOME"
    ),
}
_ALLOWED_DIAGNOSTIC = "STRUCTURAL_TOPOLOGY_ONLY_NOT_MODEL_CERTIFIED"
_ALLOWED_SNAPSHOT = "PAYOUT_MINUS_ENTRY_BEFORE_UNOBSERVED_FEES"


class ResearchPacketHold(ValueError):
    """Untested or unsafe evidence cannot become a research handoff."""


def build_research_packet(
    *, topology_diagnostics: list[Mapping[str, Any]],
    snapshot_audit: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile code-only research signals; never include users or market IDs.

    We do not transmit, persist, score, train on, or authenticate observations.
    After PR #2670, an *independent engine-originated* finding may be reconciled
    for POST by its own authorized host. The original user-sourced record must
    never be misrepresented as originating from one of the two engines.
    """
    if not isinstance(topology_diagnostics, list):
        raise ResearchPacketHold("TOPOLOGY_LIST_REQUIRED")
    codes = Counter()
    included = 0
    for diag in topology_diagnostics:
        if not isinstance(diag, Mapping):
            raise ResearchPacketHold("TOPOLOGY_OBJECT_REQUIRED")
        if (diag.get("status") != _ALLOWED_DIAGNOSTIC
                or diag.get("calibration_eligible") is not False
                or diag.get("model_feature_eligible") is not False
                or diag.get("probability_publishable") is not False
                or diag.get("can_execute") is not False):
            raise ResearchPacketHold("TOPOLOGY_NOT_RESEARCH_ONLY_VERIFIED")
        structure = diag.get("structure")
        if structure not in (
            "SINGLE_PLAYER_SAME_GAME_MULTI_MARKET",
            "MULTI_PLAYER_SAME_GAME",
            "MULTI_EVENT_COMBINATION",
        ):
            raise ResearchPacketHold("STRUCTURE_TYPE_UNKNOWN")
        codes[structure] += 1
        factors = diag.get("shared_failure_factors")
        if not isinstance(factors, list) or any(
            not isinstance(x, str) for x in factors
        ):
            raise ResearchPacketHold("INVALID_FAILURE_FACTORS")
        for code in set(factors):
            if code in PATTERN_CODES:
                codes[code] += 1
        included += 1

    if snapshot_audit is not None:
        if (not isinstance(snapshot_audit, Mapping)
                or snapshot_audit.get("economics_basis") != _ALLOWED_SNAPSHOT
                or snapshot_audit.get("fees_observed") is not False
                or snapshot_audit.get("model_feature_eligible") is not False
                or snapshot_audit.get("probability_publishable") is not False
                or snapshot_audit.get("can_execute") is not False):
            raise ResearchPacketHold("SNAPSHOT_BASIS_UNSAFE")
        codes[_ALLOWED_SNAPSHOT] += 1
        mismatch = snapshot_audit.get("total_vs_settlement_payout_mismatch_rows")
        if type(mismatch) is not int or mismatch < 0:
            raise ResearchPacketHold("SNAPSHOT_MISMATCH_COUNT_INVALID")
        if mismatch:
            codes["TOTAL_VS_SETTLEMENT_PAYOUT_MISMATCH"] += 1

    findings = [
        {
            "finding_type": PATTERN_CODES[code][0],
            "finding_code": PATTERN_CODES[code][1],
            "observed_research_groups": codes[code],
            "analysis_stage": "POSTMORTEM_RESEARCH",
            "evidence_status": "UNVERIFIED_RESEARCH",
            "model_feature_eligible": False,
            "probability_publishable": False,
            "can_execute": False,
        }
        for code in sorted(codes) if code in PATTERN_CODES
    ]

    return {
        "schema_version": SCHEMA,
        "status": "RESEARCH_PACKET_LOCAL_ONLY_NOT_PERSISTED",
        "origin_provenance": "USER_ACCOUNT_NOTIFICATIONS_OR_UNVERIFIED_RESEARCH",
        "source_host": None,  # NEVER impersonate an engine-authenticated host
        "target_consumer_hosts": list(CONSUMERS),
        "scope": "COHORT_RESEARCH",
        "topology_groups_inspected": included,
        "findings": findings,
        "cross_host_published": False,
        "scientific_lift_established": False,
        "model_feature_eligible": False,
        "probability_publishable": False,
        "can_execute": False,
    }
