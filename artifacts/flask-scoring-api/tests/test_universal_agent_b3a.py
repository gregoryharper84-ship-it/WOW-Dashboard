"""
tests/test_universal_agent_b3a.py
WOW-PATCH-2026-08-10-UNIVERSAL-AGENT-CORE-V1-B3A

Focused tests for the MLB Moneyline lane adapter.

Coverage
--------
TestMlbMoneylineValidation      — AdapterInputError on every required-field check
TestMlbMoneylineFieldMap         — pure field extraction and derivation functions
TestDsiRoleInput                 — DATA_SLATE_INTEGRITY payload: valid + degraded cases
TestNewsStatusRoleInput          — NEWS_STATUS payload: starter→player_status mapping
TestMarketExactLineRoleInput     — MARKET_EXACT_LINE payload: odds present and absent
TestSportSpecialistRoleInput     — SPORT_SPECIALIST payload: assessment dict and gaps
TestFailureContradictionRoleInput— FAILURE_CONTRADICTION: preflight outcomes mapped
TestFinalRefreshRoleInput        — FINAL_REFRESH: synthesis flags
TestMlbMoneylineAdapterFull      — adapter integration: COMPLETE, DEGRADED, identity
TestAdapterToOrchestrator        — end-to-end: adapter output → B2 orchestrator pipeline

Scope invariants asserted
-------------------------
- can_execute = False on every module
- No governance keys at any depth in any payload
- All six role payloads pass their B1 validators
- EvidencePacket.lane == Lane.MLB_MONEYLINE, frozen, player_id == None
- Full B2 orchestrator pipeline reaches BundleStatus.COMPLETE on all-pass row
"""
from __future__ import annotations

import unittest
from typing import Any

# ── Adapter imports ───────────────────────────────────────────────────────────
from gate_engine.universal_agent.lanes.mlb_moneyline.validation import (
    AdapterInputError,
    validate_mlb_moneyline_row,
    can_execute as VALIDATION_CAN_EXECUTE,
)
from gate_engine.universal_agent.lanes.mlb_moneyline.field_map import (
    build_data_gaps,
    build_source_coverage,
    derive_assessment_confidence,
    derive_contradiction_detected,
    derive_contradiction_severity,
    derive_data_freshness,
    derive_evidence_snapshot_valid,
    derive_failure_detected,
    derive_market_status,
    derive_refresh_status,
    derive_resolution_recommendation,
    derive_slate_consistency,
    extract_canonical_event_id,
    extract_deterministic_model_inputs,
    extract_event_date,
    extract_event_name,
    extract_market_snapshot,
    extract_source_failures,
    extract_source_timestamps,
    extract_team_identity,
    map_starter_to_player_status,
    can_execute as FIELD_MAP_CAN_EXECUTE,
    SOURCE_ROW_FIELDS_USED,
)
from gate_engine.universal_agent.lanes.mlb_moneyline.role_inputs import (
    build_data_slate_integrity_input,
    build_failure_contradiction_input,
    build_final_refresh_input,
    build_market_exact_line_input,
    build_news_status_input,
    build_sport_specialist_input,
    RoleInputBuildError,
    can_execute as ROLE_INPUTS_CAN_EXECUTE,
)
from gate_engine.universal_agent.lanes.mlb_moneyline.adapter import (
    AdapterStatus,
    MlbMoneylineAdapter,
    MlbMoneylineAdapterResult,
    can_execute as ADAPTER_CAN_EXECUTE,
)
from gate_engine.universal_agent.lanes.mlb_moneyline import (
    MlbMoneylineAdapter as PublicAdapter,
    AdapterInputError as PublicAdapterInputError,
    AdapterStatus as PublicAdapterStatus,
)

# ── Universal Agent Core imports ──────────────────────────────────────────────
from gate_engine.universal_agent.evidence_packet import EvidencePacket, Lane
from gate_engine.universal_agent.output_contract import FORBIDDEN_GOVERNANCE_KEYS, _scan_forbidden_keys
from gate_engine.universal_agent.roles.data_slate_integrity import (
    ROLE_ID as DSI_ROLE_ID,
    validate_data_slate_integrity_output,
)
from gate_engine.universal_agent.roles.news_status import (
    ROLE_ID as NS_ROLE_ID,
    validate_news_status_output,
)
from gate_engine.universal_agent.roles.market_exact_line import (
    ROLE_ID as MEL_ROLE_ID,
    validate_market_exact_line_output,
)
from gate_engine.universal_agent.roles.sport_specialist import (
    ROLE_ID as SS_ROLE_ID,
    validate_sport_specialist_output,
)
from gate_engine.universal_agent.roles.failure_contradiction import (
    ROLE_ID as FC_ROLE_ID,
    validate_failure_contradiction_output,
)
from gate_engine.universal_agent.roles.final_refresh import (
    ROLE_ID as FR_ROLE_ID,
    validate_final_refresh_output,
)
from gate_engine.universal_agent.output_contract import OUTPUT_VALID
from gate_engine.universal_agent.orchestrator import run_orchestrator, B1_ROLE_IDS
from gate_engine.universal_agent.bundle_assembler import BundleStatus
from gate_engine.universal_agent.role_runner import MockRoleRunner
from gate_engine.universal_agent.roles.registry_b1 import ALL_B1_ENTRIES, build_b1_registry


# ── Shared test fixtures ──────────────────────────────────────────────────────

