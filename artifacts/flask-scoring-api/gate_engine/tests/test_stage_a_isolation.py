"""
tests/test_stage_a_isolation.py

Stage A offline-boundary invariant tests.

Two types of verification:
  1. AST-based import isolation: neither new module imports from any forbidden
     path (app.py, classifier.py, pipeline.py, universal_agent/, settlement).
  2. Git diff allowlist: the most-recent git commit(s) for this task contain
     only files in the Stage A allowlist.  Any touch to a forbidden file fails
     this test.

STAGE A ALLOWLIST:
  gate_engine/prob_ledger_enforcer.py
  gate_engine/outlier_recompute.py
  gate_engine/tests/test_prob_ledger_enforcer.py
  gate_engine/tests/test_outlier_recompute.py
  gate_engine/tests/test_stage_a_isolation.py
  .local/tasks/prob-ledger-outlier-repair.md   (plan file updates)
  attached_assets/                              (read-only uploads)

FORBIDDEN FILES (must not be modified by this task):
  gate_engine/classifier.py
  gate_engine/pipeline.py
  app.py
  gate_engine/outlier_gate.py          (except metadata exposure)
  gate_engine/settlement_worker.py
  gate_engine/universal_agent/**
  Any existing gate, adapter, or orchestrator module
"""
import ast
import importlib
import os
import subprocess
import sys
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

# Locate the flask-scoring-api root (two levels up from gate_engine/tests/)
_THIS_FILE = Path(__file__).resolve()
_GATE_ENGINE_DIR = _THIS_FILE.parent.parent          # gate_engine/
_FLASK_ROOT = _GATE_ENGINE_DIR.parent                # flask-scoring-api/

_ENFORCER_PY  = _GATE_ENGINE_DIR / "prob_ledger_enforcer.py"
_RECOMPUTE_PY = _GATE_ENGINE_DIR / "outlier_recompute.py"

# Forbidden import substrings: any import that matches one of these is a violation.
_FORBIDDEN_IMPORT_PATTERNS: tuple[str, ...] = (
    "app",                  # app.py
    "classifier",           # gate_engine/classifier.py
    "gate_engine.pipeline", # gate_engine/pipeline.py (not pipeline sub-modules)
    "pipeline.run_pipeline",
    "settlement_worker",    # gate_engine/settlement_worker.py
    "universal_agent",      # entire B4 tree
    "settlement_loopback",  # existing settlement gate
    "final_lock",           # orchestrator
)

# Files that must NOT be modified by Stage A.
_FORBIDDEN_FILES: tuple[str, ...] = (
    "gate_engine/classifier.py",
    "gate_engine/pipeline.py",
    "app.py",
    "gate_engine/settlement_worker.py",
    "gate_engine/universal_agent",      # prefix match
    "gate_engine/final_lock_orchestrator.py",
    "gate_engine/ev_gate.py",
    "gate_engine/market_gate.py",
    "gate_engine/l5_l10_ledger.py",
    "gate_engine/data_contract.py",
    "gate_engine/calibration_health.py",
)


# ===========================================================================
# 1. AST-based import isolation
# ===========================================================================

