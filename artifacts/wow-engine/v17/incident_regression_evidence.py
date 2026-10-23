"""Read-only authenticated GitHub Actions proof for incident closure regression tests.

This is a Reporter-closure acceptance check, NOT a GitHub merge/release gate.
Fail closed on incomplete SHA, CI outcome, test-case or source provenance.
"""
from __future__ import annotations

import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen


_REPO = "gregoryharper84-ship-it/WOW-Dashboard"
_JOB_URL = re.compile(
    r"https://github\.com/gregoryharper84-ship-it/WOW-Dashboard/actions/runs/([1-9][0-9]*)/job/([1-9][0-9]*)\Z"
)
_TEST_ID = re.compile(
    r"artifacts/wow-engine/(?:[a-zA-Z0-9_-]+/)*test_[a-zA-Z0-9_-]+\.py::test_[a-zA-Z0-9_]+(?:\[[a-zA-Z0-9_, -]+\])?\Z"
)
_SHA = re.compile(r"[a-f0-9]{40}\Z")
_TRUSTED_WORKFLOWS = frozenset({"wow-verify", "wow-engine-verify", "wow-v17-rapid-repair"})
_MAX_LOG_BYTES = 12 * 1024 * 1024


def _trusted_job_url(value: object) -> tuple[int, int]:
    match = _JOB_URL.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise ValueError("learning receipt requires authentic regression GitHub Actions job evidence URL")
    return int(match.group(1)), int(match.group(2))


def validate_regression_fields(receipt: dict) -> list[str]:
    """Side-effect-free shape checks used by the common closure contract."""
    errors = []
    try:
        _trusted_job_url(receipt.get("regression_test_evidence_ref"))
    except ValueError as exc:
        errors.append(str(exc))
    if not _TEST_ID.fullmatch(str(receipt.get("regression_test_added") or "")):
        errors.append("learning receipt regression_test_added must identify a pytest test case")
    if not _SHA.fullmatch(str(receipt.get("regression_test_sha") or "")):
        errors.append("learning receipt regression_test_sha invalid")
    return errors


def _job_json(job_id: int, token: str) -> dict:
    request = Request(
        f"https://api.github.com/repos/{_REPO}/actions/jobs/{job_id}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "wow-incident-regression-verifier",
        },
    )
    with urlopen(request, timeout=20) as response:
        return json.load(response)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _job_log(job_id: int, token: str) -> str:
    """Get GitHub-generated, expiring signed log URL without forwarding credentials."""
    request = Request(
        f"https://api.github.com/repos/{_REPO}/actions/jobs/{job_id}/logs",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "wow-incident-regression-verifier",
        },
    )
    try:
        build_opener(_NoRedirect()).open(request, timeout=20)
    except HTTPError as exc:
        if exc.code not in (302, 303, 307):
            raise ValueError("regression evidence GitHub log unavailable") from exc
        signed = exc.headers.get("Location", "")
    else:
        raise ValueError("regression evidence missing authenticated GitHub log redirect")
    parsed = urlsplit(signed)
    host = (parsed.hostname or "").lower()
    if (parsed.scheme != "https" or not (
        host.endswith(".blob.core.windows.net")
        or host.endswith(".actions.githubusercontent.com")
        or host == "objects.githubusercontent.com"
        or host.endswith(".s3.amazonaws.com")
    )):
        raise ValueError("regression evidence untrusted log redirect host")
    with urlopen(Request(signed, headers={"User-Agent": "wow-incident-regression-verifier"}), timeout=20) as response:
        raw = response.read(_MAX_LOG_BYTES + 1)
    if len(raw) > _MAX_LOG_BYTES:
        raise ValueError("regression evidence job log exceeds verification limit")
    return raw.decode("utf-8", errors="replace")


def verify_authenticated_regression(receipt: dict, *, expected_sha: str) -> dict:
    """Require one named pytest case PASSED in a real successful exact-SHA CI job.

    The job and log are fetched from GitHub using read-only authentication.
    String assertions or job URLs supplied by the incident author alone never pass.
    """
    errors = validate_regression_fields(receipt)
    if errors:
        raise ValueError("; ".join(errors))
    if not _SHA.fullmatch(str(expected_sha or "")) or receipt["regression_test_sha"] != expected_sha:
        raise ValueError("regression evidence SHA does not match verified release")
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError("regression evidence GitHub read credential unavailable")
    run_id, job_id = _trusted_job_url(receipt["regression_test_evidence_ref"])
    try:
        job = _job_json(job_id, token)
        if not isinstance(job, dict):
            raise ValueError("regression evidence job metadata invalid")
        if (job.get("id") != job_id or job.get("run_id") != run_id or
                job.get("head_sha") != expected_sha or
                job.get("status") != "completed" or job.get("conclusion") != "success" or
                job.get("workflow_name") not in _TRUSTED_WORKFLOWS):
            raise ValueError("regression evidence job failed, mismatched or untrusted")
        log = _job_log(job_id, token)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise ValueError("regression evidence GitHub verification unavailable") from exc
    test_id = receipt["regression_test_added"]
    if not re.search(
        r"(?m)^(?:\d{4}-\d{2}-\d{2}T[^\s]+Z\s+)?"
        + re.escape(test_id) + r"\s+PASSED(?:\s|\[|$)",
        log,
    ):
        raise ValueError("regression evidence named test has no authenticated PASS log")
    return {
        "source": "GITHUB_ACTIONS_AUTHENTICATED_JOB_LOG",
        "job_id": job_id,
        "run_id": run_id,
        "tested_sha": expected_sha,
        "test": test_id,
        "workflow": job["workflow_name"],
        "result": "PASS",
    }
