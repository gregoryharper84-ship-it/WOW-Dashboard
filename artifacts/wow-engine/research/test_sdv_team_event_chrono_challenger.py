import sys
from pathlib import Path
import numpy as np, pandas as pd, pytest
sys.path.insert(0, str(Path(__file__).parent))
import sdv_team_event_chrono_challenger as s
from nfl_ml_chronological_challenger import EvidenceError

def nhl_csv(path, seasons=range(2014, 2027), degenerate=(), extra_rows=()):
    rng = np.random.default_rng(3); teams = [f"T{i}" for i in range(10)]; st = dict(zip(teams, rng.normal(0, .5, 10))); rows = []; gid = 0
    for yr in seasons:
        for k in range(160):
            day = pd.Timestamp(f"{yr-1}-10-05") + pd.Timedelta(days=k // 5 * 2)
            h, a = rng.choice(teams, 2, replace=False); gid += 1
            hs, as_ = (4, 1) if yr in degenerate else (max(0, int(rng.poisson(3 + st[h] - st[a] * .5 + .15))), max(0, int(rng.poisson(3))))
            if hs == as_: hs += 1
            rows.append(dict(game_id=gid, game_type="R", game_date=str(day.date()), home_team_abbr=h, away_team_abbr=a,
                             home_score=hs, away_score=as_, game_state="OFF", season=yr))
    rows += list(extra_rows); pd.DataFrame(rows).to_csv(path, index=False); return path

def test_valid_case_and_provenance(tmp_path):
    p = nhl_csv(tmp_path / "n.csv"); r = s.run("NHL", [p], tmp_path / "o")
    assert r["status"] == "CANDIDATE_ONLY_NOT_PROMOTABLE" and r["can_execute"] is False and len(r["artifact_checksum"]) == 64
    assert r["splits"]["train_last"] < r["splits"]["cal_first"] < r["splits"]["holdout_first"]
    assert r["excluded_degenerate_seasons"] == []

def test_degenerate_placeholder_season_excluded_not_trained(tmp_path):
    p = nhl_csv(tmp_path / "n.csv", degenerate=(2016,))
    assert s.run("NHL", [p], tmp_path / "o")["excluded_degenerate_seasons"] == [2016]

def test_preseason_and_nonfinal_dropped_and_season_from_date(tmp_path):
    ex = [dict(game_id=9001, game_type="PR", game_date="2019-09-20", home_team_abbr="T0", away_team_abbr="T1", home_score=1, away_score=0, game_state="OFF", season=2019),
          dict(game_id=9002, game_type="R", game_date="2019-10-20", home_team_abbr="T0", away_team_abbr="T1", home_score=1, away_score=0, game_state="FUT", season=2019)]
    d = s.load_sport("NHL", [nhl_csv(tmp_path / "n.csv", extra_rows=ex)])
    assert not d.game_id.isin(["9001", "9002"]).any() and set(d.season) == set(range(2014, 2027))

def test_stale_history_not_used():
    cfg = dict(s.CONFIG["NHL"]); rows = []
    for i in range(12):
        rows.append(dict(game_id=str(i), season=2016, date=pd.Timestamp("2016-01-01") + pd.Timedelta(days=i), home="A", away="B", home_score=5, away_score=0, neutral=0.0))
    rows.append(dict(game_id="x", season=2024, date=pd.Timestamp("2024-01-10"), home="A", away="B", home_score=1, away_score=0, neutral=0.0))
    feats = s.build_features(pd.DataFrame(rows), cfg)
    assert "x" not in feats.game_id.tolist()  # 2016 form is >365 days old, so 2024 game has no usable history
    rows[-1]["date"] = pd.Timestamp("2016-06-01")
    assert "x" in s.build_features(pd.DataFrame(rows), cfg).game_id.tolist()  # control: fresh history is used

def test_future_result_cannot_change_earlier_features(tmp_path):
    p = nhl_csv(tmp_path / "n.csv"); d = s.load_sport("NHL", [p]); f1 = s.build_features(d, s.CONFIG["NHL"])
    d2 = d.copy(); d2.loc[d2.index[-1], "home_score"] += 40; f2 = s.build_features(d2, s.CONFIG["NHL"])
    assert f1.iloc[:-1].reset_index(drop=True).equals(f2.iloc[:-1].reset_index(drop=True))

def test_ncaaf_adapter_filters_and_neutral_flag(tmp_path):
    rows = [dict(game_id=i, season=2024, game_date="2024-09-0%d" % (1 + i % 8), home_id=1 + i % 6, away_id=7 + i % 6, home_score=24 + i % 11, away_score=20 + i % 9,
                 status="STATUS_FINAL" if i % 10 else "STATUS_CANCELED", neutral_site="true" if i % 7 == 0 else "false") for i in range(1, 120)]
    p = tmp_path / "c.csv"; pd.DataFrame(rows).to_csv(p, index=False)
    d = s.load_sport("NCAAF", [p]); assert (d.game_id.astype(int) % 10 != 0).all() and set(d.neutral) == {0.0, 1.0}

@pytest.mark.parametrize("case,code", [("missing", "SDV_SOURCE_MISSING"), ("schema", "SDV_SOURCE_SCHEMA_MISMATCH"), ("dup", "SDV_SOURCE_DUPLICATE_EVENT_IDENTITY"),
                                        ("date", "SDV_SOURCE_UNPARSEABLE_DATE"), ("sport", "SDV_SPORT_UNSUPPORTED")])
def test_missing_mismatched_evidence_fail_closed(tmp_path, case, code):
    p = tmp_path / "n.csv"
    if case != "missing": nhl_csv(p)
    if case == "schema": pd.read_csv(p).drop(columns=["home_score"]).to_csv(p, index=False)
    if case == "dup": df = pd.read_csv(p); df.loc[1, "game_id"] = df.loc[0, "game_id"]; df.to_csv(p, index=False)
    if case == "date": df = pd.read_csv(p); df.loc[0, "game_date"] = "not-a-date"; df.to_csv(p, index=False)
    with pytest.raises(EvidenceError, match=code): s.load_sport("XXX" if case == "sport" else "NHL", [p])

def test_stale_no_holdout_fails_closed(tmp_path):
    with pytest.raises(EvidenceError, match="SDV_CHRONOLOGICAL_SPLIT_EMPTY"): s.run("NHL", [nhl_csv(tmp_path / "n.csv", seasons=range(2014, 2019))], tmp_path / "o")

def test_invariants():
    assert s.CAN_EXECUTE is False and s.PROMOTION_AUTHORIZED is False and s.PROBABILITY_PUBLISHABLE is False
