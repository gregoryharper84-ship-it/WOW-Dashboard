"""Offline synthetic regression tests: no live odds/model certification claims."""
from __future__ import annotations

import csv
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v17.offline_market_calibration import evaluate_binary_market_baseline


def sample(**overrides):
    """A two-way, same-book frozen NFL game forecast plus settlement fixture."""
    row = {
        "sport": "NFL", "event_id": "2025_01_ABC_DEF", "market": "MONEYLINE",
        "period": "FULL_GAME", "selection": "HOME", "line": "NO_LINE",
        "direction": "WIN", "home_team_id": "ABC", "away_team_id": "DEF",
        "quote_home_team_id": "ABC", "quote_away_team_id": "DEF",
        "quote_selection": "HOME", "quote_line": "NO_LINE",
        "opposite_quote_selection": "AWAY", "opposite_quote_direction": "WIN",
        "opposite_quote_event_id": "2025_01_ABC_DEF",
        "opposite_quote_market": "MONEYLINE", "opposite_quote_period": "FULL_GAME",
        "opposite_quote_line": "NO_LINE", "opposite_quote_book": "TEST_BOOK",
        "opposite_quote_asof_utc": "2025-09-07T16:45:00Z",
        "opposite_quote_source_sha256": "b" * 64,
        "quote_event_id": "2025_01_ABC_DEF", "settled_event_id": "2025_01_ABC_DEF",
        "quote_market": "MONEYLINE", "quote_period": "FULL_GAME",
        "quote_direction": "WIN", "settled_home_team_id": "ABC",
        "settled_away_team_id": "DEF",
        "settled_market": "MONEYLINE", "settled_period": "FULL_GAME",
        "settled_selection": "HOME", "settled_line": "NO_LINE",
        "settled_direction": "WIN",
        "event_start_utc": "2025-09-07T18:00:00Z",
        "forecast_asof_utc": "2025-09-07T17:00:00Z",
        "latest_feature_available_utc": "2025-09-07T16:30:00Z",
        "training_cutoff_utc": "2025-09-01T00:00:00Z",
        "settled_at_utc": "2025-09-08T00:00:00Z",
        "quote_asof_utc": "2025-09-07T16:45:00Z",
        "partition": "HOLDOUT", "immutable_prediction_id": "pred-1",
        "model_artifact_checksum": "a" * 64,
        "quote_source_sha256": "b" * 64,
        "quote_book": "TEST_BOOK", "quote_outcome_count": "2",
        "market_settlement_type": "BINARY",
        "selection_decimal_odds": "2.0", "opposite_decimal_odds": "2.0",
        "model_probability": "0.7", "result_status": "FINAL",
        "void_or_push": "false", "tie": "false", "outcome": "1",
    }
    return {**row, **overrides}


def test_correct_scoring_no_vig_metrics_reliability_and_no_authority():
    """Two symmetric 2.0 odds form a 0.5 no-vig market baseline."""
    report = evaluate_binary_market_baseline([sample()])
    assert report["eligible_unattested"] == 1 and report["held"] == 0
    assert report["model_brier_unattested"] == pytest.approx(.09)
    assert report["market_brier_unattested"] == pytest.approx(.25)
    assert report["brier_delta_model_minus_market"] == pytest.approx(-.16)
    assert report["market_log_loss_unattested"] == pytest.approx(.69314718)
    assert report["model_reliability_10_bins_unattested"][7]["n"] == 1
    assert report["status"] == "NOT_ENOUGH_EVIDENCE"
    for k in ("source_rights_approved", "input_provenance_attested",
              "independent_calibration_certified", "model_probability_publishable",
              "registration_attempted", "can_execute"):
        assert report[k] is False


def test_same_book_de_vig_rejects_using_raw_vig_as_baseline():
    """1.8 / 2.2 pricing is normalized before Brier and log loss."""
    report = evaluate_binary_market_baseline([sample(selection_decimal_odds="1.8",
                                                     opposite_decimal_odds="2.2")])
    fair = (1 / 1.8) / (1 / 1.8 + 1 / 2.2)
    assert report["market_brier_unattested"] == pytest.approx((1 - fair) ** 2)
    assert fair != pytest.approx(1 / 1.8)


