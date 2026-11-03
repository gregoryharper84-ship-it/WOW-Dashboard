from __future__ import annotations

import json

import pytest

from v17 import restoration_loop as rl

NOW = "2026-11-03T12:00:00Z"
HEAD = "a" * 40
OTHER = "b" * 40
BOT = rl.ENGINEERING_BOT


def _reservation(*, state="DISPATCH_ACCEPTED", head=HEAD, attempts=1, login=BOT, comment_id=11):
    return {
        "id": comment_id,
        "user": {"login": login},
        "body": (
            f"{rl.RESERVATION_MARKER}\n"
            "- attempts: 1\n"
            f"- head_sha: {head}\n"
            f"- state: {state}\n"
            "- can_execute: false"
        ).replace("- attempts: 1", f"- attempts: {attempts}"),
    }


def _record(**over):
    row = {
        "incident_id": "502",
        "issue_number": 502,
        "severity": "P0",
        "priority_rank": 1,
        "execution_lane": "RAPID",
        "rapid_stream": "C",
        "lease_group": "P0_STREAM_C",
        "state": "OPEN",
        "work_stream": "RESTORATION",
        "conflict_keys": ["INTERACTIVE_RUNTIME"],
        "pr_number": 123,
        "head_sha": HEAD,
        "branch": "wow-engineering/incident-502",
        "acceptance_test": "GET /health => 200",
        "can_execute": False,
        "terminal_authority": rl.TERMINAL_AUTHORITY,
    }
    row.update(over)
    return row


def _queue(records=None):
    return {
        "records": [_record()] if records is None else records,
        "can_execute": False,
        "terminal_authority": rl.TERMINAL_AUTHORITY,
    }


def _repair(**over):
    row = {
        "issue_number": 502,
        "pr_number": 123,
        "head_sha": HEAD,
        "branch": "wow-engineering/incident-502",
        "acceptance_test": "GET /health => 200",
        "reservation_comments": [_reservation()],
        "ci": {"status": "PASSED", "required": ["wow-verify"], "failed": []},
    }
    row.update(over)
    return row


def _release(**over):
    row = {
        "issue_number": 502,
        "pr_number": 123,
        "merged_commit": HEAD,
        "state": "DEPLOYED_PENDING_VERIFY",
        "deployed_commit": HEAD,
        "probe": {
            "passed": True,
            "name": "GET /health => 200",
            "evidence_ref": "canary-run-1",
            "verified_commit": HEAD,
        },
    }
    row.update(over)
    return row


def _obs(**over):
    row = {
        "now_utc": NOW,
        "queue": _queue(),
        "active_repair": None,
        "releases_in_flight": [],
        "production": {"reachable": True, "deployed_commit": None},
        "evidence": None,
        "collection_errors": [],
    }
    row.update(over)
    return row


# --------------------------------------------------------------------------- #
# reservation reconciliation
# --------------------------------------------------------------------------- #
def test_no_reservation_allows_existing_dispatcher_to_reserve():
    result = rl.reconcile_writer_reservation([], expected_head_sha=HEAD)
    assert result["status"] == "NO_RESERVATION"
    assert result["dispatch_allowed"] is True
    assert result["can_execute"] is False


def test_foreign_marker_holds_and_never_claims_ownership():
    result = rl.reconcile_writer_reservation(
        [_reservation(login="some-user")], expected_head_sha=HEAD
    )
    assert result["status"] == "FOREIGN_OWNERSHIP_PRESENT"
    assert result["dispatch_allowed"] is False


def test_multiple_bot_reservations_are_ambiguous():
    result = rl.reconcile_writer_reservation(
        [_reservation(comment_id=11), _reservation(comment_id=12)], expected_head_sha=HEAD
    )
    assert result["status"] == "AMBIGUOUS_MULTIPLE_RESERVATIONS"
    assert result["dispatch_allowed"] is False


def test_malformed_reservation_is_invalid():
    result = rl.reconcile_writer_reservation(
        [{"id": 11, "user": {"login": BOT}, "body": rl.RESERVATION_MARKER}],
        expected_head_sha=HEAD,
    )
    assert result["status"] == "INVALID_RESERVATION"


