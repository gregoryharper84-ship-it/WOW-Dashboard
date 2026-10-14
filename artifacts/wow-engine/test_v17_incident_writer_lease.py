"""#1739 lease client negative-path and fencing tests."""
import json
from pathlib import Path
from unittest.mock import patch
import pytest

from v17 import incident_writer_lease as lease


def test_missing_credentials_fails_closed(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    with pytest.raises(lease.LeaseError, match="LEASE_STORE_UNAVAILABLE"):
        lease.rpc("claim", {})


def test_claim_rejects_bad_provenance(tmp_path):
    with pytest.raises(lease.LeaseError, match="BAD_PROVENANCE"):
        lease.claim([823], "unverified", "GLOBAL", tmp_path / "r.json")


def test_claim_busy_does_not_persist_receipt(tmp_path):
    path = tmp_path / "r.json"
    with patch.object(lease, "rpc", return_value={"ok": False, "reason": "LEASE_BUSY"}):
        with pytest.raises(lease.LeaseError, match="LEASE_BUSY:823"):
            lease.claim([823], "github-actions:123:1:anthropic", "GLOBAL", path)
    assert not path.exists()


def test_claim_and_fenced_gate_and_release(tmp_path):
    path = tmp_path / "r.json"
    owner = "github-actions:123:1:anthropic"
    calls = []
    def fake_rpc(name, payload):
        calls.append((name, payload))
        if name == "claim":
            return {"ok": True, "owner_id": owner, "incident_id": 823, "epoch": 4}
        return True
    with patch.object(lease, "rpc", side_effect=fake_rpc):
        lease.claim([823], owner, "GLOBAL", path)
        assert json.loads(path.read_text())[0]["epoch"] == 4
        lease.gate(path)
        lease.release(path)
    assert [x[0] for x in calls] == ["claim", "renew", "release"]
    assert not path.exists()


def test_stale_lease_cannot_pass_pre_mutation_gate(tmp_path):
    path = tmp_path / "r.json"
    path.write_text(json.dumps([{"incident_id": 823, "owner_id": "github-actions:123:1:anthropic", "epoch": 1}]))
    with patch.object(lease, "rpc", return_value=False):
        with pytest.raises(lease.LeaseError, match="LEASE_LOST:823"):
            lease.gate(path)


def test_failed_second_claim_releases_first(tmp_path):
    path = tmp_path / "r.json"
    owner = "github-actions:123:1:anthropic"
    actions = []
    def fake_rpc(name, payload):
        actions.append(name)
        if name == "claim" and payload["p_incident_id"] == 41:
            return {"ok": False}
        if name == "claim":
            return {"ok": True, "owner_id": owner, "incident_id": 40, "epoch": 1}
        return True
    with patch.object(lease, "rpc", side_effect=fake_rpc):
        with pytest.raises(lease.LeaseError, match="LEASE_BUSY"):
            lease.claim([40, 41], owner, "GLOBAL", path)
    assert actions == ["claim", "claim", "release"]
    assert not path.exists()


def test_heartbeat_renews_then_stops_on_lost_lease(tmp_path):
    path = tmp_path / "lease.json"
    path.write_text(json.dumps([{"incident_id": 823, "owner_id": "github-actions:123:1:anthropic", "epoch": 3}]))
    with patch.object(lease, "rpc", side_effect=[True, False]) as rpc_mock:
        with patch.object(lease.time, "sleep", return_value=None) as sleeper:
            with pytest.raises(lease.LeaseError, match="LEASE_LOST:823"):
                lease.heartbeat(path, interval=60)
    assert rpc_mock.call_count == 2
    assert sleeper.call_count == 1


def test_heartbeat_rejects_unsafe_interval(tmp_path):
    with pytest.raises(lease.LeaseError, match="INVALID_RENEW_INTERVAL"):
        lease.heartbeat(tmp_path / "missing", interval=30)
