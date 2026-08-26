"""
gate_engine/tests/test_mlb_plate_appearances_gate.py

Regression tests for WOW-PATCH-2026-08-06-MLB-PLATE-APPEARANCES-COVERAGE.
Covers:
  - Normalizer alias resolution
  - Model registry lookup
  - Gate routing: DATA_CONTRACT_FAIL, REJECT_DATA_QUALITY, HOLD,
    MICRO_WINDOW, CORE_ELIGIBLE
  - Volatility flag assignment
  - Route-registry PROP_TYPE_REQUIRED_GATES entries
  - Non-PA rows are no-ops
  - can_execute invariant
"""
import pytest
from gate_engine.mlb import plate_appearances_gate as _gate
from gate_engine import normalizer, model_registry, route_registry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FULL_ENRICHMENT = {
    "lineup_slot":                    2,
    "starting_status_confirmed":      True,
    "home_away":                      "away",
    "team_implied_run_total":         4.5,
    "opposing_starter_run_prevention": 3.85,
    "opposing_starter_bb_rate":       0.07,
    "opposing_bullpen_quality":       "AVERAGE",
    "l5_pa_exact_line":               0.60,
    "l10_pa_exact_line":              0.65,
    "l10_pa_median":                  4.0,
    "l10_pa_average":                 4.1,
    "recent_full_game_start_rate":    0.90,
    "platoon_substitution_risk":      "LOW",
    "pinch_hit_risk":                 "LOW",
    "defensive_replacement_risk":     "LOW",
    "expected_pa":                    4.1,
}


def _make_row(stat_key="MLB_PLATE_APPEARANCES", line=3.5, enrichment=None, **extra):
    row = {
        "player":         "Wade Meckler",
        "sport":          "MLB",
        "stat_key":       stat_key,
        "prop_type":      stat_key,
        "line":           line,
        "direction":      "MORE",
        "enrichment":     enrichment if enrichment is not None else dict(_FULL_ENRICHMENT),
        "gates":          {},
        "blockers":       [],
    }
    row.update(extra)
    return row


# ---------------------------------------------------------------------------
# can_execute invariant
# ---------------------------------------------------------------------------

def test_can_execute_false():
    assert _gate.can_execute is False


# ---------------------------------------------------------------------------
# Normalizer alias tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label,expected_stat_key", [
    # Task #124: common user-facing aliases now route to canonical "PA"
    ("plate appearances",  "PA"),
    ("plate appearance",   "MLB_PLATE_APPEARANCES"),   # distinct singular form → Section 18.9
    # NOTE: "plate_appearances" / "pa" now map to "PA" (task #124); tested separately below
    # Section 18.9 gate-specific display labels → MLB_PLATE_APPEARANCES
    ("plate app",                  "MLB_PLATE_APPEARANCES"),
    ("plate app.",                 "MLB_PLATE_APPEARANCES"),
    ("total plate appearances",    "MLB_PLATE_APPEARANCES"),
    ("total pa",                   "MLB_PLATE_APPEARANCES"),
    ("mlb plate appearances",      "MLB_PLATE_APPEARANCES"),
])
def test_normalizer_aliases_resolve_to_canonical(label, expected_stat_key):
    result = normalizer._map_stat_key(label, "MLB")
    stat_key = result.get("stat_key")
    assert stat_key == expected_stat_key, (
        f"Expected {expected_stat_key!r} for {label!r}, got {stat_key!r} (full={result})"
    )


@pytest.mark.parametrize("label,expected_stat_key", [
    # Task #124 canonical PA aliases — these override any MLB_PLATE_APPEARANCES mapping
    ("plate_appearances",  "PA"),
    ("pa",                 "PA"),
])
def test_task124_aliases_resolve_to_pa(label, expected_stat_key):
    """
    Task #124 established that 'plate_appearances' / 'pa' must resolve to
    canonical stat_key 'PA' (Poisson counting model) rather than the
    Section 18.9 gate's MLB_PLATE_APPEARANCES internal stat_key.
    """
    result = normalizer._map_stat_key(label, "MLB")
    stat_key = result.get("stat_key")
    assert stat_key == expected_stat_key, (
        f"Expected {expected_stat_key!r} for {label!r}, got {stat_key!r} (full={result})"
    )


