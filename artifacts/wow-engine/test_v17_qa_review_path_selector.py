"""Positive and negative checks for the deterministic QA review-path selector (PR 1).

The selector may only ever fail toward more review. Every case below that is not the
explicit positive path must route to ``scoped_independent`` and must never pass
zero-credit QA.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "artifacts/wow-engine/v17"))
sys.path.insert(0, str(ROOT / ".github/scripts"))
import independent_release_checks as policy
import qa_review_path_selector as sel

SHA = "a" * 40
BASE = "b" * 40
REPO = "gregoryharper84-ship-it/WOW-Dashboard"
ALLOW = policy.CLASS_A_EXACT_PATHS
README = "artifacts/wow-engine/learning/README.md"
TEST_FILE = "artifacts/wow-engine/learning/tests/test_evaluate.py"
EVALUATE = "artifacts/wow-engine/learning/evaluate.py"
CASES = "artifacts/wow-engine/learning/data/cases.json"
PATCH = "@@ -1,1 +1,2 @@\n context\n+added line\n"


def is_root(path):
    return path.startswith((".github/", ".agents/", ".claude/"))


def entry(path=README, status="modified", patch=PATCH, **extra):
    return {"filename": path, "status": status, "patch": patch, "sha": "c" * 40, **extra}


def choose(files, types=None, **kw):
    types = types if types is not None else {e.get("filename"): "file" for e in files}
    return sel.select_review_path(
        files, allowlist=kw.pop("allowlist", ALLOW), content_types=types, trust_root_fn=is_root, **kw
    )


def reasons_of(result):
    return " ".join(result["reasons"])


# ---- positive ----------------------------------------------------------------
def test_eligible_class_a_takes_deterministic_path():
    r = choose([entry()])
    assert (r["path"], r["mode"], r["reasons"]) == (sel.PATH_DETERMINISTIC, "zero_credit", [])
    assert r["can_execute"] is False


def test_additive_test_coverage_and_additive_data_stay_eligible():
    added = "@@ -1,1 +1,3 @@\n context\n+def test_new():\n+    assert 1 + 1 == 2\n"
    assert choose([entry(TEST_FILE, patch=added)])["path"] == sel.PATH_DETERMINISTIC
    assert choose([entry(CASES, patch='@@ -1,1 +1,2 @@\n   {"a": 1},\n+  {"b": 2},\n')])["path"] == sel.PATH_DETERMINISTIC


# ---- inventory / path ----------------------------------------------------------
def test_non_allowlisted_and_mixed_pr_route_to_independent_review():
    other = "artifacts/wow-engine/v17/nba_current_pregame_scorer.py"
    assert choose([entry(other)])["path"] == sel.PATH_SCOPED
    mixed = choose([entry(), entry(other)])
    assert mixed["path"] == sel.PATH_SCOPED and f"{other}:PATH_NOT_ALLOWLISTED" in mixed["reasons"]


def test_trust_root_path_is_reported_even_when_allowlisted():
    wf = ".github/workflows/wow-v17-independent-qa-check.yml"
    r = choose([entry(wf)], allowlist=ALLOW | {wf})
    assert r["path"] == sel.PATH_SCOPED and f"{wf}:TRUST_ROOT_PATH" in r["reasons"]


def test_rename_whose_origin_is_trust_root_is_scoped():
    r = choose([entry(README, status="renamed", previous_filename=".github/workflows/x.yml")])
    text = reasons_of(r)
    assert r["path"] == sel.PATH_SCOPED
    assert "TRUST_ROOT_PATH" in text and "RENAME_OR_COPY" in text and "OPERATION_NOT_ALLOWED:renamed" in text


def test_deletion_copy_and_unknown_operation_are_scoped():
    for status in ("removed", "renamed", "copied", "unchanged", None):
        assert choose([entry(status=status)])["path"] == sel.PATH_SCOPED, status


def test_empty_truncated_and_oversized_inventories_are_scoped():
    assert "FILES_EMPTY" in reasons_of(choose([]))
    assert "FILES_TRUNCATED" in reasons_of(choose([entry()], declared_changed_files=2))
    many = [entry(f"d/f{i}.md") for i in range(sel.MAX_FILES + 1)]
    assert "TOO_MANY_FILES" in reasons_of(choose(many, allowlist=frozenset(e["filename"] for e in many)))
    assert "INVENTORY_ERROR:FILES_UNREADABLE:500" in reasons_of(choose([entry()], inventory_errors=["FILES_UNREADABLE:500"]))


def test_malformed_records_are_scoped():
    assert choose([{"status": "modified", "patch": PATCH}])["path"] == sel.PATH_SCOPED
    assert choose([entry(path="")])["path"] == sel.PATH_SCOPED


# ---- object type ---------------------------------------------------------------
def test_symlink_submodule_directory_and_unreadable_object_types_are_scoped():
    for kind in ("symlink", "submodule", "dir"):
        r = choose([entry()], {README: kind})
        assert r["path"] == sel.PATH_SCOPED and f"CONTENT_TYPE_NOT_REGULAR_FILE:{kind}" in reasons_of(r)
    assert "CONTENT_TYPE_UNREADABLE" in reasons_of(choose([entry()], {README: None}))
    assert "CONTENT_TYPE_UNREADABLE" in reasons_of(choose([entry()], {}))


# ---- content-aware -------------------------------------------------------------
def test_missing_or_empty_patch_is_scoped():
    for patch in (None, ""):
        assert "PATCH_UNAVAILABLE" in reasons_of(choose([entry(patch=patch)]))


def test_test_weakening_is_never_eligible_by_path_alone():
    weak = {
        "TEST_ASSERTION_REMOVED": "@@ -1,2 +1,1 @@\n context\n-    assert result == 3\n",
        "TEST_ASSERTION_REMOVED ": "@@ -1,2 +1,1 @@\n context\n-def test_gate():\n",
        "TEST_ASSERTION_REMOVED  ": "@@ -1,2 +1,2 @@\n-    self.assertEqual(a, b)\n+    pass\n",
        "TEST_SKIP_ADDED": "@@ -1,1 +1,2 @@\n context\n+@pytest.mark.skip(reason='later')\n",
        "TEST_SKIP_ADDED ": "@@ -1,1 +1,2 @@\n context\n+@pytest.mark.xfail\n",
        "TEST_SKIP_ADDED  ": "@@ -1,1 +1,2 @@\n context\n+    self.skipTest('x')\n",
        "TRIVIAL_ASSERT_ADDED": "@@ -1,1 +1,2 @@\n context\n+    assert True\n",
    }
    for code, patch in weak.items():
        r = choose([entry(TEST_FILE, patch=patch)])
        assert r["path"] == sel.PATH_SCOPED and code.strip() in reasons_of(r), code


def test_data_file_may_only_grow():
    r = choose([entry(CASES, patch='@@ -1,2 +1,2 @@\n-  {"expected": 1},\n+  {"expected": 9},\n')])
    assert r["path"] == sel.PATH_SCOPED and "DATA_LINES_REMOVED" in reasons_of(r)


def test_added_process_network_or_dynamic_code_is_scoped_but_removal_is_not():
    for line in ("import subprocess", "os.system('x')", "eval(s)", "exec(s)", "import requests",
                 "import socket", "os.environ['K']", "__import__('x')", "import pickle"):
        r = choose([entry(EVALUATE, patch=f"@@ -1,1 +1,2 @@\n context\n+{line}\n")])
        assert r["path"] == sel.PATH_SCOPED and "RISKY_CALL_ADDED" in reasons_of(r), line
    removed = choose([entry(EVALUATE, patch="@@ -1,2 +1,1 @@\n context\n-import subprocess\n")])
    assert removed["path"] == sel.PATH_DETERMINISTIC


# ---- binding ---------------------------------------------------------------------
def test_diff_and_policy_hashes_bind_content_and_policy():
    a, b = choose([entry()]), choose([entry(patch=PATCH + "+more\n")])
    assert a["diff_sha256"] != b["diff_sha256"]
    assert a["policy_sha256"] == choose([entry()])["policy_sha256"]
    assert a["policy_sha256"] != choose([entry()], allowlist=ALLOW | {"x.md"})["policy_sha256"]


def test_selector_reads_no_flag_or_environment():
    import ast
    tree = ast.parse((ROOT / "artifacts/wow-engine/v17/qa_review_path_selector.py").read_text())
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not imported & {"os", "subprocess", "sys"} and not attrs & {"environ", "getenv"}


# ---- exact-head glue (never raises, always fails toward more review) -------------
def fake_fetch(*, pr_status=200, head=SHA, files=None, files_status=200, ctype="file", declared=1, log=None):
    files = [entry()] if files is None else files

    def fetch(path):
        if log is not None:
            log.append(path)
        if "/contents/" in path:
            return 200, {"type": ctype}
        if path.endswith(("/files?per_page=100&page=1",)):
            return files_status, files if files_status == 200 else None
        if path.startswith(f"repos/{REPO}/pulls/"):
            return pr_status, {"head": {"sha": head}, "base": {"sha": BASE}, "changed_files": declared}
        return 404, None
    return fetch


def run_pr(fetch):
    return sel.select_for_pr(REPO, 7, SHA, fetch=fetch, allowlist=ALLOW, trust_root_fn=is_root)


def test_select_for_pr_positive_records_exact_head_and_base():
    r = run_pr(fake_fetch())
    assert (r["path"], r["head_sha"], r["base_sha"], r["pr_number"]) == (sel.PATH_DETERMINISTIC, SHA, BASE, 7)


def test_select_for_pr_fails_toward_review_on_every_error():
    assert "HEAD_STALE" in reasons_of(run_pr(fake_fetch(head="d" * 40)))
    assert "PULL_REQUEST_UNREADABLE:500" in reasons_of(run_pr(fake_fetch(pr_status=500)))
    assert "FILES_UNREADABLE:403" in reasons_of(run_pr(fake_fetch(files_status=403)))
    assert run_pr(fake_fetch(ctype="symlink"))["path"] == sel.PATH_SCOPED
    assert run_pr(fake_fetch(declared=2))["path"] == sel.PATH_SCOPED
    assert run_pr(lambda path: (0, None))["path"] == sel.PATH_SCOPED


def test_select_for_pr_skips_object_lookups_unless_allowlist_holds():
    log = []
    r = run_pr(fake_fetch(files=[entry("artifacts/wow-engine/v17/other.py")], log=log))
    assert r["path"] == sel.PATH_SCOPED and not any("/contents/" in p for p in log)


# ---- QA/Release decision: no false PASS --------------------------------------------
def zc_evidence(selection):
    from test_v17_zero_credit_release import evidence
    ev = evidence()
    ev["pr"]["head_sha"] = SHA
    ev["review_path"] = selection
    return ev


def good_selection():
    return {**choose([entry()]), "head_sha": SHA}


def qa(ev):
    return policy.qa_findings(ev, governance_files=frozenset())


def test_zero_credit_passes_only_with_a_matching_deterministic_selection():
    assert qa(zc_evidence(good_selection())) == []


def test_zero_credit_never_passes_without_or_against_the_selection():
    assert "QA_ZERO_CREDIT_REVIEW_PATH_INELIGIBLE" in qa(zc_evidence(None))
    scoped = {**choose([entry(patch=None)]), "head_sha": SHA}
    findings = qa(zc_evidence(scoped))
    assert "QA_ZERO_CREDIT_REVIEW_PATH_INELIGIBLE" in findings
    assert any(f.startswith("QA_ZERO_CREDIT_CONTENT_INELIGIBLE:") for f in findings)
    stale = {**good_selection(), "head_sha": "e" * 40}
    assert "QA_ZERO_CREDIT_REVIEW_PATH_STALE" in qa(zc_evidence(stale))
    wrong_policy = {**good_selection(), "policy_sha256": "0" * 64}
    assert "QA_ZERO_CREDIT_REVIEW_PATH_STALE" in qa(zc_evidence(wrong_policy))


def test_release_rederives_the_same_findings():
    ev = zc_evidence({**choose([entry(patch=None)]), "head_sha": SHA})
    assert any(f.startswith("RELEASE_QA_ZERO_CREDIT_") for f in
               policy.release_findings(ev, qa_app_id="1", release_app_id="2", governance_files=frozenset()))


def test_independent_mode_without_review_receipt_never_passes():
    ev = zc_evidence(good_selection())
    ev["qa_review_policy"]["mode"] = "independent"
    ev["independent_review_active"] = True
    ev["source_review"] = None
    assert "QA_INDEPENDENT_SOURCE_REVIEW_MISSING" in qa(ev)


# ---- workflows: no flag can select or conflict with a path -------------------------
def test_workflows_derive_the_path_and_no_flag_selects_it():
    qa_wf = (ROOT / ".github/workflows/wow-v17-independent-qa-check.yml").read_text()
    release_wf = (ROOT / ".github/workflows/wow-v17-release-authority-check.yml").read_text()
    step = qa_wf[qa_wf.index("Select exact-head review policy"):qa_wf.index("Build immutable QA review-policy receipt")]
    assert "select-review-path" in step
    for forbidden in ("QA_REVIEW_MODES_CONFLICT", "mode=legacy", "mode=canary", "mode=independent\n"):
        assert forbidden not in step, forbidden
    assert "QA_LEGACY_REVIEW_FLAGS_IGNORED" in step
    for var in ("WOW_QA_ZERO_CREDIT_MODE", "WOW_QA_INDEPENDENT_REVIEW_ACTIVE"):
        assert var not in release_wf
