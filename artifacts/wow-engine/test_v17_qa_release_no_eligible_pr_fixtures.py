"""Phase A regression fixtures for #1926 (coordinated under #1934 workstream 3).

Scope is test-and-fixture only: no production/workflow source change ships here.
These are deterministic, network-free reproductions of the exact post-merge #1915
incident evidence cited in #1926 (QA run 39642765367 / Release run 39642800720):

    workflow_run with WR_PRS=[] -> QA exits 1 with QA_TARGETS_UNRESOLVED
    -> QA never uploads its policy artifact
    -> Release's resolve-qa-policy fails QA_POLICY_ARTIFACT_DUPLICATE_OR_MISSING

Two tests below (marked xfail(strict=True)) encode the NOT-YET-IMPLEMENTED target
behavior from #1926/#1934: a typed ``NO_ELIGIBLE_PR`` neutral outcome instead of a
job failure, and a neutral Release follower. They must stay red until a separate
Phase B implementation PR lands; a strict xfail flips to a reported XPASS (not a
silent pass) the moment that happens, which is the signal to remove the marker.
Every other test in this file asserts behavior that already holds today and is
intended to keep holding across the Phase B change.

Deliberately kept in its own file, separate from test_v17_independent_release_checks.py,
to avoid overlapping #1923 (verify_existing_pr_governance_receipt.py) and #1932
(QA workflow run-name) in-flight PRs.
"""
import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "artifacts/wow-engine/v17/independent_release_checks.py"
SOURCE_REVIEW_MODULE = ROOT / ".github/scripts/independent_source_review_receipt.py"
QA_WF = ROOT / ".github/workflows/wow-v17-independent-qa-check.yml"
REL_WF = ROOT / ".github/workflows/wow-v17-release-authority-check.yml"
REPO = "gregoryharper84-ship-it/WOW-Dashboard"
FOREIGN_REPO = "someone-else/WOW-Dashboard-fork"
HEAD = "a" * 40
OLD = "b" * 40

spec = importlib.util.spec_from_file_location("independent_release_checks_no_eligible_pr", MODULE)
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)

source_spec = importlib.util.spec_from_file_location(
    "independent_source_review_receipt_no_eligible_pr", SOURCE_REVIEW_MODULE
)
source_review = importlib.util.module_from_spec(source_spec)
assert source_spec and source_spec.loader
sys.modules[source_spec.name] = source_review
source_spec.loader.exec_module(source_review)


class Fake:
    """Deterministic ``fetch``/``get_json`` double. Asserts never called on a path
    list of forbidden calls, so a sweep-fallback regression fails loudly rather
    than silently guessing a PR."""

    def __init__(self, routes, forbidden=()):
        self.routes = routes
        self.forbidden = set(forbidden)
        self.calls = []

    def __call__(self, path):
        self.calls.append(path)
        assert not any(path.startswith(f) for f in self.forbidden), f"unexpected call: {path}"
        return self.routes.get(path, (404, {"message": "Not Found"}))


def _step_script(wf, name):
    body = wf.read_text().split(f"      - name: {name}\n", 1)[1]
    lines = []
    for line in body.splitlines():
        if line.strip() and len(line) - len(line.lstrip()) <= 6:
            break
        lines.append(line)
    return dedent("\n".join(lines).split("        run: |\n", 1)[1]) + "\n"


@pytest.fixture(scope="module")
def bash_path(tmp_path_factory):
    """The real workflows invoke ``python`` (as `actions/setup-python` guarantees
    on the runner); shim that onto PATH so these scripts run unmodified locally."""
    bindir = tmp_path_factory.mktemp("bin")
    (bindir / "python").symlink_to(sys.executable)
    return f"{bindir}:{os.environ['PATH']}"


# --- 1. targets(): empty / invalid workflow_run PR identity, never a guess -----------