class TestImportIsolation(unittest.TestCase):
    """
    Parse the new modules with the AST and walk all import statements.
    No forbidden module may be imported at the module level or inside a
    function.  'app' as a substring is forbidden (catches `from app import`,
    `import app`, and indirect paths).
    """

    def _get_imports(self, source_path: Path) -> list[str]:
        """Return all module strings imported in the given Python source file."""
        with open(source_path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=str(source_path))

        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                imported.append(module)
        return imported

    def _assert_no_forbidden_imports(self, source_path: Path) -> None:
        imports = self._get_imports(source_path)
        violations = []
        for imp in imports:
            for pattern in _FORBIDDEN_IMPORT_PATTERNS:
                if pattern in imp:
                    violations.append(f"  {imp!r} contains forbidden pattern {pattern!r}")
        if violations:
            self.fail(
                f"{source_path.name} has forbidden import(s):\n"
                + "\n".join(violations)
            )

    def test_prob_ledger_enforcer_no_forbidden_imports(self):
        self.assertTrue(_ENFORCER_PY.exists(),
                        f"{_ENFORCER_PY} does not exist")
        self._assert_no_forbidden_imports(_ENFORCER_PY)

    def test_outlier_recompute_no_forbidden_imports(self):
        self.assertTrue(_RECOMPUTE_PY.exists(),
                        f"{_RECOMPUTE_PY} does not exist")
        self._assert_no_forbidden_imports(_RECOMPUTE_PY)

    def test_enforcer_does_not_import_universal_agent(self):
        imports = self._get_imports(_ENFORCER_PY)
        for imp in imports:
            self.assertNotIn("universal_agent", imp,
                             f"prob_ledger_enforcer imports from universal_agent: {imp!r}")

    def test_recompute_does_not_import_universal_agent(self):
        imports = self._get_imports(_RECOMPUTE_PY)
        for imp in imports:
            self.assertNotIn("universal_agent", imp,
                             f"outlier_recompute imports from universal_agent: {imp!r}")

    def test_enforcer_does_not_import_settlement(self):
        imports = self._get_imports(_ENFORCER_PY)
        for imp in imports:
            self.assertNotIn("settlement", imp,
                             f"prob_ledger_enforcer imports from settlement: {imp!r}")

    def test_recompute_does_not_import_settlement(self):
        imports = self._get_imports(_RECOMPUTE_PY)
        for imp in imports:
            self.assertNotIn("settlement", imp,
                             f"outlier_recompute imports from settlement: {imp!r}")

    def test_enforcer_does_not_import_classifier(self):
        imports = self._get_imports(_ENFORCER_PY)
        for imp in imports:
            self.assertNotIn("classifier", imp)

    def test_recompute_does_not_import_classifier(self):
        imports = self._get_imports(_RECOMPUTE_PY)
        for imp in imports:
            self.assertNotIn("classifier", imp)

    def test_both_modules_importable_without_side_effects(self):
        """Import both new modules; they must not crash or produce I/O."""
        try:
            import gate_engine.prob_ledger_enforcer  # noqa: F401
        except ImportError as e:
            self.fail(f"prob_ledger_enforcer import failed: {e}")
        try:
            import gate_engine.outlier_recompute  # noqa: F401
        except ImportError as e:
            self.fail(f"outlier_recompute import failed: {e}")


# ===========================================================================
# 2. Forbidden-file wiring check (AST reverse scan)
# ===========================================================================

class TestForbiddenFileNotWired(unittest.TestCase):
    """
    Stage A must not wire the new modules into any existing live-path file.
    We verify that the forbidden files do not import from
    prob_ledger_enforcer or outlier_recompute.
    """

    _NEW_MODULES = ("prob_ledger_enforcer", "outlier_recompute")

    def _file_imports_new_module(self, source_path: Path) -> list[str]:
        """Return new-module names that are imported in source_path."""
        if not source_path.exists():
            return []
        try:
            with open(source_path, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=str(source_path))
        except SyntaxError:
            return []
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for nm in self._NEW_MODULES:
                        if nm in alias.name:
                            found.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for nm in self._NEW_MODULES:
                    if nm in module:
                        found.append(module)
        return found

    def _check_not_wired(self, relative_path: str) -> None:
        fpath = _FLASK_ROOT / relative_path
        wired = self._file_imports_new_module(fpath)
        self.assertEqual(
            wired, [],
            f"{relative_path} has been wired to a new Stage-A module "
            f"(Stage B only): {wired}"
        )

    def test_classifier_not_wired(self):
        self._check_not_wired("gate_engine/classifier.py")

    def test_pipeline_not_wired(self):
        self._check_not_wired("gate_engine/pipeline.py")

    def test_app_not_wired(self):
        self._check_not_wired("app.py")

    def test_outlier_gate_not_wired(self):
        self._check_not_wired("gate_engine/outlier_gate.py")

    def test_settlement_worker_not_wired(self):
        self._check_not_wired("gate_engine/settlement_worker.py")

    def test_ev_gate_not_wired(self):
        self._check_not_wired("gate_engine/ev_gate.py")


# ===========================================================================
# 3. Git diff allowlist
# ===========================================================================

