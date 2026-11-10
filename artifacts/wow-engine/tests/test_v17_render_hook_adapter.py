from __future__ import annotations

import json

import pytest

from v17 import render_hook_adapter as adapter


SHA = "a" * 40
OLD_SHA = "b" * 40
HOOK_URL = f"https://api.render.com/deploy/{adapter.SERVICE_ID}?key=opaque-test-key"


def _evidence():
    checks = {
        name: {"head_sha": SHA, "status": "completed", "conclusion": "success"}
        for name in adapter.REQUIRED_CHECKS
    }
    return {
        "target_sha": SHA,
        "current_main_sha": SHA,
        "service_id": adapter.SERVICE_ID,
        "checks": checks,
        "release_decision": {
            "decision_id": "decision-1",
            "reviewed_sha": SHA,
            "reviewer": "security-reviewer",
            "author": "candidate-author",
            "role": "security",
        },
        "provider_scope": {
            "independently_attested": True,
            "service_id": adapter.SERVICE_ID,
            "principal_id": "provider-principal-1",
            "scope": "service_deploy_hook",
        },
        "writer_inventory": {
            "complete": True,
            "active_writers": ["wow-v17-render-production-deploy:service_deploy_hook"],
            "old_controller_disabled": True,
        },
        "run": {
            "repository": "gregoryharper84-ship-it/WOW-Dashboard",
            "workflow_path": ".github/workflows/wow-v17-render-production-deploy.yml",
            "ref": "refs/heads/main",
            "head_sha": SHA,
            "run_id": "12345",
            "run_attempt": "1",
        },
        "previously_dispatched_run_ids": [],
        "expected_live_sha": OLD_SHA,
        "live_receipt": {
            "service_id": adapter.SERVICE_ID,
            "status": "live",
            "sha": OLD_SHA,
            "revision": "rev-current",
        },
        "provider_ack": None,
        "can_execute": False,
    }


def test_candidate_pins_ref_and_redacts_hook_url_without_sending():
    candidate = adapter.prepare_exact_sha_request(
        HOOK_URL,
        SHA,
        current_main_sha=SHA,
    )

    assert candidate.request.get_method() == "POST"
    assert candidate.request.full_url.endswith(f"ref={SHA}")
    assert candidate.target_sha == SHA
    assert candidate.can_execute is False
    assert adapter.HOOK_WRITER_ACTIVATED is False
    assert "opaque-test-key" not in repr(candidate)
    assert "opaque-test-key" not in json.dumps(candidate.receipt_fields())
    assert candidate.receipt_fields()["status"] == "HOOK_REQUEST_CANDIDATE_NOT_SENT"


@pytest.mark.parametrize(
    "hook_url,target_sha,current_main_sha,service_id,reason",
    [
        ("", SHA, SHA, adapter.SERVICE_ID, "RENDER_HOOK_SECRET_MISSING"),
        (HOOK_URL, "bad-sha", "bad-sha", adapter.SERVICE_ID, "RENDER_HOOK_TARGET_SHA_INVALID"),
        (HOOK_URL, SHA, OLD_SHA, adapter.SERVICE_ID, "RENDER_HOOK_STALE_MAIN_SHA"),
        (HOOK_URL, SHA, SHA, "srv-other", "RENDER_HOOK_SERVICE_IDENTITY_MISMATCH"),
        (
            "http://api.render.com/deploy/" + adapter.SERVICE_ID + "?key=k",
            SHA,
            SHA,
            adapter.SERVICE_ID,
            "RENDER_HOOK_ENDPOINT_MISMATCH",
        ),
        (
            HOOK_URL + "&ref=" + OLD_SHA,
            SHA,
            SHA,
            adapter.SERVICE_ID,
            "RENDER_HOOK_QUERY_INVALID",
        ),
    ],
)
def test_request_candidate_rejects_invalid_identity_or_url(
    hook_url, target_sha, current_main_sha, service_id, reason
):
    with pytest.raises(adapter.RenderHookContractError) as error:
        adapter.prepare_exact_sha_request(
            hook_url,
            target_sha,
            current_main_sha=current_main_sha,
            service_id=service_id,
        )
    assert error.value.reason_code == reason
    assert HOOK_URL.split("key=", 1)[1] not in str(error.value)


def test_consistent_synthetic_evidence_never_authorizes_live_dispatch():
    result = adapter.plan_hook_activation(_evidence())

    assert result == {
        "status": "CLAIMS_CONSISTENT_NOT_AUTHORIZED",
        "reason_code": "RENDER_HOOK_CUTOVER_REQUIRES_INDEPENDENT_APPROVAL",
        "can_execute": False,
        "dispatch_enabled": False,
    }


