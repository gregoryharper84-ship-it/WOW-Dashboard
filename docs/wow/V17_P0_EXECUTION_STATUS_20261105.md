# WOW V17 — Freebuff P0 execution status (2026-11-05)

Status: **WORKING_NOT_COMPLETE**. Source repairs verified locally on exact heads. Independent
SIRT/QA/Release acceptance, protected merge, production Supabase migration, Render deployment and
authenticated Action readback were **not** performed and are not claimed.

```text
can_execute = false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS = true
terminal_authority = V17_TERMINAL_REDUCER
```

## 1. Git fetch resolution (PR heads)

The earlier "absent object" result was a fetch/refspec limitation, not PR nonexistence. Pull refs
resolve and fetch without authentication errors:

```text
git ls-remote origin 'refs/pull/2473/head' 'refs/pull/2472/head'
  5819fc26720530d8248d073574ae6863080dc047  refs/pull/2473/head
  e8e35277f28518b1f1f93eed4cf95f437f2a6c03  refs/pull/2472/head

git fetch --no-tags origin 'refs/pull/2473/head:refs/remotes/pr/2473'   # ok
git fetch --no-tags origin 'refs/pull/2472/head:refs/remotes/pr/2472'   # ok
```

`main` (`02e0ada11`) is an ancestor of `pr/2472`. No force-push, no `main` write, no history rewrite.

| Lane | Branch here | Exact head |
| --- | --- | --- |
| Scout readback hardening | `freebuff/ae0ffa56dd69bd58233dae36` | `df7c050a6` |
| NBA shadow receipt integrated onto #2472 | `freebuff/ae0ffa56-2472-receipt` | `f7136b29d` (base `e8e35277`, cherry-pick of `cebdb9d11`) |

## 2. Scout readback hardening — four findings closed

`v17/scout_governed_probability_lookup.py`, rendered by `v17/scout_board_materializer.py`.

| Finding | Defect | Correction |
| --- | --- | --- |
| Authoritative event-lock chronology | The pregame boundary used Scout's own `commence_time`, so a later candidate time could admit a post-lock row | The ledger's own `event_start_time_utc` / `event_start_time` is authoritative; recording must be strictly earlier |
| Exact Decimal probability comparison | `float()` comparison collapsed distinct numerics, hiding an inverted bound (`...0004` vs `...0003`) | Exact `Decimal` parsing and `0 < lower <= probability <= upper < 1` |
| Timestamp overflow handling | `astimezone` could raise `OverflowError` outside the guarded parse | Whole conversion guarded; extreme years are a typed hold, not an exception |
| NFL terminal-label provenance | `FINAL_APPROVED` was hardcoded because `wow_nfl_event_predictions` has no `terminal_label` column | Label is read from `wow_v17_reduce_nfl_event_terminal_label(score_snapshot_id)`, requiring `status=PASS`, `global_terminal_reducer=V17_TERMINAL_REDUCER`, `can_execute=false`; MLB requires its stored `terminal_label` |

Evidence: new regressions fail against the pre-fix module (**22 failed, 3 passed** with the module
stashed) and pass with the correction (**30 passed**); the combined affected set is **111 passed**.
Positive controls keep a valid NFL and MLB receipt visible with unchanged display percentages.

## 3. NBA shadow persistence integrated onto the current #2472 head

`cebdb9d11` was cherry-picked (`-x`) onto `refs/remotes/pr/2472` (`e8e35277`) without touching
`#2476` or `#2480`. `pr/2472` had not modified the persistence module or its test between
`3531a4804` and `e8e35277`, so the pick applied without conflict. Integration tests:
**86 passed** (`test_v17_nba_event_shadow_prediction_persistence.py`, `test_v17_nba_current_pregame_scorer.py`).

## 4. Contract separation (not interchangeable)

| Contract | Governs | Owner here |
| --- | --- | --- |
| Scout readback guard | Whether an already-produced ledger number may be **displayed** | This branch |
| LLP canonical ledger verification (`_verify_governed_event_prediction`) | Whether a real `wow_event_predictions` row is a governed prediction receipt | PR #2473's writer — **unchanged** |
| NBA shadow receipt | Whether a durable NBA shadow row is the server-recorded pregame receipt | #2472 integration branch |

