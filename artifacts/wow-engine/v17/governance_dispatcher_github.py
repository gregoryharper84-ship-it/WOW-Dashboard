"""Protected-main GitHub transport for the V17 governance dispatcher."""

from __future__ import annotations

import importlib.util
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
import governance_dispatcher as policy  # noqa: E402

REPOSITORY = "gregoryharper84-ship-it/WOW-Dashboard"
OWNER_LOGIN = "gregoryharper84-ship-it"
WORKFLOWS = {
    "source_review": {
        "name": "wow-v17-independent-source-review",
        "path": ".github/workflows/wow-v17-independent-source-review.yml",
    },
    "qa": {
        "name": "wow-v17-independent-qa-check",
        "path": ".github/workflows/wow-v17-independent-qa-check.yml",
    },
    "release": {
        "name": "wow-v17-release-authority-check",
        "path": ".github/workflows/wow-v17-release-authority-check.yml",
    },
}
SIGNED_RESERVATION_PREFIX = "<!-- wow-v17-governance-dispatch-reservation-v2:"
DISPATCHER_WORKFLOW_PATH = ".github/workflows/wow-v17-governance-dispatcher.yml"
JOURNAL_KEY_ENV = "WOW_DISPATCH_JOURNAL_HMAC_KEY"
LEGACY_CERTIFIER_PATH = ".github/workflows/wow-v17-existing-pr-governance-review.yml"
LEGACY_CERTIFIER_ANCILLARY = {
    "wow-verify": ".github/workflows/wow-verify.yml",
    "wow-engine-verify": ".github/workflows/wow-engine-verify.yml",
    "wow-v17-rapid-repair": ".github/workflows/wow-v17-rapid-repair.yml",
    "wow-v17-change-impact-gate": ".github/workflows/wow-v17-change-impact-gate.yml",
    "wow-v17-engineering-auditor-code-health": ".github/workflows/wow-v17-engineering-auditor-code-health.yml",
    "wow-v17-spread-forward-shadow": ".github/workflows/wow-v17-spread-forward-shadow.yml",
    "wow-v17-release-production-verification-agent": ".github/workflows/wow-v17-release-production-verification-agent.yml",
}
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
JOURNAL_KEY_RE = re.compile(r"^[0-9a-fA-F]{64}$")
MAX_COMMENT_PAGES = 5
MAX_RUN_PAGES = 5
MAX_ARTIFACT_PAGES = 20
MAX_PULL_PAGES = 10
MAX_RETRIES = 3
TIMEOUT_SECONDS = 15
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
SOURCE_REVIEW_MAX_AGE = timedelta(minutes=30)


class DispatchError(RuntimeError):
    pass


class AmbiguousWrite(DispatchError):
    pass


class GitHub:
    def __init__(self, token: str):
        if not token:
            raise DispatchError("GITHUB_TOKEN_MISSING")
        self.token = token

    def request(self, method: str, path: str, body: dict | None = None) -> tuple[int, Any]:
        if not path.startswith("/") or "://" in path or "\n" in path:
            raise DispatchError("GITHUB_API_PATH_INVALID")
        encoded = None if body is None else json.dumps(body, separators=(",", ":")).encode()
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + self.token,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "wow-v17-governance-dispatcher",
        }
        if encoded is not None:
            headers["Content-Type"] = "application/json"
        request = Request(f"https://api.github.com{path}", data=encoded, headers=headers, method=method)
        for attempt in range(MAX_RETRIES if method == "GET" else 1):
            try:
                with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                    payload = response.read(10_000_001)
                    if len(payload) > 10_000_000:
                        raise DispatchError("GITHUB_API_RESPONSE_TOO_LARGE")
                    parsed = json.loads(payload) if payload else None
                    return response.status, parsed
            except HTTPError as exc:
                if method == "GET" and exc.code in RETRYABLE_STATUSES and attempt + 1 < MAX_RETRIES:
                    delay = min(5, max(1, int(exc.headers.get("Retry-After", "1"))))
                    time.sleep(delay)
                    continue
                return exc.code, None
            except (TimeoutError, URLError, OSError, json.JSONDecodeError) as exc:
                if method == "GET" and attempt + 1 < MAX_RETRIES:
                    time.sleep(1)
                    continue
                if method != "GET":
                    raise AmbiguousWrite("GITHUB_WRITE_RESULT_UNCERTAIN") from exc
                raise DispatchError("GITHUB_READ_FAILED") from exc
        raise DispatchError("GITHUB_READ_RETRIES_EXHAUSTED")

    def get(self, path: str) -> Any:
        status, body = self.request("GET", path)
        if status != 200:
            raise DispatchError(f"GITHUB_READ_FAILED:{status}")
        return body

    def post(self, path: str, body: dict) -> int:
        try:
            status, _ = self.request("POST", path, body)
        except AmbiguousWrite:
            raise
        if status not in {201, 202, 204}:
            raise DispatchError(f"GITHUB_WRITE_REJECTED:{status}")
        return status


