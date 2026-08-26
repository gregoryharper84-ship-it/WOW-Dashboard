"""
gate_engine/tests/test_wnba_props_adapter.py
WOW-PATCH-2026-08-11-UNIVERSAL-AGENT-CORE-V1-B4

Regression tests for the WNBA/NBA Props lane adapter (B4).

Coverage
--------
TestWnbaPropsValidation          — AdapterInputError on every required-field check
TestWnbaPropsFieldMap            — field extraction and derivation helpers
TestWnbaPropsRoleInputs          — all six B1 role payloads: valid + degraded cases
TestWnbaPropsAdapterFull         — full adapter integration (COMPLETE / DEGRADED)
TestWnbaPropsAdapterScopeInvariants — can_execute=False, no governance keys, lane=WNBA_PROPS
TestWnbaPropsAdapterGameScript   — game_script_shadow present on full rows
"""
from __future__ import annotations

import unittest
from typing import Any

from gate_engine.universal_agent.lanes.wnba_props.validation import (
    AdapterInputError,
    validate_wnba_props_row,
    can_execute as VALIDATION_CAN_EXECUTE,
)
from gate_engine.universal_agent.lanes.wnba_props.field_map import (
    build_source_coverage, build_data_gaps,
    derive_data_freshness, derive_slate_consistency,
    map_active_status_to_player_status, derive_market_status,
    derive_assessment_confidence, derive_contradiction_severity,
    derive_resolution_recommendation, derive_failure_detected,
    derive_contradiction_detected, derive_refresh_status,
    derive_evidence_snapshot_valid,
    can_execute as FIELD_MAP_CAN_EXECUTE,
    SOURCE_ROW_FIELDS_USED,
)
from gate_engine.universal_agent.lanes.wnba_props.role_inputs import (
    build_data_slate_integrity_input,
    build_news_status_input,
    build_market_exact_line_input,
    build_sport_specialist_input,
    build_failure_contradiction_input,
    build_final_refresh_input,
    RoleInputBuildError,
    can_execute as ROLE_INPUTS_CAN_EXECUTE,
)
from gate_engine.universal_agent.lanes.wnba_props.adapter import (
    AdapterStatus, WnbaPropsAdapter, WnbaPropsAdapterResult,
    can_execute as ADAPTER_CAN_EXECUTE,
)
from gate_engine.universal_agent.lanes.wnba_props import (
    WnbaPropsAdapter as PubAdapter,
    WnbaPropsAdapterResult as PubResult,
    AdapterInputError as PubError,
    AdapterStatus as PubStatus,
)
from gate_engine.universal_agent.evidence_packet import Lane


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _minimal_row(**overrides) -> dict:
    """Minimal valid WNBA props row (Angel Reese rebounds shape)."""
    row = {
        "sport":    "WNBA",
        "market":   "rebounds",
        "event_id": "wnba-2026-chi-sea-001",
        "player":   "Angel Reese",
        "team":     "CHI",
        "opponent": "SEA",
        "line":     10.5,
        "direction": "over",
        "slate_date": "2026-08-11",
        "role_status": {
            "active_status": "ACTIVE",
            "projected_minutes": 32.0,
            "minutes_low": 26.0,
            "minutes_high": 38.0,
            "usage_role": "STARTER",
            "sources": ["espn", "wnba_official"],
        },
        "gates": {
            "wnba_evidence_acquisition": {
                "packet_status": "PACKET_COMPLETE",
                "field_status_map": {
                    "active_status": "PRIMARY_RETRIEVED",
                    "projected_minutes": "PRIMARY_RETRIEVED",
                },
                "fields_unresolved": [],
            }
        },
        "hit_probability": 0.58,
        "calibrated_probability": 0.55,
        "model_used": "wnba_rebounds_poisson_v1",
        "pulled_at": "2026-08-11T14:00:00Z",
    }
    row.update(overrides)
    return row


def _full_enrichment(**overrides) -> dict:
    enr = {
        "event_status": "SCHEDULED",
        "game_log": [
            {"min": 33, "reb": 12, "pts": 18},
            {"min": 31, "reb": 9,  "pts": 14},
            {"min": 35, "reb": 13, "pts": 20},
            {"min": 28, "reb": 8,  "pts": 11},
            {"min": 30, "reb": 10, "pts": 16},
        ],
        "market_comparison": {"over_odds": -115, "under_odds": -105},
        "news_contradiction_check": {"conflict_status": "CLEAR"},
    }
    enr.update(overrides)
    return enr


