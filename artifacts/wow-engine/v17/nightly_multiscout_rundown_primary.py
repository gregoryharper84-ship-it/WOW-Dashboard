"""Production Nightly Multi-Scout runner with TheRundown board-primary acquisition.

This composes the existing OIDC/router fallback stack rather than replacing it.
For the sports/events/event-market/event-odds paths that define the Scout board,
TheRundown is authoritative for inventory. A board auth/entitlement/coverage
failure is returned as a typed source failure instead of being silently replaced
by a smaller provider slate. Paths outside that contract keep the existing
OIDC/router/secondary behavior.

No probability or execution authority is introduced here. ``can_execute=false``
remains unchanged and the normal Nightly Multi-Scout routing contract sends
team/event candidates to LLP_TEAM_BETTING_ENGINE and props to WOW_PROP_LANE.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v17 import nightly_multiscout_oidc as base
from v17 import rundown_board_primary_v2 as board


def install_rundown_board_primary() -> None:
    existing = base.scout.proxy_get

    def _proxy_get(path: str, params: dict[str, Any] | None = None) -> base.scout.FetchResult:
        result = board.serve(path, params)
        if result is None:
            return existing(path, params)
        return base.scout.FetchResult(
            bool(result.ok),
            data=result.data,
            status=result.status,
            code=result.code or ("RUNDOWN_BOARD_PRIMARY_USED" if result.ok else "RUNDOWN_BOARD_PRIMARY_FAILED"),
        )

    base.scout.proxy_get = _proxy_get


def main() -> int:
    base.configure_source_failure_scope()
    base.configure_acquisition_router()
    base.enable_research_market_evidence()
    base.install_sharpapi_prop_compat()
    base.install_refreshable_oidc_proxy_auth()
    install_rundown_board_primary()
    return base.scout.main()


if __name__ == "__main__":
    sys.exit(main())
