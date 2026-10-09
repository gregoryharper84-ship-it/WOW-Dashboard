"""Regression checks for conflict-aware rapid engineering dispatch."""
from __future__ import annotations

import pytest

from v17.engineering_resident_supervisor import active_engineering_workflow, dispatch_once


def issue(number, lease, keys):
    return {
        "issue_number": number, "severity": "P0", "priority_rank": number,
        "execution_lane": "RAPID", "rapid_stream": "LLP_RESTORE",
        "lease_group": lease, "conflict_keys": keys,
    }


A = issue(101, "P0_LLP", ["LLP_RUNTIME"])
B = issue(102, "P0_PROPS", ["PROP_RUNTIME"])
C = issue(103, "P0_PROPS", ["LLP_RUNTIME"])
MANIFEST = {
    "can_execute": False, "terminal_authority": "V17_TERMINAL_REDUCER",
    "restoration": [A, B, C], "acceleration": [],
}


class Client:
    def __init__(self, title):
        self.title = title
        self.sent = []

    def get(self, suffix):
        if suffix.startswith("actions/runs?"):
            if "status=in_progress" in suffix:
                return {"total_count": 1, "workflow_runs": [{
                    "name": "wow-v17-chatgpt-engineering-worker",
                    "display_title": self.title,
                }]}
            return {"total_count": 0, "workflow_runs": []}
        if suffix.startswith("issues/"):
            return {"state": "open"}
        if suffix.startswith("pulls?"):
            return []
        raise AssertionError(suffix)

    def dispatch(self, task):
        self.sent.append(task["issue_number"])


class Redis:
    def __init__(self):
        self.values = {}
    def set(self, key, value, *, nx=False, ex=0):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True
    def exists(self, key):
        return key in self.values
    def get(self, key):
        return self.values.get(key)
    def incr(self, key):
        self.values[key] = int(self.values.get(key) or 0) + 1
    def expire(self, key, seconds):
        return True


def test_active_disjoint_domain_is_safe():
    client = Client("wow-v17-chatgpt-engineering-worker lease=P0_LLP incident=101")
    assert active_engineering_workflow(client)
    assert not active_engineering_workflow(client, B, MANIFEST)
    assert active_engineering_workflow(client, C, MANIFEST)


@pytest.mark.parametrize("title", [
    "wow-v17-chatgpt-engineering-worker",
    "wow-v17-chatgpt-engineering-worker lease=P0_LLP incident=AUTO",
    "wow-v17-chatgpt-engineering-worker lease=P0_WRONG incident=101",
    "wow-v17-chatgpt-engineering-worker lease=P0_PROPS incident=999",
])
def test_unknown_or_untrusted_identity_blocks(title):
    assert active_engineering_workflow(Client(title), B, MANIFEST)


def test_dispatch_selects_disjoint_p0_while_first_is_active():
    client = Client("wow-v17-chatgpt-engineering-worker lease=P0_LLP incident=101")
    assert dispatch_once(client, Redis(), MANIFEST) == "DISPATCHED_TO_PROTECTED_ENGINEERING_WORKFLOW"
    assert client.sent == [102]


def test_same_incident_or_conflicting_domain_not_dispatched():
    manifest = {**MANIFEST, "restoration": [A, C]}
    client = Client("wow-v17-chatgpt-engineering-worker lease=P0_LLP incident=101")
    assert dispatch_once(client, Redis(), manifest) == "EXISTING_ENGINEERING_WORKFLOW_ACTIVE"
    assert not client.sent