# ── TestWnbaPropsValidation ───────────────────────────────────────────────────

class TestWnbaPropsValidation(unittest.TestCase):

    def test_non_dict_raises(self):
        with self.assertRaises(AdapterInputError) as ctx:
            validate_wnba_props_row("not a dict")
        self.assertEqual(ctx.exception.code, "ADAPTER_INPUT_NOT_DICT")

    def test_list_raises(self):
        with self.assertRaises(AdapterInputError) as ctx:
            validate_wnba_props_row([{"sport": "WNBA"}])
        self.assertEqual(ctx.exception.code, "ADAPTER_INPUT_NOT_DICT")

    def test_wrong_sport_mlb_raises(self):
        with self.assertRaises(AdapterInputError) as ctx:
            validate_wnba_props_row({"sport": "MLB", "market": "hits", "event_id": "x"})
        self.assertEqual(ctx.exception.code, "ADAPTER_SPORT_MISMATCH")

    def test_wrong_sport_empty_raises(self):
        with self.assertRaises(AdapterInputError) as ctx:
            validate_wnba_props_row({"sport": "", "market": "rebounds", "event_id": "x"})
        self.assertEqual(ctx.exception.code, "ADAPTER_SPORT_MISMATCH")

    def test_nba_sport_accepted(self):
        row = {"sport": "NBA", "market": "points", "event_id": "nba-001"}
        validate_wnba_props_row(row)  # should not raise

    def test_wnba_lowercase_accepted(self):
        row = {"sport": "wnba", "market": "rebounds", "event_id": "wnba-001"}
        validate_wnba_props_row(row)

    def test_winner_market_raises(self):
        with self.assertRaises(AdapterInputError) as ctx:
            validate_wnba_props_row({"sport": "WNBA", "market": "game winner", "event_id": "x"})
        self.assertEqual(ctx.exception.code, "ADAPTER_MARKET_MISMATCH")

    def test_moneyline_market_raises(self):
        with self.assertRaises(AdapterInputError) as ctx:
            validate_wnba_props_row({"sport": "WNBA", "market": "moneyline", "event_id": "x"})
        self.assertEqual(ctx.exception.code, "ADAPTER_MARKET_MISMATCH")

    def test_ml_keyword_in_market_raises(self):
        with self.assertRaises(AdapterInputError) as ctx:
            validate_wnba_props_row({"sport": "NBA", "market": "ml spread", "event_id": "x"})
        self.assertEqual(ctx.exception.code, "ADAPTER_MARKET_MISMATCH")

    def test_missing_event_id_raises(self):
        with self.assertRaises(AdapterInputError) as ctx:
            validate_wnba_props_row({"sport": "WNBA", "market": "rebounds"})
        self.assertEqual(ctx.exception.code, "ADAPTER_MISSING_EVENT_ID")

    def test_empty_event_id_raises(self):
        with self.assertRaises(AdapterInputError) as ctx:
            validate_wnba_props_row({"sport": "WNBA", "market": "rebounds", "event_id": "  "})
        self.assertEqual(ctx.exception.code, "ADAPTER_MISSING_EVENT_ID")

    def test_prop_type_field_accepted_as_market(self):
        row = {"sport": "WNBA", "prop_type": "assists", "event_id": "x-001"}
        validate_wnba_props_row(row)  # should not raise

    def test_adapter_input_error_repr(self):
        err = AdapterInputError("SOME_CODE", "some message")
        self.assertIn("SOME_CODE", repr(err))

    def test_can_execute_false(self):
        self.assertFalse(VALIDATION_CAN_EXECUTE)


# ── TestWnbaPropsFieldMap ─────────────────────────────────────────────────────

