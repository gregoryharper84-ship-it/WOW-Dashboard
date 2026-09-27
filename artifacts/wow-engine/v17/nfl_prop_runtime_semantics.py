"""Narrow probability-neutral runtime patches for NFL prop boundary repair.

This module deliberately does *not* replace the canonical sport-aware hydration
router. Existing canonical/opponent identity conflicts must keep their original
PROP_EVENT_IDENTITY_CONFLICT semantics and the router's test/injection seams must
remain intact.

Only two independent defects are patched here:
- parallel row exceptions preserve typed blockers without falsely claiming a
  specialist ran; and
- pre-scorer durable terminal rows return an explicit no-prediction receipt state.
"""
from __future__ import annotations

from v17 import interactive_pick_parallel
from v17 import nfl_prop_boundary_integrity as boundary

_PATCH_FLAG = "_wow_nfl_prop_runtime_semantics_installed"


def install_nfl_prop_runtime_semantics() -> bool:
    if getattr(interactive_pick_parallel, _PATCH_FLAG, False):
        return True

    interactive_pick_parallel._unexpected_row_failure = boundary._typed_parallel_failure
    boundary._install_receipt_semantics()
    setattr(interactive_pick_parallel, _PATCH_FLAG, True)
    return True


__all__ = ["install_nfl_prop_runtime_semantics"]
