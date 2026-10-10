"""Owner-signed release path: real SSH signature round-trips plus job isolation.

The verifier must accept only a valid, unexpired signature from the
``wow-owner`` principal over the exact repo/PR/head/incident/expiry, and the
workflow must keep the agent (QA) away from write tokens and the merge job
away from the agent.
"""
import base64
import json
import os
import shutil
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
VERIFY = ROOT / ".github/release/verify_owner_release.sh"
SIGN = ROOT / ".github/release/sign_owner_release.sh"
WORKFLOW = ROOT / ".github/workflows/wow-v17-owner-signed-release.yml"
REPO = "gregoryharper84-ship-it/WOW-Dashboard"
HEAD = "a" * 40

# GitHub-hosted runners ship OpenSSH; absence is a CI environment gap, so fail loudly.
assert shutil.which("ssh-keygen"), "ssh-keygen is required for owner-signature tests"


def _keypair(tmp_path, name):
    key = tmp_path / name
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    return key


def _signers(tmp_path, key, principal="wow-owner"):
    pub = (key.with_suffix(".pub")).read_text().strip()
    path = tmp_path / f"signers_{key.name}"
    path.write_text(f'# comment\n{principal} namespaces="wow-release" {pub}\n')
    return path


def _expires(hours=2):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sign(tmp_path, key, *, repo=REPO, pr="1568", head=HEAD, incident="1562", expires=None, namespace="wow-release"):
    expires = expires or _expires()
    msg = tmp_path / "m"
    msg.write_text(f"WOW_OWNER_RELEASE_V1\nrepo={repo}\npr={pr}\nhead={head}\nincident={incident}\nexpires={expires}\n")
    sig = tmp_path / "m.sig"
    sig.unlink(missing_ok=True)
    subprocess.run(["ssh-keygen", "-q", "-Y", "sign", "-f", str(key), "-n", namespace, str(msg)], check=True,
                   capture_output=True)
    return expires, base64.b64encode(sig.read_bytes()).decode()


def _verify(signers, *, repo=REPO, pr="1568", head=HEAD, incident="1562", expires, sig):
    return subprocess.run(["bash", str(VERIFY), str(signers), repo, pr, head, incident, expires, sig],
                          capture_output=True, text=True)


def test_valid_owner_signature_is_accepted(tmp_path):
    key = _keypair(tmp_path, "owner")
    expires, sig = _sign(tmp_path, key)
    out = _verify(_signers(tmp_path, key), expires=expires, sig=sig)
    assert out.returncode == 0, out.stderr
    assert "OWNER_RELEASE_SIGNATURE_VALID" in out.stdout


@pytest.mark.parametrize("field,value", [("pr", "1569"), ("head", "b" * 40), ("incident", "1563"),
                                         ("repo", "someone/else")])
def test_signature_is_bound_to_every_field(tmp_path, field, value):
    key = _keypair(tmp_path, "owner")
    expires, sig = _sign(tmp_path, key)
    out = _verify(_signers(tmp_path, key), expires=expires, sig=sig, **{field: value})
    assert out.returncode != 0 and "SIGNATURE_INVALID" in out.stderr


def test_agent_key_is_rejected(tmp_path):
    owner, agent = _keypair(tmp_path, "owner"), _keypair(tmp_path, "agent")
    expires, sig = _sign(tmp_path, agent)
    out = _verify(_signers(tmp_path, owner), expires=expires, sig=sig)
    assert out.returncode != 0 and "SIGNATURE_INVALID" in out.stderr


def test_wrong_principal_and_namespace_rejected(tmp_path):
    key = _keypair(tmp_path, "owner")
    expires, sig = _sign(tmp_path, key)
    out = _verify(_signers(tmp_path, key, principal="someone"), expires=expires, sig=sig)
    assert "OWNER_SIGNER_NOT_BOOTSTRAPPED" in out.stderr
    expires, sig = _sign(tmp_path, key, namespace="git")
    out = _verify(_signers(tmp_path, key), expires=expires, sig=sig)
    assert out.returncode != 0 and "SIGNATURE_INVALID" in out.stderr


def test_expired_and_too_long_lived_signatures_rejected(tmp_path):
    key = _keypair(tmp_path, "owner")
    signers = _signers(tmp_path, key)
    expires, sig = _sign(tmp_path, key, expires=_expires(-1))
    assert "EXPIRED" in _verify(signers, expires=expires, sig=sig).stderr
    expires, sig = _sign(tmp_path, key, expires=_expires(48))
    assert "EXPIRY_TOO_FAR" in _verify(signers, expires=expires, sig=sig).stderr


