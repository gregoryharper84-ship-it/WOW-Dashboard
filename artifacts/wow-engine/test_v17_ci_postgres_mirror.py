"""Pin required PostgreSQL CI services to a working official image source.

Incidents: #1388 and #1688. Public ECR returned toomanyrequests/Data limit exceeded.
Only the distribution registry changes, not PostgreSQL major version,
job matrices, test selection, authorization checks, or protected summaries.
"""
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
OFFICIAL_IMAGE = "postgres:16"


def test_all_required_postgres_services_use_verified_official_image():
    expected = {
        ".github/workflows/wow-verify.yml": 3,
        ".github/workflows/wow-engine-verify.yml": 4,
    }
    for relative, required in expected.items():
        workflow = yaml.safe_load((ROOT / relative).read_text(encoding="utf-8"))
        jobs = workflow["jobs"]
        service_jobs = [job for job in jobs.values()
                        if isinstance(job, dict)
                        and isinstance(job.get("services"), dict)
                        and "postgres" in job["services"]]
        assert len(service_jobs) == required, (relative, len(service_jobs))
        if relative == ".github/workflows/wow-engine-verify.yml":
            # The NBA calibrator job is an *additional mandatory* isolated DB
            # proof, never a replacement for Agent Runtime or backend shards.
            assert {"regression-shards", "agent-runtime-integration",
                    "nba-calibrator-db-contract",
                    "nba-shadow-receipt-db-contract"}.issubset(jobs)
            nba = jobs["nba-calibrator-db-contract"]
            assert nba["services"]["postgres"]["image"] == OFFICIAL_IMAGE
            assert "nba-calibrator-db-contract" in jobs["governed-probability-backend"]["needs"]
            run_steps = [str(step.get("run") or "") for step in nba["steps"]
                         if isinstance(step, dict)]
            assert any("NBA_CALIBRATOR_DB_CONTRACT_VERIFIED" in step for step in run_steps)
            assert any("if total < 19 or skipped or failures or errors" in step
                       for step in run_steps)
            # The NBA shadow-receipt contract is also mandatory. Keep the
            # zero-skip real PostgreSQL proof and aggregate dependency.
            shadow = jobs["nba-shadow-receipt-db-contract"]
            assert shadow["services"]["postgres"]["image"] == OFFICIAL_IMAGE
            assert "nba-shadow-receipt-db-contract" in jobs["governed-probability-backend"]["needs"]
            shadow_steps = [str(step.get("run") or "") for step in shadow["steps"]
                            if isinstance(step, dict)]
            assert any("NBA_SHADOW_DB_CONTRACT_VERIFIED" in step for step in shadow_steps)
            assert any("if total < 7 or skipped or failures or errors" in step
                       for step in shadow_steps)
        for job in service_jobs:
            service = job["services"]["postgres"]
            assert service["image"] == OFFICIAL_IMAGE
            assert service["env"]["POSTGRES_USER"]
            assert service["env"]["POSTGRES_DB"]
            assert service["env"]["POSTGRES_PASSWORD"]
            assert "pg_isready" in service["options"]
            assert job.get("steps"), "No test steps remain"
        text = (ROOT / relative).read_text()
        assert "image: public.ecr.aws/docker/library/postgres:16" not in text


def test_ci_image_source_does_not_remove_protected_aggregate_gates():
    verify = yaml.safe_load((ROOT / ".github/workflows/wow-verify.yml").read_text())
    engine = yaml.safe_load((ROOT / ".github/workflows/wow-engine-verify.yml").read_text())
    assert "required-three" in " ".join(verify["jobs"])
    assert "governed" in " ".join(engine["jobs"])
    assert any("needs:" in line for line in (ROOT / ".github/workflows/wow-verify.yml").read_text().splitlines())
    assert any("needs:" in line for line in (ROOT / ".github/workflows/wow-engine-verify.yml").read_text().splitlines())
