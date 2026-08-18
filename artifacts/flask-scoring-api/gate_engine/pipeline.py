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

# ── WOW-PATCH-2026-08-16-AUDIT: module-level terminal-label classification sets ──
# These frozensets drive the non-tautological row reconciliation in run_pipeline().
# Defining them at module level lets tests import them directly to verify
# membership without running the full pipeline.

_RC_COMPLETED_LABELS: frozenset = frozenset({
    PropLabel.FINAL_APPROVED.value,
})

_RC_HELD_LABELS: frozenset = frozenset({
    # Core hold / research states
    PropLabel.MODEL_QUALIFIED_HOLD.value,
    PropLabel.MARKET_VERIFIED_HOLD.value,
    PropLabel.CALIBRATION_STALE_HOLD.value,
    PropLabel.MLB_OUTS_MORE_HOLD.value,
    PropLabel.MONEY_QUALIFIED.value,
    PropLabel.RESEARCH_INTEREST.value,
    PropLabel.FORMAT_PENDING.value,
    PropLabel.DEGRADED_ENGINE_RUN.value,
    PropLabel.MARKET_QUALIFIED_BUT_SLIP_NEGATIVE.value,
    # Watch / scout / advisory states
    PropLabel.MLB_K_LESS_WATCH.value,
    PropLabel.WNBA_COMPOSITE_WATCH.value,
    PropLabel.WNBA_COMPOSITE_SCOUT.value,
    PropLabel.WNBA_COMPOSITE_MODEL_READY.value,
    PropLabel.FLIP_CANDIDATE.value,
    # Caution / exposure states
    PropLabel.SESSION_EXPOSURE_WARNING.value,
    PropLabel.HOUSE_RULES_CAUTION.value,
    PropLabel.SERIES_STATE_CAUTION.value,
    # Conflict / audit review states (non-terminal rejects)
    PropLabel.COMPONENT_COMPOSITE_CONFLICT.value,
    PropLabel.HIGH_CONFIDENCE_SUSPENDED.value,  # value: HIGH_CONFIDENCE_SUSPENDED_CALIBRATION_FAILURE
    PropLabel.PREDICTION_MARKET_SOURCE_CEILING.value,
    PropLabel.HISTORICAL_NON_OCCURRENCE_MISUSED.value,
    PropLabel.LINE_ACTIVE_UNCONFIRMED.value,
    PropLabel.RECONCILIATION_REQUIRED.value,
    PropLabel.EXACT_LINE_AUDIT_REQUIRED.value,
    PropLabel.WEATHER_SOURCE_INVALID_FOR_SETTLEMENT.value,
    PropLabel.ESPN_BLURB_STALE_AVERAGES.value,
    PropLabel.SETTLEMENT_SOURCE_CONFLICT.value,
    PropLabel.PREGAME_SNAPSHOT_BLOCK.value,
    PropLabel.FINAL_REFRESH_REQUIRED.value,
    # Analytical caution states
    PropLabel.VARIANCE_INCREASE.value,
    PropLabel.CROSS_BOOK_PARLAY_ILLUSION.value,
    PropLabel.SAME_GAME_CORRELATED_STACK.value,
    PropLabel.SELECTIVE_RECENCY_APPLIED.value,
    PropLabel.RECENT_FORM_DIVERGENCE.value,
    PropLabel.OUTLIER_OR_ROLE_AUDIT_REQUIRED.value,
    PropLabel.SAME_PLAYER_SHARED_THESIS.value,
    PropLabel.NEXT_DAY_PREVIEW.value,
    PropLabel.LAW_OF_AVERAGES_SUPPORT.value,
    PropLabel.HOT_STREAK_AS_PROBABILITY.value,
    PropLabel.ONE_GAME_SAMPLE_INSUFFICIENT.value,
})

_RC_REJECTED_LABELS: frozenset = frozenset({
    PropLabel.DATA_CONTRACT_FAIL.value,
    PropLabel.SLATE_PURGE.value,
    PropLabel.WNBA_SLATE_PURGE.value,
    PropLabel.NO_PLAY.value,
    PropLabel.DUPLICATE_EXPOSURE_BLOCK.value,
    PropLabel.DIRECTIONAL_EXPOSURE_BLOCK.value,
    PropLabel.SESSION_DIRECTIONAL_EXPOSURE_BLOCK.value,
    PropLabel.PIPELINE_INTEGRITY_FAILURE.value,
    PropLabel.HARD_REJECT_COMBO_MULTIPLICATION.value,
    PropLabel.MLB_WINNER_PREFLIGHT_BLOCK.value,
    PropLabel.REJECT_PP_PROMOTION_GATE.value,
    PropLabel.REJECT_SAME_EVENT_NO_JOINT_MODEL.value,
    PropLabel.REJECT_RECENCY_SHOCK.value,
    PropLabel.FATAL_REJECTED_LEG_IN_CARD.value,
    PropLabel.NO_DUPLICATE_EXPOSURE.value,
    PropLabel.SOURCE_CONFLICT.value,
    PropLabel.REJECT_BAD_STRUCTURE.value,
    PropLabel.REJECT_DATA_QUALITY.value,
    PropLabel.REJECT_NO_EDGE.value,
    PropLabel.REJECT_SHARP_CONFLICT.value,
    PropLabel.REJECT_FALLING_KNIFE.value,
    PropLabel.REJECT_HOUSE_RULES_VULNERABILITY.value,
    PropLabel.REJECT_EXECUTION_STALE.value,
    PropLabel.REJECT_PAYOUT_CHANGED.value,
    PropLabel.REJECT_LOW_LIQUIDITY.value,
    PropLabel.REJECT_LINE_MOVED_AGAINST_SIDE.value,
    PropLabel.REJECT_POWER_CORRELATED.value,
    PropLabel.REJECT_MARKET_ADVERSE_THRESHOLD.value,
    PropLabel.REJECT_MARKET_ADVERSE_PUSH_LOSS.value,
    PropLabel.REJECT_CONTRADICTORY_ROLE_STATE.value,
    PropLabel.REJECT_OPPORTUNITY_SUM_MISMATCH.value,
    PropLabel.REJECT_ALTERNATE_THRESHOLD_DUPLICATE.value,
    PropLabel.REJECT_EXACT_DUPLICATE.value,
    PropLabel.REJECT_DUPLICATE_STRUCTURE.value,
    PropLabel.REJECT_DUPLICATE_PITCHER_THESIS.value,
    # Acquisition failure labels (not PropLabel enum members — string literals)
    "RUN_INVALID — ACQUISITION_INCOMPLETE",
    "RUN_INVALID_GOVERNANCE_MISMATCH",
    "INPUT_FAILURE — ACQUISITION_NOT_COMPLETED",
})


def _rc_label_is_reject(lbl: str) -> bool:
    """Return True if lbl is an explicit reject label or has a REJECT_ prefix."""
    return lbl in _RC_REJECTED_LABELS or lbl.startswith("REJECT_")
# WOW-PATCH-2026-07-15 — new gates
from . import market_adverse, component_composite, opportunity_state
from .governance import get_governance_status
# WOW Stage 2 — hard structural gates + weakest-leg finalizer (reviewer-mandated)
from . import card_finalizer, hitter_fantasy_score as _hfs_mod
from . import route_registry
# WOW-PATCH-2026-08-10-STAGE-A — Prob-ledger enforcer + outlier recompute (advisory only)
from . import prob_ledger_enforcer as _ple
from . import outlier_recompute as _or_mod
# WOW-PATCH-2026-08-17-PROB-LEDGER-HANDOFF — canonical ledger contract + adapters
from . import prob_ledger_schema as _pls
from .wnba import prob_ledger_adapter as _wnba_pla
from .mlb import pitcher_prob_ledger_adapter as _mlb_pla
# WOW-PATCH-2026-08-15 — PP Promotion Gate, Final Refresh, Pregame Snapshot
from . import pp_promotion_gate, pp_final_refresh, pp_pregame_snapshot
# WOW-PATCH-FMCG-v1.0 — Full Model Contract Gatekeeper
from . import full_model_gatekeeper as _fmcg
# LLP MLB Winner Preflight Gate (reviewer-mandated, patch-level required)
from . import llp_mlb_winner_preflight
from . import mlb_directional_firewall, wnba_composite_gate, cross_ticket_governor
# WOW v16 Tennis Total Games lane — exact Markov chain, three-outcome model
from . import tennis_total_games_gate
# WOW v16 Fantasy Score Generative Model — shadow/test only; can_execute=False unconditional
from . import fantasy_score_model as _fantasy_score_model
# WOW v16 WNBA Generative Probability lane — role-regime / Poisson mixture model
from . import wnba_generative_gate
from .mlb import plate_appearances_gate as _mlb_pa_gate
from .mlb import first_inning_efficiency as _1ip_eff
from . import model_registry as _mr   # WOW-PATCH-2026-08-16-AUDIT: PROVISIONAL ceiling enforcement
from .wnba import opportunity_engine as _wnba_opp_gate
from .wnba import evidence_acquisition as _wnba_evidence_acq
# WOW Task #136 — Opportunity, Event & Exact-Market Acquisition Layer
from .opportunity_acquisition.orchestrator import (
    AcquisitionOrchestrator as _OppOrchestrator,
    is_composite_prop_row as _is_composite_prop_row,
)
_opp_orchestrator = _OppOrchestrator()
from .portfolio.cross_slip_exposure import PortfolioExposureGovernor as _PortfolioGov