def _paged(client: GitHub, path: str, key: str | None = None, max_pages: int = policy.MAX_API_PAGES) -> list:
    items: list = []
    separator = "&" if "?" in path else "?"
    for page in range(1, max_pages + 1):
        body = client.get(f"{path}{separator}per_page=100&page={page}")
        rows = body.get(key) if key and isinstance(body, dict) else body
        if not isinstance(rows, list):
            raise DispatchError("GITHUB_INVENTORY_INVALID")
        items.extend(rows)
        if len(rows) < 100:
            return items
    raise DispatchError("GITHUB_INVENTORY_TRUNCATED")


def list_workflows(client: GitHub) -> dict[str, dict]:
    result = {}
    for role, expected in WORKFLOWS.items():
        workflow = main_workflow_available(client, expected)
        if workflow:
            result[role] = workflow
    return result


def main_workflow_available(client: GitHub, expected: dict) -> dict | None:
    filename = Path(expected["path"]).name
    status, workflow = client.request(
        "GET",
        f"/repos/{REPOSITORY}/actions/workflows/{filename}",
    )
    if status == 404:
        return None
    if status != 200:
        raise DispatchError(f"GITHUB_READ_FAILED:{status}")
    if not isinstance(workflow, dict):
        raise DispatchError("GITHUB_WORKFLOW_INVENTORY_INVALID")
    if (
        workflow.get("path") == expected["path"]
        and workflow.get("name") == expected["name"]
        and workflow.get("state") == "active"
        and isinstance(workflow.get("id"), int)
        and not isinstance(workflow.get("id"), bool)
    ):
        return workflow
    return None


def list_pulls(client: GitHub) -> list[dict]:
    return _paged(
        client,
        f"/repos/{REPOSITORY}/pulls?state=open&base=main",
        max_pages=MAX_PULL_PAGES,
    )


def pull_files(client: GitHub, number: int, changed_files: int) -> list[dict]:
    rows = _paged(
        client,
        f"/repos/{REPOSITORY}/pulls/{number}/files",
        max_pages=MAX_PULL_PAGES,
    )
    if len(rows) != changed_files:
        raise DispatchError("PR_FILE_INVENTORY_INCOMPLETE")
    return rows


def check_runs(client: GitHub, head_sha: str) -> list[dict]:
    return _paged(
        client,
        f"/repos/{REPOSITORY}/commits/{head_sha}/check-runs",
        "check_runs",
    )


def list_role_runs(client: GitHub, workflow_id: int, role: str, pr_number: int, head_sha: str) -> list[dict]:
    rows = _paged(
        client,
        f"/repos/{REPOSITORY}/actions/workflows/{workflow_id}/runs?branch=main",
        "workflow_runs",
        MAX_RUN_PAGES,
    )
    expected_name = policy.run_name(role, pr_number, head_sha)
    found = []
    for run in rows:
        if not isinstance(run, dict):
            continue
        run_names = {run.get("name"), run.get("display_title")}
        if (
            expected_name in run_names
            and run.get("path") == WORKFLOWS[role]["path"]
            and (
                run.get("event") in {"workflow_dispatch", "workflow_run"}
                if role == "source_review"
                else run.get("event") == "workflow_dispatch"
            )
            and run.get("head_branch") == "main"
            and (run.get("head_repository") or {}).get("full_name") == REPOSITORY
            and SHA_RE.fullmatch(str(run.get("head_sha") or ""))
        ):
            found.append(run)
    return found


def matching_role_runs(client: GitHub, workflows: dict[str, dict], pr_number: int, head_sha: str) -> dict[str, dict]:
    result = {}
    for role, workflow in workflows.items():
        rows = list_role_runs(client, workflow["id"], role, pr_number, head_sha)
        if role == "source_review":
            active = [row for row in rows if row.get("status") != "completed"]
            if len(active) > 1:
                raise DispatchError(f"DUPLICATE_ACTIVE_ROLE_RUNS:{role}")
            rows.sort(key=lambda row: str(row.get("created_at") or ""))
            result[role] = {"history": rows, "latest": active[0] if active else (rows[-1] if rows else None)}
            continue
        if len(rows) > 1:
            raise DispatchError(f"DUPLICATE_ROLE_RUNS:{role}")
        if rows:
            result[role] = rows[0]
    return result


