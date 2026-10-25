"""Offline negative/regression tests for the non-authorizing Copilot handoff."""
import copy
import importlib.util
import pathlib
import unittest

FILE = pathlib.Path(__file__).resolve().parent / "v17" / "copilot_handoff_contract.py"
SPEC = importlib.util.spec_from_file_location("copilot_handoff_contract", FILE)
contract = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(contract)
REPO = "gregoryharper84-ship-it/WOW-Dashboard"
MAIN = "a" * 40
HEAD = "b" * 40


def packet():
    return {
        "schema": contract.SCHEMA,
        "repository": REPO,
        "incident_id": 1763,
        "mode": "PROPOSE_ONLY",
        "can_execute": False,
        "source_main_sha": MAIN,
        "severity": "P1",
        "change_class": "A",
        "owner": "WOW_ENGINEERING",
        "lease_group": "COPILOT_PILOT",
        "allowed_paths": ["artifacts/wow-engine/test_v17_copilot_issue_pilot.py"],
    }


def pr():
    return {
        "state": "open",
        "draft": True,
        "merged": False,
        "user": {"login": "Copilot"},
        "head": {"sha": HEAD, "repo": {"full_name": REPO}},
        "base": {"ref": "main", "sha": MAIN, "repo": {"full_name": REPO}},
        "changed_files": 1,
        "body": "Proposes a narrow test-only change for #1763.",
    }


