"""Fail-closed source contract for independently reducer-issued receipts.

This is an intentionally RED regression against PR #2487. It does not certify
a model, issue a terminal verdict, or enable any production feature.
"""
from pathlib import Path
import re

SQL = (
    Path(__file__).resolve().parents[1]
    / "v17/sql_candidates/20261105_multisport_nfl_receipt_admission_UNDEPLOYED.sql"
)


def test_admission_must_not_mint_its_own_reducer_receipt_id():
    """The admission layer cannot impersonate a terminal reducer's receipt."""
    source = SQL.read_text(encoding="utf-8")
    assert not re.search(r"reducer_id\s*:=\s*pg_catalog\.gen_random_uuid\s*\(", source, re.I), (
        "P0: admission locally mints reducer_receipt_id; require reducer-issued, "
        "independently authenticated receipt identity and binding"
    )


def test_admission_must_not_hash_its_own_reducer_receipt_as_authority():
    """A local digest of reducer JSON is not reducer-issued attestation."""
    source = SQL.read_text(encoding="utf-8")
    assert not re.search(r"reducer_hash\s*:=\s*pg_catalog\.encode\s*\(", source, re.I), (
        "P0: admission locally constructs reducer_receipt_sha256; require "
        "trusted reducer-origin receipt/hash with independent verification"
    )
