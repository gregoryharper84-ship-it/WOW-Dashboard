import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / "wow_qa_ledger_recovery.py"
spec = importlib.util.spec_from_file_location("ledger_recovery", PATH)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

DIGEST = "a" * 64
NOW = 1_800_000_000
OLD = "2026-10-29T10:00:00Z"  # stale relative to any realistic NOW in tests


def ledger_run(run_id=999, status="in_progress", conclusion=None, title="",
               digest=DIGEST, app_id=m.QA_APP_ID, started_at=OLD, summary=""):
    return {"id": run_id, "name": m.LEDGER_NAME_PREFIX + digest,
            "head_sha": m.LEDGER_SENTINEL_SHA, "external_id": digest,
            "status": status, "conclusion": conclusion,
            "app": {"id": app_id}, "started_at": started_at,
            "output": {"title": title, "summary": summary}}


def evidence(**overrides):
    base = {"pr_number": 2275, "head_sha": "b" * 40, "base_sha": "c" * 40,
            "run_id": 4242, "run_attempt": 1,
            "provider_decision": "NONE_RECEIVED",
            "evidence_ref": "workflow run 4242 attempt 1 cancelled"}
    base.update(overrides)
    return base


class FakeClient:
    """In-memory GitHub Checks store; mutations recorded with their API."""

    def __init__(self, ledger=(), head_checks=(), claims=(), pr_head="b" * 40,
                 patch_failures=()):
        self.read_token = "read-token"
        self.qa_token = "qa-app-token"
        self.ledger = [dict(r) for r in ledger]
        self.head_checks = list(head_checks)
        self.claims = [dict(c) for c in claims]
        self.pr = {"number": 2275, "state": "open",
                   "head": {"sha": pr_head, "repo": {"full_name": m.REPO}},
                   "base": {"sha": "c" * 40, "ref": "main",
                            "repo": {"full_name": m.REPO}}}
        self.patch_failures = list(patch_failures)
        self.patch_calls = []
        self.create_calls = []
        self._next_id = 5000

    def get_pr(self, pr_number):
        return self.pr

    def list_ledger(self, digest):
        return [r for r in self.ledger if r["external_id"] == digest]

    def list_head_checks(self, head_sha):
        return self.head_checks

    def list_claims(self, reservation_id):
        return list(self.claims)

    def create_check(self, payload):
        self.create_calls.append(payload)
        self._next_id += 1
        claim = {"id": self._next_id, "status": "in_progress",
                 "app": {"id": m.QA_APP_ID}, **payload}
        self.claims.append(claim)
        return {"id": self._next_id}

    def patch_check(self, check_run_id, payload):
        self.patch_calls.append((check_run_id, payload))
        if self.patch_failures:
            raise self.patch_failures.pop(0)
        for store in (self.ledger, self.claims):
            for run in store:
                if run["id"] == check_run_id:
                    run.update({"status": payload["status"],
                                "conclusion": payload["conclusion"],
                                "output": payload["output"]})
                    return {"id": check_run_id,
                            "conclusion": payload["conclusion"],
                            "external_id": run.get("external_id")}
        raise AssertionError(check_run_id)


def run_recovery(client, **kw):
    args = dict(digest=DIGEST, reservation_id=999, evidence=evidence(),
                actor=m.OWNER_LOGIN, authorized_actors={m.OWNER_LOGIN},
                now_epoch=NOW, dry_run=False)
    args.update(kw)
    return m.execute_recovery(client, **args)