def run_pipeline(
    raw_rows: list[dict[str, Any]],
    target_date: date | None = None,
    enrichment: dict[str, dict[str, Any]] | None = None,
    record_entries: bool = False,
    skip_health_gate: bool = False,
    skip_data_contract: bool = False,
    skip_settlement_check: bool = False,
    existing_ledger: "ExposureLedger | None" = None,
    portfolio_governor: "_PortfolioGov | None" = None,
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
    # Explicit None check: preserves object identity when caller passes {}.
    # enrichment or {} would silently replace an empty dict with a new private
    # one, severing the caller's reference and discarding any writes the
    # pre-pipeline orchestrator already made into that dict.
    if enrichment is None:
        enrichment = {}
    failed_modules: list[str] = []

    rows = board_intake.normalize_board(raw_rows)

    # WOW-PATCH-2026-08-17-PROB-LEDGER-HANDOFF — stage counter reconciliation
    _pl_counter = ProbabilityPipelineCounter()
    _pl_counter.counts["rows_discovered"] = len(rows)
    _pl_contract_breaches: list[str] = []

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

        # WOW-PATCH-2026-08-16-R4: carry player_id from enrichment to the
        # normalized row if not already set.  build_auto_enrichment writes
        # player_id to entry["player_id"] after its MLB-ID lookup.  The R3c
        # block below only runs when game_log is absent from enr, so when
        # auto_enrich has already populated it, player_id would otherwise
        # stay None on the row output even though acquisition succeeded.
        if not row.get("player_id") and enr.get("player_id"):
            row["player_id"] = enr["player_id"]

        # -------------------------------------------------------------------
        # In-pipeline game-log acquisition (WOW-PATCH-2026-08-16-R3)
        # Mirrors AcquisitionOrchestrator.acquire(row, enr) in-place mutation.
        #
        # Uses a per-row temporary enrichment dict for the fetch so the batch
        # enrichment dict is NEVER mutated before the market-join audit reads
        # it.  Fetched fields are merged into enr in-place; downstream gates
        # (including l5_l10_ledger) read enr directly and therefore see the
        # game_log without any change to the batch enrichment snapshot.
        # -------------------------------------------------------------------
        if not enr.get("game_log"):
            _sport = (row.get("sport") or "").upper()
            if _sport in {"NBA", "WNBA", "MLB"}:
                try:
                    from gate_engine.acquisition_orchestrator import (  # noqa: PLC0415
                        _resolve_mlb_player_id as _orch_mlb_id,
                        _resolve_bdl_player_id as _orch_bdl_id,
                        _attempt_game_log_fetch as _orch_fetch_gl,
                    )
                    _pid: str = row.get("player_id") or ""
                    _pname: str = row.get("player") or ""
                    if not _pid:
                        if _sport == "MLB":
                            _pid = _orch_mlb_id(_pname) or ""
                        elif _sport in ("NBA", "WNBA"):
                            _pid = _orch_bdl_id(_pname, _sport) or ""
                    if _pid:
                        if not row.get("player_id"):
                            row["player_id"] = _pid
                        # Resolve canonical stat_key via normalizer alias table.
                        # normalize_board() copies prop_type verbatim (e.g. "Hits",
                        # "hits", "H"); _MLB_STAT_FIELDS only has uppercase short
                        # keys ("H", "K", …).  Without this step, _fetch_mlb raises
                        # GameLogUnavailable for any non-canonical prop_type string,
                        # which _attempt_game_log_fetch catches and silently discards,
                        # leaving direct_game_log_feed=NOT_CALLED.
                        _raw_sk: str = (
                            row.get("stat_key") or row.get("prop_type") or ""
                        ).strip()
                        try:
                            from gate_engine.normalizer import (  # noqa: PLC0415
                                _resolve_stat_key as _norm_resolve_sk,
                            )
                            _canon_sk, _ = _norm_resolve_sk(_raw_sk, _sport)
                            _stat_key_for_fetch: str = _canon_sk or _raw_sk
                        except Exception:
                            _stat_key_for_fetch = _raw_sk
                        # Fetch into a per-row scratch dict — never touches the
                        # batch enrichment dict before the market-join audit.
                        _row_enr: dict = {rid: dict(enr)}
                        _orch_fetch_gl(
                            row_id=rid,
                            player_id=_pid,
                            sport=_sport,
                            stat_key=_stat_key_for_fetch,
                            enrichment=_row_enr,
                            target_date=str(target_date) if target_date else None,
                        )
                        # Merge only the fetched fields into enr in-place.
                        # Downstream gates read enr directly; no batch mutation.
                        _fetched = _row_enr.get(rid) or {}
                        if _fetched.get("game_log"):
                            enr.update(_fetched)
                except Exception:
                    pass  # fail-closed: missing game_log surfaces as DATA_CONTRACT_FAIL

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
        # WOW-PATCH-2026-08-06-WNBA-EVIDENCE-ACQUISITION-STRUCTURAL
        # WNBA Evidence Acquisition — structural pipeline (plumbing and
        # observability only; no probability / calibration math).
        #
        # Gate insertion order per spec §8:
        #   SLATE → IDENTITY → PRIMARY_ACQUISITION → COVERAGE_AUDIT →
        #   FALLBACK_ROUTING → SOURCE_RECONCILIATION →
        #   OPPORTUNITY_PACKET_VALIDATION →
        #   [existing analytical pipeline unchanged] → FINAL_REFRESH
        #
        # Runs AFTER status_role so role_status signal is available.
        # Runs BEFORE the existing opportunity engine so the packet feeds it.
        # PACKET_INCOMPLETE_REJECTED → terminal label; skip analytical gates.
        # PACKET_PARTIAL_HOLD       → row proceeds; non-terminal note added.
        # PACKET_COMPLETE / PACKET_RECONSTRUCTED_COMPLETE → continue normally.
        # -------------------------------------------------------------------
        if _wnba_evidence_acq.is_wnba_row(row):
            _ea_result = _wnba_evidence_acq.run(row, enrichment=enr)
            _ea_ps = _ea_result.get("packet_status", "")
            if _ea_ps == "PACKET_PARTIAL_HOLD":
                # Qualification-blocking field(s) unresolved — row proceeds
                # but cannot reach a probability-qualified label downstream.
                row.setdefault("blockers", []).append(
                    "WNBA_EVIDENCE_ACQUISITION:PACKET_PARTIAL_HOLD:"
                    "qualification_fields_unresolved="
                    + ",".join(_ea_result.get("fields_unresolved") or [])
                )
            if _ea_ps == "PACKET_INCOMPLETE_REJECTED":
                row.setdefault("blockers", []).append(
                    "WNBA_EVIDENCE_ACQUISITION:PACKET_INCOMPLETE_REJECTED:"
                    "unresolved=" + ",".join(_ea_result.get("fields_unresolved") or [])
                )
                if row.get("terminal_label") is None:
                    row["terminal_label"] = PropLabel.DATA_CONTRACT_FAIL.value
                continue

        # -------------------------------------------------------------------
        # PATCH-WNBA-001: WNBA Opportunity and Role Gate
        # Runs after status_role so role_status signal is available.
        # Hard rejects (unstable opportunity, rotation volatility) exit the
        # loop early.  Soft holds (role uncertainty) apply MODEL_QUALIFIED_HOLD
        # ceiling and let the row continue.
        # -------------------------------------------------------------------
        _wnba_opp_gate.run(row, enrichment=enr)
        if row.get("terminal_label") in (
            _wnba_opp_gate.LABEL_REJECT_UNSTABLE,
            _wnba_opp_gate.LABEL_REJECT_ROTATION,
        ):
            continue

        # -------------------------------------------------------------------
        # Task #136: Opportunity, Event & Exact-Market Acquisition Layer
        # Runs after the WNBA opportunity gate so role/status signals are
        # available.  Only fires for NBA/WNBA composite props (PRA, P+R,
        # R+A, P+A).  Non-composite and non-NBA/WNBA rows pass through.
        #
        # On success:
        #   - row["gates"]["opportunity_acquisition"] is populated
        #   - enr["minutes_conflict_penalty"] / enr["source_conflict"] are
        #     set for downstream calibration awareness
        #   - enr["joint_model_provided"] = True signals that composite
        #     gates (wnba_generative_gate, wnba_composite_gate,
        #     component_composite) should use the correlated joint model
        #   - enr["opportunity_state"] holds the OpportunityState for the
        #     composite simulator
        #
        # can_execute=False is unconditional in every output.
        # -------------------------------------------------------------------
        if _is_composite_prop_row(row):
            # Capture any pre-supplied OpportunityState with live data before
            # the orchestrator overwrites it (orchestrator may get no live data
            # when running without credentials in test/dev environments).
            _pre_supplied_opp_state = enr.get("opportunity_state")
            try:
                _opp_state = _opp_orchestrator.acquire(row, enr)
                # Prefer the orchestrator state if it obtained live minutes/rates;
                # otherwise fall back to a pre-supplied state that already has them.
                if (
                    not _opp_state.has_live_opportunity_data()
                    and _pre_supplied_opp_state is not None
                    and _pre_supplied_opp_state.has_live_opportunity_data()
                ):
                    enr["opportunity_state"] = _pre_supplied_opp_state
                else:
                    enr["opportunity_state"] = _opp_state
                enr["joint_model_provided"] = True
                # Set flag where component_composite.run() reads it
                # (row["enrichment_flags"]["joint_model_provided"], not enr)
                row.setdefault("enrichment_flags", {})["joint_model_provided"] = True
            except Exception as _opp_exc:
                row.setdefault("blockers", []).append(
                    f"OPPORTUNITY_ACQUISITION:ORCHESTRATOR_ERROR:{_opp_exc!s:.80}"
                )
                row.setdefault("gates", {}).setdefault(
                    "opportunity_acquisition", {"error": str(_opp_exc)[:80], "can_execute": False}
                )

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

            # [FIX-4] Write l5/l10 computed values back into enr so that
            # run_deferred() can see them as present.
            #
            # Safety-net path: when build_auto_enrichment couldn't pre-populate
            # these fields (game-log fetch failed at enrichment time but
            # season_log reconstruction succeeded here, or the caller supplied
            # game_log without pre-computing the l5/l10 sub-fields), the deferred
            # contract check would otherwise find them absent and emit
            # DATA_CONTRACT_FAIL even though the data is available in the gate
            # result.
            #
            # Ledger internal key → data_contract field name:
            #   l5_games   → l5_values   (same list, different key names)
            #   l10_games  → l10_values
            #   l10_avg    → l10_mean    (ledger uses "avg"; contract uses "mean")
            #   l10_median → l10_median  (identical)
            #   l5_line_used → l5_line_used (identical)
            #
            # Caller-supplied values always win (only copy when enr[field] is None).
            if _l5_result.get("passed") is True and enr is not None:
                _l5_contract_map = {
                    "l5_games":     "l5_values",
                    "l10_games":    "l10_values",
                    "l10_avg":      "l10_mean",
                    "l10_median":   "l10_median",
                    "l5_line_used": "l5_line_used",
                }
                for _lk, _ck in _l5_contract_map.items():
                    _lv = _l5_result.get(_lk)
                    if _lv is not None and enr.get(_ck) is None:
                        enr[_ck] = _lv

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
        # WOW-PATCH-2026-08-17-PROB-LEDGER-HANDOFF — sport ingestion adapters
        # Translate the acquisition packet (ESPN / MLB Stats API) into the
        # canonical ProbabilityLedgerInput components BEFORE prob_ledger and
        # failure_path consume the enrichment.  Adapters populate data only;
        # no label authority; can_execute=False in every module.
        # -------------------------------------------------------------------
        _pla_breach = _apply_prob_ledger_adapter(row, enr)
        if _pla_breach:
            _pl_contract_breaches.append(_pla_breach)
        _pl_counter.increment("rows_acquired")

        # -------------------------------------------------------------------
        # Module D: Probability Component Ledger + Shrinkage
        # WOW-PATCH-2026-08-18-1IP-ROUTE-FIX (part A): bypass prob_ledger and
        # failure_path for 1IP_PITCHES_THROWN rows.
        #
        # Root-cause: MODEL_REQUIRED_COMPONENTS = {"l10_distribution","role_usage"}
        # are pitcher K/Outs adapter constructs built by mlb.pitcher_prob_ledger_adapter.
        # That adapter's canonical_stat_key("1IP_PITCHES_THROWN") returns None, so
        # it never runs for 1IP rows, leaving the ledger empty →
        # model_probability_complete=False before the event-tree gate can fire.
        #
        # Similarly, failure_path_inputs (primary/secondary/black_swan scenarios)
        # are structurally undefined for the single-path Monte Carlo event-tree lane;
        # failure_path.run() fires DATA_CONTRACT_FAIL on every 1IP row, which
        # consumes the row at line 757 — BEFORE the 1IP field gate at line 783+
        # even has a chance to run.
        #
        # Fix: for 1IP rows, stamp model_probability_complete directly from
        # bf_distribution presence (the event-tree's own data-contract requirement),
        # then skip failure_path. The 1IP field gate below remains the sole
        # data-contract enforcer for this lane. can_execute=False unconditional;
        # terminal ceiling = MODEL_QUALIFIED_HOLD.
        # -------------------------------------------------------------------
        _1ip_stat_bypass = (row.get("stat_key") or row.get("prop_type") or "").upper()
        if _1ip_stat_bypass == "1IP_PITCHES_THROWN":
            _bf_present = bool(enr.get("first_inning_bf_distribution"))
            row["model_probability_complete"] = _bf_present
            row["rank_eligible"]              = _bf_present
            row["market_lane_available"]      = False
            row["market_status"]              = "STALE_MARKET"
            row.setdefault("gates", {})["prob_ledger"] = {
                "passed":                     _bf_present,
                "rank_eligible":              _bf_present,
                "model_probability_complete": _bf_present,
                "market_lane_available":      False,
                "market_status":              "STALE_MARKET",
                "code":   "1IP_EVENT_TREE_BYPASS" if _bf_present else "1IP_BF_DIST_MISSING",
                "detail": (
                    "1IP_PITCHES_THROWN: prob_ledger bypassed — Monte Carlo event-tree "
                    "is the controlling model; l10_distribution/role_usage do not apply; "
                    "failure_path_inputs undefined for single-path event-tree lane; "
                    "can_execute=False; ceiling=MODEL_QUALIFIED_HOLD."
                ),
            }
            row["_pl_hydrated"] = True
            # Stash enrichment for the finalize pass (same reason as the else branch).
            row["_enr"] = enr
            # Module F (failure_path) is bypassed for 1IP — see patch note above.
        else:
            prob_ledger.run(row, enrichment=enr)
            row["_pl_hydrated"] = True
            # Stash enrichment now so the finalize pass can re-evaluate the ledger
            # even when a later gate terminates this row with `continue` before
            # the end-of-loop `row["_enr"] = enr` assignment.
            row["_enr"] = enr

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

        # -------------------------------------------------------------------
        # WOW-PATCH-2026-08-08-1IP-LEDGER-WIRING — 1IP event-tree field gate
        # Fires UNCONDITIONALLY for 1IP_PITCHES_THROWN, regardless of whether
        # run_deferred ran (first_inning_bf_distribution is not in
        # ENRICHMENT_REQUIRED_FIELDS so _tracker._missing_at_intake never
        # flags it).  Enforces the same DATA_CONTRACT_FAIL convention as every
        # other required enrichment field.
        #
        # Governance: lane_status=TEST_ONLY, can_execute=False unconditional,
        # ceiling=MODEL_QUALIFIED_HOLD; this gate does not raise that ceiling.
        # -------------------------------------------------------------------
        _1ip_stat = (row.get("stat_key") or row.get("prop_type") or "").upper()
        if (not skip_data_contract
                and _1ip_stat == "1IP_PITCHES_THROWN"
                and row.get("terminal_label") != PropLabel.DATA_CONTRACT_FAIL.value):
            if not enr.get("first_inning_bf_distribution"):
                row["terminal_label"] = PropLabel.DATA_CONTRACT_FAIL.value
                row.setdefault("blockers", []).append(
                    "DATA_CONTRACT_FAIL:missing_field:first_inning_bf_distribution"
                )
                row.setdefault("gates", {})["data_contract"] = {
                    "passed":         False,
                    "missing_fields": ["first_inning_bf_distribution"],
                    "code":           "DATA_CONTRACT_FAIL",
                    "detail": (
                        "1IP_PITCHES_THROWN: first_inning_bf_distribution absent after "
                        "acquisition. Backend attempts Savant CSV → pybaseball fallback; "
                        "if both fail, a PROBABILITY_PIPELINE_CONTRACT_BREACH is recorded "
                        "in enrichment['1ip_breach_contract']. GPT may also supply "
                        "first_inning_bf_distribution directly in enrichment. "
                        "mlb_1ip_pitches_poisson_v1 unconditionally excluded; "
                        "Poisson model never fires for this stat key."
                    ),
                    "phase": "1ip_event_tree_enrichment_check",
                }
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

        # Store per-row enrichment on the row so the second per-row loop's
        # sport-specific gates (wnba_generative_gate, etc.) can access it
        # without the batch-level enrichment dict being in scope.
        row["_enr"] = enr

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
    # PATCH-014 — Cross-Ticket Exposure Governor (slip-level)
    # Builds exact_leg_key, player_event_key, distribution_key,
    # pitcher_thesis_key, event_script_key.  Detects exact duplicates,
    # alternate-threshold duplicates, shared latent exposure, and
    # copied Power/Flex structures.  Assigns duplicate_group_id and
    # cross_ticket_fragility to every row.  Hard-rejects duplicate legs.
    # Runs after card_finalizer so terminal labels are set, and before
    # classifier so rejected rows are stamped before final output.
    # -------------------------------------------------------------------
    cross_ticket_report = cross_ticket_governor.run(
        rows,
        session_id=None,
        slate_date=target_date,
    )

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

        # -------------------------------------------------------------------
        # PATCH-PORTFOLIO-001: Cross-Slip Exposure Governor
        # Runs after PgSessionLedger (player/game/archetype dedup) to enforce
        # market-family dedup (same player+stat_family, any line/direction) and
        # thesis dedup (same player+stat+direction).
        # If no portfolio_governor was injected, create a per-call in-memory one.
        # -------------------------------------------------------------------
        if portfolio_governor is not None:
            portfolio_governor.check_and_register(row)

    # -------------------------------------------------------------------
    # WOW-PATCH-FMCG-v1.1 — Pre-fetch final-refresh baselines so the
    # FMCG pregame_snapshot gate can fire per-row before apply_gatekeeper.
    # Moved here (from post-gatekeeper position) so enforce_final_refresh
    # writes row["gates"]["pp_final_refresh"] before FMCG reads it.
    # Caller-supplied pp_baseline in enrichment always overrides DB-fetched.
    # Best-effort: DB unavailable → vacuous pass for all rows.
    # -------------------------------------------------------------------
    _pp_baselines: dict[str, dict] = {}
    try:
        import os as _ppbl_os
        import psycopg2 as _ppbl_pg  # type: ignore
        _ppbl_url = _ppbl_os.environ.get("DATABASE_URL")
        if _ppbl_url:
            _ppbl_conn = _ppbl_pg.connect(_ppbl_url, connect_timeout=5)
            try:
                for _ppbl_row in rows:
                    _ppbl_rid = _ppbl_row.get("row_id") or ""
                    if not _ppbl_rid:
                        continue
                    _ppbl_snap = pp_pregame_snapshot.fetch_latest_snapshot(
                        _ppbl_conn, _ppbl_rid
                    )
                    if isinstance(_ppbl_snap, dict):
                        _pp_baselines[_ppbl_rid] = _ppbl_snap
            finally:
                _ppbl_conn.close()
    except Exception:
        pass  # best-effort; DB unavailable → vacuous pass for all rows

    # Caller-supplied pp_baseline in enrichment always overrides DB-fetched
    for _rid, _enr_dict in enrichment.items():
        _bl = _enr_dict.get("pp_baseline") if isinstance(_enr_dict, dict) else None
        if isinstance(_bl, dict):
            _pp_baselines[_rid] = _bl

    for row in rows:
        # WOW-PATCH-2026-07-10: SETTLEMENT_SOURCE_CONFLICT is terminal — the
        # classifier must not overwrite it with REJECT_DATA_QUALITY or any
        # other label, since the reconciliation path owns the final outcome.
        if row.get("terminal_label") == PropLabel.SETTLEMENT_SOURCE_CONFLICT.value:
            continue
        # LLP MLB Winner Preflight Gate — reviewer-mandated, code-enforced.
        # Runs for MLB winner/moneyline rows before the classifier can assign
        # FINAL_APPROVED or MONEY_QUALIFIED.  Missing starter, lineup, weather,
        # no-vig, or model-breakeven evidence caps the row at MARKET_VERIFIED_HOLD
        # or MLB_WINNER_PREFLIGHT_BLOCK before the classifier ever sees it.
        # POSTPONED/CANCELLED/SUSPENDED → SLATE_PURGE (pick dies; rerun required).
        llp_mlb_winner_preflight.run(row)

        # WOW-PATCH-2026-08-16 — First-Inning Efficiency Deterioration Gate (#119/#118)
        # Applies probability haircut and ceiling when the GPT supplies pitcher
        # metric flags (via enrichment key "pitcher_metric_flags").
        # Advisory only when metric_flags is absent — function returns
        # EFFICIENCY_SCORE_INCOMPLETE ceiling=MODEL_QUALIFIED_HOLD by design.
        # Runs only for 1IP_PITCHES_THROWN rows that haven't been terminated.
        _1ip_sk = (row.get("stat_key") or row.get("prop_type") or "").upper()
        if _1ip_sk == "1IP_PITCHES_THROWN" and row.get("terminal_label") is None:
            _1ip_enr = row.get("_enr") or {}
            _1ip_result = _1ip_eff.calculate_recent_1ip_efficiency_score(
                pitcher_id            = str(row.get("player") or ""),
                as_of                 = str(row.get("game_date") or ""),
                metric_flags          = _1ip_enr.get("pitcher_metric_flags") or None,
                whip_increase_15pct   = _1ip_enr.get("whip_increase_15pct"),
                hard_hit_increase_5pp = _1ip_enr.get("hard_hit_increase_5pp"),
                chase_decrease_4pp    = _1ip_enr.get("chase_decrease_4pp"),
            )
            row.setdefault("gates", {})["first_inning_efficiency"] = _1ip_result
            # Apply ceiling from efficiency result if it is more restrictive
            _1ip_ceiling = _1ip_result.get("efficiency_ceiling")
            if _1ip_ceiling:
                _current_label = row.get("terminal_label")
                # MODEL_QUALIFIED_HOLD ceiling: cap anything above it
                if (_1ip_ceiling == _1ip_eff.CEILING_HOLD
                        and _current_label not in (
                            None,
                            PropLabel.NO_PLAY.value,
                            PropLabel.REJECT_DATA_QUALITY.value,
                            PropLabel.DATA_CONTRACT_FAIL.value,
                        )):
                    row["terminal_label"] = PropLabel.MODEL_QUALIFIED_HOLD.value
                    row.setdefault("blockers", []).append(
                        "FIRST_INNING_EFFICIENCY:ceiling=MODEL_QUALIFIED_HOLD:"
                        f"score={_1ip_result.get('final_efficiency_deterioration_score')}"
                    )
                # WATCH ceiling: cap anything above WATCH
                elif (_1ip_ceiling == _1ip_eff.CEILING_WATCH
                        and _current_label not in (
                            None,
                            PropLabel.NO_PLAY.value,
                            PropLabel.REJECT_DATA_QUALITY.value,
                            PropLabel.DATA_CONTRACT_FAIL.value,
                            PropLabel.MODEL_QUALIFIED_HOLD.value,
                        )):
                    row["terminal_label"] = PropLabel.WATCH.value
                    row.setdefault("blockers", []).append(
                        "FIRST_INNING_EFFICIENCY:ceiling=WATCH:"
                        f"score={_1ip_result.get('final_efficiency_deterioration_score')}"
                    )

        # PATCH-015 — MLB Directional Firewall
        # K LESS=WATCH_ONLY, OUTS MORE=MODEL_QUALIFIED_HOLD ceiling.
        # Stamps directional_lane, short_outing_support_share, etc.
        mlb_directional_firewall.run(row)

        # WOW-PATCH-2026-08-06-MLB-PLATE-APPEARANCES-COVERAGE
        # Section 18.9 gating and routing for MLB Plate Appearances props.
        # No-ops for all other stat_keys.
        _mlb_pa_gate.run(row)

        # WOW v16 WNBA Generative Probability Engine
        # Role-regime mixture + Poisson PMF; three-outcome for integer lines,
        # binary for half-points. 65% LB floor for YES_MODEL_QUALIFIED.
        # Runs before wnba_composite_gate so the composite gate can read
        # calibrated_probability_lower_bound stamped here.
        # can_execute=False unconditional. No-op for non-WNBA rows.
        wnba_generative_gate.run(row, enr=row.get("_enr") or {})

        # PATCH-017 — WNBA Composite Forward-Test Gate
        # MODEL_QUALIFIED_HOLD ceiling until 20 unique player-games settled.
        # Blocks promo upgrades on unresolved role/status.
        wnba_composite_gate.run(row)

        # WOW-PATCH-2026-08-08-FOUR-PILLAR — Composite Joint Probability Engine
        # Status: SHADOW_MODE — diagnostic only; can_execute=False unconditional.
        # For NBA/WNBA composite prop rows with an acquired OpportunityState:
        #   1. Invoke run_composite_simulation() → posterior samples (production call site)
        #   2. Build WOWProbabilityOutputs (Beta epistemic posterior over the hit rate)
        #   3. If minutes_conflict_penalty > 0: add FALLBACK_HAIRCUT risk factor that
        #      widens p_lb, representing genuine epistemic uncertainty about minutes
        #   4. Set calibrated_probability = p_true (posterior median)
        #   5. Set calibrated_probability_lower_bound = p_lb (Q10 – conflict haircut)
        #   6. Store full shadow output in row["gates"]["composite_joint_probability"]
        #
        # Aleatoric uncertainty (game-to-game volatility) lives inside the simulator.
        # Epistemic uncertainty (uncertainty about which probability model is correct)
        # lives in the Beta posterior drawn here.  Market contradiction is a
        # HARD_BLOCK/ceiling, never a posterior random variable — per doctrine.
        if _is_composite_prop_row(row):
            _cjp_enr  = row.get("_enr") or {}
            _opp_st   = _cjp_enr.get("opportunity_state")
            if _opp_st is not None:
                _cfam_raw = (row.get("prop_type") or "").lower().replace(" ", "")
                _cline: float | None = None
                for _lf in ("line", "line_value", "threshold"):
                    try:
                        _cline = float(row.get(_lf) or 0)
                        if _cline > 0:
                            break
                        _cline = None
                    except (TypeError, ValueError):
                        _cline = None
                if _cline and _cline > 0:
                    try:
                        from gate_engine.opportunity_acquisition.composite_simulator import (
                            run_composite_simulation as _run_comp_sim,
                            canonicalize_prop_family as _canon_fam,
                        )
                        from gate_engine.probability_uncertainty_engine import (
                            build_composite_probability_outputs as _build_cpo,
                        )
                        # Fix (3): canonicalize prop family so "pts+reb+ast" →
                        # "pra", "pts+reb" → "p+r", etc.  Without this, unrecognized
                        # aliases fall through to a points-only composite silently.
                        _cfam = _canon_fam(_cfam_raw)

                        # Fix (1): select hit probability by row side.
                        # MORE/OVER bets use p_more; LESS/UNDER bets use p_less.
                        _cjp_side = (
                            row.get("side") or row.get("direction") or "more"
                        ).lower().strip()

                        _cjp_sim     = _run_comp_sim(_opp_st, _cfam, _cline, n_sims=3000)
                        _cjp_penalty = float(_cjp_enr.get("minutes_conflict_penalty") or 0.0)
                        _cjp_outputs = _build_cpo(
                            _cjp_sim,
                            conflict_penalty=_cjp_penalty,
                            side=_cjp_side,
                        )

                        # Fix (2): fail closed when opportunity data is synthetic.
                        # Only publish joint-model calibrated fields when the
                        # OpportunityState carries real (non-default) minutes.
                        # If data is synthetic: store gate report for diagnostics
                        # but do NOT overwrite existing calibrated_probability.
                        _has_live = _opp_st.has_live_opportunity_data()
                        _data_quality = "LIVE" if _has_live else "SYNTHETIC_DEFAULTS"

                        if _has_live and _cjp_outputs.publishable:
                            if _cjp_outputs.p_true is not None:
                                row["calibrated_probability"] = round(_cjp_outputs.p_true, 4)
                            if _cjp_outputs.p_lb is not None:
                                row["calibrated_probability_lower_bound"] = round(
                                    _cjp_outputs.p_lb, 4
                                )
                        elif not _has_live:
                            # Diagnostic note: data was synthetic; do not publish
                            row.setdefault("blockers", []).append(
                                "COMPOSITE_JOINT_PROBABILITY:SYNTHETIC_DATA_NO_PUBLISH:"
                                "calibrated_probability unchanged (no live minutes)"
                            )

                        # Always store shadow diagnostic regardless of data quality
                        row.setdefault("gates", {})["composite_joint_probability"] = {
                            "can_execute":         False,
                            "patch_id":            "WOW-PATCH-2026-08-08-FOUR-PILLAR",
                            "patch_status":        "SHADOW_MODE",
                            "data_quality":        _data_quality,
                            "side":                _cjp_side,
                            "prop_family":         _cfam,
                            "prop_family_raw":     _cfam_raw,
                            "uncertainty_mode":    _cjp_outputs.uncertainty_mode.value,
                            "p_structural":        _cjp_outputs.p_structural,
                            "p_scenario":          _cjp_outputs.p_scenario,
                            "p_calibrated":        _cjp_outputs.p_calibrated,
                            "p_true":              _cjp_outputs.p_true,
                            "p_lb":                _cjp_outputs.p_lb,
                            "p_ub":                _cjp_outputs.p_ub,
                            "epistemic_width":     _cjp_outputs.epistemic_width,
                            "floor_distance":      _cjp_outputs.floor_distance,
                            "n_posterior_samples": len(_cjp_outputs.posterior_samples),
                            "conflict_penalty":    _cjp_penalty,
                            "calibrated_fields_published": _has_live,
                            "risk_factors": [
                                {
                                    "risk_id":    r.risk_id,
                                    "risk_family": r.risk_family.value,
                                    "effect_mode": r.effect_mode.value,
                                    "severity":    r.severity,
                                    "estimated_effect_mean": r.estimated_effect_mean,
                                }
                                for r in _cjp_outputs.risks
                            ],
                            "sim_summary": _cjp_sim.to_dict(),
                        }
                        # Ensure component_composite sees the joint_model_provided flag
                        row.setdefault("enrichment_flags", {})["joint_model_provided"] = True
                    except Exception as _cjp_err:
                        row.setdefault("blockers", []).append(
                            f"COMPOSITE_JOINT_PROBABILITY:ERROR:{str(_cjp_err)[:60]}"
                        )

        # WOW v16 Tennis Total Games lane
        # Exact Markov chain simulation; three-outcome (More+Exact+Less=1) contract.
        # PROVISIONAL ceiling (MODEL_QUALIFIED_HOLD). can_execute=False unconditional.
        # No-op for all rows except TENNIS / TOTAL_GAMES.
        tennis_total_games_gate.run(row)

        # WOW v16 Fantasy Score Generative Model (shadow / test)
        # PROVISIONAL ceiling; can_execute=False unconditional; no-op for non-FS rows.
        # Writes to row["gates"]["fantasy_score_model"] only; never touches terminal_label.
        _fantasy_score_model.run(row)

        # WOW-PATCH-2026-08-17-PROB-LEDGER-HANDOFF — finalize the probability
        # ledger IN the live path, after model/specialist outputs (e.g.
        # wnba_generative) are available and BEFORE classify/FMCG, so the
        # classifier and gatekeeper decide against the completed ledger.
        _finalize_prob_ledger_row(row)

        classifier.classify(row)

        # WOW-PATCH-FMCG-v1.1 — Wire per-row final-refresh check so the
        # FMCG pregame_snapshot gate reads row["gates"]["pp_final_refresh"]
        # before apply_gatekeeper fires.  Uses the _pp_baselines dict fetched
        # above (before this loop).  The batch pp_final_refresh.run() below
        # re-enforces for the batch report; result is identical so double-call
        # is safe (enforce_final_refresh is idempotent on same baseline).
        pp_final_refresh.enforce_final_refresh(
            row, _pp_baselines.get(row.get("row_id") or "")
        )

        # WOW-PATCH-FMCG-v1.0 — Full Model Contract Gatekeeper
        # Fail-closed: any FINAL_APPROVED without a valid Gatekeeper PASS is
        # downgraded to MODEL_QUALIFIED_HOLD before route completion propagates
        # the label through settlement_loopback and four-lane stamping.
        # Insertion order: after classify() (terminal_label is set),
        # before route_registry (corrected label must propagate downstream).
        _fmcg.apply_gatekeeper(row)

        # WOW-PATCH-2026-08-02-MANDATORY-ROUTE-COMPLETION
        # Enforce that all required gates ran before a qualifying label stands.
        # Must run immediately after classify() so the correct label flows
        # through settlement_loopback and four-lane stamping below.
        route_registry.enforce_route_completion(row)

        # WOW-PATCH-2026-08-16-AUDIT fix (b): PROVISIONAL model ceiling enforcement.
        # After the full classifier + route-completion sequence, check model_registry.
        # If the row's (sport, stat_key) resolves to a PROVISIONAL model whose
        # provisional_ceiling.money_grade_allowed=False, cap at MODEL_QUALIFIED_HOLD.
        # This is fail-closed: a missing registry entry is NOT treated as PROVISIONAL.
        _prov_sport = (row.get("sport") or "").upper()
        _prov_sk    = (row.get("stat_key") or row.get("prop_type") or "").upper()
        if _prov_sport and _prov_sk:
            _prov_entry = _mr.lookup(_prov_sport, _prov_sk)
            if (
                _prov_entry is not None
                and _prov_entry.get("status") == "PROVISIONAL"
                and not _prov_entry.get("provisional_ceiling", {}).get("money_grade_allowed", True)
            ):
                _prov_cur = row.get("terminal_label") or ""
                _PROV_ABOVE: frozenset = frozenset({
                    PropLabel.FINAL_APPROVED.value,
                    PropLabel.MONEY_QUALIFIED.value,
                })
                if _prov_cur in _PROV_ABOVE:
                    _prov_prior = _prov_cur
                    row["terminal_label"] = PropLabel.MODEL_QUALIFIED_HOLD.value
                    row.setdefault("blockers", []).append(
                        f"PROVISIONAL_MODEL_CEILING:sport={_prov_sport}:stat={_prov_sk}:"
                        f"model={_prov_entry.get('model_id')}:"
                        f"max={_prov_entry['provisional_ceiling'].get('maximum_label','MODEL_QUALIFIED_HOLD')}"
                    )
                    row.setdefault("gates", {})["provisional_ceiling_applied"] = {
                        "ceiling_applied": True,
                        "model_id":        _prov_entry.get("model_id"),
                        "model_status":    "PROVISIONAL",
                        "prior_label":     _prov_prior,
                        "enforced_label":  PropLabel.MODEL_QUALIFIED_HOLD.value,
                        "can_execute":     False,
                    }

            # WOW-PATCH-2026-08-16-AUDIT fix (6): NO_REGISTERED_MODEL fail-closed ceiling.
            # If no model is registered for this (sport, stat_key), the row cannot reach
            # FINAL_APPROVED or MONEY_QUALIFIED — cap at MODEL_QUALIFIED_HOLD.
            # This complements PROVISIONAL: both statuses cap money-grade labels.
            elif (
                _prov_entry is not None
                and _prov_entry.get("status") == "NO_REGISTERED_MODEL"
            ):
                _nr_cur = row.get("terminal_label") or ""
                _NR_ABOVE: frozenset = frozenset({
                    PropLabel.FINAL_APPROVED.value,
                    PropLabel.MONEY_QUALIFIED.value,
                })
                if _nr_cur in _NR_ABOVE:
                    row["terminal_label"] = PropLabel.MODEL_QUALIFIED_HOLD.value
                    row.setdefault("blockers", []).append(
                        f"NO_REGISTERED_MODEL_CEILING:sport={_prov_sport}:stat={_prov_sk}"
                    )
                    row.setdefault("gates", {})["no_registered_model_ceiling"] = {
                        "ceiling_applied": True,
                        "sport":           _prov_sport,
                        "stat_key":        _prov_sk,
                        "prior_label":     _nr_cur,
                        "enforced_label":  PropLabel.MODEL_QUALIFIED_HOLD.value,
                        "can_execute":     False,
                    }

        # WOW-PATCH-2026-08-16-AUDIT fix (e): 1IP_PITCHES_THROWN TEST_ONLY blanket ceiling.
        # The first-inning lane is TEST_ONLY (can_execute=False).  No 1IP row may
        # exceed MODEL_QUALIFIED_HOLD regardless of efficiency score outcome.
        # Applied after classifier so label is stable; this is the final ceiling
        # before four-lane stamping.
        _1ip_test_sk = (row.get("stat_key") or row.get("prop_type") or "").upper()
        if _1ip_test_sk == "1IP_PITCHES_THROWN":
            _1ip_test_cur = row.get("terminal_label")
            _1IP_TERMINAL_REJECTS = frozenset({
                PropLabel.NO_PLAY.value,
                PropLabel.REJECT_DATA_QUALITY.value,
                PropLabel.DATA_CONTRACT_FAIL.value,
            })
            if _1ip_test_cur not in _1IP_TERMINAL_REJECTS:
                _1IP_ABOVE_HOLD = frozenset({
                    PropLabel.FINAL_APPROVED.value,
                    PropLabel.MONEY_QUALIFIED.value,
                })
                if _1ip_test_cur in _1IP_ABOVE_HOLD or _1ip_test_cur is None:
                    _prior_1ip_test = _1ip_test_cur
                    row["terminal_label"] = PropLabel.MODEL_QUALIFIED_HOLD.value
                    row.setdefault("blockers", []).append(
                        f"1IP_TEST_ONLY_CEILING:lane=TEST_ONLY:"
                        f"prior={_prior_1ip_test}:max=MODEL_QUALIFIED_HOLD"
                    )
                    row.setdefault("gates", {})["1ip_test_only_ceiling"] = {
                        "ceiling_applied": True,
                        "prior_label":     _prior_1ip_test,
                        "enforced_label":  PropLabel.MODEL_QUALIFIED_HOLD.value,
                        "reason":          "TEST_ONLY_lane_unconditional_ceiling",
                        "can_execute":     False,
                    }

        # ---------------------------------------------------------------
        # WOW-PATCH-2026-08-18-1IP-PREDICTION-LOGGER — observational hook.
        # Fail-open: logging failure never alters scoring outcome.
        # Fires only for MLB 1IP_PITCHES_THROWN rows reaching
        # MODEL_QUALIFIED_HOLD with a real model probability.
        # can_execute=False; no label authority.
        # ---------------------------------------------------------------
        if (_1ip_test_sk == "1IP_PITCHES_THROWN"
                and row.get("terminal_label") == PropLabel.MODEL_QUALIFIED_HOLD.value):
            try:
                from validation.prediction_logger import (  # lazy import — cold-start safe
                    log_1ip_prediction as _log_1ip_pred,
                )
                _log_status = _log_1ip_pred(row, enr)
                row.setdefault("gates", {})["prediction_logger"] = _log_status
            except Exception as _log_exc:  # fail-open: never propagate
                row.setdefault("gates", {}).setdefault(
                    "prediction_logger", {}
                )["error"] = str(_log_exc)[:80]

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
    # Task-186 — Reconstructed Evidence Promotion Cap
    # Must run AFTER _derive_four_lanes() because confidence_lane is one of
    # the reconstruction signals (it can be stamped "RECONSTRUCTED" by lane
    # derivation, which runs only above).  Checking before lane derivation
    # would miss rows whose reconstruction is identified only by lane logic.
    #
    # Rows with enrichment_source=RECONSTRUCTED (or equivalent provenance
    # tag, including confidence_lane=RECONSTRUCTED) must not reach
    # FINAL_APPROVED or MODEL_QUALIFIED_HOLD.
    # Downgrade to WATCH and stamp RECONSTRUCTED_EVIDENCE_CEILING.
    # -------------------------------------------------------------------
    for row in rows:
        _recon_label = row.get("terminal_label") or ""
        _is_reconstructed = (
            row.get("enrichment_source") == "RECONSTRUCTED"
            or row.get("provenance_source_type") == "RECONSTRUCTED"
            or (row.get("provenance") or {}).get("source_type") == "RECONSTRUCTED"
            or "RECONSTRUCTED" in str(row.get("confidence_lane") or "").upper()
        )
        if _is_reconstructed and _recon_label in (
            PropLabel.FINAL_APPROVED.value,
            PropLabel.MODEL_QUALIFIED_HOLD.value,
        ):
            row["terminal_label"] = PropLabel.WATCH.value
            row.setdefault("blockers", []).append(
                "RECONSTRUCTED_EVIDENCE_CEILING:enrichment_source=RECONSTRUCTED "
                "→ max_label=WATCH; cannot reach FINAL_APPROVED or MODEL_QUALIFIED_HOLD"
            )

    # -------------------------------------------------------------------
    # WOW Stage 2 — Weakest-Leg Finalizer (reviewer-mandated, code-enforced)
    # Runs after all label mutations are finalized and lanes are stamped.
    # Identifies and removes the weakest leg when the gap is material (>0.05)
    # rather than retaining it as a filler to pad card size.
    # SHRINK_CARD_WHEN_NO_REPLACEMENT = True is unconditional.
    # -------------------------------------------------------------------
    card_finalizer_report = card_finalizer.finalize_card(rows)

    # _pp_baselines was pre-fetched before the classifier loop (above) so the
    # FMCG pregame_snapshot gate could fire per-row.  Re-use the same dict
    # here for the batch report (no second DB round-trip needed).
    pp_final_refresh_report = pp_final_refresh.run(rows, baselines=_pp_baselines)

    # -------------------------------------------------------------------
    # WOW-PATCH-2026-08-15 — PP Promotion Gate (always runs, no DB)
    # Caps FINAL_APPROVED / MONEY_QUALIFIED at MARKET_VERIFIED_HOLD when:
    #   1. Calibrated lower bound < break-even + safety_buffer
    #   2. Two-way no-vig probability < break-even + safety_buffer
    #   3. Recency-shock LOO detects result concentration (|Δ| ≥ 0.030)
    # HIGH_PROBABILITY ≠ QUALIFIED_PAID_CARD.
    # Probability fields and research labels are never modified.
    # Insertion: after finalize_card() so weakest-leg exclusions are set.
    # -------------------------------------------------------------------
    pp_promotion_report = pp_promotion_gate.run(rows)

    # -------------------------------------------------------------------
    # WOW-PATCH-2026-08-15 — Pregame Snapshot (unconditional, fail-closed)
    # Written for each row that still holds a paid-card label after the
    # promotion gate.  Runs unconditionally — NOT gated on record_entries.
    # The snapshot is an immutable audit trail, not an exposure counter;
    # it must fire on every qualifying scoring run (including analysis-only
    # runs where record_entries=False) so subsequent runs have a baseline
    # to compare against via the final-refresh gate above.
    # tracker.record_entry() and the session exposure ledger remain gated
    # on record_entries exactly as before (see line below this block).
    # Write failure → label capped at MARKET_VERIFIED_HOLD; research output
    # never silenced.
    # -------------------------------------------------------------------
    _pp_snap_results: list[dict] = []
    try:
        import os as _pp_os
        import psycopg2 as _pp_pg  # type: ignore
        _pp_db_url = _pp_os.environ.get("DATABASE_URL")
        if _pp_db_url:
            _pp_conn = _pp_pg.connect(_pp_db_url, connect_timeout=10)
            try:
                pp_pregame_snapshot.ensure_table(_pp_conn)
                _paid_labels = {"MONEY_QUALIFIED", "FINAL_APPROVED"}
                for _pp_row in rows:
                    if _pp_row.get("terminal_label") not in _paid_labels:
                        continue
                    _refresh_passed = not bool(
                        _pp_row.get("final_refresh_required")
                    )
                    _snap_res = pp_pregame_snapshot.snapshot_and_enforce(
                        _pp_conn, _pp_row, final_refresh_passed=_refresh_passed
                    )
                    _pp_snap_results.append(_snap_res)
                _pp_conn.commit()
            finally:
                _pp_conn.close()
    except Exception as _pp_snap_err:
        failed_modules.append(
            f"pp_pregame_snapshot:{str(_pp_snap_err)[:80]}"
        )

    if record_entries:
        for row in rows:
            if row.get("terminal_label") == PropLabel.FINAL_APPROVED.value:
                tracker.record_entry(row)

    run_status = "DEGRADED_ENGINE_RUN" if failed_modules else "COMPLETE"

    _result = _build_output(
        rows, ledger,
        pl_counter=_pl_counter,
        pl_contract_breaches=_pl_contract_breaches,
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
    _result["card_hard_gate_report"]    = card_hard_gate_report
    _result["card_finalizer_report"]    = card_finalizer_report
    # WOW-PATCH-2026-08-15 gate reports
    _result["pp_final_refresh_report"]  = pp_final_refresh_report
    _result["pp_promotion_report"]      = pp_promotion_report
    _result["pp_pregame_snap_results"]  = _pp_snap_results
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
    # FIX-C: also try stat_key as alternate prop join key (display-name vs
    # canonical mismatch — e.g. caller keys by "rebounds" but row has "REB")
    stat_key_lower = (row.get("stat_key") or "").lower()
    enr_by_stat = (
        enrichment.get(f"{player}:{stat_key_lower}")
        if stat_key_lower and stat_key_lower != prop
        else None
    )
    enr_for_row = (
        enr_by_rid if isinstance(enr_by_rid, dict) else (
            enr_by_key if isinstance(enr_by_key, dict) else (
                enr_by_stat if isinstance(enr_by_stat, dict) else None
            )
        )
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
                  opportunity_state_report: dict | None = None,
                  pl_counter: "ProbabilityPipelineCounter | None" = None,
                  pl_contract_breaches: list[str] | None = None) -> dict[str, Any]:
    # WOW-PATCH-2026-08-17-PROB-LEDGER-HANDOFF — counter is created by
    # run_pipeline; a direct _build_output test call gets a fresh one whose
    # rows_discovered/acquired are backfilled from the hydrated-row count so
    # reconciliation stays exact.
    _pl_counter = pl_counter if pl_counter is not None else ProbabilityPipelineCounter()
    _pl_contract_breaches: list[str] = (
        pl_contract_breaches if pl_contract_breaches is not None else []
    )
    if pl_counter is None:
        _n_hydrated = sum(1 for r in rows if r.get("_pl_hydrated"))
        _pl_counter.counts["rows_discovered"] = len(rows)
        _pl_counter.counts["rows_acquired"]   = _n_hydrated
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

    # WOW-PATCH-2026-08-17-PROB-LEDGER-HANDOFF — per-row live ledger sequence.
    # Replaces the former post-processing-only advisory passes.  For every
    # hydrated row, in order, before rank eligibility is committed:
    #   1. per-row outlier_recompute when outlier_gate flagged the row
    #      (RESOLVED/UNRESOLVED attached before prob_ledger evaluates
    #      rank_eligible)
    #   2. adapter refresh — model outputs (e.g. wnba_generative) produced in
    #      the second per-row loop are folded into the canonical ledger
    #   3. prob_ledger.run re-evaluation with fully-populated ledger data
    #   4. prob_ledger_enforcer.enforce_for_label — existing downgrade logic,
    #      now firing with fully-populated ledger data
    # All modules retain can_execute=False; no new label authority.
    _ple_violations: list[dict] = []
    _ple_incomplete_count = 0
    _or_results: list[dict] = []
    _pipeline_diagnostics: list[dict] = []

    for _fr in rows:
        if not _fr.get("_pl_hydrated"):
            continue
        _pl_counter.increment("rows_hydrated")
        _enr_for_row = _fr.get("_enr") or {}

        # Live-path finalize already ran (before classify/FMCG in the per-row
        # loop).  This call is a no-op there and only finalizes rows that
        # terminated early (continue) before reaching the classify block —
        # their ledger state is still completed for reporting/diagnostics.
        _finalize_prob_ledger_row(_fr)
        _fr.pop("_pl_hydrated", None)
        _fr.pop("_pl_finalized", None)

        # Collect per-row finalize artifacts (stamped by the helper).
        if _fr.get("outlier_recompute_status") is not None:
            _or_results.append({
                "row_id":      _fr.get("row_id"),
                "status":      _fr.get("outlier_recompute_status"),
                "can_execute": False,
            })
        for _pla_breach2 in _fr.pop("_pl_breaches", []):
            _pl_contract_breaches.append(_pla_breach2)
        _pl_counter.increment("outlier_review_complete")

        if _fr.get("model_probability_complete"):
            _pl_counter.increment("rows_model_ready")
        # failure_path either ran (gate stamped) or was intentionally skipped
        # via skip_data_contract — both count as a completed boundary for the
        # reconciliation invariant (a crash inside failure_path.run surfaces
        # as MODULE_FAILURE and terminates the row before hydration).
        _pl_counter.increment("failure_path_complete")
        _fr["failure_path_executed"] = (_fr.get("gates") or {}).get("failure_path") is not None
        if _fr.get("rank_eligible"):
            _pl_counter.increment("ledgers_complete")
            _pl_counter.increment("probabilities_validated")
            _pl_counter.increment("rank_eligible")
        else:
            # Typed diagnostic — never a bare PROB_LEDGER_INCOMPLETE.
            _diag = _build_pipeline_diagnostic(_fr, _enr_for_row)
            _fr["pipeline_diagnostic"] = _diag.to_dict()
            _fr.setdefault("blockers", []).append(_diag.to_blocker_string())
            _pipeline_diagnostics.append(_diag.to_dict())

    # (4) Prob-ledger completeness enforcement — existing downgrade logic,
    # moved after the per-row sequence so it fires with fully-populated
    # ledger data.  Runs over ALL rows so incomplete ledgers on qualifying
    # labels are surfaced even when the row reached a non-terminal label.
    for _ple_row in rows:
        _ple_label = _ple_row.get("terminal_label") or ""
        _ple_ledger = (
            (_ple_row.get("gates") or {})
            .get("prob_ledger") or {}
        )
        try:
            _ple_result = _ple.enforce_for_label(_ple_ledger, _ple_label, row=_ple_row)
            if _ple_result.label_is_probability_bearing and not _ple_result.enforcer_passed:
                _ple_incomplete_count += 1
                _ple_violations.append({
                    "row_id":           _ple_row.get("row_id"),
                    "terminal_label":   _ple_label,
                    "enforcement_code": _ple_result.enforcement_code,
                    "violations":       list(_ple_result.violations),
                    "missing_fields":   list(_ple_result.missing_fields),
                })
                # WOW-PATCH-2026-08-16-AUDIT fix (a): prob_ledger_incomplete=True
                # CANNOT coexist with FINAL_APPROVED.  Downgrade the row so the
                # summary never counts it as a qualifying result and the DB row
                # written by record_entry() reflects the corrected label.
                # WOW-PATCH-2026-08-16-AUDIT fix (5): cap MONEY_QUALIFIED too,
                # not only FINAL_APPROVED.  An incomplete ledger must not support
                # any money/final label.
                if _ple_label in (PropLabel.FINAL_APPROVED.value, PropLabel.MONEY_QUALIFIED.value):
                    _ple_row["terminal_label"] = PropLabel.MODEL_QUALIFIED_HOLD.value
                    _ple_row.setdefault("blockers", []).append(
                        f"PROB_LEDGER_ENFORCER:MONEY_LABEL_BLOCKED:{_ple_label}:"
                        f"enforcement_code={_ple_result.enforcement_code}"
                    )
        except Exception:
            pass  # enforcer is advisory; never block the response

    # WOW-PATCH-2026-08-16-AUDIT fix (1): row label normalization pass A.
    # None / empty labels are normalized to DATA_CONTRACT_FAIL immediately.
    for _norm_row in rows:
        _lbl_a = _norm_row.get("terminal_label")
        if not _lbl_a:
            _norm_row["terminal_label"] = PropLabel.DATA_CONTRACT_FAIL.value
            _norm_row.setdefault("blockers", []).append(
                f"UNLABELED_ROW_NORMALIZED:terminal_label_was={_lbl_a!r}"
            )

    # NOTE (WOW-PATCH-2026-08-17-PROB-LEDGER-HANDOFF): the former
    # post-processing outlier recompute pass was promoted to the per-row live
    # sequence above — outlier_recompute now fires per-row when
    # outlier_gate.any_flag is set, before prob_ledger evaluates
    # rank_eligible.  _or_results is populated there.

    # WOW-PATCH-2026-08-17 — stage counter reconciliation (Step 8).
    # rows_discovered >= rows_acquired >= rows_hydrated >= rows_model_ready
    # and ledgers_complete + typed missing-field blocker rows == rows_hydrated.
    # A mismatch adds a run-level PROBABILITY_PIPELINE_CONTRACT_BREACH blocker;
    # the run still returns so the GPT can report the discrepancy.
    _pl_counter_breaches = _pl_counter.reconcile(
        typed_blocker_rows=len(_pipeline_diagnostics),
    )
    _run_blockers: list[str] = list(_pl_counter_breaches)
    for _cb in _pl_contract_breaches:
        _run_blockers.append(
            f"{_pls.PROBABILITY_PIPELINE_CONTRACT_BREACH}:{_cb}"
        )

    # WOW-PATCH-2026-07-15 — governance fingerprint in every output
    _gov_status = get_governance_status()

    # WOW-PATCH-2026-08-02-MANDATORY-ROUTE-COMPLETION
    # Gate execution trace — built after all enforcement so terminal_label is final.
    gate_execution_summary = [
        route_registry.build_row_execution_trace(row) for row in rows
    ]
    _route_failures = sum(1 for t in gate_execution_summary if t["route_downgraded"])

    # WOW-PATCH-2026-08-16-AUDIT fix (1) revised: module-level frozensets.
    # _RC_COMPLETED_LABELS, _RC_HELD_LABELS, _RC_REJECTED_LABELS, and
    # _rc_label_is_reject() are defined at module level (imported by tests directly).

    # Normalization pass B: any label not in a registered bucket → DATA_CONTRACT_FAIL.
    # Catches CONDITIONAL, BOGUS_LABEL, "conditional", whitespace, etc.
    for _norm_row in rows:
        _lbl_b = _norm_row.get("terminal_label") or ""
        if (
            _lbl_b not in _RC_COMPLETED_LABELS
            and _lbl_b not in _RC_HELD_LABELS
            and not _rc_label_is_reject(_lbl_b)
        ):
            _old_b = _lbl_b
            _norm_row["terminal_label"] = PropLabel.DATA_CONTRACT_FAIL.value
            _norm_row.setdefault("blockers", []).append(
                f"UNKNOWN_LABEL_NORMALIZED:was={_old_b!r}"
            )

    # Explicit, non-tautological counting after both normalization passes.
    # After passes A+B every label is in exactly one registered bucket.
    _rc_completed = sum(1 for r in rows if r.get("terminal_label") in _RC_COMPLETED_LABELS)
    _rc_held      = sum(1 for r in rows if r.get("terminal_label") in _RC_HELD_LABELS)
    _rc_rejected  = sum(1 for r in rows if _rc_label_is_reject(r.get("terminal_label") or ""))
    _rc_unknown   = len(rows) - _rc_completed - _rc_held - _rc_rejected   # must be 0
    _rc_other     = 0

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
        # WOW-PATCH-2026-08-02-MANDATORY-ROUTE-COMPLETION
        "gate_execution_summary": gate_execution_summary,
        # WOW-PATCH-2026-08-10-STAGE-A — Prob-ledger enforcer + outlier recompute
        "prob_ledger_enforcement_report": {
            "incomplete_count": _ple_incomplete_count,
            "violations":       _ple_violations,
            "can_execute":      False,
        },
        "outlier_recompute_report": {
            "recomputed_count": len(_or_results),
            "results":          _or_results,
            "can_execute":      False,
        },
        # WOW-PATCH-2026-08-17-PROB-LEDGER-HANDOFF — Steps 7 & 8
        "pipeline_diagnostic": _pipeline_diagnostics,
        "probability_pipeline_counter": _pl_counter.as_dict(),
        "run_blockers": _run_blockers,
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
            "route_completion_failures": _route_failures,
            # WOW-PATCH-2026-08-10-STAGE-A — task #71: surface incomplete prob ledgers
            "prob_ledger_incomplete":   _ple_incomplete_count > 0,
            "prob_ledger_incomplete_count": _ple_incomplete_count,
            # WOW-PATCH-2026-08-16-AUDIT fix (1) revised: exact, non-tautological
            # row reconciliation using three explicit registered frozensets.
            # Normalization passes A+B guarantee every label is in a known bucket.
            # rows_unknown must be 0; row_balance_valid requires both equality
            # AND rows_unknown == 0 so the check is never tautological.
            "rows_in":         len(rows),
            "rows_completed":  _rc_completed,
            "rows_held":       _rc_held,
            "rows_rejected":   _rc_rejected,
            "rows_unknown":    _rc_unknown,   # must be 0 after normalization passes A+B
            "rows_other":      _rc_other,     # always 0 (alias preserved for API compat)
            "row_balance_valid": (
                (_rc_completed + _rc_held + _rc_rejected) == len(rows)
                and _rc_unknown == 0
            ),
        },
        # WOW-PATCH-2026-08-16-AUDIT fix (8): canonical ceiling enforcement status.
        # Each active ceiling mechanism reports its enforcement mode.  ACTIVE_FAIL_CLOSED
        # means the mechanism runs on every row and downgrades rather than skipping on error.
        "backend_global_ceiling_enforcement_status": {
            # WOW-PATCH-2026-08-16-AUDIT fix (7): all mechanisms verified active after
            # closeout commit — see audit report 2026-08-16 items (1)–(6).
            "prob_ledger_enforcer":         "ACTIVE_FAIL_CLOSED",   # caps FA+MQ on incomplete ledger
            "provisional_model_ceiling":    "ACTIVE_FAIL_CLOSED",   # caps FA+MQ on PROVISIONAL models
            "no_registered_model_ceiling":  "ACTIVE_FAIL_CLOSED",   # caps FA+MQ when no model registered
            "1ip_test_only_ceiling":        "ACTIVE_FAIL_CLOSED",   # blanket TEST_ONLY cap on 1IP lane
            "fmcg_gatekeeper":              "ACTIVE_FAIL_CLOSED",   # final-approval contract gatekeeper
            "settlement_loopback":          "ACTIVE_FAIL_CLOSED",   # stale-ledger ceiling on all rows
            "reconstructed_evidence_cap":   "ACTIVE_FAIL_CLOSED",   # caps reconstructed-evidence rows
            "source_ceiling":               "ACTIVE_FAIL_CLOSED",   # per-source max-supportable ceiling
            "route_completion_enforcer":    "ACTIVE_FAIL_CLOSED",   # mandatory route completion gate
            "row_reconciliation_enforcer":  "ACTIVE_FAIL_CLOSED",   # normalization sweep + exact counts
            "provenance_fail_closed":       "ACTIVE_FAIL_CLOSED",   # transactional snapshot downgrade
            "can_execute":                  False,
        },
    }


