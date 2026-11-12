from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable


AUTHENTIC_QA_APP_ID = 5245832
AUTHENTIC_RELEASE_APP_ID = 5245853
QA_CHECK_NAME = "WOW Independent QA exact-head"
RELEASE_CHECK_NAME = "WOW Release Authority exact-head"
REQUIRED_ENVIRONMENT = "wow-release"
CONTROLLER_FILENAME = "wow-v17-render-production-deploy.yml"
JOURNAL_REF_PREFIX = "refs/heads/wow-release"
RENDER_CREDENTIAL_SCOPE_ATTESTED = False
ZERO_OID = "0" * 40
UPDATE_REFS_MUTATION = (
    "mutation($repositoryId: ID!, $refUpdates: [RefUpdate!]!) {"
    " updateRefs(input: {repositoryId: $repositoryId, refUpdates: $refUpdates})"
    " { clientMutationId } }"
)
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SERVICE_RE = re.compile(r"^srv-[a-z0-9]+$")
_TERMINAL_DEPLOY_STATUSES = frozenset(
    {"build_failed", "update_failed", "pre_deploy_failed", "deactivated", "canceled", "cancelled"}
)
_IN_PROGRESS_DEPLOY_STATUSES = frozenset(
    {"created", "build_in_progress", "update_in_progress", "queued", "pre_deploy_in_progress", "pending"}
)


class JournalProtocolError(RuntimeError):
    pass


def ambiguous_render_ack(code: str) -> bool:
    text = str(code or "")
    return text in {
        "RENDER_DEPLOY_API_UNREACHABLE",
        "RENDER_DEPLOY_API_INVALID_JSON",
        "RENDER_DEPLOY_API_HTTP_408",
        "RENDER_DEPLOY_API_HTTP_429",
    } or text.startswith("RENDER_DEPLOY_API_HTTP_5")


def render_post_followup(*, action: str, error_code: str | None) -> dict[str, Any]:
    if action != "POST_ONCE" or error_code:
        return {
            "post": False,
            "retry": False,
            "ambiguous": bool(error_code and ambiguous_render_ack(error_code)),
            "can_execute": False,
        }
    return {"post": True, "retry": False, "ambiguous": False, "can_execute": False}


def observation_decision(
    *,
    timed_out: bool,
    deploy_status: str,
    deploy_sha: str,
    target_sha: str,
    current_main_sha: str,
) -> dict[str, Any]:
    if deploy_sha and deploy_sha != target_sha:
        return {"action": "MISMATCH_HOLD", "post": False, "can_execute": False}
    if (
        deploy_status == "live"
        and deploy_sha == target_sha
        and current_main_sha == target_sha
        and _SHA_RE.fullmatch(target_sha or "")
    ):
        return {"action": "LIVE", "post": False, "can_execute": False}
    if timed_out:
        return {
            "action": "TIMEOUT_HOLD",
            "status": "DEPLOY_STILL_BUILDING_OBSERVATION_TIMED_OUT",
            "post": False,
            "can_execute": False,
        }
    return {"action": "WAIT", "post": False, "can_execute": False}


def paginate(fetch_page: Callable[[int], list], *, max_pages: int = 20, page_size: int = 100) -> list:
    if max_pages < 1 or page_size < 1:
        raise JournalProtocolError("GITHUB_CHECK_PAGE_LIMIT")
    found = []
    for page in range(1, max_pages + 1):
        batch = fetch_page(page)
        if not isinstance(batch, list):
            raise JournalProtocolError("GITHUB_CHECK_PAGE_INVALID")
        found.extend(batch)
        if len(batch) < page_size:
            return found
    raise JournalProtocolError("GITHUB_CHECK_PAGE_LIMIT")


def _sha(value: str) -> str:
    text = str(value or "")
    if not _SHA_RE.fullmatch(text):
        raise JournalProtocolError("RENDER_DEPLOY_TARGET_SHA_INVALID")
    return text


def _service(value: str) -> str:
    text = str(value or "")
    if not _SERVICE_RE.fullmatch(text):
        raise JournalProtocolError("RENDER_SERVICE_ID_INVALID")
    return text


