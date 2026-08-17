"""
gate_engine/tests/test_control_plane_scripts.py

Controlled-failure validation for:
  scripts/wow-preflight       — pre-patch safety checks
  scripts/wow-verify-patch    — post-patch diff boundary audit

Strategy
--------
Each test case creates an isolated temporary git repository that mirrors
the minimal directory layout the scripts expect.  Controlled failure states
are injected one at a time (dirty tree, missing protected file, secret
pattern, can_execute=True, protected file in diff) and both the exit code
and stdout content are verified.

The pytest invocation inside the scripts runs against a minimal
test_stage_a_isolation.py stub that always passes so that the
"set -euo pipefail" pipeline in the scripts does not abort the test
on "no tests collected" (exit 5) or a genuine test failure.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import textwrap
import unittest
import tempfile

# ---------------------------------------------------------------------------
# Paths to the real scripts in the workspace
# ---------------------------------------------------------------------------

# gate_engine/tests/ → flask-scoring-api/ → artifacts/ → workspace root
_FLASK_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
_REPO_ROOT  = _FLASK_ROOT.parent.parent          # workspace root
_PREFLIGHT_SRC   = _REPO_ROOT / "scripts" / "wow-preflight"
_VERIFY_SRC      = _REPO_ROOT / "scripts" / "wow-verify-patch"

# ---------------------------------------------------------------------------
# Protected files the scripts monitor
# ---------------------------------------------------------------------------

_PROTECTED = [
    "artifacts/flask-scoring-api/gate_engine/labels.py",
    "artifacts/flask-scoring-api/gate_engine/llp_governance.py",
    "artifacts/flask-scoring-api/gate_engine/data_contract.py",
    "artifacts/flask-scoring-api/gate_engine/failure_path.py",
]

# ---------------------------------------------------------------------------
# Minimal always-passing isolation test stub
# Replaces the real test_stage_a_isolation.py inside the fake repo so that
# "python3 -m pytest gate_engine/tests/test_stage_a_isolation.py" exits 0.
# ---------------------------------------------------------------------------

_STUB_ISOLATION_TEST = textwrap.dedent("""\
    import unittest

    class TestStub(unittest.TestCase):
        \"\"\"Minimal stub that satisfies the preflight/verify-patch pytest call.\"\"\"

        def test_pass(self):
            pass
