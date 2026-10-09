"""Regression for the #1550 agent identity / protection policy verifier.

Sections map to the independent review of PR #1551 (findings 1-6).
"""
import base64
import copy
import importlib.util
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "artifacts/wow-engine/v17/agent_identity_policy.py"
PROPOSED = ROOT / "artifacts/wow-engine/v17/agent_identity"
OWNER = "gregoryharper84-ship-it"
REPO = f"{OWNER}/WOW-Dashboard"
REPO_ID = 990001
APP_IDS = {"engineering": "1001", "qa": "1002", "release": "1003"}
NONCE = "a" * 32
NOW = datetime(2026, 10, 9, 3, 0, tzinfo=timezone.utc)


def _load():
    spec = importlib.util.spec_from_file_location("agent_identity_policy", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


m = _load()
TRUST_ROOTS = m.trust_root_paths(ROOT)


def _iso(stamp):
    return stamp.isoformat().replace("+00:00", "Z")


def _proposed_ruleset():
    text = (PROPOSED / "ruleset-main.proposed.json").read_text()
    text = text.replace("__WOW_QA_APP_ID__", APP_IDS["qa"]).replace("__WOW_RELEASE_APP_ID__", APP_IDS["release"])
    return json.loads(text)


def owner_inventory():
    return {
        "kind": "owner_inventory", "repository": REPO, "repository_id": REPO_ID, "nonce": NONCE,
        "collected_at": _iso(NOW), "collection_errors": [],
        "owner_login": OWNER, "collector_login": OWNER,          # owner-collected (finding 1)
        "role_bindings": dict(APP_IDS),
        "installations": [
            {"id": 7000 + i, "app_id": int(APP_IDS[role]), "repository_selection": "selected",
             "repository_ids": [REPO_ID], "permissions": dict(perms)}
            for i, (role, perms) in enumerate(m.APP_PERMISSIONS.items())
        ],
        "codeowners": (PROPOSED / "CODEOWNERS.proposed").read_text(),
        "codeowners_location": ".github/CODEOWNERS", "codeowners_confirmed_absent": False,
        "rulesets": [_proposed_ruleset()],
    }


def principal(runtime, **over):
    rec = {"kind": "ai_principal", "runtime": runtime, "repository": REPO, "repository_id": REPO_ID,
           "nonce": NONCE, "collected_at": _iso(NOW - timedelta(hours=1)),
           "expires_at": _iso(NOW + timedelta(hours=11)), "login": "wow-engineering[bot]",
           "account_type": "Bot", "merge_probe": {"pr": 1, "status": 403, "verdict": "DENIED"}}
    rec.update(over)
    return rec


def live(**over):
    records = [{"probe": p, "expected": e, "observed": e, "pr": 4000 + i, "head_sha": "c" * 40,
                "observed_at": _iso(NOW), "nonce": NONCE}
               for i, (p, e) in enumerate(m.LIVE_ACCEPTANCE_PROBES.items())]
    for rec in records:
        rec.update(over.get(rec["probe"], {}))
    return records


def run(owner=None, principals=None, live_acceptance=None, now=NOW):
    return m.evaluate(owner if owner is not None else owner_inventory(),
                      principals=[principal(r) for r in m.AI_RUNTIMES] if principals is None else principals,
                      live_acceptance=live() if live_acceptance is None else live_acceptance,
                      trust_roots=TRUST_ROOTS, now=now)


def install(owner, role):
    return next(i for i in owner["installations"] if str(i["app_id"]) == APP_IDS[role])


def rule(owner, kind):
    return next(r for r in owner["rulesets"][0]["rules"] if r["type"] == kind)


# --- baseline ----------------------------------------------------------------------


def test_fully_evidenced_configuration_passes_but_closes_nothing():
    result = run()
    assert result["status"] == "PASS", result["findings"]
    assert result["disposition"] is None
    assert result["closure_eligible"] is False
    assert result["merge_or_release_authorized"] is False and result["can_execute"] is False


# --- finding 1: owner inventory and AI principal evidence are separate -----------


def test_owner_collected_inventory_is_not_mistaken_for_an_ai_principal():
    """The owner's own login/merge rights must never produce AI findings."""
    findings = run()["findings"]
    assert not any("AI_CREDENTIAL" in f for f in findings)


def test_owner_only_snapshot_holds_as_unmeasured_not_as_ai_acting_as_owner():
    findings = run(principals=[])["findings"]
    for runtime in m.AI_RUNTIMES:
        assert f"IDENTITY_AI_PRINCIPAL_UNMEASURED:{runtime}" in findings
    assert not any(f.startswith("IDENTITY_AI_CREDENTIAL_IS_OWNER_USER") for f in findings)


def test_ai_runtime_still_acting_as_owner_is_detected_per_runtime():
    principals = [principal("claude_session", login=OWNER, account_type="User"), principal("chatgpt_session")]
    assert "IDENTITY_AI_CREDENTIAL_IS_OWNER_USER:claude_session" in run(principals=principals)["findings"]


@pytest.mark.parametrize("over, code", [
    ({"nonce": "b" * 32}, "IDENTITY_AI_PRINCIPAL_NONCE_MISMATCH"),
    ({"repository_id": 1}, "IDENTITY_AI_PRINCIPAL_REPO_MISMATCH"),
    ({"expires_at": _iso(NOW - timedelta(minutes=1))}, "IDENTITY_AI_PRINCIPAL_EVIDENCE_STALE"),
    ({"collected_at": _iso(NOW - timedelta(hours=30)), "expires_at": _iso(NOW + timedelta(hours=1))},
     "IDENTITY_AI_PRINCIPAL_EVIDENCE_STALE"),
    ({"login": None}, "IDENTITY_AI_PRINCIPAL_EVIDENCE_INVALID"),
    ({"kind": "owner_inventory"}, "IDENTITY_AI_PRINCIPAL_EVIDENCE_INVALID"),
])
def test_principal_evidence_binding(over, code):
    principals = [principal("claude_session", **over), principal("chatgpt_session")]
    assert f"{code}:claude_session" in run(principals=principals)["findings"]


def test_duplicate_principal_evidence_is_ambiguous():
    principals = [principal("claude_session"), principal("claude_session"), principal("chatgpt_session")]
    assert "IDENTITY_AI_PRINCIPAL_EVIDENCE_AMBIGUOUS:claude_session" in run(principals=principals)["findings"]


def test_snapshot_not_collected_by_owner_holds():
    owner = owner_inventory()
    owner["collector_login"] = "someone-else"
    assert "IDENTITY_SNAPSHOT_NOT_OWNER_COLLECTED" in run(owner)["findings"]


# --- finding 2: installation must include THIS repository ------------------------


def test_installation_on_a_different_repository_does_not_count():
    owner = owner_inventory()
    install(owner, "qa")["repository_ids"] = [123]
    assert "IDENTITY_INSTALLATION_REPO_NOT_INCLUDED:qa" in run(owner)["findings"]


def test_installation_repository_list_unknown_is_incomplete():
    owner = owner_inventory()
    install(owner, "release")["repository_ids"] = None
    assert "IDENTITY_SNAPSHOT_INCOMPLETE:INSTALLATION_REPOSITORIES_MISSING:release" in run(owner)["findings"]


def test_installation_on_all_repositories_is_not_scoped():
    owner = owner_inventory()
    install(owner, "engineering")["repository_selection"] = "all"
    assert "IDENTITY_INSTALLATION_NOT_REPO_SCOPED:engineering" in run(owner)["findings"]


# --- finding 3: collector types every unreadable / malformed input ---------------


class FakeGitHub:
    def __init__(self, **overrides):
        ruleset = _proposed_ruleset()
        ruleset["id"] = 55
        self.routes = {
            "user": (200, {"login": OWNER, "type": "User"}),
            f"repos/{REPO}": (200, {"id": REPO_ID, "owner": {"login": OWNER}}),
            f"repos/{REPO}/rulesets?per_page=100&includes_parents=true": (200, [{"id": 55}]),
            f"repos/{REPO}/rulesets/55": (200, ruleset),
            f"repos/{REPO}/contents/.github/CODEOWNERS": (200, {
                "encoding": "base64",
                "content": base64.b64encode((PROPOSED / "CODEOWNERS.proposed").read_bytes()).decode()}),
            f"repos/{REPO}/actions/variables?per_page=30": (200, {"total_count": 3, "variables": [
                {"name": f"WOW_{r.upper()}_APP_ID", "value": v} for r, v in APP_IDS.items()]}),
            "user/installations?per_page=100": (200, {"total_count": 3, "installations": [
                {"id": 7000 + i, "app_id": int(APP_IDS[r]), "app_slug": f"wow-{r}",
                 "repository_selection": "selected", "permissions": dict(p)}
                for i, (r, p) in enumerate(m.APP_PERMISSIONS.items())]}),
        }
        for i in range(3):
            self.routes[f"user/installations/{7000 + i}/repositories?per_page=100"] = (
                200, {"total_count": 1, "repositories": [{"id": REPO_ID}]})
        self.routes.update(overrides)

    def __call__(self, path):
        return self.routes.get(path, (404, {"message": "Not Found"}))


def collected(**overrides):
    return m.collect_owner(REPO, fetch=FakeGitHub(**overrides), now=NOW)


def test_collector_with_complete_access_feeds_a_pass():
    owner = collected()
    assert owner["collection_errors"] == []
    owner["nonce"] = NONCE
    assert run(owner)["status"] == "PASS", run(owner)["findings"]


@pytest.mark.parametrize("override, error, must_not", [
    ({f"repos/{REPO}/rulesets?per_page=100&includes_parents=true": (403, {})}, "RULESETS_UNREADABLE:403",
     "PROTECTION_RULESET_MISSING"),
    ({f"repos/{REPO}/rulesets/55": (200, {"id": 55})}, "RULESET_DETAIL_UNREADABLE:55:200",
     "PROTECTION_RULESET_MISSING"),
    ({f"repos/{REPO}/contents/.github/CODEOWNERS": (403, {})}, "CODEOWNERS_UNREADABLE:.github/CODEOWNERS:403",
     "PROTECTION_CODEOWNERS_MISSING"),
    ({f"repos/{REPO}/actions/variables?per_page=30": (403, {})}, "VARIABLES_UNREADABLE:403",
     "IDENTITY_APP_UNBOUND"),
    ({"user/installations?per_page=100": (500, None)}, "INSTALLATIONS_UNREADABLE:500",
     "IDENTITY_APP_NOT_INSTALLED"),
    ({f"repos/{REPO}": (403, {})}, "REPOSITORY_UNREADABLE:403", None),
    ({f"repos/{REPO}": (200, {"owner": {"login": OWNER}})}, "REPOSITORY_UNREADABLE:200", None),
    ({"user": (401, {})}, "COLLECTOR_IDENTITY_UNREADABLE:401", None),
    ({"user/installations/7001/repositories?per_page=100": (403, {})},
     "INSTALLATION_REPOSITORIES_UNREADABLE:1002:403", None),
    ({"user/installations/7002/repositories?per_page=100": (200, {"total_count": 150, "repositories": [{"id": REPO_ID}]})},
     "INSTALLATION_REPOSITORIES_TRUNCATED:1003", None),
    ({f"repos/{REPO}/rulesets?per_page=100&includes_parents=true": (200, [{"id": 55}] * 100)},
     "RULESETS_TRUNCATED", None),
])
def test_unreadable_inputs_are_typed_and_never_read_as_absent(override, error, must_not):
    owner = collected(**override)
    assert error in owner["collection_errors"]
    owner["nonce"] = NONCE
    findings = run(owner)["findings"]
    assert f"IDENTITY_SNAPSHOT_INCOMPLETE:{error}" in findings
    assert run(owner)["status"] == "HOLD"
    if must_not:
        assert not any(f.startswith(must_not) for f in findings), findings


def test_codeowners_missing_only_when_every_location_is_confirmed_404():
    owner = collected(**{f"repos/{REPO}/contents/.github/CODEOWNERS": (404, {})})
    assert owner["codeowners_confirmed_absent"] is True
    owner["nonce"] = NONCE
    assert "PROTECTION_CODEOWNERS_MISSING" in run(owner)["findings"]


def test_codeowners_found_in_later_github_location():
    content = {"encoding": "base64", "content": base64.b64encode(b"/.github/ @x\n").decode()}
    owner = collected(**{f"repos/{REPO}/contents/.github/CODEOWNERS": (404, {}),
                         f"repos/{REPO}/contents/CODEOWNERS": (200, content)})
    assert owner["codeowners_location"] == "CODEOWNERS" and owner["codeowners_confirmed_absent"] is False


@pytest.mark.parametrize("raw, expected", [
    ("HTTP/2.0 200 OK\r\nX: y\r\n\r\n{\"a\": 1}", (200, {"a": 1})),
    ("HTTP/1.1 403 Forbidden\nX: y\n\n{\"message\": \"no\"}", (403, {"message": "no"})),
    ("HTTP/2.0 200 OK\r\n\r\nnot-json", (200, None)),
    ("garbage", (0, None)),
])
def test_gh_response_parsing(raw, expected):
    assert m._parse_gh_response(raw) == expected


# --- finding 4: merge probe is bounded and read fail-closed ----------------------


@pytest.mark.parametrize("status, message, verdict", [
    (403, "Resource not accessible by integration", "DENIED"),
    (404, "Not Found", "DENIED"),
    (409, "Head branch was modified. Review and try the merge again.", "NOT_DENIED"),
    (409, "Merge conflict", "INCONCLUSIVE"),
    (405, "Pull Request is not mergeable", "INCONCLUSIVE"),
    (422, "Required status check is expected", "INCONCLUSIVE"),
    (500, "", "INCONCLUSIVE"),
    (0, "", "INCONCLUSIVE"),
])
def test_merge_probe_interpretation(status, message, verdict):
    assert m.interpret_merge_probe(status, message) == verdict


@pytest.mark.parametrize("verdict, code", [
    ("NOT_DENIED", "IDENTITY_AI_CREDENTIAL_CAN_MERGE"),
    ("INCONCLUSIVE", "IDENTITY_AI_MERGE_PROBE_INCONCLUSIVE"),
    (None, "IDENTITY_AI_MERGE_PROBE_INCONCLUSIVE"),
])
def test_only_a_proven_denial_passes(verdict, code):
    principals = [principal("claude_session", merge_probe={"verdict": verdict}), principal("chatgpt_session")]
    assert f"{code}:claude_session" in run(principals=principals)["findings"]


def test_merge_probe_skipped_unless_target_pr_is_open():
    calls = []
    fetch = FakeGitHub(**{f"repos/{REPO}/pulls/9": (200, {"state": "closed", "merged": False})})
    rec = m.collect_principal(REPO, runtime="claude_session", nonce=NONCE, merge_probe_pr=9, fetch=fetch,
                              merge_attempt=lambda p: calls.append(p) or (409, "Head branch was modified"), now=NOW)
    assert calls == []
    assert rec["merge_probe"]["verdict"] == "INCONCLUSIVE"


def test_merge_probe_makes_exactly_one_bounded_attempt_on_an_open_pr():
    calls = []
    fetch = FakeGitHub(**{f"repos/{REPO}/pulls/9": (200, {"state": "open", "merged": False})})
    rec = m.collect_principal(REPO, runtime="claude_session", nonce=NONCE, merge_probe_pr=9, fetch=fetch,
                              merge_attempt=lambda p: calls.append(p) or (403, "denied"), now=NOW)
    assert calls == [f"repos/{REPO}/pulls/9/merge"]
    assert rec["merge_probe"]["verdict"] == "DENIED"
    assert rec["nonce"] == NONCE and rec["repository_id"] == REPO_ID
    assert '"sha=" + "0" * 40' in MODULE_PATH.read_text()


def test_principal_evidence_ttl_is_capped():
    rec = m.collect_principal(REPO, runtime="chatgpt_session", nonce=NONCE, merge_probe_pr=None,
                              fetch=FakeGitHub(), now=NOW, ttl=timedelta(days=30))
    assert m._parse_time(rec["expires_at"]) - NOW == m.MAX_EVIDENCE_TTL


# --- finding 5: no false closure vocabulary --------------------------------------


@pytest.mark.parametrize("scenario", ["pass", "hold"])
def test_disposition_is_never_a_closure(scenario):
    result = run() if scenario == "pass" else run(principals=[])
    assert result["disposition"] in m.ALLOWED_DISPOSITIONS | {None}
    assert "FIXED" not in json.dumps(result)
    assert result["closure_eligible"] is False


# --- finding 6: live allow/deny acceptance is mandatory ---------------------------


def test_plausible_json_without_live_probes_holds():
    findings = run(live_acceptance=[])["findings"]
    for probe in m.LIVE_ACCEPTANCE_PROBES:
        assert f"PROTECTION_LIVE_PROBE_MISSING:{probe}" in findings


def test_ordinary_pr_blocked_or_trust_root_allowed_fails_acceptance():
    records = live(ordinary_app_pr_merges_without_human_review={"observed": "DENY"},
                   trust_root_pr_blocked_without_owner_approval={"observed": "ALLOW"})
    findings = run(live_acceptance=records)["findings"]
    assert "PROTECTION_LIVE_PROBE_FAILED:ordinary_app_pr_merges_without_human_review" in findings
    assert "PROTECTION_LIVE_PROBE_FAILED:trust_root_pr_blocked_without_owner_approval" in findings


@pytest.mark.parametrize("over", [
    {"nonce": "z" * 32}, {"head_sha": "abc"}, {"pr": "12"}, {"observed_at": "yesterday"},
    {"expected": "ALLOW"}, {"observed": "MAYBE"},
])
def test_malformed_live_probe_record_is_invalid(over):
    records = live(push_after_owner_approval_requires_reapproval=over)
    assert "PROTECTION_LIVE_PROBE_INVALID:push_after_owner_approval_requires_reapproval" in run(
        live_acceptance=records)["findings"]


def test_required_live_probes_cover_the_reviewed_risks():
    probes = m.LIVE_ACCEPTANCE_PROBES
    assert probes["ordinary_app_pr_merges_without_human_review"] == "ALLOW"
    for deny in ("trust_root_pr_blocked_without_owner_approval", "push_after_owner_approval_requires_reapproval",
                 "new_workflow_file_requires_owner_approval", "ai_session_cannot_merge",
                 "qa_check_published_by_release_app_does_not_satisfy", "engineering_app_cannot_merge"):
        assert probes[deny] == "DENY"


# --- identity separation and protection (unchanged policy) -------------------------


def test_one_app_for_two_roles_is_not_independent():
    owner = owner_inventory()
    owner["role_bindings"]["release"] = APP_IDS["qa"]
    assert "IDENTITY_APPS_NOT_DISTINCT" in run(owner)["findings"]


@pytest.mark.parametrize("role, perm, level, code", [
    ("engineering", "administration", "write", "IDENTITY_PERMISSION_FORBIDDEN:engineering:administration:write"),
    ("release", "secrets", "read", "IDENTITY_PERMISSION_FORBIDDEN:release:secrets:read"),
    ("qa", "contents", "write", "IDENTITY_PERMISSION_EXCESS:qa:contents:write"),
    ("qa", "pull_requests", "write", "IDENTITY_PERMISSION_EXCESS:qa:pull_requests:write"),
    ("release", "workflows", "write", "IDENTITY_PERMISSION_EXCESS:release:workflows:write"),
    ("engineering", "deployments", "write", "IDENTITY_PERMISSION_EXCESS:engineering:deployments:write"),
])
def test_excess_or_forbidden_permission_holds(role, perm, level, code):
    owner = owner_inventory()
    install(owner, role)["permissions"][perm] = level
    assert code in run(owner)["findings"]


def test_missing_permission_holds():
    owner = owner_inventory()
    del install(owner, "release")["permissions"]["deployments"]
    install(owner, "qa")["permissions"]["checks"] = "read"
    findings = run(owner)["findings"]
    assert "IDENTITY_PERMISSION_MISSING:release:deployments:write" in findings
    assert "IDENTITY_PERMISSION_MISSING:qa:checks:write" in findings


def test_qa_check_satisfiable_by_release_app_is_not_source_pinned():
    owner = owner_inventory()
    checks = rule(owner, "required_status_checks")["parameters"]["required_status_checks"]
    next(c for c in checks if c["context"] == m.QA_CHECK)["integration_id"] = APP_IDS["release"]
    assert f"PROTECTION_CHECK_NOT_SOURCE_PINNED:{m.QA_CHECK}" in run(owner)["findings"]


def test_any_bypass_actor_holds():
    owner = owner_inventory()
    owner["rulesets"][0]["bypass_actors"] = [{"actor_type": "RepositoryRole", "actor_id": 5, "bypass_mode": "pull_request"}]
    assert "PROTECTION_BYPASS_PRESENT:RepositoryRole:5" in run(owner)["findings"]


@pytest.mark.parametrize("mutate, code", [
    (lambda o: rule(o, "pull_request")["parameters"].update(require_code_owner_review=False),
     "PROTECTION_CODE_OWNER_REVIEW_NOT_REQUIRED"),
    (lambda o: rule(o, "pull_request")["parameters"].update(require_last_push_approval=False),
     "PROTECTION_LAST_PUSH_APPROVAL_NOT_REQUIRED"),
    (lambda o: rule(o, "pull_request")["parameters"].update(dismiss_stale_reviews_on_push=False),
     "PROTECTION_STALE_REVIEWS_NOT_DISMISSED"),
    (lambda o: rule(o, "required_status_checks")["parameters"].update(strict_required_status_checks_policy=False),
     "PROTECTION_CHECKS_NOT_STRICT"),
    (lambda o: o["rulesets"][0]["rules"].remove(rule(o, "non_fast_forward")), "PROTECTION_FORCE_PUSH_ALLOWED"),
    (lambda o: o["rulesets"][0]["rules"].remove(rule(o, "deletion")), "PROTECTION_DELETION_ALLOWED"),
    (lambda o: o["rulesets"][0].update(enforcement="evaluate"), "PROTECTION_RULESET_MISSING"),
    (lambda o: o["rulesets"][0]["conditions"]["ref_name"].update(include=["refs/heads/dev"]),
     "PROTECTION_RULESET_MISSING"),
    (lambda o: o["rulesets"][0]["rules"].remove(rule(o, "pull_request")), "PROTECTION_PULL_REQUEST_RULE_MISSING"),
])
def test_weakened_ruleset_holds(mutate, code):
    owner = owner_inventory()
    mutate(owner)
    assert code in run(owner)["findings"]


def test_missing_regression_check_holds():
    owner = owner_inventory()
    params = rule(owner, "required_status_checks")["parameters"]
    params["required_status_checks"] = [c for c in params["required_status_checks"]
                                        if c["context"] != "WOW governed probability backend"]
    assert "PROTECTION_CHECK_MISSING:WOW governed probability backend" in run(owner)["findings"]


def test_listing_only_named_files_leaves_new_workflows_unprotected():
    owner = owner_inventory()
    owner["codeowners"] = "\n".join(f"/{p} @{OWNER}" for p in TRUST_ROOTS) + f"\n/.github/CODEOWNERS @{OWNER}\n"
    findings = run(owner)["findings"]
    assert "PROTECTION_CODEOWNERS_UNCOVERED:.github/workflows/__new_workflow__.yml" in findings
    assert "PROTECTION_CODEOWNERS_UNCOVERED:.github/actions/__new_action__/action.yml" in findings


def test_later_codeowners_rule_overriding_owner_is_detected():
    owner = owner_inventory()
    owner["codeowners"] += "/.github/workflows/ @someone-else\n"
    assert "PROTECTION_CODEOWNERS_UNCOVERED:.github/workflows/wow-v17-claude-engineering-worker.yml" in run(owner)["findings"]


@pytest.mark.parametrize("pattern, path, expected", [
    ("/.github/", ".github/workflows/x.yml", True),
    ("/.github/", "docs/.github/x", False),
    ("*.yml", ".github/workflows/x.yml", True),
    ("/.github/actions/wow-claude-agent/*", ".github/actions/wow-claude-agent/action.yml", True),
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
    for probe in m.LIVE_ACCEPTANCE_PROBES:
        assert f"`{probe}`" in doc
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

    files = {}
    for name, owner in (("good", owner_inventory()), ("bad", {**owner_inventory(), "rulesets": []})):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(owner))
        files[name] = path
    principal_files = []
    for runtime in m.AI_RUNTIMES:
        rec = principal(runtime, collected_at=_iso(m._utcnow() - timedelta(minutes=5)),
                        expires_at=_iso(m._utcnow() + timedelta(hours=1)))
        path = tmp_path / f"{runtime}.json"
        path.write_text(json.dumps(rec))
        principal_files += ["--principal", str(path)]
    live_path = tmp_path / "live.json"
    live_path.write_text(json.dumps(live()))
    for name, rc in (("good", 0), ("bad", 2)):
        proc = subprocess.run([sys.executable, str(MODULE_PATH), "evaluate", "--owner", str(files[name]),
                               *principal_files, "--live-acceptance", str(live_path), "--repo-root", str(ROOT)],
                              capture_output=True, text=True)
        assert proc.returncode == rc, proc.stdout + proc.stderr
        out = json.loads(proc.stdout)
        assert out["can_execute"] is False and out["closure_eligible"] is False
