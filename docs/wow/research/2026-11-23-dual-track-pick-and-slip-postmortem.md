# WOW V17 — Independent pick/model and slip-construction retrospectives

**Status:** Class A retrospective/process diagnostics; proposed in PR #2848. Not a
fitted sporting upgrade, not an authenticated payout or submitted ticket, not a
production-deployed improvement, and not verified prediction lift.

## Two separate ownership lanes

| Lane | Source of truth | May report | Must never claim |
| --- | --- | --- | --- |
| **Sporting pick / model postmortem** | Existing `v17.postmortem_engine`, independently settled exact-event pick and any genuinely immutable pregame fitted specialist receipt | Unique selections, settled outcomes, source and opportunity/role misses; Brier/log loss only when genuine time-correct calibrated probabilities exist | That anecdotal tips, VIP consensus, odds or postgame-derived percentages were certified probabilities |
| **Slip-construction retrospective** | New pure `v17.slip_construction_postmortem` built from already-normalized settled legs and position records; same authenticated ledger read for API | Repeated losing hinges, exposed all-or-nothing positions, same-event dependency, concentrated 4+ leg structures, missing prelock quote/payout/joint evidence | A new sport predictor, independent marginal product as joint probability, submission/profit claims, or model-promotion authority |

The core postmortem persistence remains unchanged. The new read-only API is
`GET /v17/postmortem-runs/{research_run_id}/slip-construction` (existing auth).
A successful `POST /v17/postmortem-runs` also includes the distinct slip
construction report without altering the trusted persistence RPC.

## What November 23 revealed (diagnostic hypotheses, not promotion evidence)

- Repeated favorite selections in multiple ChatGPT-proposed cards created a
  shared failure hinge. The exact exposure analysis must de-duplicate a selection
  across cards, rather than count each appearance as a new independent trial.
- High priced MLB/NHL/NBA/NFL favorites were treated as 'strong' without
  independently certifying a price-adjusted advantage. Future value reports
  require time-correct accepted quotes and fitted calibrated lower bounds.
- Some stat lines failed from uncertain minutes, lineup and role, including
  sickness and teammate-return conditions; the roster and source timestamp
  must be versioned and invalidate stale advice across every affected market.
- The VIP alternate-line choices did well in one retrospective, but no
  challenger should receive new weights until comparable forward cohorts are
  graded against the same opportunity universe and closing-line benchmark.
- Today's chat-generated examples were not authenticated submitted tickets.
  Neither losses, winnings, ROI nor P/L may be attributed to GH from these
  recommendations. Late-built alternatives and withdrawn slips must retain
  their frozen disposition and cannot be replaced after observing a result.

## Required future prelock slip-build receipt (not manufactured here)

Persist through the existing privileged governed infrastructure, only after
proper source and rights acceptance:

1. Exact UTC decision time before all relevant starts; immutable build ID,
   slate revision, candidate set, rejected alternatives, chosen card structure,
   platform and full exact canonical sport/event/player/market/side/line/period/
   settlement identity.
2. Verified offered and accepted payout terms, bookmaker/platform and quote
   receipt with observed time and quote age; distinction between proposed,
   quoted, user-submitted, platform-accepted and voided entries.
3. Per-leg controlling fitted specialist, calibrator artifact and lower-bound
   pregame receipt *if genuinely certified*. Explicit `NO_MODEL` otherwise.
   Capture material injury/lineup/rest/starting-goalie news version and proof
   of post-update feature consumption, or hold.
4. Same-event and overlapping-thesis dependencies, bounded joint scenarios,
   push/void treatment and uncertainty, weakest-leg sensitivity, and
   price-versus-joint-break-even evidence; absent genuine joint receipts
   means the slip cannot be called high-confidence.
5. Immutable prelock incumbent and challenger proposal, shared candidate
   universe, inclusion/exclusion status and a timestamped independent
   reference quote. Freeze before event start, not inferred from settlement.

The current read-only slip report does **not** attest that such a prelock
record exists. Its missing-evidence flags remain visible.

## Forward acceptance

- Grade each future selection only against independent official finals on
  the original exact line and rules, with push/void separated. Attribute fitted
  model grades only to authenticated, time-correct receipts.
- Report both **distinct selection hit rate** and **slip leg occurrence hit
  rate**, and show cross-slip shared hinge exposure separately.
- For all-or-nothing tickets, a losing leg prevents all legs clearing; for Flex,
  pushes and voids, never infer a specific payout without verified platform
  rules and accepted-ticket evidence.
- Compare incumbent/challenger Brier, log loss, calibration, uncertainty,
  coverage and acceptance rate per sport, market, model version and period
  using only prospective, matched opportunities. Price-aware advantage needs
  realistic quote timestamps and closing-line reference.
- For slip-builder uplift, independently verify calibration of joint scenarios,
  expected-return estimates against real payout tables, and outcome accuracy
  on held-out prelock cards. One good day is not validation.
- Human review, exact-head CI, protected merge, deployed SHA parity, live
  readback, privilege checks and source-rights constraints remain separate.
  No self-certification, model parameter auto-adjustment or sports gambling.

## Existing integration — no parallel scorer

- Project Hub #2076 retains authority.
- Existing portfolio optimizer: `v17/slip_portfolio_optimizer.py`.
- Existing research preflight: `v17/slip_research_preflight.py`.
- Existing fitted-model path and clinical calibrated evaluation stay separate.
- Existing news/matchup handoff and material-change PR #2814 remain the owner
  for 13-sport/position/market stale-context safety.
- Fitted distribution research from merged #2829 is diagnostic, not automatic
  serving promotion.
- `V17_TERMINAL_REDUCER` is sole terminal authority; `can_execute=false`.
