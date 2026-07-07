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
from . import js_style_conversion
from . import pp_thresholds, mutex_groups
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
    enrichment    = enrichment or {}
    failed_modules: list[str] = []

    rows = board_intake.normalize_board(raw_rows)

    # -------------------------------------------------------------------
    # Phase 1: PP Threshold Conversion (whole-number push rules)
    # Must run before any gate that compares a sportsbook line to the
    # displayed PrizePicks line, so cash_threshold is available.
    # -------------------------------------------------------------------
    pp_threshold_ledger = pp_thresholds.run_batch(rows)

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
        # DEGRADED_ENGINE_RUN: wrap critical data-source modules so a
        # ClientResponseError or fetch failure on one row doesn't crash
        # the entire run. Failures are logged in failed_modules; the row
        # is capped at REJECT_DATA_QUALITY so it never reaches approvals.
        # -------------------------------------------------------------------
        try:
            l5_l10_ledger.run(
                row,
                game_log=enr.get("game_log"),
                season_log=enr.get("season_log"),
            )
        except Exception as _exc:
            _tag = f"l5_l10_ledger:{type(_exc).__name__}:{str(_exc)[:100]}"
            failed_modules.append(_tag)
            row.setdefault("blockers", []).append(f"MODULE_FAILURE:l5_l10_ledger")
            if row.get("terminal_label") is None:
                row["terminal_label"] = PropLabel.REJECT_DATA_QUALITY.value

        outlier_gate.run(row)

        try:
            market_gate.run(
                row,
                sportsbook_line = enr.get("sportsbook_line"),
                best_available  = enr.get("best_available"),
                consensus_line  = enr.get("consensus_line"),
                clv_entry_price = enr.get("clv_entry_price"),
                closing_price   = enr.get("closing_price"),
            )
        except Exception as _exc:
            _tag = f"market_gate:{type(_exc).__name__}:{str(_exc)[:100]}"
            failed_modules.append(_tag)
            row.setdefault("blockers", []).append("MODULE_FAILURE:market_gate")
            if row.get("terminal_label") is None:
                row["terminal_label"] = PropLabel.REJECT_DATA_QUALITY.value

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

        try:
            ev_gate.run(row)
        except Exception as _exc:
            _tag = f"ev_gate:{type(_exc).__name__}:{str(_exc)[:100]}"
            failed_modules.append(_tag)
            row.setdefault("blockers", []).append("MODULE_FAILURE:ev_gate")
            if row.get("terminal_label") is None:
                row["terminal_label"] = PropLabel.REJECT_DATA_QUALITY.value

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

        # -------------------------------------------------------------------
        # WOW-PATCH-2026-07-07 — JS Style Conversion Layer
        # Runs after projection/cushion data is available (L5/L10 complete)
        # and before slip_builder / final_approval.
        # -------------------------------------------------------------------
        js_style_conversion.run(row, enrichment=enr)

        slip_structure.run_single(row)

    slip_structure.run_slip(rows)

    # WOW-PATCH-2026-07-07 — JS Style slip-level gate (same-game PRA cluster, etc.)
    js_style_conversion.run_slip(rows)

    # -------------------------------------------------------------------
    # Phase 1: Mutex Grouping + Best-Candidate Selection
    # Runs after ev_gate (edge scores available) and before classifier
    # so rejected mutex candidates get DUPLICATE_EXPOSURE_BLOCK before
    # the classifier counts buckets.
    # Same-player/same-stat-family and same-pitcher-script conflicts are
    # resolved here. Only the best candidate per group survives.
    # -------------------------------------------------------------------
    mutex_report = mutex_groups.run(rows)

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

    run_status = "DEGRADED_ENGINE_RUN" if failed_modules else "COMPLETE"

    return _build_output(
        rows, ledger,
        health_report      = health_report,
        settlement_status  = settlement_status,
        enrichment         = enrichment,
        failed_modules     = failed_modules,
        run_status         = run_status,
        pp_threshold_ledger = pp_threshold_ledger,
        mutex_report       = mutex_report,
    )


MARKET_NO_DATA_BLOCKER = "MARKET:NO_MARKET_AVAILABLE:MAX_LABEL=MODEL_QUALIFIED_HOLD"
MARKET_ENRICHMENT_FIELDS = ("sportsbook_line", "best_available", "consensus_line")


