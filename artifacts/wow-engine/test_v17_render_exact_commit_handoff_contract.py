"""Trust-root contract for exact-commit Render deployment request (issue #1752).

This suite checks the actual inline workflow Python AST rather than treating
an unrelated text occurrence of commitId as an authorization guarantee.
"""
import ast
import textwrap
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "wow-v17-render-production-deploy.yml"


def _script(text=None):
    contents = WORKFLOW.read_text(encoding="utf-8") if text is None else text
    release_step = contents.split(
        "      - name: Verify protected-main gates and deploy exact SHA", 1
    )[1].split("      - name: Reconcile exact Render receipt to durable deployment pointer", 1)[0]
    return textwrap.dedent(
        release_step.split("          python - <<'PY'\n", 1)[1]
        .split("\n          PY", 1)[0]
    )


def _post_calls(script):
    tree = ast.parse(script)
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "render_json"
        and node.args and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "POST"
    ]
    return calls


def _has_pinned_deploy(script):
    calls = _post_calls(script)
    if len(calls) != 1 or len(calls[0].args) != 3:
        return False
    payload = calls[0].args[2]
    if not isinstance(payload, ast.Dict):
        return False
    fields = {
        key.value: value
        for key, value in zip(payload.keys, payload.values)
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }
    return (
        set(fields) == {"clearCache", "commitId"}
        and isinstance(fields["commitId"], ast.Name)
        and fields["commitId"].id == "target_sha"
        and isinstance(fields["clearCache"], ast.Constant)
        and fields["clearCache"].value == "do_not_clear"
    )


def test_render_post_pins_exact_authorized_commit():
    assert _has_pinned_deploy(_script())


def test_render_post_rejects_branch_default_without_sha():
    modified = _script().replace(
        '{"clearCache":"do_not_clear", "commitId":target_sha}',
        '{"clearCache":"do_not_clear"}',
    )
    assert not _has_pinned_deploy(modified)


def test_release_main_is_rechecked_immediately_before_post():
    script = _script()
    section = script.split("if active is None:", 1)[1].split("created_deploy = created", 1)[0]
    assert 'github_json("/branches/main")' in section
    assert "latest_main_before_post != target_sha" in section
    assert section.index("latest_main_before_post != target_sha") < section.index(
        'created = render_json('
    )
    assert "STALE_SHA_NO_DEPLOY" in section
    assert "raise SystemExit(0)" in section


def test_release_no_unfenced_render_posts_remain():
    calls = _post_calls(_script())
    assert len(calls) == 1
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert 'timeout-minutes: 110' in workflow
    assert "deploy_deadline = time.time() + 5400" in workflow
    assert "check_deadline = time.time() + 900" in workflow
    assert "concurrency:" in workflow
    assert "cancel-in-progress: false" in workflow


def test_release_build_timeout_cannot_revert_to_pre_observed_build_duration():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "timeout-minutes: 25" not in workflow
    assert "deploy_deadline = time.time() + 900" not in workflow
    assert "RENDER_EXACT_SHA_DEPLOY_TIMEOUT" in workflow
    # A 65-minute Render build needs >65 minutes of bounded poll runway.
    assert "deploy_deadline = time.time() + 5400" in workflow


def test_both_live_render_receipt_paths_recheck_current_main():
    script = _script()
    assert "def require_current_main_for_live_receipt():" in script
    assert "current != target_sha" in script
    assert '"status": "DEPLOY_SUPERSEDED_BY_NEW_MAIN"' in script
    original_live = 'if str(deploy.get("status") or "") == "live":'
    polled_live = 'if status == "live":'
    assert original_live in script
    assert polled_live in script
    initial = script.split(original_live, 1)[1].split("emit_receipt({", 1)[0]
    polled = script.split(polled_live, 1)[1].split("emit_receipt({", 1)[0]
    assert "require_current_main_for_live_receipt()" in initial
    assert "require_current_main_for_live_receipt()" in polled


def test_stale_render_live_bypasses_no_current_main_recheck():
    script = _script()
    stale = script.replace(
        'if status == "live":\n            require_current_main_for_live_receipt()',
        'if status == "live":',
    )
    # Mutation removes one of the two call sites; it must be observable.
    assert stale != script
    assert script.count("require_current_main_for_live_receipt()") == 3
    assert stale.count("require_current_main_for_live_receipt()") == 2
    assert "DEPLOY_SUPERSEDED_BY_NEW_MAIN" in script



