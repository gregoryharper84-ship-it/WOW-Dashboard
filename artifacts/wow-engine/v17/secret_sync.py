from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

RENDER_API_BASE = "https://api.render.com/v1"
DEFAULT_MANIFEST = Path(__file__).with_name("secret_sync_manifest.json")
SECRET_DOT_REF_RE = re.compile(r"\bsecrets\.([A-Za-z_][A-Za-z0-9_]*)")
SECRET_BRACKET_REF_RE = re.compile(r"\bsecrets\[['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\]")

CONTROL_PLANE_KEYS = {
    "RENDER_API_KEY",
    "WOW_SECRET_SYNC_RENDER_API_KEY",
    "WOW_MCP_AUTH_TOKEN",
    "WOW_GITHUB_SECRET_SYNC_TOKEN",
    "SUPABASE_SERVICE_ROLE_KEY",
}


class SecretSyncError(RuntimeError):
    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class SyncEntry:
    name: str
    required: bool = False


@dataclass
class SyncSummary:
    github_to_render_checked: int = 0
    github_to_render_changed: int = 0
    github_to_render_skipped: int = 0
    render_to_github_checked: int = 0
    render_to_github_changed: int = 0
    render_to_github_skipped: int = 0
    deploy_triggered: bool = False
    release_request_status: str = "NOT_REQUIRED"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": "COMPLETE",
            "github_to_render_checked": self.github_to_render_checked,
            "github_to_render_changed": self.github_to_render_changed,
            "github_to_render_skipped": self.github_to_render_skipped,
            "render_to_github_checked": self.render_to_github_checked,
            "render_to_github_changed": self.render_to_github_changed,
            "render_to_github_skipped": self.render_to_github_skipped,
            "deploy_triggered": self.deploy_triggered,
            "release_request_status": self.release_request_status,
            "delete_propagation": False,
            "secret_values_exposed": False,
            "can_execute": False,
        }


class RenderClient:
    def __init__(self, api_key: str, service_id: str):
        self.api_key = api_key
        self.service_id = service_id

    def _request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
        *,
        allow_404: bool = False,
    ) -> Any:
        body = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        if payload is not None:
            body = json.dumps(dict(payload)).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(f"{RENDER_API_BASE}{path}", data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=30) as response:  # nosec B310: fixed HTTPS API origin
                raw = response.read()
        except HTTPError as exc:
            if allow_404 and exc.code == 404:
                return None
            raise SecretSyncError(
                "RENDER_API_REQUEST_FAILED",
                f"Render API request failed with HTTP {exc.code}",
            ) from exc
        except (URLError, TimeoutError) as exc:
            raise SecretSyncError(
                "RENDER_API_REQUEST_FAILED",
                "Render API request failed before receiving a response",
            ) from exc
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SecretSyncError(
                "RENDER_API_RESPONSE_INVALID",
                "Render API returned a non-JSON response",
            ) from exc

    def get_env(self, name: str) -> str | None:
        data = self._request(
            "GET",
            f"/services/{quote(self.service_id, safe='')}/env-vars/{quote(name, safe='')}",
            allow_404=True,
        )
        if data is None:
            return None
        if not isinstance(data, dict) or data.get("key") != name or not isinstance(data.get("value"), str):
            raise SecretSyncError(
                "RENDER_ENV_RESPONSE_INVALID",
                "Render returned an invalid environment-variable record",
            )
        return data["value"]

    def set_env(self, name: str, value: str) -> None:
        data = self._request(
            "PUT",
            f"/services/{quote(self.service_id, safe='')}/env-vars/{quote(name, safe='')}",
            {"value": value},
        )
        if not isinstance(data, dict) or data.get("key") != name:
            raise SecretSyncError(
                "RENDER_ENV_UPDATE_INVALID",
                "Render did not confirm the updated environment-variable key",
            )

    def assert_auto_deploy_disabled(self) -> None:
        service = self._request(
            "GET",
            f"/services/{quote(self.service_id, safe='')}",
        )
        if (
            not isinstance(service, dict)
            or service.get("autoDeploy") not in (False, "no")
            or service.get("autoDeployTrigger") != "off"
        ):
            raise SecretSyncError(
                "RENDER_AUTO_DEPLOY_STATE_UNVERIFIED",
                "Render live auto-deploy must be verified off before secret mutation",
            )


