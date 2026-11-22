"""Paired incumbent-vs-challenger NFL scoring (research only; cannot promote or execute).

Incumbent probabilities come from decoding artifact 7fee1209-0bed-4bd0-aa0f-fc7a881eff80
(JSON_COEFFICIENT_BUNDLE_V1: standard-scale, logistic, Platt a*logit+b) over
wow_nfl_pregame_feature_rows, read-only. The snapshot file lists game_id:p_home.
Challenger windows mirror the incumbent: train<=2023, calibrate 2024, score 2025/2026.
Usage: python nfl_incumbent_paired_comparison.py games.csv evidence/<snapshot>.txt
"""
import sys
from pathlib import Path
import numpy as np
import nfl_ml_chronological_challenger as m

def main(games, snap):
    inc = {k: float(v) for k, v in (t.split(":") for t in Path(snap).read_text().split())}
    f = m.build_features(m.load_games(Path(games))); f = f[f.y.notna()].copy()
    tr, cal = f[f.season <= 2023], f[f.season == 2024]
    mu = tr[m.FEATURES].mean().values; sd = np.where(tr[m.FEATURES].std().values > 0, tr[m.FEATURES].std().values, 1.0)
    z = lambda x: (x[m.FEATURES].values - mu) / sd
    b = m.fit_logit(z(tr), tr.y.values); pl = m.fit_logit(m.logit(m.predict(b, z(cal)))[:, None], cal.y.values, l2=0)
    for yr in (2025, 2026):
        s = f[f.season == yr].copy(); s["inc"] = s.game_id.map(inc); s = s[s.inc.notna()]
        s["ch"] = m.predict(pl, m.logit(m.predict(b, z(s)))[:, None]); y = s.y.values
        oi, oc = (s.inc > .5) == (s.y > .5), (s.ch > .5) == (s.y > .5)
        print(yr, "n", len(s), "incumbent", m.metrics(s.inc.values, y), "challenger", m.metrics(s.ch.values, y),
              "harmed", int((oi & ~oc).sum()), "fixed", int((~oi & oc).sum()))

if __name__ == "__main__":
    main(*sys.argv[1:3])
