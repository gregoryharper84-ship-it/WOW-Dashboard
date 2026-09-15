from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


_TIME_SENSITIVE_DISCOVERY_MODULES = {
    "test_v17_cross_sport_winner_scan",
    "test_v17_certification_and_discovery_map",
}


@pytest.fixture(autouse=True)
def _keep_synthetic_discovery_slate_future(request, monkeypatch):
    """Prevent synthetic pregame fixtures from expiring as wall-clock time advances.

    These suites validate discovery/routing taxonomy, not the historical date
    2026-09-15. Explicit started/final fixtures still provide their own status or
    past timestamp and therefore continue to exercise the real pregame guard.
    """
    module = getattr(request, "module", None)
    module_name = str(getattr(module, "__name__", "")).split(".")[-1]
    if module_name not in _TIME_SENSITIVE_DISCOVERY_MODULES or not hasattr(module, "SLATE_DATE"):
        return
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()
    monkeypatch.setattr(module, "SLATE_DATE", tomorrow)