def _full_row(**overrides: Any) -> dict:
    """
    Minimal fully-valid MLB moneyline row with all eight coverage fields present
    and preflight PASS. Represents a row that has already passed through
    llp_mlb_winner_preflight.run() in the WOW pipeline.
    """
    row: dict = {
        # Identity
        "event_id":   "mlb-2026-08-10-nyy-bos",
        "sport":      "MLB",
        "market":     "moneyline",
        # Team / event
        "team":       "New York Yankees",
        "opponent":   "Boston Red Sox",
        "team_id":    "nyy",
        "opponent_id": "bos",
        "slate_date": "2026-08-10",
        # Source metadata
        "pulled_at":       "2026-08-10T10:00:00+00:00",
        "starter_source":  "mlb-stats-api",
        "lineup_source":   "mlb-stats-api",
        "weather_source":  "nws-api",
        "odds_source":     "odds-api",
        # Gate 1 — starter / lineup (preflight values)
        "starter_status": "CONFIRMED",
        "lineup_status":  "CONFIRMED",
        # Gate 2 — event / weather (preflight values)
        "event_status":   "SCHEDULED",
        "weather_status": "CLEAR",
        # Gate 3 — no-vig / model (preflight values)
        "kalshi_multiplier":              1.80,
        "sportsbook_no_vig_probability":  0.58,
        "kalshi_breakeven_probability":   0.5556,
        "breakeven_gap":                  0.0244,
        # Moneyline model outputs
        "model_probability":                  0.60,
        "calibrated_probability_lower_bound": 0.575,
        # Odds
        "candidate_odds": -138,
        "opponent_odds":  +118,
        # Preflight outcome
        "preflight_checked":  True,
        "preflight_status":   "PASS",
        "preflight_blockers": [],
        "upgrade_allowed":    True,
        "terminal_label":     "MODEL_QUALIFIED_HOLD",
        # Gate record
        "gates": {
            "mlb_winner_preflight": {
                "hard_blockers":  [],
                "watch_blockers": [],
                "preflight_status": "PASS",
            }
        },
    }
    row.update(overrides)
    return row


def _degraded_row(**overrides: Any) -> dict:
    """
    MLB moneyline row with evidence gaps (starter, weather, odds fields absent)
    and preflight WATCH.
    """
    row: dict = {
        "event_id":    "mlb-2026-08-10-chc-stl",
        "sport":       "MLB",
        "market":      "game winner",
        "team":        "Chicago Cubs",
        "opponent":    "St. Louis Cardinals",
        "slate_date":  "2026-08-10",
        "pulled_at":   "2026-08-10T09:30:00+00:00",
        # Gate 1 — starter absent, lineup confirmed
        "starter_status": None,
        "lineup_status":  "CONFIRMED",
        # Gate 2 — event present, weather absent
        "event_status":   "SCHEDULED",
        "weather_status": None,
        # Gate 3 — no-vig present, model absent
        "sportsbook_no_vig_probability": 0.52,
        "kalshi_multiplier":             1.95,
        "model_probability": None,
        "calibrated_probability_lower_bound": None,
        # Preflight
        "preflight_checked":  True,
        "preflight_status":   "WATCH",
        "preflight_blockers": [
            "NO_DATA_QUALITY:STARTER_STATUS_MISSING",
            "NO_DATA_QUALITY:WEATHER_STATUS_MISSING",
        ],
        "upgrade_allowed": False,
        "gates": {
            "mlb_winner_preflight": {
                "hard_blockers":  [],
                "watch_blockers": [
                    "NO_DATA_QUALITY:STARTER_STATUS_MISSING",
                    "NO_DATA_QUALITY:WEATHER_STATUS_MISSING",
                ],
                "preflight_status": "WATCH",
            }
        },
    }
    row.update(overrides)
    return row


def _fail_row(**overrides: Any) -> dict:
    """MLB moneyline row where Gate 3 hard-failed (no-vig below breakeven)."""
    row: dict = {
        "event_id":    "mlb-2026-08-10-lad-sf",
        "sport":       "MLB",
        "market":      "moneyline",
        "team":        "Los Angeles Dodgers",
        "opponent":    "San Francisco Giants",
        "slate_date":  "2026-08-10",
        "pulled_at":   "2026-08-10T08:00:00+00:00",
        "starter_source": "mlb-stats-api",
        "starter_status": "CONFIRMED",
        "lineup_status":  "CONFIRMED",
        "event_status":   "SCHEDULED",
        "weather_status": "CLEAR",
        "kalshi_multiplier":             1.35,
        "sportsbook_no_vig_probability": 0.71,
        "kalshi_breakeven_probability":  0.7407,
        "breakeven_gap":                 -0.0307,
        "model_probability":             0.70,
        "calibrated_probability_lower_bound": 0.685,
        "preflight_checked":  True,
        "preflight_status":   "FAIL",
        "preflight_blockers": ["NO_VIG_BELOW_BREAKEVEN", "MODEL_LOWER_BOUND_BELOW_BREAKEVEN"],
        "upgrade_allowed":    False,
        "terminal_label":     "MLB_WINNER_PREFLIGHT_BLOCK",
        "gates": {
            "mlb_winner_preflight": {
                "hard_blockers":  ["NO_VIG_BELOW_BREAKEVEN", "MODEL_LOWER_BOUND_BELOW_BREAKEVEN"],
                "watch_blockers": [],
                "preflight_status": "FAIL",
            }
        },
    }
    row.update(overrides)
    return row


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1 — Validation
# ─────────────────────────────────────────────────────────────────────────────

