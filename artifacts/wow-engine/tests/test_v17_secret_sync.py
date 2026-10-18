from __future__ import annotations

import json
from pathlib import Path

import pytest

from v17 import secret_sync

REPO_ROOT = Path(__file__).resolve().parents[3]


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
        self.auto_deploy_checks = 0
        self.auto_deploy_disabled = True

    def get_env(self, name):
        return self.values.get(name)

    def set_env(self, name, value):
        self.set_calls.append((name, value))
        self.values[name] = value

    def assert_auto_deploy_disabled(self):
        self.auto_deploy_checks += 1
        if not self.auto_deploy_disabled:
            raise secret_sync.SecretSyncError(
                "RENDER_AUTO_DEPLOY_STATE_UNVERIFIED",
                "Render live auto-deploy must be verified off before secret mutation",
            )

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


def test_github_to_render_updates_secret_and_queues_release_request_without_deploy(manifest, tmp_path):
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
    assert render.deploy_calls == 0
    assert summary.github_to_render_changed == 1
    assert summary.deploy_triggered is False
    assert summary.release_request_status == "SECRET_ENV_CHANGED_RELEASE_REQUEST"
    assert render.auto_deploy_checks == 1


def test_live_auto_deploy_unknown_or_enabled_blocks_secret_mutation(manifest, tmp_path):
    render = FakeRender({"RUNDOWN_API_KEY": "old"})
    render.auto_deploy_disabled = False

    with pytest.raises(secret_sync.SecretSyncError, match="verified off"):
        secret_sync.run_sync(
            manifest,
            mode="github-to-render",
            repo_root=tmp_path,
            environ={
                "RENDER_API_KEY": "render-control",
                "RUNDOWN_API_KEY": "new",
            },
            render_client=render,
        )

    assert render.set_calls == []
    assert render.deploy_calls == 0
    assert render.auto_deploy_checks == 1


def test_render_client_checks_live_service_auto_deploy_fields():
    client = secret_sync.RenderClient("render-control", "srv-test")
    calls = []
    client._request = lambda method, path: calls.append((method, path)) or {
        "autoDeploy": "no",
        "autoDeployTrigger": "off",
    }

    client.assert_auto_deploy_disabled()

    assert calls == [("GET", "/services/srv-test")]


@pytest.mark.parametrize(
    "service",
    [
        {"autoDeploy": "yes", "autoDeployTrigger": "off"},
        {"autoDeploy": "no", "autoDeployTrigger": "checksPass"},
        {"autoDeployTrigger": "off"},
    ],
)
def test_render_client_fails_closed_when_live_auto_deploy_is_not_off(service):
    client = secret_sync.RenderClient("render-control", "srv-test")
    client._request = lambda _method, _path: service

    with pytest.raises(secret_sync.SecretSyncError) as error:
        client.assert_auto_deploy_disabled()

    assert error.value.reason_code == "RENDER_AUTO_DEPLOY_STATE_UNVERIFIED"


def test_secret_change_release_receipt_is_durable_typed_and_secret_free(manifest, tmp_path):
    render = FakeRender({"RUNDOWN_API_KEY": "old"})
    summary = secret_sync.run_sync(
        manifest,
        mode="github-to-render",
        repo_root=tmp_path,
        environ={"RENDER_API_KEY": "render-control", "RUNDOWN_API_KEY": "new-secret"},
        render_client=render,
    )
    environment = {
        "GITHUB_REPOSITORY": manifest["github_repository"],
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_SHA": "a" * 40,
        "GITHUB_RUN_ID": "12345",
        "GITHUB_RUN_ATTEMPT": "2",
    }
    receipt = secret_sync.build_release_request_receipt(
        summary,
        environ=environment,
        repository=manifest["github_repository"],
        service_id=manifest["render_service_id"],
    )
    receipt_path = tmp_path / "request" / "receipt.json"
    secret_sync.write_release_request_receipt(receipt_path, receipt)

    stored = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert stored == receipt
    assert stored["request_type"] == "SECRET_ENV_CHANGED_RELEASE_REQUEST"
    assert stored["service_id"] == manifest["render_service_id"]
    assert stored["source_sha"] == "a" * 40
    assert stored["workflow_run_id"] == "12345"
    assert stored["workflow_run_attempt"] == "2"
    assert stored["can_execute"] is False
    assert stored["secret_values_exposed"] is False
    assert "new-secret" not in json.dumps(stored)
    assert "render-control" not in json.dumps(stored)
    assert render.set_calls == [("RUNDOWN_API_KEY", "new-secret")]
    assert render.deploy_calls == 0