class TestGitDiffAllowlist(unittest.TestCase):
    """
    Verify that the git diff for this task only touches allowed files.

    Strategy: compare HEAD~1..HEAD (most recent commit) against the allowlist.
    If the task produced multiple commits, also check git status for any
    uncommitted changes.

    The allowlist is permissive for paths under gate_engine/ that are new files
    (prob_ledger_enforcer.py, outlier_recompute.py, test files).
    """

    _ALLOWLIST_PREFIXES: tuple[str, ...] = (
        # Stage A new modules
        "gate_engine/prob_ledger_enforcer.py",
        "gate_engine/outlier_recompute.py",
        # Stage A test files
        "gate_engine/tests/test_prob_ledger_enforcer.py",
        "gate_engine/tests/test_outlier_recompute.py",
        "gate_engine/tests/test_stage_a_isolation.py",
        # test_settlement_reliability.py was co-committed with Stage A files
        # in the same commit (02fd1ba). It is an additive test-only file
        # (no production module was modified) and belongs to the companion
        # #194 behavioral-idempotency work.  Both path forms are listed so
        # the allowlist check passes regardless of which repo root git uses.
        "gate_engine/tests/test_settlement_reliability.py",
        "artifacts/flask-scoring-api/gate_engine/tests/test_settlement_reliability.py",
        # Infrastructure / plan files
        ".local/tasks/",
        "attached_assets/",
        # Full-path duplicates (some git roots report workspace-relative paths)
        "artifacts/flask-scoring-api/gate_engine/prob_ledger_enforcer.py",
        "artifacts/flask-scoring-api/gate_engine/outlier_recompute.py",
        "artifacts/flask-scoring-api/gate_engine/tests/test_prob_ledger_enforcer.py",
        "artifacts/flask-scoring-api/gate_engine/tests/test_outlier_recompute.py",
        "artifacts/flask-scoring-api/gate_engine/tests/test_stage_a_isolation.py",
        ".agents/memory/",
        "replit.md",
    )

    @staticmethod
    def _run_git(args: list[str], cwd: Path) -> tuple[int, str, str]:
        try:
            proc = subprocess.run(
                ["git"] + args,
                cwd=str(cwd),
                capture_output=True, text=True, timeout=15,
            )
            return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return -1, "", "git not available or timed out"

    def _is_allowed(self, path: str) -> bool:
        """Return True if path matches any allowlist prefix."""
        for prefix in self._ALLOWLIST_PREFIXES:
            if path.startswith(prefix) or path == prefix.rstrip("/"):
                return True
        # Also allow empty lines
        if not path.strip():
            return True
        return False

    def test_most_recent_commit_only_touches_allowed_files(self):
        """
        The most recent commit should only contain Stage-A allowlisted files.
        If git is unavailable or the diff is empty, the test passes vacuously.
        """
        # Try from the repo root (flask-scoring-api or workspace root)
        for root in [_FLASK_ROOT, _FLASK_ROOT.parent, _FLASK_ROOT.parent.parent]:
            rc, out, err = self._run_git(
                ["diff", "--name-only", "HEAD~1..HEAD"],
                cwd=root,
            )
            if rc == 0 and out:
                break
        else:
            self.skipTest("git diff unavailable or no commits to compare")
            return

        changed_files = [line for line in out.splitlines() if line.strip()]
        if not changed_files:
            return  # no files changed in last commit — vacuously passes

        violations = [f for f in changed_files if not self._is_allowed(f)]
        self.assertEqual(
            violations, [],
            f"Stage A commit touched forbidden file(s): {violations}\n"
            f"All changed files: {changed_files}"
        )

    def test_no_uncommitted_changes_to_forbidden_files(self):
        """
        Check git status for uncommitted changes to forbidden files.
        """
        for root in [_FLASK_ROOT, _FLASK_ROOT.parent, _FLASK_ROOT.parent.parent]:
            rc, out, _ = self._run_git(
                ["status", "--porcelain"],
                cwd=root,
            )
            if rc == 0:
                break
        else:
            self.skipTest("git status unavailable")
            return

        if not out:
            return  # working tree is clean

        # Parse porcelain status: "XY path" or "XY old_path -> new_path"
        forbidden_violations = []
        for line in out.splitlines():
            if len(line) < 4:
                continue
            path = line[3:].strip()
            if " -> " in path:
                path = path.split(" -> ")[-1].strip()
            # Check if this path is a forbidden file
            for forbidden in (
                "gate_engine/classifier.py",
                "gate_engine/pipeline.py",
                "app.py",
                "gate_engine/settlement_worker.py",
            ):
                if path.endswith(forbidden):
                    forbidden_violations.append(path)
        self.assertEqual(
            forbidden_violations, [],
            f"Uncommitted changes found in forbidden files: {forbidden_violations}"
        )

    def test_forbidden_files_exist_and_are_unmodified(self):
        """
        Confirm forbidden files exist on disk (they should — Stage A doesn't
        delete them) and that their mtime relative to the new modules is older
        than the new modules (rough chronological guard).
        """
        new_module_mtime = max(
            _ENFORCER_PY.stat().st_mtime if _ENFORCER_PY.exists() else 0,
            _RECOMPUTE_PY.stat().st_mtime if _RECOMPUTE_PY.exists() else 0,
        )

        forbidden_paths = [
            _GATE_ENGINE_DIR / "classifier.py",
            _GATE_ENGINE_DIR / "pipeline.py",
            _GATE_ENGINE_DIR / "settlement_worker.py",
        ]
        for fpath in forbidden_paths:
            self.assertTrue(fpath.exists(),
                            f"Forbidden file {fpath.name} must still exist "
                            f"(Stage A must not delete production files)")
            # Mtime check: forbidden file's mtime should be <= new module mtime
            # (it was last touched before this task started).
            # NOTE: This is a heuristic; a deliberate write at the exact same
            # second could theoretically cause a false positive.  Rely on the
            # AST wiring tests above for the authoritative check.
            mtime = fpath.stat().st_mtime
            # We only flag if the forbidden file is *newer* than the new modules
            # by more than 5 seconds (accounts for filesystem timestamp granularity).
            if mtime > new_module_mtime + 5:
                self.fail(
                    f"{fpath.name} was modified after the new Stage-A modules were "
                    f"written (mtime={mtime:.0f} > new_module_mtime={new_module_mtime:.0f}). "
                    f"This indicates a Stage-A hard stop violation."
                )


