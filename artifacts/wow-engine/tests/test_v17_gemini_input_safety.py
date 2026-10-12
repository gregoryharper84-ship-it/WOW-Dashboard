"""Gemini privacy preflight: reject before ANY external request."""
import json
import io
import pytest
from v17.gemini_input_safety import (GeminiSensitiveMaterialError,
    ensure_no_obvious_sensitive_material)
from v17.gemini_async_contracts import (GeminiContractError,
    prepare_inline_batch,prepare_explicit_cache)
from v17.gemini_optional_worker import GeminiWorkerError,run_challenger


@pytest.mark.parametrize("secret", [
    "SUPABASE_SERVICE_ROLE_KEY=super-secret-test",
    "Authorization: Bearer token-abc-for-test",
    "-----BEGIN PRIVATE KEY-----",
    "AIza"+"A"*35,
    "AKIA"+"B"*16,
    "sk-proj-"+"c"*20,
    "postgresql://svc:password@db-host.example",
    "test.person@example.com",
    "eyJ"+"a"*10+".eyJ"+"b"*10+"."+"c"*15,
])
def test_obvious_sensitive_material_blocked_without_echo(secret):
    with pytest.raises(GeminiSensitiveMaterialError) as exc:
        ensure_no_obvious_sensitive_material(secret)
    assert str(exc.value)=="GEMINI_SENSITIVE_MATERIAL_REJECTED"
    assert secret not in str(exc.value)


def test_public_code_and_public_reference_accepted():
    ensure_no_obvious_sensitive_material("def hello(): return 42",
        "https://github.com/example/public-repo/blob/main/README.md")


def test_worker_blocks_before_network_even_if_claimed_sanitized():
    invoked=[]
    env={"WOW_GEMINI_ENABLED":"true","WOW_GEMINI_ACTIVE_TASKS":"code_review",
         "WOW_GEMINI_PAID_API_PROJECT_VERIFIED":"true",
         "GEMINI_API_KEY":"test-key","GEMINI_MODEL":"gemini-test-model"}
    with pytest.raises(GeminiWorkerError,match="GEMINI_SENSITIVE_MATERIAL_REJECTED"):
        run_challenger("code_review","Contact test.person@example.com",
            data_classification="SANITIZED",env=env,
            opener=lambda *a,**k: invoked.append(True))
    assert not invoked


def test_batch_rejects_sensitive_source_ref_and_cache_content():
    with pytest.raises(GeminiContractError, match="GEMINI_SENSITIVE_MATERIAL_REJECTED"):
        prepare_inline_batch(model="gemini-test-model",urgency="BACKGROUND",
            data_classification="PUBLIC",
            items=[{"key":"row_a","task":"code_review","prompt":"public text",
                    "evidence_refs":["Authorization: Bearer test123"]}])
    with pytest.raises(GeminiContractError,match="GEMINI_SENSITIVE_MATERIAL_REJECTED"):
        prepare_explicit_cache(model="gemini-test-model",
            content="password is in DB_PASSWORD=not-okay",ttl_seconds=300,
            source_refs=["file:public"],data_classification="PUBLIC")
