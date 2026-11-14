"""Guard the CI PostgreSQL service image against Docker Hub anonymous 429s.

GitHub-hosted runner service images are pulled before workflow steps; a login
step cannot repair a failed Initialize containers phase. This is CI-only.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "public.ecr.aws/docker/library/postgres:16"


def test_verification_workflows_use_official_postgres_registry_mirror():
    expected = {
        ".github/workflows/wow-verify.yml": 3,
        ".github/workflows/wow-engine-verify.yml": 2,
    }
    for relative_path, n in expected.items():
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        assert text.count("image: " + IMAGE) == n, relative_path
        assert "image: postgres:16" not in text, relative_path


def test_engine_workflow_uses_official_redis_registry_mirror():
    path = ROOT / ".github/workflows/wow-engine-verify.yml"
    text = path.read_text(encoding="utf-8")
    assert text.count("image: public.ecr.aws/docker/library/redis:7-alpine") == 2
    assert "image: redis:7-alpine" not in text
    assert 'redis-cli ping' in text
    assert "6379:6379" in text


def test_postgrest_same_version_same_digest_from_ghcr():
    path = ROOT / ".github/workflows/wow-engine-verify.yml"
    text = path.read_text(encoding="utf-8")
    # GitHub Container Registry publishes the exact PostgREST v12.2.3 index
    # digest used by the existing Docker Hub image. No version promotion.
    image = (
        "ghcr.io/supabase/postgrest:v12.2.3@"
        "sha256:729bf65c733b73f5b52777f0e4b853f22ed73aa67a22d38269d289779b0a8401"
    )
    assert text.count("image: " + image) == 2
    assert "image: postgrest/postgrest:v12.2.3" not in text
    assert "PGRST_DB_URI:" in text
    assert "PGRST_DB_SCHEMAS:" in text


def test_service_postgres_identity_and_port_remain_unchanged():
    for relative_path in (
        ".github/workflows/wow-verify.yml",
        ".github/workflows/wow-engine-verify.yml",
    ):
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        assert text.count("postgres:") >= 1
        assert "POSTGRES_DB: wow_agent_ci" in text or "POSTGRES_DB:" in text
        assert "5432:5432" in text
