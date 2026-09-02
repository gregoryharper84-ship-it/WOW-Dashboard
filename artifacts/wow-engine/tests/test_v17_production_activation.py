import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "v17" / "custom_engine_alignment_contract.json"


def test_v17_contract_is_backend_production_active_and_non_executable():
    payload = json.loads(CONTRACT.read_text())
    assert payload["status"] == "V17_PRODUCTION_ACTIVE_BACKEND"
    assert payload["activation"]["v17_active"] is True
    assert payload["activation"]["v17_cutover_allowed"] is True
    assert payload["activation"]["can_execute"] is False
    assert payload["shared_core"]["single_global_terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert payload["backend_contract"]["legacy_replit_primary_routing_allowed"] is False


def test_production_entrypoint_mounts_v17_routes_only_when_flag_enabled():
    code = r'''
import json
import api_ncaaf_acceptance as api
paths = {getattr(r, "path", "") for r in api.app.router.routes}
print(json.dumps({
    "active": api.V17_ACTIVE,
    "team": "/score-team-event" in paths,
    "daily": "/v17/daily-snapshot-run" in paths,
    "host": "/v17/host-contract" in paths,
    "record": "/record-recommendations" in paths,
    "can_execute": False,
}))
'''
    env = dict(os.environ)
    env["WOW_V17_ACTIVE"] = "1"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "active": True,
        "team": True,
        "daily": True,
        "host": True,
        "record": True,
        "can_execute": False,
    }


def test_v17_disabled_preserves_compatibility_surface_without_new_host_route():
    code = r'''
import json
import api_ncaaf_acceptance as api
paths = {getattr(r, "path", "") for r in api.app.router.routes}
print(json.dumps({"active": api.V17_ACTIVE, "team": "/score-team-event" in paths, "daily": "/v17/daily-snapshot-run" in paths, "host": "/v17/host-contract" in paths}))
'''
    env = dict(os.environ)
    env["WOW_V17_ACTIVE"] = "0"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {"active": False, "team": False, "daily": False, "host": False}


def test_legacy_prospective_pythonpath_cannot_replace_production_governance():
    """The retired prospective shim must never win module resolution again."""
    code = r'''
import json
import api_ncaaf_acceptance as api

governance = next(route for route in api.app.router.routes if getattr(route, "path", None) == "/governance")
print(json.dumps({
    "entrypoint": api.__file__,
    "governance_module": governance.endpoint.__module__,
}))
'''
    env = dict(os.environ)
    env["PYTHONPATH"] = "prospective_entrypoint:."
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["entrypoint"].endswith("/api_ncaaf_acceptance.py")
    assert payload["governance_module"] != "mlb_event_prospective_runtime"
