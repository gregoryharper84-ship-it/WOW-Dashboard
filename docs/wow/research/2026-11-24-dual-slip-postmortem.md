# WOW V17 — November 24 three-leg / five-leg slip postmortem

**Class:** OBSERVATIONAL CHAT RETROSPECTIVE / NOT CERTIFIED WOW FORECASTS / NO USER SUBMISSIONS VERIFIED.

**Scope:** The final revised three-leg and five-leg player-prop proposals from the
2026-11-24 WOW conversation, eight unique selections across two sports. The
original overlapping slips are excluded from the scorecard so no pick is counted
twice. Fixture:
[2026-11-24-dual-slip-postmortem.json](./2026-11-24-dual-slip-postmortem.json).

## 1. Result scorecard

| Slip | Player | Final pick | Actual | Result |
| --- | --- | --- | --- | --- |
| 3-leg | Anthony Davis | LESS 1.5 steals | 2 | LOSS |
| 3-leg | Connor McDavid | MORE 0.5 points | 1 | WIN |
| 3-leg | Christian Braun | MORE 2.5 assists | at least 3 | WIN |
| 5-leg | Daniss Jenkins | LESS 0.5 blocks | 0 | WIN |
| 5-leg | Paul Reed | MORE 7.5 rebounds + assists | 7 | LOSS |
| 5-leg | Naji Marshall | MORE 0.5 steals | at least 1 | WIN |
| 5-leg | Sam Hauser | MORE 6.5 points | 4 | LOSS |
| 5-leg | Leon Draisaitl | MORE 0.5 points | 0 | LOSS |

**4 wins · 4 losses (50%).** Perfect slips 0/2. Three-leg 2/3 (66.7%),
five-leg 2/5 (40.0%). NBA 3/6, NHL 1/2, MORE 3/6, LESS 1/2.

## 2. Evidence boundary — what this file does and does not establish

- Both winning thresholds marked "at least" were confirmed from **partial-game**
  box-score snapshots. Final individual totals for those two picks were not
  independently re-fetched.
- No PrizePicks settlement, accepted payout, stake, or financial return was
  verified. No ROI or profit/loss may be attributed from these slips.
- No authenticated WOW pregame specialist receipt, calibrated probability, or
  joint scenario receipt exists for any of the eight selections. They are
  therefore excluded from governed calibration and are **not** evidence of model
  miscalibration; they are evidence about a **selection process**.
- No engineering change described here is deployed. `can_execute=false` and
  `V17_TERMINAL_REDUCER` authority are unchanged.

## 3. Process findings

1. **Historical hit rate was used as if it were a matchup probability.** The
   Davis LESS 1.5 steals anchor recorded 2 steals in about 37 minutes. A recent
   under rate says nothing about opponent turnovers, passing patterns, defensive
   assignment, or projected minutes.
2. **Injury-driven opportunity was assumed, not distributed.** Paul Reed had
   6 rebounds + 1 assist in 22:27. Teammate absences do not transfer usage to a
   specific player by default; the redistribution has to be quantified.
3. **Low-volume scoring props were taken without projected shot volume.**
   Hauser scored 4 points on 4 field-goal attempts in about 22 minutes.
4. **NHL point props ignored power-play and shot-generation opportunity.**
   Draisaitl had 0 points and 1 shot on goal in nearly 24 minutes; Edmonton had
   one power play.
5. **De-duplicating players did not de-duplicate games.** The eight selections
   touched four games; three of them (BOS-WAS, VGK-EDM, DAL-DEN) appear in both
   scored slips, and the five-leg card held two Detroit players. Different
   players in the same game are not independent outcomes.
6. **A strong-confidence label had no certification behind it.** No verified
   exact platform lines, no calibrated joint distribution, no accepted payout
   context.

## 4. Corrective backlog — implementation status

Priority/status are engineering facts, not a sporting certification.

