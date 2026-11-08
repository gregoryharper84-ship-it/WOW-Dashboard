"""Negative tests: the live-SHA ancestry check never deadlocks or self-certifies."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v17 import live_deploy_ancestry as m

SHA = "a" * 40
LIVE = "b" * 40


def service(role, trigger="checksPass"):
    return {
        "id": m.SERVICES[role], "branch": "main",
        "repo": "https://github.com/" + m.REPO,
        "autoDeployTrigger": trigger,
    }


def compare(status, ahead, behind):
    return {"status": status, "ahead_by": ahead, "behind_by": behind}


def test_equal_deployment_is_contained_and_never_verified():
    out = m.classify_ancestry(
        live_sha=SHA, main_sha=SHA, comparison=compare("identical", 0, 0))
    assert out["status"] == "LIVE_SHA_CONTAINED_IN_MAIN"
    assert out["deployed_equals_main"] is True
    assert out["deployment_lag_commits"] == 0
    assert out["production_verified"] is False
    assert out["model_ready"] == "UNVERIFIED"
    assert out["can_execute"] is False


def test_lagging_deployment_is_pending_not_a_failure():
    """The checksPass circularity: a recent merge cannot already be live."""
    out = m.classify_ancestry(
        live_sha=LIVE, main_sha=SHA, comparison=compare("ahead", 4, 0))
    assert out["deployment_lag_commits"] == 4
    assert out["deployed_equals_main"] is False
    assert out["reason"] == "LIVE_SHA_BEHIND:4"
    assert out["production_verified"] is False


@pytest.mark.parametrize("status,behind", [("behind", 0), ("ahead", 1), ("diverged", 0)])
def test_live_sha_outside_main_history_is_a_hold(status, behind):
    """Production may never carry commits that protected main does not contain."""
    with pytest.raises(m.AncestryHold, match="LIVE_SHA_NOT_IN_MAIN_HISTORY"):
        m.classify_ancestry(
            live_sha=LIVE, main_sha=SHA, comparison=compare(status, 1, behind))


@pytest.mark.parametrize("live,main", [("", SHA), (SHA, ""), ("zz", SHA), (LIVE, "short")])
def test_malformed_sha_is_a_hold(live, main):
    with pytest.raises(m.AncestryHold, match="LIVE_SHA_INVALID"):
        m.classify_ancestry(live_sha=live, main_sha=main, comparison=compare("identical", 0, 0))


@pytest.mark.parametrize("payload", [
    None, {}, {"status": "identical"},
    {"status": "identical", "ahead_by": True, "behind_by": 0},
    {"status": None, "ahead_by": 0, "behind_by": 0},
    {"status": "identical", "ahead_by": 1, "behind_by": 0},
])
def test_unreadable_or_contradictory_comparison_is_a_hold(payload):
    with pytest.raises(m.AncestryHold):
        m.classify_ancestry(live_sha=SHA, main_sha=SHA, comparison=payload)


@pytest.mark.parametrize("role,field,value", [
    ("web", "id", "srv-foreign"),
    ("web", "branch", "staging"),
    ("worker", "repo", "https://github.com/foreign/repo"),
    ("worker", "autoDeployTrigger", "off"),
])
def test_service_must_remain_the_canonical_ci_gated_pair(role, field, value):
    item = service(role)
    item[field] = value
    with pytest.raises(m.AncestryHold, match="RENDER_CI_GATED_SERVICE_CONFIGURATION_MISMATCH"):
        m.assert_service(role, item)


def test_non_mapping_service_is_a_hold():
    with pytest.raises(m.AncestryHold, match="RENDER_SERVICE_READBACK_INVALID:web"):
        m.assert_service("web", None)


@pytest.mark.parametrize("credential", ["GH_TOKEN", "RENDER_API_KEY"])
def test_missing_credentials_fail_closed_before_any_read(monkeypatch, credential):
    monkeypatch.setenv("GITHUB_REPOSITORY", m.REPO)
    monkeypatch.setenv("GH_TOKEN", "fixture-token")
    monkeypatch.setenv("RENDER_API_KEY", "fixture-token")
    monkeypatch.delenv(credential, raising=False)

    def unexpected_get(*_a, **_kw):
        raise AssertionError("credential failure must stop before reading a source")

    monkeypatch.setattr(m, "_get_json", unexpected_get)
    with pytest.raises(m.AncestryHold, match="READ_ONLY_API_CREDENTIAL_MISSING"):
        m._assert_live_ancestry()


def test_repository_mismatch_is_a_hold(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "foreign/repo")
    monkeypatch.setenv("GH_TOKEN", "fixture-token")
    monkeypatch.setenv("RENDER_API_KEY", "fixture-token")
    with pytest.raises(m.AncestryHold, match="REPOSITORY_MISMATCH"):
        m._assert_live_ancestry()


def test_holds_write_a_typed_credential_free_receipt(monkeypatch, tmp_path):
    import json

    receipt = tmp_path / "ancestry.json"
    monkeypatch.setenv("ANCESTRY_RECEIPT", str(receipt))
    monkeypatch.setenv("GITHUB_REPOSITORY", m.REPO)
    monkeypatch.setenv("GH_TOKEN", "fixture-token")
    monkeypatch.setenv("RENDER_API_KEY", "fixture-token")

    def getter(url, _headers):
        if url.endswith("/branches/main"):
            return {"commit": {"sha": SHA}}
        if "/services/" in url and "/deploys?" not in url:
            role = next(role for role, sid in m.SERVICES.items() if sid in url)
            return service(role)
        if "/deploys?" in url:
            role = next(role for role, sid in m.SERVICES.items() if sid in url)
            return [{"deploy": {"id": "dep-" + role, "status": "live",
                                "commit": {"id": LIVE if role == "worker" else SHA}}}]
        if "/compare/" in url:
            # worker live commit is ahead of main: contained nowhere.
            if url.endswith(SHA + "..." + SHA):
                return compare("identical", 0, 0)
            return compare("behind", 0, 1)
        raise AssertionError("Unexpected authenticated GET " + url)

    monkeypatch.setattr(m, "_get_json", getter)
    assert m.main() == 1
    body = json.loads(receipt.read_text())
    assert body["status"] == "ANCESTRY_HOLD"
    assert body["reason"].startswith("LIVE_SHA_NOT_IN_MAIN_HISTORY")
    assert body["production_verified"] is False
    assert "fixture-token" not in receipt.read_text()


def test_lagging_worker_writes_a_passing_pending_receipt(monkeypatch, tmp_path):
    import json

    receipt = tmp_path / "ancestry.json"
    monkeypatch.setenv("ANCESTRY_RECEIPT", str(receipt))
    monkeypatch.setenv("GITHUB_REPOSITORY", m.REPO)
    monkeypatch.setenv("GH_TOKEN", "fixture-token")
    monkeypatch.setenv("RENDER_API_KEY", "fixture-token")

    def getter(url, _headers):
        if url.endswith("/branches/main"):
            return {"commit": {"sha": SHA}}
        if "/services/" in url and "/deploys?" not in url:
            role = next(role for role, sid in m.SERVICES.items() if sid in url)
            return service(role)
        if "/deploys?" in url:
            role = next(role for role, sid in m.SERVICES.items() if sid in url)
            return [{"deploy": {"id": "dep-" + role, "status": "live",
                                "commit": {"id": LIVE}}}]
        if "/compare/" in url:
            return compare("ahead", 4, 0)
        raise AssertionError("Unexpected authenticated GET " + url)

    monkeypatch.setattr(m, "_get_json", getter)
    assert m.main() == 0
    body = json.loads(receipt.read_text())
    assert body["status"] == "BOTH_SERVICES_LIVE_SHA_CONTAINED_IN_MAIN"
    assert body["observed_lag_commits"] == 4
    assert body["production_verified"] is False


def test_verify_workflow_runs_the_ancestry_check_off_the_deploy_commit_set():
    """The assertion must be wired, non-deploying, and main-scoped only."""
    import yaml

    root = Path(__file__).resolve().parents[2]
    text = (root / ".github/workflows/wow-engine-verify.yml").read_text()
    workflow = yaml.load(text, Loader=yaml.BaseLoader)
    job = workflow["jobs"]["deployment-truth"]
    assert job["if"] == ("github.repository == 'gregoryharper84-ship-it/WOW-Dashboard' "
                         "&& (github.event_name == 'push' || github.event_name == 'workflow_dispatch')")
    assert job["permissions"] == {"contents": "read"}
    step = next(s for s in job["steps"] if s.get("id") == "ancestry")
    assert "v17.live_deploy_ancestry" in step["run"]
    assert "render.com/v1/services" not in text
    assert "gh pr merge" not in text
    assert "RENDER_API_KEY" in step["env"]