class GitHubSecretWriter:
    def __init__(self, token: str, repository: str):
        self.token = token
        self.repository = repository

    def set_secret(self, name: str, value: str) -> None:
        env = os.environ.copy()
        env["GH_TOKEN"] = self.token
        try:
            result = subprocess.run(
                ["gh", "secret", "set", name, "--repo", self.repository],
                input=value,
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                check=False,
            )
        except FileNotFoundError as exc:
            raise SecretSyncError(
                "GITHUB_CLI_UNAVAILABLE",
                "GitHub CLI is unavailable on the sync runner",
            ) from exc
        if result.returncode != 0:
            raise SecretSyncError(
                "GITHUB_SECRET_UPDATE_FAILED",
                "GitHub rejected a repository-secret update",
            )


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SecretSyncError("SECRET_SYNC_MANIFEST_INVALID", "Secret sync manifest is unreadable") from exc
    validate_manifest(data)
    return data


def _github_entries(manifest: Mapping[str, Any]) -> list[SyncEntry]:
    return [
        SyncEntry(name=str(item["name"]), required=bool(item.get("required", False)))
        for item in manifest.get("github_to_render", [])
    ]


def validate_manifest(manifest: Mapping[str, Any]) -> None:
    if manifest.get("version") != 1:
        raise SecretSyncError("SECRET_SYNC_MANIFEST_INVALID", "Unsupported secret sync manifest version")
    if not manifest.get("github_repository") or not manifest.get("render_service_id"):
        raise SecretSyncError("SECRET_SYNC_MANIFEST_INVALID", "Sync targets must be explicit")

    entries = manifest.get("github_to_render")
    reverse = manifest.get("render_to_github")
    governance = manifest.get("governance")
    if not isinstance(entries, list) or not isinstance(reverse, dict) or not isinstance(governance, dict):
        raise SecretSyncError("SECRET_SYNC_MANIFEST_INVALID", "Sync lanes must be typed")

    names: list[str] = []
    for item in entries:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"]:
            raise SecretSyncError("SECRET_SYNC_MANIFEST_INVALID", "GitHub-owned secret entry is invalid")
        names.append(item["name"])
    if len(names) != len(set(names)):
        raise SecretSyncError("SECRET_SYNC_MANIFEST_INVALID", "GitHub-owned secret names must be unique")

    explicit_reverse = reverse.get("explicit_names", [])
    if not isinstance(explicit_reverse, list) or not all(isinstance(name, str) and name for name in explicit_reverse):
        raise SecretSyncError("SECRET_SYNC_MANIFEST_INVALID", "Render-owned explicit secret names are invalid")
    if set(names) & set(explicit_reverse):
        raise SecretSyncError("SECRET_SYNC_OWNERSHIP_CONFLICT", "A secret cannot be owned by both stores")

    protected = set(names) | set(explicit_reverse)
    if protected & CONTROL_PLANE_KEYS:
        raise SecretSyncError(
            "SECRET_SYNC_CONTROL_PLANE_KEY_FORBIDDEN",
            "Control-plane credentials cannot be mirrored automatically",
        )

    if governance.get("can_execute") is not False:
        raise SecretSyncError("SECRET_SYNC_GOVERNANCE_INVALID", "can_execute must remain false")
    if governance.get("delete_propagation") is not False:
        raise SecretSyncError("SECRET_SYNC_GOVERNANCE_INVALID", "Delete propagation must remain disabled")
    if governance.get("render_replace_all") is not False:
        raise SecretSyncError("SECRET_SYNC_GOVERNANCE_INVALID", "Render replace-all updates are forbidden")
    if governance.get("secret_values_may_be_logged") is not False:
        raise SecretSyncError("SECRET_SYNC_GOVERNANCE_INVALID", "Secret-value logging must remain disabled")


