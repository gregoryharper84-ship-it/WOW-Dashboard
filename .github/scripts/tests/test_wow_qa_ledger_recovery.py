import hashlib
import importlib.util
import json
import threading
from pathlib import Path
from unittest.mock import patch
import unittest

ROOT = Path(__file__).resolve().parents[1]
rec_spec = importlib.util.spec_from_file_location(
    "wow_qa_ledger_recovery", ROOT / "wow_qa_ledger_recovery.py")
r = importlib.util.module_from_spec(rec_spec)
rec_spec.loader.exec_module(r)
pol_spec = importlib.util.spec_from_file_location(
    "wow_qa_reroll_policy", ROOT / "wow_qa_reroll_policy.py")
p = importlib.util.module_from_spec(pol_spec)
pol_spec.loader.exec_module(p)


HEAD = "a" * 40
BASE = "b" * 40
BLOB = "c" * 40
DIGEST = hashlib.sha256(
    json.dumps(sorted({"src/v17.py": BLOB}.items()), separators=(",", ":")).encode()
).hexdigest()


def _target(pr=2269):
    return {"pr": pr, "head_sha": HEAD, "base_sha": BASE,
            "file_blobs": {"src/v17.py": BLOB}}


def _evidence_text(check_id=501, run_id=9001, provider_invoked="false",
                   semantic_decision="none", actor="wow-qa[bot]"):
    return r._format_block(r.RESERVATION_MARK, {
        "digest": DIGEST,
        "check_run_id": str(check_id),
        "pr": "2269",
        "head": HEAD,
        "base": BASE,
        "run_id": str(run_id),
        "run_attempt": "1",
        "actor": actor,
        "workflow": r.REVIEW_WORKFLOW,
        "provider_invoked": provider_invoked,
        "semantic_decision": semantic_decision,
    })


def _check(check_id=501, status="in_progress", conclusion=None, summary=None,
           title="IN_PROGRESS — content review reservation", app_id=r.QA_APP_ID):
    return {
        "id": check_id,
        "name": r.LEDGER_NAME_PREFIX + DIGEST,
        "head_sha": r.LEDGER_SENTINEL_SHA,
        "external_id": DIGEST,
        "status": status,
        "conclusion": conclusion,
        "app": {"id": app_id},
        "output": {"title": title, "summary": summary or _evidence_text(check_id=check_id)},
        "started_at": "2026-10-29T18:00:00Z",
    }


def _run(run_id=9001, status="completed", conclusion="cancelled",
         path=r.REVIEW_WORKFLOW_PATH, name=r.REVIEW_WORKFLOW):
    return {"id": run_id, "status": status, "conclusion": conclusion,
            "path": path, "name": name}


def _jobs(started_at=None, conclusion="cancelled"):
    return {"jobs": [{"steps": [
        {"name": "Checkout trusted protected main only", "conclusion": "success",
         "started_at": "2026-10-29T18:00:00Z"},
        {"name": r.REVIEW_STEP_NAME, "conclusion": conclusion, "started_at": started_at},
    ]}]}


