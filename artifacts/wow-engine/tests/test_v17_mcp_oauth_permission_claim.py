from pathlib import Path


SQL_PATH = (
    Path(__file__).resolve().parents[1]
    / "v17"
    / "sql"
    / "20260922_v17_mcp_oauth_permission_claim.sql"
)


def _sql() -> str:
    return SQL_PATH.read_text(encoding="utf-8").lower()


def test_oauth_permission_claim_is_server_controlled_and_fail_closed():
    sql = _sql()
    assert "wow_private.wow_mcp_oauth_client_permissions" in sql
    assert "enabled boolean not null default false" in sql
    assert "coalesce(allowed_permissions, array[]::text[])" in sql
    assert "app_metadata" in sql
    assert "wow_permissions" in sql
    assert "security invoker" in sql
    assert "security definer" not in sql


def test_oauth_permission_table_is_not_exposed_to_user_roles():
    sql = _sql()
    assert "revoke all on schema wow_private from public" in sql
    assert "revoke all on schema wow_private from anon" in sql
    assert "revoke all on schema wow_private from authenticated" in sql
    assert "revoke all on table wow_private.wow_mcp_oauth_client_permissions from public" in sql
    assert "revoke all on table wow_private.wow_mcp_oauth_client_permissions from anon" in sql
    assert "revoke all on table wow_private.wow_mcp_oauth_client_permissions from authenticated" in sql
    assert "enable row level security" in sql


def test_oauth_permission_allowlist_matches_governed_v17_operations():
    sql = _sql()
    expected = {
        "wow.runtime.read",
        "wow.governance.read",
        "wow.evidence.read",
        "wow.predictions.read",
        "wow.predictions.score",
        "wow.daily.run",
        "wow.recommendations.write",
        "wow.settlements.write",
    }
    for permission in expected:
        assert f"'{permission}'" in sql


def test_approved_oauth_client_is_bound_to_exact_https_resource_audience():
    sql = _sql()
    assert "resource_audience text" in sql
    assert "chk_wow_mcp_oauth_resource_audience_when_enabled" in sql
    assert "resource_audience ~ '^https://[^[:space:]]+$'" in sql
    assert "jsonb_set(claims, '{aud}', to_jsonb(protected_resource_audience), true)" in sql
    assert "if protected_resource_audience is not null then" in sql
    # The hook must not stamp a generic Supabase audience onto approved MCP tokens.
    assert "to_jsonb('authenticated'" not in sql


def test_oauth_hook_does_not_create_execution_authority():
    sql = _sql()
    forbidden_permissions = {
        "wow.wagers.execute",
        "wow.bets.place",
        "wow.orders.submit",
        "wow.orders.cancel",
    }
    for permission in forbidden_permissions:
        assert permission not in sql
