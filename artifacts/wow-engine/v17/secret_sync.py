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
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

RENDER_API_BASE = "https://api.render.com/v1"
DEFAULT_MANIFEST = Path(__file__).with_name("secret_sync_manifest.json")
SECRET_DOT_REF_RE = re.compile(r"\bsecrets\.([A-Za-z_][A-Za-z0-9_]*)")
SECRET_BRACKET_REF_RE = re.compile(r"\bsecrets\[['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\]")

CONTROL_PLANE_KEYS = {
    "RENDER_API_KEY",
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

    def trigger_deploy(self) -> None:
        data = self._request(
            "POST",
            f"/services/{quote(self.service_id, safe='')}/deploys",
            {"clearCache": "do_not_clear"},
        )
        if not isinstance(data, dict) or not data.get("id"):
            raise SecretSyncError(
                "RENDER_DEPLOY_RESPONSE_INVALID",
                "Render did not return a deployment identifier",
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
) -> SyncSummary:
    env = dict(os.environ if environ is None else environ)
    render_api_key = str(env.get("RENDER_API_KEY", ""))
    if not render_api_key:
        raise SecretSyncError(
            "BOOTSTRAP_RENDER_API_KEY_UNCONFIGURED",
            "RENDER_API_KEY is required to synchronize Render environment variables",
        )

    reverse_token = str(env.get("WOW_GITHUB_SECRET_SYNC_TOKEN", ""))
    if mode in {"both", "render-to-github"} and not reverse_token and not dry_run:
        raise SecretSyncError(
            "BOOTSTRAP_GITHUB_SECRET_SYNC_TOKEN_UNCONFIGURED",
            "WOW_GITHUB_SECRET_SYNC_TOKEN is required for Render-to-GitHub synchronization",
        )

    render = render_client or RenderClient(render_api_key, str(manifest["render_service_id"]))
    summary = SyncSummary()

    if mode in {"both", "github-to-render"}:
        checked, changed, skipped = sync_github_to_render(
            manifest,
            render,
            env,
            dry_run=dry_run,
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

    if summary.github_to_render_changed and not dry_run:
        render.trigger_deploy()
        summary.deploy_triggered = True

    return summary


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
    args = parser.parse_args(argv)

    try:
        manifest = load_manifest(args.manifest)
        summary = run_sync(
            manifest,
            mode=args.mode,
            repo_root=_repo_root_from_script(),
            dry_run=args.dry_run,
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