class FakeGitHub:
    def __init__(self):
        self.checks = {}
        self.runs = {}
        self.jobs = {}
        self.head_checks = {}
        self.ledger_pages = None
        self.lock = threading.Lock()
        self.patch_log = []
        self.get_log = []
        self.fail_patch_left = 0
        self.app_by_token = {"qa-app": r.QA_APP_ID, "eng-app": 17}
        self.read_barrier = None
        self.initial_check_reads = 0
        self.overwrite_completed = False

    def request_json(self, url, *, token=None, data=None, headers=None, method=None):
        if not url.startswith("https://api.github.com/repos/" + r.REPO + "/"):
            raise r.Hold("REVIEW_ENDPOINT_UNTRUSTED")
        path = url.split("/repos/" + r.REPO, 1)[1]
        if method == "PATCH":
            return self._patch(path, token, data)
        if method is not None or data is not None:
            raise r.Hold("REVIEW_HTTP_METHOD_UNTRUSTED")
        return self._get(path, token)

    def _get(self, path, token):
        self.get_log.append(path)
        if self.read_barrier is not None and path.startswith("/check-runs/"):
            with self.lock:
                self.initial_check_reads += 1
                n = self.initial_check_reads
            if n <= 2:
                self.read_barrier.wait(timeout=5)
        if path.startswith("/check-runs/"):
            check_id = int(path.rsplit("/", 1)[1].split("?")[0])
            with self.lock:
                if check_id not in self.checks:
                    raise r.Hold("GITHUB_HTTP_404")
                return json.loads(json.dumps(self.checks[check_id]))
        if path.startswith("/actions/runs/") and path.endswith("/jobs?per_page=100"):
            run_id = int(path.split("/")[3])
            return json.loads(json.dumps(self.jobs[run_id]))
        if path.startswith("/actions/runs/"):
            run_id = int(path.split("/")[3].split("?")[0])
            if run_id not in self.runs:
                raise r.Hold("GITHUB_HTTP_404")
            return json.loads(json.dumps(self.runs[run_id]))
        if "/commits/" in path and "check-runs" in path:
            sha = path.split("/commits/")[1].split("/")[0]
            if "WOW%20Independent%20Security%20Review" in path:
                runs = self.head_checks.get(sha, [])
            else:
                runs = [c for c in self.checks.values()
                        if c.get("head_sha") == sha or sha == r.LEDGER_SENTINEL_SHA]
            if self.ledger_pages is not None and sha == r.LEDGER_SENTINEL_SHA:
                runs = self.ledger_pages
            return {"total_count": len(runs), "check_runs": json.loads(json.dumps(runs))}
        raise AssertionError(path)

    def _patch(self, path, token, data):
        if self.fail_patch_left:
            self.fail_patch_left -= 1
            raise r.Hold("GITHUB_HTTP_503")
        check_id = int(path.rsplit("/", 1)[1])
        app_id = self.app_by_token.get(token)
        if app_id != r.QA_APP_ID:
            result = {"id": check_id, "app": {"id": app_id}}
            return result
        with self.lock:
            current = self.checks[check_id]
            if (current.get("status") == "completed"
                    and current.get("conclusion") == "failure"
                    and not self.overwrite_completed):
                self.patch_log.append({"id": check_id, "denied": "reject-retain", "token": token})
                return json.loads(json.dumps(current))
            updated = json.loads(json.dumps(current))
            updated.update({k: data[k] for k in data if k != "output"})
            if "output" in data:
                updated["output"] = dict(data["output"])
            updated["app"] = {"id": r.QA_APP_ID}
            self.checks[check_id] = updated
            self.patch_log.append({"id": check_id, "data": data, "token": token})
            return json.loads(json.dumps(updated))


class LedgerRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeGitHub()
        self.patches = [
            patch.object(r, "request_json", self.api.request_json),
            patch.object(r, "_now", lambda: r.datetime(2026, 10, 29, 18, 20, tzinfo=r.timezone.utc)),
        ]
        for item in self.patches:
            item.start()
        self.addCleanup(lambda: [item.stop() for item in self.patches])

    def _ready(self, *, provider_invoked="false", semantic_decision="none",
               step_started=None, run_conclusion="cancelled", check_id=501, run_id=9001):
        check = _check(check_id=check_id, summary=_evidence_text(
            check_id=check_id, run_id=run_id, provider_invoked=provider_invoked,
            semantic_decision=semantic_decision))
        self.api.checks[check_id] = check
        self.api.runs[run_id] = _run(run_id=run_id, conclusion=run_conclusion)
        self.api.jobs[run_id] = _jobs(started_at=step_started, conclusion=run_conclusion)
        self.api.head_checks[HEAD] = []
        return check

    def test_kill_after_reservation_completes_hold_never_pass(self):
        self._ready(provider_invoked="false", step_started=None)
        receipt = r.recover_check(501, read_token="read", qa_token="qa-app",
                                  recovery_run_id=7001, recovery_attempt=1)
        self.assertEqual(receipt["decision"], "HOLD")
        self.assertEqual(receipt["reason"], "SOURCE_RUN_CANCELLED_BEFORE_PROVIDER")
        self.assertNotEqual(receipt["decision"], "PASS")
        stored = self.api.checks[501]
        self.assertEqual(stored["status"], "completed")
        self.assertEqual(stored["conclusion"], "neutral")
        self.assertEqual(stored["output"]["title"], "HOLD — content review")
        self.assertIn(r.RESERVATION_MARK, stored["output"]["summary"])
        self.assertIn("run_id=9001", stored["output"]["summary"])
        self.assertIn(r.RECEIPT_MARK, stored["output"]["summary"])

    def test_cancel_before_provider_holds(self):
        self._ready(provider_invoked="false", step_started=None, run_conclusion="cancelled")
        receipt = r.recover_check(501, read_token="read", qa_token="qa-app",
                                  recovery_run_id=7001, recovery_attempt=1)
        self.assertEqual(receipt["reason"], "SOURCE_RUN_CANCELLED_BEFORE_PROVIDER")
        self.assertEqual(self.api.checks[501]["conclusion"], "neutral")

    def test_cancel_during_provider_without_decision_fails_closed(self):
        self._ready(provider_invoked="true", step_started="2026-10-29T18:01:00Z",
                    semantic_decision="none")
        with self.assertRaises(r.Hold) as cm:
            r.recover_check(501, read_token="read", qa_token="qa-app",
                            recovery_run_id=7001, recovery_attempt=1)
        self.assertEqual(str(cm.exception), "LEDGER_RECOVERY_PROVIDER_DECISION_UNPROVEN")
        self.assertEqual(self.api.checks[501]["status"], "in_progress")
        self.assertIsNone(self.api.checks[501]["conclusion"])

    def test_confirmed_reject_after_failed_ledger_patch_completes_reject(self):
        self._ready(provider_invoked="true", semantic_decision="REJECT",
                    step_started="2026-10-29T18:01:00Z", run_conclusion="failure")
        receipt = r.recover_check(501, read_token="read", qa_token="qa-app",
                                  recovery_run_id=7001, recovery_attempt=1)
        self.assertEqual(receipt["decision"], "REJECT")
        self.assertEqual(self.api.checks[501]["conclusion"], "failure")
        self.assertEqual(self.api.checks[501]["output"]["title"], "REJECT — content review")
        self.assertIn("semantic_reject_observed=true", self.api.checks[501]["output"]["summary"])

    def test_pr_head_reject_check_upgrades_orphaned_reservation(self):
        self._ready(provider_invoked="true", semantic_decision="none",
                    step_started="2026-10-29T18:01:00Z", run_conclusion="failure")
        self.api.head_checks[HEAD] = [{
            "name": r.CHECK_NAME, "conclusion": "failure", "app": {"id": r.QA_APP_ID},
            "output": {"title": "REJECT — independent semantic source review"},
        }]
        receipt = r.recover_check(501, read_token="read", qa_token="qa-app",
                                  recovery_run_id=7001, recovery_attempt=1)
        self.assertEqual(receipt["decision"], "REJECT")
        self.assertEqual(self.api.checks[501]["conclusion"], "failure")

    def test_two_recovery_workers_race_single_writer(self):
        self._ready(provider_invoked="false")
        self.api.read_barrier = threading.Barrier(2)
        results = []

        def worker(run_id):
            try:
                results.append(r.recover_check(
                    501, read_token="read", qa_token="qa-app",
                    recovery_run_id=run_id, recovery_attempt=1))
            except r.Hold as exc:
                results.append(str(exc))

        threads = [threading.Thread(target=worker, args=(8001,)),
                   threading.Thread(target=worker, args=(8002,))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual(len(results), 2)
        stored = self.api.checks[501]
        self.assertEqual(stored["status"], "completed")
        self.assertEqual(stored["conclusion"], "neutral")
        self.assertEqual(stored["output"]["title"], "HOLD — content review")
        self.assertIn("run_id=9001", stored["output"]["summary"])
        outcomes = []
        for item in results:
            if type(item) is dict:
                outcomes.append(item.get("decision"))
                outcomes.append(item.get("reason"))
            else:
                outcomes.append(item)
        self.assertNotIn("PASS", outcomes)
        self.assertTrue(any(value == "HOLD" for value in outcomes))
        self.assertTrue(any(value in {
            "HOLD", "LEDGER_RECOVERY_LEASE_HELD", "LEDGER_RECOVERY_LEASE_LOST",
            "ALREADY_COMPLETED_HOLD", "SOURCE_RUN_CANCELLED_BEFORE_PROVIDER",
        } for value in outcomes))
        completions = [item for item in self.api.patch_log
                       if item.get("data", {}).get("status") == "completed"]
        self.assertGreaterEqual(len(completions), 1)
        self.assertTrue(all(item["data"].get("conclusion") == "neutral" for item in completions))

    def test_missing_qa_app_token_does_not_mutate(self):
        self._ready()
        with self.assertRaises(r.Hold) as cm:
            r.recover_check(501, read_token="read", qa_token="",
                            recovery_run_id=7001, recovery_attempt=1)
        self.assertEqual(str(cm.exception), "CONTENT_LEDGER_QA_APP_TOKEN_MISSING")
        self.assertEqual(self.api.checks[501]["status"], "in_progress")
        self.assertEqual(self.api.patch_log, [])

    def test_read_token_cannot_be_reused_as_qa_app_token(self):
        self._ready()
        with self.assertRaises(r.Hold) as cm:
            r.recover_check(501, read_token="same", qa_token="same",
                            recovery_run_id=7001, recovery_attempt=1)
        self.assertEqual(str(cm.exception), "LEDGER_RECOVERY_QA_APP_TOKEN_NOT_DISTINCT")
        self.assertEqual(self.api.checks[501]["status"], "in_progress")

    def test_malformed_qa_app_token_cannot_complete_reservation(self):
        self._ready()
        with self.assertRaises(r.Hold) as cm:
            r.recover_check(501, read_token="read", qa_token="eng-app",
                            recovery_run_id=7001, recovery_attempt=1)
        self.assertEqual(str(cm.exception), "LEDGER_RECOVERY_QA_APP_IDENTITY_UNVERIFIED")
        self.assertEqual(self.api.checks[501]["status"], "in_progress")

    def test_duplicate_recovery_event_is_idempotent(self):
        self._ready()
        first = r.recover_check(501, read_token="read", qa_token="qa-app",
                                recovery_run_id=7001, recovery_attempt=1)
        second = r.recover_check(501, read_token="read", qa_token="qa-app",
                                 recovery_run_id=7001, recovery_attempt=1)
        self.assertEqual(first["decision"], "HOLD")
        self.assertEqual(second["reason"], "DUPLICATE_RECOVERY_EVENT")
        self.assertEqual(second["idempotent"], "true")
        self.assertEqual(self.api.checks[501]["conclusion"], "neutral")
        completions = [item for item in self.api.patch_log
                       if item.get("data", {}).get("status") == "completed"]
        self.assertEqual(len(completions), 1)

    def test_repeated_provider_timeouts_without_verdict_fail_closed(self):
        self._ready(provider_invoked="true", semantic_decision="none",
                    step_started="2026-10-29T18:01:00Z", run_conclusion="timed_out")
        with self.assertRaises(r.Hold) as cm:
            r.recover_check(501, read_token="read", qa_token="qa-app",
                            recovery_run_id=7001, recovery_attempt=1)
        self.assertEqual(str(cm.exception), "LEDGER_RECOVERY_PROVIDER_DECISION_UNPROVEN")
        self.assertEqual(self.api.checks[501]["status"], "in_progress")

    def test_stale_base_still_holds_ledger_and_never_publishes_pass(self):
        self._ready()
        receipt = r.recover_check(501, read_token="read", qa_token="qa-app",
                                  recovery_run_id=7001, recovery_attempt=1)
        self.assertEqual(receipt["decision"], "HOLD")
        self.assertEqual(receipt["base"], BASE)
        self.assertNotIn("PASS", json.dumps(receipt))
        publish_calls = [item for item in self.api.patch_log
                         if item.get("data", {}).get("conclusion") == "success"]
        self.assertEqual(publish_calls, [])

    def test_identical_content_reject_survives_other_pr(self):
        self._ready(semantic_decision="REJECT", provider_invoked="true",
                    step_started="2026-10-29T18:01:00Z", run_conclusion="failure")
        r.recover_check(501, read_token="read", qa_token="qa-app",
                        recovery_run_id=7001, recovery_attempt=1)
        other = _check(check_id=777, status="completed", conclusion="failure",
                       title="REJECT — content review")
        self.api.checks[777] = other
        self.assertTrue(r.sibling_ledger_reject(DIGEST, "read", ignore_id=None))
        with self.assertRaises(p.Hold) as cm:
            p.evaluate_reroll_policy(
                {"pr": 2270, "file_blobs": {"src/v17.py": BLOB}},
                [self.api.checks[501]])
        self.assertEqual(str(cm.exception), "PREVIOUS_SEMANTIC_REJECT_IDENTICAL_CONTENT")

    def test_legitimate_revised_pr_after_reject_is_new_digest(self):
        rejected = _check(status="completed", conclusion="failure",
                          title="REJECT — content review")
        self.api.checks[501] = rejected
        revised = {"pr": 2269, "file_blobs": {"src/v17.py": "d" * 40, "fix.py": "e" * 40}}
        result = p.evaluate_reroll_policy(revised, [rejected])
        self.assertTrue(result["allowed"])
        self.assertNotEqual(result["digest"], DIGEST)

    def test_unauthorized_actor_cannot_erase_reject_or_publish_pass(self):
        check = _check(status="completed", conclusion="failure",
                       title="REJECT — content review",
                       summary=_evidence_text(semantic_decision="REJECT"))
        self.api.checks[501] = check
        self.api.runs[9001] = _run(conclusion="failure")
        self.api.jobs[9001] = _jobs(started_at="2026-10-29T18:01:00Z", conclusion="failure")
        retained = r.recover_check(501, read_token="read", qa_token="qa-app",
                                   recovery_run_id=7001, recovery_attempt=1)
        self.assertEqual(retained["reason"], "RETAINED_SEMANTIC_REJECT")
        with self.assertRaises(r.Hold) as denied:
            r.patch_check_run(501, "eng-app", {
                "status": "completed", "conclusion": "success",
                "output": {"title": "PASS — content review", "summary": "forged"}})
        self.assertEqual(str(denied.exception), "LEDGER_RECOVERY_QA_APP_IDENTITY_UNVERIFIED")
        self.assertEqual(self.api.checks[501]["conclusion"], "failure")
        self.assertEqual(self.api.checks[501]["output"]["title"], "REJECT — content review")
        self.assertNotIn("PASS", self.api.checks[501]["output"]["title"])
        self.assertNotIn("forged", self.api.checks[501]["output"].get("summary", ""))
        with self.assertRaises(r.Hold) as cm:
            r.complete_reservation(
                self.api.checks[501], r.parse_reservation_evidence(check["output"]["summary"]),
                decision="PASS", reason="forged", qa_token="qa-app",
                recovery_run_id=7003, recovery_attempt=1,
                lease={"lease_run_id": "7003"})
        self.assertEqual(str(cm.exception), "LEDGER_RECOVERY_PASS_FORBIDDEN")

    def test_missing_provenance_fails_closed(self):
        check = _check(summary="not a reservation block")
        self.api.checks[501] = check
        with self.assertRaises(r.Hold) as cm:
            r.recover_check(501, read_token="read", qa_token="qa-app",
                            recovery_run_id=7001, recovery_attempt=1)
        self.assertEqual(str(cm.exception), "LEDGER_RECOVERY_PROVENANCE_UNVERIFIED")
        self.assertEqual(self.api.checks[501]["status"], "in_progress")

    def test_active_source_run_is_not_recovered(self):
        self._ready()
        self.api.runs[9001] = _run(status="in_progress", conclusion=None)
        with self.assertRaises(r.Hold) as cm:
            r.recover_check(501, read_token="read", qa_token="qa-app",
                            recovery_run_id=7001, recovery_attempt=1)
        self.assertEqual(str(cm.exception), "LEDGER_RECOVERY_SOURCE_RUN_STILL_ACTIVE")
        self.assertEqual(self.api.checks[501]["status"], "in_progress")

    def test_attach_evidence_never_completes_reservation(self):
        self.api.checks[501] = _check(summary="")
        evidence = r.attach_reservation_evidence(
            {"id": 501, "digest": DIGEST}, _target(),
            run_id=9001, run_attempt=1, actor="wow-qa[bot]", qa_token="qa-app")
        stored = self.api.checks[501]
        self.assertEqual(stored["status"], "in_progress")
        self.assertEqual(evidence["provider_invoked"], "false")
        parsed = r.parse_reservation_evidence(stored["output"]["summary"])
        self.assertEqual(parsed["head"], HEAD)
        self.assertEqual(parsed["digest"], DIGEST)

    def test_bounded_retries_on_transient_patch(self):
        self._ready()
        self.api.fail_patch_left = 2
        receipt = r.recover_check(501, read_token="read", qa_token="qa-app",
                                  recovery_run_id=7001, recovery_attempt=1)
        self.assertEqual(receipt["decision"], "HOLD")
        self.assertEqual(self.api.checks[501]["status"], "completed")

    def test_untrusted_patch_targets_denied(self):
        original = self.patches[0]
        original.stop()
        try:
            cases = (
                ("https://api.anthropic.com/v1/messages", "REVIEW_ENDPOINT_UNTRUSTED"),
                ("https://api.github.com/repos/" + r.REPO + "/pulls/2269",
                 "REVIEW_HTTP_METHOD_UNTRUSTED"),
                ("https://api.github.com/repos/" + r.REPO + "/check-runs/abc",
                 "REVIEW_HTTP_METHOD_UNTRUSTED"),
            )
            for url, expected in cases:
                with self.subTest(url=url):
                    with self.assertRaises(r.Hold) as cm:
                        r.request_json(url, token="qa-app", method="PATCH", data={})
                    self.assertEqual(str(cm.exception), expected)
        finally:
            original.start()

    def test_redirects_do_not_forward_credentials(self):
        from urllib.request import Request
        req = Request("https://api.github.com/repos/" + r.REPO + "/check-runs/1",
                      data=b"{}", headers={"Authorization": "Bearer secret"})
        self.assertIsNone(r.NoCredentialRedirect().redirect_request(
            req, None, 302, "Found", {"Location": "https://evil.example"},
            "https://evil.example"))

    def test_recover_all_records_unproven_without_pass(self):
        self._ready(provider_invoked="true", step_started="2026-10-29T18:01:00Z")
        receipts = r.recover_all(read_token="read", qa_token="qa-app",
                                 recovery_run_id=7001, recovery_attempt=1)
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]["mutated"], "false")
        self.assertEqual(receipts[0]["reason"], "LEDGER_RECOVERY_PROVIDER_DECISION_UNPROVEN")
        self.assertEqual(receipts[0]["decision"], "HOLD")
        self.assertEqual(self.api.checks[501]["status"], "in_progress")

    def test_source_invariants_forbid_pass(self):
        import inspect
        source = inspect.getsource(r)
        self.assertIn("LEDGER_RECOVERY_PASS_FORBIDDEN", source)
        workflow = Path(__file__).resolve().parents[2].joinpath(
            "workflows/wow-v17-qa-ledger-recovery.yml").read_text()
        self.assertIn("wow-independent-security-qa-ledger-writer", workflow)
        self.assertIn("environment: wow-qa", workflow)
        self.assertNotIn('decision="PASS"', inspect.getsource(r.complete_reservation))
        self.assertIn("ALLOWED_RECOVERY_DECISIONS", source)