def test_empty_workflow_run_pr_list_resolves_to_no_targets_without_guessing():
    """An authenticated workflow_run with an empty pull_requests array must resolve
    to zero targets and zero errors -- never fall back to a repo-wide sweep."""
    out = m.targets(REPO, workflow_run_prs=[], fetch=Fake({}, forbidden=("repos/",)))
    assert out == {"targets": [], "errors": []}


def test_workflow_run_entries_with_no_usable_identity_are_skipped_not_guessed():
    """Entries missing a PR number, a non-main base, or an invalid head SHA are
    dropped (optionally typed) -- never silently promoted to a resolved target."""
    prs = [
        {"head": {"sha": HEAD}, "base": {"ref": "main"}},            # no number
        {"number": 11, "head": {"sha": HEAD}, "base": {"ref": "dev"}},  # wrong base
        {"number": 12, "head": {"sha": "not-a-sha"}, "base": {"ref": "main"}},
    ]
    out = m.targets(REPO, workflow_run_prs=prs, fetch=Fake({}, forbidden=(f"repos/{REPO}/pulls/11",)))
    assert out["targets"] == []
    assert out["errors"] == ["TARGET_HEAD_INVALID:12"]


def test_workflow_run_resolution_never_falls_back_to_sweep_when_prs_key_present():
    """``workflow_run_prs=[]`` (the authenticated empty-PR event shape) must take a
    materially different code path than omitting the key entirely (used only for
    an explicit unscoped sweep elsewhere in this module)."""
    sweep_route = f"repos/{REPO}/pulls?state=open&base=main&per_page=100"
    out = m.targets(REPO, workflow_run_prs=[], fetch=Fake({}, forbidden=(sweep_route,)))
    assert out == {"targets": [], "errors": []}


# --- 2. The exact #1926 incident: bash-level reproduction ---------------------------


def test_qa_workflow_run_with_empty_pr_identity_currently_fails_closed(bash_path):
    """Reproduces QA run 39642765367 from #1926: event=workflow_run,
    WR_PRS=[] currently exits 1 with QA_TARGETS_UNRESOLVED -- a false QA failure for
    a main-branch run that was never associated with any PR."""
    proc = subprocess.run(
        ["bash", "-c", _step_script(QA_WF, "Resolve one exact-head QA target")],
        cwd=ROOT,
        env=dict(os.environ, PATH=bash_path, GITHUB_REPOSITORY=REPO, GH_TOKEN="read-only",
                 EVENT="workflow_run", INPUT_PR="", WR_PRS="[]",
                 GITHUB_OUTPUT="/dev/null"),
        capture_output=True, text=True,
    )
    assert proc.returncode != 0
    assert "::error::QA_TARGETS_UNRESOLVED: workflow_run PR identity is missing" in proc.stdout


@pytest.mark.xfail(
    strict=True,
    reason="#1926/#1934 Phase B: empty workflow_run PR identity must resolve to a "
           "typed NO_ELIGIBLE_PR neutral/skipped outcome, not a job failure. Flip to "
           "XPASS (and delete this marker) once that implementation PR lands.",
)
def test_qa_workflow_run_with_empty_pr_identity_should_resolve_neutral_not_fail(bash_path):
    proc = subprocess.run(
        ["bash", "-c", _step_script(QA_WF, "Resolve one exact-head QA target")],
        cwd=ROOT,
        env=dict(os.environ, PATH=bash_path, GITHUB_REPOSITORY=REPO, GH_TOKEN="read-only",
                 EVENT="workflow_run", INPUT_PR="", WR_PRS="[]",
                 GITHUB_OUTPUT="/dev/null"),
        capture_output=True, text=True,
    )
    assert proc.returncode == 0
    assert "NO_ELIGIBLE_PR" in proc.stdout