class TestWnbaPropsFieldMap(unittest.TestCase):

    def setUp(self):
        self.combined = {**_full_enrichment(), **_minimal_row()}

    def test_source_coverage_all_available(self):
        cov = build_source_coverage(self.combined)
        self.assertIn("active_status", cov)
        self.assertIn("hit_probability", cov)
        self.assertEqual(cov["hit_probability"], "available")

    def test_source_coverage_missing_when_absent(self):
        c = dict(self.combined)
        c.pop("hit_probability", None)
        c.pop("calibrated_probability", None)
        cov = build_source_coverage(c)
        self.assertEqual(cov["hit_probability"], "missing")

    def test_build_data_gaps_empty_when_complete(self):
        gaps = build_data_gaps(self.combined)
        self.assertEqual(gaps, [])

    def test_build_data_gaps_present_when_missing(self):
        c = dict(self.combined)
        c.pop("hit_probability", None)
        gaps = build_data_gaps(c)
        self.assertTrue(any("hit_probability" in g for g in gaps))

    def test_data_freshness_fresh(self):
        self.assertEqual(derive_data_freshness(self.combined), "FRESH")

    def test_data_freshness_missing(self):
        c = {k: v for k, v in self.combined.items()
             if k not in ("hit_probability", "calibrated_probability")}
        self.assertEqual(derive_data_freshness(c), "MISSING")

    def test_data_freshness_stale(self):
        c = {**self.combined, "data_stale": True}
        self.assertEqual(derive_data_freshness(c), "STALE")

    def test_slate_consistency_complete(self):
        self.assertEqual(derive_slate_consistency(self.combined), "CONSISTENT")

    def test_slate_consistency_rejected(self):
        c = dict(self.combined)
        c["gates"]["wnba_evidence_acquisition"]["packet_status"] = "PACKET_INCOMPLETE_REJECTED"
        self.assertEqual(derive_slate_consistency(c), "INCONSISTENT")

    def test_slate_consistency_unknown(self):
        c = {**self.combined, "gates": {}}
        self.assertEqual(derive_slate_consistency(c), "UNKNOWN")

    def test_active_status_mapping_active(self):
        self.assertEqual(map_active_status_to_player_status("ACTIVE"), "ACTIVE")

    def test_active_status_mapping_gtd(self):
        self.assertEqual(map_active_status_to_player_status("GTD"), "QUESTIONABLE")

    def test_active_status_mapping_out(self):
        self.assertEqual(map_active_status_to_player_status("OUT"), "OUT")

    def test_active_status_mapping_dnp(self):
        self.assertEqual(map_active_status_to_player_status("DNP"), "OUT")

    def test_active_status_mapping_none(self):
        self.assertEqual(map_active_status_to_player_status(None), "UNKNOWN")

    def test_active_status_mapping_unknown_string(self):
        self.assertEqual(map_active_status_to_player_status("LIMBO"), "UNKNOWN")

    def test_derive_market_status_scheduled(self):
        self.assertEqual(derive_market_status(self.combined), "OPEN")

    def test_derive_market_status_final(self):
        c = {**self.combined, "event_status": "FINAL"}
        self.assertEqual(derive_market_status(c), "CLOSED")

    def test_derive_market_status_suspended(self):
        c = {**self.combined, "event_status": "SUSPENDED"}
        self.assertEqual(derive_market_status(c), "SUSPENDED")

    def test_derive_market_status_unknown(self):
        c = {**self.combined, "event_status": "LIMBO"}
        self.assertEqual(derive_market_status(c), "UNKNOWN")

    def test_derive_assessment_confidence_high(self):
        self.assertEqual(derive_assessment_confidence(self.combined), "HIGH")

    def test_derive_assessment_confidence_low(self):
        # Remove hit_probability + calibrated_probability but keep projected_minutes
        # → n=1 (only min_ok), packet=COMPLETE → LOW (not UNKNOWN)
        c = dict(self.combined)
        c.pop("calibrated_probability", None)
        c.pop("hit_probability", None)
        self.assertEqual(derive_assessment_confidence(c), "LOW")

    def test_derive_assessment_confidence_unknown(self):
        # Remove all three metric fields → n=0 → UNKNOWN
        c = dict(self.combined)
        c.pop("calibrated_probability", None)
        c.pop("hit_probability", None)
        c["role_status"] = {k: v for k, v in c["role_status"].items()
                            if k != "projected_minutes"}
        self.assertEqual(derive_assessment_confidence(c), "UNKNOWN")

    def test_derive_contradiction_severity_none(self):
        self.assertEqual(derive_contradiction_severity(self.combined), "NONE")

    def test_derive_contradiction_severity_high_rejected(self):
        c = dict(self.combined)
        c["gates"]["wnba_evidence_acquisition"]["packet_status"] = "PACKET_INCOMPLETE_REJECTED"
        self.assertEqual(derive_contradiction_severity(c), "HIGH")

    def test_derive_resolution_recommendation_proceed(self):
        self.assertEqual(derive_resolution_recommendation(self.combined), "PROCEED")

    def test_derive_resolution_recommendation_abort_on_reject(self):
        c = dict(self.combined)
        c["gates"]["wnba_evidence_acquisition"]["packet_status"] = "PACKET_INCOMPLETE_REJECTED"
        self.assertEqual(derive_resolution_recommendation(c), "ABORT")

    def test_derive_failure_detected_false(self):
        self.assertFalse(derive_failure_detected(self.combined))

    def test_derive_failure_detected_true_on_reject(self):
        c = dict(self.combined)
        c["gates"]["wnba_evidence_acquisition"]["packet_status"] = "PACKET_INCOMPLETE_REJECTED"
        self.assertTrue(derive_failure_detected(c))

    def test_derive_contradiction_detected_false(self):
        self.assertFalse(derive_contradiction_detected(self.combined))

    def test_derive_contradiction_detected_true(self):
        c = {**self.combined, "news_contradiction_check": {"conflict_status": "CONFLICT"}}
        self.assertTrue(derive_contradiction_detected(c))

    def test_derive_refresh_status_complete(self):
        self.assertEqual(derive_refresh_status([]), "COMPLETE")

    def test_derive_refresh_status_partial(self):
        self.assertEqual(derive_refresh_status(["MISSING:x"]), "PARTIAL")

    def test_derive_evidence_snapshot_valid_true(self):
        self.assertTrue(derive_evidence_snapshot_valid(self.combined))

    def test_derive_evidence_snapshot_valid_false(self):
        c = dict(self.combined)
        c["gates"]["wnba_evidence_acquisition"]["packet_status"] = "PACKET_INCOMPLETE_REJECTED"
        self.assertFalse(derive_evidence_snapshot_valid(c))

    def test_source_row_fields_used_is_tuple(self):
        self.assertIsInstance(SOURCE_ROW_FIELDS_USED, tuple)
        self.assertIn("event_id", SOURCE_ROW_FIELDS_USED)
        self.assertIn("game_log", SOURCE_ROW_FIELDS_USED)

    def test_field_map_can_execute_false(self):
        self.assertFalse(FIELD_MAP_CAN_EXECUTE)


