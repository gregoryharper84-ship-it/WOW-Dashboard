"""Graded source-rights receipt loader tests (receipts are evidence, never authorization)."""
from pathlib import Path

import pytest

from v17 import source_coverage_audit as sca

ROOT = Path(__file__).parent

RECEIPTS = ROOT / "v17" / "source_rights_receipts_2026-11-10.json"


def _rows():
    return {(r["sport"], r["source_id"]): r
            for r in sca.build_source_matrix(ROOT, rights_receipts=sca.load_rights_receipts(RECEIPTS))}


def test_rights_receipts_never_authorize_and_add_typed_blockers():
    rows = _rows()
    assert not any(r["legal_reproducible_fitted_training"] for r in rows.values())
    assert sca.B_NONCOMMERCIAL in rows[("NHL", "NHL_PUBLIC_WEB_API")]["blockers"]
    assert sca.B_TIMESTAMP_GAP in rows[("NHL", "NHL_PUBLIC_WEB_API")]["blockers"]
    assert sca.B_NONCOMMERCIAL in rows[("MLB", "MLB_STATS_API")]["blockers"]


def test_summary_or_partial_receipt_is_not_probative_for_license_blocker():
    rows = _rows()
    for key in (("NFL", "NFLVERSE"), ("NCAAF", "CFBD")):
        assert sca.B_RECEIPT_WEAK in rows[key]["blockers"]
        assert sca.B_NO_LICENSE_RECEIPT not in rows[key]["blockers"]


def test_open_licensed_data_store_keeps_upstream_blocker():
    assert sca.B_UPSTREAM in _rows()[("NBA", "SPORTSDATAVERSE_ESPN")]["blockers"]


def test_failed_fetch_receipt_for_unregistered_source_is_reported_unmatched():
    rec = sca.load_rights_receipts(RECEIPTS)
    um = sca.unmatched_receipts(sca.build_source_matrix(ROOT, rights_receipts=rec), rec)
    assert "NOAA_NCEI" in um


def test_receipt_loader_fails_closed(tmp_path):
    p = tmp_path / "r.json"
    p.write_text('{"schema":"WRONG","can_execute":false,"receipts":[]}')
    with pytest.raises(ValueError, match="SCHEMA_INVALID"):
        sca.load_rights_receipts(p)
    p.write_text('{"schema":"WOW_SOURCE_RIGHTS_RECEIPTS_V1","can_execute":false,"receipts":[{"source_id":"X"}]}')
    with pytest.raises(ValueError, match="FIELD_MISSING"):
        sca.load_rights_receipts(p)
    p.write_text('{"schema":"WOW_SOURCE_RIGHTS_RECEIPTS_V1","can_execute":true,"receipts":[]}')
    with pytest.raises(ValueError, match="SCHEMA_INVALID"):
        sca.load_rights_receipts(p)


def test_nflverse_receipt_keeps_upstream_blocker_and_is_not_probative():
    rows = _rows()[("NFL", "NFLVERSE")]
    assert sca.B_UPSTREAM in rows["blockers"] and sca.B_RECEIPT_WEAK in rows["blockers"]
    assert "DATASET_SPLIT_CCBY_MAJORITY_FTN_SA" in rows["rights_findings"]
    assert rows["legal_reproducible_fitted_training"] is False


def test_nflverse_policy_excludes_share_alike_families_matching_publisher_split():
    import json
    pol = json.loads((ROOT / "nflverse_dataset_policy_v1.json").read_text())
    for d in pol["datasets"]:
        if "SA" in d["license"]:
            assert d["production_training_allowed"] is False, d["dataset"]
    names = {d["dataset"] for d in pol["datasets"] if d["production_training_allowed"]}
    assert not names & {"participation", "ftn_charting"}


def test_ncei_terms_not_addressed_receipt_is_unmatched_and_grants_nothing():
    rec = sca.load_rights_receipts(RECEIPTS)
    assert {f["finding"] for f in rec["NOAA_NCEI"]} >= {"FETCH_FAILED", "TERMS_NOT_ADDRESSED_NO_AUTH_STATED"}
    assert not any(f["finding"].startswith("PERMITS") for f in rec["NOAA_NCEI"])


# --- Evidence-integrity hardening (#2574 review findings) -------------------
import hashlib
import json
from datetime import datetime, timezone

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
SCHEMA = "WOW_SOURCE_RIGHTS_RECEIPTS_V1"


def _receipt(**over):
    excerpt = over.pop("excerpt", "Terms state data may be used to train models for commercial purposes.")
    r = {
        "source_id": "NFLVERSE", "finding": "PERMITS_TRAINING", "evidence_grade": "RAW_BYTES",
        "url": "https://example.com/terms", "excerpt": excerpt,
        "excerpt_sha256": hashlib.sha256(excerpt.encode()).hexdigest(), "retrieved_at": "2026-11-09",
    }
    r.update(over)
    return r


def _write(tmp_path, payload):
    p = tmp_path / "r.json"
    p.write_text(json.dumps(payload))
    return p


def _blockers(receipt, source=("NFL", "NFLVERSE")):
    rec = {receipt["source_id"]: [receipt]}
    rows = {(r["sport"], r["source_id"]): r for r in sca.build_source_matrix(ROOT, rights_receipts=rec, now=NOW)}
    return rows[source]["blockers"], rows[source]


def test_missing_receipts_collection_is_rejected_not_an_empty_audit(tmp_path):
    for payload in ({"schema": SCHEMA, "can_execute": False},
                    {"schema": SCHEMA, "can_execute": False, "receipts": None},
                    {"schema": SCHEMA, "can_execute": False, "receipts": {}}):
        with pytest.raises(ValueError, match="COLLECTION_MISSING"):
            sca.load_rights_receipts(_write(tmp_path, payload))