def role_run_statuses(runs: dict[str, dict]) -> dict[str, str]:
    result = {}
    for role, run in runs.items():
        if role == "source_review":
            run = run.get("latest")
        if run:
            result[role] = str(run.get("conclusion") or run.get("status") or "")
    return result


def count_wip(client: GitHub, workflows: dict[str, dict]) -> dict[str, int]:
    active = {role: 0 for role in WORKFLOWS}
    for role, workflow in workflows.items():
        rows = _paged(
            client,
            f"/repos/{REPOSITORY}/actions/workflows/{workflow['id']}/runs?branch=main",
            "workflow_runs",
            MAX_RUN_PAGES,
        )
        active[role] += sum(
            1 for run in rows
            if isinstance(run, dict) and run.get("status") in {"queued", "in_progress", "waiting", "pending", "requested"}
        )
    return active


def legacy_certifier_ancillary_hold(client: GitHub, head_sha: str, pr_number: int) -> str | None:
    rows = _paged(
        client,
        f"/repos/{REPOSITORY}/actions/runs?head_sha={head_sha}",
        "workflow_runs",
        MAX_RUN_PAGES,
    )
    matching: dict[str, list[dict]] = {}
    known_paths = set(LEGACY_CERTIFIER_ANCILLARY.values()) | {LEGACY_CERTIFIER_PATH}
    for run in rows:
        if (
            not isinstance(run, dict)
            or run.get("path") not in known_paths
            or run.get("head_sha") != head_sha
            or ((run.get("head_repository") or {}).get("full_name")) != REPOSITORY
            or not isinstance(run.get("pull_requests"), list)
            or run.get("event") not in {"pull_request", "pull_request_target"}
            or not any(
                pull == pr_number
                or (
                    isinstance(pull, dict)
                    and pull.get("number") == pr_number
                    and ((pull.get("head") or {}).get("sha")) == head_sha
                )
                for pull in run.get("pull_requests", [])
            )
        ):
            continue
        matching.setdefault(run["path"], []).append(run)
    latest = {
        path: max(
            path_runs,
            key=lambda run: (
                str(run.get("created_at") or ""),
                int(run.get("id") or 0),
            ),
        )
        for path, path_runs in matching.items()
    }
    certifier = latest.get(LEGACY_CERTIFIER_PATH)
    if (
        not certifier
        or certifier.get("status") != "completed"
        or certifier.get("conclusion") != "failure"
        or certifier.get("event") != "pull_request_target"
    ):
        return None
    for workflow, path in LEGACY_CERTIFIER_ANCILLARY.items():
        run = latest.get(path)
        if (
            not run
            or run.get("status") != "completed"
            or run.get("conclusion") != "action_required"
            or run.get("event") != "pull_request"
            or not isinstance(run.get("id"), int)
            or isinstance(run.get("id"), bool)
        ):
            continue
        jobs = client.get(f"/repos/{REPOSITORY}/actions/runs/{run['id']}/jobs?per_page=100&page=1")
        if (
            isinstance(jobs, dict)
            and jobs.get("total_count") == 0
            and jobs.get("jobs") == []
        ):
            return (
                "ANCILLARY_CI_EVENT_POLICY_HOLD:"
                f"workflow={workflow}:run_id={run['id']}:"
                "owner_action=review_protected_certifier_failure"
            )
    return None


