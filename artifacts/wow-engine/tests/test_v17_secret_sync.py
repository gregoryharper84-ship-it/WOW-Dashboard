from __future__ import annotations

import json
from pathlib import Path

import pytest

from v17 import secret_sync


@pytest.fixture
def manifest():
    return {
        "version": 1,
        "github_repository": "owner/repo",
        "render_service_id": "srv-test",
        "github_to_render": [
            {"name": "RUNDOWN_API_KEY", "required": True},
            {"name": "OPTIONAL_API_KEY", "required": False},
        ],
        "render_to_github": {
            "workflow_referenced": True,
            "explicit_names": ["PLATFORM_SHARED_API_KEY"],
            "exclude_names": ["WOW_ACTION_API_KEY", "SUPABASE_SERVICE_ROLE_KEY"],
            "exclude_prefixes": ["GITHUB_", "RENDER_"],
        },
        "governance": {
            "can_execute": False,
            "delete_propagation": False,
            "render_replace_all": False,
            "secret_values_may_be_logged": False,
        },
    }


class FakeRender:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.set_calls = []
        self.deploy_calls = 0

    def get_env(self, name):
        return self.values.get(name)

    def set_env(self, name, value):
        self.set_calls.append((name, value))
        self.values[name] = value

    def trigger_deploy(self):
        self.deploy_calls += 1


class FakeGitHub:
    def __init__(self):
        self.calls = []

    def set_secret(self, name, value):
        self.calls.append((name, value))


def test_manifest_rejects_control_plane_secret(manifest):
    manifest["github_to_render"].append({"name": "RENDER_API_KEY", "required": True})
    with pytest.raises(secret_sync.SecretSyncError, match="Control-plane") as exc:
        secret_sync.validate_manifest(manifest)
    assert exc.value.reason_code == "SECRET_SYNC_CONTROL_PLANE_KEY_FORBIDDEN"


def test_manifest_rejects_dual_ownership(manifest):
    manifest["render_to_github"]["explicit_names"].append("RUNDOWN_API_KEY")
    with pytest.raises(secret_sync.SecretSyncError) as exc:
        secret_sync.validate_manifest(manifest)
    assert exc.value.reason_code == "SECRET_SYNC_OWNERSHIP_CONFLICT"


def test_workflow_secret_discovery_reads_names_only(tmp_path):
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "a.yml").write_text(
        "env:\n  A: ${{ secrets.PLATFORM_SHARED_API_KEY }}\n  B: ${{ secrets['SECOND_TOKEN'] }}\n",
        encoding="utf-8",
    )
    assert secret_sync.discover_workflow_secret_names(tmp_path) == {
        "PLATFORM_SHARED_API_KEY",
        "SECOND_TOKEN",
    }


def test_github_to_render_required_missing_fails_closed(manifest, tmp_path):
    render = FakeRender()
    with pytest.raises(secret_sync.SecretSyncError) as exc:
        secret_sync.run_sync(
            manifest,
            mode="github-to-render",
            repo_root=tmp_path,
            environ={"RENDER_API_KEY": "render-control"},
            render_client=render,
        )
    assert exc.value.reason_code == "GITHUB_SOURCE_SECRET_UNCONFIGURED"
    assert render.set_calls == []
    assert render.deploy_calls == 0


def test_both_mode_missing_reverse_bootstrap_fails_before_any_mutation(manifest, tmp_path):
    render = FakeRender({"RUNDOWN_API_KEY": "old"})
    with pytest.raises(secret_sync.SecretSyncError) as exc:
        secret_sync.run_sync(
            manifest,
            mode="both",
            repo_root=tmp_path,
            environ={
                "RENDER_API_KEY": "render-control",
                "RUNDOWN_API_KEY": "new",
            },
            render_client=render,
        )
    assert exc.value.reason_code == "BOOTSTRAP_GITHUB_SECRET_SYNC_TOKEN_UNCONFIGURED"
    assert render.set_calls == []
    assert render.deploy_calls == 0