def test_reserved_dispatch_is_never_redispatched():
    result = rl.reconcile_writer_reservation(
        [_reservation(state="DISPATCH_RESERVED")], expected_head_sha=HEAD
    )
    assert result["status"] == "RESERVED_UNCONFIRMED"
    assert result["dispatch_allowed"] is False


def test_accepted_exact_head_is_an_active_writer_not_a_dispatch_grant():
    result = rl.reconcile_writer_reservation([_reservation()], expected_head_sha=HEAD)
    assert result["status"] == "ACCEPTED_EXACT_HEAD"
    assert result["dispatch_allowed"] is False
    assert result["attempts"] == 1


def test_accepted_reservation_on_another_head_is_stale():
    result = rl.reconcile_writer_reservation(
        [_reservation(head=OTHER)], expected_head_sha=HEAD
    )
    assert result["status"] == "ACCEPTED_STALE_HEAD"


def test_invalid_expected_head_fails_closed():
    with pytest.raises(ValueError, match="EXPECTED_HEAD_SHA_INVALID"):
        rl.reconcile_writer_reservation([], expected_head_sha="not-a-sha")


# --------------------------------------------------------------------------- #
# bounded recovery
# --------------------------------------------------------------------------- #
def test_transient_failure_is_retried_exactly_once():
    first = rl.decide_bounded_recovery(failure_class="PROVIDER_TIMEOUT", retries_used=0)
    assert first["action"] == "RETRY_ONCE"
    second = rl.decide_bounded_recovery(failure_class="PROVIDER_TIMEOUT", retries_used=1)
    assert second["action"] == rl.HOLD


def test_transient_after_retry_uses_only_an_authorized_alternative():
    result = rl.decide_bounded_recovery(
        failure_class="PROVIDER_TIMEOUT", retries_used=1, authorized_alternative="claude"
    )
    assert result["action"] == "USE_AUTHORIZED_ALTERNATIVE"


def test_quota_failure_never_creates_paid_retries():
    result = rl.decide_bounded_recovery(failure_class="CREDIT_EXHAUSTED", retries_used=0)
    assert result["action"] == rl.HOLD
    assert result["reason_codes"] == ["QUOTA_NO_PAID_RETRY_NO_ALTERNATIVE"]


def test_quota_failure_with_authorized_alternative_is_routed():
    result = rl.decide_bounded_recovery(
        failure_class="CREDIT_EXHAUSTED", retries_used=2, authorized_alternative="existing-free-worker"
    )
    assert result["action"] == "USE_AUTHORIZED_ALTERNATIVE"


def test_deterministic_failure_is_repaired_not_retried():
    result = rl.decide_bounded_recovery(failure_class="TEST_REGRESSION", retries_used=0)
    assert result["action"] == "REPAIR_INPUT"


def test_unclassified_failure_holds():
    result = rl.decide_bounded_recovery(failure_class="SOMETHING_NEW", retries_used=0)
    assert result["action"] == rl.HOLD


def test_negative_retries_are_invalid():
    with pytest.raises(ValueError, match="RETRIES_USED_INVALID"):
        rl.decide_bounded_recovery(failure_class="PROVIDER_TIMEOUT", retries_used=-1)


# --------------------------------------------------------------------------- #
# metrics
# --------------------------------------------------------------------------- #
def test_never_verified_is_not_a_green_score():
    metrics = rl.compute_metrics(None, now_utc=NOW)
    assert metrics == {
        "verified_repairs": 0,
        "last_verified_repair_at": None,
        "seconds_since_last_verified_repair": None,
        "status": "NEVER_VERIFIED",
    }


def test_metrics_count_distinct_verified_repairs_and_age():
    metrics = rl.compute_metrics(
        {
            "verified_incident_ids": ["111", "111", "222"],
            "last_verified_repair_at": "2026-11-03T11:00:00Z",
        },
        now_utc=NOW,
    )
    assert metrics["verified_repairs"] == 2
    assert metrics["seconds_since_last_verified_repair"] == 3600
    assert metrics["status"] == "VERIFIED"