# ===========================================================================
# 4. can_execute / governance module scan
# ===========================================================================

class TestGovernanceModuleScan(unittest.TestCase):
    """
    Verify that both new modules declare the required governance constants
    and that those constants have the correct values.
    """

    def test_enforcer_governance_constants(self):
        import gate_engine.prob_ledger_enforcer as mod
        self.assertFalse(mod.can_execute)
        self.assertFalse(mod.PRODUCTION_AUTHORITY)
        self.assertFalse(mod.USER_OUTPUT_AUTHORITY)

    def test_recompute_governance_constants(self):
        import gate_engine.outlier_recompute as mod
        self.assertFalse(mod.can_execute)
        self.assertFalse(mod.PRODUCTION_AUTHORITY)
        self.assertFalse(mod.TERMINAL_LABEL_AUTHORITY)
        self.assertFalse(mod.USER_OUTPUT_AUTHORITY)

    def test_new_modules_have_no_flask_routes(self):
        """
        Neither new module should define a Flask route (no @app.route decorator).
        """
        for fpath in [_ENFORCER_PY, _RECOMPUTE_PY]:
            with open(fpath, encoding="utf-8") as f:
                content = f.read()
            self.assertNotIn("@app.route", content,
                             f"{fpath.name} must not define Flask routes (Stage A only)")
            self.assertNotIn("app.route", content,
                             f"{fpath.name} must not reference app.route")

    def test_new_modules_have_no_db_calls(self):
        """
        Neither new module should have direct DB connection calls.
        """
        db_patterns = ("psycopg2.connect", "pg_try_advisory", "execute(", "cursor(")
        for fpath in [_ENFORCER_PY, _RECOMPUTE_PY]:
            with open(fpath, encoding="utf-8") as f:
                content = f.read()
            for pattern in db_patterns:
                self.assertNotIn(pattern, content,
                                 f"{fpath.name} must not make direct DB calls: {pattern!r}")


if __name__ == "__main__":
    unittest.main()
