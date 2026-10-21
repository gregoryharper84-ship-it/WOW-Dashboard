"""Protected-main GitHub transport for the V17 governance dispatcher."""

from __future__ import annotations

import importlib.util
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
RESERVATION_PREFIX = "<!-- wow-v17-governance-dispatch-reservation-v1:"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
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
    rows = _paged(client, "/repos/{}/actions/workflows".format(REPOSITORY), "workflows")
    by_path = {row.get("path"): row for row in rows if isinstance(row, dict)}
    result = {}
    for role, expected in WORKFLOWS.items():
        workflow = by_path.get(expected["path"])
        if (
            isinstance(workflow, dict)
            and workflow.get("name") == expected["name"]
            and workflow.get("state") == "active"
            and isinstance(workflow.get("id"), int)
        ):
            result[role] = workflow
    return result


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
                return "MISSING" if not runs_by_id else "INVALID"
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
    except Exception:
        return "INVALID"


def pull_reviews(client: GitHub, number: int) -> list[dict]:
    return _paged(client, f"/repos/{REPOSITORY}/pulls/{number}/reviews", max_pages=MAX_COMMENT_PAGES)


def list_reservations(client: GitHub, number: int) -> list[dict]:
    rows = _paged(
        client,
        f"/repos/{REPOSITORY}/issues/{number}/comments",
        max_pages=MAX_COMMENT_PAGES,
    )
    reservations = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        user = row.get("user") or {}
        body = row.get("body")
        if (
            user.get("login") != "github-actions[bot]"
            or user.get("type") != "Bot"
            or not isinstance(body, str)
            or not body.startswith(RESERVATION_PREFIX)
            or not body.endswith("-->")
        ):
            continue
        try:
            value = json.loads(body[len(RESERVATION_PREFIX):-3].strip())
        except json.JSONDecodeError:
            raise DispatchError("DISPATCH_JOURNAL_INVALID")
        required = {
            "schema_version", "role", "pr_number", "head_sha", "workflow_path",
            "controller_run_id", "attempt",
        }
        if not isinstance(value, dict) or set(value) != required or value.get("schema_version") != 1:
            raise DispatchError("DISPATCH_JOURNAL_SCHEMA_INVALID")
        role = value.get("role")
        if role not in WORKFLOWS or value.get("workflow_path") != WORKFLOWS[role]["path"]:
            raise DispatchError("DISPATCH_JOURNAL_ROLE_INVALID")
        if (
            not isinstance(value.get("pr_number"), int)
            or isinstance(value.get("pr_number"), bool)
            or value["pr_number"] < 1
            or not SHA_RE.fullmatch(str(value.get("head_sha") or ""))
            or not re.fullmatch(r"[1-9][0-9]*", str(value.get("controller_run_id") or ""))
        ):
            raise DispatchError("DISPATCH_JOURNAL_IDENTITY_INVALID")
        if not isinstance(value.get("attempt"), int) or isinstance(value.get("attempt"), bool) or value["attempt"] < 1:
            raise DispatchError("DISPATCH_JOURNAL_ATTEMPT_INVALID")
        reservations.append(value)
    return reservations


def reservation_attempts(reservations: list[dict], role: str, pr_number: int, head_sha: str) -> list[int]:
    attempts = [
        row["attempt"]
        for row in reservations
        if row.get("role") == role
        and row.get("pr_number") == pr_number
        and row.get("head_sha") == head_sha
    ]
    if len(attempts) != len(set(attempts)):
        raise DispatchError("DISPATCH_JOURNAL_DUPLICATE_ATTEMPT")
    return sorted(attempts)


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
        "schema_version": 1,
        "role": role,
        "pr_number": pr_number,
        "head_sha": head_sha,
        "workflow_path": WORKFLOWS[role]["path"],
        "controller_run_id": controller_run_id,
        "attempt": attempt,
    }
    body = RESERVATION_PREFIX + json.dumps(reservation, sort_keys=True, separators=(",", ":")) + " -->"
    client.post(f"/repos/{REPOSITORY}/issues/{pr_number}/comments", {"body": body})


def dispatch(client: GitHub, role: str, workflow: dict, pr_number: int, head_sha: str) -> None:
    if role not in WORKFLOWS or workflow.get("path") != WORKFLOWS[role]["path"]:
        raise DispatchError("DISPATCH_TARGET_NOT_ALLOWLISTED")
    client.post(
        f"/repos/{REPOSITORY}/actions/workflows/{workflow['id']}/dispatches",
        {"ref": "main", "inputs": {"pr_number": str(pr_number), "head_sha": head_sha}},
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


def reconcile(
    *,
    client: GitHub,
    input_pr: str | None,
    run_number: int,
    controller_run_id: str,
    source_sha: str,
) -> dict[str, Any]:
    if not SHA_RE.fullmatch(source_sha):
        raise DispatchError("CONTROLLER_SOURCE_SHA_INVALID")
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
                "available_roles": available_roles,
                "completed_role_runs": statuses,
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
                attempt=attempt,
            )
            dispatch(client, role, workflows[role], number, head_sha)
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
    token = os.getenv("GH_TOKEN", "")
    run_number = os.getenv("GITHUB_RUN_NUMBER", "")
    run_id = os.getenv("GITHUB_RUN_ID", "")
    source_sha = os.getenv("GITHUB_SHA", "")
    input_pr = os.getenv("INPUT_PR_NUMBER", "").strip() or None
    if not run_number.isdigit() or not run_id.isdigit():
        print(json.dumps({"action": "HOLD", "finding": "CONTROLLER_RUN_IDENTITY_INVALID"}))
        return 2
    try:
        result = reconcile(
            client=GitHub(token),
            input_pr=input_pr,
            run_number=int(run_number),
            controller_run_id=run_id,
            source_sha=source_sha,
        )
    except DispatchError as exc:
        print(json.dumps({"action": "HOLD", "finding": str(exc), "can_execute": False}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