def intent_ref(service_id: str, target_sha: str) -> str:
    return f"{JOURNAL_REF_PREFIX}/intent/{_service(service_id)}/{_sha(target_sha)}"


def lock_ref(service_id: str) -> str:
    return f"{JOURNAL_REF_PREFIX}/lock/{_service(service_id)}"


def _api_ref(full_ref: str) -> str:
    if not full_ref.startswith("refs/"):
        raise JournalProtocolError("RELEASE_JOURNAL_REF_INVALID")
    return full_ref[len("refs/"):]


def _hold(status: str, blockers: list[str], **extra: Any) -> dict[str, Any]:
    return {
        "action": "HOLD",
        "eligible": False,
        "status": status,
        "blockers": list(blockers),
        "payload": None,
        "deploy_id": "",
        "can_execute": False,
        **extra,
    }


def _reconcile(deploy_id: str, blockers: list[str] | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "action": "RECONCILE",
        "eligible": False,
        "status": "RELEASE_JOURNAL_RECONCILE_NO_SECOND_POST",
        "blockers": list(blockers or []),
        "payload": None,
        "deploy_id": str(deploy_id or ""),
        "can_execute": False,
        "retry": False,
        **extra,
    }


def role_admission(
    role_checks: Any, *, qa_app_id: Any, release_app_id: Any, source_head: str
) -> str:
    if str(qa_app_id) != str(AUTHENTIC_QA_APP_ID) or str(release_app_id) != str(AUTHENTIC_RELEASE_APP_ID):
        return "RENDER_RELEASE_APP_IDENTITIES_UNBOUND"
    if not _SHA_RE.fullmatch(str(source_head or "")):
        return "RENDER_RELEASE_PR_HEAD_INVALID"
    if not isinstance(role_checks, list):
        return "RENDER_RELEASE_CHECK_EVIDENCE_INVALID"
    for name, app_id in (
        (QA_CHECK_NAME, AUTHENTIC_QA_APP_ID),
        (RELEASE_CHECK_NAME, AUTHENTIC_RELEASE_APP_ID),
    ):
        matching = [
            check
            for check in role_checks
            if isinstance(check, dict)
            and str(check.get("name") or "") == name
            and str(check.get("head_sha") or "") == source_head
            and (check.get("app") or {}).get("id") == app_id
        ]
        if not matching:
            return "RENDER_RELEASE_ROLE_CHECK_MISSING_OR_FOREIGN:" + name
        latest = max(matching, key=lambda check: int(check.get("id") or 0))
        if latest.get("status") != "completed" or latest.get("conclusion") != "success":
            return "RENDER_RELEASE_ROLE_CHECK_NOT_SUCCESS:" + name
    return "PASS"


def _pattern_matches(pattern: str, ref: str) -> bool:
    pieces = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**", index):
            pieces.append(".*")
            index += 2
        elif pattern[index] == "*":
            pieces.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            pieces.append("[^/]")
            index += 1
        else:
            pieces.append(re.escape(pattern[index]))
            index += 1
    return re.fullmatch("".join(pieces), ref) is not None


def protected_release_job_environment(workflow_texts: Any) -> str:
    if not isinstance(workflow_texts, dict):
        return ""
    source = workflow_texts.get(CONTROLLER_FILENAME)
    if not isinstance(source, str):
        return ""
    lines = source.splitlines()
    if lines.count("jobs:") != 1:
        return ""
    start = [i for i, line in enumerate(lines) if line == "  deploy-production:"]
    if len(start) != 1 or start[0] <= lines.index("jobs:"):
        return ""
    end = next(
        (i for i in range(start[0] + 1, len(lines)) if re.fullmatch(r"  [A-Za-z0-9_-]+:", lines[i])),
        len(lines),
    )
    bindings = [
        line.removeprefix("    environment:").strip()
        for line in lines[start[0] + 1:end]
        if line.startswith("    environment:")
    ]
    if len(bindings) != 1:
        return ""
    declared = bindings[0].strip("'\"")
    return declared if declared == REQUIRED_ENVIRONMENT else ""


def workflow_posts_render_deploy(text: str) -> bool:
    return "/deploys" in text and bool(re.search(r'["\']POST["\']|\.post\s*\(', text))