class ProbabilityPipelineCounter:
    """
    WOW-PATCH-2026-08-17-PROB-LEDGER-HANDOFF — Step 8.

    Atomic stage counters at each named pipeline boundary.  reconcile()
    verifies the monotonic invariants and returns a list of run-level
    PROBABILITY_PIPELINE_CONTRACT_BREACH blocker strings (empty when clean).
    can_execute=False — diagnostic only; never a label authority.
    """

    STAGES = (
        "rows_discovered", "rows_acquired", "rows_hydrated", "rows_model_ready",
        "ledgers_complete", "outlier_review_complete", "failure_path_complete",
        "probabilities_validated", "rank_eligible",
    )

    def __init__(self) -> None:
        self.counts: dict[str, int] = {s: 0 for s in self.STAGES}

    def increment(self, stage: str) -> None:
        if stage not in self.counts:
            raise KeyError(f"unknown pipeline counter stage: {stage}")
        self.counts[stage] += 1

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = dict(self.counts)
        out["can_execute"] = False
        return out

    def reconcile(self, typed_blocker_rows: int) -> list[str]:
        breaches: list[str] = []
        c = self.counts
        _breach = _pls.PROBABILITY_PIPELINE_CONTRACT_BREACH
        if not (c["rows_discovered"] >= c["rows_acquired"]
                >= c["rows_hydrated"] >= c["rows_model_ready"]):
            breaches.append(
                f"{_breach}:stage_monotonicity:"
                f"discovered={c['rows_discovered']},acquired={c['rows_acquired']},"
                f"hydrated={c['rows_hydrated']},model_ready={c['rows_model_ready']}"
            )
        if c["ledgers_complete"] + typed_blocker_rows != c["rows_hydrated"]:
            breaches.append(
                f"{_breach}:ledger_reconciliation:"
                f"ledgers_complete={c['ledgers_complete']}+"
                f"typed_blocker_rows={typed_blocker_rows}"
                f"!=rows_hydrated={c['rows_hydrated']}"
            )
        return breaches


