import re
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


V17_DIR = REPO_ROOT / "artifacts" / "wow-engine" / "v17"
CANONICAL_SPEC = V17_DIR / "openapi.llp-team-engine.v17.yaml"
DIAGNOSTIC_SPEC = V17_DIR / "openapi.llp-team-engine.v17.supabase-gateway-diagnostic.yaml"
EXTERNAL_PREFIX = "/functions/v1/wow-llp-action-gateway"


def _gateway_routes():
    source = GATEWAY.read_text(encoding="utf-8")
    return [
        (m.group(1), m.group(2), m.group(3) == "true")
        for m in re.finditer(
            r'\{ method: "(GET|POST)", pattern: /(.+?)/, auth: (true|false) \}', source
        )
    ]


def _spec_paths(spec):
    return re.findall(r"^  (/[^\s:]+):\s*$", spec.read_text(encoding="utf-8"), re.M)


def _concrete(path):
    return re.sub(r"\{[^}]+\}", "x", path)


def test_gateway_route_auth_flags_are_pinned():
    routes = _gateway_routes()
    assert len(routes) == 12
    public = {pattern for _, pattern, auth in routes if not auth}
    assert public == {r"^\/health$", r"^\/governance$"}


def test_gateway_missing_bearer_is_typed_401_fail_closed():
    source = GATEWAY.read_text(encoding="utf-8")
    assert 'jsonResponse(401, "LLP_GATEWAY_AUTH_REQUIRED", requestId)' in source
    helper = source[source.index("function jsonResponse("):]
    assert "can_execute: false" in helper[:300]


def test_openapi_paths_match_gateway_routes_both_directions():
    canonical = _spec_paths(CANONICAL_SPEC)
    diagnostic = _spec_paths(DIAGNOSTIC_SPEC)
    for path in canonical + diagnostic:
        assert path.startswith(EXTERNAL_PREFIX + "/")
    runtime = [p[len(EXTERNAL_PREFIX):] for p in canonical]
    matchers = [re.compile(pattern.replace("\\/", "/")) for _, pattern, _ in _gateway_routes()]
    for path in runtime:
        assert any(m.match(_concrete(path)) for m in matchers), path
    for m in matchers:
        assert any(m.match(_concrete(p)) for p in runtime), m.pattern
    assert set(diagnostic) <= set(canonical)


def test_openapi_operation_ids_unique_and_diagnostic_ids_subset_of_canonical():
    ids = {}
    for spec in (CANONICAL_SPEC, DIAGNOSTIC_SPEC):
        found = re.findall(r"operationId:\s*(\S+)", spec.read_text(encoding="utf-8"))
        assert len(found) == len(set(found))
        ids[spec] = set(found)
    assert ids[DIAGNOSTIC_SPEC] == {"getLlpV17BackendHealth", "scoreLlpV17SpreadForwardShadow"}
    assert ids[DIAGNOSTIC_SPEC] <= ids[CANONICAL_SPEC]


def test_repo_does_not_assert_live_editor_sync_verified():
    for path in (CANONICAL_SPEC, DIAGNOSTIC_SPEC, GATEWAY):
        assert "LIVE_GPT_EDITOR_SYNC=VERIFIED" not in path.read_text(encoding="utf-8")
