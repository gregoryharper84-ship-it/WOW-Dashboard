"""Protected post-primary-release Render background-worker handoff.

This never approves a release or alters probabilities. Invoked ONLY after the
existing protected deploy-production job succeeds, using the same event SHA.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from render_worker_governed_initiator import initiate_once
from render_worker_lease_adapter import ALLOWED_SERVICE, DeploymentLeaseError, attest_release_control_identity, renew_committed, record_committed

WORKER_SERVICE = "srv-dapkbc0473hc73c1978g"
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
TERMINAL = {"build_failed", "update_failed", "pre_deploy_failed", "canceled", "cancelled", "deactivated"}

def validated_target(main_sha, event_sha, primary_success):
    if primary_success != "success":
        raise RuntimeError("WORKER_PRIMARY_RELEASE_NOT_SUCCESS")
    if not SHA_PATTERN.fullmatch(event_sha or "") or main_sha != event_sha:
        raise RuntimeError("WORKER_RELEASE_SHA_STALE_OR_INVALID")
    return event_sha

def choose_deploy(deploys, sha):
    matches = [d for d in deploys if (d.get("commit") or {}).get("id") == sha]
    for item in matches:
        if item.get("status") == "live":
            return "LIVE", item
    for item in matches:
        if item.get("status") in TERMINAL:
            raise RuntimeError("WORKER_EXACT_SHA_DEPLOY_FAILED:" + str(item["status"]))
    return ("ACTIVE", matches[0]) if matches else ("ABSENT", None)

def reconcile_intent(record, deployments, expected_sha):
    """Pure reconciliation: an uncertain POST is never authorization for another."""
    if not isinstance(record, dict):
        raise RuntimeError("WORKER_DEPLOY_INTENT_INVALID")
    if record.get("service_id") != WORKER_SERVICE or record.get("target_sha") != expected_sha:
        raise RuntimeError("WORKER_DEPLOY_INTENT_IDENTITY_MISMATCH")
    state = record.get("state")
    if state not in {"PENDING", "INITIATING", "ACK_AMBIGUOUS", "DEPLOYING", "LIVE_VERIFIED", "HOLD"}:
        raise RuntimeError("WORKER_DEPLOY_INTENT_STATE_INVALID")
    observed, deployment = choose_deploy(deployments, expected_sha)
    # These are provider observations, not permission to advance the
    # database lease. A separate owner+fence-adjudicated reconciliation must
    # persist the exact provider ID before calling anything LIVE_VERIFIED.
    if observed in {"LIVE", "ACTIVE"}:
        return "RECONCILIATION_REQUIRED", deployment
    if state in {"INITIATING", "ACK_AMBIGUOUS", "DEPLOYING", "LIVE_VERIFIED", "HOLD"}:
        return "RECONCILIATION_REQUIRED", None
    return "PENDING", None


def request_json(url, token, method="GET", payload=None):
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    body = None if payload is None else json.dumps(payload).encode()
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=20) as res:
        return json.load(res)

def run():
    github_token = os.environ["GH_API_TOKEN"]
    render_token = os.environ["RENDER_API_KEY"]
    repo = os.environ["GITHUB_REPOSITORY"]
    event_sha = os.environ["TARGET_MAIN_SHA"]
    receipt = os.path.join(os.environ["RUNNER_TEMP"], "wow-render-worker-deploy-receipt.json")
    gh = lambda path: request_json("https://api.github.com/repos/" + repo + path, github_token)
    base = "https://api.render.com/v1/services/" + WORKER_SERVICE + "/deploys"
    def list_deploys():
        data = request_json(base + "?limit=20", render_token)
        if not isinstance(data, list):
            raise RuntimeError("WORKER_DEPLOYS_RESPONSE_INVALID")
        results = []
        for row in data:
            if not isinstance(row, dict):
                raise RuntimeError("WORKER_DEPLOY_ENTRY_INVALID")
            item = row.get("deploy", row)
            if not isinstance(item, dict):
                raise RuntimeError("WORKER_DEPLOY_ENTRY_INVALID")
            results.append(item)
        return results
    def check_main():
        actual = gh("/branches/main")["commit"]["sha"]
        return validated_target(actual, event_sha, os.environ["PRIMARY_RELEASE_RESULT"])
    def post_exact_deploy():
        # Revalidate the protected revision immediately before the only POST.
        check_main()
        return request_json(
            base, render_token, method="POST",
            payload={"clearCache": "do_not_clear", "commitId": event_sha},
        )
    check_main()
    current = list_deploys()
    state, deploy = choose_deploy(current, event_sha)
    fence_token = None
    fence_dsn = None
    fence_owner = None
    if state == "ABSENT":
        dsn = os.environ.get("WOW_RELEASE_CONTROL_DSN", "")
        if not dsn:
            raise RuntimeError("WORKER_RELEASE_CONTROL_DSN_NOT_PROVISIONED")
        # Only the independently authorized protected workflow may supply this
        # private DB credential; connection must not be inferred or logged.
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("WORKER_PSYCO_PG_DRIVER_UNAVAILABLE") from exc
        with psycopg.connect(dsn, connect_timeout=10, autocommit=False) as conn:
            # A DSN is not authority: verify the independent production DB
            # identity and least-privilege approved login *before* any claim.
            attest_release_control_identity(
                conn,
                expected_project_ref=os.environ.get("WOW_RELEASE_CONTROL_PROJECT_REF", ""),
            )
            handoff = initiate_once(
                connection=conn, target_sha=event_sha,
                current_main_sha=check_main(), owner_run_id=os.environ["GITHUB_RUN_ID"],
                primary_release_result=os.environ["PRIMARY_RELEASE_RESULT"],
                list_deploys=list_deploys,
                post_deploy=post_exact_deploy,
            )
        if handoff.state != "DEPLOYING" or not handoff.deploy_id:
            raise RuntimeError("WORKER_DEPLOY_RECONCILIATION_REQUIRED")
        # Keep the issuance identity for subsequent renewal and durable LIVE
        # reconciliation. Without it, a 900-second poll can outlive a
        # 300-second database lease and falsely claim closure in a local file.
        if type(handoff.lease_token) is not int or handoff.lease_token < 1:
            raise RuntimeError("WORKER_DEPLOY_FENCING_TOKEN_MISSING")
        fence_token = handoff.lease_token
        fence_dsn = dsn
        fence_owner = os.environ["GITHUB_RUN_ID"]
        deploy_id = handoff.deploy_id
    else:
        # Provider observation is not release authority. An existing exact-SHA
        # deployment may have been created by another actor or previous run;
        # without a currently fenced DB intent this runner must not certify
        # it as LIVE_VERIFIED. Independent reconciler must adopt it first.
        raise RuntimeError("WORKER_EXISTING_DEPLOY_REQUIRES_FENCED_RECONCILIATION")
    if not deploy_id.startswith("dep-"):
        raise RuntimeError("WORKER_DEPLOY_ID_MISSING")
    # Production Render builds can exceed the original 15-minute budget.
    # Renew the fenced DB claim during bounded long observation; this is
    # strictly polling and never authorizes a repeated provider POST.
    until = time.monotonic() + 5400
    last_lease_renew = time.monotonic()
    while True:
        # The database owns authorization. Renew before TTL expiry rather
        # than pretending a provider-side poll preserves the fenced claim.
        if fence_token is not None and time.monotonic() - last_lease_renew >= 90:
            import psycopg
            with psycopg.connect(fence_dsn, connect_timeout=10, autocommit=False) as conn:
                renew_committed(
                    connection=conn, service_id=ALLOWED_SERVICE,
                    target_sha=event_sha, owner_run_id=fence_owner,
                    token=fence_token, lease_seconds=300,
                )
            last_lease_renew = time.monotonic()
        check_main()
        state, latest = choose_deploy(list_deploys(), event_sha)
        if state == "LIVE" and latest.get("id") == deploy_id:
            # Provider reads are non-atomic with GitHub main. An advance during
            # list_deploys must never be recorded as LIVE_VERIFIED.
            check_main()
            if fence_token is not None:
                # Never call a transient runner-file receipt durable evidence.
                # Persist the exact provider ID using the original fenced token.
                import psycopg
                with psycopg.connect(fence_dsn, connect_timeout=10, autocommit=False) as conn:
                    record_committed(
                        connection=conn, service_id=ALLOWED_SERVICE,
                        target_sha=event_sha, owner_run_id=fence_owner,
                        token=fence_token, state="LIVE_VERIFIED", deploy_id=deploy_id,
                    )
            result = {
                "status": "WORKER_EXACT_SHA_LIVE",
                "service_id": WORKER_SERVICE,
                "commit_sha": event_sha,
                "render_deploy_id": deploy_id,
                "can_execute": False,
                "primary_release_result": "success",
            }
            with open(receipt, "w", encoding="utf-8") as f:
                json.dump(result, f, sort_keys=True)
                f.write("\n")
            print(json.dumps(result, sort_keys=True))
            return
        if time.monotonic() >= until:
            raise RuntimeError("WORKER_EXACT_SHA_DEPLOY_TIMEOUT")
        time.sleep(10)

if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print("WORKER_DEPLOY_HOLD:" + str(exc), file=sys.stderr)
        sys.exit(1)