# ---------------------------------------------------------------------------
# Model registry tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stat_key,expected_model", [
    # Section 18.9 specialized gate model
    ("MLB_PLATE_APPEARANCES", "mlb_pa_opportunity_v1"),
    # Canonical PA stat_key (task #124 Poisson model)
    ("PA",               "mlb_counting_poisson_v1"),
    ("PLATE_APPEARANCES", "mlb_counting_poisson_v1"),
])
def test_model_registry_entries_exist(stat_key, expected_model):
    entry = model_registry.lookup("MLB", stat_key)
    assert entry.get("model_id") == expected_model, (
        f"Wrong model_id for {stat_key}: expected {expected_model}, got {entry}"
    )
    assert entry.get("status") == "PROVISIONAL"
    assert "game_log" in (entry.get("minimum_inputs") or [])


# ---------------------------------------------------------------------------
# Route registry tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("prop_type", [
    "MLB_PLATE_APPEARANCES",
    "PA",
    "PLATE_APPEARANCES",
])
def test_route_registry_requires_mlb_pa_gate(prop_type):
    row = _make_row(stat_key=prop_type)
    required = route_registry.get_required_gates(row)
    assert "mlb_pa_gate" in required


# ---------------------------------------------------------------------------
# Non-PA rows are no-ops
# ---------------------------------------------------------------------------

def test_non_pa_row_is_noop():
    row = _make_row(stat_key="OUTS")
    _gate.run(row)
    assert "mlb_pa_gate" not in row["gates"]
    assert not any("MLB_PA_GATE" in b for b in row["blockers"])


def test_nba_row_is_noop():
    row = _make_row(stat_key="REB")
    row["sport"] = "NBA"
    _gate.run(row)
    assert "mlb_pa_gate" not in row["gates"]


# ---------------------------------------------------------------------------
# DATA_CONTRACT_FAIL — missing required fields
# ---------------------------------------------------------------------------

def test_missing_lineup_slot_returns_data_contract_fail():
    """User test: lineup_slot missing → DATA_CONTRACT_FAIL, not guessing."""
    enr = dict(_FULL_ENRICHMENT)
    del enr["lineup_slot"]
    row = _make_row(enrichment=enr)
    _gate.run(row)

    gate_result = row["gates"]["mlb_pa_gate"]
    assert gate_result["result"] == "DATA_CONTRACT_FAIL"
    assert "lineup_slot" in gate_result["missing_fields"]
    assert row["terminal_label"] == "DATA_CONTRACT_FAIL"
    assert any("missing=lineup_slot" in b for b in row["blockers"])


def test_multiple_missing_fields_all_listed():
    enr = dict(_FULL_ENRICHMENT)
    del enr["platoon_substitution_risk"]
    del enr["pinch_hit_risk"]
    row = _make_row(enrichment=enr)
    _gate.run(row)

    gate_result = row["gates"]["mlb_pa_gate"]
    assert gate_result["result"] == "DATA_CONTRACT_FAIL"
    missing = gate_result["missing_fields"]
    assert "platoon_substitution_risk" in missing
    assert "pinch_hit_risk" in missing


def test_none_value_counts_as_missing():
    enr = dict(_FULL_ENRICHMENT)
    enr["team_implied_run_total"] = None
    row = _make_row(enrichment=enr)
    _gate.run(row)
    assert row["gates"]["mlb_pa_gate"]["result"] == "DATA_CONTRACT_FAIL"


def test_existing_terminal_label_not_upgraded_on_contract_fail():
    """DATA_CONTRACT_FAIL never overwrites a more-restrictive existing label."""
    enr = dict(_FULL_ENRICHMENT)
    del enr["lineup_slot"]
    row = _make_row(enrichment=enr)
    row["terminal_label"] = "DATA_CONTRACT_FAIL"
    _gate.run(row)
    assert row["terminal_label"] == "DATA_CONTRACT_FAIL"