def _restricted_db_preflight_script():
    import textwrap

    workflow = WORKFLOW.read_text(encoding="utf-8")
    step = workflow.split(
        "      - name: Fail closed if restricted worker release-control database is not authorized",
        1,
    )[1].split("      - name: Verify protected-main gates and deploy exact SHA", 1)[0]
    return textwrap.dedent(
        step.split("          python - <<'PY'\n", 1)[1].split("\n          PY", 1)[0]
    )


def test_restricted_db_approval_runs_before_first_render_deploy():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    attest = workflow.index("      - name: Fail closed if restricted worker release-control database is not authorized")
    render = workflow.index("      - name: Verify protected-main gates and deploy exact SHA")
    assert attest < render
    assert "WOW_RELEASE_CONTROL_DSN" in workflow[attest:render]
    assert "WOW_RELEASE_CONTROL_PROJECT_REF" in workflow[attest:render]
    assert "RENDER_RELEASE_CONTROL_DB_PREFLIGHT_DENIED" in workflow[attest:render]
    assert "RENDER_RELEASE_CONTROL_DB_PREFLIGHT_PASS" in workflow[attest:render]
    assert "python -m pip install 'psycopg[binary]==3.2.10'" in workflow[attest:render]
    assert "/services/" not in workflow[attest:render]
    assert "POST" not in workflow[attest:render]


def test_restricted_db_preflight_matches_sql_declared_service():
    import ast

    script = _restricted_db_preflight_script()
    assert "wow_release_control.release_control_identity" in script
    assert "wow_release_controller" in script
    assert "srv-dapkbc0473hc73c1978g" in script
    assert "authorized is not True" in script
    ast.parse(script)


def test_restricted_db_preflight_denies_unapproved_identity_without_secrets(monkeypatch, capsys):
    import sys
    import types

    class Conn:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def cursor(self): return self
        def execute(self, *args): pass
        def fetchall(self):
            return [(
                "wow_release_controller", "iczfhsmjrrafhvcpmqhr",
                "production", "srv-dapkbc0473hc73c1978g",
                False, None, None,
            )]

    monkeypatch.setitem(sys.modules, "psycopg", types.SimpleNamespace(connect=lambda *args, **kwargs: Conn()))
    monkeypatch.setenv("WOW_RELEASE_CONTROL_PROJECT_REF", "iczfhsmjrrafhvcpmqhr")
    monkeypatch.setenv("WOW_RELEASE_CONTROL_DSN", "private://do-not-log-this-token")
    import pytest
    with pytest.raises(SystemExit) as raised:
        exec(compile(_restricted_db_preflight_script(), "<release-db-preflight>", "exec"), {"__name__": "__main__"})
    assert raised.value.code == 1
    output = capsys.readouterr().out
    assert "RENDER_RELEASE_CONTROL_DB_PREFLIGHT_DENIED:ValueError" in output
    assert "do-not-log-this-token" not in output


def test_restricted_db_preflight_accepts_only_independently_approved_role(monkeypatch, capsys):
    import sys
    import types

    class Conn:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def cursor(self): return self
        def execute(self, *args): pass
        def fetchall(self):
            return [(
                "wow_release_controller", "iczfhsmjrrafhvcpmqhr",
                "production", "srv-dapkbc0473hc73c1978g",
                True, "independent-dba", "2026-10-14T23:00:00Z",
            )]

    monkeypatch.setitem(sys.modules, "psycopg", types.SimpleNamespace(connect=lambda *args, **kwargs: Conn()))
    monkeypatch.setenv("WOW_RELEASE_CONTROL_PROJECT_REF", "iczfhsmjrrafhvcpmqhr")
    monkeypatch.setenv("WOW_RELEASE_CONTROL_DSN", "private://not-real")
    exec(compile(_restricted_db_preflight_script(), "<release-db-preflight>", "exec"), {"__name__": "__main__"})
    assert "RENDER_RELEASE_CONTROL_DB_PREFLIGHT_PASS" in capsys.readouterr().out



def test_production_release_never_exposes_secrets_to_branch_dispatch():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    trigger = workflow.split("permissions:", 1)[0]
    assert "workflow_run:" in trigger
    assert "branches: [main]" in trigger
    assert "workflow_dispatch:" not in trigger
    assert "repository_dispatch:" not in trigger
    assert workflow.count("environment: wow-release") == 2
    deploy = workflow.split("  deploy-production:", 1)[1].split("  deploy-background-worker:", 1)[0]
    assert "github.event_name == 'workflow_run'" in deploy
    assert "github.event.workflow_run.head_branch == 'main'" in deploy
    assert "github.event.workflow_run.conclusion == 'success'" in deploy
    # Environment protection is configured independently by GitHub admins;
    # this workflow must never be used as evidence of that setup by itself.
