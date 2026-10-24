"""Deterministic no-network tests for the Cursor engineering handoff."""
import importlib.util
import pathlib
import unittest

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("cursor_cloud_handoff", HERE / "cursor_cloud_handoff.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
SHA = "a" * 40
REPO = "gregoryharper84-ship-it/WOW-Dashboard"


def pr(sha=SHA, repo=REPO, state="open", base="main"):
    return {"number": 1948, "state": state,
            "head": {"sha": sha, "ref": "fix/daily", "repo": {"full_name": repo}},
            "base": {"ref": base}}


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
                    mod.validate_pr(data, repo=REPO, number=1948, sha=SHA)
                self.assertEqual(cm.exception.code, reason)

    def test_missing_key_fails_before_network(self):
        with self.assertRaises(mod.HandoffBlocked) as cm:
            mod.dispatch(REPO, 1948, SHA, "Fix it", "gh", "")
        self.assertEqual(cm.exception.code, "CURSOR_API_KEY_MISSING")

    def test_active_agent_prevents_post(self):
        calls = []
        def api(url, **kwargs):
            calls.append((url, kwargs))
            if "/pulls/" in url:
                return pr()
            if "prUrl=" in url:
                return {"items": [{"id": "bc-prior", "status": "ACTIVE"}]}
            return {"items": []}
        with self.assertRaises(mod.HandoffBlocked) as cm:
            mod.dispatch(REPO, 1948, SHA, "Fix it", "gh", "cursor", api)
        self.assertEqual(cm.exception.code, "CURSOR_ACTIVE_PR_AGENT_EXISTS")
        self.assertEqual(len(calls), 3)

    def test_incomplete_inventory_fails_closed(self):
        def api(url, **kwargs):
            if "/pulls/" in url:
                return pr()
            return {"items": [], "nextCursor": "next"} if "prUrl=" in url else {"items": []}
        with self.assertRaises(mod.HandoffBlocked) as cm:
            mod.dispatch(REPO, 1948, SHA, "Fix", "gh", "cursor", api)
        self.assertEqual(cm.exception.code, "CURSOR_AGENT_INVENTORY_UNVERIFIED")

    def test_head_changes_between_checks_no_post(self):
        calls = []
        def api(url, **kwargs):
            calls.append(url)
            if "/pulls/" in url:
                return pr() if len([x for x in calls if "/pulls/" in x]) == 1 else pr(sha="b"*40)
            return {"items": []}
        with self.assertRaises(mod.HandoffBlocked) as cm:
            mod.dispatch(REPO, 1948, SHA, "Fix", "gh", "cursor", api)
        self.assertEqual(cm.exception.code, "CURSOR_STALE_PR_HEAD")
        self.assertEqual(len(calls), 4)

    def test_success_returns_queued_not_production_verified(self):
        calls = []
        def api(url, **kwargs):
            calls.append((url, kwargs))
            if "/pulls/" in url:
                return pr()
            if kwargs.get("method") == "POST":
                self.assertEqual(kwargs["body"]["agentId"], mod.agent_id(REPO, 1948, SHA))
                return {"agent": {"id": kwargs["body"]["agentId"]},
                        "run": {"id": "run-example", "status": "CREATING"}}
            return {"items": []}
        result = mod.dispatch(REPO, 1948, SHA, "Fix", "gh", "cursor", api)
        self.assertEqual(result["status"], "CURSOR_AGENT_ENQUEUED")
        self.assertFalse(result["production_verified"])
        self.assertEqual(len(calls), 5)

    def test_global_active_agent_prevents_new_charge(self):
        calls = []
        def api(url, **kwargs):
            calls.append((url, kwargs))
            if "/pulls/" in url:
                return pr()
            if "prUrl=" in url:
                raise AssertionError("must stop before PR-scope inventory")
            return {"items": [{"status": "ACTIVE", "id": "bc-other"}]}
        with self.assertRaises(mod.HandoffBlocked) as cm:
            mod.dispatch(REPO, 1948, SHA, "Fix", "gh", "cursor", api)
        self.assertEqual(cm.exception.code, "CURSOR_GLOBAL_ACTIVE_AGENT_EXISTS")
        self.assertEqual(len(calls), 2)

    def test_global_paginated_agent_inventory_fails_closed(self):
        def api(url, **kwargs):
            return pr() if "/pulls/" in url else {"items": [], "nextCursor": "page2"}
        with self.assertRaises(mod.HandoffBlocked) as cm:
            mod.dispatch(REPO, 1948, SHA, "Fix", "gh", "cursor", api)
        self.assertEqual(cm.exception.code, "CURSOR_GLOBAL_AGENT_INVENTORY_UNVERIFIED")

    def test_automatic_label_bridge_is_guarded(self):
        bridge = (HERE.parent.parent.parent / ".github" / "workflows" /
                  "wow-v17-cursor-pr-handoff-bridge.yml").read_text()
        self.assertIn("pull_request_target:", bridge)
        self.assertIn("types: [labeled]", bridge)
        self.assertIn("github.event.label.name == 'wow-cursor-ready'", bridge)
        self.assertIn("github.event.sender.login == 'wow-engineering[bot]'", bridge)
        self.assertIn("CURSOR_AUTOMATIC_INCIDENT_NOT_OPEN", bridge)
        self.assertIn('gh workflow run wow-v17-engineering-provider-dispatcher.yml', bridge)

    def test_spending_gate_default_disabled_and_global_cursor_serialization(self):
        workflow = (HERE.parent.parent.parent / ".github" / "workflows" /
                    "wow-v17-engineering-provider-dispatcher.yml").read_text()
        self.assertIn("CURSOR_SINGLE_AGENT", workflow)
        self.assertIn("CURSOR_SPEND_CAP_CONFIRMED", workflow)
        self.assertIn("CURSOR_SPEND_CAP_UNCONFIRMED", workflow)
        self.assertIn('CURSOR_HANDOFF_ENABLED:-false', workflow)

    def test_cursor_dispatch_requires_remote_writer_fence(self):
        workflow = (HERE.parent.parent.parent / ".github" / "workflows" /
                    "wow-v17-engineering-provider-dispatcher.yml").read_text()
        step = workflow.split("      - name: Dispatch scoped Cursor Cloud Agent", 1)[1]
        step = step.split("      - name: Deterministic survival checks", 1)[0]
        self.assertIn("steps.remote_fence.outcome == 'success'", step)

    def test_invalid_agent_receipt_fails_closed(self):
        def api(url, **kwargs):
            if "/pulls/" in url:
                return pr()
            return {"agent": {"id": "wrong"}, "run": {"id": "run"}} if kwargs.get("method") == "POST" else {"items": []}
        with self.assertRaises(mod.HandoffBlocked) as cm:
            mod.dispatch(REPO, 1948, SHA, "Fix", "gh", "cursor", api)
        self.assertEqual(cm.exception.code, "CURSOR_DISPATCH_RECEIPT_UNVERIFIED")


if __name__ == "__main__":
    unittest.main()