# ---------------------------------------------------------------------------
# REJECT_DATA_QUALITY — routing failures
# ---------------------------------------------------------------------------

def test_lineup_unconfirmed_returns_reject_dq():
    enr = dict(_FULL_ENRICHMENT)
    enr["starting_status_confirmed"] = False
    row = _make_row(enrichment=enr)
    _gate.run(row)

    gate = row["gates"]["mlb_pa_gate"]
    assert gate["result"] == "REJECT_DATA_QUALITY"
    assert gate["route_reason"] == "STARTING_LINEUP_UNCONFIRMED"
    assert row["terminal_label"] == "REJECT_DATA_QUALITY"


@pytest.mark.parametrize("bad_val", ["no", "false", "unconfirmed", None, 0])
def test_various_unconfirmed_values_reject(bad_val):
    enr = dict(_FULL_ENRICHMENT)
    enr["starting_status_confirmed"] = bad_val
    row = _make_row(enrichment=enr)
    _gate.run(row)
    # None triggers DATA_CONTRACT_FAIL (missing field), others trigger REJECT_DQ
    result = row["gates"]["mlb_pa_gate"]["result"]
    assert result in ("DATA_CONTRACT_FAIL", "REJECT_DATA_QUALITY")


def test_missing_line_returns_reject_dq():
    row = _make_row(line=None)
    _gate.run(row)
    gate = row["gates"]["mlb_pa_gate"]
    assert gate["result"] == "REJECT_DATA_QUALITY"
    assert gate["route_reason"] == "EXACT_PA_LINE_UNAVAILABLE"


def test_all_l5_l10_none_returns_reject_dq():
    enr = dict(_FULL_ENRICHMENT)
    enr["l5_pa_exact_line"]  = None
    enr["l10_pa_exact_line"] = None
    enr["l10_pa_median"]     = None
    enr["l10_pa_average"]    = None
    row = _make_row(enrichment=enr)
    _gate.run(row)
    gate = row["gates"]["mlb_pa_gate"]
    assert gate["result"] == "REJECT_DATA_QUALITY"
    assert gate["route_reason"] == "L5_L10_LEDGER_UNAVAILABLE"


def test_missing_expected_pa_returns_reject_dq():
    enr = dict(_FULL_ENRICHMENT)
    del enr["expected_pa"]
    row = _make_row(enrichment=enr)
    _gate.run(row)
    gate = row["gates"]["mlb_pa_gate"]
    assert gate["result"] == "REJECT_DATA_QUALITY"
    assert gate["route_reason"] == "PA_DISTRIBUTION_NOT_BUILT"


# ---------------------------------------------------------------------------
# HOLD — slot unresolved
# ---------------------------------------------------------------------------

def test_unknown_slot_returns_hold():
    enr = dict(_FULL_ENRICHMENT)
    enr["lineup_slot"] = "DH"   # non-integer slot
    row = _make_row(enrichment=enr)
    _gate.run(row)
    gate = row["gates"]["mlb_pa_gate"]
    assert gate["result"] == "HOLD"
    assert gate["route_reason"] == "BATTING_SLOT_UNRESOLVED"
    assert row.get("terminal_label") == "MODEL_QUALIFIED_HOLD"


def test_slot_11_returns_hold():
    enr = dict(_FULL_ENRICHMENT)
    enr["lineup_slot"] = 11    # out of valid range
    row = _make_row(enrichment=enr)
    _gate.run(row)
    assert row["gates"]["mlb_pa_gate"]["result"] == "HOLD"


# ---------------------------------------------------------------------------
# MICRO_WINDOW — substitution risk / slot 7-9 instability
# ---------------------------------------------------------------------------

def test_high_platoon_risk_returns_micro_window():
    enr = dict(_FULL_ENRICHMENT)
    enr["platoon_substitution_risk"] = "HIGH"
    row = _make_row(enrichment=enr)
    _gate.run(row)
    gate = row["gates"]["mlb_pa_gate"]
    assert gate["result"] == "MICRO_WINDOW"
    assert gate["route_reason"] == "SUBSTITUTION_RISK_ELEVATED"
    assert row.get("terminal_label") == "MODEL_QUALIFIED_HOLD"