""")

# ---------------------------------------------------------------------------
# Helper base class
# ---------------------------------------------------------------------------


class _ScriptBase(unittest.TestCase):
    """Spin up an isolated git repo before each test; tear it down after."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="wow_ctrl_")
        self._repo   = pathlib.Path(self._tmpdir) / "repo"
        self._repo.mkdir()
        self._bootstrap()

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Git helpers
    # ------------------------------------------------------------------

    def _git(self, *args, check=True):
        return subprocess.run(
            ["git", "-C", str(self._repo)] + list(args),
            capture_output=True, text=True, check=check,
        )

    # ------------------------------------------------------------------
    # Repo bootstrap
    # ------------------------------------------------------------------

    def _bootstrap(self):
        """
        Create a minimal git repo that satisfies everything both scripts need:

          scripts/wow-preflight          — real script (copied)
          scripts/wow-verify-patch       — real script (copied)
          artifacts/flask-scoring-api/
            gate_engine/
              labels.py / llp_governance.py / data_contract.py / failure_path.py
                                         — protected stubs (can_execute = False)
              tests/
                __init__.py
                test_stage_a_isolation.py — always-passing stub

        All files are committed so the working tree starts clean.
        """
        # Git identity
        self._git("init", "-b", "main")
        self._git("config", "user.email", "wow-test@local")
        self._git("config", "user.name",  "WOW Test Runner")

        # Scripts
        scripts = self._repo / "scripts"
        scripts.mkdir()
        shutil.copy(_PREFLIGHT_SRC,  scripts / "wow-preflight")
        shutil.copy(_VERIFY_SRC,     scripts / "wow-verify-patch")
        (scripts / "wow-preflight" ).chmod(0o755)
        (scripts / "wow-verify-patch").chmod(0o755)

        # Protected stubs
        for rel in _PROTECTED:
            target = self._repo / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("# protected stub\ncan_execute = False\n")

        # Minimal gate_engine structure
        ge     = self._repo / "artifacts" / "flask-scoring-api" / "gate_engine"
        tests  = ge / "tests"
        tests.mkdir(parents=True, exist_ok=True)
        (tests / "__init__.py").write_text("")
        (tests / "test_stage_a_isolation.py").write_text(_STUB_ISOLATION_TEST)

        self._git("add", ".")
        self._git("commit", "-m", "baseline: initial commit")

    # ------------------------------------------------------------------
    # Script runner
    # ------------------------------------------------------------------

    def _run(self, script_name, *extra_args, extra_env=None):
        """
        Execute a script from the fake repo's scripts/ directory.

        GPT_ACTION_SECRET is suppressed so the optional live-endpoint
        section of wow-verify-patch is always skipped.

        WOW_MIN_TESTS defaults to "1" so the stub regression suite
        (which only has one test) satisfies the minimum-count gate.
        Tests that specifically exercise the min-count gate should pass
        extra_env={"WOW_MIN_TESTS": "<higher value>"}.
        """
        env = os.environ.copy()
        env.pop("GPT_ACTION_SECRET", None)
        env["WOW_MIN_TESTS"] = "1"   # override for test harness; real default is 5000
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["bash", str(self._repo / "scripts" / script_name)] + list(extra_args),
            capture_output=True, text=True,
            cwd=str(self._repo), env=env,
        )

    def _add_always_failing_test(self):
        """
        Commit a test file to the fake repo whose single test always fails.
        Used to inject a non-sentinel regression so wow-verify-patch can be
        proven to reject it.  Returns the SHA of the injected commit.
        """
        always_fail = textwrap.dedent("""\
            import unittest

            class TestAlwaysFails(unittest.TestCase):
                def test_unexpected_regression(self):
                    self.fail("Simulated unexpected regression — not the isolation sentinel")
        """)
        target = (
            self._repo
            / "artifacts" / "flask-scoring-api" / "gate_engine" / "tests"
            / "test_always_fails.py"
        )
        target.write_text(always_fail)
        self._git("add", str(target.relative_to(self._repo)))
        self._git("commit", "-m", "inject always-failing test for sentinel-allowlist validation")
        return self._git("rev-parse", "HEAD").stdout.strip()


# ===========================================================================
# wow-preflight controlled-failure tests
# ===========================================================================


