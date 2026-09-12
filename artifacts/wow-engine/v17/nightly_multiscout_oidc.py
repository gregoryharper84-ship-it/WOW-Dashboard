"""Run Nightly Multi-Scout with refreshable GitHub Actions OIDC source auth.

If the legacy proxy bearer is configured it remains authoritative and unchanged.
Otherwise this wrapper mints a short-lived GitHub OIDC token before proxy calls;
the OIDC client caches only briefly, so long scans refresh automatically.
Transient proxy cold-start failures on the OIDC path are retried without
weakening typed auth/governance failures. Scout itself remains discovery-only
and can_execute=false.
"""
from __future__ import annotations

import os
import sys
import time
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

TRANSIENT_HTTP_STATUSES = {500, 502, 503, 504}
TRANSIENT_SOURCE_CODES = {
    "URLError",
    "TimeoutError",
    "ConnectionError",
    "ConnectionRefusedError",
    "ConnectionResetError",
    "RemoteDisconnected",
}


def _retry_attempts() -> int:
    try:
        return max(1, int(os.environ.get("WOW_SCOUT_SOURCE_RETRY_ATTEMPTS", "3")))
    except ValueError:
        return 3


def _retry_base_seconds() -> float:
    try:
        return max(0.0, float(os.environ.get("WOW_SCOUT_SOURCE_RETRY_BASE_SECONDS", "2")))
    except ValueError:
        return 2.0


def _is_transient(result: scout.FetchResult) -> bool:
    if result.ok:
        return False
    if result.status in TRANSIENT_HTTP_STATUSES:
        return True
    return result.status is None and str(result.code or "") in TRANSIENT_SOURCE_CODES


def install_refreshable_oidc_proxy_auth() -> None:
    # Preserve the existing legacy bearer path exactly. The Sept. 12 cold-start
    # incident occurred on the GitHub-OIDC path, so resilience belongs there.
    if os.environ.get("WOW_ODDS_PROXY_ACTION_KEY"):
        return

    original = scout.proxy_get

    def _proxy_get(path: str, params: dict[str, Any] | None = None) -> scout.FetchResult:
        attempts = _retry_attempts()
        base_seconds = _retry_base_seconds()
        last_result: scout.FetchResult | None = None

        for attempt in range(1, attempts + 1):
            try:
                os.environ["WOW_GITHUB_OIDC_TOKEN"] = mint_github_actions_oidc()
            except GitHubOIDCMintError as exc:
                # Authentication/governance failures remain fail-closed and
                # are never relabeled as a transient source outage.
                return scout.FetchResult(False, code=str(exc))

            last_result = original(path, params)
            if last_result.ok or not _is_transient(last_result) or attempt == attempts:
                return last_result

            if base_seconds:
                time.sleep(base_seconds * attempt)

        return last_result or scout.FetchResult(False, code="SCOUT_SOURCE_RETRY_EXHAUSTED")

    scout.proxy_get = _proxy_get


def main() -> int:
    install_refreshable_oidc_proxy_auth()
    return scout.main()


if __name__ == "__main__":
    sys.exit(main())
