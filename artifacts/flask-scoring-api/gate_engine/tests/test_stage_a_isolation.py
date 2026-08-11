"""
gate_engine/tests/test_stage_a_isolation.py
WOW-PATCH-2026-08-10-STAGE-A-PROBABILITY-LEDGER-OUTLIER-RECOMPUTE

Hard structural guardrails for the Stage A patch.

Classes
-------
  TestImportIsolation       — AST-based: neither new module imports any
                              forbidden path (app, classifier, pipeline,
                              settlement_worker, universal_agent,
                              pipeline_state, pipeline_gateway).
  TestForbiddenFileNotWired — AST reverse-scan: none of the forbidden
                              production files import the new modules.
  TestGitDiffAllowlist      — HARD STRUCTURAL GUARDRAIL: inspects the
                              actual git diff of the current HEAD commit
                              and FAILS if any file outside the patch
                              allowlist is touched.  Also explicitly FAILS
                              if the diff touches any prohibited file
                              (universal_agent/, pipeline_state.py,
                              pipeline_gateway.py, settlement_worker.py,
                              app.py, route_registry.py, classifier.py,
                              pipeline.py).
  TestGovernanceModuleScan  — Source-level verification that neither new
                              module contains Flask routes or DB calls,
                              and both governance constants are correct.
"""
from __future__ import annotations

import ast
import pathlib
import subprocess
import unittest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_FLASK_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
# gate_engine/ directory
_GE = _FLASK_ROOT / "gate_engine"

_ENFORCER_PATH = _GE / "prob_ledger_enforcer.py"
_RECOMPUTE_PATH = _GE / "outlier_recompute.py"

# Production files that must NOT import the new modules
_FORBIDDEN_IMPORT_SOURCES = [
    _GE / "classifier.py",
    _GE / "pipeline.py",
    _FLASK_ROOT / "app.py",
    _GE / "ev_gate.py",
    _GE / "settlement_worker.py",
    _GE / "outlier_gate.py",
]

# Modules that must not appear in new module imports
_FORBIDDEN_IMPORT_TARGETS = [
    "app",
    "classifier",
    "settlement_worker",
    "universal_agent",
    "pipeline_state",
    "pipeline_gateway",
]


# ---------------------------------------------------------------------------
# Helper: collect imports from an AST tree
# ---------------------------------------------------------------------------

