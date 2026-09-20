#!/usr/bin/env python3
"""Inspect the pinned NHL SportsDataverse raw snapshot without transforming it.

Produces a deterministic schema/row-count report used to design the canonical
adapter. This script does not infer field meaning beyond the raw CSV column
names, does not fit a model, and does not publish probability.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nhl_sportsdataverse_snapshot import (  # noqa: E402
    CAN_EXECUTE,
    PROBABILITY_PUBLISHABLE,
    SOURCE_ID,
    SOURCE_REVIEW_REQUIRED,
    pinned_assets,
    verify_asset_file,
)


def _trim(value: str | None, limit: int = 160) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def inspect_snapshot(directory: str | Path) -> dict[str, object]:
    root = Path(directory)
    reports: list[dict[str, object]] = []
    for asset in pinned_assets():
        path = root / asset.filename
        verify_asset_file(path, asset)
        row_count = 0
        samples: list[dict[str, str | None]] = []
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = tuple(reader.fieldnames or ())
            if not fieldnames:
                raise RuntimeError(f"NHL_SDV_SCHEMA_EMPTY:{asset.filename}")
            for row in reader:
                row_count += 1
                if len(samples) < 2:
                    samples.append({key: _trim(row.get(key)) for key in fieldnames})
        reports.append(
            {
                "kind": asset.kind,
                "season": asset.season,
                "filename": asset.filename,
                "sha256": asset.sha256,
                "row_count": row_count,
                "columns": list(fieldnames),
                "sample_rows": samples,
            }
        )
    return {
        "schema_version": "WOW_NHL_SDV_SCHEMA_REPORT_V1",
        "source_id": SOURCE_ID,
        "source_review_required": SOURCE_REVIEW_REQUIRED,
        "probability_publishable": PROBABILITY_PUBLISHABLE,
        "can_execute": CAN_EXECUTE,
        "assets": reports,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = inspect_snapshot(args.input_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "source_id": report["source_id"],
        "asset_count": len(report["assets"]),
        "output": str(args.output),
        "can_execute": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
