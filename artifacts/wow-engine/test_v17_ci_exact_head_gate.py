"""Exact-head acceptance regression tests for the protected CI aggregate.

The gate runs at the trust-root workflow level, without checking out or
executing untrusted PR code. These tests execute the exact embedded script
against deterministic mocked GitHub API responses.
"""
import io
import json
import textwrap
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "wow-engine-verify.yml"
REPO = "gregoryharper84-ship-it/WOW-Dashboard"
HEAD = "a" * 40
OTHER_HEAD = "b" * 40
MERGE_REF = "c" * 40
BASE_SHA = "d" * 40


def _gate_script():
    text = WORKFLOW.read_text()
    assert "pull-requests: read" in text
    assert "Refuse stale or unverifiable exact-head verification" in text
    return textwrap.dedent(
        text.split("python3 - <<'PY'\n", 1)[1].split("\n          PY", 1)[0]
    )


def _execute(monkeypatch, tmp_path, *, event="pull_request", current=HEAD,
             payload_head=HEAD, payload_base=BASE_SHA, current_base=BASE_SHA,
             main_sha=BASE_SHA, current_merge=MERGE_REF,
             token="test-token", fail_api=False,
             wrong_repo=False, ref=None):
    payload = {"repository": {"full_name": "untrusted/other" if wrong_repo else REPO}}
    if event == "pull_request":
        payload["pull_request"] = {
            "number": 73,
            "head": {"sha": payload_head, "repo": {"full_name": REPO}},
            "base": {"sha": payload_base, "ref": "main"},
        }
        current_json = {
            "state": "open", "base": {"ref": "main", "sha": current_base},
            "head": {"sha": current, "repo": {"full_name": REPO}},
            "merge_commit_sha": current_merge,
        }
        ref = ref or "refs/pull/73/merge"
    else:
        current_json = {"commit": {"sha": current}}
        ref = ref or "refs/heads/main"
    event_file = tmp_path / "github-event.json"
    event_file.write_text(json.dumps(payload))
    for key, value in {
        "WOW_EVENT_NAME": event,
        "GITHUB_EVENT_PATH": str(event_file),
        "WOW_REPOSITORY": REPO,
        "WOW_REF": ref,
        "WOW_SHA": MERGE_REF if event == "pull_request" else HEAD,
        "GH_TOKEN": token,
    }.items():
        monkeypatch.setenv(key, value)

    def urlopen(request, timeout):
        assert timeout == 8
        assert request.full_url.startswith("https://api.github.com/repos/" + REPO + "/")
        assert request.get_header("Authorization") == "Bearer test-token"
        if fail_api:
            raise ConnectionError("private credential details must not leak")
        if event == "pull_request" and request.full_url.endswith("/branches/main"):
            return io.BytesIO(json.dumps({"commit": {"sha": main_sha}}).encode())
        return io.BytesIO(json.dumps(current_json).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    exec(compile(_gate_script(), "<embedded-ci-head-gate>", "exec"), {"__name__": "__main__"})


def test_exact_head_gate_accepts_current_pr(monkeypatch, tmp_path, capsys):
    _execute(monkeypatch, tmp_path)
    assert "EXACT_HEAD_GATE_PASS" in capsys.readouterr().out


def test_exact_head_gate_rejects_superseded_pending_pr(monkeypatch, tmp_path, capsys):
    with pytest.raises(SystemExit) as error:
        _execute(monkeypatch, tmp_path, current=OTHER_HEAD)
    assert error.value.code == 1
    assert "EXACT_HEAD_GATE_HEAD_STALE_OR_UNKNOWN" in capsys.readouterr().out


def test_exact_head_gate_rejects_wrong_event_repository(monkeypatch, tmp_path):
    with pytest.raises(SystemExit):
        _execute(monkeypatch, tmp_path, wrong_repo=True)


def test_exact_head_gate_rejects_github_api_failure_without_secret_leak(monkeypatch, tmp_path, capsys):
    with pytest.raises(SystemExit):
        _execute(monkeypatch, tmp_path, fail_api=True)
    output = capsys.readouterr().out
    assert "EXACT_HEAD_GATE_VERIFICATION_UNAVAILABLE_ConnectionError" in output
    assert "private credential" not in output
    assert "test-token" not in output


def test_exact_head_gate_rejects_missing_token(monkeypatch, tmp_path):
    with pytest.raises(SystemExit):
        _execute(monkeypatch, tmp_path, token="")


def test_exact_head_gate_rejects_pr_wrong_ref(monkeypatch, tmp_path):
    with pytest.raises(SystemExit):
        _execute(monkeypatch, tmp_path, ref="refs/heads/main")


def test_exact_head_gate_accepts_current_protected_main_push(monkeypatch, tmp_path):
    _execute(monkeypatch, tmp_path, event="push", current=HEAD)


def test_exact_head_gate_rejects_stale_protected_main_push(monkeypatch, tmp_path):
    with pytest.raises(SystemExit):
        _execute(monkeypatch, tmp_path, event="push", current=OTHER_HEAD)


def test_exact_head_gate_rejects_non_main_dispatch(monkeypatch, tmp_path):
    with pytest.raises(SystemExit):
        _execute(monkeypatch, tmp_path, event="workflow_dispatch", ref="refs/heads/feature")


def test_exact_head_gate_cannot_pass_unknown_event(monkeypatch, tmp_path):
    with pytest.raises(SystemExit):
        _execute(monkeypatch, tmp_path, event="workflow_run")


def test_exact_head_gate_reads_builtin_runner_event_path():
    workflow = WORKFLOW.read_text()
    assert "WOW_EVENT_PATH: ${{ github.event_path }}" not in workflow
    assert 'os.environ.get("GITHUB_EVENT_PATH", "")' in workflow


def test_exact_head_gate_rejects_old_main_with_unchanged_pr_head(monkeypatch, tmp_path, capsys):
    """Copilot's reproduced stale-base counterexample: head unchanged, main moves."""
    with pytest.raises(SystemExit) as failure:
        _execute(monkeypatch, tmp_path, current=HEAD, current_base=BASE_SHA,
                 main_sha=OTHER_HEAD)
    assert failure.value.code == 1
    assert "EXACT_HEAD_GATE_PR_BASE_STALE_OR_UNKNOWN" in capsys.readouterr().out


def test_exact_head_gate_rejects_old_event_base_after_main_move(monkeypatch, tmp_path):
    with pytest.raises(SystemExit):
        _execute(monkeypatch, tmp_path, payload_base=OTHER_HEAD)


def test_exact_head_gate_rejects_current_pr_base_not_equal_to_main(monkeypatch, tmp_path):
    with pytest.raises(SystemExit):
        _execute(monkeypatch, tmp_path, current_base=OTHER_HEAD)


def test_exact_head_gate_rejects_stale_synthetic_merge_commit(monkeypatch, tmp_path, capsys):
    with pytest.raises(SystemExit) as failure:
        _execute(monkeypatch, tmp_path, current_merge=OTHER_HEAD)
    assert failure.value.code == 1
    assert "EXACT_HEAD_GATE_PR_MERGE_REF_STALE_OR_UNKNOWN" in capsys.readouterr().out


def test_exact_head_gate_rejects_invalid_event_base_identity(monkeypatch, tmp_path):
    with pytest.raises(SystemExit):
        _execute(monkeypatch, tmp_path, payload_base="invalid")


def test_exact_head_gate_checks_current_pr_merge_against_actual_ci_sha():
    source = WORKFLOW.read_text()
    assert 'merge_sha = str(current["merge_commit_sha"])' in source
    assert 'merge_sha != sha' in source
    assert 'current_main_sha = str(get_json("branches/main")["commit"]["sha"])' in source
