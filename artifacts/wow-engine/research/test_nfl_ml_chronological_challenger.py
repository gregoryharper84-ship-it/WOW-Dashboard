import sys, hashlib
from pathlib import Path
import numpy as np, pandas as pd, pytest
sys.path.insert(0, str(Path(__file__).parent))
import nfl_ml_chronological_challenger as m

def synth(path, seasons=range(2015, 2026), dup=False, drop=None):
    rng = np.random.default_rng(7); teams = [f"T{i}" for i in range(12)]; strength = dict(zip(teams, rng.normal(0, 4, 12)))
    rows = []; gid = 0
    for s in seasons:
        day = pd.Timestamp(f"{s}-09-07")
        for w in range(17):
            order = rng.permutation(teams)
            for i in range(0, 12, 2):
                h, a = order[i], order[i + 1]
                mg = strength[h] - strength[a] + 2 + rng.normal(0, 10)
                hs = int(round(24 + mg / 2)); a_s = int(round(24 - mg / 2))
                if hs == a_s: hs += 1
                rows.append(dict(game_id=f"g{gid}", season=s, gameday=(day + pd.Timedelta(days=7 * w)).date(), home_team=h, away_team=a,
                                 home_score=hs, away_score=a_s, home_rest=7, away_rest=7)); gid += 1
    d = pd.DataFrame(rows)
    if dup: d.loc[1, "game_id"] = d.loc[0, "game_id"]
    if drop: d = d.drop(columns=[drop])
    d.to_csv(path, index=False); return d

def test_valid_case_and_no_lookahead(tmp_path):
    p = tmp_path / "g.csv"; d = synth(p)
    res = m.run(p, tmp_path / "o", expected_sha256=hashlib.sha256(p.read_bytes()).hexdigest())
    assert res["status"] == "CANDIDATE_ONLY_NOT_PROMOTABLE" and res["can_execute"] is False
    assert res["holdout"]["challenger_platt"]["brier"] < res["holdout"]["baseline_home_rate"]["brier"]
    assert res["splits"]["train_last"] < res["splits"]["cal_first"] < res["splits"]["holdout_first"]
    f = m.build_features(m.load_games(p))
    first = f.sort_values("gameday").iloc[0]  # no features until MIN_HISTORY games exist
    assert first["gameday"] > pd.Timestamp("2015-09-07") + pd.Timedelta(days=7 * (m.MIN_HISTORY - 1))

def test_future_result_change_cannot_alter_earlier_features(tmp_path):
    p = tmp_path / "g.csv"; d = synth(p); f1 = m.build_features(m.load_games(p))
    d2 = d.copy(); d2.loc[d2.index[-1], "home_score"] += 50; q = tmp_path / "g2.csv"; d2.to_csv(q, index=False)
    f2 = m.build_features(m.load_games(q)); assert f1.iloc[:-1].reset_index(drop=True).equals(f2.iloc[:-1].reset_index(drop=True))

@pytest.mark.parametrize("case,code", [("missing", "NFL_SOURCE_MISSING"), ("checksum", "NFL_SOURCE_CHECKSUM_MISMATCH"),
                                        ("schema", "NFL_SOURCE_SCHEMA_MISMATCH"), ("dup", "NFL_SOURCE_DUPLICATE_EVENT_IDENTITY")])
def test_missing_mismatched_stale_evidence_fail_closed(tmp_path, case, code):
    p = tmp_path / "g.csv"
    if case != "missing": synth(p, dup=(case == "dup"), drop="home_rest" if case == "schema" else None)
    with pytest.raises(m.EvidenceError, match=code):
        m.load_games(p, "0" * 64 if case == "checksum" else None)

def test_stale_history_has_no_holdout(tmp_path):
    p = tmp_path / "g.csv"; synth(p, seasons=range(2015, 2022))
    with pytest.raises(m.EvidenceError, match="NFL_CHRONOLOGICAL_SPLIT_EMPTY"): m.run(p, tmp_path / "o")

def test_invariants():
    assert m.CAN_EXECUTE is False and m.PROMOTION_AUTHORIZED is False and m.PROBABILITY_PUBLISHABLE is False
