from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "wow-v17-mcp-remote-acceptance.yml"


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_remote_acceptance_triggers_for_every_security_critical_mcp_runtime_file():
    text = _text()
    required_paths = {
        ".github/workflows/wow-v17-mcp-remote-acceptance.yml",
        "artifacts/mcp-server/src/v17_remote_acceptance.js",
        "artifacts/mcp-server/src/v17_backend.js",
        "artifacts/mcp-server/src/v17_contract.js",
        "artifacts/mcp-server/src/v17_server.js",
        "artifacts/mcp-server/src/v17_http.js",
        "artifacts/mcp-server/src/v17_oauth.js",
    }
    for path in required_paths:
        assert f'- "{path}"' in text


def test_remote_acceptance_remains_main_only_and_fail_closed():
    text = _text()
    assert "branches: [main]" in text
    assert "if: github.ref == 'refs/heads/main'" in text
    assert "cancel-in-progress: false" in text
    assert "WOW_MCP_ACCEPTANCE_SERVICE_ID: srv-dapfrkrbc2fs73fjjsig" in text
    assert "WOW_MCP_SHARED_TOKEN" in text
    assert "::add-mask::" in text
    assert "acceptance:remote:v17" in text
    assert "can_execute=false" in text
    assert "terminal_authority=V17_TERMINAL_REDUCER" in text


def test_remote_acceptance_never_inlines_mcp_or_backend_credentials():
    text = _text()
    assert "WOW_MCP_AUTH_TOKEN:" not in text
    assert "WOW_ACTION_API_KEY:" not in text
    assert "server-side-secret-only" not in text
