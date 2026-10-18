"""Regression: trusted-main isolated proof cannot silently approve production."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import tarfile
import xml.etree.ElementTree as ET

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/wow-v17-controlled-pr-sandbox.yml"
MODULE = ROOT / "artifacts/wow-engine/v17/controlled_pr_proof_contract.py"
spec = spec_from_file_location("controlled_pr_proof_contract", MODULE)
mod = module_from_spec(spec)
spec.loader.exec_module(mod)
SHA = "a" * 40
REPO = "gregoryharper84-ship-it/WOW-Dashboard"

def evidence(tmp_path, sha=SHA, count=112, failures=0, missing_hash=False):
    path = tmp_path / "release-evidence"
    path.mkdir()
    (path / "source-identity.txt").write_text(
        f"repository={REPO}\nsource_sha={sha}\nrun_id=1234\n"
        "provenance_kind=SANDBOX_NON_RELEASE\n")
    hashed = sorted(mod.HASHED_PATHS)
    (path / "source-hashes.txt").write_text("".join(
        f"{'0'*64}  {p}\n" for p in hashed
        if not missing_hash or p != hashed[0]))
    suite = ET.Element("testsuite", tests=str(count), errors="0",
                       failures=str(failures), skipped="0")
    ET.ElementTree(suite).write(path / "junit.xml", encoding="utf-8")
    (path / "pytest.log").write_text(f"{count} passed in 1.0s\n")
    with tarfile.open(tmp_path / "wow-v17-controlled-pr-sandbox-evidence.tar.gz", "w:gz") as a:
        for p in path.iterdir():
            a.add(p, arcname=p.name)
    return path

def test_complete_exact_head_evidence_is_non_release(tmp_path):
    proof = mod.verify_evidence(evidence(tmp_path), SHA, "1234", REPO)
    assert proof["status"] == "SANDBOX_PROOF_VERIFIED_NON_RELEASE"
    assert proof["production_authorized"] is False
    assert proof["can_execute"] is False
    assert proof["test_count"] == 112

@pytest.mark.parametrize("change,reason", [
    ("wrong_sha", "SOURCE_EXACT_HEAD_MISMATCH_HOLD"),
    ("too_few", "EXACT_HEAD_TESTS_FAILED_INCOMPLETE_OR_SKIPPED_HOLD"),
    ("failed", "EXACT_HEAD_TESTS_FAILED_INCOMPLETE_OR_SKIPPED_HOLD"),
    ("missing_hash", "CRITICAL_SOURCE_HASHES_MISSING_HOLD"),
    ("forged_tar", "ATTESTATION_SUBJECT_CONTENT_MISMATCH_HOLD"),
    ("duplicate_identity", "SOURCE_IDENTITY_FORMAT_OR_DUPLICATE_HOLD"),
])
def test_forged_stale_and_partial_evidence_rejected(tmp_path, change, reason):
    p = evidence(tmp_path, sha="b"*40 if change=="wrong_sha" else SHA,
                 count=100 if change=="too_few" else 112,
                 failures=1 if change=="failed" else 0,
                 missing_hash=change=="missing_hash")
    if change == "forged_tar":
        (p / "pytest.log").write_text("fake green\n")
    if change == "duplicate_identity":
        with (p / "source-identity.txt").open("a") as f:
            f.write(f"source_sha={SHA}\n")
    with pytest.raises(mod.EvidenceHold, match=reason):
        mod.verify_evidence(p, SHA, "1234", REPO)

def test_wrong_repo_and_run_rejected(tmp_path):
    p = evidence(tmp_path)
    for repo, run, reason in [
        ("other/repo", "1234", "REPOSITORY_IDENTITY_MISMATCH_HOLD"),
        (REPO, "bad", "RUN_ID_INVALID_HOLD"),
        (REPO, "9999", "SOURCE_EXACT_HEAD_MISMATCH_HOLD"),
    ]:
        with pytest.raises(mod.EvidenceHold, match=reason):
            mod.verify_evidence(p, SHA, run, repo)

def test_main_only_manual_workflow_is_not_release_shortcut():
    text = WORKFLOW.read_text()
    parsed = yaml.safe_load(text)
    triggers = parsed.get("on", parsed.get(True, {}))
    assert list(triggers) == ["workflow_dispatch"]
    assert "pull_request_target" not in text
    assert "issue_comment" not in text
    assert "repository_dispatch" not in text
    assert "github.ref == 'refs/heads/main'" in text
    for marker in ["EXACT_HEAD_CHANGED_HOLD", "CANDIDATE_ANCESTRY_HOLD",
                   "SANDBOX_NON_RELEASE", "persist-credentials: false",
                   "postgres:17.6", "RELEASE_JOURNAL_PREFLIGHT_HOLD_NO_DEPLOY",
                   "can_execute=false",
                   "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true"]:
        assert marker in text
    for banned in ("RENDER_API_KEY", "SUPABASE_SERVICE_ROLE_KEY", "gh pr merge",
                   "gh api -X POST", "git push", "POST /deploys"):
        assert banned not in text
    jobs = parsed["jobs"]
    assert set(jobs) == {"bind-exact-pr", "sandbox-proof", "sign-only-evidence"}
    assert jobs["sandbox-proof"]["permissions"] == {"contents": "read"}
    signer = jobs["sign-only-evidence"]
    assert signer["needs"] == ["bind-exact-pr", "sandbox-proof"]
    assert signer["permissions"]["id-token"] == "write"
    assert signer["permissions"]["attestations"] == "write"
    assert "--signer-workflow" in text
    assert "--deny-self-hosted-runners" in text
