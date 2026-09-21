from pathlib import Path


def _workflow_text() -> str:
    repo_root = Path(__file__).resolve().parents[2]
    return (repo_root / ".github/workflows/wow-v17-first-six-model-maintenance.yml").read_text()


def test_source_heavy_maintenance_retries_transient_http_and_application_blocks():
    text = _workflow_text()
    assert "def retryable_application_block(payload):" in text
    for marker in (
        "HTTP_ERROR",
        "HTTP_FAILED",
        "HTTP_429",
        "HTTP_502",
        "HTTP_503",
        "HTTP_504",
        "TRANSPORT_ERROR",
        "TRANSPORT_UNAVAILABLE",
    ):
        assert marker in text
    assert "payload = post_bounded(name, path, attempts=3, timeout=180)" in text


def test_source_heavy_retry_mints_fresh_oidc_token_and_keeps_auth_failures_hard():
    text = _workflow_text()
    loop_pos = text.index("for attempt in range(attempts):")
    token_pos = text.index("token = fresh_oidc_token()", loop_pos)
    assert token_pos > loop_pos
    assert "if exc.code in {401, 403}:" in text
    assert "raise RuntimeError" in text
    assert 'WOW_CAN_EXECUTE: "false"' in text
    assert 'WOW_DRY_RUN_ONLY: "true"' in text


def test_model_quality_and_missing_credentials_are_not_marked_retryable():
    text = _workflow_text()
    retry_section = text.split("def retryable_application_block(payload):", 1)[1].split("def post_bounded", 1)[0]
    assert "MODEL_UNAVAILABLE" not in retry_section
    assert "CREDENTIAL" not in retry_section
    assert "RESEARCH_SCREEN_FAILED" not in retry_section
    assert "SCHEMA" not in retry_section
