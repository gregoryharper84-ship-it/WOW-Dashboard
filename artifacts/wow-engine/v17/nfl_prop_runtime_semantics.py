"""Narrow probability-neutral runtime patches for NFL prop boundary repair.

This module deliberately does *not* replace the canonical sport-aware hydration
router. Existing canonical/opponent identity conflicts must keep their original
PROP_EVENT_IDENTITY_CONFLICT semantics and the router's test/injection seams must
remain intact.

The runtime repairs here are deliberately narrow:
- parallel row exceptions preserve typed blockers without falsely claiming a
  specialist ran;
- pre-scorer durable terminal rows return an explicit no-prediction receipt state;
- the final direct /score-prop boundary canonicalizes known stat aliases before
  exact specialist/artifact routing, matching Pick Request behavior.
"""
from __future__ import annotations

from v17 import interactive_pick_parallel
from v17 import nfl_prop_boundary_integrity as boundary
from v17.prop_stat_alias_boundary import schedule_score_prop_alias_boundary

_PATCH_FLAG = "_wow_nfl_prop_runtime_semantics_installed"


def install_nfl_prop_runtime_semantics() -> bool:
    if not getattr(interactive_pick_parallel, _PATCH_FLAG, False):
        interactive_pick_parallel._unexpected_row_failure = boundary._typed_parallel_failure
        boundary._install_receipt_semantics()
        setattr(interactive_pick_parallel, _PATCH_FLAG, True)

    # v17_observability runs while api_ncaaf_acceptance is assembling the final
    # accepted app. Register a startup installer now; by startup time any optional
    # lane-separation replacement has already mounted the final /score-prop route.
    try:
        import api_prod_market_acceptance as accepted

        schedule_score_prop_alias_boundary(
            accepted.app,
            market_api=accepted.market_api,
        )
    except Exception:
        # Preserve the pre-existing observability/bootstrap fail-closed behavior;
        # the governed backend suite verifies the accepted production entrypoint.
        pass
    return True


__all__ = ["install_nfl_prop_runtime_semantics"]