def _source_review_module() -> Any | None:
    path = Path(__file__).resolve().parents[3] / ".github/scripts/independent_source_review_receipt.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("wow_dispatch_source_review_receipt", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def source_review_status(
    client: GitHub,
    *,
    module: Any | None,
    pr: dict,
    files: list[dict],
    source_runs: list[dict] | None = None,
) -> str:
    if module is None:
        return "INVALID"
    if any(run.get("status") != "completed" for run in source_runs or [] if isinstance(run, dict)):
        return "PENDING"
    paths = [row["filename"] for row in files if isinstance(row, dict) and isinstance(row.get("filename"), str)]
    artifact_pages: dict[str, Any] = {}

    def get_json(path: str) -> Any:
        if not path.startswith("/actions/"):
            raise DispatchError("SOURCE_REVIEW_API_PATH_INVALID")
        body = client.get(f"/repos/{REPOSITORY}{path}")
        if path.startswith("/actions/artifacts?"):
            artifact_pages[path] = body
        return body

    try:
        module.resolve_current_source_review(
            repository=REPOSITORY,
            pr_number=pr["number"],
            head_sha=(pr.get("head") or {}).get("sha") or "",
            head_ref=(pr.get("head") or {}).get("ref") or "",
            token=client.token,
            expected_file_paths=paths,
            get_json=get_json,
        )
        return "PASS"
    except module.SourceReviewError as exc:
        text = str(exc)
        if text == "SOURCE_REVIEW_ARTIFACT_DUPLICATE_OR_MISSING":
            prefix = f"wow-v17-independent-source-review-{pr['number']}-{(pr.get('head') or {}).get('sha')}-"
            if artifact_pages:
                artifacts = []
                for path, body in sorted(artifact_pages.items()):
                    rows = body.get("artifacts") if isinstance(body, dict) else None
                    if not isinstance(rows, list):
                        return "INVALID"
                    artifacts.extend(rows)
            else:
                artifacts = _paged(
                    client,
                    f"/repos/{REPOSITORY}/actions/artifacts",
                    "artifacts",
                    MAX_ARTIFACT_PAGES,
                )
            matching = [
                item for item in artifacts
                if isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"].startswith(prefix)
            ]
            if len(matching) > 1:
                return "INVALID"
            runs_by_id = {
                str(run.get("id")): run
                for run in source_runs or []
                if isinstance(run, dict) and run.get("id") is not None
            }
            if not matching:
                latest_run = max(
                    runs_by_id.values(),
                    key=lambda run: str(run.get("created_at") or ""),
                    default=None,
                )
                if latest_run is None:
                    return "MISSING"
                if (
                    latest_run.get("status") == "completed"
                    and latest_run.get("conclusion") == "action_required"
                    and isinstance(latest_run.get("id"), int)
                    and not isinstance(latest_run.get("id"), bool)
                    and latest_run.get("path") == WORKFLOWS["source_review"]["path"]
                    and latest_run.get("head_branch") == "main"
                    and (latest_run.get("head_repository") or {}).get("full_name") == REPOSITORY
                    and latest_run.get("event") in {"workflow_dispatch", "workflow_run"}
                    and policy.run_name("source_review", pr["number"], (pr.get("head") or {}).get("sha") or "")
                    in {latest_run.get("name"), latest_run.get("display_title")}
                    and isinstance(latest_run.get("run_attempt"), int)
                    and not isinstance(latest_run.get("run_attempt"), bool)
                    and latest_run.get("run_attempt") > 0
                ):
                    jobs = client.get(
                        f"/repos/{REPOSITORY}/actions/runs/{latest_run['id']}/jobs"
                        "?per_page=100&page=1"
                    )
                    if (
                        isinstance(jobs, dict)
                        and jobs.get("total_count") == 0
                        and jobs.get("jobs") == []
                    ):
                        return "ACTION_REQUIRED_ZERO_JOBS"
                return "INVALID"
            now = datetime.now(timezone.utc)
            stale = True
            for item in matching:
                name = item.get("name") or ""
                suffix = name[len(prefix):]
                match = re.fullmatch(r"([1-9][0-9]*)-([1-9][0-9]*)", suffix)
                created_at = item.get("created_at")
                run = runs_by_id.get(match.group(1)) if match else None
                if (
                    match is None
                    or not isinstance(item.get("id"), int)
                    or isinstance(item.get("id"), bool)
                    or item["id"] < 1
                    or run is None
                    or (item.get("workflow_run") or {}).get("id") != int(match.group(1))
                    or run.get("run_attempt") != int(match.group(2))
                    or run.get("status") != "completed"
                    or run.get("conclusion") != "success"
                    or not isinstance(item.get("expired"), bool)
                    or not isinstance(created_at, str)
                ):
                    return "INVALID"
                try:
                    created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                except ValueError:
                    return "INVALID"
                if created.tzinfo is None or created > now:
                    return "INVALID"
                if not item["expired"] and now < created.astimezone(timezone.utc) + SOURCE_REVIEW_MAX_AGE:
                    stale = False
            return "STALE" if stale else "INVALID"
        return "INVALID"
    except DispatchError:
        raise
    except Exception:
        return "INVALID"


def pull_reviews(client: GitHub, number: int) -> list[dict]:
    return _paged(client, f"/repos/{REPOSITORY}/pulls/{number}/reviews", max_pages=MAX_COMMENT_PAGES)