def test_unbootstrapped_repository_signers_fail_closed(tmp_path):
    key = _keypair(tmp_path, "owner")
    expires, sig = _sign(tmp_path, key)
    out = _verify(ROOT / ".github/release/owner_allowed_signers", expires=expires, sig=sig)
    assert out.returncode != 0 and "OWNER_SIGNER_NOT_BOOTSTRAPPED" in out.stderr


@pytest.mark.parametrize("head,sig", [("A" * 40, None), ("a" * 39, None), (HEAD, "not-base64!"),
                                      (HEAD, base64.b64encode(b"x" * 200).decode())])
def test_malformed_inputs_rejected(tmp_path, head, sig):
    key = _keypair(tmp_path, "owner")
    expires, good = _sign(tmp_path, key)
    out = _verify(_signers(tmp_path, key), head=head, expires=expires, sig=sig or good)
    assert out.returncode != 0 and "OWNER_RELEASE_SIGNATURE_DENIED" in out.stderr


def test_sign_helper_output_verifies(tmp_path):
    key = _keypair(tmp_path, "owner")
    out = subprocess.run(["bash", str(SIGN), "1568", HEAD, "1562", str(key), "3"],
                         capture_output=True, text=True, check=True).stdout
    fields = dict(part.split("=", 1) for part in out.split() if part.startswith(("expires_utc=", "owner_signature=")))
    res = _verify(_signers(tmp_path, key), expires=fields["expires_utc"], sig=fields["owner_signature"])
    assert res.returncode == 0, res.stderr


def _wf():
    return yaml.safe_load(WORKFLOW.read_text())


def test_only_manual_dispatch_from_main():
    wf = _wf()
    triggers = wf.get("on") or wf.get(True)
    assert set(triggers) == {"workflow_dispatch"}
    assert wf["permissions"] == {}
    assert wf["jobs"]["verify"]["if"] == "github.ref == 'refs/heads/main'"


def test_job_isolation():
    jobs = _wf()["jobs"]
    assert jobs["qa"]["permissions"] == {"contents": "read"}
    assert "environment" not in jobs["qa"] and "env" not in jobs["qa"]
    assert set(jobs["verify"]["permissions"].values()) == {"read"}
    merge = jobs["merge"]
    assert merge["needs"] == ["verify", "qa"] and merge["environment"] == "wow-release"
    merge_text = yaml.safe_dump(merge)
    assert "wow-claude-agent" not in merge_text and "secrets." not in merge_text
    assert "verify_owner_release.sh" in merge_text  # re-verified where the write token lives
    for job in jobs.values():
        for step in job["steps"]:
            if step.get("uses", "").startswith("actions/checkout"):
                assert step["with"]["ref"] == "main" and step["with"]["persist-credentials"] is False


def test_trust_root_and_class_c_denied_and_unsafe_bridge_removed():
    text = WORKFLOW.read_text()
    assert ".github/*|.agents/*) deny" in text
    assert "CLASS_C_DENIED" in text
    assert "issue_comment" not in text and "WOW_OWNER_RELEASE_APPROVAL" not in text
    assert not (ROOT / ".github/workflows/wow-v17-temporary-owner-release-bridge.yml").exists()



def test_signed_release_external_actions_are_immutable_pins():
    # These SHAs are resolved from the official action repositories. A tag
    # such as @v4/@v6 is mutable and cannot be trusted on a release-authority
    # workflow that handles owner signatures, model QA and merge credentials.
    expected = {
        "actions/checkout": "11bd71901bbe5b1630ceea73d27597364c9af683",
        "actions/upload-artifact": "ea165f8d65b6e75b540449e92b4886f43607fa02",
        "actions/download-artifact": "d3f86a106a0bac45b974a628896c90dbdf5c8093",
    }
    found = set()
    for job in _wf()["jobs"].values():
        for step in job["steps"]:
            action = step.get("uses", "")
            if not action.startswith("actions/"):
                continue
            assert re.fullmatch(r"actions/[a-z0-9-]+@[0-9a-f]{40}", action), action
            name, sha = action.split("@", 1)
            assert name in expected and sha == expected[name], action
            found.add(name)
    assert found == set(expected)



