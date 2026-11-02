# WOW Engineering 24/7 — Clean Reset V1

Owner objective: **production restoration and sustained production improvement, around the clock**.

This is a staged replacement for Engineering orchestration, **not** a replacement for
the sports probability backend, sports Agent Runtime, Kalshi, SIRT, or required CI.

## Working design

1. A **single GitHub Actions controller** wakes on issue changes, relevant CI/PR
   completion, or a five-minute safety-net schedule (GitHub may delay cron).
2. One deterministic selector reads the entire *bounded* open-issue/open-PR
   inventory. P0 and P1 restoration outrank improvement. One existing PR
   belonging to that incident is resumed rather than creating duplicate code.
3. One implementation writer is admitted through the existing GitHub mutex.
   A selected **available and authorized** coding provider receives a concrete
   numeric incident ID. It must produce a source PR; a selector alone is not code.
4. The existing three required regression checks and protected policies bind
   exact head/source. Security/CI/auth/secret/workflow changes retain genuine
   independent protected-source assessment. Model math/publication changes
   retain scientific and calibration certification.
5. Only an authorized protected merge may deploy. Verify the actual Render
   deployment and real user/model action canary; then re-enter the next task.

## Truthful deployment conditions

The workflow **defaults to shadow-only**. It must not dispatch a second writer
alongside the old control plane. Activation requires both protected repository
variables:

- `WOW_24X7_ACTIVE=true`
- `WOW_24X7_LEGACY_ROUTES_OFF=true`

The coding-provider variable `WOW_24X7_CODING_PROVIDER` accepts `openai`
or `claude` only. Missing/unsupported selection produces a typed warning
rather than launching Cursor or spending credits.

Before activation, Engineering must prove absence of an active old remote writer,
record a signed exact-reservation cutover receipt for #1138/6553867635, then
disable the old automatic engineering selectors and retire the old resident
engineering dispatcher. Existing Claude/OpenAI workers still enforce their own
remote-writer checks; **this PR does not bypass or remove them**. A separate,
reviewed, fail-closed provider adapter is needed for the final Cursor-free
cutover. Do not pretend that a GitHub concurrency group proves a preexisting
remote worker is absent.

## Backlog and limitations

- Current source module performs deterministic, side-effect-free selection and
  tests. It cannot itself generate code, authorize merge, or certify models.
- Continuous 24/7 means continuous **availability for useful work**, not paid
  infinite model invocations. Provider credit exhaustion is a genuine capacity
  issue; deterministic checks and low-cost diagnosis can continue.
- Release automation and exact Render SHA + real post-deploy canary are **not
  implemented in this candidate**. Existing governed paths remain authoritative.
- Do not disable healthy old components until an independently accepted new
  worker has produced two consecutive end-to-end production-verified cycles.
- Leave all historical GitHub reservation/release receipts and security REJECT
  evidence immutable; never bulk-delete it or use a feature flag to forgive a
  possibly active remote writer.

References: [autonomous engineering #1021](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1021)
and [security bootstrap #2136](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/2136).
