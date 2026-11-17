# Model restoration: implementation and acceptance

This repair preserves the existing V17 runtime, fitted specialist contracts,
terminal reducer, immutable ledgers and `can_execute=false`. It does not certify
an artifact or replace a missing probability with sportsbook odds or an LLM.

## What changes

- Canonical prop requests distinguish publication-only research from model or
  infrastructure failure. A research-only continuation cannot write a governed
  prediction, publish, rank or enter card construction.
- NFL history uses official provider season and completed-game chronology before
  the official target kickoff. Calendar year and week number alone are not dates.
- NBA maintenance includes the season-ending year and verifies actual current
  canonical source/persisted history. Historical replay is separate from a fresh
  acquisition and cannot backdate evidence.
- The reviewed three-start minimum is limited to MLB pitcher strikeouts. Other
  sport/stat routes retain ten observations. Both Python and the database evidence
  function must agree before a 3–9-start request can complete acquisition.
- The real integration job uses Redis and a separate Celery worker, not eager
  in-process execution. Native Render acceptance verifies both service commits
  before and after its scoring lanes, including the full-board lane.
- Both worker launch commands use `python -m celery`, which keeps the engine
  directory importable when tasks load local dependencies after startup.
- The dashboard has a protected canonical V17 request/receipt surface. Historical
  Flask pages remain explicitly legacy and do not become V17 model authority.

## Database release dependency

Apply `artifacts/wow-engine/migrations/20261107064224_v17_mlb_strikeout_snapshot_min3.sql`
through the existing reviewed migration procedure to the authenticated runtime
database. Do not run it against a guessed project. It requires the existing
opponent-context and detailed-evidence snapshot migrations. The new migration
changes only the exact strikeout history floor, preserves detailed evidence,
reasserts backend-only execution grants and leaves publication/execution false.
It does not update any artifact, forecast, outcome or calibration policy.

Until that migration is applied and read back, 3–9-start database acquisition
remains an explicit deployment dependency even if application tests pass.

## Reproducible validation

From the repository root, use the project Python environment and installed pnpm
workspace dependencies:

```sh
python -m pytest -q artifacts/wow-engine/test_pick_request_publication_research.py
python -m pytest -q artifacts/wow-engine/test_nfl_prop_history_chronology.py
python -m pytest -q artifacts/wow-engine/test_basketball_current_season_restoration.py
pnpm run typecheck
pnpm run build
```

The existing `wow-engine-verify` integration job provisions ephemeral Postgres,
Redis and PostgREST, starts the actual Celery worker, and runs
`test_agent_runtime_postgres_integration.py` plus
`test_prop_snapshot_min3_postgres.py`. The latter namespaces actual migration
SQL in a temporary local schema and tests the 3/10 boundaries, identity,
malformed evidence, privilege preservation and idempotence. It refuses a remote
database. Never supply production credentials to this job.

Dashboard setup and the distinction between its access key and the server-held
Action credential are documented in the [dashboard README](../../../artifacts/final-lock/README.md). No backend
credential belongs in a `VITE_` variable, browser storage or client bundle.

## Certification is a separate deliverable

`v17/prop_certification_runtime.py` and
`v17/prop_production_registration.py` intentionally contain no fabricated
reviewed policy or release. Scheduled evidence collection is not certification.
For each exact artifact:

1. Recover immutable pregame forecasts and independently sourced official
   settlements; exclude invalid timestamps, pushes/voids, duplicates and direction
   twins. Isolate model version/checksum rather than mixing artifacts.
2. Use the existing `v17.prop_calibrator_candidate_runtime` research audit for
   chronological training and untouched holdout evidence. Its candidate packet
   is not a production calibrator, approved probability or publication bound.
3. Obtain independent review of a versioned route policy and candidate evidence,
   then an exact reviewed calibration/release package. Do not invent thresholds
   or change registry flags solely to remove a hold.
4. Verify model/calibrator/hydration adapters, exact registry identity, calibrated
   bounds, real Action canary and immutable receipt readback under the existing
   registration audit. Preserve every native typed blocker that remains.

A November 7 read-only assessment of the worker-linked database found a real MLB
strikeout cohort large enough to generate an offline candidate review packet.
That establishes a next review artifact, not certification, web database binding
or production approval. NFL cohorts and inactive NBA artifacts remain separately
qualified; an MLB packet cannot authorize another sport or stat.

The snapshot is preserved in
`artifacts/wow-engine/v17/research/20261107_mlb_strikeout_calibrator_candidate.json`.
It contains 658 independent settled theses, split into 547 training and 111
untouched holdout observations by the existing candidate builder. Holdout Brier
changed from 0.275179 to 0.256044, log loss from 0.753122 to 0.705981 and ECE from
0.150477 to 0.073427. These are descriptive research results on that exact cohort;
they do not validate current inputs, production bounds, independent review or a
certification policy. Parameters are not loaded by a runtime adapter or registry.

## Production acceptance

The source branch, protected checks, merge, migration application, deployment,
Custom GPT editor synchronization and usable model result are separate facts.
After protected release, independently verify:

- current protected-main SHA on both fixed Render services, before/after canaries;
- the actual Render worker start command matches `render.yaml`, including
  `python -m celery`; committing the Blueprint alone does not prove that an
  existing service has applied its settings;
- actual runtime database binding and migration/function definition;
- one current event through acquisition, controlling fitted scorer, calibration,
  immutable prediction persistence, terminal reducer and receipt lookup;
- all source rows reconcile, and research/blocked rows stay nonpublishable;
- a fresh production Custom GPT session calls the current Action schema and
  displays the actual governed receipt;
- `can_execute=false`, with independent SIRT/product acceptance and rollback proof.

A green build, deployment liveness, candidate fit or static status document alone
does not complete this acceptance.
