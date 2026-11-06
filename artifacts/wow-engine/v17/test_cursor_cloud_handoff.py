"""Deterministic no-network tests for the Cursor engineering handoff."""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from urllib import error
from unittest.mock import patch

import yaml

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent
sys.path.insert(0, str(HERE))
import engineering_remote_fallback as remote

spec = importlib.util.spec_from_file_location("cursor_cloud_handoff", HERE / "cursor_cloud_handoff.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
SHA = "a" * 40
REPO = "gregoryharper84-ship-it/WOW-Dashboard"
INCIDENT = 2008
ACTOR = "gregoryharper84-ship-it"
MAIN_SHA = "c" * 40


def pr(sha=SHA, repo=REPO, state="open", base="main", body="Incident: #2008"):
    return {"number": 1948, "state": state, "body": body,
            "head": {"sha": sha, "ref": "fix/daily", "repo": {"full_name": repo}},
            "base": {"ref": base}}


def issue(number=INCIDENT, state="open", repo=REPO):
    return {"number": number, "state": state,
            "html_url": f"https://github.com/{repo}/issues/{number}"}


def dispatch(*args, **kwargs):
    kwargs.setdefault("incident_number", INCIDENT)
    kwargs.setdefault("actor", ACTOR)
    return mod.dispatch(*args, **kwargs)


class FakeApi:
    def __init__(self, *, global_agents=None, pr_agents=None, global_next=None,
                 pr_next=None, issue_data=None, stale_pr_read=None,
                 malformed_pr_read=None, post_result=None, post_error=None,
                 fail_ledger_write=False):
        self.calls = []
        self.comments = []
        self.cursor_posts = []
        self.global_agents = global_agents or []
        self.pr_agents = pr_agents or []
        self.global_next = global_next
        self.pr_next = pr_next
        self.issue_data = issue_data or issue()
        self.stale_pr_read = stale_pr_read
        self.malformed_pr_read = malformed_pr_read
        self.pr_reads = 0
        self.post_result = post_result
        self.post_error = post_error
        self.fail_ledger_write = fail_ledger_write

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if "/pulls/" in url:
            self.pr_reads += 1
            if self.pr_reads == self.malformed_pr_read:
                return []
            return pr(sha="b" * 40) if self.pr_reads == self.stale_pr_read else pr()
        if "/issues/comments/" in url and kwargs.get("method") == "PATCH":
            self.comments[0]["body"] = kwargs["body"]["body"]
            return self.comments[0]
        if "/issues/1138/comments" in url:
            if kwargs.get("body") is not None:
                if self.fail_ledger_write:
                    raise remote.ProviderUnavailable("HTTP_403")
                saved = {"id": 55, "body": kwargs["body"]["body"]}
                self.comments.append(saved)
                return saved
            return self.comments
        if f"/issues/{INCIDENT}" in url:
            return self.issue_data
        if "/commits/main" in url:
            return {"sha": MAIN_SHA}
        if "/v1/agents" in url:
            if kwargs.get("method") == "POST":
                self.cursor_posts.append(kwargs)
                if self.post_error is not None:
                    raise self.post_error
                if self.post_result is not None:
                    return self.post_result
                return {"agent": {"id": kwargs["body"]["agentId"]},
                        "run": {"id": "run-example", "status": "CREATING"}}
            if "prUrl=" in url:
                response = {"items": self.pr_agents}
                if self.pr_next:
                    response["nextCursor"] = self.pr_next
                return response
            response = {"items": self.global_agents}
            if self.global_next:
                response["nextCursor"] = self.global_next
            return response
        raise AssertionError(url)


class HandoffTests(unittest.TestCase):
    def test_stable_deterministic_agent_id(self):
        first = mod.agent_id(REPO, 1948, SHA)
        self.assertEqual(first, mod.agent_id(REPO, 1948, SHA))
        self.assertNotEqual(first, mod.agent_id(REPO, 1948, "b" * 40))
        self.assertTrue(first.startswith("bc-"))

    def test_payload_same_pr_no_auto_pr(self):
        data = mod.payload(REPO, 1948, SHA, "Fix existing tests")
        self.assertTrue(data["workOnCurrentBranch"])
        self.assertFalse(data["autoCreatePR"])
        self.assertEqual(data["repos"][0]["prUrl"], f"https://github.com/{REPO}/pull/1948")
        self.assertIn("can_execute=false", data["prompt"]["text"])

    def test_reject_invalid_task(self):
        for task in ("", " " * 5, "x" * 4001):
            with self.assertRaises(mod.HandoffBlocked):
                mod.payload(REPO, 1948, SHA, task)

    def test_reject_stale_fork_closed_wrong_base(self):
        for data, reason in [
            (pr(sha="b" * 40), "CURSOR_STALE_PR_HEAD"),
            (pr(repo="someone/other"), "CURSOR_FORK_HEAD_UNSUPPORTED"),
            (pr(state="closed"), "CURSOR_PR_NOT_OPEN_OR_MISMATCH"),
            (pr(base="feature"), "CURSOR_UNEXPECTED_BASE_BRANCH"),
        ]:
            with self.subTest(reason=reason):
                with self.assertRaises(mod.HandoffBlocked) as cm:
                    mod.validate_pr(data, repo=REPO, number=1948, sha=SHA,
                                   incident_number=INCIDENT)
                self.assertEqual(cm.exception.code, reason)

    def test_reject_pr_without_exact_incident_binding(self):
        with self.assertRaises(mod.HandoffBlocked) as cm:
            mod.validate_pr(pr(body="Incident: #2008\nIncident: #2009"),
                            repo=REPO, number=1948, sha=SHA,
                            incident_number=INCIDENT)
        self.assertEqual(cm.exception.code, "CURSOR_PR_INCIDENT_MISMATCH")

    def test_malformed_pr_shape_fails_with_typed_error(self):
        malformed = (
            {"number": 1948, "state": "open", "head": [], "base": {}},
            pr(body=[]),
        )
        for data in malformed:
            with self.subTest(data=data), self.assertRaises(mod.HandoffBlocked) as cm:
                mod.validate_pr(data, repo=REPO, number=1948, sha=SHA,
                                incident_number=INCIDENT)
            self.assertEqual(cm.exception.code, "CURSOR_GITHUB_PR_RESPONSE_INVALID")

    def test_missing_key_fails_before_network(self):
        with self.assertRaises(mod.HandoffBlocked) as cm:
            dispatch(REPO, 1948, SHA, "Fix it", "gh", "")
        self.assertEqual(cm.exception.code, "CURSOR_API_KEY_MISSING")

    def test_invalid_cli_numbers_write_typed_receipts(self):
        for pr_number, incident_number, expected in (
            ("not-a-number", "2008", "CURSOR_PR_NUMBER_INVALID"),
            ("1948", "not-a-number", "CURSOR_INCIDENT_ID_INVALID"),
        ):
            with self.subTest(expected=expected):
                with tempfile.TemporaryDirectory() as temp:
                    receipt_path = pathlib.Path(temp) / "receipt.json"
                    args = [
                        "cursor_cloud_handoff.py", "--repo", REPO,
                        "--pr-number", pr_number, "--incident-number", incident_number,
                        "--actor", ACTOR, "--expected-head", SHA, "--task", "Fix it",
                        "--receipt", str(receipt_path),
                    ]
                    with patch.object(sys, "argv", args), \
                            patch.dict(os.environ, {"GH_TOKEN": "gh", "CURSOR_API_KEY": "cursor"}), \
                            redirect_stdout(StringIO()):
                        self.assertEqual(mod.main(), 1)
                    receipt = json.loads(receipt_path.read_text())
                    self.assertEqual(receipt["code"], expected)
                    self.assertFalse(receipt["can_execute"])

    def test_invalid_owner_fails_before_network(self):
        calls = []
        with self.assertRaises(mod.HandoffBlocked) as cm:
            mod.dispatch(REPO, 1948, SHA, "Fix it", "gh", "cursor",
                         lambda *args, **kwargs: calls.append(args),
                         incident_number=INCIDENT, actor="untrusted-user")
        self.assertEqual(cm.exception.code, "CURSOR_OWNER_NOT_AUTHORIZED")
        self.assertEqual(calls, [])

    def test_invalid_or_missing_incident_fails_before_cursor_api(self):
        api = FakeApi(issue_data=issue(state="closed"))
        with self.assertRaises(mod.HandoffBlocked) as cm:
            dispatch(REPO, 1948, SHA, "Fix it", "gh", "cursor", api)
        self.assertEqual(cm.exception.code, "CURSOR_INCIDENT_NOT_OPEN_OR_MISMATCH")
        self.assertEqual(api.cursor_posts, [])
        self.assertEqual(len(api.calls), 2)

        with self.assertRaises(mod.HandoffBlocked) as cm:
            mod.dispatch(REPO, 1948, SHA, "Fix it", "gh", "cursor",
                         incident_number=None, actor=ACTOR)
        self.assertEqual(cm.exception.code, "CURSOR_INCIDENT_ID_INVALID")

    def test_active_agent_prevents_post(self):
        api = FakeApi(pr_agents=[{"id": "bc-prior", "status": "ACTIVE"}])
        with self.assertRaises(mod.HandoffBlocked) as cm:
            dispatch(REPO, 1948, SHA, "Fix it", "gh", "cursor", api)
        self.assertEqual(cm.exception.code, "CURSOR_ACTIVE_PR_AGENT_EXISTS")
        self.assertEqual(api.cursor_posts, [])
        self.assertEqual(len(api.calls), 5)

    def test_incomplete_inventory_fails_closed(self):
        api = FakeApi(pr_next="next")
        with self.assertRaises(mod.HandoffBlocked) as cm:
            dispatch(REPO, 1948, SHA, "Fix", "gh", "cursor", api)
        self.assertEqual(cm.exception.code, "CURSOR_AGENT_INVENTORY_UNVERIFIED")
        self.assertEqual(api.cursor_posts, [])

    def test_head_changes_between_checks_no_post(self):
        api = FakeApi(stale_pr_read=2)
        with self.assertRaises(mod.HandoffBlocked) as cm:
            dispatch(REPO, 1948, SHA, "Fix", "gh", "cursor", api)
        self.assertEqual(cm.exception.code, "CURSOR_STALE_PR_HEAD")
        self.assertEqual(api.cursor_posts, [])

    def test_malformed_pr_recheck_fails_typed_before_lease_or_create(self):
        api = FakeApi(malformed_pr_read=2)
        with self.assertRaises(mod.HandoffBlocked) as cm:
            dispatch(REPO, 1948, SHA, "Fix", "gh", "cursor", api)
        self.assertEqual(cm.exception.code, "CURSOR_GITHUB_PR_RESPONSE_INVALID")
        self.assertEqual(api.cursor_posts, [])
        self.assertEqual(api.comments, [])

    def test_shared_remote_or_local_writer_lease_blocks_billable_dispatch(self):
        remote_hold = remote.receipt(
            "REMOTE_LAUNCH_RESERVED", provider="copilot", incident_id="99",
            main_sha=MAIN_SHA, hold=True,
        )
        api = FakeApi()
        api.comments = [{"id": 10, "body": remote._body(remote_hold)}]
        with self.assertRaises(mod.HandoffBlocked) as cm:
            dispatch(REPO, 1948, SHA, "Fix", "gh", "cursor", api)
        self.assertEqual(cm.exception.code, "CURSOR_SHARED_WRITER_LEASE_ACTIVE")
        self.assertEqual(api.cursor_posts, [])

        api = FakeApi()
        api.comments = [{"body": "<!-- WOW_DUAL_STREAM_HEARTBEAT:GLOBAL -->\n"
                                "- worker_mode: RUNNING\n- lease_expires_at: "
                                "2999-01-01T00:00:00Z"}]
        with self.assertRaises(mod.HandoffBlocked) as cm:
            dispatch(REPO, 1948, SHA, "Fix", "gh", "cursor", api)
        self.assertEqual(cm.exception.code, "CURSOR_SHARED_WRITER_LEASE_ACTIVE")
        self.assertEqual(api.cursor_posts, [])

    def test_success_returns_queued_not_production_verified(self):
        api = FakeApi()
        result = dispatch(REPO, 1948, SHA, "Fix", "gh", "cursor", api)
        self.assertEqual(result["status"], "CURSOR_AGENT_ENQUEUED")
        self.assertFalse(result["production_verified"])
        self.assertEqual(result["incident_number"], INCIDENT)
        self.assertEqual(result["stages"], {
            "TASK_DISPATCHED": "CONFIRMED",
            "CURSOR_AGENT_STARTED": "NOT_CONFIRMED",
            "CODE_VERIFIED": "NOT_VERIFIED",
            "PRODUCTION_VERIFIED": "NOT_VERIFIED",
        })
        self.assertEqual(result["writer_lease_comment_id"], 55)
        self.assertEqual(result["writer_lease_status"], "CURSOR_AGENT_ENQUEUED")
        self.assertEqual(len(api.cursor_posts), 1)
        self.assertLess(
            next(i for i, (url, kwargs) in enumerate(api.calls)
                 if url.endswith(remote.LEDGER) and kwargs.get("body")),
            next(i for i, (url, kwargs) in enumerate(api.calls)
                 if "/v1/agents" in url and kwargs.get("method") == "POST"),
        )
        self.assertTrue(remote.reservations(api.comments)[0][1]["hold"])

    def test_global_active_agent_prevents_new_charge(self):
        api = FakeApi(global_agents=[{"status": "ACTIVE", "id": "bc-other"}])
        with self.assertRaises(mod.HandoffBlocked) as cm:
            dispatch(REPO, 1948, SHA, "Fix", "gh", "cursor", api)
        self.assertEqual(cm.exception.code, "CURSOR_GLOBAL_ACTIVE_AGENT_EXISTS")
        self.assertEqual(api.cursor_posts, [])
        self.assertEqual(len(api.calls), 4)

    def test_global_paginated_agent_inventory_fails_closed(self):
        api = FakeApi(global_next="page2")
        with self.assertRaises(mod.HandoffBlocked) as cm:
            dispatch(REPO, 1948, SHA, "Fix", "gh", "cursor", api)
        self.assertEqual(cm.exception.code, "CURSOR_GLOBAL_AGENT_INVENTORY_UNVERIFIED")
        self.assertEqual(api.cursor_posts, [])

    def test_duplicate_dispatch_is_stopped_by_persisted_shared_lease(self):
        api = FakeApi()
        dispatch(REPO, 1948, SHA, "Fix", "gh", "cursor", api)
        with self.assertRaises(mod.HandoffBlocked) as cm:
            dispatch(REPO, 1948, SHA, "Fix", "gh", "cursor", api)
        self.assertEqual(cm.exception.code, "CURSOR_SHARED_WRITER_LEASE_ACTIVE")
        self.assertEqual(len(api.cursor_posts), 1)

    def test_failed_ledger_reservation_prevents_billable_cursor_post(self):
        api = FakeApi(fail_ledger_write=True)
        with self.assertRaises(mod.HandoffBlocked) as cm:
            dispatch(REPO, 1948, SHA, "Fix", "gh", "cursor", api)
        self.assertEqual(cm.exception.code, "CURSOR_SHARED_WRITER_LEASE_UNAVAILABLE")
        self.assertEqual(api.cursor_posts, [])
        self.assertEqual(api.comments, [])

    def test_ambiguous_cursor_post_ack_retains_hold_and_blocks_retry(self):
        api = FakeApi(post_error=mod.HandoffBlocked("CURSOR_NETWORK_OR_RESPONSE_UNVERIFIED"))
        with self.assertRaises(mod.HandoffBlocked) as cm:
            dispatch(REPO, 1948, SHA, "Fix", "gh", "cursor", api)
        self.assertEqual(cm.exception.code, "CURSOR_NETWORK_OR_RESPONSE_UNVERIFIED")
        self.assertEqual(len(api.cursor_posts), 1)
        self.assertTrue(remote.reservations(api.comments)[0][1]["hold"])
        with self.assertRaises(mod.HandoffBlocked) as cm:
            dispatch(REPO, 1948, SHA, "Fix", "gh", "cursor", api)
        self.assertEqual(cm.exception.code, "CURSOR_SHARED_WRITER_LEASE_ACTIVE")
        self.assertEqual(len(api.cursor_posts), 1)

    def test_automatic_bridge_requires_trusted_actor_and_open_incident(self):
        path = ROOT / ".github/workflows/wow-v17-cursor-pr-handoff-bridge.yml"
        workflow = yaml.safe_load(path.read_text())
        condition = workflow["jobs"]["route-to-existing-dispatcher"]["if"]
        script = workflow["jobs"]["route-to-existing-dispatcher"]["steps"][0]["run"]
        self.assertIn("github.event.sender.login == github.repository_owner", condition)
        self.assertIn("github.event.sender.login == 'wow-engineering[bot]'", condition)
        self.assertIn("CURSOR_AUTOMATIC_INCIDENT_ID_MISSING", script)
        self.assertIn("CURSOR_AUTOMATIC_INCIDENT_NOT_OPEN", script)
        self.assertLess(script.index("incident_json="), script.index('if [ "$EVENT_NAME"'))
        self.assertIn('-f target_incident="$incident"', script)

    def test_cursor_enablement_without_redundant_billing_attestation(self):
        # The owner confirmed the provider-level $30 cap. GitHub requires only
        # the explicit handoff enable switch; provider API retains cost refusal.
        workflow = yaml.safe_load(
            (ROOT / ".github/workflows/wow-v17-engineering-provider-dispatcher.yml").read_text()
        )
        steps = workflow["jobs"]["independent-provider"]["steps"]
        step = next(item for item in steps if item.get("name") == "Dispatch scoped Cursor Cloud Agent")
        self.assertIn("cursor_dispatch", step["if"])
        self.assertIn("CURSOR_HANDOFF_ENABLED", step["env"])
        self.assertNotIn("CURSOR_SPEND_CAP_CONFIRMED", step["env"])
        self.assertNotIn("CURSOR_SPEND_CAP_UNCONFIRMED", step["run"])
        control = workflow["jobs"]["provider-control"]
        self.assertIn("steps.remote_fence.outcome == 'success'", control["outputs"]["cursor_dispatch"])
        self.assertEqual(
            workflow["jobs"]["independent-provider"]["concurrency"]["group"],
            "wow-v17-engineering-implementation-single-writer",
        )
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            bindir = root / "bin"
            bindir.mkdir()
            marker = root / "python-called"
            stub = bindir / "python"
            stub.write_text('#!/bin/sh\n: > "$CURSOR_PYTHON_CALL_MARKER"\n')
            stub.chmod(0o755)
            runner = root / "runner"
            runner.mkdir()
            env = os.environ | {
                "PATH": f"{bindir}:{os.environ['PATH']}",
                "RUNNER_TEMP": str(runner),
                "GITHUB_REPOSITORY": REPO,
                "CURSOR_HANDOFF_ENABLED": "",
                "CURSOR_PR_NUMBER": "1948",
                "CURSOR_INCIDENT": str(INCIDENT),
                "CURSOR_EXPECTED_HEAD": SHA,
                "CURSOR_TASK": "Fix existing tests",
                "CURSOR_ACTOR": ACTOR,
                "CURSOR_PYTHON_CALL_MARKER": str(marker),
            }
            env.pop("CURSOR_SPEND_CAP_CONFIRMED", None)
            disabled = subprocess.run(
                ["bash", "-c", step["run"]], env=env,
                capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(disabled.returncode, 0)
            receipt = json.loads((runner / "cursor-handoff.json").read_text())
            self.assertEqual(receipt["code"], "CURSOR_HANDOFF_DISABLED")
            self.assertFalse(receipt["can_execute"])
            self.assertFalse(marker.exists())
            env["CURSOR_HANDOFF_ENABLED"] = "true"
            enabled = subprocess.run(
                ["bash", "-c", step["run"]], env=env,
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(enabled.returncode, 0, enabled.stderr)
            self.assertTrue(marker.exists())

    def test_cursor_provider_billing_and_rate_limits_still_fail_closed(self):
        for status in (402, 429):
            with self.subTest(status=status):
                exc = error.HTTPError("https://api.cursor.com/v1/agents", status, "blocked", {}, None)
                with patch.object(mod.request, "urlopen", side_effect=exc):
                    with self.assertRaises(mod.HandoffBlocked) as cm:
                        mod.api_json("https://api.cursor.com/v1/agents",
                                     token="fake", basic=True, method="POST",
                                     body={"agentId": "test"})
                self.assertEqual(cm.exception.code, "CURSOR_BILLING_OR_RATE_LIMIT_BLOCKED")

    def test_cursor_dispatch_requires_remote_writer_fence(self):
        workflow = yaml.safe_load(
            (ROOT / ".github/workflows/wow-v17-engineering-provider-dispatcher.yml").read_text()
        )
        provider_control = workflow["jobs"]["provider-control"]
        cursor_dispatch = provider_control["outputs"]["cursor_dispatch"]
        cursor_job = workflow["jobs"]["independent-provider"]
        self.assertIn("steps.remote_fence.outcome == 'success'", cursor_dispatch)
        self.assertIn("needs.provider-control.outputs.cursor_dispatch == 'true'", cursor_job["if"])
        self.assertIn("steps.remote_fence.outcome == 'success'", provider_control["outputs"]["fallback_required"])
        self.assertNotIn("remote_fence.outcome == 'failure'", provider_control["outputs"]["fallback_required"])

    def test_invalid_agent_receipt_fails_closed(self):
        for response in (
            {"agent": {"id": "wrong"}, "run": {"id": "run"}},
            {"agent": [], "run": []},
            {"agent": {"id": mod.agent_id(REPO, 1948, SHA)}, "run": {"id": []}},
        ):
            api = FakeApi(post_result=response)
            with self.subTest(response=response), self.assertRaises(mod.HandoffBlocked) as cm:
                dispatch(REPO, 1948, SHA, "Fix", "gh", "cursor", api)
            self.assertEqual(cm.exception.code, "CURSOR_DISPATCH_RECEIPT_UNVERIFIED")
            self.assertEqual(len(api.cursor_posts), 1)
            self.assertEqual(len(api.comments), 1)
            self.assertTrue(remote.reservations(api.comments)[0][1]["hold"])

    def test_failed_cursor_launch_preserves_reconciliation_identity_and_http_status(self):
        err = mod.HandoffBlocked(
            "CURSOR_BAD_REQUEST_OR_REPOSITORY_ACCESS", provider_http_status=400,
        )
        api = FakeApi(post_error=err)
        with self.assertRaises(mod.HandoffBlocked) as cm:
            dispatch(REPO, 1948, SHA, "Fix", "gh", "cursor", api)
        self.assertEqual(cm.exception.code, "CURSOR_BAD_REQUEST_OR_REPOSITORY_ACCESS")
        self.assertEqual(cm.exception.details["writer_lease_comment_id"], 55)
        self.assertEqual(cm.exception.details["writer_lease_status"], "CURSOR_LAUNCH_RESERVED")
        self.assertEqual(cm.exception.details["agent_id"], mod.agent_id(REPO, 1948, SHA))
        self.assertIsNone(cm.exception.details["run_id"])
        self.assertEqual(cm.exception.details["main_sha"], MAIN_SHA)
        self.assertTrue(cm.exception.details["reconciliation_required"])
        self.assertEqual(cm.exception.details["provider_http_status"], 400)


if __name__ == "__main__":
    unittest.main()