# ── TestWnbaPropsRoleInputs ───────────────────────────────────────────────────

class TestWnbaPropsRoleInputs(unittest.TestCase):

    def setUp(self):
        self.combined = {**_full_enrichment(), **_minimal_row()}

    def _advisory(self, payload: dict) -> dict:
        return payload["advisory_findings"]

    def test_dsi_builds_valid(self):
        p = build_data_slate_integrity_input(self.combined)
        a = self._advisory(p)
        self.assertEqual(a["role_id"], "DATA_SLATE_INTEGRITY")
        self.assertIn("data_freshness_status", a)
        self.assertIn("slate_consistency_check", a)
        self.assertIsInstance(a["source_coverage"], dict)
        self.assertIsInstance(a["data_gaps_identified"], list)

    def test_dsi_fresh_consistent_when_complete(self):
        a = self._advisory(build_data_slate_integrity_input(self.combined))
        self.assertEqual(a["data_freshness_status"], "FRESH")
        self.assertEqual(a["slate_consistency_check"], "CONSISTENT")

    def test_ns_builds_valid(self):
        p = build_news_status_input(self.combined)
        a = self._advisory(p)
        self.assertEqual(a["role_id"], "NEWS_STATUS")
        self.assertEqual(a["player_status"], "ACTIVE")
        self.assertFalse(a["injury_flag"])
        self.assertFalse(a["dnp_risk"])

    def test_ns_out_sets_injury_flag(self):
        c = dict(self.combined)
        c["role_status"] = {**c["role_status"], "active_status": "OUT"}
        a = self._advisory(build_news_status_input(c))
        self.assertEqual(a["player_status"], "OUT")
        self.assertTrue(a["injury_flag"])

    def test_ns_questionable_sets_dnp_risk(self):
        c = dict(self.combined)
        c["role_status"] = {**c["role_status"], "active_status": "GTD"}
        a = self._advisory(build_news_status_input(c))
        self.assertEqual(a["player_status"], "QUESTIONABLE")
        self.assertTrue(a["dnp_risk"])
        self.assertFalse(a["injury_flag"])

    def test_ns_missing_role_status_gives_unknown(self):
        c = {k: v for k, v in self.combined.items() if k != "role_status"}
        a = self._advisory(build_news_status_input(c))
        self.assertEqual(a["player_status"], "UNKNOWN")

    def test_mel_builds_valid(self):
        p = build_market_exact_line_input(self.combined)
        a = self._advisory(p)
        self.assertEqual(a["role_id"], "MARKET_EXACT_LINE")
        self.assertTrue(a["line_confirmed"])
        self.assertEqual(a["confirmed_line"], 10.5)
        self.assertEqual(a["market_status"], "OPEN")

    def test_mel_no_line_gives_unconfirmed(self):
        c = {k: v for k, v in self.combined.items() if k != "line"}
        a = self._advisory(build_market_exact_line_input(c))
        self.assertFalse(a["line_confirmed"])
        self.assertIsNone(a["confirmed_line"])

    def test_ss_builds_valid(self):
        p = build_sport_specialist_input(self.combined)
        a = self._advisory(p)
        self.assertEqual(a["role_id"], "SPORT_SPECIALIST")
        self.assertEqual(a["sport"], "WNBA")
        self.assertIsInstance(a["statistical_assessment"], dict)
        self.assertIsInstance(a["key_metrics"], list)

    def test_ss_missing_fields_give_missing_sentinel(self):
        c = {k: v for k, v in self.combined.items()
             if k not in ("hit_probability", "calibrated_probability")}
        a = self._advisory(build_sport_specialist_input(c))
        self.assertIn("hit_probability", a["missing_metrics"])

    def test_ss_sport_nba_preserved(self):
        c = {**self.combined, "sport": "NBA"}
        a = self._advisory(build_sport_specialist_input(c))
        self.assertEqual(a["sport"], "NBA")

    def test_fc_builds_valid(self):
        p = build_failure_contradiction_input(self.combined)
        a = self._advisory(p)
        self.assertEqual(a["role_id"], "FAILURE_CONTRADICTION")
        self.assertFalse(a["contradiction_detected"])
        self.assertFalse(a["failure_detected"])
        self.assertEqual(a["resolution_recommendation"], "PROCEED")

    def test_fc_conflict_sets_contradiction(self):
        c = {**self.combined,
             "news_contradiction_check": {"conflict_status": "CONFLICT",
                                          "conflict_detail": "role dispute"}}
        a = self._advisory(build_failure_contradiction_input(c))
        self.assertTrue(a["contradiction_detected"])
        self.assertEqual(a["resolution_recommendation"], "ABORT")

    def test_fr_builds_valid(self):
        p = build_final_refresh_input(self.combined)
        a = self._advisory(p)
        self.assertEqual(a["role_id"], "FINAL_REFRESH")
        self.assertTrue(a["all_roles_completed"])
        self.assertEqual(a["refresh_status"], "COMPLETE")
        self.assertTrue(a["evidence_snapshot_valid"])
        self.assertEqual(len(a["roles_completed"]), 5)
        self.assertEqual(a["roles_missing"], [])

    def test_fr_partial_when_gaps(self):
        c = {k: v for k, v in self.combined.items()
             if k not in ("hit_probability", "calibrated_probability", "game_log",
                          "market_comparison", "event_status")}
        a = self._advisory(build_final_refresh_input(c))
        self.assertEqual(a["refresh_status"], "PARTIAL")

    def test_role_inputs_can_execute_false(self):
        self.assertFalse(ROLE_INPUTS_CAN_EXECUTE)