def test_partial_secret_mutation_keeps_release_request_receipt(manifest, tmp_path):
    class FailingRender(FakeRender):
        def set_env(self, name, value):
            if name == "OPTIONAL_API_KEY":
                raise secret_sync.SecretSyncError("RENDER_API_REQUEST_FAILED", "write failed")
            super().set_env(name, value)

    render = FailingRender({"RUNDOWN_API_KEY": "old", "OPTIONAL_API_KEY": "old-optional"})
    receipt_path = tmp_path / "partial-request.json"
    with pytest.raises(secret_sync.SecretSyncError, match="write failed"):
        secret_sync.run_sync(
            manifest,
            mode="github-to-render",
            repo_root=tmp_path,
            environ={
                "RENDER_API_KEY": "render-control",
                "RUNDOWN_API_KEY": "new-secret",
                "OPTIONAL_API_KEY": "new-optional",
                "GITHUB_REPOSITORY": manifest["github_repository"],
                "GITHUB_REF": "refs/heads/main",
                "GITHUB_SHA": "a" * 40,
                "GITHUB_RUN_ID": "12345",
                "GITHUB_RUN_ATTEMPT": "1",
            },
            render_client=render,
            release_request_file=receipt_path,
        )

    stored = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert stored["request_type"] == "SECRET_ENV_CHANGED_RELEASE_REQUEST"
    assert stored["source_sha"] == "a" * 40
    assert render.set_calls == [("RUNDOWN_API_KEY", "new-secret")]
    assert render.deploy_calls == 0


def test_secret_change_request_requires_exact_main_workflow_identity(manifest):
    with pytest.raises(secret_sync.SecretSyncError, match="verified main-branch"):
        secret_sync.validate_release_request_identity(
            {
                "GITHUB_REPOSITORY": manifest["github_repository"],
                "GITHUB_REF": "refs/heads/feature",
                "GITHUB_SHA": "a" * 40,
                "GITHUB_RUN_ID": "12345",
                "GITHUB_RUN_ATTEMPT": "1",
            },
            repository=manifest["github_repository"],
        )


def test_secret_sync_workflow_archives_request_before_controller_dispatch():
    workflow = (REPO_ROOT / ".github/workflows/wow-v17-secret-sync.yml").read_text(encoding="utf-8")

    assert "actions: write" in workflow
    assert "--release-request-file" in workflow
    artifact_step = workflow.index("Persist typed secret-change release request")
    dispatch_step = workflow.index("Dispatch protected Render release controller")
    assert artifact_step < dispatch_step
    assert "if: always() && steps.release-request.outputs.release_request == 'true'" in workflow
    assert "if: always() && steps.release-request.outputs.release_request == 'true' && steps.persist-request.outcome == 'success'" in workflow
    assert "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in workflow
    assert "wow-v17-render-production-deploy.yml" in workflow
    assert "secret_sync_request_run_id" in workflow
    assert "/deploys" not in (REPO_ROOT / "artifacts/wow-engine/v17/secret_sync.py").read_text(
        encoding="utf-8"
    )


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
    assert summary.release_request_status == "DRY_RUN_NO_RELEASE_REQUEST"


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
