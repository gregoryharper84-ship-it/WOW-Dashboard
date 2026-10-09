"""Regression for the #1550 agent identity / protection policy verifier."""
import copy
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "artifacts/wow-engine/v17/agent_identity_policy.py"
PROPOSED = ROOT / "artifacts/wow-engine/v17/agent_identity"
OWNER = "gregoryharper84-ship-it"
APP_IDS = {"engineering": "1001", "qa": "1002", "release": "1003"}


def _load():
    spec = importlib.util.spec_from_file_location("agent_identity_policy", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


m = _load()
TRUST_ROOTS = m.trust_root_paths(ROOT)


def _proposed_ruleset():
    text = (PROPOSED / "ruleset-main.proposed.json").read_text()
    text = text.replace("__WOW_QA_APP_ID__", APP_IDS["qa"]).replace("__WOW_RELEASE_APP_ID__", APP_IDS["release"])
    return json.loads(text)


def compliant():
    return {
        "owner_login": OWNER,
        "role_bindings": dict(APP_IDS),
        "installations": [
            {"app_id": int(APP_IDS[role]), "repository_selection": "selected", "permissions": dict(perms)}
            for role, perms in m.APP_PERMISSIONS.items()
        ],
        "ai_credential": {"login": "wow-engineering[bot]", "acts_as_owner_user": False, "can_merge": False},
        "codeowners": (PROPOSED / "CODEOWNERS.proposed").read_text(),
        "rulesets": [_proposed_ruleset()],
    }


def run(snapshot):
    return m.evaluate(snapshot, trust_roots=TRUST_ROOTS)


def install(snapshot, role):
    return next(i for i in snapshot["installations"] if str(i["app_id"]) == APP_IDS[role])


def rule(snapshot, kind):
    return next(r for r in snapshot["rulesets"][0]["rules"] if r["type"] == kind)


# --- the proposal is sufficient; today's state is not -------------------------


def test_proposed_configuration_passes():
    result = run(compliant())
    assert result["status"] == "PASS", result["findings"]
    assert result["merge_or_release_authorized"] is False
    assert result["can_execute"] is False


def test_measured_current_state_holds_for_the_exact_reasons():
    """Mirrors what was measured on 2026-10-09 (evidence on #1550)."""
    current = {
        "owner_login": OWNER,
        "role_bindings": {"engineering": None, "qa": None, "release": None},
        "installations": [],
        "ai_credential": {"login": OWNER, "acts_as_owner_user": True, "can_merge": True},
        "codeowners": None,
        "rulesets": [],
    }
    result = run(current)
    assert result["status"] == "HOLD"
    assert result["disposition"] == "BLOCKED_WITH_EXACT_REASON"
    for code in (
        "IDENTITY_APP_UNBOUND:engineering", "IDENTITY_APP_UNBOUND:qa", "IDENTITY_APP_UNBOUND:release",
        "IDENTITY_AI_CREDENTIAL_CAN_MERGE", "IDENTITY_AI_CREDENTIAL_IS_OWNER_USER",
        "PROTECTION_CODEOWNERS_MISSING", "PROTECTION_RULESET_MISSING",
    ):
        assert code in result["findings"], code
    assert result["merge_or_release_authorized"] is False


# --- identity separation ------------------------------------------------------


def test_unreadable_inputs_are_reported_not_assumed_absent():
    s = compliant()
    s["collection_errors"] = ["VARIABLES_UNREADABLE:403"]
    result = run(s)
    assert result["status"] == "HOLD"
    assert "IDENTITY_SNAPSHOT_INCOMPLETE:VARIABLES_UNREADABLE:403" in result["findings"]


def test_one_app_for_two_roles_is_not_independent():
    s = compliant()
    s["role_bindings"]["release"] = APP_IDS["qa"]
    assert "IDENTITY_APPS_NOT_DISTINCT" in run(s)["findings"]


@pytest.mark.parametrize("role, perm, level, code", [
    ("engineering", "administration", "write", "IDENTITY_PERMISSION_FORBIDDEN:engineering:administration:write"),
    ("release", "secrets", "read", "IDENTITY_PERMISSION_FORBIDDEN:release:secrets:read"),
    ("qa", "contents", "write", "IDENTITY_PERMISSION_EXCESS:qa:contents:write"),
    ("qa", "pull_requests", "write", "IDENTITY_PERMISSION_EXCESS:qa:pull_requests:write"),
    ("release", "workflows", "write", "IDENTITY_PERMISSION_EXCESS:release:workflows:write"),
    ("engineering", "deployments", "write", "IDENTITY_PERMISSION_EXCESS:engineering:deployments:write"),
])
def test_excess_or_forbidden_permission_holds(role, perm, level, code):
    s = compliant()
    install(s, role)["permissions"][perm] = level
    assert code in run(s)["findings"]


def test_missing_permission_holds():
    s = compliant()
    del install(s, "release")["permissions"]["deployments"]
    install(s, "qa")["permissions"]["checks"] = "read"
    findings = run(s)["findings"]
    assert "IDENTITY_PERMISSION_MISSING:release:deployments:write" in findings
    assert "IDENTITY_PERMISSION_MISSING:qa:checks:write" in findings


def test_installation_must_be_scoped_to_selected_repositories():
    s = compliant()
    install(s, "engineering")["repository_selection"] = "all"
    assert "IDENTITY_INSTALLATION_NOT_REPO_SCOPED:engineering" in run(s)["findings"]


def test_bound_but_uninstalled_app_holds():
    s = compliant()
    s["installations"] = [i for i in s["installations"] if str(i["app_id"]) != APP_IDS["qa"]]
    assert "IDENTITY_APP_NOT_INSTALLED:qa" in run(s)["findings"]


def test_unmeasured_ai_merge_capability_is_not_assumed_safe():
    s = compliant()
    s["ai_credential"].pop("can_merge")
    assert "IDENTITY_AI_CREDENTIAL_UNMEASURED" in run(s)["findings"]


# --- protection --------------------------------------------------------------


def test_qa_check_satisfiable_by_release_app_is_not_source_pinned():
    s = compliant()
    checks = rule(s, "required_status_checks")["parameters"]["required_status_checks"]
    next(c for c in checks if c["context"] == m.QA_CHECK)["integration_id"] = APP_IDS["release"]
    assert f"PROTECTION_CHECK_NOT_SOURCE_PINNED:{m.QA_CHECK}" in run(s)["findings"]


def test_unpinned_release_check_holds():
    s = compliant()
    checks = rule(s, "required_status_checks")["parameters"]["required_status_checks"]
    next(c for c in checks if c["context"] == m.RELEASE_CHECK).pop("integration_id")
    assert f"PROTECTION_CHECK_NOT_SOURCE_PINNED:{m.RELEASE_CHECK}" in run(s)["findings"]


def test_any_bypass_actor_holds_while_ai_can_act_as_owner():
    s = compliant()
    s["rulesets"][0]["bypass_actors"] = [{"actor_type": "RepositoryRole", "actor_id": 5, "bypass_mode": "pull_request"}]
    assert "PROTECTION_BYPASS_PRESENT:RepositoryRole:5" in run(s)["findings"]


@pytest.mark.parametrize("mutate, code", [
    (lambda s: rule(s, "pull_request")["parameters"].update(require_code_owner_review=False),
     "PROTECTION_CODE_OWNER_REVIEW_NOT_REQUIRED"),
    (lambda s: rule(s, "pull_request")["parameters"].update(require_last_push_approval=False),
     "PROTECTION_LAST_PUSH_APPROVAL_NOT_REQUIRED"),
    (lambda s: rule(s, "pull_request")["parameters"].update(dismiss_stale_reviews_on_push=False),
     "PROTECTION_STALE_REVIEWS_NOT_DISMISSED"),
    (lambda s: rule(s, "required_status_checks")["parameters"].update(strict_required_status_checks_policy=False),
     "PROTECTION_CHECKS_NOT_STRICT"),
    (lambda s: s["rulesets"][0]["rules"].remove(rule(s, "non_fast_forward")), "PROTECTION_FORCE_PUSH_ALLOWED"),
    (lambda s: s["rulesets"][0]["rules"].remove(rule(s, "deletion")), "PROTECTION_DELETION_ALLOWED"),
    (lambda s: s["rulesets"][0].update(enforcement="evaluate"), "PROTECTION_RULESET_MISSING"),
    (lambda s: s["rulesets"][0]["conditions"]["ref_name"].update(include=["refs/heads/dev"]),
     "PROTECTION_RULESET_MISSING"),
])
def test_weakened_ruleset_holds(mutate, code):
    s = compliant()
    mutate(s)
    assert code in run(s)["findings"]


def test_missing_regression_check_holds():
    s = compliant()
    params = rule(s, "required_status_checks")["parameters"]
    params["required_status_checks"] = [c for c in params["required_status_checks"]
                                        if c["context"] != "WOW governed probability backend"]
    assert "PROTECTION_CHECK_MISSING:WOW governed probability backend" in run(s)["findings"]


# --- CODEOWNERS ---------------------------------------------------------------


def test_listing_only_named_files_leaves_new_workflows_unprotected():
    s = compliant()
    s["codeowners"] = "\n".join(f"/{p} @{OWNER}" for p in TRUST_ROOTS) + f"\n/.github/CODEOWNERS @{OWNER}\n"
    findings = run(s)["findings"]
    assert "PROTECTION_CODEOWNERS_UNCOVERED:.github/workflows/__new_workflow__.yml" in findings
    assert "PROTECTION_CODEOWNERS_UNCOVERED:.github/actions/__new_action__/action.yml" in findings


def test_later_codeowners_rule_overriding_owner_is_detected():
    s = compliant()
    s["codeowners"] += "/.github/workflows/ @someone-else\n"
    assert "PROTECTION_CODEOWNERS_UNCOVERED:.github/workflows/wow-v17-claude-engineering-worker.yml" in run(s)["findings"]


@pytest.mark.parametrize("pattern, path, expected", [
    ("/.github/", ".github/workflows/x.yml", True),
    ("/.github/", "docs/.github/x", False),
    ("*.yml", ".github/workflows/x.yml", True),
    ("/.github/actions/wow-claude-agent/*", ".github/actions/wow-claude-agent/action.yml", True),
    ("/.github/actions/wow-claude-agent/*", ".github/actions/wow-claude-agent/sub/deep.yml", True),
    ("/.github/workflows/a.yml", ".github/workflows/a.yml.bak", False),
    ("/.github/**/x.py", ".github/scripts/deep/x.py", True),
])
def test_codeowners_pattern_semantics(pattern, path, expected):
    assert bool(m._codeowners_regex(pattern).match(path)) is expected


def test_proposed_codeowners_names_every_protected_trust_root():
    text = (PROPOSED / "CODEOWNERS.proposed").read_text()
    for path in TRUST_ROOTS:
        assert f"/{path} @{OWNER}" in text


# --- spec, docs and registry stay in sync ---------------------------------------


def test_setup_doc_permission_table_matches_policy():
    doc = (ROOT / "artifacts/wow-engine/docs/v17_agent_identity_setup.md").read_text()
    for role, perms in m.APP_PERMISSIONS.items():
        for perm, level in perms.items():
            assert re.search(rf"\| {role} \| `{perm}` \| {level} \|", doc), (role, perm)
    assert m.QA_CHECK in doc and m.RELEASE_CHECK in doc


def test_every_finding_code_is_registered():
    source = MODULE_PATH.read_text()
    emitted = set(re.findall(r'"((?:IDENTITY|PROTECTION)_[A-Z_]+)', source))
    emitted |= set(re.findall(r'f"((?:IDENTITY|PROTECTION)_[A-Z_]+):', source))
    registry = (ROOT / "artifacts/wow-engine/docs/failure_codes.md").read_text()
    section = registry.split("## Agent identity and protection policy codes", 1)[1].split("\n## ", 1)[0]
    registered = set(re.findall(r"^\| `([A-Z_]+)` \|", section, re.M))
    assert emitted == registered, (emitted - registered, registered - emitted)


def test_cli_evaluate_exit_codes(tmp_path):
    import subprocess

    good = tmp_path / "good.json"
    good.write_text(json.dumps(compliant()))
    bad = tmp_path / "bad.json"
    worse = copy.deepcopy(compliant())
    worse["rulesets"] = []
    bad.write_text(json.dumps(worse))
    for path, rc in ((good, 0), (bad, 2)):
        proc = subprocess.run([sys.executable, str(MODULE_PATH), "evaluate", "--snapshot", str(path),
                               "--repo-root", str(ROOT)], capture_output=True, text=True)
        assert proc.returncode == rc, proc.stderr
        assert json.loads(proc.stdout)["can_execute"] is False
