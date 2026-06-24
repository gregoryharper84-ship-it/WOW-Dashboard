"""
tracker.py
Track result, CLV, line movement, and bucket performance after games finish.
Provides postmortem auto-fill and historical ledger for accountability.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any


TRACKER_LOG_PATH = os.environ.get(
    "GATE_ENGINE_TRACKER_PATH",
    "/tmp/gate_engine_tracker.jsonl"
)


def record_entry(row: dict[str, Any]) -> dict[str, Any]:
    """
    Write an entry-time snapshot to the tracker ledger.
    Call this when the slip is submitted (before game starts).
    """
    entry = {
        "event":           "ENTRY",
        "ts":              datetime.now(timezone.utc).isoformat(),
        "row_id":          row.get("row_id"),
        "player":          row.get("player"),
        "sport":           row.get("sport"),
        "prop_type":       row.get("prop_type"),
        "line":            row.get("line"),
        "direction":       row.get("direction"),
        "terminal_label":  row.get("terminal_label"),
        "entry_line":      row.get("line"),
        "market_line":     row.get("gates", {}).get("market_gate", {}).get("sportsbook_line"),
        "clv_entry":       row.get("gates", {}).get("market_gate", {}).get("clv_entry"),
        "delta":           row.get("gates", {}).get("market_gate", {}).get("delta"),
        "edge_score":      row.get("gates", {}).get("ev_gate", {}).get("edge_score"),
        "result":          None,
        "hit":             None,
        "closing_price":   None,
        "clv_status":      None,
        "postmortem":      None,
    }
    _append(entry)
    return entry


def record_result(row_id: str, final_stat: float, closing_price: float | None = None,
                  notes: str | None = None) -> dict[str, Any]:
    """
    Call after the game finishes with the actual stat.
    Reads entry record, computes hit/miss, writes result record.
    """
    entry_record = _find_entry(row_id)
    if entry_record is None:
        result = {
            "event":   "RESULT_ERROR",
            "ts":      datetime.now(timezone.utc).isoformat(),
            "row_id":  row_id,
            "error":   "NO_ENTRY_RECORD_FOUND",
        }
        _append(result)
        return result

    line      = entry_record.get("line")
    direction = (entry_record.get("direction") or "MORE").upper()
    clv_entry = entry_record.get("clv_entry")

    hit: bool | None = None
    if line is not None:
        if direction in ("MORE", "OVER"):
            hit = final_stat > line
        else:
            hit = final_stat < line

    clv_status: str | None = None
    if clv_entry is not None and closing_price is not None:
        clv_status = "CLV_BEAT" if clv_entry < closing_price else "CLV_MISS"

    result = {
        "event":          "RESULT",
        "ts":             datetime.now(timezone.utc).isoformat(),
        "row_id":         row_id,
        "player":         entry_record.get("player"),
        "sport":          entry_record.get("sport"),
        "prop_type":      entry_record.get("prop_type"),
        "line":           line,
        "direction":      direction,
        "terminal_label": entry_record.get("terminal_label"),
        "entry_line":     entry_record.get("entry_line"),
        "market_line":    entry_record.get("market_line"),
        "final_stat":     final_stat,
        "hit":            hit,
        "closing_price":  closing_price,
        "clv_entry":      clv_entry,
        "clv_status":     clv_status,
        "delta":          entry_record.get("delta"),
        "edge_score":     entry_record.get("edge_score"),
        "notes":          notes,
    }
    _append(result)
    return result


def get_bucket_performance() -> dict[str, Any]:
    """Aggregate hit rates by terminal_label bucket."""
    records = _load_results()
    buckets: dict[str, dict[str, int]] = {}
    for r in records:
        if r.get("event") != "RESULT":
            continue
        label = r.get("terminal_label") or "UNKNOWN"
        if label not in buckets:
            buckets[label] = {"total": 0, "hits": 0, "misses": 0, "pushes": 0}
        hit = r.get("hit")
        buckets[label]["total"] += 1
        if hit is True:
            buckets[label]["hits"] += 1
        elif hit is False:
            buckets[label]["misses"] += 1
        else:
            buckets[label]["pushes"] += 1

    out = {}
    for label, counts in buckets.items():
        total = counts["total"]
        out[label] = {
            **counts,
            "hit_rate": round(counts["hits"] / total, 3) if total else None,
        }
    return out


def _append(record: dict) -> None:
    try:
        with open(TRACKER_LOG_PATH, "a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        pass


def _load_results() -> list[dict]:
    try:
        with open(TRACKER_LOG_PATH) as f:
            return [json.loads(line) for line in f if line.strip()]
    except (OSError, json.JSONDecodeError):
        return []


def _find_entry(row_id: str) -> dict | None:
    for r in _load_results():
        if r.get("event") == "ENTRY" and r.get("row_id") == row_id:
            return r
    return None
