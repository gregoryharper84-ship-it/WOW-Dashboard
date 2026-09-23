from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_mlb_pitcher_strikeouts_low_start.py"
spec = importlib.util.spec_from_file_location("mlb_low_start_replay", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def test_bucket_contract_is_explicit_and_disjoint():
    expected = {
        1: "1-2",
        2: "1-2",
        3: "3-5",
        5: "3-5",
        6: "6-9",
        9: "6-9",
        10: "10+",
        50: "10+",
    }
    assert {n: module._bucket_name(n) for n in expected} == expected


def test_negative_binomial_support_normalizes():
    pmf = module._nb_pmf(4.75, 20.0, 20)
    assert set(pmf) == set(range(21))
    assert math.isclose(sum(pmf.values()), 1.0, rel_tol=0.0, abs_tol=1e-12)
    assert all(value >= 0.0 for value in pmf.values())


def test_shrinkage_strengthens_with_more_history():
    league = 0.30
    pitcher = 0.60
    one = module._shrink(pitcher, league, 1, 8.0)
    nine = module._shrink(pitcher, league, 9, 8.0)
    assert league < one < nine < pitcher


def test_report_is_read_only_governance_artifact():
    source = SCRIPT.read_text(encoding="utf-8")
    assert '"classification": "CLASS_C_CHALLENGER_ONLY"' in source
    assert '"production_change": False' in source
    assert '"can_execute": False' in source
    assert "sportsbook" in source.lower()
    assert "production_hydration_min_starts" in source
    assert "candidate_3_to_9" in source
    assert "one_to_two_holdout" in source