def discover_workflow_secret_names(repo_root: Path) -> set[str]:
    workflow_dir = repo_root / ".github" / "workflows"
    names: set[str] = set()
    if not workflow_dir.is_dir():
        return names
    for path in workflow_dir.glob("*.y*ml"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        names.update(match.group(1).upper() for match in SECRET_DOT_REF_RE.finditer(text))
        names.update(match.group(1).upper() for match in SECRET_BRACKET_REF_RE.finditer(text))
    return names


def render_owned_candidates(manifest: Mapping[str, Any], repo_root: Path) -> list[str]:
    reverse = manifest["render_to_github"]
    names = {str(name).upper() for name in reverse.get("explicit_names", [])}
    if reverse.get("workflow_referenced", False):
        names.update(discover_workflow_secret_names(repo_root))

    github_owned = {entry.name.upper() for entry in _github_entries(manifest)}
    excluded = {str(name).upper() for name in reverse.get("exclude_names", [])}
    prefixes = tuple(str(prefix).upper() for prefix in reverse.get("exclude_prefixes", []))

    return sorted(
        name
        for name in names
        if name not in github_owned
        and name not in excluded
        and name not in CONTROL_PLANE_KEYS
        and not name.startswith(prefixes)
    )


def sync_github_to_render(
    manifest: Mapping[str, Any],
    render: RenderClient,
    environ: Mapping[str, str],
    *,
    dry_run: bool = False,
    on_secret_changed: Callable[[], None] | None = None,
) -> tuple[int, int, int]:
    checked = changed = skipped = 0
    for entry in _github_entries(manifest):
        value = str(environ.get(entry.name, ""))
        if not value:
            if entry.required:
                raise SecretSyncError(
                    "GITHUB_SOURCE_SECRET_UNCONFIGURED",
                    f"Required GitHub-owned secret {entry.name} is not configured",
                )
            skipped += 1
            continue
        checked += 1
        current = render.get_env(entry.name)
        if current is not None and hmac.compare_digest(current, value):
            continue
        changed += 1
        if not dry_run:
            render.set_env(entry.name, value)
            if on_secret_changed is not None:
                on_secret_changed()
            applied = render.get_env(entry.name)
            if applied is None or not hmac.compare_digest(applied, value):
                raise SecretSyncError(
                    "RENDER_ENV_UPDATE_READBACK_MISMATCH",
                    "Render did not confirm the updated environment-variable value",
                )
    return checked, changed, skipped


def sync_render_to_github(
    manifest: Mapping[str, Any],
    render: RenderClient,
    github: GitHubSecretWriter,
    repo_root: Path,
    *,
    dry_run: bool = False,
) -> tuple[int, int, int]:
    checked = changed = skipped = 0
    for name in render_owned_candidates(manifest, repo_root):
        checked += 1
        value = render.get_env(name)
        if not value:
            skipped += 1
            continue
        changed += 1
        if not dry_run:
            github.set_secret(name, value)
    return checked, changed, skipped


def run_sync(
    manifest: Mapping[str, Any],
    *,
    mode: str,
    repo_root: Path,
    environ: Mapping[str, str] | None = None,
    dry_run: bool = False,
    render_client: RenderClient | None = None,
    github_writer: GitHubSecretWriter | None = None,
    release_request_file: Path | None = None,
) -> SyncSummary:
    env = dict(os.environ if environ is None else environ)
    render_api_key = str(env.get("WOW_SECRET_SYNC_RENDER_API_KEY", ""))
    if not render_api_key:
        raise SecretSyncError(
            "BOOTSTRAP_SYNC_RENDER_CREDENTIAL_UNCONFIGURED",
            "WOW_SECRET_SYNC_RENDER_API_KEY must be configured for the isolated secret-sync principal",
        )

    reverse_token = str(env.get("WOW_GITHUB_SECRET_SYNC_TOKEN", ""))
    if mode in {"both", "render-to-github"} and not reverse_token and not dry_run:
        raise SecretSyncError(
            "BOOTSTRAP_GITHUB_SECRET_SYNC_TOKEN_UNCONFIGURED",
            "WOW_GITHUB_SECRET_SYNC_TOKEN is required for Render-to-GitHub synchronization",
        )

    render = render_client or RenderClient(render_api_key, str(manifest["render_service_id"]))
    summary = SyncSummary()

    def record_release_request() -> None:
        summary.release_request_status = "SECRET_ENV_CHANGED_RELEASE_REQUEST"
        if release_request_file is not None:
            receipt = build_release_request_receipt(
                summary,
                environ=env,
                repository=str(manifest["github_repository"]),
                service_id=str(manifest["render_service_id"]),
            )
            write_release_request_receipt(release_request_file, receipt)

    if mode in {"both", "github-to-render"}:
        if not dry_run:
            render.assert_auto_deploy_disabled()
        checked, changed, skipped = sync_github_to_render(
            manifest,
            render,
            env,
            dry_run=dry_run,
            on_secret_changed=record_release_request,
        )
        summary.github_to_render_checked = checked
        summary.github_to_render_changed = changed
        summary.github_to_render_skipped = skipped

    if mode in {"both", "render-to-github"}:
        github = github_writer or GitHubSecretWriter(
            reverse_token or "dry-run-no-write",
            str(manifest["github_repository"]),
        )
        checked, changed, skipped = sync_render_to_github(
            manifest,
            render,
            github,
            repo_root,
            dry_run=dry_run,
        )
        summary.render_to_github_checked = checked
        summary.render_to_github_changed = changed
        summary.render_to_github_skipped = skipped

    if summary.github_to_render_changed and dry_run:
        summary.release_request_status = "DRY_RUN_NO_RELEASE_REQUEST"

    if release_request_file is not None and not release_request_file.exists():
        receipt = build_release_request_receipt(
            summary,
            environ=env,
            repository=str(manifest["github_repository"]),
            service_id=str(manifest["render_service_id"]),
        )
        write_release_request_receipt(release_request_file, receipt)

    return summary


def validate_release_request_identity(
    environ: Mapping[str, str], *, repository: str
) -> dict[str, str]:
    sha = str(environ.get("GITHUB_SHA") or "")
    run_id = str(environ.get("GITHUB_RUN_ID") or "")
    run_attempt = str(environ.get("GITHUB_RUN_ATTEMPT") or "")
    if (
        environ.get("GITHUB_REPOSITORY") != repository
        or environ.get("GITHUB_REF") != "refs/heads/main"
        or not re.fullmatch(r"[0-9a-f]{40}", sha)
        or not run_id.isdigit()
        or not run_attempt.isdigit()
        or int(run_attempt) < 1
    ):
        raise SecretSyncError(
            "SECRET_ENV_RELEASE_REQUEST_IDENTITY_INVALID",
            "A release request requires a verified main-branch workflow identity",
        )
    return {"source_sha": sha, "workflow_run_id": run_id, "workflow_run_attempt": run_attempt}


def build_release_request_receipt(
    summary: SyncSummary,
    *,
    environ: Mapping[str, str],
    repository: str,
    service_id: str,
) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "status": summary.release_request_status,
        "repository": repository,
        "service_id": service_id,
        "can_execute": False,
        "secret_values_exposed": False,
    }
    if summary.release_request_status == "SECRET_ENV_CHANGED_RELEASE_REQUEST":
        receipt.update(validate_release_request_identity(environ, repository=repository))
        receipt.update(
            {
                "request_type": "SECRET_ENV_CHANGED_RELEASE_REQUEST",
                "runtime_generation": "V17_ACTIVE",
                "terminal_authority": "V17_TERMINAL_REDUCER",
                "custom_gpt_identity": "WOW_BETTING_ENGINE",
                "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS": True,
            }
        )
    return receipt


def write_release_request_receipt(path: Path, receipt: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(dict(receipt), sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[3]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Governed two-way GitHub/Render secret sync")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--mode",
        choices=("both", "github-to-render", "render-to-github"),
        default="both",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--release-request-file", type=Path)
    args = parser.parse_args(argv)

    try:
        manifest = load_manifest(args.manifest)
        if not args.dry_run and args.mode in {"both", "github-to-render"}:
            validate_release_request_identity(os.environ, repository=str(manifest["github_repository"]))
        summary = run_sync(
            manifest,
            mode=args.mode,
            repo_root=_repo_root_from_script(),
            dry_run=args.dry_run,
            release_request_file=args.release_request_file,
        )
    except SecretSyncError as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "reason_code": exc.reason_code,
                    "message": str(exc),
                    "secret_values_exposed": False,
                    "can_execute": False,
                },
                sort_keys=True,
            )
        )
        return 2

    result = summary.as_dict()
    if args.dry_run:
        result["status"] = "DRY_RUN_COMPLETE"
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