class TestMlbMoneylineValidation(unittest.TestCase):

    def test_can_execute_false(self):
        self.assertFalse(VALIDATION_CAN_EXECUTE)

    def test_valid_row_passes(self):
        validate_mlb_moneyline_row(_full_row())  # must not raise

    def test_non_dict_raises(self):
        with self.assertRaises(AdapterInputError) as ctx:
            validate_mlb_moneyline_row("not-a-dict")
        self.assertEqual(ctx.exception.code, "ADAPTER_INPUT_NOT_DICT")

    def test_non_dict_list_raises(self):
        with self.assertRaises(AdapterInputError) as ctx:
            validate_mlb_moneyline_row([{"event_id": "x"}])
        self.assertEqual(ctx.exception.code, "ADAPTER_INPUT_NOT_DICT")

    def test_wrong_sport_raises(self):
        with self.assertRaises(AdapterInputError) as ctx:
            validate_mlb_moneyline_row(_full_row(sport="NBA"))
        self.assertEqual(ctx.exception.code, "ADAPTER_SPORT_MISMATCH")

    def test_empty_sport_raises(self):
        with self.assertRaises(AdapterInputError) as ctx:
            validate_mlb_moneyline_row(_full_row(sport=""))
        self.assertEqual(ctx.exception.code, "ADAPTER_SPORT_MISMATCH")

    def test_sport_case_insensitive(self):
        validate_mlb_moneyline_row(_full_row(sport="mlb"))  # must not raise

    def test_wrong_market_raises(self):
        with self.assertRaises(AdapterInputError) as ctx:
            validate_mlb_moneyline_row(_full_row(market="strikeouts", prop_type="strikeouts"))
        self.assertEqual(ctx.exception.code, "ADAPTER_MARKET_MISMATCH")

    def test_market_keyword_winner(self):
        validate_mlb_moneyline_row(_full_row(market="game winner"))

    def test_market_keyword_ml(self):
        validate_mlb_moneyline_row(_full_row(market="ML"))

    def test_prop_type_fallback(self):
        row = _full_row()
        del row["market"]
        row["prop_type"] = "moneyline"
        validate_mlb_moneyline_row(row)  # must not raise

    def test_missing_event_id_raises(self):
        row = _full_row()
        del row["event_id"]
        with self.assertRaises(AdapterInputError) as ctx:
            validate_mlb_moneyline_row(row)
        self.assertEqual(ctx.exception.code, "ADAPTER_MISSING_EVENT_ID")

    def test_empty_event_id_raises(self):
        with self.assertRaises(AdapterInputError) as ctx:
            validate_mlb_moneyline_row(_full_row(event_id="   "))
        self.assertEqual(ctx.exception.code, "ADAPTER_MISSING_EVENT_ID")

    def test_adapter_input_error_has_code_and_message(self):
        exc = AdapterInputError("MY_CODE", "my message")
        self.assertEqual(exc.code, "MY_CODE")
        self.assertEqual(exc.message, "my message")


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2 — Field map
# ─────────────────────────────────────────────────────────────────────────────

