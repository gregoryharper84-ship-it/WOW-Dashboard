"""Source-only AMRT team registry invariants; no network and no production writes."""
import json
from pathlib import Path

from v17.amrt_closure_gate import TEAM, MAX_PROVIDER_ATTEMPTS, TERMINAL_AUTHORITY
from v17 import amrt_operational_lanes as handlers

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "artifacts/wow-engine/v17/amrt_team_registry.json"


def load_registry():
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def test_exactly_seven_conductor_owned_roles():
    spec = load_registry()
    assert spec["team_id"] == "WOW_AUTONOMOUS_MODEL_RECOVERY_TEAM"
    assert spec["status"] == "SOURCE_CANDIDATE_NOT_DEPLOYED"
    assert [role["role_id"] for role in spec["specialists"]] == list(TEAM)
    assert len(set(TEAM)) == 7
    assert spec["parent_control_plane"] == "WOW_ECOSYSTEM_CONDUCTOR"
    assert spec["active_worker"] == "EXISTING_AUTHENTICATED_ENGINEERING_WORKER_ONLY"
    assert spec["max_provider_attempts"] == MAX_PROVIDER_ATTEMPTS
    assert 0 < spec["max_parallel_workers"] <= 2


def test_skill_definitions_bind_existing_handlers():
    for role in load_registry()["specialists"]:
        skill = ROOT / role["skill_path"]
        assert skill.is_file(), role["skill_path"]
        content = skill.read_text(encoding="utf-8")
        assert content.splitlines()[0] == "---"
        assert content.splitlines()[1].startswith("name: wow-amrt-")
        assert role["role_id"] in content
        assert "v17.amrt_operational_lanes." + role["handler"] in content
        assert callable(getattr(handlers, role["handler"]))


def test_safety_and_independence():
    spec = load_registry()
    assert spec["terminal_authority"] == TERMINAL_AUTHORITY
    assert spec["can_execute"] is False
    assert spec["DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"] is True
    assert spec["class_c_policy"] == "CERTIFICATION_REQUIRED_NO_AMRT_SELF_APPROVAL"
    assert all(not role["probability_authority"] and not role["approval_authority"]
               for role in spec["specialists"])
    assert len(spec["production_closure_requirements"]) == 9
    assert spec["shared_lease_authority"] == "EXISTING_CONDUCTOR_ATOMIC_INCIDENT_LEASE"
    assert spec["receipts_table"] == "wow_engineering_attempt_receipts"
    assert spec["ownership"].startswith("ONE_ACCOUNTABLE")
    assert set(spec["priority_policy"]) == {"P0", "P1", "P2", "P3"}
    assert spec["specialists"][-2]["role_id"] == "INDEPENDENT_QA"
    assert spec["specialists"][-1]["role_id"] == "RELEASE_VERIFICATION"
    for role in spec["specialists"]:
        skill = (ROOT / role["skill_path"]).read_text(encoding="utf-8")
        assert "can_execute=false" in skill
        assert "V17_TERMINAL_REDUCER" in skill
        assert "Class C" in skill
        assert "SOURCE_CANDIDATE_NOT_DEPLOYED" in skill