def inventory_render_writers(workflow_texts: Any) -> dict[str, Any]:
    if not isinstance(workflow_texts, dict) or CONTROLLER_FILENAME not in workflow_texts:
        return {"complete": False, "foreign_posts": [], "credential_holders": [],
                "blocker": "RENDER_SERVICE_WRITER_INVENTORY_UNVERIFIED"}
    foreign = sorted(
        name for name, text in workflow_texts.items()
        if name != CONTROLLER_FILENAME and isinstance(text, str) and workflow_posts_render_deploy(text)
    )
    holders = sorted(
        name for name, text in workflow_texts.items()
        if name != CONTROLLER_FILENAME and isinstance(text, str) and "RENDER_API_KEY" in text
    )
    return {
        "complete": not foreign,
        "foreign_posts": foreign,
        "credential_holders": holders,
        "blocker": "RENDER_FOREIGN_DEPLOY_WRITER" if foreign else None,
    }


def load_workflow_texts(directory: str | Path) -> dict[str, str]:
    root = Path(directory)
    if not root.is_dir():
        return {}
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(root.glob("*.yml")) + sorted(root.glob("*.yaml"))
    }


def _commit_sha(deploy: Any) -> str:
    commit = deploy.get("commit") if isinstance(deploy, dict) else None
    return str((commit or {}).get("id") or "") if isinstance(commit, dict) else ""


def _deploy_status(deploy: Any) -> str:
    return str(deploy.get("status") or "") if isinstance(deploy, dict) else ""


