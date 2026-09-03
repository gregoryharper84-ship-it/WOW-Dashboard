"""WOW V17 active-generation modules.

Importing the package in shared lower-layer tests must not mutate runtime routing.
Response-semantics adapters are composed only in an explicitly active V17 runtime.
Numerical computation is exposed through a side-effect-free registry; controlling
specialists remain responsible for registering certified sport/stat adapters.
"""
from __future__ import annotations

import os


def get_certified_numerical_registry():
    """Return the shared all-sports PROP/ML numerical adapter registry."""
    from v17.certified_numerical_engine import DEFAULT_NUMERICAL_REGISTRY

    return DEFAULT_NUMERICAL_REGISTRY


def compose_active_runtime() -> bool:
    """Install V17 response semantics only for the explicitly active runtime."""
    if os.getenv("WOW_V17_ACTIVE", "0") != "1":
        return False
    from v17.prop_response_semantics import install_prop_response_semantics
    from v17.projected_lineup_scenario_modeling import install_projected_lineup_semantics

    # Import/initialize the registry without auto-registering generic models. Exact
    # sport/stat specialists own registration and remain the sole model authority.
    get_certified_numerical_registry()
    prop_ok = install_prop_response_semantics()
    lineup_ok = install_projected_lineup_semantics()
    return bool(prop_ok and lineup_ok)


# Production activation is environment-governed. Shared imports with V17 disabled
# are side-effect free, preserving lower-layer contract-test isolation.
compose_active_runtime()


__all__ = ["compose_active_runtime", "get_certified_numerical_registry"]
