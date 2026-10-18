"""Read-only exact-head CI evidence validator, never an independent Release approval."""
import argparse
import hashlib
import re
from pathlib import Path
from xml.etree import ElementTree as ET
import tarfile

VALID_SHA = re.compile(r"^[0-9a-f]{40}$")
HASHED_PATHS = frozenset({
    ".github/workflows/wow-v17-render-production-deploy.yml",
    "artifacts/wow-engine/migrations/20261018_release_rpc_boundary_REVIEW_ONLY.sql",
    "artifacts/wow-engine/migrations/20261018_release_observer_RECOVERY_REVIEW_ONLY.sql",
    "artifacts/wow-engine/tests/test_v17_render_release_provenance_gate.py",
    "artifacts/wow-engine/tests/test_render_release_rpc_postgres.py",
})

class EvidenceHold(ValueError):
    """No evidence-derived automatic promotion is permitted."""

def verify_evidence(directory: Path, expected_sha: str, run_id: str,
                    repository: str, min_tests: int = 112) -> dict:
    if not VALID_SHA.fullmatch(expected_sha):
        raise EvidenceHold("EXPECTED_SHA_INVALID_HOLD")
    if not run_id.isdecimal() or not run_id or len(run_id) > 20:
        raise EvidenceHold("RUN_ID_INVALID_HOLD")
    if repository != "gregoryharper84-ship-it/WOW-Dashboard":
        raise EvidenceHold("REPOSITORY_IDENTITY_MISMATCH_HOLD")
    directory = Path(directory)
    data = {}
    for line in (directory / "source-identity.txt").read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if not sep or key in data:
            raise EvidenceHold("SOURCE_IDENTITY_FORMAT_OR_DUPLICATE_HOLD")
        data[key] = value
    if (data.get("source_sha") != expected_sha or data.get("run_id") != run_id
            or data.get("repository") != repository
            or data.get("provenance_kind") != "SANDBOX_NON_RELEASE"):
        raise EvidenceHold("SOURCE_EXACT_HEAD_MISMATCH_HOLD")
    root = ET.parse(directory / "junit.xml").getroot()
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    if not suites:
        raise EvidenceHold("JUNIT_SUITES_MISSING_HOLD")
    counts = {key: sum(int(s.attrib.get(key, "0")) for s in suites)
              for key in ("tests", "errors", "failures", "skipped")}
    if (counts["tests"] < min_tests or counts["errors"] or
            counts["failures"] or counts["skipped"]):
        raise EvidenceHold("EXACT_HEAD_TESTS_FAILED_INCOMPLETE_OR_SKIPPED_HOLD")
    hashes = {}
    for line in (directory / "source-hashes.txt").read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (\S+)", line)
        if not match or match[2] in hashes:
            raise EvidenceHold("SOURCE_HASH_FORMAT_OR_DUPLICATE_HOLD")
        hashes[match[2]] = match[1]
    if not HASHED_PATHS.issubset(hashes):
        raise EvidenceHold("CRITICAL_SOURCE_HASHES_MISSING_HOLD")
    logfile = directory / "pytest.log"
    if not logfile.exists() or not logfile.read_text(encoding="utf-8").strip():
        raise EvidenceHold("TEST_LOG_MISSING_HOLD")
    bundle = directory.parent / "wow-v17-controlled-pr-sandbox-evidence.tar.gz"
    if not bundle.is_file():
        raise EvidenceHold("ATTESTATION_SUBJECT_MISSING_HOLD")
    with tarfile.open(bundle, "r:gz") as archive:
        for filename in ("source-identity.txt", "source-hashes.txt", "junit.xml", "pytest.log"):
            members = [m for m in archive.getmembers()
                       if m.name in (filename, "./" + filename)]
            if len(members) != 1 or not members[0].isfile():
                raise EvidenceHold("ATTESTATION_SUBJECT_MEMBER_MISSING_HOLD")
            stream = archive.extractfile(members[0])
            if stream is None or stream.read() != (directory / filename).read_bytes():
                raise EvidenceHold("ATTESTATION_SUBJECT_CONTENT_MISMATCH_HOLD")
    return {"status": "SANDBOX_PROOF_VERIFIED_NON_RELEASE",
            "source_sha": expected_sha, "test_count": counts["tests"],
            "bundle_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
            "production_authorized": False, "can_execute": False}

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--repository", required=True)
    args = parser.parse_args()
    import json
    print(json.dumps(verify_evidence(args.evidence_dir, args.expected_sha,
                                     args.run_id, args.repository), sort_keys=True))

if __name__ == "__main__":
    main()
