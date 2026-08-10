"""
gate_engine/universal_agent/roles/__init__.py
WOW-PATCH-2026-08-09-UNIVERSAL-AGENT-CORE-V1 / Phase B1

Public API for the six universal advisory role contracts.

All validators are advisory-only, fail-closed, and forbidden-governance-safe.
No LLM calls, no production routing, no app.py wiring.
"""
from gate_engine.universal_agent.roles.role_base import (
    SCHEMA_VERSION,
    RoleViolationCode,
    EvidenceAvailability,
    validate_role_advisory_output,
)
from gate_engine.universal_agent.roles.data_slate_integrity import (
    ROLE_ID as DATA_SLATE_INTEGRITY_ROLE_ID,
    validate_data_slate_integrity_output,
    valid_data_slate_integrity_payload,
    REGISTRY_ENTRY as DATA_SLATE_INTEGRITY_ENTRY,
)
from gate_engine.universal_agent.roles.news_status import (
    ROLE_ID as NEWS_STATUS_ROLE_ID,
    validate_news_status_output,
    valid_news_status_payload,
    REGISTRY_ENTRY as NEWS_STATUS_ENTRY,
)
from gate_engine.universal_agent.roles.market_exact_line import (
    ROLE_ID as MARKET_EXACT_LINE_ROLE_ID,
    validate_market_exact_line_output,
    valid_market_exact_line_payload,
    REGISTRY_ENTRY as MARKET_EXACT_LINE_ENTRY,
)
from gate_engine.universal_agent.roles.sport_specialist import (
    ROLE_ID as SPORT_SPECIALIST_ROLE_ID,
    validate_sport_specialist_output,
    valid_sport_specialist_payload,
    REGISTRY_ENTRY as SPORT_SPECIALIST_ENTRY,
)
from gate_engine.universal_agent.roles.failure_contradiction import (
    ROLE_ID as FAILURE_CONTRADICTION_ROLE_ID,
    validate_failure_contradiction_output,
    valid_failure_contradiction_payload,
    REGISTRY_ENTRY as FAILURE_CONTRADICTION_ENTRY,
)
from gate_engine.universal_agent.roles.final_refresh import (
    ROLE_ID as FINAL_REFRESH_ROLE_ID,
    validate_final_refresh_output,
    valid_final_refresh_payload,
    REGISTRY_ENTRY as FINAL_REFRESH_ENTRY,
)
from gate_engine.universal_agent.roles.registry_b1 import (
    ALL_B1_ENTRIES,
    build_b1_registry,
    register_b1_roles,
)

__all__ = [
    # Base
    "SCHEMA_VERSION",
    "RoleViolationCode",
    "EvidenceAvailability",
    "validate_role_advisory_output",
    # Role IDs
    "DATA_SLATE_INTEGRITY_ROLE_ID",
    "NEWS_STATUS_ROLE_ID",
    "MARKET_EXACT_LINE_ROLE_ID",
    "SPORT_SPECIALIST_ROLE_ID",
    "FAILURE_CONTRADICTION_ROLE_ID",
    "FINAL_REFRESH_ROLE_ID",
    # Validators
    "validate_data_slate_integrity_output",
    "validate_news_status_output",
    "validate_market_exact_line_output",
    "validate_sport_specialist_output",
    "validate_failure_contradiction_output",
    "validate_final_refresh_output",
    # Test helpers
    "valid_data_slate_integrity_payload",
    "valid_news_status_payload",
    "valid_market_exact_line_payload",
    "valid_sport_specialist_payload",
    "valid_failure_contradiction_payload",
    "valid_final_refresh_payload",
    # Registry entries
    "DATA_SLATE_INTEGRITY_ENTRY",
    "NEWS_STATUS_ENTRY",
    "MARKET_EXACT_LINE_ENTRY",
    "SPORT_SPECIALIST_ENTRY",
    "FAILURE_CONTRADICTION_ENTRY",
    "FINAL_REFRESH_ENTRY",
    # Registry helpers
    "ALL_B1_ENTRIES",
    "build_b1_registry",
    "register_b1_roles",
]