The Scout guard is **not** a producer-to-ledger connection and does not create, certify or publish
any prediction. The observed validation ledger remains MLB-only, so the producer→ledger gap is
still unproven and must be resolved in #2473's own scope.

## 5. Verification actually performed

```text
cd artifacts/wow-engine   # Python 3.12.3 venv from requirements.txt
python -m pytest -q test_v17_scout_governed_probability_display.py           → 30 passed
  (same file with the pre-fix module stashed)                                → 22 failed, 3 passed
python -m pytest -q test_v17_scout_governed_probability_display.py \
  tests/test_v17_nba_event_shadow_prediction_persistence.py \
  tests/test_v17_nba_current_pregame_scorer.py                               → 111 passed
# on freebuff/ae0ffa56-2472-receipt
python -m pytest -q tests/test_v17_nba_event_shadow_prediction_persistence.py \
  tests/test_v17_nba_current_pregame_scorer.py                               → 86 passed
```

No PostgreSQL-backed test, GitHub Actions run, Render revision, Supabase project or live Action was
reachable, so none is reported as passing.

## 6. Outstanding

1. Verify `_verify_governed_event_prediction` on `pr/2473` (`5819fc26`) against a real canonical
   ledger row, including exact Decimal bounds and prelock terminal ordering. Fetchable now; not
   inspected here.
2. Independent SIRT #2469 semantic PASS, IB QA/Release App checks and CODEOWNER approval bound to
   the resulting exact heads.
3. Authorized protected merge of #2472 (after incorporating `#2476` once by its owner).
4. Production Supabase migrations in order (`20261007`, `20261102`, `20261103`), Render web+worker
   revision parity, current-season settled NBA persistence, certified champion/calibrator, and one
   authenticated Action→terminal→DB readback.

`can_execute=false` and `V17_TERMINAL_REDUCER` terminal authority are unchanged.

## 7. Presentation boundary regression (Scout)

A Decimal inside the exact `0 < p < 1` domain can still become `1.0` as a float,
and a value just below 1 formats as `100.00%` at two decimal places. Both would
display a probability the domain excludes, so the display path now requires the
float and its rounded percentage to remain strictly inside the boundary.

Evidence: 4 new regressions fail with the pre-guard module (**26 passed, 4 failed**)
and pass after it (**30 passed**). Positive control: a small but renderable lower
bound (`0.0001`) still displays `0.01%`.

## 8. P0 — the canonical ledger cannot satisfy the LLP readback contract

PostgreSQL-backed evidence, not inference. A disposable cluster is created with
bundled binaries, the repository's own DDL from `artifacts/wow-engine/event_schema.sql`
is applied, and the real `_verify_governed_event_prediction` runs against it through
a psycopg adapter exposing the governed client's query surface.

| Observation | Result |
| --- | --- |
| Enforced guards | `sport = 'MLB'`, `league = 'MLB'`, `controlling_specialist = 'wow.mlb-game-win-probability-expert'`, `settlement_basis = 'FULL_GAME_INCLUDING_EXTRA_INNINGS'` |
| NFL / NBA / NHL canonical insert | rejected, SQLSTATE `23514`, sport guard; table stays empty |
| NFL settlement basis on an MLB-sport row | rejected independently by the settlement-basis guard |
| Genuinely well-formed MLB row | inserts (count = 1) but the verifier returns `LLP_EVENT_PREDICTION_RECEIPT_MISMATCH`, because the basis map has no MLB entry |
| LLP supported sports | `{NFL, NBA, NHL}` |
| Base DDL vs verifier select | `terminal_reduced_at` and `source_snapshot_timestamp` are migration additions, absent from the base table DDL |

The stored domain and the verified domain are **disjoint**, so every LLP winner is
permanently held in this schema. This is a schema/design incompatibility, not a
Scout guard defect, and no Scout change can repair it.

Prohibited remedies: dropping or widening the MLB constraints, synthesising a
canonical UUID from a legacy identifier, or creating shadow rows that masquerade as
certified receipts. The governed remedy is a separately approved multisport receipt
ledger or an official per-sport receipt adapter, with its own migrations and a
versioned readback contract.

Lane: `freebuff/ae0ffa56-2473-ledger-schema` at `157769502`, adding
`tests/test_llp_event_prediction_ledger_schema_postgres.py` (**5 passed**; suite with
#2473's own research/readback/bundle tests **236 passed**). The new file is a
characterisation test and must be replaced when the real contract exists.