class RefJournal:
    def __init__(
        self,
        request: Callable[..., tuple[int, Any]],
        graphql: Callable[[str, dict], tuple[int, Any]] | None = None,
        *,
        repository_id: str = "",
    ):
        self._request = request
        self._graphql = graphql
        self._repository_id = str(repository_id or "")

    def _get(self, full_ref: str) -> str | None:
        status, body = self._request("GET", "/git/ref/" + _api_ref(full_ref), None)
        if status == 404:
            return None
        if status == 403:
            raise JournalProtocolError("RELEASE_JOURNAL_REF_PERMISSION_DENIED")
        if status != 200 or not isinstance(body, dict):
            raise JournalProtocolError("RELEASE_JOURNAL_REF_UNREADABLE")
        obj = body.get("object")
        sha = str((obj if isinstance(obj, dict) else {}).get("sha") or "")
        if not _SHA_RE.fullmatch(sha):
            raise JournalProtocolError("RELEASE_JOURNAL_REF_UNREADABLE")
        return sha

    def _update(self, updates: list[tuple[str, str, str]]) -> str:
        if self._graphql is None or not self._repository_id:
            raise JournalProtocolError("RELEASE_JOURNAL_CAS_UNAVAILABLE")
        variables = {
            "repositoryId": self._repository_id,
            "refUpdates": [
                {"name": ref, "beforeOid": before, "afterOid": after, "force": True}
                for ref, before, after in updates
            ],
        }
        status, body = self._graphql(UPDATE_REFS_MUTATION, variables)
        if status in (401, 403):
            raise JournalProtocolError("RELEASE_JOURNAL_REF_PERMISSION_DENIED")
        if status != 200 or not isinstance(body, dict):
            raise JournalProtocolError("RELEASE_JOURNAL_CAS_UNAVAILABLE")
        errors = body.get("errors")
        if errors:
            if any(
                any(word in str(error).lower() for word in ("forbidden", "not accessible", "permission"))
                for error in errors
            ):
                raise JournalProtocolError("RELEASE_JOURNAL_REF_PERMISSION_DENIED")
            return "rejected"
        result = body.get("data")
        if not isinstance(result, dict) or not isinstance(result.get("updateRefs"), dict):
            raise JournalProtocolError("RELEASE_JOURNAL_CAS_UNAVAILABLE")
        return "applied"

    def _reclaim_blockers(
        self, service: str, holder: str, *, holder_resolved: Callable[[str], bool] | None, consume_only: bool
    ) -> list[str]:
        blockers = ["RELEASE_JOURNAL_SERVICE_LOCK_HELD"]
        if consume_only or holder_resolved is None:
            return blockers
        if self._get(intent_ref(service, holder)) != holder or not holder_resolved(holder):
            return blockers + ["RELEASE_JOURNAL_SERVICE_LOCK_HOLDER_UNRESOLVED"]
        return []

    def reserve(
        self,
        service_id: str,
        target_sha: str,
        *,
        consume_only: bool = False,
        holder_resolved: Callable[[str], bool] | None = None,
    ) -> dict[str, Any]:
        service, sha = _service(service_id), _sha(target_sha)
        lock, intent = lock_ref(service), intent_ref(service, sha)
        refs = {"lock_ref": lock, "intent_ref": intent}
        try:
            current, existing = self._get(lock), self._get(intent)
            if existing not in (None, sha):
                return _hold("RELEASE_JOURNAL_PREFLIGHT_HOLD_NO_DEPLOY", ["RELEASE_JOURNAL_INTENT_SHA_MISMATCH"], **refs)
            intent_update = [] if existing == sha else [(intent, ZERO_OID, sha)]
            if current is None:
                if self._update([(lock, ZERO_OID, sha)] + intent_update) == "rejected":
                    holder = self._get(lock)
                    if holder == sha and self._get(intent) == sha:
                        return _reconcile("", **refs)
                    return _hold(
                        "RELEASE_JOURNAL_SERVICE_LOCK_HELD",
                        ["RELEASE_JOURNAL_SERVICE_LOCK_HELD", "RELEASE_JOURNAL_CAS_REJECTED"],
                        lock_holder_sha=holder, **refs,
                    )
            elif current != sha:
                blockers = self._reclaim_blockers(
                    service, current, holder_resolved=holder_resolved, consume_only=consume_only
                )
                if not blockers and self._get(lock) != current:
                    blockers = ["RELEASE_JOURNAL_SERVICE_LOCK_HELD"]
                if blockers:
                    return _hold("RELEASE_JOURNAL_SERVICE_LOCK_HELD", blockers, lock_holder_sha=current, **refs)
                if self._update([(lock, current, sha)] + intent_update) == "rejected":
                    return _hold(
                        "RELEASE_JOURNAL_SERVICE_LOCK_HELD",
                        ["RELEASE_JOURNAL_SERVICE_LOCK_HELD", "RELEASE_JOURNAL_STALE_LOCK_RECLAIM_FAILED"],
                        lock_holder_sha=current, **refs,
                    )
            elif intent_update:
                return _hold("RELEASE_JOURNAL_PREFLIGHT_HOLD_NO_DEPLOY", ["RELEASE_JOURNAL_LOCK_WITHOUT_INTENT"], **refs)
            if existing == sha or consume_only:
                return _reconcile("", **refs)
            return {
                "action": "POST_ONCE", "eligible": True, "status": "RELEASE_JOURNAL_INTENT_RESERVED",
                "blockers": [], "payload": {"clearCache": "do_not_clear", "commitId": sha},
                "deploy_id": "", "can_execute": False, "retry": False, **refs,
            }
        except JournalProtocolError as exc:
            return _hold("RELEASE_JOURNAL_PREFLIGHT_HOLD_NO_DEPLOY", [str(exc)], **refs)

    def release_lock(self, service_id: str, target_sha: str) -> dict[str, Any]:
        try:
            service, sha = _service(service_id), _sha(target_sha)
            lock, intent = lock_ref(service), intent_ref(service, sha)
            if self._get(intent) != sha or self._get(lock) != sha:
                return {"released": False, "reason": "LOCK_OR_INTENT_NOT_OURS", "can_execute": False}
            if self._update([(lock, sha, ZERO_OID)]) == "rejected":
                return {"released": False, "reason": "LOCK_NOT_OURS", "can_execute": False}
        except JournalProtocolError as exc:
            return {"released": False, "reason": str(exc), "can_execute": False}
        return {"released": True, "reason": "RELEASED", "can_execute": False}


def _same_sha(deploys: list, target_sha: str) -> list[dict]:
    return [d for d in deploys if isinstance(d, dict) and _commit_sha(d) == target_sha]