def _build_market_enrichment_report(rows: list[dict]) -> dict[str, Any]:
    """
    Diagnostic-only coverage report: tells us whether MODEL_QUALIFIED_HOLD
    caps are coming from a genuinely absent market (upstream caller never
    sent sportsbook_line/best_available/consensus_line) vs. some other
    cause. Does not affect classification — read-only over gate results.
    """
    total_rows = len(rows)
    rows_with_any_field = 0
    rows_all_missing = 0
    rows_capped_no_market = 0
    rows_without_market_gate_result = 0
    blocker_samples_by_prop: dict[str, list[dict[str, Any]]] = {}

    for row in rows:
        if not isinstance(row, dict):
            rows_without_market_gate_result += 1
            continue

        mkt = (row.get("gates") or {}).get("market_gate")
        if not mkt or not isinstance(mkt, dict):
            rows_without_market_gate_result += 1
            continue

        has_field = any(
            mkt.get(k) not in (None, "") for k in MARKET_ENRICHMENT_FIELDS
        )
        if has_field:
            rows_with_any_field += 1
        else:
            rows_all_missing += 1

        blockers = row.get("blockers") or []
        if MARKET_NO_DATA_BLOCKER in blockers:
            rows_capped_no_market += 1
            prop = row.get("prop_type") or "UNKNOWN"
            samples = blocker_samples_by_prop.setdefault(prop, [])
            if len(samples) < 3:
                samples.append({
                    "row_id":   row.get("row_id"),
                    "player":   row.get("player"),
                    "line":     row.get("line"),
                    "direction": row.get("direction"),
                    "blockers": blockers,
                })

    return {
        "total_rows":                            total_rows,
        "rows_with_any_market_field":             rows_with_any_field,
        "rows_with_all_market_fields_missing":    rows_all_missing,
        "rows_capped_model_qualified_hold_no_market": rows_capped_no_market,
        "rows_without_market_gate_result":        rows_without_market_gate_result,
        "blocker_samples_by_prop":                blocker_samples_by_prop,
    }


# -------------------------------------------------------------------
# WOW-PATCH-2026-07-04-MARKET-JOIN-AUDIT
# Per-row diagnostics explaining *why* market enrichment did or did not
# attach to a given prop row. Observability only — never alters
# classification, thresholds, or terminal labels.
# -------------------------------------------------------------------
JOIN_STATUS_JOINED               = "JOINED"
JOIN_STATUS_NO_MARKET_FOUND      = "NO_MARKET_FOUND"
JOIN_STATUS_SOURCE_NOT_CALLED    = "SOURCE_NOT_CALLED"
JOIN_STATUS_SOURCE_FAILED        = "SOURCE_FAILED"
JOIN_STATUS_JOIN_KEY_MISMATCH    = "JOIN_KEY_MISMATCH"
JOIN_STATUS_PROP_MAPPING_UNSUPPORTED = "PROP_MAPPING_UNSUPPORTED"
JOIN_STATUS_MARKET_FILTERED_OUT  = "MARKET_FILTERED_OUT"
JOIN_STATUS_SCHEMA_MISSING_FIELD = "SCHEMA_MISSING_FIELD"
JOIN_STATUS_UNKNOWN              = "UNKNOWN"

_JOIN_MARKET_FIELDS = ("sportsbook_line", "best_available", "consensus_line")


def _present(value: Any) -> bool:
    return value not in (None, "")


def _build_market_join_audit(row: dict, enrichment: dict) -> dict[str, Any]:
    """
    Per-row market join audit — approved fields:
      market_join_status, market_source_called, matching_market_found,
      sportsbook_line_present, consensus_line_present, best_available_present,
      odds_join_key, prop_join_key, market_rejection_reason

    Computed from the enrichment payload actually supplied by the caller
    for this row, not from classification results — this never upgrades
    or downgrades a row, it only explains the market_gate input.
    """
    if not isinstance(row, dict):
        return {
            "market_join_status":      JOIN_STATUS_UNKNOWN,
            "market_source_called":    False,
            "matching_market_found":   None,
            "sportsbook_line_present": None,
            "consensus_line_present":  None,
            "best_available_present":  None,
            "odds_join_key":           None,
            "prop_join_key":           None,
            "market_rejection_reason": JOIN_STATUS_UNKNOWN,
        }

    rid = row.get("row_id", "")
    player = (row.get("player") or "").lower()
    prop   = (row.get("prop_type") or "").lower()
    prop_join_key = f"{player}:{prop}"
    odds_join_key = rid

    enrichment = enrichment if isinstance(enrichment, dict) else {}
    enr_by_rid = enrichment.get(rid) if rid else None
    enr_by_key = enrichment.get(prop_join_key)
    enr_for_row = enr_by_rid if isinstance(enr_by_rid, dict) else (
        enr_by_key if isinstance(enr_by_key, dict) else None
    )
    source_called = enr_for_row is not None

    sportsbook_line_present: bool | None = None
    consensus_line_present:  bool | None = None
    best_available_present:  bool | None = None
    matching_market_found:   bool | None = None

    # Prefer the actual market_gate result when the row reached that gate —
    # it also accounts for a row-level `market_line` override, not just
    # caller-supplied enrichment.
    mkt = (row.get("gates") or {}).get("market_gate")
    if isinstance(mkt, dict):
        sportsbook_line_present = _present(mkt.get("sportsbook_line"))
        consensus_line_present  = _present(mkt.get("consensus_line"))
        best_available_present  = _present(mkt.get("best_available"))
        matching_market_found   = mkt.get("market_status") != market_gate.MARKET_STATUS_NONE
    elif source_called:
        sportsbook_line_present = _present(enr_for_row.get("sportsbook_line"))
        consensus_line_present  = _present(enr_for_row.get("consensus_line"))
        best_available_present  = _present(enr_for_row.get("best_available"))
        matching_market_found   = (
            sportsbook_line_present or consensus_line_present or best_available_present
        )

    if matching_market_found:
        market_join_status = JOIN_STATUS_JOINED
        market_rejection_reason = None
    elif source_called:
        market_join_status = JOIN_STATUS_NO_MARKET_FOUND
        market_rejection_reason = market_join_status
    elif enrichment:
        # Caller supplied an enrichment payload for the batch, but nothing
        # in it matched this row's row_id or player:prop key.
        market_join_status = JOIN_STATUS_JOIN_KEY_MISMATCH
        market_rejection_reason = market_join_status
    else:
        # No enrichment payload was supplied for the whole batch at all.
        market_join_status = JOIN_STATUS_SOURCE_NOT_CALLED
        market_rejection_reason = market_join_status

    return {
        "market_join_status":      market_join_status,
        "market_source_called":    source_called,
        "matching_market_found":   matching_market_found,
        "sportsbook_line_present": sportsbook_line_present,
        "consensus_line_present":  consensus_line_present,
        "best_available_present":  best_available_present,
        "odds_join_key":           odds_join_key,
        "prop_join_key":           prop_join_key,
        "market_rejection_reason": market_rejection_reason,
    }


