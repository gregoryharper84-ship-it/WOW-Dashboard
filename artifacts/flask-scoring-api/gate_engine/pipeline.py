"""
pipeline.py
Orchestrates the full gate engine pipeline for a board of prop rows.
Every input row appears in the output — no drops, no fake-fills.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from . import board_intake, slate_validation, status_role
from . import l5_l10_ledger, outlier_gate, market_gate, ev_gate
from . import slip_structure, exposure_gate, classifier, tracker
from . import calibration_health
from .labels import PropLabel
from .exposure_gate import ExposureLedger


def run_pipeline(
    raw_rows: list[dict[str, Any]],
    target_date: date | None = None,
    enrichment: dict[str, dict[str, Any]] | None = None,
    record_entries: bool = False,
    skip_health_gate: bool = False,
) -> dict[str, Any]:
    """
    Run the full gate engine pipeline.

    Args:
        raw_rows      — raw board rows (PrizePicks export, API pull, paste)
        target_date   — slate date to validate against (default: today UTC)
        enrichment    — dict keyed by row_id or player+prop with:
                          game_log, season_log, status_payload,
                          sportsbook_line, best_available, consensus_line,
                          clv_entry_price, closing_price
        record_entries — if True, write tracker ENTRY records

    Returns:
        {
          prop_ledger       list[dict]   — all rows with gate results
          data_status_ledger list[dict]  — data status per row
          terminal_labels   list[dict]   — {row_id, label, blockers}
          final_card        list[dict]   — rows that reached FINAL_APPROVED
          exposure_report   dict         — exposure snapshot
          clv_table         list[dict]   — CLV tracking per row
          summary           dict         — counts by label
        }
    """
    enrichment = enrichment or {}

    rows = board_intake.normalize_board(raw_rows)

    # -------------------------------------------------------------------
    # Layer 0.5: Calibration Health Gate
    # Runs before all other gates. Checks historical failure patterns
    # using blended CLV + result signals. A SUPPRESS grade caps the row
    # at LLP_REJECT before any analysis begins.
    # -------------------------------------------------------------------
    health_report: dict[str, Any] = {}
    if not skip_health_gate:
        for row in rows:
            if row.get("terminal_label") is not None:
                continue
            enr_pre = _get_enrichment(enrichment, row)
            health_candidate = {
                "sport":        row.get("sport"),
                "market":       row.get("prop_type"),
                "player":       row.get("player"),
                "failure_tags": enr_pre.get("failure_tags") or [],
            }
            health_result = calibration_health.validate_calibration_health(health_candidate)
            row["gates"]["calibration_health"] = health_result
            if not health_result["passed"]:
                row["blockers"].append(
                    f"CALIBRATION_HEALTH:{health_result['code']}:"
                    f"grade={health_result['grade']}"
                )
            health_report[row.get("row_id", "")] = {
                "grade":   health_result["grade"],
                "ceiling": health_result["ceiling"],
                "code":    health_result["code"],
            }

    ledger = ExposureLedger()

    for row in rows:
        if row.get("terminal_label") is not None:
            continue

        rid  = row["row_id"]
        enr  = _get_enrichment(enrichment, row)

        slate_validation.run(row, target_date=target_date)
        if row.get("terminal_label"):
            continue

        status_role.run(row, status_payload=enr.get("status_payload"))

        l5_l10_ledger.run(
            row,
            game_log=enr.get("game_log"),
            season_log=enr.get("season_log"),
        )

        outlier_gate.run(row)

        market_gate.run(
            row,
            sportsbook_line  = enr.get("sportsbook_line"),
            best_available   = enr.get("best_available"),
            consensus_line   = enr.get("consensus_line"),
            clv_entry_price  = enr.get("clv_entry_price"),
            closing_price    = enr.get("closing_price"),
        )

        ev_gate.run(row)

        slip_structure.run_single(row)

    slip_structure.run_slip(rows)

    for row in rows:
        if row.get("terminal_label") in (
            PropLabel.SLATE_PURGE.value,
            PropLabel.REJECT_DATA_QUALITY.value,
            PropLabel.SOURCE_CONFLICT.value,
        ):
            continue
        ledger.check_and_register(row)

    for row in rows:
        classifier.classify(row)

    if record_entries:
        for row in rows:
            if row.get("terminal_label") == PropLabel.FINAL_APPROVED.value:
                tracker.record_entry(row)

    return _build_output(rows, ledger, health_report)


def _build_output(rows: list[dict], ledger: ExposureLedger,
                  health_report: dict | None = None) -> dict[str, Any]:
    label_counts: dict[str, int] = {}
    terminal_labels  = []
    final_card       = []
    clv_table        = []
    data_status_ledger = []

    for row in rows:
        label = row.get("terminal_label") or PropLabel.NO_PLAY.value
        label_counts[label] = label_counts.get(label, 0) + 1

        terminal_labels.append({
            "row_id":   row["row_id"],
            "player":   row.get("player"),
            "prop":     row.get("prop_type"),
            "line":     row.get("line"),
            "direction": row.get("direction"),
            "label":    label,
            "blockers": row.get("blockers", []),
        })

        data_status_ledger.append({
            "row_id":      row["row_id"],
            "player":      row.get("player"),
            "data_status": row.get("data_status"),
            "intake_errors": row.get("intake_errors", []),
        })

        if label == PropLabel.FINAL_APPROVED.value:
            final_card.append({
                "row_id":    row["row_id"],
                "player":    row.get("player"),
                "sport":     row.get("sport"),
                "prop_type": row.get("prop_type"),
                "line":      row.get("line"),
                "direction": row.get("direction"),
                "edge_score": row.get("gates", {}).get("ev_gate", {}).get("edge_score"),
                "market_status": row.get("gates", {}).get("market_gate", {}).get("market_status"),
            })

        mkt = row.get("gates", {}).get("market_gate", {})
        if mkt.get("clv_entry") or mkt.get("clv_status"):
            clv_table.append({
                "row_id":       row["row_id"],
                "player":       row.get("player"),
                "prop_type":    row.get("prop_type"),
                "label":        label,
                "clv_entry":    mkt.get("clv_entry"),
                "closing_price": mkt.get("closing_price"),
                "clv_status":   mkt.get("clv_status"),
                "delta":        mkt.get("delta"),
            })

    no_play = len(final_card) == 0

    return {
        "prop_ledger":        rows,
        "data_status_ledger": data_status_ledger,
        "terminal_labels":    terminal_labels,
        "final_card":         final_card,
        "exposure_report":    ledger.snapshot(),
        "clv_table":          clv_table,
        "health_report":      health_report or {},
        "summary": {
            "total_rows":   len(rows),
            "by_label":     label_counts,
            "final_count":  len(final_card),
            "no_play":      no_play,
        },
    }


def _get_enrichment(enrichment: dict, row: dict) -> dict:
    rid = row.get("row_id", "")
    player = (row.get("player") or "").lower()
    prop   = (row.get("prop_type") or "").lower()
    key = f"{player}:{prop}"

    return enrichment.get(rid) or enrichment.get(key) or {}
