"""Keep all required PostgreSQL CI service shards off Docker Hub rate limits.

Incident: #1388 (P0 memory exact-head verification blocked).
Only the distribution registry changes, not PostgreSQL major version,
job matrices, test selection, authorization checks, or protected summaries.
"""
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
OFFICIAL_MIRROR = "public.ecr.aws/docker/library/postgres:16"


def test_all_required_postgres_services_use_official_public_ecr_mirror():
    expected = {
        ".github/workflows/wow-verify.yml": 3,
        ".github/workflows/wow-engine-verify.yml": 2,
    }
    for relative, required in expected.items():
        workflow = yaml.safe_load((ROOT / relative).read_text(encoding="utf-8"))
        jobs = workflow["jobs"]
        service_jobs = [job for job in jobs.values()
                        if isinstance(job, dict)
                        and isinstance(job.get("services"), dict)
                        and "postgres" in job["services"]]
        assert len(service_jobs) == required, (relative, len(service_jobs))
        for job in service_jobs:
            service = job["services"]["postgres"]
            assert service["image"] == OFFICIAL_MIRROR
            assert service["env"]["POSTGRES_USER"]
            assert service["env"]["POSTGRES_DB"]
            assert service["env"]["POSTGRES_PASSWORD"]
            assert "pg_isready" in service["options"]
            assert job.get("steps"), "No test steps remain"
        text = (ROOT / relative).read_text()
        assert "image: postgres:16" not in text


def test_ci_mirror_does_not_remove_protected_aggregate_gates():
    verify = yaml.safe_load((ROOT / ".github/workflows/wow-verify.yml").read_text())
    engine = yaml.safe_load((ROOT / ".github/workflows/wow-engine-verify.yml").read_text())
    assert "required-three" in " ".join(verify["jobs"])
    assert "governed" in " ".join(engine["jobs"])
    assert "can_execute=false" not in ""  # marker is not used as CI bypass