def _build_output(rows: list[dict], ledger: ExposureLedger,
                  health_report: dict | None = None,
                  settlement_status: dict | None = None,
                  enrichment: dict | None = None,
                  failed_modules: list[str] | None = None,
                  run_status: str = "COMPLETE",
                  pp_threshold_ledger: list[dict] | None = None,
                  mutex_report: list[dict] | None = None) -> dict[str, Any]:
    # -------------------------------------------------------------------
    # DEGRADED_ENGINE_RUN ceiling: applied here (not only in run_pipeline)
    # so that _build_output works correctly when called directly in tests.
    # If any critical module failed, no row may carry FINAL_APPROVED or
    # MONEY_QUALIFIED — downgrade both to MODEL_QUALIFIED_HOLD.
    # -------------------------------------------------------------------
    if run_status == "DEGRADED_ENGINE_RUN":
        for row in rows:
            _lbl = row.get("terminal_label") or ""
            if _lbl in (PropLabel.FINAL_APPROVED.value, PropLabel.MONEY_QUALIFIED.value):
                row["terminal_label"] = PropLabel.MODEL_QUALIFIED_HOLD.value
                row.setdefault("blockers", []).append("DEGRADED_ENGINE_RUN")

    label_counts: dict[str, int] = {}
    terminal_labels  = []
    final_card       = []
    clv_table        = []
    data_status_ledger = []

    market_join_audits: list[dict[str, Any]] = []

    for row in rows:
        label = row.get("terminal_label") or PropLabel.NO_PLAY.value
        label_counts[label] = label_counts.get(label, 0) + 1

        join_audit = _build_market_join_audit(row, enrichment or {})
        if isinstance(row.get("gates"), dict):
            row["gates"]["market_join_audit"] = join_audit
        market_join_audits.append(join_audit)

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

    market_enrichment_report = _build_market_enrichment_report(rows)

    join_status_counts: dict[str, int] = {}
    rows_market_joined = 0
    for audit in market_join_audits:
        status = audit.get("market_join_status") or JOIN_STATUS_UNKNOWN
        join_status_counts[status] = join_status_counts.get(status, 0) + 1
        if status == JOIN_STATUS_JOINED:
            rows_market_joined += 1

    market_enrichment_report["rows_market_joined"] = rows_market_joined
    market_enrichment_report["rows_by_market_join_status"] = join_status_counts

    # DEGRADED_ENGINE_RUN: re-count after ceiling enforcement so the summary
    # accurately reflects 0 final/money-qualified when the run degraded.
    _degraded = run_status == "DEGRADED_ENGINE_RUN"
    _effective_final_count = 0 if _degraded else len(final_card)

    return {
        "prop_ledger":        rows,
        "data_status_ledger": data_status_ledger,
        "terminal_labels":    terminal_labels,
        "final_card":         final_card,
        "exposure_report":    ledger.snapshot(),
        "clv_table":          clv_table,
        "health_report":      health_report or {},
        "settlement_status":  settlement_status or {},
        "market_enrichment_report": market_enrichment_report,
        # Phase 1 additions
        "run_status":         run_status,
        "failed_modules":     failed_modules or [],
        "pp_threshold_ledger": pp_threshold_ledger or [],
        "mutex_report":       mutex_report or [],
        "summary": {
            "total_rows":          len(rows),
            "by_label":            label_counts,
            "final_count":         _effective_final_count,
            "no_play":             no_play,
            "run_status":          run_status,
            "degraded_run":        _degraded,
            "failed_module_count": len(failed_modules or []),
            "mutex_group_count":   len(mutex_report or []),
        },
    }


def _get_enrichment(enrichment: dict, row: dict) -> dict:
    rid = row.get("row_id", "")
    player = (row.get("player") or "").lower()
    prop   = (row.get("prop_type") or "").lower()
    key = f"{player}:{prop}"

    return enrichment.get(rid) or enrichment.get(key) or {}
