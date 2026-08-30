"""Compatibility no-op for the retired experimental pick-request installer.

The canonical governed batch boundary is installed by pick_request_runtime via
api_ncaaf_acceptance. This module exists only so the route-first /score-prop
compatibility layer can be replayed without mounting a second ingress.
"""
from __future__ import annotations

from typing import Any


def install_pick_request_routes(**_kwargs: Any) -> None:
    return None
