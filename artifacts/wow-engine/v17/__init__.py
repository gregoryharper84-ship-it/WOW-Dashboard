"""WOW V17 active-generation modules.

Importing the package in shared lower-layer tests must not mutate runtime routing.
Response-semantics adapters are composed only in an explicitly active V17 runtime.
Adapters are idempotent and cannot authorize execution.
"""
from __future__ import annotations

import os


def compose_active_runtime() -> bool:
    """Install V17 response semantics only for the explicitly active runtime."""
    if os.getenv("WOW_V17_ACTIVE", "0") != "1":
        return False
    from v17.prop_response_semantics import install_prop_response_semantics
    from v17.projected_lineup_scenario_modeling import install_projected_lineup_semantics

    prop_ok = install_prop_response_semantics()
    lineup_ok = install_projected_lineup_semantics()
    return bool(prop_ok and lineup_ok)


# Production activation is environment-governed. Shared imports with V17 disabled
# are side-effect free, preserving lower-layer contract-test isolation.
compose_active_runtime()


__all__ = ["compose_active_runtime"]