def holder_deploy_resolved(deploys: Any, holder_sha: str) -> bool:
    if not isinstance(deploys, list) or not _SHA_RE.fullmatch(str(holder_sha or "")):
        return False
    mine = _same_sha(deploys, holder_sha)
    return bool(mine) and all(
        _deploy_status(deploy) in _TERMINAL_DEPLOY_STATUSES | {"live"} for deploy in mine
    )


def _writer_state(service: Any, service_id: str) -> tuple[bool, str]:
    if not isinstance(service, dict):
        return False, "RENDER_LIVE_AUTO_DEPLOY_STATE_UNVERIFIED"
    if str(service.get("id") or "") != service_id:
        return False, "RELEASE_SERVICE_IDENTITY_UNVERIFIED"
    if service.get("autoDeploy") == "no" and service.get("autoDeployTrigger") == "off":
        return True, ""
    return False, "RENDER_NATIVE_AUTO_DEPLOY_WRITER_ENABLED"


def plan_release_admission(
    *,
    environment_document: Any,
    branch_policies: Any,
    target_sha: str,
    current_main_sha: str,
    qa_app_id: Any,
    release_app_id: Any,
    source_head: str,
    role_checks: Any,
    live_service: Any,
    workflow_texts: Any,
    deploys: Any,
    journal: RefJournal,
    service_id: str,
    environment_secret_names: Any = None,
    secret_list_readable: bool = False,
    recheck_main_sha: Callable[[], str] | None = None,
    recheck_deploys: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    inventory = inventory_render_writers(workflow_texts)
    if protected_release_job_environment(workflow_texts) != REQUIRED_ENVIRONMENT:
        return _hold("RELEASE_ENVIRONMENT_RESTRICTION_FAILED", ["RELEASE_ENVIRONMENT_JOB_BINDING_MISSING"])
    if not isinstance(environment_document, dict) or environment_document.get("name") != REQUIRED_ENVIRONMENT:
        return _hold("RELEASE_ENVIRONMENT_RESTRICTION_FAILED", ["RELEASE_ENVIRONMENT_RESTRICTION_FAILED"])
    if environment_document.get("can_admins_bypass") is not False:
        return _hold("RELEASE_ENVIRONMENT_RESTRICTION_FAILED", ["RELEASE_ENVIRONMENT_ADMIN_BYPASS_ENABLED"])
    policy = environment_document.get("deployment_branch_policy")
    if not isinstance(policy, dict):
        return _hold("RELEASE_ENVIRONMENT_RESTRICTION_FAILED", ["RELEASE_ENVIRONMENT_BRANCH_POLICY_UNRESTRICTED"])
    if policy.get("custom_branch_policies") is True:
        policies = branch_policies if isinstance(branch_policies, list) else []
        if not any(
            _pattern_matches(str(item.get("name") or ""), "main")
            for item in policies if isinstance(item, dict)
        ):
            return _hold("RELEASE_ENVIRONMENT_RESTRICTION_FAILED", ["RELEASE_ENVIRONMENT_BRANCH_POLICY_EXCLUDES_MAIN"])
    elif policy.get("protected_branches") is not True:
        return _hold("RELEASE_ENVIRONMENT_RESTRICTION_FAILED", ["RELEASE_ENVIRONMENT_BRANCH_POLICY_UNRESTRICTED"])
    if secret_list_readable and (
        not isinstance(environment_secret_names, list) or "RENDER_API_KEY" not in environment_secret_names
    ):
        return _hold("RELEASE_ENVIRONMENT_CREDENTIAL_UNSCOPED", ["RELEASE_ENVIRONMENT_CREDENTIAL_UNSCOPED"])
    if not _SHA_RE.fullmatch(str(target_sha or "")) or current_main_sha != target_sha:
        return _hold("STALE_SHA_NO_DEPLOY", ["STALE_SHA_NO_DEPLOY"])
    role = role_admission(
        role_checks, qa_app_id=qa_app_id, release_app_id=release_app_id, source_head=source_head
    )
    if role != "PASS":
        return _hold("RELEASE_AUTHORIZATION_HOLD_NO_DEPLOY", [role])
    writer_off, writer_blocker = _writer_state(live_service, service_id)
    if not writer_off:
        return _hold("RELEASE_JOURNAL_PREFLIGHT_HOLD_NO_DEPLOY", [writer_blocker])
    if not inventory["complete"]:
        return _hold("RELEASE_JOURNAL_PREFLIGHT_HOLD_NO_DEPLOY", [inventory["blocker"]])
    if not isinstance(deploys, list):
        return _hold("RELEASE_JOURNAL_PREFLIGHT_HOLD_NO_DEPLOY", ["RENDER_DEPLOY_LIST_INVALID"])

    def classify(items: list) -> dict[str, Any] | None:
        for deploy in items:
            if not isinstance(deploy, dict) or not _deploy_status(deploy):
                return _hold("RELEASE_JOURNAL_PREFLIGHT_HOLD_NO_DEPLOY", ["RENDER_DEPLOY_LIST_INVALID"])
            sha, status = _commit_sha(deploy), _deploy_status(deploy)
            if status not in _TERMINAL_DEPLOY_STATUSES and not _SHA_RE.fullmatch(sha):
                return _hold("RELEASE_JOURNAL_PREFLIGHT_HOLD_NO_DEPLOY", ["RENDER_DEPLOY_LIST_INVALID"])
            if sha != target_sha and status in _IN_PROGRESS_DEPLOY_STATUSES:
                return _hold("RELEASE_OTHER_SHA_DEPLOY_ACTIVE", ["RELEASE_OTHER_SHA_DEPLOY_ACTIVE"])
        for deploy in _same_sha(items, target_sha):
            status = _deploy_status(deploy)
            if status in _TERMINAL_DEPLOY_STATUSES:
                return _hold("PRODUCTION_RELEASE_HOLD", [f"RENDER_TARGET_DEPLOY_TERMINAL_FAILURE:{status}"])
            if status == "live":
                return {
                    "action": "ALREADY_LIVE", "eligible": False, "status": "EXACT_SHA_ALREADY_LIVE",
                    "blockers": [], "payload": None, "deploy_id": str(deploy.get("id") or ""),
                    "can_execute": False,
                }
            if status in _IN_PROGRESS_DEPLOY_STATUSES:
                return _reconcile(str(deploy.get("id") or ""))
        return None

    classified = classify(deploys)
    if classified is not None:
        if classified["action"] == "ALREADY_LIVE":
            journal.release_lock(service_id, target_sha)
        return classified

    holders = inventory["credential_holders"]
    if not RENDER_CREDENTIAL_SCOPE_ATTESTED or holders or not secret_list_readable:
        return _hold(
            "RELEASE_RENDER_CREDENTIAL_SCOPE_UNATTESTED",
            ["RENDER_API_CREDENTIAL_SCOPE_UNATTESTED"],
            credential_holders=holders,
            environment_credential_scope=(
                "UNVERIFIED" if not secret_list_readable else "ENVIRONMENT_SECRET_PRESENT_SCOPE_UNATTESTED"
            ),
        )

    reserved = journal.reserve(
        service_id, target_sha, holder_resolved=lambda sha: holder_deploy_resolved(deploys, sha)
    )
    if reserved["action"] != "POST_ONCE":
        return reserved
    try:
        fresh_main = current_main_sha if recheck_main_sha is None else str(recheck_main_sha() or "")
        fresh_deploys = deploys if recheck_deploys is None else recheck_deploys()
    except Exception:
        journal.release_lock(service_id, target_sha)
        return _hold("RELEASE_JOURNAL_PREFLIGHT_HOLD_NO_DEPLOY", ["RENDER_RECHECK_UNREADABLE"])
    if fresh_main != target_sha:
        journal.release_lock(service_id, target_sha)
        return _hold("STALE_SHA_NO_DEPLOY", ["STALE_SHA_NO_DEPLOY"])
    if not isinstance(fresh_deploys, list):
        journal.release_lock(service_id, target_sha)
        return _hold("RELEASE_JOURNAL_PREFLIGHT_HOLD_NO_DEPLOY", ["RENDER_DEPLOY_LIST_INVALID"])
    reclassified = classify(fresh_deploys)
    if reclassified is not None:
        if reclassified["action"] == "ALREADY_LIVE":
            journal.release_lock(service_id, target_sha)
        elif reclassified["action"] == "HOLD":
            journal.release_lock(service_id, target_sha)
        return reclassified
    return reserved