def _journal_key(value: str | None = None) -> bytes:
    raw = os.getenv(JOURNAL_KEY_ENV, "") if value is None else value
    if not isinstance(raw, str) or not JOURNAL_KEY_RE.fullmatch(raw):
        raise DispatchError("DISPATCH_JOURNAL_KEY_MISSING_OR_INVALID")
    return bytes.fromhex(raw)


def _canonical_payload(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _signed_reservation(value: dict, key: bytes) -> str:
    payload = base64.urlsafe_b64encode(_canonical_payload(value)).decode("ascii").rstrip("=")
    signature = hmac.new(key, payload.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{SIGNED_RESERVATION_PREFIX}{payload}.{signature} -->"


def _verified_reservation(client: GitHub, marker: str, key: bytes) -> dict | None:
    encoded, separator, signature = marker.partition(".")
    if (
        not separator
        or not re.fullmatch(r"[A-Za-z0-9_-]+", encoded)
        or not re.fullmatch(r"[0-9a-f]{64}", signature)
    ):
        return None
    expected = hmac.new(key, encoded.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        value = json.loads(payload)
    except (ValueError, json.JSONDecodeError):
        raise DispatchError("DISPATCH_JOURNAL_PAYLOAD_INVALID")
    if not isinstance(value, dict) or _canonical_payload(value) != payload:
        raise DispatchError("DISPATCH_JOURNAL_PAYLOAD_NOT_CANONICAL")
    if set(value) != {"schema", "pr_number", "head_sha", "role", "run_id", "attempt"}:
        raise DispatchError("DISPATCH_JOURNAL_SCHEMA_INVALID")
    if value.get("schema") != 2 or value.get("role") not in WORKFLOWS:
        raise DispatchError("DISPATCH_JOURNAL_SCHEMA_INVALID")
    if (
        not isinstance(value.get("pr_number"), int)
        or isinstance(value.get("pr_number"), bool)
        or value["pr_number"] < 1
        or not SHA_RE.fullmatch(str(value.get("head_sha") or ""))
        or not isinstance(value.get("run_id"), str)
        or not re.fullmatch(r"[1-9][0-9]*", value.get("run_id", ""))
        or not isinstance(value.get("attempt"), int)
        or isinstance(value.get("attempt"), bool)
        or value["attempt"] < 1
    ):
        raise DispatchError("DISPATCH_JOURNAL_IDENTITY_INVALID")

    run = client.get(f"/repos/{REPOSITORY}/actions/runs/{value['run_id']}")
    if (
        not isinstance(run, dict)
        or str(run.get("id")) != value["run_id"]
        or run.get("path") != DISPATCHER_WORKFLOW_PATH
        or ((run.get("repository") or {}).get("full_name")) != REPOSITORY
        or run.get("head_branch") != "main"
        or run.get("event") not in {"schedule", "workflow_dispatch"}
        or not isinstance(run.get("run_attempt"), int)
        or isinstance(run.get("run_attempt"), bool)
        or run.get("run_attempt") != value["attempt"]
    ):
        raise DispatchError("DISPATCH_JOURNAL_PROVENANCE_INVALID")
    return value


def list_reservations(client: GitHub, number: int) -> list[dict]:
    key = _journal_key()
    rows = _paged(
        client,
        f"/repos/{REPOSITORY}/issues/{number}/comments",
        max_pages=MAX_COMMENT_PAGES,
    )
    reservations = []
    verified_runs: set[tuple[str, int, int, str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        user = row.get("user") or {}
        body = row.get("body")
        if not isinstance(body, str) or not body.startswith(SIGNED_RESERVATION_PREFIX) or not body.endswith("-->"):
            continue
        marker = body[len(SIGNED_RESERVATION_PREFIX):-3].strip()
        try:
            value = _verified_reservation(client, marker, key)
        except DispatchError:
            raise
        if value is None or user.get("login") != "github-actions[bot]" or user.get("type") != "Bot":
            continue
        if value["pr_number"] != number:
            continue
        run_identity = (
            value["run_id"],
            value["attempt"],
            value["pr_number"],
            value["head_sha"],
            value["role"],
        )
        if run_identity in verified_runs:
            continue
        verified_runs.add(run_identity)
        reservations.append(value)
    return reservations


def reservation_attempts(reservations: list[dict], role: str, pr_number: int, head_sha: str) -> list[int]:
    matching = [
        (row["run_id"], row["attempt"])
        for row in reservations
        if row.get("role") == role
        and row.get("pr_number") == pr_number
        and row.get("head_sha") == head_sha
    ]
    if len(matching) != len(set(matching)):
        raise DispatchError("DISPATCH_JOURNAL_DUPLICATE_ATTEMPT")
    return list(range(1, len(matching) + 1))


def next_dispatch_attempt(
    role: str,
    reviewer_status: str,
    role_statuses: dict[str, str],
    previous_attempts: list[int],
) -> int:
    if not previous_attempts:
        return 1
    if (
        role == "source_review"
        and reviewer_status == "STALE"
        and role_statuses.get("source_review") == "success"
        and previous_attempts == [1]
    ):
        return 2
    if role == "source_review" and max(previous_attempts) >= 2:
        raise DispatchError("DISPATCH_RETRY_LIMIT_REACHED")
    raise DispatchError("DISPATCH_RESERVATION_UNCERTAIN")


def reserve_dispatch(
    client: GitHub,
    *,
    role: str,
    pr_number: int,
    head_sha: str,
    controller_run_id: str,
    attempt: int = 1,
) -> None:
    reservation = {
        "schema": 2,
        "role": role,
        "pr_number": pr_number,
        "head_sha": head_sha,
        "run_id": controller_run_id,
        "attempt": attempt,
    }
    body = _signed_reservation(reservation, _journal_key())
    client.post(f"/repos/{REPOSITORY}/issues/{pr_number}/comments", {"body": body})


def dispatch(client: GitHub, role: str, workflow: dict, pr_number: int, head_sha: str, *,
             qa_run_id: int | None = None, qa_run_attempt: int | None = None) -> None:
    if role not in WORKFLOWS or workflow.get("path") != WORKFLOWS[role]["path"]:
        raise DispatchError("DISPATCH_TARGET_NOT_ALLOWLISTED")
    inputs = {"pr_number": str(pr_number), "head_sha": head_sha}
    if role == "release":
        if (
            not isinstance(qa_run_id, int) or isinstance(qa_run_id, bool) or qa_run_id <= 0
            or not isinstance(qa_run_attempt, int) or isinstance(qa_run_attempt, bool)
            or qa_run_attempt <= 0
        ):
            raise DispatchError("RELEASE_QA_RUN_IDENTITY_MISSING")
        inputs.update({"qa_run_id": str(qa_run_id), "qa_run_attempt": str(qa_run_attempt)})
    client.post(
        f"/repos/{REPOSITORY}/actions/workflows/{workflow['id']}/dispatches",
        {"ref": "main", "inputs": inputs},
    )


def _owner(client: GitHub) -> tuple[int, str]:
    repo = client.get(f"/repos/{REPOSITORY}")
    owner = repo.get("owner") if isinstance(repo, dict) else None
    if not isinstance(owner, dict) or owner.get("login") != OWNER_LOGIN or not isinstance(owner.get("id"), int):
        raise DispatchError("REPOSITORY_OWNER_UNTRUSTED")
    return owner["id"], owner["login"]


def _target_pull(client: GitHub, number: int, expected_head: str | None = None) -> dict:
    pr = client.get(f"/repos/{REPOSITORY}/pulls/{number}")
    head = (pr.get("head") or {}).get("sha") if isinstance(pr, dict) else None
    if expected_head and head != expected_head:
        raise DispatchError("PR_HEAD_MOVED_DURING_RECONCILIATION")
    return pr


def _target_list(client: GitHub, input_pr: str | None, run_number: int) -> list[dict]:
    if input_pr:
        if not re.fullmatch(r"[1-9][0-9]*", input_pr):
            raise DispatchError("INPUT_PR_NUMBER_INVALID")
        return [_target_pull(client, int(input_pr))]
    pulls = list_pulls(client)
    return policy.select_targets(pulls, run_number)


def _role_run_state(runs: dict[str, dict], role: str) -> str | None:
    run = runs.get(role)
    if not run:
        return None
    return str(run.get("conclusion") or run.get("status") or "")


def required_evidence_ready(check_runs_for_head: list[dict], head_sha: str) -> bool:
    for name in policy.QA_EVIDENCE_CHECKS:
        check = policy.latest_check(
            check_runs_for_head,
            name,
            head_sha,
            policy.GITHUB_ACTIONS_APP_ID,
        )
        if (
            check is None
            or check.get("_foreign_source")
            or check.get("status") != "completed"
            or check.get("conclusion") != "success"
        ):
            return False
    return True


def reconcile(
    *,
    client: GitHub,
    input_pr: str | None,
    run_number: int,
    controller_run_id: str,
    controller_run_attempt: int = 1,
    source_sha: str,
    zero_credit_mode: bool = False,
) -> dict[str, Any]:
    if not SHA_RE.fullmatch(source_sha):
        raise DispatchError("CONTROLLER_SOURCE_SHA_INVALID")
    if (
        not isinstance(controller_run_id, str)
        or not re.fullmatch(r"[1-9][0-9]*", controller_run_id)
    ):
        raise DispatchError("CONTROLLER_RUN_ID_INVALID")
    if (
        not isinstance(controller_run_attempt, int)
        or isinstance(controller_run_attempt, bool)
        or controller_run_attempt < 1
    ):
        raise DispatchError("CONTROLLER_RUN_ATTEMPT_INVALID")
    _journal_key()
    if not isinstance(zero_credit_mode, bool):
        raise DispatchError("ZERO_CREDIT_POLICY_INVALID")
    owner_id, owner_login = _owner(client)
    workflows = list_workflows(client)
    wip_by_role = count_wip(client, workflows) if workflows else {role: 0 for role in WORKFLOWS}
    wip_count = sum(wip_by_role.values())
    module = _source_review_module()
    targets = _target_list(client, input_pr, run_number)
    decisions = []
    dispatches = 0

    for listed in targets:
        number = listed.get("number")
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            decisions.append({"pr_number": None, "action": "HOLD", "findings": ["PR_NUMBER_INVALID"]})
            continue
        try:
            listed_sha = ((listed.get("head") or {}).get("sha") or "")
            pr = _target_pull(client, number, listed_sha or None)
            head_sha = (pr.get("head") or {}).get("sha") or ""
            if not SHA_RE.fullmatch(head_sha):
                raise DispatchError("PR_HEAD_INVALID")
            files = pull_files(client, number, pr.get("changed_files"))
            checks = check_runs(client, head_sha)
            reviews = pull_reviews(client, number)
            role_runs = matching_role_runs(client, workflows, number, head_sha)
            statuses = role_run_statuses(role_runs)
            available_roles = set(workflows)

            target_evidence = {
                "repository": REPOSITORY,
                "owner_id": owner_id,
                "owner_login": owner_login,
                "pull": pr,
                "file_records": files,
                "check_runs": checks,
                "reviews": reviews,
                "ancillary_ci_hold": None,
                "available_roles": available_roles,
                "completed_role_runs": statuses,
                "zero_credit_mode": zero_credit_mode,
            }
            preflight = policy.classify_target(
                **target_evidence,
                source_review="PENDING",
            )
            if preflight.get("findings") == ["SOURCE_REVIEW_PENDING"]:
                source_history = (role_runs.get("source_review") or {}).get("history", [])
                reviewer_status = source_review_status(
                    client,
                    module=module,
                    pr=pr,
                    files=files,
                    source_runs=source_history,
                )
                if reviewer_status == "PASS" and required_evidence_ready(checks, head_sha):
                    target_evidence["ancillary_ci_hold"] = legacy_certifier_ancillary_hold(
                        client,
                        head_sha,
                        number,
                    )
                decision = policy.classify_target(
                    **target_evidence,
                    source_review=reviewer_status,
                )
            else:
                decision = preflight
                reviewer_status = "PENDING"
            decision["pr_number"] = number
            if decision["action"] != "DISPATCH":
                decisions.append(decision)
                continue
            role = decision["role"]
            qa_source = (role_runs.get("qa") or {}) if role == "release" else {}
            if role == "release" and (
                not isinstance(qa_source, dict)
                or qa_source.get("status") != "completed"
                or qa_source.get("conclusion") != "success"
                or not isinstance(qa_source.get("id"), int)
                or not isinstance(qa_source.get("run_attempt"), int)
            ):
                decisions.append({
                    "pr_number": number,
                    "action": "HOLD",
                    "head_sha": head_sha,
                    "findings": ["RELEASE_QA_RUN_PROVENANCE_MISSING"],
                })
                continue
            reservations = list_reservations(client, number)
            prior_attempts = reservation_attempts(reservations, role, number, head_sha)
            try:
                attempt = next_dispatch_attempt(role, reviewer_status, statuses, prior_attempts)
            except DispatchError as exc:
                decisions.append({
                    "pr_number": number,
                    "action": "HOLD",
                    "head_sha": head_sha,
                    "findings": [str(exc)],
                })
                continue
            if wip_count >= policy.MAX_WIP:
                decisions.append({
                    "pr_number": number,
                    "action": "HOLD",
                    "head_sha": head_sha,
                    "findings": ["WIP_LIMIT_REACHED"],
                })
                continue
            if wip_by_role.get(role, 0) > 0:
                decisions.append({
                    "pr_number": number,
                    "action": "HOLD",
                    "head_sha": head_sha,
                    "findings": ["ROLE_WIP_LIMIT_REACHED"],
                })
                continue
            if dispatches >= policy.MAX_DISPATCHES_PER_RUN:
                decisions.append({
                    "pr_number": number,
                    "action": "HOLD",
                    "head_sha": head_sha,
                    "findings": ["DISPATCH_BUDGET_EXHAUSTED"],
                })
                continue
            if role not in workflows:
                decisions.append({
                    "pr_number": number,
                    "action": "HOLD",
                    "head_sha": head_sha,
                    "findings": ["DISPATCH_WORKFLOW_NOT_ALLOWLISTED_ON_MAIN"],
                })
                continue

            reserve_dispatch(
                client,
                role=role,
                pr_number=number,
                head_sha=head_sha,
                controller_run_id=controller_run_id,
                attempt=controller_run_attempt,
            )
            dispatch(
                client, role, workflows[role], number, head_sha,
                qa_run_id=qa_source.get("id"), qa_run_attempt=qa_source.get("run_attempt"),
            )
            dispatches += 1
            wip_count += 1
            wip_by_role[role] = wip_by_role.get(role, 0) + 1
            decisions.append({
                "pr_number": number,
                "action": "DISPATCHED",
                "role": role,
                "head_sha": head_sha,
                "ref": "main",
                "can_execute": False,
            })
        except AmbiguousWrite as exc:
            decisions.append({
                "pr_number": number,
                "action": "HOLD",
                "findings": [str(exc)],
            })
        except DispatchError as exc:
            decisions.append({
                "pr_number": number,
                "action": "HOLD",
                "findings": [str(exc)],
            })
        except Exception:
            decisions.append({
                "pr_number": number,
                "action": "HOLD",
                "findings": ["RECONCILIATION_UNEXPECTED_ERROR"],
            })

    return {
        "repository": REPOSITORY,
        "source_ref": "main",
        "source_sha": source_sha,
        "dispatch_count": dispatches,
        "decisions": decisions,
        "can_execute": False,
        "merge_or_release_authorized": False,
    }


def main() -> int:
    if os.getenv("GITHUB_REPOSITORY") != REPOSITORY or os.getenv("GITHUB_REF") != "refs/heads/main":
        print(json.dumps({"action": "HOLD", "finding": "CONTROLLER_SOURCE_NOT_PROTECTED_MAIN"}))
        return 2
    if os.getenv("GITHUB_EVENT_NAME") not in {"schedule", "workflow_dispatch"}:
        print(json.dumps({"action": "HOLD", "finding": "CONTROLLER_EVENT_NOT_ALLOWLISTED"}))
        return 2
    try:
        _journal_key()
    except DispatchError as exc:
        print(json.dumps({"action": "HOLD", "finding": str(exc), "can_execute": False}))
        return 2
    token = os.getenv("GH_TOKEN", "")
    run_number = os.getenv("GITHUB_RUN_NUMBER", "")
    run_id = os.getenv("GITHUB_RUN_ID", "")
    run_attempt = os.getenv("GITHUB_RUN_ATTEMPT", "")
    source_sha = os.getenv("GITHUB_SHA", "")
    input_pr = os.getenv("INPUT_PR_NUMBER", "").strip() or None
    zero_credit_setting = os.getenv("WOW_QA_ZERO_CREDIT_MODE", "")
    paid_review_setting = os.getenv("WOW_QA_INDEPENDENT_REVIEW_ACTIVE", "")
    if zero_credit_setting not in {"", "false", "true"} or paid_review_setting not in {"", "false", "true"}:
        print(json.dumps({"action": "HOLD", "finding": "REVIEW_POLICY_SETTING_INVALID", "can_execute": False}))
        return 2
    if zero_credit_setting == "true" and paid_review_setting == "true":
        print(json.dumps({"action": "HOLD", "finding": "REVIEW_POLICY_MODES_CONFLICT", "can_execute": False}))
        return 2
    if not run_number.isdigit() or not run_id.isdigit() or not run_attempt.isdigit():
        print(json.dumps({"action": "HOLD", "finding": "CONTROLLER_RUN_IDENTITY_INVALID"}))
        return 2
    try:
        result = reconcile(
            client=GitHub(token),
            input_pr=input_pr,
            run_number=int(run_number),
            controller_run_id=run_id,
            controller_run_attempt=int(run_attempt),
            source_sha=source_sha,
            zero_credit_mode=(zero_credit_setting == "true"),
        )
    except DispatchError as exc:
        print(json.dumps({"action": "HOLD", "finding": str(exc), "can_execute": False}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
