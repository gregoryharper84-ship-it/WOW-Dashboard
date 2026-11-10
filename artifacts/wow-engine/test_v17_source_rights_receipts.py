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
