"""Synthetic-only tests for source archive integrity; no external fetches."""
from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

from v17.cricsheet_source_candidate import (
    SourceCandidateHold, audit_t20_archive, acquire_published_t20_archive,
)


def sample(*, outcome=None, teams=None, date="2026-08-01", match_type="T20"):
    return {"info": {"match_type": match_type, "teams": teams or ["A", "B"],
                     "dates": [date], "outcome": outcome or {"winner": "A"}},
            "innings": []}


def archive(*, match_items=None, license_text="Open Data Commons Attribution License ODC-BY 1.0", extras=None):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("LICENSE.txt", license_text)
        for name, obj in (match_items if match_items is not None else [("42.json", sample())]):
            z.writestr(name, json.dumps(obj))
        for name, value in extras or []:
            z.writestr(name, value)
    return out.getvalue()


def test_candidate_archive_is_pinned_and_never_model_certified():
    b = archive(match_items=[("42.json", sample()), ("43.json", sample(outcome={"result": "tie"}))])
    r = audit_t20_archive(b, expected_sha256=hashlib.sha256(b).hexdigest())
    assert r["unique_t20_match_ids"] == 2
    assert (r["binary_settled_matches"], r["nonbinary_or_void_matches"]) == (1, 1)
    assert r["archive_pin_status"] == "MATCHED"
    assert r["pregame_feature_chronology_status"] == "UNVERIFIED"
    assert r["model_training_authorized"] is False
    assert r["probability_publishable"] is False
    assert r["can_execute"] is False


@pytest.mark.parametrize("data,error", [
    (b"", "SOURCE_ARCHIVE_SIZE_INVALID"),
    (b"not a ZIP", "SOURCE_ARCHIVE_FORMAT_INVALID"),
    (archive(license_text="No license"), "SOURCE_ARCHIVE_LICENSE_NOT_RECOGNIZED"),
    (archive(match_items=[("a.json", sample())]), "SOURCE_ARCHIVE_MATCH_ID_INVALID"),
    (archive(match_items=[("42.json", sample(teams=["A", "A"]))]), "SOURCE_ARCHIVE_MATCH_IDENTITY_OR_DATE_INVALID"),
    (archive(match_items=[("42.json", sample(match_type="ODI"))]), "SOURCE_ARCHIVE_MATCH_TYPE_UNVERIFIED"),
    (archive(match_items=[("42.json", sample(outcome={"winner": "Z"}))]), "SOURCE_ARCHIVE_SETTLEMENT_TEAM_MISMATCH"),
    (archive(match_items=[("42.json", sample(outcome={"result": "unknown"}))]), "SOURCE_ARCHIVE_OUTCOME_UNRESOLVED"),
    (archive(extras=[("../escape.py", "danger")]), "SOURCE_ARCHIVE_PATH_INVALID"),
    (archive(extras=[("evil.sh", "echo 1")]), "SOURCE_ARCHIVE_EXECUTABLE_MEMBER_FORBIDDEN"),
])
def test_archive_rejects_bad_sources(data, error):
    with pytest.raises(SourceCandidateHold, match=error):
        audit_t20_archive(data)


def test_wrong_pin_rejected_before_archive_parsing():
    with pytest.raises(SourceCandidateHold, match="SOURCE_ARCHIVE_PIN_MISMATCH"):
        audit_t20_archive(archive(), expected_sha256="f" * 64)


def test_duplicate_match_identifiers_are_not_double_counted():
    with pytest.raises(SourceCandidateHold, match="SOURCE_ARCHIVE_DUPLICATE_MATCH_ID"):
        audit_t20_archive(archive(match_items=[("folder/42.json", sample()), ("42.json", sample())]))


def test_download_rejects_mismatched_final_origin():
    class R:
        def __enter__(self): return self
        def __exit__(self, *a): return None
        def geturl(self): return "https://third-party.example/data.zip"
        def read(self, *_): return archive()
    class O:
        def open(self, *a, **kw): return R()
    with pytest.raises(SourceCandidateHold, match="SOURCE_ARCHIVE_ORIGIN_UNVERIFIED"):
        acquire_published_t20_archive(opener=O())


def test_cli_creates_only_receipt_and_does_not_overwrite(tmp_path):
    from v17.cricsheet_source_candidate import main
    raw = tmp_path / "candidate.zip"
    raw.write_bytes(archive())
    receipt = tmp_path / "out.json"
    assert main(["--archive", str(raw), "--receipt", str(receipt)]) == 0
    record = json.loads(receipt.read_text())
    assert record["model_training_authorized"] is False
    with pytest.raises(FileExistsError):
        main(["--archive", str(raw), "--receipt", str(receipt)])
