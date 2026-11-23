# WOW NFL Prospective Research Shadow

Read-only scientific owner: existing LLP NFL team-event fitted model. This is a nonpublishing challenger research module.

## Mechanics

- `prospective.py` fits a deterministic 2021–24 L2 logistic model and 2025 Platt calibration using prior-only, checksum-attributed stored feature vectors. It predicts upcoming canonical events using the production prior-feature builder's exact schema. 2026 outcomes never enter fit/calibration.
- `runner.py capture` fetches and hashes the latest canonical nflverse schedule, checks matchup/kickoff and counts of completed team history, then inserts the first pregame record into existing `wow_nfl_ml_challenger_forward_shadow`. Model/feature/schedule hashes and the exact incumbent receipt (where available) are embedded in immutable research metadata. A duplicate is never rewritten.
- `runner.py grade` reconciles a separately retrieved ESPN final with the preserved NFLverse result before one-time grade. ESPN+NFLverse consensus is **not NFL league-official attestation**. Unresolved games remain held/ungraded. Paired Brier delta is preliminary research, not model certification.
- SQL migration `20261117_nfl_prospective_immutable_freeze.sql` stamps actual DB server time, disallows postkickoff inserts and forecast changes, rejects deletes, and permits one postgame grade with source manifest. No new table and no public grants.
- GitHub Actions schedule runs on protected main at 12:17 and 18:17 UTC during NFL months; PRs verify only. It uses existing `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` GitHub secrets and never prints credentials. GitHub execution may be delayed; do not backfill after kickoff. The 48-hour horizon includes Wednesday morning for Thursday Night Football. When all eligible games are held by missing evidence, the scheduled job now fails visibly; an empty window or already-frozen game is a normal idempotent result.

## Verification

```bash
cd artifacts/wow-engine
python -m pytest -q v17/experiments/nfl_prospective/test_prospective.py v17/experiments/nfl_prospective/test_runner.py v17/experiments/nfl_prospective/test_release_contract.py
```

Status remains `PROSPECTIVE_CODE_RESEARCH_ONLY` until the **actual** protected-main schedule runs and recorded DB rows prove prekickoff capture. No automatic model promotion, live slip publication or wagering execution. `V17_TERMINAL_REDUCER` remains exclusive terminal authority.