def _run_signed_release_qa_enforcement(payload):
    assert shutil.which("jq"), "jq required by signed-release QA enforcement"
    qa_job = _wf()["jobs"]["qa"]
    step = next(s for s in qa_job["steps"] if s.get("name") == "Enforce QA result")
    env = dict(os.environ, EXPECTED_HEAD_SHA=HEAD, QA_RESULT=json.dumps(payload))
    return subprocess.run(
        ["bash", "-c", step["run"]],
        env=env, text=True, capture_output=True,
    )


def _qa_payload(**updates):
    base = {
        "decision": "PASS",
        "change_class": "B",
        "exact_head_sha": HEAD,
        "findings": [],
        "reason": "Exact-head evidence verified independently",
    }
    base.update(updates)
    return base


@pytest.mark.parametrize("class_code", ["A", "B"])
def test_signed_release_qa_accepts_only_complete_class_a_or_b(class_code):
    out = _run_signed_release_qa_enforcement(_qa_payload(change_class=class_code))
    assert out.returncode == 0, out.stderr


@pytest.mark.parametrize(
    "payload",
    [
        _qa_payload(change_class="C"),
        _qa_payload(change_class=None),
        _qa_payload(change_class="X"),
        _qa_payload(change_class=""),
        _qa_payload(decision="HOLD"),
        _qa_payload(decision=None),
        _qa_payload(exact_head_sha="b"*40),
        _qa_payload(findings=None),
        _qa_payload(findings=[{"malformed": True}]),
        _qa_payload(reason=None),
        _qa_payload(reason=""),
    ],
)
def test_signed_release_qa_missing_invalid_or_class_c_fails_closed(payload):
    out = _run_signed_release_qa_enforcement(payload)
    assert out.returncode != 0, payload
    assert "OWNER_SIGNED_RELEASE_" in out.stderr


def test_signed_release_qa_omitted_class_or_reason_does_not_pass():
    for missing in ("decision", "change_class", "exact_head_sha", "findings", "reason"):
        payload = _qa_payload()
        payload.pop(missing)
        out = _run_signed_release_qa_enforcement(payload)
        assert out.returncode != 0, missing



def _owner_signed_pr_file_filter(rows):
    # Run the exact jq expression used by the workflow over a fake GitHub
    # paginated /pulls/:number/files response. This is real parser behavior,
    # not merely a string check on source code.
    source = WORKFLOW.read_text()
    match = re.search(r"--jq '([^']+)' > evidence/files\.txt", source)
    assert match, "signed owner release must retain an auditable file filter"
    assert shutil.which("jq"), "jq is required for file-scope parsing tests"
    outcome = subprocess.run(
        ["jq", "-r", match.group(1)],
        input=json.dumps(rows), text=True, capture_output=True,
    )
    assert outcome.returncode == 0, outcome.stderr
    return outcome.stdout.splitlines()


@pytest.mark.parametrize("origin", [
    ".github/workflows/wow-verify.yml",
    ".github/release/owner_allowed_signers",
    ".agents/skills/wow-engineering-qa-verification-agent/SKILL.md",
])
def test_signed_release_file_filter_exposes_protected_rename_origin(origin):
    paths = _owner_signed_pr_file_filter([{
        "filename": "docs/innocent-filename.md",
        "previous_filename": origin,
        "status": "renamed",
    }])
    assert paths == ["docs/innocent-filename.md", origin]
    workflow = WORKFLOW.read_text()
    assert '.github/*|.agents/*) deny "TRUST_ROOT_CHANGE:$path"' in workflow


@pytest.mark.parametrize("origin", [None, "", 17, False])
def test_signed_release_file_filter_rejects_missing_or_corrupt_rename_origin(origin):
    row = {"filename": "docs/ordinary.md", "status": "renamed"}
    if origin is not None:
        row["previous_filename"] = origin
    assert "__MISSING_RENAME_ORIGIN__" in _owner_signed_pr_file_filter([row])
    assert 'deny RENAME_ORIGIN_MISSING' in WORKFLOW.read_text()


def test_signed_release_file_filter_rejects_invalid_path_and_empty_evidence():
    assert _owner_signed_pr_file_filter([{"status": "modified"}]) == ["__INVALID_FILE_PATH__"]
    assert _owner_signed_pr_file_filter([{"status": "added", "filename": ""}]) == ["__INVALID_FILE_PATH__"]
    assert _owner_signed_pr_file_filter([]) == []
    source = WORKFLOW.read_text()
    assert 'deny PR_FILES_MISSING' in source
    assert 'deny PR_FILE_EVIDENCE_TRUNCATED' in source
    assert 'deny FILE_PATH_INVALID' in source
