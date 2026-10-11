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