# ── TestWnbaPropsAdapterFull ──────────────────────────────────────────────────

class TestWnbaPropsAdapterFull(unittest.TestCase):

    def setUp(self):
        self.adapter = WnbaPropsAdapter()
        self.row     = _minimal_row()
        self.enr     = _full_enrichment()

    def test_complete_result_type(self):
        r = self.adapter.adapt(row=self.row, run_id="test-run-1", enrichment=self.enr)
        self.assertIsInstance(r, WnbaPropsAdapterResult)

    def test_complete_status_when_all_fields_present(self):
        r = self.adapter.adapt(row=self.row, run_id="test-run-2", enrichment=self.enr)
        self.assertEqual(r.adapter_status, AdapterStatus.COMPLETE)
        self.assertEqual(r.degradation_reasons, ())

    def test_degraded_status_when_missing_evidence(self):
        row = _minimal_row()
        row.pop("hit_probability", None)
        row.pop("calibrated_probability", None)
        enr = {}
        r = self.adapter.adapt(row=row, run_id="test-run-3", enrichment=enr)
        self.assertEqual(r.adapter_status, AdapterStatus.DEGRADED)
        self.assertTrue(len(r.degradation_reasons) > 0)

    def test_evidence_packet_lane(self):
        r = self.adapter.adapt(row=self.row, run_id="test-run-4", enrichment=self.enr)
        self.assertEqual(r.packet.lane, Lane.WNBA_PROPS)

    def test_evidence_packet_player_name(self):
        r = self.adapter.adapt(row=self.row, run_id="test-run-5", enrichment=self.enr)
        self.assertEqual(r.packet.player_name, "Angel Reese")

    def test_evidence_packet_player_id_none(self):
        r = self.adapter.adapt(row=self.row, run_id="test-run-6", enrichment=self.enr)
        self.assertIsNone(r.packet.player_id)

    def test_six_role_payloads_present(self):
        r = self.adapter.adapt(row=self.row, run_id="test-run-7", enrichment=self.enr)
        expected_roles = {
            "DATA_SLATE_INTEGRITY", "NEWS_STATUS", "MARKET_EXACT_LINE",
            "SPORT_SPECIALIST", "FAILURE_CONTRADICTION", "FINAL_REFRESH",
        }
        self.assertEqual(set(r.role_payloads.keys()), expected_roles)

    def test_enrichment_merged_event_status(self):
        r = self.adapter.adapt(row=self.row, run_id="test-run-8", enrichment=self.enr)
        mel = r.role_payloads["MARKET_EXACT_LINE"]["advisory_findings"]
        self.assertEqual(mel["market_status"], "OPEN")

    def test_row_wins_on_collision(self):
        # event_status in both row and enrichment — row should win
        row = {**_minimal_row(), "event_status": "ROW_VALUE"}
        enr = {**_full_enrichment(), "event_status": "ENRICHMENT_VALUE"}
        r = self.adapter.adapt(row=row, run_id="test-run-9", enrichment=enr)
        mel = r.role_payloads["MARKET_EXACT_LINE"]["advisory_findings"]
        # ROW_VALUE maps to UNKNOWN in market_status
        self.assertEqual(mel["market_status"], "UNKNOWN")

    def test_no_enrichment_succeeds(self):
        r = self.adapter.adapt(row=self.row, run_id="test-run-10")
        self.assertIsInstance(r, WnbaPropsAdapterResult)

    def test_snapshot_id_override(self):
        r = self.adapter.adapt(
            row=self.row, run_id="test-run-11",
            enrichment=self.enr, snapshot_id="fixed-snap"
        )
        self.assertEqual(r.packet.snapshot_id, "fixed-snap")

    def test_missing_run_id_raises(self):
        with self.assertRaises(AdapterInputError):
            self.adapter.adapt(row=self.row, run_id="   ")

    def test_source_row_fields_used_is_tuple(self):
        r = self.adapter.adapt(row=self.row, run_id="test-run-12", enrichment=self.enr)
        self.assertIsInstance(r.source_row_fields_used, tuple)

    def test_result_is_frozen(self):
        r = self.adapter.adapt(row=self.row, run_id="test-run-13", enrichment=self.enr)
        with self.assertRaises((AttributeError, TypeError)):
            r.adapter_status = "MUTATED"  # type: ignore[misc]

    def test_public_api_matches(self):
        r = PubAdapter().adapt(row=self.row, run_id="test-run-14", enrichment=self.enr)
        self.assertIsInstance(r, PubResult)

    def test_nba_sport_accepted(self):
        row = {**_minimal_row(), "sport": "NBA"}
        r = self.adapter.adapt(row=row, run_id="test-run-15", enrichment=self.enr)
        ss = r.role_payloads["SPORT_SPECIALIST"]["advisory_findings"]
        self.assertEqual(ss["sport"], "NBA")