| Priority | Correction | Status in this patch |
| --- | --- | --- |
| P0 | Prevent unsupported strong-confidence classifications | **Hardened.** `v17/slip_research_preflight.py` now normalizes caller labels before screening, so `"STRONG CONFIDENCE"`, `"strong-confidence"` and `"Lock of the Day"` are held exactly like `"STRONG_CONFIDENCE"`. Previously only the exact underscore spelling matched and a space/hyphen variant passed unflagged. |
| P0 | Require verified player, event and prop-line identity | **Already present, regression-covered.** `exact_key`, `_player_blockers`, `_quote_blockers` and `_prizepicks_offer_blockers` require official player/event/market/period/side/line/settlement bindings. The November 24 fixture carries no official event ID, and the construction report emits `OFFICIAL_EVENT_ID_MISSING` rather than inventing one. |
| P0 | Recompute affected projections after material news | **Already present.** `MATERIAL_NEWS_RESCORE_NOT_VERIFIED`, `PROP_OPPORTUNITY_RESCENARIO_REQUIRED`, `PRIZEPICKS_MATERIAL_NEWS_PREDICTION_STALE` block pre-change receipts across sports. |
| P0 | Preserve immutable pregame prediction receipts | **Already present.** `postmortem_engine` forces probability fields to `null` and `capture_timing=POST_EVENT_RETROACTIVE` without a matched immutable pregame record; `card_immutable_receipt_gate` and `test_prop_immutable_pregame_lock` protect the pregame lock. |
| P1 | Improve minutes, usage and opportunity distributions | **Gate present, model not supplied.** `_opportunity_blockers` rejects `LAST_10` / hit-rate-only forecasts and requires expected/p10/p90 opportunity context. No fitted minutes/usage distribution was added here. |
| P1 | Improve steals and blocks count-stat specialists | **NOT IMPLEMENTED.** The advertised NBA prop capability set does not declare `STEALS` or `BLOCKS`; adding them is fitted-model work with chronological calibration, not a plumbing fix. Until then these markets cannot be certified from historical hit rates. |
| P1 | Improve PRA and combination-stat modeling | **PARTIAL.** NBA/WNBA composite candidate modules and the portfolio component/composite overlap check exist; a certified forward PRA calibration is not established. |
| P1 | Improve NHL power-play and scoring distributions | **NOT IMPLEMENTED.** No fitted power-play-opportunity / shot-generation distribution backs the NHL point props reviewed here. |
| P1 | Add game-level and cross-slip correlation evaluation | **Implemented and enforced in this patch.** `v17/slip_construction_postmortem.py` tracks sport+event exposure across cards independently of player identity and emits per-card `cross_slip_shared_event_legs`, `distinct_event_count` and `CROSS_SLIP_GAME_EXPOSURE_UNVERIFIED`, plus portfolio `cross_slip_shared_event_groups` and `cross_slip_shared_event_position_occurrences`. More importantly, `v17/slip_research_preflight.py` now **fails closed on any game shared between two proposed cards**: each affected card and leg is `RESEARCH_HOLD` with `CROSS_SLIP_EVENT_EXPOSURE_UNVERIFIED` and `joint_probability=null`. The hold is symmetric — the first-read card is held exactly like the later one — and it is **absolute** in this audit. A `cross_slip_joint_verification` block typed into the same untrusted payload is not a clearing path: the pure preflight cannot authenticate it, so accepting it would recreate the failure it exists to prevent. A governed joint-scenario artifact would need a separate authenticated path, which does not exist. The former `CROSS_SLIP_EVENT_HINGE_CONCENTRATION` threshold and its `max_distinct_slips_per_event` parameter were removed as redundant: any shared game already holds every affected card. |
| P1 | Require separate prediction and slip-construction postmortems | **Already present.** `build_postmortem_payload` returns an independent `slip_construction_postmortem`; the read-only route is `GET /v17/postmortem-runs/{research_run_id}/slip-construction`. |
| P1 | Validate improvements through out-of-sample replay | **Not satisfied for these picks.** No frozen pregame incumbent/challenger receipts exist for this conversation, so nothing here can authorize a model change. |

## 5. What the new construction fields show for this slate

Using explicitly synthetic `CHAT_GAME_LABEL:` game identifiers (the fixture has
no official event IDs) to exercise the correlation path:

- 4 distinct games, 3 of them shared across the two scored slips
  (`cross_slip_shared_event_groups == 3`).
- All three legs of the three-leg card sit in games also used by the five-leg
  card (`cross_slip_shared_event_legs == 3`), so both cards carry
  `CROSS_SLIP_GAME_EXPOSURE_UNVERIFIED`.
- Run through the research preflight, the same two cards are **held**, not
  merely flagged: both report `CROSS_SLIP_EVENT_EXPOSURE_UNVERIFIED` and
  `cross_slip_shared_game_count == 3`. Only the two Detroit legs (Jenkins, Reed)
  carry no cross-card game exposure. A caller-supplied
  `cross_slip_joint_verification` receipt does **not** clear the hold; the
  regression test asserts both cards stay `RESEARCH_HOLD` with
  `joint_probability=null` and `can_publish_as_certified=false`.
- The five-leg card also carries `SAME_EVENT_DEPENDENCY_UNVERIFIED` (two Detroit
  selections) and `FOUR_PLUS_LEGS_FRAGILITY`.
- With the real fixture (no official event IDs) the same report instead reports
  `distinct_event_count == 0` and `OFFICIAL_EVENT_ID_MISSING` per card: an
  unverified game identity is not silently upgraded into a certified one.

No joint probability, expected value, accepted ticket settlement, or realized
ROI is produced by any of these fields.

## 6. Acceptance

From the `artifacts/wow-engine` import context:

```bash
python -m pytest -q \
  v17/test_nov24_dual_slip_postmortem.py \
  v17/test_slip_portfolio_optimizer.py \
  v17/test_session_exposure_card_shrink_p0.py \
  v17/test_slip_construction_postmortem.py \
  v17/test_postmortem_engine.py \
  v17/test_postmortem_shared_hinge_attribution.py \
  v17/test_nov22_chat_slip_postmortem.py \
  v17/test_slip_research_preflight.py
```

## 7. Remaining open work (not implied by this patch)

Fitted minute/usage/opportunity distributions for steals, blocks, combination
stats and NHL power-play scoring; certified joint scenario distributions with
verified platform payout terms; a forward, matched-opportunity replay comparing
incumbent and challenger before any strong-confidence label is authorized. Until
those exist, the correct output for a card like this is a research hold, not a
strong-confidence recommendation.
