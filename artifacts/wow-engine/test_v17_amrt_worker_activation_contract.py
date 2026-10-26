"""Existing trusted worker AMRT call sites have one source/lease authority."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / "artifacts/wow-engine"
WORKFLOWS = (
    ROOT / ".github/workflows/wow-v17-chatgpt-engineering-worker.yml",
    ROOT / ".github/workflows/wow-v17-claude-engineering-worker.yml",
)
MANIFEST = ENGINE / "v17/engineering_dispatch_manifest.json"


@pytest.mark.parametrize("path", WORKFLOWS)
def test_no_new_writer_only_protected_lease_fenced_amrt_worker_step(path):
    source = path.read_text()
    workflow = yaml.safe_load(source)
    steps = workflow["jobs"]["multi-agent-engineering"]["steps"]
    by_name = {step.get("name"): (i, step) for i, step in enumerate(steps)}
    assert source.count("- name: Record lease-fenced AMRT specialist decisions") == 1
    init_idx, _ = by_name["Initialize persistent-worker safety state"]
    gate_idx, _ = by_name["Enforce mutation safety gate"]
    checkout_idx, checkout = by_name["Checkout protected AMRT integration source"]
    handoff_idx, handoff = by_name["Record lease-fenced AMRT specialist decisions"]
    impl_idx, _ = by_name["Implementation agent"]
    assert init_idx < gate_idx < checkout_idx < handoff_idx < impl_idx
    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout["with"]["ref"] == "$" + "{{ github.sha }}"
    assert checkout["with"]["persist-credentials"] is False
    assert "github.ref == 'refs/heads/main'" in checkout["if"]
    assert "steps.mutation_gate.outputs.allowed == 'true'" in handoff["if"]
    assert "github.ref == 'refs/heads/main'" in handoff["if"]
    # An admitted AMRT persistence/readback failure must fail this existing job.\n    assert handoff.get("continue-on-error", False) is False\n    assert "set -euo pipefail" in handoff["run"]
    assert handoff["env"]["WOW_CAN_EXECUTE"] == "false"
    assert handoff["env"]["WOW_DRY_RUN_ONLY"] == "true"
    assert "steps.eng_token.outputs.token" in handoff["env"]["GH_TOKEN"]
    assert "steps.safety.outputs.worker_id" in handoff["env"]["AMRT_WORKER_ID"]
    assert "amrt_protected_worker_adoption.py" in handoff["run"]
    assert "git -C .wow-v17-protected-amrt" in handoff["run"]
    assert "PYTHONPATH=" in handoff["run"]


def test_existing_open_p0_sport_recovery_lanes_admitted_without_second_queue():
    manifest = json.loads(MANIFEST.read_text())
    restoration = manifest["restoration"]
    numbers = [row["issue_number"] for row in restoration]
    assert len(numbers) == len(set(numbers))
    assert manifest["can_execute"] is False
    assert manifest["terminal_authority"] == "V17_TERMINAL_REDUCER"
    for issue, group in (
        (1950, "P0_NBA_RESTORE"),
        (1951, "P0_NCAAF_RESTORE"),
        (1952, "P0_NHL_RESTORE"),
    ):
        row, = [r for r in restoration if r["issue_number"] == issue]
        assert row["amrt_owned"] is True
        assert row["can_execute"] is False
        assert row["severity"] == "P0"
        assert row["execution_lane"] == "RAPID"
        assert row["lease_group"] == group
        assert row["change_class"] == "B"
        assert row["amrt_accountable_owner"] == "gregoryharper84-ship-it"


def test_no_autonomous_model_publication_or_agent_approval():
    source = (ENGINE / "v17/amrt_protected_worker_adoption.py").read_text()
    assert "persist_authenticated_specialist_handoff" in source
    assert 'issue.get("state") == "open"' in source
    assert "AMRT_LEASE_CHANGED_DURING_HANDOFF" in source
    assert "AMRT_CANONICAL_LEDGER_CREDENTIAL_UNAVAILABLE" in source
    assert "AUTO_APPROVE" not in source
    assert "place_wager" not in source
    assert "cancel_wager" not in source
    assert "can_execute" in source