def test_authenticated_no_eligible_pr_never_consults_manual_dispatch_input(bash_path):
    """A workflow_run trigger is the authenticated source of truth for its own PR
    identity; a stray/leftover ``inputs.pr_number`` (only meaningful on
    workflow_dispatch) must never be consulted to fill an empty workflow_run PR
    list -- that would be exactly the "guess a PR" behavior #1926 forbids."""
    proc = subprocess.run(
        ["bash", "-c", _step_script(QA_WF, "Resolve one exact-head QA target")],
        cwd=ROOT,
        env=dict(os.environ, PATH=bash_path, GITHUB_REPOSITORY=REPO, GH_TOKEN="read-only",
                 EVENT="workflow_run", INPUT_PR="999", WR_PRS="[]",
                 GITHUB_OUTPUT="/dev/null"),
        capture_output=True, text=True,
    )
    assert "999" not in proc.stdout
    assert proc.returncode != 0  # current fail-closed behavior; see xfail above for the target fix


# --- 3. Release follower: neutral-upstream cascade -----------------------------------


def _resolve_qa_policy_missing():
    return source_review.resolve_qa_review_policy(
        repository=REPO, run_id=39642765367, run_attempt=1, token="read-only",
        get_json=lambda path: (
            {"id": 39642765367, "run_attempt": 1} if path == "/actions/runs/39642765367"
            else {"artifacts": []}
        ),
    )


def test_release_resolve_qa_policy_reproduces_artifact_missing_when_qa_skipped():
    """Reproduces Release run 39642800720 from #1926: a genuinely neutral/no-target
    QA run (today: a QA job that failed closed before uploading its policy
    receipt) leaves Release's policy lookup with zero matching artifacts."""
    with pytest.raises(source_review.SourceReviewError, match="QA_POLICY_ARTIFACT_DUPLICATE_OR_MISSING"):
        _resolve_qa_policy_missing()


def test_release_resolve_qa_policy_detects_duplicate_artifacts_as_malformed():
    """Two artifacts matching the expected pinned name is just as untrustworthy as
    zero -- both are typed identically so Release cannot pick either nondeterministically."""
    duplicate = {"name": "wow-v17-qa-policy-555-1", "id": 1, "expired": False,
                 "created_at": "2026-10-09T03:00:00Z"}
    with pytest.raises(source_review.SourceReviewError, match="QA_POLICY_ARTIFACT_DUPLICATE_OR_MISSING"):
        source_review.resolve_qa_review_policy(
            repository=REPO, run_id=555, run_attempt=1, token="read-only",
            get_json=lambda path: (
                {"id": 555, "run_attempt": 1} if path == "/actions/runs/555"
                else {"artifacts": [duplicate, dict(duplicate, id=2)]}
            ),
        )


def test_release_resolve_qa_policy_rejects_cross_repository_run():
    with pytest.raises(source_review.SourceReviewError, match="QA_POLICY_REPOSITORY_MISMATCH"):
        source_review.resolve_qa_review_policy(
            repository=FOREIGN_REPO, run_id=555, run_attempt=1, token="read-only",
            get_json=lambda path: (_ for _ in ()).throw(AssertionError("must fail before any fetch")),
        )


@pytest.mark.parametrize("run_id, run_attempt", [(0, 1), (-1, 1), (1, 0), (1, -1)])
def test_release_resolve_qa_policy_rejects_non_positive_run_identity(run_id, run_attempt):
    with pytest.raises(source_review.SourceReviewError):
        source_review.resolve_qa_review_policy(
            repository=REPO, run_id=run_id, run_attempt=run_attempt, token="read-only",
            get_json=lambda path: (_ for _ in ()).throw(AssertionError("must fail before any fetch")),
        )