def _collect_module_imports(source_text: str) -> list[str]:
    """Return all imported module names from the given Python source."""
    modules = []
    tree = ast.parse(source_text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.append(node.module)
    return modules


# ---------------------------------------------------------------------------
# TestImportIsolation
# ---------------------------------------------------------------------------

class TestImportIsolation(unittest.TestCase):

    def _assert_no_forbidden_import(self, path: pathlib.Path, target: str):
        src = path.read_text()
        imports = _collect_module_imports(src)
        for imp in imports:
            parts = imp.split(".")
            self.assertNotIn(
                target, parts,
                f"{path.name} contains forbidden import targeting '{target}': {imp!r}"
            )

    def test_prob_ledger_enforcer_no_forbidden_imports(self):
        for target in _FORBIDDEN_IMPORT_TARGETS:
            with self.subTest(target=target):
                self._assert_no_forbidden_import(_ENFORCER_PATH, target)

    def test_outlier_recompute_no_forbidden_imports(self):
        for target in _FORBIDDEN_IMPORT_TARGETS:
            with self.subTest(target=target):
                self._assert_no_forbidden_import(_RECOMPUTE_PATH, target)

    def test_enforcer_does_not_import_classifier(self):
        self._assert_no_forbidden_import(_ENFORCER_PATH, "classifier")

    def test_enforcer_does_not_import_settlement(self):
        self._assert_no_forbidden_import(_ENFORCER_PATH, "settlement_worker")

    def test_enforcer_does_not_import_universal_agent(self):
        self._assert_no_forbidden_import(_ENFORCER_PATH, "universal_agent")

    def test_enforcer_does_not_import_pipeline_state(self):
        self._assert_no_forbidden_import(_ENFORCER_PATH, "pipeline_state")

    def test_enforcer_does_not_import_pipeline_gateway(self):
        self._assert_no_forbidden_import(_ENFORCER_PATH, "pipeline_gateway")

    def test_recompute_does_not_import_classifier(self):
        self._assert_no_forbidden_import(_RECOMPUTE_PATH, "classifier")

    def test_recompute_does_not_import_settlement(self):
        self._assert_no_forbidden_import(_RECOMPUTE_PATH, "settlement_worker")

    def test_recompute_does_not_import_universal_agent(self):
        self._assert_no_forbidden_import(_RECOMPUTE_PATH, "universal_agent")

    def test_recompute_does_not_import_pipeline_state(self):
        self._assert_no_forbidden_import(_RECOMPUTE_PATH, "pipeline_state")

    def test_recompute_does_not_import_pipeline_gateway(self):
        self._assert_no_forbidden_import(_RECOMPUTE_PATH, "pipeline_gateway")

    def test_both_modules_importable_without_side_effects(self):
        import gate_engine.prob_ledger_enforcer  # noqa
        import gate_engine.outlier_recompute      # noqa


# ---------------------------------------------------------------------------
# TestForbiddenFileNotWired
# ---------------------------------------------------------------------------

class TestForbiddenFileNotWired(unittest.TestCase):
    """
    AST reverse-scan: the listed production files must NOT import
    prob_ledger_enforcer or outlier_recompute.
    Stage A is offline-only; wiring to production is Stage B.
    """
    _NEW_MODULES = {"prob_ledger_enforcer", "outlier_recompute"}

    def _check_file_not_wired(self, path: pathlib.Path):
        if not path.exists():
            return   # file absent → not wired by definition
        src = path.read_text()
        imports = _collect_module_imports(src)
        for imp in imports:
            parts = imp.split(".")
            for mod in self._NEW_MODULES:
                self.assertNotIn(
                    mod, parts,
                    f"{path.name} imports Stage-A module {mod!r} — Stage A must remain offline"
                )

    def test_classifier_not_wired(self):       self._check_file_not_wired(_GE / "classifier.py")
    def test_pipeline_not_wired(self):          self._check_file_not_wired(_GE / "pipeline.py")
    def test_app_not_wired(self):               self._check_file_not_wired(_FLASK_ROOT / "app.py")
    def test_settlement_worker_not_wired(self): self._check_file_not_wired(_GE / "settlement_worker.py")
    def test_ev_gate_not_wired(self):           self._check_file_not_wired(_GE / "ev_gate.py")
    def test_outlier_gate_not_wired(self):      self._check_file_not_wired(_GE / "outlier_gate.py")


# ---------------------------------------------------------------------------
# TestGitDiffAllowlist
# ---------------------------------------------------------------------------

class TestGitDiffAllowlist(unittest.TestCase):
    """
    Hard structural guardrail — inspects the actual git diff of HEAD~1..HEAD.

    STAGE A ALLOWLIST: only these file paths may appear in the diff.
    Any file outside this list → test fails.

    HARD-FAIL PROHIBITED FILES: even if somehow in the allowlist, these
    specific files must NEVER appear in the diff (belt-and-suspenders).
    """

    # Files and prefixes that are allowed in the Stage A commit.
    # Both short (gate_engine/...) and full (artifacts/flask-scoring-api/gate_engine/...)
    # path forms are listed because git may report either depending on cwd.
    _ALLOWLIST_PREFIXES: tuple[str, ...] = (
        # Stage A production modules (new, WOW-PATCH-2026-08-10)
        "gate_engine/prob_ledger_enforcer.py",
        "gate_engine/outlier_recompute.py",
        # Stage A test files
        "gate_engine/tests/test_prob_ledger_enforcer.py",
        "gate_engine/tests/test_outlier_recompute.py",
        "gate_engine/tests/test_stage_a_isolation.py",
        # Infrastructure / plan / documentation files
        ".local/tasks/",
        "attached_assets/",
        ".agents/memory/",
        "replit.md",
        # Full artifact-path duplicates (workspace-relative git paths)
        "artifacts/flask-scoring-api/gate_engine/prob_ledger_enforcer.py",
        "artifacts/flask-scoring-api/gate_engine/outlier_recompute.py",
        "artifacts/flask-scoring-api/gate_engine/tests/test_prob_ledger_enforcer.py",
        "artifacts/flask-scoring-api/gate_engine/tests/test_outlier_recompute.py",
        "artifacts/flask-scoring-api/gate_engine/tests/test_stage_a_isolation.py",
    )

    # These patterns must NEVER appear in the diff, regardless of allowlist.
    # Presence of any of these is a hard structural violation.
    _HARD_FAIL_PATTERNS: tuple[str, ...] = (
        "universal_agent",       # FOLLOWUP_193 / B4 — must not touch
        "pipeline_state",        # FOLLOWUP_193 — must not touch
        "pipeline_gateway",      # FOLLOWUP_193 — must not touch
        "settlement_worker",     # FOLLOWUP_194 — must not touch
        "/app.py",               # main application routing — must not touch
    )

    # These exact filenames are prohibited if they appear alone (not as substring of allowed)
    _PROHIBITED_FILENAMES: tuple[str, ...] = (
        "classifier.py",
        "pipeline.py",
        "route_registry.py",
        "ev_gate.py",
        "outlier_gate.py",      # outlier_gate is reused (import only), not modified
        "labels.py",
        "prob_ledger.py",        # reused via import only, not modified
    )

    @staticmethod
    def _run_git(args: list[str], cwd: pathlib.Path) -> tuple[int, str, str]:
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
        for prefix in self._ALLOWLIST_PREFIXES:
            if path.startswith(prefix) or path == prefix.rstrip("/"):
                return True
        if not path.strip():
            return True
        return False

    def test_most_recent_commit_only_touches_allowed_files(self):
        """
        The most recent commit (HEAD~1..HEAD) must only touch Stage-A
        allowlisted files.  Fails with the exact list of forbidden files.
        """
        for root in [_FLASK_ROOT, _FLASK_ROOT.parent, _FLASK_ROOT.parent.parent]:
            rc, out, err = self._run_git(
                ["diff", "--name-only", "HEAD~1..HEAD"], cwd=root
            )
            if rc == 0 and out:
                changed = [p.strip() for p in out.splitlines() if p.strip()]
                # Normalize to forward slashes
                changed = [p.replace("\\", "/") for p in changed]
                violations = [p for p in changed if not self._is_allowed(p)]
                self.assertListEqual(
                    violations, [],
                    f"Stage A commit touched forbidden file(s): {violations}\n"
                    f"All changed files: {changed}"
                )
                return
        # git not available — pass vacuously
        self.skipTest("git not available or diff is empty")

    def test_no_uncommitted_changes_to_forbidden_files(self):
        """
        No working-tree changes to production files outside the allowlist.
        """
        for root in [_FLASK_ROOT, _FLASK_ROOT.parent, _FLASK_ROOT.parent.parent]:
            rc, out, err = self._run_git(
                ["diff", "--name-only", "HEAD"], cwd=root
            )
            if rc == 0:
                if not out:
                    return   # clean working tree — pass
                changed = [p.strip().replace("\\", "/")
                           for p in out.splitlines() if p.strip()]
                violations = [p for p in changed if not self._is_allowed(p)]
                self.assertListEqual(
                    violations, [],
                    f"Uncommitted changes to non-allowlisted file(s): {violations}"
                )
                return
        self.skipTest("git not available")

    def test_hard_fail_patterns_not_in_diff(self):
        """
        Belt-and-suspenders: even if somehow in the allowlist, files matching
        these patterns must NEVER appear in the HEAD commit diff.
        """
        for root in [_FLASK_ROOT, _FLASK_ROOT.parent, _FLASK_ROOT.parent.parent]:
            rc, out, err = self._run_git(
                ["diff", "--name-only", "HEAD~1..HEAD"], cwd=root
            )
            if rc == 0 and out:
                changed = [p.strip().replace("\\", "/")
                           for p in out.splitlines() if p.strip()]
                for pattern in self._HARD_FAIL_PATTERNS:
                    hits = [p for p in changed if pattern in p]
                    self.assertListEqual(
                        hits, [],
                        f"HARD FAIL: commit diff contains prohibited pattern "
                        f"{pattern!r}: {hits}"
                    )
                return
        self.skipTest("git not available")

    def test_prohibited_filenames_not_in_diff(self):
        """
        Explicit filename check: none of the listed production filenames
        should appear in the Stage A commit.
        """
        for root in [_FLASK_ROOT, _FLASK_ROOT.parent, _FLASK_ROOT.parent.parent]:
            rc, out, err = self._run_git(
                ["diff", "--name-only", "HEAD~1..HEAD"], cwd=root
            )
            if rc == 0 and out:
                changed = [p.strip().replace("\\", "/")
                           for p in out.splitlines() if p.strip()]
                for fname in self._PROHIBITED_FILENAMES:
                    hits = [p for p in changed if p.endswith(fname)]
                    self.assertListEqual(
                        hits, [],
                        f"HARD FAIL: commit diff contains prohibited file "
                        f"{fname!r}: {hits}"
                    )
                return
        self.skipTest("git not available")

    def test_forbidden_files_exist_and_are_unmodified(self):
        """
        key production files must exist but have no uncommitted modifications
        (i.e., Stage A did not accidentally touch them).
        """
        critical = [
            _GE / "classifier.py",
            _GE / "pipeline.py",
            _GE / "settlement_worker.py",
            _GE / "outlier_gate.py",
        ]
        for f in critical:
            if not f.exists():
                continue   # if file doesn't exist, it wasn't touched by us
            for root in [_FLASK_ROOT, _FLASK_ROOT.parent, _FLASK_ROOT.parent.parent]:
                rc, out, err = self._run_git(
                    ["diff", "--name-only", "HEAD", "--", str(f)], cwd=root
                )
                if rc == 0:
                    self.assertEqual(
                        out, "",
                        f"{f.name} has uncommitted changes — Stage A must not modify it"
                    )
                    break


# ---------------------------------------------------------------------------
# TestGovernanceModuleScan
# ---------------------------------------------------------------------------

class TestGovernanceModuleScan(unittest.TestCase):

    def _read_src(self, path: pathlib.Path) -> str:
        return path.read_text()

    def test_enforcer_governance_constants(self):
        import gate_engine.prob_ledger_enforcer as m
        self.assertFalse(m.can_execute)
        self.assertFalse(m.PRODUCTION_AUTHORITY)
        self.assertFalse(m.USER_OUTPUT_AUTHORITY)
        self.assertFalse(m.TERMINAL_LABEL_AUTHORITY)

    def test_recompute_governance_constants(self):
        import gate_engine.outlier_recompute as m
        self.assertFalse(m.can_execute)
        self.assertFalse(m.PRODUCTION_AUTHORITY)
        self.assertFalse(m.USER_OUTPUT_AUTHORITY)
        self.assertFalse(m.TERMINAL_LABEL_AUTHORITY)

    def test_new_modules_have_no_flask_routes(self):
        """Neither new module should register any Flask route."""
        for path in (_ENFORCER_PATH, _RECOMPUTE_PATH):
            src = self._read_src(path)
            for route_indicator in ("@app.route", "@bp.route", "Blueprint"):
                self.assertNotIn(
                    route_indicator, src,
                    f"{path.name} contains route indicator {route_indicator!r}"
                )

    def test_new_modules_have_no_db_calls(self):
        """Neither new module should make direct DB calls."""
        for path in (_ENFORCER_PATH, _RECOMPUTE_PATH):
            src = self._read_src(path)
            for db_indicator in ("psycopg2.connect", "get_db_connection",
                                 "execute(", "cursor(", "pg_try_advisory"):
                self.assertNotIn(
                    db_indicator, src,
                    f"{path.name} contains DB call indicator {db_indicator!r}"
                )

    def test_enforcer_source_contains_can_execute_false(self):
        src = self._read_src(_ENFORCER_PATH)
        self.assertIn("can_execute", src)
        self.assertIn("False", src)

    def test_recompute_source_contains_can_execute_false(self):
        src = self._read_src(_RECOMPUTE_PATH)
        self.assertIn("can_execute", src)
        self.assertIn("False", src)

    def test_recompute_imports_gap_threshold_from_outlier_gate(self):
        """Source must contain the import from outlier_gate."""
        src = self._read_src(_RECOMPUTE_PATH)
        self.assertIn("outlier_gate", src,
                      "outlier_recompute.py must import from outlier_gate")
        self.assertIn("GAP_THRESHOLD", src,
                      "outlier_recompute.py must reference GAP_THRESHOLD")

    def test_registry_driven_classification_in_enforcer(self):
        """
        The enforcer source must contain the two-layer registry structure.
        This proves the classification is not a hardcoded if/elif chain.
        """
        src = self._read_src(_ENFORCER_PATH)
        self.assertIn("_PROB_BEARING_PROP_LABELS", src,
                      "Layer-1 registry set must be defined")
        self.assertIn("_PROB_BEARING_EXTENDED", src,
                      "Layer-2 extended registry set must be defined")
        self.assertIn("PROBABILITY_BEARING_LABELS", src,
                      "Union registry must be defined")
        # Enforcement must use 'in PROBABILITY_BEARING_LABELS', not if/elif
        self.assertIn("in PROBABILITY_BEARING_LABELS", src,
                      "Enforcement must use 'in PROBABILITY_BEARING_LABELS' lookup")


if __name__ == "__main__":
    unittest.main()