@pytest.mark.parametrize(
    "mutate,reason",
    [
        (lambda e: e.update(current_main_sha=OLD_SHA), "RENDER_HOOK_STALE_MAIN_SHA"),
        (lambda e: e["checks"].pop(adapter.REQUIRED_CHECKS[0]), "RENDER_HOOK_REQUIRED_CHECK_MISSING"),
        (
            lambda e: e["checks"][adapter.REQUIRED_CHECKS[1]].update(conclusion="failure"),
            "RENDER_HOOK_REQUIRED_CHECK_NOT_SUCCESS",
        ),
        (lambda e: e.update(release_decision=None), "RENDER_HOOK_INDEPENDENT_RELEASE_DECISION_MISSING"),
        (
            lambda e: e["release_decision"].update(reviewer="candidate-author"),
            "RENDER_HOOK_INDEPENDENT_RELEASE_DECISION_INVALID",
        ),
        (
            lambda e: e["release_decision"].update(author=""),
            "RENDER_HOOK_INDEPENDENT_RELEASE_DECISION_INVALID",
        ),
        (lambda e: e.update(provider_scope=None), "RENDER_HOOK_PROVIDER_SCOPE_UNATTESTED"),
        (
            lambda e: e["provider_scope"].update(independently_attested=False),
            "RENDER_HOOK_PROVIDER_SCOPE_UNATTESTED",
        ),
        (
            lambda e: e["provider_scope"].update(principal_id="unknown"),
            "RENDER_HOOK_PROVIDER_SCOPE_UNATTESTED",
        ),
        (
            lambda e: e["writer_inventory"].update(
                active_writers=[
                    "wow-v17-render-production-deploy:service_deploy_hook",
                    "manual-render-writer",
                ]
            ),
            "RENDER_HOOK_DUPLICATE_OR_UNKNOWN_WRITER",
        ),
        (
            lambda e: e.update(
                previously_dispatched_run_ids=["12345"],
            ),
            "RENDER_HOOK_DUPLICATE_RUN",
        ),
        (lambda e: e.update(provider_ack="ambiguous"), "RENDER_HOOK_PROVIDER_ACK_UNCERTAIN"),
        (lambda e: e.update(can_execute=True), "RENDER_HOOK_CAN_EXECUTE_MUST_BE_FALSE"),
    ],
)
def test_invalid_or_unsafe_release_evidence_fails_closed(mutate, reason):
    evidence = _evidence()
    mutate(evidence)

    result = adapter.plan_hook_activation(evidence)

    assert result["status"] == "BLOCKED_WITH_EXACT_REASON"
    assert result["reason_code"].startswith(reason)
    assert result["dispatch_enabled"] is False
    assert result["can_execute"] is False


def test_stale_live_receipt_and_unknown_run_identity_hold():
    stale_receipt = _evidence()
    stale_receipt["live_receipt"]["revision"] = ""
    assert adapter.plan_hook_activation(stale_receipt)["reason_code"] == (
        "RENDER_HOOK_LIVE_RECEIPT_STALE_OR_UNVERIFIED"
    )

    unknown_run = _evidence()
    unknown_run["run"]["workflow_path"] = ".github/workflows/other.yml"
    assert adapter.plan_hook_activation(unknown_run)["reason_code"] == (
        "RENDER_HOOK_WORKFLOW_IDENTITY_UNVERIFIED"
    )


@pytest.mark.parametrize(
    "status,body,expected",
    [
        (200, '{"deploy":{"id":"dep-abc123"}}', "HOOK_ACK_PENDING_EXACT_SHA_READBACK"),
        (202, "queued", "HOOK_QUEUED_RECONCILE_NO_RETRY"),
        (400, "rejected", "HOOK_RESPONSE_HOLD"),
        (408, "request timeout", "HOOK_ACK_AMBIGUOUS_RECONCILE_NO_RETRY"),
        (429, "rate limited", "HOOK_ACK_AMBIGUOUS_RECONCILE_NO_RETRY"),
        (500, "opaque error", "HOOK_ACK_AMBIGUOUS_RECONCILE_NO_RETRY"),
        (502, "gateway error", "HOOK_ACK_AMBIGUOUS_RECONCILE_NO_RETRY"),
        (503, "service unavailable", "HOOK_ACK_AMBIGUOUS_RECONCILE_NO_RETRY"),
        (504, "gateway timeout", "HOOK_ACK_AMBIGUOUS_RECONCILE_NO_RETRY"),
        (None, None, "HOOK_ACK_AMBIGUOUS_RECONCILE_NO_RETRY"),
        (True, None, "HOOK_ACK_AMBIGUOUS_RECONCILE_NO_RETRY"),
        (200, '{"deploy":{}}', "HOOK_ACK_AMBIGUOUS_RECONCILE_NO_RETRY"),
    ],
)
def test_acknowledgements_never_retry_or_claim_live(status, body, expected):
    result = adapter.classify_hook_ack(status, body)

    assert result["status"] == expected
    assert result["retry"] is False
    assert result["can_execute"] is False
    if expected == "HOOK_ACK_AMBIGUOUS_RECONCILE_NO_RETRY":
        assert result["next_action"] == "READ_ONLY_RECONCILIATION_ONLY"
    else:
        assert "next_action" not in result
