import copy, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
import verify_nfl_prop_lineage as v

D = Path(__file__).resolve().parent / "data"
ARTS = json.loads((D / "wow_nfl_prop_artifacts_v1.json").read_text())
REP = json.loads((D / "wow_nfl_prop_training_report_v1.json").read_text())
EV = json.loads((D / "wow_nfl_prop_lineage_evidence_2026-11-11.json").read_text())


def test_receipt_safety_flags_and_five_separate_assertions():
    r = v.build_receipt()
    assert r["can_execute"] is False and r["probability_publishable"] is False and r["grants_model_capability"] is False
    assert len(r["assertions"]) == 5
    assert r["assertions"]["5_verified_production_registration"]["status"] != "VERIFIED"


def test_committed_receipt_matches_regeneration():
    assert json.loads((D / "wow_nfl_prop_lineage_receipt_v1.json").read_text()) == v.build_receipt()


def test_tampered_payload_fails_internal_consistency():
    bad = copy.deepcopy(ARTS)
    bad[0]["artifact_payload"]["tamper"] = 1
    assert not all(v.verify_internal_consistency(bad, REP)[bad[0]["stat_type"]].values())


def test_wrong_dataset_hash_fails():
    bad = copy.deepcopy(ARTS)
    bad[1]["training_dataset_hash"] = "0" * 64
    assert not v.verify_internal_consistency(bad, REP)[bad[1]["stat_type"]]["training_dataset_hash_matches_formula"]


def test_registry_mismatch_detected():
    rows = copy.deepcopy(EV["registry_snapshot"]["rows"])
    rows["RUSHING_YARDS"]["artifact_checksum"] = "f" * 64
    assert not v.verify_registry_matches(ARTS, rows)["RUSHING_YARDS"]["checksum_equal"]
    del rows["PASSING_YARDS"]
    assert not v.verify_registry_matches(ARTS, rows)["PASSING_YARDS"]["registry_row_present"]


def test_trainer_bytes_match_recovered_commit_and_no_time_leakage_split():
    assert v.sha256_bytes((Path(v.ROOT) / "scripts" / "train_nfl_props.py").read_bytes()) == EV["training_code"]["trainer_sha256_at_commit"]
    for lane in v.LANES:
        m = REP["routes"][lane]
        assert max(m["train_seasons"]) < m["calibration_season"] < m["holdout_season"]
        assert m["whole_week_chronological_split"] is True