class HandoffContractTests(unittest.TestCase):
    def check(self, item, expected="HOLD", sha=MAIN):
        actual = contract.validate_handoff(item, repository=REPO, current_main_sha=sha)
        self.assertEqual(expected, actual["decision"])
        self.assertIs(actual["can_execute"], False)
        self.assertIs(actual["assignment_authorized"], False)
        return actual

    def test_valid_packet_is_non_authorizing(self):
        res = self.check(packet(), "PACKET_VALID_NON_AUTHORIZING")
        self.assertIn("authoritative_lease_and_conflict_inventory", res["required_next_evidence"])

    def test_missing_lease_is_not_authorization(self):
        data = packet()
        data["lease_group"] = None
        self.assertIn("LEASE_GROUP_INVALID", self.check(data)["reasons"])

    def test_boolean_incident_is_rejected(self):
        data = packet()
        data["incident_id"] = True
        self.assertIn("INCIDENT_ID_INVALID", self.check(data)["reasons"])

    def test_missing_or_non_string_live_main_fails_closed(self):
        for value in (None, 123, False):
            with self.subTest(value=value):
                self.assertIn("MAIN_SHA_STALE", self.check(packet(), sha=value)["reasons"])

    def test_governance_codeowners_paths_rejected(self):
        for path in ("artifacts/wow-engine/v17/agent_identity_policy.py",
                     "artifacts/wow-engine/v17/engineering_dispatch_queue.py",
                     "artifacts/wow-engine/requirements.txt",
                     "artifacts/flask-scoring-api/requirements.txt"):
            data = packet()
            data["allowed_paths"] = [path]
            with self.subTest(path=path):
                self.assertIn("PROTECTED_PATH_REQUIRES_GOVERNED_REVIEW", self.check(data)["reasons"])

    def test_fails_closed_when_main_moves(self):
        self.assertIn("MAIN_SHA_STALE", self.check(packet(), sha="c" * 40)["reasons"])

    def test_uppercase_sha_is_rejected(self):
        data = packet()
        data["source_main_sha"] = "A" * 40
        self.assertIn("SOURCE_SHA_INVALID", self.check(data)["reasons"])

    def test_reject_execution_toggle(self):
        data = packet()
        data["can_execute"] = True
        self.assertIn("EXECUTION_AUTHORITY_FORBIDDEN", self.check(data)["reasons"])

    def test_reject_relative_traversal(self):
        for path in ("../README.md", "docs/../README.md", "/etc/passwd", "docs//x", "docs/./x", "docs\\x", "docs/x\nfoo"):
            data = packet()
            data["allowed_paths"] = [path]
            with self.subTest(path=path):
                self.assertIn("UNSAFE_PATH", self.check(data)["reasons"])

    def test_protected_trust_roots_fail_closed(self):
        for path in (".github/workflows/rogue.yml", ".github/agents/wow-repair.agent.md",
                     ".agents/skills/x.md", "render.yaml"):
            data = packet()
            data["allowed_paths"] = [path]
            with self.subTest(path=path):
                self.assertIn("PROTECTED_PATH_REQUIRES_GOVERNED_REVIEW", self.check(data)["reasons"])

    def test_class_b_and_c_cannot_auto_execute(self):
        for value in ("B", "C"):
            data = packet()
            data["change_class"] = value
            with self.subTest(change_class=value):
                self.assertIn("GOVERNED_CHANGE_REQUIRES_INDEPENDENT_APPROVAL", self.check(data)["reasons"])

    def test_malformed_json_fields_fail_closed_without_type_errors(self):
        for field in ("change_class", "severity"):
            for value in ([], {}, 42, None):
                data = packet()
                data[field] = value
                with self.subTest(field=field, value=value):
                    result = self.check(data)
                    self.assertIn("CHANGE_CLASS_INVALID" if field == "change_class" else "SEVERITY_INVALID",
                                  result["reasons"])

    def test_duplicate_paths_rejected(self):
        data = packet()
        data["allowed_paths"] *= 2
        self.assertIn("ALLOWED_PATHS_DUPLICATE", self.check(data)["reasons"])

    def test_wrong_repo_is_not_adopted(self):
        data = packet()
        data["repository"] = "thirdparty/repo"
        self.assertIn("REPOSITORY_MISMATCH", self.check(data)["reasons"])

    def test_no_code_pr_is_not_verified_closure(self):
        result = contract.verify_draft_pr(packet(), pr(),
            ["artifacts/wow-engine/test_v17_copilot_issue_pilot.py"],
            repository=REPO, current_main_sha=MAIN)
        self.assertEqual("PR_CREATED_UNVERIFIED", result["decision"])
        self.assertNotIn("FIXED_AND_VERIFIED", str(result))
        self.assertIs(result["can_execute"], False)

    def test_wrong_pr_author_does_not_spoof_copilot(self):
        snapshot = pr()
        snapshot["user"]["login"] = "gregoryharper84-ship-it"
        result = contract.verify_draft_pr(packet(), snapshot, packet()["allowed_paths"],
                                           repository=REPO, current_main_sha=MAIN)
        self.assertIn("PR_AUTHOR_NOT_COPILOT", result["reasons"])

    def test_malformed_pr_author_identity_fails_closed(self):
        for login in ([], {}, 1, None):
            snapshot = pr()
            snapshot["user"]["login"] = login
            with self.subTest(login=login):
                result = contract.verify_draft_pr(
                    packet(), snapshot, packet()["allowed_paths"],
                    repository=REPO, current_main_sha=MAIN)
                self.assertEqual("HOLD", result["decision"])
                self.assertIn("PR_AUTHOR_NOT_COPILOT", result["reasons"])

    def test_wrong_or_stale_pr_rejected(self):
        for key, value in (("sha", "c" * 40), ("ref", "develop")):
            snapshot = pr()
            snapshot["base"][key] = value
            with self.subTest(key=key):
                result = contract.verify_draft_pr(packet(), snapshot, packet()["allowed_paths"],
                                                   repository=REPO, current_main_sha=MAIN)
                self.assertIn("PR_BASE_SHA_UNBOUND", result["reasons"])

    def test_expanded_pr_scope_rejected(self):
        for changes in ([], [".github/workflows/unsafe.yml"],
                        packet()["allowed_paths"] + ["README.md"]):
            result = contract.verify_draft_pr(packet(), pr(), changes,
                                               repository=REPO, current_main_sha=MAIN)
            self.assertEqual("HOLD", result["decision"])

    def test_pr_without_issue_binding_rejected(self):
        snapshot = pr()
        snapshot["body"] = "No original incident link"
        result = contract.verify_draft_pr(packet(), snapshot, packet()["allowed_paths"],
                                           repository=REPO, current_main_sha=MAIN)
        self.assertIn("PR_INCIDENT_LINK_MISSING", result["reasons"])

    def test_profiles_are_explicitly_invoked_and_least_privileged(self):
        repo_root = pathlib.Path(__file__).resolve().parents[2]
        expected = {
            "wow-repair": 'tools: ["read", "search", "edit", "execute", "github/*"]',
            "wow-regression": 'tools: ["read", "search", "edit", "execute", "github/*"]',
            "wow-review": 'tools: ["read", "search", "github/*"]',
        }
        for role, tools_line in expected.items():
            with self.subTest(role=role):
                content = (repo_root / ".github" / "agents" / (role + ".agent.md")).read_text(
                    encoding="utf-8")
                frontmatter = content.split("---", 2)[1]
                self.assertIn("target: github-copilot", frontmatter)
                self.assertIn("disable-model-invocation: true", frontmatter)
                self.assertIn(tools_line, frontmatter)
                self.assertNotIn('tools: ["*"]', frontmatter)

    def test_read_only_review_has_no_shell_or_edit_tools(self):
        repo_root = pathlib.Path(__file__).resolve().parents[2]
        content = (repo_root / ".github" / "agents" / "wow-review.agent.md").read_text(
            encoding="utf-8")
        frontmatter = content.split("---", 2)[1]
        self.assertIn('tools: ["read", "search", "github/*"]', frontmatter)
        for disallowed in ('"edit"', '"execute"', '"agent"', '"*"'):
            self.assertNotIn(disallowed, frontmatter)

    def test_malformed_allowed_path_types_do_not_crash(self):
        for paths in ([{}, {}], [[], "a"], [1, True], ["README.md", {}],
                      [None], [["docs"]]):
            data = packet()
            data["allowed_paths"] = paths
            with self.subTest(paths=paths):
                self.assertIn("UNSAFE_PATH", self.check(data)["reasons"])

    def test_protected_glob_action_paths(self):
        for path in (".github/actions/wow-claude-agent/action.yml",
                     ".github/actions/wow-chatgpt-agent/invoke.py"):
            data = packet()
            data["allowed_paths"] = [path]
            with self.subTest(path=path):
                self.assertIn("PROTECTED_PATH_REQUIRES_GOVERNED_REVIEW",
                              self.check(data)["reasons"])

    def test_base_repository_attestation(self):
        for repo_binding in ({}, {"full_name": "thirdparty/repo"}, None):
            snapshot = pr()
            if repo_binding is None:
                snapshot["base"].pop("repo")
            else:
                snapshot["base"]["repo"] = repo_binding
            with self.subTest(binding=repo_binding):
                result = contract.verify_draft_pr(
                    packet(), snapshot, packet()["allowed_paths"],
                    repository=REPO, current_main_sha=MAIN)
                self.assertIn("PR_BASE_REPOSITORY_MISMATCH", result["reasons"])

    def test_exact_incident_link_formats(self):
        valid = (
            "#1763",
            "gregoryharper84-ship-it/WOW-Dashboard#1763",
            "https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1763",
        )
        invalid = (
            "other/repo#1763",
            "https://github.com/other/repo/issues/1763",
            "https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/17630",
            "https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1763/other",
            "#17630",
        )
        for link in valid:
            snapshot = pr()
            snapshot["body"] = "Fixes " + link
            with self.subTest(valid=link):
                result = contract.verify_draft_pr(
                    packet(), snapshot, packet()["allowed_paths"],
                    repository=REPO, current_main_sha=MAIN)
                self.assertEqual("PR_CREATED_UNVERIFIED", result["decision"])
        for link in invalid:
            snapshot = pr()
            snapshot["body"] = "Fixes " + link
            with self.subTest(invalid=link):
                result = contract.verify_draft_pr(
                    packet(), snapshot, packet()["allowed_paths"],
                    repository=REPO, current_main_sha=MAIN)
                self.assertIn("PR_INCIDENT_LINK_MISSING", result["reasons"])

    def test_incomplete_or_invalid_changed_file_inventory(self):
        for count, files in ((2, packet()["allowed_paths"]),
                             (0, packet()["allowed_paths"]),
                             (True, packet()["allowed_paths"]),
                             (None, packet()["allowed_paths"]),
                             (1, [[], {}]), (1, [{}])):
            snapshot = pr()
            snapshot["changed_files"] = count
            with self.subTest(count=count, files=files):
                result = contract.verify_draft_pr(
                    packet(), snapshot, files,
                    repository=REPO, current_main_sha=MAIN)
                self.assertEqual("HOLD", result["decision"])

    def test_missing_changed_file_inventory_count(self):
        snapshot = pr()
        snapshot.pop("changed_files")
        result = contract.verify_draft_pr(
            packet(), snapshot, packet()["allowed_paths"],
            repository=REPO, current_main_sha=MAIN)
        self.assertIn("PR_CHANGED_FILE_COUNT_INVALID", result["reasons"])

    def test_non_mapping_snapshot_is_hold(self):
        for snapshot in (None, [], "invalid"):
            with self.subTest(snapshot=snapshot):
                result = contract.verify_draft_pr(
                    packet(), snapshot, packet()["allowed_paths"],
                    repository=REPO, current_main_sha=MAIN)
                self.assertEqual("HOLD", result["decision"])

    def test_non_draft_is_not_accepted(self):
        snapshot = pr()
        snapshot["draft"] = False
        result = contract.verify_draft_pr(packet(), snapshot, packet()["allowed_paths"],
                                           repository=REPO, current_main_sha=MAIN)
        self.assertIn("PR_NOT_OPEN_DRAFT", result["reasons"])


if __name__ == "__main__":
    unittest.main()