class TestWowPreflight(_ScriptBase):

    # -----------------------------------------------------------------------
    # Happy path
    # -----------------------------------------------------------------------

    def test_clean_repo_exits_zero(self):
        """Preflight on a clean repo with all required files → exit 0 + PASSED."""
        r = self._run("wow-preflight")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Preflight PASSED", r.stdout)

    def test_summary_block_always_present(self):
        """Preflight always emits Passed / Warned / Failed summary lines."""
        r = self._run("wow-preflight")
        self.assertIn("Passed:", r.stdout)
        self.assertIn("Warned:", r.stdout)
        self.assertIn("Failed:", r.stdout)

    # -----------------------------------------------------------------------
    # Failure: dirty working tree
    # -----------------------------------------------------------------------

    def test_dirty_tree_exits_nonzero(self):
        """Preflight fails when staged-but-uncommitted files exist."""
        new = self._repo / "artifacts" / "flask-scoring-api" / "gate_engine" / "new.py"
        new.write_text("x = 1\n")
        self._git("add", "artifacts/flask-scoring-api/gate_engine/new.py")
        # deliberately NOT committing

        r = self._run("wow-preflight")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Uncommitted changes detected", r.stdout)

    def test_dirty_tree_lists_dirty_files(self):
        """Preflight prints the list of dirty files so the engineer knows what to commit."""
        dirty = self._repo / "artifacts" / "flask-scoring-api" / "gate_engine" / "dirty.py"
        dirty.write_text("y = 2\n")
        self._git("add", "artifacts/flask-scoring-api/gate_engine/dirty.py")

        r = self._run("wow-preflight")
        self.assertIn("dirty.py", r.stdout)

    def test_unstaged_modification_also_dirty(self):
        """Preflight detects unstaged modifications (not just staged files)."""
        protected = self._repo / _PROTECTED[0]
        protected.write_text("# tampered\ncan_execute = False\n")
        # Not staged — git diff HEAD will still show it

        r = self._run("wow-preflight")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Uncommitted changes detected", r.stdout)

    # -----------------------------------------------------------------------
    # Failure: missing protected file
    # -----------------------------------------------------------------------

    def test_missing_one_protected_file_fails(self):
        """Preflight fails when any protected file is absent."""
        target = self._repo / _PROTECTED[0]
        target.unlink()
        self._git("add", "-A")
        self._git("commit", "-m", "delete protected file")

        r = self._run("wow-preflight")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Protected file MISSING", r.stdout)

    def test_missing_two_protected_files_reports_both(self):
        """Preflight reports each missing protected file independently."""
        for rel in _PROTECTED[:2]:
            (self._repo / rel).unlink()
        self._git("add", "-A")
        self._git("commit", "-m", "delete two protected files")

        r = self._run("wow-preflight")
        self.assertNotEqual(r.returncode, 0)
        count = r.stdout.count("Protected file MISSING")
        self.assertGreaterEqual(count, 2,
            "Expected at least 2 'Protected file MISSING' lines")

    def test_all_protected_files_present_passes_check(self):
        """Preflight passes protected-file check when all 4 files exist."""
        r = self._run("wow-preflight")
        self.assertNotIn("Protected file MISSING", r.stdout)

    # -----------------------------------------------------------------------
    # Failure: secret pattern
    # -----------------------------------------------------------------------

    def test_sk_bearer_secret_pattern_fails(self):
        """Preflight catches a Bearer token that matches the secret regex."""
        secret_file = (
            self._repo / "artifacts" / "flask-scoring-api"
            / "gate_engine" / "oops.py"
        )
        secret_file.write_text(
            "token = 'Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abcdefghij'\n"
        )
        self._git("add", "artifacts/flask-scoring-api/gate_engine/oops.py")
        self._git("commit", "-m", "add file with secret")

        r = self._run("wow-preflight")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Possible secret pattern", r.stdout)

    def test_api_key_pattern_fails(self):
        """Preflight catches api_key = '...' assignments that look like real keys."""
        secret_file = (
            self._repo / "artifacts" / "flask-scoring-api"
            / "gate_engine" / "cfg.py"
        )
        secret_file.write_text("api_key = 'sk-thisisaverylongsecretkey'\n")
        self._git("add", "artifacts/flask-scoring-api/gate_engine/cfg.py")
        self._git("commit", "-m", "leaked key")

        r = self._run("wow-preflight")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Possible secret pattern", r.stdout)

    def test_normal_constants_no_false_positive(self):
        """Preflight does not flag normal constant assignments as secrets."""
        clean = (
            self._repo / "artifacts" / "flask-scoring-api"
            / "gate_engine" / "const.py"
        )
        clean.write_text(
            "BASE_URL = 'https://example.com/api'\nTIMEOUT = 30\n"
        )
        self._git("add", "artifacts/flask-scoring-api/gate_engine/const.py")
        self._git("commit", "-m", "add constants")

        r = self._run("wow-preflight")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("Possible secret pattern", r.stdout)

    # -----------------------------------------------------------------------
    # Failure: can_execute=True in production code
    # -----------------------------------------------------------------------

    def test_can_execute_true_in_production_py_fails(self):
        """Preflight fails if a production gate_engine module sets can_execute=True."""
        bad = (
            self._repo / "artifacts" / "flask-scoring-api"
            / "gate_engine" / "bad_module.py"
        )
        bad.write_text("can_execute = True\n")
        self._git("add", "artifacts/flask-scoring-api/gate_engine/bad_module.py")
        self._git("commit", "-m", "accidentally set can_execute=True")

        r = self._run("wow-preflight")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("can_execute=True found", r.stdout)

    def test_can_execute_true_commented_not_flagged(self):
        """Lines where can_execute=True is in a comment are excluded by grep -v '#'."""
        commented = (
            self._repo / "artifacts" / "flask-scoring-api"
            / "gate_engine" / "commented.py"
        )
        # grep pattern is: grep -v "test_\|#"
        # A line with '#' is excluded → should not trigger
        commented.write_text("# can_execute = True  (not real)\n")
        self._git("add", "artifacts/flask-scoring-api/gate_engine/commented.py")
        self._git("commit", "-m", "comment only")

        r = self._run("wow-preflight")
        self.assertNotIn("can_execute=True found in production", r.stdout)

    def test_can_execute_false_does_not_fail(self):
        """can_execute=False in production code is fine."""
        good = (
            self._repo / "artifacts" / "flask-scoring-api"
            / "gate_engine" / "good.py"
        )
        good.write_text("can_execute = False\n")
        self._git("add", "artifacts/flask-scoring-api/gate_engine/good.py")
        self._git("commit", "-m", "good module")

        r = self._run("wow-preflight")
        self.assertNotIn("can_execute=True found", r.stdout)

    # -----------------------------------------------------------------------
    # Multiple failures accumulate
    # -----------------------------------------------------------------------

    def test_multiple_failures_accumulate_before_exit(self):
        """Preflight reports all failures, not just the first one."""
        # Delete a protected file AND add a can_execute=True module, then commit
        (self._repo / _PROTECTED[1]).unlink()
        bad = (
            self._repo / "artifacts" / "flask-scoring-api"
            / "gate_engine" / "multi_bad.py"
        )
        bad.write_text("can_execute = True\n")
        self._git("add", "-A")
        self._git("commit", "-m", "multiple violations")

        r = self._run("wow-preflight")
        self.assertNotEqual(r.returncode, 0)
        # Both failure types must appear in output
        self.assertIn("Protected file MISSING", r.stdout)
        self.assertIn("can_execute=True found", r.stdout)
        # Failed count must be ≥ 2
        for line in r.stdout.splitlines():
            if line.strip().startswith("Failed:"):
                count = int(line.split(":")[1].strip())
                self.assertGreaterEqual(count, 2)
                break

    # -----------------------------------------------------------------------
    # Failure: untracked files in working tree
    # -----------------------------------------------------------------------

    def test_untracked_file_in_tree_fails(self):
        """Preflight fails when there are untracked (never-staged) files in the tree."""
        untracked = (
            self._repo / "artifacts" / "flask-scoring-api"
            / "gate_engine" / "untracked_new.py"
        )
        untracked.write_text("# brand-new file, never staged\nx = 1\n")
        # Deliberately NOT staging or committing this file

        r = self._run("wow-preflight")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Uncommitted changes detected", r.stdout)

    def test_untracked_file_appears_in_output(self):
        """Preflight prints the untracked filename so the engineer knows what to commit."""
        untracked = (
            self._repo / "artifacts" / "flask-scoring-api"
            / "gate_engine" / "mystery_file.py"
        )
        untracked.write_text("mystery = True\n")

        r = self._run("wow-preflight")
        self.assertIn("mystery_file.py", r.stdout)

    # -----------------------------------------------------------------------
    # Failure: can_execute=True — compact form and YAML/JSON variants
    # -----------------------------------------------------------------------

    def test_can_execute_compact_true_no_spaces_fails(self):
        """Preflight catches can_execute=True with no spaces around the equals sign."""
        bad = (
            self._repo / "artifacts" / "flask-scoring-api"
            / "gate_engine" / "compact.py"
        )
        bad.write_text("can_execute=True\n")
        self._git("add", "artifacts/flask-scoring-api/gate_engine/compact.py")
        self._git("commit", "-m", "compact form violation")

        r = self._run("wow-preflight")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("can_execute=True found", r.stdout)

    def test_can_execute_yaml_colon_true_lowercase_fails(self):
        """Preflight catches can_execute: true (YAML colon form, lowercase) in gate_engine."""
        bad_yaml = (
            self._repo / "artifacts" / "flask-scoring-api"
            / "gate_engine" / "bad_contract.yaml"
        )
        bad_yaml.write_text("can_execute: true\nversion: v1\n")
        self._git("add", "artifacts/flask-scoring-api/gate_engine/bad_contract.yaml")
        self._git("commit", "-m", "yaml can_execute: true violation")

        r = self._run("wow-preflight")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("can_execute: true found", r.stdout)

    def test_can_execute_yaml_colon_true_capital_T_fails(self):
        """Preflight catches can_execute: True (YAML colon form, capital T) in gate_engine."""
        bad_yaml = (
            self._repo / "artifacts" / "flask-scoring-api"
            / "gate_engine" / "bad_contract2.yaml"
        )
        bad_yaml.write_text("can_execute: True\nversion: v1\n")
        self._git("add", "artifacts/flask-scoring-api/gate_engine/bad_contract2.yaml")
        self._git("commit", "-m", "yaml can_execute: True violation")

        r = self._run("wow-preflight")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("can_execute: true found", r.stdout)

    def test_can_execute_yaml_false_not_flagged(self):
        """Preflight does not flag can_execute: false in a YAML file — that is correct."""
        good_yaml = (
            self._repo / "artifacts" / "flask-scoring-api"
            / "gate_engine" / "good_contract.yaml"
        )
        good_yaml.write_text("can_execute: false\nversion: v1\n")
        self._git("add", "artifacts/flask-scoring-api/gate_engine/good_contract.yaml")
        self._git("commit", "-m", "yaml can_execute: false is fine")

        r = self._run("wow-preflight")
        self.assertNotIn("can_execute: true found", r.stdout)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