@pytest.mark.parametrize("change,expected", [
    ({"event_id": ""}, "REPLAY_EXACT_IDENTITY_MISMATCH"),
    ({"quote_home_team_id": "BAD"}, "REPLAY_EXACT_IDENTITY_MISMATCH"),
    ({"quote_line": "-3.5"}, "REPLAY_EXACT_IDENTITY_MISMATCH"),
    ({"quote_selection": "AWAY"}, "REPLAY_EXACT_IDENTITY_MISMATCH"),
    ({"quote_event_id": "other-game"}, "REPLAY_EXACT_IDENTITY_MISMATCH"),
    ({"settled_event_id": "other-game"}, "REPLAY_EXACT_IDENTITY_MISMATCH"),
    ({"quote_period": "HALF_GAME"}, "REPLAY_EXACT_IDENTITY_MISMATCH"),
    ({"settled_home_team_id": "OTHER"}, "REPLAY_EXACT_IDENTITY_MISMATCH"),
    ({"quote_asof_utc": "2025-09-07T17:01:00Z"}, "REPLAY_TIME_LEAKAGE_OR_MISMATCH"),
    ({"forecast_asof_utc": "2025-09-07T18:00:00Z"}, "REPLAY_TIME_LEAKAGE_OR_MISMATCH"),
    ({"latest_feature_available_utc": "2025-09-07T17:01:00Z"}, "REPLAY_TIME_LEAKAGE_OR_MISMATCH"),
    ({"training_cutoff_utc": "2025-09-07T17:01:00Z"}, "REPLAY_TIME_LEAKAGE_OR_MISMATCH"),
    ({"event_start_utc": "2025-09-07T18:00:00"}, "REPLAY_TIME_LEAKAGE_OR_MISMATCH"),
    ({"quote_asof_utc": "2025-09-07T15:00:00Z"}, "REPLAY_MARKET_CAPTURE_STALE"),
    ({"quote_outcome_count": "3"}, "REPLAY_MARKET_QUOTE_INVALID"),
    ({"market_settlement_type": "THREE_WAY"}, "REPLAY_MARKET_QUOTE_INVALID"),
    ({"selection_decimal_odds": "inf"}, "REPLAY_MARKET_QUOTE_INVALID"),
    ({"selection_decimal_odds": "1.0"}, "REPLAY_MARKET_QUOTE_INVALID"),
    ({"opposite_decimal_odds": "1e999"}, "REPLAY_MARKET_QUOTE_INVALID"),
    ({"model_probability": "nan"}, "REPLAY_PROBABILITY_PACKAGE_INVALID"),
    ({"model_probability": "0"}, "REPLAY_PROBABILITY_PACKAGE_INVALID"),
    ({"quote_source_sha256": "x" * 64}, "REPLAY_MARKET_QUOTE_INVALID"),
    ({"model_artifact_checksum": ""}, "REPLAY_FITTED_CHECKSUM_MISSING_OR_INVALID"),
    ({"partition": "TRAIN"}, "REPLAY_NOT_HOLDOUT"),
    ({"result_status": "PENDING"}, "REPLAY_UNSETTLED_OR_VOID"),
    ({"void_or_push": "true"}, "REPLAY_UNSETTLED_OR_VOID"),
    ({"tie": "true"}, "REPLAY_UNSETTLED_OR_VOID"),
    ({"outcome": "2"}, "REPLAY_SETTLEMENT_INVALID"),
    ({"immutable_prediction_id": ""}, "REPLAY_MISSING_IMMUTABLE_RECEIPT"),
])
def test_held_fail_closed_bad_evidence(change, expected):
    """One unsupported input invalidates its metric contribution."""
    # Keep the same-book opposite quote aligned when testing the pregame
    # cutoff and stale quote check independently of pair matching.
    if "quote_asof_utc" in change:
        change = {**change, "opposite_quote_asof_utc": change["quote_asof_utc"]}
    r = evaluate_binary_market_baseline([sample(**change)])
    assert r["eligible_unattested"] == 0 and r["held"] == 1
    assert r["rows"][0]["status"] == expected
    assert r["model_brier_unattested"] is None
    assert r["can_execute"] is False