def test_invalid_metrics_evidence_fails_closed():
    with pytest.raises(ValueError, match="LAST_VERIFIED_REPAIR_AT_INVALID"):
        rl.compute_metrics({"last_verified_repair_at": "yesterday"}, now_utc=NOW)


# --------------------------------------------------------------------------- #
# planning: inspection and invariants
# --------------------------------------------------------------------------- #
def test_invalid_observation_holds():
    assert rl.plan_cycle("nope")["action"] == rl.HOLD


def test_collection_errors_hold_rather_than_infer_health():
    decision = rl.plan_cycle(_obs(collection_errors=["RENDER_API_UNREACHABLE"]))
    assert decision["action"] == rl.HOLD
    assert "OBSERVATION_INCOMPLETE" in decision["reason_codes"]


def test_broken_queue_governance_holds():
    queue = _queue()
    queue["can_execute"] = True
    decision = rl.plan_cycle(_obs(queue=queue))
    assert decision["reason_codes"] == ["QUEUE_EXECUTION_AUTHORITY_BROKEN"]


def test_more_than_one_release_holds():
    decision = rl.plan_cycle(_obs(releases_in_flight=[_release(), _release(issue_number=9)]))
    assert decision["reason_codes"] == ["MULTIPLE_ACTIVE_RELEASES"]


def test_same_issue_as_repair_and_release_holds():
    decision = rl.plan_cycle(_obs(active_repair=_repair(), releases_in_flight=[_release()]))
    assert decision["reason_codes"] == ["DUPLICATE_ACTIVE_UNIT"]


def test_every_decision_preserves_safety_invariants():
    for observation in (
        _obs(),
        _obs(active_repair=_repair()),
        _obs(releases_in_flight=[_release()]),
        _obs(collection_errors=["X"]),
    ):
        decision = rl.plan_cycle(observation)
        assert decision["can_execute"] is False
        assert decision["terminal_authority"] == rl.TERMINAL_AUTHORITY


# --------------------------------------------------------------------------- #
# planning: repair
# --------------------------------------------------------------------------- #
def test_active_repair_without_reservation_holds():
    decision = rl.plan_cycle(_obs(active_repair=_repair(reservation_comments=[])))
    assert decision["action"] == rl.HOLD
    assert "WRITER_NO_RESERVATION" in decision["reason_codes"]


def test_active_repair_reserved_but_unconfirmed_is_reconciled_read_only():
    decision = rl.plan_cycle(
        _obs(active_repair=_repair(reservation_comments=[_reservation(state="DISPATCH_RESERVED")]))
    )
    assert decision["action"] == "RECONCILE_ACTIVE_REPAIR"
    assert decision["dispatch"] is None
    assert decision["next_action"] == "READ_ONLY_RECONCILE_DISPATCH_OUTCOME"


def test_active_repair_on_stale_head_never_gets_a_second_writer():
    decision = rl.plan_cycle(
        _obs(active_repair=_repair(head_sha=HEAD, reservation_comments=[_reservation(head=OTHER)]))
    )
    assert decision["action"] == rl.HOLD
    assert "WRITER_ACCEPTED_STALE_HEAD" in decision["reason_codes"]


def test_active_repair_without_acceptance_test_holds():
    decision = rl.plan_cycle(_obs(active_repair=_repair(acceptance_test="")))
    assert decision["reason_codes"] == ["ACCEPTANCE_TEST_MISSING"]


def test_failed_required_checks_feed_back_into_the_same_repair():
    decision = rl.plan_cycle(
        _obs(active_repair=_repair(ci={"status": "FAILED", "required": ["wow-verify"], "failed": ["wow-verify"]}))
    )
    assert decision["action"] == "FEEDBACK_TO_SAME_REPAIR"
    assert decision["dispatch"]["workflow"] == rl.REPAIR_WORKFLOW
    assert decision["head_sha"] == HEAD


