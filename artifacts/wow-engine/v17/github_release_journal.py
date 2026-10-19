"""GitHub-native exactly-once journal for the production Render controller.

GitHub's Deployments API is not this fence. ``POST /repos/.../deployments``
has no idempotency key: two creates both succeed, and each create emits a
``deployment`` event. Actions concurrency serializes only this workflow.

The atomic compare-and-swap is ``POST /git/refs``. Creating a ref returns
422 when that ref already exists. The intent ref is one service plus one
full SHA and this controller never deletes it. The lock ref is the
service-wide fence; it is deleted only after a terminal observation and
only when it still points at the same SHA.

``refs/heads/wow-release/...`` is outside every current push filter. Those
workflows listen to ``main`` or named feature branches, not this prefix.
No workflow in this repository listens to ``create``.

The connected Supabase project is the application database. It is not a
certified release-control schema, and a caller-supplied project ref is not
accepted as one. This journal is the release-control store.

can_execute stays false. This module does not place a wager or a Render POST.
"""

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
DEPLOYMENT_API_CAS_BLOCKER = "GITHUB_DEPLOYMENT_CREATE_HAS_NO_ATOMIC_IDEMPOTENCY"
APPLICATION_DATABASE_IS_NOT_RELEASE_CONTROL_SCHEMA = (
    "APPLICATION_DATABASE_PROJECT_IS_NOT_RELEASE_CONTROL_SCHEMA"
)
RELEASE_CONTROL_SCHEMA_NOT_CERTIFIED = "RELEASE_CONTROL_SCHEMA_NOT_CERTIFIED"
RENDER_DEPLOY_LIST_IS_NOT_THE_FENCE = "RENDER_DEPLOY_LIST_SNAPSHOT_IS_NOT_DURABLE_FENCE"
BLUEPRINT_AUTO_DEPLOY_TRIGGER = "checksPass"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SERVICE_RE = re.compile(r"^srv-[a-z0-9]+$")
_TERMINAL_DEPLOY_STATUSES = frozenset(
    {
        "build_failed",
        "update_failed",
        "pre_deploy_failed",
        "deactivated",
        "canceled",
        "cancelled",
    }
)
_IN_PROGRESS_DEPLOY_STATUSES = frozenset(
    {
        "created",
        "build_in_progress",
        "update_in_progress",
        "queued",
        "pre_deploy_in_progress",
        "pending",
    }
)
_AMBIGUOUS_RENDER_CODES = frozenset(
    {
        "RENDER_DEPLOY_API_UNREACHABLE",
        "RENDER_DEPLOY_API_INVALID_JSON",
        "RENDER_DEPLOY_API_HTTP_408",
        "RENDER_DEPLOY_API_HTTP_429",
    }
)


class JournalProtocolError(RuntimeError):
    pass


def deployments_api_is_not_a_cas(store_name: str) -> None:
    """Refuse any journal that is not the git-ref compare-and-swap."""
    if store_name != "git_ref":
        raise JournalProtocolError(DEPLOYMENT_API_CAS_BLOCKER)


def paginate(fetch_page: Callable[[int], list], *, max_pages: int = 20, page_size: int = 100) -> list:
    """Read every full page. Hitting the bound with a full page fails closed."""
    if max_pages < 1 or page_size < 1:
        raise JournalProtocolError("GITHUB_CHECK_PAGE_LIMIT")
    found: list = []
    for page in range(1, max_pages + 1):
        batch = fetch_page(page)
        if not isinstance(batch, list):
            raise JournalProtocolError("GITHUB_CHECK_PAGE_INVALID")
        found.extend(batch)
        if len(batch) < page_size:
            return found
    raise JournalProtocolError("GITHUB_CHECK_PAGE_LIMIT")


def ambiguous_render_ack(code: str) -> bool:
    """Transport loss, 5xx, and empty/invalid bodies must not be retried."""
    text = str(code or "")
    if text in _AMBIGUOUS_RENDER_CODES:
        return True
    return text.startswith("RENDER_DEPLOY_API_HTTP_5")


