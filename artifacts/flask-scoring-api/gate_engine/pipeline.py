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
from . import data_contract, source_grade, role_timestamp as role_ts_mod
from . import prob_ledger, failure_path, payout_context
from . import directional_exposure
from . import sharp_anchor, house_rules, settlement_loopback
from .labels import PropLabel
from .exposure_gate import ExposureLedger


def run_pipeline(
    raw_rows: list[dict[str, Any]],
    target_date: date | None = None,
    enrichment: dict[str, dict[str, Any]] | None = None,
    record_entries: bool = False,
    skip_health_gate: bool = False,
    skip_data_contract: bool = False,
    skip_settlement_check: bool = False,
) -> dict[str, Any]:
    """
    Run the full gate engine pipeline.

    Args:
        raw_rows             — raw board rows (PrizePicks export, API pull, paste)
        target_date          — slate date to validate against (default: today UTC)
        enrichment           — dict keyed by row_id or player+prop with:
                                 game_log, season_log, status_payload,
                                 sportsbook_line, best_available, consensus_line,
                                 clv_entry_price, closing_price,
                                 sharp_no_vig_prob, sharp_fair_line,
                                 house_rules (dict)
        record_entries       — if True, write tracker ENTRY records
        skip_settlement_check — if True, skip settlement loopback freshness gate

    Returns:
        {
          prop_ledger         list[dict]   — all rows with gate results
          data_status_ledger  list[dict]   — data status per row
          terminal_labels     list[dict]   — {row_id, label, blockers}
          final_card          list[dict]   — rows that reached FINAL_APPROVED
          exposure_report     dict         — exposure snapshot
          clv_table           list[dict]   — CLV tracking per row
          summary             dict         — counts by label
          settlement_status   dict         — loopback freshness result
        }
    """
    enrichment = enrichment or {}

    rows = board_intake.normalize_board(raw_rows)

    # -------------------------------------------------------------------
    # Layer 0.4: Settlement Loopback Freshness
    # Checks whether the calibration ledger has been updated within 18h.
    # Stale ledger caps all rows at MODEL_QUALIFIED_HOLD after classifier.
    # -------------------------------------------------------------------
    settlement_status: dict[str, Any] = {"stale": False}
    if not skip_settlement_check:
        try:
            settlement_status = settlement_loopback.check_freshness()
        except Exception:
            settlement_status = {
                "stale": False,
                "code": "SETTLEMENT_CHECK_ERROR",
                "detail": "Settlement freshness check failed — no constraint applied.",
            }

    settlement_stale = settlement_status.get("stale", False)

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
    session_exposure = directional_exposure.SessionExposureLedger()

    for row in rows:
        if row.get("terminal_label") is not None:
            continue

        rid = row["row_id"]
        enr = _get_enrichment(enrichment, row)

        # -------------------------------------------------------------------
        # Module B: Data Contract Enforcement
        # -------------------------------------------------------------------
        if not skip_data_contract:
            data_contract.run(row, enrichment=enr)
            if row.get("terminal_label") == PropLabel.DATA_CONTRACT_FAIL.value:
                continue

        # -------------------------------------------------------------------
        # Module H: Source Timestamp Grading
        # -------------------------------------------------------------------
        source_grade.run(row, enrichment=enr)
        if row.get("terminal_label") in (PropLabel.SOURCE_CONFLICT.value,):
            continue

        # -------------------------------------------------------------------
        # Layer 0 / Module E: Reality Verification + Role Timestamp
        # -------------------------------------------------------------------
        slate_validation.run(row, target_date=target_date)
        if row.get("terminal_label"):
            continue

        role_ts_mod.run(row, enrichment=enr)

        status_role.run(row, status_payload=enr.get("status_payload"))

        # -------------------------------------------------------------------
        # Patch 2026-06-27 — House Rules Matrix
        # Runs after status_role so injury/role signals are available.
        # Only fires when enrichment provides house_rules data.
        # -------------------------------------------------------------------
        if enr.get("house_rules"):
            house_rules.run(row, enrichment=enr)
            if row.get("terminal_label") == PropLabel.REJECT_HOUSE_RULES_VULNERABILITY.value:
                continue

        # -------------------------------------------------------------------
        # Layers 1–2: Data Intake + Adjustments
        # -------------------------------------------------------------------
        l5_l10_ledger.run(
            row,
            game_log=enr.get("game_log"),
            season_log=enr.get("season_log"),
        )

        outlier_gate.run(row)

        market_gate.run(
            row,
            sportsbook_line = enr.get("sportsbook_line"),
            best_available  = enr.get("best_available"),
            consensus_line  = enr.get("consensus_line"),
            clv_entry_price = enr.get("clv_entry_price"),
            closing_price   = enr.get("closing_price"),
        )

        # -------------------------------------------------------------------
        # Patch 2026-06-27 — Sharp Market Anchor (Directional)
        # Only fires when enrichment provides sharp_no_vig_prob or sharp_fair_line.
        # PrizePicks/DFS lines are target markets — sharp books are the reference.
        # -------------------------------------------------------------------
        if enr.get("sharp_no_vig_prob") is not None or enr.get("sharp_fair_line") is not None:
            sharp_anchor.run(
                row,
                sharp_no_vig_prob = enr.get("sharp_no_vig_prob"),
                sharp_fair_line   = enr.get("sharp_fair_line"),
            )
            if row.get("terminal_label") in (
                PropLabel.REJECT_SHARP_CONFLICT.value,
                PropLabel.REJECT_FALLING_KNIFE.value,
            ):
                continue

        ev_gate.run(row)

        # -------------------------------------------------------------------
        # Module D: Probability Component Ledger + Shrinkage
        # -------------------------------------------------------------------
        prob_ledger.run(row, enrichment=enr)

        # -------------------------------------------------------------------
        # Module F: Failure Path Matrix
        # -------------------------------------------------------------------
        if not skip_data_contract:
            failure_path.run(row, enrichment=enr)
            if row.get("terminal_label") == PropLabel.DATA_CONTRACT_FAIL.value:
                continue

        # -------------------------------------------------------------------
        # Module C: Payout Context / Slip EV
        # -------------------------------------------------------------------
        payout_context.run(row, enrichment=enr)

        # -------------------------------------------------------------------
        # Module G: Directional Exposure
        # -------------------------------------------------------------------
        directional_exposure.run(row, session_ledger=session_exposure)

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

    # -------------------------------------------------------------------
    # Patch 2026-06-27 — Settlement Loopback ceiling enforcement
    # After classifier: if ledger is stale, downgrade FINAL_APPROVED → MODEL_QUALIFIED_HOLD
    # -------------------------------------------------------------------
    if settlement_stale:
        settlement_loopback.apply_stale_ceiling_to_output(rows, stale=True)

    if record_entries:
        for row in rows:
            if row.get("terminal_label") == PropLabel.FINAL_APPROVED.value:
                tracker.record_entry(row)

    return _build_output(rows, ledger, health_report, settlement_status)


def _build_output(rows: list[dict], ledger: ExposureLedger,
                  health_report: dict | None = None,
                  settlement_status: dict | None = None) -> dict[str, Any]:
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
                "sharp_anchor": row.get("gates", {}).get("sharp_anchor", {}).get("anchor_status"),
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
        "settlement_status":  settlement_status or {},
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
