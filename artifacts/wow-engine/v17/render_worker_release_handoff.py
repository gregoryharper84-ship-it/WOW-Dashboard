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
        return [v.get("deploy", v) for v in data]
    def check_main():
        actual = gh("/branches/main")["commit"]["sha"]
        return validated_target(actual, event_sha, os.environ["PRIMARY_RELEASE_RESULT"])
    check_main()
    current = list_deploys()
    state, deploy = choose_deploy(current, event_sha)
    if state == "ABSENT":
        check_main()
        result = request_json(base, render_token, "POST", {"clearCache": "do_not_clear"})
        deploy = result.get("deploy", result)
        if (deploy.get("commit") or {}).get("id") not in (None, event_sha):
            raise RuntimeError("WORKER_DEPLOY_CREATED_WRONG_SHA")
    deploy_id = str((deploy or {}).get("id") or "")
    if not deploy_id.startswith("dep-"):
        raise RuntimeError("WORKER_DEPLOY_ID_MISSING")
    until = time.monotonic() + 900
    while True:
        check_main()
        state, latest = choose_deploy(list_deploys(), event_sha)
        if state == "LIVE" and latest.get("id") == deploy_id:
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
