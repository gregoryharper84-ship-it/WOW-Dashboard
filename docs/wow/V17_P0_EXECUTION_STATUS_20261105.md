# WOW V17 — Freebuff P0 execution status (2026-11-05)

Status: **WORKING_NOT_COMPLETE**. Source repair verified locally; independent SIRT/QA/Release
acceptance, protected merge, production Supabase migration, Render deployment and authenticated
Action readback were **not** performed and are not claimed here.

```text
can_execute = false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS = true
terminal_authority = V17_TERMINAL_REDUCER
```

## 1. Audit context

| Item | Value |
| --- | --- |
| Audited protected main | `02e0ada11c5a0685b5e3121ecc89fa559b6b48b1` |
| NBA lane base (PR #2472 head) | `3531a48042dcb549cbc0312decf6c18eeeb1f130` |
| Working branch (this work) | `freebuff/ae0ffa56dd69bd58233dae36` |
| Intended change | fail-closed NBA forward-shadow receipt readback authenticity |
| Relevant tests | `artifacts/wow-engine/tests/test_v17_nba_event_shadow_prediction_persistence.py` and the basketball suite (232 passed) |

No history was rewritten, no force-push was used, and `main` was not written to.

## 2. Executed repair

`artifacts/wow-engine/v17/nba_event_shadow_prediction_persistence.py`

A stored digest plus a non-empty primary key is not independent proof of a genuine immutable
receipt. The readback path now re-reads and verifies the durable row itself:

- `nba_prediction_id` must be a database-generated UUID (client-shaped strings are rejected).
- `model_timestamp` and `created_at` must be present and parseable; both must precede tip-off,
  and the stored scoring instant must equal the submitted scoring instant. A row recorded after
  the event can no longer be returned as a pregame `MODEL_RESEARCH_ONLY_HOLD`.
- Every bound identity, timestamp and probability column (event, teams, source snapshot, model
  and calibrator identity/digests, certification, artifact version, raw/calibrated probabilities
  and bounds) is compared directly against the submitted evidence, so a row that no longer names
  this exact fixture fails closed even when the digest column is unchanged.

Failure codes are unchanged and reused: `NBA_SHADOW_READBACK_INVALID`,
`NBA_SHADOW_NOT_PROSPECTIVE`, `NBA_SHADOW_IMMUTABLE_READBACK_CONFLICT`. No probability semantics,
model promotion, bounds policy or publication state was changed; `can_execute` remains `false`
and the receipt remains non-publishable and non-rank-eligible.

### Regression coverage added

- non-UUID / client-supplied receipt identity is rejected;
- missing `created_at` or `model_timestamp` readback is unverifiable;
- a receipt inserted after tip-off is never prospective;
- a stored row that no longer carries the bound team identity is a conflict despite a valid digest;
- a stored scoring instant that differs from the submitted instant is rejected.

## 3. Verification actually performed

```text
cd artifacts/wow-engine
python -m pytest -q tests/test_v17_nba_event_shadow_prediction_persistence.py \
  tests/test_v17_nba_current_pregame_scorer.py tests/test_v17_nba_certified_calibrator_artifact_rpc.py \
  test_v17_basketball_hydration_fallback.py test_v17_basketball_model_maintenance.py \
  tests/test_v17_basketball_team_event_forward_features.py \
  tests/test_v17_basketball_team_event_calibration_artifact.py \
  tests/test_v17_multisport_calibration_digest.py
→ 232 passed
```

Environment: Python 3.12.3, project `requirements.txt` dependencies installed into a local venv.
No PostgreSQL-backed test, GitHub Actions run, Render revision, Supabase project or live Custom GPT
Action was reachable from this workspace, so none of those are reported as passing.

## 4. Prerequisites blocked in this workspace (not repaired, not claimed)

| Blocker | Evidence | Consequence |
| --- | --- | --- |
| PR #2473 (LLP producer→ledger receipts) branch unavailable | observed heads `5819fc26…`, `4932567…`, `029055a…` are absent objects in this clone; the authenticated fetch only returns `main` | LLP receipt repair and readback could not be advanced from actual source here |
| PR #2476 stacked commits unavailable | `5d7c21cefc…` and `4b7ffa9d…` are absent objects | The requested one-time incorporation of the chronology and CM-DDL commits into #2472 could not be performed without fabricating content |
| No merge / deployment / provider authority | no GitHub merge tool, no Render or Supabase credentials, no Actions dispatch authority | Protected merge, production migration, deployed-SHA proof, SIRT #2469 semantic PASS, QA/Release App checks and authenticated Action receipt remain outstanding |

## 5. Exact actions for authorized Engineering

1. **Integrate this repair into the existing #2472 branch only** — fetch
   `freebuff/ae0ffa56dd69bd58233dae36`, verify the single commit, and cherry-pick or merge it onto
   `3531a48042dcb549cbc0312decf6c18eeeb1f130`. Do not open a duplicate PR and do not write `main`.
2. **Reconcile the stacked lane once** — incorporate the separately attributable `#2476` commits
   (`5d7c21cefc7c217abfcb3d303272c8a5179d702d`, `4b7ffa9df6aa99e34fb8f0802612c9ab31ce8968`) into
   the resulting #2472 head with ancestry/byte-level diff, then rerun the full main-targeted
   native workflows on the **new** head. Do not inherit checks from any earlier head.
3. **Independent acceptance** — obtain SIRT #2469 semantic PASS (2027 source finality/leakage,
   genuine fitting, source entitlement, ledger originality, receipt authenticity), plus QA and
   Release App checks bound to the resulting exact SHA. A green native workflow is not SIRT PASS.
4. **Protected merge** — merge #2472 to `main` only through CODEOWNER review after the above.
5. **Production Supabase** — after review, apply in order
   `20261007_basketball_calibration_artifact_evidence.sql`,
   `20261102_basketball_certified_calibrator_artifact_rpc.sql`,
   `20261103_nba_event_shadow_prediction_receipts.sql`; then verify `pg_proc` exposes both
   basketball RPCs, RLS is enabled on `wow_nba_event_shadow_predictions`, and only `service_role`
   holds `select, insert`.
6. **Render** — deploy the merged SHA to both the web and worker services, prove deployed-revision
   parity with `main`, and run the production canaries. A successful build is not deployment proof.
7. **Current-season and model evidence** — persist real settled NBA current-season rows, register a
   genuinely fitted champion plus an independently certified calibrator and separately approved
   bounds receipt, and capture one authenticated end-to-end Action receipt. Until then the NBA lane
   remains a typed research HOLD; do not publish a probability or substitute a qualitative estimate.

`can_execute=false` and `V17_TERMINAL_REDUCER` terminal authority are unchanged by all of the above.
