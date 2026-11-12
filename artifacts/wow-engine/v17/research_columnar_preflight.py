"""Fail-closed, read-only Parquet corpus preflight for offline V17 research.

This is NOT a fitter, calibrator, model registry, data rights grant, or scorer.
It never computes probabilities or alters the production source of truth.
Optional DuckDB is listed in requirements-research.txt, not requirements.txt.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

SCHEMA = "WOW_V17_COLUMNAR_PREFLIGHT_V1"
CAN_EXECUTE = False
_REQUIRED_MANIFEST = frozenset({"schema", "can_execute", "dataset_id", "sport", "season", "files"})
_REQUIRED_FILE = frozenset({"path", "sha256"})
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_IDENTIFIER = re.compile(r"[a-zA-Z0-9_\-]{1,80}\Z")

# Required Parquet columns, with UTC-epoch seconds to avoid naive timezone casts:
# sport, season, observation_id, event_id, event_start_epoch,
# feature_available_epoch, quote_captured_epoch (NULL allowed for nonmarket rows).
_QUERY = """
SELECT
    COUNT(*) AS row_count,
    COUNT(DISTINCT observation_id) AS distinct_observations,
    SUM(CASE WHEN observation_id IS NULL OR event_id IS NULL THEN 1 ELSE 0 END) AS missing_identity,
    SUM(CASE WHEN sport IS NULL OR sport <> ? OR season IS NULL
                  OR CAST(season AS VARCHAR) <> ? THEN 1 ELSE 0 END) AS wrong_partition,
    SUM(CASE WHEN TRY_CAST(event_start_epoch AS BIGINT) IS NULL
                  OR TRY_CAST(event_start_epoch AS BIGINT) <= 0
                  OR TRY_CAST(event_start_epoch AS DOUBLE) <> TRY_CAST(event_start_epoch AS BIGINT)
             THEN 1 ELSE 0 END) AS invalid_event_time,
    SUM(CASE WHEN TRY_CAST(feature_available_epoch AS BIGINT) IS NULL
                  OR TRY_CAST(feature_available_epoch AS BIGINT) <= 0
                  OR TRY_CAST(feature_available_epoch AS DOUBLE) <> TRY_CAST(feature_available_epoch AS BIGINT)
             THEN 1 ELSE 0 END) AS missing_feature_time,
    SUM(CASE WHEN TRY_CAST(feature_available_epoch AS BIGINT) > TRY_CAST(event_start_epoch AS BIGINT)
             THEN 1 ELSE 0 END) AS features_after_start,
    SUM(CASE WHEN quote_captured_epoch IS NOT NULL THEN 1 ELSE 0 END) AS quoted_rows,
    SUM(CASE WHEN quote_captured_epoch IS NOT NULL
                  AND (TRY_CAST(quote_captured_epoch AS BIGINT) IS NULL
                       OR TRY_CAST(quote_captured_epoch AS BIGINT) <= 0
                       OR TRY_CAST(quote_captured_epoch AS DOUBLE) <> TRY_CAST(quote_captured_epoch AS BIGINT)
                       OR TRY_CAST(quote_captured_epoch AS BIGINT) > TRY_CAST(event_start_epoch AS BIGINT))
             THEN 1 ELSE 0 END) AS invalid_quote_time
FROM read_parquet(?)
"""
_METRICS = (
    "row_count", "distinct_observations", "missing_identity", "wrong_partition",
    "invalid_event_time", "missing_feature_time", "features_after_start",
    "quoted_rows", "invalid_quote_time",
)


class PreflightHold(Exception):
    """A typed offline HOLD; never an authorization or fitted-model result."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise PreflightHold(code)


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PreflightHold("COLUMNAR_MANIFEST_INVALID") from exc
    _require(isinstance(data, dict) and set(data) == _REQUIRED_MANIFEST,
             "COLUMNAR_MANIFEST_INVALID")
    _require(data["schema"] == SCHEMA and data["can_execute"] is False,
             "COLUMNAR_MANIFEST_INVALID")
    for field in ("dataset_id", "sport"):
        _require(isinstance(data[field], str) and bool(_IDENTIFIER.fullmatch(data[field])),
                 "COLUMNAR_MANIFEST_INVALID")
    _require(isinstance(data["season"], int) and not isinstance(data["season"], bool)
             and 1900 <= data["season"] <= 2200, "COLUMNAR_MANIFEST_INVALID")
    files = data["files"]
    _require(isinstance(files, list) and bool(files), "COLUMNAR_MANIFEST_INVALID")
    paths = []
    for entry in files:
        _require(isinstance(entry, dict) and set(entry) == _REQUIRED_FILE,
                 "COLUMNAR_MANIFEST_INVALID")
        rel, digest = entry["path"], entry["sha256"]
        _require(isinstance(rel, str) and rel and isinstance(digest, str)
                 and bool(_SHA256.fullmatch(digest)), "COLUMNAR_MANIFEST_INVALID")
        candidate = Path(rel)
        _require(not candidate.is_absolute() and ".." not in candidate.parts
                 and candidate.suffix == ".parquet", "COLUMNAR_MANIFEST_INVALID")
        paths.append(rel)
    _require(paths == sorted(set(paths)), "COLUMNAR_MANIFEST_INVALID")
    return data