class OrphanRecoveryTests(unittest.TestCase):
    def test_kill_after_reservation_recovers_as_hold_never_pass(self):
        client = FakeClient(ledger=[ledger_run()])
        receipt = run_recovery(client)
        self.assertEqual(receipt["outcome"], "HOLD")
        self.assertEqual(receipt["reservation"]["check_run_id"], 999)
        self.assertEqual(receipt["original"]["run_id"], 4242)
        saved = client.ledger[0]
        self.assertEqual((saved["status"], saved["conclusion"]),
                         ("completed", "neutral"))  # neutral, never success
        self.assertIn("recovered, never PASS", saved["output"]["summary"])

    def test_fresh_reservation_is_not_stale_and_untouched(self):
        import datetime
        fresh = datetime.datetime.fromtimestamp(
            NOW, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        client = FakeClient(ledger=[ledger_run(started_at=fresh)])
        with self.assertRaises(m.Hold) as cm:
            run_recovery(client)
        self.assertEqual(str(cm.exception), "RESERVATION_NOT_STALE")
        self.assertEqual(client.patch_calls, [])
        self.assertEqual(client.ledger[0]["status"], "in_progress")

    def test_cancelled_during_provider_review_recovers_as_hold(self):
        client = FakeClient(ledger=[ledger_run()])
        receipt = run_recovery(client, evidence=evidence(
            provider_decision="PROVIDER_UNAVAILABLE",
            evidence_ref="run 4242 cancelled during provider call"))
        self.assertEqual(receipt["outcome"], "HOLD")

    def test_confirmed_reject_completed_as_reject_not_hold(self):
        client = FakeClient(ledger=[ledger_run()])
        receipt = run_recovery(client, evidence=evidence(
            provider_decision="REJECT_CONFIRMED",
            reviewer_identity="anthropic-claude-api",
            evidence_ref="artifact receipt run 4242: semantic REJECT"))
        self.assertEqual(receipt["outcome"], "REJECT")
        self.assertEqual(client.ledger[0]["conclusion"], "failure")
        self.assertTrue(client.ledger[0]["output"]["title"].startswith("REJECT"))

    def test_reject_corroborated_by_prior_completed_ledger_entry(self):
        prior = ledger_run(run_id=111, status="completed", conclusion="failure",
                           title="REJECT — content review")
        client = FakeClient(ledger=[prior, ledger_run()])
        receipt = run_recovery(client)  # evidence claims none received
        self.assertEqual(receipt["outcome"], "REJECT")

    def test_reject_corroborated_by_pr_head_check(self):
        head_reject = {"name": m.CHECK_NAME, "conclusion": "failure",
                       "app": {"id": m.QA_APP_ID},
                       "output": {"title": "REJECT — independent review"}}
        client = FakeClient(ledger=[ledger_run()], head_checks=[head_reject])
        self.assertEqual(run_recovery(client)["outcome"], "REJECT")

    def test_two_recovery_workers_race_only_lowest_claim_wins(self):
        rival = {"id": 42, "status": "in_progress", "app": {"id": m.QA_APP_ID},
                 "name": m.RECOVERY_CLAIM_PREFIX + "999"}
        client = FakeClient(ledger=[ledger_run()], claims=[rival])
        with self.assertRaises(m.Hold) as cm:
            run_recovery(client)
        self.assertEqual(str(cm.exception), "RECOVERY_CLAIM_LOST")
        self.assertEqual(client.ledger[0]["status"], "in_progress")
        # Loser completes only its own claim as superseded, never the ledger.
        patched_ids = [run_id for run_id, _ in client.patch_calls]
        self.assertNotIn(999, patched_ids)

    def test_missing_or_malformed_qa_authentication_fails_closed(self):
        client = FakeClient(ledger=[ledger_run()])
        client.qa_token = ""
        with self.assertRaises(m.Hold) as cm:
            run_recovery(client)
        self.assertEqual(str(cm.exception), "QA_APP_TOKEN_MISSING")
        client = FakeClient(ledger=[ledger_run()])
        client.qa_token = client.read_token
        with self.assertRaises(m.Hold):
            run_recovery(client)
        self.assertEqual(client.patch_calls, [])
        client = FakeClient(ledger=[ledger_run()],
                            patch_failures=[m.Hold("HTTP_401")])
        with self.assertRaises(m.Hold) as cm:
            run_recovery(client)
        self.assertEqual(str(cm.exception), "HTTP_401")
        self.assertEqual(client.ledger[0]["status"], "in_progress")
        self.assertEqual(len([p for p in client.patch_calls if p[0] == 999]), 1)

    def test_unauthorized_actor_cannot_recover_or_erase_reject(self):
        client = FakeClient(ledger=[ledger_run()])
        with self.assertRaises(m.Hold) as cm:
            run_recovery(client, actor="random-user",
                         authorized_actors={m.OWNER_LOGIN})
        self.assertEqual(str(cm.exception), "RECOVERY_ACTOR_UNAUTHORIZED")
        self.assertEqual(client.patch_calls, [])
        self.assertEqual(client.create_calls, [])

    def test_duplicate_recovery_event_is_idempotent(self):
        client = FakeClient(ledger=[ledger_run()])
        run_recovery(client)
        patches_after_first = len(client.patch_calls)
        receipt = run_recovery(client)
        self.assertEqual(receipt["status"], "ALREADY_COMPLETED")
        self.assertEqual(receipt["outcome"], "HOLD")
        self.assertEqual(len(client.patch_calls), patches_after_first)

    def test_transient_patch_failures_bounded_and_fail_closed(self):
        client = FakeClient(ledger=[ledger_run()], patch_failures=[
            m.Hold("HTTP_503"), m.Hold("HTTP_503")])
        self.assertEqual(run_recovery(client)["outcome"], "HOLD")
        self.assertEqual(len([p for p in client.patch_calls if p[0] == 999]), 3)
        client = FakeClient(ledger=[ledger_run()], patch_failures=[
            m.Hold("HTTP_503")] * 5)
        with self.assertRaises(m.Hold):
            run_recovery(client)
        self.assertEqual(len([p for p in client.patch_calls if p[0] == 999]),
                         m.MAX_PATCH_ATTEMPTS)
        self.assertEqual(client.ledger[0]["status"], "in_progress")

    def test_dry_run_mutates_nothing(self):
        client = FakeClient(ledger=[ledger_run()])
        receipt = run_recovery(client, dry_run=True)
        self.assertEqual(receipt["status"], "PLANNED")
        self.assertEqual(client.patch_calls, [])
        self.assertEqual(client.create_calls, [])

    def test_unestablishable_provider_decision_fails_closed(self):
        for bad in (evidence(provider_decision="UNKNOWN"),
                    evidence(provider_decision="REJECT_CONFIRMED"),  # no reviewer
                    evidence(evidence_ref=""),
                    {k: v for k, v in evidence().items() if k != "base_sha"}):
            client = FakeClient(ledger=[ledger_run()])
            with self.assertRaises(m.Hold):
                run_recovery(client, evidence=bad)
            self.assertEqual(client.patch_calls, [])

    def test_stale_base_and_moved_head_recorded_not_silently_trusted(self):
        client = FakeClient(ledger=[ledger_run()], pr_head="d" * 40)
        receipt = run_recovery(client)
        self.assertTrue(receipt["original"]["pr_head_moved_since_reservation"])
        self.assertEqual(receipt["outcome"], "HOLD")  # content digest is base-free
        client = FakeClient(ledger=[ledger_run()])
        with self.assertRaises(m.Hold):
            run_recovery(client, evidence=evidence(base_sha="not-a-sha"))

    def test_provenance_forgery_is_rejected(self):
        foreign = ledger_run(app_id=17)
        client = FakeClient(ledger=[foreign])
        with self.assertRaises(m.Hold) as cm:
            run_recovery(client)
        self.assertEqual(str(cm.exception), "RESERVATION_NOT_FOUND")
        forged = ledger_run()
        forged["head_sha"] = "e" * 40
        client = FakeClient(ledger=[forged])
        with self.assertRaises(m.Hold) as cm:
            run_recovery(client)
        self.assertEqual(str(cm.exception), "LEDGER_PROVENANCE_INVALID")

    def test_qa_client_uses_read_token_for_reads_qa_token_for_mutations(self):
        seen = []
        def fake_request(url, *, token=None, data=None, method=None):
            seen.append((method, token))
            return {"total_count": 0, "check_runs": []}
        client = m.GitHubRecoveryClient("read-tok", "qa-tok")
        with patch.object(m, "request_json", fake_request):
            client.list_ledger(DIGEST)
            client.create_check({"name": "x"})
            client.patch_check(1, {"status": "completed"})
        self.assertEqual(seen, [(None, "read-tok"), ("POST", "qa-tok"),
                                ("PATCH", "qa-tok")])


class AttemptBudgetDesignTests(unittest.TestCase):
    def _completed(self, run_id, digest, pr, decision="REJECT"):
        return ledger_run(run_id=run_id, status="completed",
                          conclusion="failure" if decision == "REJECT" else "neutral",
                          title=f"{decision} — content review", digest=digest,
                          summary=f"PR={pr} decision={decision}")

    def test_identical_content_moved_to_another_pr_stays_blocked(self):
        # Same digest, new PR/head: the completed REJECT is digest-bound.
        records = [self._completed(1, DIGEST, 2300), ledger_run(run_id=999)]
        receipt = run_recovery(FakeClient(ledger=records),
                               evidence=evidence(pr_number=2301))
        self.assertEqual(receipt["outcome"], "REJECT")

    def test_legitimate_revision_after_reject_is_within_budget(self):
        history = [self._completed(1, "f" * 64, 2301)]
        result = m.assess_attempt_budget(history, 2301, budget=3)
        self.assertEqual(result["distinct_rejected_digests"], 1)
        self.assertFalse(result["exceeded"])
        self.assertFalse(result["enforced"])

    def test_repeated_one_byte_rerolls_exceed_experimental_budget(self):
        history = [self._completed(i, str(i) * 64, 2301) for i in range(1, 5)]
        history.append(self._completed(99, "1" * 64, 2301))  # identical: no inflate
        result = m.assess_attempt_budget(history, 2301, budget=3)
        self.assertEqual(result["distinct_rejected_digests"], 4)
        self.assertTrue(result["exceeded"])
        self.assertEqual(result["recommendation"], "HOLD_FOR_INDEPENDENT_REVIEW")
        self.assertFalse(result["enforced"])  # design only, not activated

    def test_attempt_budget_cannot_be_activated_unilaterally(self):
        with self.assertRaises(m.Hold) as cm:
            m.assess_attempt_budget([], 2301, enabled=True)
        self.assertEqual(str(cm.exception),
                         "ATTEMPT_BUDGET_NOT_AUTHORIZED_FOR_ACTIVATION")


if __name__ == "__main__":
    unittest.main()