class TestMlbMoneylineFieldMap(unittest.TestCase):

    def test_can_execute_false(self):
        self.assertFalse(FIELD_MAP_CAN_EXECUTE)

    def test_source_row_fields_used_is_tuple_of_strings(self):
        self.assertIsInstance(SOURCE_ROW_FIELDS_USED, tuple)
        self.assertTrue(all(isinstance(f, str) for f in SOURCE_ROW_FIELDS_USED))
        self.assertIn("event_id", SOURCE_ROW_FIELDS_USED)

    def test_extract_canonical_event_id(self):
        row = _full_row()
        self.assertEqual(extract_canonical_event_id(row), "mlb-2026-08-10-nyy-bos")

    def test_extract_event_name_both_teams(self):
        self.assertEqual(
            extract_event_name(_full_row()),
            "New York Yankees vs Boston Red Sox",
        )

    def test_extract_event_name_team_only(self):
        row = _full_row()
        del row["opponent"]
        # Function returns team name when opponent is absent
        self.assertEqual(extract_event_name(row), "New York Yankees")

    def test_extract_event_date_from_slate_date(self):
        self.assertEqual(extract_event_date(_full_row()), "2026-08-10")

    def test_extract_event_date_none_when_absent(self):
        row = _full_row()
        del row["slate_date"]
        self.assertIsNone(extract_event_date(row))

    def test_extract_team_identity_full(self):
        ids = extract_team_identity(_full_row())
        self.assertEqual(ids["team_name"], "New York Yankees")
        self.assertEqual(ids["opponent_team_name"], "Boston Red Sox")
        self.assertEqual(ids["team_id"], "nyy")

    def test_extract_source_timestamps_present(self):
        ts = extract_source_timestamps(_full_row())
        self.assertIn("pulled_at", ts)
        self.assertEqual(ts["pulled_at"], "2026-08-10T10:00:00+00:00")

    def test_extract_source_timestamps_empty_when_absent(self):
        row = _full_row()
        for k in ("pulled_at", "as_of", "odds_pulled_at", "starter_as_of", "lineup_as_of"):
            row.pop(k, None)
        self.assertEqual(extract_source_timestamps(row), {})

    def test_extract_market_snapshot_has_no_vig(self):
        snap = extract_market_snapshot(_full_row())
        self.assertAlmostEqual(snap["sportsbook_no_vig_probability"], 0.58)
        self.assertAlmostEqual(snap["kalshi_multiplier"], 1.80)

    def test_extract_source_failures_empty_on_pass(self):
        self.assertEqual(extract_source_failures(_full_row()), [])

    def test_extract_source_failures_populated_on_hard_fail(self):
        failures = extract_source_failures(_fail_row())
        self.assertEqual(len(failures), 2)
        sev = {f["severity"] for f in failures}
        self.assertIn("HIGH", sev)

    def test_build_source_coverage_all_available(self):
        cov = build_source_coverage(_full_row())
        self.assertTrue(all(v == "available" for v in cov.values()))

    def test_build_source_coverage_missing_fields(self):
        cov = build_source_coverage(_degraded_row())
        self.assertEqual(cov["starter_status"], "missing")
        self.assertEqual(cov["weather_status"], "missing")
        self.assertEqual(cov["model_probability"], "missing")

    def test_build_data_gaps_empty_on_full_row(self):
        self.assertEqual(build_data_gaps(_full_row()), [])

    def test_build_data_gaps_sorted(self):
        gaps = build_data_gaps(_degraded_row())
        self.assertEqual(gaps, sorted(gaps))
        self.assertIn("MISSING:starter_status", gaps)

    def test_derive_data_freshness_fresh(self):
        self.assertEqual(derive_data_freshness(_full_row()), "FRESH")

    def test_derive_data_freshness_missing_both(self):
        row = _full_row(model_probability=None, sportsbook_no_vig_probability=None)
        self.assertEqual(derive_data_freshness(row), "MISSING")

    def test_derive_data_freshness_stale_flag(self):
        row = _full_row(data_stale=True)
        self.assertEqual(derive_data_freshness(row), "STALE")

    def test_derive_slate_consistency_pass(self):
        self.assertEqual(derive_slate_consistency(_full_row()), "CONSISTENT")

    def test_derive_slate_consistency_fail(self):
        self.assertEqual(derive_slate_consistency(_fail_row()), "INCONSISTENT")

    def test_derive_slate_consistency_unknown(self):
        row = _full_row()
        del row["preflight_status"]
        self.assertEqual(derive_slate_consistency(row), "UNKNOWN")

    def test_map_starter_confirmed_to_active(self):
        self.assertEqual(map_starter_to_player_status("CONFIRMED"), "ACTIVE")

    def test_map_starter_probable_strong_to_active(self):
        self.assertEqual(map_starter_to_player_status("PROBABLE_STRONG"), "ACTIVE")

    def test_map_starter_probable_only_to_questionable(self):
        self.assertEqual(map_starter_to_player_status("PROBABLE_ONLY"), "QUESTIONABLE")

    def test_map_starter_scratched_to_out(self):
        self.assertEqual(map_starter_to_player_status("SCRATCHED"), "OUT")

    def test_map_starter_none_to_unknown(self):
        self.assertEqual(map_starter_to_player_status(None), "UNKNOWN")

    def test_map_starter_unrecognised_to_unknown(self):
        self.assertEqual(map_starter_to_player_status("MYSTERY_STATUS"), "UNKNOWN")

    def test_derive_market_status_open(self):
        self.assertEqual(derive_market_status(_full_row()), "OPEN")

    def test_derive_market_status_closed_postponed(self):
        row = _full_row(event_status="POSTPONED")
        self.assertEqual(derive_market_status(row), "CLOSED")

    def test_derive_market_status_suspended(self):
        row = _full_row(event_status="SUSPENDED")
        self.assertEqual(derive_market_status(row), "SUSPENDED")

    def test_derive_market_status_unknown(self):
        row = _full_row(event_status=None)
        self.assertEqual(derive_market_status(row), "UNKNOWN")

    def test_derive_assessment_confidence_high(self):
        self.assertEqual(derive_assessment_confidence(_full_row()), "HIGH")

    def test_derive_assessment_confidence_low_partial(self):
        row = _degraded_row()  # model_probability absent
        conf = derive_assessment_confidence(row)
        self.assertIn(conf, ("LOW", "MEDIUM", "UNKNOWN"))

    def test_derive_contradiction_detected_true_on_hard(self):
        self.assertTrue(derive_contradiction_detected(_fail_row()))

    def test_derive_contradiction_detected_false_on_pass(self):
        self.assertFalse(derive_contradiction_detected(_full_row()))

    def test_derive_contradiction_severity_high_on_hard(self):
        self.assertEqual(derive_contradiction_severity(_fail_row()), "HIGH")

    def test_derive_contradiction_severity_none_on_pass(self):
        self.assertEqual(derive_contradiction_severity(_full_row()), "NONE")

    def test_derive_resolution_recommendation_proceed(self):
        self.assertEqual(derive_resolution_recommendation(_full_row()), "PROCEED")

    def test_derive_resolution_recommendation_hold(self):
        self.assertEqual(derive_resolution_recommendation(_degraded_row()), "HOLD")

    def test_derive_resolution_recommendation_abort(self):
        self.assertEqual(derive_resolution_recommendation(_fail_row()), "ABORT")

    def test_derive_refresh_status_complete(self):
        self.assertEqual(derive_refresh_status([]), "COMPLETE")

    def test_derive_refresh_status_partial(self):
        self.assertEqual(derive_refresh_status(["MISSING:x"]), "PARTIAL")

    def test_derive_evidence_snapshot_valid_pass(self):
        self.assertTrue(derive_evidence_snapshot_valid(_full_row()))

    def test_derive_evidence_snapshot_valid_fail(self):
        self.assertFalse(derive_evidence_snapshot_valid(_fail_row()))

    def test_derive_evidence_snapshot_valid_watch(self):
        self.assertTrue(derive_evidence_snapshot_valid(_degraded_row()))

    def test_derive_failure_detected_true_on_blockers(self):
        self.assertTrue(derive_failure_detected(_degraded_row()))

    def test_derive_failure_detected_false_on_clean(self):
        self.assertFalse(derive_failure_detected(_full_row()))

    def test_extract_deterministic_model_inputs_keys(self):
        dmi = extract_deterministic_model_inputs(_full_row())
        self.assertIn("model_probability", dmi)
        self.assertAlmostEqual(dmi["model_probability"], 0.60)
        self.assertNotIn("terminal_label_at_capture", dmi)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3 — DSI role input
# ─────────────────────────────────────────────────────────────────────────────