def test_github_to_render_updates_only_changed_key_and_triggers_one_deploy(manifest, tmp_path):
    render = FakeRender({"RUNDOWN_API_KEY": "old"})
    summary = secret_sync.run_sync(
        manifest,
        mode="github-to-render",
        repo_root=tmp_path,
        environ={
            "RENDER_API_KEY": "render-control",
            "RUNDOWN_API_KEY": "new",
        },
        render_client=render,
    )
    assert render.set_calls == [("RUNDOWN_API_KEY", "new")]
    assert render.deploy_calls == 1
    assert summary.github_to_render_changed == 1
    assert summary.deploy_triggered is True


def test_github_to_render_equal_value_does_not_redeploy(manifest, tmp_path):
    render = FakeRender({"RUNDOWN_API_KEY": "same"})
    summary = secret_sync.run_sync(
        manifest,
        mode="github-to-render",
        repo_root=tmp_path,
        environ={
            "RENDER_API_KEY": "render-control",
            "RUNDOWN_API_KEY": "same",
        },
        render_client=render,
    )
    assert render.set_calls == []
    assert render.deploy_calls == 0
    assert summary.github_to_render_changed == 0


def test_render_to_github_uses_workflow_refs_but_excludes_github_owned_and_control_plane(manifest, tmp_path):
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "sync.yml").write_text(
        "\n".join(
            [
                "A: ${{ secrets.PLATFORM_SHARED_API_KEY }}",
                "B: ${{ secrets.SECOND_TOKEN }}",
                "C: ${{ secrets.RUNDOWN_API_KEY }}",
                "D: ${{ secrets.WOW_ACTION_API_KEY }}",
                "E: ${{ secrets.RENDER_API_KEY }}",
            ]
        ),
        encoding="utf-8",
    )
    render = FakeRender(
        {
            "PLATFORM_SHARED_API_KEY": "render-one",
            "SECOND_TOKEN": "render-two",
            "RUNDOWN_API_KEY": "must-not-win",
            "WOW_ACTION_API_KEY": "must-not-copy",
            "RENDER_API_KEY": "must-not-copy",
        }
    )
    github = FakeGitHub()
    summary = secret_sync.run_sync(
        manifest,
        mode="render-to-github",
        repo_root=tmp_path,
        environ={
            "RENDER_API_KEY": "render-control",
            "WOW_GITHUB_SECRET_SYNC_TOKEN": "github-control",
        },
        render_client=render,
        github_writer=github,
    )
    assert sorted(github.calls) == [
        ("PLATFORM_SHARED_API_KEY", "render-one"),
        ("SECOND_TOKEN", "render-two"),
    ]
    assert summary.render_to_github_changed == 2
    assert render.deploy_calls == 0


def test_dry_run_never_mutates_either_store_and_needs_no_github_write_token(manifest, tmp_path):
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "sync.yml").write_text(
        "A: ${{ secrets.PLATFORM_SHARED_API_KEY }}\n",
        encoding="utf-8",
    )
    render = FakeRender({"RUNDOWN_API_KEY": "old", "PLATFORM_SHARED_API_KEY": "render"})
    github = FakeGitHub()
    summary = secret_sync.run_sync(
        manifest,
        mode="both",
        repo_root=tmp_path,
        environ={
            "RENDER_API_KEY": "render-control",
            "RUNDOWN_API_KEY": "new",
        },
        dry_run=True,
        render_client=render,
        github_writer=github,
    )
    assert render.set_calls == []
    assert render.deploy_calls == 0
    assert github.calls == []
    assert summary.github_to_render_changed == 1
    assert summary.render_to_github_changed == 1


def test_summary_never_contains_secret_values(manifest, tmp_path):
    secret_value = "top-secret-value"
    render = FakeRender({"RUNDOWN_API_KEY": secret_value})
    summary = secret_sync.run_sync(
        manifest,
        mode="github-to-render",
        repo_root=tmp_path,
        environ={
            "RENDER_API_KEY": "render-control",
            "RUNDOWN_API_KEY": secret_value,
        },
        render_client=render,
    )
    encoded = json.dumps(summary.as_dict(), sort_keys=True)
    assert secret_value not in encoded
    assert "render-control" not in encoded
    assert summary.as_dict()["secret_values_exposed"] is False
    assert summary.as_dict()["can_execute"] is False
