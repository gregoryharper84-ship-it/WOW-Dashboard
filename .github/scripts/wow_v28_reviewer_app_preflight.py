#!/usr/bin/env python3
"""Read-only bootstrap audit for the dedicated V28 reviewer App."""
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import Request, urlopen

APP_ID = 5765620
REPO = "gregoryharper84-ship-it/WOW-Dashboard"


class ReviewerConfigHold(ValueError):
    pass


def validate_installation(installations, repos):
    if len(installations) != 1 or len(repos) != 1:
        raise ReviewerConfigHold("REVIEWER_INSTALLATION_NOT_EXCLUSIVE")
    app = installations[0]
    scopes = app.get("permissions") or {}
    if (app.get("app_id") != APP_ID or app.get("repository_selection") != "selected"
            or app.get("account", {}).get("login") != "gregoryharper84-ship-it"
            or scopes.get("contents") != "read" or scopes.get("pull_requests") != "write"
            or any(k not in {"contents", "pull_requests", "metadata"} for k in scopes)
            or repos[0].get("full_name") != REPO):
        raise ReviewerConfigHold("REVIEWER_APP_SCOPE_INVALID")


def _b64(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _jwt(key):
    now = int(time.time())
    head = _b64(b'{"alg":"RS256","typ":"JWT"}')
    payload = _b64(json.dumps({"iat": now - 60, "exp": now + 480, "iss": APP_ID}).encode())
    message = head + "." + payload
    with tempfile.TemporaryDirectory(prefix="wow-app-") as tmp:
        name = Path(tmp) / "key"
        name.write_text(key)
        name.chmod(0o600)
        result = subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(name)],
                                input=message.encode(), capture_output=True, check=False)
        if result.returncode:
            raise ReviewerConfigHold("REVIEWER_APP_JWT_SIGN_FAILED")
    return message + "." + _b64(result.stdout)


def _get(path, token, payload=None):
    if not path.startswith("/app/") and path != "/installation/repositories?per_page=100":
        raise ReviewerConfigHold("REVIEWER_API_PATH_INVALID")
    data = None if payload is None else json.dumps(payload).encode()
    req = Request("https://api.github.com" + path, data=data, headers={
        "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
        "User-Agent": "wow-v28-reviewer-preflight",
    })
    with urlopen(req, timeout=20) as response:
        return json.loads(response.read(100000))


def run():
    key = os.getenv("WOW_REVIEWER_APP_PRIVATE_KEY") or ""
    if os.getenv("WOW_REVIEWER_APP_ID") != str(APP_ID) or not key:
        raise ReviewerConfigHold("REVIEWER_APP_CREDENTIAL_UNAVAILABLE")
    jwt = _jwt(key)
    installs = _get("/app/installations?per_page=100", jwt)
    if not isinstance(installs, list) or len(installs) != 1:
        raise ReviewerConfigHold("REVIEWER_INSTALLATION_NOT_EXCLUSIVE")
    iid = installs[0].get("id")
    if type(iid) is not int or iid <= 0:
        raise ReviewerConfigHold("REVIEWER_INSTALLATION_ID_INVALID")
    # Temporary downscoped token audits FULL installation repository reach.
    grant = _get("/app/installations/" + str(iid) + "/access_tokens",
                 jwt, {"permissions": {"contents": "read", "pull_requests": "read"}})
    token = grant.get("token") if isinstance(grant, dict) else None
    if not token:
        raise ReviewerConfigHold("REVIEWER_INSTALLATION_TOKEN_UNAVAILABLE")
    available = _get("/installation/repositories?per_page=100", token)
    if not isinstance(available, dict) or available.get("total_count") != 1:
        raise ReviewerConfigHold("REVIEWER_REPOSITORY_COUNT_INVALID")
    validate_installation(installs, available.get("repositories") or [])
    print("REVIEWER_APP_SCOPE_VERIFIED: ID=5765620, selected repo only; no tokens printed")


if __name__ == "__main__":
    try:
        if os.getenv("GITHUB_REF") != "refs/heads/main":
            raise ReviewerConfigHold("REVIEWER_MAIN_REQUIRED")
        run()
    except ReviewerConfigHold as exc:
        print("::error::" + str(exc), file=sys.stderr)
        raise SystemExit(1)
    except (OSError, ValueError, KeyError, TypeError):
        print("::error::REVIEWER_APP_PREFLIGHT_UNAVAILABLE", file=sys.stderr)
        raise SystemExit(1)