class TestDsiRoleInput(unittest.TestCase):

    def _build(self, row: dict) -> dict:
        return build_data_slate_integrity_input(row)

    def test_can_execute_false(self):
        self.assertFalse(ROLE_INPUTS_CAN_EXECUTE)

    def test_full_row_passes_b1_validator(self):
        payload = self._build(_full_row())
        self.assertIs(validate_data_slate_integrity_output(payload), OUTPUT_VALID)

    def test_full_row_freshness_fresh(self):
        af = self._build(_full_row())["advisory_findings"]
        self.assertEqual(af["data_freshness_status"], "FRESH")

    def test_full_row_consistency_consistent(self):
        af = self._build(_full_row())["advisory_findings"]
        self.assertEqual(af["slate_consistency_check"], "CONSISTENT")

    def test_full_row_no_data_gaps(self):
        af = self._build(_full_row())["advisory_findings"]
        self.assertEqual(af["data_gaps_identified"], [])

    def test_full_row_source_coverage_all_available(self):
        af = self._build(_full_row())["advisory_findings"]
        self.assertTrue(all(v == "available" for v in af["source_coverage"].values()))

    def test_degraded_row_has_gaps(self):
        af = self._build(_degraded_row())["advisory_findings"]
        self.assertGreater(len(af["data_gaps_identified"]), 0)

    def test_degraded_row_still_passes_validator(self):
        payload = self._build(_degraded_row())
        self.assertIs(validate_data_slate_integrity_output(payload), OUTPUT_VALID)

    def test_fail_row_inconsistent(self):
        af = self._build(_fail_row())["advisory_findings"]
        self.assertEqual(af["slate_consistency_check"], "INCONSISTENT")

    def test_no_governance_keys(self):
        payload = self._build(_full_row())
        # _scan_forbidden_keys returns None on clean (no violations)
        self.assertIsNone(_scan_forbidden_keys(payload, FORBIDDEN_GOVERNANCE_KEYS))

    def test_role_id_correct(self):
        af = self._build(_full_row())["advisory_findings"]
        self.assertEqual(af["role_id"], DSI_ROLE_ID)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4 — NEWS_STATUS role input
# ─────────────────────────────────────────────────────────────────────────────

class TestNewsStatusRoleInput(unittest.TestCase):

    def _build(self, row: dict) -> dict:
        return build_news_status_input(row)

    def test_full_row_passes_b1_validator(self):
        self.assertIs(validate_news_status_output(self._build(_full_row())), OUTPUT_VALID)

    def test_confirmed_starter_maps_to_active(self):
        af = self._build(_full_row())["advisory_findings"]
        self.assertEqual(af["player_status"], "ACTIVE")
        self.assertFalse(af["injury_flag"])

    def test_scratched_starter_maps_to_out(self):
        row = _full_row(starter_status="SCRATCHED")
        af = self._build(row)["advisory_findings"]
        self.assertEqual(af["player_status"], "OUT")
        self.assertTrue(af["injury_flag"])

    def test_missing_starter_maps_to_unknown(self):
        row = _degraded_row()
        af = self._build(row)["advisory_findings"]
        self.assertEqual(af["player_status"], "UNKNOWN")
        self.assertFalse(af["injury_flag"])

    def test_probable_only_maps_to_questionable(self):
        row = _full_row(starter_status="PROBABLE_ONLY")
        af = self._build(row)["advisory_findings"]
        self.assertEqual(af["player_status"], "QUESTIONABLE")

    def test_no_governance_keys(self):
        self.assertIsNone(
            _scan_forbidden_keys(self._build(_full_row()), FORBIDDEN_GOVERNANCE_KEYS)
        )

    def test_status_source_fallback_unknown(self):
        row = _full_row()
        del row["starter_source"]
        af = self._build(row)["advisory_findings"]
        self.assertEqual(af["status_source"], "UNKNOWN")

    def test_role_id_correct(self):
        self.assertEqual(self._build(_full_row())["advisory_findings"]["role_id"], NS_ROLE_ID)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 5 — MARKET_EXACT_LINE role input
# ─────────────────────────────────────────────────────────────────────────────

class TestMarketExactLineRoleInput(unittest.TestCase):

    def _build(self, row: dict) -> dict:
        return build_market_exact_line_input(row)

    def test_full_row_passes_b1_validator(self):
        self.assertIs(validate_market_exact_line_output(self._build(_full_row())), OUTPUT_VALID)

    def test_no_vig_present_line_confirmed(self):
        af = self._build(_full_row())["advisory_findings"]
        self.assertTrue(af["line_confirmed"])
        self.assertAlmostEqual(af["confirmed_line"], 0.58)

    def test_no_vig_absent_line_not_confirmed(self):
        row = _full_row(sportsbook_no_vig_probability=None)
        af = self._build(row)["advisory_findings"]
        self.assertFalse(af["line_confirmed"])
        self.assertIsNone(af["confirmed_line"])

    def test_market_status_open_when_scheduled(self):
        af = self._build(_full_row())["advisory_findings"]
        self.assertEqual(af["market_status"], "OPEN")

    def test_market_status_closed_when_postponed(self):
        row = _full_row(event_status="POSTPONED")
        af = self._build(row)["advisory_findings"]
        self.assertEqual(af["market_status"], "CLOSED")

    def test_odds_present(self):
        af = self._build(_full_row())["advisory_findings"]
        self.assertEqual(af["over_odds"], -138)
        self.assertEqual(af["under_odds"], +118)

    def test_no_governance_keys(self):
        self.assertIsNone(
            _scan_forbidden_keys(self._build(_full_row()), FORBIDDEN_GOVERNANCE_KEYS)
        )

    def test_role_id_correct(self):
        self.assertEqual(self._build(_full_row())["advisory_findings"]["role_id"], MEL_ROLE_ID)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 6 — SPORT_SPECIALIST role input
# ─────────────────────────────────────────────────────────────────────────────

