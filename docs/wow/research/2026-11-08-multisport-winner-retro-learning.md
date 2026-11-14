# 2026-11-08 NFL/NBA/NHL research-winner card — learning transfer

Status: **RESEARCH_DIAGNOSTIC_ONLY / PREGAME_RECEIPT_RECONCILIATION_PENDING**

Ownership: LLP_TEAM_BETTING_ENGINE per project hub #2076. Engineering integration: V17 postmortem, research/upset-watch skill contracts. This is not a fitted-model update, deployed release, or certified performance proof.

## Reported retrospective (descriptive, not certification)

A preceding conversational review reported the Sunday research winner card as:
- NFL preferred 12/13.
- NBA preferred 5/7.
- NHL preferred 4/6.
- Total preferred 21/26.
- Three separate NFL upset-watch alternatives 0/3; the original preferred winners on those same games 3/3.

**Evidence caution:** The conversational result table and the published research preferences are not substitutes for the canonical immutable *pregame* receipt ledger and independently settled official event IDs. These observed counts must not be posted into V17's fitted specialist forward-certification cohort or GH's actually submitted Pick'em card. No immutable card/settlement receipt set was supplied to this change; the reported record remains research-only and unverified for durable postmortem-grade acceptance. A test fixture reproducing the 13/7/6 structure is explicitly synthetic.

## What to preserve

- Keep the preferred winner as its own frozen card; distinguish contender/watch alternatives, pool submission, and fitted model output.
- Rank governed winners by the fitted specialist's calibrated lower bounds when certified. Research preferences without a certified fitted artifact remain provisional or held according to the existing board contract.
- Keep the full exact event inventory, not only successful predictions or the first page. For an NFL weekly card reconcile the full official Thursday-to-Monday slate even if a daily Sunday-only card is a legitimate smaller research cohort.
- Independently verify official finals; never grade a close loss as a research hit.

## Learning hypotheses, not proven root causes

| Sport | Trigger for review | Frozen evidence needed | What must not happen |
|---|---|---|---|
| NFL | One preferred miss; separate 0/3 upset-watch misses | QB efficiency/availability, pass protection, turnover exposure, challenger flip rationale, timestamped source contradiction | Treat external 51-49 lean as fitted upset probability; force contrarian flip |
| NBA | Two preferred misses | Exact time of player status, expected replacements, opponent shooting/usage ceiling, rest/travel | Retrospectively insert a player injury update or a successful bench performance as if pregame known |
| NHL | Two preferred misses, including a late-decided and an OT game | Confirmed starter/goalie source timing, rest/travel, injuries, special-teams context, OT/shootout settlement basis | Convert close regulation margin into a win; apply manual cross-sport risk probability haircuts |

These are questions to test on historical point-in-time data. Variance and unexpected in-game events may explain a single realized loss without identifying a persistent modeling defect.

## Implemented contract (read-only)

- New `v17.llp_research_card_retro.audit_research_card` grades one immutable research `PREFERRED` and an optional distinct `UPSET_WATCH` per official event, only with independent pregame and settlement receipt attestations.
- Strict kickoff, frozen-as-of, visible-before-lock and nonempty research-input availability timestamps block hindsight leakage. Duplicates, unresolved official identity, invalid settlement and incomplete official-slate coverage fail closed. Settlement freshness, including any later corrected or superseding official final, is **not independently established by this audit**: the upstream independent settlement verifier must check the receipt is current and not superseded before asserting `independently_verified=True`.
- The only overall headline research accuracy is for a complete verified official event set; otherwise show partial typed grades and blockers, with headline accuracy null.
- Separate preferred/upset grading, same-game counterfactual comparison and pregame-feature review questions are diagnostics. No official model probability is generated and no automatic upset flip is allowed.
- Optional `v17.llp_postmortem_recalibration.build_llp_learning_report` arguments append the research audit in a distinct `research_card` field without changing fitted-model Brier/log-loss/calibration.
- New regression suite includes 15 synthetic tests (including receipt-reuse rejection): full three-sport reconciliation, 21/26 and 0/3 synthetic shape, missing receipts, late pick, hindsight evidence, unverified final, incomplete slate, duplicate picks, invalid event identity, unknown research and no retroactive upset flip.

## Verification and remaining work

- Targeted isolated code regression run: `pytest -q test_llp_research_card_retro.py` — 15 passed in an isolated local extraction (not a complete repository test).
- Exact-head GitHub CI and independent review must complete before declaring the PR accepted. This is an unmerged research-layer patch until then.
- The actual Nov 8 research card still requires its true immutable pregame source IDs, independent official final receipts, and verified official event manifest. Do not manufacture these from post-lock sheets.
- No model/calibrator, terminal reducer, database migration, production build, or live GPT editor action is changed. Model validation and promotion remain their separate governed process.

## Next evaluation

Compare unchanged baseline vs candidate feature hypotheses on a registered, immutable, out-of-time cohort with authentic pregame known-availability and independent final settlement. Report Brier/log loss only for genuine governed fitted probability rows; report exact-correct counts for research winners separately. Keep the challenger research-only until independent certification and protected release.

Links: issue #2076, scorecard #1530, independent QA #1521, prior postmortem #1852, active full-board draft PR #2533 and independent receipt-verifier PR #2487.
