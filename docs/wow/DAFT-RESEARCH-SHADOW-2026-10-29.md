# WOW V17 — DAFT external research and LLP sharp-alert shadow (2026-10-29)

**Status:** implemented research-only candidate on feature branch; not merged, activated, independently audited, or deployed. External research has no sporting-probability or terminal authority. `can_execute=false` throughout.

## Evidence and limits

The October 29 DAFT VIP Picks/Recaps research and sharp-alert LLP audit describe two different source strategies. VIP reasoning focuses on minutes, usage redistribution, defensive coverage, beneficiary identification and exposure. The sharp-alert bot appears to identify reference-market moves (often Pinnacle, over roughly 29–30 minutes), compare available prices, publish claimed fair probabilities and minimum odds, then follow with graded results and CLV. **This process is an inference from visible messages, not recovered private algorithm source code.** De-vig mechanics, alternate-line conversions and proprietary selection rules remain unknown. Source odds/probabilities cannot substitute for V17 fitted sport-specialist outputs.

The private audits identified original/result line mismatches, unclear soccer draw settlements, stale or time-ambiguous book offers, unsupported leagues, duplicate theses and discrepancies between source performance summaries and retrievable records. Preserve these as reconciliation problems: do not silently grade unlike lines or promote old captures to prospective evidence. Exact counts, message inventories, source URLs, screenshots, and research ledgers remain private; do not commit them.

Short-sample recap profitability and public creator claims do not establish subscriber fill performance or enduring predictive accuracy. The implementation neither reproduces nor certifies any external return figure.

The two markdown research reports were available to this implementation. The referenced raw ZIP packages were **not accessible in this session**. No private Discord payloads, source messages, book quotes, personal bankroll records, screenshots, or creator transcripts are committed.

## Architecture and contracts

- `v17/daft_research_market.py`: research message normalization, immutable capture versus publication timestamps, edit/version records, exact thesis keys, structured unverified prop hypotheses, binary/three-way proportional de-vig, push-aware EV, quote-age and exact-contract CLV. No guessed timestamps or line conversions.
- `v17/llp_daft_research_shadow.py`: post-score optional LLP research annotations with explicit agreement/disagreement, exact contract, quote freshness, available price, provenance/verification flags, source market estimates and CLV. The public response does not expose private message text. Missing/failed research never blocks an otherwise valid LLP sporting probability.
- `v17/daft_shadow_evaluation.py`: append-only pregame capture and separate independent postgame outcomes, strict exact contract identity, no retroactive model wins, price-realistic grading when a contemporaneous quote is established, and conservative evaluation summaries. Incremental performance is withheld until paired chronological holdouts exist.
- `tests/test_v17_daft_research_shadow.py`: synthetic tests only.

The adapter is **installed through `v17.compose_active_runtime` only when the feature flag is true**. The target is `v17.team_event_request_runtime.score_team_event_request`. It can append `llp_daft_research_shadow` to a dict response; it never changes `calibrated_*probability`, `calibrated_*lower_bound`, market prior, rank eligibility, terminal label, blocker list, or scorer. Any row without a complete `canonical_contract` remains unresolved rather than being matched heuristically.

### Private signal handoff

There is no background Discord scraping or authenticated DAFT connection. An operator-controlled importer can normalize caller-supplied records in a private environment. Do not put the resulting data in GitHub.

Private captured inputs may use:

```json
{
  "channel": "SHARP_BASKETBALL",
  "kind": "ORIGINAL_ALERT",
  "message_id": "synthetic-id",
  "published_at": "2026-10-29T17:00:00Z",
  "captured_at": "2026-10-29T17:01:00Z",
  "event_start_at": "2026-10-29T22:00:00Z",
  "edit_history_complete": true,
  "contract": {
    "official_event_id": "synthetic-event",
    "market_family": "MONEYLINE",
    "period": "FULL_GAME",
    "selection": "HOME",
    "line": null,
    "side": "home",
    "settlement_basis": "INCL_OT",
    "overtime_rule": "INCLUDE"
  }
}
```

This is **synthetic schema documentation**, not a real alert. Missing fields remain unknown. `VIP_CHAT` is excluded. The sharp channel inventory includes US football, basketball, MLB, soccer, NHL, and the older general sharp-alert feed. Picks and recaps are distinct from sharp alerts.

The private run-time input to the LLP annotation adapter is a JSON array of curated signal dictionaries, each with an exact `contract` as above, `research_eligibility` (explicitly `PROSPECTIVE` only when earned), `identity_verified`, `offered_quote_at`, `offered_decimal_odds`, `source_estimated_probability`, and `verification_status`. It is **not** raw Discord message text. Attribution to prospective capture depends on the frozen pre-event collector record, not the source message timestamp alone. The adapter cannot independently certify imported flags: import review and evidence reconciliation remain required.

Set `WOW_LLP_DAFT_SHADOW_ENABLED=true` and `WOW_DAFT_PRIVATE_SIGNALS_PATH=/absolute/private/location/signals.json` only in an authorized test/shadow runtime; the file must be outside the public checkout and is read-only, max 2 MB. No runtime flag enables bets or funding. Without a configured private file, the optional annotation returns unavailable. No production setting change was made.

### Market arithmetic

For a complete binary outcome space, proportional de-vig is `(1/o_i) / sum(1/o_j)` over both opposing outcomes. For soccer 1X2 require home, draw and away; do not pair two outcomes and silently erase draws. Keep regulation three-way, draw-no-bet, advancement and full-game-including-OT separate. Whole-number spread/total outcomes have a push state: `EV = P(win)*(decimal_odds - 1) - P(loss)`. The no-push binary shortcut is forbidden when a push is possible or unspecified. Alternative lines require exact certified market/distribution evidence; do not apply per-point linear shifts. For CLV, compare the **same** contract/selection/line/settlement, with timestamped entry and close.

Source-reported units, quarter Kelly and price floors are stored only as descriptive source attributes. No stake is recommended by the adapter. The word *sharp* is a source characterization of movement, not independently observed bettor identities or wager volume.

## Promotion and evaluation

Maintain an immutable prospective cohort including all eligible alerts, not only winners, and map original alerts, edits and updates to independently reconciled exact selections. Evaluate parse coverage, quote timestamp fidelity, staleness under actual latency, independent settlement consistency, CLV, actual-price return after relevant costs, deduped thesis exposure, Brier/calibration only for valid immutable model forecasts, and incremental paired performance against LLP alone. Use chronological holdouts and uncertainty; measure the source's standalone results separately from incremental model value.

Only after a governed Class C improvement loop — Discover → Hypothesis → Challenger → Historical Replay → Counterexample Review → Holdout/Forward Validation → Regression → Recommendation — may a proposal to alter fitted weights, confidence bounds or qualification be considered. `V17_TERMINAL_REDUCER` remains the sole global terminal authority.

## Verification

Run in a clone with Python test dependencies:

```bash
cd artifacts/wow-engine
python -m pytest tests/test_v17_daft_research_shadow.py -q
```

Passes here are logic tests only. Production integration, authenticated private data import, compliant raw-data retention and publication-path acceptance require separate evidence. Never claim the recovered DAFT sample was regraded or that model profitability improved merely because the synthetic tests pass.