class TestSportSpecialistRoleInput(unittest.TestCase):

    def _build(self, row: dict) -> dict:
        return build_sport_specialist_input(row)

    def test_full_row_passes_b1_validator(self):
        self.assertIs(validate_sport_specialist_output(self._build(_full_row())), OUTPUT_VALID)

    def test_sport_is_mlb(self):
        af = self._build(_full_row())["advisory_findings"]
        self.assertEqual(af["sport"], "MLB")

    def test_assessment_is_dict(self):
        af = self._build(_full_row())["advisory_findings"]
        self.assertIsInstance(af["statistical_assessment"], dict)

    def test_full_row_no_missing_metrics(self):
        af = self._build(_full_row())["advisory_findings"]
        self.assertEqual(af["missing_metrics"], [])

    def test_model_probability_in_assessment(self):
        af = self._build(_full_row())["advisory_findings"]
        self.assertAlmostEqual(af["statistical_assessment"]["model_probability"], 0.60)

    def test_degraded_row_missing_metrics_populated(self):
        af = self._build(_degraded_row())["advisory_findings"]
        self.assertIn("model_probability", af["missing_metrics"])

    def test_missing_fields_use_sentinel_not_none(self):
        af = self._build(_degraded_row())["advisory_findings"]
        asmt = af["statistical_assessment"]
        self.assertEqual(asmt["model_probability"], "MISSING")

    def test_no_governance_keys(self):
        self.assertIsNone(
            _scan_forbidden_keys(self._build(_full_row()), FORBIDDEN_GOVERNANCE_KEYS)
        )

    def test_no_governance_keys_in_assessment_dict(self):
        # governance keys must not appear inside statistical_assessment at any depth
        af = self._build(_full_row())["advisory_findings"]
        self.assertIsNone(
            _scan_forbidden_keys(af["statistical_assessment"], FORBIDDEN_GOVERNANCE_KEYS)
        )

    def test_role_id_correct(self):
        self.assertEqual(self._build(_full_row())["advisory_findings"]["role_id"], SS_ROLE_ID)

    def test_assessment_confidence_high_on_full_row(self):
        af = self._build(_full_row())["advisory_findings"]
        self.assertEqual(af["assessment_confidence"], "HIGH")


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 7 — FAILURE_CONTRADICTION role input
# ─────────────────────────────────────────────────────────────────────────────

class TestFailureContradictionRoleInput(unittest.TestCase):

    def _build(self, row: dict) -> dict:
        return build_failure_contradiction_input(row)

    def test_full_row_passes_b1_validator(self):
        self.assertIs(validate_failure_contradiction_output(self._build(_full_row())), OUTPUT_VALID)

    def test_clean_pass_no_contradiction_no_failure(self):
        af = self._build(_full_row())["advisory_findings"]
        self.assertFalse(af["contradiction_detected"])
        self.assertFalse(af["failure_detected"])
        self.assertEqual(af["resolution_recommendation"], "PROCEED")

    def test_watch_row_failure_detected(self):
        af = self._build(_degraded_row())["advisory_findings"]
        self.assertTrue(af["failure_detected"])
        self.assertEqual(af["resolution_recommendation"], "HOLD")

    def test_fail_row_contradiction_and_failure(self):
        af = self._build(_fail_row())["advisory_findings"]
        self.assertTrue(af["contradiction_detected"])
        self.assertTrue(af["failure_detected"])
        self.assertEqual(af["resolution_recommendation"], "ABORT")

    def test_fail_row_severity_high(self):
        af = self._build(_fail_row())["advisory_findings"]
        self.assertEqual(af["contradiction_severity"], "HIGH")

    def test_failures_list_populated_on_fail(self):
        af = self._build(_fail_row())["advisory_findings"]
        self.assertIsInstance(af["failures"], list)
        self.assertGreater(len(af["failures"]), 0)

    def test_failures_list_empty_on_pass(self):
        af = self._build(_full_row())["advisory_findings"]
        self.assertEqual(af["failures"], [])

    def test_no_governance_keys(self):
        self.assertIsNone(
            _scan_forbidden_keys(self._build(_fail_row()), FORBIDDEN_GOVERNANCE_KEYS)
        )

    def test_role_id_correct(self):
        self.assertEqual(self._build(_full_row())["advisory_findings"]["role_id"], FC_ROLE_ID)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 8 — FINAL_REFRESH role input
# ─────────────────────────────────────────────────────────────────────────────

class TestFinalRefreshRoleInput(unittest.TestCase):

    def _build(self, row: dict) -> dict:
        return build_final_refresh_input(row)

    def test_full_row_passes_b1_validator(self):
        self.assertIs(validate_final_refresh_output(self._build(_full_row())), OUTPUT_VALID)

    def test_all_roles_completed_true(self):
        af = self._build(_full_row())["advisory_findings"]
        self.assertTrue(af["all_roles_completed"])

    def test_roles_completed_has_five_entries(self):
        af = self._build(_full_row())["advisory_findings"]
        self.assertEqual(len(af["roles_completed"]), 5)

    def test_roles_missing_empty(self):
        af = self._build(_full_row())["advisory_findings"]
        self.assertEqual(af["roles_missing"], [])

    def test_refresh_status_complete_on_full_row(self):
        af = self._build(_full_row())["advisory_findings"]
        self.assertEqual(af["refresh_status"], "COMPLETE")

    def test_refresh_status_partial_on_degraded(self):
        af = self._build(_degraded_row())["advisory_findings"]
        self.assertEqual(af["refresh_status"], "PARTIAL")

    def test_snapshot_valid_true_on_pass(self):
        af = self._build(_full_row())["advisory_findings"]
        self.assertTrue(af["evidence_snapshot_valid"])

    def test_snapshot_valid_false_on_fail(self):
        af = self._build(_fail_row())["advisory_findings"]
        self.assertFalse(af["evidence_snapshot_valid"])

    def test_snapshot_valid_true_on_watch(self):
        af = self._build(_degraded_row())["advisory_findings"]
        self.assertTrue(af["evidence_snapshot_valid"])

    def test_no_governance_keys(self):
        self.assertIsNone(
            _scan_forbidden_keys(self._build(_full_row()), FORBIDDEN_GOVERNANCE_KEYS)
        )

    def test_role_id_correct(self):
        self.assertEqual(self._build(_full_row())["advisory_findings"]["role_id"], FR_ROLE_ID)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 9 — Adapter integration
