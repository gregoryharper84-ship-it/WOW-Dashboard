"""The existing worker may invoke five AMRT specialists only with live source fences."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from v17.amrt_protected_worker_adoption import (
    _canonical_heartbeat, admitted_context, run_worker_handoffs,
)
from v17.amrt_specialist_agents import AGENT_TO_EXISTING_ROLE

WORKER = "github-actions:12345:1"
ISSUE = "1950"
GROUP = "P0_NBA_RESTORE"
OWNER = "gregoryharper84-ship-it"
URL = "https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1950"
NOW = datetime(2026, 10, 26, 20, tzinfo=timezone.utc)
EXPIRES = (NOW + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


def manifest():
    return {"restoration": [{
        "issue_number": int(ISSUE),
        "amrt_owned": True, "can_execute": False, "severity": "P0",
        "execution_lane": "RAPID", "change_class": "B",
        "lease_group": GROUP, "amrt_accountable_owner": OWNER,
    }]}


def issue():
    return {
        "number": int(ISSUE), "html_url": URL, "state": "open",
        "assignees": [{"login": OWNER}, {"login": "Copilot"}],
    }


def lease():
    return {
        "work_item_id": ISSUE, "worker_id": WORKER,
        "lease_epoch": 2, "expires_at": EXPIRES,
        "lease_group": GROUP, "worker_mode": "RUNNING",
        "can_execute": False, "terminal_authority": "V17_TERMINAL_REDUCER",
    }


def heartbeat():
    return {
        "lease_epoch": "2", "worker_id": WORKER,
        "lease_expires_at": EXPIRES, "lease_group": GROUP,
        "worker_mode": "RUNNING", "current_restoration_target": ISSUE,
        "can_execute": "false", "terminal_authority": "V17_TERMINAL_REDUCER",
    }


def comments_body():
    return ("\n".join([
        "<!-- WOW_DUAL_STREAM_HEARTBEAT:" + GROUP + " -->",
        *(f"- {key}: {value}" for key, value in heartbeat().items()),
    ]))


def fake_read(path):
    if path.endswith("/issues/" + ISSUE):
        return issue()
    if "issues/1138/comments?" in path:
        return [{"body": comments_body()}]
    raise AssertionError("unexpected source query")


def test_canonical_assignee_and_different_worker_are_fenced_separately():
    found, claim = admitted_context(
        manifest=manifest(), issue_number=ISSUE, issue=issue(),
        heartbeat=heartbeat(), lease_data=lease(), worker_id=WORKER,
        lease_group=GROUP,
    )
    assert found["owner_id"] == OWNER and found["lease_owner"] == WORKER
    assert OWNER != WORKER
    assert claim.lease_epoch == 2


def test_all_five_real_specialists_route_via_existing_ledger(monkeypatch):
    records = []

    def writer(**kwargs):
        records.append(kwargs)
        assert kwargs["producer_stages"] == {}
        assert kwargs["incident"]["owner_id"] == OWNER
        assert kwargs["verified_worker_id"] == WORKER
        return {"receipt_id": "fixture" + str(len(records)),
                "disposition": "BLOCKED_WITH_EXACT_REASON"}

    monkeypatch.setattr(
        "v17.amrt_protected_worker_adoption.persist_authenticated_specialist_handoff",
        writer,
    )
    rows = run_worker_handoffs(
        read=fake_read, manifest=manifest(), lease_data=lease(),
        issue_number=ISSUE, lease_group=GROUP, worker_id=WORKER,
        head_sha="c" * 40, supabase_url="https://example.invalid",
        service_credential="fake-only", now=NOW,
    )
    assert [r["specialist"] for r in rows] == list(AGENT_TO_EXISTING_ROLE)
    assert len({r["receipt_id"] for r in rows}) == 5


@pytest.mark.parametrize("variant,code", [
    ("closed", "AMRT_CURRENT_ISSUE_INVALID"),
    ("wrong_issue", "AMRT_CURRENT_ISSUE_INVALID"),
    ("unassigned", "AMRT_ACCOUNTABLE_ISSUE_OWNER_UNVERIFIED"),
    ("wrong_lease_worker", "AMRT_ACTIVE_LEASE_HEARTBEAT_MISMATCH"),
    ("wrong_epoch", "AMRT_ACTIVE_LEASE_HEARTBEAT_MISMATCH"),
    ("execution_enabled", "AMRT_INCIDENT_NOT_ADMITTED"),
    ("different_group", "AMRT_INCIDENT_NOT_ADMITTED"),
])
def test_unverified_context_never_admits(variant, code):
    v_issue, v_manifest, v_lease = issue(), manifest(), lease()
    if variant == "closed":
        v_issue["state"] = "closed"
    if variant == "wrong_issue":
        v_issue["number"] = 1988
    if variant == "unassigned":
        v_issue["assignees"] = [{"login": "Copilot"}]
    if variant == "wrong_lease_worker":
        v_lease["worker_id"] = "github-actions:999:1"
    if variant == "wrong_epoch":
        v_lease["lease_epoch"] = 3
    if variant == "execution_enabled":
        v_manifest["restoration"][0]["can_execute"] = True
    if variant == "different_group":
        v_manifest["restoration"][0]["lease_group"] = "P0_OTHER"
    with pytest.raises(ValueError, match=code):
        admitted_context(
            manifest=v_manifest, issue_number=ISSUE, issue=v_issue,
            heartbeat=heartbeat(), lease_data=v_lease,
            worker_id=WORKER, lease_group=GROUP,
        )


def test_missing_canonical_credential_blocks_before_any_write(monkeypatch):
    written = []
    monkeypatch.setattr(
        "v17.amrt_protected_worker_adoption.persist_authenticated_specialist_handoff",
        lambda **kwargs: written.append(kwargs),
    )
    with pytest.raises(ValueError, match="AMRT_CANONICAL_LEDGER_CREDENTIAL_UNAVAILABLE"):
        run_worker_handoffs(
            read=fake_read, manifest=manifest(), lease_data=lease(),
            issue_number=ISSUE, lease_group=GROUP, worker_id=WORKER,
            head_sha="c" * 40, supabase_url="",
            service_credential="", now=NOW,
        )
    assert written == []


def test_duplicate_active_heartbeat_is_an_error():
    def duplicate(path):
        return [{"body": comments_body()}, {"body": comments_body()}]

    with pytest.raises(ValueError, match="AMRT_HEARTBEAT_NOT_UNIQUE_OR_MISSING"):
        _canonical_heartbeat(duplicate, lease_group=GROUP)


def test_lease_change_between_specialists_halts_the_sequence(monkeypatch):
    called = []
    page_calls = 0

    def read(path):
        nonlocal page_calls
        if "/comments?" not in path:
            return issue()
        page_calls += 1
        if page_calls <= 2:
            return [{"body": comments_body()}]
        bad = comments_body().replace("- lease_epoch: 2", "- lease_epoch: 4")
        return [{"body": bad}]

    def writer(**kwargs):
        called.append(kwargs["specialist"])
        return {"receipt_id": "saved-once", "disposition": "IN_PROGRESS"}

    monkeypatch.setattr(
        "v17.amrt_protected_worker_adoption.persist_authenticated_specialist_handoff",
        writer,
    )
    with pytest.raises(ValueError, match="AMRT_LEASE_CHANGED_DURING_HANDOFF"):
        run_worker_handoffs(
            read=read, manifest=manifest(), lease_data=lease(),
            issue_number=ISSUE, lease_group=GROUP, worker_id=WORKER,
            head_sha="c" * 40, supabase_url="https://example.invalid",
            service_credential="fake-only", now=NOW,
        )
    assert len(called) == 1
