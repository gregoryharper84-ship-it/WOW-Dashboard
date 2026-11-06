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


def test_admission_requires_reducer_origin_identity_before_persistence():
    """Reducer PASS alone cannot authorize a locally minted receipt identity."""
    source = SQL.read_text(encoding="utf-8")
    admission = source.split(
        "CREATE OR REPLACE FUNCTION public.wow_v17_admit_nfl_event_receipt", 1
    )[1].split("END $f$;", 1)[0]
    # These fields must be read from the authenticated reducer result itself.
    # A future implementation may use a separately authenticated reducer RPC,
    # but must not merely check the reducer's terminal PASS label.
    assert re.search(r"reduction\s*->>\s*'reducer_receipt_id'", admission, re.I), (
        "P0: no reducer-origin receipt identifier is consumed by admission"
    )
    assert re.search(r"reduction\s*->>\s*'reducer_receipt_sha256'", admission, re.I), (
        "P0: no reducer-origin receipt digest is consumed by admission"
    )


def test_missing_snapshot_artifact_checksum_is_fail_closed():
    """SQL NULL comparison must not permit an irreversible bad receipt."""
    source = SQL.read_text(encoding="utf-8")
    admission = source.split(
        "CREATE OR REPLACE FUNCTION public.wow_v17_admit_nfl_event_receipt", 1
    )[1].split("END $f$;", 1)[0]
    assert re.search(
        r"a\.artifact_checksum\s+IS\s+DISTINCT\s+FROM\s+"
        r"s\.model_output_snapshot\s*->>\s*'artifact_checksum'",
        admission,
        re.I,
    ), "A missing snapshot checksum must return HOLD before immutable admission"
