# QA Content-Ledger Recovery — P0 #2136 (Cursor lane)

Status: `PR_CREATED` candidate only. Not merged, not deployed, not
production-verified. This lane does not modify, rebase or write to the
canonical Engineering PR #2275 branch
(`engineering/p0-qa-content-ledger-20261029`, verified head
`391cc48ef75a4e4389f305768785f5211508b7e6` at handoff; `main` verified at
`408e345f5a8ac49824c29d2cfbb58e500d3213cc`).

`custom_gpt_identity=WOW_BETTING_ENGINE`
`runtime_generation=V17_ACTIVE`
`terminal_authority=V17_TERMINAL_REDUCER`
`can_execute=false`
`DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`

## Root cause and reproduction

PR #2275 reserves identical content before any provider call:
`reserve_content_review` creates a QA App (5245832) `in_progress` check run
named `WOW Semantic Content Ledger <digest>` on sentinel commit
`79fa0c275a58cd9d397c5a1bc81d641b6dad7f75`. `trusted_content_history` raises
`CONTENT_LEDGER_IN_PROGRESS` for any non-completed ledger record. That is
correct fail-closed behavior while a review is live — but if the runner is
cancelled, killed, times out or loses network access between reservation and
`commit_content_review`, nothing ever completes the reservation. Every
subsequent review of identical content deadlocks permanently, on every PR,
because the digest is base- and PR-independent.

Reproduced locally against the exact #2275 head source with a mocked
QA App `in_progress` ledger record: three consecutive
`trusted_content_history` calls each raised
`Hold(CONTENT_LEDGER_IN_PROGRESS)`, and no recovery code existed on `main`.
The #2275 governance text describes a manual recovery *procedure*, but a
procedure without code, identity checks or receipts is not an enforceable
mechanism. This lane implements that mechanism in code.

## Deliverable 1 — Governed orphan recovery