# ─────────────────────────────────────────────────────────────────────────────

class TestMlbMoneylineAdapterFull(unittest.TestCase):

    def setUp(self):
        self.adapter = MlbMoneylineAdapter()

    def test_can_execute_false(self):
        self.assertFalse(ADAPTER_CAN_EXECUTE)

    def test_full_row_returns_result(self):
        result = self.adapter.adapt(row=_full_row(), run_id="run-001")
        self.assertIsInstance(result, MlbMoneylineAdapterResult)

    def test_result_is_frozen(self):
        result = self.adapter.adapt(row=_full_row(), run_id="run-001")
        with self.assertRaises((AttributeError, TypeError)):
            result.adapter_status = "HACKED"  # type: ignore[misc]

    def test_packet_is_evidence_packet(self):
        result = self.adapter.adapt(row=_full_row(), run_id="run-001")
        self.assertIsInstance(result.packet, EvidencePacket)

    def test_packet_lane_is_mlb_moneyline(self):
        result = self.adapter.adapt(row=_full_row(), run_id="run-001")
        self.assertEqual(result.packet.lane, Lane.MLB_MONEYLINE)

    def test_packet_is_frozen(self):
        result = self.adapter.adapt(row=_full_row(), run_id="run-001")
        with self.assertRaises(Exception):
            result.packet.lane = "HACKED"  # type: ignore[misc]

    def test_packet_player_id_is_none(self):
        # Team-level market — no individual player
        result = self.adapter.adapt(row=_full_row(), run_id="run-001")
        self.assertIsNone(result.packet.player_id)
        self.assertIsNone(result.packet.player_name)

    def test_packet_event_id_matches_row(self):
        result = self.adapter.adapt(row=_full_row(), run_id="run-001")
        self.assertEqual(result.packet.canonical_event_id, "mlb-2026-08-10-nyy-bos")

    def test_packet_run_id_matches_arg(self):
        result = self.adapter.adapt(row=_full_row(), run_id="my-run-xyz")
        self.assertEqual(result.packet.run_id, "my-run-xyz")

    def test_packet_team_name_populated(self):
        result = self.adapter.adapt(row=_full_row(), run_id="run-001")
        self.assertEqual(result.packet.team_name, "New York Yankees")
        self.assertEqual(result.packet.opponent_team_name, "Boston Red Sox")

    def test_full_row_status_complete(self):
        result = self.adapter.adapt(row=_full_row(), run_id="run-001")
        self.assertEqual(result.adapter_status, AdapterStatus.COMPLETE)

    def test_full_row_no_degradation_reasons(self):
        result = self.adapter.adapt(row=_full_row(), run_id="run-001")
        self.assertEqual(result.degradation_reasons, ())

    def test_degraded_row_status_degraded(self):
        result = self.adapter.adapt(row=_degraded_row(), run_id="run-002")
        self.assertEqual(result.adapter_status, AdapterStatus.DEGRADED)

    def test_degraded_row_has_degradation_reasons(self):
        result = self.adapter.adapt(row=_degraded_row(), run_id="run-002")
        self.assertGreater(len(result.degradation_reasons), 0)
        for reason in result.degradation_reasons:
            self.assertTrue(reason.startswith("MISSING:"))

    def test_six_role_payloads_present(self):
        result = self.adapter.adapt(row=_full_row(), run_id="run-001")
        expected = {DSI_ROLE_ID, NS_ROLE_ID, MEL_ROLE_ID, SS_ROLE_ID, FC_ROLE_ID, FR_ROLE_ID}
        self.assertEqual(set(result.role_payloads.keys()), expected)

    def test_all_role_payloads_pass_b1_validators(self):
        result = self.adapter.adapt(row=_full_row(), run_id="run-001")
        validators = {
            DSI_ROLE_ID: validate_data_slate_integrity_output,
            NS_ROLE_ID:  validate_news_status_output,
            MEL_ROLE_ID: validate_market_exact_line_output,
            SS_ROLE_ID:  validate_sport_specialist_output,
            FC_ROLE_ID:  validate_failure_contradiction_output,
            FR_ROLE_ID:  validate_final_refresh_output,
        }
        for role_id, validator in validators.items():
            with self.subTest(role_id=role_id):
                self.assertIs(validator(result.role_payloads[role_id]), OUTPUT_VALID)

    def test_no_governance_keys_in_any_role_payload(self):
        result = self.adapter.adapt(row=_full_row(), run_id="run-001")
        for role_id, payload in result.role_payloads.items():
            with self.subTest(role_id=role_id):
                # _scan_forbidden_keys returns None on clean (no violations)
                self.assertIsNone(
                    _scan_forbidden_keys(payload, FORBIDDEN_GOVERNANCE_KEYS),
                    msg=f"Governance keys found in {role_id}",
                )

    def test_source_row_fields_used_is_tuple(self):
        result = self.adapter.adapt(row=_full_row(), run_id="run-001")
        self.assertIsInstance(result.source_row_fields_used, tuple)
        self.assertIn("event_id", result.source_row_fields_used)

    def test_wrong_sport_raises_adapter_input_error(self):
        with self.assertRaises(AdapterInputError):
            self.adapter.adapt(row=_full_row(sport="NBA"), run_id="run-001")

    def test_missing_event_id_raises_adapter_input_error(self):
        row = _full_row()
        del row["event_id"]
        with self.assertRaises(AdapterInputError):
            self.adapter.adapt(row=row, run_id="run-001")

    def test_empty_run_id_raises_adapter_input_error(self):
        with self.assertRaises(AdapterInputError):
            self.adapter.adapt(row=_full_row(), run_id="")

    def test_snapshot_id_override(self):
        result = self.adapter.adapt(
            row=_full_row(), run_id="run-001", snapshot_id="fixed-snap-id"
        )
        self.assertEqual(result.packet.snapshot_id, "fixed-snap-id")

    def test_fail_row_still_produces_result(self):
        # A hard-failed preflight still produces a valid adapter result
        # (authority to reject remains in WOW pipeline, not adapter)
        result = self.adapter.adapt(row=_fail_row(), run_id="run-003")
        self.assertIsInstance(result, MlbMoneylineAdapterResult)
        self.assertEqual(result.packet.lane, Lane.MLB_MONEYLINE)

    def test_public_api_matches(self):
        result = PublicAdapter().adapt(row=_full_row(), run_id="run-pub")
        self.assertIsInstance(result, MlbMoneylineAdapterResult)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 10 — Adapter → B2 orchestrator end-to-end pipeline