# ===========================================================================
# wow-verify-patch controlled-failure tests
# ===========================================================================


class TestWowVerifyPatch(_ScriptBase):

    # ------------------------------------------------------------------
    # Helper: make a test commit and return its SHA
    # ------------------------------------------------------------------

    def _commit(self, files: dict, message="test commit") -> str:
        for rel, content in files.items():
            target = self._repo / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
            self._git("add", rel)
        self._git("commit", "-m", message)
        return self._git("rev-parse", "HEAD").stdout.strip()

    # -----------------------------------------------------------------------
    # Happy path
    # -----------------------------------------------------------------------

    def test_clean_safe_commit_exits_zero(self):
        """A commit touching only non-protected files passes verify-patch."""
        sha = self._commit({"docs/readme.md": "# docs\n"})
        r = self._run("wow-verify-patch", sha)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Patch verification PASSED", r.stdout)

    def test_defaults_to_head_when_no_sha_given(self):
        """verify-patch defaults to HEAD when no commit SHA argument is supplied."""
        self._commit({"docs/auto.md": "auto\n"})
        r = self._run("wow-verify-patch")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Patch verification PASSED", r.stdout)

    def test_summary_always_present(self):
        """verify-patch always emits Passed / Failed summary."""
        sha = self._commit({"docs/s.md": "s\n"})
        r = self._run("wow-verify-patch", sha)
        self.assertIn("Passed:", r.stdout)
        self.assertIn("Failed:", r.stdout)

    def test_next_steps_shown_on_pass(self):
        """verify-patch prints next-step instructions when verification passes."""
        sha = self._commit({"docs/n.md": "n\n"})
        r = self._run("wow-verify-patch", sha)
        if r.returncode == 0:
            self.assertIn("git commit", r.stdout)

    # -----------------------------------------------------------------------
    # Failure: protected file in diff
    # -----------------------------------------------------------------------

    def test_protected_file_in_diff_fails(self):
        """Touching a protected file in the commit diff is rejected."""
        sha = self._commit({_PROTECTED[0]: "# modified\ncan_execute = False\n"})
        r = self._run("wow-verify-patch", sha)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("PROTECTED FILE MODIFIED", r.stdout)

    def test_all_four_protected_files_individually_trigger_failure(self):
        """Each of the 4 protected files independently triggers boundary failure."""
        for rel in _PROTECTED:
            with self.subTest(protected=rel):
                sha = self._commit(
                    {rel: "# touched\ncan_execute = False\n"},
                    message=f"touch {rel}",
                )
                r = self._run("wow-verify-patch", sha)
                self.assertNotEqual(r.returncode, 0,
                    f"Expected failure for protected file: {rel}")
                self.assertIn("PROTECTED FILE MODIFIED", r.stdout)

    def test_non_protected_file_clears_boundary_check(self):
        """A commit that touches only safe files does not trigger the boundary check."""
        sha = self._commit(
            {"artifacts/flask-scoring-api/gate_engine/new_feature.py":
                "# new feature\ncan_execute = False\n"}
        )
        r = self._run("wow-verify-patch", sha)
        self.assertNotIn("PROTECTED FILE MODIFIED", r.stdout)

    def test_protected_file_not_modified_passes_boundary(self):
        """verify-patch confirms protected files were NOT modified when clean."""
        sha = self._commit({"docs/clean.md": "# clean\n"})
        r = self._run("wow-verify-patch", sha)
        self.assertIn("No protected files modified", r.stdout)

    # -----------------------------------------------------------------------
    # Failure: secret in changed file
    # -----------------------------------------------------------------------

    def test_bearer_token_in_changed_file_fails(self):
        """A Bearer token in a committed file is caught by the secret scan."""
        sha = self._commit(
            {"artifacts/flask-scoring-api/gate_engine/bearer.py":
                "auth = 'Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abcdefghijklmnop'\n"}
        )
        r = self._run("wow-verify-patch", sha)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Possible secret in changed file", r.stdout)

    def test_sk_prefixed_token_in_changed_file_fails(self):
        """An sk-... API key in a committed file is caught."""
        sha = self._commit(
            {"artifacts/flask-scoring-api/gate_engine/key.py":
                "api_key = 'sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456'\n"}
        )
        r = self._run("wow-verify-patch", sha)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Possible secret in changed file", r.stdout)

    def test_clean_changed_file_no_secret_false_positive(self):
        """A normal changed file with no secret patterns is not flagged."""
        sha = self._commit(
            {"artifacts/flask-scoring-api/gate_engine/normal.py":
                "BASE = 'https://example.com'\nTIMEOUT = 30\n"}
        )
        r = self._run("wow-verify-patch", sha)
        self.assertNotIn("Possible secret in changed file", r.stdout)

    # -----------------------------------------------------------------------
    # Failure: can_execute=True invariant
    # -----------------------------------------------------------------------

    def test_can_execute_true_in_production_fails(self):
        """verify-patch fails when gate_engine production code has can_execute=True."""
        sha = self._commit(
            {"artifacts/flask-scoring-api/gate_engine/cex.py":
                "can_execute = True\n"}
        )
        r = self._run("wow-verify-patch", sha)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("can_execute=True found", r.stdout)

    def test_can_execute_false_passes_invariant(self):
        """verify-patch passes the invariant check when only can_execute=False appears."""
        sha = self._commit(
            {"artifacts/flask-scoring-api/gate_engine/ok.py":
                "can_execute = False\n"}
        )
        r = self._run("wow-verify-patch", sha)
        self.assertNotIn("can_execute=True found", r.stdout)

    # -----------------------------------------------------------------------
    # Multiple failures accumulate
    # -----------------------------------------------------------------------

    def test_protected_file_and_secret_both_reported(self):
        """verify-patch accumulates multiple failure types in a single run."""
        sha = self._commit({
            # Touches protected file
            _PROTECTED[2]: "# tampered\ncan_execute = False\n",
            # Introduces secret
            "artifacts/flask-scoring-api/gate_engine/both_bad.py":
                "bearer = 'Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xxxxxx'\n",
        })
        r = self._run("wow-verify-patch", sha)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("PROTECTED FILE MODIFIED", r.stdout)
        self.assertIn("Possible secret in changed file", r.stdout)
        for line in r.stdout.splitlines():
            if line.strip().startswith("Failed:"):
                count = int(line.split(":")[1].strip())
                self.assertGreaterEqual(count, 2)
                break

    # -----------------------------------------------------------------------
    # Diff section output
    # -----------------------------------------------------------------------

    def test_changed_files_listed_in_output(self):
        """verify-patch prints the list of changed files in the diff section."""
        sha = self._commit({"docs/listed.md": "# listed\n"})
        r = self._run("wow-verify-patch", sha)
        self.assertIn("listed.md", r.stdout)

    def test_commit_sha_appears_in_header(self):
        """verify-patch echoes the commit SHA in the diff boundary section header."""
        sha = self._commit({"docs/sha.md": "sha\n"})
        r = self._run("wow-verify-patch", sha)
        self.assertIn(sha[:7], r.stdout)

    # -----------------------------------------------------------------------
    # Failure: test-count floor (minimum expected inventory)
    # -----------------------------------------------------------------------

    def test_low_test_count_is_rejected(self):
        """verify-patch rejects a run where the test count is suspiciously low.

        With WOW_MIN_TESTS=10 and a stub that only has 1 test, the regression
        gate must fail with a 'possible test removal' message even though there
        are zero failures.
        """
        sha = self._commit({"docs/safe.md": "safe\n"})
        # WOW_MIN_TESTS=10 but stub only has 1 test → count below floor
        r = self._run("wow-verify-patch", sha, extra_env={"WOW_MIN_TESTS": "10"})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("possible test removal", r.stdout)

    # -----------------------------------------------------------------------
    # Failure: unexpected failing test (not the isolation sentinel)
    # -----------------------------------------------------------------------

    def test_wrong_failing_test_is_rejected(self):
        """verify-patch rejects when the failing test is not the isolation sentinel.

        Injects a non-sentinel always-failing test into the fake repo, then runs
        verify-patch on a subsequent innocuous commit.  The script must report
        that the failure is NOT the expected sentinel and exit non-zero.
        """
        # Commit the always-failing test into the fake repo
        self._add_always_failing_test()
        # Make an innocuous follow-up commit so we have a diff to audit
        sha = self._commit({"docs/after_injection.md": "after\n"})

        r = self._run("wow-verify-patch", sha)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("NOT the expected isolation sentinel", r.stdout)

    def test_wrong_failing_test_prints_both_expected_and_actual_ids(self):
        """verify-patch prints expected vs. actual test IDs when sentinel check fails."""
        self._add_always_failing_test()
        sha = self._commit({"docs/ids.md": "ids\n"})

        r = self._run("wow-verify-patch", sha)
        self.assertNotEqual(r.returncode, 0)
        # Should print the sentinel ID so the engineer knows what was expected
        self.assertIn("test_stage_a_isolation.py", r.stdout)
        # Should print the actually-failing test
        self.assertIn("test_always_fails", r.stdout)

    # -----------------------------------------------------------------------
    # Failure: can_execute=True — compact form and YAML/JSON variants in diff
    # -----------------------------------------------------------------------

    def test_can_execute_compact_true_no_spaces_in_diff_fails(self):
        """verify-patch catches can_execute=True (compact, no spaces) committed to gate_engine."""
        sha = self._commit(
            {"artifacts/flask-scoring-api/gate_engine/cex_compact.py":
                "can_execute=True\n"}
        )
        r = self._run("wow-verify-patch", sha)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("can_execute=True found", r.stdout)

    def test_can_execute_yaml_colon_true_in_diff_fails(self):
        """verify-patch catches can_execute: true in a YAML file committed to gate_engine."""
        sha = self._commit(
            {"artifacts/flask-scoring-api/gate_engine/live_contract.yaml":
                "can_execute: true\nversion: v1\n"}
        )
        r = self._run("wow-verify-patch", sha)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("can_execute: true found", r.stdout)

    def test_can_execute_yaml_capital_T_in_diff_fails(self):
        """verify-patch catches can_execute: True (capital T YAML form) in committed file."""
        sha = self._commit(
            {"artifacts/flask-scoring-api/gate_engine/live_contract2.yaml":
                "can_execute: True\nversion: v1\n"}
        )
        r = self._run("wow-verify-patch", sha)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("can_execute: true found", r.stdout)

    def test_can_execute_yaml_false_not_flagged_in_diff(self):
        """verify-patch does not flag can_execute: false in a committed YAML file."""
        sha = self._commit(
            {"artifacts/flask-scoring-api/gate_engine/good_contract.yaml":
                "can_execute: false\nversion: v1\n"}
        )
        r = self._run("wow-verify-patch", sha)
        self.assertNotIn("can_execute: true found", r.stdout)


if __name__ == "__main__":
    unittest.main()