def _apply_prob_ledger_adapter(row: dict, enr: dict) -> "str | None":
    """
    WOW-PATCH-2026-08-17-PROB-LEDGER-HANDOFF — Steps 3–5.

    Route the row to its sport ingestion adapter (WNBA player props / MLB
    pitcher K+Outs), translate the acquisition packet into the canonical
    ProbabilityLedgerInput, and merge the resulting payload into
    enr["model_probability_ledger"] without clobbering caller-supplied fields.

    Returns a contract-breach detail string when the adapter provably dropped
    a populated component between input and payload (test-G invariant), or
    None on success / no-op.  Adapter type errors surface as typed blockers
    on the row — never a bare PROB_LEDGER_INCOMPLETE.
    """
    # Contract-version enforcement at ledger ingress — BEFORE the adapter
    # reads the supplied payload, so a stale/unsupported payload's values can
    # never seed the canonical ledger.  Rejected payload is quarantined for
    # diagnostics; a typed blocker is attached.
    _sup0 = enr.get("model_probability_ledger")
    if isinstance(_sup0, dict):
        _sup_cv = _sup0.get("contract_version")
        if _sup_cv is not None and str(_sup_cv) not in _pls.SUPPORTED_CONTRACT_VERSIONS:
            row.setdefault("blockers", []).append(
                f"PROB_LEDGER_SCHEMA:UNSUPPORTED_CONTRACT_VERSION:"
                f"supplied={str(_sup_cv)[:20]}:supported={sorted(_pls.SUPPORTED_CONTRACT_VERSIONS)}:"
                f"row_id={row.get('row_id')}"
            )
            enr["model_probability_ledger_rejected"] = _sup0
            enr.pop("model_probability_ledger", None)

    sport = str(row.get("sport") or "").upper()
    ledger_input = None
    try:
        if sport == "WNBA" and (
            enr.get("game_log") is not None or enr.get("box_score_log") is not None
        ):
            ledger_input = _wnba_pla.build_ledger_input(row, enr)
        elif sport == "MLB" and _mlb_pla.canonical_stat_key(
            row.get("stat_key") or row.get("prop_type")
        ) is not None:
            ledger_input = _mlb_pla.build_ledger_input(row, enr)
    except ValueError as exc:
        row.setdefault("blockers", []).append(
            f"PROB_LEDGER_ADAPTER:{str(exc)[:160]}"
        )
        return None
    except Exception as exc:
        row.setdefault("blockers", []).append(
            f"PROB_LEDGER_ADAPTER:ADAPTER_ERROR:{type(exc).__name__}:{str(exc)[:120]}"
        )
        return None

    if ledger_input is None:
        return None

    payload = ledger_input.to_ledger_payload()

    # Structural drop/rename check (test G): every component populated on the
    # canonical input must survive into the ledger payload.
    _payload_comp_names = {
        (c.get("name") or "").lower() for c in payload.get("components") or []
    }
    for _comp in _pls.COMPONENT_GUARDS:
        if isinstance(getattr(ledger_input, _comp, None), dict) and _comp not in _payload_comp_names:
            return (
                f"adapter_dropped_field:row_id={row.get('row_id')}:"
                f"component={_comp}:contract_version={ledger_input.contract_version}"
            )

    # Contract-version enforcement at ledger ingress: a caller-supplied
    # payload carrying an unsupported contract_version is rejected (typed
    # blocker; adapter-built canonical payload used instead) — an arbitrary
    # or stale payload cannot reach the scorer by carrying a version string.
    supplied = enr.get("model_probability_ledger")
    if isinstance(supplied, dict) and supplied:
        _sup_cv = supplied.get("contract_version")
        if _sup_cv is not None and str(_sup_cv) not in _pls.SUPPORTED_CONTRACT_VERSIONS:
            row.setdefault("blockers", []).append(
                f"PROB_LEDGER_SCHEMA:UNSUPPORTED_CONTRACT_VERSION:"
                f"supplied={str(_sup_cv)[:20]}:supported={sorted(_pls.SUPPORTED_CONTRACT_VERSIONS)}:"
                f"row_id={row.get('row_id')}"
            )
            supplied = None
    if isinstance(supplied, dict) and supplied:
        merged = dict(supplied)
        _sup_comps = [
            c for c in (supplied.get("components") or []) if isinstance(c, dict)
        ]
        _sup_names = {(c.get("name") or "").lower() for c in _sup_comps}
        merged_comps = _sup_comps + [
            c for c in payload["components"]
            if (c.get("name") or "").lower() not in _sup_names
        ]
        for k, v in payload.items():
            if k == "components":
                continue
            if merged.get(k) is None:
                merged[k] = v
        merged["components"] = merged_comps
        # The merged canonical record is always stamped with the enforced
        # contract version (supplied version already validated above).
        merged["contract_version"] = _pls.CONTRACT_VERSION
        enr["model_probability_ledger"] = merged
    else:
        enr["model_probability_ledger"] = payload

    # Enforce the versioned schema at ingress: validate the canonical input
    # (adapter fields + merged caller payload) and attach the structured
    # result so diagnostics never degrade to a vague incompleteness flag.
    _sv = _pls.validate_schema(
        dict(enr["model_probability_ledger"],
             row_id=row.get("row_id"),
             acquisition_status=ledger_input.acquisition_status,
             provider_status=dict(ledger_input.provider_status),
             missing_fields=list(ledger_input.missing_fields)),
        stage="prob_ledger_ingress",
    )
    row["prob_ledger_schema_validation"] = _sv.to_dict()
    for _iv in _sv.invalid_fields:
        if str(_iv).startswith("contract_version:unsupported"):
            row.setdefault("blockers", []).append(
                f"PROB_LEDGER_SCHEMA:UNSUPPORTED_CONTRACT_VERSION:{_iv}:"
                f"row_id={row.get('row_id')}"
            )

    row["prob_ledger_input"] = {
        "contract_version":   ledger_input.contract_version,
        "acquisition_status": ledger_input.acquisition_status,
        "provider_status":    dict(ledger_input.provider_status),
        "missing_fields":     list(ledger_input.missing_fields),
        "can_execute":        False,
    }
    return None


