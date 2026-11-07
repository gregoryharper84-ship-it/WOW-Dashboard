---
name: verify-wow
description: Verify WOW V17 backend, prop Action, LLP winner, live ChatGPT golden path, and engineering recovery with actual user-facing behavior and attributable proof before claiming repairs work.
---

# WOW V17 verification — behavioral evidence first

This is a project-local Cursor skill, not a new scheduler, model, reviewer, approval gate, worker, datastore, lease controller or release authority. Reuse the existing Engine, GitHub Actions, AMRT, SIRT and QA/Release mechanisms.

## Launch

Primary surface: the real production WOW_BETTING_ENGINE Custom GPT, which uses the canonical scoreWowPickRequest Action on the existing Render backend. This host cannot be launched inside a repository checkout. Backend read-only diagnostics use the already-running https://wow-governed-probability-engine.onrender.com service. Local service recipe in render.yaml: run in artifacts/wow-engine using uvicorn api_ncaaf_acceptance:app --host 0.0.0.0 --port $PORT. Never run it against production secrets or shared Supabase from a local agent. An isolated local launch requires appropriately provisioned database and other dependencies; if absent, mark LOCAL_LAUNCH_NOT_VERIFIED, not PASS.

From repository root run the doctor tests (Python 3.11, stdlib only):

~~~bash
python3 -m unittest discover -s .cursor/skills/verify-wow/tests -v
~~~

## Doctor

Run before first drive and again after any surprising behavior:

~~~bash
python3 .cursor/skills/verify-wow/scripts/doctor.py --output "/tmp/wow-doctor-$(date -u +%Y%m%dT%H%M%SZ)-$$.json"
~~~

Doctor does only GET /health/live, records a hash rather than server body, uses no secrets, writes a new immutable evidence filename, and returns BACKEND_LIVENESS_PASS, BACKEND_LIVENESS_FAIL or LIVENESS_UNPROVEN. Even a PASS proves only public HTTP liveness. It never proves deployed SHA, a certified model, Action transport, editor sync, or a user journey. Remote origins must be HTTPS; HTTP is allowed only on loopback. If the doctor fails, do not drive an uncertain authenticated instance; investigate and retry after reset.

## Drive

Read features/README.md and the mapped feature's recipe first. For an API, use real authenticated requests only through existing authorized Action/CI interfaces, not invented auth, toy stubs or hidden setters. For the ChatGPT UI, interact with the real host and capture both the user's request and the final result. User-facing feature map:

- Backend liveness via read-only doctor.
- Canonical prop Action via existing wow-v17-canonical-prop-action workflow (only an authorized current pregame request).
- Live golden ChatGPT-to-Action-to-model-to-terminal-to-rendered-response path.
- LLP team/event winner via exact fitted specialist, calibration/lower bound and terminal receipt.
- Engineering recovery via original incident, exact repair, authorized release and independently observed production result.

The native golden-path receipt evaluator is:

~~~bash
python3 artifacts/wow-engine/v17/engineering_effectiveness.py journey /path/to/real-live-host-receipt.json
~~~

This validates fields in a supplied receipt; it does NOT authenticate arbitrary JSON assertions. Independently reconcile original host Action metadata, exact rows, model/terminal/persistence receipts and user-visible result before claiming live-host PASS. Never construct all-true JSON to manufacture success. Repository health declarations, unit tests, source PRs or an HTTP 200 are not a substitute for live host proof.

## Evidence

For each drive record expected outcome, actual outcome, UTC timestamp, environment, exact source SHA, actual deployed SHA when verified, method/route, actor/host, command/exit code, genuine run/prediction/incident identifier, redacted original request/response, UI screenshot/video when relevant, and observed side effect/readback when applicable. Show original typed errors. Put proof in an evidence directory outside scratch process state and confirm it survives cleanup. GitHub Actions artifacts may retain evidence; do not introduce a second receipt authority. Historical or synthetic replay must be explicitly marked and never pass as current production evidence.

Do not relabel odds, hit rates, Scout estimates or generic reasoning as fitted probabilities. Model input gaps, missing artifacts, scorer failures, invalid output, invocation failures and market-only failures are distinct. Governed official ranks use calibrated_lower_bound when required. Every scoped row must reconcile exactly once.

## Cleanup and authority

Terminate only local PIDs created by the run. Remove only run-owned scratch state, preserve evidence, never kill by process name, reset shared sessions, release a writer lease, alter a model, deploy, merge, approve, or dispatch another writer. If a required environment/auth/session is unavailable, record VERIFIED_UNREACHABLE and the exact prerequisite. This skill does not bypass the existing independent QA, SIRT, protected merge, or Release roles. Return PASS_WITH_ATTRIBUTED_EVIDENCE, FAIL_WITH_REPRODUCTION or UNVERIFIED_WITH_EXACT_REASON for each feature.

V17_TERMINAL_REDUCER is the only global terminal authority. Exactly one controlling fitted specialist owns each row/event. can_execute=false. DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true. No wagers or market orders.

Use maintain-verify-wow after feature/route changes. Maintenance itself is not a release gate.
