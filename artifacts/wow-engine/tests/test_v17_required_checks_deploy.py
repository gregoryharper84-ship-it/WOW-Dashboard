"""Required-checks deploy recovery: exact current main, one deploy, never noisy."""
from __future__ import annotations

import json
import urllib.parse
from pathlib import Path

import pytest
import yaml

from v17 import required_checks_deploy as deployer
from v17.agent_identity_policy import REQUIRED_REGRESSION_CHECKS
from v17.github_release_journal import inventory_render_writers, load_workflow_texts
from v17.lean_production_readback import REPO, SERVICES

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "wow-v17-required-checks-deploy.yml"
SHA = "a" * 40
NEWER = "b" * 40
OLD = "c" * 40


def _service(role: str, trigger: str = "checksPass") -> dict:
    return {"id": SERVICES[role], "branch": "main", "repo": "https://github.com/" + REPO,
            "autoDeployTrigger": trigger}


def _check(name: str, *, conclusion: str = "success", status: str = "completed",
           app: int = 15368, ident: int = 1, sha: str = SHA) -> dict:
    return {"id": ident, "name": name, "head_sha": sha, "status": status,
            "conclusion": conclusion if status == "completed" else None, "app": {"id": app}}


class Fake:
    """GitHub + Render double. Each sleep advances in-progress deploys one step."""

    def __init__(self, *, checks=None, deploys=None, main=SHA, services=None, outcome="live"):
        self.checks = [_check(n, ident=i + 1) for i, n in enumerate(REQUIRED_REGRESSION_CHECKS)] \
            if checks is None else checks
        self.deploys = deploys or {
            role: [{"id": f"dep-old-{role}", "status": "live", "commit": {"id": OLD}}]
            for role in SERVICES}
        self.main = main
        self.services = services or {role: _service(role) for role in SERVICES}
        self.outcome = outcome
        self.posts: list[tuple[str, dict]] = []
        self.sleeps = 0
        self.main_after_posts: str | None = None

    def _role(self, url: str) -> str:
        return next(r for r, sid in SERVICES.items() if "/services/" + sid in url)

    def fetch(self, method, url, body):
        if url.endswith("/branches/main"):
            return {"commit": {"sha": self.main}}
        if "/check-runs" in url:
            name = urllib.parse.unquote(url.split("check_name=", 1)[1])
            return {"check_runs": [c for c in self.checks if c["name"] == name]}
        role = self._role(url)
        if method == "POST":
            assert url.endswith("/deploys") and body == {"commitId": SHA, "clearCache": "do_not_clear"}
            self.posts.append((role, body))
            self.deploys[role].insert(0, {"id": f"dep-new-{role}", "status": "build_in_progress",
                                          "commit": {"id": SHA}})
            if self.main_after_posts:
                self.main = self.main_after_posts
            return {"id": f"dep-new-{role}"}
        if url.endswith("/deploys?limit=20"):
            return [{"deploy": d, "cursor": "x"} for d in self.deploys[role]]
        return self.services[role]

    def sleep(self, _seconds):
        self.sleeps += 1
        for role, items in self.deploys.items():
            for deploy in items:
                if deploy["status"] == "build_in_progress" and deploy["commit"]["id"] == SHA:
                    deploy["status"] = self.outcome
                    if self.outcome == "live":
                        for other in items:
                            if other is not deploy and other["status"] == "live":
                                other["status"] = "deactivated"


def _run(fake: Fake, *, health: bool = True, **kwargs):
    return deployer.run(target_sha=SHA, fetch=fake.fetch, sleep=fake.sleep,
                        health=lambda: health, poll_seconds=20, **kwargs)


def test_superseded_target_never_deploys():
    fake = Fake(main=NEWER)
    assert _run(fake)["status"] == "SUPERSEDED_NO_DEPLOY"
    assert fake.posts == [] and fake.sleeps == 0


