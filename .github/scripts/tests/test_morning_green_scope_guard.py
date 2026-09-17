from pathlib import Path
import importlib.util
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "morning_green_scope_guard.py"
spec = importlib.util.spec_from_file_location("morning_green_scope_guard", SCRIPT)
guard = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = guard
spec.loader.exec_module(guard)

def body(risk="R1", allowed=None, protected=None):
    allowed = allowed or ["src/fix.py"]
    protected = [] if protected is None else protected
    lines = [
        "Morning-Green-Autonomous: true",
        f"Morning-Green-Risk: {risk}",
        "",
        "```morning-green-scope",
        f"risk_class: {risk}",
        "allowed_files:",
    ]
    lines += [f"  - {p}" for p in allowed]
    if protected:
        lines.append("protected_files:")
        lines += [f"  - {p}" for p in protected]
    else:
        lines.append("protected_files: []")
    lines.append("```")
    return "\n".join(lines)

def rows(*paths):
    return [{"filename": p, "status": "modified", "patch": "@@\n-old\n+new"} for p in paths]

def verify(*, b=None, risk="R1", changed=None, patterns=None, expected="abc", actual="abc"):
    return guard.verify_scope(
        body=b or body(risk=risk),
        marker_risk=risk,
        expected_head=expected,
        actual_head=actual,
        files=changed or rows("src/fix.py"),
        protected_patterns=patterns or [".github/workflows/wow-v17-morning-green-continuation.yml"],
    )

def test_pass_r0_exact_scope():
    assert verify(b=body("R0"), risk="R0")["ok"]

def test_pass_r1_exact_scope():
    assert verify()["ok"]

def test_pass_r2_restorative_exact_scope():
    assert verify(b=body("R2-restorative"), risk="R2-restorative")["ok"]

def test_missing_scope_block_fails():
    with pytest.raises(guard.ScopeGuardError, match="exactly one"):
        verify(b="Morning-Green-Risk: R1")

def test_duplicate_scope_block_fails():
    block = body()
    with pytest.raises(guard.ScopeGuardError, match="found 2"):
        verify(b=block + "\n" + block[block.index("```morning-green-scope"):])

def test_malformed_scope_unknown_key_fails():
    b = body().replace("protected_files: []", "protected_files: []\nextra: nope")
    with pytest.raises(guard.ScopeGuardError, match="unsupported key"):
        verify(b=b)

def test_missing_required_scope_key_fails():
    b = body().replace("protected_files: []", "")
    with pytest.raises(guard.ScopeGuardError, match="missing required scope keys"):
        verify(b=b)

def test_glob_in_allowed_files_fails():
    b = body(allowed=["src/*.py"])
    with pytest.raises(guard.ScopeGuardError, match="glob syntax"):
        verify(b=b)

def test_undeclared_changed_file_fails():
    with pytest.raises(guard.ScopeGuardError, match="outside allowed_files"):
        verify(changed=rows("src/fix.py", "src/extra.py"))

def test_centrally_protected_changed_file_fails():
    p = ".github/workflows/wow-v17-morning-green-continuation.yml"
    with pytest.raises(guard.ScopeGuardError, match="centrally protected"):
        verify(b=body(allowed=[p]), changed=rows(p), patterns=[p])

def test_protected_file_explicitly_listed_in_allowed_fails():
    p = "src/fix.py"
    with pytest.raises(guard.ScopeGuardError, match="also declares protected"):
        verify(b=body(allowed=[p], protected=[p]))

def test_risk_mismatch_fails():
    b = body("R0")
    with pytest.raises(guard.ScopeGuardError, match="disagrees"):
        verify(b=b, risk="R1")

def test_head_sha_drift_fails():
    with pytest.raises(guard.ScopeGuardError, match="head changed"):
        verify(expected="old", actual="new")

def test_safe_workflow_change_passes_when_allowed_and_not_protected():
    p = ".github/workflows/safe-read-only.yml"
    changed = [{"filename": p, "status": "modified", "patch": "@@\n-name: old\n+name: new"}]
    assert verify(b=body(allowed=[p]), changed=changed, patterns=["render.yaml"])["ok"]

def test_workflow_without_patch_fails_closed():
    p = ".github/workflows/safe-read-only.yml"
    changed = [{"filename": p, "status": "modified"}]
    with pytest.raises(guard.ScopeGuardError, match="no inspectable patch"):
        verify(b=body(allowed=[p]), changed=changed, patterns=["render.yaml"])

def test_workflow_permission_escalation_fails():
    p = ".github/workflows/new-bot.yml"
    changed = [{
        "filename": p,
        "status": "added",
        "patch": "@@\n+permissions:\n+  contents: write\n+steps:\n+  - run: echo hi",
    }]
    with pytest.raises(guard.ScopeGuardError, match="privileged control token"):
        verify(b=body(allowed=[p]), changed=changed, patterns=["render.yaml"])

def test_workflow_merge_command_escalation_fails():
    p = ".github/workflows/new-bot.yml"
    changed = [{
        "filename": p,
        "status": "added",
        "patch": "@@\n+steps:\n+  - run: gh pr merge 123 --merge",
    }]
    with pytest.raises(guard.ScopeGuardError, match="privileged control token"):
        verify(b=body(allowed=[p]), changed=changed, patterns=["render.yaml"])
