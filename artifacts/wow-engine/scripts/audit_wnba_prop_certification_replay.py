#!/usr/bin/env python3
"""Compare a fresh WNBA fit replay with checked candidate artifacts."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from wnba_prop_certification_replay import audit_wnba_certification_replay


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--checked",
        type=Path,
        default=root / "data" / "wow_wnba_prop_artifacts_v1.json",
    )
    parser.add_argument(
        "--replay",
        type=Path,
        default=root / "data" / "wnba-replay" / "wow_wnba_prop_artifacts_v1.json",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=root / "historical_source_manifest_v1.json",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=root / "data" / "wnba_prop_certification_replay_v1.json",
    )
    args = parser.parse_args()

    report = audit_wnba_certification_replay(
        checked_artifacts=_load(args.checked),
        replay_artifacts=_load(args.replay),
        source_manifest=_load(args.manifest),
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ready_for_lifecycle_review"] else 3


if __name__ == "__main__":
    sys.exit(main())