@pytest.mark.parametrize("checks, status", [
    ([], "REQUIRED_CHECKS_PENDING_NO_DEPLOY"),
    ([_check(n, ident=i) for i, n in enumerate(REQUIRED_REGRESSION_CHECKS[:2])],
     "REQUIRED_CHECKS_PENDING_NO_DEPLOY"),
    ([_check(n, ident=i, status="in_progress") for i, n in enumerate(REQUIRED_REGRESSION_CHECKS)],
     "REQUIRED_CHECKS_PENDING_NO_DEPLOY"),
    ([_check(n, ident=i, app=5245832) for i, n in enumerate(REQUIRED_REGRESSION_CHECKS)],
     "REQUIRED_CHECKS_PENDING_NO_DEPLOY"),
    ([_check(n, ident=i, sha=OLD) for i, n in enumerate(REQUIRED_REGRESSION_CHECKS)],
     "REQUIRED_CHECKS_PENDING_NO_DEPLOY"),
    ([_check(n, ident=i, conclusion="failure" if i == 1 else "success")
      for i, n in enumerate(REQUIRED_REGRESSION_CHECKS)], "REQUIRED_CHECKS_FAILED_NO_DEPLOY"),
])
def test_only_three_green_app_15368_checks_at_exact_sha_unlock_a_deploy(checks, status):
    fake = Fake(checks=checks)
    assert _run(fake)["status"] == status
    assert fake.posts == []


def test_latest_rerun_of_a_required_check_decides():
    checks = [_check(n, ident=i + 10) for i, n in enumerate(REQUIRED_REGRESSION_CHECKS)]
    checks.append(_check(REQUIRED_REGRESSION_CHECKS[0], ident=1, conclusion="failure"))
    assert deployer.required_checks_state(checks, SHA) == "success"
    checks.append(_check(REQUIRED_REGRESSION_CHECKS[0], ident=99, conclusion="failure"))
    assert deployer.required_checks_state(checks, SHA) == "failure"


def test_recovers_a_skipped_auto_deploy_with_exactly_one_post_per_service():
    fake = Fake()
    out = _run(fake)
    assert out["status"] == "DEPLOY_LIVE_AND_HEALTHY"
    assert sorted(role for role, _ in fake.posts) == ["web", "worker"]
    assert out["readback"]["web_deploy_id"] == "dep-new-web"
    assert out["production_verified"] is False and out["can_execute"] is False


def test_render_native_deploy_in_progress_is_watched_not_duplicated():
    deploys = {role: [{"id": f"dep-native-{role}", "status": "build_in_progress", "commit": {"id": SHA}},
                      {"id": f"dep-old-{role}", "status": "live", "commit": {"id": OLD}}]
               for role in SERVICES}
    fake = Fake(deploys=deploys)
    out = _run(fake)
    assert out["status"] == "DEPLOY_LIVE_AND_HEALTHY"
    assert fake.posts == [] and out["readback"]["web_deploy_id"] == "dep-native-web"


def test_main_moving_while_watching_a_deploy_is_superseded_not_failed():
    fake = Fake(outcome="canceled")
    fake.main_after_posts = None
    original = fake.sleep

    def sleep(seconds):
        original(seconds)
        fake.main = NEWER  # Render cancels the older build once newer main lands
    out = deployer.run(target_sha=SHA, fetch=fake.fetch, sleep=sleep, health=lambda: True,
                       poll_seconds=20)
    assert out["status"] == "SUPERSEDED_NO_DEPLOY"
    assert sorted(out["posted"]) == ["web", "worker"]


def test_already_live_sha_is_observed_not_redeployed():
    fake = Fake(deploys={role: [{"id": f"dep-{role}", "status": "live", "commit": {"id": SHA}}]
                         for role in SERVICES})
    out = _run(fake)
    assert out["status"] == "DEPLOY_LIVE_AND_HEALTHY" and fake.posts == [] and fake.sleeps == 0


def test_only_the_missing_service_is_deployed():
    deploys = {"web": [{"id": "dep-web", "status": "live", "commit": {"id": SHA}}],
               "worker": [{"id": "dep-old-worker", "status": "live", "commit": {"id": OLD}}]}
    fake = Fake(deploys=deploys)
    assert _run(fake)["status"] == "DEPLOY_LIVE_AND_HEALTHY"
    assert [role for role, _ in fake.posts] == ["worker"]


def test_main_moving_before_a_write_stops_further_posts():
    fake = Fake()
    fake.main_after_posts = NEWER
    out = _run(fake)
    assert out["status"] == "SUPERSEDED_NO_DEPLOY"
    assert len(fake.posts) == 1


def test_failed_build_is_a_typed_hold():
    with pytest.raises(deployer.DeployHold, match="RENDER_DEPLOY_FAILED:web,worker"):
        _run(Fake(outcome="build_failed"))


