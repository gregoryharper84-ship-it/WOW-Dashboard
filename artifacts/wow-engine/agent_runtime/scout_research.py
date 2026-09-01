"""WOW v16 Scout + mandatory Research Team contracts.

These helpers are intentionally non-predictive. Scouts may prioritize discovery
work and researchers may summarize supplied/provider evidence, but neither may
originate a model probability, calibrated bound, terminal label, money field,
or execution authority. Exactly one controlling fitted-model specialist remains
responsible for probability production downstream.
"""
from __future__ import annotations

from typing import Any

SCOUT_WORKERS = frozenset({
    "wow.global-scout-coordinator",
    "wow.prop-scout-router",
    "wow.ml-event-scout-router",
})

RESEARCH_WORKERS = (
    "wow.source-provenance-researcher",
    "wow.participant-status-researcher",
    "wow.history-comparables-researcher",
    "wow.matchup-context-researcher",
    "wow.market-settlement-researcher",
)
RESEARCH_RECONCILER = "wow.research-evidence-reconciler"
ALL_RESEARCH_WORKERS = frozenset((*RESEARCH_WORKERS, RESEARCH_RECONCILER))

# Keys a Scout/Research worker may never create or echo as its own authority.
# Market-implied data remains permitted only under explicitly market-labelled
# source fields; these names are reserved for governed predictive output.
FORBIDDEN_AUTHORITY_KEYS = frozenset({
    "raw_probability",
    "model_probability",
    "calibrated_probability",
    "calibrated_lower_bound",
    "calibrated_upper_bound",
    "calibrated_probability_lower_bound",
    "calibrated_probability_upper_bound",
    "probability_publishable",
    "terminal_label",
    "final_label",
    "final_terminal_ceiling",
    "edge",
    "ev",
    "expected_value",
    "stake",
    "stake_size",
    "capital_allocation",
})

PROP_MARKET_FAMILIES = frozenset({
    "PLAYER_PROP", "PLAYER_PROPS", "PROP", "PITCHER_PROP", "BATTER_PROP",
})


def scout_lane(candidate: dict[str, Any]) -> str:
    market = str(candidate.get("market_family") or "").upper().strip()
    return "PROP" if market in PROP_MARKET_FAMILIES else "ML_EVENT"


def discovery_mode(payload: dict[str, Any]) -> str:
    rows = payload.get("rows")
    discovery_enabled = payload.get("discovery_enabled") is True
    return "FOCUSED" if isinstance(rows, list) and bool(rows) else ("FULL_SLATE" if discovery_enabled else "FOCUSED")


def scrub_authority(value: Any) -> Any:
    """Recursively remove predictive/money/terminal authority from evidence.

    The original candidate/evidence remains in the immutable request payload and
    is consumed by the existing evidence hydrator; this sanitized form is only
    for Scout/Research worker outputs and audit summaries.
    """
    if isinstance(value, dict):
        return {
            str(k): scrub_authority(v)
            for k, v in value.items()
            if str(k).lower() not in FORBIDDEN_AUTHORITY_KEYS
            and not (str(k).lower() == "can_execute" and v is True)
        }
    if isinstance(value, list):
        return [scrub_authority(v) for v in value]
    return value


def evidence_summary(evidence: Any, role: str) -> dict[str, Any]:
    """Return an evidence-only status summary without inventing missing facts."""
    if not isinstance(evidence, dict):
        return {
            "research_status": "DATA_UNOBTAINABLE",
            "research_role": role,
            "missing_fields": ["evidence"],
            "evidence_fragment": {},
            "prediction_authority": False,
            "can_execute": False,
        }

    keys = set(evidence.keys())
    checks: dict[str, tuple[str, ...]] = {
        "SOURCE_PROVENANCE": ("source_attempts", "sources", "source_records"),
        "PARTICIPANT_STATUS": ("role_validation", "participant_status", "official_event_status"),
        "HISTORY_COMPARABLES": ("game_log", "box_score_log", "historical_evidence"),
        "MATCHUP_CONTEXT": ("matchup_context", "event_context", "role_validation"),
        "MARKET_SETTLEMENT": ("market_identity", "settlement_identity", "exact_line", "settlement_rules"),
    }
    candidates = checks.get(role, ())
    present = [k for k in candidates if k in keys and evidence.get(k) not in (None, "", [], {})]
    status = "READY" if present else "PARTIAL"
    fragment = {k: evidence.get(k) for k in present}
    return {
        "research_status": status,
        "research_role": role,
        "observed_fields": sorted(present),
        "missing_fields": sorted(k for k in candidates if k not in present),
        "evidence_fragment": scrub_authority(fragment),
        "prediction_authority": False,
        "can_execute": False,
    }


def validate_non_predictive_output(output: dict[str, Any]) -> list[str]:
    """Defense-in-depth check for Scout/Research authority leakage."""
    violations: list[str] = []

    def walk(value: Any, path: str = "") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                key_s = str(key).lower()
                child_path = f"{path}.{key}" if path else str(key)
                if key_s in FORBIDDEN_AUTHORITY_KEYS:
                    violations.append(child_path)
                if key_s == "can_execute" and child is True:
                    violations.append(child_path)
                walk(child, child_path)
        elif isinstance(value, list):
            for idx, child in enumerate(value):
                walk(child, f"{path}[{idx}]")

    walk(output)
    return sorted(set(violations))