def test_high_defensive_risk_returns_micro_window():
    enr = dict(_FULL_ENRICHMENT)
    enr["defensive_replacement_risk"] = "ELEVATED"
    row = _make_row(enrichment=enr)
    _gate.run(row)
    assert row["gates"]["mlb_pa_gate"]["result"] == "MICRO_WINDOW"


def test_slot_7_9_low_start_rate_returns_micro_window():
    enr = dict(_FULL_ENRICHMENT)
    enr["lineup_slot"]             = 8
    enr["recent_full_game_start_rate"] = 0.55   # below 0.70 threshold
    row = _make_row(enrichment=enr)
    _gate.run(row)
    gate = row["gates"]["mlb_pa_gate"]
    assert gate["result"] == "MICRO_WINDOW"
    assert gate["route_reason"] == "SLOT_7_9_UNSTABLE_START_HISTORY"


def test_slot_7_9_high_start_rate_passes_to_core():
    """Slot 8 with stable starts (≥0.70) should not hit MICRO_WINDOW ceiling."""
    enr = dict(_FULL_ENRICHMENT)
    enr["lineup_slot"]             = 8
    enr["recent_full_game_start_rate"] = 0.85
    row = _make_row(enrichment=enr)
    _gate.run(row)
    assert row["gates"]["mlb_pa_gate"]["result"] == "CORE_ELIGIBLE"


# ---------------------------------------------------------------------------
# CORE_ELIGIBLE — positive path (user-specified test case)
# ---------------------------------------------------------------------------

def test_wade_meckler_away_slot_2_core_eligible():
    """
    User test: Wade Meckler MORE 3.5 Plate Appearances, lineup_slot=2,
    starting_status_confirmed=True, home_away='away', all required fields
    populated → must NOT return DATA_CONTRACT_FAIL; must return a real
    routing decision.
    """
    row = _make_row(
        stat_key="MLB_PLATE_APPEARANCES",
        line=3.5,
        enrichment={
            "lineup_slot":                     2,
            "starting_status_confirmed":       True,
            "home_away":                       "away",
            "team_implied_run_total":          4.5,
            "opposing_starter_run_prevention": 3.85,
            "opposing_starter_bb_rate":        0.07,
            "opposing_bullpen_quality":        "AVERAGE",
            "l5_pa_exact_line":                0.60,
            "l10_pa_exact_line":               0.65,
            "l10_pa_median":                   4.0,
            "l10_pa_average":                  4.1,
            "recent_full_game_start_rate":     0.90,
            "platoon_substitution_risk":       "LOW",
            "pinch_hit_risk":                  "LOW",
            "defensive_replacement_risk":      "LOW",
            "expected_pa":                     4.1,
        },
    )
    _gate.run(row)

    gate = row["gates"]["mlb_pa_gate"]
    # Must not be DATA_CONTRACT_FAIL
    assert gate["result"] != "DATA_CONTRACT_FAIL", (
        f"Unexpectedly got DATA_CONTRACT_FAIL: {gate}"
    )
    # Must produce a real routing decision
    assert gate["result"] in (
        "CORE_ELIGIBLE", "MICRO_WINDOW", "HOLD", "REJECT_DATA_QUALITY"
    ), f"Unexpected result: {gate['result']}"
    # With slot 2, confirmed starter, low risk — should be CORE_ELIGIBLE
    assert gate["result"] == "CORE_ELIGIBLE"
    assert gate["passed"] is True
    assert gate["slot_band"] == "1-3"
    assert gate["volatility_flag"] == "GREEN"
    # No terminal_label pre-set (downstream gates decide)
    assert row.get("terminal_label") is None


def test_core_eligible_gate_passes_true():
    row = _make_row()
    _gate.run(row)
    assert row["gates"]["mlb_pa_gate"]["passed"] is True
    assert row["gates"]["mlb_pa_gate"]["result"] == "CORE_ELIGIBLE"


