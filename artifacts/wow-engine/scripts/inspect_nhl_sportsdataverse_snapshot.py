#!/usr/bin/env python3
"""Inspect the pinned NHL SportsDataverse raw snapshot without transforming it.

Produces a deterministic schema/row-count/completeness report used to design
the canonical adapter. This script does not infer field meaning beyond the raw
CSV column names and literal values, does not fit a model, and does not publish
probability.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
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

PLAYER_IDENTITY_COLUMNS = ("game_id", "season", "game_date", "player_id", "team_id")
PLAYER_REQUIRED_VALUE_COLUMNS = PLAYER_IDENTITY_COLUMNS + ("shots_on_goal", "home_away", "team_abbrev", "position")
GAME_IDENTITY_COLUMNS = ("game_id", "season", "game_date", "home_team_abbr", "away_team_abbr")
GAME_REQUIRED_VALUE_COLUMNS = GAME_IDENTITY_COLUMNS + ("game_type", "game_state")
SCHEDULE_MIN_IDENTITY_COLUMNS = ("game_id", "season")
CATEGORICAL_REPORT_COLUMNS = ("game_type", "game_state", "home_away", "position")
EXACT_START_TIME_NAMES = {
    "start_time_utc",
    "starttimeutc",
    "game_start_time",
    "start_time",
    "starttime",
    "game_datetime",
    "game_date_time",
    "datetime",
}


def _trim(value: str | None, limit: int = 160) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _is_blank(value: str | None) -> bool:
    return value is None or not str(value).strip()


def _column_contract(kind: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if kind == "PLAYER_BOXSCORES":
        return PLAYER_REQUIRED_VALUE_COLUMNS, PLAYER_IDENTITY_COLUMNS
    if kind == "GAME_INFO":
        return GAME_REQUIRED_VALUE_COLUMNS, GAME_IDENTITY_COLUMNS
    if kind == "SCHEDULE":
        return SCHEDULE_MIN_IDENTITY_COLUMNS, SCHEDULE_MIN_IDENTITY_COLUMNS
    raise RuntimeError(f"NHL_SDV_ASSET_KIND_UNKNOWN:{kind}")


def inspect_snapshot(directory: str | Path) -> dict[str, object]:
    root = Path(directory)
    reports: list[dict[str, object]] = []
    for asset in pinned_assets():
        path = root / asset.filename
        verify_asset_file(path, asset)
        row_count = 0
        samples: list[dict[str, str | None]] = []
        required_columns, identity_columns = _column_contract(asset.kind)
        missing_counts: Counter[str] = Counter()
        categorical_counts: dict[str, Counter[str]] = {
            key: Counter() for key in CATEGORICAL_REPORT_COLUMNS
        }
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = tuple(reader.fieldnames or ())
            if not fieldnames:
                raise RuntimeError(f"NHL_SDV_SCHEMA_EMPTY:{asset.filename}")
            fieldset = set(fieldnames)
            missing_columns = tuple(sorted(set(required_columns) - fieldset))
            for row in reader:
                row_count += 1
                if len(samples) < 2:
                    samples.append({key: _trim(row.get(key)) for key in fieldnames})
                for key in required_columns:
                    if key in fieldset and _is_blank(row.get(key)):
                        missing_counts[key] += 1
                for key in CATEGORICAL_REPORT_COLUMNS:
                    if key in fieldset:
                        raw = row.get(key)
                        categorical_counts[key]["<BLANK>" if _is_blank(raw) else str(raw).strip()] += 1
        identity_complete = all(key in fieldnames for key in identity_columns)
        lower_map = {key.lower(): key for key in fieldnames}
        exact_time_columns = sorted(
            original for lowered, original in lower_map.items() if lowered in EXACT_START_TIME_NAMES
        )
        temporal_columns = sorted(
            key for key in fieldnames if "time" in key.lower() or "date" in key.lower()
        )
        report = {
            "kind": asset.kind,
            "season": asset.season,
            "filename": asset.filename,
            "sha256": asset.sha256,
            "row_count": row_count,
            "columns": list(fieldnames),
            "missing_required_columns": list(missing_columns),
            "missing_value_counts": {key: int(missing_counts.get(key, 0)) for key in required_columns if key in fieldnames},
            "identity_columns_present": identity_complete,
            "categorical_value_counts": {
                key: dict(sorted(counter.items()))
                for key, counter in categorical_counts.items()
                if key in fieldnames
            },
            "temporal_columns": temporal_columns,
            "exact_start_time_columns": exact_time_columns,
            "sample_rows": samples,
        }
        if asset.kind == "PLAYER_BOXSCORES":
            report["player_game_identity_complete"] = identity_complete
            report["has_shots_on_goal"] = "shots_on_goal" in fieldnames
        elif asset.kind == "GAME_INFO":
            report["game_identity_complete"] = identity_complete
            report["has_exact_start_time"] = bool(exact_time_columns)
        else:
            report["schedule_min_identity_complete"] = identity_complete
            report["has_exact_start_time"] = bool(exact_time_columns)
        reports.append(report)
    return {
        "schema_version": "WOW_NHL_SDV_SCHEMA_REPORT_V3",
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
