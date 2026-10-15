"""Conservative private-data egress defense; zero network."""
import pytest
from v17.gemini_input_safety import (
    GeminiSensitiveMaterialError, ensure_no_obvious_sensitive_material,
)

@pytest.mark.parametrize("fixture", [
    "SUPABASE_SERVICE_ROLE_KEY=synthetic",
    "Authorization: Bearer synthetic",
    "-----BEGIN PRIVATE KEY-----",
    "AIza" + "A" * 35,
    "person@example.com",
    "postgresql://svc:password@db-host.example",
])
def test_rejects_obvious_sensitive_material(fixture):
    with pytest.raises(GeminiSensitiveMaterialError) as exc:
        ensure_no_obvious_sensitive_material(fixture)
    assert fixture not in str(exc.value)

def test_allows_public_fixture():
    ensure_no_obvious_sensitive_material("def hello(): return 42",
        "https://github.com/example/public/blob/main/README.md")


@pytest.mark.parametrize("private_fixture", [
    "SANITIZED record: SSN 123-45-6789",
    "SANITIZED record: (415) 555-0132",
    "SSN 123456789",
    "social security number 123456789",
    "phone 4155550132",
    "+1 (415) 555-0132",
    "000-11-2222",
    "call 415-555-0132",
    "call 1.415.555.0132",
])
def test_social_security_or_phone_like_material_blocks_without_http(private_fixture):
    from v17 import gemini_optional_worker as worker
    with pytest.raises(GeminiSensitiveMaterialError):
        ensure_no_obvious_sensitive_material(private_fixture)
    network = []
    env = {
        "WOW_GEMINI_ENABLED": "true",
        "WOW_GEMINI_ACTIVE_TASKS": "code_review",
        "WOW_GEMINI_PAID_API_PROJECT_VERIFIED": "true",
        "WOW_GEMINI_MANUAL_CHALLENGE_APPROVED": "true",
        "GEMINI_MODEL": "gemini-synthetic-test",
        "GEMINI_API_KEY": "fake-test-only",
    }
    with pytest.raises(worker.GeminiWorkerError, match="GEMINI_SENSITIVE_MATERIAL_REJECTED"):
        worker.run_challenger(
            "code_review", private_fixture, data_classification="SANITIZED",
            env=env, opener=lambda *a, **kw: network.append(1))
    assert network == []


@pytest.mark.parametrize("public_fixture", [
    "2026-10-15 KDFW METAR 30.05",
    "contract station KBOS 82 F",
    "public:fixture",
    "sha256 " + "a" * 64,
    "API docs v1.4 release 2026",
])
def test_public_weather_and_source_identifiers_still_pass_preflight(public_fixture):
    ensure_no_obvious_sensitive_material(public_fixture)
