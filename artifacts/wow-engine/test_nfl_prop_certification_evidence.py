import json
from pathlib import Path
from v17.prop_capability_manifest import CERTIFIED_PRODUCTION, prop_capability

ROOT=Path(__file__).resolve().parent
EXPECTED={"PASSING_YARDS","RUSHING_YARDS","RECEIVING_YARDS","ANYTIME_TD"}

def _artifacts(): return json.loads((ROOT/"data/wow_nfl_prop_artifacts_v1.json").read_text())

def test_all_exact_routes_have_passed_frozen_certification_evidence():
    rows=_artifacts(); assert {r["stat_type"] for r in rows}==EXPECTED
    for row in rows:
        m=row["validation_metrics"]
        assert row["certification_eligible"] is True
        assert m["validation_status"]=="PASS" and m["blockers"]==[]
        assert m["holdout_season"]==2025 and m["whole_week_chronological_split"] is True
        assert m["holdout_rows"]>=300 and m["calibration_rows"]>=300 and m["train_rows"]>=1000
        assert m["holdout_ood_rate_z_gt_6"]<=0.08
        assert row["source_license_id"]=="CC-BY-4.0"
        assert row["can_execute"] is False and row["probability_publishable"] is False
        if row["stat_type"]=="ANYTIME_TD": assert m["brier_ratio_vs_naive"]<=1.03
        else: assert m["mae_ratio_vs_naive"]<=1.03

def test_manifest_declares_only_validated_exact_routes_as_nfl_production():
    for stat in EXPECTED:
        lane=prop_capability("NFL",stat)
        assert lane.lane_status==CERTIFIED_PRODUCTION and lane.route_active and lane.publication_allowed
        assert lane.controlling_specialist=="wow.nfl-player-prop-probability-expert"
        assert lane.can_execute is False
    unsupported=prop_capability("NFL","TACKLES")
    assert unsupported.route_active is False and unsupported.publication_allowed is False

def test_activation_migration_is_exact_and_fail_closed():
    sql=(ROOT/"migrations/20260920_nfl_direct_prop_governed_activation.sql").read_text()
    for stat in EXPECTED: assert stat in sql
    assert "wow.nfl-player-prop-probability-expert" in sql
    assert "PROSPECTIVE_CERTIFIED" in sql
    compact=sql.replace(" ","")
    assert "probability_publishable=false" in compact and "can_execute=false" in compact
    assert "MODEL_UNAVAILABLE" in sql
