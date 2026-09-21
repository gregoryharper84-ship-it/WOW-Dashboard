from pathlib import Path


def _workflow_text() -> str:
    repo_root = Path(__file__).resolve().parents[2]
    return (repo_root / ".github/workflows/wow-v17-nhl-model-maintenance.yml").read_text()


def test_nhl_maintenance_mints_fresh_oidc_token_inside_retry_loop():
    text = _workflow_text()
    loop_pos = text.index("for attempt in range(20):")
    mint_call_pos = text.index("oidc_token = mint_oidc_token()")

    assert "def mint_oidc_token():" in text
    assert mint_call_pos > loop_pos
    assert text.count("oidc_token = mint_oidc_token()") == 1


def test_nhl_maintenance_keeps_fresh_token_401_as_hard_failure():
    text = _workflow_text()

    assert "if exc.code not in {404, 502, 503, 504}:" in text
    assert "401" not in text.split("if exc.code not in {404, 502, 503, 504}:", 1)[0].split("except urllib.error.HTTPError as exc:", 1)[-1]
    assert 'WOW_CAN_EXECUTE: "false"' in text
    assert 'WOW_DRY_RUN_ONLY: "true"' in text
