# NFL Prospective Capture Reliability — Design (2026-11-18)

## Observed problem
PRs #2706/#2707 added scheduled research-only capture, dual-provider grading and a protected PostgreSQL server-clock immutable guard. Live validation DB still has one October 5 challenger row, **zero new Nov 17+ challenger forecasts and zero grades**. An official Nov 19 Colts-at-Texans 8:15 PM ET kickoff is Nov 20 01:15 UTC. The original Wednesday 12:00 UTC sweep's 36-hour horizon ended before this game, postponing the first opportunity to the noon-Central sweep. The old all-held receipt returned a successful exit. GitHub documents delays/drop at minute-zero cron. The available connector cannot attest whether the November 18 scheduled run occurred, so that remains an independent operational verification.

## Minimal design
- Keep the existing protected-main capture job, database table, canonical nflverse SHA evidence, exact kickoff, source age limit and two-source postgame grade.
- Use a **48-hour lookahead**, which admits the Thursday fixture at Wednesday 12:17 UTC while retaining the strict postkickoff prohibition.
- Stagger the two UTC sweeps to **12:17 and 18:17** in existing NFL months, without adding a controller or more runs.
- Return a bounded **run_ok** and status flag: `EVIDENCE_ALL_HELD` when every eligible game is held; `CAPTURE_ACCOUNTING_INCONSISTENT` on impossible counts; retain green idempotence for empty windows and already-frozen rows.
- In the same workflow job, independently inspect the written capture JSON after grading and **fail if the receipt is missing or not run_ok**. Keep `if:always()` artifact retention to preserve the evidence even on failure.
- Run the new tests inside the existing hosted PostgreSQL verification suite on PRs.

## Scientific, security and operational constraints
No model promotion, no team-event publication, no wagers, no Pick'em submission. `WOW_CAN_EXECUTE=false`, `WOW_DRY_RUN_ONLY=true`, `V17_TERMINAL_REDUCER` sole terminal authority. Existing hash, identity and actual DB clock guards are unchanged. No database credentials on PR jobs. Real success requires protected merge, authenticated scheduled/manual capture run and independently verified immutable pregame rows, followed by dual-source grading. This patch does **not** assert a live run succeeded or that the fitted challenger is more accurate.
