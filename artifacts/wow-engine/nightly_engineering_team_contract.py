"""Compatibility import for the WOW V17 nightly engineering team contract.

The canonical implementation lives in ``v17.nightly_engineering_team_contract``.
This shim preserves the historical root-level import mode used by existing
incident-record tests and tooling that load modules via ``spec_from_file_location``.
It contains no independent workflow or probability logic.
"""

from v17.nightly_engineering_team_contract import (
    CLOSURE_RELEASE_STATUSES,
    PROTECTED_CONTRACT_KEYS,
    PROTECTED_SUBSYSTEMS,
    REPRODUCTION_STATUSES,
    ROLES,
    STAGE_OWNER,
    SUBSYSTEMS,
    TEAM_CONTRACT_VERSION,
    WORKFLOW_STAGES,
    architect_required,
    initial_team_fields,
    route_subsystem,
    self_check,
    transition_record,
    utc_now,
    validate_handoff_history,
    validate_team_record,
)

__all__ = [
    "CLOSURE_RELEASE_STATUSES",
    "PROTECTED_CONTRACT_KEYS",
    "PROTECTED_SUBSYSTEMS",
    "REPRODUCTION_STATUSES",
    "ROLES",
    "STAGE_OWNER",
    "SUBSYSTEMS",
    "TEAM_CONTRACT_VERSION",
    "WORKFLOW_STAGES",
    "architect_required",
    "initial_team_fields",
    "route_subsystem",
    "self_check",
    "transition_record",
    "utc_now",
    "validate_handoff_history",
    "validate_team_record",
]