def test_pending_checks_are_not_progress():
    observation = _obs(active_repair=_repair(ci={"status": "PENDING", "required": ["wow-verify"], "failed": []}))
    observation["last_cycle"] = {
        "step": "VERIFY", "action": "AWAIT_CI", "incident_id": "502", "head_sha": HEAD,
    }
    decision = rl.plan_cycle(observation)
    assert decision["action"] == "AWAIT_CI"
    assert decision["progress_made"] is False
    assert decision["heartbeat_only"] is True


def test_passed_checks_hand_off_to_the_existing_protected_release_path():
    decision = rl.plan_cycle(_obs(active_repair=_repair()))
    assert decision["action"] == "HAND_OFF_PROTECTED_RELEASE"
    assert decision["dispatch"]["workflow"] == rl.PROTECTED_RELEASE_WORKFLOW
    assert decision["dispatch"]["mode"] == "existing_protected_router"


def test_release_hold_stops_only_that_release():
    decision = rl.plan_cycle(_obs(active_repair=_repair(release_hold={"reason": "CODEOWNER_PENDING"})))
    assert decision["action"] == "HOLD_RELEASE_ONLY"
    assert decision["release_blocked"] is True
    assert decision["unrelated_work_may_progress"] is True
    assert decision["preparation"]["writer_dispatch_allowed"] is False


def test_retry_cap_requires_owner():
    decision = rl.plan_cycle(
        _obs(active_repair=_repair(reservation_comments=[_reservation(attempts=3)]))
    )
    assert decision["action"] == rl.HOLD
    assert decision["needs_owner"] is True


def test_transient_repair_failure_dispatches_one_retry():
    decision = rl.plan_cycle(
        _obs(active_repair=_repair(repair_failure={"class": "PROVIDER_TIMEOUT", "retries_used": 0}))
    )
    assert decision["action"] == "RETRY_SAME_REPAIR_ONCE"


def test_quota_repair_failure_without_alternative_holds():
    decision = rl.plan_cycle(
        _obs(active_repair=_repair(repair_failure={"class": "CREDIT_EXHAUSTED", "retries_used": 0}))
    )
    assert decision["action"] == rl.HOLD
    assert "QUOTA_NO_PAID_RETRY_NO_ALTERNATIVE" in decision["reason_codes"]


# --------------------------------------------------------------------------- #
# planning: release and proof
# --------------------------------------------------------------------------- #
def test_merged_release_waits_for_deploy():
    decision = rl.plan_cycle(_obs(releases_in_flight=[_release(state="MERGED_PENDING_DEPLOY")]))
    assert decision["action"] == "AWAIT_DEPLOY"
    assert decision["progress_made"] is False


def test_deployed_commit_must_equal_the_merged_commit():
    decision = rl.plan_cycle(
        _obs(
            releases_in_flight=[_release(deployed_commit=OTHER)],
            production={"reachable": True, "deployed_commit": OTHER},
        )
    )
    assert decision["action"] == rl.HOLD
    assert decision["reason_codes"] == ["DEPLOYED_COMMIT_MISMATCH"]


def test_probe_must_exist_and_bind_to_the_deployed_commit():
    decision = rl.plan_cycle(_obs(releases_in_flight=[_release(probe=None)]))
    assert decision["reason_codes"] == ["USER_FUNCTION_PROBE_MISSING"]
    mismatch = rl.plan_cycle(
        _obs(releases_in_flight=[_release(probe={
            "passed": True, "evidence_ref": "x", "verified_commit": OTHER,
        })])
    )
    assert mismatch["reason_codes"] == ["PROBE_COMMIT_MISMATCH"]


def test_failed_probe_keeps_the_issue_open():
    decision = rl.plan_cycle(
        _obs(releases_in_flight=[_release(probe={
            "passed": False, "evidence_ref": "x", "verified_commit": HEAD,
        })])
    )
    assert decision["action"] == rl.HOLD
    assert decision["next_action"] == "KEEP_ISSUE_OPEN_AND_REPAIR"


