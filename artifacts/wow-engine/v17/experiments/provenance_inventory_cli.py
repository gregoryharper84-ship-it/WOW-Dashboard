"""Offline, read-only V17 feature-provenance reconciliation.

Consumes exported JSON arrays; never connects to a database or publishes
models. This CLI records only aggregate results, never source rows.
"""
import argparse
import json
from pathlib import Path
from pregame_provenance_gate import audit_d1_provenance, audit_snapshot_alignment


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("d1", "source"), required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = json.loads(args.input.read_text(encoding="utf-8"))
    result = audit_d1_provenance(rows) if args.kind == "d1" else audit_snapshot_alignment(rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
