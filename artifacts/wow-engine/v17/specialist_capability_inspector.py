from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping


FRAMEWORK_VERSION = "PR_1199_CLASS_B"
CAN_EXECUTE = False


def resolve_capability_matrix(rows: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    """Build an inspection-only matrix. Never upgrades capability or certifies a model."""
    matrix: Dict[str, Any] = {}
    for row in rows:
        sport = str(row.get("sport") or "").upper()
        market = str(row.get("market_type") or row.get("market") or "").upper()
        if not sport or not market:
            continue
        key = f"{sport}:{market}"
        registered = bool(row.get("registered", False))
        certification = row.get("certification") or {}
        is_active = bool(certification.get("is_active", False))
        specialist_id = row.get("specialist_id")

        if not registered or not specialist_id:
            status = "CHALLENGER_SPEC_REQUIRED"
            gate_reason = "SPECIALIST_NOT_REGISTERED"
            model_qualified = False
        elif not certification:
            status = "CHALLENGER_SPEC_REQUIRED"
            gate_reason = "CERTIFICATION_ARTIFACT_MISSING"
            model_qualified = False
        elif not is_active:
            status = "REGISTERED_UNCERTIFIED"
            gate_reason = "SPECIALIST_NOT_CERTIFIED"
            model_qualified = False
        else:
            status = "CERTIFICATION_PRESENT_REQUIRES_RUNTIME_VERIFICATION"
            gate_reason = "RUNTIME_VERIFICATION_REQUIRED"
            model_qualified = False

        matrix[key] = {
            "specialist_id": specialist_id,
            "status": status,
            "owner_lane": row.get("owner_lane", "unregistered"),
            "model_qualified": model_qualified,
            "typed_gate_reason": gate_reason,
        }

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "framework_version": FRAMEWORK_VERSION,
        "can_execute": CAN_EXECUTE,
        "capability_matrix": matrix,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect V17 specialist capability declarations.")
    parser.add_argument("--input", required=True, help="JSON file containing a list of specialist capability rows.")
    parser.add_argument("--format", choices=("json",), default="json")
    args = parser.parse_args()

    rows = json.loads(Path(args.input).read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise SystemExit("input must be a JSON list")
    print(json.dumps(resolve_capability_matrix(rows), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