def test_verified_deploy_closes_the_issue_and_counts_one_repair():
    decision = rl.plan_cycle(
        _obs(
            releases_in_flight=[_release()],
            production={"reachable": True, "deployed_commit": HEAD},
            evidence={
                "verified_incident_ids": ["111"],
                "last_verified_repair_at": "2026-11-06T11:00:00Z",
            },
            now_utc="2026-11-06T12:00:00Z",
        )
    )
    assert decision["action"] == "CLOSE_VERIFIED_ISSUE"
    assert decision["dispatch"] == {"issue_close": True}
    assert decision["metrics"]["verified_repairs"] == 2
    assert decision["metrics"]["seconds_since_last_verified_repair"] == 0
    assert "502" in decision["verified_incident_ids"]


# --------------------------------------------------------------------------- #
# planning: selection
# --------------------------------------------------------------------------- #
def test_empty_queue_is_a_noop_not_progress():
    decision = rl.plan_cycle(_obs(queue=_queue([])))
    assert decision["action"] == "IDLE_NO_WORK"
    assert decision["progress_made"] is False


def test_selection_reuses_existing_pr_and_requires_acceptance():
    decision = rl.plan_cycle(_obs())
    assert decision["action"] == "RESERVE_AND_DISPATCH_REPAIR"
    assert decision["incident_id"] == "502"
    assert decision["pr_number"] == 123
    assert decision["dispatch"]["workflow"] == rl.REPAIR_WORKFLOW


def test_selection_holds_without_an_existing_pr():
    queue = _queue([_record(pr_number=None, head_sha=None)])
    decision = rl.plan_cycle(_obs(queue=queue))
    assert decision["reason_codes"] == ["EXISTING_PR_OR_BRANCH_MISSING"]


def test_selection_holds_without_an_acceptance_test():
    decision = rl.plan_cycle(_obs(queue=_queue([_record(acceptance_test="")])))
    assert decision["reason_codes"] == ["ACCEPTANCE_TEST_MISSING"]


# --------------------------------------------------------------------------- #
# evidence
# --------------------------------------------------------------------------- #
def test_evidence_comment_round_trips_metrics():
    decision = rl.plan_cycle(
        _obs(
            releases_in_flight=[_release()],
            production={"reachable": True, "deployed_commit": HEAD},
            evidence={"verified_incident_ids": ["111"], "last_verified_repair_at": "2026-11-06T11:00:00Z"},
            now_utc="2026-11-06T12:00:00Z",
        )
    )
    body = rl.render_evidence_comment(decision)
    assert body.startswith(rl.EVIDENCE_MARKER)
    assert "- commit: " + HEAD in body
    assert "- test_result: PROBE_PASSED" in body
    assert "- deployment_result: DEPLOYED_PENDING_VERIFY" in body
    assert "- progress_made: true" in body
    assert "- can_execute: false" in body
    parsed = rl.parse_evidence_comment([{"id": 1, "body": body, "user": {"login": "github-actions[bot]"}}])
    assert parsed == {
        "verified_incident_ids": ["111", "502"],
        "last_verified_repair_at": "2026-11-06T12:00:00Z",
    }


def test_evidence_record_always_carries_commit_test_deploy_next_action():
    decision = rl.plan_cycle(_obs(active_repair=_repair()))
    record = decision["evidence"]
    assert set(record) >= {
        "commit", "test_result", "deployment_result", "next_action",
        "can_execute", "terminal_authority",
    }
    assert record["can_execute"] is False
    assert record["terminal_authority"] == rl.TERMINAL_AUTHORITY