def test_repeated_contradictory_event_identity_rejects_both_records():
    """Never let the first of two duplicate rows influence mean metrics."""
    row = sample()
    duplicate = copy.deepcopy(row)
    duplicate["model_probability"] = "0.99"
    report = evaluate_binary_market_baseline([row, duplicate])
    assert report["eligible_unattested"] == 0 and report["held"] == 2
    assert all(r["status"] == "REPLAY_DUPLICATE_THESIS" for r in report["rows"])


def test_30_fixture_rows_remain_descriptive_and_not_certified():
    """Minimum size changes descriptive status only, never model authority."""
    rows = [sample(event_id=f"game-{i}", quote_event_id=f"game-{i}",
                   opposite_quote_event_id=f"game-{i}",
                   settled_event_id=f"game-{i}", immutable_prediction_id=f"pred-{i}")
            for i in range(30)]
    report = evaluate_binary_market_baseline(rows)
    assert report["status"] == "DESCRIPTIVE_MARKET_BASELINE_NOT_ATTESTED"
    assert report["eligible_unattested"] == 30
    assert report["independent_calibration_certified"] is False
    assert report["statistically_significant_superiority"] is None


def test_csv_cli_does_not_download_and_never_overwrites_input(tmp_path):
    """Unattested input yields JSON report with authority disabled."""
    src = tmp_path / "fixture.csv"
    out = tmp_path / "receipt.json"
    with src.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(sample()))
        writer.writeheader()
        writer.writerow(sample())
    script = Path(__file__).resolve().parents[1] / "v17" / "offline_market_calibration.py"
    run = subprocess.run([sys.executable, str(script), "--csv", str(src), "--out", str(out)],
                         capture_output=True, text=True, check=True)
    assert json.loads(run.stdout)["can_execute"] is False
    assert json.loads(out.read_text())["model_probability_publishable"] is False
    reject = subprocess.run([sys.executable, str(script), "--csv", str(src), "--out", str(src)],
                            capture_output=True, text=True)
    assert reject.returncode != 0
    with src.open() as handle:
        assert sum(1 for _ in handle) == 2
    existing = out.read_bytes()
    refuse = subprocess.run([sys.executable, str(script), "--csv", str(src), "--out", str(out)],
                            capture_output=True, text=True)
    assert refuse.returncode != 0 and out.read_bytes() == existing


def test_invalid_batch_limits_fail_closed():
    """Never relabel tiny synthetic samples as calibrated evidence."""
    with pytest.raises(ValueError, match="PAIR_REPLAY_MINIMUM_N_INVALID"):
        evaluate_binary_market_baseline([sample()], minimum_n=1)
    with pytest.raises(ValueError, match="PAIR_REPLAY_INPUT_INVALID"):
        evaluate_binary_market_baseline([], max_quote_age_minutes=0)
    with pytest.raises(ValueError, match="PAIR_REPLAY_INPUT_INVALID"):
        evaluate_binary_market_baseline({})


def test_opposite_quote_book_mismatch_fails_closed():
    """Opposite price must be evidenced by the same book, not a mixed-book pair."""
    r = evaluate_binary_market_baseline([sample(opposite_quote_book="FOREIGN_BOOK")])
    assert r["eligible_unattested"] == 0 and r["rows"][0]["status"] == "REPLAY_MARKET_QUOTE_PAIR_MISMATCH"


def test_settlement_market_mismatch_fails_closed():
    """Moneyline forecast cannot inherit a spread settlement label."""
    r = evaluate_binary_market_baseline([sample(settled_market="SPREAD")])
    assert r["eligible_unattested"] == 0 and r["rows"][0]["status"] == "REPLAY_EXACT_IDENTITY_MISMATCH"


