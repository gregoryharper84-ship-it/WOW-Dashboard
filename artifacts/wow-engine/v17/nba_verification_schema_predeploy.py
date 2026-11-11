"""Fail-closed, read-only Render pre-deploy probe for NBA shadow evidence schema.

Only confirms a reviewed database schema is present. Never runs migrations,
creates certification, promotes artifacts, or publishes probabilities.
"""
from __future__ import annotations

import os
import sys
from typing import Any

# V2 attests the canonical ledger (wow_independent_verification_receipts) and
# the calibrator evidence columns. The superseded V1 named a separate draft
# table that the verifier wiring never read or wrote.
CONTRACT = "NBA_TEAM_EVENT_VERIFICATION_RECEIPTS_V2"
RPC = "wow_nba_team_event_verification_receipt_schema_ready"
HOLD = "NBA_VERIFICATION_RECEIPT_SCHEMA_INCOMPATIBLE"
CAN_EXECUTE = False


def check_schema(db: Any) -> bool:
    """Require exact typed proof from the database itself, not a table-count guess."""
    try:
        response = db.rpc(RPC).execute()
        data = getattr(response, "data", None)
    except Exception:  # no database error strings or credentials printed
        return False
    return (
        type(data) is dict
        and data.get("contract_version") == CONTRACT
        and data.get("compatible") is True
        and data.get("can_execute") is False
        and data.get("probability_publishable") is False
    )


def main() -> int:
    url = os.environ.get("SUPABASE_URL", "").strip()
    token = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not url.startswith("https://") or not token:
        print(HOLD, file=sys.stderr)
        return 1
    try:
        from supabase import create_client
        db = create_client(url, token)
        ok = check_schema(db)
    except Exception:
        ok = False
    if not ok:
        print(HOLD, file=sys.stderr)
        return 1
    print("NBA_VERIFICATION_RECEIPT_SCHEMA_READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