@pytest.mark.parametrize("bad,match", [
    ("not-an-object", "NOT_AN_OBJECT"),
    (_receipt(source_id=""), "FIELD_MISSING:source_id"),
    (_receipt(finding=None), "FIELD_MISSING:finding"),
    (_receipt(url=123), "FIELD_MISSING:url"),
    (_receipt(excerpt="   "), "FIELD_MISSING:excerpt"),
    (_receipt(evidence_grade="NOTARIZED"), "GRADE_UNKNOWN"),
    (_receipt(retrieved_at="yesterday"), "RETRIEVED_AT_INVALID"),
    (_receipt(retrieved_at=None), "FIELD_MISSING:retrieved_at"),
    (_receipt(excerpt_sha256="0" * 64), "DIGEST_MISMATCH"),
    (_receipt(excerpt_sha256="ABC"), "DIGEST_MISMATCH"),
])
def test_malformed_receipt_rejects_whole_file(tmp_path, bad, match):
    with pytest.raises(ValueError, match=match):
        sca.load_rights_receipts(_write(tmp_path, {"schema": SCHEMA, "can_execute": False, "receipts": [bad]}))


def test_non_object_top_level_is_schema_invalid(tmp_path):
    with pytest.raises(ValueError, match="SCHEMA_INVALID"):
        sca.load_rights_receipts(_write(tmp_path, []))


def test_valid_fresh_raw_bytes_receipt_clears_only_the_license_receipt_blocker():
    blockers, row = _blockers(_receipt())
    assert sca.B_NO_LICENSE_RECEIPT not in blockers and sca.B_RECEIPT_WEAK not in blockers
    # Every other hold stays, so nothing is authorized for training.
    assert row["legal_reproducible_fitted_training"] is False and row["can_execute"] is False
    assert sca.B_NO_LIVE_RECEIPT in blockers


def test_forged_probative_claims_do_not_clear_the_license_blocker():
    forged = [
        _receipt(excerpt_sha256="f" * 64),            # arbitrary digest
        _receipt(excerpt_sha256="not-a-digest"),
        _receipt(excerpt=""),                          # no bytes at all
        _receipt(url="http://example.com/terms"),      # not https
        _receipt(url=""),
        _receipt(retrieved_at="not-a-date"),
        _receipt(evidence_grade="SUMMARY"),            # declared finding, lower grade
        _receipt(finding="PERMITS_TRAINING ", ),       # near-miss spelling
    ]
    for r in forged:
        assert sca.receipt_is_probative(r, now=NOW) is False, r
        blockers, _ = _blockers(r)
        assert sca.B_RECEIPT_WEAK in blockers, r


def test_stale_or_future_dated_evidence_is_not_probative():
    assert sca.receipt_is_probative(_receipt(retrieved_at="2026-11-10"), now=NOW) is True
    assert sca.receipt_is_probative(_receipt(retrieved_at="2026-10-11"), now=NOW) is True      # exactly 30 days
    assert sca.receipt_is_probative(_receipt(retrieved_at="2026-10-10"), now=NOW) is False     # 31 days
    assert sca.receipt_is_probative(_receipt(retrieved_at="2025-01-01"), now=NOW) is False
    assert sca.receipt_is_probative(_receipt(retrieved_at="2026-11-11"), now=NOW) is False     # future
    blockers, _ = _blockers(_receipt(retrieved_at="2025-01-01"))
    assert sca.B_RECEIPT_WEAK in blockers


def test_matrix_rechecks_receipts_passed_directly_bypassing_the_loader():
    # A caller can hand build_source_matrix a dict without going through the loader;
    # the matrix must still verify rather than trust the declared fields.
    forged = _receipt(excerpt_sha256="a" * 64)
    blockers, _ = _blockers(forged)
    assert sca.B_RECEIPT_WEAK in blockers
    with pytest.raises(ValueError, match="MAPPING_INVALID"):
        sca.build_source_matrix(ROOT, rights_receipts={"NFLVERSE": ["x"]}, now=NOW)


def test_blank_receipt_ids_do_not_clear_live_or_license_blockers():
    rows = {(r["sport"], r["source_id"]): r for r in sca.build_source_matrix(
        ROOT, live_receipts={"NFLVERSE": ""}, license_receipts={"NFLVERSE": "  ", "CFBD": None}, now=NOW)}
    for key in (("NFL", "NFLVERSE"), ("NCAAF", "CFBD")):
        assert sca.B_NO_LIVE_RECEIPT in rows[key]["blockers"] or key[1] == "CFBD"
        assert sca.B_NO_LICENSE_RECEIPT in rows[key]["blockers"]


def test_shipped_receipt_file_still_authorizes_nothing_and_keeps_legal_holds():
    rows = _rows()
    assert not any(r["legal_reproducible_fitted_training"] for r in rows.values())
    assert sca.B_UPSTREAM in rows[("NBA", "SPORTSDATAVERSE_ESPN")]["blockers"]
    assert sca.B_NONCOMMERCIAL in rows[("NHL", "NHL_PUBLIC_WEB_API")]["blockers"]
    # Digest integrity holds for every shipped receipt (loader would have raised otherwise).
    for items in sca.load_rights_receipts(RECEIPTS).values():
        for item in items:
            assert hashlib.sha256(item["excerpt"].encode()).hexdigest() == item["excerpt_sha256"]