@pytest.mark.xfail(
    strict=True,
    reason="#1926/#1934 Phase B: Release must treat a neutral/no-target upstream QA "
           "run as a skip, not QA_POLICY_ARTIFACT_DUPLICATE_OR_MISSING. Flip to XPASS "
           "(and delete this marker) once that implementation PR lands.",
)
def test_release_follower_should_be_neutral_when_upstream_qa_had_no_eligible_pr():
    # Today, an upstream QA run that resolved zero targets never ran the policy-receipt
    # step at all, so Release's lookup legitimately finds nothing -- which is exactly
    # the false-failure cascade #1926 reports. Phase B's neutral QA outcome must carry
    # an explicit, typed "no target" marker Release can recognize as a skip rather than
    # treating absence of evidence as a malformed/missing artifact.
    resolved = source_review.resolve_qa_review_policy(
        repository=REPO, run_id=39642765367, run_attempt=1, token="read-only",
        get_json=lambda path: (
            {"id": 39642765367, "run_attempt": 1} if path == "/actions/runs/39642765367"
            else {"artifacts": []}
        ),
    )
    assert resolved.get("outcome") == "NO_ELIGIBLE_PR"


def test_release_publish_step_is_gated_on_decide_having_actually_produced_a_count():
    """Defence in depth for the cascade: whether or not Phase B changes the QA
    side, the Release workflow's publish step is only reachable when its decide
    step actually ran to completion and emitted a ``count`` output. A decide step
    that fails (e.g. the resolve-qa-policy SystemExit reproduced above) never sets
    that output, so the real workflow job never even attempts to publish -- no
    false Release App check is fabricated from a resolution failure."""
    text = REL_WF.read_text()
    publish_if = text.split('      - name: Publish exact-head check\n', 1)[1].split("\n", 2)[0]
    assert "steps.decide.outputs.count != '0'" in publish_if


def test_release_publish_step_errors_closed_on_an_unparseable_decision_glob(tmp_path):
    """If the publish step *did* somehow run against a decisions directory that
    was never populated, it must still fail loudly (typed invariant violation)
    rather than post anything: there is no silent path to a fabricated check-run."""
    env = dict(os.environ, GITHUB_REPOSITORY=REPO, RUNNER_TEMP=str(tmp_path / "nonexistent"),
               GITHUB_STEP_SUMMARY=str(tmp_path / "summary.md"), GH_TOKEN="release-app-token")
    proc = subprocess.run(
        ["bash", "-c", _step_script(REL_WF, "Publish exact-head check")],
        env=env, capture_output=True, text=True,
    )
    assert proc.returncode != 0
    assert "::error::RELEASE_DECISION_INVARIANT_VIOLATED" in proc.stdout
    assert "gh api" not in proc.stdout  # no evidence the publish POST was ever reached


# --- 4. Invalid manual (workflow_dispatch) PR targets --------------------------------


@pytest.mark.parametrize("bad_pr", ["-7", "7.0", " 7", "7 ", "1e1", "7;rm -rf", "", "abc"])
def test_dispatch_rejects_every_non_canonical_pr_number_shape(bad_pr):
    out = m.targets(REPO, pr=bad_pr, fetch=Fake({}, forbidden=("repos/",)))
    assert out["targets"] == []
    assert out["errors"] == ["TARGET_PR_INVALID"]


@pytest.mark.parametrize("digit_pr", ["0", "007"])
def test_digit_shaped_pr_zero_or_zero_padded_is_not_format_rejected_but_fails_on_lookup(digit_pr):
    """Not a format violation (``str.isdigit()`` accepts them), but PR 0 and
    leading-zero numbers never exist as real GitHub PR numbers, so they fail
    closed one step later as an unreadable target -- never silently resolved."""
    out = m.targets(REPO, pr=digit_pr, fetch=Fake({}))
    assert out == {"targets": [], "errors": ["TARGET_PR_UNREADABLE:404"]}


def test_dispatch_target_in_a_foreign_repository_is_rejected():
    live = {"number": 7, "head": {"sha": HEAD, "ref": "x", "repo": {"full_name": FOREIGN_REPO}},
            "base": {"ref": "main", "repo": {"full_name": FOREIGN_REPO}}}
    out = m.targets(REPO, pr="7", fetch=Fake({f"repos/{REPO}/pulls/7": (200, live)}))
    assert out == {"targets": [], "errors": ["TARGET_PR_IDENTITY_MISMATCH"]}