def test_deploy_that_never_goes_live_times_out():
    with pytest.raises(deployer.DeployHold, match="RENDER_DEPLOY_TIMEOUT"):
        _run(Fake(outcome="build_in_progress"), deploy_timeout_seconds=60)


def test_unhealthy_service_after_live_is_a_hold():
    with pytest.raises(deployer.DeployHold, match="HEALTH_LIVE_NOT_SUCCESS"):
        _run(Fake(), health=False)


def test_service_identity_drift_holds_before_any_write():
    fake = Fake(services={"web": _service("web", trigger="off"), "worker": _service("worker")})
    with pytest.raises(deployer.DeployHold, match="RENDER_CI_GATED_SERVICE_CONFIGURATION_MISMATCH:web"):
        _run(fake)
    assert fake.posts == []


def _env(monkeypatch, tmp_path, **overrides):
    env = {"WOW_CAN_EXECUTE": "false", "WOW_DRY_RUN_ONLY": "true", "GITHUB_REPOSITORY": REPO,
           "GH_TOKEN": "t", "RENDER_API_KEY": "r", "TARGET_SHA": SHA,
           "DEPLOY_RECEIPT": str(tmp_path / "receipt.json")}
    env.update(overrides)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    return tmp_path / "receipt.json"


@pytest.mark.parametrize("status", sorted(deployer.NON_ADVANCING))
def test_non_advancing_outcomes_exit_zero_so_main_is_never_failed(monkeypatch, tmp_path, status):
    receipt = _env(monkeypatch, tmp_path)
    monkeypatch.setattr(deployer, "run", lambda **_: {"status": status, "can_execute": False})
    assert deployer.main() == 0
    assert json.loads(receipt.read_text())["status"] == status


@pytest.mark.parametrize("override, reason", [
    ({"WOW_CAN_EXECUTE": "true"}, "EXECUTION_INVARIANT_MISMATCH"),
    ({"GITHUB_REPOSITORY": "someone/else"}, "REPOSITORY_MISMATCH"),
    ({"RENDER_API_KEY": ""}, "API_CREDENTIAL_MISSING"),
])
def test_invariant_or_credential_gaps_fail_closed_with_typed_receipt(monkeypatch, tmp_path, override, reason):
    receipt = _env(monkeypatch, tmp_path, **override)
    assert deployer.main() == 1
    body = json.loads(receipt.read_text())
    assert body["reason"] == reason and body["can_execute"] is False
    assert "r" not in body.values() and "t" not in body.values()


def test_workflow_binds_trigger_environment_and_least_privilege():
    workflow = yaml.safe_load(WORKFLOW.read_text())
    on = workflow[True]
    assert on["workflow_run"]["workflows"] == ["wow-verify", "wow-engine-verify"]
    assert on["workflow_run"]["branches"] == ["main"]
    assert set(workflow["permissions"].values()) == {"read"}
    job = workflow["jobs"]["deploy"]
    assert job["environment"] == "wow-release"
    for clause in ("github.ref == 'refs/heads/main'",
                   "github.event.workflow_run.conclusion == 'success'",
                   "github.event.workflow_run.event == 'push'",
                   "github.event.workflow_run.head_branch == 'main'"):
        assert clause in job["if"]
    assert "head_sha" in workflow["concurrency"]["group"]
    assert workflow["concurrency"]["cancel-in-progress"] is False
    assert job["env"]["WOW_CAN_EXECUTE"] == "false"
    run_step = next(s for s in job["steps"] if "v17.required_checks_deploy" in str(s.get("run")))
    assert "continue-on-error" not in run_step  # a genuine deploy failure must stay visible
    assert run_step["working-directory"] == "artifacts/wow-engine"


def test_recovery_workflow_is_a_detected_and_sanctioned_render_writer():
    inventory = inventory_render_writers(load_workflow_texts(ROOT / ".github" / "workflows"))
    assert inventory["foreign_posts"] == []
    texts = load_workflow_texts(ROOT / ".github" / "workflows")
    from v17.github_release_journal import SANCTIONED_RENDER_WRITERS, workflow_posts_render_deploy
    assert WORKFLOW.name in SANCTIONED_RENDER_WRITERS
    assert workflow_posts_render_deploy(texts[WORKFLOW.name])
    # Any other workflow that starts posting deploys is still foreign.
    texts["rogue.yml"] = 'run: curl -X "POST" https://api.render.com/v1/services/x/deploys'
    assert inventory_render_writers(texts)["foreign_posts"] == ["rogue.yml"]
