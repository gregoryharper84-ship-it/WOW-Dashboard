import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HARDENING_MIGRATION = ROOT / "migrations" / "20260920_v17_core_intelligence_access_hardening.sql"
CORE_PATHS = {
    "/v17/intelligence/summary",
    "/v17/intelligence/cycle",
    "/v17/intelligence/event-capture",
    "/v17/intelligence/full-cycle",
    "/v17/intelligence/compounding-summary",
    "/v17/intelligence/challenger-lab/shadow-evaluate",
}


def _run_import(*, core_active: bool) -> dict:
    code = r'''
import json
import api_ncaaf_acceptance as api
paths = {getattr(r, "path", "") for r in api.app.router.routes}
core_paths = {
    "/v17/intelligence/summary",
    "/v17/intelligence/cycle",
    "/v17/intelligence/event-capture",
    "/v17/intelligence/full-cycle",
    "/v17/intelligence/compounding-summary",
    "/v17/intelligence/challenger-lab/shadow-evaluate",
}
auth = {}
for route in api.app.router.routes:
    path = getattr(route, "path", "")
    if path not in core_paths or not hasattr(route, "dependant"):
        continue
    auth[path] = [
        getattr(getattr(dep, "call", None), "__name__", "")
        for dep in route.dependant.dependencies
    ]
print(json.dumps({
    "v17_active": api.V17_ACTIVE,
    "core_active": api.V17_CORE_INTELLIGENCE_ACTIVE,
    "mounted": sorted(core_paths.intersection(paths)),
    "auth": auth,
    "can_execute": False,
}))
'''
    env = dict(os.environ)
    env["WOW_V17_ACTIVE"] = "1"
    env["WOW_V17_CORE_INTELLIGENCE_ACTIVE"] = "1" if core_active else "0"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_core_intelligence_routes_mount_only_under_separate_production_kill_switch():
    disabled = _run_import(core_active=False)
    assert disabled["v17_active"] is True
    assert disabled["core_active"] is False
    assert disabled["mounted"] == []

    enabled = _run_import(core_active=True)
    assert enabled["v17_active"] is True
    assert enabled["core_active"] is True
    assert set(enabled["mounted"]) == CORE_PATHS
    assert enabled["can_execute"] is False


def test_every_production_core_intelligence_route_requires_action_api_key():
    enabled = _run_import(core_active=True)
    assert set(enabled["auth"]) == CORE_PATHS
    for path, dependency_names in enabled["auth"].items():
        assert "_require_action_api_key" in dependency_names, path


def test_access_hardening_migration_is_defense_in_depth_append_only():
    sql = HARDENING_MIGRATION.read_text().lower()
    expected_tables = {
        "wow_intelligence_observations",
        "wow_intelligence_hypotheses",
        "wow_intelligence_challenger_evaluations",
        "wow_intelligence_market_observations",
        "wow_intelligence_signal_observations",
        "wow_intelligence_signal_scorecards",
        "wow_intelligence_market_scorecards",
        "wow_intelligence_specialist_scorecards",
        "wow_intelligence_challenger_proposals",
        "wow_intelligence_promotion_reviews",
        "wow_intelligence_shadow_rows",
    }
    assert expected_tables.issubset(set(sql.split("'")))
    assert "enable row level security" in sql
    assert "revoke all on table public.%i from service_role" in sql
    assert "grant select, insert on table public.%i to service_role" in sql
    assert "revoke all on table public.%i from public, anon, authenticated" in sql
    assert "before truncate" in sql
    assert "wow_core_intelligence_block_mutation" in sql
