"""Fail-closed, token-isolated GitHub mutation gateway contract (#1739).

This module does NOT grant credentials to providers. The trusted runner must own
both the database RPC adapter and GitHub App writer; all provider output is data.
No workflow is wired to this candidate until independent exact-SHA review.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol
from uuid import UUID

REPO = "gregoryharper84-ship-it/WOW-Dashboard"
SHA = re.compile(r"[0-9a-f]{40}\Z")
OWNER = re.compile(r"[A-Za-z0-9:._/-]{8,256}\Z")
GROUP = re.compile(r"(?:GLOBAL|P0_[A-Z0-9_]+)\Z")
OPERATIONS = frozenset({"CREATE_BRANCH", "PUSH_REF", "CREATE_PR"})


class FencingHold(RuntimeError):
    """Typed denial; do not retry an ambiguous GitHub mutation blindly."""


@dataclass(frozen=True)
class Intent:
    mutation_id: str
    incident_id: int
    owner_id: str
    epoch: int
    lease_group: str
    operation: str
    branch: str
    sha: str
    payload: str

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.payload.encode("utf-8")).hexdigest()

    def validate(self) -> None:
        try:
            UUID(self.mutation_id)
        except (ValueError, TypeError, AttributeError) as exc:
            raise FencingHold("MUTATION_ID_INVALID") from exc
        if (type(self.incident_id) is not int or self.incident_id <= 0
                or type(self.epoch) is not int or self.epoch < 1
                or not OWNER.fullmatch(self.owner_id)
                or not GROUP.fullmatch(self.lease_group)
                or self.operation not in OPERATIONS
                or not SHA.fullmatch(self.sha)
                or not isinstance(self.payload, str)):
            raise FencingHold("MUTATION_PROVENANCE_INVALID")
        prefix = f"wow-engineering/fenced-{self.incident_id}-{self.epoch}-"
        if (not self.branch.startswith(prefix)
                or not re.fullmatch(r"[a-z0-9-]{8,64}", self.branch[len(prefix):])
                or len(self.branch) > 150):
            raise FencingHold("MUTATION_BRANCH_UNBOUND")
        if self.operation in {"CREATE_BRANCH", "PUSH_REF"} and self.payload:
            raise FencingHold("CREATE_BRANCH_PAYLOAD_FORBIDDEN")
        if self.operation == "CREATE_PR" and not self.payload.strip():
            raise FencingHold("CREATE_PR_BODY_REQUIRED")


class LeaseStore(Protocol):
    def begin(self, intent: Intent) -> str:
        """Atomic DB RPC. STARTED, PENDING, CONFIRMED or DENIED.

        Must lock the canonical lease row; check exact owner/epoch/group and
        expiry; reserve a durable PENDING intent. Claims/releases/reclaims MUST
        be denied while any PENDING external mutation is unresolved.
        """

    def confirm(self, intent: Intent) -> bool:
        """DB CAS PENDING -> CONFIRMED, exact original digest/identity only."""


class GitHubWriter(Protocol):
    def create_branch(self, intent: Intent) -> None: ...
    def push_ref(self, intent: Intent) -> None: ...
    def create_pr(self, intent: Intent) -> None: ...
    def exact_readback(self, intent: Intent) -> bool:
        """Trust GitHub API exact immutable SHA, branch, PR body and identity."""


class Gateway:
    def __init__(self, store: LeaseStore, github: GitHubWriter):
        self.store, self.github = store, github

    def deliver(self, intent: Intent) -> str:
        intent.validate()
        # Durable begin is atomic; never a read-then-write observation.
        # This must run inside the trusted runner which alone holds the token.
        status = self.store.begin(intent)
        if status == "DENIED":
            raise FencingHold("LEASE_STALE_OR_CONFLICTING")
        if status not in {"STARTED", "PENDING", "CONFIRMED"}:
            raise FencingHold("LEASE_STORE_UNAVAILABLE")
        if status == "CONFIRMED":
            if not self.github.exact_readback(intent):
                raise FencingHold("CONFIRMED_GITHUB_DRIFT")
            return "ALREADY_CONFIRMED"
        if status == "STARTED":
            try:
                if intent.operation == "CREATE_BRANCH":
                    self.github.create_branch(intent)
                elif intent.operation == "PUSH_REF":
                    self.github.push_ref(intent)
                else:
                    self.github.create_pr(intent)
            except Exception as exc:
                # GitHub may have accepted the write before transport failed.
                # Never clear the PENDING reservation or repeat POST blindly.
                raise FencingHold("GITHUB_MUTATION_OUTCOME_UNKNOWN") from exc
        # PENDING means recovery of an earlier ambiguous write, NEVER retry.
        if not self.github.exact_readback(intent):
            raise FencingHold("GITHUB_MUTATION_RECONCILIATION_REQUIRED")
        if self.store.confirm(intent) is not True:
            raise FencingHold("MUTATION_CONFIRMATION_UNVERIFIED")
        return "EXACT_GITHUB_WRITE_CONFIRMED"


class SupabaseRpcStore:
    """Trusted-worker-only RPC adapter. The provider never receives this key."""

    def __init__(self, base_url: str, service_role_key: str, expected_project_id: str):
        from urllib.parse import urlsplit
        parsed = urlsplit(base_url)
        if (not re.fullmatch(r"[a-z]{20}", expected_project_id or "")
                or parsed.scheme != "https" or
                parsed.hostname != expected_project_id + ".supabase.co"
                or parsed.username or parsed.password or parsed.port or parsed.query
                or parsed.fragment or parsed.path not in ("", "/")
                or not service_role_key):
            raise FencingHold("LEASE_STORE_CONFIGURATION_INVALID")
        self.base_url = base_url.rstrip("/")
        self.key = service_role_key

    def _rpc(self, name: str, payload: dict):
        import json
        from urllib.request import Request, build_opener, HTTPRedirectHandler
        from urllib.error import URLError
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        path = f"{self.base_url}/rest/v1/rpc/{name}"
        req = Request(path, data=json.dumps(payload).encode("utf-8"),
                      headers={"apikey": self.key, "Authorization": "Bearer " + self.key,
                               "Content-Type": "application/json", "Accept": "application/json"},
                      method="POST")
        try:
            with build_opener(NoRedirect).open(req, timeout=15) as response:
                return json.load(response)
        except (OSError, ValueError, URLError) as exc:
            raise FencingHold("LEASE_STORE_UNAVAILABLE") from exc

    def begin(self, intent: Intent) -> str:
        value = self._rpc("wow_engineering_begin_writer_mutation", {
            "p_mutation_id": intent.mutation_id, "p_incident_id": intent.incident_id,
            "p_owner_id": intent.owner_id, "p_epoch": intent.epoch,
            "p_lease_group": intent.lease_group, "p_operation": intent.operation,
            "p_branch": intent.branch, "p_sha": intent.sha,
            "p_digest": intent.digest,
        })
        if not isinstance(value, dict) or value.get("status") not in {
                "STARTED", "PENDING", "CONFIRMED", "DENIED"}:
            raise FencingHold("LEASE_STORE_INVALID_RESPONSE")
        return value["status"]

    def confirm(self, intent: Intent) -> bool:
        value = self._rpc("wow_engineering_confirm_writer_mutation", {
            "p_mutation_id": intent.mutation_id, "p_incident_id": intent.incident_id,
            "p_owner_id": intent.owner_id, "p_epoch": intent.epoch,
            "p_digest": intent.digest,
        })
        return value is True


class GitHubAppWriter:
    """Single-purpose GitHub API writer in the protected runner, never an LLM tool."""

    def __init__(self, app_token: str):
        if not app_token:
            raise FencingHold("GITHUB_WRITER_TOKEN_MISSING")
        self.token = app_token

    def _call(self, method: str, path: str, payload: dict | None = None):
        import json
        from urllib.request import Request, build_opener, HTTPRedirectHandler
        from urllib.error import HTTPError, URLError
        if not path.startswith(f"/repos/{REPO}/") or ".." in path or "//" in path:
            raise FencingHold("GITHUB_MUTATION_PATH_INVALID")
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = Request("https://api.github.com" + path, data=data, method=method,
                      headers={"Authorization": "Bearer " + self.token,
                               "Accept": "application/vnd.github+json",
                               "Content-Type": "application/json",
                               "X-GitHub-Api-Version": "2022-11-28"})
        try:
            with build_opener(NoRedirect).open(req, timeout=15) as response:
                return json.load(response)
        except (HTTPError, URLError, OSError, ValueError) as exc:
            raise FencingHold("GITHUB_API_OUTCOME_UNKNOWN") from exc

    def create_branch(self, intent: Intent) -> None:
        result = self._call("POST", f"/repos/{REPO}/git/refs",
                            {"ref": "refs/heads/" + intent.branch, "sha": intent.sha})
        if (not isinstance(result, dict)
                or result.get("ref") != "refs/heads/" + intent.branch
                or result.get("object", {}).get("sha") != intent.sha):
            raise FencingHold("GITHUB_CREATE_BRANCH_ACK_INVALID")

    def push_ref(self, intent: Intent) -> None:
        # A newly created App-adoption commit does not yet exist on GitHub.
        # Only an exact, non-force push to the epoch-scoped source branch is permitted.
        if subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() != intent.sha:
            raise FencingHold("PUSH_REF_LOCAL_SHA_MISMATCH")
        origin = subprocess.check_output(["git", "remote", "get-url", "origin"], text=True).strip()
        if origin not in {f"https://github.com/{REPO}", f"https://github.com/{REPO}.git"}:
            raise FencingHold("PUSH_REF_REMOTE_UNTRUSTED")
        with tempfile.TemporaryDirectory(prefix="wow-fenced-push-") as temp:
            askpass = Path(temp) / "askpass"
            askpass.write_text('#!/bin/sh\ncase "$1" in *Username*) printf "x-access-token\\n";; *) printf "%s\\n" "$GIT_FENCED_TOKEN";; esac\n')
            askpass.chmod(0o700)
            env = {k:v for k,v in os.environ.items()
                   if not k.startswith(("GIT_CONFIG_", "GIT_ASKPASS", "GIT_SSH", "SSH_ASKPASS"))}
            env.update(GIT_ASKPASS=str(askpass), GIT_FENCED_TOKEN=self.token,
                       GIT_TERMINAL_PROMPT="0")
            subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "push",
                            "--porcelain", "origin", f"HEAD:refs/heads/{intent.branch}"],
                           check=True, capture_output=True, text=True, env=env, timeout=45)

    def create_pr(self, intent: Intent) -> None:
        result = self._call("POST", f"/repos/{REPO}/pulls", {
            "title": f"Fenced engineering source for incident #{intent.incident_id}",
            "head": intent.branch, "base": "main",
            "draft": True, "body": intent.payload,
        })
        if (not isinstance(result, dict) or not isinstance(result.get("number"), int)
                or result["number"] < 1 or result.get("head", {}).get("sha") != intent.sha):
            raise FencingHold("GITHUB_CREATE_PR_ACK_INVALID")

    def exact_readback(self, intent: Intent) -> bool:
        try:
            ref = self._call("GET", f"/repos/{REPO}/git/ref/heads/{intent.branch}")
            if (not isinstance(ref, dict)
                    or ref.get("ref") != "refs/heads/" + intent.branch
                    or ref.get("object", {}).get("sha") != intent.sha):
                return False
            if intent.operation in {"CREATE_BRANCH", "PUSH_REF"}:
                return True
            from urllib.parse import quote
            head = quote(f"{REPO.split('/')[0]}:{intent.branch}", safe=":")
            pulls = self._call("GET", f"/repos/{REPO}/pulls?head={head}&state=all&per_page=100")
            if not isinstance(pulls, list) or len(pulls) != 1:
                return False
            pr = pulls[0]
            return (pr.get("body") == intent.payload and
                    pr.get("head", {}).get("sha") == intent.sha and
                    pr.get("head", {}).get("ref") == intent.branch and
                    pr.get("head", {}).get("repo", {}).get("full_name") == REPO and
                    pr.get("base", {}).get("ref") == "main" and
                    pr.get("draft") is True and
                    pr.get("user", {}).get("login") == "wow-engineering[bot]")
        except FencingHold:
            return False