def test_dispatch_target_against_non_main_base_is_rejected():
    live = {"number": 7, "head": {"sha": HEAD, "ref": "x", "repo": {"full_name": REPO}},
            "base": {"ref": "release", "repo": {"full_name": REPO}}}
    out = m.targets(REPO, pr="7", fetch=Fake({f"repos/{REPO}/pulls/7": (200, live)}))
    assert out == {"targets": [], "errors": ["TARGET_PR_IDENTITY_MISMATCH"]}


def test_dispatch_target_for_a_nonexistent_pr_is_unreadable_not_silently_skipped():
    out = m.targets(REPO, pr="999999", fetch=Fake({}))
    assert out == {"targets": [], "errors": ["TARGET_PR_UNREADABLE:404"]}


def test_invalid_dispatch_target_never_reaches_app_token_or_publish(bash_path, tmp_path):
    """End-to-end: an invalid manual dispatch target fails the resolve step and must
    never mint the checks-write App token nor publish a check."""
    output_file = tmp_path / "out.txt"
    proc = subprocess.run(
        ["bash", "-c", _step_script(QA_WF, "Resolve one exact-head QA target")],
        cwd=ROOT,
        env=dict(os.environ, PATH=bash_path, GITHUB_REPOSITORY=REPO, GH_TOKEN="read-only",
                 EVENT="workflow_dispatch", INPUT_PR="7;rm -rf", WR_PRS="null",
                 GITHUB_OUTPUT=str(output_file)),
        capture_output=True, text=True,
    )
    assert proc.returncode != 0
    assert "::error::QA_TARGETS_UNRESOLVED" in proc.stdout
    assert not output_file.exists() or "pr_number=" not in output_file.read_text()


# --- 5. Wrong repository / head identity (adjacent to, not overlapping, #1932) -------


def test_workflow_run_target_whose_live_pr_moved_to_a_different_head_holds():
    event_pr = [{"number": 7, "head": {"sha": HEAD}, "base": {"ref": "main"}}]
    moved = {"number": 7, "head": {"sha": OLD, "repo": {"full_name": REPO}},
             "base": {"ref": "main", "repo": {"full_name": REPO}}}
    out = m.targets(REPO, workflow_run_prs=event_pr, fetch=Fake({f"repos/{REPO}/pulls/7": (200, moved)}))
    assert out == {"targets": [], "errors": ["TARGET_PR_IDENTITY_MISMATCH:7"]}


def test_workflow_run_target_whose_live_pr_is_in_a_different_repository_holds():
    event_pr = [{"number": 7, "head": {"sha": HEAD}, "base": {"ref": "main"}}]
    foreign = {"number": 7, "head": {"sha": HEAD, "repo": {"full_name": FOREIGN_REPO}},
               "base": {"ref": "main", "repo": {"full_name": REPO}}}
    out = m.targets(REPO, workflow_run_prs=event_pr, fetch=Fake({f"repos/{REPO}/pulls/7": (200, foreign)}))
    assert out == {"targets": [], "errors": ["TARGET_PR_IDENTITY_MISMATCH:7"]}


def test_dispatch_targets_each_pr_at_a_distinct_canonical_repository():
    """A dispatch run against repo B must never resolve a PR number that only
    exists, at that number, in repo A -- the repo string is part of identity."""
    live_in_a = {"number": 7, "head": {"sha": HEAD, "ref": "x", "repo": {"full_name": REPO}},
                 "base": {"ref": "main", "repo": {"full_name": REPO}}}
    out = m.targets(FOREIGN_REPO, pr="7", fetch=Fake({f"repos/{REPO}/pulls/7": (200, live_in_a)}))
    assert out == {"targets": [], "errors": ["TARGET_PR_UNREADABLE:404"]}
