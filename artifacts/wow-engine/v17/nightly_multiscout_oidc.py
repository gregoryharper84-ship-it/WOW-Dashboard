"""Run Nightly Multi-Scout with refreshable GitHub Actions OIDC source auth.

If the legacy proxy bearer is configured it remains authoritative. Otherwise
this wrapper mints a short-lived GitHub OIDC token before proxy calls; the OIDC
client caches only briefly, so long scans refresh automatically. Scout itself
remains discovery-only and can_execute=false.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# The post-merge GitHub workflow executes this wrapper directly from the
# artifacts/wow-engine working directory (``python v17/nightly_multiscout_oidc.py``).
# In that mode Python puts only ``.../wow-engine/v17`` on sys.path, so the
# package-level ``from v17 ...`` import below would fail before any governed
# acquisition logic could run. Add only the package parent for direct-script
# compatibility; module/import execution is unchanged.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v17 import nightly_multiscout as scout
from v17.github_actions_oidc_client import GitHubOIDCMintError, mint_github_actions_oidc


def install_refreshable_oidc_proxy_auth() -> None:
    if os.environ.get("WOW_ODDS_PROXY_ACTION_KEY"):
        return

    original = scout.proxy_get

    def _proxy_get(path: str, params: dict[str, Any] | None = None) -> scout.FetchResult:
        try:
            os.environ["WOW_GITHUB_OIDC_TOKEN"] = mint_github_actions_oidc()
        except GitHubOIDCMintError as exc:
            return scout.FetchResult(False, code=str(exc))
        return original(path, params)

    scout.proxy_get = _proxy_get


def main() -> int:
    install_refreshable_oidc_proxy_auth()
    return scout.main()


if __name__ == "__main__":
    sys.exit(main())