def _finalize_prob_ledger_row(row: dict) -> None:
    """
    WOW-PATCH-2026-08-17-PROB-LEDGER-HANDOFF — per-row live ledger finalize.

    Runs in the LIVE scoring path, immediately before classifier.classify /
    FMCG, so classification and gatekeeping see the fully-populated ledger:
      1. per-row outlier_recompute when outlier_gate flagged the row
      2. adapter refresh — model outputs (e.g. wnba_generative) produced
         earlier in the same per-row loop are folded into the canonical ledger
      3. prob_ledger.run re-evaluation (prior ledger blockers stripped first)
    Idempotent: no-ops when the row was never hydrated or already finalized.
    Breaches are stashed on row["_pl_breaches"]; outlier results on
    row["outlier_recompute_status"].  can_execute=False; no label authority.
    """
    if not row.get("_pl_hydrated") or row.get("_pl_finalized"):
        return
    enr = row.get("_enr") or {}

    # (1) per-row outlier recompute — live scoring path, not post-report
    _blockers = row.get("blockers") or []
    if any("OUTLIER_FLAG:REVIEW_REQUIRED" in str(b) for b in _blockers):
        try:
            _or_result = _or_mod.run(row, enrichment=enr)
            _or_status = (
                _or_result.status if hasattr(_or_result, "status") else str(_or_result)
            )
        except Exception as _or_exc:
            _or_status = f"UNRESOLVED:recompute_error:{type(_or_exc).__name__}"
        row["outlier_recompute_status"] = _or_status

    # (2) adapter refresh with model outputs now available on the row
    _breach = _apply_prob_ledger_adapter(row, enr)
    if _breach:
        row.setdefault("_pl_breaches", []).append(_breach)

    # (3) prob_ledger re-evaluation — strip prior ledger blockers first so
    # the re-run does not duplicate them.
    row["blockers"] = [
        b for b in (row.get("blockers") or [])
        if not (isinstance(b, str)
                and (b.startswith("PROB_LEDGER:") or b.startswith("MARKET_LANE:")))
    ]
    prob_ledger.run(row, enrichment=enr)
    row["_pl_finalized"] = True


