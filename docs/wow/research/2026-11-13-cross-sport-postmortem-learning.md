# November 13, 2026 cross-sport research card — evidence-scoped retrospective

Status: **RESEARCH_ONLY / IMMUTABLE_PREGAME_CARD_RECONCILIATION_PENDING**.
Owner: LLP_TEAM_BETTING_ENGINE and the existing research-card retrospective PR #2547, Project Hub #2076. Schedule/fatigue reconstruction remains owned by PR #2655; do not duplicate or replace its producer.

## Known independent official results (examples, not a 40-slot certified grade)

| Event | Independently reported final | Original research selection in chat | Research outcome |
| --- | --- | --- | --- |
| Georgetown at Clemson (NCAAB) | Georgetown 85, Clemson 78 | Georgetown ML / Georgetown +3.5 | Winner and spread direction correct |
| Florida State at Pittsburgh (NCAAF) | Florida State 16, Pittsburgh 10 | Florida State upset watch; Pittsburgh -3.5 on primary spread card | Upset hit; favorite spread missed |
| Nashville at Vegas (NHL) | Nashville 3, Vegas 2 (shootout) | Nashville upset watch, Vegas preferred ML, Under 6.0 | Upset hit; preferred ML missed; total Under hit under the stated full-game rules |
| Estonia at Luxembourg (Soccer) | Estonia 1, Luxembourg 0 | Estonia upset watch (regulation 1X2) | Upset hit |

Independent public sources:
- Clemson University Athletics, https://clemsontigers.com/news/2026/11/13/clemson-falls-to-georgetown-85-78
- Florida State Athletics, https://seminoles.com/news/2026/11/13/football-seminoles-take-down-pitt-for-second-straight-road-win
- NHL official recap, https://www.nhl.com/news/nashville-predators-vegas-golden-knights-game-recap-november-13-2026
- UEFA 2026/27 Nations League matchday 5 results, https://www.uefa.com/uefanationsleague/news/02a2-1fea18abbcbc-456e846509e7-1000--2026-27-uefa-nations-league-all-the-league-phase-fixtures-a/

**Important provenance limitation:** These verified final scores are not proof that every pick, timestamp, quoted line, or odds snapshot was independently frozen before kickoff. No authentic frozen 40-entry card manifest, upstream receipt-verification bundle, or complete official per-sport settlement receipt package has been bound to this note. The conversational 7/10 ML, 4/10 spreads, 7/10 totals, 6/10 upset and 24/40 numbers remain **descriptive claims, not a certified full-card result**. Do not load them into forward model certification, Brier/log loss, actual wager returns, or GH's submitted-card record.

## Patch on the existing #2547 research owner

New read-only `v17.llp_research_multimarket_retro.audit_multimarket_retro`:
- Grades moneyline (including separately grouped upset watches), signed spread and totals by exact sport/event/market/side/line and full-game/regulation basis. Tennis official prematch withdrawal is VOID, not a predicted winner.
- Enforces official outcome receipts, pregame immutability/visibility/as-of, event/participant/kickoff identity, confirmed final score and chronology; prevents duplicate selections inside a cohort, reused pick receipts and reused official settlement receipts.
- Rejects unimplemented quarter-point/Asian handicap grading rather than using incorrect simple push math. Distinguishes WIN, LOSS, PUSH, VOID and HELD.
- Requires a distinct verified complete frozen selection manifest per named cohort to show a cohort's hit rate. Missing selections and whole missing cohorts remain visible; a cross-market 24/40 aggregate is deliberately not exported.
- Never uses odds or external probabilities as model inputs, assigns model confidence, changes a preferred pick, grades GH's submitted NFL card, or modifies calibrated model probabilities. `can_execute=false`.

Synthetic regression command:

```bash
PYTHONPATH=artifacts/wow-engine python -m pytest -q artifacts/wow-engine/test_llp_research_multimarket_retro.py
```

Test cases include Nov 13 score-shaped examples, side-vs-spread disagreement, pushes, soccer 1X2 draws, tennis voids, bad/missing receipts, postfreeze evidence, duplicate event/final receipts, missing cohort inventory, wrong settlement, and malformed lines. Synthetic tests **are not** real immutable receipt certification. Hosted exact-head CI, independent SIRT/QA/Release, authorized merge, runtime SHA/readback are separate prerequisites.

## Learning to investigate prospectively; no hand-tuned coefficients

1. **Winner versus spread:** Orlando beating Milwaukee while failing -8.5 and UCLA winning without covering are hypotheses for separate margin distributions. Do not infer spreads from ML conviction.
2. **Underdog stress paths:** FSU's defensive stops and Clemson's comeback against Georgetown motivate explicit leading/trailing, turnover, pressure, bench and tempo paths. They were outcomes, not necessarily forecasts available before kickoff.
3. **Schedule and fatigue:** Nashville won despite road-travel workload. Include authenticated event order, 3-in-4, 4-in-6, road-only sequences, rest differential, player/goalie minutes, venue and time zones via existing #2655. Do not apply universal fatigue deductions or conflate actual flights with scheduled venues.
4. **Soccer:** Keep draws as a genuine third regulation outcome, never split draws 50/50 into a two-way ML probability.
5. **Tennis:** Withdrawals and replacements must never be graded as wins for the original matchup; maintain exact participant identities and market-specific retirement rules.
6. **Upset ranking:** Distinguish highest outright underdog win probability from speculative market discrepancy; require time-correct no-vig price comparisons and independent confidence/holdout validation.

Next gate: reconcile original November 13 immutable research card IDs, timestamped lines and official event/settlement receipts for every cohort. Grade separately by market and sport. Only then publish an accepted research scorecard, and evaluate fitted feature challengers with chronological out-of-time holdouts and independent calibration evidence. No main write, betting, submission, model promotion or production change under this patch.
