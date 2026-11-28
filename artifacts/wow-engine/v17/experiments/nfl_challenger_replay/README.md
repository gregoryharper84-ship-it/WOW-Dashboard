# WOW V17 — NFL retrospective challenger/replay provenance

Research-only. Existing `v17/team_state_challenger_training.py` and the fitted NFL champion retain model ownership. The Nov 16/17 scenario bridges are unchanged. This experiment supplies deterministic, cutoff-safe **retrospective** baseline comparison, not a fitted score-state distribution or production scoring.

`replay.py` accepts caller-supplied frozen/prior-feature records, fits a predeclared 2021–24 L2 logistic research challenger, calibrates it on 2025, and evaluates it on untouched 2026 outcomes. Paired historical incumbent records must carry the same official event ID and kickoff and have a prediction timestamp strictly before kickoff. It reports Brier, log loss, accuracy, week-clustered paired Brier uncertainty, train/cal/test counts and source-manifest digests. Hashes in input are *claims* that must be externally authenticated; the derived manifest is not proof they existed before kickoff.

**Required JSON input:** `{ "rows": [ ... ], "incumbent": {"<game_id>":{...}} }`. Each row has `game_id,season,week,gameday,feature_cutoff_date,max_prior_gameday,locked_at,event_start_time_utc,feature_order,feature_vector,target_outcome,source_content_sha256s,row_inputs_hash`. `event_start_time_utc` may be null for unmatched historical rows; those cannot establish a pregame timestamp. Each incumbent has `official_event_id,probability` (home full-game probability), `created_at,event_start_time_utc`. The upstream exporter must explicitly choose **one** time-correct incumbent snapshot per game (latest before actual kickoff, not latest after final). Never fill missing predictions from market price. Source provenance must be matched against nflverse source snapshot objects and canonical schedule independently.

Run:

```bash
python -m pytest -q artifacts/wow-engine/v17/experiments/nfl_challenger_replay/test_replay.py
python artifacts/wow-engine/v17/experiments/nfl_challenger_replay/run_replay.py --input /path/to/verified-export.json --out /tmp/nfl-retrospective.json
```

This runner makes **no network calls, schema changes, database writes, model promotions, bets, orders, or Pick'em submissions**. `CAN_EXECUTE=False`; `PROBABILITY_PUBLISHABLE=False`; `V17_TERMINAL_REDUCER` retains exclusive terminal authority. It does not override independent QA or approved release governance.

## Database export guidance (read only)

Use `wow_nfl_pregame_feature_rows` for feature vectors, target and source hashes, and the canonical schedule for exact kickoff. `wow_nfl_event_predictions` contains multiple model receipts per event. Select the intended incumbent model version and **one** actual pre-event snapshot per event, e.g. `DISTINCT ON (official_event_id) ... WHERE created_at < event_start_time_utc ORDER BY official_event_id, created_at DESC`; ensure official ID, kickoff, market and selected model are consistent. Do not treat a reconstructed `locked_at` or pregame-looking feature cutoff as proof of prospective capture.

## Acceptance

* Chronological replay can support a *hypothesis* if feature math is strictly prior-only. It **cannot** establish an immutable pregame challenger receipt when the feature row and fitted prediction were created after kickoff.
* Historical team/event and player scenario distributions still require their own fitting and frozen pregame receipts; this runner does **not** manufacture them.
* Before promotion, independently authenticate source snapshots, confirm lineup/evidence timestamps, freeze the challenger forecast before actual kickoff for enough future games, grade independently settled results, and compare paired sport/market cohorts with calibrated lower bounds and uncertainty.
