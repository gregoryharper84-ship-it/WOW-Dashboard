# WOW Scoring Engine — Replit Engineering Control Plane

Replit is the **backend/service layer** for the WOW betting-model runtime. It is not the WOW Betting Engine identity. All probability formulas, sport logic, terminal labels, and candidate-selection rules live in `gate_engine/` and are governed by external WOW documentation — not in this file.

---

## Universal Project Rules

These rules apply to every session, every agent, every patch. They are non-negotiable and must be checked before any code change is accepted.

- **`can_execute=false`** — no code path may place, route, modify, or cancel wagers. This is unconditional.
- **No downstream gate may erase an upstream blocker.** Once stamped, a blocker survives.
- **Missing evidence fails closed.** Never fabricate data, fill gaps silently, or construct a passing result from absent inputs.
- **Preserve native WOW terminal labels.** No patch may redefine, reorder, or rename terminal labels or the governance hash without explicit authority.
- **No secrets in code, prompts, logs, tests, or documentation.** Use Replit Secrets exclusively.
- **No unrelated refactors.** Every diff must be bounded to the declared `allowed_files` in the patch contract.
- **Every change requires tests, diff evidence, and exact changed-file reporting** before the session ends.
- **Database changes require an explicit migration and rollback strategy.** No schema change ships without both.
- **Production publication requires readiness regression and post-publish smoke verification** using fresh session/run IDs.
- **Checkpoint before risky work; isolated commit after verified work.** Never bundle unrelated changes into one commit.

---

## Run & Operate

| Command | Purpose |
|---------|---------|
| `cd artifacts/flask-scoring-api && PORT=25643 python app.py` | Local dev server (single worker) |
| `python -m gunicorn -c gunicorn_conf.py app:app` | Production-equivalent (2 workers + keepalive) |
| `python -m pytest gate_engine/tests/ -q --tb=short` | Full regression suite |
| `python -m pytest gate_engine/tests/test_production_repair_2026_08_16.py` | Targeted repair tests |
| `bash scripts/wow-preflight` | Pre-patch safety checks |
| `bash scripts/wow-verify-patch <commit-sha>` | Post-patch diff audit |
| `GET /wow/engine/health` | Engine health + governance hash |
| `GET /wow/governance/status` | Full governance status |

---

## Stack

- **Runtime:** Python 3.11, Flask, gunicorn (2 workers, `--preload` disabled to avoid `threading.Lock` deadlocks post-fork)
- **Database:** PostgreSQL via psycopg2; `pg_try_advisory_lock(778597299)` guards shared-DB cron writes
- **AI:** Anthropic Claude via `AI_INTEGRATIONS_ANTHROPIC_API_KEY` proxy
- **External data:** MLB Stats API, BallDontLie, Odds API (free + paid keys), ESPN
- **Deployment:** Replit autoscale; keepalive daemon pings `/wow/engine/health` every 10 min to prevent SIGTERM at 15-min idle

---

## Where Things Live

```
artifacts/flask-scoring-api/
  app.py                        — Flask routes, startup, auth middleware
  gunicorn_conf.py              — Worker config, keepalive daemon, post_fork hook
  gate_engine/
    pipeline.py                 — Core prop-scoring pipeline (run_pipeline)
    board_intake.py             — Row normalization (normalize_board / normalize_row)
    data_contract.py            — Required-field enforcement (ROW + ENRICHMENT fields)
    failure_path.py             — Failure-path matrix structural validator
    acquisition_orchestrator.py — Auto-enrichment key-promotion + stat-key canon
    auto_enrichment.py          — build_auto_enrichment, fetch_missing_game_logs
    auto_game_log.py            — fetch_game_log, MLB Stats API adapter
    labels.py                   — PropLabel enum (canonical terminal labels)
    llp_governance.py           — LLP gate engine (run_llp_governance)
    model_registry.py           — (sport, stat_key) → model_id/status/bounds
    tests/                      — All tests; isolation sentinels in test_stage_a_isolation.py

docs/wow/
  architecture/                 — Architecture decision records (ADRs)
  contracts/                    — Authoritative WOW skill/contract documents
  runbooks/                     — Operational playbooks (deploy, rollback, incident)

scripts/
  wow-preflight                 — Pre-patch safety checks (uncommitted changes, isolation)
  wow-verify-patch              — Post-patch diff audit (forbidden-file scan, test gate)

.agents/skills/
  wow-replit-patch-governor/    — 13-step patch governance skill (load for every WOW patch)
  wow-replit-patch-governor/SKILL.md

.agents/memory/
  MEMORY.md                     — Persistent cross-session index
  *.md                          — Topic files (one per durable lesson)
```

---

## Architecture Decisions

- **gunicorn `--preload` disabled:** `threading.Lock` instances inherited across fork → permanent WORKER TIMEOUT. `post_fork` hook re-creates all locks.
- **`python -m gunicorn` in production run command:** bare `gunicorn` silently fails in Replit deployment container (no output, no port bind).
- **Enrichment keyed by `player:prop` then promoted to `row_id`:** `build_auto_enrichment` writes under `"jeremy peña:hits"` format; `_check_prop_game_log` promotes the full entry to `enrichment[row_id]` so `_get_enrichment` (rid-first) finds the complete enrichment including all sentinels.
- **Governance hash enforced at handshake:** every `/gate-engine/run` call must supply the current `governance_hash` from `GET /wow/governance/status`; mismatch → 409 before pipeline runs.
- **`pg_try_advisory_lock` for shared-DB cron:** 2 gunicorn workers → in-process crons run twice; advisory lock ensures only one worker commits.

---

## User Preferences

- Commit every verified patch independently with full description of root cause, fix, and test evidence.
- Never bundle governance changes with model/probability/schema changes.
- Always run `gate_engine/tests/` full suite before committing; report exact pass/fail count.
- Report changed files explicitly in every session summary.
- Acceptance tests must use fresh session/run IDs and call live endpoints, not mocked paths.

---

## Gotchas

- `slate_validation.run()` fires unconditionally OUTSIDE the `skip_data_contract` guard — pass `target_date=date.today()` in all tests or they get `SLATE_PURGE:DATE_MISMATCH`.
- `_is_counting_stat` uses substring matching (`"kw in sk_low"`); stat keys containing short MLB keywords (e.g. `"ip"`) must add an exact `sk_low == "..."` guard BEFORE the `any()` loop.
- `failure_path_matrix` must be a dict with `PRIMARY_KILL_PATH`, `SECONDARY_KILL_PATH`, `BLACK_SWAN_PATH` — a string sentinel fails `failure_path.run()` structural check.
- `opponent` and `odds_or_payout` are **enrichment-level** fields (not row-level); they go in `enrichment[rid]`, not `raw_row`.
- `model_probability_ledger` triggers Stage-2 prob_ledger pre-check (422 before pipeline) if supplied in incomplete format — omit or provide full Stage-2 ledger.
- nba_api cold-start took 10.7 s → use `_nba_ensure()` lazy-init pattern; never import at module level.
- f-string for INTERVAL literals in psycopg2 (not `%` dict formatting — causes TypeError with `%s`).