New files (no changes to #2275's files, so the diff cannot conflict with it):

- `.github/scripts/wow_qa_ledger_recovery.py` — recovery engine and CLI.
- `.github/scripts/tests/test_wow_qa_ledger_recovery.py` — 20 tests.
- `.github/workflows/wow-v17-qa-ledger-recovery.yml` — manual dispatch only,
  protected `main`, `wow-qa` environment, dry-run by default.
- `docs/WOW_QA_LEDGER_RECOVERY.md` — this handoff.

Behavior:

1. Reads the full ledger inventory for the exact digest (bounded, paginated,
   `filter=all`) and accepts only QA App 5245832 records on the exact
   sentinel SHA with matching `external_id`; forged provenance fails closed.
2. Retains the original reservation: check-run ID, digest, name, sentinel
   SHA and `started_at` are copied into the recovery receipt, together with
   the operator-evidenced original PR/head/base, workflow run/attempt,
   provider decision, reviewer identity and evidence reference. The original
   check run is completed **in place**; nothing is deleted or rewritten, and
   completed adverse (REJECT) ledger history for the digest is never touched.
3. Mutation requires the separately minted QA App checks:write token,
   distinct from the read token; reads use the read token only. The dispatch
   actor must be in `WOW_QA_RECOVERY_ACTORS` (default: repository owner;
   QA/SIRT actors are added by an administrator in the protected
   environment, not in source).
4. No semantic REJECT may become HOLD: a corroborated REJECT — from a prior
   completed ledger entry for the digest, from a QA App REJECT check on the
   original PR head, or from operator evidence `REJECT_CONFIRMED` with a
   named provider reviewer — completes the reservation as REJECT
   (`failure`). Uncorroborated or unestablishable decisions fail closed and
   leave the reservation untouched.
5. Staleness is mandatory: a reservation younger than the staleness bound
   (default 1800s, bounded to [900, 86400]; the reviewer timeout is 12
   minutes) is assumed live and is never recovered.
6. Concurrency: recovery runs in the same global single-writer concurrency
   group as the #2275 ledger writer, and additionally takes a QA App claim
   check run per reservation. The lowest claim ID wins; losers complete only
   their own claim as superseded and never touch the reservation.
7. Bounded retries and idempotency: the reservation PATCH is attempted at
   most 3 times, only for transient HTTP 429/5xx/transport failures; a
   duplicate recovery event returns `ALREADY_COMPLETED` without a second
   mutation. Every attempt carries `idempotency_key =
   sha256(reservation_id:digest:outcome)`, recorded in the claim check, the
   completed reservation summary and the uploaded receipt artifact.
8. Recovery outcomes are HOLD (`neutral`) or REJECT (`failure`) only. There
   is no code path from recovery to PASS/`success`.

Stale-base handling: the content digest is deliberately base-independent, so
a moved base does not invalidate a HOLD recovery; a moved PR head since the
reservation is recorded in the receipt (`pr_head_moved_since_reservation`)
rather than silently trusted, and malformed head/base SHAs fail closed.

## Deliverable 2 — Security re-roll resistance (design only, not activated)

Cross-PR identical-content REJECT protection already exists in #2275 and is
preserved: identical bytes mint the same digest and stay REJECT-terminal.
A one-byte edit mints a new digest and therefore resamples review — the
ledger cannot link digests by itself. `assess_attempt_budget` in the
recovery module is the read-only, **non-enforced** measurement for a future
policy: it counts *distinct* digests a PR has had completed as REJECT
(identical-content resubmissions do not inflate the count) and recommends
`HOLD_FOR_INDEPENDENT_REVIEW` above a budget. Passing `enabled=True` raises
`ATTEMPT_BUDGET_NOT_AUTHORIZED_FOR_ACTIVATION`; no gate behavior changes.

Recommended policy for governance approval (not implemented as enforcement):
budget of 3 distinct rejected contents per PR, then mandatory independent
human security review of the diff series; legitimate revisions below the
budget are unaffected, and a corrected genuine finding never counts the
same digest twice. Activation requires a separately approved patch that
wires the count into the reviewer before reservation, plus tests proving a
legitimate fix after REJECT still proceeds. Do not activate unilaterally.

## Deliverable 3 — Fault-injection and regression tests

`test_wow_qa_ledger_recovery.py` (20 tests, all mocked, no network) covers:
kill after reservation; cancellation during provider review; confirmed
REJECT followed by the original failed ledger PATCH; two recovery workers
racing (lowest claim wins, loser cannot touch the reservation); missing,
non-distinct and HTTP 401 QA App authentication; unauthorized actor;
duplicate recovery events (idempotent); repeated transient PATCH failures
(bounded at 3, reservation left fail-closed); dry-run (zero mutations);
unestablishable provider decisions; stale/malformed base and moved head;
forged app/sentinel provenance; identical content moved to another PR
(REJECT retained); a legitimate revision after REJECT within budget;
repeated one-byte re-rolls exceeding the experimental budget; and proof the
budget cannot be self-activated. In every mutation test the only
conclusions ever written are `neutral` or `failure` — never `success` —
and unauthorized actors create no claims and no patches.

## Deliverable 4 — Integration handoff for Engineering

- Base: `main` @ `408e345f5a8ac49824c29d2cfbb58e500d3213cc`. This lane adds
  new files only and touches none of #2275's four files, so it merges
  before, after or alongside #2275 without textual conflict. Recovery is
  inert until #2275's ledger exists in production, because on current
  `main` no ledger reservations are ever created.
- Constants that must stay byte-identical with the reviewer are marked in
  source (`LEDGER_SENTINEL_SHA`, `LEDGER_NAME_PREFIX`, `QA_APP_ID`). If
  #2275 changes any of them before merge, update this module in the same
  integration PR and re-run both test suites.
- The recovery workflow intentionally reuses #2275's global concurrency
  group `wow-independent-security-qa-ledger-writer`; on current `main` the
  reviewer workflow still uses its per-PR group, so Engineering should land
  #2275's group change (or rename both together) at integration.
- Mandatory administrator authorization before first non-dry-run use: the
  repository administrator confirms the `wow-qa` environment protection,
  `WOW_QA_APP_ID`/private-key presence (values never printed), and the
  `WOW_QA_RECOVERY_ACTORS` membership. No new secrets, apps or rulesets are
  introduced by this lane; no ruleset change is made or implied.
- QA/SIRT acceptance (independent, authenticated): run one dry-run against
  a known stale reservation, inspect the receipt, then one real recovery on
  a canary PR, and verify the original check-run ID now reads HOLD/REJECT,
  that no PASS was published, and that a fresh review of the content can
  proceed. Claude's independent adversarial review should specifically
  challenge the claim-race and evidence-attestation boundaries.
- Risk classification: Class B (trust-root adjacent). It completes security
  ledger state but cannot create a PASS, a merge, a deployment or any
  trading action; the failure mode of every defect found in review remains
  HOLD.
- Rollback: revert this lane's single PR. The reviewer and ledger are
  untouched; any reservation already recovered stays completed (HOLD is
  retryable by a fresh review, REJECT stays terminal for identical
  content), so rollback never resurrects a deadlock or erases a REJECT.

## Residual risks Claude / QA should challenge

- GitHub check-run listing is eventually consistent; the claim protocol is
  defense-in-depth on top of the workflow concurrency group, not an atomic
  compare-and-swap.
- `provider_decision` is operator-attested evidence. The corroboration
  scans bound the damage (any hidden REJECT still forces REJECT), but a
  false `NONE_RECEIVED` attestation can convert an unrecorded provider
  REJECT into HOLD. HOLD never publishes PASS and a fresh semantic review
  is still required, but QA/SIRT may additionally require the original run
  artifact digest inside `evidence_ref` before first production use.
- The staleness bound assumes the 12-minute reviewer timeout in the
  workflow; if that timeout is raised above the bound, raise
  `DEFAULT_STALE_AFTER_SECONDS` in the same change.