# ── TestWnbaPropsAdapterScopeInvariants ───────────────────────────────────────

class TestWnbaPropsAdapterScopeInvariants(unittest.TestCase):

    FORBIDDEN_GOVERNANCE_KEYS = frozenset({
        "place_bet", "execute_trade", "settlement_authority",
        "terminal_label", "final_decision", "stake_dollars",
        "production_authority", "user_output_authority", "capital_authority",
    })

    def setUp(self):
        self.adapter = WnbaPropsAdapter()
        self.row     = _minimal_row()
        self.enr     = _full_enrichment()

    def _scan_keys(self, obj, path=""):
        """Recursively scan for forbidden governance keys."""
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in self.FORBIDDEN_GOVERNANCE_KEYS:
                    self.fail(f"Forbidden governance key {k!r} found at {path}.{k}")
                self._scan_keys(v, f"{path}.{k}")
        elif isinstance(obj, (list, tuple)):
            for i, item in enumerate(obj):
                self._scan_keys(item, f"{path}[{i}]")

    def test_can_execute_false_all_modules(self):
        self.assertFalse(ADAPTER_CAN_EXECUTE)
        self.assertFalse(VALIDATION_CAN_EXECUTE)
        self.assertFalse(FIELD_MAP_CAN_EXECUTE)
        self.assertFalse(ROLE_INPUTS_CAN_EXECUTE)

    def test_no_governance_keys_in_role_payloads(self):
        r = self.adapter.adapt(row=self.row, run_id="inv-1", enrichment=self.enr)
        for role_id, payload in r.role_payloads.items():
            self._scan_keys(payload, role_id)

    def test_lane_is_wnba_props(self):
        r = self.adapter.adapt(row=self.row, run_id="inv-2", enrichment=self.enr)
        self.assertEqual(r.packet.lane, Lane.WNBA_PROPS)

    def test_packet_frozen(self):
        r = self.adapter.adapt(row=self.row, run_id="inv-3", enrichment=self.enr)
        with self.assertRaises((AttributeError, TypeError)):
            r.packet.lane = "MUTATED"  # type: ignore[misc]

    def test_adapter_is_stateless(self):
        # Two independent calls on same adapter instance produce independent results
        r1 = self.adapter.adapt(row=self.row, run_id="inv-4a", enrichment=self.enr,
                                snapshot_id="snap-a")
        r2 = self.adapter.adapt(row=self.row, run_id="inv-4b", enrichment=self.enr,
                                snapshot_id="snap-b")
        self.assertEqual(r1.packet.snapshot_id, "snap-a")
        self.assertEqual(r2.packet.snapshot_id, "snap-b")