def _build_pipeline_diagnostic(row: dict, enr: dict) -> "_pls.PipelineDiagnostic":
    """Build the typed per-row diagnostic for a row that failed rank eligibility."""
    _gates = row.get("gates") if isinstance(row.get("gates"), dict) else {}
    pl_gate = _gates.get("prob_ledger")
    if not isinstance(pl_gate, dict):
        pl_gate = {}
    schema = pl_gate.get("probability_schema")
    if not isinstance(schema, dict):
        schema = {}
    pli = row.get("prob_ledger_input")
    if not isinstance(pli, dict):
        pli = {}
    ledger_payload = enr.get("model_probability_ledger") if isinstance(enr, dict) else None
    if not isinstance(ledger_payload, dict):
        ledger_payload = {}
    missing = list(dict.fromkeys(
        (schema.get("missing_fields") or [])
        + (pl_gate.get("model_missing_components") or [])
        + (pli.get("missing_fields") or [])
    ))
    invalid = list(
        (schema.get("type_violations") or []) + (schema.get("bound_violations") or [])
    )
    fp_gate = (row.get("gates") or {}).get("failure_path")
    retryable = bool(pli.get("acquisition_status") in ("ATTEMPTED", "NOT_ATTEMPTED", None)
                     or any(m for m in missing if m not in ("narrative",)))
    return _pls.PipelineDiagnostic(
        stage="prob_ledger_rank_eligibility",
        contract_version=str(pli.get("contract_version")
                             or ledger_payload.get("contract_version")
                             or _pls.CONTRACT_VERSION),
        row_id=str(row.get("row_id") or ""),
        received_fields=[k for k in ledger_payload.keys() if k != "components"],
        normalized_fields=[
            (c.get("name") or "") for c in (ledger_payload.get("components") or [])
            if isinstance(c, dict)
        ],
        missing_fields=missing,
        invalid_fields=invalid,
        acquisition_attempted=(pli.get("acquisition_status") not in (None, "NOT_ATTEMPTED")),
        provider_status=dict(pli.get("provider_status") or {}),
        specialist_status=_gates.get("wnba_generative", {}).get("model_status")
            if isinstance(_gates.get("wnba_generative"), dict) else None,
        ledger_status=pl_gate.get("code"),
        outlier_status=row.get("outlier_recompute_status"),
        failure_path_status=(fp_gate or {}).get("code") if isinstance(fp_gate, dict) else None,
        market_status=row.get("market_status"),
        rank_eligible=bool(row.get("rank_eligible")),
        retryable=retryable,
    )


def _get_enrichment(enrichment: dict, row: dict) -> dict:
    rid = row.get("row_id", "")
    player = (row.get("player") or "").lower()
    prop   = (row.get("prop_type") or "").lower()
    key = f"{player}:{prop}"

    # FIX-C: Also try stat_key as alternate enrichment key.
    # When the normalizer converts "Rebounds" → stat_key "REB", the pipeline
    # row's prop_type becomes "REB" but the caller may have keyed enrichment
    # under "angel reese:rebounds" (display name).  Trying both prevents a
    # JOIN_KEY_MISMATCH that hides a legitimate sportsbook_line.
    stat_key_lower = (row.get("stat_key") or "").lower()
    key_by_stat = (
        f"{player}:{stat_key_lower}"
        if stat_key_lower and stat_key_lower != prop
        else None
    )

    return (
        enrichment.get(rid)
        or enrichment.get(key)
        or (enrichment.get(key_by_stat) if key_by_stat else None)
        or {}
    )
