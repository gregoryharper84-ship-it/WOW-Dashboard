from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "llp-action-gateway-production-canary.yml"
GATEWAY = REPO_ROOT / "artifacts" / "wow-engine" / "v17" / "supabase" / "functions" / "wow-llp-action-gateway" / "index.ts"


def test_gateway_propagates_one_nonsecret_request_id_end_to_end():
    source = GATEWAY.read_text(encoding="utf-8")
    assert 'req.headers.get("x-wow-request-id")' in source
    assert 'req.headers.get("x-request-id")' in source
    assert 'headers.set("x-request-id", requestId)' in source
    assert 'headers.set("x-wow-request-id", requestId)' in source
    assert '"x-request-id": requestId' in source
    assert '"x-wow-request-id": requestId' in source
    for stage in ("INGRESS", "UPSTREAM_DISPATCH", "UPSTREAM_RESPONSE", "UPSTREAM_FAILURE"):
        assert stage in source
    assert "authorization" not in source[source.index('function gatewayLog('):source.index('function jsonResponse(')]


def test_canary_proves_public_health_before_private_credential_gate():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    health = workflow.index("- name: Verify unauthenticated gateway health path")
    credential = workflow.index("- name: Fail closed if Action credential is unavailable")
    host = workflow.index("- name: Verify authenticated governed host contract through gateway")
    score = workflow.index("- name: Verify score-team-event ingress reaches governed backend")
    assert health < credential < host < score
    assert "GITHUB_ACTION_CANARY_CREDENTIAL_UNAVAILABLE" in workflow
    assert "unauthenticated_gateway_health=PASS" in workflow
    assert 'X-WOW-Request-ID: $request_id' in workflow
    assert '^x-wow-request-id: $request_id' in workflow
    assert "can_execute=false" in workflow