# ── TestWnbaPropsAdapterGameScript ────────────────────────────────────────────

class TestWnbaPropsAdapterGameScript(unittest.TestCase):
    """game_script_shadow field is present when enrichment includes matchup data."""

    def setUp(self):
        self.adapter = WnbaPropsAdapter()

    def test_game_script_shadow_none_without_matchup(self):
        row = _minimal_row()
        enr = _full_enrichment()
        r = self.adapter.adapt(row=row, run_id="gs-1", enrichment=enr)
        # Shadow may be None (missing spread/total) or a dict — just assert type
        self.assertTrue(r.game_script_shadow is None or isinstance(r.game_script_shadow, dict))

    def test_game_script_shadow_dict_with_matchup(self):
        row = _minimal_row()
        enr = {
            **_full_enrichment(),
            "matchup": {"spread": 4.5, "total_line": 163.0},
        }
        r = self.adapter.adapt(row=row, run_id="gs-2", enrichment=enr)
        # Should produce a dict with at least status and ceiling keys
        self.assertIsInstance(r.game_script_shadow, dict)
        self.assertIn("status", r.game_script_shadow)
        self.assertIn("ceiling", r.game_script_shadow)
        self.assertEqual(r.game_script_shadow["ceiling"], "MODEL_QUALIFIED_HOLD")
        self.assertFalse(r.game_script_shadow.get("can_execute", True))

    def test_game_script_shadow_never_raises(self):
        # Even with completely broken enrichment, adapter should not raise
        row = _minimal_row()
        try:
            r = self.adapter.adapt(row=row, run_id="gs-3", enrichment={"garbage": True})
            self.assertIsInstance(r, WnbaPropsAdapterResult)
        except AdapterInputError:
            raise  # real input errors are expected
        except Exception as exc:
            self.fail(f"Adapter raised unexpected exception: {exc}")


if __name__ == "__main__":
    unittest.main()