def _verified_paths(root: Path, entries: Sequence[Mapping[str, str]]) -> list[str]:
    _require(root.is_dir(), "COLUMNAR_SOURCE_UNAVAILABLE")
    base = root.resolve(strict=True)
    result: list[str] = []
    for item in entries:
        candidate = root / item["path"]
        try:
            # Deny symlinks in every ancestor, including the leaf.
            relative = Path(item["path"])
            cursor = root
            _require(not root.is_symlink(), "COLUMNAR_SOURCE_UNAVAILABLE")
            for part in relative.parts:
                cursor = cursor / part
                _require(not cursor.is_symlink(), "COLUMNAR_SOURCE_UNAVAILABLE")
            path = candidate.resolve(strict=True)
            _require(path.is_file() and path.is_relative_to(base), "COLUMNAR_SOURCE_UNAVAILABLE")
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        except (OSError, RuntimeError) as exc:
            raise PreflightHold("COLUMNAR_SOURCE_UNAVAILABLE") from exc
        _require(digest.hexdigest() == item["sha256"], "COLUMNAR_SOURCE_HASH_MISMATCH")
        result.append(str(path))
    return result


def _duckdb_scan(paths: Sequence[str], sport: str, season: int) -> dict[str, int]:
    try:
        duckdb = importlib.import_module("duckdb")
    except ImportError as exc:
        raise PreflightHold("COLUMNAR_DUCKDB_UNAVAILABLE") from exc
    connection = duckdb.connect(database=":memory:")
    try:
        # Parameter binding prevents file paths or sport values becoming SQL.
        values = connection.execute(_QUERY, [sport, str(season), list(paths)]).fetchone()
    finally:
        connection.close()
    _require(values is not None and len(values) == len(_METRICS), "COLUMNAR_SCAN_INVALID")
    try:
        metrics = {name: int(value or 0) for name, value in zip(_METRICS, values)}
    except (TypeError, ValueError, OverflowError) as exc:
        raise PreflightHold("COLUMNAR_SCAN_INVALID") from exc
    _require(all(value >= 0 for value in metrics.values()), "COLUMNAR_SCAN_INVALID")
    return metrics


def audit(
    manifest: Path, root: Path,
    *, scanner: Callable[[Sequence[str], str, int], dict[str, int]] = _duckdb_scan,
) -> dict[str, Any]:
    """Return corpus-integrity evidence only, never model or training approval."""
    source = _read_manifest(manifest)
    paths = _verified_paths(root, source["files"])
    started = time.perf_counter()
    try:
        metrics = scanner(paths, source["sport"], source["season"])
    except PreflightHold:
        raise
    except Exception as exc:
        raise PreflightHold("COLUMNAR_SCAN_INVALID") from exc
    # Close the hash-then-scan TOCTOU window before accepting evidence.
    _verified_paths(root, source["files"])
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    _require(isinstance(metrics, dict) and set(metrics) == set(_METRICS), "COLUMNAR_SCAN_INVALID")
    _require(all(isinstance(v, int) and not isinstance(v, bool) and v >= 0
                 for v in metrics.values()), "COLUMNAR_SCAN_INVALID")
    _require(metrics["row_count"] > 0, "COLUMNAR_EMPTY_CORPUS")
    _require(metrics["distinct_observations"] <= metrics["row_count"]
             and metrics["quoted_rows"] <= metrics["row_count"], "COLUMNAR_SCAN_INVALID")
    _require(metrics["row_count"] == metrics["distinct_observations"]
             and not any(metrics[key] for key in (
                 "missing_identity", "wrong_partition", "invalid_event_time",
                 "missing_feature_time", "features_after_start", "invalid_quote_time",
             )), "COLUMNAR_INTEGRITY_HOLD")
    digest = hashlib.sha256(json.dumps(source, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        "schema": SCHEMA,
        "status": "DATA_PREFLIGHT_PASS_ONLY",
        "dataset_id": source["dataset_id"],
        "sport": source["sport"],
        "season": source["season"],
        "manifest_sha256": digest,
        "file_count": len(paths),
        "metrics": metrics,
        "duckdb_scan_ms": elapsed_ms,
        "training_authorized": False,
        "model_fitted": False,
        "certification_eligible": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only frozen Parquet corpus audit")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = audit(args.manifest, args.root)
    except PreflightHold as exc:
        print(json.dumps({"schema": SCHEMA, "status": "HOLD", "reason": exc.code,
                          "training_authorized": False, "model_fitted": False,
                          "probability_publishable": False, "can_execute": False}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
