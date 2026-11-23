# NFL retrospective challenger assessment — November 17, 2026

**Status:** `RESEARCH_REPLAY_ONLY / NO_CERTIFIED_LIFT / NO_MODEL_PROMOTION`

Read-only discovery in connected WOW Supabase, excluding ungraded/tie rows:

| Cohort | Observed research records |
|---|---:|
| NFL prior-feature rows marked eligible | 1,509 (including ties) |
| Binary HOME_WIN / AWAY_WIN valid source-feature rows | 1,504 |
| Training (2021–2024) | 1,072 |
| Calibration (2025) | 284 |
| 2026 holdout | 148 |
| Matched latest pre-event incumbent snapshots | 108 |

**Research-only model:** fixed L2 logistic on 31 predeclared prior-only opponent/team features, fitted 2021–24, Platt calibrated on 2025, then compared once against 2026 historical outcomes. These inputs existed as prior-game statistics but were not all materialized/frozen at game time. This is a retrofitted challenger, **not** a prospectively certified WOW V17 specialist.

| 108 paired 2026 games | Incumbent | Retrospective challenger |
|---|---:|---:|
| Brier | 0.223995 | 0.220345 |
| Log loss | 0.637122 | 0.630135 |
| Winner accuracy | 0.6111 | 0.6759 |

Challenger minus incumbent: Brier `-0.003649`, log loss `-0.006987`. Deterministic week-cluster bootstrap (10 NFL weeks, 2,500 resamples, seed 20261117) 95% Brier difference interval: `[-0.016276, +0.008421]`; log-loss interval `[-0.033863, +0.020500]`. **Both include zero.** Positive point estimates do not establish statistically reliable improvement, nor are source rows independently certified as pregame.

**Point-in-time defect:** All 1,509 eligible rows reference previous-game features (max prior gameday earlier than target), but most were materialized after the corresponding fixture. Of 149 2026 feature rows, only **13 were locked on or before their local gameday**. Date-only locking is weaker than exact kickoff attestation. The existing forward moneyline challenger ledger had **one** row and **zero** graded pregame challenger outcomes at the inventory read. Thus no completed immutable paired forward challenger cohort exists.

**Next scientific action:** Make genuine candidate predictions and exact source snapshots *before* kickoff, archive immutable inputs with model fit artifact, then grade them independently on the same outcome/market basis as incumbent. The merged Nov16/17 research bridges are ready to consume separately fitted game-state scenario distributions only after those exist. Avoid treating the existing NFL winner logit artifact (787 training rows) as a leading/tied/trailing distribution.

No real-money action, automatic pick submission, credential use, fitted-model promotion, or production scoring change is authorized by this report.