# ─────────────────────────────────────────────────────────────────────────────

class TestAdapterToOrchestrator(unittest.TestCase):
    """
    Proves that an MlbMoneylineAdapterResult can be plugged directly into
    the B2 orchestrator and produces a BundleStatus.COMPLETE result when
    all six role payloads pass.
    """

    def _run_pipeline(self, row: dict) -> "OrchestratorResult":
        adapter = MlbMoneylineAdapter()
        result  = adapter.adapt(row=row, run_id="e2e-run-001")

        # Build a MockRoleRunner preset from the adapter's role_payloads.
        # One MockRoleRunner instance serves all six agents.
        registry = build_b1_registry()
        presets: dict = {}
        for entry in ALL_B1_ENTRIES:
            presets[entry.agent_id] = result.role_payloads[entry.role]
        runner = MockRoleRunner(presets)

        role_runners = {entry.agent_id: runner for entry in ALL_B1_ENTRIES}

        return run_orchestrator(
            packet=result.packet,
            registry=registry,
            role_runners=role_runners,
            db_conn=None,
        )

    def test_full_row_pipeline_accepted_count_six(self):
        orch = self._run_pipeline(_full_row())
        self.assertEqual(orch.accepted_count(), 6)

    def test_full_row_pipeline_bundle_status_complete(self):
        orch = self._run_pipeline(_full_row())
        self.assertEqual(orch.bundle.bundle_status, BundleStatus.COMPLETE)

    def test_full_row_pipeline_no_contradictions(self):
        orch = self._run_pipeline(_full_row())
        # No HIGH contradictions expected on clean PASS row
        high_contras = [c for c in orch.contradictions if c.severity == "HIGH"]
        self.assertEqual(high_contras, [])

    def test_full_row_pipeline_packet_identity_preserved(self):
        adapter = MlbMoneylineAdapter()
        result  = adapter.adapt(row=_full_row(), run_id="e2e-identity-001")
        registry = build_b1_registry()
        presets  = {e.agent_id: result.role_payloads[e.role] for e in ALL_B1_ENTRIES}
        runner   = MockRoleRunner(presets)
        role_runners = {e.agent_id: runner for e in ALL_B1_ENTRIES}

        run_orchestrator(
            packet=result.packet,
            registry=registry,
            role_runners=role_runners,
        )
        # All 6 calls must have received the exact same packet object
        seen_ids = runner.packet_ids_seen()
        self.assertEqual(len(seen_ids), 6)
        self.assertEqual(len(set(seen_ids)), 1, "All runners must receive the same packet object")

    def test_degraded_row_pipeline_still_completes(self):
        orch = self._run_pipeline(_degraded_row())
        # Degraded rows produce DEGRADED (or PARTIAL bundle) but all 6 roles accepted
        self.assertEqual(orch.accepted_count(), 6)

    def test_degraded_row_bundle_status_partial_or_complete(self):
        # Degraded row has data gaps — FINAL_REFRESH.refresh_status=PARTIAL
        # which may trigger RULE-4 contradiction if final_refresh claims completed
        # but the B2 contradiction rules only fire on ACCEPTED roles. Since all
        # 6 are accepted, the bundle is COMPLETE unless a HIGH contradiction fires.
        orch = self._run_pipeline(_degraded_row())
        self.assertIn(orch.bundle.bundle_status, (BundleStatus.COMPLETE, BundleStatus.PARTIAL))

    def test_fail_row_pipeline_accepted_count_six(self):
        # Hard-failed preflight: adapter still produces valid payloads;
        # the existing WOW pipeline (not the adapter) owns the rejection authority.
        orch = self._run_pipeline(_fail_row())
        self.assertEqual(orch.accepted_count(), 6)

    def test_packet_lane_in_bundle(self):
        orch = self._run_pipeline(_full_row())
        self.assertEqual(orch.bundle.lane, Lane.MLB_MONEYLINE)

    def test_orchestrator_result_frozen(self):
        orch = self._run_pipeline(_full_row())
        with self.assertRaises(Exception):
            orch.persisted = True  # type: ignore[misc]

    def test_b1_role_ids_all_in_accepted(self):
        orch = self._run_pipeline(_full_row())
        self.assertEqual(set(orch.bundle.accepted_role_ids), set(B1_ROLE_IDS))


if __name__ == "__main__":
    unittest.main()