# ---------------------------------------------------------------------------
# Volatility flags
# ---------------------------------------------------------------------------

def test_volatility_green_slot_1_3_high_start_rate():
    enr = dict(_FULL_ENRICHMENT)
    enr["lineup_slot"]             = 3
    enr["recent_full_game_start_rate"] = 0.95
    enr["platoon_substitution_risk"] = "LOW"
    enr["pinch_hit_risk"]          = "LOW"
    row = _make_row(enrichment=enr)
    _gate.run(row)
    assert row["gates"]["mlb_pa_gate"]["volatility_flag"] == "GREEN"


def test_volatility_yellow_slot_7_9():
    enr = dict(_FULL_ENRICHMENT)
    enr["lineup_slot"]             = 9
    enr["recent_full_game_start_rate"] = 0.80   # stable but slot 7-9
    row = _make_row(enrichment=enr)
    _gate.run(row)
    gate = row["gates"]["mlb_pa_gate"]
    # Gate passes (start_rate ≥ 0.70) — but volatility should be YELLOW
    assert gate["volatility_flag"] == "YELLOW"


def test_volatility_red_low_start_rate():
    enr = dict(_FULL_ENRICHMENT)
    enr["recent_full_game_start_rate"] = 0.40
    row = _make_row(enrichment=enr)
    _gate.run(row)
    # Low start rate → RED (and also platoon risk check may trigger earlier)
    gate = row["gates"]["mlb_pa_gate"]
    # RED triggers MICRO_WINDOW; verify volatility in that case
    assert gate.get("volatility_flag") == "RED" or gate["result"] in (
        "MICRO_WINDOW", "REJECT_DATA_QUALITY"
    )


# ---------------------------------------------------------------------------
# Slot band
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("slot,expected_band", [
    (1, "1-3"), (2, "1-3"), (3, "1-3"),
    (4, "4-6"), (5, "4-6"), (6, "4-6"),
    (7, "7-9"), (8, "7-9"), (9, "7-9"),
])
def test_slot_band_assignment(slot, expected_band):
    assert _gate._slot_band(slot) == expected_band


def test_slot_band_unknown_for_invalid():
    assert _gate._slot_band(None)  == "UNKNOWN"
    assert _gate._slot_band("DH") == "UNKNOWN"
    assert _gate._slot_band(0)    == "UNKNOWN"
    assert _gate._slot_band(10)   == "UNKNOWN"


# ---------------------------------------------------------------------------
# PA stat-key variants all fire the gate
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stat_key", ["MLB_PLATE_APPEARANCES", "PA", "PLATE_APPEARANCES"])
def test_all_stat_key_variants_trigger_gate(stat_key):
    row = _make_row(stat_key=stat_key)
    _gate.run(row)
    assert "mlb_pa_gate" in row["gates"]


# ---------------------------------------------------------------------------
# Negative test from spec: bench player, slot 9, only 2 recent starts
# ---------------------------------------------------------------------------

def test_bench_player_slot_9_two_starts():
    """
    Negative test from WOW-MASTER-SPEC Section 18.9:
    Bench Player, slot 9, recent_starts=2 → NOT auto-approved;
    expect MICRO_WINDOW ceiling or REJECT_DATA_QUALITY, not CORE_ELIGIBLE.
    """
    enr = dict(_FULL_ENRICHMENT)
    enr["lineup_slot"]                 = 9
    enr["recent_full_game_start_rate"] = 0.20    # only ~2 of last 10 games started
    enr["platoon_substitution_risk"]   = "HIGH"
    row = _make_row(player="Bench Player X", enrichment=enr)
    _gate.run(row)

    gate = row["gates"]["mlb_pa_gate"]
    assert gate["result"] != "CORE_ELIGIBLE", (
        "Bench player with slot 9 and platoon risk must not reach CORE_ELIGIBLE"
    )
    assert gate["result"] in ("MICRO_WINDOW", "REJECT_DATA_QUALITY", "HOLD")