def observation_decision(
    *,
    timed_out: bool,
    deploy_status: str,
    deploy_sha: str,
    target_sha: str,
    current_main_sha: str,
) -> dict[str, Any]:
    """Read-only observation. Never an authorization to POST."""
    status = str(deploy_status or "")
    if deploy_sha and deploy_sha != target_sha:
        return {"action": "MISMATCH_HOLD", "post": False, "can_execute": False}
    if (
        status == "live"
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


def render_post_followup(*, action: str, error_code: str | None) -> dict[str, Any]:
    """A POST is attempted at most once. Ambiguous and rejected results do not retry."""
    if action != "POST_ONCE":
        return {"post": False, "retry": False, "ambiguous": False, "can_execute": False}
    if error_code:
        return {
            "post": False,
            "retry": False,
            "ambiguous": ambiguous_render_ack(error_code),
            "can_execute": False,
        }
    return {"post": True, "retry": False, "ambiguous": False, "can_execute": False}


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
    prefix = "refs/"
    if not full_ref.startswith(prefix):
        raise JournalProtocolError("RELEASE_JOURNAL_REF_INVALID")
    return full_ref[len(prefix):]


def _hold(status: str, blockers: list[str], **extra: Any) -> dict[str, Any]:
    return {
        "action": "HOLD",
        "eligible": False,
        "status": status,
        "blockers": list(blockers),
        "payload": None,
        "deploy_id": "",
        "can_execute": False,
        "release_control_store": "github_ref_journal",
        "application_database_is_release_control_schema": False,
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
        "release_control_store": "github_ref_journal",
        "retry": False,
        **extra,
    }


def role_admission(
    role_checks: Any,
    *,
    qa_app_id: Any,
    release_app_id: Any,
    source_head: str,
) -> str:
    """Latest success from the pinned QA and Release apps on the source head."""
    if str(qa_app_id) != str(AUTHENTIC_QA_APP_ID) or str(release_app_id) != str(AUTHENTIC_RELEASE_APP_ID):
        return "RENDER_RELEASE_APP_IDENTITIES_UNBOUND"
    if int(AUTHENTIC_QA_APP_ID) == int(AUTHENTIC_RELEASE_APP_ID):
        return "RENDER_RELEASE_APP_IDENTITIES_NOT_DISTINCT"
    if not _SHA_RE.fullmatch(str(source_head or "")):
        return "RENDER_RELEASE_PR_HEAD_INVALID"
    if not isinstance(role_checks, list):
        return "RENDER_RELEASE_CHECK_EVIDENCE_INVALID"
    required = {
        QA_CHECK_NAME: AUTHENTIC_QA_APP_ID,
        RELEASE_CHECK_NAME: AUTHENTIC_RELEASE_APP_ID,
    }
    for name, app_id in required.items():
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


def _ref_pattern_matches(pattern: str, ref: str) -> bool:
    pieces: list[str] = []
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


def protected_environment_admission(
    environment_document: Any,
    branch_policies: Any,
    *,
    environment_secret_names: Any = None,
    secret_list_readable: bool = False,
) -> str:
    """Decide from GitHub's environment API, not from a self-set environment variable.

    ``GITHUB_ENVIRONMENT`` is not a default Actions variable. Setting it in the
    workflow would only repeat the job name and would not prove protection.
    """
    if not isinstance(environment_document, dict) or environment_document.get("name") != REQUIRED_ENVIRONMENT:
        return "RELEASE_ENVIRONMENT_RESTRICTION_FAILED"
    if environment_document.get("can_admins_bypass") is not False:
        return "RELEASE_ENVIRONMENT_ADMIN_BYPASS_ENABLED"
    policy = environment_document.get("deployment_branch_policy")
    if not isinstance(policy, dict):
        return "RELEASE_ENVIRONMENT_BRANCH_POLICY_UNRESTRICTED"
    custom = policy.get("custom_branch_policies") is True
    protected_only = policy.get("protected_branches") is True
    if not custom and not protected_only:
        return "RELEASE_ENVIRONMENT_BRANCH_POLICY_UNRESTRICTED"
    if custom:
        if not isinstance(branch_policies, list):
            return "RELEASE_ENVIRONMENT_BRANCH_POLICY_UNRESTRICTED"
        names = [
            str(item.get("name") or "") if isinstance(item, dict) else str(item or "")
            for item in branch_policies
        ]
        if not any(name and _ref_pattern_matches(name, "main") for name in names):
            return "RELEASE_ENVIRONMENT_BRANCH_POLICY_EXCLUDES_MAIN"
    if secret_list_readable:
        names = environment_secret_names if isinstance(environment_secret_names, list) else []
        if "RENDER_API_KEY" not in names:
            return "RELEASE_ENVIRONMENT_CREDENTIAL_UNSCOPED"
    return "PASS"


def environment_reviewers_configured(environment_document: Any) -> bool:
    """Record whether GitHub requires reviewers. Absence is attested, not a code hold.

    The live wow-release environment currently has a branch policy and no
    required-reviewer rule. Security and Release own that attestation. Treating
    the absence as a hard hold would block every production run.
    """
    if not isinstance(environment_document, dict):
        return False
    rules = environment_document.get("protection_rules")
    if not isinstance(rules, list):
        return False
    return any(isinstance(rule, dict) and rule.get("type") == "required_reviewers" for rule in rules)


def native_writer_state(service: Any, *, service_id: str | None = None) -> dict[str, Any]:
    if not isinstance(service, dict):
        return {
            "off": False,
            "trigger": "UNVERIFIED",
            "blocker": "RENDER_LIVE_AUTO_DEPLOY_STATE_UNVERIFIED",
        }
    if service_id is not None and str(service.get("id") or "") != service_id:
        return {
            "off": False,
            "trigger": str(service.get("autoDeployTrigger") or "UNVERIFIED"),
            "blocker": "RELEASE_SERVICE_IDENTITY_UNVERIFIED",
        }
    trigger = service.get("autoDeployTrigger")
    off = service.get("autoDeploy") == "no" and trigger == "off"
    if off:
        return {"off": True, "trigger": "off", "blocker": None}
    return {
        "off": False,
        "trigger": str(trigger or "UNVERIFIED"),
        "blocker": "RENDER_NATIVE_AUTO_DEPLOY_WRITER_ENABLED",
    }


def workflow_posts_render_deploy(text: str) -> bool:
    if "/deploys" not in text:
        return False
    return bool(re.search(r'["\']POST["\']|\.post\s*\(', text))


def inventory_render_writers(workflow_texts: Any) -> dict[str, Any]:
    if not isinstance(workflow_texts, dict) or CONTROLLER_FILENAME not in workflow_texts:
        return {
            "complete": False,
            "foreign_posts": [],
            "credential_holders": [],
            "blocker": "RENDER_SERVICE_WRITER_INVENTORY_UNVERIFIED",
        }
    foreign = sorted(
        name
        for name, text in workflow_texts.items()
        if name != CONTROLLER_FILENAME and isinstance(text, str) and workflow_posts_render_deploy(text)
    )
    holders = sorted(
        name
        for name, text in workflow_texts.items()
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
    texts: dict[str, str] = {}
    if not root.is_dir():
        return texts
    for path in sorted(root.glob("*.yml")) + sorted(root.glob("*.yaml")):
        texts[path.name] = path.read_text(encoding="utf-8")
    return texts


def protected_release_job_environment(workflow_texts: Any) -> str:
    """Read the environment declaration from the checked-out controller job.

    GitHub Actions does not export GITHUB_ENVIRONMENT. GitHub enforces the
    YAML job environment *before* the runner starts. This confirms the exact
    checked-out workflow still binds the production job to wow-release; it
    does NOT independently certify the environment's reviewer settings or
    exclusive Render credential. Security/Release must attest those settings.
    """
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
        (i for i in range(start[0] + 1, len(lines))
         if re.fullmatch(r"  [A-Za-z0-9_-]+:", lines[i])),
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


def _commit_sha(deploy: Any) -> str:
    if not isinstance(deploy, dict):
        return ""
    commit = deploy.get("commit")
    if isinstance(commit, dict):
        return str(commit.get("id") or "")
    return ""


def _deploy_status(deploy: Any) -> str:
    if not isinstance(deploy, dict):
        return ""
    return str(deploy.get("status") or "")


ZERO_OID = "0" * 40
UPDATE_REFS_MUTATION = (
    "mutation($repositoryId: ID!, $refUpdates: [RefUpdate!]!) {"
    " updateRefs(input: {repositoryId: $repositoryId, refUpdates: $refUpdates})"
    " { clientMutationId } }"
)


class RefJournal:
    """All journal mutations use atomic GitHub GraphQL updateRefs beforeOid CAS."""

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
        sha = str(((body.get("object") or {}) if isinstance(body.get("object"), dict) else {}).get("sha") or "")
        if not _SHA_RE.fullmatch(sha):
            raise JournalProtocolError("RELEASE_JOURNAL_REF_UNREADABLE")
        return sha

    def _update(self, updates: list[tuple[str, str, str]]) -> str:
        """Apply all ref mutations atomically; do not retry ambiguous results."""
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
            err_text = str(errors).lower()
            if "forbidden" in err_text or "not accessible" in err_text:
                raise JournalProtocolError("RELEASE_JOURNAL_REF_PERMISSION_DENIED")
            return "rejected"
        data = body.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("updateRefs"), dict):
            raise JournalProtocolError("RELEASE_JOURNAL_CAS_UNAVAILABLE")
        return "applied"

    def _reclaim_blockers(
        self,
        service: str,
        holder: str,
        *,
        holder_resolved: Callable[[str], bool] | None,
        consume_only: bool,
    ) -> list[str]:
        held = ["RELEASE_JOURNAL_SERVICE_LOCK_HELD"]
        if consume_only or holder_resolved is None:
            return held
        if self._get(intent_ref(service, holder)) != holder or not holder_resolved(holder):
            return held + ["RELEASE_JOURNAL_SERVICE_LOCK_HOLDER_UNRESOLVED"]
        return []

    def reserve(
        self,
        service_id: str,
        target_sha: str,
        *,
        consume_only: bool = False,
        holder_resolved: Callable[[str], bool] | None = None,
    ) -> dict[str, Any]:
        service = _service(service_id)
        sha = _sha(target_sha)
        lock = lock_ref(service)
        intent = intent_ref(service, sha)
        refs = {"lock_ref": lock, "intent_ref": intent}
        reclaimed_from = None
        try:
            current = self._get(lock)
            existing = self._get(intent)
            if existing not in (None, sha):
                return _hold("RELEASE_JOURNAL_PREFLIGHT_HOLD_NO_DEPLOY",
                             ["RELEASE_JOURNAL_INTENT_SHA_MISMATCH"], **refs)
            intent_update = [] if existing == sha else [(intent, ZERO_OID, sha)]
            if current is None:
                if self._update([(lock, ZERO_OID, sha)] + intent_update) == "rejected":
                    holder = self._get(lock)
                    if holder == sha and self._get(intent) == sha:
                        return _reconcile("", **refs)
                    return _hold("RELEASE_JOURNAL_SERVICE_LOCK_HELD",
                                 ["RELEASE_JOURNAL_SERVICE_LOCK_HELD", "RELEASE_JOURNAL_CAS_REJECTED"],
                                 lock_holder_sha=holder, **refs)
            elif current != sha:
                blockers = self._reclaim_blockers(
                    service, current, holder_resolved=holder_resolved, consume_only=consume_only
                )
                if not blockers and self._get(lock) != current:
                    blockers = ["RELEASE_JOURNAL_SERVICE_LOCK_HELD"]
                if blockers:
                    return _hold("RELEASE_JOURNAL_SERVICE_LOCK_HELD",
                                 blockers, lock_holder_sha=current, **refs)
                if self._update([(lock, current, sha)] + intent_update) == "rejected":
                    return _hold("RELEASE_JOURNAL_SERVICE_LOCK_HELD",
                                 ["RELEASE_JOURNAL_SERVICE_LOCK_HELD", "RELEASE_JOURNAL_STALE_LOCK_RECLAIM_FAILED"],
                                 lock_holder_sha=current, **refs)
                reclaimed_from = current
            elif intent_update:
                # A lock without its intent is an incomplete prior reservation.
                # Never treat it as permission for a second Render POST.
                return _hold("RELEASE_JOURNAL_PREFLIGHT_HOLD_NO_DEPLOY",
                             ["RELEASE_JOURNAL_LOCK_WITHOUT_INTENT"], **refs)
            if existing == sha or consume_only:
                return _reconcile("", **refs)
            return {
                "action": "POST_ONCE",
                "eligible": True,
                "status": "RELEASE_JOURNAL_INTENT_RESERVED",
                "blockers": [],
                "payload": {"clearCache": "do_not_clear", "commitId": sha},
                "deploy_id": "",
                "can_execute": False,
                "retry": False,
                "lock_ref": lock,
                "intent_ref": intent,
                "reclaimed_lock_from": reclaimed_from,
                "release_control_store": "github_ref_journal",
            }
        except JournalProtocolError as exc:
            return _hold("RELEASE_JOURNAL_PREFLIGHT_HOLD_NO_DEPLOY", [str(exc)], **refs)

    def release_lock(self, service_id: str, target_sha: str) -> dict[str, Any]:
        """Release only our current lock via expected-holder CAS, never bare DELETE."""
        try:
            service = _service(service_id)
            sha = _sha(target_sha)
        except JournalProtocolError as exc:
            return {"released": False, "reason": str(exc), "can_execute": False}
        intent = intent_ref(service, sha)
        lock = lock_ref(service)
        try:
            intent_sha = self._get(intent)
            lock_sha = self._get(lock)
            if lock_sha != sha:
                return {"released": False, "reason": "LOCK_NOT_OURS", "can_execute": False}
            if intent_sha not in (None, sha):
                return {"released": False, "reason": "INTENT_ABSENT", "can_execute": False}
            if self._update([(lock, sha, ZERO_OID)]) == "rejected":
                return {"released": False, "reason": "LOCK_NOT_OURS", "can_execute": False}
        except JournalProtocolError as exc:
            return {"released": False, "reason": str(exc), "can_execute": False}
        return {"released": True, "reason": "RELEASED", "can_execute": False}


def _same_sha(deploys: list, target_sha: str) -> list[dict]:
    return [deploy for deploy in deploys if isinstance(deploy, dict) and _commit_sha(deploy) == target_sha]


def holder_deploy_resolved(deploys: Any, holder_sha: str) -> bool:
    """Positive Render evidence that the lock holder's own deploy reached live or terminal.

    No deploy for the holder (an uncertain or rejected POST) is not evidence.
    """
    if not isinstance(deploys, list) or not _SHA_RE.fullmatch(str(holder_sha or "")):
        return False
    mine = _same_sha(deploys, holder_sha)
    if not mine:
        return False
    resolved = _TERMINAL_DEPLOY_STATUSES | {"live"}
    return all(_deploy_status(deploy) in resolved for deploy in mine)


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
    """Admit at most one pinned Render POST, or a typed hold, or read-only reconcile."""
    deployments_api_is_not_a_cas("git_ref")
    writer = native_writer_state(live_service, service_id=service_id)
    inventory = inventory_render_writers(workflow_texts)
    credential_scope = (
        "ENVIRONMENT_SECRET"
        if secret_list_readable
        and isinstance(environment_secret_names, list)
        and "RENDER_API_KEY" in environment_secret_names
        else "UNVERIFIED_API_FORBIDDEN"
        if not secret_list_readable
        else "MISSING"
    )
    common = {
        "live_render_auto_deploy_trigger": writer["trigger"],
        "repository_blueprint_auto_deploy_trigger": BLUEPRINT_AUTO_DEPLOY_TRIGGER,
        "credential_holders": inventory["credential_holders"],
        "environment_credential_scope": credential_scope,
        "environment_reviewers_configured": environment_reviewers_configured(environment_document),
        "release_control_notes": [
            APPLICATION_DATABASE_IS_NOT_RELEASE_CONTROL_SCHEMA,
            RELEASE_CONTROL_SCHEMA_NOT_CERTIFIED,
            RENDER_DEPLOY_LIST_IS_NOT_THE_FENCE,
            DEPLOYMENT_API_CAS_BLOCKER,
        ],
    }
    if protected_release_job_environment(workflow_texts) != REQUIRED_ENVIRONMENT:
        return _hold(
            "RELEASE_ENVIRONMENT_RESTRICTION_FAILED",
            ["RELEASE_ENVIRONMENT_JOB_BINDING_MISSING"],
            **common,
        )
    environment_code = protected_environment_admission(
        environment_document,
        branch_policies,
        environment_secret_names=environment_secret_names,
        secret_list_readable=secret_list_readable,
    )
    if environment_code != "PASS":
        status = (
            environment_code
            if environment_code
            in {
                "RELEASE_ENVIRONMENT_RESTRICTION_FAILED",
                "RELEASE_ENVIRONMENT_REVIEWERS_REQUIRED",
                "RELEASE_ENVIRONMENT_CREDENTIAL_UNSCOPED",
            }
            else "RELEASE_ENVIRONMENT_RESTRICTION_FAILED"
        )
        return _hold(status, [environment_code], **common)
    if not _SHA_RE.fullmatch(str(target_sha or "")) or not _SHA_RE.fullmatch(str(current_main_sha or "")):
        return _hold("STALE_SHA_NO_DEPLOY", ["RENDER_DEPLOY_TARGET_SHA_INVALID"], **common)
    if current_main_sha != target_sha:
        return _hold("STALE_SHA_NO_DEPLOY", ["STALE_SHA_NO_DEPLOY"], **common)
    role = role_admission(
        role_checks,
        qa_app_id=qa_app_id,
        release_app_id=release_app_id,
        source_head=source_head,
    )
    if role != "PASS":
        return _hold("RELEASE_AUTHORIZATION_HOLD_NO_DEPLOY", [role], **common)
    if not writer["off"]:
        status = (
            "RELEASE_NATIVE_WRITER_ENABLED"
            if writer["blocker"] == "RENDER_NATIVE_AUTO_DEPLOY_WRITER_ENABLED"
            else "RELEASE_JOURNAL_PREFLIGHT_HOLD_NO_DEPLOY"
        )
        return _hold(status, [writer["blocker"] or "RENDER_LIVE_AUTO_DEPLOY_STATE_UNVERIFIED"], **common)
    if not inventory["complete"]:
        return _hold(
            "RELEASE_JOURNAL_PREFLIGHT_HOLD_NO_DEPLOY",
            [inventory["blocker"] or "RENDER_SERVICE_WRITER_INVENTORY_UNVERIFIED"],
            **common,
        )
    if not isinstance(deploys, list):
        return _hold("RELEASE_JOURNAL_PREFLIGHT_HOLD_NO_DEPLOY", ["RENDER_DEPLOY_LIST_INVALID"], **common)

    def _classify(items: list) -> dict[str, Any] | None:
        for deploy in items:
            if not isinstance(deploy, dict):
                return _hold("RELEASE_JOURNAL_PREFLIGHT_HOLD_NO_DEPLOY", ["RENDER_DEPLOY_LIST_INVALID"], **common)
            status = _deploy_status(deploy)
            sha = _commit_sha(deploy)
            if not status:
                return _hold("RELEASE_JOURNAL_PREFLIGHT_HOLD_NO_DEPLOY", ["RENDER_DEPLOY_LIST_INVALID"], **common)
            if status not in _TERMINAL_DEPLOY_STATUSES and not _SHA_RE.fullmatch(sha or ""):
                return _hold("RELEASE_JOURNAL_PREFLIGHT_HOLD_NO_DEPLOY", ["RENDER_DEPLOY_LIST_INVALID"], **common)
            if sha != target_sha and status in _IN_PROGRESS_DEPLOY_STATUSES:
                return _hold(
                    "RELEASE_OTHER_SHA_DEPLOY_ACTIVE",
                    ["RELEASE_OTHER_SHA_DEPLOY_ACTIVE"],
                    **common,
                )
        for deploy in _same_sha(items, target_sha):
            status = _deploy_status(deploy)
            if status in _TERMINAL_DEPLOY_STATUSES:
                return _hold(
                    "PRODUCTION_RELEASE_HOLD",
                    [f"RENDER_TARGET_DEPLOY_TERMINAL_FAILURE:{status}"],
                    **common,
                )
            if status == "live":
                return {
                    "action": "ALREADY_LIVE",
                    "eligible": False,
                    "status": "EXACT_SHA_ALREADY_LIVE",
                    "blockers": [],
                    "payload": None,
                    "deploy_id": str(deploy.get("id") or ""),
                    "can_execute": False,
                    "release_control_store": "github_ref_journal",
                    **common,
                }
            if status in _IN_PROGRESS_DEPLOY_STATUSES:
                reserved = journal.reserve(service_id, target_sha, consume_only=True)
                return _reconcile(
                    str(deploy.get("id") or ""),
                    reserved.get("blockers") or [],
                    **common,
                )
        return None

    classified = _classify(deploys)
    if classified is not None:
        if classified["action"] == "ALREADY_LIVE":
            journal.release_lock(service_id, target_sha)
        return classified

    reserved = journal.reserve(
        service_id,
        target_sha,
        consume_only=False,
        holder_resolved=lambda holder: holder_deploy_resolved(deploys, holder),
    )
    if reserved["action"] != "POST_ONCE":
        return {**reserved, **common}

    try:
        fresh_main = current_main_sha if recheck_main_sha is None else str(recheck_main_sha() or "")
        fresh_deploys = deploys if recheck_deploys is None else recheck_deploys()
    except Exception:
        journal.release_lock(service_id, target_sha)
        return _hold(
            "RELEASE_JOURNAL_PREFLIGHT_HOLD_NO_DEPLOY",
            ["RENDER_RECHECK_UNREADABLE"],
            **common,
        )
    if fresh_main != target_sha:
        journal.release_lock(service_id, target_sha)
        return _hold("STALE_SHA_NO_DEPLOY", ["STALE_SHA_NO_DEPLOY"], **common)
    if not isinstance(fresh_deploys, list):
        journal.release_lock(service_id, target_sha)
        return _hold("RELEASE_JOURNAL_PREFLIGHT_HOLD_NO_DEPLOY", ["RENDER_DEPLOY_LIST_INVALID"], **common)
    reclassified = _classify(fresh_deploys)
    if reclassified is not None and reclassified["action"] != "ALREADY_LIVE":
        if reclassified["action"] == "HOLD":
            journal.release_lock(service_id, target_sha)
        return reclassified
    if reclassified is not None and reclassified["action"] == "ALREADY_LIVE":
        journal.release_lock(service_id, target_sha)
        return reclassified
    return {**reserved, **common}
