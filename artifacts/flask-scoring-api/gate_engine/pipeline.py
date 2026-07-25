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
from . import injury_decision_tree
from . import event_normalization, settlement_conflict
from . import acquisition as _acq_mod
from .acquisition import AcquisitionTracker, SourceStatus, build_run_acquisition_report
from .labels import PropLabel
from .exposure_gate import ExposureLedger
# WOW-PATCH-2026-07-15 — new gates
from . import market_adverse, component_composite, opportunity_state
from .governance import get_governance_status
# WOW Stage 2 — hard structural gates + weakest-leg finalizer (reviewer-mandated)
from . import card_finalizer, hitter_fantasy_score as _hfs_mod


def run_pipeline(
    raw_rows: list[dict[str, Any]],
    target_date: date | None = None,
    enrichment: dict[str, dict[str, Any]] | None = None,
    record_entries: bool = False,
    skip_health_gate: bool = False,
    skip_data_contract: bool = False,
    skip_settlement_check: bool = False,
    existing_ledger: "ExposureLedger | None" = None,
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

    # Use caller-supplied ledger when available (cross-request session persistence).
    # Fall back to a fresh ledger for standalone calls.
    ledger = existing_ledger if existing_ledger is not None else ExposureLedger()
    session_exposure = directional_exposure.SessionExposureLedger()

    for row in rows:
        if row.get("terminal_label") is not None:
            continue

        rid = row["row_id"]
        enr = _get_enrichment(enrichment, row)

        # -------------------------------------------------------------------
        # WOW-PATCH-MANDATORY-RECONSTRUCTION-v1.0
        # Per-row acquisition tracker — documents every field-level source
        # attempt for run-level Acquisition Execution Report (Section 29.2).
        # -------------------------------------------------------------------
        _tracker = AcquisitionTracker(rid)

        # -------------------------------------------------------------------
        # Module B: Data Contract Enforcement — Phase 1 (Intake)
        # Row-level fields (player, sport, prop_type, line, direction) still
        # fail immediately. Enrichment-level missing fields are noted for
        # the acquisition ladder and do NOT terminate the row here.
        # -------------------------------------------------------------------
        if not skip_data_contract:
            _intake = data_contract.run_intake(row, enrichment=enr)
            if _intake.get("row_level_fail"):
                continue
            if _intake.get("enrichment_missing"):
                _tracker.mark_missing_at_intake(_intake["enrichment_missing"])
                for _f in _intake["enrichment_missing"]:
                    _tracker.record_attempt(
                        _f, "data_contract_intake",
                        SourceStatus.NOT_CALLED,
                        detail="field absent at intake; acquisition ladder will attempt",
                    )

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
        # Phase 3: Injury Decision Tree
        # Runs after status_role so the main player's status is available.
        # dependency_status_payload is a dict of {lowercased_player_name: {status, confirmed_at}}
        # for the teammate(s) / game-script risk this row depends on.
        # -------------------------------------------------------------------
        injury_decision_tree.run(
            row,
            dependency_status_payload=enr.get("dependency_status_payload"),
        )

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
        #
        # WOW-PATCH-MANDATORY-RECONSTRUCTION-v1.0:
        # Module exceptions are recorded in the acquisition tracker as FAILED
        # (Section 9 — Internal Tool Failure Rule). The row is still capped
        # at REJECT_DATA_QUALITY so the existing approval ceiling is preserved;
        # however the tracker distinguishes "module crashed" from "data does
        # not exist" so the run-level report can surface which rows had
        # INPUT_FAILURE — ACQUISITION_NOT_COMPLETED vs DATA_UNOBTAINABLE.
        # -------------------------------------------------------------------
        try:
            l5_l10_ledger.run(
                row,
                game_log=enr.get("game_log"),
                season_log=enr.get("season_log"),
            )
            # Record the source attempt from the ledger result
            _l5_result = (row.get("gates") or {}).get("l5_l10_ledger") or {}
            for _sa in _l5_result.get("source_attempts", []):
                _tracker.record_attempt(
                    "game_log", _sa["source"], _sa["status"],
                    detail=_sa.get("detail", ""),
                )
            for _sa in _l5_result.get("source_attempts", []):
                _tracker.record_attempt(
                    "l5_values", _sa["source"], _sa["status"],
                    detail=_sa.get("detail", ""),
                )
                _tracker.record_attempt(
                    "l10_values", _sa["source"], _sa["status"],
                    detail=_sa.get("detail", ""),
                )
        except Exception as _exc:
            _tag = f"l5_l10_ledger:{type(_exc).__name__}:{str(_exc)[:100]}"
            failed_modules.append(_tag)
            row.setdefault("blockers", []).append(f"MODULE_FAILURE:l5_l10_ledger")
            _tracker.record_attempt(
                "game_log", "l5_l10_ledger_module",
                SourceStatus.FAILED,
                detail=f"{type(_exc).__name__}: {str(_exc)[:80]}",
            )
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
            # Track market acquisition result
            _mkt_result = (row.get("gates") or {}).get("market_gate") or {}
            _mkt_status = (
                SourceStatus.RETRIEVED
                if _mkt_result.get("exact_market_found") or _mkt_result.get("adjacent_market_used")
                else SourceStatus.NOT_CALLED
            )
            _tracker.record_attempt(
                "market_no_vig_probability", "sportsbook_odds_api",
                _mkt_status,
                detail=_mkt_result.get("market_status", ""),
            )
        except Exception as _exc:
            _tag = f"market_gate:{type(_exc).__name__}:{str(_exc)[:100]}"
            failed_modules.append(_tag)
            row.setdefault("blockers", []).append("MODULE_FAILURE:market_gate")
            _tracker.record_attempt(
                "market_no_vig_probability", "market_gate_module",
                SourceStatus.FAILED,
                detail=f"{type(_exc).__name__}: {str(_exc)[:80]}",
            )
            if row.get("terminal_label") is None:
                row["terminal_label"] = PropLabel.REJECT_DATA_QUALITY.value

        # -------------------------------------------------------------------
        # WOW-PATCH-2026-07-15 — Settlement-Aware Market Adverse Gate
        # Runs immediately after market_gate so sportsbook_line/consensus_line
        # are in the same enrichment dict. If the PP line is adverse vs the
        # sportsbook reference (push-loss or threshold), the row is terminated.
        # -------------------------------------------------------------------
        try:
            market_adverse.run(
                row,
                sportsbook_line = enr.get("sportsbook_line"),
                consensus_line  = enr.get("consensus_line"),
                # best_available is intentionally excluded: it reflects the best
                # obtainable price across all books, not the reference consensus.
                # Adversity is measured against the main sportsbook line or
                # consensus, not the most-favorable book in any market.
            )
        except Exception as _exc:
            _tag = f"market_adverse:{type(_exc).__name__}:{str(_exc)[:100]}"
            failed_modules.append(_tag)

        # Source ceiling — prediction-market-only support cannot exceed
        # MARKET_VERIFIED_HOLD / MEDIUM (WOW-PATCH-2026-07-15 Section 3)
        _mkt_source_type = enr.get("market_source_type", "")
        if (
            _mkt_source_type in ("prediction_market", "polymarket", "kalshi_market")
            and not enr.get("sportsbook_line")
            and not enr.get("consensus_line")
        ):
            # Always stamp the gate so callers can audit — then cap if label is
            # above the ceiling (FINAL_APPROVED or MONEY_QUALIFIED).
            _cur_lbl = row.get("terminal_label") or ""
            _above_ceiling = _cur_lbl in (
                PropLabel.FINAL_APPROVED.value,
                PropLabel.MONEY_QUALIFIED.value,
            )
            if _above_ceiling:
                row["terminal_label"] = PropLabel.MARKET_VERIFIED_HOLD.value
                row.setdefault("blockers", []).append(
                    "SOURCE_CEILING:PREDICTION_MARKET_ONLY:capped_at_MARKET_VERIFIED_HOLD"
                )
            row.setdefault("gates", {})["source_ceiling"] = {
                "ceiling_applied":    _above_ceiling,
                "ceiling_enforced":   _above_ceiling,
                "reason":             "prediction_market_only",
                "max_confidence":     "MEDIUM",
                "max_label":          PropLabel.MARKET_VERIFIED_HOLD.value,
                "market_source_type": _mkt_source_type,
                "prior_label":        _cur_lbl,
            }

        # Terminate if market_adverse blocked the row
        if row.get("terminal_label") in (
            PropLabel.REJECT_MARKET_ADVERSE_THRESHOLD.value,
            PropLabel.REJECT_MARKET_ADVERSE_PUSH_LOSS.value,
        ):
            continue

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
        # WOW-PATCH-MANDATORY-RECONSTRUCTION-v1.0 — Module B Phase 2
        # Deferred enrichment contract check: runs after all data-intake gates
        # so acquisition and reconstruction have had a chance to fill fields.
        # Still missing enrichment fields → DATA_CONTRACT_FAIL at this point.
        # -------------------------------------------------------------------
        if not skip_data_contract and _tracker._missing_at_intake:
            data_contract.run_deferred(row, enrichment=enr, tracker=_tracker)
            if row.get("terminal_label") == PropLabel.DATA_CONTRACT_FAIL.value:
                row["gates"]["acquisition"] = _tracker.build_row_report()
                continue

        # Stamp the per-row acquisition gate result
        row["gates"]["acquisition"] = _tracker.build_row_report()

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
    # WOW Stage 2 — Hard Structural Gates (reviewer-mandated, code-enforced)
    # These gates run unconditionally after slip-level classification.
    # They cannot be bypassed by prompt instructions or governance degradation.
    #   1. MAX_SAME_EVENT_LEGS = 2 (permanent — not freeze-only)
    #   2. MAX_LIVE_MICRO_LEGS_PER_EVENT = 1
    #   3. REJECT_ALL_SAME_DIRECTION_CONCENTRATION (N >= 3, all same direction)
    #   4. REQUIRE_LIVE_STATE_FOR_LIVE_MARKETS
    # -------------------------------------------------------------------
    card_hard_gate_report = card_finalizer.run_hard_gates(rows)

    # -------------------------------------------------------------------
    # WOW-PATCH-2026-07-15 — Component/Composite Mutex (Section 4)
    # Detect conflicting composite + component directions per player.
    # Runs after all per-row gates so final terminal labels are set.
    # -------------------------------------------------------------------
    component_composite_report = component_composite.run(rows)

    # -------------------------------------------------------------------
    # WOW-PATCH-2026-07-15 — Opportunity-State Consistency (Section 5)
    # Validate that multiple LESS entries reconcile with team totals.
    # -------------------------------------------------------------------
    opportunity_state_report = opportunity_state.run(rows)

    # -------------------------------------------------------------------
    # Phase 1: Mutex Grouping + Best-Candidate Selection
    # Runs after ev_gate (edge scores available) and before classifier
    # so rejected mutex candidates get DUPLICATE_EXPOSURE_BLOCK before
    # the classifier counts buckets.
    # Same-player/same-stat-family and same-pitcher-script conflicts are
    # resolved here. Only the best candidate per group survives.
    # -------------------------------------------------------------------
    mutex_report = mutex_groups.run(rows)

    # -------------------------------------------------------------------
    # WOW-PATCH-2026-07-10 — Cross-Platform Event Grouping
    # Group rows by canonical event_id (league + date + normalized teams).
    # financial_entry_count counts all entries for the event.
    # model_observation_count is always 1 per event (deduplication rule):
    # duplicates cannot raise model win count, accuracy %, or calibration.
    # -------------------------------------------------------------------
    # Event-level groups: used for financial aggregation (all entries same game).
    event_groups = event_normalization.group_entries_by_event(rows)
    for event_id, event_rows in event_groups.items():
        n = len(event_rows)
        stake_sum = sum(
            float(r.get("stake") or 0) for r in event_rows
        )
        # gross_return: sum of payout_return field if available
        return_sum = sum(
            float(r.get("payout_return") or 0) for r in event_rows
        )
        # net_event_result: sum of net_result field if available
        net_sum = sum(
            float(r.get("net_result") or 0) for r in event_rows
        )
        for row in event_rows:
            row["financial_entry_count"]    = n
            row["duplicate_exposure_count"] = n - 1
            row["gross_stake"]              = round(stake_sum, 4)
            row["gross_return"]             = round(return_sum, 4)
            row["net_event_result"]         = round(net_sum, 4)

    # Event+side groups: model deduplication.
    # Multiple entries on the SAME side of the same game collapse to
    # model_observation_count=1 so they cannot inflate model win rate or
    # calibration. Entries on OPPOSITE sides (OVER vs UNDER) are
    # separate model observations and each get their own count of 1.
    event_side_groups = event_normalization.group_entries_by_event_and_side(rows)
    for side_key, side_rows in event_side_groups.items():
        for row in side_rows:
            row["model_observation_count"] = 1  # 1 per (event, side) group

    # -------------------------------------------------------------------
    # WOW-PATCH-2026-07-10 — Settlement Conflict Gate
    # Scan event groups for cross-platform result disagreements.
    # Conflicts → SETTLEMENT_SOURCE_CONFLICT, PENDING_RECONCILIATION,
    # model_result=null, calibration_eligible=False.
    # -------------------------------------------------------------------
    settlement_conflict_map = settlement_conflict.apply_conflict_to_rows(event_groups)

    for row in rows:
        if row.get("settlement_conflict") is True:
            # Apply SETTLEMENT_SOURCE_CONFLICT as the terminal label so it
            # overrides any classifier-derived label.
            row["terminal_label"]  = PropLabel.SETTLEMENT_SOURCE_CONFLICT.value
            row["model_result"]    = None
            row["calibration_eligible"] = False
            row.setdefault("gates", {})["settlement_conflict"] = {
                "conflict_detected": True,
                "conflict_label":    row.get("conflict_label"),
                "bankroll_status":   row.get("bankroll_status"),
                "calibration_eligible": False,
            }

    for row in rows:
        if row.get("terminal_label") in (
            PropLabel.SLATE_PURGE.value,
            PropLabel.REJECT_DATA_QUALITY.value,
            PropLabel.SOURCE_CONFLICT.value,
        ):
            continue
        ledger.check_and_register(row)

    for row in rows:
        # WOW-PATCH-2026-07-10: SETTLEMENT_SOURCE_CONFLICT is terminal — the
        # classifier must not overwrite it with REJECT_DATA_QUALITY or any
        # other label, since the reconciliation path owns the final outcome.
        if row.get("terminal_label") == PropLabel.SETTLEMENT_SOURCE_CONFLICT.value:
            continue
        classifier.classify(row)

    # -------------------------------------------------------------------
    # Patch 2026-06-27 — Settlement Loopback ceiling enforcement
    # After classifier: if ledger is stale, downgrade FINAL_APPROVED → MODEL_QUALIFIED_HOLD
    # -------------------------------------------------------------------
    if settlement_stale:
        settlement_loopback.apply_stale_ceiling_to_output(rows, stale=True)

    # -------------------------------------------------------------------
    # Four-Lane Stamping — must run after all label mutations are final.
    # Stamps confidence_lane, market_lane, money_lane, slip_lane on every
    # row so downstream consumers read lane state without re-interpreting
    # gate outputs.
    # -------------------------------------------------------------------
    for row in rows:
        _derive_four_lanes(row)

    # -------------------------------------------------------------------
    # WOW Stage 2 — Weakest-Leg Finalizer (reviewer-mandated, code-enforced)
    # Runs after all label mutations are finalized and lanes are stamped.
    # Identifies and removes the weakest leg when the gap is material (>0.05)
    # rather than retaining it as a filler to pad card size.
    # SHRINK_CARD_WHEN_NO_REPLACEMENT = True is unconditional.
    # -------------------------------------------------------------------
    card_finalizer_report = card_finalizer.finalize_card(rows)

    if record_entries:
        for row in rows:
            if row.get("terminal_label") == PropLabel.FINAL_APPROVED.value:
                tracker.record_entry(row)

    run_status = "DEGRADED_ENGINE_RUN" if failed_modules else "COMPLETE"

    _result = _build_output(
        rows, ledger,
        health_report                = health_report,
        settlement_status            = settlement_status,
        enrichment                   = enrichment,
        failed_modules               = failed_modules,
        run_status                   = run_status,
        pp_threshold_ledger          = pp_threshold_ledger,
        mutex_report                 = mutex_report,
        settlement_conflict_map      = settlement_conflict_map,
        component_composite_report   = component_composite_report,
        opportunity_state_report     = opportunity_state_report,
    )
    # Attach Stage 2 gate reports so callers can inspect hard gate outcomes
    _result["card_hard_gate_report"]  = card_hard_gate_report
    _result["card_finalizer_report"]  = card_finalizer_report
    return _result


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


def _derive_four_lanes(row: dict[str, Any]) -> None:
    """
    Stamp confidence_lane, market_lane, money_lane, slip_lane onto a row.

    All four fields are derived directly from existing gate outputs — no new
    scoring logic is introduced here.  Every lane has a small, stable set of
    string values so downstream consumers can branch on them without parsing
    prose statuses or diving into nested gate dicts.

    Call order: must run AFTER classifier.classify() and any stale-ceiling
    enforcement so that terminal_label is fully settled.
    """
    gates    = row.get("gates", {})
    terminal = row.get("terminal_label") or ""

    # ------------------------------------------------------------------
    # Confidence Lane — hit confidence independent of market verification
    # Source: gates["l5_l10_ledger"]
    # Values: HIGH | MEDIUM | LOW | RECONSTRUCTED | DATA_UNAVAILABLE
    # ------------------------------------------------------------------
    l5l10 = gates.get("l5_l10_ledger", {})
    if not l5l10.get("passed"):
        confidence_lane: str = "DATA_UNAVAILABLE"
    else:
        conf_tier = l5l10.get("confidence_tier")
        if conf_tier:
            # confidence_tier already carries the engine's own tier label
            confidence_lane = str(conf_tier).upper()
        else:
            l10_hit = l5l10.get("l10_hit_rate")
            if l10_hit is None:
                confidence_lane = "RECONSTRUCTED"
            elif l10_hit >= 0.60:
                confidence_lane = "HIGH"
            elif l10_hit >= 0.55:
                confidence_lane = "MEDIUM"
            else:
                confidence_lane = "LOW"

    # ------------------------------------------------------------------
    # Market Lane — market-edge status
    # Source: gates["market_gate"]
    # Values: EXACT_VERIFIED | ADJACENT_RECONSTRUCTED |
    #         MODEL_ONLY_RECONSTRUCTED | UNAVAILABLE | CONTRADICTION |
    #         SEVERE_DRIFT | CLV_PENDING | CLV_UNAVAILABLE | NO_MARKET_DATA
    # ------------------------------------------------------------------
    mkt           = gates.get("market_gate", {})
    mkt_status    = mkt.get("market_status", "")
    exact_found   = mkt.get("exact_market_found")
    adjacent_used = mkt.get("adjacent_market_used")

    if not mkt:
        market_lane: str = "NO_MARKET_DATA"
    elif mkt_status == "MARKET_CONTRADICTION":
        market_lane = "CONTRADICTION"
    elif mkt_status == "SEVERE_BOARD_VS_BOOK_DRIFT":
        market_lane = "SEVERE_DRIFT"
    elif mkt_status == "CLV_PENDING":
        market_lane = "CLV_PENDING"
    elif mkt_status in ("OPENER_UNAVAILABLE", "NO_CLOSE_AVAILABLE"):
        market_lane = "CLV_UNAVAILABLE"
    elif mkt_status == "NO_MARKET_AVAILABLE":
        market_lane = "UNAVAILABLE"
    elif exact_found:
        # MARKET_VERIFIED or MARKET_EDGE_DETECTED with a confirmed exact line
        market_lane = "EXACT_VERIFIED"
    elif adjacent_used:
        # Sportsbook line found but is adjacent (half-point shift), not exact
        market_lane = "ADJACENT_RECONSTRUCTED"
    elif exact_found is False:
        # No exact line and no adjacent substitution — model-only estimate
        market_lane = "MODEL_ONLY_RECONSTRUCTED"
    else:
        market_lane = "NO_MARKET_DATA"

    # ------------------------------------------------------------------
    # Money Lane — payout / economics readiness
    # Source: gates["ev_gate"] + gates["payout_context"]
    # Values: QUALIFIED | PAYOUT_UNRESOLVED | NO_MARKET | NOT_QUALIFIED |
    #         DATA_UNAVAILABLE
    # ------------------------------------------------------------------
    ev  = gates.get("ev_gate", {})
    pay = gates.get("payout_context", {})

    if not ev:
        money_lane: str = "DATA_UNAVAILABLE"
    elif any("NO_MARKET" in b for b in (ev.get("ev_blockers") or [])):
        money_lane = "NO_MARKET"
    elif ev.get("money_qualified"):
        money_lane = "QUALIFIED" if pay.get("passed") else "PAYOUT_UNRESOLVED"
    else:
        money_lane = "NOT_QUALIFIED"

    # ------------------------------------------------------------------
    # Slip Lane — slip eligibility (structure + correlation + ladder)
    # Source: gates["slip_structure"], ["component_composite"],
    #         ["opportunity_state"], ["exposure_gate"]
    # Values: ELIGIBLE | BLOCKED_COMPOSITE_CONFLICT |
    #         BLOCKED_OPPORTUNITY_CONFLICT | BLOCKED_DUPLICATE_EXPOSURE |
    #         BLOCKED_STRUCTURE | NOT_ELIGIBLE
    # ------------------------------------------------------------------
    _hard_reject_labels = {
        PropLabel.REJECT_DATA_QUALITY.value,
        PropLabel.REJECT_NO_EDGE.value,
        PropLabel.REJECT_BAD_STRUCTURE.value,
        PropLabel.SLATE_PURGE.value,
        PropLabel.SOURCE_CONFLICT.value,
        PropLabel.DUPLICATE_EXPOSURE_BLOCK.value,
        PropLabel.SETTLEMENT_SOURCE_CONFLICT.value,
        PropLabel.COMPONENT_COMPOSITE_CONFLICT.value,
        PropLabel.REJECT_CONTRADICTORY_ROLE_STATE.value,
    }

    cc   = gates.get("component_composite", {})
    opp  = gates.get("opportunity_state", {})
    exp  = gates.get("exposure_gate", {})
    slip = gates.get("slip_structure", {})

    if terminal in _hard_reject_labels:
        slip_lane: str = "NOT_ELIGIBLE"
    elif cc.get("passed") is False:
        slip_lane = "BLOCKED_COMPOSITE_CONFLICT"
    elif opp.get("passed") is False or opp.get("conflict_detected"):
        slip_lane = "BLOCKED_OPPORTUNITY_CONFLICT"
    elif exp.get("passed") is False:
        slip_lane = "BLOCKED_DUPLICATE_EXPOSURE"
    elif slip.get("passed") is False:
        slip_lane = "BLOCKED_STRUCTURE"
    else:
        slip_lane = "ELIGIBLE"

    row["confidence_lane"] = confidence_lane
    row["market_lane"]     = market_lane
    row["money_lane"]      = money_lane
    row["slip_lane"]       = slip_lane


def _build_output(rows: list[dict], ledger: ExposureLedger,
                  health_report: dict | None = None,
                  settlement_status: dict | None = None,
                  enrichment: dict | None = None,
                  failed_modules: list[str] | None = None,
                  run_status: str = "COMPLETE",
                  pp_threshold_ledger: list[dict] | None = None,
                  mutex_report: list[dict] | None = None,
                  settlement_conflict_map: dict | None = None,
                  component_composite_report: dict | None = None,
                  opportunity_state_report: dict | None = None) -> dict[str, Any]:
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

    # Phase 3: injury decision ledger — one entry per row showing dependency
    # player, status, role state, and injury_tree_status / blocker.
    injury_decision_ledger = injury_decision_tree.build_injury_decision_ledger(rows)
    _dep_count       = sum(1 for e in injury_decision_ledger if e.get("injury_dependency_flag"))
    _unresolved_count = sum(
        1 for e in injury_decision_ledger
        if e.get("injury_tree_status") in (
            injury_decision_tree.STATUS_DEPENDENCY_UNRESOLVED,
            injury_decision_tree.STATUS_ROLE_STATE_STALE,
        )
    )

    # Phase 2: market validation ledger — one entry per row showing cash threshold
    # status, exact/adjacent market classification, and confidence cap applied.
    market_validation_ledger: list[dict] = []
    for row in rows:
        mkt = (row.get("gates") or {}).get("market_gate") or {}
        market_validation_ledger.append({
            "row_id":               row.get("row_id"),
            "player":               row.get("player"),
            "prop_type":            row.get("prop_type"),
            "line":                 row.get("line"),
            "direction":            row.get("direction"),
            "cash_threshold":       (row.get("pp_thresholds") or {}).get("cash_threshold"),
            "whole_number_line":    (row.get("pp_thresholds") or {}).get("whole_number_line"),
            "cash_threshold_status": mkt.get("cash_threshold_status"),
            "exact_market_found":   mkt.get("exact_market_found"),
            "exact_market_line":    mkt.get("exact_market_line"),
            "adjacent_market_used": mkt.get("adjacent_market_used"),
            "adjacent_market_line": mkt.get("adjacent_market_line"),
            "substitution_allowed": mkt.get("substitution_allowed"),
            "confidence_cap":       mkt.get("confidence_cap"),
            "terminal_label":       row.get("terminal_label"),
        })

    # -------------------------------------------------------------------
    # WOW-PATCH-MANDATORY-RECONSTRUCTION-v1.0
    # Section 29.2 — Acquisition Execution Report (run-level)
    # Aggregates per-row acquisition gate results.
    # -------------------------------------------------------------------
    _row_acq_reports = [
        row.get("gates", {}).get("acquisition") or {}
        for row in rows
    ]
    _acq_run_report = build_run_acquisition_report(
        _row_acq_reports,
        failed_source_calls=failed_modules or [],
    )

    # WOW-PATCH-2026-07-15 — governance fingerprint in every output
    _gov_status = get_governance_status()

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
        # Phase 2 addition
        "market_validation_ledger": market_validation_ledger,
        # Phase 3 addition
        "injury_decision_ledger": injury_decision_ledger,
        # WOW-PATCH-2026-07-10 addition
        "settlement_conflict_map": settlement_conflict_map or {},
        # WOW-PATCH-MANDATORY-RECONSTRUCTION-v1.0 — Section 29.2
        "acquisition_execution_report": _acq_run_report,
        # WOW-PATCH-2026-07-15 — slip-level gate reports
        "component_composite_report": component_composite_report,
        "opportunity_state_report":   opportunity_state_report,
        # WOW-PATCH-2026-07-15 — governance fingerprint
        "governance_hash":      _gov_status["governance_hash"],
        "patch_ids_applied":    _gov_status["active_patch_ids"],
        "engine_code_version":  _gov_status["engine_code_version"],
        "can_execute":          False,
        "summary": {
            "total_rows":               len(rows),
            "by_label":                 label_counts,
            "final_count":              _effective_final_count,
            "no_play":                  no_play,
            "run_status":               run_status,
            "degraded_run":             _degraded,
            "failed_module_count":      len(failed_modules or []),
            "mutex_group_count":        len(mutex_report or []),
            "injury_dependency_count":  _dep_count,
            "unresolved_dependency_count": _unresolved_count,
        },
    }


def _get_enrichment(enrichment: dict, row: dict) -> dict:
    rid = row.get("row_id", "")
    player = (row.get("player") or "").lower()
    prop   = (row.get("prop_type") or "").lower()
    key = f"{player}:{prop}"

    return enrichment.get(rid) or enrichment.get(key) or {}