class RerollPolicyTests(unittest.TestCase):
    def test_policy_inactive_by_default(self):
        with patch.dict(p.os.environ, {}, clear=False):
            p.os.environ.pop(p.EXPERIMENTAL_ENV, None)
            self.assertIsNone(p.experimental_budget())
            result = p.evaluate_reroll_policy(
                {"pr": 2269, "file_blobs": {"src/v17.py": BLOB}}, [])
            self.assertEqual(result["policy"], "inactive")
            self.assertTrue(result["allowed"])

    def test_one_byte_edit_creates_new_digest(self):
        before, after, changed = p.one_byte_edit_changes_digest(
            {"src/v17.py": BLOB}, "src/v17.py", "d" * 40)
        self.assertTrue(changed)
        self.assertNotEqual(before, after)

    def test_identical_content_reject_not_removed_by_budget(self):
        checks = [_check(status="completed", conclusion="failure",
                         title="REJECT — content review")]
        with patch.dict(p.os.environ, {p.EXPERIMENTAL_ENV: "3"}):
            with self.assertRaises(p.Hold) as cm:
                p.evaluate_reroll_policy({"pr": 2271, "file_blobs": {"src/v17.py": BLOB}}, checks)
            self.assertEqual(str(cm.exception), "PREVIOUS_SEMANTIC_REJECT_IDENTICAL_CONTENT")

    def test_experimental_budget_holds_trivial_rerolls_but_allows_revision(self):
        prior = {
            "name": r.CHECK_NAME, "conclusion": "failure", "app": {"id": r.QA_APP_ID},
            "pr_number": 2269, "output": {"title": "REJECT — independent semantic source review"},
        }
        history = [prior, dict(prior), dict(prior)]
        with patch.dict(p.os.environ, {p.EXPERIMENTAL_ENV: "3"}):
            with self.assertRaises(p.Hold) as cm:
                p.evaluate_reroll_policy(
                    {"pr": 2269, "file_blobs": {"src/v17.py": "d" * 40}}, history)
            self.assertEqual(str(cm.exception), "EXPERIMENTAL_REROLL_BUDGET_EXHAUSTED")
        with patch.dict(p.os.environ, {p.EXPERIMENTAL_ENV: "3"}):
            allowed = p.evaluate_reroll_policy(
                {"pr": 2269, "file_blobs": {"src/v17.py": "d" * 40}}, history[:2])
            self.assertTrue(allowed["allowed"])
            self.assertEqual(allowed["pr_rejects"], 2)

    def test_invalid_budget_fails_closed_and_does_not_self_activate(self):
        with patch.dict(p.os.environ, {p.EXPERIMENTAL_ENV: "0"}):
            with self.assertRaises(p.Hold):
                p.experimental_budget()
        with patch.dict(p.os.environ, {p.EXPERIMENTAL_ENV: "unlimited"}):
            with self.assertRaises(p.Hold):
                p.evaluate_reroll_policy({"pr": 2269, "file_blobs": {"src/v17.py": BLOB}}, [])


if __name__ == "__main__":
    unittest.main()
