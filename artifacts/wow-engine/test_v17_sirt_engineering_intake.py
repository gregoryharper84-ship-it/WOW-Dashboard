"""Contract regressions for the governed SIRT -> Engineering bridge."""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "sirt_bridge", ROOT / "artifacts/wow-engine/v17/sirt_engineering_intake.py")
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)
NOW = datetime(2026, 10, 9, 22, 0, tzinfo=timezone.utc)
FP = "a" * 64


def finding(**updates):
    record = dict(fingerprint=FP, severity="P0", status="OPEN",
                  finding_type="STALE_WORK", component="wow-llp-runtime",
                  evidence={"head_sha": "b" * 40, "source_ref": "run-12"},
                  first_detected_at=(NOW - timedelta(minutes=20)).isoformat(),
                  can_execute=False, terminal_authority="V17_TERMINAL_REDUCER")
    record.update(updates)
    return record


class FakeTransport:
    def __init__(self, origin=None):
        self.origin = origin
        self.issues = {1021: {"number": 1021, "state": "open", "body": "control plane"}}
        if origin:
            self.issues[origin] = {"number": origin, "state": "open", "body": "existing issue"}
        self.comments_by_issue = {}
        self.created = 0
        self.posts = []

    def issue_from_work_item(self, record):
        return self.origin

    def find_generated_issue(self, fingerprint):
        found = [n for n, row in self.issues.items()
                 if bridge.INTAKE_PREFIX + fingerprint in row["body"]]
        return found[0] if found else None

    def issue(self, number):
        return self.issues[number]

    def comments(self, number):
        return self.comments_by_issue.get(number, [])[:]

    def create_issue(self, item):
        self.created += 1
        n = 2000 + self.created
        self.issues[n] = {"number": n, "state": "open",
                          "body": bridge.render_intake(item)}
        return n

    def post_comment(self, number, message):
        self.posts.append((number, message))
        self.comments_by_issue.setdefault(number, []).append({
            "body": message, "created_at": NOW.isoformat(),
            "user": {"login": "github-actions[bot]"}})

    def authorized_ack(self, comment, fingerprint, first=None):
        body = comment.get("body", "")
        return (comment.get("user", {}).get("login") == "engineer"
                and f"Engineering-ACK: {fingerprint}" in body
                and "Owner:" in body and "Next action:" in body)


def test_existing_issue_receives_idempotent_handoff_and_p0_escalation():
    f = FakeTransport(1388)
    result = bridge.act(f, finding(), NOW)
    assert result["issue"] == 1388
    assert result["status"] == "P0_ESCALATED_UNACKNOWLEDGED_ACK_PENDING"
    assert len(f.posts) == 3
    assert {i for i, _ in f.posts} == {1388, 1021}
    bridge.act(f, finding(), NOW + timedelta(minutes=1))
    assert len(f.posts) == 3


def test_new_issue_delivery_is_deduplicated_and_not_ack():
    f = FakeTransport()
    first = bridge.act(f, finding(severity="P1"), NOW)
    second = bridge.act(f, finding(severity="P1"), NOW)
    assert first["issue"] == second["issue"]
    assert f.created == 1
    assert first["status"].endswith("ACK_PENDING")


def test_real_engineering_ack_stops_escalation():
    f = FakeTransport()
    first = bridge.act(f, finding(), NOW)
    number = first["issue"]
    f.comments_by_issue[number].append({
        "body": f"Engineering-ACK: {FP}\nOwner: engineer\nNext action: reproduce",
        "user": {"login": "engineer"}, "created_at": NOW.isoformat()})
    result = bridge.act(f, finding(), NOW + timedelta(minutes=1))
    assert result["status"] == "ENGINEERING_ACKNOWLEDGED"
    assert len(f.posts) == 2


def test_forged_bot_ack_is_not_engineering():
    f = FakeTransport(1388)
    f.comments_by_issue[1388] = [{
        "body": f"Engineering-ACK: {FP}\nOwner: bot\nNext action: close",
        "user": {"login": "github-actions[bot]"}, "created_at": NOW.isoformat()}]
    assert bridge.act(f, finding(), NOW)["status"] != "ENGINEERING_ACKNOWLEDGED"


@pytest.mark.parametrize("bad", [
    {"can_execute": True}, {"terminal_authority": "OTHER"},
    {"status": "RESOLVED"}, {"fingerprint": "bad"},
    {"severity": "UNDEFINED"}, {"evidence": []},
    {"evidence": {"head_sha": "untrusted"}}, {"first_detected_at": "n/a"},
    {"finding_type": "RANDOM"},
])
def test_invalid_findings_fail_closed(bad):
    with pytest.raises(ValueError):
        bridge.classify(finding(**bad))


def test_closed_issue_cannot_silently_absorb_open_finding():
    f = FakeTransport(1388)
    f.issues[1388]["state"] = "closed"
    with pytest.raises(RuntimeError, match="CLOSED_UNVERIFIED"):
        bridge.act(f, finding(), NOW)


def test_authorized_ack_requires_non_bot_write_permission():
    class Permission:
        def gh(self, path):
            assert path == "collaborators/engineer/permission"
            return {"permission": "read"}
    comment = {"body": f"Engineering-ACK: {FP}\nOwner: engineer\nNext action: triage",
               "user": {"login": "engineer"}}
    assert not bridge.Transport.authorized_ack(Permission(), comment, FP)
    comment["user"]["login"] = "github-actions[bot]"
    assert not bridge.Transport.authorized_ack(Permission(), comment, FP)


def test_workflow_separates_intake_writes_from_sirt_and_release_authority():
    trusted = (ROOT / ".github/workflows/wow-sirt-engineering-finding-bridge.yml").read_text()
    sirt = (ROOT / ".github/workflows/wow-sirt-independent-reliability-sentinel.yml").read_text()
    assert "  issues: write" in trusted and "ref: main" in trusted
    assert "concurrency:" in trusted and "workflow_run:" in trusted
    assert "  issues: write" not in sirt
    for prohibited in ("  actions: write", "  contents: write",
                       "  pull-requests: write", "  deployments: write",
                       "  id-token: write"):
        assert prohibited not in trusted
