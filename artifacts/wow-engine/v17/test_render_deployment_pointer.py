import pytest

from v17.render_deployment_pointer import (
    ADVANCING_RECEIPT_STATUSES,
    NON_ADVANCING_RECEIPT_STATUSES,
    DeploymentPointerContractError,
    POINTER_ENVIRONMENT,
    POINTER_SOURCE,
    POINTER_TASK,
    receipt_advances_pointer,
    reconcile_deployment_pointer,
)


COMMIT_SHA = "7f13238683774b8fa4e95c743de3497d1e0be26b"
DEPLOY_ID = "dep-daq3gd8u01pc73fllam0"


def _advancing_receipt(status="EXACT_SHA_RENDER_DEPLOY_LIVE"):
    return {
        "status": status,
        "commit_sha": COMMIT_SHA,
        "deploy_id": DEPLOY_ID,
        "deploy_authority": "GOVERNED_RENDER_API_HANDOFF",
        "workflow_run_id": "35916975478",
        "workflow_run_attempt": "1",
        "can_execute": False,
    }


@pytest.mark.parametrize("status", sorted(NON_ADVANCING_RECEIPT_STATUSES))
def test_no_deploy_receipts_never_advance_or_call_github(status):
    calls = []

    def github_api(method, path, payload=None):
        calls.append((method, path, payload))
        raise AssertionError("non-advancing receipt must not call GitHub deployment API")

    receipt = {"status": status, "target_sha": COMMIT_SHA, "can_execute": False}
    result = reconcile_deployment_pointer(receipt, github_api, repository="owner/repo")

    assert result == {
        "status": "DEPLOYMENT_POINTER_UNCHANGED",
        "source_receipt_status": status,
        "can_execute": False,
    }
    assert calls == []


@pytest.mark.parametrize("status", sorted(ADVANCING_RECEIPT_STATUSES))
def test_exact_live_receipts_create_successful_pointer_with_provenance(status):
    calls = []

    def github_api(method, path, payload=None):
        calls.append((method, path, payload))
        if method == "GET" and path == "/branches/main":
            return {"commit": {"sha": COMMIT_SHA}}
        if method == "GET" and path.startswith("/deployments?"):
            return []
        if method == "POST" and path == "/deployments":
            return {"id": 4242}
        if method == "GET" and path == "/deployments/4242/statuses?per_page=100":
            return []
        if method == "POST" and path == "/deployments/4242/statuses":
            return {"id": 5252, "state": "success"}
        raise AssertionError((method, path, payload))

    result = reconcile_deployment_pointer(
        _advancing_receipt(status),
        github_api,
        repository="owner/repo",
    )

    assert result["status"] == "DEPLOYMENT_POINTER_RECONCILED"
    assert result["github_deployment_id"] == 4242
    assert result["github_deployment_created"] is True
    assert result["commit_sha"] == COMMIT_SHA
    assert result["render_deploy_id"] == DEPLOY_ID
    assert result["can_execute"] is False

    create_payload = next(payload for method, path, payload in calls if method == "POST" and path == "/deployments")
    assert create_payload["ref"] == COMMIT_SHA
    assert create_payload["environment"] == POINTER_ENVIRONMENT
    assert create_payload["task"] == POINTER_TASK
    assert create_payload["auto_merge"] is False
    assert create_payload["payload"]["source"] == POINTER_SOURCE
    assert create_payload["payload"]["render_deploy_id"] == DEPLOY_ID
    assert create_payload["payload"]["can_execute"] is False


@pytest.mark.parametrize("status", sorted(ADVANCING_RECEIPT_STATUSES))
def test_replaying_same_exact_receipt_is_idempotent(status):
    calls = []
    existing_payload = {
        "source": POINTER_SOURCE,
        "commit_sha": COMMIT_SHA,
        "render_deploy_id": DEPLOY_ID,
        "source_receipt_status": status,
        "can_execute": False,
    }
    success_description = f"{status} | render={DEPLOY_ID} | can_execute=false"

    def github_api(method, path, payload=None):
        calls.append((method, path, payload))
        if method == "GET" and path == "/branches/main":
            return {"commit": {"sha": COMMIT_SHA}}
        if method == "GET" and path.startswith("/deployments?"):
            return [{"id": 4242, "payload": existing_payload}]
        if method == "GET" and path == "/deployments/4242/statuses?per_page=100":
            return [{"state": "success", "description": success_description}]
        raise AssertionError((method, path, payload))

    result = reconcile_deployment_pointer(
        _advancing_receipt(status),
        github_api,
        repository="owner/repo",
    )

    assert result["github_deployment_id"] == 4242
    assert result["github_deployment_created"] is False
    assert not any(method == "POST" for method, _path, _payload in calls)


