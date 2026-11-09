"""Offline, fail-closed chronological audit for V17 *candidate* model corpora.

This is a research-quality preflight, not a model scorer, source-rights grant,
artifact registry, calibration certificate, or V17 terminal authority.

It verifies file bytes against supplied digests and tests timestamp/split
consistency. Supplied timestamps and historical source legitimacy still require
independent provenance review; a passing report MUST NOT be used as a receipt.
No network, secrets, persistence or fitted probability generation is performed.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

CAN_EXECUTE = False
SPLITS = ("TRAIN", "CALIBRATION", "HOLDOUT")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_FIELDS = (
    "row_id", "sport", "model_family", "market_family", "official_event_id",
    "thesis_key", "split", "source_snapshot_id", "source_file",
    "source_payload_sha256", "feature_file", "feature_snapshot_sha256",
    "source_published_at", "source_captured_at", "feature_frozen_at",
    "event_start_at", "label_available_at",
)
TIMESTAMPS = (
    "source_published_at", "source_captured_at", "feature_frozen_at",
    "event_start_at", "label_available_at",
)


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def _file_digest(path_value: object, root: Path, cache: dict[Path, str]) -> tuple[str | None, str | None]:
    if not isinstance(path_value, str) or not path_value:
        return None, "FILE_PATH_MISSING"
    relative = Path(path_value)
    if relative.is_absolute() or ".." in relative.parts:
        return None, "FILE_PATH_OUTSIDE_ROOT"
    try:
        target = (root / relative).resolve(strict=True)
        if not target.is_relative_to(root) or not target.is_file():
            return None, "FILE_PATH_OUTSIDE_ROOT"
        if target not in cache:
            digest = sha256()
            with target.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            cache[target] = digest.hexdigest()
        return cache[target], None
    except (OSError, ValueError, RuntimeError):
        return None, "FILE_UNREADABLE"


def audit_chronological_corpus(
    rows: Sequence[Mapping[str, Any]], *, source_root: Path | str,
) -> dict[str, Any]:
    """Check a fitted *candidate* dataset's file integrity and as-of chronology.

    Every source row gets exactly one diagnostic row. Checks are intentionally
    stricter than a casual exploratory dataset: a fitted candidate needs three
    disjoint time-ordered splits, and a label must be available before the next
    split's earliest feature freeze. The caller must separately prove source
    timestamps, permitted licensing, official IDs and forward immutability.
    """
    root = Path(source_root).resolve()
    records = list(rows)
    file_cache: dict[Path, str] = {}
    checked: list[dict[str, Any]] = []
    split_counts: Counter[str] = Counter()
    cohorts: dict[tuple[str, str, str], dict[str, list[dict[str, datetime]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    seen_rows: set[str] = set()
    seen_theses: set[tuple[str, str, str, str]] = set()
    event_splits: dict[tuple[str, str], str] = {}
    global_blockers: set[str] = set()

    if not root.is_dir():
        global_blockers.add("SOURCE_ROOT_UNAVAILABLE")
    if not records:
        global_blockers.add("CORPUS_EMPTY")

    for index, row in enumerate(records):
        issues: set[str] = set()
        if not isinstance(row, Mapping):
            issues.add("ROW_NOT_OBJECT")
            row = {}
        for name in REQUIRED_FIELDS:
            if not isinstance(row.get(name), str) or not str(row.get(name)).strip():
                issues.add(f"FIELD_MISSING:{name}")
        row_id = str(row.get("row_id") or "")
        sport = str(row.get("sport") or "").upper().strip()
        model_family = str(row.get("model_family") or "").strip()
        market_family = str(row.get("market_family") or "").strip()
        event_id = str(row.get("official_event_id") or "").strip()
        thesis_key = str(row.get("thesis_key") or "").strip()
        split = str(row.get("split") or "").upper().strip()
        if split not in SPLITS:
            issues.add("SPLIT_UNKNOWN")
        else:
            split_counts[split] += 1

        if row_id:
            if row_id in seen_rows:
                issues.add("DUPLICATE_ROW_ID")
            seen_rows.add(row_id)
        if sport and event_id and thesis_key and market_family:
            thesis = (sport, event_id, market_family, thesis_key)
            if thesis in seen_theses:
                issues.add("DUPLICATE_THESIS")
            seen_theses.add(thesis)
            key = (sport, event_id)
            if key in event_splits and event_splits[key] != split:
                issues.add("EVENT_CROSSES_SPLITS")
            else:
                event_splits[key] = split

        for field_name, file_field in (
            ("source_payload_sha256", "source_file"),
            ("feature_snapshot_sha256", "feature_file"),
        ):
            expected = row.get(field_name)
            if not isinstance(expected, str) or not SHA256_RE.fullmatch(expected):
                issues.add(f"HASH_INVALID:{field_name}")
            if root.is_dir():
                actual, failure = _file_digest(row.get(file_field), root, file_cache)
                if failure:
                    issues.add(f"{failure}:{file_field}")
                elif isinstance(expected, str) and actual != expected:
                    issues.add(f"HASH_MISMATCH:{field_name}")

        parsed = {name: _timestamp(row.get(name)) for name in TIMESTAMPS}
        for name, value in parsed.items():
            if value is None:
                issues.add(f"TIMESTAMP_INVALID_OR_NAIVE:{name}")
        if all(parsed.values()):
            published, captured, frozen, start, label = (parsed[n] for n in TIMESTAMPS)
            if not (published <= captured <= frozen < start <= label):
                issues.add("SOURCE_OR_FEATURE_CHRONOLOGY_INVALID")
            elif sport and model_family and market_family and split in SPLITS:
                cohorts[(sport, model_family, market_family)][split].append(
                    {"frozen": frozen, "event": start, "label": label}
                )

        checked.append({
            "source_index": index,
            "row_id": row.get("row_id") if row_id else None,
            "status": "ROW_HOLD" if issues else "ROW_CHECKS_PASS",
            "blockers": sorted(issues),
        })
        global_blockers.update(issues)

    # Cross-row checks are not retroactively hidden by successful row checks.
    cohort_reports = []
    for (sport, model_family, market_family), spans in sorted(cohorts.items()):
        blockers: set[str] = set()
        for split in SPLITS:
            if not spans.get(split):
                blockers.add(f"SPLIT_MISSING:{split}")
        if not blockers:
            train, calibration, holdout = (spans[s] for s in SPLITS)
            if max(x["event"] for x in train) >= min(x["event"] for x in calibration):
                blockers.add("TRAIN_CALIBRATION_EVENT_CHRONOLOGY_OVERLAP")
            if max(x["event"] for x in calibration) >= min(x["event"] for x in holdout):
                blockers.add("CALIBRATION_HOLDOUT_EVENT_CHRONOLOGY_OVERLAP")
            if max(x["label"] for x in train) >= min(x["frozen"] for x in calibration):
                blockers.add("TRAIN_LABEL_LEAKS_INTO_CALIBRATION")
            if max(x["label"] for x in calibration) >= min(x["frozen"] for x in holdout):
                blockers.add("CALIBRATION_LABEL_LEAKS_INTO_HOLDOUT")
        global_blockers.update(blockers)
        cohort_reports.append({
            "sport": sport, "model_family": model_family,
            "market_family": market_family,
            "split_counts": {s: len(spans.get(s, ())) for s in SPLITS},
            "blockers": sorted(blockers),
        })
    if records and not cohorts:
        global_blockers.add("NO_VALID_CHRONOLOGICAL_COHORT")

    # Deterministic fingerprint of claimed metadata; never a source timestamp
    # attestation or proof that content was actually available before kickoff.
    digest_material = json.dumps(
        [dict(r) if isinstance(r, Mapping) else {"invalid_index": i}
         for i, r in enumerate(records)],
        sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")
    return {
        "schema": "WOW_V17_OFFLINE_CHRONOLOGY_AUDIT_V1",
        "status": "RESEARCH_AUDIT_HOLD" if global_blockers else "RESEARCH_AUDIT_PASS",
        "row_count": len(records),
        "row_results": checked,
        "split_counts": {split: split_counts[split] for split in SPLITS},
        "cohorts": cohort_reports,
        "blockers": sorted(global_blockers),
        "input_metadata_sha256": sha256(digest_material).hexdigest(),
        "source_bytes_digest_verified": bool(records) and not any(
            "FILE_" in b or "HASH_" in b or "SOURCE_ROOT_" in b
            for b in global_blockers
        ),
        "source_timestamps_independently_verified": False,
        "source_rights_independently_verified": False,
        "official_event_identity_independently_verified": False,
        "fitted_model_certified": False,
        "calibration_certified": False,
        "probability_publishable": False,
        "calibrated_probability": None,
        "calibrated_lower_bound": None,
        "can_execute": CAN_EXECUTE,
    }


def main() -> int:
    """Audit local JSONL with no external network or backend writes."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("jsonl", type=Path, help="local JSONL evidence records")
    parser.add_argument("--source-root", type=Path, required=True)
    args = parser.parse_args()
    with args.jsonl.open("r", encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    report = audit_chronological_corpus(rows, source_root=args.source_root)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "RESEARCH_AUDIT_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
