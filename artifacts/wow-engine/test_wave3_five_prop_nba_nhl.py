"""Wave 3 FB-NFL-FIVE / FB-NBA-ML / FB-NHL-ML typed-HOLD tests."""

from __future__ import annotations

from v17.wave3_nba_evidence import nba_evidence_preflight, nba_hard_negative
from v17.wave3_nfl_five_prop_evidence import (
    CHRONOLOGY_PARTITION,
    FIVE_FAMILIES,
    RESIDUAL_DISAGREEMENTS,
    family_decision,
    five_family_inventory,
    old_four_nonregression_gate,
)
from v17.wave3_nhl_evidence import nhl_evidence_preflight, reconcile_partition_accounting

SHA = "b" * 64


def test_five_families_all_model_build_required_with_quarantine_blockers():
    inventory = five_family_inventory()
    assert set(inventory) == set(FIVE_FAMILIES)
    for family, evidence in inventory.items():
        assert evidence.status == "MODEL_BUILD_REQUIRED"
        assert evidence.fitted is False
        assert evidence.probability_publishable is False
        assert "FIVE_PROP_UNKNOWN_ROWS_QUARANTINED_3994" in evidence.blockers
        assert "FIVE_PROP_IDENTITY_GAPS_48" in evidence.blockers
        assert family_decision(family, fitted=False, holdout_pass=False)["decision"] == "HOLD"


def test_residuals_and_chronology_and_old_four_guard():
    assert len(RESIDUAL_DISAGREEMENTS) == 2
    assert CHRONOLOGY_PARTITION == {"train": "2021-2023", "calibration": "2024", "holdout": "2025"}
    assert family_decision("FANTASY_SCORE", fitted=True, holdout_pass=True)["decision"] == "CANDIDATE_ONLY"
    assert old_four_nonregression_gate(True)["candidate_published_delta"] == 0
    assert old_four_nonregression_gate(False)["decision"] == "HOLD"
    ready = family_decision("COMPLETIONS", fitted=True, holdout_pass=True)
    assert ready["decision"] == "READY_FOR_INDEPENDENT_MODEL_REVIEW"
    assert ready["probability_publishable"] is False


def _triad(sport: str) -> dict:
    return {
        "sport": sport,
        "market": "MONEYLINE",
        "model_family": f"{sport}_FAMILY",
        "model_version": "v1",
        "fitted_artifact_sha256": SHA,
        "feature_schema_version": f"{sport}_SCHEMA_V1",
        "calibrator_artifact_sha256": SHA,
        "calibration_method": "PLATT_TIME_SPLIT_V1",
        "pregame_corpus_digest": SHA,
        "settlement_basis": "OFFICIAL_FINAL",
    }


def test_nba_preflight_holds_without_overlapping_partitions_or_leakage():
    train = ["2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"]
    cal = ["2026-02-01T00:00:00Z"]
    hold = ["2026-03-01T00:00:00Z"]
    verdict = nba_evidence_preflight(
        triad=_triad("NBA"),
        train=train,
        calibration=cal,
        holdout=hold,
        settlement_includes_ot=True,
        post_tipoff_features_used=False,
    )
    assert verdict["probability_publishable"] is False
    leaked = nba_evidence_preflight(
        triad=_triad("NBA"),
        train=train,
        calibration=cal,
        holdout=hold,
        settlement_includes_ot=True,
        post_tipoff_features_used=True,
    )
    assert "NBA_POST_TIPOFF_FEATURE_LEAKAGE" in leaked["blockers"]
    overlap = nba_evidence_preflight(
        triad=_triad("NBA"),
        train=train + cal,
        calibration=cal,
        holdout=hold,
        settlement_includes_ot=True,
        post_tipoff_features_used=False,
    )
    assert "NBA_PARTITION_EVENT_REUSE" in overlap["blockers"]
    for kind in (
        "swapped_home_away",
        "late_scratch",
        "missing_tip_time",
        "stale_source",
        "missing_artifact",
        "missing_calibrator",
        "bounds_violation",
    ):
        probe = nba_hard_negative(kind, triad=_triad("NBA"))
        assert probe["held_closed"] is True, kind


def test_nhl_rejects_8580_and_holds_on_goalie_rights():
    train = [f"G{i:05d}" for i in range(3676)]
    cal = [f"C{i:05d}" for i in range(1226)]
    test = [f"T{i:05d}" for i in range(1226)]
    accounting = reconcile_partition_accounting(train, cal, test)
    assert accounting["unique_total"] == 6128
    assert accounting["ok"] is True
    bad = reconcile_partition_accounting(train + cal, cal, test)
    assert bad["ok"] is False
    verdict = nhl_evidence_preflight(
        triad=_triad("NHL"),
        train_ids=train,
        calibration_ids=cal,
        test_ids=test,
        settlement_includes_ot_shootout=True,
        goalie_source_permitted=False,
        goalie_second_corroboration=False,
        goalie_confirmation_prelock=False,
    )
    assert verdict["decision"] == "REMAIN_SHADOW_MORE_EVIDENCE_REQUIRED"
    assert "SOURCE_RIGHTS_OR_GOALIE_DATA_HOLD" in verdict["blockers"]
    regulation = nhl_evidence_preflight(
        triad=_triad("NHL"),
        train_ids=train,
        calibration_ids=cal,
        test_ids=test,
        settlement_includes_ot_shootout=False,
        goalie_source_permitted=True,
        goalie_second_corroboration=True,
        goalie_confirmation_prelock=True,
    )
    assert "NHL_REGULATION_1X2_NOT_FULL_GAME_WIN" in regulation["blockers"]