def test_cli_plan_prints_json(capsys, tmp_path):
    path = tmp_path / "observation.json"
    path.write_text(json.dumps(_obs()))
    assert rl.main(["plan", "--observation", str(path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "RESERVE_AND_DISPATCH_REPAIR"
    assert payload["can_execute"] is False


def test_required_check_missing_is_pending_even_when_other_checks_pass():
    rules = [{"type": "required_status_checks", "parameters": {
        "required_status_checks": [{"context": "QA", "integration_id": 42}]}}]
    assert rl.required_ci([], rules, HEAD)["status"] == "PENDING"


def test_required_check_rejects_wrong_app_or_head():
    rules = [{"type": "required_status_checks", "parameters": {
        "required_status_checks": [{"context": "QA", "integration_id": 42}]}}]
    check = {"id": 1, "name": "QA", "head_sha": HEAD,
             "app": {"id": 99}, "status": "completed", "conclusion": "success"}
    assert rl.required_ci([check], rules, HEAD)["status"] == "PENDING"
    check.update(app={"id": 42}, head_sha=OTHER)
    assert rl.required_ci([check], rules, HEAD)["status"] == "PENDING"


def test_required_check_uses_latest_bound_run_and_skipped_is_failed():
    rules = [{"type": "required_status_checks", "parameters": {
        "required_status_checks": [{"context": "QA", "integration_id": 42}]}}]
    check = {"id": 1, "name": "QA", "head_sha": HEAD,
             "app": {"id": 42}, "status": "completed", "conclusion": "success"}
    assert rl.required_ci([check], rules, HEAD)["status"] == "PASSED"
    assert rl.required_ci([check, {**check, "id": 2, "conclusion": "skipped"}], rules, HEAD)["status"] == "FAILED"


def test_no_protection_inventory_never_means_ci_passed():
    assert rl.required_ci([], [], HEAD)["status"] == "UNKNOWN"


def test_forged_ledger_comment_cannot_invent_verified_repairs():
    body = rl.render_evidence_comment(rl.plan_cycle(_obs(releases_in_flight=[_release()])))
    assert rl.parse_evidence_comment([{"id": 1, "body": body, "user": {"login": "someone"}}]) is None


def test_repeated_proof_does_not_count_a_repair_twice():
    decision = rl.plan_cycle(_obs(releases_in_flight=[_release()], evidence={
        "verified_incident_ids": ["502"], "last_verified_repair_at": "2026-11-03T11:00:00Z"}))
    assert decision["metrics"]["verified_repairs"] == 1
    assert decision["metrics"]["last_verified_repair_at"] == "2026-11-03T11:00:00Z"


def test_observer_does_not_add_a_second_controller_or_mutation_authority():
    from pathlib import Path
    import yaml
    workflow = Path(__file__).resolve().parents[3] / ".github/workflows/wow-v17-restoration-loop.yml"
    text = workflow.read_text()
    config = yaml.safe_load(text)
    triggers = config.get("on", config.get(True))
    assert "schedule" not in triggers
    assert set(triggers) == {"workflow_dispatch"}
    assert all(value == "read" for value in config["permissions"].values())
    assert "gh workflow run" not in text
    assert "--method PATCH" not in text
    assert "--method POST" not in text


def test_collector_keeps_open_incident_release_even_if_latest_pr_is_unrelated(tmp_path, monkeypatch):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"can_execute": False, "terminal_authority": rl.TERMINAL_AUTHORITY,
        "restoration": [{"issue_number": 502, "severity": "P1", "priority_rank": 1,
                          "conflict_keys": ["RUNTIME"]}], "acceleration": []}))
    repo = "gregoryharper84-ship-it/WOW-Dashboard"
    def pull(number, incident):
        return {"number": number, "user": {"login": BOT}, "state": "closed",
                "body": f"Incident: {incident}", "base": {"ref": "main"},
                "head": {"repo": {"full_name": repo}},
                "merged_at": NOW, "merge_commit_sha": HEAD}
    def pages(path):
        if "/issues?" in path:
            return [{"number": 502, "state": "open"}]
        if "/pulls?" in path:
            return [pull(1, 502), pull(2, 999)]
        return []
    monkeypatch.setattr(rl, "_production_observation", lambda _: ({"reachable": True}, []))
    monkeypatch.setattr(rl, "_render_live_commit", lambda *_: (HEAD, []))
    result = rl.collect_observation(repo=repo, manifest_path=str(manifest), now_utc=NOW, gh_pages=pages)
    assert [row["issue_number"] for row in result["releases_in_flight"]] == [502]
    assert rl.plan_cycle(result)["reason_codes"] == ["USER_FUNCTION_PROBE_MISSING"]


def test_normal_cycle_preserves_existing_verified_incident_ids():
    decision = rl.plan_cycle(_obs(evidence={"verified_incident_ids": ["111"],
        "last_verified_repair_at": "2026-11-03T11:00:00Z"}))
    assert "- verified_incident_ids: 111" in rl.render_evidence_comment(decision)