def test_same_event_conflicting_team_context_holds_all_selections():
    """A conflicting official event must not contribute even across different sides."""
    home = sample()
    away = sample(selection="AWAY", quote_selection="AWAY", settled_selection="AWAY",
                  opposite_quote_selection="HOME", immutable_prediction_id="pred-2",
                  home_team_id="XYZ", quote_home_team_id="XYZ", settled_home_team_id="XYZ")
    r = evaluate_binary_market_baseline([home, away])
    assert r["eligible_unattested"] == 0 and r["held"] == 2
    assert all(x["status"] == "REPLAY_EVENT_CONTEXT_CONFLICT" for x in r["rows"])


@pytest.mark.parametrize("field,value", [
    ("opposite_quote_source_sha256", "invalid"),
    ("opposite_quote_asof_utc", "2025-09-07T16:46:00Z"),
    ("opposite_quote_event_id", "other-game"),
    ("opposite_quote_selection", "HOME"),
    ("opposite_quote_line", "-3.5"),
])
def test_opposite_quote_source_identity_is_independently_required(field, value):
    """Reject mixed source snapshots and market-side identities."""
    r = evaluate_binary_market_baseline([sample(**{field: value})])
    assert r["eligible_unattested"] == 0
    assert r["rows"][0]["status"] == "REPLAY_MARKET_QUOTE_PAIR_MISMATCH"


def test_same_immutable_prediction_receipt_cannot_grade_two_events():
    """A reused pregame prediction ID cannot manufacture independent samples."""
    first = sample()
    second = sample(event_id="other-game", quote_event_id="other-game",
                    opposite_quote_event_id="other-game", settled_event_id="other-game")
    report = evaluate_binary_market_baseline([first, second])
    assert report["eligible_unattested"] == 0 and report["held"] == 2
    assert all(row["status"] == "REPLAY_PREDICTION_RECEIPT_REUSED" for row in report["rows"])


def test_whitespace_book_claim_cannot_be_a_same_book_quote():
    """A blank book identity cannot establish two-sided no-vig comparison."""
    report = evaluate_binary_market_baseline([sample(quote_book=" ", opposite_quote_book=" ")])
    assert report["eligible_unattested"] == 0
    assert report["rows"][0]["status"] == "REPLAY_MARKET_QUOTE_INVALID"


def test_extreme_valid_probability_uses_real_log_loss_without_clipping():
    """Clipping a valid 1e-100 fitted output would understate loss by ~203 nats."""
    import math
    r = evaluate_binary_market_baseline([sample(model_probability="1e-100", outcome="1")])
    assert r["eligible_unattested"] == 1
    assert r["model_log_loss_unattested"] == pytest.approx(-math.log(1e-100))
    assert r["model_brier_unattested"] == pytest.approx(1.0)


def test_underflowing_utc_conversion_holds_only_affected_row():
    """A valid local year-1 datetime can overflow during UTC adjustment."""
    r = evaluate_binary_market_baseline([sample(quote_asof_utc="0001-01-01T00:00:00+01:00",
                                                opposite_quote_asof_utc="0001-01-01T00:00:00+01:00")])
    assert r["eligible_unattested"] == 0 and r["held"] == 1
    assert r["rows"][0]["status"] == "REPLAY_MARKET_QUOTE_PAIR_MISMATCH"


@pytest.mark.parametrize("patch", [
    {"opposite_quote_selection": "AWAY", "opposite_quote_direction": "LOSE"},
    {"market": "SPREAD", "quote_market": "SPREAD", "settled_market": "SPREAD",
     "opposite_quote_market": "SPREAD"},
    {"opposite_quote_selection": "HOME", "opposite_quote_direction": "LOSE"},
])
def test_unsupported_quote_complement_never_becomes_market_baseline(patch):
    """Only two proven opposite moneyline WIN sides are in this pilot contract."""
    r = evaluate_binary_market_baseline([sample(**patch)])
    assert r["eligible_unattested"] == 0
    assert r["rows"][0]["status"] == "REPLAY_MARKET_QUOTE_PAIR_MISMATCH"