def test_unknown_or_execution_enabled_receipt_fails_closed():
    with pytest.raises(DeploymentPointerContractError, match="UNKNOWN_RECEIPT_STATUS"):
        receipt_advances_pointer({"status": "WORKFLOW_GREEN", "can_execute": False})

    receipt = _advancing_receipt()
    receipt["can_execute"] = True
    with pytest.raises(DeploymentPointerContractError, match="CAN_EXECUTE_MUST_BE_FALSE"):
        receipt_advances_pointer(receipt)



def test_stale_main_rejects_live_receipt_before_any_github_pointer_write():
    calls = []

    def github_api(method, path, payload=None):
        calls.append((method, path, payload))
        if method == "GET" and path == "/branches/main":
            return {"commit": {"sha": "b" * 40}}
        raise AssertionError((method, path, payload))

    with pytest.raises(DeploymentPointerContractError, match="MAIN_SHA_STALE_OR_UNKNOWN"):
        reconcile_deployment_pointer(_advancing_receipt(), github_api, repository="owner/repo")
    assert calls == [("GET", "/branches/main", None)]


def test_moving_main_between_receipt_and_pointer_create_fails_closed():
    calls = []
    reads = []

    def github_api(method, path, payload=None):
        calls.append((method, path, payload))
        if method == "GET" and path == "/branches/main":
            reads.append(1)
            return {"commit": {"sha": COMMIT_SHA if len(reads) == 1 else "b" * 40}}
        if method == "GET" and path.startswith("/deployments?"):
            return []
        raise AssertionError((method, path, payload))

    with pytest.raises(DeploymentPointerContractError, match="MAIN_SHA_STALE_OR_UNKNOWN"):
        reconcile_deployment_pointer(_advancing_receipt(), github_api, repository="owner/repo")
    assert len(reads) == 2
    assert not any(method == "POST" for method, _path, _payload in calls)


def test_moving_main_after_pointer_create_never_posts_success_status():
    calls = []
    reads = []

    def github_api(method, path, payload=None):
        calls.append((method, path, payload))
        if method == "GET" and path == "/branches/main":
            reads.append(1)
            return {"commit": {"sha": COMMIT_SHA if len(reads) <= 2 else "b" * 40}}
        if method == "GET" and path.startswith("/deployments?"):
            return []
        if method == "POST" and path == "/deployments":
            return {"id": 4242}
        if method == "GET" and path == "/deployments/4242/statuses?per_page=100":
            return []
        raise AssertionError((method, path, payload))

    with pytest.raises(DeploymentPointerContractError, match="MAIN_SHA_STALE_OR_UNKNOWN"):
        reconcile_deployment_pointer(_advancing_receipt(), github_api, repository="owner/repo")
    assert len(reads) == 3
    assert any(method == "POST" and path == "/deployments" for method, path, _ in calls)
    assert not any(path.endswith("/statuses") and method == "POST" for method, path, _ in calls)


def test_unknown_main_lookup_response_is_typed_blocker_without_write():
    calls = []

    def github_api(method, path, payload=None):
        calls.append((method, path, payload))
        if method == "GET" and path == "/branches/main":
            return {"message": "temporarily unavailable"}
        raise AssertionError((method, path, payload))

    with pytest.raises(DeploymentPointerContractError, match="MAIN_SHA_STALE_OR_UNKNOWN"):
        reconcile_deployment_pointer(_advancing_receipt(), github_api, repository="owner/repo")
    assert not any(method == "POST" for method, _path, _payload in calls)
